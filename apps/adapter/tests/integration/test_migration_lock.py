"""迁移互斥锁与「DDL + 台账同事务」（阶段四 T4-01.4，架构 §10.3）。

**为什么需要一个双连接用例**：迁移锁这种东西，"单人跑一遍绿"完全不能说明问题——
锁只在**并发**下才有意义，而并发下的失败形态是"其中一方随机报错"或"DDL 执行了两次"，
两者都不会被顺序执行的用例看见。所以本用例同时开两个**独立连接**跑 `--apply`。

三条判据（缺一不可）：

1. **只执行一次 DDL**：探针脚本每执行一次就往 `demo_migration_lock` 插一行，
   结束后行数必须恰好 1（= 只有拿到锁的那一方真的执行了）；
2. **台账无重复**：`schema_migration` 里该版本恰好 1 行（PK 保证，但要断言，
   因为"重复执行"通常伴随 upsert 掩盖）；
3. **另一方等待后成功**：两个协程都正常返回，都不抛异常——第二方应当是
   「拿到锁 → 发现已应用 → 跳过」，而不是超时或报错。

为什么探针用 `pg_sleep`：让先拿到锁的一方在锁内停留足够久，制造真实竞争窗口。
没有 sleep 时两个协程可能串行完成，用例就变成"测了个寂寞"（假绿）。
"""

# ruff: noqa: S608 - 表名是本用例自己生成的随机标识（非外部输入），无注入面
from __future__ import annotations

import asyncio
import pathlib
import uuid

import asyncpg
import pytest

from app.db.migrate import (
    MIGRATION_LOCK_KEY,
    AsyncpgMigrationDatabase,
    apply_migrations,
    discover_migrations,
)

pytestmark = pytest.mark.integration

#: 属主角色（与 `deploy/init-app-role.sql` 一致）：探针表必须由它建，
#: 应用角色 `officeai_app` 在 `public` 上没有 CREATE 权限（实测：permission denied
#: for schema public）。这正好复现了客户现场的分工——DDL 归 DBA，应用只写数据。
OWNER_ROLE = "officeai"


def _owner_dsn(app_dsn: str) -> str:
    """按"同一口令换用户名"派生属主连接串（与 validate_migrations 同法）。

    Args:
        app_dsn: 应用角色连接串。

    Returns:
        str: 属主连接串。
    """
    head, _, tail = app_dsn.partition("://")
    credentials, _, host_part = tail.partition("@")
    _user, sep, password = credentials.partition(":")
    return f"{head}://{OWNER_ROLE}{sep}{password}@{host_part}"


def _probe_sql(table: str, marker: str) -> str:
    """构造探针迁移脚本（**不写 BEGIN/COMMIT**，由 migrate.py 包裹）。

    Args:
        table: 探针表名。
        marker: 本次运行的唯一标记（用于区分"跑了几次"）。

    Returns:
        str: 脚本内容。
    """
    # 只 INSERT 不 CREATE：应用角色对 public 无 CREATE 权限，而"探针表"由属主
    # 预先建好（见 `probe_dir` 夹具），这才符合真实分工。
    # sleep 是为了让先拿到锁的一方在锁内停留足够久，制造真实竞争窗口——
    # 没有它，两个协程可能串行完成，用例就变成假绿。
    return f"""-- probe migration (migration lock test)
DO $probe$
BEGIN
    PERFORM pg_sleep(0.6);
END $probe$;

INSERT INTO {table} (marker) VALUES ('{marker}');
"""


@pytest.fixture
def probe_dir(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """构造只含一个探针迁移的临时 SQL 目录。

    返回：
        `(目录, 版本号, 探针表名)`
    """
    version = f"9999_lock_probe_{uuid.uuid4().hex[:8]}"
    table = f"demo_migration_lock_{uuid.uuid4().hex[:8]}"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / f"{version}.sql").write_text(
        _probe_sql(table, uuid.uuid4().hex), encoding="utf-8"
    )
    return tmp_path, version, table


async def _create_probe_table(dsn: str, table: str) -> None:
    """由属主创建探针表（应用角色无 CREATE 权限）。

    Args:
        dsn: 应用角色 DSN（用于派生属主 DSN）。
        table: 表名。
    """
    connection = await asyncpg.connect(_owner_dsn(dsn))
    try:
        await connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (marker TEXT NOT NULL)")
    finally:
        await connection.close()


async def _cleanup(dsn: str, version: str, table: str) -> None:
    """清理探针产生的表与台账行（失败不掩盖主结论）。"""
    connection = await asyncpg.connect(_owner_dsn(dsn))
    try:
        await connection.execute(f"DROP TABLE IF EXISTS {table}")
        await connection.execute("DELETE FROM schema_migration WHERE version = $1", version)
    finally:
        await connection.close()


async def test_two_connections_apply_only_once(pg_dsn: str, probe_dir: tuple) -> None:
    """两个连接同时 `--apply`：只有一方执行 DDL，台账无重复，另一方等待后成功。"""
    sql_dir, version, table = probe_dir
    await _create_probe_table(pg_dsn, table)
    left = AsyncpgMigrationDatabase(pg_dsn)
    right = AsyncpgMigrationDatabase(pg_dsn)
    try:
        results = await asyncio.gather(
            apply_migrations(left, sql_dir),
            apply_migrations(right, sql_dir),
            return_exceptions=True,
        )
        for result in results:
            assert not isinstance(result, BaseException), f"并发 apply 抛异常：{result!r}"

        connection = await asyncpg.connect(pg_dsn)
        try:
            # ① 探针行数 = 1：探针脚本只被执行了一次
            rows = await connection.fetchval(f"SELECT count(*) FROM {table}")
            assert rows == 1, f"探针脚本被执行了 {rows} 次（迁移锁失效）"

            # ② 台账恰好 1 行
            recorded = await connection.fetchval(
                "SELECT count(*) FROM schema_migration WHERE version = $1", version
            )
            assert recorded == 1, f"台账出现 {recorded} 行（应为 1）"

            # ③ 锁已释放：能立刻取到同一把锁，说明没有残留的会话级锁
            acquired = await connection.fetchval(
                f"SELECT pg_try_advisory_lock({MIGRATION_LOCK_KEY})"
            )
            assert acquired is True, "迁移锁未释放（pg_advisory_lock 泄漏）"
            await connection.execute(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})")
        finally:
            await connection.close()
    finally:
        await left.close()
        await right.close()
        await _cleanup(pg_dsn, version, table)


async def test_applied_probe_is_skipped_inside_lock(pg_dsn: str, probe_dir: tuple) -> None:
    """已应用的版本再 `--apply` 一次：pending 为空，探针行数不变（锁内二次判定同样生效）。"""
    sql_dir, version, table = probe_dir
    await _create_probe_table(pg_dsn, table)
    database = AsyncpgMigrationDatabase(pg_dsn)
    try:
        first = await apply_migrations(database, sql_dir)
        assert [item.version for item in first.pending] == [version]

        second = await apply_migrations(database, sql_dir)
        assert second.pending == [], "已应用的版本不得再次进入 pending"

        connection = await asyncpg.connect(pg_dsn)
        try:
            rows = await connection.fetchval(f"SELECT count(*) FROM {table}")
            assert rows == 1, f"重跑导致探针脚本又执行了一次（{rows} 行）"
        finally:
            await connection.close()
    finally:
        await database.close()
        await _cleanup(pg_dsn, version, table)


def test_lock_key_is_a_stable_constant() -> None:
    """锁键必须是固定常量：改了它等于让新旧版本各锁各的，锁失去意义。"""
    assert MIGRATION_LOCK_KEY == 7326017


def test_discover_migrations_sees_probe(probe_dir: tuple) -> None:
    """探针目录只含 1 条迁移（保证上面的用例真的只测一个脚本）。"""
    sql_dir, version, _table = probe_dir
    found = discover_migrations(sql_dir)
    assert [item.version for item in found] == [version]
