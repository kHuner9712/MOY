"""运营后台业务服务（阶段二 B10，架构文档 §4.6 OPS1-OPS8）。

**最小可见原则**（PRD 风险 R5）在服务层的落地方式：

| 约束 | 落地 |
| --- | --- |
| 不返回对话内容 / 文档原文 | 本服务**不查询**这两类数据，统计只取聚合数字 |
| 不返回成员邮箱明文 | 响应模型里根本没有邮箱字段（不是「查出来再过滤」） |
| 企业名可脱敏 | `mask_tenant_name()` 提供脱敏形态；本阶段保留原名以支持运营检索 |
| 凭据不外泄 | 不读 `credential_store` / `tool_connection.secret_*` |

**统计口径单一**（验收 ③）：`quota_units_total` 由 `UsageService.range_stats()`
逐企业求和，与 BL5 企业维度用**同一张表、同一段聚合**——「误差 0」是结构保证，
不是对账凑出来的。

**所有写操作都写 `audit_log`**（验收 ②）：`actor_id` = 运营人，
`tenant_id` = 被操作企业（平台级配置用哨兵 `__platform__` 并在 `change_summary`
里标注 `scope=platform`），`change_summary` 含运营人与备注。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.business.audit_service import (
    ACTION_ORDER_CONFIRMED,
    ACTION_SETTINGS_UPDATE,
    ACTION_TENANT_STATUS,
    RESOURCE_ORDER,
    RESOURCE_SETTINGS,
    RESOURCE_TENANT,
    AuditService,
)
from app.business.order_service import OrderService
from app.business.plan_service import PlanService
from app.business.policy_service import PolicyService, get_policy_service
from app.business.subscription_service import SubscriptionService
from app.business.usage_service import UsageService
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.billing import (
    TENANT_SUSPENDED,
    BillingStore,
    OrderRecord,
    TenantRecord,
    get_billing_store,
    now_iso,
)
from app.db.repositories.ops_repo import OpsSettings, OpsStore, get_ops_store
from app.schemas.ops import (
    ConfirmOrderData,
    ConfirmOrderRequest,
    OpsMetricsData,
    OpsOrderItem,
    OpsOrderListData,
    OpsSettingsPayload,
    PlanDistributionItem,
    TenantDetailData,
    TenantListData,
    TenantListItem,
    TenantProfile,
    TenantSubscriptionSummary,
    TenantUsageSummary,
    TopTenantItem,
    UpdateOpsSettingsRequest,
    UpdateTenantStatusData,
    UpdateTenantStatusRequest,
)

logger = get_logger(__name__)

#: 统计区间 → 天数
METRICS_RANGES: Final[dict[str, int]] = {"7d": 7, "30d": 30}

#: Top 企业榜条数
TOP_TENANT_LIMIT: Final[int] = 20

#: 企业详情里的近期订单条数
RECENT_ORDER_LIMIT: Final[int] = 10

#: 停用原因缺失时的兜底说明（写进操作记录，不阻断操作）
DEFAULT_SUSPEND_REASON: Final[str] = "运营停用（未填写原因）"

#: 配置更新至少一项的文案
MESSAGE_EMPTY_SETTINGS: Final[str] = "请至少修改一项配置后重试"


def mask_tenant_name(name: str) -> str:
    """把企业名称脱敏为展示形态（保留首尾字符）。

    Args:
        name: 企业名称。

    Returns:
        str: 脱敏名称；空值返回空串。
    """
    value = (name or "").strip()
    if not value:
        return ""
    if len(value) <= 2:
        return f"{value[0]}*"
    return f"{value[0]}{'*' * (len(value) - 2)}{value[-1]}"


@dataclass(frozen=True, slots=True)
class MetricsWindow:
    """统计时间窗（左开右闭到秒）。

    Attributes:
        label: 区间标签（`7d` / `30d`）。
        start_at: 起始时间（ISO 8601，含）。
        end_at: 结束时间（ISO 8601，不含）。
    """

    label: str
    start_at: str
    end_at: str


def metrics_window(label: str, moment: datetime | None = None) -> MetricsWindow:
    """按区间标签计算时间窗。

    Args:
        label: 区间标签（`7d` / `30d`）。
        moment: 结束时刻（缺省取当前 UTC 时间）。

    Returns:
        MetricsWindow: 时间窗。

    Raises:
        ApiError: 区间取值非法（20001）。
    """
    days = METRICS_RANGES.get(label)
    if days is None:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            message="统计区间仅支持最近 7 天或最近 30 天",
            internal_detail=f"range={label}",
        )
    end = moment or datetime.now(UTC)
    start = end - timedelta(days=days)
    return MetricsWindow(
        label=label,
        start_at=start.isoformat().replace("+00:00", "Z"),
        end_at=end.isoformat().replace("+00:00", "Z"),
    )


class OpsService:
    """运营后台业务服务（OPS1-OPS8 的唯一业务入口）。

    Attributes:
        _settings: 配置实例。
        _store: 运营存储（配置 + 跨租户读取）。
        _billing: 计费存储（企业与订单的权威源）。
        _plans: 套餐服务。
        _subscriptions: 订阅服务。
        _orders: 订单服务。
        _usage: 计量服务。
        _audit: 操作记录服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: OpsStore | None = None,
        audit: AuditService | None = None,
        usage: UsageService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选运营存储（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            usage: 可选计量服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: OpsStore = store or get_ops_store(self._settings)
        # 计费存储必须与运营存储**指向同一份数据**：运营看到的订单就是企业下的单，
        # 否则「企业侧订单」与「运营侧订单」会分裂（核销找不到单、统计对不上）。
        shared_billing: Any = getattr(self._store, "billing", None)
        self._billing: BillingStore = (
            shared_billing if shared_billing is not None else get_billing_store(self._settings)
        )
        self._plans = PlanService(self._settings, self._billing)
        self._subscriptions = SubscriptionService(
            self._settings, self._billing, plans=self._plans, usage=usage
        )
        self._orders = OrderService(
            settings=self._settings,
            store=self._billing,
            plans=self._plans,
            audit=audit,
            subscriptions=self._subscriptions,
        )
        self._usage: UsageService = usage or UsageService(self._settings)
        self._audit = audit or AuditService(self._settings)

    @property
    def store(self) -> OpsStore:
        """当前运营存储（测试可据此断言）。"""
        return self._store

    @property
    def orders(self) -> OrderService:
        """订单服务（核销复用同一套支付生效逻辑）。"""
        return self._orders

    # ------------------------------------------------------------------
    # OPS1 企业列表
    # ------------------------------------------------------------------
    async def list_tenants(
        self, *, keyword: str = "", status: str = "", page: int = 1, page_size: int = 20
    ) -> TenantListData:
        """跨租户企业列表（OPS1）。

        用量只对**当前页**的企业逐个聚合（避免全表 N+1），且与 BL5 同源。

        Args:
            keyword: 名称关键字。
            status: 状态过滤。
            page: 页码（从 1 开始）。
            page_size: 每页条数。

        Returns:
            TenantListData: 企业列表与分页信息。
        """
        tenants = await self._store.list_tenants(keyword=keyword, status=status)
        total = len(tenants)
        start = max(page - 1, 0) * page_size
        slice_items = tenants[start : start + page_size]
        items: list[TenantListItem] = []
        for tenant in slice_items:
            subscription = await self._billing.get_active_subscription(tenant.tenant_id)
            seats_total = subscription.seats_total if subscription is not None else 3
            used_units = await self._usage.tenant_total(tenant_id=tenant.tenant_id)
            items.append(
                TenantListItem(
                    tenant_id=tenant.tenant_id,
                    name=tenant.name,
                    plan_code=tenant.plan_code,
                    seats_total=seats_total,
                    seats_used=await self._subscriptions.seats_used(tenant.tenant_id),
                    status=tenant.status,
                    created_at=tenant.created_at,
                    quota_units_used=used_units,
                )
            )
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        logger.info("ops_tenants_listed", keyword=keyword, status=status, total=total, page=page)
        return TenantListData(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    # ------------------------------------------------------------------
    # OPS2 企业详情
    # ------------------------------------------------------------------
    async def tenant_detail(self, tenant_id: str) -> TenantDetailData:
        """企业详情（OPS2）。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantDetailData: 企业信息 + 订阅 + 用量 + 近期订单。

        Raises:
            ApiError: 企业不存在（20004）。
        """
        tenant = await self._store.get_tenant(tenant_id)
        if tenant is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message="该企业不存在或已被删除",
                internal_detail=f"tenant_id={tenant_id}",
            )
        plan = await self._plans.get(tenant.plan_code)
        subscription = await self._billing.get_active_subscription(tenant_id)
        snapshot = await self._usage.tenant_snapshot(tenant_id=tenant_id)
        seats_used = await self._subscriptions.seats_used(tenant_id)
        orders, _ = await self._store.list_orders(
            tenant_id=tenant_id, offset=0, limit=RECENT_ORDER_LIMIT
        )
        return TenantDetailData(
            tenant=self._profile(tenant),
            subscription=TenantSubscriptionSummary(
                plan_code=tenant.plan_code,
                plan_name=plan.display_name if plan is not None else tenant.plan_code,
                status=subscription.status if subscription is not None else "active",
                seats_total=subscription.seats_total if subscription is not None else 3,
                seats_used=seats_used,
                current_period_end=(
                    subscription.current_period_end if subscription is not None else ""
                ),
                auto_renew=subscription.auto_renew if subscription is not None else True,
            ),
            usage=TenantUsageSummary(
                period=snapshot.period,
                quota_units_total=snapshot.total_units,
                quota_units_used=snapshot.quota_units_used,
                usage_percent=snapshot.usage_percent,
                member_count=seats_used,
            ),
            recent_orders=[self._order_item(order, tenant) for order in orders],
        )

    def _profile(self, tenant: TenantRecord) -> TenantProfile:
        """把企业记录转换为详情用的基本信息。

        Args:
            tenant: 企业记录。

        Returns:
            TenantProfile: 企业基本信息。
        """
        return TenantProfile(
            tenant_id=tenant.tenant_id,
            name=tenant.name,
            status=tenant.status,
            plan_code=tenant.plan_code,
            deploy_mode=tenant.deploy_mode,
            created_at=tenant.created_at,
            suspended_reason=tenant.suspended_reason,
        )

    # ------------------------------------------------------------------
    # OPS3 启用 / 停用企业
    # ------------------------------------------------------------------
    async def update_tenant_status(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        request: UpdateTenantStatusRequest,
    ) -> UpdateTenantStatusData:
        """启用 / 停用企业（OPS3）。

        Args:
            tenant_id: 企业 ID。
            actor_id: 运营人用户 ID。
            request: 状态变更请求。

        Returns:
            UpdateTenantStatusData: 变更结果。

        Raises:
            ApiError: 企业不存在（20004）。
        """
        before = await self._store.get_tenant(tenant_id)
        if before is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message="该企业不存在或已被删除",
                internal_detail=f"tenant_id={tenant_id}",
            )
        reason = ""
        if request.status == TENANT_SUSPENDED:
            reason = request.reason.strip() or DEFAULT_SUSPEND_REASON
        updated = await self._store.update_tenant_status(tenant_id, request.status, reason)
        if updated is None:  # pragma: no cover - 刚读到的企业不可能消失
            raise ApiError(ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"tenant_id={tenant_id}")
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=ACTION_TENANT_STATUS,
            resource_type=RESOURCE_TENANT,
            resource_id=tenant_id,
            change_summary={
                "before": {"status": before.status, "suspended_reason": before.suspended_reason},
                "after": {"status": updated.status, "suspended_reason": updated.suspended_reason},
                "operator": actor_id,
                "remark": reason,
            },
        )
        logger.info(
            "ops_tenant_status_updated",
            tenant_id=tenant_id,
            status=updated.status,
            actor_id=actor_id,
        )
        return UpdateTenantStatusData(
            tenant_id=tenant_id,
            status=updated.status,
            updated_at=updated.updated_at or now_iso(),
        )

    # ------------------------------------------------------------------
    # OPS4 订单列表
    # ------------------------------------------------------------------
    async def list_orders(
        self,
        *,
        tenant_id: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> OpsOrderListData:
        """跨租户订单列表（OPS4）。

        Args:
            tenant_id: 企业过滤。
            status: 状态过滤。
            page: 页码。
            page_size: 每页条数。

        Returns:
            OpsOrderListData: 订单列表与分页信息。
        """
        offset = max(page - 1, 0) * page_size
        orders, total = await self._store.list_orders(
            tenant_id=tenant_id, status=status, offset=offset, limit=page_size
        )
        names = await self._tenant_names({order.tenant_id for order in orders})
        items = [self._order_item(order, names.get(order.tenant_id, "")) for order in orders]
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return OpsOrderListData(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    async def _tenant_names(self, tenant_ids: set[str]) -> dict[str, str]:
        """批量取企业展示名（一次遍历，避免逐条查询）。

        Args:
            tenant_ids: 企业 ID 集合。

        Returns:
            dict[str, str]: `tenant_id → name`。
        """
        if not tenant_ids:
            return {}
        names: dict[str, str] = {}
        for tenant_id in tenant_ids:
            tenant = await self._store.get_tenant(tenant_id)
            names[tenant_id] = tenant.name if tenant is not None else ""
        return names

    def _order_item(
        self, order: OrderRecord, tenant: TenantRecord | str | None = None
    ) -> OpsOrderItem:
        """把订单记录转换为运营视图条目。

        Args:
            order: 订单记录。
            tenant: 企业记录或企业展示名（可选）。

        Returns:
            OpsOrderItem: 运营视图订单条目。
        """
        name = ""
        if isinstance(tenant, TenantRecord):
            name = tenant.name
        elif isinstance(tenant, str):
            name = tenant
        return OpsOrderItem(
            order_id=order.order_id,
            tenant_id=order.tenant_id,
            tenant_name=name,
            order_type=order.order_type,
            plan_code=order.plan_code,
            seats=order.seats,
            amount_cents=order.amount_cents,
            status=order.status,
            payment_method=order.payment_method,
            created_at=order.created_at,
            paid_at=order.paid_at,
        )

    # ------------------------------------------------------------------
    # OPS5 订单核销
    # ------------------------------------------------------------------
    async def confirm_order(
        self, *, order_id: str, actor_id: str, request: ConfirmOrderRequest
    ) -> ConfirmOrderData:
        """运营核销订单（OPS5）。

        幂等：已 `paid` 订单再次核销 → `15008`；金额不符 → `15009`。

        Args:
            order_id: 订单 ID。
            actor_id: 运营人用户 ID。
            request: 核销请求（到账金额 + 备注）。

        Returns:
            ConfirmOrderData: 核销结果。

        Raises:
            ApiError: 订单不存在（20004）、已处理（15008）、金额不符（15009）。
        """
        order = await self._store.get_order(order_id)
        if order is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message="订单不存在或已被删除",
                internal_detail=f"order_id={order_id}",
            )
        remark = request.remark.strip() or "运营核销"
        paid = await self._orders.confirm_paid(
            tenant_id=order.tenant_id,
            order_id=order.order_id,
            paid_amount_cents=request.paid_amount_cents,
            actor_id=actor_id,
            remark=remark,
        )
        subscription = await self._billing.get_active_subscription(order.tenant_id)
        await self._audit.record(
            tenant_id=order.tenant_id,
            actor_id=actor_id,
            action=ACTION_ORDER_CONFIRMED,
            resource_type=RESOURCE_ORDER,
            resource_id=order.order_id,
            change_summary={
                "order_id": order.order_id,
                "paid_amount_cents": request.paid_amount_cents,
                "operator": actor_id,
                "remark": remark,
                "status": paid.status,
            },
        )
        logger.info(
            "ops_order_confirmed",
            order_id=order.order_id,
            tenant_id=order.tenant_id,
            actor_id=actor_id,
        )
        return ConfirmOrderData(
            order_id=paid.order_id,
            status=paid.status,
            subscription_status=subscription.status if subscription is not None else "active",
        )

    # ------------------------------------------------------------------
    # OPS6 平台统计
    # ------------------------------------------------------------------
    async def metrics(
        self, *, range_label: str = "7d", moment: datetime | None = None
    ) -> OpsMetricsData:
        """平台统计（OPS6）。

        Args:
            range_label: 区间标签（`7d` / `30d`）。
            moment: 结束时刻（测试可注入）。

        Returns:
            OpsMetricsData: 平台统计。

        Raises:
            ApiError: 区间取值非法（20001）。
        """
        window = metrics_window(range_label, moment)
        tenants = await self._store.list_tenants()
        catalog = await self._plans.catalog()
        plan_names = {item.plan_code: item.display_name for item in catalog.plans}
        quota_units_total = 0
        active_tenant_count = 0
        active_user_count = 0
        conversation_count = 0
        document_count = 0
        distribution: dict[str, int] = {}
        top: list[TopTenantItem] = []
        for tenant in tenants:
            distribution[tenant.plan_code] = distribution.get(tenant.plan_code, 0) + 1
            stats = await self._usage.range_stats(
                tenant_id=tenant.tenant_id,
                start_at=window.start_at,
                end_at=window.end_at,
            )
            quota_units_total += stats.units
            active_user_count += stats.distinct_users
            conversation_count += stats.conversation_count
            document_count += stats.document_count
            if stats.event_count > 0:
                active_tenant_count += 1
            if stats.units > 0:
                top.append(
                    TopTenantItem(
                        tenant_id=tenant.tenant_id,
                        name=tenant.name,
                        plan_code=tenant.plan_code,
                        quota_units_used=stats.units,
                    )
                )
        top.sort(key=lambda item: (-item.quota_units_used, item.tenant_id))
        plan_distribution = [
            PlanDistributionItem(
                plan_code=plan_code,
                plan_name=plan_names.get(plan_code, plan_code),
                tenant_count=count,
            )
            for plan_code, count in sorted(distribution.items())
        ]
        logger.info(
            "ops_metrics_queried",
            range=window.label,
            tenants=len(tenants),
            quota_units_total=quota_units_total,
        )
        return OpsMetricsData(
            range=window.label,
            tenant_count=len(tenants),
            active_tenant_count=active_tenant_count,
            active_user_count=active_user_count,
            conversation_count=conversation_count,
            document_count=document_count,
            quota_units_total=quota_units_total,
            plan_distribution=plan_distribution,
            top_tenants=top[:TOP_TENANT_LIMIT],
        )

    # ------------------------------------------------------------------
    # OPS7 读取配置
    # ------------------------------------------------------------------
    async def get_settings(self) -> OpsSettingsPayload:
        """读取系统配置（OPS7）。

        Returns:
            OpsSettingsPayload: 全量配置（含套餐上下架状态）。
        """
        settings = await self._store.get_settings()
        status_items = await self._plans.status_items()
        return self._payload(settings, {item.plan_code: item.status for item in status_items})

    def _payload(self, settings: OpsSettings, plan_status: dict[str, str]) -> OpsSettingsPayload:
        """把配置记录转换为响应载荷。

        Args:
            settings: 配置记录。
            plan_status: 套餐上下架状态。

        Returns:
            OpsSettingsPayload: 响应载荷。
        """
        return OpsSettingsPayload(
            quota_warn_threshold=settings.quota_warn_threshold,
            trial_days=settings.trial_days,
            quota_unit_label=settings.quota_unit_label,
            announcement=settings.announcement,
            plan_status=plan_status,
            updated_by=settings.updated_by,
            updated_at=settings.updated_at,
        )

    # ------------------------------------------------------------------
    # OPS8 修改配置
    # ------------------------------------------------------------------
    async def update_settings(
        self, *, actor_id: str, request: UpdateOpsSettingsRequest
    ) -> OpsSettingsPayload:
        """修改系统配置（OPS8）。

        Args:
            actor_id: 运营人用户 ID。
            request: 部分更新请求。

        Returns:
            OpsSettingsPayload: 更新后的全量配置。

        Raises:
            ApiError: 未传任何可改字段（20001）。
        """
        values: dict[str, Any] = {
            key: value
            for key, value in request.model_dump(exclude_none=True).items()
            if key
            in {
                "quota_warn_threshold",
                "trial_days",
                "quota_unit_label",
                "announcement",
            }
        }
        if not values:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message=MESSAGE_EMPTY_SETTINGS,
                internal_detail="empty settings patch",
            )
        before = await self._store.get_settings()
        saved = await self._store.save_settings(values, actor_id)
        await self._audit.record(
            tenant_id="__platform__",
            actor_id=actor_id,
            action=ACTION_SETTINGS_UPDATE,
            resource_type=RESOURCE_SETTINGS,
            resource_id="platform",
            change_summary={
                "scope": "platform",
                "before": {
                    "quota_warn_threshold": before.quota_warn_threshold,
                    "trial_days": before.trial_days,
                    "quota_unit_label": before.quota_unit_label,
                    "announcement": before.announcement,
                },
                "after": {
                    "quota_warn_threshold": saved.quota_warn_threshold,
                    "trial_days": saved.trial_days,
                    "quota_unit_label": saved.quota_unit_label,
                    "announcement": saved.announcement,
                },
                "operator": actor_id,
            },
        )
        logger.info("ops_settings_updated", actor_id=actor_id, keys=sorted(values))
        status_items = await self._plans.status_items()
        return self._payload(saved, {item.plan_code: item.status for item in status_items})

    # ------------------------------------------------------------------
    # 供路由层复用
    # ------------------------------------------------------------------
    def policy(self) -> PolicyService:
        """取可见性判定服务（供路由层判权，保持单一出口）。

        Returns:
            PolicyService: 判定服务实例。
        """
        return get_policy_service(self._settings)


__all__: list[str] = [
    "DEFAULT_SUSPEND_REASON",
    "MESSAGE_EMPTY_SETTINGS",
    "METRICS_RANGES",
    "RECENT_ORDER_LIMIT",
    "TOP_TENANT_LIMIT",
    "MetricsWindow",
    "OpsService",
    "mask_tenant_name",
    "metrics_window",
]
