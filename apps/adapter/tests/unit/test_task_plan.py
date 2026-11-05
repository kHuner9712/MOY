"""通用任务计划生成 → 确认（矩阵第 2 项）· 服务层单测。

钉住五条一票否决项：

1. **确认前零执行**：任务停在 `waiting_plan`，`EchoGateway.calls` 为空；
2. **计划可读可编辑**：详情带结构化 `plan`，取消勾选落库为步骤 `skipped`；
3. **不可取消的步骤**（`seq=1` / 产物被后续 `needs` 引用）→ 20001；
4. **确认幂等**：重复确认 `already_confirmed=true`，不会二次推进；
5. **超时/放弃清理**：过期计划被条件取消；用户取消同样有效。

计划默认关闭的行为也有一条：`plan_first=None` + 开关关闭 → 直接执行（存量路径不变）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.business.task.plan import PLAN_EXPIRED_MESSAGE, plan_expires_at
from app.business.task.plan_cleanup import sweep_expired_plans
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.plugins.engine import STEP_PENDING, STEP_SKIPPED, TASK_CANCELLED, TASK_COMPLETED
from app.schemas.agent import CreateTaskRequest
from app.schemas.task import TaskPlanConfirmRequest, TaskPlanStepSelection, TaskPlanUpdateRequest
from tests.unit.task_helpers import (
    TENANT,
    EchoGateway,
    FakeUsageService,
    InMemoryArtifactStore,
    InMemoryTaskStore,
    TaskService,
    auth,
    build_service,
    fixture_manifest,
)


def _run(coro: object) -> object:
    """同步执行协程（单测不引 pytest-asyncio）。

    Args:
        coro: 协程对象。

    Returns:
        object: 协程返回值。
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def _request(plan_first: bool | None = None) -> CreateTaskRequest:
    """构造夹具插件的创建请求。

    Args:
        plan_first: 是否先出计划。

    Returns:
        CreateTaskRequest: 请求体。
    """
    return CreateTaskRequest(
        conversation_id="conv_plan",
        inputs={"topic": "登录页改版"},
        plugin_id="plg_fixture_case",
        plan_first=plan_first,
    )


def _prepare(
    *, enabled: bool = True, timeout: int = 900
) -> tuple[TaskService, InMemoryTaskStore, InMemoryArtifactStore, FakeUsageService, EchoGateway]:
    """构造开启计划的服务。

    步骤 2 特意换成**非对外发送**的工具操作：夹具声明的 gh 建 PR 属对外发送，
    声明校验会强制要求 `confirm`（`loader._check_confirm`），执行时会停在
    `waiting_confirm`，与本用例要验的"计划确认"是两件事。

    Args:
        enabled: 是否开启计划。
        timeout: 计划确认窗口（秒）。

    Returns:
        tuple: `(service, store, artifacts, usage, gateway)`。
    """
    manifest = fixture_manifest(
        steps=[
            {
                "seq": 1,
                "title": "生成稿件",
                "action": "compose",
                "produces": ["draft"],
                "on_failure": "continue",
            },
            {
                "seq": 2,
                "title": "读取素材",
                "action": "tool",
                "tool_type": "feishu",
                "operation": "create_calendar_event",
                "produces": [],
                "needs": ["draft"],
                "on_failure": "abort",
            },
        ]
    )
    gateway = EchoGateway()
    service, store, artifacts, usage = build_service(
        manifest,
        gateway=gateway,
        task_plan_enabled=enabled,
        task_plan_timeout_seconds=timeout,
    )
    return service, store, artifacts, usage, gateway


def test_plan_created_without_side_effects() -> None:
    """计划任务一落库就带结构化计划，且**没有任何原语被调用**。"""
    service, _, _, _, gateway = _prepare()

    data = _run(service.create_plugin_task(_request(True), auth()))

    assert data.status.value == "waiting_plan"
    detail = _run(service.get_task(data.task_id, auth()))
    assert detail.plan_version == 1
    assert detail.plan_expires_at
    assert detail.plan is not None
    assert [step.seq for step in detail.plan.steps] == [1, 2]
    # 第 1 步的 draft 被第 2 步 needs 引用 → 不可取消；第 2 步无产物 → 可取消
    assert detail.plan.steps[0].skippable is False
    assert detail.plan.steps[1].skippable is True
    assert detail.plan.steps[0].tool_type is None
    assert detail.plan.steps[1].tool_type == "feishu"
    assert detail.plan.steps[1].tool_operation == "create_calendar_event"
    assert gateway.calls == [], "确认前不得执行任何有副作用的步骤"


def test_plan_edit_requires_matching_version_and_persists_selection() -> None:
    """版本不匹配 → 20005；匹配后取消勾选 → 步骤 `skipped`，版本 +1。"""
    service, store, _, _, gateway = _prepare()
    data = _run(service.create_plugin_task(_request(True), auth()))

    with pytest.raises(ApiError) as stale:
        _run(
            service.update_plan(
                data.task_id,
                TaskPlanUpdateRequest(
                    plan_version=0,
                    steps=[TaskPlanStepSelection(seq=2, selected=False)],
                ),
                auth(),
            )
        )
    assert stale.value.code == ErrorCode.CONFLICT
    assert stale.value.http_status == 409

    plan = _run(
        service.update_plan(
            data.task_id,
            TaskPlanUpdateRequest(
                plan_version=1,
                steps=[TaskPlanStepSelection(seq=2, selected=False)],
            ),
            auth(),
        )
    )
    assert plan.version == 2
    assert plan.steps[1].selected is False

    steps = _run(store.list_steps(TENANT, data.task_id))
    assert {step.seq: step.status for step in steps}[2] == STEP_SKIPPED
    assert gateway.calls == []

    # 再勾回来 → 恢复 pending（编辑仍然是"版本 +1"）
    plan = _run(
        service.update_plan(
            data.task_id,
            TaskPlanUpdateRequest(
                plan_version=2,
                steps=[TaskPlanStepSelection(seq=2, selected=True)],
            ),
            auth(),
        )
    )
    assert plan.version == 3
    assert plan.steps[1].selected is True
    assert {step.seq: step.status for step in _run(store.list_steps(TENANT, data.task_id))}[
        2
    ] == STEP_PENDING


def test_unskippable_step_cannot_be_deselected() -> None:
    """第 1 步是必做步骤：取消勾选 → 20001，且不改版本。"""
    service, store, _, _, _ = _prepare()
    data = _run(service.create_plugin_task(_request(True), auth()))

    with pytest.raises(ApiError) as blocked:
        _run(
            service.update_plan(
                data.task_id,
                TaskPlanUpdateRequest(
                    plan_version=1,
                    steps=[TaskPlanStepSelection(seq=1, selected=False)],
                ),
                auth(),
            )
        )
    assert blocked.value.code == ErrorCode.PARAM_INVALID
    record = _run(store.get_task(TENANT, data.task_id))
    assert record is not None and record.plan_version == 1


def test_confirm_executes_once_and_is_idempotent() -> None:
    """确认后才执行；重复确认返回 `already_confirmed` 且不再推进。"""
    service, _, _, _, gateway = _prepare()
    data = _run(service.create_plugin_task(_request(True), auth()))
    assert gateway.calls == []

    result = _run(
        service.confirm_plan(data.task_id, TaskPlanConfirmRequest(plan_version=1), auth())
    )
    assert result.status.value == TASK_COMPLETED
    assert result.already_confirmed is False
    assert gateway.calls == [("compose", 1), ("tool", 2)]

    again = _run(service.confirm_plan(data.task_id, TaskPlanConfirmRequest(plan_version=1), auth()))
    assert again.already_confirmed is True
    assert again.plan_confirmed_at == result.plan_confirmed_at
    assert gateway.calls == [("compose", 1), ("tool", 2)], "重复确认不得二次执行"

    detail = _run(service.get_task(data.task_id, auth()))
    assert detail.status.value == TASK_COMPLETED
    assert detail.plan is not None and detail.plan.confirmed_at


def test_confirm_goes_queued_when_quota_exhausted() -> None:
    """额度用尽时确认 → `queued`（不执行），由既有 promote 在额度恢复后拉起。"""
    service, _, _, usage, gateway = _prepare()
    data = _run(service.create_plugin_task(_request(True), auth()))
    usage.exhausted = True

    result = _run(
        service.confirm_plan(data.task_id, TaskPlanConfirmRequest(plan_version=1), auth())
    )
    assert result.status.value == "queued"
    assert gateway.calls == []
    detail = _run(service.get_task(data.task_id, auth()))
    assert detail.plan is not None and detail.plan.confirmed_at


def test_cancel_abandons_plan_without_executing() -> None:
    """用户放弃计划：`waiting_plan` 可取消，且不执行任何步骤。"""
    service, store, _, _, gateway = _prepare()
    data = _run(service.create_plugin_task(_request(True), auth()))

    detail = _run(service.cancel(data.task_id, auth()))
    assert detail.status.value == TASK_CANCELLED
    assert gateway.calls == []
    record = _run(store.get_task(TENANT, data.task_id))
    assert record is not None and record.cancel_requested is True


def test_expired_plan_is_swept_and_confirm_rejected() -> None:
    """超时清理：条件取消、可重复执行；过期后确认 → 20005。"""
    service, store, _, _, gateway = _prepare(timeout=60)
    data = _run(service.create_plugin_task(_request(True), auth()))

    # 把截止时间拨到过去（模拟时间流逝，不 sleep）
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    _run(store.update_task(TENANT, data.task_id, plan_expires_at=past))

    settings = Settings(env="test", task_plan_enabled=True, task_plan_timeout_seconds=60)
    assert _run(sweep_expired_plans(store, settings, tenant_ids=[TENANT])) == 1
    record = _run(store.get_task(TENANT, data.task_id))
    assert record is not None
    assert record.status == TASK_CANCELLED
    assert record.error_message == PLAN_EXPIRED_MESSAGE
    assert record.cancel_requested is False, "系统超时不算用户取消（完成率口径不变）"
    assert gateway.calls == []

    # 重复清理幂等
    assert _run(sweep_expired_plans(store, settings, tenant_ids=[TENANT])) == 0

    with pytest.raises(ApiError) as expired:
        _run(service.confirm_plan(data.task_id, TaskPlanConfirmRequest(plan_version=1), auth()))
    assert expired.value.code == ErrorCode.CONFLICT
    record = _run(store.get_task(TENANT, data.task_id))
    assert record is not None and record.error_message == PLAN_EXPIRED_MESSAGE


def test_expired_plan_confirm_lazily_cancels() -> None:
    """未等清理器，直接确认过期计划 → 就地取消并拒绝（用户视角无"可确认"假象）。"""
    service, store, _, _, gateway = _prepare(timeout=60)
    data = _run(service.create_plugin_task(_request(True), auth()))
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    _run(store.update_task(TENANT, data.task_id, plan_expires_at=past))

    with pytest.raises(ApiError) as expired:
        _run(service.confirm_plan(data.task_id, TaskPlanConfirmRequest(plan_version=1), auth()))
    assert expired.value.code == ErrorCode.CONFLICT
    assert PLAN_EXPIRED_MESSAGE in expired.value.message
    record = _run(store.get_task(TENANT, data.task_id))
    assert record is not None and record.status == TASK_CANCELLED
    assert gateway.calls == []


def test_plan_feature_off_keeps_legacy_behaviour() -> None:
    """开关关闭 + `plan_first=None` → 直接执行（存量路径不变）。"""
    service, _, _, _, gateway = _prepare(enabled=False)
    data = _run(service.create_plugin_task(_request(None), auth()))
    assert data.status.value == TASK_COMPLETED
    assert gateway.calls == [("compose", 1), ("tool", 2)]

    with pytest.raises(ApiError) as disabled:
        _run(service.create_plugin_task(_request(True), auth()))
    assert disabled.value.code == ErrorCode.PARAM_INVALID


def test_only_initiator_can_edit_or_confirm() -> None:
    """非发起人 → 20003。"""
    service, _, _, _, _ = _prepare()
    data = _run(service.create_plugin_task(_request(True), auth()))

    with pytest.raises(ApiError) as forbidden:
        _run(
            service.confirm_plan(
                data.task_id, TaskPlanConfirmRequest(plan_version=1), auth("usr_other")
            )
        )
    assert forbidden.value.code == ErrorCode.FORBIDDEN


def test_plan_expiry_helper_is_utc_iso() -> None:
    """截止时间格式固定为 UTC `Z`，避免前后端时区歧义。"""
    value = plan_expires_at(Settings(env="test", task_plan_timeout_seconds=120))
    assert value.endswith("Z")
    assert "+00:00" not in value
