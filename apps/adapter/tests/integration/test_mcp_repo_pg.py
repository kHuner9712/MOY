"""`mcp_server` 的 PG 仓储与 RLS 回归（矩阵第 3 项）。

只在带库 CI 跑（`--run-pg` + `OA_TEST_DATABASE_URL`）：

1. 真实 upsert/list/get/delete；
2. 写 A 租户的行，B 租户列表/按名读取都拿不到；
3. 表开启且强制 RLS，策略 `p_tenant_isolation` 存在。

缺库时 skip，绝不假绿。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.mcp_repo import McpServerRecord, PostgresMcpServerStore
from app.db.session import platform_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

TENANT_A = f"tnt_pg_mcp_a_{uuid.uuid4().hex[:8]}"
TENANT_B = f"tnt_pg_mcp_b_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎。

    Yields:
        AsyncIterator[None]: 无值。
    """
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()
    reset_settings_cache()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """PG 测试配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="pg-mcp-secret",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
    )


@pytest.fixture
async def store(pg_settings: Settings) -> AsyncIterator[PostgresMcpServerStore]:
    """PG 仓储（结束清理本次租户）。

    Args:
        pg_settings: 配置。

    Yields:
        AsyncIterator[PostgresMcpServerStore]: 仓储。
    """
    instance = PostgresMcpServerStore(pg_settings)
    try:
        yield instance
    finally:
        async with platform_scope(pg_settings) as uow:
            for tenant in (TENANT_A, TENANT_B):
                await uow.execute(
                    "DELETE FROM mcp_server WHERE tenant_id = :tenant_id",
                    {"tenant_id": tenant},
                )


def _record(tenant_id: str, *, name: str = "demo", server_id: str | None = None) -> McpServerRecord:
    """构造登记。

    Args:
        tenant_id: 租户。
        name: 名称。
        server_id: 可选 ID。

    Returns:
        McpServerRecord: 登记。
    """
    return McpServerRecord(
        tenant_id=tenant_id,
        server_id=server_id or f"mcp_{uuid.uuid4().hex[:24]}",
        name=name,
        transport="http",
        endpoint="https://mcp.example.com/rpc",
        credential_ref="cred_abc1234567",
        allowed_tools=("echo",),
        enabled=True,
        timeout_ms=15_000,
        max_output_bytes=65_536,
    )


async def test_upsert_get_list_delete(store: PostgresMcpServerStore) -> None:
    """真实 PG CRUD。"""
    saved = await store.upsert_server(_record(TENANT_A))
    loaded = await store.get_server(TENANT_A, saved.server_id)
    assert loaded is not None
    assert loaded.name == "demo"
    assert loaded.allowed_tools == ("echo",)
    assert loaded.args == ()
    assert loaded.credential_ref == "cred_abc1234567"
    assert [row.server_id for row in await store.list_servers(TENANT_A)] == [saved.server_id]
    assert await store.count_servers(TENANT_A) == 1
    assert await store.delete_server(TENANT_A, saved.server_id) is True
    assert await store.get_server(TENANT_A, saved.server_id) is None


async def test_tenant_isolation(store: PostgresMcpServerStore) -> None:
    """A 租户的行对 B 租户不可见（RLS + 仓储条件）。"""
    saved = await store.upsert_server(_record(TENANT_A))
    assert await store.list_servers(TENANT_B) == []
    assert await store.get_server(TENANT_B, saved.server_id) is None
    assert await store.get_by_name(TENANT_B, saved.name) is None


async def test_rls_is_forced(pg_settings: Settings) -> None:
    """表必须开启并强制 RLS，且有 `p_tenant_isolation` 策略。"""
    async with platform_scope(pg_settings) as uow:
        row = await uow.fetch_one(
            """
            SELECT c.relrowsecurity, c.relforcerowsecurity
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relname = 'mcp_server'
            """
        )
        assert row is not None
        assert bool(row["relrowsecurity"]) is True
        assert bool(row["relforcerowsecurity"]) is True
        policy = await uow.fetch_one(
            """
            SELECT policyname FROM pg_policies
             WHERE schemaname = 'public' AND tablename = 'mcp_server'
               AND policyname = 'p_tenant_isolation'
            """
        )
        assert policy is not None
