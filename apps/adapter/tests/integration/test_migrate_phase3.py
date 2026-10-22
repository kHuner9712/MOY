# ruff: noqa: S608 - 断言 SQL 里的表名来自模块常量，不含外部输入
"""阶段三表结构迁移测试（阶段三 B17，`architecture-phase3.md` §4.6 / §10）。

三条路径（`--run-pg` 启用）：

1. **空库**：`init-schema-phase2.sql` → `init-schema-phase3.sql` → 再跑一遍 phase3，
   断言 9 张表存在、6 条目录种子、重复执行不报错且不改行数（幂等）；
2. **RLS**：7 张严格表 + 2 张共享目录表全部 `ENABLE` + **`FORCE`** ROW LEVEL SECURITY
   （`FORCE` 是关键：表所有者也要受限，否则迁移用超级用户跑完就等于没隔离）；
3. **对账迁移**：`deploy/migrations/0003_*.sql` 是**只读对账**（不建表），
   在已初始化库上应空转通过、在缺表库里应失败并给出可执行指引。

> **种子偏差（已在 commit message 与回执中说明）**：`plugin_version` 的 6 条快照
> 由运行期从声明文件幂等登记（sha256 与磁盘同源），`plugin_capability_policy`
> 的 3 行按租户在首次写入时生成——initdb 阶段没有租户，无法预置租户级行。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import AsyncpgMigrationDatabase, resolve_sql_dir
from tests.integration.pg_fixture import (
    create_empty_database,
    drop_database,
    to_async_dsn,
    to_raw_dsn,
)

pytest_plugins = ["tests.integration.pg_fixture"]

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 空库名前缀
SCRATCH_DB_PREFIX = "oa_qa_phase3"

#: 9 张阶段三表
PHASE3_TABLES = (
    "plugin_catalog",
    "plugin_version",
    "plugin_installation",
    "plugin_grant",
    "plugin_capability_policy",
    "plugin_review",
    "plugin_task",
    "plugin_task_step",
    "plugin_artifact",
)

#: 严格隔离表（7 张）
STRICT_TABLES = (
    "plugin_installation",
    "plugin_grant",
    "plugin_capability_policy",
    "plugin_review",
    "plugin_task",
    "plugin_task_step",
    "plugin_artifact",
)

#: 共享目录表（2 张，允许读 `__platform__` 平台行）
SHARED_TABLES = ("plugin_catalog", "plugin_version")

#: 表名 IN 列表（模块常量，非外部输入）
_TABLE_LIST = "','".join(PHASE3_TABLES)

#: 阶段三表数量断言 SQL
TABLES_COUNT_SQL = (
    "SELECT count(*) FROM information_schema.tables "
    f"WHERE table_schema = 'public' AND table_name IN ('{_TABLE_LIST}')"
)

#: 强制 RLS 检查 SQL
FORCE_RLS_SQL = (
    "SELECT c.relname, c.relforcerowsecurity FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
    f"AND c.relname IN ('{_TABLE_LIST}')"
)


@pytest.fixture
async def scratch_dsn(b01_superuser_dsn: str) -> AsyncIterator[str]:
    """建一个空库并在用例结束后删除。

    Args:
        b01_superuser_dsn: 超级用户连接串。

    Yields:
        AsyncIterator[str]: 空库连接串。
    """
    from app.db.engine import dispose_engine

    name = f"{SCRATCH_DB_PREFIX}_{uuid.uuid4().hex[:8]}"
    dsn = await create_empty_database(b01_superuser_dsn, name)
    try:
        yield dsn
    finally:
        await drop_database(b01_superuser_dsn, name)
        await dispose_engine()


async def _execute_script(dsn: str, path: Path) -> None:
    """执行一份 SQL 脚本（走迁移器的简单查询协议）。

    Args:
        dsn: 连接串。
        path: 脚本路径。
    """
    database = AsyncpgMigrationDatabase(to_raw_dsn(dsn))
    try:
        script = await asyncio.to_thread(path.read_text, encoding="utf-8")
        await database.execute_script(script)
    finally:
        await database.close()


async def _scalar(dsn: str, sql: str) -> int:
    """执行单值查询。

    Args:
        dsn: 连接串。
        sql: SQL。

    Returns:
        int: 标量值。
    """
    engine = create_async_engine(to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            return int((await connection.execute(sql_text(sql))).scalar() or 0)
    finally:
        await engine.dispose()


async def _rows(dsn: str, sql: str) -> list[tuple[str, bool]]:
    """执行两列查询（表名 + 是否强制 RLS）。

    Args:
        dsn: 连接串。
        sql: SQL。

    Returns:
        list[tuple[str, bool]]: 结果行。
    """
    engine = create_async_engine(to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(sql_text(sql))
            return [(str(row[0]), bool(row[1])) for row in result]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_phase3_schema_is_idempotent_and_complete(scratch_dsn: str) -> None:
    """空库跑 phase2 → phase3 两遍：9 表齐备、6 条目录种子、行数不因重跑变化。"""
    directory = resolve_sql_dir()
    await _execute_script(scratch_dsn, directory / "init-schema-phase2.sql")
    phase3 = directory / "init-schema-phase3.sql"
    await _execute_script(scratch_dsn, phase3)

    tables = await _scalar(scratch_dsn, TABLES_COUNT_SQL)
    assert tables == len(PHASE3_TABLES), f"阶段三表数量应为 {len(PHASE3_TABLES)}，实际 {tables}"
    assert await _scalar(scratch_dsn, "SELECT count(*) FROM plugin_catalog") == 6

    before = (
        await _scalar(scratch_dsn, "SELECT count(*) FROM plugin_catalog"),
        await _scalar(scratch_dsn, "SELECT count(*) FROM plugin_version"),
    )
    await _execute_script(scratch_dsn, phase3)
    after = (
        await _scalar(scratch_dsn, "SELECT count(*) FROM plugin_catalog"),
        await _scalar(scratch_dsn, "SELECT count(*) FROM plugin_version"),
    )
    assert after == before, "重复执行基线不得增删行（种子 ON CONFLICT DO NOTHING）"


@pytest.mark.asyncio
async def test_phase3_tables_force_rls(scratch_dsn: str) -> None:
    """9 张表全部开启 FORCE RLS（所有者亦受限，否则隔离形同虚设）。"""
    directory = resolve_sql_dir()
    await _execute_script(scratch_dsn, directory / "init-schema-phase2.sql")
    await _execute_script(scratch_dsn, directory / "init-schema-phase3.sql")

    rows = await _rows(scratch_dsn, FORCE_RLS_SQL)
    mapping = dict(rows)
    assert set(mapping) == set(PHASE3_TABLES)
    missing = [name for name, forced in mapping.items() if not forced]
    assert missing == [], f"以下表未开启强制行级隔离：{missing}"


@pytest.mark.asyncio
async def test_shared_catalog_rows_are_readable_across_tenants(scratch_dsn: str) -> None:
    """共享目录策略允许租户会话读到平台行（否则市场会空白）。"""
    directory = resolve_sql_dir()
    await _execute_script(scratch_dsn, directory / "init-schema-phase2.sql")
    await _execute_script(scratch_dsn, directory / "init-schema-phase3.sql")

    engine = create_async_engine(to_async_dsn(scratch_dsn), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as connection:
            await connection.execute(sql_text("SELECT set_config('app.tenant_id', 'tnt_x', true)"))
            count = (
                await connection.execute(
                    sql_text("SELECT count(*) FROM plugin_catalog WHERE status = 'available'")
                )
            ).scalar()
            assert int(count or 0) == 6, "租户会话应能读到 6 条平台目录行"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0003_migration_is_read_only_reconcile(scratch_dsn: str) -> None:
    """`0003` 只做对账：9 表齐备时通过；缺表时失败并给出可执行指引。"""
    directory = resolve_sql_dir()
    migration = directory / "migrations" / "0003_phase3_plugin_tables.sql"
    assert migration.is_file(), "缺少 0003 对账迁移"

    # 缺表 → 必须失败（fail-fast，而不是静默通过）
    with pytest.raises(Exception) as excinfo:
        await _execute_script(scratch_dsn, migration)
    assert "缺少阶段三数据表" in str(excinfo.value)

    # 补齐基线后 → 通过
    await _execute_script(scratch_dsn, directory / "init-schema-phase2.sql")
    await _execute_script(scratch_dsn, directory / "init-schema-phase3.sql")
    await _execute_script(scratch_dsn, migration)
