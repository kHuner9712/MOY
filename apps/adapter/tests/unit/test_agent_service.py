"""Agent 服务协议转换单元测试（架构文档 3.3 / 3.3.5）。"""

from __future__ import annotations

import pytest

from app.clients.dify_client import DifyClient
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.agent import (
    CreateTaskRequest,
    ListTasksQuery,
    StepStatus,
    TaskStatus,
)
from app.services.agent_service import AgentService, new_task_id

pytestmark = pytest.mark.unit

_AUTH = AuthContext(user_id="usr_1", tenant_id="tnt_1")


def _service() -> AgentService:
    """构造桩模式 Agent 服务。

    Returns:
        AgentService: 服务实例。
    """
    return AgentService(
        settings=Settings(downstream_enabled=False),
        dify=DifyClient("http://example.invalid", "app-test"),
    )


def test_task_id_prefix() -> None:
    """任务 ID 前缀符合架构文档 9.1。"""
    assert new_task_id().startswith("task_")


async def test_create_task_stub() -> None:
    """桩模式创建任务返回 running 初态。"""
    service = _service()
    data = await service.create_task(
        CreateTaskRequest(conversation_id="conv_1", goal="生成技术方案"), _AUTH
    )
    assert data.task_id.startswith("task_")
    assert data.status == TaskStatus.RUNNING
    assert data.estimated_seconds > 0


async def test_get_task_returns_stored_detail() -> None:
    """创建后可查询任务详情。"""
    service = _service()
    created = await service.create_task(
        CreateTaskRequest(conversation_id="conv_1", goal="目标"), _AUTH
    )
    detail = await service.get_task(created.task_id, _AUTH)
    assert detail.task_id == created.task_id
    assert detail.status == TaskStatus.RUNNING


async def test_get_task_unknown_returns_12001() -> None:
    """未知任务返回 12001。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().get_task("task_missing", _AUTH)
    assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND


async def test_list_steps_unknown_task_returns_12001() -> None:
    """未知任务的步骤查询返回 12001。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().list_steps("task_missing", _AUTH)
    assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND


async def test_cancel_task_marks_cancelled() -> None:
    """取消任务后状态为 cancelled。"""
    service = _service()
    created = await service.create_task(
        CreateTaskRequest(conversation_id="conv_1", goal="目标"), _AUTH
    )
    detail = await service.cancel_task(created.task_id, _AUTH)
    assert detail.status == TaskStatus.CANCELLED


async def test_cancel_task_twice_returns_12003() -> None:
    """重复取消返回 12003（终态冲突）。"""
    service = _service()
    created = await service.create_task(
        CreateTaskRequest(conversation_id="conv_1", goal="目标"), _AUTH
    )
    await service.cancel_task(created.task_id, _AUTH)
    with pytest.raises(ApiError) as exc_info:
        await service.cancel_task(created.task_id, _AUTH)
    assert exc_info.value.code == ErrorCode.TASK_CANCELLED


async def test_cancel_unknown_task_returns_12001() -> None:
    """取消未知任务返回 12001。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().cancel_task("task_missing", _AUTH)
    assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND


def test_list_tasks_paginates() -> None:
    """任务列表按分页返回。"""
    service = _service()
    result = service.list_tasks(ListTasksQuery(page=1, page_size=20), _AUTH)
    assert result.meta.page == 1
    assert result.meta.page_size == 20
    assert isinstance(result.items, list)


async def test_list_tasks_filters_by_status() -> None:
    """按状态过滤生效。"""
    service = _service()
    result = service.list_tasks(
        ListTasksQuery(page=1, page_size=20, status=TaskStatus.RUNNING), _AUTH
    )
    assert all(item.status == TaskStatus.RUNNING for item in result.items)


@pytest.mark.parametrize(
    ("native_event", "native_status", "expected_status"),
    [
        ("node_started", "running", StepStatus.RUNNING),
        ("node_finished", "succeeded", StepStatus.COMPLETED),
        ("node_finished", "failed", StepStatus.FAILED),
        ("node_finished", "stopped", StepStatus.SKIPPED),
    ],
)
def test_transform_node_event_maps_status(
    native_event: str, native_status: str, expected_status: StepStatus
) -> None:
    """底座节点事件映射为自研步骤状态。"""
    step = _service().transform_node_event(
        {
            "event": native_event,
            "data": {"index": 0, "status": native_status, "title": "理解需求"},
        }
    )
    assert step is not None
    assert step.status == expected_status
    assert step.step_id.startswith("step_")
    assert step.sequence == 1


def test_transform_node_event_ignores_non_node_events() -> None:
    """非节点事件返回 None。"""
    assert _service().transform_node_event({"event": "workflow_started"}) is None


def test_transform_node_event_ignores_malformed_data() -> None:
    """数据体异常时返回 None，不抛出。"""
    assert _service().transform_node_event({"event": "node_started", "data": "bad"}) is None


def test_summarize_outputs_produces_business_text() -> None:
    """节点输出归纳为业务语言摘要。"""
    service = _service()
    assert service._summarize_outputs("已完成需求拆解") == "已完成需求拆解"  # noqa: SLF001
    assert service._summarize_outputs({"summary": "提取 5 个功能点"}) == "提取 5 个功能点"  # noqa: SLF001
    assert service._summarize_outputs({"a": 1, "b": 2}) == "已生成 2 项输出"  # noqa: SLF001
    assert service._summarize_outputs(None) is None  # noqa: SLF001


def test_step_titles_never_expose_native_node_types() -> None:
    """步骤标题为业务语言，底座节点类型名不外泄。"""
    step = _service().transform_node_event(
        {"event": "node_started", "data": {"index": 2, "title": "生成技术方案"}}
    )
    assert step is not None
    serialized = step.model_dump_json()
    for leaked in ("llm", "if-else", "code", "http-request", "iteration"):
        assert leaked not in serialized.lower()


async def test_stream_events_emits_self_defined_frames() -> None:
    """A6 事件流使用自研事件名。"""
    service = _service()
    created = await service.create_task(
        CreateTaskRequest(conversation_id="conv_1", goal="目标"), _AUTH
    )
    frames = [frame async for frame in service.stream_events(created.task_id, _AUTH)]
    assert frames
    assert all(frame.startswith("event: ") for frame in frames)
    assert frames[-1].startswith("event: task_completed")
    assert not any("workflow_run" in frame for frame in frames)


async def test_stream_events_unknown_task_raises() -> None:
    """未知任务在建立 SSE 响应前即抛 12001（由 ensure_task_exists 守卫）。"""
    service = _service()
    with pytest.raises(ApiError) as exc_info:
        await service.ensure_task_exists("task_missing", _AUTH)
    assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND


async def test_append_steps_populates_steps() -> None:
    """追加步骤后可通过 A4 查询。"""
    from app.schemas.agent import StepItem

    service = _service()
    created = await service.create_task(
        CreateTaskRequest(conversation_id="conv_1", goal="目标"), _AUTH
    )
    service.append_steps(
        created.task_id,
        [StepItem(step_id="step_1", sequence=1, title="理解需求", status=StepStatus.COMPLETED)],
        _AUTH,
    )
    steps = await service.list_steps(created.task_id, _AUTH)
    assert len(steps.steps) == 1
    assert steps.steps[0].title == "理解需求"


def test_transform_task_detail_maps_native_status() -> None:
    """底座四态映射为自研五态。"""
    service = _service()
    for native, expected in [
        ("running", TaskStatus.RUNNING),
        ("succeeded", TaskStatus.COMPLETED),
        ("failed", TaskStatus.FAILED),
        ("stopped", TaskStatus.CANCELLED),
        ("waiting", TaskStatus.PENDING),
    ]:
        detail = service._transform_task_detail("task_1", {"status": native})  # noqa: SLF001
        assert detail.status == expected


def test_transform_task_detail_extracts_artifacts() -> None:
    """从底座 outputs 提取产物列表。"""
    service = _service()
    detail = service._transform_task_detail(  # noqa: SLF001
        "task_1",
        {
            "status": "succeeded",
            "outputs": {
                "summary": "已生成技术方案",
                "artifacts": [
                    {"type": "file", "name": "LoginForm.tsx", "download_url": "https://x"}
                ],
            },
        },
    )
    assert detail.status == TaskStatus.COMPLETED
    assert detail.progress == 100
    assert detail.result is not None
    assert detail.result.summary == "已生成技术方案"
    assert detail.result.artifacts[0].name == "LoginForm.tsx"
