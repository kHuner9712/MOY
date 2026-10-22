"""迁移执行器测试（阶段二 B01，架构文档 3.7）。

两类用例：

1. **无需 PostgreSQL**：用 `FakeMigrationDatabase` 验证版本比对、checksum 漂移中止、
   幂等重跑（空库 → 全量 → 再跑一次无变更）。这些是「迁移机制」本身的验收点，
   必须能在离线环境跑，故不做真实连库。
2. **需要 PostgreSQL**：设置 `OA_TEST_DATABASE_URL` 后，用真实库跑
   「全量执行两遍（幂等）+ `--check` 通过」，与主理人实测口径一致；未设置则 `skip`
   （不允许 fail，见 B01 裁决 3）。
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.migrate import (
    BASELINE_FILENAME,
    BASELINE_VERSION,
    MANAGED_CHECKSUM_SENTINEL,
    Migration,
    apply_migrations,
    build_plan,
    check_migrations,
    checksum_of,
    discover_migrations,
    resolve_sql_dir,
    run_startup_migrations,
)

pytestmark = pytest.mark.integration

_BASELINE_SQL = """-- baseline
BEGIN;
CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY);
INSERT INTO schema_migration (version, checksum)
VALUES ('0001_phase2_init', 'sha256:managed-by-app-db-migrate')
ON CONFLICT (version) DO NOTHING;
COMMIT;
"""

_INCREMENT_ONE = """-- increment 1
BEGIN;
CREATE TABLE IF NOT EXISTS demo_add_one (id TEXT PRIMARY KEY);
COMMIT;
"""

_INCREMENT_TWO = """-- increment 2
BEGIN;
CREATE TABLE IF NOT EXISTS demo_add_two (id TEXT PRIMARY KEY);
COMMIT;
"""


class FakeMigrationDatabase:
    """迁移数据库测试替身（内存记录已应用版本）。"""

    def __init__(self, applied: dict[str, str] | None = None) -> None:
        """初始化。

        Args:
            applied: 初始已应用版本 → checksum。
        """
        self.applied: dict[str, str] = dict(applied or {})
        self.executed_scripts: list[str] = []
        self.closed = False

    async def fetch_applied(self) -> dict[str, str]:
        """读取已应用版本。"""
        return dict(self.applied)

    async def execute_script(self, sql: str) -> None:
        """记录脚本执行。"""
        self.executed_scripts.append(sql)

    async def record(self, version: str, checksum: str) -> None:
        """记录版本。"""
        self.applied[version] = checksum

    async def apply_migration(self, migration: Migration) -> bool:
        """执行迁移（内存替身：已应用则跳过，与 PG 实现的锁内判定同语义）。

        Args:
            migration: 待应用的迁移。

        Returns:
            bool: 真的执行了返回 True；已应用返回 False。
        """
        if migration.version in self.applied:
            return False
        self.executed_scripts.append(migration.path.read_text(encoding="utf-8"))
        self.applied[migration.version] = migration.checksum
        return True

    async def apply_rollback(self, version: str, sql: str) -> bool:
        """执行回滚（内存替身）。

        Args:
            version: 版本号。
            sql: 回滚脚本内容。

        Returns:
            bool: 执行了返回 True；版本未应用返回 False。
        """
        if version not in self.applied:
            return False
        self.executed_scripts.append(sql)
        self.applied.pop(version, None)
        return True

    async def close(self) -> None:
        """关闭连接。"""
        self.closed = True


@pytest.fixture
def sql_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """构造一个含基线 + 2 个增量的临时 SQL 目录。"""
    (tmp_path / BASELINE_FILENAME).write_text(_BASELINE_SQL, encoding="utf-8")
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0002_demo_add_one.sql").write_text(_INCREMENT_ONE, encoding="utf-8")
    (migrations / "0003_demo_add_two.sql").write_text(_INCREMENT_TWO, encoding="utf-8")
    return tmp_path


def test_discover_migrations_orders_baseline_first(sql_dir: pathlib.Path) -> None:
    """基线在前、增量按版本升序。"""
    found = discover_migrations(sql_dir)
    assert [item.version for item in found] == [
        BASELINE_VERSION,
        "0002_demo_add_one",
        "0003_demo_add_two",
    ]
    assert found[0].checksum.startswith("sha256:")
    assert found[0].path.name == BASELINE_FILENAME


def test_checksum_changes_with_content(tmp_path: pathlib.Path) -> None:
    """脚本内容变化必须导致 checksum 变化（漂移检测的基础）。"""
    path = tmp_path / "0002_x.sql"
    path.write_text("SELECT 1;\n", encoding="utf-8")
    first = checksum_of(path)
    path.write_text("SELECT 2;\n", encoding="utf-8")
    assert checksum_of(path) != first


def test_build_plan_lists_pending_and_ignores_baseline_sentinel() -> None:
    """已应用基线的哨兵 checksum 不算漂移；未应用增量进入 pending。"""
    available = [
        Migration("0001_phase2_init", pathlib.Path("a.sql"), "sha256:aaa"),
        Migration("0002_x", pathlib.Path("b.sql"), "sha256:bbb"),
    ]
    plan = build_plan(available, {BASELINE_VERSION: MANAGED_CHECKSUM_SENTINEL})
    assert [item.version for item in plan.pending] == ["0002_x"]
    assert plan.drifted == []
    assert plan.checked == 1
    assert plan.is_consistent


def test_build_plan_detects_checksum_drift() -> None:
    """已应用版本的文件被手工改动 → 漂移（启动必须失败）。"""
    available = [Migration("0002_x", pathlib.Path("b.sql"), "sha256:new")]
    plan = build_plan(available, {"0002_x": "sha256:old"})
    assert plan.is_consistent is False
    assert plan.drifted == [("0002_x", "sha256:old", "sha256:new")]


async def test_apply_then_reapply_is_idempotent(sql_dir: pathlib.Path) -> None:
    """空库 → 全量 → 再跑一次：第二次不执行任何脚本（幂等）。"""
    database = FakeMigrationDatabase()

    first = await apply_migrations(database, sql_dir)
    assert [item.version for item in first.pending] == [
        BASELINE_VERSION,
        "0002_demo_add_one",
        "0003_demo_add_two",
    ]
    assert len(database.executed_scripts) == 3

    second = await apply_migrations(database, sql_dir)
    assert second.pending == []
    assert len(database.executed_scripts) == 3, "幂等重跑不应重复执行脚本"


async def test_check_reports_pending_without_executing(sql_dir: pathlib.Path) -> None:
    """`--check` 只报告待应用版本，不执行任何 SQL。"""
    database = FakeMigrationDatabase()
    plan = await check_migrations(database, sql_dir)
    assert len(plan.pending) == 3
    assert database.executed_scripts == []


async def test_apply_aborts_on_drift(sql_dir: pathlib.Path) -> None:
    """checksum 漂移时中止并抛 30004。"""
    baseline = discover_migrations(sql_dir)[0]
    database = FakeMigrationDatabase({BASELINE_VERSION: "sha256:tampered"})
    with pytest.raises(ApiError) as excinfo:
        await apply_migrations(database, sql_dir)
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert database.executed_scripts == []
    assert baseline.version == BASELINE_VERSION


def test_resolve_sql_dir_finds_repo_deploy() -> None:
    """默认定位能命中仓库 `deploy/`（容器内为 `/app/deploy`）。"""
    directory = resolve_sql_dir()
    assert (directory / BASELINE_FILENAME).is_file()


async def test_startup_migration_skipped_in_memory_mode() -> None:
    """`business_store=memory` 时启动期迁移直接跳过（返回 None）。"""
    plan = await run_startup_migrations(Settings(env="test", business_store="memory"))
    assert plan is None


async def test_startup_migration_skipped_when_auto_migrate_off() -> None:
    """未开启 `db_auto_migrate` 时不触库。"""
    settings = Settings(
        env="test",
        business_store="postgres",
        database_url="postgresql://officeai:pw@127.0.0.1:1/officeai",
        db_auto_migrate=False,
    )
    assert await run_startup_migrations(settings) is None


# ===========================================================================
# 真实 PostgreSQL 用例（`--run-pg` 启用；未启用或缺少 DSN 时 skip，绝不允许 fail）
# ===========================================================================
@pytest.mark.pg
async def test_real_postgres_migration_is_idempotent(pg_dsn: str) -> None:
    """真实库：全量执行两遍 + `--check` 通过（与主理人实测口径一致）。"""
    database: Any = _build_real_database(pg_dsn)
    try:
        await apply_migrations(database)
        first_applied = await database.fetch_applied()
        await apply_migrations(database)
        second_applied = await database.fetch_applied()
        assert first_applied == second_applied
        plan = await check_migrations(database)
        assert plan.is_consistent
        assert plan.pending == []
    finally:
        await database.close()


def _build_real_database(dsn: str) -> Any:
    """构造真实 PostgreSQL 迁移库（延迟导入，避免离线环境依赖 asyncpg）。

    夹具 `pg_dsn` 传入的是 SQLAlchemy 形式（`postgresql+asyncpg://`），
    而 asyncpg 只认原生 scheme，直接传会抛
    `ClientConfigurationError: scheme is expected to be either
    "postgresql" or "postgres"`。此处归一化，避免该门禁整体失效。

    Args:
        dsn: `postgresql://` 或 `postgresql+asyncpg://` 连接串。

    Returns:
        Any: 迁移数据库实现。
    """
    from app.db.migrate import AsyncpgMigrationDatabase

    return AsyncpgMigrationDatabase(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
