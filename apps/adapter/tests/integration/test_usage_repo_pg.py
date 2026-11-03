"""usage 仓储 PG 覆盖补测（审计 2026-10-31 M4）。

`usage/postgres.py` 的 PG 实现此前无独立用例（仅散见 `test_usage_endpoints.py`）。
本文件覆盖用量事件写入、月度聚合、日总量、事件计数与租户隔离。

用 `--run-pg` 启用；缺 `OA_TEST_DATABASE_URL` 时 skip，**绝不假绿**。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.usage.postgres import PostgresUsageStore
from app.db.repositories.usage.records import UsageEventRecord
from app.db.session import platform_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

pytest_plugins = ["tests.integration.pg_fixture"]

TENANT_ID = f"tnt_pg_usage_{uuid.uuid4().hex[:8]}"
OTHER_TENANT = f"tnt_pg_usage_other_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _release_engine() -> AsyncIterator[None]:
    """用例**前后**都释放进程内引擎（对齐 `test_budget_pg.py` 的既有约定）。

    `get_engine()` 是进程级缓存，而 pytest-asyncio 每个用例一个新事件循环：
    旧循环里建的 asyncpg 连接留在池里，下一个用例取到就是
    `RuntimeError: ... got Future ... attached to a different loop`。
    前置也释放，避免被排在"建了引擎但没释放"的文件之后（本文件首例即如此变红）。
    """
    from app.db.engine import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
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
    )


@pytest.fixture
async def store(pg_settings: Settings) -> AsyncIterator[PostgresUsageStore]:
    instance = PostgresUsageStore(pg_settings)
    try:
        yield instance
    finally:
        await _cleanup(pg_settings)


async def _cleanup(settings: Settings) -> None:
    async with platform_scope(settings=settings) as uow:
        await uow.execute(
            "DELETE FROM usage_event WHERE tenant_id = :t1 OR tenant_id = :t2",
            {"t1": TENANT_ID, "t2": OTHER_TENANT},
        )
        await uow.execute(
            "DELETE FROM usage_monthly WHERE tenant_id = :t1 OR tenant_id = :t2",
            {"t1": TENANT_ID, "t2": OTHER_TENANT},
        )


def _event(tenant: str, user: str = "usr_u1", **overrides: object) -> UsageEventRecord:
    """构造用量事件（`event_id` / `idem_key` 每条唯一）。

    `usage_event` 主键是 `(tenant_id, event_id)`，而 `ON CONFLICT` 只覆盖
    `(tenant_id, capability, idem_key)` 幂等键——`event_id` 共用空串/固定值时，
    同租户第二条写入直接撞 `usage_event_pkey`（IntegrityError），
    幂等子句拦不住主键冲突。
    """
    defaults: dict[str, object] = {
        "tenant_id": tenant,
        "user_id": user,
        "capability": "chat",
        "event_id": f"use_{uuid.uuid4().hex}",
        "idem_key": f"idem_{uuid.uuid4().hex[:8]}",
        "units": 100,
        "period": "2026-10",
    }
    defaults.update(overrides)
    return UsageEventRecord(**defaults)  # type: ignore[arg-type]


class TestUsageEvents:
    async def test_append_event(self, store: PostgresUsageStore) -> None:
        record = _event(TENANT_ID)
        result = await store.append(record)
        assert result is True

    async def test_monthly_rows(self, store: PostgresUsageStore) -> None:
        await store.append(_event(TENANT_ID, user="usr_a", units=50))
        await store.append(_event(TENANT_ID, user="usr_b", units=30))
        rows = await store.monthly_rows(TENANT_ID, "2026-10")
        total = sum(r.units for r in rows)
        assert total >= 80

    async def test_event_counts(self, store: PostgresUsageStore) -> None:
        await store.append(_event(TENANT_ID, user="usr_ec", capability="chat"))
        await store.append(_event(TENANT_ID, user="usr_ec", capability="doc_qa"))
        counts = await store.event_counts(TENANT_ID, "usr_ec", "2026-10")
        assert "chat" in counts or "doc_qa" in counts

    async def test_event_units_total(self, store: PostgresUsageStore) -> None:
        await store.append(_event(TENANT_ID, user="usr_eu", units=200))
        total = await store.event_units_total(TENANT_ID, "2026-10")
        assert total >= 200


class TestTenantIsolation:
    async def test_cross_tenant_isolation(self, store: PostgresUsageStore) -> None:
        await store.append(_event(TENANT_ID, user="usr_iso", units=100))
        other_total = await store.event_units_total(OTHER_TENANT, "2026-10")
        assert other_total == 0
        other_rows = await store.monthly_rows(OTHER_TENANT, "2026-10")
        assert other_rows == []
