"""A6 SSE 迁移用例：`id:` 帧、`Last-Event-ID` 续传（A2）、灰度开关、步骤投影。

覆盖的验收（团队验收第 2 条 / 第 3 条）：

- **A2 断线续传**：跑到一半断开连接，带 `Last-Event-ID` 重连，拼起来的事件序列
  与"不中断地跑完"**逐字节一致**（零丢失、零重复）；
- 帧格式是**向后兼容增量**：`event:` 仍是第一行，`id:` 插在中间，`data` 只增字段；
- `event_log_enabled=false` 时只退化到终态帧，且**没有**回落到内存步骤字典；
- A4 步骤详情是事件流的投影（不读任何"当前状态"副本）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pytest

from app.api.v1.agent import _parse_last_event_id
from app.clients.dify_client import DifyClient
from app.core.config import Settings
from app.db.repositories.event_log.memory import InMemoryEventStore
from app.kernel.events import build_session_id
from app.schemas.agent import CreateTaskRequest, StepItem, StepStatus, TaskStatus
from app.services.agent_service import AgentService

pytestmark = pytest.mark.unit

TENANT = "tnt_sse"
AUTH_TENANT = TENANT
OTHER_TENANT = "tnt_other"

#: 帧里的 id 行
_ID_PATTERN = re.compile(r"^id: (\d+)$", re.MULTILINE)


def _auth(tenant_id: str = AUTH_TENANT) -> Any:
    """构造鉴权上下文。"""
    from app.core.security import AuthContext

    return AuthContext(user_id="usr_1", tenant_id=tenant_id)


def _service(
    *,
    store: InMemoryEventStore | None = None,
    event_log_enabled: bool = True,
) -> AgentService:
    """构造注入了内存事件存储的服务（不产生任何下游请求）。

    Args:
        store: 可选事件存储。
        event_log_enabled: 事件日志灰度开关。

    Returns:
        AgentService: 服务实例。
    """
    return AgentService(
        settings=Settings(
            downstream_enabled=False,
            event_log_enabled=event_log_enabled,
        ),
        dify=DifyClient("http://example.invalid", "app-test"),
        events=store or InMemoryEventStore(),
    )


async def _task_with_steps(service: AgentService, *, step_count: int = 4) -> str:
    """建一个任务并写入若干步骤事件。

    Args:
        service: 服务实例。
        step_count: 步骤数。

    Returns:
        str: 任务 ID。
    """
    created = await service.create_task(
        CreateTaskRequest(conversation_id="conv_1", goal="生成技术方案"), _auth()
    )
    steps = [
        StepItem(
            step_id=f"step_{index}",
            sequence=index,
            title=f"步骤{index}",
            status=StepStatus.RUNNING if index % 2 else StepStatus.COMPLETED,
        )
        for index in range(1, step_count + 1)
    ]
    await service.append_steps(created.task_id, steps, _auth())
    return created.task_id


def _frame_id(frame: str) -> int:
    """取帧里的 `id:` 值。

    Args:
        frame: SSE 帧文本。

    Returns:
        int: 回放游标。
    """
    match = _ID_PATTERN.search(frame)
    assert match is not None, f"帧里没有 id 行：{frame!r}"
    return int(match.group(1))


def _digest(frames: list[str]) -> str:
    """给一串帧算逐字节摘要（A2 的比对口径）。

    Args:
        frames: SSE 帧列表。

    Returns:
        str: sha256 十六进制摘要。
    """
    return hashlib.sha256("".join(frames).encode("utf-8")).hexdigest()


# ===========================================================================
# 帧格式：id 帧 + 向后兼容
# ===========================================================================
class TestFrameFormat:
    """`id:` 是新增的，其余字段与顺序保持既有解析习惯。"""

    async def test_frames_carry_monotonic_ids(self) -> None:
        """每帧带 `id:` 且严格递增（续传的唯一依据）。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=3)

        frames = [frame async for frame in service.stream_events(task_id, _auth())]
        ids = [_frame_id(frame) for frame in frames]
        assert ids == sorted(ids)
        assert len(set(ids[:-1])) == len(ids) - 1  # 只有终态帧允许复用最后一个游标

    async def test_event_line_stays_first(self) -> None:
        """`event:` 仍是第一行（既有客户端与用例按此解析）。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=1)

        frames = [frame async for frame in service.stream_events(task_id, _auth())]
        for frame in frames:
            assert frame.splitlines()[0].startswith("event: ")

    async def test_step_frames_keep_legacy_fields(self) -> None:
        """`data.step` 仍在帧数据顶层（老客户端不改就能读）。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=2)

        frames = [frame async for frame in service.stream_events(task_id, _auth())]
        first = json.loads(frames[0].split("data: ", 1)[1])
        assert first["task_id"] == task_id
        assert first["step"]["step_id"] == "step_1"
        # 新增字段（加性）：seq / type / payload。`seq` 即回放游标，
        # 首帧是 2 而不是 1：1 号事件是 `session.created`（进流但不产帧）。
        assert first["seq"] == _frame_id(frames[0]) == 2
        assert first["type"] == "tool.requested"
        assert first["payload"]["step"]["step_id"] == "step_1"

    async def test_terminal_frame_is_task_completed(self) -> None:
        """末帧仍是 `task_completed`，带当前任务状态。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=1)

        frames = [frame async for frame in service.stream_events(task_id, _auth())]
        assert frames[-1].startswith("event: task_completed")
        payload = json.loads(frames[-1].split("data: ", 1)[1])
        assert payload["status"] == "running"

    async def test_session_created_emits_no_progress_frame(self) -> None:
        """`session.created` 进事件流但不产帧（会话建立不是"进度"）。"""
        service = _service()
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        frames = [frame async for frame in service.stream_events(created.task_id, _auth())]
        assert (
            frames == [frame for frame in frames if frame.startswith("event: task_completed")]
            or frames == []
        )
        assert all("session.created" not in frame for frame in frames)

    async def test_unknown_task_raises_before_first_frame(self) -> None:
        """建流前的归属校验在产出第一帧之前完成（否则无法再返回错误体）。"""
        from app.core.errors import ApiError, ErrorCode

        service = _service()
        with pytest.raises(ApiError) as exc_info:
            async for _frame in service.stream_events("task_missing", _auth()):
                pass  # pragma: no cover - 不应产出任何帧
        assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND


# ===========================================================================
# A2 断线续传：零丢失、零重复
# ===========================================================================
class TestResumeAfterDisconnect:
    """断线重连是 E1 的第一条推论：投影时带 `after_seq`。"""

    async def test_resume_is_lossless_and_duplicate_free(self) -> None:
        """**A2 验收**：中途断开 + 带 `Last-Event-ID` 重连 = 不中断的完整序列。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=5)

        uninterrupted = [frame async for frame in service.stream_events(task_id, _auth())]

        # 跑到第 3 帧就断开（模拟 kill 连接）
        stream = service.stream_events(task_id, _auth())
        head = [await stream.__anext__() for _ in range(3)]
        await stream.aclose()

        # 客户端带着最后一次收到的 id 重连
        last_id = _frame_id(head[-1])
        tail = [frame async for frame in service.stream_events(task_id, _auth(), after_id=last_id)]

        resumed = head + tail
        assert resumed == uninterrupted
        assert _digest(resumed) == _digest(uninterrupted)
        ids = [_frame_id(frame) for frame in resumed]
        assert ids == sorted(ids), "续传后游标必须仍单调递增（无重复、无回退）"

    async def test_resume_from_zero_replays_everything(self) -> None:
        """`Last-Event-ID: 0`（或缺失）等于从头回放。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=3)

        full = [frame async for frame in service.stream_events(task_id, _auth())]
        replay = [frame async for frame in service.stream_events(task_id, _auth(), after_id=0)]
        assert replay == full

    async def test_resume_from_tail_returns_only_terminal_frame(self) -> None:
        """已经追平后再连只拿到终态帧（零重复：不再重发历史）。"""
        service = _service()
        task_id = await _task_with_steps(service, step_count=2)

        frames = [frame async for frame in service.stream_events(task_id, _auth())]
        cursor = _frame_id(frames[-2])
        tail = [frame async for frame in service.stream_events(task_id, _auth(), after_id=cursor)]
        assert len(tail) == 1
        assert tail[0].startswith("event: task_completed")

    async def test_after_id_beyond_tail_is_clamped_and_not_echoed(self) -> None:
        """**D3 验收**：越界 `after_id` 不回显，按"从头回放"处理。

        缺陷形态：`after_id=999999` 被原样回显成终态帧的 `id:`，客户端下次仍带
        同一个越界值 → 永远拿不到进度帧，而服务端一切正常、日志无异常
        （"永久黑屏"）。钳位后：越界即从头，客户端自己就恢复了。
        """
        service = _service()
        task_id = await _task_with_steps(service, step_count=2)
        full = [frame async for frame in service.stream_events(task_id, _auth())]
        tail_id = _frame_id(full[-2])

        replay = [
            frame async for frame in service.stream_events(task_id, _auth(), after_id=tail_id + 999)
        ]
        ids = [_frame_id(frame) for frame in replay]
        assert ids == [_frame_id(frame) for frame in full], "越界游标必须退化成从头回放"
        assert max(ids) <= tail_id, f"回显了越界 id：{max(ids)} > {tail_id}"

    async def test_headers_parse_tolerates_garbage(self) -> None:
        """`Last-Event-ID` 宽容解析：空串 / 非数字 / 负数都回落到从头。"""
        assert _parse_last_event_id(None) == 0
        assert _parse_last_event_id("") == 0
        assert _parse_last_event_id("  ") == 0
        assert _parse_last_event_id("abc") == 0
        assert _parse_last_event_id("-3") == 0
        assert _parse_last_event_id(" 42 ") == 42


# ===========================================================================
# 灰度开关 + 步骤投影（E1：只有一个可写副本）
# ===========================================================================
class TestCreateTaskOrdering:
    """**D18 验收**：先写事件、再登记任务（失败不留幽灵任务）。"""

    async def test_append_failure_leaves_no_ghost_task(self) -> None:
        """事件写入失败时任务表里不能留下"有任务、无事件"的幽灵任务。

        顺序反过来（先登记任务、后 append）时，append 一失败就留下一条永远
        `running` 的任务：前端能看到它、事件流里却没有事实支撑它——E1 之下
        "没有事件的状态"就是不存在。
        """
        store = InMemoryEventStore()

        async def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("event store down")

        store.append = _boom  # type: ignore[method-assign]
        service = _service(store=store)

        with pytest.raises(RuntimeError):
            await service.create_task(
                CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
            )
        assert service._tasks == {}, "append 失败后不应留下幽灵任务"  # noqa: SLF001


class TestEventLogSwitchAndProjection:
    """开关只做灰度回退，不提供第二套存储路径。"""

    async def test_disabled_flag_writes_no_event(self) -> None:
        """开关关闭时不写事件（存储里一条都没有）。"""
        store = InMemoryEventStore()
        service = _service(store=store, event_log_enabled=False)
        await _task_with_steps(service, step_count=2)
        assert store.counts() == 0

    async def test_disabled_flag_streams_only_terminal_frame(self) -> None:
        """开关关闭时 SSE 只退化到终态帧（可见的降级，不是静默双写）。"""
        service = _service(event_log_enabled=False)
        task_id = await _task_with_steps(service, step_count=3)

        frames = [frame async for frame in service.stream_events(task_id, _auth())]
        assert len(frames) == 1
        assert frames[0].startswith("event: task_completed")

    async def test_steps_are_projected_from_events(self) -> None:
        """步骤详情来自事件流：事件被清空后步骤查询也变空（没有第二副本）。"""
        store = InMemoryEventStore()
        service = _service(store=store)
        task_id = await _task_with_steps(service, step_count=2)

        before = await service.list_steps(task_id, _auth())
        assert [item.step_id for item in before.steps] == ["step_1", "step_2"]

        store.clear()
        after = await service.list_steps(task_id, _auth())
        assert after.steps == []

    async def test_step_state_follows_last_event(self) -> None:
        """同一个 `step_id` 后到的事件覆盖状态（追加写 + 投影取最后一条）。"""
        service = _service()
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        await service.append_steps(
            created.task_id,
            [StepItem(step_id="step_1", sequence=1, title="理解需求", status=StepStatus.RUNNING)],
            _auth(),
        )
        await service.append_steps(
            created.task_id,
            [StepItem(step_id="step_1", sequence=1, title="理解需求", status=StepStatus.COMPLETED)],
            _auth(),
        )

        steps = await service.list_steps(created.task_id, _auth())
        assert len(steps.steps) == 1
        assert steps.steps[0].status == StepStatus.COMPLETED

    async def test_cancel_appends_turn_finished_event(self) -> None:
        """**D8 验收**：取消任务必须落一条 `turn.finished{cancelled}`。

        只改 `_tasks.status` 的话，取消回放不出来、审计查不到、重启即丢——
        而"任务为什么停在这里"正是审计最常问的问题。用已有 14 类之一承载，
        不新增枚举。
        """
        store = InMemoryEventStore()
        service = _service(store=store)
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        await service.cancel_task(created.task_id, _auth())

        session = build_session_id(TENANT, created.task_id)
        events = await store.read_after(TENANT, session)
        assert [event.type for event in events] == ["session.created", "turn.finished"]
        assert events[-1].payload["outcome"] == "cancelled"
        assert events[-1].payload["status"] == "cancelled"

    async def test_cancel_survives_event_write_failure(self) -> None:
        """**N2 验收**：事件写失败时取消仍然成功（降级为 WARN），状态是 cancelled。

        写入点在底座 `/stop` **之后**：那时下游已经停了，再抛错只会让用户重试、
        再打一次 `/stop`（可能 404），于是"取消一直失败但任务其实停了"，
        而事件流里没有任何记录——状态、界面、事实三者互相矛盾。
        取舍是"用户感知已取消"优先：接口返回成功 + 状态 cancelled + WARN 记账失败。
        """
        store = InMemoryEventStore()
        service = _service(store=store)
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        # 建任务之后再把存储打坏：取消路径的 `POST /stop` 已成功，只剩记账会失败
        async def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("event store down")

        store.append = _boom  # type: ignore[method-assign]

        cancelled = await service.cancel_task(created.task_id, _auth())
        assert cancelled.status == TaskStatus.CANCELLED

        detail = await service.get_task(created.task_id, _auth())
        assert detail.status == TaskStatus.CANCELLED

    async def test_events_are_tenant_scoped(self) -> None:
        """事件流按租户隔离：另一个租户读同一个 task_id 读不到任何事件。"""
        store = InMemoryEventStore()
        service = _service(store=store)
        task_id = await _task_with_steps(service, step_count=2)

        session = build_session_id(OTHER_TENANT, task_id)
        assert await store.read_after(OTHER_TENANT, session) == []
