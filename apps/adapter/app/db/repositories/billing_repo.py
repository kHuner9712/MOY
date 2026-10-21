# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""计费仓储（阶段二 B09，架构文档 §4.5 / §5 计费 DDL）。

覆盖五张表 + 企业主表：

| 表 | 用途 | 关键约束 |
| --- | --- | --- |
| `billing_plan` | 套餐目录（平台共享行） | 主键 `(__platform__, plan_code)`，**读它，不新建表** |
| `billing_subscription` | 一企业一有效订阅 | 部分唯一索引 `uq_subscription_active` |
| `billing_order` | 订单 | 部分唯一索引 `uq_order_pending`（同企业仅一个待支付） |
| `billing_invoice` | 账单 | `uq_invoice_order`（一单一票，回调幂等的第二道保险） |
| `payment_callback_log` | 回调留痕 | `uq_callback_external(channel, external_txn_id)` |
| `tenant` | 企业主表 | `plan_code` 是「功能开关」的权威落点 |

**幂等的三道防线**（架构文档 §4.5 要点 + 2.2 第 6 条）：

1. `uq_order_pending`：并发下单时数据库层只允许一条 `pending_payment`；
2. `uq_callback_external`：同渠道同流水号只处理一次（`append_callback` 返回 False）；
3. `uq_invoice_order`：一单一票，`create_invoice` 命中则返回既有行（不重复开票）。

双后端（`OA_BUSINESS_STORE`）：`memory` 供 development/test（无 PostgreSQL 也能全绿），
`postgres` 为生产唯一实现；两条路径的方法签名与返回语义完全一致。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import platform_scope, tenant_scope

logger = get_logger(__name__)

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

#: 平台哨兵租户（共享目录行所属）
PLATFORM_TENANT: Final[str] = "__platform__"

#: 免费版套餐标识
PLAN_FREE: Final[str] = "free"

#: 专业版套餐标识
PLAN_PROFESSIONAL: Final[str] = "professional"

#: 企业版套餐标识
PLAN_ENTERPRISE: Final[str] = "enterprise"

#: 免费版默认席位数（架构文档 §4.5 要点：无订阅时 `seats_total=3`）
FREE_PLAN_SEATS: Final[int] = 3

#: 套餐可购状态
PLAN_AVAILABLE: Final[str] = "available"

#: 套餐下架状态
PLAN_OFFLINE: Final[str] = "offline"

#: 订阅状态：试用中
SUBSCRIPTION_TRIALING: Final[str] = "trialing"

#: 订阅状态：生效中
SUBSCRIPTION_ACTIVE: Final[str] = "active"

#: 订阅状态：欠费
SUBSCRIPTION_PAST_DUE: Final[str] = "past_due"

#: 订阅状态：已取消
SUBSCRIPTION_CANCELLED: Final[str] = "cancelled"

#: 订阅状态：已过期
SUBSCRIPTION_EXPIRED: Final[str] = "expired"

#: 「有效订阅」状态集合（与 DDL 的部分唯一索引一致）
ACTIVE_SUBSCRIPTION_STATUSES: Final[tuple[str, ...]] = (
    SUBSCRIPTION_TRIALING,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_PAST_DUE,
)

#: 订单状态：待支付
ORDER_PENDING: Final[str] = "pending_payment"

#: 订单状态：已支付
ORDER_PAID: Final[str] = "paid"

#: 订单状态：超时关闭
ORDER_CLOSED: Final[str] = "closed"

#: 订单状态：已取消
ORDER_CANCELLED: Final[str] = "cancelled"

#: 订单状态：已退款
ORDER_REFUNDED: Final[str] = "refunded"

#: 订单类型：新购
ORDER_TYPE_NEW: Final[str] = "new"

#: 订单类型：续费
ORDER_TYPE_RENEW: Final[str] = "renew"

#: 订单类型：升级
ORDER_TYPE_UPGRADE: Final[str] = "upgrade"

#: 订单类型：加席
ORDER_TYPE_SEATS: Final[str] = "seats"

#: 支付方式：在线支付
PAYMENT_ONLINE: Final[str] = "online"

#: 支付方式：对公转账
PAYMENT_OFFLINE: Final[str] = "offline_transfer"

#: 账单状态：已开具
INVOICE_ISSUED: Final[str] = "issued"

#: 账单状态：待开具
INVOICE_PENDING: Final[str] = "pending"

#: 企业状态：正常
TENANT_ACTIVE: Final[str] = "active"

#: 企业状态：已停用
TENANT_SUSPENDED: Final[str] = "suspended"

#: 支付渠道：微信支付
CHANNEL_WECHAT_PAY: Final[str] = "wechat_pay"

#: 订单可变字段白名单（`update_order` 只接受这些键，杜绝动态列名注入）
ORDER_MUTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "order_type",
        "plan_code",
        "seats",
        "amount_cents",
        "status",
        "payment_method",
        "pay_url",
        "bank_info",
        "payment_no_masked",
        "paid_amount_cents",
        "paid_at",
        "confirmed_by",
        "confirm_remark",
        "expires_at",
    }
)

#: 企业可变字段白名单
TENANT_MUTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"name", "status", "plan_code", "suspended_reason"}
)


def now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def to_iso(moment: datetime) -> str:
    """把 `datetime` 规范化为 ISO 8601（UTC）。

    Args:
        moment: 时间点。

    Returns:
        str: ISO 8601 字符串。
    """
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """解析 ISO 8601 时间串（空值/非法值回落到当前时刻）。

    Args:
        value: ISO 8601 字符串。

    Returns:
        datetime: 带 UTC 时区的时间点。
    """
    raw = (value or "").strip()
    if not raw:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def default_plans() -> tuple[PlanRecord, ...]:
    """内存后端的套餐种子（与 `deploy/init-schema-phase2.sql` 第 15 节逐字一致）。

    **只在内存后端使用**：生产环境的套餐目录来自数据库种子行，
    代码不重复定义、也不覆盖运营侧调整后的价格。

    Returns:
        tuple[PlanRecord, ...]: 三个套餐（免费版 / 专业版 / 企业版）。
    """
    return (
        PlanRecord(
            tenant_id=PLATFORM_TENANT,
            plan_code=PLAN_FREE,
            display_name="免费版",
            price_per_seat_month_cents=0,
            min_seats=3,
            quota_units_per_seat_month=200_000,
            purchasable_quota=False,
            features={
                "kb_count": 3,
                "kb_document_limit": 50,
                "tool_count": 1,
                "plugin_market": False,
                "org_structure": False,
                "role_permission": False,
                "audit_log": False,
                "private_deploy": False,
            },
            status=PLAN_AVAILABLE,
            trial_days=0,
            sort_order=1,
        ),
        PlanRecord(
            tenant_id=PLATFORM_TENANT,
            plan_code=PLAN_PROFESSIONAL,
            display_name="专业版",
            price_per_seat_month_cents=3_900,
            min_seats=5,
            quota_units_per_seat_month=3_000_000,
            purchasable_quota=True,
            features={
                "kb_count": None,
                "kb_document_limit": 2_000,
                "tool_count": 5,
                "plugin_market": True,
                "org_structure": True,
                "role_permission": True,
                "audit_log": True,
                "private_deploy": False,
            },
            status=PLAN_AVAILABLE,
            trial_days=14,
            sort_order=2,
        ),
        PlanRecord(
            tenant_id=PLATFORM_TENANT,
            plan_code=PLAN_ENTERPRISE,
            display_name="企业版",
            price_per_seat_month_cents=9_900,
            min_seats=20,
            quota_units_per_seat_month=8_000_000,
            purchasable_quota=True,
            features={
                "kb_count": None,
                "kb_document_limit": 10_000,
                "tool_count": 5,
                "plugin_market": True,
                "org_structure": True,
                "role_permission": True,
                "audit_log": True,
                "private_deploy": True,
            },
            status=PLAN_AVAILABLE,
            trial_days=30,
            sort_order=3,
        ),
    )


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """套餐目录行（`billing_plan`）。

    Attributes:
        tenant_id: 所属租户（平台行恒为 `__platform__`）。
        plan_code: 套餐标识。
        display_name: 套餐名称。
        price_per_seat_month_cents: 每席位每月价格（分）。
        currency: 币种。
        min_seats: 最小席位数。
        quota_units_per_seat_month: 每席位每月字数额度。
        quota_unit_label: 额度单位文案。
        purchasable_quota: 是否可单独购买额度。
        features: 功能开关（`billing_plan.features` JSONB）。
        status: 可购状态。
        trial_days: 试用天数。
        sort_order: 排序号。
    """

    tenant_id: str = PLATFORM_TENANT
    plan_code: str = ""
    display_name: str = ""
    price_per_seat_month_cents: int = 0
    currency: str = "CNY"
    min_seats: int = 1
    quota_units_per_seat_month: int = 0
    quota_unit_label: str = "字数额度"
    purchasable_quota: bool = False
    features: dict[str, Any] = field(default_factory=dict)
    status: str = PLAN_AVAILABLE
    trial_days: int = 0
    sort_order: int = 0

    @property
    def is_available(self) -> bool:
        """是否可购（上架中）。"""
        return self.status == PLAN_AVAILABLE

    def amount_cents(self, seats: int) -> int:
        """按席位数计算应付金额（分）。

        Args:
            seats: 席位数。

        Returns:
            int: 金额（分）。
        """
        return max(int(self.price_per_seat_month_cents), 0) * max(int(seats), 0)


@dataclass(frozen=True, slots=True)
class TenantRecord:
    """企业主表行（`tenant`）。

    Attributes:
        tenant_id: 企业 ID。
        name: 企业名称。
        status: 状态（`active` / `suspended`）。
        deploy_mode: 部署模式。
        plan_code: 当前套餐标识（功能开关的权威落点）。
        suspended_reason: 停用原因。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    tenant_id: str = ""
    name: str = ""
    status: str = TENANT_ACTIVE
    deploy_mode: str = "saas"
    plan_code: str = PLAN_FREE
    suspended_reason: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class SubscriptionRecord:
    """订阅行（`billing_subscription`）。

    Attributes:
        tenant_id: 企业 ID。
        subscription_id: 订阅 ID。
        plan_code: 套餐标识。
        status: 订阅状态。
        seats_total: 总席位。
        seats_used: 已用席位。
        started_at: 开始时间。
        current_period_start: 当前周期开始时间。
        current_period_end: 当前周期结束时间。
        auto_renew: 是否自动续费。
        pending_plan_code: 待生效套餐（降级）。
        pending_change_at: 待生效时间（= 当前周期末）。
        grace_until: 宽限截止时间。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    tenant_id: str = ""
    subscription_id: str = ""
    plan_code: str = PLAN_FREE
    status: str = SUBSCRIPTION_ACTIVE
    seats_total: int = FREE_PLAN_SEATS
    seats_used: int = 0
    started_at: str = ""
    current_period_start: str = ""
    current_period_end: str = ""
    auto_renew: bool = True
    pending_plan_code: str = ""
    pending_change_at: str = ""
    grace_until: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_active(self) -> bool:
        """是否为有效订阅。"""
        return self.status in ACTIVE_SUBSCRIPTION_STATUSES

    @property
    def has_pending_change(self) -> bool:
        """是否有待生效的套餐变更。"""
        return bool(self.pending_plan_code)


@dataclass(frozen=True, slots=True)
class OrderRecord:
    """订单行（`billing_order`）。

    Attributes:
        tenant_id: 企业 ID。
        order_id: 订单 ID。
        order_type: 订单类型。
        plan_code: 套餐标识。
        seats: 席位数。
        amount_cents: 应付金额（分）。
        currency: 币种。
        status: 订单状态。
        payment_method: 支付方式。
        pay_url: 在线支付地址。
        bank_info: 对公转账收款信息。
        payment_no_masked: 脱敏支付流水号。
        paid_amount_cents: 实付金额（分）。
        paid_at: 支付时间。
        confirmed_by: 核销人（运营）。
        confirm_remark: 核销备注。
        expires_at: 支付截止时间。
        created_by: 下单人。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    tenant_id: str = ""
    order_id: str = ""
    order_type: str = ORDER_TYPE_NEW
    plan_code: str = ""
    seats: int = 1
    amount_cents: int = 0
    currency: str = "CNY"
    status: str = ORDER_PENDING
    payment_method: str = PAYMENT_ONLINE
    pay_url: str = ""
    bank_info: dict[str, Any] | None = None
    payment_no_masked: str = ""
    paid_amount_cents: int | None = None
    paid_at: str = ""
    confirmed_by: str = ""
    confirm_remark: str = ""
    expires_at: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_pending(self) -> bool:
        """是否待支付。"""
        return self.status == ORDER_PENDING

    @property
    def is_paid(self) -> bool:
        """是否已支付。"""
        return self.status == ORDER_PAID


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    """账单行（`billing_invoice`）。

    Attributes:
        tenant_id: 企业 ID。
        invoice_id: 账单 ID。
        order_id: 关联订单 ID。
        period: 账期 `YYYY-MM`。
        amount_cents: 金额（分）。
        currency: 币种。
        status: 状态（`issued` / `pending`）。
        download_path: 下载路径（本阶段为空）。
        issued_at: 开具时间。
        created_at: 创建时间。
    """

    tenant_id: str = ""
    invoice_id: str = ""
    order_id: str = ""
    period: str = ""
    amount_cents: int = 0
    currency: str = "CNY"
    status: str = INVOICE_PENDING
    download_path: str = ""
    issued_at: str = ""
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class CallbackLogRecord:
    """支付回调留痕（`payment_callback_log`）。

    Attributes:
        tenant_id: 企业 ID（未知时写入平台哨兵，保证留痕不丢）。
        callback_id: 回调 ID。
        channel: 渠道。
        external_txn_id: 渠道流水号（幂等唯一键）。
        order_id: 关联订单 ID。
        amount_cents: 金额（分）。
        signature_ok: 验签是否通过。
        payload_sha256: 载荷摘要（**不存原文**，防止敏感字段落库）。
        processed: 是否已处理。
        received_at: 接收时间。
    """

    tenant_id: str = PLATFORM_TENANT
    callback_id: str = ""
    channel: str = CHANNEL_WECHAT_PAY
    external_txn_id: str = ""
    order_id: str = ""
    amount_cents: int | None = None
    signature_ok: bool = False
    payload_sha256: str = ""
    processed: bool = False
    received_at: str = ""


class BillingStore(Protocol):
    """计费存储契约（内存 / PostgreSQL 双实现）。"""

    async def list_plans(self) -> list[PlanRecord]:
        """列出全部套餐（按 `sort_order` 升序）。"""
        ...

    async def get_plan(self, plan_code: str) -> PlanRecord | None:
        """按标识读取套餐。"""
        ...

    async def ensure_tenant(self, tenant_id: str, name: str = "") -> TenantRecord:
        """读取或登记企业行（不存在时按免费版创建）。"""
        ...

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """按 ID 读取企业行。"""
        ...

    async def update_tenant(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        plan_code: str | None = None,
        suspended_reason: str | None = None,
    ) -> TenantRecord | None:
        """更新企业行（仅状态/套餐/停用原因）。"""
        ...

    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """列出企业（关键字模糊匹配名称；平台视图使用）。"""
        ...

    async def get_active_subscription(self, tenant_id: str) -> SubscriptionRecord | None:
        """读取有效订阅。"""
        ...

    async def save_subscription(self, record: SubscriptionRecord) -> SubscriptionRecord:
        """写入（或按 `subscription_id` 更新）订阅。"""
        ...

    async def get_order(self, tenant_id: str, order_id: str) -> OrderRecord | None:
        """按 ID 读取订单（租户内）。"""
        ...

    async def find_order_by_id(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 跨企业读取（**仅支付回调与运营核销可用**）。

        支付回调只有 `out_trade_no`（=订单 ID），没有租户上下文；`order_id` 是
        ULID（不可枚举），且回调**必须验签**通过才会走到这里，因此不构成探测面。
        调用方不得把它用于任何面向企业用户的接口（那是「列表过滤了但详情没校验」）。
        """
        ...

    async def find_pending_order(self, tenant_id: str) -> OrderRecord | None:
        """读取该企业的待支付订单。"""
        ...

    async def create_order(self, record: OrderRecord) -> OrderRecord:
        """写入订单；已有待支付订单时返回既有行（**不产生第二单**）。"""
        ...

    async def update_order(
        self, tenant_id: str, order_id: str, **fields: Any
    ) -> OrderRecord | None:
        """更新订单可变字段。"""
        ...

    async def list_orders(
        self, tenant_id: str, *, status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """分页列出企业订单。"""
        ...

    async def list_all_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨企业分页列出订单（**仅运营后台可用**，调用方必须先过 `require_ops_access`）。"""
        ...

    async def expire_orders(self, deadline_iso: str, tenant_id: str = "") -> list[OrderRecord]:
        """把 `expires_at` 已过期的待支付订单置为 `closed`，返回被关闭的订单。"""
        ...

    async def ensure_invoice(self, record: InvoiceRecord) -> InvoiceRecord:
        """写入账单；一单已开票时返回既有行（**不重复开票**）。"""
        ...

    async def get_invoice_by_order(self, tenant_id: str, order_id: str) -> InvoiceRecord | None:
        """按订单读取账单。"""
        ...

    async def list_invoices(self, tenant_id: str, period: str = "") -> list[InvoiceRecord]:
        """列出企业账单。"""
        ...

    async def append_callback(self, record: CallbackLogRecord) -> bool:
        """写入回调留痕；同渠道同流水号已存在时返回 False（幂等）。"""
        ...

    async def get_callback(self, channel: str, external_txn_id: str) -> CallbackLogRecord | None:
        """按渠道流水号读取回调留痕。"""
        ...

    async def mark_callback_processed(self, channel: str, external_txn_id: str) -> None:
        """把回调留痕标记为已处理。"""
        ...


class InMemoryBillingStore:
    """进程内计费存储（development/test）。"""

    def __init__(self) -> None:
        """初始化（写入与 DDL 一致的三条套餐种子）。"""
        self._plans: dict[str, PlanRecord] = {plan.plan_code: plan for plan in default_plans()}
        self._tenants: dict[str, TenantRecord] = {}
        self._subscriptions: dict[str, SubscriptionRecord] = {}
        self._orders: dict[tuple[str, str], OrderRecord] = {}
        self._invoices: dict[tuple[str, str], InvoiceRecord] = {}
        self._invoice_by_order: dict[tuple[str, str], str] = {}
        self._callbacks: dict[tuple[str, str], CallbackLogRecord] = {}
        self._callback_txn: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 套餐
    # ------------------------------------------------------------------
    async def list_plans(self) -> list[PlanRecord]:
        """列出全部套餐。

        Returns:
            list[PlanRecord]: 按 `sort_order` 升序的套餐列表。
        """
        async with self._lock:
            plans = list(self._plans.values())
        plans.sort(key=lambda item: (item.sort_order, item.plan_code))
        return plans

    async def get_plan(self, plan_code: str) -> PlanRecord | None:
        """按标识读取套餐。

        Args:
            plan_code: 套餐标识。

        Returns:
            PlanRecord | None: 套餐；不存在返回 None。
        """
        async with self._lock:
            return self._plans.get(plan_code)

    # ------------------------------------------------------------------
    # 企业
    # ------------------------------------------------------------------
    async def ensure_tenant(self, tenant_id: str, name: str = "") -> TenantRecord:
        """读取或登记企业行。

        Args:
            tenant_id: 企业 ID。
            name: 企业名称（仅首次登记时使用）。

        Returns:
            TenantRecord: 企业行。
        """
        async with self._lock:
            current = self._tenants.get(tenant_id)
            if current is not None:
                if name and not current.name:
                    updated = replace(current, name=name, updated_at=now_iso())
                    self._tenants[tenant_id] = updated
                    return updated
                return current
            stamp = now_iso()
            created = TenantRecord(
                tenant_id=tenant_id,
                name=name or tenant_id,
                status=TENANT_ACTIVE,
                plan_code=PLAN_FREE,
                created_at=stamp,
                updated_at=stamp,
            )
            self._tenants[tenant_id] = created
            return created

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """按 ID 读取企业行。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantRecord | None: 企业行；不存在返回 None。
        """
        async with self._lock:
            return self._tenants.get(tenant_id)

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
        async with self._lock:
            current = self._tenants.get(tenant_id)
            if current is None:
                return None
            updated = replace(
                current,
                status=status if status is not None else current.status,
                plan_code=plan_code if plan_code is not None else current.plan_code,
                suspended_reason=(
                    suspended_reason if suspended_reason is not None else current.suspended_reason
                ),
                updated_at=now_iso(),
            )
            self._tenants[tenant_id] = updated
            return updated

    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """列出企业。

        Args:
            keyword: 名称关键字（大小写不敏感）。
            status: 状态过滤。

        Returns:
            list[TenantRecord]: 企业列表（创建时间倒序）。
        """
        async with self._lock:
            items = list(self._tenants.values())
        needle = keyword.strip().lower()
        if needle:
            items = [item for item in items if needle in item.name.lower()]
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.created_at, item.tenant_id), reverse=True)
        return items

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
        async with self._lock:
            current = self._subscriptions.get(tenant_id)
        if current is None or not current.is_active:
            return None
        return current

    async def save_subscription(self, record: SubscriptionRecord) -> SubscriptionRecord:
        """写入（或按 `subscription_id` 更新）订阅。

        Args:
            record: 订阅行。

        Returns:
            SubscriptionRecord: 落库后的订阅行。
        """
        stamp = now_iso()
        async with self._lock:
            current = self._subscriptions.get(record.tenant_id)
            payload = replace(
                record,
                created_at=record.created_at or (current.created_at if current else stamp),
                updated_at=stamp,
            )
            self._subscriptions[record.tenant_id] = payload
        return payload

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
        async with self._lock:
            return self._orders.get((tenant_id, order_id))

    async def find_order_by_id(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 跨企业读取订单。

        Args:
            order_id: 订单 ID。

        Returns:
            OrderRecord | None: 订单；不存在返回 None。
        """
        async with self._lock:
            for (_, current), item in self._orders.items():
                if current == order_id:
                    return item
        return None

    async def find_pending_order(self, tenant_id: str) -> OrderRecord | None:
        """读取该企业的待支付订单。

        Args:
            tenant_id: 企业 ID。

        Returns:
            OrderRecord | None: 待支付订单；无则返回 None。
        """
        async with self._lock:
            pending = [
                item
                for (owner, _), item in self._orders.items()
                if owner == tenant_id and item.is_pending
            ]
        pending.sort(key=lambda item: item.created_at)
        return pending[0] if pending else None

    async def create_order(self, record: OrderRecord) -> OrderRecord:
        """写入订单（同企业仅允许一个待支付订单）。

        Args:
            record: 订单行。

        Returns:
            OrderRecord: 落库后的订单；**已有待支付订单时返回既有行**。
        """
        stamp = now_iso()
        async with self._lock:
            existing = [
                item
                for (owner, _), item in self._orders.items()
                if owner == record.tenant_id and item.is_pending
            ]
            if existing:
                existing.sort(key=lambda item: item.created_at)
                return existing[0]
            payload = replace(
                record,
                created_at=record.created_at or stamp,
                updated_at=stamp,
            )
            self._orders[(record.tenant_id, record.order_id)] = payload
        return payload

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
        async with self._lock:
            current = self._orders.get((tenant_id, order_id))
            if current is None:
                return None
            updated = replace(current, **fields, updated_at=now_iso())
            self._orders[(tenant_id, order_id)] = updated
            return updated

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
        async with self._lock:
            items = [item for (owner, _), item in self._orders.items() if owner == tenant_id]
        return _slice_orders(items, status=status, offset=offset, limit=limit)

    async def list_all_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨企业分页列出订单。

        Args:
            tenant_id: 企业过滤（空表示全部）。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[OrderRecord], int]: 当前页订单与总条数。
        """
        async with self._lock:
            items = [
                item
                for (owner, _), item in self._orders.items()
                if not tenant_id or owner == tenant_id
            ]
        return _slice_orders(items, status=status, offset=offset, limit=limit)

    async def expire_orders(self, deadline_iso: str, tenant_id: str = "") -> list[OrderRecord]:
        """把已过期的待支付订单置为 `closed`。

        Args:
            deadline_iso: 判定阈值（`expires_at < deadline_iso` 即过期）。
            tenant_id: 企业过滤（空表示全部，仅运营侧补偿任务使用）。

        Returns:
            list[OrderRecord]: 被关闭的订单。
        """
        closed: list[OrderRecord] = []
        async with self._lock:
            for key, item in list(self._orders.items()):
                if item.status != ORDER_PENDING:
                    continue
                if tenant_id and key[0] != tenant_id:
                    continue
                if not item.expires_at or item.expires_at >= deadline_iso:
                    continue
                updated = replace(item, status=ORDER_CLOSED, updated_at=now_iso())
                self._orders[key] = updated
                closed.append(updated)
        return closed

    # ------------------------------------------------------------------
    # 账单
    # ------------------------------------------------------------------
    async def ensure_invoice(self, record: InvoiceRecord) -> InvoiceRecord:
        """写入账单（一单一票）。

        Args:
            record: 账单行。

        Returns:
            InvoiceRecord: 落库后的账单；该订单已开票时返回既有行。
        """
        stamp = now_iso()
        async with self._lock:
            existed_id = self._invoice_by_order.get((record.tenant_id, record.order_id))
            if existed_id is not None:
                return self._invoices[(record.tenant_id, existed_id)]
            payload = replace(record, created_at=record.created_at or stamp)
            self._invoices[(record.tenant_id, record.invoice_id)] = payload
            self._invoice_by_order[(record.tenant_id, record.order_id)] = record.invoice_id
            return payload

    async def get_invoice_by_order(self, tenant_id: str, order_id: str) -> InvoiceRecord | None:
        """按订单读取账单。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。

        Returns:
            InvoiceRecord | None: 账单；未开票返回 None。
        """
        async with self._lock:
            invoice_id = self._invoice_by_order.get((tenant_id, order_id))
            if invoice_id is None:
                return None
            return self._invoices.get((tenant_id, invoice_id))

    async def list_invoices(self, tenant_id: str, period: str = "") -> list[InvoiceRecord]:
        """列出企业账单。

        Args:
            tenant_id: 企业 ID。
            period: 账期过滤（空表示全部）。

        Returns:
            list[InvoiceRecord]: 账单列表（创建时间倒序）。
        """
        async with self._lock:
            items = [item for (owner, _), item in self._invoices.items() if owner == tenant_id]
        if period:
            items = [item for item in items if item.period == period]
        items.sort(key=lambda item: (item.created_at, item.invoice_id), reverse=True)
        return items

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
        key = (record.channel, record.external_txn_id)
        async with self._lock:
            if key in self._callback_txn:
                return False
            payload = replace(record, received_at=record.received_at or now_iso())
            self._callbacks[(record.tenant_id, record.callback_id)] = payload
            self._callback_txn[key] = record.callback_id
            return True

    async def get_callback(self, channel: str, external_txn_id: str) -> CallbackLogRecord | None:
        """按渠道流水号读取回调留痕。

        Args:
            channel: 渠道。
            external_txn_id: 渠道流水号。

        Returns:
            CallbackLogRecord | None: 留痕；不存在返回 None。
        """
        key = (channel, external_txn_id)
        async with self._lock:
            callback_id = self._callback_txn.get(key)
            if callback_id is None:
                return None
            for (_, current), item in self._callbacks.items():
                if current == callback_id:
                    return item
        return None

    async def mark_callback_processed(self, channel: str, external_txn_id: str) -> None:
        """把回调留痕标记为已处理（内存后端内部使用）。

        Args:
            channel: 渠道。
            external_txn_id: 渠道流水号。
        """
        key = (channel, external_txn_id)
        async with self._lock:
            callback_id = self._callback_txn.get(key)
            if callback_id is None:
                return
            for tenant_key, item in list(self._callbacks.items()):
                if item.callback_id != callback_id:
                    continue
                self._callbacks[tenant_key] = replace(item, processed=True)

    def counts(self) -> dict[str, int]:
        """返回各表行数（测试断言用）。

        Returns:
            dict[str, int]: `orders` / `invoices` / `callbacks` / `subscriptions` 行数。
        """
        return {
            "orders": len(self._orders),
            "invoices": len(self._invoices),
            "callbacks": len(self._callbacks),
            "subscriptions": len(self._subscriptions),
        }

    def clear(self) -> None:
        """清空业务数据（测试隔离用；保留套餐种子）。"""
        self._tenants.clear()
        self._subscriptions.clear()
        self._orders.clear()
        self._invoices.clear()
        self._invoice_by_order.clear()
        self._callbacks.clear()
        self._callback_txn.clear()


def _slice_orders(
    items: list[OrderRecord], *, status: str, offset: int, limit: int
) -> tuple[list[OrderRecord], int]:
    """按状态过滤并分页（内存后端共用）。

    Args:
        items: 订单全集。
        status: 状态过滤。
        offset: 偏移量。
        limit: 每页条数。

    Returns:
        tuple[list[OrderRecord], int]: 当前页订单与总条数。
    """
    filtered = [item for item in items if not status or item.status == status]
    filtered.sort(key=lambda item: (item.created_at, item.order_id), reverse=True)
    start = max(offset, 0)
    return filtered[start : start + limit], len(filtered)


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


# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryBillingStore | None = None


def get_billing_store(settings: Settings | None = None) -> BillingStore:
    """获取计费存储。

    Args:
        settings: 可选配置实例。

    Returns:
        BillingStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresBillingStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryBillingStore()
    return _memory_store


def get_memory_billing_store() -> InMemoryBillingStore | None:
    """取内存计费存储单例（**不创建**；供计量域读取额度口径）。

    计量仓储（`usage_repo.InMemoryUsageStore`）用它补齐「内存模式下的额度总量」：
    否则开发/测试环境的 BL5 额度恒为 0，与 BL2 的 `quota_units_total` 不一致。

    Returns:
        InMemoryBillingStore | None: 已创建的内存存储；未创建返回 None。
    """
    return _memory_store


def reset_billing_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "ACTIVE_SUBSCRIPTION_STATUSES",
    "CALLBACK_TABLE",
    "CHANNEL_WECHAT_PAY",
    "FREE_PLAN_SEATS",
    "INVOICE_ISSUED",
    "INVOICE_PENDING",
    "INVOICE_TABLE",
    "ORDER_CLOSED",
    "ORDER_MUTABLE_FIELDS",
    "ORDER_PAID",
    "ORDER_PENDING",
    "ORDER_TABLE",
    "ORDER_TYPE_NEW",
    "ORDER_TYPE_RENEW",
    "ORDER_TYPE_SEATS",
    "ORDER_TYPE_UPGRADE",
    "PAYMENT_OFFLINE",
    "PAYMENT_ONLINE",
    "PLAN_AVAILABLE",
    "PLAN_ENTERPRISE",
    "PLAN_FREE",
    "PLAN_OFFLINE",
    "PLAN_PROFESSIONAL",
    "PLAN_TABLE",
    "PLATFORM_TENANT",
    "SUBSCRIPTION_ACTIVE",
    "SUBSCRIPTION_TABLE",
    "TENANT_ACTIVE",
    "TENANT_MUTABLE_FIELDS",
    "TENANT_SUSPENDED",
    "TENANT_TABLE",
    "BillingStore",
    "CallbackLogRecord",
    "InMemoryBillingStore",
    "InvoiceRecord",
    "OrderRecord",
    "PlanRecord",
    "PostgresBillingStore",
    "SubscriptionRecord",
    "TenantRecord",
    "default_plans",
    "get_billing_store",
    "get_memory_billing_store",
    "now_iso",
    "parse_iso",
    "reset_billing_store",
    "to_iso",
]
