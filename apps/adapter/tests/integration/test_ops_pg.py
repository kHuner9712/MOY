"""运营仓储的 PostgreSQL 实现测试（阶段二 B10）。

验证 `ops_settings` 的逐键 upsert / 读回，以及运营侧「停用企业 + 跨企业订单列表」
在真实库上与计费存储同源（不另写一套 SQL）。
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.billing_repo import (
    PLAN_PROFESSIONAL,
    OrderRecord,
    PostgresBillingStore,
    now_iso,
)
from app.db.repositories.ops_repo import OpsSettings, PostgresOpsStore
from app.db.session import platform_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 32 字节密钥的 base64（仅测试用）
TEST_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()

#: 本次运行专用租户
TENANT_ID = f"tnt_pg_ops_{uuid.uuid4().hex[:8]}"

#: 另一个租户
OTHER_TENANT = f"tnt_pg_ops_other_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎（事件循环按用例重建）。

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
        jwt_secret="pg-ops-secret",
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
async def test_ops_settings_upsert_roundtrip(
    pg_settings: Settings, billing_store: PostgresBillingStore
) -> None:
    """`ops_settings` 逐键 upsert 后能读回全量配置。"""
    store = PostgresOpsStore(pg_settings, billing_store)
    initial = await store.get_settings()
    assert isinstance(initial, OpsSettings)
    saved = await store.save_settings(
        {"quota_warn_threshold": 91, "announcement": "PG 公告", "trial_days": 21},
        "usr_pg_ops",
    )
    assert saved.quota_warn_threshold == 91
    assert saved.announcement == "PG 公告"
    assert saved.trial_days == 21
    assert saved.updated_by == "usr_pg_ops"
    reread = await store.get_settings()
    assert reread.quota_warn_threshold == 91
    # 还原，避免影响其他用例
    await store.save_settings(
        {"quota_warn_threshold": 80, "announcement": "", "trial_days": 14}, "usr_pg_ops"
    )


@pytest.mark.asyncio
async def test_ops_tenant_status_and_order_listing(
    pg_settings: Settings, billing_store: PostgresBillingStore
) -> None:
    """OPS3/OPS4 的 PostgreSQL 路径：停用企业、跨企业订单列表。"""
    store = PostgresOpsStore(pg_settings, billing_store)
    await billing_store.ensure_tenant(TENANT_ID, "PG 甲企业")
    await billing_store.ensure_tenant(OTHER_TENANT, "PG 乙企业")
    updated = await store.update_tenant_status(TENANT_ID, "suspended", "欠费")
    assert updated is not None
    assert updated.status == "suspended"
    assert updated.suspended_reason == "欠费"
    await store.update_tenant_status(TENANT_ID, "active", "")

    order = await billing_store.create_order(
        OrderRecord(
            tenant_id=OTHER_TENANT,
            order_id=f"ord_{uuid.uuid4().hex[:16]}",
            order_type="new",
            plan_code=PLAN_PROFESSIONAL,
            seats=5,
            amount_cents=19_500,
            expires_at=now_iso(),
            created_by="usr_pg_owner",
        )
    )
    orders, total = await store.list_orders(tenant_id=OTHER_TENANT)
    assert total == 1
    assert orders[0].order_id == order.order_id
    found = await store.get_order(order.order_id)
    assert found is not None
    assert found.tenant_id == OTHER_TENANT
