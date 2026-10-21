"""订阅与席位服务单元测试（阶段二 B09，架构文档 §4.5 BL2/BL3/BL4）。

对应验收条目（架构文档 9.2 B09「完成后可验证」）：

1. 免费版开通 `status=active`、`seats_total=3`；
2. 升级生成 `order_id` 且功能开关立即变化（`tenant.plan_code` 同步）；
3. 降级 `effective_at` = 当前周期末，且下发 `notice_code=15005`（**非 0 code 不出现**）；
4. 席位用满 / 减席越界 → `15003`；
5. 待支付订单存在时再下单 → `15004`（订单数不增加）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from app.business.subscription_service import SubscriptionService, add_one_month
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.billing_repo import (
    FREE_PLAN_SEATS,
    PLAN_AVAILABLE,
    PLAN_ENTERPRISE,
    PLAN_FREE,
    PLAN_OFFLINE,
    PLAN_PROFESSIONAL,
    InMemoryBillingStore,
    PlanRecord,
    default_plans,
)
from app.db.repositories.org_repo import DepartmentRecord, MemberLink, get_org_store
from app.schemas.billing import ChangeSubscriptionRequest

pytestmark = pytest.mark.unit

TENANT_ID = "tnt_b09_sub"


@pytest.fixture(autouse=True)
def _isolate_org_store() -> Iterator[None]:
    """隔离组织存储（席位占用依赖组织在册成员数，跨用例串数据会让断言漂移）。

    Yields:
        Iterator[None]: 无值。
    """
    from app.db.repositories.org_repo import reset_org_store

    reset_org_store()
    yield
    reset_org_store()


def _settings() -> Settings:
    """构造测试配置（桩模式，不产生任何真实下游请求）。

    Returns:
        Settings: 测试配置。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
    )


class OfflinePlanStore(InMemoryBillingStore):
    """把指定套餐标记为已下架的内存存储（构造 15002 场景，避免改动私有状态）。"""

    def __init__(self, offline_plan: str) -> None:
        """初始化。

        Args:
            offline_plan: 需要下架的套餐标识。
        """
        super().__init__()
        self._offline_plan = offline_plan

    async def get_plan(self, plan_code: str) -> PlanRecord | None:
        """读取套餐（命中目标时返回下架版本）。

        Args:
            plan_code: 套餐标识。

        Returns:
            PlanRecord | None: 套餐记录。
        """
        plan = await super().get_plan(plan_code)
        if plan is None or plan_code != self._offline_plan:
            return plan
        return replace(plan, status=PLAN_OFFLINE)


def _service(settings: Settings, store: InMemoryBillingStore | None = None) -> SubscriptionService:
    """构造订阅服务（显式注入存储，避免跨用例串状态）。

    Args:
        settings: 配置实例。
        store: 可选存储。

    Returns:
        SubscriptionService: 订阅服务。
    """
    return SubscriptionService(settings=settings, store=store or InMemoryBillingStore())


async def _link_members(settings: Settings, count: int) -> None:
    """往组织里塞 `count` 个在册成员（用于席位占用断言）。

    席位占用口径是「组织在册成员数」，因此必须先建部门再建成员关联——
    `department_member` 的孤儿行不计入在册成员（与生产 SQL 口径一致）。

    Args:
        settings: 配置实例。
        count: 成员数。
    """
    store = get_org_store(settings)
    await store.create_department(
        DepartmentRecord(
            tenant_id=TENANT_ID,
            department_id="dept_test",
            org_id="org_test",
            parent_id="",
            name="研发部",
            path="/dept_test/",
            level=1,
        )
    )
    for index in range(count):
        await store.link_member(
            MemberLink(
                tenant_id=TENANT_ID,
                department_id="dept_test",
                user_id=f"usr_seat_{index}",
                is_primary=index == 0,
            )
        )


@pytest.mark.asyncio
@pytest.mark.p0
async def test_free_subscription_opens_with_three_seats() -> None:
    """免费版开通：`status=active`、`seats_total=3`（验收 ①）。"""
    settings = _settings()
    service = _service(settings)
    data = await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_FREE, seats=1, action="subscribe"),
    )
    assert data.status == "active"
    assert data.order_id == "", "免费版不应产生订单"
    record = await service.store.get_active_subscription(TENANT_ID)
    assert record is not None
    assert record.seats_total == FREE_PLAN_SEATS
    assert record.plan_code == PLAN_FREE
    current = await service.current(tenant_id=TENANT_ID)
    assert current.status == "active"
    assert current.seats_total == FREE_PLAN_SEATS
    assert current.plan_code == PLAN_FREE


@pytest.mark.asyncio
async def test_synthetic_view_when_no_subscription() -> None:
    """无订阅时 BL2 返回免费版合成视图（**不返回 404**）。"""
    settings = _settings()
    service = _service(settings)
    current = await service.current(tenant_id="tnt_never_subscribed")
    assert current.plan_code == PLAN_FREE
    assert current.status == "active"
    assert current.seats_total == FREE_PLAN_SEATS
    assert current.subscription_id == ""
    assert current.quota_units_total == 200_000 * FREE_PLAN_SEATS


@pytest.mark.asyncio
@pytest.mark.p0
async def test_upgrade_creates_order_and_refreshes_features() -> None:
    """升级：生成 `order_id`，`tenant.plan_code` 立即变化（验收 ②）。"""
    settings = _settings()
    store = InMemoryBillingStore()
    service = _service(settings, store)
    data = await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    assert data.order_id.startswith("ord_")
    assert data.status == "active"
    tenant = await store.get_tenant(TENANT_ID)
    assert tenant is not None
    assert tenant.plan_code == PLAN_PROFESSIONAL, "功能开关必须立即刷新"
    orders, total = await store.list_orders(TENANT_ID)
    assert total == 1
    assert orders[0].amount_cents == 3_900 * 5
    current = await service.current(tenant_id=TENANT_ID)
    assert current.plan_code == PLAN_PROFESSIONAL
    assert current.quota_units_total == 3_000_000 * 5


@pytest.mark.asyncio
@pytest.mark.p0
async def test_upgrade_blocked_by_pending_order() -> None:
    """已有待支付订单时再升级 → `15004`，且订单数不增加（验收 ⑥）。"""
    settings = _settings()
    store = InMemoryBillingStore()
    service = _service(settings, store)
    await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    with pytest.raises(ApiError) as excinfo:
        await service.change(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=ChangeSubscriptionRequest(
                plan_code=PLAN_ENTERPRISE, seats=20, action="upgrade"
            ),
        )
    assert excinfo.value.code is ErrorCode.PENDING_ORDER_EXISTS
    _, total = await store.list_orders(TENANT_ID)
    assert total == 1, "冲突时不得产生第二个订单"


@pytest.mark.asyncio
@pytest.mark.p0
async def test_downgrade_effective_at_period_end() -> None:
    """降级：`effective_at` = 周期末，`notice_code=15005`，`status` 不变（验收 ③）。"""
    settings = _settings()
    store = InMemoryBillingStore()
    service = _service(settings, store)
    await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    before = await store.get_active_subscription(TENANT_ID)
    assert before is not None
    data = await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_FREE, seats=3, action="downgrade"),
    )
    assert data.effective_at == before.current_period_end
    assert data.notice_code == int(ErrorCode.PLAN_CHANGE_PENDING)
    assert data.notice_message == "降级将在当前周期结束后生效"
    assert data.status == before.status, "降级不改变当前状态"
    assert data.order_id == ""
    snapshot = await service.current(tenant_id=TENANT_ID)
    assert snapshot.pending_change is not None
    assert snapshot.pending_change.plan_code == PLAN_FREE
    assert snapshot.pending_change.effective_at == before.current_period_end
    assert snapshot.plan_code == PLAN_PROFESSIONAL, "周期末之前仍是原套餐"


@pytest.mark.asyncio
async def test_downgrade_to_same_or_higher_plan_rejected() -> None:
    """降级到同等/更高档位 → `15001`（同码多文案）。"""
    settings = _settings()
    service = _service(settings)
    with pytest.raises(ApiError) as excinfo:
        await service.change(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=ChangeSubscriptionRequest(plan_code=PLAN_FREE, seats=3, action="downgrade"),
        )
    assert excinfo.value.code is ErrorCode.PLAN_FEATURE_UNAVAILABLE
    assert "无需降级" in excinfo.value.message


@pytest.mark.asyncio
@pytest.mark.p0
async def test_seats_below_used_rejected() -> None:
    """减席越界（低于已用席位）→ `15003`（验收 ④）。"""
    settings = _settings()
    store = InMemoryBillingStore()
    service = _service(settings, store)
    await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    await _link_members(settings, 3)
    assert await service.seats_used(TENANT_ID) == 3
    with pytest.raises(ApiError) as excinfo:
        await service.update_seats(tenant_id=TENANT_ID, actor_id="usr_owner", seats=2)
    assert excinfo.value.code is ErrorCode.SEATS_EXHAUSTED
    assert excinfo.value.message == "成员席位已用满，请先增加席位"


@pytest.mark.asyncio
async def test_seats_update_on_free_plan_rejected() -> None:
    """免费版调整席位 → `15001`（免费版席位固定 3 个）。"""
    settings = _settings()
    service = _service(settings)
    with pytest.raises(ApiError) as excinfo:
        await service.update_seats(tenant_id=TENANT_ID, actor_id="usr_owner", seats=10)
    assert excinfo.value.code is ErrorCode.PLAN_FEATURE_UNAVAILABLE
    assert "免费版席位固定" in excinfo.value.message


@pytest.mark.asyncio
async def test_seats_increase_creates_order() -> None:
    """加席需要支付：返回 `order_id`，席位立即生效（软生效，支付后开票）。"""
    settings = _settings()
    store = InMemoryBillingStore()
    service = _service(settings, store)
    await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    # 先支付掉升级订单，否则加席会被 15004 拦下
    pending = await store.find_pending_order(TENANT_ID)
    assert pending is not None
    await store.update_order(TENANT_ID, pending.order_id, status="paid")
    data = await service.update_seats(tenant_id=TENANT_ID, actor_id="usr_owner", seats=8)
    assert data.order_id.startswith("ord_")
    assert data.seats_total == 8
    assert data.seats_used == 0


@pytest.mark.asyncio
async def test_offline_plan_rejected() -> None:
    """已下架套餐 → `15002`（文案含「已下架」）。"""
    settings = _settings()
    service = _service(settings, OfflinePlanStore(PLAN_PROFESSIONAL))
    with pytest.raises(ApiError) as excinfo:
        await service.change(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=ChangeSubscriptionRequest(
                plan_code=PLAN_PROFESSIONAL, seats=5, action="subscribe"
            ),
        )
    assert excinfo.value.code is ErrorCode.PLAN_NOT_PURCHASABLE
    assert "已下架" in excinfo.value.message


@pytest.mark.asyncio
async def test_upgrade_then_pay_activates_subscription() -> None:
    """支付成功后订阅生效（回调/核销共用 `apply_paid_order`）。"""
    settings = _settings()
    store = InMemoryBillingStore()
    service = _service(settings, store)
    await service.change(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_ENTERPRISE, seats=20, action="upgrade"),
    )
    pending = await store.find_pending_order(TENANT_ID)
    assert pending is not None
    paid = await store.update_order(TENANT_ID, pending.order_id, status="paid")
    assert paid is not None
    record = await service.apply_paid_order(order=paid, actor_id="system")
    assert record.plan_code == PLAN_ENTERPRISE
    assert record.status == "active"
    assert record.seats_total == 20
    current = await service.current(tenant_id=TENANT_ID)
    assert current.quota_units_total == 8_000_000 * 20


def test_add_one_month_handles_month_end() -> None:
    """周期末计算：1 月 31 日 + 1 个月 → 2 月 28/29 日（不抛异常）。"""
    from datetime import UTC, datetime

    january_end = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
    assert add_one_month(january_end).month == 2
    assert add_one_month(january_end).day == 28
    december = datetime(2026, 12, 15, 0, 0, tzinfo=UTC)
    january = add_one_month(december)
    assert (january.year, january.month) == (2027, 1)


def test_default_plans_match_ddl_seed() -> None:
    """内存后端的套餐种子与 DDL 种子逐项一致（价格/席位/额度）。"""
    plans = {plan.plan_code: plan for plan in default_plans()}
    assert set(plans) == {PLAN_FREE, PLAN_PROFESSIONAL, PLAN_ENTERPRISE}
    assert plans[PLAN_FREE].price_per_seat_month_cents == 0
    assert plans[PLAN_FREE].min_seats == 3
    assert plans[PLAN_FREE].quota_units_per_seat_month == 200_000
    assert plans[PLAN_PROFESSIONAL].price_per_seat_month_cents == 3_900
    assert plans[PLAN_ENTERPRISE].price_per_seat_month_cents == 9_900
    assert all(plan.status == PLAN_AVAILABLE for plan in plans.values())
