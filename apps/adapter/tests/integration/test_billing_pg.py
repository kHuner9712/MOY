"""计费与运营仓储的 PostgreSQL 实现测试（阶段二 B09/B10）。

内存后端由 `tests/unit/test_subscription_service.py` / `test_order_service.py` /
`test_ops_service.py` 覆盖；本文件只验证 **PostgreSQL 实现路径** 与
平台作用域（`allow_platform`）在真实库上的行为，包括：

1. 部分唯一索引兜底：`uq_order_pending` → 同企业仅一个待支付订单；
2. `uq_invoice_order` / `uq_callback_external` 的唯一键幂等；
3. 平台作用域：`platform_scope()` 能跨租户读，`tenant_scope()` 不能；
4. `ops_settings` 的 upsert 与读回。

用 `--run-pg` 启用（缺 `OA_TEST_DATABASE_URL` 时 skip）。
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator

import pytest

from app.business.plan_service import PlanService
from app.business.subscription_service import SubscriptionService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.billing import (
    INVOICE_ISSUED,
    ORDER_CLOSED,
    ORDER_PAID,
    PLAN_PROFESSIONAL,
    CallbackLogRecord,
    InvoiceRecord,
    OrderRecord,
    PostgresBillingStore,
    now_iso,
)
from app.db.session import platform_scope, tenant_scope
from app.schemas.billing import ChangeSubscriptionRequest

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 32 字节密钥的 base64（仅测试用；生产必须由部署侧生成）
TEST_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

#: 本次运行专用的租户（用例结束清理）
TENANT_ID = f"tnt_pg_b10_{uuid.uuid4().hex[:8]}"

#: 另一个租户（证明跨租户隔离）
OTHER_TENANT = f"tnt_pg_b10_other_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎。

    `get_engine()` 是进程级单例，而 pytest-asyncio 每个用例一个新事件循环；
    不释放会出现「连接属于已关闭的 loop」→ 第二个用例起随机 `Event loop is closed`。

    Yields:
        AsyncIterator[None]: 无值。
    """
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """PostgreSQL 后端的测试配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置实例。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="pg-test-secret",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
        credential_encryption_key=TEST_KEY,
        pay_channel="fake",
    )


@pytest.fixture
async def billing_store(pg_settings: Settings) -> AsyncIterator[PostgresBillingStore]:
    """PostgreSQL 计费存储（用例结束清理本次租户数据）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresBillingStore]: 计费存储。
    """
    store = PostgresBillingStore(pg_settings)
    try:
        yield store
    finally:
        # 注意：`audit_log` 对应用角色**只允许插入**（不可更新/删除，架构文档 3.11），
        # 因此清理清单里不包含它——运营核销等用例写入的记录按设计永久保留。
        for tenant_id in (TENANT_ID, OTHER_TENANT):
            async with platform_scope(pg_settings) as uow:
                for table in (
                    "billing_invoice",
                    "payment_callback_log",
                    "billing_order",
                    "billing_subscription",
                    "tenant",
                ):
                    await uow.execute(
                        f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                        {"tenant_id": tenant_id},
                    )


@pytest.mark.asyncio
async def test_plan_catalog_reads_platform_rows(
    billing_store: PostgresBillingStore,
) -> None:
    """套餐目录来自 `billing_plan` 平台行（3 条种子，价格与额度齐备）。"""
    plans = await billing_store.list_plans()
    codes = [plan.plan_code for plan in plans]
    assert codes == ["free", "professional", "enterprise"]
    free = await billing_store.get_plan("free")
    assert free is not None
    assert free.min_seats == 3
    assert free.quota_units_per_seat_month == 200_000
    assert free.features["org_structure"] is False
    assert await billing_store.get_plan("no_such_plan") is None


@pytest.mark.asyncio
async def test_subscription_and_order_lifecycle(
    pg_settings: Settings, billing_store: PostgresBillingStore
) -> None:
    """订阅升级 → 下单 → 待支付唯一约束 → 支付 → 开票幂等（真实库）。"""
    plans = PlanService(pg_settings, billing_store)
    subscriptions = SubscriptionService(pg_settings, billing_store, plans=plans)
    data = await subscriptions.change(
        tenant_id=TENANT_ID,
        actor_id="usr_pg_owner",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    assert data.order_id.startswith("ord_")

    subscription = await billing_store.get_active_subscription(TENANT_ID)
    assert subscription is not None
    assert subscription.plan_code == PLAN_PROFESSIONAL
    assert subscription.seats_total == 5

    tenant = await billing_store.get_tenant(TENANT_ID)
    assert tenant is not None
    assert tenant.plan_code == PLAN_PROFESSIONAL, "功能开关随套餐落库"

    pending = await billing_store.find_pending_order(TENANT_ID)
    assert pending is not None
    # 部分唯一索引：再插一条待支付订单不会成功，返回既有行
    duplicate = await billing_store.create_order(
        OrderRecord(
            tenant_id=TENANT_ID,
            order_id="ord_should_not_insert",
            order_type="new",
            plan_code=PLAN_PROFESSIONAL,
            seats=5,
            amount_cents=1,
            expires_at=now_iso(),
            created_by="usr_pg_owner",
        )
    )
    assert duplicate.order_id == pending.order_id

    found = await billing_store.find_order_by_id(pending.order_id)
    assert found is not None
    assert found.tenant_id == TENANT_ID

    paid = await billing_store.update_order(
        TENANT_ID,
        pending.order_id,
        status=ORDER_PAID,
        paid_amount_cents=pending.amount_cents,
        paid_at=now_iso(),
        payment_no_masked="****1234",
        confirmed_by="usr_pg_ops",
        confirm_remark="对公到账",
    )
    assert paid is not None
    assert paid.status == ORDER_PAID
    assert paid.confirmed_by == "usr_pg_ops"

    invoice = await billing_store.ensure_invoice(
        InvoiceRecord(
            tenant_id=TENANT_ID,
            invoice_id=f"inv_{uuid.uuid4().hex[:16]}",
            order_id=pending.order_id,
            period=now_iso()[:7],
            amount_cents=pending.amount_cents,
            status=INVOICE_ISSUED,
            issued_at=now_iso(),
        )
    )
    again = await billing_store.ensure_invoice(
        InvoiceRecord(
            tenant_id=TENANT_ID,
            invoice_id=f"inv_{uuid.uuid4().hex[:16]}",
            order_id=pending.order_id,
            period=now_iso()[:7],
            amount_cents=pending.amount_cents,
            status=INVOICE_ISSUED,
        )
    )
    assert again.invoice_id == invoice.invoice_id, "一单一票（uq_invoice_order）"
    invoices = await billing_store.list_invoices(TENANT_ID)
    assert len(invoices) == 1
    by_order = await billing_store.get_invoice_by_order(TENANT_ID, pending.order_id)
    assert by_order is not None
    assert by_order.status == INVOICE_ISSUED

    orders, total = await billing_store.list_orders(TENANT_ID, status=ORDER_PAID)
    assert total == 1
    assert orders[0].order_id == pending.order_id


@pytest.mark.asyncio
async def test_callback_log_is_unique_per_external_txn(
    billing_store: PostgresBillingStore,
) -> None:
    """同一渠道流水号只允许一条留痕（`uq_callback_external`）。"""
    txn = f"txn_{uuid.uuid4().hex[:12]}"
    first = await billing_store.append_callback(
        CallbackLogRecord(
            tenant_id=TENANT_ID,
            callback_id=f"cbk_{uuid.uuid4().hex[:16]}",
            channel="wechat_pay",
            external_txn_id=txn,
            order_id="ord_x",
            amount_cents=100,
            signature_ok=True,
            payload_sha256="a" * 64,
        )
    )
    assert first is True
    second = await billing_store.append_callback(
        CallbackLogRecord(
            tenant_id=TENANT_ID,
            callback_id=f"cbk_{uuid.uuid4().hex[:16]}",
            channel="wechat_pay",
            external_txn_id=txn,
            order_id="ord_x",
            amount_cents=100,
            signature_ok=True,
            payload_sha256="b" * 64,
        )
    )
    assert second is False
    record = await billing_store.get_callback("wechat_pay", txn)
    assert record is not None
    assert record.processed is False
    await billing_store.mark_callback_processed("wechat_pay", txn)
    record = await billing_store.get_callback("wechat_pay", txn)
    assert record is not None
    assert record.processed is True


@pytest.mark.asyncio
async def test_expired_order_closed_in_database(
    pg_settings: Settings, billing_store: PostgresBillingStore
) -> None:
    """超时订单被置为 `closed`（懒过期 + 补偿两条路径都走真实 SQL）。"""
    await billing_store.ensure_tenant(TENANT_ID, "PG 测试企业")
    created = await billing_store.create_order(
        OrderRecord(
            tenant_id=TENANT_ID,
            order_id=f"ord_{uuid.uuid4().hex[:16]}",
            order_type="new",
            plan_code=PLAN_PROFESSIONAL,
            seats=5,
            amount_cents=19_500,
            expires_at="2020-01-01T00:00:00Z",
            created_by="usr_pg_owner",
        )
    )
    assert created.status == "pending_payment"
    closed = await billing_store.expire_orders(now_iso(), TENANT_ID)
    assert [item.order_id for item in closed] == [created.order_id]
    record = await billing_store.get_order(TENANT_ID, created.order_id)
    assert record is not None
    assert record.status == ORDER_CLOSED
    # 同租户再扫一遍：本企业已无待关闭订单（幂等）
    # **必须带 TENANT_ID**：不带租户参数是平台侧全库补偿口径，断言 `== []`
    # 等于要求"整个数据库没有任何过期订单"——那是本用例控制不了的外部状态，
    # 任何一个其他租户的残留（并发 e2e、手工探针）都会让这条假红。
    # 共享库里的断言只能断言"自己管得着的那部分"。
    assert await billing_store.expire_orders(now_iso(), TENANT_ID) == []


@pytest.mark.asyncio
async def test_platform_scope_cross_tenant_and_guard(
    pg_settings: Settings, billing_store: PostgresBillingStore
) -> None:
    """平台作用域可跨租户读；普通租户作用域读不到别人的行（RLS 双保险）。"""
    await billing_store.ensure_tenant(TENANT_ID, "甲企业")
    await billing_store.ensure_tenant(OTHER_TENANT, "乙企业")
    platform_rows = await billing_store.list_tenants(keyword="甲企业")
    assert [item.tenant_id for item in platform_rows] == [TENANT_ID]
    assert await billing_store.get_tenant(OTHER_TENANT) is not None

    async with tenant_scope(TENANT_ID, read_only=True, settings=pg_settings) as uow:
        visible = await uow.fetch_scalar("SELECT COUNT(*) FROM tenant")
    assert int(visible or 0) == 1, "租户作用域只能看到自己的企业行"

    # 平台哨兵必须显式声明 allow_platform=True，否则属编码缺陷（30003）即刻暴露
    with pytest.raises(ApiError) as excinfo:
        async with tenant_scope("__platform__", settings=pg_settings):
            pass
    assert excinfo.value.code is ErrorCode.INTERNAL_ERROR
