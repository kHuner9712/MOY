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
from app.db.repositories.billing_repo import (
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
