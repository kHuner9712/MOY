"""操作记录导出的 PG 回归（矩阵第 4 项）。

只在带库 CI 跑（`--run-pg` + `OA_TEST_DATABASE_URL`）：

1. 键集分页跨页不重不漏（同一时间戳的多行按 `id` 稳定排序）；
2. `tenant_scope` + SQL `tenant_id` 双重过滤：A 租户导不出 B 租户的行；
3. RLS 留存：`audit_log` 仍是 ENABLE + FORCE，`p_tenant_isolation` 存在；
4. 迁移 0016 的键集索引存在；
5. 导出留痕不进入导出集。

缺库时 skip，绝不假绿。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.audit_actions import ACTION_AUDIT_LOG_EXPORT
from app.core.config import Settings, reset_settings_cache
from app.db.repositories.audit_repo import (
    AuditQuery,
    count_audit_logs,
    iter_audit_export,
)
from app.db.session import platform_scope, tenant_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

TENANT_A = f"tnt_pg_audit_exp_a_{uuid.uuid4().hex[:8]}"
TENANT_B = f"tnt_pg_audit_exp_b_{uuid.uuid4().hex[:8]}"
START = "2026-10-01T00:00:00Z"
END = "2026-11-06T00:00:00Z"


@pytest.fixture(autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎。"""
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()
    reset_settings_cache()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """PG 测试配置。"""
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="pg-audit-export-secret",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
        audit_log_export_enabled=True,
    )


@pytest.fixture
async def seeded(pg_settings: Settings) -> AsyncIterator[None]:
    """写入两租户记录（A 租户 5 条、其中 2 条同一时间戳 + 1 条导出留痕；B 租户 2 条）。"""
    rows = [
        (TENANT_A, "kb.create", "2026-10-10T00:00:00Z"),
        (TENANT_A, "kb.update", "2026-10-11T00:00:00Z"),
        (TENANT_A, "kb.delete", "2026-10-12T00:00:00Z"),
        (TENANT_A, "doc.upload", "2026-10-12T00:00:00Z"),
        (TENANT_A, ACTION_AUDIT_LOG_EXPORT, "2026-10-13T00:00:00Z"),
        (TENANT_B, "kb.create", "2026-10-10T00:00:00Z"),
        (TENANT_B, "kb.update", "2026-10-11T00:00:00Z"),
    ]
    async with platform_scope(pg_settings) as uow:
        await uow.execute(
            "DELETE FROM audit_log WHERE tenant_id IN (:a, :b)", {"a": TENANT_A, "b": TENANT_B}
        )
        for tenant, action, occurred in rows:
            await uow.execute(
                """
                INSERT INTO audit_log
                    (tenant_id, actor_id, action, resource_type, resource_id, result,
                     actor_role, action_display_name, change_summary, occurred_at, trace_id)
                VALUES
                    (:tenant_id, 'usr_pg', :action, 'kb', 'kb_1', 'success',
                     'org_admin', :action, '{"name": "制度库"}'::jsonb, :occurred_at, 'trc_pg')
                """,
                {"tenant_id": tenant, "action": action, "occurred_at": occurred},
            )
    try:
        yield
    finally:
        async with platform_scope(pg_settings) as uow:
            await uow.execute(
                "DELETE FROM audit_log WHERE tenant_id IN (:a, :b)", {"a": TENANT_A, "b": TENANT_B}
            )


@pytest.fixture
async def unseeded(pg_settings: Settings) -> AsyncIterator[None]:
    """清空两租户记录（不预置数据，供索引/RLS 结构断言使用）。"""
    async with platform_scope(pg_settings) as uow:
        await uow.execute(
            "DELETE FROM audit_log WHERE tenant_id IN (:a, :b)", {"a": TENANT_A, "b": TENANT_B}
        )
    yield
    async with platform_scope(pg_settings) as uow:
        await uow.execute(
            "DELETE FROM audit_log WHERE tenant_id IN (:a, :b)", {"a": TENANT_A, "b": TENANT_B}
        )


def _query(**overrides: object) -> AuditQuery:
    """构造导出查询条件。"""
    values: dict[str, object] = {"start_at": START, "end_at": END}
    values.update(overrides)
    return AuditQuery(**values)  # type: ignore[arg-type]


async def test_keyset_pagination_is_stable_and_complete(
    pg_settings: Settings, seeded: None
) -> None:
    """每页 2 条翻完 A 租户：4 条业务记录，不重不漏，导出留痕被排除。"""
    assert count_audit_logs(pg_settings, TENANT_A, _query()) == 4
    collected: list[dict[str, object]] = []
    async for row in iter_audit_export(pg_settings, TENANT_A, _query(), page_size=2):
        collected.append(row)
    assert len(collected) == 4
    ids = [int(row["id"]) for row in collected]  # type: ignore[arg-type]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(row["tenant_id"] == TENANT_A for row in collected)
    assert all(row["action"] != ACTION_AUDIT_LOG_EXPORT for row in collected)


async def test_tenant_scope_and_rls_block_cross_tenant(pg_settings: Settings, seeded: None) -> None:
    """A 租户导出看不到 B 租户；B 租户在自己的事务里也只能看到 B 的行。"""
    collected = [
        row async for row in iter_audit_export(pg_settings, TENANT_A, _query(), page_size=50)
    ]
    assert {row["tenant_id"] for row in collected} == {TENANT_A}

    async with tenant_scope(TENANT_B, read_only=True, settings=pg_settings) as uow:
        visible = await uow.fetch_all("SELECT tenant_id FROM audit_log ORDER BY id")
    assert {row["tenant_id"] for row in visible} == {TENANT_B}


async def test_audit_log_keeps_force_rls_and_policy(pg_settings: Settings, unseeded: None) -> None:
    """迁移 0016 不得削弱隔离：ENABLE + FORCE + 策略仍在。"""
    async with platform_scope(pg_settings) as uow:
        flags = await uow.fetch_all(
            """
            SELECT c.relrowsecurity AS enabled, c.relforcerowsecurity AS forced
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'audit_log'
            """
        )
        policies = await uow.fetch_all(
            """
            SELECT policyname FROM pg_policies
            WHERE schemaname = 'public' AND tablename = 'audit_log'
            """
        )
    assert flags and all(row["enabled"] and row["forced"] for row in flags)
    assert "p_tenant_isolation" in {row["policyname"] for row in policies}


async def test_export_keyset_index_exists(pg_settings: Settings, unseeded: None) -> None:
    """迁移 0016 新建的键集分页索引存在。"""
    async with platform_scope(pg_settings) as uow:
        present = await uow.fetch_scalar(
            "SELECT to_regclass('public.idx_audit_log_tenant_time_id') IS NOT NULL"
        )
    assert present is True
