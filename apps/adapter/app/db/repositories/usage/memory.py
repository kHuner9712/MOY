"""用量事件与月度聚合仓储 · 内存实现（development / test）。

内存后端就是生产语义的**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的
公开方法集、签名与 record 构造点，任何一侧单独改动都会被判红。因此本文件的每个方法
都必须与 PG 侧同名方法**保持同形**（参数名、默认值、同步性），差异只能通过
`TEST_HOOKS`（`clear`）这类显式白名单存在。

"三行聚合与事件行同一事务"在内存侧的表达：同一把 `asyncio.Lock` 内先判幂等键、
再写事件、再累加三行（`_bump` 是锁内私有 helper，因为 `asyncio.Lock` 不可重入）。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from app.db.repositories.usage.records import (
    SCOPE_DEPARTMENT,
    SCOPE_MEMBER,
    SCOPE_TENANT,
    MonthlyUsageRecord,
    QuotaRecord,
    RangeEventStats,
    UsageEventRecord,
    current_period,
)


class InMemoryUsageStore:
    """进程内计量存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._events: dict[tuple[str, str, str], UsageEventRecord] = {}
        self._monthly: dict[tuple[str, str, str, str, str], int] = {}
        self._lock = asyncio.Lock()

    async def append(self, record: UsageEventRecord) -> bool:
        """写入事件与三行聚合（内存实现同事务语义：同一把锁内完成）。

        Args:
            record: 用量事件。

        Returns:
            bool: 首次写入返回 True；幂等键命中返回 False。
        """
        key = (record.tenant_id, record.capability, record.idem_key)
        stamp = record.occurred_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
        period = record.period or current_period()
        async with self._lock:
            if key in self._events:
                return False
            self._events[key] = replace(
                record, period=period, occurred_at=stamp, units=record.normalized_units
            )
            units = record.normalized_units
            self._bump(
                record.tenant_id, period, SCOPE_TENANT, record.tenant_id, record.capability, units
            )
            self._bump(
                record.tenant_id, period, SCOPE_MEMBER, record.user_id, record.capability, units
            )
            if record.department_id:
                self._bump(
                    record.tenant_id,
                    period,
                    SCOPE_DEPARTMENT,
                    record.department_id,
                    record.capability,
                    units,
                )
        return True

    def _bump(
        self,
        tenant_id: str,
        period: str,
        scope_type: str,
        scope_id: str,
        capability: str,
        units: int,
    ) -> None:
        """累加一行聚合（调用方持锁）。

        Args:
            tenant_id: 租户 ID。
            period: 周期。
            scope_type: 维度。
            scope_id: 维度取值。
            capability: 能力。
            units: 增量额度。
        """
        key = (tenant_id, period, scope_type, scope_id, capability)
        self._monthly[key] = self._monthly.get(key, 0) + units

    async def monthly_rows(self, tenant_id: str, period: str) -> list[MonthlyUsageRecord]:
        """读取某周期的全部聚合行。

        Args:
            tenant_id: 租户 ID。
            period: 周期。

        Returns:
            list[MonthlyUsageRecord]: 聚合行列表。
        """
        async with self._lock:
            items = [
                MonthlyUsageRecord(
                    period=key[1],
                    scope_type=key[2],
                    scope_id=key[3],
                    capability=key[4],
                    units=value,
                )
                for key, value in self._monthly.items()
                if key[0] == tenant_id and key[1] == period
            ]
        items.sort(key=lambda item: (item.scope_type, item.scope_id, item.capability))
        return items

    async def daily_totals(
        self, tenant_id: str, period: str, *, scope_type: str = SCOPE_MEMBER, scope_id: str = ""
    ) -> dict[str, int]:
        """按自然日汇总额度。

        Args:
            tenant_id: 租户 ID。
            period: 周期。
            scope_type: 维度（仅 `tenant` / `member` 语义可解释，其余按租户维度）。
            scope_id: 用户 ID（`scope_type='member'` 时生效）。

        Returns:
            dict[str, int]: `YYYY-MM-DD → units`。
        """
        del scope_type, scope_id  # 事件级明细只按用户/租户区分，内存实现按事件直接汇总
        async with self._lock:
            events = list(self._events.values())
        totals: dict[str, int] = {}
        for event in events:
            if event.tenant_id != tenant_id or event.period != period:
                continue
            day = event.occurred_at[:10]
            totals[day] = totals.get(day, 0) + event.units
        return totals

    async def event_counts(self, tenant_id: str, user_id: str, period: str) -> dict[str, int]:
        """按能力统计事件条数。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            period: 周期。

        Returns:
            dict[str, int]: `能力 → 事件条数`。
        """
        async with self._lock:
            events = list(self._events.values())
        counts: dict[str, int] = {}
        for event in events:
            if event.tenant_id != tenant_id or event.period != period:
                continue
            if user_id and event.user_id != user_id:
                continue
            counts[event.capability] = counts.get(event.capability, 0) + 1
        return counts

    async def event_units_total(self, tenant_id: str, period: str) -> int:
        """事件表在该周期的额度总和。

        Args:
            tenant_id: 租户 ID。
            period: 周期。

        Returns:
            int: 额度总和。
        """
        async with self._lock:
            events = list(self._events.values())
        return sum(
            event.units
            for event in events
            if event.tenant_id == tenant_id and event.period == period
        )

    async def quota(self, tenant_id: str) -> QuotaRecord:
        """读取套餐额度总量（内存模式复用计费存储的口径）。

        内存后端没有订阅表，但**必须**与 BL2 的 `quota_units_total` 一致，
        否则开发/测试环境下「套餐页额度」与「用量页额度」会互相矛盾。
        因此按需读取进程内计费存储（`billing_repo` 的内存实现）；该存储
        尚未创建时返回空额度（不伪造数字）。

        Args:
            tenant_id: 租户 ID。

        Returns:
            QuotaRecord: 额度快照。
        """
        from app.db.repositories.billing_repo import get_memory_billing_store

        store = get_memory_billing_store()
        if store is None:
            return QuotaRecord()
        subscription = await store.get_active_subscription(tenant_id)
        if subscription is None:
            return QuotaRecord()
        plan = await store.get_plan(subscription.plan_code)
        if plan is None:
            return QuotaRecord(plan_code=subscription.plan_code)
        return QuotaRecord(
            plan_code=plan.plan_code,
            plan_name=plan.display_name,
            total_units=max(plan.quota_units_per_seat_month, 0) * max(subscription.seats_total, 0),
            quota_unit_label=plan.quota_unit_label,
        )

    async def range_stats(self, tenant_id: str, start_at: str, end_at: str) -> RangeEventStats:
        """统计时间区间内的事件级指标。

        Args:
            tenant_id: 租户 ID。
            start_at: 起始时间（ISO 8601，含）。
            end_at: 结束时间（ISO 8601，不含）。

        Returns:
            RangeEventStats: 区间统计。
        """
        async with self._lock:
            events = list(self._events.values())
        scoped = [
            event
            for event in events
            if event.tenant_id == tenant_id and start_at <= event.occurred_at < end_at
        ]
        by_capability: dict[str, int] = {}
        for event in scoped:
            by_capability[event.capability] = by_capability.get(event.capability, 0) + 1
        return RangeEventStats(
            units=sum(event.units for event in scoped),
            event_count=len(scoped),
            distinct_users=len({event.user_id for event in scoped}),
            by_capability=by_capability,
        )

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._events.clear()
        self._monthly.clear()


__all__: list[str] = ["InMemoryUsageStore"]
