"""运营后台接口契约（阶段二 B10，架构文档 §4.6 OPS1-OPS8）。

**最小可见原则**（PRD 风险 R5，落到字段层面）：

- 本文件定义的所有模型**都不含**对话内容、文档原文、成员邮箱字段；
- 企业名称可脱敏展示（`name` 为脱敏后的展示名，脱敏规则在服务层实现）；
- 金额一律以「分」为单位，额度一律为「字数额度」，**不出现 token**。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: 统计区间（OPS6）
MetricsRange = Literal["7d", "30d"]

#: 企业状态（OPS3）
TenantStatus = Literal["active", "suspended"]

#: 订单状态过滤取值（含空串表示不限）
ORDER_STATUS_FILTER = r"^(pending_payment|paid|closed|cancelled|refunded)?$"


class TenantListItem(BaseModel):
    """OPS1 企业列表条目。

    Attributes:
        tenant_id: 企业 ID。
        name: 企业展示名（可脱敏）。
        plan_code: 当前套餐。
        seats_total: 总席位。
        seats_used: 已用席位。
        status: 企业状态。
        created_at: 创建时间。
        quota_units_used: 当月已用额度（与 BL5 企业维度同源）。
    """

    tenant_id: str = Field(default="", description="企业 ID")
    name: str = Field(default="", description="企业展示名")
    plan_code: str = Field(default="free", description="当前套餐")
    seats_total: int = Field(default=0, ge=0, description="总席位")
    seats_used: int = Field(default=0, ge=0, description="已用席位")
    status: str = Field(default="active", description="企业状态")
    created_at: str = Field(default="", description="创建时间")
    quota_units_used: int = Field(default=0, ge=0, description="当月已用额度")


class TenantListData(BaseModel):
    """OPS1 企业列表。

    Attributes:
        items: 企业条目。
        page: 页码。
        page_size: 每页条数。
        total: 总条数。
        total_pages: 总页数。
    """

    items: list[TenantListItem] = Field(default_factory=list, description="企业条目")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, description="每页条数")
    total: int = Field(default=0, ge=0, description="总条数")
    total_pages: int = Field(default=0, ge=0, description="总页数")


class TenantProfile(BaseModel):
    """OPS2 企业基本信息。

    Attributes:
        tenant_id: 企业 ID。
        name: 企业展示名（可脱敏）。
        status: 企业状态。
        plan_code: 当前套餐。
        deploy_mode: 部署模式。
        created_at: 创建时间。
        suspended_reason: 停用原因。
    """

    tenant_id: str = Field(default="", description="企业 ID")
    name: str = Field(default="", description="企业展示名")
    status: str = Field(default="active", description="企业状态")
    plan_code: str = Field(default="free", description="当前套餐")
    deploy_mode: str = Field(default="saas", description="部署模式")
    created_at: str = Field(default="", description="创建时间")
    suspended_reason: str = Field(default="", description="停用原因")


class TenantSubscriptionSummary(BaseModel):
    """OPS2 订阅摘要。

    Attributes:
        plan_code: 套餐标识。
        plan_name: 套餐名称。
        status: 订阅状态。
        seats_total: 总席位。
        seats_used: 已用席位。
        current_period_end: 当前周期结束时间。
        auto_renew: 是否自动续费。
    """

    plan_code: str = Field(default="free", description="套餐标识")
    plan_name: str = Field(default="", description="套餐名称")
    status: str = Field(default="active", description="订阅状态")
    seats_total: int = Field(default=0, ge=0, description="总席位")
    seats_used: int = Field(default=0, ge=0, description="已用席位")
    current_period_end: str = Field(default="", description="周期结束时间")
    auto_renew: bool = Field(default=True, description="是否自动续费")


class TenantUsageSummary(BaseModel):
    """OPS2 用量摘要（只给聚合数字，不含任何内容字段）。

    Attributes:
        period: 账期 `YYYY-MM`。
        quota_units_total: 额度总量。
        quota_units_used: 已用额度。
        usage_percent: 使用百分比。
        member_count: 在册成员数。
    """

    period: str = Field(default="", description="账期")
    quota_units_total: int = Field(default=0, ge=0, description="额度总量")
    quota_units_used: int = Field(default=0, ge=0, description="已用额度")
    usage_percent: float = Field(default=0.0, ge=0.0, description="使用百分比")
    member_count: int = Field(default=0, ge=0, description="在册成员数")


class OpsOrderItem(BaseModel):
    """订单条目（OPS2 `recent_orders` 与 OPS4 `items` 共用）。

    Attributes:
        order_id: 订单 ID。
        tenant_id: 企业 ID。
        tenant_name: 企业展示名。
        order_type: 订单类型。
        plan_code: 套餐标识。
        seats: 席位数。
        amount_cents: 金额（分）。
        status: 订单状态。
        payment_method: 支付方式。
        created_at: 创建时间。
        paid_at: 支付时间。
    """

    order_id: str = Field(default="", description="订单 ID")
    tenant_id: str = Field(default="", description="企业 ID")
    tenant_name: str = Field(default="", description="企业展示名")
    order_type: str = Field(default="", description="订单类型")
    plan_code: str = Field(default="", description="套餐标识")
    seats: int = Field(default=0, ge=0, description="席位数")
    amount_cents: int = Field(default=0, ge=0, description="金额（分）")
    status: str = Field(default="", description="订单状态")
    payment_method: str = Field(default="", description="支付方式")
    created_at: str = Field(default="", description="创建时间")
    paid_at: str = Field(default="", description="支付时间")


class TenantDetailData(BaseModel):
    """OPS2 企业详情。

    Attributes:
        tenant: 企业基本信息。
        subscription: 订阅摘要。
        usage: 用量摘要。
        recent_orders: 近期订单（最多 10 条）。
    """

    tenant: TenantProfile = Field(default_factory=TenantProfile, description="企业信息")
    subscription: TenantSubscriptionSummary = Field(
        default_factory=TenantSubscriptionSummary, description="订阅摘要"
    )
    usage: TenantUsageSummary = Field(default_factory=TenantUsageSummary, description="用量摘要")
    recent_orders: list[OpsOrderItem] = Field(default_factory=list, description="近期订单")


class UpdateTenantStatusRequest(BaseModel):
    """OPS3 启用 / 停用企业请求。

    Attributes:
        status: 目标状态。
        reason: 停用原因（停用时必填，展示在操作记录里）。
    """

    status: TenantStatus = Field(default="active", description="目标状态")
    reason: str = Field(default="", max_length=200, description="停用原因")


class UpdateTenantStatusData(BaseModel):
    """OPS3 启用 / 停用企业结果。

    Attributes:
        tenant_id: 企业 ID。
        status: 变更后状态。
        updated_at: 更新时间。
    """

    tenant_id: str = Field(default="", description="企业 ID")
    status: str = Field(default="active", description="变更后状态")
    updated_at: str = Field(default="", description="更新时间")


class OpsOrderListData(BaseModel):
    """OPS4 订单列表。

    Attributes:
        items: 订单条目。
        page: 页码。
        page_size: 每页条数。
        total: 总条数。
        total_pages: 总页数。
    """

    items: list[OpsOrderItem] = Field(default_factory=list, description="订单条目")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, description="每页条数")
    total: int = Field(default=0, ge=0, description="总条数")
    total_pages: int = Field(default=0, ge=0, description="总页数")


class ConfirmOrderRequest(BaseModel):
    """OPS5 核销请求。

    Attributes:
        paid_amount_cents: 实际到账金额（分）。
        remark: 核销备注（写入操作记录）。
    """

    paid_amount_cents: int = Field(default=0, ge=0, description="到账金额（分）")
    remark: str = Field(default="", max_length=200, description="核销备注")


class ConfirmOrderData(BaseModel):
    """OPS5 核销结果。

    Attributes:
        order_id: 订单 ID。
        status: 订单状态（`paid`）。
        subscription_status: 订阅状态。
    """

    order_id: str = Field(default="", description="订单 ID")
    status: str = Field(default="paid", description="订单状态")
    subscription_status: str = Field(default="active", description="订阅状态")


class PlanDistributionItem(BaseModel):
    """OPS6 套餐分布条目。

    Attributes:
        plan_code: 套餐标识。
        plan_name: 套餐名称。
        tenant_count: 企业数。
    """

    plan_code: str = Field(default="", description="套餐标识")
    plan_name: str = Field(default="", description="套餐名称")
    tenant_count: int = Field(default=0, ge=0, description="企业数")


class TopTenantItem(BaseModel):
    """OPS6 用量 Top 企业条目。

    Attributes:
        tenant_id: 企业 ID。
        name: 企业展示名。
        plan_code: 套餐标识。
        quota_units_used: 区间内已用额度。
    """

    tenant_id: str = Field(default="", description="企业 ID")
    name: str = Field(default="", description="企业展示名")
    plan_code: str = Field(default="", description="套餐标识")
    quota_units_used: int = Field(default=0, ge=0, description="区间内已用额度")


class OpsMetricsData(BaseModel):
    """OPS6 平台统计。

    Attributes:
        range: 统计区间（`7d` / `30d`）。
        tenant_count: 企业总数。
        active_tenant_count: 区间内有用量的企业数。
        active_user_count: 区间内活跃成员数（去重）。
        conversation_count: 区间内对话次数。
        document_count: 区间内文档处理次数。
        quota_units_total: 区间内额度总和（与 BL5 企业维度求和**误差 0**）。
        plan_distribution: 套餐分布。
        top_tenants: 用量 Top 企业（最多 20 条）。
    """

    range: str = Field(default="7d", description="统计区间")
    tenant_count: int = Field(default=0, ge=0, description="企业总数")
    active_tenant_count: int = Field(default=0, ge=0, description="活跃企业数")
    active_user_count: int = Field(default=0, ge=0, description="活跃成员数")
    conversation_count: int = Field(default=0, ge=0, description="对话次数")
    document_count: int = Field(default=0, ge=0, description="文档处理次数")
    quota_units_total: int = Field(default=0, ge=0, description="区间内额度总和")
    plan_distribution: list[PlanDistributionItem] = Field(
        default_factory=list, description="套餐分布"
    )
    top_tenants: list[TopTenantItem] = Field(default_factory=list, description="用量 Top 企业")


class OpsSettingsPayload(BaseModel):
    """OPS7 / OPS8 系统配置（读全量、写部分）。

    Attributes:
        quota_warn_threshold: 额度告警阈值（百分比）。
        trial_days: 默认试用天数。
        quota_unit_label: 额度单位文案。
        announcement: 平台公告。
        plan_status: 套餐上下架状态（`plan_code → status`）。
        updated_by: 最后修改人。
        updated_at: 最后修改时间。
    """

    quota_warn_threshold: int = Field(default=80, ge=0, le=100, description="额度告警阈值")
    trial_days: int = Field(default=14, ge=0, le=365, description="默认试用天数")
    quota_unit_label: str = Field(default="字数额度", max_length=32, description="额度单位文案")
    announcement: str = Field(default="", max_length=500, description="平台公告")
    plan_status: dict[str, str] = Field(default_factory=dict, description="套餐上下架状态")
    updated_by: str = Field(default="", description="最后修改人")
    updated_at: str = Field(default="", description="最后修改时间")


class UpdateOpsSettingsRequest(BaseModel):
    """OPS8 修改系统配置请求（字段全部可选，只改传入项）。

    Attributes:
        quota_warn_threshold: 额度告警阈值。
        trial_days: 默认试用天数。
        quota_unit_label: 额度单位文案。
        announcement: 平台公告。
    """

    quota_warn_threshold: int | None = Field(default=None, ge=0, le=100, description="告警阈值")
    trial_days: int | None = Field(default=None, ge=0, le=365, description="试用天数")
    quota_unit_label: str | None = Field(default=None, max_length=32, description="额度单位")
    announcement: str | None = Field(default=None, max_length=500, description="平台公告")


__all__: list[str] = [
    "ORDER_STATUS_FILTER",
    "ConfirmOrderData",
    "ConfirmOrderRequest",
    "MetricsRange",
    "OpsMetricsData",
    "OpsOrderItem",
    "OpsOrderListData",
    "OpsSettingsPayload",
    "PlanDistributionItem",
    "TenantDetailData",
    "TenantListData",
    "TenantListItem",
    "TenantProfile",
    "TenantStatus",
    "TenantSubscriptionSummary",
    "TenantUsageSummary",
    "TopTenantItem",
    "UpdateOpsSettingsRequest",
    "UpdateTenantStatusData",
    "UpdateTenantStatusRequest",
]
