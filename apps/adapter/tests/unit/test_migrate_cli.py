"""迁移执行器 CLI 与计划分类补测（阶段三遗留观察项：分支余量补足）。

覆盖面：`app/db/migrate.py` 中**离线可验**的剩余分支——

1. **命令行各出口**（`--check` / `--apply` / `--json` / `--prune-orphans` /
   `--yes` / 漂移 / 无库）：迁移是客户现场首次部署的必经路径，出口码
   （0 成功 / 1 运行失败 / 2 装配失败）与 stdout 文案是运维与 CI 的判据，
   此前完全没有用例钉住；
2. **计划分类**：孤儿（有台账无脚本）、别名认领（旧名 → 新脚本）、
   别名目标缺失退化孤儿、基线哨兵 checksum 接管；
3. **SQL 目录定位**：显式/环境目录优先级与去重、仅有增量目录、
   全部候选缺失时的 30004；
4. **asyncpg 实现的非连接分支**：scheme 改写、空清单不连库、
   命令标签解析、关闭语义。

真实库执行（幂等 / RLS / 特权）由 `tests/integration/test_migrate*.py`
覆盖，本文件不重复连库，全部用例离线可跑。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.db.engine import ASYNC_DRIVER_PREFIX
from app.db.migrate import (
    BASELINE_FILENAME,
    BASELINE_VERSION,
    MANAGED_CHECKSUM_SENTINEL,
    AsyncpgMigrationDatabase,
    Migration,
    _build_database,
    _run_cli,
    apply_migrations,
    build_plan,
    candidate_sql_dirs,
    checksum_of,
    discover_migrations,
    main,
    resolve_sql_dir,
    run_startup_migrations,
)

pytestmark = pytest.mark.unit

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

#: 与 `MIGRATION_ALIASES` 认领目标同名的增量（别名用例需要它真在盘上）
_INCREMENT_ONE_NAME = "0002_callback_idempotency_selfcheck"
_INCREMENT_TWO_NAME = "0003_demo_add_two"


class FakeMigrationDatabase:
    """内存迁移库替身（含孤儿清理入口，供 CLI 用例驱动）。

    Attributes:
        applied: 已应用版本 → checksum。
        executed_scripts: 已执行脚本内容（按序）。
        deleted: 最近一次 `delete_versions` 收到的版本列表。
        closed: 是否调用过 `close()`。
    """

    def __init__(self, applied: dict[str, str] | None = None) -> None:
        """初始化。

        Args:
            applied: 初始已应用版本 → checksum。
        """
        self.applied: dict[str, str] = dict(applied or {})
        self.executed_scripts: list[str] = []
        self.deleted: list[str] = []
        self.closed = False

    async def fetch_applied(self) -> dict[str, str]:
        """读取已应用版本。

        Returns:
            dict[str, str]: 版本 → checksum（副本，模拟库上读取）。
        """
        return dict(self.applied)

    async def execute_script(self, sql: str) -> None:
        """记录脚本执行。

        Args:
            sql: 脚本内容。
        """
        self.executed_scripts.append(sql)

    async def record(self, version: str, checksum: str) -> None:
        """记录版本（幂等 upsert 的内存等价物）。

        Args:
            version: 版本号。
            checksum: 脚本摘要。
        """
        self.applied[version] = checksum

    async def delete_versions(self, versions: Any) -> int:
        """删除台账行并返回实际删除条数。

        Args:
            versions: 版本号序列。

        Returns:
            int: 实际删除条数（只数存在的行）。
        """
        self.deleted = list(versions)
        removed = 0
        for version in self.deleted:
            if self.applied.pop(version, None) is not None:
                removed += 1
        return removed

    async def close(self) -> None:
        """关闭连接。"""
        self.closed = True


class _FakeConnection:
    """asyncpg 连接替身（只实现迁移实现用到的方法）。

    Attributes:
        result_tag: `execute` 返回的命令标签（如 `DELETE 2`）。
        statements: 收到的 `(sql, args)` 记录。
        closed: 是否调用过 `close()`。
        table_exists: `fetchval` 的返回值（`to_regclass` 探活：版本表是否存在）。
        rows: `fetch` 的返回值（版本表行）。
    """

    def __init__(
        self,
        result_tag: str = "DELETE 0",
        *,
        table_exists: bool = True,
        rows: list[dict[str, str]] | None = None,
    ) -> None:
        """初始化。

        Args:
            result_tag: 命令标签。
            table_exists: 版本表是否存在。
            rows: 版本表行。
        """
        self.result_tag = result_tag
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False
        self.table_exists: bool = table_exists
        self.rows: list[dict[str, str]] = list(rows or [])

    async def execute(self, sql: str, *args: Any) -> str:
        """记录调用并返回预置命令标签。

        Args:
            sql: SQL 语句。
            *args: 绑定参数。

        Returns:
            str: 预置命令标签。
        """
        self.statements.append((sql, args))
        return self.result_tag

    async def fetchval(self, sql: str) -> bool:
        """返回版本表是否存在。

        Args:
            sql: 探活语句。

        Returns:
            bool: 预置的表存在标记。
        """
        self.statements.append((sql, ()))
        return self.table_exists

    async def fetch(self, sql: str) -> list[dict[str, str]]:
        """返回版本表行。

        Args:
            sql: 查询语句。

        Returns:
            list[dict[str, str]]: 预置的行。
        """
        self.statements.append((sql, ()))
        return list(self.rows)

    async def close(self) -> None:
        """关闭连接。"""
        self.closed = True


@pytest.fixture
def sql_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """构造含基线 + 2 个增量的临时 SQL 目录（增量名与别名表对齐）。"""
    (tmp_path / BASELINE_FILENAME).write_text(_BASELINE_SQL, encoding="utf-8")
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / f"{_INCREMENT_ONE_NAME}.sql").write_text(_INCREMENT_ONE, encoding="utf-8")
    (migrations / f"{_INCREMENT_TWO_NAME}.sql").write_text(_INCREMENT_TWO, encoding="utf-8")
    return tmp_path


def _fully_applied(directory: pathlib.Path) -> dict[str, str]:
    """构造"全部已应用"的台账（基线为哨兵值，增量为真实摘要）。

    Args:
        directory: SQL 目录。

    Returns:
        dict[str, str]: 版本 → checksum。
    """
    return {
        BASELINE_VERSION: MANAGED_CHECKSUM_SENTINEL,
        _INCREMENT_ONE_NAME: checksum_of(directory / "migrations" / f"{_INCREMENT_ONE_NAME}.sql"),
        _INCREMENT_TWO_NAME: checksum_of(directory / "migrations" / f"{_INCREMENT_TWO_NAME}.sql"),
    }


def _patch_database(monkeypatch: pytest.MonkeyPatch, database: FakeMigrationDatabase) -> None:
    """把迁移库替身注入 CLI 的装配点（免连库）。

    Args:
        monkeypatch: pytest monkeypatch 夹具。
        database: 迁移库替身。
    """
    monkeypatch.setattr("app.db.migrate._build_database", lambda settings: database)


# ===========================================================================
# build_plan：孤儿 / 别名 / 哨兵
# ===========================================================================
def test_build_plan_classifies_aliased_orphans_and_checked() -> None:
    """计划分类四支：别名认领、孤儿、哨兵免检、正常校验通过。

    分支语义（都只在"台账与磁盘不一致"时走到）：
    - 旧版本名在、新脚本也在 → `aliased`（不重复执行、不当孤儿）；
    - 版本在台账里但盘上无文件 → `orphans`（沉默通过比报错危险）；
    - 基线哨兵 checksum → `checked`（initdb 写入，不算漂移）；
    - checksum 一致 → `checked`。
    """
    available = [
        Migration(BASELINE_VERSION, pathlib.Path("a.sql"), "sha256:aaa"),
        Migration(_INCREMENT_ONE_NAME, pathlib.Path("b.sql"), "sha256:bbb"),
    ]
    applied = {
        BASELINE_VERSION: MANAGED_CHECKSUM_SENTINEL,
        "0002_billing_callback_unique": "sha256:old",
        "0009_ghost": "sha256:zzz",
        _INCREMENT_ONE_NAME: "sha256:bbb",
    }

    plan = build_plan(available, applied)

    assert plan.aliased == [("0002_billing_callback_unique", _INCREMENT_ONE_NAME)]
    assert plan.orphans == ["0009_ghost"]
    assert plan.checked == 2
    assert plan.pending == []
    assert plan.is_consistent


def test_build_plan_treats_alias_with_missing_target_as_orphan() -> None:
    """别名指向的新脚本不在盘上 → 退化为孤儿（不能假装修复）。"""
    available = [Migration(BASELINE_VERSION, pathlib.Path("a.sql"), "sha256:aaa")]
    plan = build_plan(available, {"0002_billing_callback_unique": "sha256:old"})

    assert plan.aliased == []
    assert plan.orphans == ["0002_billing_callback_unique"]


async def test_apply_adopts_baseline_sentinel_checksum(sql_dir: pathlib.Path) -> None:
    """首次 `--apply` 接管基线哨兵 checksum，且**不重跑**基线脚本。

    接管后，任何对基线文件的手工改动都会被 `--check` 判漂移
    （防「改库不改脚本」）；重跑基线会让 initdb 的种子行被二次执行。
    """
    database = FakeMigrationDatabase({BASELINE_VERSION: MANAGED_CHECKSUM_SENTINEL})

    plan = await apply_migrations(database, sql_dir)

    assert [item.version for item in plan.pending] == [_INCREMENT_ONE_NAME, _INCREMENT_TWO_NAME]
    assert len(database.executed_scripts) == 2, "基线已应用，不得再次执行"
    assert database.applied[BASELINE_VERSION] == checksum_of(sql_dir / BASELINE_FILENAME)
    assert database.applied[BASELINE_VERSION] != MANAGED_CHECKSUM_SENTINEL


# ===========================================================================
# discover_migrations / 目录定位
# ===========================================================================
def test_discover_migrations_handles_missing_baseline_and_non_file_entries(
    tmp_path: pathlib.Path,
) -> None:
    """无基线、无增量目录、目录条目伪装成脚本，三种边界都不崩。

    `migrations/` 里出现**目录**（如同步工具建的占位目录）时跳过而不是
    `read_text` 崩溃——部署现场的文件系统远比仓库脏。
    """
    only_increments = tmp_path / "only_inc"
    (only_increments / "migrations").mkdir(parents=True)
    (only_increments / "migrations" / "0002_x.sql").write_text("SELECT 1;\n", encoding="utf-8")
    assert [item.version for item in discover_migrations(only_increments)] == ["0002_x"]

    empty = tmp_path / "empty"
    empty.mkdir()
    assert discover_migrations(empty) == []

    weird = tmp_path / "weird"
    (weird / "migrations").mkdir(parents=True)
    (weird / "migrations" / "0003_dir.sql").mkdir()
    assert discover_migrations(weird) == []


def test_candidate_sql_dirs_orders_explicit_env_and_dedupes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """候选目录优先级：显式 > 环境变量 > 其余；重复候选去重。

    **必须清一次配置缓存**：`get_settings()` 是 `lru_cache(maxsize=1)` 单例，
    `candidate_sql_dirs()` 读的是 `settings.db_sql_dir`——缓存一旦被先前用例填过，
    这里 `setenv` 就看不见（整跑红、单跑绿，正是这条曾经的样子）。
    收尾再清一次，避免把"临时目录"这份配置泄漏给后面的用例。
    """
    env_dir = tmp_path / "from_env"
    env_dir.mkdir()
    monkeypatch.setenv("OA_DB_SQL_DIR", str(env_dir))
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    reset_settings_cache()

    try:
        candidates = candidate_sql_dirs(explicit)

        assert candidates[0] == explicit
        assert env_dir in candidates

        same = candidate_sql_dirs(env_dir)
        resolved = [item.resolve() if item.exists() else item for item in same]
        assert resolved.count(env_dir.resolve()) == 1, "同一目录不得候选两遍"
    finally:
        reset_settings_cache()


def test_resolve_sql_dir_uses_migrations_dir_without_baseline(tmp_path: pathlib.Path) -> None:
    """没有基线脚本但有增量目录时，该目录同样可用（纯增量现场）。"""
    (tmp_path / "migrations").mkdir()
    assert resolve_sql_dir(tmp_path) == tmp_path


def test_resolve_sql_dir_raises_when_no_candidate_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """所有候选都不可用 → 30004，并带上检查过的目录清单（可运维）。"""
    missing = tmp_path / "nowhere"
    monkeypatch.setattr("app.db.migrate.candidate_sql_dirs", lambda explicit=None: [missing])

    with pytest.raises(ApiError) as excinfo:
        resolve_sql_dir(missing)
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert str(missing) in (excinfo.value.internal_detail or "")


# ===========================================================================
# asyncpg 实现的非连接分支
# ===========================================================================
def test_build_database_rejects_memory_store() -> None:
    """`business_store=memory` 时迁移装配直接 30004（不连不存在的库）。"""
    with pytest.raises(ApiError) as excinfo:
        _build_database(Settings(env="test", business_store="memory"))
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "OA_BUSINESS_STORE=postgres" in (excinfo.value.internal_detail or "")


def test_build_database_rewrites_asyncpg_scheme() -> None:
    """postgres 配置下装配 asyncpg 实现，连接串改写为原生 scheme。

    asyncpg 不认 `postgresql+asyncpg://`（SQLAlchemy 专用前缀），
    不改写会在第一次连库时才炸，届时错误信息离根因极远。
    """
    settings = Settings(
        env="test",
        business_store="postgres",
        database_url="postgresql://officeai_app:pw@127.0.0.1:55433/officeai",
    )

    database = _build_database(settings)

    assert isinstance(database, AsyncpgMigrationDatabase)
    assert database._dsn == "postgresql://officeai_app:pw@127.0.0.1:55433/officeai"  # noqa: SLF001
    assert not database._dsn.startswith(ASYNC_DRIVER_PREFIX)  # noqa: SLF001


async def test_delete_versions_empty_list_does_not_connect() -> None:
    """空清理清单直接返回 0，不建连接（dry-run 不该产生连接开销）。"""
    database = AsyncpgMigrationDatabase("postgresql://officeai_app:pw@127.0.0.1:1/db")

    assert await database.delete_versions([]) == 0
    assert database._connection is None  # noqa: SLF001

    await database.close()
    assert database._connection is None  # noqa: SLF001


async def test_delete_versions_parses_command_tag_and_closes() -> None:
    """删除条数从 asyncpg 命令标签解析；`close` 后连接复位可重连。"""
    database = AsyncpgMigrationDatabase("postgresql://officeai_app:pw@127.0.0.1:1/db")
    connection = _FakeConnection(result_tag="DELETE 2")
    database._connection = connection  # noqa: SLF001

    removed = await database.delete_versions(["0009_ghost", "0010_old"])

    assert removed == 2
    sql, args = connection.statements[0]
    assert "DELETE FROM schema_migration" in sql
    assert args == (["0009_ghost", "0010_old"],)

    await database.close()
    assert connection.closed is True
    assert database._connection is None  # noqa: SLF001


async def test_fetch_applied_returns_empty_when_version_table_missing() -> None:
    """版本表不存在（initdb 尚未执行的裸库）→ 空台账，不再查行。

    这一支决定"全量 pending"：若此时去 `SELECT` 一张不存在的表，
    首次部署会在连接层抛 500 而不是走"从头应用"的正常路径。
    """
    database = AsyncpgMigrationDatabase("postgresql://officeai_app:pw@127.0.0.1:1/db")
    connection = _FakeConnection(table_exists=False)
    database._connection = connection  # noqa: SLF001

    assert await database.fetch_applied() == {}
    assert len(connection.statements) == 1, "表不存在时不应继续查行"


async def test_fetch_applied_reads_rows_when_version_table_exists() -> None:
    """版本表存在 → 逐行读出版本与 checksum（字符串化，防类型漂移）。"""
    database = AsyncpgMigrationDatabase("postgresql://officeai_app:pw@127.0.0.1:1/db")
    connection = _FakeConnection(
        table_exists=True,
        rows=[{"version": "0002_x", "checksum": "sha256:bbb"}],
    )
    database._connection = connection  # noqa: SLF001

    assert await database.fetch_applied() == {"0002_x": "sha256:bbb"}
    assert len(connection.statements) == 2


async def test_startup_migration_applies_when_auto_migrate_on(
    monkeypatch: pytest.MonkeyPatch, sql_dir: pathlib.Path
) -> None:
    """auto_migrate 开启且落 postgres：启动期执行迁移并**关连接**。

    `finally` 里的 `close` 是硬要求：启动期挂着不关的连接会把空闲
    连接带进整个进程生命周期（重启才释放），现场排查时极难归因。
    """
    database = FakeMigrationDatabase({BASELINE_VERSION: MANAGED_CHECKSUM_SENTINEL})
    monkeypatch.setattr("app.db.migrate._build_database", lambda settings: database)
    monkeypatch.setattr("app.db.migrate.resolve_sql_dir", lambda explicit=None: sql_dir)
    settings = Settings(
        env="test",
        business_store="postgres",
        database_url="postgresql://officeai_app:pw@127.0.0.1:1/officeai",
        db_auto_migrate=True,
    )

    plan = await run_startup_migrations(settings)

    assert plan is not None
    assert [item.version for item in plan.pending] == [_INCREMENT_ONE_NAME, _INCREMENT_TWO_NAME]
    assert database.closed is True


# ===========================================================================
# CLI：出口码与输出
# ===========================================================================
async def test_cli_check_and_apply_report_consistent_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sql_dir: pathlib.Path
) -> None:
    """`--check` 干净态出口 0；`--json` 输出三字段；有 pending 时告警；

    `--apply` 后 pending 清零（证明台账真的记了，不是只打印）。
    """
    database = FakeMigrationDatabase(_fully_applied(sql_dir))
    _patch_database(monkeypatch, database)
    argv = ["--check", "--sql-dir", str(sql_dir)]

    assert await _run_cli(argv) == 0
    out = capsys.readouterr().out
    assert "[OK]   已应用并校验通过: 3" in out
    assert "[OK]   无待应用迁移" in out

    assert await _run_cli([*argv, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload == {
        "sql_dir": str(sql_dir),
        "pending": [],
        "already_applied": 3,
    }

    database.applied.clear()
    assert await _run_cli(argv) == 0
    out = capsys.readouterr().out
    assert "[WARN] 待应用:" in out
    assert BASELINE_VERSION in out

    assert await _run_cli(["--apply", "--sql-dir", str(sql_dir)]) == 0
    out = capsys.readouterr().out
    assert "[OK]   本次应用:" in out
    assert len(database.executed_scripts) == 3
    assert set(database.applied) == {
        BASELINE_VERSION,
        _INCREMENT_ONE_NAME,
        _INCREMENT_TWO_NAME,
    }

    assert await _run_cli(argv) == 0
    assert "[OK]   无待应用迁移" in capsys.readouterr().out


async def test_cli_lists_orphans_and_aliases_every_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sql_dir: pathlib.Path
) -> None:
    """孤儿与别名**每次运行都列出**（沉默通过会让跨机器不一致成为谜案）。"""
    applied = _fully_applied(sql_dir)
    applied["0002_billing_callback_unique"] = "sha256:old"
    applied["0009_ghost"] = "sha256:zzz"
    database = FakeMigrationDatabase(applied)
    _patch_database(monkeypatch, database)

    assert await _run_cli(["--check", "--sql-dir", str(sql_dir)]) == 0

    out = capsys.readouterr().out
    assert f"[INFO] 版本别名 0002_billing_callback_unique → {_INCREMENT_ONE_NAME}" in out
    assert "[WARN] 台账有 1 个孤儿版本" in out
    assert "0009_ghost" in out


async def test_cli_prune_orphans_dryrun_then_confirmed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sql_dir: pathlib.Path
) -> None:
    """`--prune-orphans` 默认 dry-run 只列不删；`--yes` 才真删。"""
    applied = _fully_applied(sql_dir)
    applied["0002_billing_callback_unique"] = "sha256:old"
    applied["0009_ghost"] = "sha256:zzz"
    database = FakeMigrationDatabase(applied)
    _patch_database(monkeypatch, database)
    argv = ["--check", "--prune-orphans", "--sql-dir", str(sql_dir)]

    assert await _run_cli(argv) == 0
    out = capsys.readouterr().out
    assert "[DRYRUN] 将删除以下台账行" in out
    assert "0009_ghost" in out and "0002_billing_callback_unique" in out
    assert "0009_ghost" in database.applied, "dry-run 不得动库"

    assert await _run_cli([*argv, "--yes"]) == 0
    out = capsys.readouterr().out
    assert "[OK]   已删除 2 条孤儿台账行" in out
    assert "0009_ghost" not in database.applied
    assert "0002_billing_callback_unique" not in database.applied

    clean = FakeMigrationDatabase(_fully_applied(sql_dir))
    _patch_database(monkeypatch, clean)
    assert await _run_cli(argv) == 0
    assert "[OK]   没有需要清理的孤儿版本" in capsys.readouterr().out


async def test_cli_fails_with_exit_1_on_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sql_dir: pathlib.Path
) -> None:
    """`--check` 检出漂移：打印逐版本差异并出口 1（启动必须失败的场景）。"""
    database = FakeMigrationDatabase({BASELINE_VERSION: "sha256:tampered"})
    _patch_database(monkeypatch, database)

    assert await _run_cli(["--check", "--sql-dir", str(sql_dir)]) == 1

    out = capsys.readouterr().out
    assert "[FAIL] 版本 0001_phase2_init checksum 漂移" in out
    assert "recorded=sha256:tampered" in out


async def test_cli_returns_exit_2_when_database_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sql_dir: pathlib.Path
) -> None:
    """库装配失败（如 memory 模式）出口 2，与运行期失败（1）区分。

    出口码分工：2 = 配置/装配问题（改环境变量能好），1 = 数据状态问题
    （要人巡检台账）。CI 靠这个区分"挂的是环境还是库"。
    """

    def _unavailable(settings: Settings) -> FakeMigrationDatabase:
        raise ApiError(
            ErrorCode.CONFIG_ERROR, internal_detail="migrations require OA_BUSINESS_STORE=postgres"
        )

    monkeypatch.setattr("app.db.migrate._build_database", _unavailable)

    assert await _run_cli(["--check", "--sql-dir", str(sql_dir)]) == 2
    assert "[FAIL]" in capsys.readouterr().out


def test_main_delegates_to_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sql_dir: pathlib.Path
) -> None:
    """`main()` 是 `asyncio.run(_run_cli())` 的薄封装（进程入口可测）。"""
    database = FakeMigrationDatabase(_fully_applied(sql_dir))
    _patch_database(monkeypatch, database)

    assert main(["--check", "--sql-dir", str(sql_dir)]) == 0
    assert "[OK]   无待应用迁移" in capsys.readouterr().out
