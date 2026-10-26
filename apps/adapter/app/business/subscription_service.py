"""订阅与席位服务（阶段二 B09，架构文档 §4.5 BL2 / BL3 / BL4）。

四条硬规则（每条都有对应错误码与验收条目）：

1. **无订阅不返回 404**：BL2 合成「免费版 + `status=active` + `seats_total=3`」视图
   （免费版一键开通的默认态）；
2. **升级立即生效并刷新功能开关**：`tenant.plan_code` 是功能开关的权威落点，
   升级/开通在同一次调用内置位（否则前端读到的仍是旧套餐）；
3. **降级 `effective_at` = 当前周期末**：`status` 保持不变、`pending_change` 有值，
   响应 `code=0` + `notice_code=15005`（**不用非 0 码**，否则前端 `unwrap()` 会抛错）；
4. **席位不得低于已用**：`seats < seats_used` → `15003`；
   加席需要支付时返回 `order_id`，减席立即生效（不产生订单）。

**额度口径单一**：`quota_units_total = quota_units_per_seat_month × seats_total`，
与 `usage_repo` 的 `QuotaRecord.total_units` 同一算式，保证 BL2 / BL5 / M4 三处一致。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Final

from app.business.audit_service import (
    ACTION_SUBSCRIPTION_UPDATE,
    RESOURCE_SUBSCRIPTION,
    AuditService,
)
from app.business.plan_service import PlanService, plan_rank
from app.business.usage_service import UsageService, get_usage_service
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.db.repositories.billing_repo import (
    FREE_PLAN_SEATS,
    ORDER_TYPE_NEW,
    ORDER_TYPE_UPGRADE,
    PLAN_FREE,
    SUBSCRIPTION_ACTIVE,
    BillingStore,
    OrderRecord,
    PlanRecord,
    SubscriptionRecord,
    get_billing_store,
    now_iso,
    parse_iso,
    to_iso,
)
from app.schemas.billing import (
    ChangeSubscriptionData,
    ChangeSubscriptionRequest,
    PendingChange,
    SubscriptionData,
    UpdateSeatsData,
)

logger = get_logger(__name__)

#: 订阅 ID 前缀
SUBSCRIPTION_ID_PREFIX: Final[str] = "sub_"

#: 订单 ID 前缀
ORDER_ID_PREFIX: Final[str] = "ord_"

#: 免费版席位固定文案（`15001` 同码多文案之一）
MESSAGE_FREE_SEATS_FIXED: Final[str] = "免费版席位固定为 3 个，升级后即可调整席位"

#: 已是更高版本时的文案（`15001` 同码多文案之一）
MESSAGE_ALREADY_HIGHER: Final[str] = "当前套餐已不低于所选套餐，无需升级"

#: 已是同等或更低版本时的文案（`15001` 同码多文案之一）
MESSAGE_ALREADY_LOWER: Final[str] = "当前套餐已不高于所选套餐，无需降级"

#: 降级生效的提示文案（`15005`，非阻断）
MESSAGE_DOWNGRADE_PENDING: Final[str] = "降级将在当前周期结束后生效"


def add_one_month(moment: datetime) -> datetime:
    """在给定时刻上加一个自然月（月末溢出回落到当月最后一天）。

    Args:
        moment: 起始时刻（UTC）。

    Returns:
        datetime: 加一个月后的时刻。
    """
    year = moment.year + (moment.month // 12)
    month = (moment.month % 12) + 1
    day = moment.day
    while day > 28:
        try:
            candidate = moment.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1
            continue
        return candidate
    return moment.replace(year=year, month=month, day=day)


class SubscriptionService:
    """订阅与席位服务（BL2 / BL3 / BL4 的唯一业务入口）。

    Attributes:
        _settings: 配置实例。
        _store: 计费存储。
        _plans: 套餐服务。
        _usage: 计量服务（额度已用同源）。
        _audit: 操作记录服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: BillingStore | None = None,
        plans: PlanService | None = None,
        usage: UsageService | None = None,
        audit: AuditService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选计费存储（测试注入）。
            plans: 可选套餐服务（测试注入）。
            usage: 可选计量服务（测试注入）。
            audit: 可选操作记录服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: BillingStore = store or get_billing_store(self._settings)
        # 套餐服务必须与**同一个**存储绑定：否则注入自定义存储的测试（如下架套餐）
        # 会读到全局单例里的套餐，可购性判定形同虚设。
        self._plans: PlanService = plans or PlanService(self._settings, self._store)
        self._usage: UsageService = usage or get_usage_service(self._settings)
        self._audit = audit or AuditService(self._settings)
        self._orders: Any = None

    @property
    def store(self) -> BillingStore:
        """当前计费存储（测试可据此断言）。"""
        return self._store

    @property
    def plans(self) -> PlanService:
        """套餐服务。"""
        return self._plans

    def _order_service(self) -> Any:
        """取与当前存储绑定的订单服务（延迟构造，避免模块级循环导入）。

        Returns:
            Any: 订单服务实例。
        """
        if self._orders is None:
            from app.business.order_service import OrderService

            self._orders = OrderService(
                settings=self._settings,
                store=self._store,
                plans=self._plans,
                audit=self._audit,
                subscriptions=self,
            )
        return self._orders

    # ------------------------------------------------------------------
    # BL2 订阅详情
    # ------------------------------------------------------------------
    async def current(self, *, tenant_id: str) -> SubscriptionData:
        """读取订阅详情（无订阅时返回免费版合成视图）。

        Args:
            tenant_id: 租户 ID。

        Returns:
            SubscriptionData: 订阅详情。
        """
        record = await self._store.get_active_subscription(tenant_id)
        seats_used = await self.seats_used(tenant_id)
        used_units = await self._usage.tenant_total(tenant_id=tenant_id)
        if record is None:
            return await self._synthetic_free(
                tenant_id=tenant_id, seats_used=seats_used, used_units=used_units
            )
        plan = await self._plans.get(record.plan_code)
        total_units = (
            PlanService.quota_units_total(plan, record.seats_total) if plan is not None else 0
        )
        pending: PendingChange | None = None
        if record.has_pending_change:
            pending = PendingChange(
                plan_code=record.pending_plan_code,
                effective_at=record.pending_change_at or record.current_period_end,
            )
        return SubscriptionData(
            subscription_id=record.subscription_id,
            plan_code=record.plan_code,
            plan_name=plan.display_name if plan is not None else record.plan_code,
            status=record.status,
            seats_total=record.seats_total,
            seats_used=seats_used,
            started_at=record.started_at,
            current_period_end=record.current_period_end,
            quota_units_total=total_units,
            quota_units_used=used_units,
            auto_renew=record.auto_renew,
            pending_change=pending,
        )

    async def _synthetic_free(
        self, *, tenant_id: str, seats_used: int, used_units: int
    ) -> SubscriptionData:
        """构造免费版合成视图（BL2 无订阅时的默认态）。

        Args:
            tenant_id: 租户 ID。
            seats_used: 已用席位。
            used_units: 已用额度。

        Returns:
            SubscriptionData: 合成订阅视图。
        """
        plan = await self._plans.free_plan()
        seats_total = plan.min_seats if plan is not None and plan.min_seats > 0 else FREE_PLAN_SEATS
        total_units = PlanService.quota_units_total(plan, seats_total) if plan is not None else 0
        moment = datetime.now(UTC)
        logger.debug("subscription_synthetic_free", tenant_id=tenant_id, seats_total=seats_total)
        return SubscriptionData(
            subscription_id="",
            plan_code=PLAN_FREE,
            plan_name=plan.display_name if plan is not None else "免费版",
            status=SUBSCRIPTION_ACTIVE,
            seats_total=seats_total,
            seats_used=seats_used,
            started_at="",
            current_period_end=to_iso(add_one_month(moment)),
            quota_units_total=total_units,
            quota_units_used=used_units,
            auto_renew=True,
            pending_change=None,
        )

    async def seats_used(self, tenant_id: str) -> int:
        """统计已用席位（组织在册成员数）。

        口径：**组织在册成员数**（业务库是权威源，D-13）。组织域不可用时返回 0，
        绝不「猜」一个数字——席位算多了会误拒邀请，算少了会超卖。

        Args:
            tenant_id: 租户 ID。

        Returns:
            int: 已用席位。
        """
        try:
            from app.db.repositories.org import get_org_store

            members = await get_org_store(self._settings).user_ids(tenant_id)
        except Exception:  # pragma: no cover - 组织域不可用时按 0 处理
            logger.debug("seats_used_unavailable", tenant_id=tenant_id)
            return 0
        return len(set(members))

    # ------------------------------------------------------------------
    # BL3 套餐变更
    # ------------------------------------------------------------------
    async def change(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        request: ChangeSubscriptionRequest,
    ) -> ChangeSubscriptionData:
        """变更套餐（BL3）。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID（写审计）。
            request: 变更请求。

        Returns:
            ChangeSubscriptionData: 变更结果。

        Raises:
            ApiError: 套餐不可购（15002）、能力未开通（15001）、
                已有待支付订单（15004）、权限不足（20003，由端点层判定）。
        """
        action = request.action
        if action == "cancel_renew":
            return await self._cancel_renew(tenant_id=tenant_id, actor_id=actor_id)
        if action == "downgrade":
            return await self._downgrade(tenant_id=tenant_id, actor_id=actor_id, request=request)
        # subscribe / upgrade 共用「立即生效」路径
        return await self._apply_immediately(
            tenant_id=tenant_id, actor_id=actor_id, request=request
        )

    async def _cancel_renew(self, *, tenant_id: str, actor_id: str) -> ChangeSubscriptionData:
        """关闭自动续费。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID。

        Returns:
            ChangeSubscriptionData: 变更结果。
        """
        record = await self._store.get_active_subscription(tenant_id)
        if record is None:
            record = await self._create_subscription(
                tenant_id=tenant_id, plan_code=PLAN_FREE, seats=FREE_PLAN_SEATS
            )
        updated = await self._store.save_subscription(replace(record, auto_renew=False))
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_UPDATE,
            resource_type=RESOURCE_SUBSCRIPTION,
            resource_id=updated.subscription_id,
            change_summary={
                "before": {"auto_renew": record.auto_renew},
                "after": {"auto_renew": False},
            },
        )
        return ChangeSubscriptionData(
            subscription_id=updated.subscription_id,
            status=updated.status,
            effective_at=now_iso(),
            order_id="",
        )

    async def _downgrade(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        request: ChangeSubscriptionRequest,
    ) -> ChangeSubscriptionData:
        """降级（周期末生效）。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID。
            request: 变更请求。

        Returns:
            ChangeSubscriptionData: 变更结果（`notice_code=15005`）。

        Raises:
            ApiError: 套餐不可购（15002）、无需降级（15001）。
        """
        plan = await self._plans.require_purchasable(request.plan_code)
        record = await self._store.get_active_subscription(tenant_id)
        if record is None:
            record = await self._create_subscription(
                tenant_id=tenant_id, plan_code=PLAN_FREE, seats=FREE_PLAN_SEATS
            )
        if plan_rank(plan.plan_code) >= plan_rank(record.plan_code):
            raise ApiError(
                ErrorCode.PLAN_FEATURE_UNAVAILABLE,
                message=MESSAGE_ALREADY_LOWER,
                internal_detail=(
                    f"downgrade target={plan.plan_code} current={record.plan_code} "
                    f"seats={request.seats}"
                ),
            )
        effective_at = record.current_period_end or now_iso()
        updated = await self._store.save_subscription(
            replace(
                record,
                pending_plan_code=plan.plan_code,
                pending_change_at=effective_at,
            )
        )
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_UPDATE,
            resource_type=RESOURCE_SUBSCRIPTION,
            resource_id=updated.subscription_id,
            change_summary={
                "before": {"plan_code": record.plan_code, "seats_total": record.seats_total},
                "after": {
                    "pending_plan_code": plan.plan_code,
                    "pending_change_at": effective_at,
                },
            },
        )
        logger.info(
            "subscription_downgrade_scheduled",
            tenant_id=tenant_id,
            plan_code=plan.plan_code,
            effective_at=effective_at,
        )
        return ChangeSubscriptionData(
            subscription_id=updated.subscription_id,
            status=updated.status,
            effective_at=effective_at,
            order_id="",
            notice_code=int(ErrorCode.PLAN_CHANGE_PENDING),
            notice_message=MESSAGE_DOWNGRADE_PENDING,
        )

    async def _apply_immediately(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        request: ChangeSubscriptionRequest,
    ) -> ChangeSubscriptionData:
        """开通 / 升级（立即生效，需要支付时生成订单）。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID。
            request: 变更请求。

        Returns:
            ChangeSubscriptionData: 变更结果。

        Raises:
            ApiError: 套餐不可购（15002）、无需升级（15001）、
                席位不足或已有待支付订单（15004）。
        """
        plan_code = request.plan_code or PLAN_FREE
        plan = await self._plans.require_purchasable(plan_code)
        record = await self._store.get_active_subscription(tenant_id)
        seats = await self._resolve_seats(
            tenant_id=tenant_id, plan=plan, record=record, requested=request.seats
        )
        if request.action == "upgrade" and record is not None:
            if plan_rank(plan.plan_code) < plan_rank(record.plan_code):
                raise ApiError(
                    ErrorCode.PLAN_FEATURE_UNAVAILABLE,
                    message=MESSAGE_ALREADY_HIGHER,
                    internal_detail=(f"upgrade target={plan.plan_code} current={record.plan_code}"),
                )
        order_id = ""
        if plan.amount_cents(seats) > 0:
            order_id = await self._create_upgrade_order(
                tenant_id=tenant_id,
                actor_id=actor_id,
                plan=plan,
                seats=seats,
                order_type=ORDER_TYPE_UPGRADE if record is not None else ORDER_TYPE_NEW,
            )
        updated = await self._apply_plan(
            tenant_id=tenant_id, plan_code=plan.plan_code, seats=seats, record=record
        )
        over_quota = await self._over_quota(tenant_id=tenant_id, plan=plan, seats=seats)
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_UPDATE,
            resource_type=RESOURCE_SUBSCRIPTION,
            resource_id=updated.subscription_id,
            change_summary={
                "before": {
                    "plan_code": record.plan_code if record else PLAN_FREE,
                    "seats_total": record.seats_total if record else FREE_PLAN_SEATS,
                    "status": record.status if record else SUBSCRIPTION_ACTIVE,
                },
                "after": {
                    "plan_code": updated.plan_code,
                    "seats_total": updated.seats_total,
                    "status": updated.status,
                    "order_id": order_id,
                },
            },
        )
        logger.info(
            "subscription_applied",
            tenant_id=tenant_id,
            action=request.action,
            plan_code=updated.plan_code,
            seats_total=updated.seats_total,
            order_id=order_id,
        )
        return ChangeSubscriptionData(
            subscription_id=updated.subscription_id,
            status=updated.status,
            effective_at=now_iso(),
            order_id=order_id,
            over_quota_warning=over_quota,
        )

    async def _resolve_seats(
        self,
        *,
        tenant_id: str,
        plan: PlanRecord,
        record: SubscriptionRecord | None,
        requested: int,
    ) -> int:
        """确定本次变更后的席位数（含最小席位与「不得少于已用」校验）。

        Args:
            tenant_id: 租户 ID（席位判据要读实时在册人数）。
            plan: 目标套餐。
            record: 当前订阅（可能为 None）。
            requested: 请求席位数。

        Returns:
            int: 生效席位数。

        Raises:
            ApiError: 低于套餐最小席位（15001）。
        """
        seats = max(int(requested), 1)
        if plan.min_seats > seats:
            # 免费版按最小席位（=3）开通，不因前端传 1 就少卖席位
            if plan.price_per_seat_month_cents == 0:
                seats = plan.min_seats
            else:
                self._plans.require_min_seats(plan, seats)
        # 席位判据**一律用实时在册人数**（QA-P2-W3-01）：此前读 `record.seats_used`
        # 快照，而该快照只在 `_apply_plan` 时刷新、首次开通时为 0，
        # 导致 BL3 能绕过 15003 并落成「已用 > 总数」的自相矛盾订阅。
        await self.ensure_seat_available(tenant_id=tenant_id, target_seats=seats)
        return seats

    async def ensure_seat_available(
        self, *, tenant_id: str, target_seats: int, extra_reserved: int = 0
    ) -> int:
        """校验「已用席位 + 本次将占用」是否超过目标席位（BL3/BL4/U4 的唯一判据）。

        判据：`used + extra_reserved > target_seats` → `15003`。

        | 调用方 | `target_seats` | `extra_reserved` | 语义 |
        | --- | --- | --- | --- |
        | BL3 开通/升级、BL4 调席位 | 目标席位数 | `0` | 不新增占用，只校验「不能少于已用」 |
        | U4 邀请成员 | 当前 `seats_total` | `1`（+ 待接受邀请数） | 邀请会再占 1 席 |

        用 `>`（而非 `>=`）：`已用 == 目标` 是**合法**的「正好用满」状态
        （例如把席位从 10 减到 8、而正好 8 人在册），不得拒绝。

        `extra_reserved` 是「本次操作将占用 + 待接受邀请」的预留位。当前邀请
        无持久化（`InvitationData` 未落库、无 invitation 表），因此待接受邀请数
        恒为 0——邀请表落地后只需把该值加上去，**调用方签名不变**。

        Args:
            tenant_id: 租户 ID。
            target_seats: 目标席位数（BL3/BL4 为要设成的值，U4 为当前 seats_total）。
            extra_reserved: 额外预留席位数（默认 0）。

        Returns:
            int: 实时已用席位（供调用方复用，避免二次查询）。

        Raises:
            ApiError: 席位不足（15003）。
        """
        used = await self.seats_used(tenant_id)
        reserved = max(int(extra_reserved), 0)
        if used + reserved > max(int(target_seats), 0):
            raise ApiError(
                ErrorCode.SEATS_EXHAUSTED,
                internal_detail=(
                    f"target_seats={target_seats} seats_used={used} reserved={reserved}"
                ),
            )
        return used

    # ------------------------------------------------------------------
    # BL4 调整席位
    # ------------------------------------------------------------------
    async def update_seats(self, *, tenant_id: str, actor_id: str, seats: int) -> UpdateSeatsData:
        """调整席位（BL4）。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID。
            seats: 目标席位数。

        Returns:
            UpdateSeatsData: 调整结果。

        Raises:
            ApiError: 免费版不可调（15001）、低于已用席位（15003）、
                已有待支付订单（15004）。
        """
        record = await self._store.get_active_subscription(tenant_id)
        if record is None or record.plan_code == PLAN_FREE:
            raise ApiError(
                ErrorCode.PLAN_FEATURE_UNAVAILABLE,
                message=MESSAGE_FREE_SEATS_FIXED,
                internal_detail=f"seats update on plan={record.plan_code if record else PLAN_FREE}",
            )
        seats_used = await self.ensure_seat_available(tenant_id=tenant_id, target_seats=seats)
        plan = await self._plans.get(record.plan_code)
        order_id = ""
        delta = seats - record.seats_total
        if delta > 0 and plan is not None and plan.price_per_seat_month_cents > 0:
            order_id = await self._create_upgrade_order(
                tenant_id=tenant_id,
                actor_id=actor_id,
                plan=plan,
                seats=delta,
                order_type="seats",
            )
        updated = await self._store.save_subscription(replace(record, seats_total=seats))
        over_quota = (
            await self._over_quota(tenant_id=tenant_id, plan=plan, seats=seats)
            if plan is not None
            else False
        )
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_UPDATE,
            resource_type=RESOURCE_SUBSCRIPTION,
            resource_id=updated.subscription_id,
            change_summary={
                "before": {"seats_total": record.seats_total},
                "after": {"seats_total": seats, "order_id": order_id},
            },
        )
        logger.info(
            "subscription_seats_updated",
            tenant_id=tenant_id,
            seats_total=seats,
            order_id=order_id,
        )
        return UpdateSeatsData(
            seats_total=seats,
            seats_used=seats_used,
            effective_at=now_iso(),
            order_id=order_id,
            over_quota_warning=over_quota,
        )

    # ------------------------------------------------------------------
    # 内部：订阅落库与订单
    # ------------------------------------------------------------------
    async def _create_subscription(
        self, *, tenant_id: str, plan_code: str, seats: int
    ) -> SubscriptionRecord:
        """新建订阅行（开通路径）。

        Args:
            tenant_id: 租户 ID。
            plan_code: 套餐标识。
            seats: 席位数。

        Returns:
            SubscriptionRecord: 新建的订阅。
        """
        moment = datetime.now(UTC)
        await self._store.ensure_tenant(tenant_id)
        record = SubscriptionRecord(
            tenant_id=tenant_id,
            subscription_id=new_resource_id(SUBSCRIPTION_ID_PREFIX),
            plan_code=plan_code,
            status=SUBSCRIPTION_ACTIVE,
            seats_total=max(seats, 1),
            seats_used=await self.seats_used(tenant_id),
            started_at=to_iso(moment),
            current_period_start=to_iso(moment),
            current_period_end=to_iso(add_one_month(moment)),
            auto_renew=True,
        )
        return await self._store.save_subscription(record)

    async def _apply_plan(
        self,
        *,
        tenant_id: str,
        plan_code: str,
        seats: int,
        record: SubscriptionRecord | None,
    ) -> SubscriptionRecord:
        """把套餐变更落库并刷新企业级功能开关。

        Args:
            tenant_id: 租户 ID。
            plan_code: 目标套餐。
            seats: 席位数。
            record: 当前订阅（可能为 None）。

        Returns:
            SubscriptionRecord: 变更后的订阅。
        """
        if record is None:
            created = await self._create_subscription(
                tenant_id=tenant_id, plan_code=plan_code, seats=seats
            )
        else:
            moment = datetime.now(UTC)
            same_period = bool(record.current_period_end)
            created = await self._store.save_subscription(
                replace(
                    record,
                    plan_code=plan_code,
                    status=SUBSCRIPTION_ACTIVE,
                    seats_total=max(seats, 1),
                    seats_used=await self.seats_used(tenant_id),
                    pending_plan_code="",
                    pending_change_at="",
                    current_period_start=(
                        record.current_period_start if same_period else to_iso(moment)
                    ),
                    current_period_end=(
                        record.current_period_end if same_period else to_iso(add_one_month(moment))
                    ),
                )
            )
        # 功能开关立即变化（前端读 BL2 / 企业设置时立刻看到新套餐能力）
        await self._store.ensure_tenant(tenant_id)
        await self._store.update_tenant(tenant_id, plan_code=plan_code)
        return created

    async def _create_upgrade_order(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        plan: PlanRecord,
        seats: int,
        order_type: str,
    ) -> str:
        """为套餐变更创建订单（复用 OrderService 的下单口径）。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID。
            plan: 目标套餐。
            seats: 计费席位数。
            order_type: 订单类型。

        Returns:
            str: 订单 ID。

        Raises:
            ApiError: 已有待支付订单（15004）。
        """
        order = await self._order_service().create_internal(
            tenant_id=tenant_id,
            actor_id=actor_id,
            plan_code=plan.plan_code,
            seats=seats,
            order_type=order_type,
            payment_method="online",
        )
        return str(order.order_id)

    async def _over_quota(self, *, tenant_id: str, plan: PlanRecord, seats: int) -> bool:
        """判断变更后是否已超额（软提示，不阻断）。

        Args:
            tenant_id: 租户 ID。
            plan: 目标套餐。
            seats: 席位数。

        Returns:
            bool: 已用额度达到或超过总额度时返回 True。
        """
        total = PlanService.quota_units_total(plan, seats)
        if total <= 0:
            return False
        used = await self._usage.tenant_total(tenant_id=tenant_id)
        return used >= total

    # ------------------------------------------------------------------
    # 供订单域回调（支付成功 / 运营核销）
    # ------------------------------------------------------------------
    async def apply_paid_order(
        self, *, order: OrderRecord, actor_id: str, remark: str = ""
    ) -> SubscriptionRecord:
        """把已支付订单的套餐效果落到订阅上（BL10 / OPS5 共用）。

        幂等：订单已 `paid` 时调用方不应再进入本方法；本方法只做「套餐置位」。

        Args:
            order: 已支付订单。
            actor_id: 触发人（渠道回调用 `system`，运营核销用运营人 ID）。
            remark: 备注（运营核销场景写入审计）。

        Returns:
            SubscriptionRecord: 变更后的订阅。
        """
        record = await self._store.get_active_subscription(order.tenant_id)
        updated = await self._apply_plan(
            tenant_id=order.tenant_id,
            plan_code=order.plan_code,
            seats=max(order.seats, record.seats_total if record is not None else order.seats),
            record=record,
        )
        logger.info(
            "subscription_activated_by_order",
            tenant_id=order.tenant_id,
            order_id=order.order_id,
            plan_code=order.plan_code,
            seats_total=updated.seats_total,
            actor_id=actor_id,
            remark=remark,
        )
        return updated


# ===========================================================================
# 进程级单例
# ===========================================================================
_service: SubscriptionService | None = None


def get_subscription_service(settings: Settings | None = None) -> SubscriptionService:
    """获取订阅服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        SubscriptionService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = SubscriptionService(settings)
    return _service


def reset_subscription_service() -> None:
    """重置订阅服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "MESSAGE_ALREADY_HIGHER",
    "MESSAGE_ALREADY_LOWER",
    "MESSAGE_DOWNGRADE_PENDING",
    "MESSAGE_FREE_SEATS_FIXED",
    "ORDER_ID_PREFIX",
    "SUBSCRIPTION_ID_PREFIX",
    "SubscriptionService",
    "add_one_month",
    "get_subscription_service",
    "parse_iso",
    "reset_subscription_service",
]
