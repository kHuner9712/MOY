"""支付回调安全加固测试（阶段二 B09 / QA P0-07）。

**缺陷背景**（修复前）：回调的幂等键在**业务校验之前**就被写入
`payment_callback_log`。后果是一个签名错误（或交易未成功/金额不符）的载荷，
只要带上**真实** `transaction_id`，就把该流水号的幂等键占掉；此后渠道的真实
成功回调命中幂等冲突 → 直接返回 `SUCCESS` 但**永不开通**，且渠道收到 200 后
不再重试 → 用户已付款、订阅永不生效。

**修复后的不变式**（本文件逐条断言）：

| # | 不变式 |
| --- | --- |
| 1 | 验签失败 → `20001`，订单仍 `pending_payment`，无账单、无订阅、**不占幂等键** |
| 2 | 交易未成功（`trade_state != SUCCESS`）→ `15007`，不占幂等键，后续成功回调可生效 |
| 3 | 金额不符 → `15009`，不占幂等键，后续同流水号正确金额可生效 |
| 4 | 订单不存在 → `20004`，不占幂等键 |
| 5 | 每次拒绝都在 `audit_log` 留一条 `billing.payment_callback_rejected`（result=failure）|
| 6 | 真正生效一次后，重复回调仍幂等（1 张账单 / 1 个订阅） |
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.audit_service import (
    ACTION_ORDER_PAID,
    ACTION_PAYMENT_CALLBACK_REJECTED,
    AuditService,
)
from app.business.order_service import OrderService
from app.business.order_service import REJECTED_TXN_PREFIX
from app.business.payment_gateway import FakeGateway, payload_digest
from app.business.plan_service import PlanService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.billing_repo import (
    PLAN_PROFESSIONAL,
    InMemoryBillingStore,
)
from app.schemas.billing import CreateOrderRequest

pytestmark = pytest.mark.unit

TENANT_ID = "tnt_p0_07"

#: 假网关签名密钥（与 `FakeGateway` 默认值一致）
FAKE_KEY = "test-gateway-key"

#: 平台哨兵（未验签回调的审计归属）
PLATFORM_TENANT = "__platform__"


def _settings() -> Settings:
    """构造测试配置（桩模式 → 假支付网关）。

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


def _payload(
    order_id: str, amount_cents: int, txn: str, *, state: str = "SUCCESS"
) -> dict[str, Any]:
    """构造一条**签名正确**的回调载荷。

    Args:
        order_id: 订单 ID。
        amount_cents: 金额（分）。
        txn: 渠道流水号。
        state: 交易状态。

    Returns:
        dict[str, Any]: 回调载荷。
    """
    gateway = FakeGateway(FAKE_KEY)
    return {
        "out_trade_no": order_id,
        "transaction_id": txn,
        "amount_total": amount_cents,
        "sign": gateway.sign(out_trade_no=order_id, transaction_id=txn, amount_total=amount_cents),
        "trade_state": state,
    }


class _Fixture:
    """一组「服务 + 存储 + 审计」的测试装配。

    Attributes:
        store: 内存计费存储。
        audit: 操作记录服务。
        service: 订单服务。
        order_id: 已创建的待支付订单 ID。
        amount_cents: 该订单应付金额（分）。
    """

    def __init__(
        self,
        store: InMemoryBillingStore,
        audit: AuditService,
        service: OrderService,
        order_id: str,
        amount_cents: int,
    ) -> None:
        """初始化。

        Args:
            store: 内存计费存储。
            audit: 操作记录服务。
            service: 订单服务。
            order_id: 订单 ID。
            amount_cents: 应付金额。
        """
        self.store = store
        self.audit = audit
        self.service = service
        self.order_id = order_id
        self.amount_cents = amount_cents


async def _make_fixture() -> _Fixture:
    """装配一个待支付订单（每例独立存储，互不串扰）。

    Returns:
        _Fixture: 测试装配。
    """
    settings = _settings()
    store = InMemoryBillingStore()
    audit = AuditService(settings)
    service = OrderService(
        settings=settings,
        store=store,
        plans=PlanService(settings, store),
        audit=audit,
        gateway=FakeGateway(FAKE_KEY),
    )
    created = await service.create(
        tenant_id=TENANT_ID,
        actor_id="usr_owner",
        request=CreateOrderRequest(plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="online"),
    )
    return _Fixture(store, audit, service, created.order_id, created.amount_cents)


async def _rejection_entries(fixture: _Fixture, tenant_id: str) -> list[Any]:
    """读取某个租户下的「回调被拒绝」审计条目。

    Args:
        fixture: 测试装配。
        tenant_id: 审计归属租户。

    Returns:
        list[Any]: 审计条目列表。
    """
    return await fixture.audit.recent(tenant_id, action=ACTION_PAYMENT_CALLBACK_REJECTED)


async def _assert_untouched(fixture: _Fixture, payload: dict[str, Any]) -> None:
    """断言订单/账单/订阅未被改动，且**真实流水号未被占用**（幂等位留给真实回调）。

    留痕仍然存在：拒绝事件在 `payment_callback_log` 里以
    `rejected:<载荷摘要>` 为键写一条 `signature_ok=false/true` 的取证行，
    因此「有留痕」与「不占幂等位」同时成立。

    Args:
        fixture: 测试装配。
        payload: 本次被拒绝的回调载荷。
    """
    record = await fixture.store.get_order(TENANT_ID, fixture.order_id)
    assert record is not None
    assert record.status == "pending_payment", "失败回调不得改变订单状态"
    counts = fixture.store.counts()
    assert counts["invoices"] == 0, "失败回调不得开票"
    assert counts["subscriptions"] == 0, "失败回调不得开通订阅"
    txn = str(payload["transaction_id"])
    assert await fixture.store.get_callback("fake", txn) is None, (
        "失败回调不得占用真实流水号的幂等键（否则真实回调永远进不来）"
    )
    digest = payload_digest(payload)
    forensics = await fixture.store.get_callback("fake", f"{REJECTED_TXN_PREFIX}{digest[:40]}")
    assert forensics is not None, "拒绝事件仍须在 payment_callback_log 留取证行"


@pytest.mark.asyncio
@pytest.mark.p0
async def test_bad_signature_rejects_without_activating() -> None:
    """验签失败 → `20001`，且订单/账单/订阅/幂等键全部不变（不变式 1、5）。"""
    fixture = await _make_fixture()
    payload = _payload(fixture.order_id, fixture.amount_cents, "txn_bad_sign")
    payload["sign"] = "0" * 64

    with pytest.raises(ApiError) as excinfo:
        await fixture.service.handle_callback(payload)
    assert excinfo.value.code is ErrorCode.PARAM_INVALID
    assert excinfo.value.message == "支付通知校验未通过，请稍后重试"

    await _assert_untouched(fixture, payload)
    # 未验签的载荷不可信 → 审计落平台哨兵，且不写入原始流水号
    entries = await _rejection_entries(fixture, PLATFORM_TENANT)
    assert len(entries) == 1
    assert entries[0].result == "failure"
    assert entries[0].change_summary["reason"] == "signature_invalid"
    assert entries[0].change_summary["signature_ok"] is False
    assert "payload_sha256" in entries[0].change_summary
    assert await fixture.store.get_callback("fake", "txn_bad_sign") is None


@pytest.mark.asyncio
@pytest.mark.p0
async def test_bad_signature_does_not_block_real_callback() -> None:
    """**P0-07 核心回归**：脏载荷不能占掉真实流水号的幂等键。

    修复前：先写幂等键再校验 → 真实回调被判「重复」，返回 SUCCESS 但永不开通。
    """
    fixture = await _make_fixture()
    spoofed = _payload(fixture.order_id, fixture.amount_cents, "txn_real_001")
    spoofed["sign"] = "f" * 64
    with pytest.raises(ApiError):
        await fixture.service.handle_callback(spoofed)

    result = await fixture.service.handle_callback(
        _payload(fixture.order_id, fixture.amount_cents, "txn_real_001")
    )
    assert result.code == "SUCCESS"
    record = await fixture.store.get_order(TENANT_ID, fixture.order_id)
    assert record is not None
    assert record.status == "paid", "真实回调必须能生效（修复前此处为 pending_payment）"
    counts = fixture.store.counts()
    assert counts["invoices"] == 1
    assert counts["subscriptions"] == 1
    # 1 条真实回调留痕 + 1 条被拒绝的取证行（`rejected:` 前缀，不占幂等位）
    assert counts["callbacks"] == 2
    assert await fixture.store.get_callback("fake", "txn_real_001") is not None


@pytest.mark.asyncio
async def test_trade_not_success_rejects_and_allows_retry() -> None:
    """交易未成功 → `15007`，不占幂等键；随后成功回调可生效（不变式 2）。"""
    fixture = await _make_fixture()
    payload = _payload(fixture.order_id, fixture.amount_cents, "txn_fail_001", state="FAIL")
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.handle_callback(payload)
    assert excinfo.value.code is ErrorCode.PAYMENT_INCOMPLETE
    await _assert_untouched(fixture, payload)
    entries = await _rejection_entries(fixture, TENANT_ID)
    assert [item.change_summary["reason"] for item in entries] == ["trade_not_success"]

    await fixture.service.handle_callback(
        _payload(fixture.order_id, fixture.amount_cents, "txn_fail_001")
    )
    record = await fixture.store.get_order(TENANT_ID, fixture.order_id)
    assert record is not None
    assert record.status == "paid"


@pytest.mark.asyncio
async def test_amount_mismatch_rejects_and_allows_retry() -> None:
    """金额不符 → `15009`，不占幂等键；同流水号正确金额可生效（不变式 3）。"""
    fixture = await _make_fixture()
    payload = _payload(fixture.order_id, fixture.amount_cents - 100, "txn_amount_001")
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.handle_callback(payload)
    assert excinfo.value.code is ErrorCode.ORDER_AMOUNT_MISMATCH
    await _assert_untouched(fixture, payload)
    entries = await _rejection_entries(fixture, TENANT_ID)
    assert entries[0].change_summary["reason"] == "amount_mismatch"
    assert entries[0].change_summary["signature_ok"] is True, "验签已通过的拒绝要区分标记"

    await fixture.service.handle_callback(
        _payload(fixture.order_id, fixture.amount_cents, "txn_amount_001")
    )
    record = await fixture.store.get_order(TENANT_ID, fixture.order_id)
    assert record is not None
    assert record.status == "paid"


@pytest.mark.asyncio
async def test_unknown_order_rejects_without_key() -> None:
    """订单不存在 → `20004`，不占幂等键（不变式 4）。"""
    fixture = await _make_fixture()
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.handle_callback(_payload("ord_not_exists", 19_500, "txn_unknown_001"))
    assert excinfo.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert await fixture.store.get_callback("fake", "txn_unknown_001") is None
    entries = await _rejection_entries(fixture, PLATFORM_TENANT)
    assert [item.change_summary["reason"] for item in entries] == ["order_unknown"]


@pytest.mark.asyncio
@pytest.mark.p0
async def test_successful_payment_stays_idempotent() -> None:
    """真正生效一次后，重复回调仍幂等：1 张账单 / 1 个订阅 / 1 条支付审计（不变式 6）。"""
    fixture = await _make_fixture()
    payload = _payload(fixture.order_id, fixture.amount_cents, "txn_ok_001")
    for _ in range(3):
        assert (await fixture.service.handle_callback(payload)).code == "SUCCESS"

    counts = fixture.store.counts()
    assert counts["invoices"] == 1
    assert counts["subscriptions"] == 1
    assert counts["callbacks"] == 1, "成功回调只留一条痕，重复回调不新增行"
    assert await fixture.store.get_callback("fake", "txn_ok_001") is not None
    paid_entries = await fixture.audit.recent(TENANT_ID, action=ACTION_ORDER_PAID)
    assert len(paid_entries) == 1, "支付生效只允许审计一次"
    # 成功后不再产生新的拒绝记录
    assert await _rejection_entries(fixture, TENANT_ID) == []
    assert await _rejection_entries(fixture, PLATFORM_TENANT) == []


@pytest.mark.asyncio
async def test_rejected_callback_never_writes_channel_identifier_to_key_space() -> None:
    """拒绝路径不得把未受信的流水号写进幂等键空间（防御 DoS）。"""
    fixture = await _make_fixture()
    for txn in ("txn_dos_1", "txn_dos_2"):
        payload = _payload(fixture.order_id, fixture.amount_cents, txn)
        payload["sign"] = "a" * 64
        with pytest.raises(ApiError):
            await fixture.service.handle_callback(payload)
    assert (await fixture.store.get_callback("fake", "txn_dos_1")) is None
    assert (await fixture.store.get_callback("fake", "txn_dos_2")) is None
    assert fixture.store.counts()["callbacks"] == 2, "两条脏载荷各留一条取证行"
