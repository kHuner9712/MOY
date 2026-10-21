"""G-11 迁移幂等（真实 PostgreSQL **空库**，阶段二 B01）。

与 `tests/integration/test_migrate.py` 的分工：

- 那个文件的真实库用例跑在**已经由 initdb 初始化过**的主库上，此时基线版本
  `0001_phase2_init` 早已写进 `schema_migration`，两遍执行都是空转——
  能证明"不会重复执行"，但证明不了"从零开始真的建得出来"。
- 本文件先用超级用户**新建一个真正的空库**，把属主移交给应用角色，
  再连跑两遍全量迁移，并在第二遍之后核对表/套餐/权限点数量。
  这才是 `G-11` 判据里「空库连跑两遍」的本意。

夹具来源：`tests/integration/pg_fixture.py`（独立文件，不改 conftest.py）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import (
    AsyncpgMigrationDatabase,
    apply_migrations,
    check_migrations,
    resolve_sql_dir,
)
from tests.integration.pg_fixture import (
    create_empty_database,
    drop_database,
    to_async_dsn,
    to_raw_dsn,
)

# 夹具由独立文件提供：避免与 B04 批次的 conftest 改动互相踩踏
pytest_plugins = ["tests.integration.pg_fixture"]

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 空库名前缀（每个用例独立，避免互相踩）
SCRATCH_DB_PREFIX = "oa_qa_g11"

#: 迁移脚本仓库目录（CLI 用例的工作目录）
ADAPTER_ROOT = Path(__file__).resolve().parents[2]

#: 默认权限探针用的临时表名（模块常量，非外部输入）
PROBE_TABLE = "probe_default_priv"

#: 判据阈值（qa-phase2-test-plan.md §2.1 G-11）
EXPECTED_TABLES = 25
EXPECTED_PLANS = 3
EXPECTED_PERMISSIONS = 20


async def _migrate(dsn: str) -> Any:
    """构造指向空库的迁移访问对象。

    Args:
        dsn: 空库连接串。

    Returns:
        Any: `AsyncpgMigrationDatabase` 实例。
    """
    return AsyncpgMigrationDatabase(to_raw_dsn(dsn))


async def _counts(dsn: str) -> tuple[int, int, int]:
    """统计迁移后的关键对象数量。

    Args:
        dsn: 空库连接串。

    Returns:
        tuple[int, int, int]: 表数、套餐数、权限点数。
    """
    engine = create_async_engine(to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            tables = (
                await connection.execute(
                    sql_text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).scalar()
            plans = (
                await connection.execute(sql_text("SELECT count(*) FROM billing_plan"))
            ).scalar()
            permissions = (
                await connection.execute(sql_text("SELECT count(*) FROM permission_catalog"))
            ).scalar()
    finally:
        await engine.dispose()
    return int(tables), int(plans), int(permissions)


async def test_g11_apply_twice_on_empty_database_is_idempotent(b01_superuser_dsn: str) -> None:
    """空库连跑两遍全量迁移：版本集不变、无漂移、无待应用，且数量达标。"""
    name = f"{SCRATCH_DB_PREFIX}_apply"
    dsn = await create_empty_database(b01_superuser_dsn, name)
    try:
        database = await _migrate(dsn)
        try:
            await apply_migrations(database)
            first_applied = await database.fetch_applied()
            await apply_migrations(database)
            second_applied = await database.fetch_applied()

            assert first_applied == second_applied, (
                f"第二遍迁移改动了版本表：{first_applied} → {second_applied}"
            )
            assert first_applied, "空库跑完迁移后版本表为空，迁移未真正执行"

            plan = await check_migrations(database)
            assert plan.is_consistent, f"出现 checksum 漂移：{plan.drifted}"
            assert plan.pending == [], "两遍之后仍有待应用版本"
        finally:
            await database.close()

        tables, plans, permissions = await _counts(dsn)
    finally:
        await drop_database(b01_superuser_dsn, name)

    assert tables == EXPECTED_TABLES, f"迁移后表数为 {tables}，期望 {EXPECTED_TABLES}"
    assert plans == EXPECTED_PLANS, f"迁移后套餐数为 {plans}，期望 {EXPECTED_PLANS}"
    assert permissions == EXPECTED_PERMISSIONS, (
        f"迁移后权限点数为 {permissions}，期望 {EXPECTED_PERMISSIONS}"
    )


async def test_g11_second_apply_reports_nothing_pending(b01_superuser_dsn: str) -> None:
    """第一遍应真的应用基线；第二遍的待应用清单必须为空。"""
    name = f"{SCRATCH_DB_PREFIX}_twice"
    dsn = await create_empty_database(b01_superuser_dsn, name)
    try:
        database = await _migrate(dsn)
        try:
            first = await apply_migrations(database)
            second = await apply_migrations(database)
        finally:
            await database.close()
    finally:
        await drop_database(b01_superuser_dsn, name)

    assert [item.version for item in first.pending], "第一遍没有应用任何版本（空库本应全量应用）"
    assert [item.version for item in second.pending] == [], (
        f"第二遍仍在应用版本：{[item.version for item in second.pending]}"
    )
    assert second.checked >= 1, "第二遍未校验到任何已应用版本"


async def test_g11_migrated_tables_stay_forced_rls_even_for_owner(b01_superuser_dsn: str) -> None:
    """迁移产出的表由应用角色**自己拥有**，无租户作用域时仍必须 0 行。

    这条专打「表所有者默认绕过 RLS」：只有 `FORCE ROW LEVEL SECURITY` 能压住属主，
    漏了它的话，应用角色建的表对应用角色就是裸奔——而 G-11 的数量断言看不出来。
    """
    name = f"{SCRATCH_DB_PREFIX}_owner"
    dsn = await create_empty_database(b01_superuser_dsn, name)
    try:
        database = await _migrate(dsn)
        try:
            await apply_migrations(database)
        finally:
            await database.close()

        engine = create_async_engine(
            to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True
        )
        try:
            async with engine.connect() as connection:
                owner = (
                    await connection.execute(
                        sql_text(
                            "SELECT pg_get_userbyid(relowner) FROM pg_class "
                            "WHERE relname = 'knowledge_base_meta' AND relkind = 'r'"
                        )
                    )
                ).scalar()
                forced = (
                    await connection.execute(
                        sql_text(
                            "SELECT bool_and(relforcerowsecurity) FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                            "AND c.relname <> 'schema_migration'"
                        )
                    )
                ).scalar()
                leaked = (
                    await connection.execute(sql_text("SELECT count(*) FROM knowledge_base_meta"))
                ).scalar()
                seeded = (
                    await connection.execute(sql_text("SELECT count(*) FROM permission_catalog"))
                ).scalar()
        finally:
            await engine.dispose()
    finally:
        await drop_database(b01_superuser_dsn, name)

    assert owner == "officeai_app", f"迁移产出的表属主应为 officeai_app，实际 {owner}"
    assert forced is True, "迁移产出的业务表未全部开启 FORCE ROW LEVEL SECURITY"
    assert leaked == 0, f"表所有者仍能看到 {leaked} 行业务数据，FORCE RLS 未生效"
    assert seeded == EXPECTED_PERMISSIONS, "迁移后共享目录表读不到平台种子数据"


async def test_g11_cli_check_then_apply_on_empty_database(b01_superuser_dsn: str) -> None:
    """CLI 行为：空库 `--check` 列出待应用基线；`--apply` 后 `--check` 报无待应用。"""
    name = f"{SCRATCH_DB_PREFIX}_cli"
    dsn = await create_empty_database(b01_superuser_dsn, name)

    env = {
        **os.environ,
        "OA_DATABASE_URL": to_raw_dsn(dsn),
        "OA_DB_SQL_DIR": str(resolve_sql_dir()),
        "OA_ENV": "test",
        # 迁移 CLI 硬性要求业务库模式为 postgres，否则直接 30004
        "OA_BUSINESS_STORE": "postgres",
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        """执行一次迁移 CLI。

        Args:
            *args: CLI 参数。

        Returns:
            subprocess.CompletedProcess[str]: 进程结果。
        """
        return subprocess.run(  # noqa: S603
            [sys.executable, "-m", "app.db.migrate", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ADAPTER_ROOT),
            check=False,
        )

    try:
        before = run("--check")
        applied = run("--apply")
        after = run("--check")
    finally:
        await drop_database(b01_superuser_dsn, name)

    assert before.returncode == 0, f"[--check] 空库退出码 {before.returncode}：{before.stderr}"
    assert "0001_phase2_init" in before.stdout, f"[--check] 未列出待应用基线：{before.stdout}"

    assert applied.returncode == 0, f"[--apply] 退出码 {applied.returncode}：{applied.stderr}"
    assert "0001_phase2_init" in applied.stdout, f"[--apply] 未应用基线：{applied.stdout}"

    assert after.returncode == 0, f"[--check] 应用后退出码 {after.returncode}：{after.stderr}"
    assert "无待应用迁移" in after.stdout, f"[--check] 应用后仍报待应用：{after.stdout}"


async def test_g11_default_privileges_cover_future_tables(
    b01_superuser_engine: Any,
    b01_engine: Any,
) -> None:
    """`deploy/init-app-role.sql` 的 `ALTER DEFAULT PRIVILEGES` 必须真的生效。

    后续批次的新表由迁移脚本（以超级用户身份）创建；若默认权限没生效，
    这些新表对应用角色就是"建得出来、访问不了"，上线即 403。
    G-11 的表数量断言看不出这种失败，必须单列一条。
    """
    async with b01_superuser_engine.connect() as connection:
        await connection.execute(sql_text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
        await connection.execute(sql_text(f"CREATE TABLE {PROBE_TABLE} (id int)"))
        await connection.commit()

    try:
        async with b01_engine.connect() as connection:
            await connection.execute(sql_text(f"INSERT INTO {PROBE_TABLE} VALUES (1)"))  # noqa: S608
            await connection.commit()
            count = (
                await connection.execute(sql_text(f"SELECT count(*) FROM {PROBE_TABLE}"))  # noqa: S608
            ).scalar()
    finally:
        async with b01_superuser_engine.connect() as connection:
            await connection.execute(sql_text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
            await connection.commit()

    assert count == 1, f"应用角色无法读写超级用户新建的表，默认权限失效（count={count}）"
