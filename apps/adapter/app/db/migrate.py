"""迁移执行器（架构文档 3.7：编号 SQL 文件 + 版本表，不引入 Alembic）。

口径：

| 项 | 约定 |
| --- | --- |
| 基线脚本 | `deploy/init-schema-phase2.sql`（幂等全量；容器首次启动由 initdb `20-*` 执行） |
| 增量脚本 | `deploy/migrations/00NN_<name>.sql`（单事务、`BEGIN/COMMIT` 内置） |
| 版本表 | `schema_migration(version, checksum, applied_at)` |
| `--check` | 只校验：缺失版本、checksum 漂移（已应用版本文件被手工改动） |
| `--apply` | 增量执行未应用的脚本，逐脚本记录 `version + checksum` |
| 回滚 | 手写反向 SQL（`deploy/migrations/rollback/00NN_*.sql`），**不自动执行** |

为什么不引 Alembic：项目只有一条线性单团队迁移队列，Alembic 需额外
`alembic.ini` + `env.py` + 版本目录，且异步引擎配置易错（架构文档 2.1 结论第 6 条已裁决）。

**SQL 目录定位**：容器内为 `/app/deploy`，仓库内为 `<repo>/deploy`，因此按
`OA_DB_SQL_DIR` → `/app/deploy` → 仓库根 `deploy` → 当前工作目录 `deploy` 顺序探测。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import configure_logging, get_logger
from app.db.engine import normalize_database_url

logger = get_logger(__name__)

#: 基线脚本文件名
BASELINE_FILENAME: Final[str] = "init-schema-phase2.sql"

#: 基线版本号（与 `deploy/init-schema-phase2.sql` 内种子行一致）
BASELINE_VERSION: Final[str] = "0001_phase2_init"

#: 基线种子行的哨兵 checksum（由应用在执行时替换为真实值）
MANAGED_CHECKSUM_SENTINEL: Final[str] = "sha256:managed-by-app-db-migrate"

#: 增量脚本目录名
MIGRATIONS_DIRNAME: Final[str] = "migrations"

#: 容器内 SQL 目录
CONTAINER_SQL_DIR: Final[Path] = Path("/app/deploy")


@dataclass(frozen=True, slots=True)
class Migration:
    """一个迁移脚本。

    Attributes:
        version: 版本号（基线为 `0001_phase2_init`，增量为文件名去 `.sql`）。
        path: 脚本绝对路径。
        checksum: 脚本内容的 `sha256:<hex>` 摘要。
    """

    version: str
    path: Path
    checksum: str


@dataclass(slots=True)
class ApplyPlan:
    """迁移计划。

    Attributes:
        pending: 待应用脚本（按版本升序）。
        drifted: 已应用但文件内容已变的版本（启动必须失败）。
        unapplied_existing: 已存在脚本但版本表无记录的版本（等价于 pending）。
        checked: 已应用且校验通过的版本数。
    """

    pending: list[Migration] = field(default_factory=list)
    drifted: list[tuple[str, str, str]] = field(default_factory=list)
    checked: int = 0

    @property
    def is_consistent(self) -> bool:
        """版本表与脚本文件是否一致（无漂移）。"""
        return not self.drifted


class MigrationDatabase(Protocol):
    """迁移数据库访问契约。

    抽象目的：`--check` 的版本比对逻辑可在无 PostgreSQL 环境下用测试替身验证，
    真实实现基于 asyncpg 的**简单查询协议**（可一次执行多语句脚本，含 `DO $$` 块）。
    """

    async def fetch_applied(self) -> dict[str, str]:
        """读取已应用版本与 checksum。"""
        ...

    async def execute_script(self, sql: str) -> None:
        """执行一个完整的 SQL 脚本（脚本自带事务控制）。"""
        ...

    async def record(self, version: str, checksum: str) -> None:
        """记录版本与 checksum（幂等 upsert）。"""
        ...

    async def close(self) -> None:
        """释放连接。"""
        ...


def checksum_of(path: Path) -> str:
    """计算脚本文件的内容摘要。

    Args:
        path: 脚本路径。

    Returns:
        str: `sha256:<hex>` 形式的摘要。
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def candidate_sql_dirs(explicit: str | Path | None = None) -> list[Path]:
    """按优先级返回候选 SQL 目录。

    优先级：显式参数 > `OA_DB_SQL_DIR`（`settings.db_sql_dir`）> 容器 `/app/deploy`
    > 仓库根 `deploy` > 当前工作目录 `deploy`。

    Args:
        explicit: 显式指定的目录。

    Returns:
        list[Path]: 去重后的候选目录（保持优先级顺序）。
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_dir = get_settings().db_sql_dir.strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(CONTAINER_SQL_DIR)

    module_path = Path(__file__).resolve()
    # app/db/migrate.py → app/db → app → apps/adapter → apps → <repo>
    for depth in (5, 4, 3):
        if len(module_path.parents) > depth:
            candidates.append(module_path.parents[depth] / "deploy")
    candidates.append(Path.cwd() / "deploy")

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def resolve_sql_dir(explicit: str | Path | None = None) -> Path:
    """定位可用的 SQL 目录（必须包含基线脚本或增量目录）。

    Args:
        explicit: 显式指定的目录。

    Returns:
        Path: 首个可用目录。

    Raises:
        ApiError: 所有候选目录都不可用（30004）。
    """
    candidates = candidate_sql_dirs(explicit)
    for candidate in candidates:
        if (candidate / BASELINE_FILENAME).is_file():
            return candidate
        if (candidate / MIGRATIONS_DIRNAME).is_dir():
            return candidate
    raise ApiError(
        ErrorCode.CONFIG_ERROR,
        internal_detail=(
            "no migration sql directory found; checked: "
            + ", ".join(str(item) for item in candidates)
        ),
    )


def discover_migrations(sql_dir: Path) -> list[Migration]:
    """发现基线脚本与增量脚本。

    Args:
        sql_dir: SQL 目录。

    Returns:
        list[Migration]: 基线在前、增量按版本升序。
    """
    found: list[Migration] = []
    baseline = sql_dir / BASELINE_FILENAME
    if baseline.is_file():
        found.append(
            Migration(version=BASELINE_VERSION, path=baseline, checksum=checksum_of(baseline))
        )
    migrations_dir = sql_dir / MIGRATIONS_DIRNAME
    if migrations_dir.is_dir():
        for path in sorted(migrations_dir.glob("*.sql")):
            if not path.is_file():
                continue
            found.append(Migration(version=path.stem, path=path, checksum=checksum_of(path)))
    found.sort(key=lambda item: item.version)
    return found


def build_plan(available: list[Migration], applied: dict[str, str]) -> ApplyPlan:
    """比对已应用版本与可用脚本，生成迁移计划。

    Args:
        available: 可用脚本（升序）。
        applied: 已应用版本 → checksum。

    Returns:
        ApplyPlan: 计划结果（`drifted` 非空表示必须中止）。
    """
    plan = ApplyPlan()
    for migration in available:
        recorded = applied.get(migration.version)
        if recorded is None:
            plan.pending.append(migration)
            continue
        if recorded == MANAGED_CHECKSUM_SENTINEL:
            # 基线种子行由 initdb 写入，checksum 交由应用在执行时接管
            plan.checked += 1
            continue
        if recorded != migration.checksum:
            plan.drifted.append((migration.version, recorded, migration.checksum))
            continue
        plan.checked += 1
    return plan


class AsyncpgMigrationDatabase:
    """基于 asyncpg 的迁移数据库实现。

    使用 asyncpg 的**简单查询协议**（无参数调用 `execute`）执行脚本，
    这样 `DO $$ ... $$` 块与多语句脚本都能一次执行完。
    """

    def __init__(self, dsn: str) -> None:
        """初始化。

        Args:
            dsn: `postgresql://` 形式的连接串。
        """
        self._dsn = dsn
        self._connection: object | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> AsyncpgMigrationDatabase:
        """按配置构造。

        Args:
            settings: 配置实例。

        Returns:
            AsyncpgMigrationDatabase: 数据库实现实例。
        """
        url = normalize_database_url(settings.database_url)
        return cls(url.replace("postgresql+asyncpg://", "postgresql://", 1))

    async def _connect(self) -> object:
        """惰性建立连接。

        Returns:
            object: asyncpg 连接对象。
        """
        if self._connection is None:
            import asyncpg

            self._connection = await asyncpg.connect(self._dsn)
        return self._connection

    async def fetch_applied(self) -> dict[str, str]:
        """读取已应用版本。

        Returns:
            dict[str, str]: 版本 → checksum；版本表不存在时返回空字典。
        """
        connection = await self._connect()
        exists = await connection.fetchval(  # type: ignore[attr-defined]
            "SELECT to_regclass('public.schema_migration') IS NOT NULL"
        )
        if not exists:
            return {}
        rows = await connection.fetch(  # type: ignore[attr-defined]
            "SELECT version, checksum FROM schema_migration"
        )
        return {str(row["version"]): str(row["checksum"]) for row in rows}

    async def execute_script(self, sql: str) -> None:
        """执行脚本（脚本自带 `BEGIN/COMMIT`）。

        Args:
            sql: 脚本内容。
        """
        connection = await self._connect()
        await connection.execute(sql)  # type: ignore[attr-defined]

    async def record(self, version: str, checksum: str) -> None:
        """记录版本（幂等 upsert，同时覆盖基线哨兵 checksum）。

        Args:
            version: 版本号。
            checksum: 脚本摘要。
        """
        connection = await self._connect()
        await connection.execute(  # type: ignore[attr-defined]
            """
            INSERT INTO schema_migration (version, checksum)
            VALUES ($1, $2)
            ON CONFLICT (version)
            DO UPDATE SET checksum = EXCLUDED.checksum, applied_at = now()
            """,
            version,
            checksum,
        )

    async def close(self) -> None:
        """关闭连接。"""
        if self._connection is not None:
            await self._connection.close()  # type: ignore[attr-defined]
            self._connection = None


async def check_migrations(
    database: MigrationDatabase,
    sql_dir: Path | None = None,
) -> ApplyPlan:
    """校验迁移状态（`--check`）。

    Args:
        database: 迁移数据库实现。
        sql_dir: SQL 目录，缺省自动定位。

    Returns:
        ApplyPlan: 计划结果。
    """
    directory = sql_dir or resolve_sql_dir()
    available = discover_migrations(directory)
    applied = await database.fetch_applied()
    plan = build_plan(available, applied)
    logger.info(
        "migration_check_completed",
        sql_dir=str(directory),
        available=len(available),
        applied=len(applied),
        pending=len(plan.pending),
        drifted=len(plan.drifted),
    )
    return plan


async def apply_migrations(
    database: MigrationDatabase,
    sql_dir: Path | None = None,
) -> ApplyPlan:
    """增量应用迁移（`--apply`）。

    Args:
        database: 迁移数据库实现。
        sql_dir: SQL 目录，缺省自动定位。

    Returns:
        ApplyPlan: 应用前的计划（`pending` 已全部执行完毕）。

    Raises:
        ApiError: 存在 checksum 漂移时中止（30004）。
    """
    directory = sql_dir or resolve_sql_dir()
    available = discover_migrations(directory)
    applied = await database.fetch_applied()
    plan = build_plan(available, applied)
    if plan.drifted:
        for version, recorded, current in plan.drifted:
            logger.error(
                "migration_checksum_drift",
                version=version,
                recorded=recorded,
                current=current,
            )
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                "migration checksum drift detected: " + ", ".join(item[0] for item in plan.drifted)
            ),
        )

    for migration in plan.pending:
        script = migration.path.read_text(encoding="utf-8")
        logger.info("migration_applying", version=migration.version, path=str(migration.path))
        await database.execute_script(script)
        await database.record(migration.version, migration.checksum)
        logger.info("migration_applied", version=migration.version)

    # 基线种子行的 checksum 是 initdb 写入的哨兵值；首次 `--apply` 时接管为真实摘要，
    # 之后任何手工改动都会被 `--check` 判为漂移（防「改库不改脚本」）。
    for migration in available:
        if applied.get(migration.version) == MANAGED_CHECKSUM_SENTINEL:
            await database.record(migration.version, migration.checksum)
            logger.info("migration_checksum_adopted", version=migration.version)

    logger.info(
        "migration_apply_completed",
        sql_dir=str(directory),
        applied_now=len(plan.pending),
        already_applied=plan.checked,
    )
    return plan


def _build_database(settings: Settings) -> MigrationDatabase:
    """按配置构造迁移数据库实现。

    Args:
        settings: 配置实例。

    Returns:
        MigrationDatabase: 数据库实现。

    Raises:
        ApiError: `business_store=memory`（无数据库可用，30004）。
    """
    if not settings.uses_postgres:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"migrations require OA_BUSINESS_STORE=postgres (current={settings.business_store})"
            ),
        )
    return AsyncpgMigrationDatabase.from_settings(settings)


async def run_startup_migrations(settings: Settings | None = None) -> ApplyPlan | None:
    """启动期迁移（`OA_DB_AUTO_MIGRATE=true` 且 `business_store=postgres` 时才执行）。

    Args:
        settings: 可选配置实例。

    Returns:
        ApplyPlan | None: 跳过时返回 None，否则返回执行计划。
    """
    conf = settings or get_settings()
    if not conf.uses_postgres or not conf.db_auto_migrate:
        logger.info(
            "migration_skipped",
            business_store=conf.business_store,
            db_auto_migrate=conf.db_auto_migrate,
        )
        return None

    database = _build_database(conf)
    try:
        return await apply_migrations(database)
    finally:
        await database.close()


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    Returns:
        argparse.ArgumentParser: 解析器。
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.db.migrate",
        description="阶段二业务库迁移执行器（基线 + 增量，checksum 校验）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="只校验版本与 checksum，不执行 SQL")
    group.add_argument("--apply", action="store_true", help="增量执行未应用的迁移脚本")
    parser.add_argument("--sql-dir", default="", help="SQL 目录（缺省自动定位 deploy/）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果（CI 用）")
    return parser


async def _run_cli(argv: list[str] | None = None) -> int:
    """命令行入口实现。

    Args:
        argv: 命令行参数（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 进程退出码（0 成功，非 0 失败）。
    """
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)

    sql_dir = Path(args.sql_dir) if args.sql_dir else None
    try:
        directory = resolve_sql_dir(sql_dir)
        database = _build_database(settings)
    except ApiError as exc:
        logger.error("migration_cli_failed", detail=exc.log_message)
        print(f"[FAIL] {exc.internal_detail or exc.log_message}")
        return 2

    try:
        plan = (
            await check_migrations(database, directory)
            if args.check
            else await apply_migrations(database, directory)
        )
    except ApiError as exc:
        print(f"[FAIL] {exc.internal_detail or exc.log_message}")
        return 1
    finally:
        await database.close()

    if not plan.is_consistent:
        for version, recorded, current in plan.drifted:
            print(f"[FAIL] 版本 {version} checksum 漂移: recorded={recorded} current={current}")
        return 1

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "sql_dir": str(directory),
                    "pending": [item.version for item in plan.pending],
                    "already_applied": plan.checked,
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(f"[OK]   SQL 目录: {directory}")
    print(f"[OK]   已应用并校验通过: {plan.checked}")
    if args.check:
        if plan.pending:
            print(f"[WARN] 待应用: {[item.version for item in plan.pending]}")
        else:
            print("[OK]   无待应用迁移")
    else:
        print(f"[OK]   本次应用: {[item.version for item in plan.pending]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数。

    Returns:
        int: 进程退出码。
    """
    return asyncio.run(_run_cli(argv))


if __name__ == "__main__":  # pragma: no cover - 进程入口
    sys.exit(main())
