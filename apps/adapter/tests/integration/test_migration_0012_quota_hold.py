"""0012 迁移（额度挂起列）测试：新装库零 DDL、应用角色可执行、缺列时 fail-closed。

三条路径（`--run-pg` 启用）：

- **已初始化库 + 应用角色** `test_0012_is_privilege_safe_on_initialized_database`：
  这是本迁移的**设计动因**。真实部署的迁移 Job 用应用 DSN 执行
  （`deploy/init-schema-phase4.sql` 文件头写明：`officeai_app` 在 `public` 上没有
  CREATE 权限），所以列必须由 initdb 的超级用户先建好，0012 才能走「零 DDL」。
  若有人把 phase4 里那两列删掉、只留 0012 的 `ALTER TABLE`，
  本用例会以 `insufficient_privilege`/`must be owner of table` 立即变红。
- **幂等** `test_0012_second_apply_is_noop`：第二遍 `--apply` 的 `pending` 必须为空
  （证明守卫真的走了"列已存在 → 零 DDL"分支，而不是每次重放 ALTER）。
- **fail-closed** `test_0012_fails_loud_when_columns_missing_and_not_owner`：
  存量库（表属主是超级用户）缺列时，应用角色执行必须**大声失败**并给出
  「请由表属主/DBA 执行」的指引——不允许被改成"无权限只告警"。

第 1 条是 0010 与 0012 各踩过一次的那个坑的**回归锁**：0010（`background_job`）
当初也是"迁移自己建表"在应用角色下必失败，后来靠 `init-schema-phase4.sql` 建表解决。
没有这条用例，0013 还会踩第三次。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import AsyncpgMigrationDatabase, apply_migrations, resolve_sql_dir
from tests.integration.pg_fixture import (
    create_empty_database,
    drop_database,
    scratch_name,
    to_async_dsn,
    to_raw_dsn,
)

pytest_plugins = ["tests.integration.pg_fixture"]

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 临时库名前缀（fail-closed 用例用，避免污染基线库）
SCRATCH_DB_PREFIX = "oa_qa_m0012"

#: 迁移版本号
VERSION = "0012_quota_hold"

#: 迁移脚本路径（`deploy/migrations/`）
MIGRATION_PATH = resolve_sql_dir() / "migrations" / f"{VERSION}.sql"

#: 受本迁移影响的两张表
TABLES = ("document_meta", "plugin_task")

#: 部分索引名
INDEXES = ("idx_document_meta_hold", "idx_plugin_task_hold")


async def _query(dsn: str, statement: str) -> list[tuple[object, ...]]:
    """执行只读查询并返回全部行。

    Args:
        dsn: 连接串。
        statement: SQL 语句。

    Returns:
        list[tuple[object, ...]]: 结果行。
    """
    engine = create_async_engine(to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(sql_text(statement))
            return [tuple(row) for row in result.fetchall()]
    finally:
        await engine.dispose()


async def _hold_columns(dsn: str) -> dict[str, tuple[str, str | None]]:
    """读取两表的 `hold_reason` 列定义。

    Args:
        dsn: 连接串。

    Returns:
        dict[str, tuple[str, str | None]]: 表名 → (类型, 列默认值)。
    """
    rows = await _query(
        dsn,
        "SELECT table_name, data_type, column_default "
        "  FROM information_schema.columns "
        " WHERE table_schema = 'public' AND column_name = 'hold_reason'",
    )
    return {str(row[0]): (str(row[1]), None if row[2] is None else str(row[2])) for row in rows}


async def _check_constraints(dsn: str) -> set[str]:
    """读取两张表上与 `hold_reason` 相关的 CHECK 约束名。

    Args:
        dsn: 连接串。

    Returns:
        set[str]: 约束名集合（`pg_constraint.conname`）。
    """
    rows = await _query(
        dsn,
        "SELECT c.conname "
        "  FROM pg_constraint c "
        "  JOIN pg_class t ON t.oid = c.conrelid "
        " WHERE t.relname IN ('document_meta', 'plugin_task') "
        "   AND c.contype = 'c' "
        "   AND pg_get_constraintdef(c.oid) LIKE '%hold_reason%'",
    )
    return {str(row[0]) for row in rows}


async def _index_names(dsn: str) -> set[str]:
    """读取部分索引是否存在。

    Args:
        dsn: 连接串。

    Returns:
        set[str]: 命中的索引名。
    """
    rows = await _query(
        dsn,
        "SELECT indexname FROM pg_indexes "
        " WHERE schemaname = 'public' AND indexname LIKE 'idx_%_hold'",
    )
    return {str(row[0]) for row in rows}


@pytest.mark.asyncio
async def test_0012_is_privilege_safe_on_initialized_database(b01_app_dsn: str) -> None:
    """已初始化库：应用角色（非表属主）也能通过 0012。

    这是 0010 那个坑的回归锁：列由 `init-schema-phase4.sql` 的超级用户建好，
    迁移只做「零 DDL」的守卫判定，因此应用角色不需要表属主权限。
    """
    database = AsyncpgMigrationDatabase(to_raw_dsn(b01_app_dsn))
    try:
        # 只关心"能否通过"：不得抛 insufficient_privilege / must be owner。
        await apply_migrations(database)
    finally:
        await database.close()

    columns = await _hold_columns(b01_app_dsn)
    assert set(columns) == set(TABLES), f"缺 hold_reason 列：{sorted(set(TABLES) - set(columns))}"
    for table, (data_type, default) in columns.items():
        assert data_type == "text", f"{table}.hold_reason 类型应为 text，实际 {data_type}"
        # `NOT NULL DEFAULT ''` 是本迁移的核心约定：正因为它有默认值，
        # 才可以把该列从所有 INSERT 列名列表里省掉（R7 视为 omittable、G-SQL arity 不变）。
        assert default is not None and "''" in default, (
            f"{table}.hold_reason 必须有默认值 ''，实际 {default!r}"
        )

    constraints = await _check_constraints(b01_app_dsn)
    assert len(constraints) == len(TABLES), (
        f"两表都应有 hold_reason 的 CHECK，实际 {sorted(constraints)}"
    )
    assert await _index_names(b01_app_dsn) >= set(INDEXES), "两条部分索引必须存在"


@pytest.mark.asyncio
async def test_0012_second_apply_is_noop(b01_app_dsn: str) -> None:
    """第二遍 `--apply` 的 pending 必须为空（守卫走「列已存在 → 零 DDL」）。"""
    database = AsyncpgMigrationDatabase(to_raw_dsn(b01_app_dsn))
    try:
        await apply_migrations(database)
        second = await apply_migrations(database)
    finally:
        await database.close()

    assert VERSION not in [str(item.version) for item in second.pending], (
        "0012 第二遍仍出现在 pending：说明守卫没有识别「列已存在」，"
        "会每次重放 ALTER TABLE（在应用角色下必然失败）"
    )


@pytest.mark.asyncio
async def test_0012_fails_loud_when_columns_missing_and_not_owner(
    b01_superuser_dsn: str,
) -> None:
    """存量库缺列 + 非表属主 → 必须大声失败，不许静默放行。

    模拟真实存量库：表由超级用户建（属主=超级用户），应用角色执行 0012。
    这条用例锁死「fail-closed」语义——若有人把守卫改成"无权限就只告警"，
    这里会因为不抛异常而变红。
    """
    name = scratch_name(SCRATCH_DB_PREFIX, "legacy")
    dsn = await create_empty_database(b01_superuser_dsn, name)
    try:
        # 用**超级用户**建两张最小表：属主因此是超级用户，与应用角色不同。
        engine = create_async_engine(
            to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True
        )
        try:
            async with engine.connect() as connection:
                for table in TABLES:
                    await connection.execute(
                        sql_text(
                            f"CREATE TABLE IF NOT EXISTS {table} ("
                            " tenant_id TEXT NOT NULL, row_id TEXT NOT NULL)"
                        )
                    )
                await connection.commit()
        finally:
            await engine.dispose()

        # 该表由超级用户所有；用应用角色执行 0012 必须失败。
        # `create_empty_database` 返回的是**应用角色**的 DSN（库属主是应用角色，
        # 但表属主是超级用户 —— 这正是存量库的形态）。
        app_engine = create_async_engine(
            to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True
        )
        try:
            script = MIGRATION_PATH.read_text(encoding="utf-8")
            async with app_engine.connect() as connection:
                with pytest.raises(DBAPIError) as excinfo:
                    await connection.execute(sql_text(script))
                await connection.rollback()
        finally:
            await app_engine.dispose()
    finally:
        await drop_database(b01_superuser_dsn, name)

    message = str(excinfo.value)
    assert "0012" in message, f"错误信息应指明是 0012：{message}"
    assert "表属主" in message or "DBA" in message, f"错误信息应给出可照做的指引：{message}"
