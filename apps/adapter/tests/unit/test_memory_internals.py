"""记忆内核的细粒度单元验收（阶段四 T4-03 收尾补齐分支覆盖）。

覆盖那些"HTTP/真库用例走不到、但生产一定会执行"的分支：

1. 抽取应用的生产实现（`DifyChatCompletion`）：成功、上游 4xx/5xx、空答案、异常包装、释放；
2. 向量端口的默认（不可用）实现与配置不齐时的告警回落；
3. 内存仓储的分页 / 相似检索 / 按类硬删；
4. 服务层的参数校验、审计失败吞掉、召回的异常与超时降级；
5. 任务引擎的非法参数、未知任务、停机异常等边角。

这些路径都不是"为了覆盖率而写"：每一条都对应一种真实故障（上游 500、配置漏填、
审计库不可用、向量库抖动、后台协程异常退出）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from app.business.memory_service import MemoryService
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.repositories.conversation import InMemoryMessageStore, MessageRecord
from app.db.repositories.job_repo import InMemoryJobStore, JobRecord
from app.db.repositories.memory import (
    MEMORY_CATEGORIES,
    InMemoryMemoryStore,
    MemoryRecord,
    MemorySettingRecord,
    new_memory_id,
)
from app.services.memory_embedding import (
    DEFAULT_DIM,
    EmbeddingUnavailableError,
    NullEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
)
from app.services.memory_extractor import (
    ChatCompletionError,
    DifyChatCompletion,
    MemoryExtractor,
    contains_sensitive,
    content_similarity,
    normalized_hash,
)
from app.workers.job_runner import JobRunner, get_job_runner, reset_job_runner, set_job_runner
from tests.conftest import TEST_MEMORY_APP_KEY, TEST_TENANT_ID, TEST_USER_ID

pytestmark = [pytest.mark.unit, pytest.mark.memory]

TENANT = TEST_TENANT_ID
USER = TEST_USER_ID

#: 抽取用例统一使用的会话 ID（"消息写哪、抽取读哪"必须一致，否则会被判"没有用户消息"）
CONV = "conv_internals"


class _FakeResponse:
    """`DifyResponse` 替身。"""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.payload = payload


class _FakeDifyClient:
    """底座客户端替身（记录调用并可配置返回/抛错）。"""

    def __init__(
        self, *, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self._response = response or _FakeResponse(200, {"answer": "[]"})
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def post(self, path: str, payload: dict[str, Any], **_: Any) -> _FakeResponse:
        """记录并返回预设响应。"""
        self.calls.append((path, payload))
        if self._error is not None:
            raise self._error
        return self._response

    async def aclose(self) -> None:
        """标记已关闭。"""
        self.closed = True


def _settings(**overrides: Any) -> Settings:
    """构造配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    base: dict[str, Any] = {"env": "test", "business_store": "memory"}
    base.update(overrides)
    return Settings(**base)


class TestDifyChatCompletion:
    """生产抽取实现：成功、上游错误、空答案、释放。"""

    def test_app_key_and_available(self) -> None:
        """有 key 才 available；`app_key` 只读专用项，**不回落**默认对话应用。"""
        enabled = DifyChatCompletion(_settings(dify_memory_app_key=TEST_MEMORY_APP_KEY))
        fallback_only = DifyChatCompletion(_settings(dify_default_app_key="app-default"))

        assert enabled.app_key == TEST_MEMORY_APP_KEY
        assert enabled.available is True
        assert fallback_only.app_key == ""
        assert fallback_only.available is False

    async def test_complete_without_key_raises(self) -> None:
        """没配 key 时调用直接失败（由任务层按 D-1 收口）。"""
        completion = DifyChatCompletion(_settings())
        with pytest.raises(ChatCompletionError, match="not configured"):
            await completion.complete("prompt")

    async def test_complete_returns_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常路径：走 blocking 模式并返回 `answer`。"""
        fake = _FakeDifyClient(response=_FakeResponse(200, {"answer": '[{"category":"org"}]'}))
        monkeypatch.setattr(DifyChatCompletion, "_get_client", lambda self: fake)
        completion = DifyChatCompletion(_settings(dify_memory_app_key=TEST_MEMORY_APP_KEY))

        answer = await completion.complete("请抽取")

        assert answer == '[{"category":"org"}]'
        path, payload = fake.calls[0]
        assert path == "/v1/completion-messages"
        assert payload["response_mode"] == "blocking"
        assert payload["user"] == "memory-extractor"

    async def test_complete_wraps_downstream_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上游异常被包装成 `ChatCompletionError`（不泄露原始堆栈给上层）。"""
        fake = _FakeDifyClient(error=RuntimeError("connection reset"))
        monkeypatch.setattr(DifyChatCompletion, "_get_client", lambda self: fake)
        completion = DifyChatCompletion(_settings(dify_memory_app_key=TEST_MEMORY_APP_KEY))

        with pytest.raises(ChatCompletionError, match="downstream call failed"):
            await completion.complete("请抽取")

    async def test_complete_rejects_http_error_and_empty_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """4xx/5xx 与空答案都算失败（空答案会被当成"没抽到"，掩盖真实故障）。"""
        completion = DifyChatCompletion(_settings(dify_memory_app_key=TEST_MEMORY_APP_KEY))

        monkeypatch.setattr(
            DifyChatCompletion,
            "_get_client",
            lambda self: _FakeDifyClient(response=_FakeResponse(502, {})),
        )
        with pytest.raises(ChatCompletionError, match="status 502"):
            await completion.complete("请抽取")

        monkeypatch.setattr(
            DifyChatCompletion,
            "_get_client",
            lambda self: _FakeDifyClient(response=_FakeResponse(200, {"answer": "   "})),
        )
        with pytest.raises(ChatCompletionError, match="empty answer"):
            await completion.complete("请抽取")

    async def test_get_client_reuses_single_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """客户端按需创建并复用（每次新建会堆积 TIME_WAIT）。"""
        from app.clients.dify_client import DifyClient

        fake = _FakeDifyClient()
        created: list[str] = []

        def _for_app_key(cls: Any, key: str, settings: Any = None) -> Any:
            created.append(key)
            return fake

        monkeypatch.setattr(DifyClient, "for_app_key", classmethod(_for_app_key))
        completion = DifyChatCompletion(_settings(dify_memory_app_key=TEST_MEMORY_APP_KEY))

        first = completion._get_client()  # noqa: SLF001
        second = completion._get_client()  # noqa: SLF001

        assert first is fake and second is fake
        assert created == [TEST_MEMORY_APP_KEY]

        await completion.aclose()
        assert fake.closed is True


class TestEmbeddingPort:
    """向量端口的默认实现与配置回落。"""

    async def test_null_provider_is_explicitly_unavailable(self) -> None:
        """不可用实现必须**抛**而不是返回零向量（零向量会被误判为"不相似"）。"""
        provider = NullEmbeddingProvider()

        assert provider.available is False
        assert provider.model == ""
        assert provider.dim == DEFAULT_DIM
        with pytest.raises(EmbeddingUnavailableError):
            await provider.embed(["x"])

    def test_cosine_similarity_edges(self) -> None:
        """维度不一致 / 空向量 / 相同向量 / 正交向量。"""
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_provider_selection_never_fakes_availability(self) -> None:
        """关闭 → 不可用；开了但没配齐 → 打 WARNING 后仍不可用；配齐 → 仍不假装可用。"""
        assert get_embedding_provider(_settings()).available is False

        misconfigured = _settings(
            memory_embedding_enabled=True, memory_embedding_model="text-embedding-3-small"
        )
        assert get_embedding_provider(misconfigured).available is False

        configured = _settings(
            memory_embedding_enabled=True,
            memory_embedding_model="text-embedding-3-small",
            memory_embedding_base_url="https://example.invalid/v1",
            memory_embedding_api_key="k",
        )
        assert get_embedding_provider(configured).available is False


class TestPureFunctions:
    """抽取器的纯函数边界。"""

    def test_hash_and_similarity_edges(self) -> None:
        """空正文的空键、空串相似度、完全相同相似度。"""
        assert normalized_hash("identity", "   ") == ""
        assert content_similarity("", "abc") == 0.0
        assert content_similarity("abc", "") == 0.0
        assert content_similarity("abc", "abc") == 1.0
        assert content_similarity("abc", "abd") < 1.0

    def test_contains_sensitive_on_empty_text(self) -> None:
        """空文本不命中（不要因为空值抛异常）。"""
        assert contains_sensitive("") is False

    def test_extractor_available_tracks_chat(self) -> None:
        """生产实现的可用性透传到抽取器（缺 key → 不可用）。"""

        class _Unavailable:
            available = False

            async def complete(self, prompt: str) -> str:
                return "[]"

        assert MemoryExtractor(_Unavailable()).available is False  # type: ignore[arg-type]
        assert MemoryExtractor(_FakeChat()).available is True


class _FakeChat:
    """可配置返回内容的补全替身（默认返回空数组）。"""

    available = True

    def __init__(self, raw: str = "[]") -> None:
        self._raw = raw

    async def complete(self, prompt: str) -> str:
        """返回预设内容。"""
        return self._raw


class _FastEmbedding:
    """即时返回确定向量的端口替身（"向量可用"分支）。"""

    available = True
    model = "fast-embedding"
    dim = 2

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """返回维度 2 的单位向量。"""
        return [[1.0, 0.0] for _ in texts]


def _user_message(content: str, *, conversation_id: str = "conv_internals") -> MessageRecord:
    """构造一条用户消息（抽取的输入来源）。

    Args:
        content: 正文。
        conversation_id: 会话 ID。

    Returns:
        MessageRecord: 消息行。
    """
    return MessageRecord(
        tenant_id=TENANT,
        conversation_id=conversation_id,
        user_id=USER,
        role="user",
        content=content,
        status="settled",
    )


class TestInMemoryRepoGaps:
    """内存仓储里 HTTP 用例走不到的分支。"""

    async def test_list_page_and_search_similar(self) -> None:
        """分页切片、按类别过滤、向量检索（无向量的行不参与）。"""
        store = InMemoryMemoryStore()
        first = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="preference",
                content="汇报要表格式",
                normalized_hash=normalized_hash("preference", "汇报要表格式"),
            )
        )
        second = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="identity",
                content="我叫赵工",
                normalized_hash=normalized_hash("identity", "我叫赵工"),
            )
        )
        assert first is not None and second is not None
        await store.upsert_embedding(
            TENANT, first.memory_id, model="fake", dim=2, embedding=[1.0, 0.0]
        )

        page_rows, total = await store.list_page(TENANT, USER, page=1, page_size=1)
        assert total == 2
        assert len(page_rows) == 1

        filtered_rows, filtered_total = await store.list_page(
            TENANT, USER, category="identity", page=1, page_size=10
        )
        assert filtered_total == 1
        assert filtered_rows[0].memory_id == second.memory_id

        similar = await store.search_similar(TENANT, USER, [1.0, 0.0], limit=5)
        assert [item.memory_id for item in similar] == [first.memory_id]  # 无向量的行被跳过

        limited = await store.search_similar(
            TENANT, USER, [0.0, 1.0], limit=5, categories=["identity"]
        )
        assert limited == []

    async def test_delete_by_category_and_boundary(self) -> None:
        """按类删只删该类；别人的记忆删不掉；清空走同一入口。"""
        store = InMemoryMemoryStore()
        keep = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="identity",
                content="我叫赵工",
                normalized_hash=normalized_hash("identity", "我叫赵工"),
            )
        )
        drop = await store.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="preference",
                content="汇报要表格式",
                normalized_hash=normalized_hash("preference", "汇报要表格式"),
            )
        )
        assert keep is not None and drop is not None

        removed = await store.delete_by_category(TENANT, USER, category="preference")
        assert removed == 1
        assert await store.count_active(TENANT, USER) == 1

        # 别人的 ID 删不掉（个人边界）
        assert await store.delete(TENANT, "usr_other", [keep.memory_id]) == 0
        assert await store.delete_by_category(TENANT, USER) == 1
        assert await store.count_active(TENANT, USER) == 0

    async def test_setting_roundtrip_and_vector_ready(self) -> None:
        """开关写入可读回；内存实现恒报告"向量可用"。"""
        store = InMemoryMemoryStore()
        default = await store.get_setting(TENANT, USER)
        assert default.enabled is True

        saved = await store.upsert_setting(
            MemorySettingRecord(
                tenant_id=TENANT, user_id=USER, enabled=False, category_switches={"org": False}
            )
        )
        assert saved.enabled is False
        assert (await store.get_setting(TENANT, USER)).category_switches == {"org": False}
        assert await store.vector_ready() is True


class TestServiceGaps:
    """服务层的参数校验、审计容错与召回降级。"""

    def _service(self, *, embedding: Any | None = None, audit: Any | None = None) -> MemoryService:
        """构造服务。

        Args:
            embedding: 向量端口。
            audit: 审计服务替身。

        Returns:
            MemoryService: 服务实例。
        """
        return MemoryService(
            _settings(dify_memory_app_key=TEST_MEMORY_APP_KEY, memory_recall_timeout_seconds=0.05),
            memories=InMemoryMemoryStore(),
            messages=InMemoryMessageStore(),
            jobs=InMemoryJobStore(),
            extractor=MemoryExtractor(_FakeChat()),
            embedding=embedding or NullEmbeddingProvider(),
            audit=audit,
        )

    async def test_get_setting_wrapper(self) -> None:
        """`get_setting` 透传仓储（MM1 与抽取共用同一口径）。"""
        service = self._service()
        assert (await service.get_setting(TENANT, USER)).enabled is True

    async def test_update_setting_rejects_unknown_category(self) -> None:
        """非法类别 → 20001（静默忽略会让用户以为关掉了）。"""
        service = self._service()
        with pytest.raises(ApiError):
            await service.update_setting(TENANT, USER, category_switches={"salary": False})

    async def test_update_setting_keeps_other_switches(self) -> None:
        """增量覆盖：只改传入的键；`enabled=None` 保持原值。"""
        service = self._service()
        await service.update_setting(TENANT, USER, enabled=False)
        saved = await service.update_setting(TENANT, USER, category_switches={"org": False})

        assert saved.enabled is False  # 未被 None 覆盖
        assert saved.category_switches == {"org": False}

    async def test_list_and_delete_reject_unknown_category(self) -> None:
        """MM1 / MM3 的类别校验在服务层也有一份（端点层的检查不能是唯一一道）。"""
        service = self._service()
        with pytest.raises(ApiError):
            await service.list_memories(TENANT, USER, category="salary")
        with pytest.raises(ApiError):
            await service.delete_memories(TENANT, USER, category="salary")

    async def test_delete_memory_missing_returns_false(self) -> None:
        """不存在 / 不属于本人 → False（接口层据此报 18002）。"""
        service = self._service()
        assert await service.delete_memory(TENANT, USER, "mem_missing") is False

    async def test_audit_failure_does_not_block_delete(self) -> None:
        """审计库不可用时删除照常成功（隐私要求"能删掉"，不是"连带审计成功"）。"""

        class _BrokenAudit:
            async def record(self, **_: Any) -> None:
                raise RuntimeError("audit db down")

        service = self._service(audit=_BrokenAudit())
        record = await service._memories.insert(  # noqa: SLF001 - 用例内造数
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="identity",
                content="我叫赵工",
                normalized_hash=normalized_hash("identity", "我叫赵工"),
            )
        )
        assert record is not None

        assert await service.delete_memory(TENANT, USER, record.memory_id) is True
        assert await service._memories.count_active(TENANT, USER) == 0  # noqa: SLF001

    async def test_recall_vector_failure_falls_back(self) -> None:
        """向量调用抛错 → 记 `vector_unavailable` 并走按类别召回。"""

        class _BrokenEmbedding:
            available = True
            model = "broken"
            dim = 2

            async def embed(self, texts: Sequence[str]) -> list[list[float]]:
                raise RuntimeError("vector store down")

        service = self._service(embedding=_BrokenEmbedding())
        record = await service._memories.insert(  # noqa: SLF001
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="identity",
                content="我叫赵工",
                normalized_hash=normalized_hash("identity", "我叫赵工"),
            )
        )
        assert record is not None

        records = await service.recall(TENANT, USER, "帮我写周报")

        assert [item.memory_id for item in records] == [record.memory_id]

    async def test_recall_fallback_timeout_returns_empty(self) -> None:
        """降级路径本身也受预算约束：慢到超时就彻底不注入（D-2）。"""

        class _SlowStore(InMemoryMemoryStore):
            async def list_active(
                self, tenant_id: str, user_id: str, *, category: str = ""
            ) -> list[MemoryRecord]:
                await asyncio.sleep(0.2)
                return []

        service = MemoryService(
            _settings(dify_memory_app_key=TEST_MEMORY_APP_KEY, memory_recall_timeout_seconds=0.05),
            memories=_SlowStore(),
            messages=InMemoryMessageStore(),
            jobs=InMemoryJobStore(),
            extractor=MemoryExtractor(_FakeChat()),
            embedding=NullEmbeddingProvider(),
        )
        assert await service.recall(TENANT, USER, "帮我写周报") == []

    async def test_recall_with_empty_query_skips_vector_path(self) -> None:
        """空问题不走向量（省一次调用），直接返回空。"""
        service = self._service(embedding=NullEmbeddingProvider())
        assert await service.recall(TENANT, USER, "   ") == []


class TestJobEngineGaps:
    """任务引擎的边角分支。"""

    def test_register_rejects_illegal_max_attempts(self) -> None:
        """`max_attempts < 1` 属编码错误，构造时就炸。"""
        runner = JobRunner(_settings(background_jobs_enabled=False), store=InMemoryJobStore())

        async def handler(_job: JobRecord) -> None:
            return None

        with pytest.raises(ValueError, match="max_attempts"):
            runner.register("memory_extract", handler, max_attempts=0)

    def test_worker_id_and_registered_kinds(self) -> None:
        """持有者标识可读、注册表按字典序输出。"""
        runner = JobRunner(
            _settings(background_jobs_enabled=False), store=InMemoryJobStore(), worker_id="w-1"
        )

        async def handler(_job: JobRecord) -> None:
            return None

        runner.register("memory_extract", handler)
        assert runner.worker_id == "w-1"
        assert runner.registered_kinds() == ["memory_extract"]

    async def test_inmemory_store_rejects_illegal_kind(self) -> None:
        """非法 kind 在入队时被拦（内存实现无 CHECK 约束，需自己拦）。"""
        store = InMemoryJobStore()
        with pytest.raises(ValueError, match="illegal job kind"):
            await store.enqueue(JobRecord(tenant_id=TENANT, kind="nope"))

    async def test_store_handles_unknown_job(self) -> None:
        """对不存在的任务标记成功/失败不抛（幂等补偿路径）。"""
        store = InMemoryJobStore()
        ghost = JobRecord(tenant_id=TENANT, job_id="job_missing", kind="memory_extract")

        await store.mark_succeeded(ghost)
        assert await store.mark_failed(ghost, error="x") == "failed"

    async def test_exhausted_property(self) -> None:
        """`attempts >= max_attempts` 即用尽。"""
        assert JobRecord(attempts=1, max_attempts=1).exhausted is True
        assert JobRecord(attempts=1, max_attempts=3).exhausted is False

    async def test_handler_cancellation_propagates(self) -> None:
        """停机中断（CancelledError）不得被当成任务失败。"""
        store = InMemoryJobStore()
        runner = JobRunner(
            _settings(background_jobs_enabled=False), store=store, worker_id="w-cancel"
        )

        async def handler(_job: JobRecord) -> None:
            raise asyncio.CancelledError

        runner.register("memory_extract", handler, max_attempts=1)
        await store.enqueue(
            JobRecord(tenant_id=TENANT, kind="memory_extract", dedupe_key="conv_cancel")
        )

        with pytest.raises(asyncio.CancelledError):
            await runner.tick()

    async def test_start_is_idempotent(self) -> None:
        """已在跑时再 `start()` 不新建协程。"""
        runner = JobRunner(
            _settings(background_jobs_enabled=False),
            store=InMemoryJobStore(),
            poll_interval_seconds=0.01,
        )
        runner.start()
        first_task = getattr(runner, "_task", None)
        runner.start()
        second_task = getattr(runner, "_task", None)
        try:
            assert first_task is second_task
        finally:
            await runner.stop()

    async def test_stop_tolerates_unexpected_task_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """轮询协程异常结束也要能正常停机（不能因此让 lifespan 失败）。"""
        runner = JobRunner(
            _settings(background_jobs_enabled=False),
            store=InMemoryJobStore(),
            poll_interval_seconds=0.01,
        )

        async def broken_loop() -> None:
            raise RuntimeError("loop crashed")

        monkeypatch.setattr(runner, "_loop", broken_loop)
        runner.start()
        await asyncio.sleep(0.02)

        await runner.stop()  # 不抛

    async def test_loop_survives_tick_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """单轮失败不终止循环（下一轮还会再来）。"""
        runner = JobRunner(
            _settings(background_jobs_enabled=False),
            store=InMemoryJobStore(),
            poll_interval_seconds=0.01,
        )
        calls: list[int] = []

        async def failing_tick() -> int:
            calls.append(1)
            raise RuntimeError("tick boom")

        monkeypatch.setattr(runner, "tick", failing_tick)
        runner.start()
        await asyncio.sleep(0.05)
        await runner.stop()

        assert len(calls) >= 2  # 抛错后仍在继续轮询

    def test_runner_singleton_accessors(self) -> None:
        """单例可取、可替换、可重置。"""
        reset_job_runner()
        created = get_job_runner(_settings(background_jobs_enabled=False))
        assert get_job_runner(_settings(background_jobs_enabled=False)) is created

        replacement = JobRunner(
            _settings(background_jobs_enabled=False), store=InMemoryJobStore(), worker_id="w-2"
        )
        set_job_runner(replacement)
        assert get_job_runner(_settings(background_jobs_enabled=False)) is replacement
        reset_job_runner()


class TestDatetimeRowParsing:
    """仓储行里时间/JSONB 的两种形态（`datetime` 与字符串）都要能吃下。"""

    def test_memory_record_timestamps_from_datetime(self) -> None:
        """`updated_at` 传 `datetime` 时规范成 ISO（asyncpg 真实返回类型）。"""
        store = InMemoryMemoryStore()
        assert store is not None  # 仅用于保持导入被使用
        stamp = datetime(2026, 11, 1, 9, 0, 0, tzinfo=UTC)
        from app.db.repositories.memory.postgres import _timestamp

        assert _timestamp(stamp) == "2026-11-01T09:00:00Z"
        assert _timestamp(None) == ""
        assert _timestamp("2026-11-01T09:00:00+00:00") == "2026-11-01T09:00:00Z"


class TestExtractorEdgeCases:
    """抽取器剩余分支：围栏/字典包装、非字典条目、同批重复、空输入。"""

    def test_missing_key_warning_is_skipped_when_configured(self) -> None:
        """配了 key 时告警函数直接返回 False（不写日志）。"""
        from app.services.memory_extractor import warn_missing_memory_app_key_once

        assert warn_missing_memory_app_key_once(_settings(dify_memory_app_key="k")) is False

    async def test_extract_supports_dict_wrapper_and_rejects_junk(self) -> None:
        """`{"facts": [...]}` 也能解析；非字典条目与重复条目被计为无效/去重。"""
        chat = _FakeChat(
            raw=(
                '{"facts": ['
                '{"category": "org", "content": "我在供应链部"},'
                '"not-a-dict",'
                '{"category": "org", "content": "我在供应链部"},'
                '{"category": "unknown", "content": "x"}'
                "]}"
            )
        )
        outcome = await MemoryExtractor(chat).extract(["我在供应链部"])

        assert [fact.content for fact in outcome.facts] == ["我在供应链部"]
        assert outcome.dropped_invalid == 2  # 非字典 + 未知类别
        assert outcome.raw_count == 4

    async def test_extract_with_blank_messages_skips_model(self) -> None:
        """全是空白消息时不调模型（省一次上游调用）。"""
        chat = _FakeChat(raw="[]")
        outcome = await MemoryExtractor(chat).extract(["   ", ""])
        assert outcome.facts == []

    async def test_extract_respects_category_allow_list(self) -> None:
        """分类开关关闭时该类不进结果（在抽取器层就过滤，减少后续写入）。"""
        chat = _FakeChat(
            raw='[{"category": "preference", "content": "汇报要表格式"},'
            '{"category": "identity", "content": "我叫赵工"}]'
        )
        outcome = await MemoryExtractor(chat).extract(
            ["我叫赵工，汇报要表格式"], categories={"identity"}
        )

        assert [fact.category for fact in outcome.facts] == ["identity"]
        assert outcome.dropped_invalid == 1

    async def test_extract_without_wrapper_dict_raises(self) -> None:
        """字典里没有已知数组键 → 视为不可解析。"""
        chat = _FakeChat(raw='{"other": 1}')
        with pytest.raises(ChatCompletionError):
            await MemoryExtractor(chat).extract(["随便说说"])

    async def test_max_facts_property(self) -> None:
        """`max_facts` 可读（用于日志与断言）。"""
        assert MemoryExtractor(_FakeChat(), max_facts=3).max_facts == 3

    async def test_aclose_without_client_is_noop(self) -> None:
        """从未建过客户端时 `aclose()` 不抛。"""
        await DifyChatCompletion(_settings(dify_memory_app_key="k")).aclose()

    async def test_complete_passes_through_chat_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """上游抛出的 `ChatCompletionError` 原样透传（不二次包装）。"""
        fake = _FakeDifyClient(error=ChatCompletionError("already wrapped"))
        monkeypatch.setattr(DifyChatCompletion, "_get_client", lambda self: fake)
        completion = DifyChatCompletion(_settings(dify_memory_app_key="k"))

        with pytest.raises(ChatCompletionError, match="already wrapped"):
            await completion.complete("prompt")


class _VectorStore(InMemoryMemoryStore):
    """可控失败/可控返回的内存存储（覆盖服务的容错分支）。"""

    def __init__(
        self,
        *,
        fail_insert: bool = False,
        fail_list_embeddings: bool = False,
        fail_upsert_embedding: bool = False,
        fail_delete: bool = False,
    ) -> None:
        super().__init__()
        self.fail_insert = fail_insert
        self.fail_list_embeddings = fail_list_embeddings
        self.fail_upsert_embedding = fail_upsert_embedding
        self.fail_delete = fail_delete

    async def insert(self, record: MemoryRecord) -> MemoryRecord | None:
        """模拟并发下同键已被写入（返回 None）。"""
        if self.fail_insert:
            return None
        return await super().insert(record)

    async def list_embeddings(
        self, tenant_id: str, memory_ids: Sequence[str]
    ) -> dict[str, list[float]]:
        """模拟向量表读取失败。"""
        if self.fail_list_embeddings:
            raise RuntimeError("embedding table missing")
        return await super().list_embeddings(tenant_id, memory_ids)

    async def upsert_embedding(
        self,
        tenant_id: str,
        memory_id: str,
        *,
        model: str,
        dim: int,
        embedding: Sequence[float],
    ) -> None:
        """模拟向量写入失败。"""
        if self.fail_upsert_embedding:
            raise RuntimeError("vector column missing")
        await super().upsert_embedding(
            tenant_id, memory_id, model=model, dim=dim, embedding=embedding
        )

    async def delete(self, tenant_id: str, user_id: str, memory_ids: Sequence[str]) -> int:
        """模拟删除失败（用于淘汰阶段的容错）。"""
        if self.fail_delete:
            raise RuntimeError("delete failed")
        return await super().delete(tenant_id, user_id, memory_ids)


class _Chat:
    """返回固定事实的补全替身。"""

    def __init__(self, facts: list[dict[str, str]]) -> None:
        import json

        self.raw = json.dumps(facts, ensure_ascii=False)

    async def complete(self, prompt: str) -> str:
        """返回 JSON 事实数组。"""
        return self.raw


class _EmptyEmbedding:
    """可用但返回空向量（上游偶发返回空的容错分支）。"""

    available = True
    model = "empty"
    dim = 2

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """返回空列表。"""
        return []


class _RaisingEmbedding:
    """可用但调用即失败。"""

    available = True
    model = "raising"
    dim = 2

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """抛错。"""
        raise RuntimeError("embedding down")


def _memory(content: str, *, category: str = "preference") -> MemoryRecord:
    """构造一条内存记忆。

    Args:
        content: 正文。
        category: 类别。

    Returns:
        MemoryRecord: 记录。
    """
    return MemoryRecord(
        tenant_id=TENANT,
        memory_id=new_memory_id(),
        user_id=USER,
        category=category,
        content=content,
        normalized_hash=normalized_hash(category, content),
    )


class TestServiceResilience:
    """服务层容错分支：向量读写失败、并发插入、淘汰失败、开关全关。"""

    def _service(
        self,
        *,
        memories: InMemoryMemoryStore | None = None,
        embedding: Any | None = None,
        chat: Any | None = None,
        settings: Settings | None = None,
    ) -> MemoryService:
        """构造服务。

        Args:
            memories: 记忆存储。
            embedding: 向量端口。
            chat: 补全替身。
            settings: 配置。

        Returns:
            MemoryService: 服务。
        """
        return MemoryService(
            settings
            or _settings(
                dify_memory_app_key=TEST_MEMORY_APP_KEY, memory_recall_timeout_seconds=0.05
            ),
            memories=memories or InMemoryMemoryStore(),
            messages=InMemoryMessageStore(),
            jobs=InMemoryJobStore(),
            extractor=MemoryExtractor(chat or _FakeChat()),
            embedding=embedding or NullEmbeddingProvider(),
        )

    async def test_recall_fallback_exception_returns_empty(self) -> None:
        """降级路径自己也抛错时彻底不注入（D-2/D-3 的最坏情况）。"""

        class _ExplodingStore(InMemoryMemoryStore):
            async def list_active(
                self, tenant_id: str, user_id: str, *, category: str = ""
            ) -> list[MemoryRecord]:
                raise RuntimeError("db down")

        service = self._service(memories=_ExplodingStore())
        assert await service.recall(TENANT, USER, "帮我写周报") == []

    async def test_recall_with_vector_and_no_memory_counts_empty(self) -> None:
        """向量可用但没有记忆 → `result=empty`（不是 degraded）。"""
        service = self._service(embedding=_FastEmbedding())
        assert await service.recall(TENANT, USER, "帮我写周报") == []

    async def test_recall_skips_vector_for_blank_query(self) -> None:
        """空白问题不走向量（`_recall_vector` 早返回）。"""
        service = self._service(embedding=_FastEmbedding())
        assert await service.recall(TENANT, USER, "  ") == []

    async def test_recall_tolerates_empty_embedding_result(self) -> None:
        """向量端口返回空列表时按"没有结果"处理（不是崩溃）。"""
        service = self._service(embedding=_EmptyEmbedding())
        assert await service.recall(TENANT, USER, "帮我写周报") == []

    async def test_maybe_enqueue_respects_switch_states(self) -> None:
        """全局关闭 / 全部分类关闭 → 都不入队（别排空跑的任务）。"""
        service = self._service()
        await service._memories.upsert_setting(  # noqa: SLF001
            MemorySettingRecord(tenant_id=TENANT, user_id=USER, enabled=False)
        )
        assert await service.maybe_enqueue_extract(TENANT, USER, "conv_off", user_turns=10) is False

        all_off = dict.fromkeys(MEMORY_CATEGORIES, False)
        await service._memories.upsert_setting(  # noqa: SLF001
            MemorySettingRecord(
                tenant_id=TENANT, user_id=USER, enabled=True, category_switches=all_off
            )
        )
        assert await service.maybe_enqueue_extract(TENANT, USER, "conv_off", user_turns=10) is False

    async def test_extract_skips_when_all_categories_closed(self) -> None:
        """全部分类关闭 → 抽取整体跳过（不发上游调用）。"""
        service = self._service()
        all_off = dict.fromkeys(MEMORY_CATEGORIES, False)
        await service._memories.upsert_setting(  # noqa: SLF001
            MemorySettingRecord(
                tenant_id=TENANT, user_id=USER, enabled=True, category_switches=all_off
            )
        )
        await service._messages.append(  # noqa: SLF001
            _user_message("我叫赵工")
        )

        result = await service.extract_and_merge(TENANT, USER, CONV)

        assert result.skipped is True

    async def test_evict_failure_is_recorded_not_raised(self) -> None:
        """淘汰阶段失败同样只记降级（不能把已写入的事实回滚掉）。"""
        memories = _VectorStore(fail_delete=True)
        settings = _settings(dify_memory_app_key=TEST_MEMORY_APP_KEY, memory_max_per_user=1)
        service = self._service(
            memories=memories,
            chat=_Chat(
                [
                    {"category": "identity", "content": "我叫赵工"},
                    {"category": "org", "content": "我在供应链部"},
                ]
            ),
            settings=settings,
        )
        await service._messages.append(_user_message("我叫赵工，在供应链部"))  # noqa: SLF001

        result = await service.extract_and_merge(TENANT, USER, CONV)

        assert result.write_failed is True
        assert service.consecutive_write_failures >= 1

    async def test_concurrent_insert_falls_back_to_merge(self) -> None:
        """插入撞车（返回 None）时退化为合并，不丢这条事实。"""
        memories = _VectorStore()
        existing = await memories.insert(_memory("汇报要表格式"))
        assert existing is not None
        memories.fail_insert = True

        service = self._service(
            memories=memories,
            chat=_Chat([{"category": "preference", "content": "汇报要表格式"}]),
        )
        await service._messages.append(_user_message("汇报要表格式"))  # noqa: SLF001

        result = await service.extract_and_merge(TENANT, USER, CONV)

        assert result.merged == 1
        assert result.extracted == 0

    async def test_similarity_vector_read_failure_falls_back(self) -> None:
        """向量读取失败 → 退回字符串相似度，仍能判断"同一件事"。"""
        memories = _VectorStore(fail_list_embeddings=True)
        await memories.insert(_memory("汇报要表格式"))
        service = self._service(
            memories=memories,
            embedding=_FastEmbedding(),
            chat=_Chat([{"category": "preference", "content": "汇报要表格式的"}]),
        )
        await service._messages.append(_user_message("汇报要表格式的"))  # noqa: SLF001

        result = await service.extract_and_merge(TENANT, USER, CONV)

        assert result.superseded == 1

    async def test_vector_write_failures_keep_facts(self) -> None:
        """向量写入失败 / 返回空 / 抛错，都只是"没有向量"，事实照存（D-3）。"""
        for embedding in (_RaisingEmbedding(), _EmptyEmbedding()):
            memories = _VectorStore()
            service = self._service(
                memories=memories,
                embedding=embedding,
                chat=_Chat([{"category": "identity", "content": "我叫赵工"}]),
            )
            await service._messages.append(_user_message("我叫赵工"))  # noqa: SLF001

            result = await service.extract_and_merge(TENANT, USER, CONV)

            assert result.extracted == 1
            assert result.embedding_written == 0

    async def test_vector_upsert_failure_keeps_fact(self) -> None:
        """向量表写入失败（例如扩展缺失）不影响事实落库。"""
        memories = _VectorStore(fail_upsert_embedding=True)
        service = self._service(
            memories=memories,
            embedding=_FastEmbedding(),
            chat=_Chat([{"category": "identity", "content": "我叫赵工"}]),
        )
        await service._messages.append(_user_message("我叫赵工"))  # noqa: SLF001

        result = await service.extract_and_merge(TENANT, USER, CONV)

        assert result.extracted == 1
        assert result.embedding_written == 0


class TestRepoBoundaries:
    """仓储层的边界参数（空键、空列表、幽灵 ID）与纯函数解析。"""

    async def test_inmemory_ghost_operations_are_noops(self) -> None:
        """对不存在的行做合并/取代/硬删都不抛（并发下会出现）。"""
        store = InMemoryMemoryStore()
        ghost = _memory("汇报要表格式")

        await store.touch(ghost, hit_count=2, confidence="high")
        await store.supersede(TENANT, ghost.memory_id, "mem_new")
        assert await store.find_by_hash(TENANT, USER, "preference", "") is None
        assert await store.delete(TENANT, USER, ["", "mem_missing"]) == 0

        store.clear()
        assert await store.count_active(TENANT, USER) == 0

    async def test_inmemory_insert_rejects_same_hash(self) -> None:
        """同 hash 再插返回 None（由服务层退化为合并）。"""
        store = InMemoryMemoryStore()
        record = _memory("汇报要表格式")
        assert await store.insert(record) is not None
        assert await store.insert(record) is None

    def test_switch_json_parsing_variants(self) -> None:
        """JSONB 开关列的三种形态：字典、JSON 串、非法串（忽略非法键）。"""
        from app.db.repositories.memory.postgres import _json_switches

        assert _json_switches({"org": True, "salary": False}) == {"org": True}
        assert _json_switches('{"preference": false}') == {"preference": False}
        assert _json_switches("{not json") == {}
        assert _json_switches("[1,2]") == {}
        assert _json_switches(None) == {}

    def test_vector_text_parsing_variants(self) -> None:
        """pgvector 文本解析：带/不带方括号、空串、脏值。"""
        from app.db.repositories.memory.postgres import _parse_vector_text

        assert _parse_vector_text("[0.1,0.2]") == pytest.approx([0.1, 0.2])
        assert _parse_vector_text("0.1,0.2") == pytest.approx([0.1, 0.2])
        assert _parse_vector_text("") == []
        assert _parse_vector_text("[]") == []
        assert _parse_vector_text("[0.1,oops]") == []

    async def test_postgres_store_early_returns_without_db(self) -> None:
        """空键/空列表在**连库之前**就返回（避免为一次空操作开事务）。"""
        from app.db.repositories.memory import PostgresMemoryStore

        store = PostgresMemoryStore(
            _settings(business_store="postgres", database_url="postgresql://u:p@127.0.0.1:1/db")
        )

        assert await store.find_by_hash(TENANT, USER, "identity", "") is None
        assert await store.delete(TENANT, USER, []) == 0
        assert await store.list_embeddings(TENANT, []) == {}
