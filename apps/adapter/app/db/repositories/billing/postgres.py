# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。


"""计费域仓储 · PostgreSQL 实现（生产）。

六张表：

| 表 | 作用 |
| --- | --- |
| `billing_plan` | 套餐目录（平台共享行，`tenant_id='__platform__'`，运营可改价） |
| `billing_subscription` | 订阅（企业 × 套餐，`status` 决定是否 `ACTIVE_SUBSCRIPTION_STATUSES`） |
| `billing_order` | 订单（新购/续费/升级/加席位，`expires_at` 到期关单） |
| `billing_invoice` | 账单（一单一账单） |
| `payment_callback_log` | 支付回调留痕（幂等键 + 处理标记） |
| `tenant` | 企业主表（计费视角的 `name` / `status`） |

**两条口径**（与拆分前一致）：金额一律整数分（`*_cents`），不做浮点；时间一律
ISO 8601 文本进出、SQL 侧 `timestamptz`。

本文件还承载**行映射器**（`_to_plan` / `_to_tenant` / `_to_subscription` /
`_to_order` / `_to_invoice` / `_to_callback`）、JSONB 与时间还原
（`_json_object` / `_timestamp` / `to_iso`）与三条查询共用的列清单 `_ORDER_COLUMNS`：
它们只服务"从 PG 行还原 record"，内存后端直接拿对象。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.base import BaseRepository
from app.db.repositories.billing.records import (
    ACTIVE_SUBSCRIPTION_STATUSES,
    CHANNEL_WECHAT_PAY,
    INVOICE_PENDING,
    ORDER_CLOSED,
    ORDER_MUTABLE_FIELDS,
    ORDER_PENDING,
    ORDER_TYPE_NEW,
    PAYMENT_ONLINE,
    PLAN_AVAILABLE,
    PLAN_FREE,
    PLATFORM_TENANT,
    SUBSCRIPTION_ACTIVE,
    TENANT_ACTIVE,
    CallbackLogRecord,
    InvoiceRecord,
    OrderRecord,
    PlanRecord,
    SubscriptionRecord,
    TenantRecord,
    now_iso,
    to_iso,
)
from app.db.session import platform_scope, tenant_scope

#: 套餐目录表名
PLAN_TABLE: Final[str] = "billing_plan"

#: 订阅表名
SUBSCRIPTION_TABLE: Final[str] = "billing_subscription"

#: 订单表名
ORDER_TABLE: Final[str] = "billing_order"

#: 账单表名
INVOICE_TABLE: Final[str] = "billing_invoice"

#: 支付回调留痕表名
CALLBACK_TABLE: Final[str] = "payment_callback_log"

#: 企业主表名
TENANT_TABLE: Final[str] = "tenant"


class _PlanTableRepository(BaseRepository):
    """`billing_plan` 表仓储（平台共享行只读）。"""

    table = PLAN_TABLE


class _SubscriptionTableRepository(BaseRepository):
    """`billing_subscription` 表仓储。"""

    table = SUBSCRIPTION_TABLE


class _OrderTableRepository(BaseRepository):
    """`billing_order` 表仓储。"""

    table = ORDER_TABLE


class _InvoiceTableRepository(BaseRepository):
    """`billing_invoice` 表仓储。"""

    table = INVOICE_TABLE


class _CallbackTableRepository(BaseRepository):
    """`payment_callback_log` 表仓储。"""

    table = CALLBACK_TABLE


class _TenantTableRepository(BaseRepository):
    """`tenant` 表仓储。"""

    table = TENANT_TABLE


class PostgresBillingStore:
    """PostgreSQL 计费存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 套餐
    # ------------------------------------------------------------------
    async def list_plans(self) -> list[PlanRecord]:
        """列出全部套餐。

        Returns:
            list[PlanRecord]: 套餐列表。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _PlanTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, plan_code, display_name, price_per_seat_month_cents, currency,
                       min_seats, quota_units_per_seat_month, quota_unit_label, purchasable_quota,
                       features, status, trial_days, sort_order
                FROM {PLAN_TABLE}
                WHERE tenant_id = :platform
                ORDER BY sort_order, plan_code
                """,
                {"tenant_id": PLATFORM_TENANT, "platform": PLATFORM_TENANT},
            )
        return [_to_plan(row) for row in rows]

    async def get_plan(self, plan_code: str) -> PlanRecord | None:
        """按标识读取套餐。

        Args:
            plan_code: 套餐标识。

        Returns:
            PlanRecord | None: 套餐；不存在返回 None。
        """
        if not plan_code:
            return None
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _PlanTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, plan_code, display_name, price_per_seat_month_cents, currency,
                       min_seats, quota_units_per_seat_month, quota_unit_label, purchasable_quota,
                       features, status, trial_days, sort_order
                FROM {PLAN_TABLE}
                WHERE tenant_id = :platform AND plan_code = :plan_code
                """,
                {"tenant_id": PLATFORM_TENANT, "platform": PLATFORM_TENANT, "plan_code": plan_code},
            )
        return _to_plan(row) if row is not None else None

    # ------------------------------------------------------------------
    # 企业
    # ------------------------------------------------------------------
    async def ensure_tenant(self, tenant_id: str, name: str = "") -> TenantRecord:
        """读取或登记企业行。

        Args:
            tenant_id: 企业 ID。
            name: 企业名称。

        Returns:
            TenantRecord: 企业行。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _TenantTableRepository(uow)
            await repo.execute(
                f"""
                INSERT INTO {TENANT_TABLE} (tenant_id, name, plan_code)
                VALUES (:tenant_id, :name, :plan_code)
                ON CONFLICT (tenant_id) DO NOTHING
                """,
                repo.params(name=name or tenant_id, plan_code=PLAN_FREE),
            )
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, name, status, deploy_mode, plan_code, suspended_reason,
                       created_at, updated_at
                FROM {TENANT_TABLE}
                WHERE {repo.tenant_clause()}
                """,
            )
        if row is None:  # pragma: no cover - 刚插入即查不到属数据库异常
            raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail="tenant upsert lost")
        return _to_tenant(row)

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """按 ID 读取企业行。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantRecord | None: 企业行；不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TenantTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, name, status, deploy_mode, plan_code, suspended_reason,
                       created_at, updated_at
                FROM {TENANT_TABLE}
                WHERE {repo.tenant_clause()}
                """,
            )
        return _to_tenant(row) if row is not None else None

    async def update_tenant(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        plan_code: str | None = None,
        suspended_reason: str | None = None,
    ) -> TenantRecord | None:
        """更新企业行。

        Args:
            tenant_id: 企业 ID。
            status: 新状态。
            plan_code: 新套餐。
            suspended_reason: 停用原因。

        Returns:
            TenantRecord | None: 更新后的企业行；企业不存在返回 None。
        """
        assignments = ["updated_at = now()"]
        parameters: dict[str, Any] = {"tenant_id": tenant_id}
        if status is not None:
            assignments.append("status = :status")
            parameters["status"] = status
        if plan_code is not None:
            assignments.append("plan_code = :plan_code")
            parameters["plan_code"] = plan_code
        if suspended_reason is not None:
            assignments.append("suspended_reason = :suspended_reason")
            parameters["suspended_reason"] = suspended_reason
        async with platform_scope(self._settings) as uow:
            repo = _TenantTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {TENANT_TABLE}
                SET {", ".join(assignments)}
                WHERE tenant_id = :tenant_id
                RETURNING tenant_id, name, status, deploy_mode, plan_code, suspended_reason,
                          created_at, updated_at
                """,
                parameters,
            )
        return _to_tenant(row) if row is not None else None

    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """列出企业（平台视图）。

        Args:
            keyword: 名称关键字。
            status: 状态过滤。

        Returns:
            list[TenantRecord]: 企业列表。
        """
        conditions = ["tenant_id <> :platform"]
        parameters: dict[str, Any] = {"tenant_id": PLATFORM_TENANT, "platform": PLATFORM_TENANT}
        if keyword:
            conditions.append("name ILIKE :keyword")
            parameters["keyword"] = f"%{keyword}%"
        if status:
            conditions.append("status = :status")
            parameters["status"] = status
        where_clause = " AND ".join(conditions)
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _TenantTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, name, status, deploy_mode, plan_code, suspended_reason,
                       created_at, updated_at
                FROM {TENANT_TABLE}
                WHERE {where_clause}
                ORDER BY created_at DESC, tenant_id
                """,
                parameters,
            )
        return [_to_tenant(row) for row in rows]

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------
    async def get_active_subscription(self, tenant_id: str) -> SubscriptionRecord | None:
        """读取有效订阅。

        Args:
            tenant_id: 企业 ID。

        Returns:
            SubscriptionRecord | None: 有效订阅；无则返回 None。
        """
        statuses = ", ".join(f"'{item}'" for item in ACTIVE_SUBSCRIPTION_STATUSES)
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _SubscriptionTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, subscription_id, plan_code, status, seats_total, seats_used,
                       started_at, current_period_start, current_period_end, auto_renew,
                       pending_plan_code, pending_change_at, grace_until, created_at, updated_at
                FROM {SUBSCRIPTION_TABLE}
                WHERE {repo.tenant_clause()} AND status IN ({statuses})
                ORDER BY started_at DESC
                LIMIT 1
                """,
            )
        return _to_subscription(row) if row is not None else None

    async def save_subscription(self, record: SubscriptionRecord) -> SubscriptionRecord:
        """写入（或按 `subscription_id` 更新）订阅。

        Args:
            record: 订阅行。

        Returns:
            SubscriptionRecord: 落库后的订阅行。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _SubscriptionTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {SUBSCRIPTION_TABLE} (
                    tenant_id, subscription_id, plan_code, status, seats_total, seats_used,
                    started_at, current_period_start, current_period_end, auto_renew,
                    pending_plan_code, pending_change_at, grace_until
                ) VALUES (
                    :tenant_id, :subscription_id, :plan_code, :status, :seats_total, :seats_used,
                    CAST(CAST(:started_at AS text) AS timestamptz),
                    CAST(CAST(:period_start AS text) AS timestamptz),
                    CAST(CAST(:period_end AS text) AS timestamptz),
                    :auto_renew, NULLIF(:pending_plan_code, ''),
                    CAST(NULLIF(:pending_change_at, '') AS timestamptz),
                    CAST(NULLIF(:grace_until, '') AS timestamptz)
                )
                ON CONFLICT (tenant_id, subscription_id) DO UPDATE SET
                    plan_code = EXCLUDED.plan_code,
                    status = EXCLUDED.status,
                    seats_total = EXCLUDED.seats_total,
                    seats_used = EXCLUDED.seats_used,
                    current_period_start = EXCLUDED.current_period_start,
                    current_period_end = EXCLUDED.current_period_end,
                    auto_renew = EXCLUDED.auto_renew,
                    pending_plan_code = EXCLUDED.pending_plan_code,
                    pending_change_at = EXCLUDED.pending_change_at,
                    grace_until = EXCLUDED.grace_until,
                    updated_at = now()
                RETURNING tenant_id, subscription_id, plan_code, status, seats_total, seats_used,
                          started_at, current_period_start, current_period_end, auto_renew,
                          pending_plan_code, pending_change_at, grace_until, created_at, updated_at
                """,
                repo.params(
                    subscription_id=record.subscription_id,
                    plan_code=record.plan_code,
                    status=record.status,
                    seats_total=record.seats_total,
                    seats_used=record.seats_used,
                    started_at=record.started_at or now_iso(),
                    period_start=record.current_period_start or now_iso(),
                    period_end=record.current_period_end or now_iso(),
                    auto_renew=record.auto_renew,
                    pending_plan_code=record.pending_plan_code,
                    pending_change_at=record.pending_change_at,
                    grace_until=record.grace_until,
                ),
            )
        if row is None:  # pragma: no cover - RETURNING 缺失属数据库异常
            raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail="subscription upsert lost")
        return _to_subscription(row)

    # ------------------------------------------------------------------
    # 订单
    # ------------------------------------------------------------------
    async def get_order(self, tenant_id: str, order_id: str) -> OrderRecord | None:
        """按 ID 读取订单。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。

        Returns:
            OrderRecord | None: 订单；不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _OrderTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT {_ORDER_COLUMNS}
                FROM {ORDER_TABLE}
                WHERE {repo.tenant_clause()} AND order_id = :order_id
                """,
                repo.params(order_id=order_id),
            )
        return _to_order(row) if row is not None else None

    async def find_order_by_id(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 跨企业读取订单（平台作用域，仅回调/核销路径）。

        Args:
            order_id: 订单 ID。

        Returns:
            OrderRecord | None: 订单；不存在返回 None。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _OrderTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT {_ORDER_COLUMNS}
                FROM {ORDER_TABLE}
                WHERE order_id = :order_id
                LIMIT 1
                """,
                {"tenant_id": PLATFORM_TENANT, "order_id": order_id},
            )
        return _to_order(row) if row is not None else None

    async def find_pending_order(self, tenant_id: str) -> OrderRecord | None:
        """读取该企业的待支付订单。

        Args:
            tenant_id: 企业 ID。

        Returns:
            OrderRecord | None: 待支付订单；无则返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _OrderTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT {_ORDER_COLUMNS}
                FROM {ORDER_TABLE}
                WHERE {repo.tenant_clause()} AND status = :status
                ORDER BY created_at
                LIMIT 1
                """,
                repo.params(status=ORDER_PENDING),
            )
        return _to_order(row) if row is not None else None

    async def create_order(self, record: OrderRecord) -> OrderRecord:
        """写入订单（部分唯一索引兜底：同企业仅一个待支付订单）。

        Args:
            record: 订单行。

        Returns:
            OrderRecord: 落库后的订单；**已有待支付订单时返回既有行**。
        """
        bank_info = (
            json.dumps(record.bank_info, ensure_ascii=False, sort_keys=True)
            if record.bank_info
            else None
        )
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _OrderTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {ORDER_TABLE} (
                    tenant_id, order_id, order_type, plan_code, seats, amount_cents, currency,
                    status, payment_method, pay_url, bank_info, payment_no_masked,
                    paid_amount_cents, paid_at, confirmed_by, confirm_remark, expires_at, created_by
                ) VALUES (
                    :tenant_id, :order_id, :order_type, :plan_code, :seats, :amount_cents,
                    :currency, :status, :payment_method, NULLIF(:pay_url, ''),
                    CAST(:bank_info AS jsonb), NULLIF(:payment_no_masked, ''),
                    :paid_amount_cents, CAST(NULLIF(:paid_at, '') AS timestamptz),
                    NULLIF(:confirmed_by, ''), NULLIF(:confirm_remark, ''),
                    CAST(CAST(:expires_at AS text) AS timestamptz), :created_by
                )
                ON CONFLICT (tenant_id) WHERE status = 'pending_payment' DO NOTHING
                RETURNING {_ORDER_COLUMNS}
                """,
                repo.params(
                    order_id=record.order_id,
                    order_type=record.order_type,
                    plan_code=record.plan_code,
                    seats=record.seats,
                    amount_cents=record.amount_cents,
                    currency=record.currency,
                    status=record.status,
                    payment_method=record.payment_method,
                    pay_url=record.pay_url,
                    bank_info=bank_info,
                    payment_no_masked=record.payment_no_masked,
                    paid_amount_cents=record.paid_amount_cents,
                    paid_at=record.paid_at,
                    confirmed_by=record.confirmed_by,
                    confirm_remark=record.confirm_remark,
                    expires_at=record.expires_at or now_iso(),
                    created_by=record.created_by,
                ),
            )
            if row is not None:
                return _to_order(row)
            existing = await repo.fetch_one(
                f"""
                SELECT {_ORDER_COLUMNS}
                FROM {ORDER_TABLE}
                WHERE {repo.tenant_clause()} AND status = :status
                ORDER BY created_at
                LIMIT 1
                """,
                repo.params(status=ORDER_PENDING),
            )
        if existing is None:  # pragma: no cover - 冲突却查不到属并发异常
            raise ApiError(
                ErrorCode.PENDING_ORDER_EXISTS, internal_detail="pending order conflict without row"
            )
        return _to_order(existing)

    async def update_order(
        self, tenant_id: str, order_id: str, **fields: Any
    ) -> OrderRecord | None:
        """更新订单可变字段。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。
            **fields: 可变字段（白名单校验）。

        Returns:
            OrderRecord | None: 更新后的订单；不存在返回 None。

        Raises:
            ApiError: 出现白名单外的字段（20001）。
        """
        unknown = set(fields) - ORDER_MUTABLE_FIELDS
        if unknown:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"order fields not mutable: {sorted(unknown)}",
            )
        if not fields:
            return await self.get_order(tenant_id, order_id)
        assignments = ["updated_at = now()"]
        parameters: dict[str, Any] = {"tenant_id": tenant_id, "order_id": order_id}
        for key, value in fields.items():
            if key in {"bank_info"}:
                assignments.append(f"{key} = CAST(NULLIF(:{key}, '') AS jsonb)")
                parameters[key] = (
                    json.dumps(value, ensure_ascii=False, sort_keys=True) if value else ""
                )
            elif key in {"paid_at", "expires_at"}:
                assignments.append(f"{key} = CAST(NULLIF(:{key}, '') AS timestamptz)")
                parameters[key] = value or ""
            else:
                assignments.append(f"{key} = :{key}")
                parameters[key] = value
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _OrderTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {ORDER_TABLE}
                SET {", ".join(assignments)}
                WHERE tenant_id = :tenant_id AND order_id = :order_id
                RETURNING {_ORDER_COLUMNS}
                """,
                parameters,
            )
        return _to_order(row) if row is not None else None

    async def list_orders(
        self, tenant_id: str, *, status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """分页列出企业订单。

        Args:
            tenant_id: 企业 ID。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[OrderRecord], int]: 当前页订单与总条数。
        """
        status_filter = "AND status = :status" if status else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _OrderTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT {_ORDER_COLUMNS}
                FROM {ORDER_TABLE}
                WHERE {repo.tenant_clause()} {status_filter}
                ORDER BY created_at DESC, order_id DESC
                LIMIT :limit OFFSET :offset
                """,
                repo.params(status=status, limit=limit, offset=max(offset, 0)),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {ORDER_TABLE}
                WHERE {repo.tenant_clause()} {status_filter}
                """,
                repo.params(status=status),
            )
        return [_to_order(row) for row in rows], int(total or 0)

    async def list_all_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨企业分页列出订单（运营视图）。

        Args:
            tenant_id: 企业过滤（空表示全部）。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[OrderRecord], int]: 当前页订单与总条数。
        """
        conditions = ["1 = 1"]
        parameters: dict[str, Any] = {
            "tenant_id": PLATFORM_TENANT,
            "limit": limit,
            "offset": max(offset, 0),
        }
        if tenant_id:
            conditions.append("tenant_id = :owner")
            parameters["owner"] = tenant_id
        if status:
            conditions.append("status = :status")
            parameters["status"] = status
        where_clause = " AND ".join(conditions)
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _OrderTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT {_ORDER_COLUMNS}
                FROM {ORDER_TABLE}
                WHERE {where_clause}
                ORDER BY created_at DESC, order_id DESC
                LIMIT :limit OFFSET :offset
                """,
                parameters,
            )
            total = await repo.fetch_scalar(
                f"SELECT COUNT(*) FROM {ORDER_TABLE} WHERE {where_clause}", parameters
            )
        return [_to_order(row) for row in rows], int(total or 0)

    async def expire_orders(self, deadline_iso: str, tenant_id: str = "") -> list[OrderRecord]:
        """把已过期的待支付订单置为 `closed`。

        Args:
            deadline_iso: 判定阈值。
            tenant_id: 企业过滤（空表示全部）。

        Returns:
            list[OrderRecord]: 被关闭的订单。
        """
        parameters: dict[str, Any] = {
            "tenant_id": tenant_id or PLATFORM_TENANT,
            "status": ORDER_PENDING,
            "closed": ORDER_CLOSED,
            "deadline": deadline_iso,
        }
        if tenant_id:
            async with tenant_scope(tenant_id, settings=self._settings) as uow:
                repo = _OrderTableRepository(uow)
                rows = await repo.fetch_all(
                    f"""
                    UPDATE {ORDER_TABLE}
                    SET status = :closed, updated_at = now()
                    WHERE status = :status
                      AND expires_at < CAST(CAST(:deadline AS text) AS timestamptz)
                    RETURNING {_ORDER_COLUMNS}
                    """,
                    parameters,
                )
            return [_to_order(row) for row in rows]
        async with platform_scope(self._settings) as uow:
            repo = _OrderTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                UPDATE {ORDER_TABLE}
                SET status = :closed, updated_at = now()
                WHERE status = :status
                  AND expires_at < CAST(CAST(:deadline AS text) AS timestamptz)
                RETURNING {_ORDER_COLUMNS}
                """,
                parameters,
            )
        return [_to_order(row) for row in rows]

    # ------------------------------------------------------------------
    # 账单
    # ------------------------------------------------------------------
    async def ensure_invoice(self, record: InvoiceRecord) -> InvoiceRecord:
        """写入账单（一单一票，`uq_invoice_order` 兜底）。

        Args:
            record: 账单行。

        Returns:
            InvoiceRecord: 落库后的账单；已开票时返回既有行。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _InvoiceTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {INVOICE_TABLE} (
                    tenant_id, invoice_id, order_id, period, amount_cents, currency, status,
                    download_path, issued_at
                ) VALUES (
                    :tenant_id, :invoice_id, :order_id, :period, :amount_cents, :currency, :status,
                    NULLIF(:download_path, ''), CAST(NULLIF(:issued_at, '') AS timestamptz)
                )
                ON CONFLICT (tenant_id, order_id) DO NOTHING
                RETURNING tenant_id, invoice_id, order_id, period, amount_cents, currency,
                          status, download_path, issued_at, created_at
                """,
                repo.params(
                    invoice_id=record.invoice_id,
                    order_id=record.order_id,
                    period=record.period,
                    amount_cents=record.amount_cents,
                    currency=record.currency,
                    status=record.status,
                    download_path=record.download_path,
                    issued_at=record.issued_at,
                ),
            )
            if row is None:
                row = await repo.fetch_one(
                    f"""
                    SELECT tenant_id, invoice_id, order_id, period, amount_cents, currency,
                           status, download_path, issued_at, created_at
                    FROM {INVOICE_TABLE}
                    WHERE {repo.tenant_clause()} AND order_id = :order_id
                    """,
                    repo.params(order_id=record.order_id),
                )
        if row is None:  # pragma: no cover - 冲突却查不到属并发异常
            raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail="invoice upsert lost")
        return _to_invoice(row)

    async def get_invoice_by_order(self, tenant_id: str, order_id: str) -> InvoiceRecord | None:
        """按订单读取账单。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。

        Returns:
            InvoiceRecord | None: 账单；未开票返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _InvoiceTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, invoice_id, order_id, period, amount_cents, currency,
                       status, download_path, issued_at, created_at
                FROM {INVOICE_TABLE}
                WHERE {repo.tenant_clause()} AND order_id = :order_id
                """,
                repo.params(order_id=order_id),
            )
        return _to_invoice(row) if row is not None else None

    async def list_invoices(self, tenant_id: str, period: str = "") -> list[InvoiceRecord]:
        """列出企业账单。

        Args:
            tenant_id: 企业 ID。
            period: 账期过滤。

        Returns:
            list[InvoiceRecord]: 账单列表。
        """
        period_filter = "AND period = :period" if period else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _InvoiceTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, invoice_id, order_id, period, amount_cents, currency,
                       status, download_path, issued_at, created_at
                FROM {INVOICE_TABLE}
                WHERE {repo.tenant_clause()} {period_filter}
                ORDER BY created_at DESC, invoice_id DESC
                """,
                repo.params(period=period),
            )
        return [_to_invoice(row) for row in rows]

    # ------------------------------------------------------------------
    # 回调留痕
    # ------------------------------------------------------------------
    async def append_callback(self, record: CallbackLogRecord) -> bool:
        """写入回调留痕（同渠道同流水号只允许一条）。

        Args:
            record: 回调留痕。

        Returns:
            bool: 首次写入返回 True；重复流水号返回 False。
        """
        async with platform_scope(self._settings) as uow:
            repo = _CallbackTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {CALLBACK_TABLE} (
                    tenant_id, callback_id, channel, external_txn_id, order_id, amount_cents,
                    signature_ok, payload_sha256, processed
                ) VALUES (
                    :tenant_id, :callback_id, :channel, :external_txn_id, NULLIF(:order_id, ''),
                    :amount_cents, :signature_ok, :payload_sha256, :processed
                )
                ON CONFLICT (channel, external_txn_id) DO NOTHING
                RETURNING callback_id
                """,
                {
                    "tenant_id": record.tenant_id or PLATFORM_TENANT,
                    "callback_id": record.callback_id,
                    "channel": record.channel,
                    "external_txn_id": record.external_txn_id,
                    "order_id": record.order_id,
                    "amount_cents": record.amount_cents,
                    "signature_ok": record.signature_ok,
                    "payload_sha256": record.payload_sha256,
                    "processed": record.processed,
                },
            )
        return row is not None

    async def get_callback(self, channel: str, external_txn_id: str) -> CallbackLogRecord | None:
        """按渠道流水号读取回调留痕。

        Args:
            channel: 渠道。
            external_txn_id: 渠道流水号。

        Returns:
            CallbackLogRecord | None: 留痕；不存在返回 None。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _CallbackTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, callback_id, channel, external_txn_id, order_id, amount_cents,
                       signature_ok, payload_sha256, processed, received_at
                FROM {CALLBACK_TABLE}
                WHERE channel = :channel AND external_txn_id = :external_txn_id
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "channel": channel,
                    "external_txn_id": external_txn_id,
                },
            )
        return _to_callback(row) if row is not None else None

    async def mark_callback_processed(self, channel: str, external_txn_id: str) -> None:
        """把回调留痕标记为已处理。

        Args:
            channel: 渠道。
            external_txn_id: 渠道流水号。
        """
        async with platform_scope(self._settings) as uow:
            repo = _CallbackTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {CALLBACK_TABLE}
                SET processed = TRUE
                WHERE channel = :channel AND external_txn_id = :external_txn_id
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "channel": channel,
                    "external_txn_id": external_txn_id,
                },
            )


#: 订单查询的列清单（三条查询共用，避免列漂移）
_ORDER_COLUMNS: Final[str] = (
    "tenant_id, order_id, order_type, plan_code, seats, amount_cents, currency, status, "
    "payment_method, pay_url, bank_info, payment_no_masked, paid_amount_cents, paid_at, "
    "confirmed_by, confirm_remark, expires_at, created_by, created_at, updated_at"
)


def _timestamp(value: Any) -> str:
    """把数据库时间值规范化为 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str: ISO 8601 字符串；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return to_iso(value)
    return str(value)


def _json_object(value: Any) -> dict[str, Any]:
    """把数据库 JSONB 值规范化为字典。

    Args:
        value: JSONB 值（可能是 dict 或 JSON 字符串）。

    Returns:
        dict[str, Any]: 字典；无法解析返回空字典。
    """
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return {str(key): item for key, item in parsed.items()} if isinstance(parsed, dict) else {}
    return {}


def _to_plan(row: Mapping[str, Any]) -> PlanRecord:
    """把数据库行映射为套餐记录。

    Args:
        row: 行映射。

    Returns:
        PlanRecord: 套餐记录。
    """
    return PlanRecord(
        tenant_id=str(row.get("tenant_id", PLATFORM_TENANT)),
        plan_code=str(row.get("plan_code", "")),
        display_name=str(row.get("display_name", "")),
        price_per_seat_month_cents=int(row.get("price_per_seat_month_cents", 0) or 0),
        currency=str(row.get("currency", "CNY")),
        min_seats=int(row.get("min_seats", 1) or 1),
        quota_units_per_seat_month=int(row.get("quota_units_per_seat_month", 0) or 0),
        quota_unit_label=str(row.get("quota_unit_label", "字数额度")),
        purchasable_quota=bool(row.get("purchasable_quota", False)),
        features=_json_object(row.get("features")),
        status=str(row.get("status", PLAN_AVAILABLE)),
        trial_days=int(row.get("trial_days", 0) or 0),
        sort_order=int(row.get("sort_order", 0) or 0),
    )


def _to_tenant(row: Mapping[str, Any]) -> TenantRecord:
    """把数据库行映射为企业记录。

    Args:
        row: 行映射。

    Returns:
        TenantRecord: 企业记录。
    """
    return TenantRecord(
        tenant_id=str(row.get("tenant_id", "")),
        name=str(row.get("name", "")),
        status=str(row.get("status", TENANT_ACTIVE)),
        deploy_mode=str(row.get("deploy_mode", "saas")),
        plan_code=str(row.get("plan_code", PLAN_FREE)),
        suspended_reason=str(row.get("suspended_reason", "") or ""),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")),
    )


def _to_subscription(row: Mapping[str, Any]) -> SubscriptionRecord:
    """把数据库行映射为订阅记录。

    Args:
        row: 行映射。

    Returns:
        SubscriptionRecord: 订阅记录。
    """
    return SubscriptionRecord(
        tenant_id=str(row.get("tenant_id", "")),
        subscription_id=str(row.get("subscription_id", "")),
        plan_code=str(row.get("plan_code", PLAN_FREE)),
        status=str(row.get("status", SUBSCRIPTION_ACTIVE)),
        seats_total=int(row.get("seats_total", 1) or 0),
        seats_used=int(row.get("seats_used", 0) or 0),
        started_at=_timestamp(row.get("started_at")),
        current_period_start=_timestamp(row.get("current_period_start")),
        current_period_end=_timestamp(row.get("current_period_end")),
        auto_renew=bool(row.get("auto_renew", True)),
        pending_plan_code=str(row.get("pending_plan_code", "") or ""),
        pending_change_at=_timestamp(row.get("pending_change_at")),
        grace_until=_timestamp(row.get("grace_until")),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")),
    )


def _to_order(row: Mapping[str, Any]) -> OrderRecord:
    """把数据库行映射为订单记录。

    Args:
        row: 行映射。

    Returns:
        OrderRecord: 订单记录。
    """
    paid_amount = row.get("paid_amount_cents")
    bank_info = row.get("bank_info")
    return OrderRecord(
        tenant_id=str(row.get("tenant_id", "")),
        order_id=str(row.get("order_id", "")),
        order_type=str(row.get("order_type", ORDER_TYPE_NEW)),
        plan_code=str(row.get("plan_code", "")),
        seats=int(row.get("seats", 1) or 0),
        amount_cents=int(row.get("amount_cents", 0) or 0),
        currency=str(row.get("currency", "CNY")),
        status=str(row.get("status", ORDER_PENDING)),
        payment_method=str(row.get("payment_method", PAYMENT_ONLINE)),
        pay_url=str(row.get("pay_url", "") or ""),
        bank_info=_json_object(bank_info) if bank_info is not None else None,
        payment_no_masked=str(row.get("payment_no_masked", "") or ""),
        paid_amount_cents=int(paid_amount) if paid_amount is not None else None,
        paid_at=_timestamp(row.get("paid_at")),
        confirmed_by=str(row.get("confirmed_by", "") or ""),
        confirm_remark=str(row.get("confirm_remark", "") or ""),
        expires_at=_timestamp(row.get("expires_at")),
        created_by=str(row.get("created_by", "") or ""),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")),
    )


def _to_invoice(row: Mapping[str, Any]) -> InvoiceRecord:
    """把数据库行映射为账单记录。

    Args:
        row: 行映射。

    Returns:
        InvoiceRecord: 账单记录。
    """
    return InvoiceRecord(
        tenant_id=str(row.get("tenant_id", "")),
        invoice_id=str(row.get("invoice_id", "")),
        order_id=str(row.get("order_id", "")),
        period=str(row.get("period", "")),
        amount_cents=int(row.get("amount_cents", 0) or 0),
        currency=str(row.get("currency", "CNY")),
        status=str(row.get("status", INVOICE_PENDING)),
        download_path=str(row.get("download_path", "") or ""),
        issued_at=_timestamp(row.get("issued_at")),
        created_at=_timestamp(row.get("created_at")),
    )


def _to_callback(row: Mapping[str, Any]) -> CallbackLogRecord:
    """把数据库行映射为回调留痕。

    Args:
        row: 行映射。

    Returns:
        CallbackLogRecord: 回调留痕。
    """
    amount = row.get("amount_cents")
    return CallbackLogRecord(
        tenant_id=str(row.get("tenant_id", PLATFORM_TENANT)),
        callback_id=str(row.get("callback_id", "")),
        channel=str(row.get("channel", CHANNEL_WECHAT_PAY)),
        external_txn_id=str(row.get("external_txn_id", "")),
        order_id=str(row.get("order_id", "") or ""),
        amount_cents=int(amount) if amount is not None else None,
        signature_ok=bool(row.get("signature_ok", False)),
        payload_sha256=str(row.get("payload_sha256", "")),
        processed=bool(row.get("processed", False)),
        received_at=_timestamp(row.get("received_at")),
    )


__all__: list[str] = [
    "CALLBACK_TABLE",
    "INVOICE_TABLE",
    "ORDER_TABLE",
    "PLAN_TABLE",
    "SUBSCRIPTION_TABLE",
    "TENANT_TABLE",
    "PostgresBillingStore",
]
