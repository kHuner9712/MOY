# ruff: noqa: S608 - 本模块所有 SQL 的表名/列名均为模块常量（经 assert_identifier 校验），
# 值一律走命名参数绑定；不用 f-string 无法表达「表名由常量决定」这一层。


"""用量事件与月度聚合仓储 · PostgreSQL 实现（生产）。

**唯一计量真相源**（D-12 第 1/2/4 条）：

1. 事件表 `usage_event` 幂等：`(tenant_id, capability, idem_key)` 唯一，
   重放走 `ON CONFLICT DO NOTHING`（**不累加**）；
2. 聚合表 `usage_monthly` 三行（`tenant` / `member` / `department`）与事件行
   **同一事务**写入：因此「按成员求和 == 企业总量」是**结构保证**，不是对账凑出来的；
3. 本仓储只做存取，不含任何计量口径计算（口径在 `business/usage_service.py`）。

四张表：

| 表 | 作用 |
| --- | --- |
| `usage_event` | 用量事件（幂等写入） |
| `usage_monthly` | 月度聚合（三行/事件） |
| `billing_subscription` | 额度总量来源（只读） |
| `billing_plan` | 套餐目录（平台共享行，只读） |

表名常量与四个 `_*TableRepository` 放在本文件（而不是 `records.py`）：它们只服务
PG 实现，内存后端用不到。
"""

from __future__ import annotations

from typing import Final

from app.core.config import Settings, get_settings
from app.db.repositories.base import BaseRepository
from app.db.repositories.usage.records import (
    DEFAULT_QUOTA_UNIT_LABEL,
    SCOPE_DEPARTMENT,
    SCOPE_MEMBER,
    SCOPE_TENANT,
    MonthlyUsageRecord,
    QuotaRecord,
    RangeEventStats,
    UsageEventRecord,
    current_period,
    period_bounds,
)
from app.db.schema_guard import usage_dimension_supported
from app.db.session import tenant_scope

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
        # 四列的唯一读取口径（架构 §7.4）：旧库没有这些列时降级为不写，而不是报错
        dimensions = await usage_dimension_supported(self._settings)
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            event_repo = _UsageEventTableRepository(uow)
            # 两条**静态** SQL 而不是拼接列名：`test_sql_insert_consistency` 会静态
            # 解析 INSERT 的列清单与 DDL 对账（拼接出的列名它看不见，等于把守门人绕过去）。
            if dimensions:
                inserted = await event_repo.fetch_one(
                    f"""
                    INSERT INTO {USAGE_EVENT_TABLE} (
                        tenant_id, event_id, org_id, department_id, user_id, capability,
                        resource_type, resource_id, idem_key, units, period, source,
                        profile, provider, prompt_tokens, completion_tokens
                    ) VALUES (
                        :tenant_id, :event_id, :org_id, :department_id, :user_id, :capability,
                        :resource_type, :resource_id, :idem_key, :units, :period, :source,
                        :profile, :provider, :prompt_tokens, :completion_tokens
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
                        profile=record.profile,
                        provider=record.provider,
                        prompt_tokens=max(0, int(record.prompt_tokens)),
                        completion_tokens=max(0, int(record.completion_tokens)),
                    ),
                )
            else:
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


__all__: list[str] = [
    "PLAN_TABLE",
    "PLATFORM_TENANT",
    "SUBSCRIPTION_TABLE",
    "USAGE_EVENT_TABLE",
    "USAGE_MONTHLY_TABLE",
    "PostgresUsageStore",
]
