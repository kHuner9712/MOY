"""计费域仓储 · records 层（`billing_repo` 三拆后的数据记录、域常量与套餐种子）。

本文件是 `app/db/repositories/billing/` 包拆分后的**最底层**：6 个数据记录
（dataclass）、两侧共用的状态/枚举/字段白名单常量、三个时间与 JSON 口径纯函数
（`now_iso` / `to_iso` / `parse_iso`），以及内存后端的套餐种子 `default_plans()`，
**不依赖** protocol / memory / postgres，因此可被它们任意引用而不成环
（依赖方向 `records ← protocol ← {memory, postgres} ← __init__`）。

**表名不在这里**：`PLAN_TABLE` / `SUBSCRIPTION_TABLE` / `ORDER_TABLE` /
`INVOICE_TABLE` / `CALLBACK_TABLE` / `TENANT_TABLE` 只服务 PG 的 SQL，放 `postgres.py`；
`PLATFORM_TENANT`（平台哨兵租户）留在本层，因为它同时是 `default_plans()`、
`PlanRecord` / `CallbackLogRecord` 的默认值与 PG 共享行的过滤条件——两侧都要用。

**`default_plans()` 与 SQL 种子是"有意镜像"，不是重复真源**：它只给内存后端播种，
docstring 已声明与 `deploy/init-schema-phase2.sql` 第 15 节逐字一致；生产环境的套餐
目录来自数据库种子行（运营后台可改价），代码**不覆盖**也不回读。拆包不改这条语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

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


__all__: list[str] = [
    "ACTIVE_SUBSCRIPTION_STATUSES",
    "CHANNEL_WECHAT_PAY",
    "FREE_PLAN_SEATS",
    "INVOICE_ISSUED",
    "INVOICE_PENDING",
    "ORDER_CANCELLED",
    "ORDER_CLOSED",
    "ORDER_MUTABLE_FIELDS",
    "ORDER_PAID",
    "ORDER_PENDING",
    "ORDER_REFUNDED",
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
    "PLATFORM_TENANT",
    "SUBSCRIPTION_ACTIVE",
    "SUBSCRIPTION_CANCELLED",
    "SUBSCRIPTION_EXPIRED",
    "SUBSCRIPTION_PAST_DUE",
    "SUBSCRIPTION_TRIALING",
    "TENANT_ACTIVE",
    "TENANT_MUTABLE_FIELDS",
    "TENANT_SUSPENDED",
    "CallbackLogRecord",
    "InvoiceRecord",
    "OrderRecord",
    "PlanRecord",
    "SubscriptionRecord",
    "TenantRecord",
    "default_plans",
    "now_iso",
    "parse_iso",
    "to_iso",
]
