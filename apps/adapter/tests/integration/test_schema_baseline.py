"""业务库结构基线（G-11）：表清单、RLS 强制状态、平台种子数据数量。

对应 `docs/qa-phase2-test-plan.md` §2.1 G-11 与 §6.3「端点基线之外的结构基线」。

为什么单独一个文件：`tests/integration/test_migrate.py` 验证的是**迁移机制**
（版本比对、checksum 漂移、幂等重跑）；本文件验证的是**迁移结果**——
库里到底有没有这 25 张表、业务表是否真的 `FORCE ROW LEVEL SECURITY`、
套餐与权限点是否按 PRD 预置。两者失败原因完全不同，混在一起会互相掩盖。

基线一旦变化（如新增迁移），必须同步 `docs/architecture-phase2.md` §3.1 与本文件，
否则本文件会红——这是有意的，防止"悄悄多出一张表"。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.db.migrate import BASELINE_VERSION

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 架构文档 §3.1 冻结的 25 张表（含 schema_migration 与扩展后的 audit_log）
EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "schema_migration",
        "tenant",
        "knowledge_base_meta",
        "knowledge_base_grant",
        "document_meta",
        "segment_map",
        "credential_store",
        "tool_connection",
        "tool_execution",
        "usage_event",
        "usage_monthly",
        "billing_plan",
        "billing_subscription",
        "billing_order",
        "billing_invoice",
        "payment_callback_log",
        "department",
        "department_member",
        "role",
        "role_permission",
        "permission_catalog",
        "member_role",
        "audit_log",
        "ops_settings",
        "integration_outbox",
        # 阶段三（deploy/init-schema-phase3.sql 9 张）：B17/B18/B19 陆续建出，
        # 漏登记会让"多出未登记的表"每批都红一次——新增表时必须同批改这里。
        "plugin_catalog",
        "plugin_version",
        "plugin_installation",
        "plugin_grant",
        "plugin_capability_policy",
        "plugin_review",
        "plugin_task",
        "plugin_task_step",
        "plugin_artifact",
    }
)

#: 启用「严格租户隔离」策略的表（`deploy/init-schema-phase2.sql` 第 14 节 strict_tables）
STRICT_TENANT_TABLES: frozenset[str] = frozenset(
    {
        "tenant",
        "knowledge_base_meta",
        "knowledge_base_grant",
        "document_meta",
        "segment_map",
        "credential_store",
        "tool_connection",
        "tool_execution",
        "usage_event",
        "usage_monthly",
        "billing_subscription",
        "billing_order",
        "billing_invoice",
        "payment_callback_log",
        "department",
        "department_member",
        "role",
        "role_permission",
        "member_role",
        "audit_log",
        "integration_outbox",
    }
)

#: 「平台共享目录」表：额外允许读取 `tenant_id='__platform__'` 的平台行
SHARED_CATALOG_TABLES: frozenset[str] = frozenset(
    {"permission_catalog", "billing_plan", "ops_settings"}
)

#: PRD 5.1 预置套餐数
EXPECTED_PLAN_COUNT = 3

#: PRD 6.2 权限点数（16 业务 + 4 运营）
EXPECTED_PERMISSION_COUNT = 20


async def test_public_schema_contains_exactly_expected_tables(pg_engine: Any) -> None:
    """public schema 的基础表集合必须与架构 §3.1 完全一致（多一张少一张都红）。"""
    async with pg_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                    )
                )
            )
            .scalars()
            .all()
        )

    actual = set(rows)
    missing = EXPECTED_TABLES - actual
    extra = actual - EXPECTED_TABLES

    assert not missing, f"缺少表：{sorted(missing)}（迁移未执行完整？）"
    assert not extra, (
        f"多出未登记的表：{sorted(extra)}；如属新增迁移，"
        "必须同步 architecture-phase2.md §3.1 与本文件基线"
    )


async def test_business_tables_force_row_level_security(pg_engine: Any) -> None:
    """严格租户表必须 `ENABLE` **且** `FORCE` RLS（FORCE 才能约束表所有者）。"""
    async with pg_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT c.relname AS name, c.relrowsecurity AS enabled, "
                        "       c.relforcerowsecurity AS forced "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                    )
                )
            )
            .mappings()
            .all()
        )

    flags = {row["name"]: (row["enabled"], row["forced"]) for row in rows}

    for table in sorted(STRICT_TENANT_TABLES):
        assert table in flags, f"表 {table} 不存在，无法检查 RLS"
        enabled, forced = flags[table]
        assert enabled is True, f"表 {table} 未启用 RLS"
        assert forced is True, f"表 {table} 只 ENABLE 未 FORCE，表所有者仍可绕过策略"


async def test_shared_catalog_tables_force_row_level_security(pg_engine: Any) -> None:
    """平台目录表同样必须 FORCE RLS（平台行通过策略豁免，而非关闭策略）。"""
    async with pg_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT c.relname AS name, c.relrowsecurity AS enabled, "
                        "       c.relforcerowsecurity AS forced "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                    )
                )
            )
            .mappings()
            .all()
        )

    flags = {row["name"]: (row["enabled"], row["forced"]) for row in rows}

    for table in sorted(SHARED_CATALOG_TABLES):
        enabled, forced = flags[table]
        assert (enabled, forced) == (True, True), f"表 {table} 的 RLS 未 FORCE"


async def test_platform_seed_counts(pg_engine: Any) -> None:
    """平台种子：3 个套餐 + 20 个权限点（PRD 5.1 / 6.2 的默认配置）。"""
    async with pg_engine.connect() as connection:
        plans = (
            await connection.execute(
                text("SELECT count(*) FROM billing_plan WHERE tenant_id = '__platform__'")
            )
        ).scalar()
        permissions = (
            await connection.execute(
                text("SELECT count(*) FROM permission_catalog WHERE tenant_id = '__platform__'")
            )
        ).scalar()

    assert plans == EXPECTED_PLAN_COUNT, f"套餐数应为 {EXPECTED_PLAN_COUNT}，实际 {plans}"
    assert permissions == EXPECTED_PERMISSION_COUNT, (
        f"权限点数应为 {EXPECTED_PERMISSION_COUNT}，实际 {permissions}"
    )


async def test_plan_codes_and_prices_match_prd(pg_engine: Any) -> None:
    """套餐编码与单价必须与 PRD 5.1 一致（0 / 39 / 99 元，按分存整数）。"""
    async with pg_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT plan_code, price_per_seat_month_cents, min_seats "
                        "FROM billing_plan WHERE tenant_id = '__platform__'"
                    )
                )
            )
            .mappings()
            .all()
        )

    actual = {row["plan_code"]: row for row in rows}
    assert set(actual) == {"free", "professional", "enterprise"}
    assert actual["free"]["price_per_seat_month_cents"] == 0
    assert actual["professional"]["price_per_seat_month_cents"] == 3900
    assert actual["enterprise"]["price_per_seat_month_cents"] == 9900
    assert actual["free"]["min_seats"] == 3, "免费版默认 3 席位（PRD 5.1）"


async def test_schema_migration_records_baseline(pg_engine: Any) -> None:
    """`schema_migration` 必须记录基线版本（否则下次启动会重跑全量 DDL）。"""
    async with pg_engine.connect() as connection:
        versions = (
            (await connection.execute(text("SELECT version FROM schema_migration"))).scalars().all()
        )

    assert BASELINE_VERSION in versions, (
        f"schema_migration 缺少基线版本 {BASELINE_VERSION}，实际记录：{list(versions)}"
    )
