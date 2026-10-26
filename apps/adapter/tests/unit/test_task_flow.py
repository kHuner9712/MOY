"""插件任务流程单测（阶段三 B19，`architecture-phase3.md` §3.3 / §5.3 / D-19）。

钉住四条一票否决项：

1. **接管点不自动推进**：`confirm` 步骤未确认时任务停在 `waiting_confirm`；
2. **`continue` 同任务续跑**：`task_id` 不变、`resumed=true`、`retry_of=null`；
3. **`retry_step` 新建任务**：`retry_of` 指向原任务，原任务状态**永不改变**；
4. **并发超限走状态**：第 N+1 个任务 `status=queued` + `queue_position`（不是 429/20006），
   额度释放后**自动**转 `running`。
"""

from __future__ import annotations

import asyncio

import pytest

from app.business.task_service import TaskService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.artifact_repo import InMemoryArtifactStore
from app.db.repositories.task import InMemoryTaskStore
from app.plugins.engine import TASK_COMPLETED, TASK_QUEUED, TASK_WAITING_CONFIRM
from app.schemas.agent import CreateTaskRequest
from app.schemas.task import RetryTaskRequest
from tests.unit.task_helpers import (
    TENANT,
    USER,
    EchoGateway,
    FakeUsageService,
    auth,
    build_service,
    load_manifest,
)

#: 三个标杆里带两处人工接管点的插件（第 2 步方案确认、第 5 步提交前确认）
PLUGIN_ID = "plg_dev_workbench"


def _inputs() -> dict[str, object]:
    """构造该插件的必填输入。

    Returns:
        dict[str, object]: 输入。
    """
    return {
        "title": "登录页改版",
        "requirement": "把登录页换成新版设计稿的实现",
        "repo": "acme/web",
        "branch_policy": "新建分支",
    }


def _request() -> CreateTaskRequest:
    """构造 A1 请求（插件分支）。

    Returns:
        CreateTaskRequest: 请求体。
    """
    return CreateTaskRequest(
        conversation_id="conv_1",
        inputs=_inputs(),
        plugin_id=PLUGIN_ID,
    )


def _run(coro: object) -> object:
    """同步执行协程（单测不引 pytest-asyncio）。

    Args:
        coro: 协程对象。

    Returns:
        object: 协程返回值。
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def _first_confirm_seq(manifest: object) -> int:
    """取声明里第一个接管点步骤序号（不硬编码 seq，随装配调整而变）。

    Args:
        manifest: 声明。

    Returns:
        int: 步骤序号；无接管点时返回 0。
    """
    for spec in manifest.steps:  # type: ignore[attr-defined]
        if spec.confirm is not None:
            return int(spec.seq)
    return 0


def test_engine_without_gateway_factory_raises_config_error() -> None:
    """未注入 `engine` 且未注入 `gateway_factory` → 构造即抛 30004（审计 P1-6）。

    为什么必须有一条用例：P1-6 把「谁造动作门面」从业务层搬到了组合根
    （`api/v1/agent.py::_action_gateway`）。搬完之后业务层**不再知道**门面从哪来，
    于是「组合根忘了注入」不再是 ImportError（那种会当场炸），而是运行期拿到
    `None`——如果不显式报错，就会退化成
    `TypeError: 'NoneType' object is not callable`，现场完全看不出是**装配遗漏**。

    本用例钉住三件事：① 构造期就失败（不拖到第一个任务）；② 码必须是
    `30004 CONFIG_ERROR`（不是 500 内部错误、不是 20001）；③ `internal_detail`
    指出该注入什么，否则排障只能靠猜。
    """
    settings = Settings(
        env="test",
        mock_mode=True,
        jwt_secret="task-helper-test-secret",
        plugin_retry_backoff_ms=0,
    )

    with pytest.raises(ApiError) as excinfo:
        TaskService(settings)

    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert int(excinfo.value.code) == 30004
    assert excinfo.value.http_status == 500
    detail = excinfo.value.internal_detail or ""
    assert "gateway_factory" in detail, "内部诊断必须指出该注入什么（组合根职责）"


def test_confirm_step_pauses_task_and_does_not_auto_advance() -> None:
    """首个接管点 → 任务停在 `waiting_confirm`，且该步**未执行**。"""
    manifest = load_manifest(PLUGIN_ID)
    confirm_seq = _first_confirm_seq(manifest)
    assert confirm_seq > 0, "研发工作台声明里应至少有一个接管点"
    gateway = EchoGateway()
    service, tasks, _, _ = build_service(load_manifest(PLUGIN_ID), gateway=gateway)
    created = _run(service.create_plugin_task(_request(), auth()))
    record = _run(tasks.get_task(TENANT, created.task_id))
    assert record is not None
    assert record.status == TASK_WAITING_CONFIRM, f"应停在接管点，实际 {record.status}"
    assert record.confirm_step_seq == confirm_seq
    # 接管点之前的步骤执行、接管步本身不执行
    assert [seq for _, seq in gateway.calls] == list(range(1, confirm_seq))


def test_continue_resumes_same_task_without_creating_another() -> None:
    """`decision=continue` → 同一 task、`resumed=true`、`retry_of=null`。"""
    manifest = load_manifest(PLUGIN_ID)
    service, tasks, _, _ = build_service(manifest)
    created = _run(service.create_plugin_task(_request(), auth()))
    result = _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    assert result.task_id == created.task_id, "continue 必须续跑同一任务（新建会让核查清单分裂）"
    assert result.resumed is True
    assert result.retry_of is None, "resumed=true 时 retry_of 必须为 null（QA TC-P3-13 ②）"
    # 第 5 步又一处接管点 → 再次停住
    record = _run(tasks.get_task(TENANT, created.task_id))
    assert record is not None
    assert record.status == TASK_WAITING_CONFIRM
    assert record.confirm_step_seq > _first_confirm_seq(manifest), "应推进到下一处接管点"
    # 任务总数仍是 1（没偷偷建第二个）
    _, total = _run(tasks.list_tasks(TENANT))
    assert total == 1


def test_continue_twice_reaches_completed_with_checklist() -> None:
    """两次确认后任务完成，核查清单挂**同一个**任务（AC-02 核心断言）。"""
    manifest = load_manifest(PLUGIN_ID)
    service, _tasks, _artifacts, usage = build_service(manifest)
    created = _run(service.create_plugin_task(_request(), auth()))
    _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    detail = _run(service.get_task(created.task_id, auth()))
    assert str(detail.status) == TASK_COMPLETED
    assert detail.result is not None
    checklist = detail.result.checklist
    assert len(checklist) == len(manifest.checklist), "核查清单项数必须与声明一致"
    assert {item["status"] for item in checklist} <= {"checked", "pending", "missing"}
    assert detail.progress == 100
    assert usage.calls, "任务完成后应写一条 automation 计量"


def test_retry_step_creates_new_task_and_keeps_original_untouched() -> None:
    """`retry_step` → 新任务 + `retry_of` 指向原任务，原任务**状态永不改变**。

    失败点与起点都**从声明推导**（找第一个 `on_failure=abort` 的步骤），不硬编码
    seq —— 声明由 B20 装配，硬编码会让这条契约钉子随装配漂移。
    """
    manifest = load_manifest(PLUGIN_ID)
    abort_seq = next(
        spec.seq for spec in manifest.steps if spec.on_failure == "abort" and spec.seq > 1
    )
    service, tasks, _, _ = build_service(manifest, gateway=EchoGateway(fail_at=abort_seq))
    created = _run(service.create_plugin_task(_request(), auth()))

    # 逐个人工接管点确认，直到走到会 abort 的那一步（最多 5 次，防死循环）
    for _ in range(5):
        record = _run(tasks.get_task(TENANT, created.task_id))
        if record is None or record.status != TASK_WAITING_CONFIRM:
            break
        _run(service.retry(created.task_id, RetryTaskRequest(decision="continue"), auth()))
    original = _run(tasks.get_task(TENANT, created.task_id))
    assert original is not None
    assert original.status == "failed", f"第 {abort_seq} 步失败应 abort，实际 {original.status}"
    before_status = original.status

    result = _run(
        service.retry(
            created.task_id,
            RetryTaskRequest(decision="retry_step", from_step=abort_seq),
            auth(),
        )
    )
    assert result.resumed is False, "retry_step 是新任务，不能谎报 resumed"
    assert result.retry_of == created.task_id
    assert result.task_id != created.task_id
    after = _run(tasks.get_task(TENANT, created.task_id))
    assert after is not None and after.status == before_status, "重试不得改写原任务状态"


def test_concurrency_limit_queues_instead_of_erroring() -> None:
    """占用额度已满 → `queued` + `queue_position`，**不抛 20006/429**。"""
    service, tasks, _, _ = build_service(load_manifest(PLUGIN_ID), concurrency=1)
    first = _run(service.create_plugin_task(_request(), auth()))
    second = _run(service.create_plugin_task(_request(), auth()))
    assert str(second.status) == TASK_QUEUED
    assert second.queue_position == 1
    # 排队任务确实落库且状态为 queued（不是只在响应里改了个字段）
    queued = _run(tasks.get_task(TENANT, second.task_id))
    assert queued is not None and queued.status == TASK_QUEUED
    assert first.status != TASK_QUEUED


def test_queue_is_promoted_automatically_when_slot_frees() -> None:
    """额度释放后最早排队的任务**自动**转 running（用户不需要再点一次）。"""
    service, tasks, _, _ = build_service(load_manifest(PLUGIN_ID), concurrency=1)
    first = _run(service.create_plugin_task(_request(), auth()))
    second = _run(service.create_plugin_task(_request(), auth()))
    assert str(second.status) == TASK_QUEUED

    # 让占额度的任务走完（两次确认）
    _run(service.retry(first.task_id, RetryTaskRequest(decision="continue"), auth()))
    _run(service.retry(first.task_id, RetryTaskRequest(decision="continue"), auth()))
    promoted = _run(tasks.get_task(TENANT, second.task_id))
    assert promoted is not None
    assert promoted.status != TASK_QUEUED, "额度释放后排队任务应被自动拉起"
    assert promoted.queue_position is None


def test_retry_on_running_task_is_rejected_with_17001() -> None:
    """非可重试状态调用 WF1 → `17001`（不是 500，也不是静默成功）。"""
    service, tasks, _, _ = build_service(load_manifest(PLUGIN_ID))
    created = _run(service.create_plugin_task(_request(), auth()))
    record = _run(tasks.get_task(TENANT, created.task_id))
    assert record is not None
    with pytest.raises(ApiError) as excinfo:
        _run(service.retry(created.task_id, RetryTaskRequest(decision="retry_all"), auth()))
    assert int(excinfo.value.code) == int(ErrorCode.TASK_NOT_RETRYABLE)


def test_missing_input_returns_20001_before_touching_engine() -> None:
    """必填输入缺失 → 20001，且**不会**先弹"缺能力"（顺序决定用户出路）。"""
    service, tasks, _, _ = build_service(load_manifest(PLUGIN_ID))
    bad = CreateTaskRequest(
        conversation_id="conv_1", inputs={"title": "只有标题"}, plugin_id=PLUGIN_ID
    )
    with pytest.raises(ApiError) as excinfo:
        _run(service.create_plugin_task(bad, auth()))
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)
    _, total = _run(tasks.list_tasks(TENANT))
    assert total == 0, "校验失败不得留下半成品任务"


def test_task_visible_only_to_initiator() -> None:
    """对象级判权：他人查询 → 20003（不泄露他人任务是否存在）。"""
    service, _, _, _ = build_service(load_manifest(PLUGIN_ID))
    created = _run(service.create_plugin_task(_request(), auth()))
    with pytest.raises(ApiError) as excinfo:
        _run(service.get_task(created.task_id, auth(user_id="usr_other")))
    assert int(excinfo.value.code) == int(ErrorCode.FORBIDDEN)


# 便于 mypy/ruff 明确未使用导入（类型别名在签名里引用）
_ = (TaskService, InMemoryTaskStore, InMemoryArtifactStore, FakeUsageService, USER)
