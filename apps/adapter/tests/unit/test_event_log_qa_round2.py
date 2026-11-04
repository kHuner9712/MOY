"""QA 第二轮复核用例（4f79a36 修复轮，非开发者提交内容）。

目的不是"再验一遍已修的 8 条"，而是**验修复本身有没有引入新的失败面**：

1. **D3 的钳位语义**：`after_id` 越界 → 从头回放。这条语义会让客户端**重复收到
   全部历史**，必须用用例把"重复多少、什么条件下重复"固定下来，而不是靠注释里的
   "回放幂等"四个字。同时对**范围内**的续传补一条"不重复"的对照，防止钳位改宽。
2. **D4 的退避总时长**：最坏总等待不能靠注释里的数字（工程代码里两处注释写了
   1.3s、提交说明写 3s）。这里把 `asyncio.sleep` 换成本地记录器，实测 16 次冲突
   的真实累计等待与尝试次数。
3. **D7 的切点边界**：两层与三层 fork 的 `lowest - 1` / `lowest` 两侧都钉住。
4. **D1 的批边界**：恰好 1000 / 2000 条（整页边界）的投影必须完整——翻页循环的
   off-by-one 只在整页处暴露。
5. **D8 的重复取消**：取消写事件之后，第二次取消不能又写一条（事件流是状态真源，
   重复写入等于把一次取消记成两次）。
6. **D18 的反向失败路径**：换 ID 后补写 `session.created` 若失败，占位任务与占位
   事件必须成对存在（不能一边有一边没有）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.clients.dify_client import DifyClient
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.event_log.memory import InMemoryEventStore
from app.db.repositories.event_log.records import (
    EVENT_TOOL_COMPLETED,
    EVENT_TURN_STARTED,
    MAX_SEQ_ATTEMPTS,
    build_session_id,
)
from app.kernel.events import replay_state_block
from app.schemas.agent import CreateTaskRequest, StepItem, StepStatus
from app.services.agent_service import AgentService

pytestmark = pytest.mark.unit

TENANT = "tnt_qa_round2"

_BASE_PARAMS: dict[str, Any] = {
    "env": "test",
    "deploy_mode": "saas",
    "business_store": "memory",
    "auth_mode": "stub",
    "jwt_secret": "qa-round2-secret",
    "ratelimit_enabled": False,
    "background_jobs_enabled": False,
}


def _auth(tenant_id: str = TENANT) -> AuthContext:
    """构造鉴权上下文。

    Args:
        tenant_id: 租户 ID。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id="usr_qa2", tenant_id=tenant_id)


class _StubResponse:
    """底座响应替身。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化。

        Args:
            payload: 响应体。
        """
        self.payload = payload


class _StubDify:
    """底座客户端替身：`post` 恒定返回给定 payload。"""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        """初始化。

        Args:
            payload: `post()` 的响应体。
        """
        self.payload = payload or {}

    async def post(self, path: str, body: dict[str, Any]) -> _StubResponse:
        """返回预设响应。

        Args:
            path: 请求路径。
            body: 请求体。

        Returns:
            _StubResponse: 响应。
        """
        return _StubResponse(self.payload)

    async def get(self, path: str) -> _StubResponse:
        """返回空响应。

        Args:
            path: 请求路径。

        Returns:
            _StubResponse: 响应。
        """
        return _StubResponse({})


def _service(
    *,
    store: InMemoryEventStore | None = None,
    dify: Any = None,
    downstream_enabled: bool = False,
) -> AgentService:
    """构造注入了内存事件存储的服务。

    Args:
        store: 事件存储（缺省新建）。
        dify: 底座客户端替身。
        downstream_enabled: 是否启用真实下游路径。

    Returns:
        AgentService: 服务实例。
    """
    settings = Settings(**_BASE_PARAMS, downstream_enabled=downstream_enabled)
    return AgentService(
        settings=settings,
        dify=dify or DifyClient("http://example.invalid", "app-qa2"),
        events=store or InMemoryEventStore(),
    )


def _frame_id(frame: str) -> int:
    """取帧里的 `id:` 值。

    Args:
        frame: SSE 帧文本。

    Returns:
        int: 回放游标。
    """
    return int(frame.split("id: ", 1)[1].split("\n", 1)[0])


async def _task_with_steps(service: AgentService, *, step_count: int = 3) -> str:
    """建一个任务并写入若干步骤事件。

    Args:
        service: 服务实例。
        step_count: 步骤数。

    Returns:
        str: 任务 ID。
    """
    created = await service.create_task(
        CreateTaskRequest(conversation_id="conv_r2", goal="目标"), _auth()
    )
    steps = [
        StepItem(
            step_id=f"step_{index}",
            sequence=index,
            title=f"步骤{index}",
            status=StepStatus.COMPLETED,
        )
        for index in range(1, step_count + 1)
    ]
    await service.append_steps(created.task_id, steps, _auth())
    return created.task_id


# ===========================================================================
# 1. D3：钳位语义的"重复量"必须有据可查
# ===========================================================================
class TestCursorClampingSemantics:
    """越界 → 从头回放的代价是**整段历史重发**，必须在用例里写清。"""

    async def test_out_of_range_after_id_redelivers_full_history(self) -> None:
        """越界游标 → 客户端会再收到一遍完整历史（重复量 = 全量）。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=3)

        full = [frame async for frame in service.stream_events(task_id, _auth())]
        replay = [frame async for frame in service.stream_events(task_id, _auth(), after_id=10**9)]

        assert replay == full, "越界游标按实现应退化成从头回放"
        # 事实陈述：非终态帧全部被重发一遍（前端若"追加渲染"就会重复）
        assert (
            len([frame for frame in replay if not frame.startswith("event: task_completed")]) == 3
        )
        # 重发帧带的是**真实**游标，所以客户端存下之后下一次就回到增量
        assert max(_frame_id(frame) for frame in replay) == _frame_id(full[-2])

    async def test_in_range_after_id_stays_duplicate_free(self) -> None:
        """范围内的续传仍然零重复（钳位没有把正常路径改宽）。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=3)

        full = [frame async for frame in service.stream_events(task_id, _auth())]
        cursor = _frame_id(full[1])
        tail = [frame async for frame in service.stream_events(task_id, _auth(), after_id=cursor)]

        assert _frame_id(tail[0]) > cursor
        assert list(tail) == [frame for frame in full if _frame_id(frame) > cursor]

    async def test_clamped_cursor_covers_forked_session_lineage(self) -> None:
        """fork 会话的尾游标 = 偏移 + max(seq)，范围内的续传不会被误判成越界。"""
        store = InMemoryEventStore()
        session = build_session_id(TENANT, "task_fork_tail")
        for index in range(4):
            await store.append(TENANT, session, EVENT_TURN_STARTED, {"n": index})
        child = await store.fork(TENANT, session, 2)
        await store.append(TENANT, child, EVENT_TOOL_COMPLETED, {"step": {"step_id": "s1"}})

        tail = await store.tail_cursor(TENANT, child)
        # 子会话自身 2 条（fork 标记 seq=1 + 追加的 tool.completed seq=2），偏移 = 2
        # → 展平流末尾游标 = 2 + 2 = 4
        assert tail == 4, f"fork 会话尾游标应为 偏移2 + 自身max_seq2 = 4，实测 {tail}"
        resumed = await store.read_after(TENANT, child, tail)
        assert resumed == [], "追平之后不应再下发任何事件"


# ===========================================================================
# 2. D4：退避的真实总等待与尝试次数
# ===========================================================================
class _ConflictUoW:
    """永不成功的假工作单元：HEAD 查空、INSERT 恒冲突。"""

    def __init__(self, tenant_id: str) -> None:
        """初始化。

        Args:
            tenant_id: 生效租户。
        """
        self.tenant_id = tenant_id

    async def fetch_one(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        """HEAD 查空、INSERT 恒抛主键冲突。

        Args:
            statement: SQL。
            parameters: 命名参数。

        Returns:
            Any: HEAD 语句返回 None。

        Raises:
            IntegrityError: INSERT 语句。
        """
        if "INSERT INTO" in str(statement):
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return None


class TestRetryBackoffBudget:
    """`MAX_SEQ_ATTEMPTS` 与退避的关系要用测量钉住，不能只写在注释里。"""

    async def test_worst_case_total_backoff_is_bounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """连续冲突时：尝试次数 = `MAX_SEQ_ATTEMPTS`，累计退避 ≤ 4s（可接受上界）。"""
        recorded: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            recorded.append(seconds)

        @asynccontextmanager
        async def _fake_scope(tenant_id: str, **_kwargs: Any) -> Any:
            yield _ConflictUoW(tenant_id)

        from app.db.repositories.event_log import postgres as pg_module
        from app.db.repositories.event_log.postgres import PostgresEventStore

        monkeypatch.setattr(pg_module.asyncio, "sleep", _fake_sleep)
        monkeypatch.setattr(pg_module, "tenant_scope", _fake_scope)

        store = PostgresEventStore(Settings(**_BASE_PARAMS))
        with pytest.raises(ApiError) as exc_info:
            await store.append(TENANT, "s_backoff", EVENT_TURN_STARTED, {"n": 1})

        assert exc_info.value.code == ErrorCode.INTERNAL_ERROR
        assert len(recorded) == MAX_SEQ_ATTEMPTS - 1, (
            f"应重试 {MAX_SEQ_ATTEMPTS - 1} 次（最后一次直接抛），实测 {len(recorded)}"
        )
        total = sum(recorded)
        assert total <= 4.0, f"最坏总退避 {total:.2f}s 超出可接受上界（单请求内等待）"
        # 单次退避封顶生效：不存在 1s 以上的单次等待
        assert max(recorded) <= 0.3, f"单次退避 {max(recorded):.2f}s 超过 200ms 封顶×1.5 抖动"


# ===========================================================================
# 3. D7：两层 / 三层 fork 的切点边界
# ===========================================================================
class TestForkCutBoundary:
    """切点下限 = 父会话自身偏移 + 1；两侧都要钉住。"""

    async def test_two_level_boundary(self) -> None:
        """两层：`lowest - 1` 拒绝、`lowest` 通过、游标连续。"""
        store = InMemoryEventStore()
        for index in range(3):
            await store.append(TENANT, "root2", EVENT_TURN_STARTED, {"n": index})
        child = await store.fork(TENANT, "root2", 2)  # child 偏移 = 2 → lowest = 3
        tail = await store.tail_cursor(TENANT, child)
        assert tail == 3

        with pytest.raises(ApiError) as exc_info:
            await store.fork(TENANT, child, tail - 1)
        assert exc_info.value.code == ErrorCode.PARAM_INVALID

        grandchild = await store.fork(TENANT, child, tail)
        cursors = [event.cursor for event in await store.read_after(TENANT, grandchild)]
        assert cursors == list(range(1, len(cursors) + 1))

    async def test_three_level_boundary_has_no_duplicate_cursor(self) -> None:
        """三层：每层都切在允许范围的最低点，展平后游标仍严格 1..N。"""
        store = InMemoryEventStore()
        for index in range(60):
            await store.append(TENANT, "root3", EVENT_TURN_STARTED, {"n": index})
        # 三层都切在"父会话自身偏移 + 1"（最低合法点）
        level1 = await store.fork(TENANT, "root3", 60)
        level2 = await store.fork(TENANT, level1, await store.tail_cursor(TENANT, level1))
        level3 = await store.fork(TENANT, level2, await store.tail_cursor(TENANT, level2))

        cursors = [event.cursor for event in await store.read_after(TENANT, level3)]
        assert len(cursors) == len(set(cursors)), "出现重复游标"
        assert cursors == sorted(cursors)
        assert cursors == list(range(1, len(cursors) + 1)), (
            f"游标断档：{cursors[:5]}…{cursors[-3:]}"
        )
        assert await store.verify_chain(TENANT, level3) is True


# ===========================================================================
# 4. D1：批边界（整页）不能 off-by-one
# ===========================================================================
class TestReplayBatchBoundary:
    """翻页循环的 off-by-one 只在"恰好整页"处暴露。"""

    @pytest.mark.parametrize("total", [1000, 1001, 2000])
    async def test_replay_is_complete_at_batch_boundaries(self, total: int) -> None:
        """恰好 1000 / 1001 / 2000 条事件的投影必须一条不少。"""
        store = InMemoryEventStore()
        session = f"s_batch_{total}"
        for index in range(total):
            await store.append(
                TENANT, session, EVENT_TOOL_COMPLETED, {"step": {"step_id": f"step_{index}"}}
            )

        block = await replay_state_block(store, TENANT, session)

        assert len(block["completed_steps"]) == total
        assert block["completed_steps"][0] == "step_0"
        assert block["completed_steps"][-1] == f"step_{total - 1}"


# ===========================================================================
# 5. D8：取消写事件不能变成"取消两次记两条"
# ===========================================================================
class TestCancelEventIdempotency:
    """取消是状态迁移，重复调用不能往真源里再写一条。"""

    async def test_second_cancel_is_rejected_and_writes_nothing(self) -> None:
        """第二次取消抛 12003，事件流里只有一条 `turn.finished`。"""
        store = InMemoryEventStore()
        service = _service(store=store)
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_c", goal="取消验收"), _auth()
        )
        await service.cancel_task(created.task_id, _auth())

        with pytest.raises(ApiError) as exc_info:
            await service.cancel_task(created.task_id, _auth())
        assert exc_info.value.code == ErrorCode.TASK_CANCELLED

        session = build_session_id(TENANT, created.task_id)
        events = await store.read_after(TENANT, session)
        assert [event.type for event in events] == ["session.created", "turn.finished"]


# ===========================================================================
# 6. D18：换 ID 后补写失败的反向路径
# ===========================================================================
class TestRunIdSwitchFailurePath:
    """补写 `session.created` 失败时，占位任务与占位事件必须成对存在。"""

    async def test_failure_on_reissued_event_leaves_consistent_pair(self) -> None:
        """第 2 次 append 失败：占位会话有事件、占位任务在表里，且**没有**新任务。"""
        store = InMemoryEventStore()
        original_append = store.append
        calls = {"n": 0}

        async def _fail_second(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("event store down on reissue")
            return await original_append(*args, **kwargs)

        store.append = _fail_second  # type: ignore[method-assign]
        service = _service(
            store=store,
            dify=_StubDify({"workflow_run_id": "run_r2", "data": {"status": "running"}}),
            downstream_enabled=True,
        )

        with pytest.raises(RuntimeError):
            await service.create_task(
                CreateTaskRequest(conversation_id="conv_o", goal="反向路径"), _auth()
            )

        # 只剩占位任务，且它的会话里有唯一一条 session.created —— 两者成对
        assert len(service._tasks) == 1  # noqa: SLF001
        ((tenant, task_id),) = service._tasks  # noqa: SLF001
        assert tenant == TENANT
        events = await store.read_after(TENANT, build_session_id(TENANT, task_id))
        assert [event.type for event in events] == ["session.created"]
        # 反向失败路径不能留下"有事件、无任务"的孤儿新会话
        assert not any(
            "task_run_r2" in session_id
            for _tenant, session_id in store._streams  # noqa: SLF001
        )
