"""跨会话「记住 → 召回 → 删除后失效」的端到端链路（阶段四 T4-03.3）。

**验的是哪个用户旅程的哪个判定点**

| 步骤 | 用户看到的事实 | 判定点 |
| --- | --- | --- |
| ① 会话 A 交代事情 | 助手正常回答（抽取异步发生） | 第 10 轮用户消息触发一次入队 |
| ② 任务执行抽取 | ——（后台） | 事实落 `user_memory`；敏感信息**丢弃且不落库** |
| ③ 新开会话 B 提问 | 助手直接知道"汇报要表格式" | 新会话（`server_empty`）仍召回 **≥2 项** |
| ④ 「我的记忆」里删掉一条 | 该条不再被提起，其余仍在 | 删除后**不再召回**，且只影响被删那条 |

**为什么这条链路必须有一个"跨会话"的用例**（不是重复单测）：

- `tests/unit/test_memory_extract.py` 验"抽取 → 落库"（起点）；
- `tests/unit/test_memory_recall_degrade.py` 验"召回 → 注入"（终点），但它用手工
  `seed_fact()` 造记忆，**跳过了起点**；
- `tests/integration/test_memory_endpoints.py` 验 MM1-MM4（列表/删除/开关），
  不验"删完之后下一轮对话还会不会提起"。

三段各自绿、接起来断掉，正是记忆域最典型的缺陷形态（"记了但不记得"／
"删了还在说"）。本文件把三段串成一条：**真实 MemoryService + 真实 MemoryExtractor
+ 真实 JobRunner + 真实 ContextAssembler**，只把「模型」与「向量」两个外部世界
换成确定性替身（注入点就是生产用的那一个）。

**为什么本文件不用 `tests/e2e/conftest.py` 的真库装配**（读这段，否则会以为走错了目录）：

e2e 的装配把下游整体关掉（`OA_DOWNSTREAM_ENABLED=false`）且不配抽取专用应用，
而记忆链路在"未配抽取应用"时**整条不启动**（`warn_missing_memory_app_key_once`，
`test_memory_extract.py::TestAppKeyRequired` 已把这条钉死）——留着真库装配就只能
测到"没有记忆"，测不到"记住并召回"。因此本文件显式覆盖 e2e conftest 的
`_purge_tenant_rows`（它依赖 `pg_dsn`，会让整个文件 skip），改为存储层自带隔离：
每个用例新建 `InMemoryMemoryStore` / `InMemoryMessageStore` / `InMemoryJobStore`，
用例之间没有共享状态，也就不需要"清理行"。

底座（PostgreSQL 的 `vector` 列、RLS、`memory_embedding` 外键）由
`tests/integration/test_memory_schema_pg.py` 与 `test_memory_endpoints.py` 覆盖，
不在本文件重复。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from functools import partial

import pytest

from app.business.context_assembler import ContextAssembler
from app.business.memory_service import MemoryService
from app.core.config import Settings
from app.db.repositories.conversation import InMemoryMessageStore, MessageRecord
from app.db.repositories.job_repo import InMemoryJobStore
from app.db.repositories.memory import InMemoryMemoryStore
from app.services.memory_embedding import EmbeddingProvider
from app.services.memory_extractor import MemoryExtractor
from app.workers.job_runner import JobRunner
from app.workers.tasks.memory_extract import handle_memory_extract
from tests.conftest import TEST_MEMORY_APP_KEY, TEST_TENANT_ID, TEST_USER_ID

pytestmark = [pytest.mark.e2e, pytest.mark.memory]

TENANT = TEST_TENANT_ID
USER = TEST_USER_ID
OTHER_USER = "usr_e2e_recall_other"

#: 会话 A（交代事情的地方）与会话 B（跨会话验证的地方）
CONVERSATION_A = "conv_e2e_recall_a"
CONVERSATION_B = "conv_e2e_recall_b"

#: 抽取替身返回的事实：两条可召回 + 一条敏感（手机号，必须被丢弃）
FACTS: list[dict[str, str]] = [
    {"category": "preference", "content": "汇报要表格式", "confidence": "high"},
    {"category": "org", "content": "我在供应链部", "confidence": "high"},
    {"category": "identity", "content": "手机号13800138000", "confidence": "high"},
]

#: 敏感原文（断言它不出现在任何存储与任何提示词里）
SECRET = "13800138000"


@pytest.fixture(autouse=True)
def _purge_tenant_rows() -> Iterator[None]:
    """覆盖 e2e conftest 的同名 PG 清理夹具（理由见模块说明末段）。

    覆盖是**显式**的：同名 fixture 在模块层定义即生效，conftest 里那个依赖
    `e2e_settings → pg_dsn` 的实现不再参与本文件。这样做的代价是本文件拿不到
    "真库"这层保障，收益是这条链路在没有 PG 的环境里也能被验到——
    而 PG 专有的部分（`vector` 列、RLS）另有专门的用例，不缺覆盖。

    Yields:
        Iterator[None]: 无值。
    """
    yield


class FakeChat:
    """抽取用的补全替身（返回固定 JSON，记录提示词）。

    Attributes:
        prompts: 每次收到的提示词（用于证明"敏感原文不进提示词之外的地方"）。
    """

    def __init__(self, facts: Sequence[dict[str, str]]) -> None:
        """初始化。

        Args:
            facts: 要返回的事实列表。
        """
        self._facts = list(facts)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        """返回固定 JSON（记录提示词）。

        Args:
            prompt: 提示词。

        Returns:
            str: JSON 数组。
        """
        self.prompts.append(prompt)
        return json.dumps(self._facts, ensure_ascii=False)


class FakeEmbedding:
    """向量替身（确定性、无网络）。

    Attributes:
        无（模型标识固定）。
    """

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
            int: 4。
        """
        return 4

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """生成确定性向量。

        Args:
            texts: 待向量化文本。

        Returns:
            list[list[float]]: 向量列表。
        """
        return [[float(len(text) % 7), 1.0, 0.5, 0.25] for text in texts]


class _Journey:
    """整个旅程的装配体（内存存储 + 替身模型）。

    Attributes:
        settings: 配置。
        memories: 记忆存储。
        messages: 消息存储（会话 A 的 10 轮历史写在这里）。
        jobs: 任务存储。
        service: 记忆服务（真实实现）。
    """

    def __init__(self, *, facts: Sequence[dict[str, str]] = ()) -> None:
        """初始化。

        Args:
            facts: 抽取替身要返回的事实。
        """
        self.settings = Settings(
            env="test",
            business_store="memory",
            dify_memory_app_key=TEST_MEMORY_APP_KEY,
            memory_extract_every_turns=10,
            memory_recall_top_k=8,
            memory_recall_timeout_seconds=0.3,
        )
        self.memories = InMemoryMemoryStore()
        self.messages = InMemoryMessageStore()
        self.jobs = InMemoryJobStore()
        self.chat = FakeChat(facts)
        embedding: EmbeddingProvider = FakeEmbedding()
        self.service = MemoryService(
            self.settings,
            memories=self.memories,
            messages=self.messages,
            jobs=self.jobs,
            extractor=MemoryExtractor(self.chat),
            embedding=embedding,
        )

    async def seed_turns(self, conversation_id: str, count: int) -> None:
        """写入若干轮「用户 + 助手」消息（抽取的输入）。

        Args:
            conversation_id: 会话 ID。
            count: 用户轮数。
        """
        for index in range(count):
            for role, content in (
                ("user", f"第{index}轮：我在整理供应链的报表"),
                ("assistant", "好的，我记下了"),
            ):
                await self.messages.append(
                    MessageRecord(
                        tenant_id=TENANT,
                        conversation_id=conversation_id,
                        user_id=USER,
                        role=role,
                        content=content,
                        status="settled",
                    )
                )

    def runner(self) -> JobRunner:
        """按生产装配构造 Runner（只注册 `memory_extract`）。

        Returns:
            JobRunner: 可 `tick()` 的 Runner。
        """
        runner = JobRunner(
            self.settings,
            store=self.jobs,
            worker_id="e2e-memory",
            task_timeout_seconds=2.0,
        )
        runner.register(
            "memory_extract",
            partial(handle_memory_extract, service=self.service),
            max_attempts=1,
        )
        return runner

    def assembler(self) -> ContextAssembler:
        """构造注入了本旅程记忆服务的组装器。

        Returns:
            ContextAssembler: 组装器。
        """
        return ContextAssembler(self.settings, messages=self.messages, memory=self.service)


async def test_memory_is_remembered_across_sessions_and_forgotten_after_delete() -> None:
    """① 会话 A 交代 → ② 抽取落库 → ③ 会话 B 召回 ≥2 项 → ④ 删除后不再召回。"""
    journey = _Journey(facts=FACTS)
    await journey.seed_turns(CONVERSATION_A, 10)

    # ---- ① 第 10 轮触发入队（阈值来自配置，不是硬编码） -------------------
    queued = await journey.service.maybe_enqueue_extract(
        TENANT, USER, CONVERSATION_A, user_turns=10
    )
    assert queued is True, "第 10 轮用户消息必须触发一次抽取入队"

    # ---- ② 任务经 Runner 跑完（与生产同一条路径） -------------------------
    runner = journey.runner()
    assert await runner.tick() == 1, "Runner 应认领并执行这一个抽取任务"
    succeeded = await journey.jobs.list_by_status(TENANT, "succeeded")
    assert [job.kind for job in succeeded] == ["memory_extract"]

    stored = await journey.memories.list_active(TENANT, USER)
    contents = [item.content for item in stored]
    assert len(contents) >= 2, f"跨会话召回要求至少 2 项，实际落库 {contents}"
    assert "汇报要表格式" in contents
    assert "我在供应链部" in contents

    # 敏感信息：丢弃且不落库（`dropped_sensitive` 的计数由单测精确断言，
    # 这里只验"用户可见的那一侧"——库里搜不到原文）
    assert all(SECRET not in content for content in contents), "手机号被写进了记忆"

    # ---- ③ 新会话（无历史）仍召回 ----------------------------------------
    assembled = await journey.assembler().build(
        TENANT,
        USER,
        CONVERSATION_B,
        current_user_text="帮我准备下周的汇报",
    )
    assert assembled.history_source == "server_empty", "会话 B 必须是全新会话（无历史）"
    assert assembled.transcript_turns == 0
    assert assembled.injected_memory_count >= 2, (
        f"跨会话召回不足 2 项，实际 {assembled.injected_memory_count}"
    )
    assert "汇报要表格式" in assembled.query_text
    assert "我在供应链部" in assembled.query_text
    assert SECRET not in assembled.query_text
    # 记忆块必须排在本轮输入**之前**（读取顺序 = 设定 → 历史 → 本轮问题）
    assert assembled.query_text.index("汇报要表格式") < assembled.query_text.index(
        "帮我准备下周的汇报"
    )

    # 别人的记忆不该出现在我的上下文里（跨用户隔离的最后一道闸）
    other = await journey.assembler().build(
        TENANT,
        OTHER_USER,
        "conv_e2e_recall_other",
        current_user_text="帮我准备下周的汇报",
    )
    assert other.injected_memory_count == 0, "别的用户看到了我的记忆"
    assert "汇报要表格式" not in other.query_text

    # ---- ④ 删除一条 → 不再召回（且只影响这一条） -------------------------
    target = next(item for item in stored if item.content == "汇报要表格式")
    assert await journey.service.delete_memory(TENANT, USER, target.memory_id) is True

    after = await journey.assembler().build(
        TENANT,
        USER,
        "conv_e2e_recall_c",
        current_user_text="帮我准备下周的汇报",
    )
    assert "汇报要表格式" not in after.query_text, "删掉的记忆又出现在上下文里了"
    assert "我在供应链部" in after.query_text, "删一条不该连带清掉其他记忆"
    assert after.injected_memory_count >= 1

    # 派生数据（向量）随记忆一起消失：残留向量会让"删了还在召回"变成幽灵
    embeddings = await journey.memories.list_embeddings(TENANT, [target.memory_id])
    assert not embeddings, f"删除后仍残留向量：{embeddings}"


async def test_sensitive_fact_is_counted_dropped_and_never_stored() -> None:
    """敏感信息：`dropped_sensitive>0`、不落库、不进入下一轮的上下文。"""
    journey = _Journey(facts=FACTS)
    await journey.seed_turns(CONVERSATION_A, 10)

    result = await journey.service.extract_and_merge(TENANT, USER, CONVERSATION_A)

    assert result.dropped_sensitive > 0, "手机号必须被判定为敏感并丢弃"
    assert result.extracted == 2, f"可召回事实应为 2 条，实际 {result.extracted}"

    stored = await journey.memories.list_active(TENANT, USER)
    assert SECRET not in " ".join(item.content for item in stored)

    assembled = await journey.assembler().build(
        TENANT, USER, CONVERSATION_B, current_user_text="我的手机号是多少"
    )
    assert SECRET not in assembled.query_text, "敏感原文进入了模型上下文"


async def test_closing_switches_stops_recall_without_touching_stored_facts() -> None:
    """关掉记忆开关 → 不再召回，但库里的事实**不被删除**（用户可随时开回来）。

    为什么这条也在 e2e 里：开关的读路径（`user_memory_setting`）与召回是同一条
    链路的两端。只测"设开关成功"（MM4）会漏掉"开关真的影响了召回"——
    那正是用户投诉"我关了它还在提"的那种缺陷。
    """
    journey = _Journey(facts=FACTS)
    await journey.seed_turns(CONVERSATION_A, 10)
    await journey.service.extract_and_merge(TENANT, USER, CONVERSATION_A)
    before = await journey.memories.list_active(TENANT, USER)
    assert len(before) >= 2

    await journey.service.update_setting(TENANT, USER, enabled=False)

    assembled = await journey.assembler().build(
        TENANT, USER, CONVERSATION_B, current_user_text="帮我准备下周的汇报"
    )
    assert assembled.injected_memory_count == 0, "关掉开关后仍在注入记忆"

    # 事实仍在库里（关闭 ≠ 清空）：重新打开即可恢复
    assert len(await journey.memories.list_active(TENANT, USER)) == len(before)
    await journey.service.update_setting(TENANT, USER, enabled=True)
    reopened = await journey.assembler().build(
        TENANT, USER, "conv_e2e_recall_d", current_user_text="帮我准备下周的汇报"
    )
    assert reopened.injected_memory_count >= 2
