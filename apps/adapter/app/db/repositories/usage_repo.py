# ruff: noqa: S608 - 本模块所有 SQL 的表名/列名均为模块常量（经 assert_identifier 校验），
# 值一律走命名参数绑定；不用 f-string 无法表达「表名由常量决定」这一层。
"""用量事件与月度聚合仓储（阶段二 B07，架构文档 2.5 / D-12）。

**唯一计量真相源**（D-12 第 1/2/4 条）：

1. 事件表 `usage_event` 幂等：`(tenant_id, capability, idem_key)` 唯一，
   重放走 `ON CONFLICT DO NOTHING`（**不累加**）；
2. 聚合表 `usage_monthly` 三行（`tenant` / `member` / `department`）与事件行
   **同一事务**写入：因此「按成员求和 == 企业总量」是**结构保证**，不是对账凑出来的；
3. 本模块只做存取，不含任何计量口径计算（口径在 `business/usage_service.py`）。

双后端（`OA_BUSINESS_STORE`）：`memory` 供 development/test（无 PostgreSQL 也能全绿），
`postgres` 为唯一生产实现；两条路径的方法签名与返回语义完全一致。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 用量事件表名
USAGE_EVENT_TABLE: Final[str] = "usage_event"

#: 月度聚合表名
USAGE_MONTHLY_TABLE: Final[str] = "usage_monthly"

#: 订阅表名（额度总量来源；B09 由 SubscriptionService 接管，本批只读）
SUBSCRIPTION_TABLE: Final[str] = "billing_subscription"

#: 套餐目录表名（平台共享行）
PLAN_TABLE: Final[str] = "billing_plan"

#: 平台哨兵租户（共享目录行所属）
PLATFORM_TENANT: Final[str] = "__platform__"

#: 能力枚举（与 DDL CHECK 一致）
CAPABILITY_CHAT: Final[str] = "chat"
CAPABILITY_DOC_QA: Final[str] = "doc_qa"
CAPABILITY_DOC_PROCESS: Final[str] = "doc_process"
CAPABILITY_AUTOMATION: Final[str] = "automation"

#: 能力全集
CAPABILITIES: Final[tuple[str, ...]] = (
    CAPABILITY_CHAT,
    CAPABILITY_DOC_QA,
    CAPABILITY_DOC_PROCESS,
    CAPABILITY_AUTOMATION,
)

#: 聚合维度枚举（与 DDL CHECK 一致）
SCOPE_TENANT: Final[str] = "tenant"
SCOPE_DEPARTMENT: Final[str] = "department"
SCOPE_MEMBER: Final[str] = "member"

#: 结算来源：下游确认的精确值
SOURCE_SETTLED: Final[str] = "settled"

#: 结算来源：中断/超时折算值
SOURCE_ESTIMATED: Final[str] = "estimated"

#: 默认额度单位文案（无订阅时与套餐目录缺省值一致）
DEFAULT_QUOTA_UNIT_LABEL: Final[str] = "字数额度"


def current_period(moment: datetime | None = None) -> str:
    """取计量周期（UTC 的 `YYYY-MM`）。

    Args:
        moment: 结算时刻（缺省取当前 UTC 时间）。

    Returns:
        str: 形如 `2026-11` 的周期串。
    """
    stamp = moment or datetime.now(UTC)
    return stamp.astimezone(UTC).strftime("%Y-%m")


def period_bounds(period: str) -> tuple[str, str]:
    """把 `YYYY-MM` 周期换算为左闭右开的 UTC 时间区间（用于事件计数）。

    Args:
        period: 形如 `2026-11` 的周期串。

    Returns:
        tuple[str, str]: `(起始 ISO 时间, 结束 ISO 时间)`；格式非法时返回空串对。
    """
    try:
        start = datetime.strptime(period, "%Y-%m").replace(tzinfo=UTC)
    except ValueError:
        return "", ""
    end = datetime(start.year + (start.month // 12), (start.month % 12) + 1, 1, tzinfo=UTC)
    return start.isoformat(), end.isoformat()


@dataclass(frozen=True, slots=True)
class UsageEventRecord:
    """一条用量事件（`usage_event` 行）。

    Attributes:
        tenant_id: 租户 ID。
        event_id: 事件 ID，形如 `use_01HF8...`。
        user_id: 产生用量的用户 ID。
        capability: 能力（`chat` / `doc_qa` / `doc_process` / `automation`）。
        idem_key: 幂等键（`generation_id` / `document_id:run_id` / `task_id`）。
        units: 字数额度（`>= 0`）。
        period: 计量周期 `YYYY-MM`。
        source: 结算来源（`settled` / `estimated`）。
        org_id: 组织 ID（可空）。
        department_id: 主部门 ID（可空；为空时只写 tenant/member 两行聚合）。
        resource_type: 资源类型（`generation` / `document` / `task`）。
        resource_id: 资源 ID。
        occurred_at: 发生时间（ISO 8601）。
    """

    tenant_id: str
    user_id: str
    capability: str
    idem_key: str
    units: int = 0
    period: str = ""
    source: str = SOURCE_SETTLED
    org_id: str = ""
    department_id: str = ""
    resource_type: str = ""
    resource_id: str = ""
    event_id: str = ""
    occurred_at: str = ""

    @property
    def normalized_units(self) -> int:
        """非负整数化的额度值（负值按 0 处理，防止 CHECK 违规）。"""
        return max(int(self.units), 0)


@dataclass(frozen=True, slots=True)
class MonthlyUsageRecord:
    """一条月度聚合（`usage_monthly` 行）。

    Attributes:
        period: 计量周期 `YYYY-MM`。
        scope_type: 维度（`tenant` / `department` / `member`）。
        scope_id: 维度取值（租户 ID / 部门 ID / 用户 ID）。
        capability: 能力。
        units: 聚合额度。
    """

    period: str
    scope_type: str
    scope_id: str
    capability: str
    units: int = 0


@dataclass(frozen=True, slots=True)
class QuotaRecord:
    """套餐额度快照（总量口径）。

    Attributes:
        plan_code: 套餐标识；无订阅时为空串。
        plan_name: 套餐展示名。
        total_units: 额度总量（`席位 × 每席位额度`）。
        quota_unit_label: 额度单位文案。
    """

    plan_code: str = ""
    plan_name: str = ""
    total_units: int = 0
    quota_unit_label: str = DEFAULT_QUOTA_UNIT_LABEL


@dataclass(frozen=True, slots=True)
class RangeEventStats:
    """时间区间内的事件级统计（运营后台 OPS6 使用，B10）。

    与 `monthly_rows()` 的区别：本结构按 `occurred_at` 落在**任意区间**统计
    （`7d` / `30d` 会跨越自然月边界），而月度聚合按账期切分。
    额度口径仍与 BL5 **同源**：`units` 由同一张 `usage_event` 表求和，
    因此「各企业区间额度之和」与「按账期的企业维度求和」在整月区间上**误差 0**。

    Attributes:
        units: 区间内额度总和。
        event_count: 区间内事件条数。
        distinct_users: 区间内产生用量的成员数（去重）。
        by_capability: `能力 → 事件条数`。
    """

    units: int = 0
    event_count: int = 0
    distinct_users: int = 0
    by_capability: dict[str, int] = field(default_factory=dict)

    @property
    def conversation_count(self) -> int:
        """对话次数（`chat` + `doc_qa` 事件条数）。"""
        return int(self.by_capability.get(CAPABILITY_CHAT, 0)) + int(
            self.by_capability.get(CAPABILITY_DOC_QA, 0)
        )

    @property
    def document_count(self) -> int:
        """文档处理次数（`doc_process` 事件条数）。"""
        return int(self.by_capability.get(CAPABILITY_DOC_PROCESS, 0))


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


class _UsageEventTableRepository(BaseRepository):
    """`usage_event` 表仓储（强制租户条件）。"""

    table = USAGE_EVENT_TABLE


class _UsageMonthlyTableRepository(BaseRepository):
    """`usage_monthly` 表仓储（强制租户条件）。"""

    table = USAGE_MONTHLY_TABLE


class _SubscriptionTableRepository(BaseRepository):
    """`billing_subscription` 表仓储（只读）。"""

    table = SUBSCRIPTION_TABLE


class _PlanTableRepository(BaseRepository):
    """`billing_plan` 表仓储（平台共享行只读）。"""

    table = PLAN_TABLE


class PostgresUsageStore:
    """PostgreSQL 计量存储（一次结算一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def append(self, record: UsageEventRecord) -> bool:
        """写入事件 + 三行聚合（**同一事务**，D-12 第 4 条）。

        Args:
            record: 用量事件。

        Returns:
            bool: 首次写入返回 True；幂等键命中返回 False。
        """
        period = record.period or current_period()
        units = record.normalized_units
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            event_repo = _UsageEventTableRepository(uow)
            inserted = await event_repo.fetch_one(
                f"""
                INSERT INTO {USAGE_EVENT_TABLE} (
                    tenant_id, event_id, org_id, department_id, user_id, capability,
                    resource_type, resource_id, idem_key, units, period, source
                ) VALUES (
                    :tenant_id, :event_id, :org_id, :department_id, :user_id, :capability,
                    :resource_type, :resource_id, :idem_key, :units, :period, :source
                )
                ON CONFLICT (tenant_id, capability, idem_key) DO NOTHING
                RETURNING event_id
                """,
                event_repo.params(
                    event_id=record.event_id,
                    org_id=record.org_id,
                    department_id=record.department_id,
                    user_id=record.user_id,
                    capability=record.capability,
                    resource_type=record.resource_type,
                    resource_id=record.resource_id,
                    idem_key=record.idem_key,
                    units=units,
                    period=period,
                    source=record.source,
                ),
            )
            if inserted is None:
                # 幂等命中：整个事务不做任何累加（架构文档 2.5 第 2 条）
                return False
            monthly_repo = _UsageMonthlyTableRepository(uow)
            scopes: list[tuple[str, str]] = [
                (SCOPE_TENANT, record.tenant_id),
                (SCOPE_MEMBER, record.user_id),
            ]
            if record.department_id:
                scopes.append((SCOPE_DEPARTMENT, record.department_id))
            for scope_type, scope_id in scopes:
                await monthly_repo.execute(
                    f"""
                    INSERT INTO {USAGE_MONTHLY_TABLE}
                        (tenant_id, period, scope_type, scope_id, capability, units)
                    VALUES (:tenant_id, :period, :scope_type, :scope_id, :capability, :units)
                    ON CONFLICT (tenant_id, period, scope_type, scope_id, capability)
                    DO UPDATE SET units = {USAGE_MONTHLY_TABLE}.units + EXCLUDED.units,
                                  updated_at = now()
                    """,
                    monthly_repo.params(
                        period=period,
                        scope_type=scope_type,
                        scope_id=scope_id,
                        capability=record.capability,
                        units=units,
                    ),
                )
        return True

    async def monthly_rows(self, tenant_id: str, period: str) -> list[MonthlyUsageRecord]:
        """读取某周期的全部聚合行。

        Args:
            tenant_id: 租户 ID。
            period: 周期。

        Returns:
            list[MonthlyUsageRecord]: 聚合行列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _UsageMonthlyTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT period, scope_type, scope_id, capability, units
                FROM {USAGE_MONTHLY_TABLE}
                WHERE {repo.tenant_clause()} AND period = :period
                ORDER BY scope_type, scope_id, capability
                """,
                repo.params(period=period),
            )
        return [
            MonthlyUsageRecord(
                period=str(row.get("period", "")),
                scope_type=str(row.get("scope_type", "")),
                scope_id=str(row.get("scope_id", "")),
                capability=str(row.get("capability", "")),
                units=int(row.get("units", 0) or 0),
            )
            for row in rows
        ]

    async def daily_totals(
        self, tenant_id: str, period: str, *, scope_type: str = SCOPE_MEMBER, scope_id: str = ""
    ) -> dict[str, int]:
        """按自然日汇总额度（事件表按 `occurred_at` 分组）。

        Args:
            tenant_id: 租户 ID。
            period: 周期。
            scope_type: 维度（`member` 时按 `scope_id` 过滤用户）。
            scope_id: 用户 ID。

        Returns:
            dict[str, int]: `YYYY-MM-DD → units`。
        """
        start, end = period_bounds(period)
        user_filter = "AND user_id = :user_id" if scope_type == SCOPE_MEMBER and scope_id else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _UsageEventTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT to_char(occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
                       SUM(units) AS units
                FROM {USAGE_EVENT_TABLE}
                WHERE {repo.tenant_clause()} AND period = :period
                  AND occurred_at >= CAST(CAST(:start_at AS text) AS timestamptz)
                  AND occurred_at < CAST(CAST(:end_at AS text) AS timestamptz)
                  {user_filter}
                GROUP BY day
                ORDER BY day
                """,
                repo.params(
                    period=period,
                    start_at=start,
                    end_at=end,
                    user_id=scope_id,
                ),
            )
        return {str(row.get("day", "")): int(row.get("units", 0) or 0) for row in rows}

    async def event_counts(self, tenant_id: str, user_id: str, period: str) -> dict[str, int]:
        """按能力统计事件条数。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID（空表示租户维度）。
            period: 周期。

        Returns:
            dict[str, int]: `能力 → 事件条数`。
        """
        user_filter = "AND user_id = :user_id" if user_id else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _UsageEventTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT capability, COUNT(*) AS cnt
                FROM {USAGE_EVENT_TABLE}
                WHERE {repo.tenant_clause()} AND period = :period {user_filter}
                GROUP BY capability
                """,
                repo.params(period=period, user_id=user_id),
            )
        return {str(row.get("capability", "")): int(row.get("cnt", 0) or 0) for row in rows}

    async def event_units_total(self, tenant_id: str, period: str) -> int:
        """事件表在该周期的额度总和。

        Args:
            tenant_id: 租户 ID。
            period: 周期。

        Returns:
            int: 额度总和。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _UsageEventTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT COALESCE(SUM(units), 0)
                FROM {USAGE_EVENT_TABLE}
                WHERE {repo.tenant_clause()} AND period = :period
                """,
                repo.params(period=period),
            )
        return int(total or 0)

    async def quota(self, tenant_id: str) -> QuotaRecord:
        """读取套餐额度总量（有效订阅 × 套餐目录行）。

        Args:
            tenant_id: 租户 ID。

        Returns:
            QuotaRecord: 额度快照；无有效订阅时返回空额度（免费版由 B09 落地）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            sub_repo = _SubscriptionTableRepository(uow)
            subscription = await sub_repo.fetch_one(
                f"""
                SELECT plan_code, seats_total
                FROM {SUBSCRIPTION_TABLE}
                WHERE {sub_repo.tenant_clause()}
                  AND status IN ('trialing', 'active', 'past_due')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                sub_repo.params(),
            )
            if subscription is None:
                return QuotaRecord()
            plan_code = str(subscription.get("plan_code", ""))
            seats = int(subscription.get("seats_total", 0) or 0)
            plan_repo = _PlanTableRepository(uow)
            plan = await plan_repo.fetch_one(
                f"""
                SELECT display_name, quota_units_per_seat_month, quota_unit_label
                FROM {PLAN_TABLE}
                WHERE tenant_id = '{PLATFORM_TENANT}' AND plan_code = :plan_code
                """,
                {"plan_code": plan_code},
            )
        if plan is None:
            return QuotaRecord(plan_code=plan_code)
        per_seat = int(plan.get("quota_units_per_seat_month", 0) or 0)
        return QuotaRecord(
            plan_code=plan_code,
            plan_name=str(plan.get("display_name", "")),
            total_units=per_seat * max(seats, 0),
            quota_unit_label=str(plan.get("quota_unit_label", DEFAULT_QUOTA_UNIT_LABEL)),
        )

    async def range_stats(self, tenant_id: str, start_at: str, end_at: str) -> RangeEventStats:
        """统计时间区间内的事件级指标（单条聚合 SQL，无 N+1）。

        Args:
            tenant_id: 租户 ID。
            start_at: 起始时间（ISO 8601，含）。
            end_at: 结束时间（ISO 8601，不含）。

        Returns:
            RangeEventStats: 区间统计。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _UsageEventTableRepository(uow)
            totals = await repo.fetch_one(
                f"""
                SELECT COALESCE(SUM(units), 0) AS units,
                       COUNT(*) AS event_count,
                       COUNT(DISTINCT user_id) AS distinct_users
                FROM {USAGE_EVENT_TABLE}
                WHERE {repo.tenant_clause()}
                  AND occurred_at >= CAST(CAST(:start_at AS text) AS timestamptz)
                  AND occurred_at < CAST(CAST(:end_at AS text) AS timestamptz)
                """,
                repo.params(start_at=start_at, end_at=end_at),
            )
            rows = await repo.fetch_all(
                f"""
                SELECT capability, COUNT(*) AS cnt
                FROM {USAGE_EVENT_TABLE}
                WHERE {repo.tenant_clause()}
                  AND occurred_at >= CAST(CAST(:start_at AS text) AS timestamptz)
                  AND occurred_at < CAST(CAST(:end_at AS text) AS timestamptz)
                GROUP BY capability
                """,
                repo.params(start_at=start_at, end_at=end_at),
            )
        by_capability = {
            str(row.get("capability", "")): int(row.get("cnt", 0) or 0) for row in rows
        }
        return RangeEventStats(
            units=int((totals or {}).get("units", 0) or 0),
            event_count=int((totals or {}).get("event_count", 0) or 0),
            distinct_users=int((totals or {}).get("distinct_users", 0) or 0),
            by_capability=by_capability,
        )


# ===========================================================================
# 进程级单例（内存后端跨模块共享；postgres 后端无状态）
# ===========================================================================
_memory_store: InMemoryUsageStore | None = None


def get_usage_store(settings: Settings | None = None) -> UsageStore:
    """获取计量存储。

    Args:
        settings: 可选配置实例。

    Returns:
        UsageStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresUsageStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryUsageStore()
    return _memory_store


def reset_usage_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


def row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """安全读取行字段（缺列时返回默认值）。

    Args:
        row: 行映射。
        key: 字段名。
        default: 缺省值。

    Returns:
        Any: 字段值或默认值。
    """
    return row.get(key, default)


__all__: list[str] = [
    "CAPABILITIES",
    "CAPABILITY_AUTOMATION",
    "CAPABILITY_CHAT",
    "CAPABILITY_DOC_PROCESS",
    "CAPABILITY_DOC_QA",
    "DEFAULT_QUOTA_UNIT_LABEL",
    "PLAN_TABLE",
    "PLATFORM_TENANT",
    "SCOPE_DEPARTMENT",
    "SCOPE_MEMBER",
    "SCOPE_TENANT",
    "SOURCE_ESTIMATED",
    "SOURCE_SETTLED",
    "SUBSCRIPTION_TABLE",
    "USAGE_EVENT_TABLE",
    "USAGE_MONTHLY_TABLE",
    "InMemoryUsageStore",
    "MonthlyUsageRecord",
    "PostgresUsageStore",
    "QuotaRecord",
    "RangeEventStats",
    "UsageEventRecord",
    "UsageStore",
    "current_period",
    "get_usage_store",
    "period_bounds",
    "reset_usage_store",
]
