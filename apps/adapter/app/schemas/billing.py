"""计费域接口契约（阶段二 BL1-BL10）。

字段口径（架构文档 §4.5）：额度一律以「字数额度」为单位，**不出现 token**；
金额单位为**分**（`amount_cents`），展示层除以 100；百分比两位小数。

B07 落 BL5（用量明细），B09 追加 BL1-BL4、BL6-BL10（加性扩展，不改既有字段）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: BL5 分组维度
UsageGroupBy = Literal["member", "department", "capability"]

#: BL3 套餐变更动作
SubscriptionAction = Literal["subscribe", "upgrade", "downgrade", "cancel_renew"]

#: BL6 订单类型
OrderType = Literal["new", "renew", "upgrade", "seats"]

#: BL6 支付方式
PaymentMethod = Literal["online", "offline_transfer"]

#: 周期格式（`YYYY-MM`）
PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class BillingUsageQuery(BaseModel):
    """BL5 用量查询参数。

    Attributes:
        period: 账期 `YYYY-MM`；缺省取当前 UTC 月。
        group_by: 分组维度；**管理视图**（需管理员）。
    """

    period: str = Field(
        default="",
        max_length=7,
        pattern=PERIOD_PATTERN,
        description="账期，形如 2026-11；缺省为当前月",
    )
    group_by: UsageGroupBy | None = Field(
        default=None, description="分组维度：member / department / capability（管理视图）"
    )


class UsageBreakdownItem(BaseModel):
    """用量分项。

    Attributes:
        key: 分项键（成员 ID / 部门 ID / 能力名）。
        display_name: 用户可见名称（未登记账号可为空）。
        units: 用量（字数额度）。
        percent: 占总额度百分比（两位小数）。
    """

    key: str = Field(default="", description="分项键")
    display_name: str = Field(default="", description="用户可见名称")
    units: int = Field(default=0, ge=0, description="用量（字数额度）")
    percent: float = Field(default=0.0, ge=0.0, description="占总额度百分比")


class UsageDailyTrendItem(BaseModel):
    """按自然日的用量点。

    Attributes:
        date: 日期 `YYYY-MM-DD`。
        units: 当日用量。
    """

    date: str = Field(default="", description="日期 YYYY-MM-DD")
    units: int = Field(default=0, ge=0, description="当日用量")


class BillingUsageData(BaseModel):
    """BL5 用量响应数据（个人视图与企业管理视图共用同一结构）。

    Attributes:
        period: 账期 `YYYY-MM`。
        quota_units_total: 额度总量。
        quota_units_used: 已用额度（与 M4 `used_units` **同刻恒等**）。
        quota_units_remaining: 剩余额度。
        usage_percent: 使用百分比。
        over_quota_warning: 是否已达/超额（软提示）。
        quota_unit: 额度单位文案。
        breakdown: 分项明细。
        daily_trend: 按日趋势。
    """

    period: str = Field(default="", description="账期，形如 2026-11")
    quota_units_total: int = Field(default=0, ge=0, description="额度总量")
    quota_units_used: int = Field(default=0, ge=0, description="已用额度")
    quota_units_remaining: int = Field(default=0, ge=0, description="剩余额度")
    usage_percent: float = Field(default=0.0, ge=0.0, description="使用百分比")
    over_quota_warning: bool = Field(default=False, description="是否已达额度上限")
    quota_unit: str = Field(default="", description="额度单位文案")
    breakdown: list[UsageBreakdownItem] = Field(default_factory=list, description="分项明细")
    daily_trend: list[UsageDailyTrendItem] = Field(default_factory=list, description="按日趋势")


__all__: list[str] = [
    "PERIOD_PATTERN",
    "BillingUsageData",
    "BillingUsageQuery",
    "ChangeSubscriptionData",
    "ChangeSubscriptionRequest",
    "CreateOrderData",
    "CreateOrderRequest",
    "InvoiceItem",
    "InvoiceListData",
    "OrderDetailData",
    "OrderItem",
    "OrderType",
    "PaymentCallbackData",
    "PaymentCallbackRequest",
    "PaymentMethod",
    "PendingChange",
    "PlanFeatureItem",
    "PlanItem",
    "PlanListData",
    "PlanStatusItem",
    "SubscriptionAction",
    "SubscriptionData",
    "UpdateSeatsData",
    "UpdateSeatsRequest",
    "UsageBreakdownItem",
    "UsageDailyTrendItem",
    "UsageGroupBy",
]


# ===========================================================================
# BL1 套餐目录
# ===========================================================================
#: 功能开关的用户可见名称（前端不硬编码额度，只渲染本表返回的文案）
FEATURE_DISPLAY_NAMES: dict[str, str] = {
    "kb_count": "知识库数量",
    "kb_document_limit": "单库文档上限",
    "tool_count": "可连接办公工具数",
    "plugin_market": "插件市场",
    "org_structure": "组织架构与部门管理",
    "role_permission": "角色与权限配置",
    "audit_log": "操作记录",
    "private_deploy": "私有化部署",
}


class PlanFeatureItem(BaseModel):
    """套餐功能开关。

    Attributes:
        feature_key: 功能键（与 `billing_plan.features` 的 JSON 键一致）。
        display_name: 用户可见名称。
        included: 是否包含该功能。
        limit: 数量上限（`None` 表示不限量）。
    """

    feature_key: str = Field(default="", description="功能键")
    display_name: str = Field(default="", description="功能名称")
    included: bool = Field(default=True, description="是否包含")
    limit: int | None = Field(default=None, description="数量上限；None 表示不限")


class PlanItem(BaseModel):
    """套餐条目（BL1 `plans[]` 元素）。

    Attributes:
        plan_code: 套餐标识（`free` / `professional` / `enterprise`）。
        display_name: 套餐名称。
        price_per_seat_month_cents: 每席位每月价格（分）。
        currency: 币种。
        min_seats: 最小席位数。
        quota_units_per_seat_month: 每席位每月字数额度。
        quota_unit_label: 额度单位文案。
        purchasable_quota: 是否可单独购买额度包。
        features: 功能开关清单。
        status: 可购状态（`available` / `offline`）。
        trial_days: 试用天数。
    """

    plan_code: str = Field(default="", description="套餐标识")
    display_name: str = Field(default="", description="套餐名称")
    price_per_seat_month_cents: int = Field(default=0, ge=0, description="每席位每月价格（分）")
    currency: str = Field(default="CNY", description="币种")
    min_seats: int = Field(default=1, ge=1, description="最小席位数")
    quota_units_per_seat_month: int = Field(default=0, ge=0, description="每席位每月字数额度")
    quota_unit_label: str = Field(default="字数额度", description="额度单位文案")
    purchasable_quota: bool = Field(default=False, description="是否可单独购买额度")
    features: list[PlanFeatureItem] = Field(default_factory=list, description="功能开关清单")
    status: str = Field(default="available", description="可购状态")
    trial_days: int = Field(default=0, ge=0, description="试用天数")


class PlanListData(BaseModel):
    """BL1 套餐列表。

    Attributes:
        plans: 套餐清单（按 `sort_order` 升序）。
    """

    plans: list[PlanItem] = Field(default_factory=list, description="套餐清单")


class PlanStatusItem(BaseModel):
    """套餐上下架状态（OPS7 `settings.plan_status` 元素）。

    Attributes:
        plan_code: 套餐标识。
        display_name: 套餐名称。
        status: 可购状态。
    """

    plan_code: str = Field(default="", description="套餐标识")
    display_name: str = Field(default="", description="套餐名称")
    status: str = Field(default="available", description="可购状态")


# ===========================================================================
# BL2 / BL3 / BL4 订阅
# ===========================================================================
class PendingChange(BaseModel):
    """待生效的套餐变更（降级）。

    Attributes:
        plan_code: 目标套餐标识。
        effective_at: 生效时间（ISO 8601，等于当前周期末）。
    """

    plan_code: str = Field(default="", description="目标套餐标识")
    effective_at: str = Field(default="", description="生效时间")


class SubscriptionData(BaseModel):
    """BL2 订阅详情。

    无订阅时返回**合成视图**（`plan_code=free`、`status=active`、`seats_total=3`），
    **不返回 404**（架构文档 §4.5 要点）。

    Attributes:
        subscription_id: 订阅 ID（合成视图为空串）。
        plan_code: 套餐标识。
        plan_name: 套餐名称。
        status: 订阅状态。
        seats_total: 总席位。
        seats_used: 已用席位。
        started_at: 订阅开始时间。
        current_period_end: 当前周期结束时间。
        quota_units_total: 额度总量（席位 × 每席位额度）。
        quota_units_used: 已用额度（与 BL5 同源）。
        auto_renew: 是否自动续费。
        pending_change: 待生效变更；无则为 None。
    """

    subscription_id: str = Field(default="", description="订阅 ID")
    plan_code: str = Field(default="free", description="套餐标识")
    plan_name: str = Field(default="", description="套餐名称")
    status: str = Field(default="active", description="订阅状态")
    seats_total: int = Field(default=3, ge=0, description="总席位")
    seats_used: int = Field(default=0, ge=0, description="已用席位")
    started_at: str = Field(default="", description="开始时间")
    current_period_end: str = Field(default="", description="当前周期结束时间")
    quota_units_total: int = Field(default=0, ge=0, description="额度总量")
    quota_units_used: int = Field(default=0, ge=0, description="已用额度")
    auto_renew: bool = Field(default=True, description="是否自动续费")
    pending_change: PendingChange | None = Field(default=None, description="待生效变更")


class ChangeSubscriptionRequest(BaseModel):
    """BL3 套餐变更请求。

    Attributes:
        plan_code: 目标套餐标识（`cancel_renew` 时可留空）。
        seats: 目标席位数。
        action: 动作（`subscribe` / `upgrade` / `downgrade` / `cancel_renew`）。
    """

    plan_code: str = Field(default="", max_length=32, description="目标套餐标识")
    seats: int = Field(default=1, ge=1, le=100_000, description="目标席位数")
    action: SubscriptionAction = Field(default="subscribe", description="变更动作")


class ChangeSubscriptionData(BaseModel):
    """BL3 套餐变更结果。

    Attributes:
        subscription_id: 订阅 ID。
        status: 变更后的订阅状态。
        effective_at: 生效时间（升级为当前时刻；降级为周期末）。
        order_id: 关联订单 ID（无需支付时为空串）。
        over_quota_warning: 变更后是否已超额（`true` 时前端提示、不弹错）。
        notice_code: 非阻断提示码（降级为 `15005`，否则 0）。
        notice_message: 提示码对应的业务文案。
    """

    subscription_id: str = Field(default="", description="订阅 ID")
    status: str = Field(default="active", description="订阅状态")
    effective_at: str = Field(default="", description="生效时间")
    order_id: str = Field(default="", description="关联订单 ID；无需支付时为空串")
    over_quota_warning: bool = Field(default=False, description="变更后是否已超额")
    notice_code: int = Field(default=0, description="非阻断提示码（15005=降级周期末生效）")
    notice_message: str = Field(default="", description="提示码对应的业务文案")


class UpdateSeatsRequest(BaseModel):
    """BL4 调整席位请求。

    Attributes:
        seats: 目标席位数（`>= 1`）。
    """

    seats: int = Field(default=1, ge=1, le=100_000, description="目标席位数")


class UpdateSeatsData(BaseModel):
    """BL4 调整席位结果。

    Attributes:
        seats_total: 调整后的总席位。
        seats_used: 已用席位。
        effective_at: 生效时间。
        order_id: 关联订单 ID（加席需支付时非空）。
        over_quota_warning: 是否已超额。
    """

    seats_total: int = Field(default=0, ge=0, description="总席位")
    seats_used: int = Field(default=0, ge=0, description="已用席位")
    effective_at: str = Field(default="", description="生效时间")
    order_id: str = Field(default="", description="关联订单 ID")
    over_quota_warning: bool = Field(default=False, description="是否已超额")


# ===========================================================================
# BL6 / BL7 / BL8 订单
# ===========================================================================
class CreateOrderRequest(BaseModel):
    """BL6 下单请求。

    Attributes:
        plan_code: 目标套餐标识。
        seats: 席位数。
        order_type: 订单类型（`new` / `renew` / `upgrade` / `seats`）。
        payment_method: 支付方式（`online` / `offline_transfer`）。
    """

    plan_code: str = Field(default="", max_length=32, description="目标套餐标识")
    seats: int = Field(default=1, ge=1, le=100_000, description="席位数")
    order_type: OrderType = Field(default="new", description="订单类型")
    payment_method: PaymentMethod = Field(default="online", description="支付方式")


class CreateOrderData(BaseModel):
    """BL6 下单结果。

    Attributes:
        order_id: 订单 ID。
        amount_cents: 应付金额（分）。
        currency: 币种。
        status: 订单状态（恒为 `pending_payment`）。
        payment_method: 支付方式。
        pay_url: 在线支付跳转地址（对公转账时为空串）。
        bank_info: 对公转账收款信息（在线支付时为 None）。
        expires_at: 支付截止时间。
    """

    order_id: str = Field(default="", description="订单 ID")
    amount_cents: int = Field(default=0, ge=0, description="应付金额（分）")
    currency: str = Field(default="CNY", description="币种")
    status: str = Field(default="pending_payment", description="订单状态")
    payment_method: str = Field(default="online", description="支付方式")
    pay_url: str = Field(default="", description="在线支付跳转地址")
    bank_info: dict[str, Any] | None = Field(default=None, description="对公转账收款信息")
    expires_at: str = Field(default="", description="支付截止时间")


class OrderItem(BaseModel):
    """BL7 订单列表条目。

    Attributes:
        order_id: 订单 ID。
        order_type: 订单类型。
        plan_code: 套餐标识。
        seats: 席位数。
        amount_cents: 金额（分）。
        status: 订单状态。
        payment_method: 支付方式。
        created_at: 创建时间。
    """

    order_id: str = Field(default="", description="订单 ID")
    order_type: str = Field(default="", description="订单类型")
    plan_code: str = Field(default="", description="套餐标识")
    seats: int = Field(default=0, ge=0, description="席位数")
    amount_cents: int = Field(default=0, ge=0, description="金额（分）")
    status: str = Field(default="", description="订单状态")
    payment_method: str = Field(default="", description="支付方式")
    created_at: str = Field(default="", description="创建时间")


class OrderDetailData(BaseModel):
    """BL8 订单详情。

    Attributes:
        order_id: 订单 ID。
        status: 订单状态。
        amount_cents: 金额（分）。
        payment_no_masked: 脱敏支付流水号。
        paid_at: 支付时间。
        invoice_status: 开票状态（`issued` / `pending` / 空串）。
        plan_code: 套餐标识。
        seats: 席位数。
        created_at: 创建时间。
        expires_at: 支付截止时间。
    """

    order_id: str = Field(default="", description="订单 ID")
    status: str = Field(default="", description="订单状态")
    amount_cents: int = Field(default=0, ge=0, description="金额（分）")
    payment_no_masked: str = Field(default="", description="脱敏支付流水号")
    paid_at: str = Field(default="", description="支付时间")
    invoice_status: str = Field(default="", description="开票状态")
    plan_code: str = Field(default="", description="套餐标识")
    seats: int = Field(default=0, ge=0, description="席位数")
    created_at: str = Field(default="", description="创建时间")
    expires_at: str = Field(default="", description="支付截止时间")


# ===========================================================================
# BL9 账单
# ===========================================================================
class InvoiceItem(BaseModel):
    """BL9 账单条目。

    Attributes:
        invoice_id: 账单 ID。
        period: 账期 `YYYY-MM`。
        amount_cents: 金额（分）。
        status: 状态（`issued` / `pending`）。
        download_url: 下载地址（本阶段为空串，由运营侧另行开通）。
    """

    invoice_id: str = Field(default="", description="账单 ID")
    period: str = Field(default="", description="账期 YYYY-MM")
    amount_cents: int = Field(default=0, ge=0, description="金额（分）")
    status: str = Field(default="pending", description="状态")
    download_url: str = Field(default="", description="下载地址")


class InvoiceListData(BaseModel):
    """BL9 账单列表。

    Attributes:
        items: 账单清单（按创建时间倒序）。
    """

    items: list[InvoiceItem] = Field(default_factory=list, description="账单清单")


# ===========================================================================
# BL10 支付回调
# ===========================================================================
class PaymentCallbackRequest(BaseModel):
    """BL10 支付回调载荷（渠道方字段，按需扩展）。

    Attributes:
        out_trade_no: 商户订单号（即 `order_id`）。
        transaction_id: 渠道流水号（幂等键）。
        amount_total: 实付金额（分）。
        sign: 渠道签名。
        trade_state: 交易状态（`SUCCESS` / 其他）。
    """

    model_config = ConfigDict(extra="allow")

    out_trade_no: str = Field(default="", max_length=128, description="商户订单号")
    transaction_id: str = Field(default="", max_length=128, description="渠道流水号")
    amount_total: int = Field(default=0, ge=0, description="实付金额（分）")
    sign: str = Field(default="", max_length=256, description="渠道签名")
    # 缺省必须是空串，**不能**是 "SUCCESS"：本模型的 `model_dump()` 是回调业务校验的
    # 入口（`api/v1/billing.py`），默认成 SUCCESS 等于「渠道没说成功就算成功」——
    # 一条缺字段的回调就能把订单推成已支付（fail-open）。空串会落到
    # `PaymentNotification.is_success` 的白名单外，走 `trade_not_success` 拒绝出口。
    trade_state: str = Field(
        default="", max_length=32, description="交易状态（`SUCCESS` 表示成功）"
    )


class PaymentCallbackData(BaseModel):
    """BL10 回调应答（**对渠道方语义**，与业务错误码解耦）。

    Attributes:
        code: 渠道侧应答码（`SUCCESS` 表示已处理）。
        message: 渠道侧应答说明。
    """

    code: str = Field(default="SUCCESS", description="渠道侧应答码")
    message: str = Field(default="支付结果已处理", description="渠道侧应答说明")
