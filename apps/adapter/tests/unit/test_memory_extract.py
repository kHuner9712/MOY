"""事实抽取与合并的单元验收（阶段四 T4-03.3，架构 §5.3 / PRD §4.2-4.7）。

覆盖的判据（每条都对应一个会被用户或审计抓到的错误）：

1. **6 类事实可解析**——schema 固定，不做自由文本记忆；
2. **只抽 `role=user`**——助手说的"我记住了"不是事实来源；
3. **敏感信息丢弃且不落库**——`dropped_sensitive>0`、库里搜不到原文；
4. **同 hash 不新增行**——同一条事实重复出现只刷新 `hit_count`；
5. **`superseded_by` 链正确**——新值覆盖为当前值，旧值保留但不参与召回；
6. **200 条上限淘汰**——按"低置信度优先、最久未更新优先"硬删；
7. **降级**——抽取失败只让任务失败（`max_attempts=1`），对话永远 `code=0`。

全部离线：抽取走 Fake 补全协议（**不走 `ModelService.generate`**，那是自喂循环的根源），
存储走内存实现。真库路径由 `tests/integration/test_memory_schema_pg.py` 覆盖。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import partial
from typing import Any

import pytest

from app.business.memory_service import MemoryService
from app.core.config import Settings
from app.db.repositories.conversation_repo import (
    InMemoryMessageStore,
    MessageRecord,
)
from app.db.repositories.job_repo import InMemoryJobStore, get_job_store
from app.db.repositories.memory import (
    InMemoryMemoryStore,
    MemorySettingRecord,
)
from app.db.tenant import PRIVATE_TENANT
from app.services.memory_embedding import EmbeddingProvider, NullEmbeddingProvider
from app.services.memory_extractor import (
    ChatCompletionError,
    FactCandidate,
    MemoryExtractor,
)
from app.workers.job_runner import JobRunner
from app.workers.tasks import build_default_runner
from app.workers.tasks.memory_extract import handle_memory_extract
from tests.conftest import TEST_MEMORY_APP_KEY, TEST_TENANT_ID, TEST_USER_ID

pytestmark = [pytest.mark.unit, pytest.mark.memory]

TENANT = TEST_TENANT_ID
USER = TEST_USER_ID
OTHER_USER = "usr_other_user"
CONVERSATION = "conv_01K4Q8Z2X7M9B3N5P6R8T0V2W4"

SIX_FACTS: list[dict[str, str]] = [
    {"category": "identity", "content": "我叫赵工", "confidence": "high"},
    {"category": "org", "content": "我在供应链部", "confidence": "high"},
    {"category": "preference", "content": "汇报要表格式", "confidence": "high"},
    {"category": "convention", "content": "我们内部把它叫交付包", "confidence": "high"},
    {"category": "constraint", "content": "金额一律不许写", "confidence": "high"},
    {"category": "project", "content": "Q4要上线供应链系统", "confidence": "low"},
]


class FakeChat:
    """记录提示词的补全替身（可配置返回或抛错）。"""

    def __init__(
        self,
        facts: Sequence[dict[str, str]] = (),
        *,
        error: Exception | None = None,
        raw: str | None = None,
    ) -> None:
        self._facts = list(facts)
        self._error = error
        self._raw = raw
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        """调用次数。

        Returns:
            int: 次数。
        """
        return len(self.prompts)

    def set_facts(self, facts: Sequence[dict[str, str]]) -> None:
        """替换下一次的返回内容。

        Args:
            facts: 事实列表。
        """
        self._facts = list(facts)

    async def complete(self, prompt: str) -> str:
        """记录提示词并返回配置的 JSON。

        Args:
            prompt: 提示词。

        Returns:
            str: 模型输出。

        Raises:
            ChatCompletionError: 配置了失败时抛出。
        """
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        if self._raw is not None:
            return self._raw
        return json.dumps(self._facts, ensure_ascii=False)


class FakeEmbedding:
    """可用的向量替身（证明"可用时写向量"这一分支没断）。"""

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim

    @property
    def available(self) -> bool:
        """恒可用。

        Returns:
            bool: True。
        """
        return True

    @property
    def model(self) -> str:
        """模型标识。

        Returns:
            str: 标识。
        """
        return "fake-embedding-4"

    @property
    def dim(self) -> int:
        """维度。

        Returns:
            int: 维度。
        """
        return self._dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """按字符和生成确定性向量。

        Args:
            texts: 待向量化文本。

        Returns:
            list[list[float]]: 向量列表。
        """
        return [[float(len(text) % 7), 1.0, 0.5, 0.25] for text in texts]


class _Harness:
    """抽取用例的装配体（内存存储 + Fake 补全）。"""

    def __init__(
        self,
        *,
        chat: FakeChat | None = None,
        settings: Settings | None = None,
        embedding: EmbeddingProvider | None = None,
        memories: InMemoryMemoryStore | None = None,
        jobs: InMemoryJobStore | None = None,
    ) -> None:
        self.settings = settings or Settings(
            env="test", business_store="memory", memory_extract_every_turns=10
        )
        self.memories = memories or InMemoryMemoryStore()
        self.messages = InMemoryMessageStore()
        self.jobs = jobs or InMemoryJobStore()
        self.chat = chat or FakeChat([])
        self.extractor = MemoryExtractor(self.chat)
        self.embedding: EmbeddingProvider = embedding or NullEmbeddingProvider()
        self.service = MemoryService(
            self.settings,
            memories=self.memories,
            messages=self.messages,
            jobs=self.jobs,
            extractor=self.extractor,
            embedding=self.embedding,
        )

    async def seed(self, entries: Sequence[tuple[str, str]]) -> None:
        """写入消息（`(role, content)` 序列）。

        Args:
            entries: 角色与正文。
        """
        for role, content in entries:
            await self.messages.append(
                MessageRecord(
                    tenant_id=TENANT,
                    conversation_id=CONVERSATION,
                    user_id=USER,
                    role=role,
                    content=content,
                    status="settled",
                )
            )

    async def seed_user_turns(self, count: int, *, prefix: str = "第") -> None:
        """写入若干轮"用户 + 助手"消息。

        Args:
            count: 用户轮数。
            prefix: 正文前缀（保证每轮内容不同）。
        """
        for index in range(count):
            await self.seed(
                [("user", f"{prefix}{index}轮：我在整理供应链的报表"), ("assistant", "好的")]
            )


def _enable_extract_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """让请求链路里的抽取应用"已配置"。

    **为什么用环境变量而不是 `app.state.settings`**：`ModelService` 由
    `get_model_service()`（无参）构造，其 `_settings` 取**进程级** `get_settings()`；
    这是阶段二就存在的既有形态（`conftest` 的注入配置只到 `app.state`）。
    生产环境里"进程级配置 = 部署环境变量"，所以生产语义正确；测试要模拟"已配置"，
    就得在进程级配置上注入。注：该形态已记入本提交的报告，供后续批次统一收口。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具。
    """
    from app.core.config import reset_settings_cache

    monkeypatch.setenv("OA_DIFY_MEMORY_APP_KEY", TEST_MEMORY_APP_KEY)
    reset_settings_cache()


def _facts(*items: tuple[str, str]) -> list[dict[str, str]]:
    """构造事实列表。

    Args:
        *items: `(category, content)` 序列。

    Returns:
        list[dict[str, str]]: 事实列表。
    """
    return [
        {"category": category, "content": content, "confidence": "high"}
        for category, content in items
    ]


class TestExtraction:
    """抽取口径：6 类、只抽 user、条数与字数上限。"""

    async def test_parses_all_six_categories(self) -> None:
        """6 类事实全部落库（单次上限 5 条，故分两轮抽取）。"""
        harness = _Harness(chat=FakeChat(SIX_FACTS[:5]))
        await harness.seed([("user", "我叫赵工，在供应链部，汇报要表格式")])

        first = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)
        harness.chat.set_facts([SIX_FACTS[5]])
        second = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert first.skipped is False
        assert first.extracted == 5  # 单次最多 5 条（PRD §4.3）
        assert second.extracted == 1
        stored = await harness.memories.list_active(TENANT, USER)
        assert len(stored) == 6
        assert {item.category for item in stored} == {
            "identity",
            "org",
            "preference",
            "convention",
            "constraint",
            "project",
        }
        low = [item for item in stored if item.confidence == "low"]
        assert len(low) == 1  # 推测性表述只降置信度，不丢弃

    async def test_only_user_messages_reach_the_model(self) -> None:
        """助手消息不得进入抽取提示词（助手说"我记住了"不是事实）。"""
        harness = _Harness()
        await harness.seed(
            [
                ("user", "我叫赵工"),
                ("assistant", "记住了：你希望汇报全部用表格"),
            ]
        )

        await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert harness.chat.calls == 1
        prompt = harness.chat.prompts[0]
        assert "我叫赵工" in prompt
        assert "全部用表格" not in prompt

    async def test_window_uses_most_recent_user_messages(self) -> None:
        """回看窗口取**最近** 20 条用户消息，且按时间正序进提示词。"""
        harness = _Harness()
        for index in range(30):
            await harness.seed([("user", f"第{index}条消息")])

        await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        prompt = harness.chat.prompts[0]
        assert "第29条消息" in prompt
        assert "第0条消息" not in prompt
        assert prompt.index("第28条消息") < prompt.index("第29条消息")

    async def test_facts_beyond_max_and_char_limit_are_trimmed(self) -> None:
        """单次最多 5 条、每条 ≤60 字（超出丢弃，不排队重试）。"""
        long_text = "我" + "很" * 80
        harness = _Harness(
            chat=FakeChat(
                [
                    {"category": "preference", "content": long_text, "confidence": "high"},
                    *[
                        {"category": "org", "content": f"我在第{i}事业部", "confidence": "high"}
                        for i in range(6)
                    ],
                ]
            )
        )
        await harness.seed([("user", "说了很多")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.extracted == 5
        assert result.dropped_overflow == 2
        stored = await harness.memories.list_active(TENANT, USER)
        assert all(len(item.content) <= 60 for item in stored)

    async def test_empty_model_output_writes_nothing(self) -> None:
        """模型返回 `[]` 时不写任何行（也不报错）。"""
        harness = _Harness(chat=FakeChat([], raw="[]"))
        await harness.seed([("user", "今天下午三点开会")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.extracted == 0
        assert await harness.memories.count_active(TENANT, USER) == 0

    async def test_no_user_message_skips_without_calling_model(self) -> None:
        """没有用户消息时不调模型（不该为空会话烧一次额度）。"""
        harness = _Harness()
        await harness.seed([("assistant", "你好，有什么可以帮你")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.skipped is True
        assert harness.chat.calls == 0


class TestSensitiveDropping:
    """敏感信息：命中即丢弃、不落库、不记原文。"""

    async def test_sensitive_facts_are_dropped_and_never_stored(self) -> None:
        """身份证 / 银行卡 / 手机号 / 密码 / 健康类全部丢弃。"""
        harness = _Harness(
            chat=FakeChat(
                [
                    {"category": "identity", "content": "我的身份证是110101199003071234"},
                    {"category": "org", "content": "工资卡6222021234567890123"},
                    {"category": "identity", "content": "手机号13800138000"},
                    {"category": "identity", "content": "我的密码是abc123"},
                    {"category": "identity", "content": "我有糖尿病"},
                    {"category": "preference", "content": "汇报要表格式"},
                ]
            )
        )
        await harness.seed([("user", "交代一些私事")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.dropped_sensitive == 5
        assert result.extracted == 1
        stored = await harness.memories.list_active(TENANT, USER)
        assert [item.content for item in stored] == ["汇报要表格式"]
        joined = " ".join(item.content for item in stored)
        for secret in ("110101199003071234", "6222021234567890123", "13800138000", "abc123"):
            assert secret not in joined


class TestDedupeAndMerge:
    """同 hash 合并、相似则写 `superseded_by` 链。"""

    async def test_same_hash_merges_without_new_row(self) -> None:
        """同一条事实重复出现：只刷新 `hit_count` 与 `updated_at`，不新增行。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[2]]))
        await harness.seed([("user", "汇报要表格式")])

        first = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)
        second = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert first.extracted == 1
        assert second.merged == 1
        assert second.extracted == 0
        assert await harness.memories.count_active(TENANT, USER) == 1
        stored = await harness.memories.list_active(TENANT, USER)
        assert stored[0].hit_count == 2

    async def test_hash_ignores_whitespace_and_punctuation(self) -> None:
        """归一化后同键：`汇报要表格式` 与 `汇报要 表格式。` 是同一条事实。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[2]]))
        await harness.seed([("user", "汇报要表格式")])
        await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        harness.chat.set_facts([{"category": "preference", "content": "汇报要 表格式。"}])
        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.merged == 1
        assert await harness.memories.count_active(TENANT, USER) == 1

    async def test_changed_fact_supersedes_old_value(self) -> None:
        """事实变更：新值是当前值，旧值写 `superseded_by`（保留可查、不参与召回）。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[2]]))
        await harness.seed([("user", "汇报要表格式")])
        await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)
        before = (await harness.memories.list_active(TENANT, USER))[0]

        harness.chat.set_facts([{"category": "preference", "content": "汇报要表格式的"}])
        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.superseded == 1
        active = await harness.memories.list_active(TENANT, USER)
        assert len(active) == 1
        assert active[0].content == "汇报要表格式的"

        old = await harness.memories.get(TENANT, before.memory_id)
        assert old is not None
        assert old.superseded_by == active[0].memory_id
        assert old.content == "汇报要表格式"

    async def test_unrelated_fact_is_inserted_not_superseded(self) -> None:
        """完全不同的事实是新增，不是"取代"。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[0]]))
        await harness.seed([("user", "我叫赵工")])
        await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        harness.chat.set_facts([SIX_FACTS[4]])
        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.extracted == 1
        assert result.superseded == 0
        assert await harness.memories.count_active(TENANT, USER) == 2


class TestLimits:
    """单用户 200 条上限与按类开关。"""

    async def test_overflow_is_evicted_oldest_low_confidence_first(self) -> None:
        """超过上限时硬删：低置信度 / 最久未更新优先，总数回到上限。"""
        settings = Settings(
            env="test",
            business_store="memory",
            memory_max_per_user=3,
            memory_extract_every_turns=10,
        )
        harness = _Harness(settings=settings)
        harness.chat.set_facts(
            _facts(
                ("identity", "我叫赵工"),
                ("org", "我在供应链部"),
                ("preference", "汇报要表格式"),
                ("convention", "我们内部把它叫交付包"),
                ("constraint", "金额一律不许写"),
            )
        )
        await harness.seed([("user", "自我介绍")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.extracted == 5
        assert result.evicted == 2
        assert await harness.memories.count_active(TENANT, USER) == 3

    async def test_settings_disabled_skips_extraction(self) -> None:
        """关全局开关：不抽取（连模型都不调）。"""
        harness = _Harness()
        await harness.memories.upsert_setting(
            MemorySettingRecord(tenant_id=TENANT, user_id=USER, enabled=False)
        )
        await harness.seed([("user", "我叫赵工")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.skipped is True
        assert harness.chat.calls == 0
        assert await harness.memories.count_active(TENANT, USER) == 0

    async def test_category_switch_disables_only_that_category(self) -> None:
        """关某一类：该类不抽取，其它类照常（已有条目保留不动）。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[0], SIX_FACTS[2]]))
        await harness.memories.upsert_setting(
            MemorySettingRecord(
                tenant_id=TENANT,
                user_id=USER,
                enabled=True,
                category_switches={"preference": False},
            )
        )
        await harness.seed([("user", "我叫赵工，汇报要表格式")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        stored = await harness.memories.list_active(TENANT, USER)
        assert [item.category for item in stored] == ["identity"]
        assert result.dropped_invalid == 1

    async def test_embedding_unavailable_stores_facts_without_vectors(self) -> None:
        """D-3：向量不可用 → 跳过向量写入，事实照存。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[2]]), embedding=NullEmbeddingProvider())
        await harness.seed([("user", "汇报要表格式")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.extracted == 1
        assert result.embedding_written == 0
        assert await harness.memories.count_active(TENANT, USER) == 1
        stored = await harness.memories.list_active(TENANT, USER)
        assert await harness.memories.list_embeddings(TENANT, [stored[0].memory_id]) == {}

    async def test_embedding_available_writes_vectors(self) -> None:
        """向量可用 → 每条事实一行向量（证明该分支未被写死关闭）。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[2]]), embedding=FakeEmbedding())
        await harness.seed([("user", "汇报要表格式")])

        result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)

        assert result.embedding_written == 1
        stored = await harness.memories.list_active(TENANT, USER)
        vectors = await harness.memories.list_embeddings(TENANT, [stored[0].memory_id])
        assert len(vectors[stored[0].memory_id]) == 4


class TestDegradation:
    """四条降级：抽取失败只影响任务，不影响对话。"""

    async def test_extraction_failure_fails_job_without_retry(self) -> None:
        """D-1：模型失败 → 任务 `failed` 且 `attempts=1`（不重试）。"""
        harness = _Harness(chat=FakeChat([], error=ChatCompletionError("upstream down")))
        await harness.seed_user_turns(1)
        queued = await harness.service.maybe_enqueue_extract(
            TENANT, USER, CONVERSATION, user_turns=10
        )
        assert queued is True

        runner = JobRunner(
            harness.settings,
            store=harness.jobs,
            worker_id="test-worker",
            task_timeout_seconds=1.0,
            batch_size=5,
        )
        runner.register(
            "memory_extract",
            partial(handle_memory_extract, service=harness.service),
            max_attempts=1,
        )

        assert await runner.tick() == 1
        jobs = await harness.jobs.list_by_status(TENANT, "failed")
        assert len(jobs) == 1
        assert jobs[0].attempts == 1
        assert "ChatCompletionError" in jobs[0].last_error
        assert await harness.memories.count_active(TENANT, USER) == 0

    async def test_db_write_failure_does_not_raise(self) -> None:
        """D-4：DB 写入失败不重试、不上抛；连续 3 次升级为告警。"""

        class _BrokenStore(InMemoryMemoryStore):
            async def insert(self, record: Any) -> Any:
                raise RuntimeError("db is down")

        harness = _Harness(chat=FakeChat([SIX_FACTS[2]]), memories=_BrokenStore())
        await harness.seed([("user", "汇报要表格式")])

        for _ in range(3):
            result = await harness.service.extract_and_merge(TENANT, USER, CONVERSATION)
            assert result.write_failed is True
        assert harness.service.consecutive_write_failures == 3

    async def test_maybe_enqueue_never_raises_when_queue_is_broken(self) -> None:
        """入队失败只记日志：调用方（对话链路）拿不到异常。"""

        class _BrokenJobs(InMemoryJobStore):
            async def enqueue(self, record: Any) -> Any:
                raise RuntimeError("queue is down")

        harness = _Harness(jobs=_BrokenJobs())
        assert (
            await harness.service.maybe_enqueue_extract(TENANT, USER, CONVERSATION, user_turns=10)
            is False
        )

    async def test_env_trigger_fires_only_on_every_tenth_turn(self) -> None:
        """阈值触发：只在第 10 的整数倍用户轮次入队一次。"""
        harness = _Harness()
        assert (
            await harness.service.maybe_enqueue_extract(TENANT, USER, "conv_x", user_turns=9)
            is False
        )
        assert (
            await harness.service.maybe_enqueue_extract(TENANT, USER, "conv_x", user_turns=10)
            is True
        )
        assert (
            await harness.service.maybe_enqueue_extract(TENANT, USER, "conv_x", user_turns=20)
            is False
        )
        queued = await harness.jobs.list_by_status(TENANT, "queued")
        assert len(queued) == 1
        assert queued[0].payload["conversation_id"] == "conv_x"
        assert queued[0].max_attempts == 1

    async def test_trigger_disabled_when_every_turns_is_zero(self) -> None:
        """`memory_extract_every_turns=0` 时不自动触发（只保留手动）。"""
        settings = Settings(env="test", business_store="memory", memory_extract_every_turns=0)
        harness = _Harness(settings=settings)
        assert (
            await harness.service.maybe_enqueue_extract(TENANT, USER, "conv_x", user_turns=10)
            is False
        )


class TestApiPath:
    """请求链路：抽取触发绝不把对话打红。"""

    async def test_generate_succeeds_when_enqueue_fails(
        self,
        async_client: Any,
        auth_headers: dict[str, str],
        make_idempotency_headers: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """入队抛错 → 每一轮对话仍是 `code=0`（D-1 的对话侧判据）。"""
        _enable_extract_app(monkeypatch)

        async def broken_enqueue(self: Any, record: Any) -> Any:
            raise RuntimeError("queue is down")

        monkeypatch.setattr(InMemoryJobStore, "enqueue", broken_enqueue)
        for index in range(10):
            response = await async_client.post(
                "/api/v1/model/generate",
                json={
                    "conversation_id": "conv_trigger_fail",
                    "profile": "general",
                    "messages": [{"role": "user", "content": f"第{index}轮的内容"}],
                },
                headers={**auth_headers, **make_idempotency_headers(f"degrade-{index}")},
            )
            assert response.status_code == 200
            assert response.json()["code"] == 0

    async def test_tenth_user_turn_enqueues_extract_job(
        self,
        async_client: Any,
        auth_headers: dict[str, str],
        make_idempotency_headers: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_extract_app(monkeypatch)

        """累计第 10 轮用户消息时入队一个 `memory_extract` 任务（去重键=会话 ID）。

        会话与任务都落在 `PRIVATE_TENANT`：`settings` 夹具是 `deploy_mode=private`
        （单租户），`_tenant_of()` 因此回落 `default`，与 `X-Tenant-Id` 头无关。
        """
        for index in range(10):
            response = await async_client.post(
                "/api/v1/model/generate",
                json={
                    "conversation_id": "conv_trigger_ok",
                    "profile": "general",
                    "messages": [{"role": "user", "content": f"第{index}轮的内容"}],
                },
                headers={**auth_headers, **make_idempotency_headers(f"trigger-{index}")},
            )
            assert response.json()["code"] == 0

        jobs = await get_job_store().list_by_status(PRIVATE_TENANT, "queued")
        assert len(jobs) == 1
        assert jobs[0].kind == "memory_extract"
        assert jobs[0].dedupe_key == "conv_trigger_ok"
        assert jobs[0].max_attempts == 1


class TestWiring:
    """装配：注册表里必须有 `memory_extract`，否则任务会被反复判失败。"""

    def test_default_runner_registers_memory_extract(self) -> None:
        """`build_default_runner` 注册的两个处理器（抽取 + 保留期清理）。

        **为什么把期望写死成完整列表**：注册表是"新增任务类型的唯一入口"，
        少注册会让任务被反复判失败，多注册会让没人负责的 kind 躺在队列里。
        因此这里断言**精确集合**而不是"包含 memory_extract"——
        阶段四 T4-05.3 加 `retention_cleanup` 时这条用例就该红一次，改的人必须
        知道自己动了装配点（顺带确认 Runner 的重试上限是 3）。
        """
        runner = build_default_runner(Settings(env="test", business_store="memory"))
        assert runner.registered_kinds() == ["memory_extract", "retention_cleanup"]

    async def test_runner_executes_extraction_end_to_end(self) -> None:
        """任务经 Runner 跑通：事实落库、任务 succeeded。"""
        harness = _Harness(chat=FakeChat([SIX_FACTS[0]]))
        await harness.seed([("user", "我叫赵工")])
        assert await harness.service.maybe_enqueue_extract(
            TENANT, USER, CONVERSATION, user_turns=10
        )

        runner = JobRunner(
            harness.settings,
            store=harness.jobs,
            worker_id="test-worker",
            task_timeout_seconds=1.0,
        )
        runner.register(
            "memory_extract",
            partial(handle_memory_extract, service=harness.service),
            max_attempts=1,
        )

        assert await runner.tick() == 1
        succeeded = await harness.jobs.list_by_status(TENANT, "succeeded")
        assert len(succeeded) == 1
        stored = await harness.memories.list_active(TENANT, USER)
        assert [item.content for item in stored] == ["我叫赵工"]


class TestAppKeyRequired:
    """抽取专用应用未配置时：不抽取、不入队，只打一次 WARNING。

    刻意**不回落 `dify_default_app_key`**：默认对话应用带自己的系统提示与 RAG 上下文，
    拿它做 JSON 抽取大概率产出非结构化文本，然后静默走 D-1——现象是"配了但没记忆"。
    """

    async def test_missing_app_key_disables_extraction(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """无 key → `maybe_enqueue_extract` 返回 False、无任务、日志恰好 1 条。"""
        settings = Settings(env="test", business_store="memory", memory_extract_every_turns=10)
        memories = InMemoryMemoryStore()
        messages = InMemoryMessageStore()
        jobs = InMemoryJobStore()
        service = MemoryService(
            settings,
            memories=memories,
            messages=messages,
            jobs=jobs,
            embedding=NullEmbeddingProvider(),
        )  # extractor 缺省 = 生产实现（DifyChatCompletion，无 key → unavailable）
        await messages.append(
            MessageRecord(
                tenant_id=TENANT,
                conversation_id=CONVERSATION,
                user_id=USER,
                role="user",
                content="我叫赵工",
                status="settled",
            )
        )

        first = await service.maybe_enqueue_extract(TENANT, USER, CONVERSATION, user_turns=10)
        # 第二次调用不得再刷同一条 WARNING
        second = await service.maybe_enqueue_extract(TENANT, USER, CONVERSATION, user_turns=20)
        assert first is False
        assert second is False
        assert await jobs.list_by_status(TENANT, "queued") == []

        result = await service.extract_and_merge(TENANT, USER, CONVERSATION)
        assert result.skipped is True
        assert await memories.count_active(TENANT, USER) == 0

        output = capsys.readouterr().out
        assert output.count("memory_extract_disabled_no_app_key") == 1

    async def test_configured_app_key_keeps_pipeline_enabled(self) -> None:
        """配了专用 key → `available=True`（对照组，防"一刀切关掉"）。"""
        from app.services.memory_extractor import DifyChatCompletion

        disabled = DifyChatCompletion(Settings(env="test", dify_default_app_key="app-default"))
        enabled = DifyChatCompletion(Settings(env="test", dify_memory_app_key=TEST_MEMORY_APP_KEY))

        assert disabled.available is False  # 有默认对话 key 也不算配了抽取
        assert enabled.available is True


class TestExtractorUnit:
    """抽取器本身的纯函数级判据。"""

    async def test_prompt_lists_only_given_messages(self) -> None:
        """提示词包含全部给定消息（顺序为时间先后）。"""
        extractor = MemoryExtractor(FakeChat([]))
        prompt = extractor.build_prompt(["第一句", "第二句"])
        assert prompt.index("第一句") < prompt.index("第二句")

    async def test_fenced_json_is_parsed(self) -> None:
        """模型输出带 ```json 围栏时仍可解析（真实模型经常这样）。"""
        chat = FakeChat(
            raw='```json\n[{"category":"org","content":"我在采购部","confidence":"high"}]\n```'
        )
        extractor = MemoryExtractor(chat)
        outcome = await extractor.extract(["我调到采购部了"])
        assert [fact.content for fact in outcome.facts] == ["我在采购部"]

    async def test_unparsable_output_raises(self) -> None:
        """输出不是 JSON 数组 → 抛 `ChatCompletionError`（由任务层降级）。"""
        extractor = MemoryExtractor(FakeChat(raw="我不太确定"))
        with pytest.raises(ChatCompletionError):
            await extractor.extract(["随便说点什么"])

    async def test_chinese_category_alias_is_accepted(self) -> None:
        """模型返回中文类别名时归一化，避免整批丢弃。"""
        chat = FakeChat(raw=json.dumps([{"category": "偏好", "content": "汇报要表格式"}]))
        outcome = await MemoryExtractor(chat).extract(["汇报要表格式"])
        assert outcome.facts == [
            FactCandidate(category="preference", content="汇报要表格式", confidence="high")
        ]
