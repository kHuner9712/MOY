"""订单与支付服务单元测试（阶段二 B09，架构文档 §4.5 BL6-BL10）。

对应验收条目（架构文档 9.2 B09「完成后可验证」）：

5. 同一回调 payload 连发 3 次：订阅只生效一次、**不重复开票**；
6. 待支付订单存在时再下单 → `15004` 且不产生第二单；
7. 30 分钟未支付自动 `closed`（懒过期 + 补偿入口两条路径都验证）。

另外覆盖 `15009`（核销金额不符）、`20001`（验签失败）、`15008`（重复核销）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.business.order_service import OrderService, mask_payment_no
from app.business.payment_gateway import FakeGateway
from app.business.plan_service import PlanService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.billing import (
    INVOICE_ISSUED,
    ORDER_CLOSED,
    ORDER_PAID,
    PLAN_ENTERPRISE,
    PLAN_FREE,
    PLAN_PROFESSIONAL,
    InMemoryBillingStore,
    now_iso,
)
from app.schemas.billing import CreateOrderRequest

pytestmark = pytest.mark.unit

TENANT_ID = "tnt_b09_order"

#: 假网关的签名密钥（与 `FakeGateway` 默认值一致）
FAKE_KEY = "test-gateway-key"


def _settings(**overrides: object) -> Settings:
    """构造测试配置（桩模式 → 自动使用假支付网关）。

    Args:
        **overrides: 需要覆盖的配置项。

    Returns:
        Settings: 测试配置。
    """
    reset_settings_cache()
    payload: dict[str, object] = {
        "env": "test",
        "deploy_mode": "saas",
        "downstream_enabled": False,
        "auth_mode": "stub",
        "jwt_secret": "unit-test-secret",
        "ratelimit_enabled": False,
    }
    payload.update(overrides)
    return Settings(**payload)  # type: ignore[arg-type]


def _service(
    settings: Settings, store: InMemoryBillingStore | None = None
) -> tuple[OrderService, InMemoryBillingStore]:
    """构造订单服务与其存储。

    Args:
        settings: 配置实例。
        store: 可选存储。

    Returns:
        tuple[OrderService, InMemoryBillingStore]: 服务与存储。
    """
    billing_store = store or InMemoryBillingStore()
    service = OrderService(
        settings=settings,
        store=billing_store,
        plans=PlanService(settings, billing_store),
        gateway=FakeGateway(FAKE_KEY),
    )
    return service, billing_store


def _callback_payload(order_id: str, amount_cents: int, txn: str) -> dict[str, object]:
    """构造一条**签名正确**的回调载荷。

    Args:
        order_id: 订单 ID。
        amount_cents: 金额（分）。
        txn: 渠道流水号。

    Returns:
        dict[str, object]: 回调载荷。
    """
    gateway = FakeGateway(FAKE_KEY)
    signature = gateway.sign(out_trade_no=order_id, transaction_id=txn, amount_total=amount_cents)
    return {
        "out_trade_no": order_id,
        "transaction_id": txn,
        "amount_total": amount_cents,
        "sign": signature,
        "trade_state": "SUCCESS",
    }


@pytest.mark.asyncio
async def test_create_order_online_returns_pay_url() -> None:
    """BL6 在线支付：返回 `pending_payment`、应付金额与跳转地址。"""
    settings = _settings()
    service, store = _service(settings)
    data = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_PROFESSIONAL,
            seats=5,
            order_type="new",
            payment_method="online",
        ),
    )
    assert data.status == "pending_payment"
    assert data.amount_cents == 3_900 * 5
    assert data.pay_url.endswith(data.order_id)
    assert data.bank_info is None
    counts = store.counts()
    assert counts["orders"] == 1


@pytest.mark.asyncio
async def test_create_order_offline_returns_bank_info() -> None:
    """BL6 对公转账：返回收款信息，不生成支付地址。"""
    settings = _settings(
        pay_bank_info='{"account_name":"某某科技","bank_name":"招商银行","account_no":"1109****1234"}'
    )
    service, _ = _service(settings)
    data = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_ENTERPRISE,
            seats=20,
            order_type="new",
            payment_method="offline_transfer",
        ),
    )
    assert data.pay_url == ""
    assert data.bank_info is not None
    assert data.bank_info["account_name"] == "某某科技"


@pytest.mark.asyncio
@pytest.mark.p0
async def test_second_order_with_pending_rejected() -> None:
    """已有待支付订单时再下单 → `15004` 且订单数不增加（验收 ⑥）。"""
    settings = _settings()
    service, store = _service(settings)
    first = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    with pytest.raises(ApiError) as excinfo:
        await service.create(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
        )
    assert excinfo.value.code is ErrorCode.PENDING_ORDER_EXISTS
    assert "请先完成支付" in excinfo.value.message
    counts = store.counts()
    assert counts["orders"] == 1, "冲突时不得产生第二单"
    assert await store.find_pending_order(TENANT_ID) is not None
    assert (await store.get_order(TENANT_ID, first.order_id)) is not None


@pytest.mark.asyncio
@pytest.mark.p0
async def test_expired_order_closed_lazily_and_new_order_allowed() -> None:
    """30 分钟未支付 → `closed`（懒过期路径），随后可以重新下单（验收 ⑦）。"""
    settings = _settings()
    service, store = _service(settings)
    first = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    past = (datetime.now(UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    await store.update_order(TENANT_ID, first.order_id, expires_at=past)
    # 任意读操作都会触发懒过期
    listed = await service.list_orders(tenant_id=TENANT_ID)
    assert listed.items[0].status == ORDER_CLOSED
    second = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    assert second.status == "pending_payment"
    assert second.order_id != first.order_id


@pytest.mark.asyncio
async def test_close_expired_orders_sweeps_all_tenants() -> None:
    """补偿入口：跨企业关闭超时订单（定时任务路径）。"""
    settings = _settings()
    service, store = _service(settings)
    first = await service.create(
        tenant_id="tnt_a",
        actor_id="usr_a",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    past = (datetime.now(UTC) - timedelta(minutes=45)).isoformat().replace("+00:00", "Z")
    await store.update_order("tnt_a", first.order_id, expires_at=past)
    assert await service.close_expired_orders() == 1
    record = await store.get_order("tnt_a", first.order_id)
    assert record is not None
    assert record.status == ORDER_CLOSED


@pytest.mark.asyncio
async def test_detail_rejects_unknown_order() -> None:
    """BL8 非本企业/不存在的订单 → `20004`（不区分「不存在」与「无权」，避免探测）。"""
    settings = _settings()
    service, _ = _service(settings)
    with pytest.raises(ApiError) as excinfo:
        await service.detail(tenant_id=TENANT_ID, order_id="ord_not_exists")
    assert excinfo.value.code is ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.p0
async def test_callback_is_idempotent_for_same_payload() -> None:
    """同一 payload 连发 3 次：订阅只生效一次、不重复开票（验收 ⑤）。"""
    settings = _settings()
    service, store = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    payload = _callback_payload(order.order_id, order.amount_cents, "txn_idem_001")
    results = [await service.handle_callback(payload) for _ in range(3)]
    assert all(item.code == "SUCCESS" for item in results)
    counts = store.counts()
    assert counts["invoices"] == 1, "不得重复开票"
    assert counts["callbacks"] == 1, "同流水号只留一条留痕"
    record = await store.get_order(TENANT_ID, order.order_id)
    assert record is not None
    assert record.status == ORDER_PAID
    invoices = await store.list_invoices(TENANT_ID)
    assert len(invoices) == 1
    assert invoices[0].status == INVOICE_ISSUED
    assert invoices[0].amount_cents == order.amount_cents
    subscription = await store.get_active_subscription(TENANT_ID)
    assert subscription is not None
    assert subscription.plan_code == PLAN_PROFESSIONAL
    assert subscription.seats_total == 5


@pytest.mark.asyncio
async def test_callback_rejects_bad_signature() -> None:
    """验签失败 → `20001`（同码多文案），且不留 processed 留痕。"""
    settings = _settings()
    service, store = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    payload = _callback_payload(order.order_id, order.amount_cents, "txn_bad_sign")
    payload["sign"] = "0" * 64
    with pytest.raises(ApiError) as excinfo:
        await service.handle_callback(payload)
    assert excinfo.value.code is ErrorCode.PARAM_INVALID
    assert "校验未通过" in excinfo.value.message
    record = await store.get_order(TENANT_ID, order.order_id)
    assert record is not None
    assert record.status == "pending_payment", "验签失败不得改变订单状态"
    assert store.counts()["invoices"] == 0


@pytest.mark.asyncio
async def test_callback_rejects_amount_mismatch() -> None:
    """回调金额与订单金额不一致 → `15009`。"""
    settings = _settings()
    service, store = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    payload = _callback_payload(order.order_id, order.amount_cents - 100, "txn_amount_bad")
    with pytest.raises(ApiError) as excinfo:
        await service.handle_callback(payload)
    assert excinfo.value.code is ErrorCode.ORDER_AMOUNT_MISMATCH
    record = await store.get_order(TENANT_ID, order.order_id)
    assert record is not None
    assert record.status == "pending_payment"


@pytest.mark.asyncio
async def test_ops_confirm_paid_and_repeat_rejected() -> None:
    """运营核销：金额相符 → `paid` 并订阅生效；重复核销 → `15008`。"""
    settings = _settings()
    service, store = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="offline_transfer"
        ),
    )
    paid = await service.confirm_paid(
        tenant_id=TENANT_ID,
        order_id=order.order_id,
        paid_amount_cents=order.amount_cents,
        actor_id="usr_ops_1",
        remark="对公到账",
    )
    assert paid.status == ORDER_PAID
    assert paid.confirmed_by == "usr_ops_1"
    assert paid.confirm_remark == "对公到账"
    assert store.counts()["invoices"] == 1
    with pytest.raises(ApiError) as excinfo:
        await service.confirm_paid(
            tenant_id=TENANT_ID,
            order_id=order.order_id,
            paid_amount_cents=order.amount_cents,
            actor_id="usr_ops_1",
            remark="重复点击",
        )
    assert excinfo.value.code is ErrorCode.ORDER_ALREADY_PROCESSED


@pytest.mark.asyncio
async def test_ops_confirm_amount_mismatch() -> None:
    """运营核销金额不符 → `15009`。"""
    settings = _settings()
    service, _ = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="offline_transfer"
        ),
    )
    with pytest.raises(ApiError) as excinfo:
        await service.confirm_paid(
            tenant_id=TENANT_ID,
            order_id=order.order_id,
            paid_amount_cents=order.amount_cents - 1,
            actor_id="usr_ops_1",
        )
    assert excinfo.value.code is ErrorCode.ORDER_AMOUNT_MISMATCH
    assert "金额与应付金额不一致" in excinfo.value.message


@pytest.mark.asyncio
async def test_invoices_listed_after_payment() -> None:
    """BL9 账单：支付成功后按账期可查，金额与订单一致。"""
    settings = _settings()
    service, _ = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_ENTERPRISE, seats=20),
    )
    await service.handle_callback(
        _callback_payload(order.order_id, order.amount_cents, "txn_invoice_001")
    )
    period = now_iso()[:7]
    invoices = await service.list_invoices(tenant_id=TENANT_ID, period=period)
    assert len(invoices.items) == 1
    assert invoices.items[0].amount_cents == order.amount_cents
    empty = await service.list_invoices(tenant_id=TENANT_ID, period="1999-01")
    assert empty.items == []


@pytest.mark.asyncio
async def test_free_plan_order_has_zero_amount() -> None:
    """免费版下单金额为 0（不产生真实收款）。"""
    settings = _settings()
    service, _ = _service(settings)
    data = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_FREE, seats=3, payment_method="online"),
    )
    assert data.amount_cents == 0


def test_mask_payment_no_keeps_tail_only() -> None:
    """支付流水号脱敏：只保留尾部 4 位。"""
    assert mask_payment_no("4200001234567890") == "****7890"
    assert mask_payment_no("12") == "****12"
    assert mask_payment_no("") == ""


@pytest.mark.asyncio
@pytest.mark.p0
async def test_offline_plan_cannot_be_purchased() -> None:
    """BL-01 验收 3：套餐下架后不可购买（`15002`），已订阅用户的套餐名仍可展示。

    对应 QA `TC-P2-BL01-03`：下架只影响「新购」，不得让存量订阅的 `plan_name` 变空。
    """
    settings = _settings()
    store = _OfflinePlanStore(PLAN_PROFESSIONAL)
    service = OrderService(
        settings=settings,
        store=store,
        plans=PlanService(settings, store),
        gateway=FakeGateway(FAKE_KEY),
    )
    with pytest.raises(ApiError) as excinfo:
        await service.create(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
        )
    assert excinfo.value.code is ErrorCode.PLAN_NOT_PURCHASABLE
    assert "已下架" in excinfo.value.message
    assert store.counts()["orders"] == 0, "不可购时不得产生订单"


@pytest.mark.asyncio
@pytest.mark.p0
async def test_concurrent_subscribe_keeps_single_active() -> None:
    """BL-02 功能 5：并发开通只产生 1 个有效订阅 + 1 个待支付订单（部分唯一索引兜底）。

    对应 QA `TC-P2-BL02-06`：5 个并发 `subscribe` 里恰好 1 个成功，
    其余被 `15004`（已有待支付订单）拦下；订阅表只有一条。
    """
    import asyncio

    from app.business.subscription_service import SubscriptionService
    from app.schemas.billing import ChangeSubscriptionRequest

    settings = _settings()
    store = InMemoryBillingStore()
    plans = PlanService(settings, store)
    subscriptions = SubscriptionService(settings, store, plans=plans)

    async def subscribe() -> str:
        """发起一次升级（并发场景下的单次调用）。

        Returns:
            str: `ok` 或错误码字符串。
        """
        try:
            await subscriptions.change(
                tenant_id=TENANT_ID,
                actor_id="usr_owner",
                request=ChangeSubscriptionRequest(
                    plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"
                ),
            )
        except ApiError as exc:
            return str(int(exc.code))
        return "ok"

    results = await asyncio.gather(*(subscribe() for _ in range(5)))
    assert results.count("ok") == 1, f"并发只允许 1 次成功，实际 {results}"
    assert results.count(str(int(ErrorCode.PENDING_ORDER_EXISTS))) == 4
    assert store.counts()["subscriptions"] == 1, "有效订阅只能有 1 条"
    assert store.counts()["orders"] == 1, "待支付订单只能有 1 条"


class _OfflinePlanStore(InMemoryBillingStore):
    """把指定套餐标记为已下架的内存存储（构造 `15002` 场景，不动私有状态）。"""

    def __init__(self, offline_plan: str) -> None:
        """初始化。

        Args:
            offline_plan: 需要下架的套餐标识。
        """
        super().__init__()
        self._offline_plan = offline_plan

    async def get_plan(self, plan_code: str) -> object | None:
        """读取套餐（命中目标时返回下架版本）。

        Args:
            plan_code: 套餐标识。

        Returns:
            object | None: 套餐记录。
        """
        from dataclasses import replace as _replace

        plan = await super().get_plan(plan_code)
        if plan is None or plan_code != self._offline_plan:
            return plan
        return _replace(plan, status="offline")


# ===========================================================================
# W3-04 覆盖率补测（只补测试、不改实现）：真实分支优先
# ===========================================================================
class _RacingCreateStore(InMemoryBillingStore):
    """模拟并发下单的存储替身。

    `find_pending_order` 返回 None（本请求「看不到」对方刚插的待支付单），
    但 `create_order` 会被部分唯一索引拦下并返回既有行——正是两个请求同时
    下单时的真实时序。
    """

    async def find_pending_order(self, tenant_id: str) -> None:
        """始终报告「没有待支付订单」，用于制造并发窗口。

        Args:
            tenant_id: 企业 ID。

        Returns:
            None: 永远没有待支付订单。
        """
        del tenant_id
        return None


class _RefusingClaimStore(InMemoryBillingStore):
    """模拟并发领取回调幂等键的存储替身。

    `get_callback` 看不到对方刚写的留痕（读在前），`append_callback` 被唯一键拦住
    （写在后）——两个渠道重试同时到达时的真实时序。
    """

    async def get_callback(self, channel: str, external_txn_id: str) -> None:
        """始终报告「没有留痕」，用于制造并发窗口。

        Args:
            channel: 渠道。
            external_txn_id: 渠道流水号。

        Returns:
            None: 永远没有留痕。
        """
        del channel, external_txn_id
        return None

    async def append_callback(self, record: object) -> bool:
        """始终报告「已被别人领取」。

        Args:
            record: 回调留痕。

        Returns:
            bool: 永远 False。
        """
        del record
        return False


@pytest.mark.asyncio
async def test_bank_info_invalid_json_falls_back_to_template() -> None:
    """对公转账收款信息非法（非 JSON / 非对象）时回落到模板，不让下单失败。"""
    for raw in ("not-a-json", '"[1,2,3]"', "123"):
        settings = _settings(pay_bank_info=raw)
        service, _store = _service(settings)
        data = await service.create(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=CreateOrderRequest(
                plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="offline_transfer"
            ),
        )
        assert data.pay_url == ""
        assert data.bank_info is not None
        assert data.bank_info.get("account_name") == "请填写收款户名", (
            f"配置 {raw!r} 非法时应回落到模板"
        )


def test_service_exposes_store_and_gateway() -> None:
    """`store` / `gateway` 访问器返回构造时注入的实例。"""
    settings = _settings()
    store = InMemoryBillingStore()
    gateway = FakeGateway(FAKE_KEY)
    service = OrderService(
        settings=settings, store=store, plans=PlanService(settings, store), gateway=gateway
    )
    assert service.store is store
    assert service.gateway is gateway


@pytest.mark.asyncio
@pytest.mark.p0
async def test_create_conflict_on_race_returns_15004() -> None:
    """并发下单：唯一索引拦下第二单时统一转成 `15004`（不产生第二单、不静默成功）。"""
    settings = _settings()
    store = _RacingCreateStore()
    service = OrderService(
        settings=settings,
        store=store,
        plans=PlanService(settings, store),
        gateway=FakeGateway(FAKE_KEY),
    )
    first = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    with pytest.raises(ApiError) as excinfo:
        await service.create(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
        )
    assert excinfo.value.code is ErrorCode.PENDING_ORDER_EXISTS
    assert store.counts()["orders"] == 1, "并发冲突不得落第二单"
    record = await store.get_order(TENANT_ID, first.order_id)
    assert record is not None


@pytest.mark.asyncio
async def test_callback_for_already_paid_order_short_circuits() -> None:
    """已支付订单（运营核销已完成）再收到回调：返回成功语义，不重复开票/开通。"""
    settings = _settings()
    service, store = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="offline_transfer"
        ),
    )
    await service.confirm_paid(
        tenant_id=TENANT_ID,
        order_id=order.order_id,
        paid_amount_cents=order.amount_cents,
        actor_id="usr_ops_1",
        remark="对公到账",
    )
    before = store.counts()
    result = await service.handle_callback(
        _callback_payload(order.order_id, order.amount_cents, "txn_after_paid")
    )
    assert result.code == "SUCCESS"
    after = store.counts()
    assert after["invoices"] == before["invoices"] == 1, "不得重复开票"
    assert after["callbacks"] == before["callbacks"], "已支付后不再占用幂等键"


@pytest.mark.asyncio
@pytest.mark.p0
async def test_callback_for_closed_order_rejected() -> None:
    """订单已关闭（超时）后收到成功回调 → `15008`，不置 paid、不开票。"""
    settings = _settings()
    service, store = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    past = (datetime.now(UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    await store.update_order(TENANT_ID, order.order_id, expires_at=past)
    assert await service.close_expired_orders() == 1

    with pytest.raises(ApiError) as excinfo:
        await service.handle_callback(
            _callback_payload(order.order_id, order.amount_cents, "txn_closed")
        )
    assert excinfo.value.code is ErrorCode.ORDER_ALREADY_PROCESSED
    record = await store.get_order(TENANT_ID, order.order_id)
    assert record is not None
    assert record.status == ORDER_CLOSED
    assert store.counts()["invoices"] == 0
    rejections = await service._audit.recent(  # noqa: SLF001 - 断言审计留痕
        TENANT_ID, action="billing.payment_callback_rejected"
    )
    assert [item.change_summary["reason"] for item in rejections] == ["order_not_pending"]


@pytest.mark.asyncio
@pytest.mark.p0
async def test_callback_lost_claim_returns_success_without_activation() -> None:
    """并发领取幂等键失败（被另一路抢先）→ 返回成功语义但不重复生效。"""
    settings = _settings()
    store = _RefusingClaimStore()
    service = OrderService(
        settings=settings,
        store=store,
        plans=PlanService(settings, store),
        gateway=FakeGateway(FAKE_KEY),
    )
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    result = await service.handle_callback(
        _callback_payload(order.order_id, order.amount_cents, "txn_lost_claim")
    )
    assert result.code == "SUCCESS"
    record = await store.get_order(TENANT_ID, order.order_id)
    assert record is not None
    assert record.status == "pending_payment", "没领到幂等键就不生效（等另一路处理）"
    assert store.counts()["invoices"] == 0


@pytest.mark.asyncio
async def test_ops_confirm_rejects_unknown_and_closed_order() -> None:
    """运营核销：订单不存在 → `20004`；订单已关闭 → `15008`。"""
    settings = _settings()
    service, store = _service(settings)
    with pytest.raises(ApiError) as unknown:
        await service.confirm_paid(
            tenant_id=TENANT_ID,
            order_id="ord_missing",
            paid_amount_cents=100,
            actor_id="usr_ops_1",
        )
    assert unknown.value.code is ErrorCode.RESOURCE_NOT_FOUND

    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="offline_transfer"
        ),
    )
    past = (datetime.now(UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z")
    await store.update_order(TENANT_ID, order.order_id, expires_at=past)
    assert await service.close_expired_orders() == 1
    with pytest.raises(ApiError) as closed:
        await service.confirm_paid(
            tenant_id=TENANT_ID,
            order_id=order.order_id,
            paid_amount_cents=order.amount_cents,
            actor_id="usr_ops_1",
        )
    assert closed.value.code is ErrorCode.ORDER_ALREADY_PROCESSED


@pytest.mark.asyncio
async def test_close_expired_orders_is_noop_when_nothing_expired() -> None:
    """补偿任务：没有超时订单时返回 0，不误伤待支付订单。"""
    settings = _settings()
    service, store = _service(settings)
    order = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
    )
    assert await service.close_expired_orders() == 0
    record = await store.get_order(TENANT_ID, order.order_id)
    assert record is not None
    assert record.status == "pending_payment", "未超时的订单不得被关闭"


@pytest.mark.asyncio
async def test_callback_amount_mismatch_boundary_one_cent() -> None:
    """金额校验边界：少 1 分 / 多 1 分都必须 `15009`（不允许任何偏差）。"""
    for delta in (-1, 1):
        settings = _settings()
        service, store = _service(settings)
        order = await service.create(
            tenant_id=TENANT_ID,
            actor_id="usr_owner",
            request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5),
        )
        with pytest.raises(ApiError) as excinfo:
            await service.handle_callback(
                _callback_payload(order.order_id, order.amount_cents + delta, f"txn_edge_{delta}")
            )
        assert excinfo.value.code is ErrorCode.ORDER_AMOUNT_MISMATCH
        record = await store.get_order(TENANT_ID, order.order_id)
        assert record is not None
        assert record.status == "pending_payment"
