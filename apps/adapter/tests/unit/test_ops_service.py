"""运营后台服务单元测试（阶段二 B10，架构文档 §4.6 OPS1-OPS8）。

验收要点（架构文档 9.2 B10「完成后可验证」逐条对齐）：

1. 企业令牌调任一 `/ops/**` 全 403（`20003`）→ 见 `test_ops_isolation.py`；
2. 核销后订单 `paid`、订阅生效、审计含运营人与备注；
3. 统计 `range=30d` 与 BL5 企业维度求和**误差 0**；
4. 响应中无对话内容/文档原文/成员邮箱明文。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.business.audit_service import ACTION_ORDER_CONFIRMED, ACTION_TENANT_STATUS, AuditService
from app.business.ops_service import OpsService, mask_tenant_name, metrics_window
from app.business.plan_service import PlanService
from app.business.subscription_service import SubscriptionService
from app.business.usage_service import UsageService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.billing_repo import (
    PLAN_PROFESSIONAL,
    InMemoryBillingStore,
)
from app.db.repositories.ops_repo import InMemoryOpsStore
from app.db.repositories.usage_repo import UsageEventRecord, get_usage_store
from app.schemas.billing import ChangeSubscriptionRequest, CreateOrderRequest
from app.schemas.ops import (
    ConfirmOrderRequest,
    UpdateOpsSettingsRequest,
    UpdateTenantStatusRequest,
)

pytestmark = pytest.mark.unit

TENANT_A = "tnt_ops_a"
TENANT_B = "tnt_ops_b"


def _settings() -> Settings:
    """构造测试配置。

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


def _service(settings: Settings) -> tuple[OpsService, InMemoryBillingStore]:
    """构造运营服务与其共用的计费存储。

    Args:
        settings: 配置实例。

    Returns:
        tuple[OpsService, InMemoryBillingStore]: 服务与计费存储。
    """
    billing = InMemoryBillingStore()
    audit = AuditService(settings)
    usage = UsageService(settings)
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=audit,
        usage=usage,
    )
    return service, billing


async def _seed_usage(tenant_id: str, *, user_id: str, units: int, key: str) -> None:
    """写入一条用量事件（构造统计口径数据）。

    Args:
        tenant_id: 企业 ID。
        user_id: 用户 ID。
        units: 额度。
        key: 幂等键。
    """
    store = get_usage_store()
    await store.append(
        UsageEventRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            capability="chat",
            idem_key=key,
            units=units,
            event_id=f"use_{key}",
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    )


@pytest.mark.asyncio
async def test_metrics_quota_total_matches_tenant_sum() -> None:
    """统计 `range=30d` 的额度总和 == 各企业 BL5 口径求和（误差 0，验收 ③）。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    await billing.ensure_tenant(TENANT_A, "甲公司")
    await billing.ensure_tenant(TENANT_B, "乙公司")
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=AuditService(settings),
        usage=UsageService(settings),
    )
    await _seed_usage(TENANT_A, user_id="usr_a1", units=1_000, key="evt_a1")
    await _seed_usage(TENANT_A, user_id="usr_a2", units=500, key="evt_a2")
    await _seed_usage(TENANT_B, user_id="usr_b1", units=250, key="evt_b1")

    metrics = await service.metrics(range_label="30d")
    usage = UsageService(settings)
    expected = 0
    for tenant_id in (TENANT_A, TENANT_B):
        expected += await usage.tenant_total(tenant_id=tenant_id)
    assert metrics.quota_units_total == expected == 1_750
    assert metrics.active_user_count == 3
    assert metrics.conversation_count == 3
    assert metrics.document_count == 0
    assert metrics.range == "30d"
    assert metrics.tenant_count == 2
    assert metrics.active_tenant_count == 2


@pytest.mark.asyncio
async def test_metrics_includes_registered_tenants_only() -> None:
    """统计口径以计费存储里的企业清单为准（未登记企业不计入企业数）。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    await billing.ensure_tenant(TENANT_A, "甲公司")
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=AuditService(settings),
        usage=UsageService(settings),
    )
    await _seed_usage(TENANT_A, user_id="usr_a1", units=120, key="evt_reg_a")
    metrics = await service.metrics(range_label="7d")
    assert metrics.tenant_count == 1
    assert metrics.active_tenant_count == 1
    assert metrics.quota_units_total == 120
    assert metrics.top_tenants[0].tenant_id == TENANT_A


@pytest.mark.asyncio
async def test_tenant_list_and_detail() -> None:
    """OPS1 / OPS2：列表与详情字段齐备，且详情不含邮箱类字段。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    await billing.ensure_tenant(TENANT_A, "示例科技有限公司")
    await billing.update_tenant(TENANT_A, plan_code=PLAN_PROFESSIONAL)
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=AuditService(settings),
        usage=UsageService(settings),
    )
    listed = await service.list_tenants(keyword="示例")
    assert listed.total == 1
    item = listed.items[0]
    assert item.tenant_id == TENANT_A
    assert item.plan_code == PLAN_PROFESSIONAL
    assert item.status == "active"

    detail = await service.tenant_detail(TENANT_A)
    assert detail.tenant.name == "示例科技有限公司"
    assert detail.subscription.seats_total == 3
    assert detail.usage.period
    payload = detail.model_dump()
    for forbidden in ("email", "members_email", "conversation", "document_content"):
        assert forbidden not in str(payload), f"详情响应不得包含字段 {forbidden}"


@pytest.mark.asyncio
async def test_tenant_detail_unknown_returns_20004() -> None:
    """OPS2 企业不存在 → 20004。"""
    settings = _settings()
    service, _ = _service(settings)
    with pytest.raises(ApiError) as excinfo:
        await service.tenant_detail("tnt_missing")
    assert excinfo.value.code is ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.asyncio
async def test_suspend_tenant_writes_audit_with_operator_and_reason() -> None:
    """OPS3：停用企业写审计，含运营人与备注（验收 ②）。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    await billing.ensure_tenant(TENANT_A, "甲公司")
    audit = AuditService(settings)
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=audit,
        usage=UsageService(settings),
    )
    data = await service.update_tenant_status(
        tenant_id=TENANT_A,
        actor_id="usr_ops_1",
        request=UpdateTenantStatusRequest(status="suspended", reason="欠费停用"),
    )
    assert data.status == "suspended"
    tenant = await billing.get_tenant(TENANT_A)
    assert tenant is not None
    assert tenant.suspended_reason == "欠费停用"
    entries = await audit.recent(TENANT_A, action=ACTION_TENANT_STATUS)
    assert len(entries) == 1
    assert entries[0].actor_id == "usr_ops_1"
    assert entries[0].change_summary["operator"] == "usr_ops_1"
    assert entries[0].change_summary["remark"] == "欠费停用"

    resumed = await service.update_tenant_status(
        tenant_id=TENANT_A,
        actor_id="usr_ops_1",
        request=UpdateTenantStatusRequest(status="active", reason="补缴恢复"),
    )
    assert resumed.status == "active"
    tenant = await billing.get_tenant(TENANT_A)
    assert tenant is not None
    assert tenant.suspended_reason == "", "恢复启用时清空停用原因"


@pytest.mark.asyncio
async def test_confirm_order_activates_subscription_and_audits() -> None:
    """OPS5：核销后订单 `paid`、订阅生效、审计含运营人与备注（验收 ②）。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    audit = AuditService(settings)
    plans = PlanService(settings, billing)
    subscriptions = SubscriptionService(settings, billing, plans=plans)
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=audit,
        usage=UsageService(settings),
    )
    order = await service.orders.create(
        tenant_id=TENANT_A,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="offline_transfer"
        ),
    )
    data = await service.confirm_order(
        order_id=order.order_id,
        actor_id="usr_ops_2",
        request=ConfirmOrderRequest(paid_amount_cents=order.amount_cents, remark="对公到账核销"),
    )
    assert data.status == "paid"
    assert data.subscription_status == "active"
    record = await billing.get_order(TENANT_A, order.order_id)
    assert record is not None
    assert record.paid_amount_cents == order.amount_cents
    assert record.confirmed_by == "usr_ops_2"
    subscription = await subscriptions.current(tenant_id=TENANT_A)
    assert subscription.plan_code == PLAN_PROFESSIONAL
    assert subscription.seats_total == 5
    entries = await audit.recent(TENANT_A, action=ACTION_ORDER_CONFIRMED)
    assert len(entries) == 1
    assert entries[0].change_summary["operator"] == "usr_ops_2"
    assert entries[0].change_summary["remark"] == "对公到账核销"
    invoices = await billing.list_invoices(TENANT_A)
    assert len(invoices) == 1


@pytest.mark.asyncio
async def test_confirm_order_amount_mismatch() -> None:
    """OPS5 金额不符 → 15009；重复核销 → 15008。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=AuditService(settings),
        usage=UsageService(settings),
    )
    order = await service.orders.create(
        tenant_id=TENANT_A,
        actor_id="usr_owner",
        request=CreateOrderRequest(
            plan_code=PLAN_PROFESSIONAL, seats=5, payment_method="offline_transfer"
        ),
    )
    with pytest.raises(ApiError) as excinfo:
        await service.confirm_order(
            order_id=order.order_id,
            actor_id="usr_ops_2",
            request=ConfirmOrderRequest(paid_amount_cents=1_000, remark="金额录错"),
        )
    assert excinfo.value.code is ErrorCode.ORDER_AMOUNT_MISMATCH

    await service.confirm_order(
        order_id=order.order_id,
        actor_id="usr_ops_2",
        request=ConfirmOrderRequest(paid_amount_cents=order.amount_cents, remark="正确金额"),
    )
    with pytest.raises(ApiError) as excinfo2:
        await service.confirm_order(
            order_id=order.order_id,
            actor_id="usr_ops_2",
            request=ConfirmOrderRequest(paid_amount_cents=order.amount_cents, remark="重复点击"),
        )
    assert excinfo2.value.code is ErrorCode.ORDER_ALREADY_PROCESSED


@pytest.mark.asyncio
async def test_confirm_unknown_order_returns_20004() -> None:
    """OPS5 订单不存在 → 20004。"""
    settings = _settings()
    service, _ = _service(settings)
    with pytest.raises(ApiError) as excinfo:
        await service.confirm_order(
            order_id="ord_missing",
            actor_id="usr_ops_2",
            request=ConfirmOrderRequest(paid_amount_cents=100, remark=""),
        )
    assert excinfo.value.code is ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.asyncio
async def test_settings_read_and_update() -> None:
    """OPS7 / OPS8：读取默认值、部分更新、写审计（含运营人）。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    audit = AuditService(settings)
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=audit,
        usage=UsageService(settings),
    )
    initial = await service.get_settings()
    assert initial.quota_warn_threshold == 80
    assert initial.trial_days == 14
    assert initial.quota_unit_label == "字数额度"
    assert initial.announcement == ""
    assert set(initial.plan_status) == {"free", "professional", "enterprise"}

    updated = await service.update_settings(
        actor_id="usr_ops_3",
        request=UpdateOpsSettingsRequest(quota_warn_threshold=90, announcement="系统升级公告"),
    )
    assert updated.quota_warn_threshold == 90
    assert updated.announcement == "系统升级公告"
    assert updated.trial_days == 14, "未传字段保持不变"
    entries = await audit.recent("__platform__")
    assert entries and entries[0].actor_id == "usr_ops_3"
    assert entries[0].change_summary["scope"] == "platform"


@pytest.mark.asyncio
async def test_settings_update_requires_at_least_one_field() -> None:
    """OPS8 未传任何字段 → 20001（同码多文案）。"""
    settings = _settings()
    service, _ = _service(settings)
    with pytest.raises(ApiError) as excinfo:
        await service.update_settings(actor_id="usr_ops_3", request=UpdateOpsSettingsRequest())
    assert excinfo.value.code is ErrorCode.PARAM_INVALID
    assert "至少修改一项配置" in excinfo.value.message


@pytest.mark.asyncio
async def test_orders_list_cross_tenant() -> None:
    """OPS4：跨企业订单列表可按企业过滤，含企业展示名。"""
    settings = _settings()
    billing = InMemoryBillingStore()
    await billing.ensure_tenant(TENANT_A, "甲公司")
    service = OpsService(
        settings=settings,
        store=InMemoryOpsStore(billing, settings),
        audit=AuditService(settings),
        usage=UsageService(settings),
    )
    subscriptions = SubscriptionService(settings, billing)
    await subscriptions.change(
        tenant_id=TENANT_B,
        actor_id="usr_owner_b",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    all_orders = await service.list_orders()
    assert all_orders.total == 1
    assert all_orders.items[0].tenant_id == TENANT_B
    filtered = await service.list_orders(tenant_id=TENANT_A)
    assert filtered.total == 0


def test_metrics_window_labels() -> None:
    """统计时间窗：7d / 30d 天数正确，非法取值 → 20001。"""
    moment = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
    window = metrics_window("30d", moment)
    assert window.start_at.startswith("2026-10-03")
    assert window.end_at.startswith("2026-11-02")
    with pytest.raises(ApiError) as excinfo:
        metrics_window("90d", moment)
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


def test_mask_tenant_name() -> None:
    """企业名脱敏：保留首尾字符。"""
    assert mask_tenant_name("示例科技有限公司") == "示******司"
    assert mask_tenant_name("甲乙") == "甲*"
    assert mask_tenant_name("") == ""
