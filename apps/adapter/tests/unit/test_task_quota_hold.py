"""额度挂起与恢复（P4-11 ③）：建任务照建但零执行；额度恢复后**主动拉起**。

两条判据对应 PRD 5.3「自动化任务排队不启动」与「处理排队到额度恢复或升级后」：

1. `test_task_is_queued_and_not_executed_when_quota_exhausted`
   —— 超额时任务**照建**（`status='queued'`、`hold_reason='quota'`），
   但**一次 step 都不执行**（`engine.run_step` 调用数为 0），进度 0。
   为什么不断言「零 step 行」：步骤行是建任务时按计划预建的（排队态也如此），
   "零 step" 指的是**零执行**，不是零行 —— 断言在行数上会误伤既有设计。
2. `test_recovery_clears_hold_and_promotes`
   —— 额度恢复后（下一次建任务判定通过）**必须真的开始推进**：
   `hold_reason` 清空 **且** `status` 不再是 `queued` **且** 有步骤被推进。
   只断言 `hold_reason == ''` 是不够的：那正是"看着有判据、实际不执行"——
   清标记容易，可如果没人 promote，任务会永远停在 `queued`。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.task_service import TaskService
from app.business.usage_service import HOLD_REASON_QUOTA
from app.plugins.engine import TASK_QUEUED
from app.schemas.agent import CreateTaskRequest
from tests.unit.task_helpers import (
    TENANT,
    auth,
    build_service,
    load_manifest,
)

pytestmark = pytest.mark.unit

#: 标杆插件（与单步超时用例同款，步骤多便于观察"是否推进"）
PLUGIN_ID = "plg_dev_workbench"


def _inputs() -> dict[str, Any]:
    """构造该插件的必填输入。

    Returns:
        dict[str, Any]: 输入。
    """
    return {
        "title": "登录页改版",
        "requirement": "把登录页换成新版设计稿的实现",
        "repo": "acme/web",
        "branch_policy": "新建分支",
    }


def _request() -> CreateTaskRequest:
    """构造建任务请求。

    Returns:
        CreateTaskRequest: 请求。
    """
    return CreateTaskRequest(
        conversation_id="conv_quota_hold", inputs=_inputs(), plugin_id=PLUGIN_ID
    )


async def _create(service: TaskService) -> str:
    """建一个任务并返回 task_id。

    Args:
        service: 任务服务。

    Returns:
        str: 任务 ID。
    """
    created = await service.create_plugin_task(_request(), auth())
    return created.task_id


async def _exhausted(*_args: Any, **_kwargs: Any) -> bool:
    """桩：额度已用尽。

    Args:
        *_args: 位置参数（忽略）。
        **_kwargs: 关键字参数（忽略）。

    Returns:
        bool: 恒为 True。
    """
    return True


async def _funded(*_args: Any, **_kwargs: Any) -> bool:
    """桩：额度充足。

    Args:
        *_args: 位置参数（忽略）。
        **_kwargs: 关键字参数（忽略）。

    Returns:
        bool: 恒为 False。
    """
    return False


@pytest.mark.asyncio
async def test_task_is_queued_and_not_executed_when_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超额：任务照建、状态 queued、挂起标记就位、**零执行**。"""
    service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID))

    calls = {"run_step": 0}
    original = service._engine.run_step  # noqa: SLF001 - 注入计数桩

    async def _patched(step_ctx: Any, plan: Any, inputs: Any, **kwargs: Any) -> Any:
        calls["run_step"] += 1
        return await original(step_ctx, plan, inputs, **kwargs)

    monkeypatch.setattr(service._engine, "run_step", _patched)  # noqa: SLF001
    monkeypatch.setattr(service._runner._deps.usage, "is_capability_exhausted", _exhausted)  # noqa: SLF001

    task_id = await _create(service)

    task = await tasks.get_task(TENANT, task_id)
    assert task is not None
    assert task.status == TASK_QUEUED, f"超额必须排队而不是开跑，实际 {task.status}"
    assert task.hold_reason == HOLD_REASON_QUOTA, "挂起标记必须落库（否则前端看不出原因）"
    assert task.progress == 0
    assert calls["run_step"] == 0, f"超额时不得执行任何步骤，实际执行 {calls['run_step']} 次"

    steps = await tasks.list_steps(TENANT, task_id)
    assert steps, "步骤行仍应预建（排队设计），本用例只证明它们没被执行"


@pytest.mark.asyncio
async def test_recovery_clears_hold_and_promotes(monkeypatch: pytest.MonkeyPatch) -> None:
    """额度恢复：清标记 **并且** 真的开始推进（不是只清标记）。"""
    service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID))
    monkeypatch.setattr(service._runner._deps.usage, "is_capability_exhausted", _exhausted)  # noqa: SLF001

    held_id = await _create(service)
    held = await tasks.get_task(TENANT, held_id)
    assert held is not None
    assert held.status == TASK_QUEUED
    assert held.hold_reason == HOLD_REASON_QUOTA
    pending_steps = await tasks.list_steps(TENANT, held_id)
    assert all(step.status == pending_steps[0].status for step in pending_steps), (
        "挂起期间不应有步骤被推进"
    )

    # 额度恢复：换成"充足"桩，再建一单 —— 这一单的判定成功分支会
    # 清本租户的挂起标记并主动 promote（而不是等"任务完成释放槽"那个事件：
    # 全租户都被挂起时，那个事件永远不会到来）。
    monkeypatch.setattr(service._runner._deps.usage, "is_capability_exhausted", _funded)  # noqa: SLF001
    await _create(service)

    recovered = await tasks.get_task(TENANT, held_id)
    assert recovered is not None
    assert recovered.hold_reason == "", "恢复后挂起标记必须清空"
    assert recovered.status != TASK_QUEUED, (
        f"恢复后必须真的开始推进（实际仍为 {recovered.status}）——"
        "只清标记不 promote 会让任务永远停在 queued"
    )

    recovered_steps = await tasks.list_steps(TENANT, held_id)
    first_status = pending_steps[0].status
    assert any(step.status != first_status for step in recovered_steps), (
        "必须出现被推进过的步骤（零 step 执行 ⇒ 恢复只是清标记，判据不成立）"
    )
