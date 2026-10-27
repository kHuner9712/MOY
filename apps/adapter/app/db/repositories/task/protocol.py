"""任务与步骤仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `TaskStore`，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖（拆分前它们同处一个上帝文件，看不出来；一拆就立刻变成环）。
独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`，
单向且无环，G-38 门禁据此判定「协议方法 ⊆ 两侧实现」。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from app.db.repositories.task.records import StepRecord, TaskRecord, TaskStats


class TaskStore(Protocol):
    """任务域存储契约（内存 / PostgreSQL 双实现）。"""

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        """插入任务行。"""
        ...

    async def get_task(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        """读取任务行。"""
        ...

    async def find_by_idem(
        self, tenant_id: str, plugin_id: str, idem_key: str
    ) -> TaskRecord | None:
        """按幂等键查任务（`uq_task_idem`）。"""
        ...

    async def list_tasks(
        self,
        tenant_id: str,
        *,
        plugin_id: str = "",
        status: str = "",
        initiator_id: str = "",
        created_from: str = "",
        created_to: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskRecord], int]:
        """分页列出任务（返回 `(items, total)`）。"""
        ...

    async def update_task(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> TaskRecord | None:
        """更新任务可变字段（可带乐观状态条件）。

        P1-2：`expected_status` 是**乐观并发条件**——写终态前先确认"库里现在的状态
        仍是这些之一"，避免"推进方拿着旧快照收尾"覆盖掉取消等并发写入的终态。
        """
        ...

    async def count_occupying(self, tenant_id: str, plugin_id: str) -> int:
        """统计占用并发额度的任务数。"""
        ...

    async def clear_hold_reasons(self, tenant_id: str) -> int:
        """按租户批量清空额度挂起标记（懒恢复）；返回被清空的条数。"""
        ...

    async def counts_occupying(self, tenant_id: str, plugin_ids: Sequence[str]) -> dict[str, int]:
        """批量统计各插件占用额度的任务数（审计 P2-6）。

        已安装列表按插件逐条调用 `count_occupying()` 是 N+1：装 40 个插件就是
        40 次聚合查询。批量版一次 `GROUP BY plugin_id` 取回；**没有在跑任务的
        插件不进字典**（调用方按 0 处理，与逐条调用的返回值一致）。
        """
        ...

    async def count_queued(self, tenant_id: str, plugin_id: str) -> int:
        """统计排队任务数。"""
        ...

    async def next_queued(self, tenant_id: str, plugin_id: str) -> TaskRecord | None:
        """取最早排队的任务（FIFO）。"""
        ...

    async def list_queued(self, tenant_id: str, plugin_id: str) -> list[TaskRecord]:
        """列出排队任务（按位次）。"""
        ...

    async def create_steps(self, records: Iterable[StepRecord]) -> list[StepRecord]:
        """批量插入步骤骨架。"""
        ...

    async def list_steps(self, tenant_id: str, task_id: str) -> list[StepRecord]:
        """列出任务的步骤（按 seq）。"""
        ...

    async def update_step(self, tenant_id: str, step_id: str, **fields: Any) -> StepRecord | None:
        """更新步骤可变字段。"""
        ...

    async def reset_steps_from(self, tenant_id: str, task_id: str, seq: int) -> int:
        """把 `seq` 及之后的步骤重置为 `pending`（WF1 `redo` 用）。"""
        ...

    async def task_stats(self, tenant_id: str, since: str, until: str) -> TaskStats:
        """窗口内任务聚合（M1/M3/M4/M5 的唯一计算入口，内存/PG 同公式）。

        Args:
            tenant_id: 企业 ID。
            since: 窗口起点（ISO 8601，含）。
            until: 窗口终点（ISO 8601，不含）。

        Returns:
            TaskStats: 原始聚合值；空窗口全 0。
        """
        ...


__all__: list[str] = ["TaskStore"]
