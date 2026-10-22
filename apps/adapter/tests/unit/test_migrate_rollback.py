"""迁移回滚的分支覆盖（阶段四 T4-01.4 补测）。

**为什么这份用例必须存在**：`--rollback` 是"出事时才会用到"的路径，全部拒绝分支
（未知版本 / 缺 down 文件 / 更晚版本仍应用 / 强制备份失败）平时一次都不会走到。
它们不被覆盖的后果不是"少几个百分点"，而是**真出事那天才发现回滚本身是坏的**——
那种场景下没人有时间调试。

两条铁律由这些用例钉住（架构 §10.2 / R-4）：

1. **不自动级联**：要退 0006 而 0007 还在，直接拒绝并列出 0007；
2. **删数据的 down 必须先备份**：`0008`/`0009` 没有可用备份就中止——
   「回滚成功但丢数据」不可逆，「回滚不了」只是原地不动。

`apply_rollback` 的 asyncpg 路径用 **fake connection** 验证：事务包裹、迁移锁、
台账判定、执行脚本、删台账行，以及"down 脚本自己写 BEGIN 则拒绝"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.errors import ApiError, ErrorCode
from app.db.migrate import (
    MIGRATION_LOCK_KEY,
    AsyncpgMigrationDatabase,
    discover_rollback_scripts,
    rollback_migrations,
    rollback_requires_backup,
    script_manages_transaction,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_test_tenant"


class FakeMigrationDatabase:
    """回滚用的内存替身（只实现回滚链需要的三个方法）。"""

    def __init__(self, applied: dict[str, str] | None = None) -> None:
        """初始化。

        Args:
            applied: 初始已应用版本 → checksum。
        """
        self.applied: dict[str, str] = dict(applied or {})
        self.rolled: list[str] = []
        self.fail_rollback = False

    async def fetch_applied(self) -> dict[str, str]:
        """读取已应用版本。

        Returns:
            dict[str, str]: 版本 → checksum。
        """
        return dict(self.applied)

    async def apply_rollback(self, version: str, sql: str) -> bool:
        """执行回滚（内存等价物）。

        Args:
            version: 版本号。
            sql: 回滚脚本内容。

        Returns:
            bool: 执行了返回 True；版本未应用返回 False。

        Raises:
            ApiError: `fail_rollback=True` 时模拟执行失败。
        """
        if self.fail_rollback:
            raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="模拟回滚执行失败")
        if version not in self.applied:
            return False
        self.applied.pop(version, None)
        self.rolled.append(version)
        return True

    async def close(self) -> None:
        """关闭（替身无连接）。"""


def _sql_dir(tmp_path: Path, versions: list[str]) -> Path:
    """构造 SQL 根目录 + 对应版本的 down 脚本。

    Args:
        tmp_path: 临时目录。
        versions: 需要 down 脚本的版本号。

    Returns:
        Path: SQL 根目录。
    """
    rollback = tmp_path / "migrations" / "rollback"
    rollback.mkdir(parents=True, exist_ok=True)
    for version in versions:
        (rollback / f"{version}.down.sql").write_text(
            f"-- down {version}\nDROP TABLE IF EXISTS demo_{version};\n", encoding="utf-8"
        )
    return tmp_path


# ===========================================================================
# 1. 三条拒绝分支
# ===========================================================================
async def test_unknown_version_without_down_script_is_rejected(tmp_path: Path) -> None:
    """缺 down 脚本 → 30004，且提示里点名版本（运维要能照做）。"""
    sql_dir = _sql_dir(tmp_path, ["0006_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:x"})

    with pytest.raises(ApiError) as excinfo:
        await rollback_migrations(database, ["0099_no_such"], sql_dir)

    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "0099_no_such" in (excinfo.value.internal_detail or "")
    assert database.rolled == [], "拒绝时不得执行任何回滚"


async def test_later_version_still_applied_is_rejected(tmp_path: Path) -> None:
    """要退 0006 但 0007 还在 → 拒绝（不自动级联），并列出 0007。"""
    sql_dir = _sql_dir(tmp_path, ["0006_demo", "0007_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:a", "0007_demo": "sha256:b"})

    with pytest.raises(ApiError) as excinfo:
        await rollback_migrations(database, ["0006_demo"], sql_dir)

    detail = excinfo.value.internal_detail or ""
    assert "0007_demo" in detail, f"必须点名更晚的版本：{detail}"
    assert "不自动级联" in detail
    assert database.rolled == []


async def test_not_applied_version_is_skipped_not_failed(tmp_path: Path) -> None:
    """版本未应用 → 跳过并返回空列表（幂等：重跑同一条命令不报错）。"""
    sql_dir = _sql_dir(tmp_path, ["0006_demo"])
    database = FakeMigrationDatabase({})

    rolled = await rollback_migrations(database, ["0006_demo"], sql_dir)

    assert rolled == []
    assert database.rolled == []


async def test_versions_are_executed_in_descending_order(tmp_path: Path) -> None:
    """多版本一次给：**倒序**执行（先退更晚的），否则会被"更晚版本仍应用"挡住。"""
    sql_dir = _sql_dir(tmp_path, ["0006_demo", "0007_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:a", "0007_demo": "sha256:b"})

    rolled = await rollback_migrations(database, ["0006_demo", "0007_demo"], sql_dir)

    assert rolled == ["0007_demo", "0006_demo"], f"必须倒序：{rolled}"


# ===========================================================================
# 2. 强制备份（R-4）
# ===========================================================================
@pytest.mark.parametrize(
    "version",
    ["0008_conversation_message", "0009_user_memory"],
)
def test_data_dropping_versions_require_backup(version: str) -> None:
    """0008/0009 属"删表"级别，必须要求备份；0006/0007 不要求（架构口径）。"""
    assert rollback_requires_backup(version) is True


@pytest.mark.parametrize(
    "version",
    ["0006_plugin_artifact_content_type", "0007_usage_event_dimensions"],
)
def test_column_drops_do_not_require_backup(version: str) -> None:
    """列级 down 不纳入强制备份（派生数据，可重新采集）。"""
    assert rollback_requires_backup(version) is False


async def test_backup_missing_hook_aborts_data_dropping_rollback(tmp_path: Path) -> None:
    """会删表的版本 + 没有备份能力 → 中止（宁可不回滚，也不能丢数据）。"""
    version = "0008_conversation_message"
    sql_dir = _sql_dir(tmp_path, [version])
    database = FakeMigrationDatabase({version: "sha256:a"})

    with pytest.raises(ApiError) as excinfo:
        await rollback_migrations(database, [version], sql_dir)

    assert "必须先备份" in (excinfo.value.internal_detail or "")
    assert database.rolled == []


async def test_backup_failure_aborts_rollback(tmp_path: Path) -> None:
    """备份回调返回 False（pg_dump 失败/产物空）→ 中止，且**不执行** down。"""
    version = "0008_conversation_message"
    sql_dir = _sql_dir(tmp_path, [version])
    database = FakeMigrationDatabase({version: "sha256:a"})
    calls: list[str] = []

    def failing_backup(item: str) -> bool:
        calls.append(item)
        return False

    with pytest.raises(ApiError) as excinfo:
        await rollback_migrations(database, [version], sql_dir, backup=failing_backup)

    assert calls == [version], "备份回调必须被调用一次"
    assert "备份失败" in (excinfo.value.internal_detail or "")
    assert database.rolled == [], "备份失败后绝不能执行 down"


async def test_backup_success_allows_rollback(tmp_path: Path) -> None:
    """备份成功 → 正常回滚。"""
    version = "0008_conversation_message"
    sql_dir = _sql_dir(tmp_path, [version])
    database = FakeMigrationDatabase({version: "sha256:a"})

    rolled = await rollback_migrations(database, [version], sql_dir, backup=lambda _v: True)

    assert rolled == [version]
    assert version not in database.applied


async def test_backup_not_called_for_column_level_down(tmp_path: Path) -> None:
    """0006/0007 不触发备份回调（别让运维为一次 drop column 等整库 dump）。"""
    sql_dir = _sql_dir(tmp_path, ["0006_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:a"})
    calls: list[str] = []

    await rollback_migrations(
        database, ["0006_demo"], sql_dir, backup=lambda item: calls.append(item) or True
    )

    assert calls == []


async def test_execution_failure_propagates_and_keeps_ledger(tmp_path: Path) -> None:
    """down 执行失败 → 异常向上抛（调用方据此非 0 退出），台账行**保留**。"""
    sql_dir = _sql_dir(tmp_path, ["0006_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:a"})
    database.fail_rollback = True

    with pytest.raises(ApiError):
        await rollback_migrations(database, ["0006_demo"], sql_dir)

    assert database.applied.get("0006_demo") == "sha256:a", "失败时台账不得被删"


# ===========================================================================
# 3. 脚本发现与事务控制判定
# ===========================================================================
def test_discover_rollback_scripts_strips_down_suffix(tmp_path: Path) -> None:
    """`NNNN_name.down.sql` 的版本号是 `NNNN_name`（去掉 `.down`）。"""
    sql_dir = _sql_dir(tmp_path, ["0006_demo", "0007_other"])

    scripts = discover_rollback_scripts(sql_dir)

    assert sorted(scripts) == ["0006_demo", "0007_other"]
    assert scripts["0006_demo"].name == "0006_demo.down.sql"


def test_discover_rollback_scripts_missing_dir(tmp_path: Path) -> None:
    """没有 rollback 目录时返回空字典（不抛异常，交给上层报"找不到脚本"）。"""
    assert discover_rollback_scripts(tmp_path) == {}


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("BEGIN;\nCREATE TABLE x (id INT);\nCOMMIT;\n", True),
        ("  commit;", True),
        ("START TRANSACTION;", True),
        # PL/pgSQL 块关键字不是事务语句——这是曾经踩过的坑
        ("DO $guard$\nBEGIN\n  RAISE NOTICE 'x';\nEND $guard$;\n", False),
        ("ALTER TABLE t ADD COLUMN c TEXT;", False),
    ],
)
def test_script_manages_transaction_only_matches_statements(sql: str, expected: bool) -> None:
    """只有**行首的事务语句**才算"脚本自带事务"，`DO $$ BEGIN …` 不算。"""
    assert script_manages_transaction(sql) is expected


# ===========================================================================
# 4. `apply_rollback` 的 asyncpg 路径（fake connection）
# ===========================================================================
class _FakeTransaction:
    """asyncpg `transaction()` 的替身（异步上下文管理器）。"""

    def __init__(self, log: list[str]) -> None:
        """初始化。

        Args:
            log: 调用序记录。
        """
        self._log = log

    async def __aenter__(self) -> _FakeTransaction:
        """进入事务。"""
        self._log.append("begin")
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """退出事务。"""
        self._log.append("commit")


class _FakeConnection:
    """asyncpg 连接的替身：记录语句序，`fetchval` 按脚本返回。"""

    def __init__(self, *, applied: bool = True) -> None:
        """初始化。

        Args:
            applied: `schema_migration` 里是否已有该版本。
        """
        self.log: list[str] = []
        self.statements: list[str] = []
        self._applied = applied

    def transaction(self) -> _FakeTransaction:
        """返回事务上下文。"""
        return _FakeTransaction(self.log)

    async def fetchval(self, query: str, *args: Any) -> Any:
        """模拟 `to_regclass` 与台账查询。

        Args:
            query: SQL。
            *args: 参数。

        Returns:
            Any: 查询结果。
        """
        if "to_regclass" in query:
            return True
        return 1 if self._applied else None

    async def execute(self, query: str, *args: Any) -> str:
        """记录语句；`DELETE FROM schema_migration` 视为删台账行。

        Args:
            query: SQL。
            *args: 参数。

        Returns:
            str: 模拟的命令标签。
        """
        self.statements.append(query)
        if query.strip().startswith("DELETE FROM schema_migration"):
            self._applied = False
            return "DELETE 1"
        return "SELECT 1"

    async def close(self) -> None:
        """关闭（替身无连接）。"""


async def test_apply_rollback_runs_inside_lock_and_deletes_ledger() -> None:
    """`apply_rollback`：事务内 → 取迁移锁 → 执行 down → 删台账行。"""
    connection = _FakeConnection(applied=True)
    database = AsyncpgMigrationDatabase("postgresql://user:pass@127.0.0.1:5432/db")
    database._connection = connection  # noqa: SLF001 - 用例注入 fake 连接

    executed = await database.apply_rollback("0006_demo", "DROP TABLE IF EXISTS demo;")

    assert executed is True
    assert connection.log == ["begin", "commit"], "必须在显式事务内执行"
    advisory = f"pg_advisory_xact_lock({MIGRATION_LOCK_KEY})"
    assert any(advisory in item for item in connection.statements), "必须先取迁移锁"
    assert any("DROP TABLE IF EXISTS demo" in item for item in connection.statements)
    assert any(
        item.strip().startswith("DELETE FROM schema_migration") for item in connection.statements
    ), "台账行必须在同一事务里删除"


async def test_apply_rollback_skips_when_version_not_applied() -> None:
    """台账里没有该版本 → 不执行 down、不删行，返回 False。"""
    connection = _FakeConnection(applied=False)
    database = AsyncpgMigrationDatabase("postgresql://user:pass@127.0.0.1:5432/db")
    database._connection = connection  # noqa: SLF001

    executed = await database.apply_rollback("0006_demo", "DROP TABLE IF EXISTS demo;")

    assert executed is False
    assert not any("DROP TABLE" in item for item in connection.statements)
    assert not any(item.strip().startswith("DELETE FROM") for item in connection.statements)


async def test_apply_rollback_rejects_script_with_own_transaction() -> None:
    """down 脚本自带 BEGIN/COMMIT → 拒绝（否则会把外层事务提交掉）。"""
    connection = _FakeConnection(applied=True)
    database = AsyncpgMigrationDatabase("postgresql://user:pass@127.0.0.1:5432/db")
    database._connection = connection  # noqa: SLF001

    with pytest.raises(ApiError) as excinfo:
        await database.apply_rollback("0006_demo", "BEGIN;\nDROP TABLE demo;\nCOMMIT;")

    assert "must not contain BEGIN" in (excinfo.value.internal_detail or "")
    assert connection.statements == [], "拒绝时不得执行任何语句"


# ===========================================================================
# 5. `apply_migration` 的 asyncpg 路径（新脚本走事务，旧脚本走会话锁）
# ===========================================================================
def _database_with(connection: _FakeConnection) -> AsyncpgMigrationDatabase:
    """构造注入了 fake 连接的迁移库。

    Args:
        connection: fake 连接。

    Returns:
        AsyncpgMigrationDatabase: 实例。
    """
    database = AsyncpgMigrationDatabase("postgresql://user:pass@127.0.0.1:5432/db")
    database._connection = connection  # noqa: SLF001 - 用例注入 fake 连接
    return database


def _migration(tmp_path: Path, version: str, sql: str) -> Any:
    """写一个迁移文件并返回 `Migration`。

    Args:
        tmp_path: 临时目录。
        version: 版本号（= 文件名去 `.sql`）。
        sql: 脚本内容。

    Returns:
        Any: `Migration` 实例。
    """
    from app.db.migrate import Migration, checksum_of

    path = tmp_path / f"{version}.sql"
    path.write_text(sql, encoding="utf-8")
    return Migration(version=version, path=path, checksum=checksum_of(path))


async def test_apply_migration_new_script_runs_ddl_and_ledger_in_one_transaction(
    tmp_path: Path,
) -> None:
    """新脚本（无 BEGIN）：事务内取 xact 锁 → 执行 → 记台账，返回 True。"""
    connection = _FakeConnection(applied=False)
    database = _database_with(connection)
    migration = _migration(tmp_path, "0006_demo", "ALTER TABLE t ADD COLUMN c TEXT;\n")

    executed = await database.apply_migration(migration)

    assert executed is True
    assert connection.log == ["begin", "commit"]
    assert any(
        f"pg_advisory_xact_lock({MIGRATION_LOCK_KEY})" in item for item in connection.statements
    )
    assert any("ALTER TABLE t ADD COLUMN c" in item for item in connection.statements)
    assert any(
        item.lstrip().startswith("INSERT INTO schema_migration") for item in connection.statements
    )


async def test_apply_migration_skips_inside_lock_when_peer_won(tmp_path: Path) -> None:
    """锁内二次判定命中（另一副本已跑完）→ 不执行 DDL，返回 False。"""
    connection = _FakeConnection(applied=True)
    database = _database_with(connection)
    migration = _migration(tmp_path, "0006_demo", "ALTER TABLE t ADD COLUMN c TEXT;\n")

    executed = await database.apply_migration(migration)

    assert executed is False
    assert not any("ALTER TABLE" in item for item in connection.statements)


async def test_apply_migration_legacy_script_uses_session_lock(tmp_path: Path) -> None:
    """旧脚本（自带 BEGIN/COMMIT）→ 会话级 advisory lock + 两步，最后必须 unlock。"""
    connection = _FakeConnection(applied=False)
    database = _database_with(connection)
    migration = _migration(tmp_path, "0002_legacy", "BEGIN;\nCREATE TABLE t (id INT);\nCOMMIT;\n")

    executed = await database.apply_migration(migration)

    assert executed is True
    assert connection.log == [], "旧脚本不得被包在显式事务里"
    assert any(f"pg_advisory_lock({MIGRATION_LOCK_KEY})" in item for item in connection.statements)
    assert any(
        f"pg_advisory_unlock({MIGRATION_LOCK_KEY})" in item for item in connection.statements
    ), "会话级锁必须显式释放"
    assert any("CREATE TABLE t" in item for item in connection.statements)


# ===========================================================================
# 6. 备份实现（R-4）：which 缺失 / pg_dump 失败 / 产物为空 / 成功
# ===========================================================================
def test_backup_returns_false_when_pg_dump_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """找不到 `pg_dump` → 返回 False（调用方据此中止回滚）。"""
    from app.core.config import Settings
    from app.db.migrate import _backup_before_rollback

    monkeypatch.setattr("app.db.migrate.shutil.which", lambda _name: None)
    settings = Settings(env="test", database_url="postgresql://u:p@h:5432/db")

    assert _backup_before_rollback(settings, tmp_path, "0008_demo") is False


def test_backup_returns_false_when_pg_dump_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pg_dump` 非 0 退出 → 返回 False。"""
    from app.core.config import Settings
    from app.db.migrate import _backup_before_rollback

    class _Result:
        returncode = 1
        stderr = "connection refused"

    monkeypatch.setattr("app.db.migrate.shutil.which", lambda _name: "/usr/bin/pg_dump")
    monkeypatch.setattr("app.db.migrate.subprocess.run", lambda *a, **k: _Result())
    settings = Settings(env="test", database_url="postgresql://u:p@h:5432/db")

    assert _backup_before_rollback(settings, tmp_path, "0008_demo") is False


def test_backup_returns_false_when_dump_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dump 退出 0 但产物为空 → 仍判失败（空备份等于没备份）。"""
    from app.core.config import Settings
    from app.db.migrate import _backup_before_rollback

    class _Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr("app.db.migrate.shutil.which", lambda _name: "/usr/bin/pg_dump")
    monkeypatch.setattr("app.db.migrate.subprocess.run", lambda *a, **k: _Result())
    settings = Settings(env="test", database_url="postgresql://u:p@h:5432/db")

    assert _backup_before_rollback(settings, tmp_path, "0008_demo") is False


def test_backup_writes_dump_and_returns_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常路径：命令执行成功且产物非空 → True，且落在 `migrations/rollback/<ts>/`。"""
    from app.core.config import Settings
    from app.db.migrate import _backup_before_rollback

    class _Result:
        returncode = 0
        stderr = ""

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Result:
        # 模拟 pg_dump 写文件（-f 的下一项就是目标路径）
        Path(cmd[cmd.index("-f") + 1]).write_bytes(b"PGDMP-fake-dump")
        return _Result()

    monkeypatch.setattr("app.db.migrate.shutil.which", lambda _name: "/usr/bin/pg_dump")
    monkeypatch.setattr("app.db.migrate.subprocess.run", _fake_run)
    settings = Settings(env="test", database_url="postgresql+asyncpg://u:p@h:5432/db")

    assert _backup_before_rollback(settings, tmp_path, "0008_demo") is True

    dumps = list((tmp_path / "migrations" / "rollback").glob("*/before_0008_demo.dump"))
    assert dumps, "备份产物必须落在 migrations/rollback/<ts>/ 下"
    assert dumps[0].read_bytes() == b"PGDMP-fake-dump"


# ===========================================================================
# 7. CLI 的 `--rollback` 出口（打印、JSON、非 0 退出）
# ===========================================================================
async def test_cli_rollback_prints_result_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI 回滚成功：退出 0，并打印已回滚的版本。"""
    from app.db.migrate import _run_cli

    sql_dir = _sql_dir(tmp_path, ["0006_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:a"})
    monkeypatch.setattr("app.db.migrate._build_database", lambda _settings: database)
    monkeypatch.setattr("app.db.migrate.resolve_sql_dir", lambda _explicit=None: sql_dir)

    code = await _run_cli(["--rollback", "0006_demo", "--sql-dir", str(sql_dir)])

    assert code == 0
    assert database.rolled == ["0006_demo"]


async def test_cli_rollback_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` 输出 `rolled_back` 列表（供 CI/运维脚本消费）。"""
    from app.db.migrate import _run_cli

    sql_dir = _sql_dir(tmp_path, ["0006_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:a"})
    monkeypatch.setattr("app.db.migrate._build_database", lambda _settings: database)
    monkeypatch.setattr("app.db.migrate.resolve_sql_dir", lambda _explicit=None: sql_dir)

    code = await _run_cli(["--rollback", "0006_demo", "--sql-dir", str(sql_dir), "--json"])

    assert code == 0
    printed = capsys.readouterr().out
    assert '"rolled_back"' in printed and "0006_demo" in printed


async def test_cli_rollback_returns_one_when_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拒绝回滚（更晚版本仍应用）→ 退出 1，且不执行任何 down。"""
    from app.db.migrate import _run_cli

    sql_dir = _sql_dir(tmp_path, ["0006_demo", "0007_demo"])
    database = FakeMigrationDatabase({"0006_demo": "sha256:a", "0007_demo": "sha256:b"})
    monkeypatch.setattr("app.db.migrate._build_database", lambda _settings: database)
    monkeypatch.setattr("app.db.migrate.resolve_sql_dir", lambda _explicit=None: sql_dir)

    code = await _run_cli(["--rollback", "0006_demo", "--sql-dir", str(sql_dir)])

    assert code == 1
    assert database.rolled == []
