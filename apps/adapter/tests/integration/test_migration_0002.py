"""0002 迁移（支付回调幂等索引「升级路径自检」）测试（阶段二 B09 chore）。

三条路径都要真跑（`--run-pg` 启用）：

- **空库（基线 + 0002）** `test_0002_on_fresh_database`：两遍 apply 幂等；
  索引存在且为 UNIQUE；版本已登记。
- **已初始化库**（表属主是 initdb 超级用户）
  `test_0002_is_privilege_safe_on_initialized_database`：应用角色能通过，
  不允许出现 `must be owner of table`。
- **约束缺失** `test_0002_fails_loud_when_unique_index_missing`：
  迁移必须失败并给出可执行的 DDL 指引。

第 2 条是本迁移的设计动因：已初始化库里 `payment_callback_log` 属主是 initdb 的
超级用户，应用角色执行任何 `CREATE INDEX` 都会 `InsufficientPrivilegeError`；
因此 0002 是「只读对账 + 失败即响」，不做 DDL（详见迁移文件头注释）。

⚠️ 本迁移**不是缺陷修复**：索引在基线里随建表创建，SaaS 与私有化两条 initdb
编排都挂了基线，因此首建库一定已有该索引。它只覆盖「存量库/私有化现场被人工
改过」的升级路径。
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
    to_async_dsn,
    to_raw_dsn,
)

pytest_plugins = ["tests.integration.pg_fixture"]

pytestmark = [pytest.mark.integration, pytest.mark.pg]

#: 空库名前缀（每例独立）
SCRATCH_DB_PREFIX = "oa_qa_m0002"

#: 迁移版本号
VERSION = "0002_callback_idempotency_selfcheck"

#: 迁移脚本路径（`deploy/migrations/`）
MIGRATION_PATH = resolve_sql_dir() / "migrations" / f"{VERSION}.sql"

#: 唯一索引名（与基线和迁移完全一致）
INDEX_NAME = "uq_callback_external"


async def _index_def(dsn: str) -> str | None:
    """读取目标索引定义。

    Args:
        dsn: 连接串。

    Returns:
        str | None: `indexdef`；不存在返回 None。
    """
    engine = create_async_engine(to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            return (
                await connection.execute(
                    sql_text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = 'payment_callback_log' "
                        "AND indexname = :name"
                    ),
                    {"name": INDEX_NAME},
                )
            ).scalar()
    finally:
        await engine.dispose()


async def _applied_versions(dsn: str) -> set[str]:
    """读取已登记的迁移版本。

    Args:
        dsn: 连接串。

    Returns:
        set[str]: 版本集合。
    """
    engine = create_async_engine(to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(sql_text("SELECT version FROM schema_migration"))
            ).scalars()
            return {str(item) for item in rows}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_0002_on_fresh_database(b01_superuser_dsn: str) -> None:
    """空库路径：基线 + 0002 连跑两遍都成功，索引唯一且版本已登记。"""
    name = f"{SCRATCH_DB_PREFIX}_fresh"
    dsn = await create_empty_database(b01_superuser_dsn, name)
    try:
        database = AsyncpgMigrationDatabase(to_raw_dsn(dsn))
        try:
            first = await apply_migrations(database)
            second = await apply_migrations(database)
        finally:
            await database.close()

        assert VERSION in {item.version for item in first.pending}
        assert second.pending == [], "第二遍不得再有待应用迁移"

        index_def = await _index_def(dsn)
        assert index_def is not None, f"迁移后仍缺索引 {INDEX_NAME}"
        assert index_def.startswith("CREATE UNIQUE INDEX"), index_def
        assert "(channel, external_txn_id)" in index_def.replace("\n", " ")
        assert VERSION in await _applied_versions(dsn)
    finally:
        await drop_database(b01_superuser_dsn, name)


@pytest.mark.asyncio
async def test_0002_is_privilege_safe_on_initialized_database(b01_app_dsn: str) -> None:
    """已初始化库：应用角色（非表属主）也能通过 0002，绝不出现 `must be owner`。

    这条用例锁死「迁移不得要求 DDL 权限」的设计：
    若有人把 0002 改回 `CREATE UNIQUE INDEX ...`，本用例会以
    `InsufficientPrivilegeError: must be owner of table` 立即变红。
    """
    database = AsyncpgMigrationDatabase(to_raw_dsn(b01_app_dsn))
    try:
        # 本用例只关心「应用角色能否通过」：不得抛 InsufficientPrivilegeError。
        # 是否 pending 取决于该库此前是否已应用过（空转/首次应用都合法），
        # 幂等性由 test_0002_on_fresh_database 的「第二遍 pending == []」严格覆盖。
        await apply_migrations(database)
    finally:
        await database.close()

    index_def = await _index_def(b01_app_dsn)
    assert index_def is not None
    assert index_def.startswith("CREATE UNIQUE INDEX")
    assert VERSION in await _applied_versions(b01_app_dsn)


@pytest.mark.asyncio
async def test_0002_fails_loud_when_unique_index_missing(b01_superuser_dsn: str) -> None:
    """约束缺失时迁移必须**失败**，并给出需要 DBA 执行的 DDL 原文。"""
    name = f"{SCRATCH_DB_PREFIX}_missing"
    dsn = await create_empty_database(b01_superuser_dsn, name)
    try:
        engine = create_async_engine(
            to_async_dsn(dsn), pool_size=1, max_overflow=0, pool_pre_ping=True
        )
        try:
            async with engine.connect() as connection:
                # 故意建一张「有表无唯一索引」的同名表（模拟历史环境被手工改坏）
                await connection.execute(
                    sql_text(
                        "CREATE TABLE payment_callback_log ("
                        " channel TEXT NOT NULL, external_txn_id TEXT NOT NULL)"
                    )
                )
                await connection.commit()
                script = MIGRATION_PATH.read_text(encoding="utf-8")
                with pytest.raises(DBAPIError) as excinfo:
                    await connection.execute(sql_text(script))
                await connection.rollback()
        finally:
            await engine.dispose()

        message = str(excinfo.value)
        assert "uq_callback_external" in message
        assert "CREATE UNIQUE INDEX" in message, "失败信息必须给出可执行的 DDL 指引"
    finally:
        await drop_database(b01_superuser_dsn, name)
