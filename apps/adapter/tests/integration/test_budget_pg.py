# ruff: noqa: S608 - 表名来自本文件常量（非外部输入）
"""租户预算与计量维度的真库验收（阶段四 T4-04.2 / T4-04.3）。

**为什么必须连真库**：

1. `0011` 是**新迁移**——`tenant_budget` 是否存在、RLS 是否真的 `FORCE`、`NUMERIC(3,2)`
   的 `warn_ratio` 能否正确往返，内存替身一条都验不到；
2. 计量维度四列是 `0007` 的**加性列**，"写进去了"只能靠 `SELECT` 证明——
   而 `schema_guard` 的探测也正是查 `information_schema`。

三条判据：预算读写与租户隔离 / 四列真的落库 / 列能力探测返回 True。
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.config import Settings
from app.db import schema_guard
from app.db.repositories.budget_repo import (
    InMemoryBudgetStore,
    PostgresBudgetStore,
    TenantBudgetRecord,
)
from app.db.repositories.usage_repo import PostgresUsageStore, UsageEventRecord

pytestmark = [pytest.mark.integration, pytest.mark.pg]

TENANT = "tnt_budget_pg"
OTHER_TENANT = "tnt_budget_other"
USER = "usr_budget_pg"

TABLES = ("tenant_budget", "usage_event", "usage_monthly")


async def _platform_execute(dsn: str, sql: str) -> None:
    """以平台模式执行一条管理语句。

    Args:
        dsn: 连接串。
        sql: 语句。
    """
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute(sql)
    finally:
        await connection.close()


async def _cleanup(dsn: str) -> None:
    """清理本文件写入的行。

    Args:
        dsn: 连接串。
    """
    for table in TABLES:
        await _platform_execute(dsn, f"DELETE FROM {table} WHERE tenant_id LIKE 'tnt_budget%'")


@pytest.fixture(autouse=True)
async def _release_engine() -> None:
    """用例**前后**都释放进程内引擎。

    `get_engine()` 是进程级缓存，而 pytest-asyncio 每个用例一个新事件循环：
    旧循环里建的 asyncpg 连接留在池里，下一个用例取到就是
    `RuntimeError: Event loop is closed`（"单独跑绿、一起跑红"的根因）。
    前置也释放，避免被排在"建了引擎但没释放"的文件之后。

    Yields:
        None: 用例运行期间。
    """
    from app.db.engine import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
async def _clean(pg_dsn: str) -> None:
    """每个用例前后清理并重置探测缓存。

    Args:
        pg_dsn: 测试库连接串。
    """
    await _cleanup(pg_dsn)
    schema_guard.reset_schema_guard()
    yield
    await _cleanup(pg_dsn)
    schema_guard.reset_schema_guard()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """指向测试库的配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置。
    """
    return Settings(env="test", business_store="postgres", database_url=pg_dsn)


class TestBudgetTable:
    """`tenant_budget` 的 DDL 语义。"""

    async def test_table_exists_and_roundtrips(self, pg_settings: Settings) -> None:
        """0011 建表成功、读写往返一致（`NUMERIC(3,2)` 的比例不丢精度）。"""
        store = PostgresBudgetStore(pg_settings)

        default = await store.get(TENANT)
        assert default.monthly_budget_units == 0
        assert default.enabled is True
        assert default.effective is False

        saved = await store.upsert(
            TenantBudgetRecord(
                tenant_id=TENANT, monthly_budget_units=5000, warn_ratio=0.75, enabled=True
            )
        )
        assert saved.monthly_budget_units == 5000
        assert saved.warn_ratio == pytest.approx(0.75)

        reloaded = await store.get(TENANT)
        assert reloaded.monthly_budget_units == 5000
        assert reloaded.warn_ratio == pytest.approx(0.75)
        assert reloaded.should_warn(4000) is True  # 80% > 75%
        assert reloaded.exhausted(5000) is True

    async def test_budget_is_tenant_isolated(self, pg_settings: Settings) -> None:
        """预算按租户隔离：A 设了额度，B 仍是"不限"（RLS + 主键双保险）。"""
        store = PostgresBudgetStore(pg_settings)
        await store.upsert(TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=999))

        other = await store.get(OTHER_TENANT)

        assert other.monthly_budget_units == 0
        assert other.effective is False

    async def test_memory_store_defaults(self) -> None:
        """内存实现与 PG 实现口径一致（未设 = 不限）。"""
        store = InMemoryBudgetStore()
        assert (await store.get(TENANT)).effective is False
        saved = await store.upsert(TenantBudgetRecord(tenant_id=TENANT, monthly_budget_units=10))
        assert saved.monthly_budget_units == 10
        assert (await store.get(TENANT)).effective is True
        store.clear()
        assert (await store.get(TENANT)).effective is False


class TestUsageDimensionsPg:
    """`usage_event` 四个维度列真的写进去了。"""

    async def test_dimension_columns_written(self, pg_dsn: str, pg_settings: Settings) -> None:
        """写入后能查到 `profile` / `provider` / 两个 token 列。"""
        store = PostgresUsageStore(pg_settings)
        inserted = await store.append(
            UsageEventRecord(
                tenant_id=TENANT,
                user_id=USER,
                capability="chat",
                idem_key="gen_dim_1",
                units=42,
                profile="code",
                provider="downstream",
                prompt_tokens=120,
                completion_tokens=64,
            )
        )
        assert inserted is True

        connection = await asyncpg.connect(pg_dsn)
        try:
            await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT)
            row = await connection.fetchrow(
                "SELECT profile, provider, prompt_tokens, completion_tokens, source "
                "FROM usage_event WHERE tenant_id = $1 AND idem_key = $2",
                TENANT,
                "gen_dim_1",
            )
        finally:
            await connection.close()

        assert row is not None
        assert row["profile"] == "code"
        assert row["provider"] == "downstream"
        assert row["prompt_tokens"] == 120
        assert row["completion_tokens"] == 64

    async def test_schema_guard_detects_columns(self, pg_settings: Settings) -> None:
        """迁移后探测返回 True（`0007` 四列齐备）。"""
        assert await schema_guard.usage_dimension_supported(pg_settings) is True

    async def test_idempotent_append_does_not_duplicate(
        self, pg_dsn: str, pg_settings: Settings
    ) -> None:
        """同幂等键重放只留一条事件（维度写入不影响幂等语义）。"""
        store = PostgresUsageStore(pg_settings)
        first = UsageEventRecord(
            tenant_id=TENANT,
            user_id=USER,
            capability="chat",
            idem_key="gen_dim_dup",
            units=10,
            profile="general",
            prompt_tokens=5,
            completion_tokens=5,
        )
        assert await store.append(first) is True
        assert await store.append(first) is False

        connection = await asyncpg.connect(pg_dsn)
        try:
            await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT)
            count = await connection.fetchval(
                "SELECT count(*) FROM usage_event WHERE tenant_id = $1 AND idem_key = $2",
                TENANT,
                "gen_dim_dup",
            )
        finally:
            await connection.close()
        assert count == 1
