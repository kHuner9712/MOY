"""用量事件与月度聚合仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `UsageStore`，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖（拆分前它们同处一个上帝文件，看不出来；一拆就立刻变成环）。
独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`，
单向且无环，G-38 门禁据此判定「协议方法 ⊆ 两侧实现」。
"""

from __future__ import annotations

from typing import Protocol

from app.db.repositories.usage.records import (
    SCOPE_MEMBER,
    MonthlyUsageRecord,
    QuotaRecord,
    RangeEventStats,
    UsageEventRecord,
)


class UsageStore(Protocol):
    """计量存储契约（内存 / PostgreSQL 双实现）。"""

    async def append(self, record: UsageEventRecord) -> bool:
        """写入一条用量事件并按维度累加聚合（同一事务）。

        Returns:
            bool: 首次写入返回 True；幂等键命中（重放）返回 False。
        """
        ...

    async def monthly_rows(self, tenant_id: str, period: str) -> list[MonthlyUsageRecord]:
        """读取某周期的全部聚合行。"""
        ...

    async def daily_totals(
        self, tenant_id: str, period: str, *, scope_type: str = SCOPE_MEMBER, scope_id: str = ""
    ) -> dict[str, int]:
        """按自然日汇总额度（`YYYY-MM-DD → units`）。"""
        ...

    async def event_counts(self, tenant_id: str, user_id: str, period: str) -> dict[str, int]:
        """按能力统计事件条数（M4 的「次数」口径）。"""
        ...

    async def event_units_total(self, tenant_id: str, period: str) -> int:
        """事件表在该周期的额度总和（对账用）。"""
        ...

    async def quota(self, tenant_id: str) -> QuotaRecord:
        """读取套餐额度总量。"""
        ...

    async def range_stats(self, tenant_id: str, start_at: str, end_at: str) -> RangeEventStats:
        """统计时间区间内的事件级指标（运营后台统计）。"""
        ...


__all__: list[str] = ["UsageStore"]
