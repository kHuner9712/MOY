"""套餐目录服务（阶段二 B09，架构文档 §4.5 BL1 / §5 计费 DDL）。

**唯一数据源**：套餐来自 `billing_plan`（平台共享行，`tenant_id='__platform__'`），
代码**不新增套餐表、不硬编码价格**——运营调整价格后重启即生效。

对外只暴露三件事：

1. `catalog()`：BL1 的套餐清单（含功能开关，前端不硬编码额度）；
2. `require_purchasable(plan_code)`：可购性校验（不存在/下架 → `15002`）；
3. `quota_units_total(plan_code, seats)`：额度总量口径（席位 × 每席位额度），
   与 `usage_repo.QuotaRecord.total_units` **同一算式**，保证 BL2 与 BL5 数字一致。
"""

from __future__ import annotations

from typing import Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.billing_repo import (
    PLAN_FREE,
    BillingStore,
    PlanRecord,
    get_billing_store,
)
from app.schemas.billing import (
    FEATURE_DISPLAY_NAMES,
    PlanFeatureItem,
    PlanItem,
    PlanListData,
    PlanStatusItem,
)

logger = get_logger(__name__)

#: 套餐能力未开通的文案（`15001`，同码多文案之一）
MESSAGE_PLAN_CAPABILITY: Final[str] = "当前套餐不支持该功能，升级后即可使用"

#: 席位数低于套餐最小席位的文案（`15001`，不得透出「min_seats」等技术词）
MESSAGE_MIN_SEATS: Final[str] = "该套餐至少需要 {seats} 个席位，请调整后重试"

#: 套餐排序（用于升级/降级方向判定；未知套餐排在最后）
PLAN_RANK: Final[dict[str, int]] = {PLAN_FREE: 0, "professional": 1, "enterprise": 2}


def feature_items(plan: PlanRecord) -> list[PlanFeatureItem]:
    """把套餐的 JSONB 功能开关转换为有序列表。

    Args:
        plan: 套餐记录。

    Returns:
        list[PlanFeatureItem]: 功能开关清单（未登记的键排在末尾）。
    """
    known_order = {key: index for index, key in enumerate(FEATURE_DISPLAY_NAMES)}
    keys = sorted(plan.features, key=lambda key: (known_order.get(key, 999), key))
    items: list[PlanFeatureItem] = []
    for key in keys:
        raw = plan.features[key]
        if isinstance(raw, bool):
            items.append(
                PlanFeatureItem(
                    feature_key=key,
                    display_name=FEATURE_DISPLAY_NAMES.get(key, key),
                    included=raw,
                    limit=None,
                )
            )
            continue
        limit = int(raw) if isinstance(raw, int) else None
        items.append(
            PlanFeatureItem(
                feature_key=key,
                display_name=FEATURE_DISPLAY_NAMES.get(key, key),
                included=True,
                limit=limit,
            )
        )
    return items


def to_plan_item(plan: PlanRecord) -> PlanItem:
    """把套餐记录转换为 BL1 条目。

    Args:
        plan: 套餐记录。

    Returns:
        PlanItem: BL1 套餐条目。
    """
    return PlanItem(
        plan_code=plan.plan_code,
        display_name=plan.display_name,
        price_per_seat_month_cents=plan.price_per_seat_month_cents,
        currency=plan.currency,
        min_seats=plan.min_seats,
        quota_units_per_seat_month=plan.quota_units_per_seat_month,
        quota_unit_label=plan.quota_unit_label,
        purchasable_quota=plan.purchasable_quota,
        features=feature_items(plan),
        status=plan.status,
        trial_days=plan.trial_days,
    )


def plan_rank(plan_code: str) -> int:
    """取套餐的升级档位序号（免费版 0）。

    Args:
        plan_code: 套餐标识。

    Returns:
        int: 档位序号；未知套餐返回 -1（按「不可比较」处理）。
    """
    return PLAN_RANK.get(plan_code, -1)


class PlanService:
    """套餐目录服务（BL1 的唯一业务入口）。

    Attributes:
        _settings: 配置实例。
        _store: 计费存储。
    """

    def __init__(self, settings: Settings | None = None, store: BillingStore | None = None) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选计费存储（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: BillingStore = store or get_billing_store(self._settings)

    @property
    def store(self) -> BillingStore:
        """当前计费存储（测试可据此断言）。"""
        return self._store

    async def catalog(self) -> PlanListData:
        """BL1 套餐清单。

        Returns:
            PlanListData: 三个套餐（按 `sort_order` 升序）。
        """
        plans = await self._store.list_plans()
        return PlanListData(plans=[to_plan_item(plan) for plan in plans])

    async def get(self, plan_code: str) -> PlanRecord | None:
        """按标识取套餐（不做可购性判定）。

        Args:
            plan_code: 套餐标识。

        Returns:
            PlanRecord | None: 套餐；不存在返回 None。
        """
        return await self._store.get_plan(plan_code)

    async def free_plan(self) -> PlanRecord | None:
        """取免费版套餐（BL2 合成视图与订阅默认席位使用）。

        Returns:
            PlanRecord | None: 免费版套餐；数据缺失时返回 None。
        """
        return await self._store.get_plan(PLAN_FREE)

    async def require_purchasable(self, plan_code: str) -> PlanRecord:
        """校验套餐可购。

        Args:
            plan_code: 套餐标识。

        Returns:
            PlanRecord: 套餐记录。

        Raises:
            ApiError: 套餐不存在或已下架（15002）。
        """
        plan = await self._store.get_plan(plan_code)
        if plan is None or not plan.is_available:
            logger.warning("plan_not_purchasable", plan_code=plan_code)
            raise ApiError(
                ErrorCode.PLAN_NOT_PURCHASABLE,
                internal_detail=f"plan_code={plan_code} missing_or_offline",
            )
        return plan

    def require_min_seats(self, plan: PlanRecord, seats: int) -> None:
        """校验席位数不低于套餐最小席位。

        Args:
            plan: 套餐记录。
            seats: 席位数。

        Raises:
            ApiError: 席位低于下限（15001，同码多文案）。
        """
        if seats >= plan.min_seats:
            return
        raise ApiError(
            ErrorCode.PLAN_FEATURE_UNAVAILABLE,
            message=MESSAGE_MIN_SEATS.format(seats=plan.min_seats),
            internal_detail=f"plan_code={plan.plan_code} seats={seats} min={plan.min_seats}",
        )

    @staticmethod
    def amount_cents(plan: PlanRecord, seats: int) -> int:
        """按套餐单价计算应付金额（分）。

        Args:
            plan: 套餐记录。
            seats: 席位数。

        Returns:
            int: 金额（分）。
        """
        return plan.amount_cents(seats)

    @staticmethod
    def quota_units_total(plan: PlanRecord, seats: int) -> int:
        """额度总量口径（席位 × 每席位额度）。

        **与 BL5/M4 同一算式**：`usage_repo.PostgresUsageStore.quota()` 用
        `quota_units_per_seat_month * seats_total`，此处不得改成别的口径，
        否则套餐页与用量页的「总额度」会不一致。

        Args:
            plan: 套餐记录。
            seats: 席位数。

        Returns:
            int: 额度总量。
        """
        return max(plan.quota_units_per_seat_month, 0) * max(seats, 0)

    async def status_items(self) -> list[PlanStatusItem]:
        """套餐上下架状态（OPS7 `settings.plan_status`）。

        Returns:
            list[PlanStatusItem]: 套餐状态清单。
        """
        plans = await self._store.list_plans()
        return [
            PlanStatusItem(
                plan_code=plan.plan_code, display_name=plan.display_name, status=plan.status
            )
            for plan in plans
        ]


# ===========================================================================
# 进程级单例
# ===========================================================================
_service: PlanService | None = None


def get_plan_service(settings: Settings | None = None) -> PlanService:
    """获取套餐服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        PlanService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = PlanService(settings)
    return _service


def reset_plan_service() -> None:
    """重置套餐服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "MESSAGE_MIN_SEATS",
    "MESSAGE_PLAN_CAPABILITY",
    "PLAN_RANK",
    "PlanService",
    "feature_items",
    "get_plan_service",
    "plan_rank",
    "reset_plan_service",
    "to_plan_item",
]
