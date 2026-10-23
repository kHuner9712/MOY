"""单步超时与防重复推进（阶段四 T4-04.4，架构 §2.6）。

两条判据对应两类线上事故：

1. **单步超时**：某一步卡住（下游不响应）时，任务不能无限挂着——用户看到的是
   "进度条永远停在 3/8"。必须**按步**限时（整任务外层限时等于"8 步共享一个预算"，
   最后仍然是卡住），超时按失败收口、已产出的产物保留；
2. **防重复推进**：两个进程/两个请求同时推进同一任务时，"读步骤 → 跑一步 → 写结果"
   会被执行两遍——产物重复、额度重复扣、步骤序号跳变。推进锁保证**步骤只 +1**。

`step_timeout_seconds` 是构造参数（生产默认取 `plugin_task_timeout_seconds`），
用例用它把 30s 的下限压到 0.2s——**不改产品配置**，只是给测试一个可注入的时钟量级。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.business.task_service import TaskService
from app.db.advisory_lock import advisory_key, reset_advance_locks, task_advance_lock
from app.plugins.engine import STEP_FAILED
from app.schemas.agent import CreateTaskRequest
from tests.unit.task_helpers import (
    TENANT,
    USER,
    auth,
    build_service,
    load_manifest,
)

pytestmark = pytest.mark.unit

#: 带两处人工接管点的标杆插件（步骤多，便于观察"第 N 步超时"）
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
        conversation_id="conv_step_lock", inputs=_inputs(), plugin_id=PLUGIN_ID
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


class TestStepTimeout:
    """单步超时：按步限时、按失败收口、产物保留。"""

    async def test_slow_step_times_out_quickly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """把某一步变慢 → 该步判失败（超时），且总耗时远小于"睡满"的时长。"""
        service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID))
        original = service._engine.run_step  # noqa: SLF001

        async def _patched(step_ctx: Any, plan: Any, inputs: Any) -> Any:
            if plan.spec.seq >= 1:
                await asyncio.sleep(10)  # 比 0.2s 预算大得多
            return await original(step_ctx, plan, inputs)

        monkeypatch.setattr(service._engine, "run_step", _patched)  # noqa: SLF001
        service._step_timeout_seconds = 0.2  # noqa: SLF001 - 注入测试量级

        started = asyncio.get_running_loop().time()
        task_id = await _create(service)
        task = await tasks.get_task(TENANT, task_id)
        assert task is not None
        task.confirmed_step_seq = 99  # 跳过人工接管点，直接跑到会超时的那一步
        await service._advance_record(task)  # noqa: SLF001
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 3.0, f"单步超时未生效，耗时 {elapsed:.1f}s"
        steps = await tasks.list_steps(TENANT, task_id)
        first = steps[0]
        assert first.status == STEP_FAILED
        assert "超过" in first.reason_text and "停止等待" in first.reason_text
        # 任务本身不能崩（仍是可查询的状态，而不是异常退出）
        task = await tasks.get_task(TENANT, task_id)
        assert task is not None

    async def test_timeout_keeps_previously_saved_artifacts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """第 1 步正常产出产物、第 2 步超时 → 第 1 步的产物仍在（不因超时被回滚）。"""
        service, tasks, artifacts, _usage = build_service(load_manifest(PLUGIN_ID))
        original = service._engine.run_step  # noqa: SLF001
        seen: list[int] = []

        async def _patched(step_ctx: Any, plan: Any, inputs: Any) -> Any:
            seen.append(plan.spec.seq)
            if plan.spec.seq > 1:
                await asyncio.sleep(10)
            return await original(step_ctx, plan, inputs)

        monkeypatch.setattr(service._engine, "run_step", _patched)  # noqa: SLF001
        service._step_timeout_seconds = 0.3  # noqa: SLF001

        task_id = await _create(service)
        task = await tasks.get_task(TENANT, task_id)
        assert task is not None
        task.confirmed_step_seq = 99  # 跳过人工接管点
        await service._advance_record(task)  # noqa: SLF001

        rows = await artifacts.list_by_task(TENANT, task_id)
        assert seen[:2] == [1, 2], f"应至少跑到第 2 步，实际 {seen}"
        # 第 1 步的产物必须留着（`list_by_task` 是详情页/下载的唯一来源）
        assert rows, "第 1 步的产物被超时连带清掉了"


class TestAdvanceLock:
    """防重复推进：并发推进同一任务时步骤只 +1。"""

    async def test_concurrent_advance_runs_once(self) -> None:
        """两个并发推进 → 只有拿到锁的那个真跑，步骤序号只 +1。"""
        reset_advance_locks()
        service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID))
        # 让单步耗时一点，保证两个推进真正重叠
        original = service._engine.run_step  # noqa: SLF001
        calls: list[int] = []

        async def _slow(step_ctx: Any, plan: Any, inputs: Any) -> Any:
            calls.append(plan.spec.seq)
            await asyncio.sleep(0.05)
            return await original(step_ctx, plan, inputs)

        service._engine.run_step = _slow  # type: ignore[method-assign]  # noqa: SLF001

        task_id = await _create(service)
        task = await tasks.get_task(TENANT, task_id)
        assert task is not None
        before = task.current_step_seq

        await asyncio.gather(
            service._advance_record(task),  # noqa: SLF001
            service._advance_record(task),  # noqa: SLF001
        )

        after_task = await tasks.get_task(TENANT, task_id)
        assert after_task is not None
        # 每个步骤序号最多出现一次（重复推进会看到同一个 seq 被执行两次）
        assert len(calls) == len(set(calls)), f"同一步被并发执行了两次：{calls}"
        assert after_task.current_step_seq >= before

    async def test_lock_is_reentrant_free_and_released(self) -> None:
        """锁用完必须释放：第二次获取必须成功（否则任务会被永久锁死）。"""
        reset_advance_locks()
        async with task_advance_lock("task_lock_demo") as first:
            assert first is True
        async with task_advance_lock("task_lock_demo") as second:
            assert second is True

    async def test_busy_lock_returns_false_immediately(self) -> None:
        """已持有锁时，第二次获取**立刻**返回 False（不排队、不等待）。"""
        reset_advance_locks()
        async with task_advance_lock("task_lock_busy") as held:
            assert held is True
            started = asyncio.get_running_loop().time()
            async with task_advance_lock("task_lock_busy") as nested:
                assert nested is False
            assert asyncio.get_running_loop().time() - started < 0.5

    def test_advisory_key_is_stable_and_positive(self) -> None:
        """锁键稳定且落在 bigint 的正区间（跨进程必须算得一样）。"""
        key = advisory_key("task_advance:task_01H")
        assert key == advisory_key("task_advance:task_01H")
        assert 0 < key < 2**63
        assert key != advisory_key("task_advance:task_01G")


__all__: list[str] = [
    "PLUGIN_ID",
    "USER",
    "TestAdvanceLock",
    "TestStepTimeout",
]
