"""单步超时、防重复推进与取消竞态（阶段四 T4-04.4，架构 §2.6 / P1-2）。

三条判据对应三类线上事故：

1. **单步超时**：某一步卡住（下游不响应）时，任务不能无限挂着——用户看到的是
   "进度条永远停在 3/8"。必须**按步**限时（整任务外层限时等于"8 步共享一个预算"，
   最后仍然是卡住），超时按失败收口、已产出的产物保留；
2. **防重复推进**：两个进程/两个请求同时推进同一任务时，"读步骤 → 跑一步 → 写结果"
   会被执行两遍——产物重复、额度重复扣、步骤序号跳变。推进锁保证**步骤只 +1**；
3. **取消竞态**（P1-2）：取消与收尾是两条并行路径。修复前 `cancel()` 不持锁写终态、
   `update_task` 无乐观条件、`_release_slot` 无条件释放——于是"取消被 COMPLETED 覆盖"
   且"并发额度被释放两次"。本组用例把三条判据钉死：取消不丢、终态一致、槽位只释放一次。

`step_timeout_seconds` 是构造参数（生产默认取 `plugin_task_timeout_seconds`），
用例用它把 30s 的下限压到 0.2s——**不改产品配置**，只是给测试一个可注入的时钟量级。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from app.business.task_service import TaskService
from app.db.advisory_lock import advisory_key, reset_advance_locks, task_advance_lock
from app.plugins.engine import STEP_FAILED, TASK_CANCELLED, TASK_COMPLETED, TASK_RUNNING
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


class TestCancelRace:
    """取消竞态（P1-2）：取消不丢、终态一致、并发额度只释放一次。"""

    async def test_cancel_is_not_overwritten_by_stale_finish(self) -> None:
        """推进方拿**旧快照**收尾时，不得把取消改回"已完成"（终态一致）。

        这是线上那条路径的确定性复现：推进在第 N 步跑着 → 用户取消成功 →
        推进方手里仍是 `running` 的旧行 → `_finish(TASK_COMPLETED)` 无条件覆盖终态。
        修复后终态写带 `expected_status`，条件不满足即放弃覆写。
        """
        reset_advance_locks()
        service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID))
        task_id = await _create(service)

        record = await tasks.get_task(TENANT, task_id)
        assert record is not None
        stale = replace(record)  # 推进方在取消之前读到的快照
        steps = await tasks.list_steps(TENANT, task_id)

        await service.cancel(task_id, auth())  # 取消胜出（持推进锁写终态）
        await service._finish(stale, steps, TASK_COMPLETED, [])  # noqa: SLF001

        final = await tasks.get_task(TENANT, task_id)
        assert final is not None
        assert final.status == TASK_CANCELLED, "取消被收尾覆盖成了已完成"
        assert final.cancel_requested is True
        assert final.error_message == "任务已取消"

    async def test_cancel_during_last_step_survives_completion(self) -> None:
        """最后一步执行中取消 → 任务终态仍是取消（不是"跑完了就算完成"）。"""
        reset_advance_locks()
        service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID))
        manifest = load_manifest(PLUGIN_ID)
        last_seq = len(manifest.steps)
        original = service._engine.run_step  # noqa: SLF001
        fired: list[int] = []

        async def _cancel_on_last_step(step_ctx: Any, plan: Any, inputs: Any) -> Any:
            # 推进锁此刻由**推进方**持有：取消必须能立刻写入（不排队等锁），
            # 否则用户点"取消"要等到当前步骤跑完才生效——这就是原来卡住的地方。
            if plan.spec.seq == last_seq and not fired:
                fired.append(plan.spec.seq)
                await service.cancel(step_ctx.task_id, auth())
            return await original(step_ctx, plan, inputs)

        service._engine.run_step = _cancel_on_last_step  # type: ignore[method-assign]  # noqa: SLF001

        task_id = await _create(service)
        record = await tasks.get_task(TENANT, task_id)
        assert record is not None
        record.confirmed_step_seq = 99  # 跳过两处人工接管点，跑到最后一步
        await service._advance_record(record)  # noqa: SLF001

        assert fired == [last_seq], "最后一步没有被执行，用例前提不成立"
        final = await tasks.get_task(TENANT, task_id)
        assert final is not None
        assert final.status == TASK_CANCELLED, "取消在收尾时被 completed 覆盖"
        assert final.cancel_requested is True

    async def test_slot_released_only_once_under_concurrent_release(self) -> None:
        """两个并行释放方 → 同一个排队任务**只被拉起一次**（额度只释放一次）。"""
        reset_advance_locks()
        service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID), concurrency=1)
        first = await _create(service)
        second = await _create(service)
        third = await _create(service)
        second_row = await tasks.get_task(TENANT, second)
        third_row = await tasks.get_task(TENANT, third)
        assert second_row is not None and third_row is not None
        assert second_row.queue_position == 1 and third_row.queue_position == 2

        # 前置：`first` 的终态已经写入（本用例只考察"释放方"两路并发时的释放次数）。
        # 正常路径里这一步就是 `cancel()` / `_finish()` 的条件写。
        await tasks.update_task(TENANT, first, status=TASK_CANCELLED, cancel_requested=True)
        record = await tasks.get_task(TENANT, first)
        assert record is not None and record.terminal

        promotions: list[str] = []
        original_update = tasks.update_task

        async def _spy(tenant_id: str, task_id: str, **fields: Any) -> Any:
            # 只统计**真正拉起成功**的那次写：同时带 status=running 与 queue_position
            result = await original_update(tenant_id, task_id, **fields)
            if (
                fields.get("status") == TASK_RUNNING
                and "queue_position" in fields
                and result is not None
            ):
                promotions.append(task_id)
            return result

        tasks.update_task = _spy  # type: ignore[method-assign]

        # 制造真实的并发窗口：内存仓储的方法内部没有 await，`gather` 天然会把
        # "检查额度"与"拉起排队任务"排成先后两步，看不到竞态。这里在两个动作之间
        # 显式让出一次事件循环——这正是 PG 上两个副本各自算完额度再写库的时间窗。
        original_count = tasks.count_occupying

        async def _slow_count(tenant_id: str, plugin_id: str) -> int:
            value = await original_count(tenant_id, plugin_id)
            await asyncio.sleep(0)  # 让另一个释放方也读到同一个额度数
            return value

        tasks.count_occupying = _slow_count  # type: ignore[method-assign]

        await asyncio.gather(
            service._release_slot(record),  # noqa: SLF001
            service._release_slot(record),  # noqa: SLF001
        )

        assert promotions == [second], f"同一排队任务被拉起 {len(promotions)} 次"
        promoted = await tasks.get_task(TENANT, second)
        still_queued = await tasks.get_task(TENANT, third)
        assert promoted is not None and promoted.status != "queued"
        assert still_queued is not None and still_queued.status == "queued"
        assert still_queued.queue_position == 1, "剩余排队任务位次必须前移且只前移一次"

    async def test_release_slot_skips_when_not_the_transition_owner(self) -> None:
        """没写成功终态的一方**不得**释放额度（`transitioned=False` 直接返回）。"""
        reset_advance_locks()
        service, tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID), concurrency=1)
        first = await _create(service)
        second = await _create(service)
        record = await tasks.get_task(TENANT, first)
        assert record is not None

        await service._release_slot(record, transitioned=False)  # noqa: SLF001

        still_queued = await tasks.get_task(TENANT, second)
        assert still_queued is not None
        assert still_queued.status == "queued", "非终态写入方不应释放额度"


__all__: list[str] = [
    "PLUGIN_ID",
    "USER",
    "TestAdvanceLock",
    "TestCancelRace",
    "TestStepTimeout",
]
