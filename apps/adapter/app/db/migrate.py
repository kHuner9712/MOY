"""迁移执行器（架构文档 3.7：编号 SQL 文件 + 版本表，不引入 Alembic）。

口径：

| 项 | 约定 |
| --- | --- |
| 基线脚本 | `deploy/init-schema-phase2.sql`（幂等全量；容器首次启动由 initdb `20-*` 执行） |
| 增量脚本 | `deploy/migrations/00NN_<name>.sql`（单事务、`BEGIN/COMMIT` 内置） |
| 版本表 | `schema_migration(version, checksum, applied_at)` |
| `--check` | 只校验：缺失版本、checksum 漂移（已应用版本文件被手工改动） |
| `--apply` | 增量执行未应用的脚本，逐脚本记录 `version + checksum` |
| `--rollback <version…>` | 倒序执行 `rollback/NNNN_*.sql` 并删台账行，**不自动级联** |
| 回滚 | 手写反向 SQL（`deploy/migrations/rollback/00NN_*.sql`），**不自动执行** |
| 并发 | `pg_advisory_xact_lock(7326017)`：多副本同时 `--apply` 只有一个在跑 DDL |
| 孤儿台账 | 有记录无脚本 → 每次运行**列出**（`[WARN]`）；`--prune-orphans [--yes]` 清理 |
| 版本改名 | `MIGRATION_ALIASES` 把旧版本号认领到新脚本（`0002_billing_callback_unique` → 现名） |

**为什么必须加迁移锁（阶段四 T4-01.4）**：多副本滚动发布时，每个副本启动都会
按 `OA_DB_AUTO_MIGRATE` 判断要不要 `--apply`（compose 已改独立一次性 Job，但
"多实例同时跑迁移"仍是运维现实）。没有锁时两个实例会同时执行同一份 DDL：
`ALTER TABLE ... ADD COLUMN` 有 `IF NOT EXISTS` 兜底，`INSERT` 种子与
`CREATE INDEX CONCURRENTLY` 则没有——表现为「其中一方随机失败」，而失败方
往往已经写了一半个事务。

**为什么 DDL 与台账同事务**：写台账失败（或进程被杀）会让「表结构变了、台账没记」
成为永久状态，下一次 `--apply` 重跑 DDL、`--check` 又认为缺失——两者的结论互相矛盾，
排障时最容易被误判成"迁移文件被改过"。

为什么不引 Alembic：项目只有一条线性单团队迁移队列，Alembic 需额外
`alembic.ini` + `env.py` + 版本目录，且异步引擎配置易错（架构文档 2.1 结论第 6 条已裁决）。

**SQL 目录定位**：容器内为 `/app/deploy`，仓库内为 `<repo>/deploy`，因此按
`OA_DB_SQL_DIR` → `/app/deploy` → 仓库根 `deploy` → 当前工作目录 `deploy` 顺序探测。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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

#: 迁移互斥锁 ID（`pg_advisory_xact_lock` 的键）。
#:
#: 任意固定常量，但**跨进程、跨版本必须一致**——改了它等于让新旧版本各锁各的，
#: 锁就失去意义。7326017 是"officeai migration"的一个随手常量。
MIGRATION_LOCK_KEY: Final[int] = 7326017

#: 回滚脚本目录名（迁移目录下的子目录）
ROLLBACK_DIRNAME: Final[str] = "rollback"

#: **回滚前强制备份**的版本前缀（架构 §10.2 / R-4）。
#:
#: 只对「down 会删表/删数据」的迁移生效（0008 会话、0009 记忆…）。理由：
#: `down` 把数据改坏比"不能回滚"更糟——回滚失败最多是原地不动，回滚成功但丢数据
#: 是不可逆的。所以这几条必须先把整库 dump 出来，dump 不成功就**拒绝执行**。
#:
#: 0006/0007 只 drop 列，虽也丢值，但属"可重新采集"的派生数据（真实 MIME、
#: 计量维度），按架构口径不纳入强制备份。
ROLLBACK_REQUIRES_BACKUP: Final[frozenset[str]] = frozenset({"0008", "0009"})


def rollback_requires_backup(version: str) -> bool:
    """该版本的回滚是否必须先备份。

    Args:
        version: 版本号（形如 `0008_conversation_message`）。

    Returns:
        bool: 需要备份返回 True。
    """
    return version[:4] in ROLLBACK_REQUIRES_BACKUP


#: 事务控制语句（**只认行首语句**）
#:
#: 不能用 `"BEGIN" in sql.upper()` 判断"脚本自带事务"：`DO $guard$ BEGIN … END $guard$`
#: 里的 `BEGIN` 是 PL/pgSQL 的块开始关键字，不是事务语句。把它误判成"旧脚本"的后果是
#: 所有 0003 起的守卫式迁移都退化成"会话锁 + 两步"，DDL 与台账就不再同事务——
#: 正是 T4-01.4 要修的那个缺陷本身（实测踩过：0006/0007 全被判成旧脚本）。
TRANSACTION_CONTROL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;", re.IGNORECASE | re.MULTILINE
)


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


#: 版本改名别名（旧版本号 → 现脚本版本号）。
#:
#: 为什么需要：`0002` 曾叫 `0002_billing_callback_unique`，改名后旧库里留着旧名台账行。
#: 不改别名的话，旧库要么报「版本表有记录但无文件」（沉默通过，换台机器才发现不一致），
#: 要么逼所有人手工改台账——前者危险、后者不可运维。
MIGRATION_ALIASES: dict[str, str] = {
    "0002_billing_callback_unique": "0002_callback_idempotency_selfcheck",
}


@dataclass(slots=True)
class ApplyPlan:
    """迁移计划。

    Attributes:
        pending: 待应用脚本（按版本升序）。
        drifted: 已应用但文件内容已变的版本（启动必须失败）。
        unapplied_existing: 已存在脚本但版本表无记录的版本（等价于 pending）。
        checked: 已应用且校验通过的版本数。
        orphans: 版本表里有记录、但磁盘上**已无对应脚本**的版本（含别名已指向新名的旧名）。
        aliased: 通过别名映射认领的旧版本号（`旧名 → 新名`），不算孤儿。
    """

    pending: list[Migration] = field(default_factory=list)
    drifted: list[tuple[str, str, str]] = field(default_factory=list)
    checked: int = 0
    orphans: list[str] = field(default_factory=list)
    aliased: list[tuple[str, str]] = field(default_factory=list)

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

    async def apply_migration(self, migration: Migration) -> bool:
        """在**迁移锁内**执行该脚本并记录台账；已被他人应用时跳过。

        Returns:
            bool: 本次真的执行了 DDL 返回 True；判定为"已应用"返回 False。
        """
        ...

    async def apply_rollback(self, version: str, sql: str) -> bool:
        """在**迁移锁内**执行回滚脚本并删除台账行。

        Returns:
            bool: 本次真的执行了回滚返回 True；版本未应用返回 False。
        """
        ...

    async def record(self, version: str, checksum: str) -> None:
        """记录版本与 checksum（幂等 upsert）。"""
        ...

    async def delete_versions(self, versions: Sequence[str]) -> int:
        """删除指定台账行（**只用于清理孤儿**），返回删除条数。"""
        ...

    async def close(self) -> None:
        """释放连接。"""
        ...


def script_manages_transaction(sql_text: str) -> bool:
    """脚本是否**自带**事务控制（行首的 `BEGIN;` / `COMMIT;`）。

    Args:
        sql_text: 脚本内容。

    Returns:
        bool: 自带事务控制返回 True（迁移执行器就不能再包一层事务）。
    """
    return TRANSACTION_CONTROL_PATTERN.search(sql_text) is not None


def checksum_of(path: Path) -> str:
    """计算脚本文件的内容摘要（**按 LF 规范化后计算**，跨平台稳定）。

    为什么不是 `read_bytes()`：`.gitattributes` 已声明 `*.sql text eol=lf`，所以
    **仓库里存的永远是 LF**；但编辑器/同步工具完全可能把工作区文件写成 CRLF。
    此时 `read_bytes()` 会把同一份脚本算成两个摘要，`--apply` 报
    `migration checksum drift`（30004）——运维看到的是"迁移内容被改了"，
    实际只是换行符，排查成本极高（阶段三实测踩过：`0003`/`0004` 台账是 LF、
    工作区被写成 CRLF，三个 `--run-pg` 用例全红）。

    规范化后：同一份脚本的摘要与它用哪种换行符落盘无关，而**已存在的台账值不变**
    （当初落盘就是 LF，摘要即 LF 版本的摘要）——所以这是一次零迁移成本的加固。

    Args:
        path: 脚本路径。

    Returns:
        str: `sha256:<hex>` 形式的摘要。
    """
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
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
    shipped = {migration.version for migration in available}
    for recorded_version in sorted(applied):
        if recorded_version in shipped:
            continue
        target = MIGRATION_ALIASES.get(recorded_version)
        if target is not None and target in shipped:
            # 旧名已被新脚本认领：不重复执行，也不当孤儿（只需人工确认后可 prune）
            plan.aliased.append((recorded_version, target))
            continue
        # 已记录但**盘上没有对应文件**：沉默通过比报错危险（换台机器就两边不一致）
        plan.orphans.append(recorded_version)
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

    async def _is_applied(self, version: str) -> bool:
        """台账里是否已有该版本（表不存在视为未应用）。

        Args:
            version: 版本号。

        Returns:
            bool: 已应用返回 True。
        """
        connection = await self._connect()
        exists = await connection.fetchval(  # type: ignore[attr-defined]
            "SELECT to_regclass('public.schema_migration') IS NOT NULL"
        )
        if not exists:
            return False
        row = await connection.fetchval(  # type: ignore[attr-defined]
            "SELECT 1 FROM schema_migration WHERE version = $1", version
        )
        return row is not None

    async def apply_migration(self, migration: Migration) -> bool:
        """在迁移锁内执行脚本并记录台账（新脚本 DDL+台账同事务）。

        **锁内二次判定**是必要的：`apply_migrations` 的 `pending` 是**开跑前**
        算出来的；等到真正拿到锁时，另一个副本可能已经跑完了同一个版本。
        不重判就会重复执行 DDL（`ALTER ... IF NOT EXISTS` 无害，但 `INSERT` 种子
        与 `CREATE INDEX CONCURRENTLY` 不是）。

        旧脚本（0002-0005）自带 `BEGIN/COMMIT`：**不能**包在 `conn.transaction()`
        里——脚本里的 `COMMIT` 会结束外层事务，asyncpg 的 transaction 对象随后
        在退出时找不到事务而报错。所以旧脚本走"会话级 advisory lock + 两步"，
        但两步**都在锁内**，仍然互斥；新脚本走事务级锁 + 单事务。

        Args:
            migration: 待应用的迁移。

        Returns:
            bool: 执行了返回 True；锁内判定已应用返回 False。
        """
        script = migration.path.read_text(encoding="utf-8")
        # 新脚本不写 BEGIN/COMMIT（由本方法包裹）；只有**真的**含事务语句才算旧脚本
        tx_managed = not script_manages_transaction(script)
        connection = await self._connect()

        if tx_managed:
            async with connection.transaction():  # type: ignore[attr-defined]
                await connection.execute(  # type: ignore[attr-defined]
                    f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK_KEY})"
                )
                if await self._is_applied(migration.version):
                    logger.info("migration_skipped_after_lock", version=migration.version)
                    return False
                await connection.execute(script)  # type: ignore[attr-defined]
                await self.record(migration.version, migration.checksum)
            return True

        # 旧脚本：会话级锁（随 unlock/连接关闭释放），DDL 与台账仍在同一临界区
        await connection.execute(  # type: ignore[attr-defined]
            f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})"
        )
        try:
            if await self._is_applied(migration.version):
                logger.info("migration_skipped_after_lock", version=migration.version)
                return False
            await connection.execute(script)  # type: ignore[attr-defined]
            await self.record(migration.version, migration.checksum)
            return True
        finally:
            await connection.execute(  # type: ignore[attr-defined]
                f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})"
            )

    async def apply_rollback(self, version: str, sql: str) -> bool:
        """在迁移锁内执行回滚脚本并删台账行（同一事务）。

        回滚脚本一律按「由 migrate.py 包裹」处理（v1.0 起新增的 rollback 文件
        都不写 BEGIN/COMMIT）；若有人写了 BEGIN，直接报错而不是嵌套执行——
        嵌套会静默把外层事务提交掉，让"回滚失败但台账已删"成为可能。

        Args:
            version: 被回滚的版本号。
            sql: 回滚脚本内容。

        Returns:
            bool: 执行了返回 True；版本未应用返回 False。

        Raises:
            ApiError: 回滚脚本自带了 BEGIN/COMMIT（30004）。
        """
        if script_manages_transaction(sql):
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    f"rollback script for {version} must not contain BEGIN/COMMIT"
                    "（由 migrate.py 包裹在同一事务内）"
                ),
            )
        connection = await self._connect()
        async with connection.transaction():  # type: ignore[attr-defined]
            await connection.execute(  # type: ignore[attr-defined]
                f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK_KEY})"
            )
            if not await self._is_applied(version):
                logger.info("rollback_skipped_not_applied", version=version)
                return False
            await connection.execute(sql)  # type: ignore[attr-defined]
            await self.delete_versions([version])
        return True

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

    async def delete_versions(self, versions: Sequence[str]) -> int:
        """删除指定台账行（**仅孤儿清理入口**使用，不动其他行）。

        Args:
            versions: 版本号列表。

        Returns:
            int: 实际删除条数。
        """
        if not versions:
            return 0
        connection = await self._connect()
        result = await connection.execute(  # type: ignore[attr-defined]
            "DELETE FROM schema_migration WHERE version = ANY($1::text[])", list(versions)
        )
        return int(str(result).split()[-1] or 0)

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
        logger.info("migration_applying", version=migration.version, path=str(migration.path))
        executed = await database.apply_migration(migration)
        if executed:
            logger.info("migration_applied", version=migration.version)
        else:
            # 锁内二次判定命中：另一个副本先跑完了，本次不重复执行 DDL
            logger.info("migration_applied_elsewhere", version=migration.version)

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


def discover_rollback_scripts(sql_dir: Path) -> dict[str, Path]:
    """发现回滚脚本（`deploy/migrations/rollback/NNNN_*.sql` → 版本号）。

    目录约定是**迁移目录下的子目录**（`migrations/rollback/`），不是 SQL 根目录下的
    `rollback/`——回滚脚本与它要撤销的迁移必须放在一起，否则把 `deploy/migrations/`
    整体拷到别的机器时，down 会被漏掉（`migrate.py:12` 的明文约定）。

    Args:
        sql_dir: SQL 根目录（含 `migrations/`）。

    Returns:
        dict[str, Path]: 版本号（= 文件名去 `.sql`）→ 路径。
    """
    directory = sql_dir / MIGRATIONS_DIRNAME / ROLLBACK_DIRNAME
    scripts: dict[str, Path] = {}
    if not directory.is_dir():
        return scripts
    for path in sorted(directory.glob("*.sql")):
        if not path.is_file():
            continue
        # 命名约定 `NNNN_name.down.sql` → 版本号是 `NNNN_name`（去掉 `.down`）。
        # 不去掉的话 `--rollback NNNN_name` 永远匹配不上，运维只会看到
        # "找不到回滚脚本"，而文件明明就在那里。
        scripts[path.stem.removesuffix(".down")] = path
    return scripts


async def rollback_migrations(
    database: MigrationDatabase,
    versions: Sequence[str],
    sql_dir: Path | None = None,
    *,
    backup: Callable[[str], bool] | None = None,
) -> list[str]:
    """回滚指定版本（**倒序**执行，不自动级联）。

    「不自动级联」是刻意的：`--rollback 0006` 只回滚 0006，**不会**顺手把 0007 也退了。
    级联回滚会让"我想试一下某个迁移能不能退"变成"把后续三个版本一起退了"，
    而每个 down 都可能丢数据（见各 down 文件头部的 ⚠）。

    因此如果还存在**更晚**的已应用版本，直接拒绝并列出它们——要求使用者逐个显式回滚。

    **强制备份（R-4）**：`0008`/`0009` 这类 down 会删表的版本，必须先备份。
    `backup` 回调返回 False（或压根没提供）即**中止**——「回滚成功但丢数据」比
    「回滚不了」更糟：前者不可逆，后者只是原地不动。

    Args:
        database: 迁移数据库实现。
        versions: 要回滚的版本号列表。
        sql_dir: SQL 目录，缺省自动定位。
        backup: 备份回调（入参版本号，返回是否成功）；仅对需要备份的版本调用。

    Returns:
        list[str]: 实际回滚的版本（倒序）。

    Raises:
        ApiError: 版本无 down 脚本、仍有更晚版本已应用、或强制备份失败（30004）。
    """
    directory = sql_dir or resolve_sql_dir()
    scripts = discover_rollback_scripts(directory)
    applied = await database.fetch_applied()

    ordered = sorted(set(versions), reverse=True)
    missing_scripts = [version for version in ordered if version not in scripts]
    if missing_scripts:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                "找不到回滚脚本（deploy/migrations/rollback/NNNN_*.sql）："
                + ", ".join(missing_scripts)
            ),
        )

    rolled_back: list[str] = []
    for version in ordered:
        if version not in applied:
            logger.info("rollback_skipped_not_applied", version=version)
            continue
        later = sorted(item for item in applied if item > version)
        if later:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    f"拒绝回滚 {version}：更晚的版本仍处于已应用状态 {later}。"
                    "本命令不自动级联，请先逐个回滚它们（按版本号倒序）。"
                ),
            )
        if rollback_requires_backup(version):
            if backup is None:
                raise ApiError(
                    ErrorCode.CONFIG_ERROR,
                    internal_detail=(
                        f"拒绝回滚 {version}：该版本会删表/删数据，必须先备份。"
                        "本次调用未提供备份能力（CLI 需要可用的 pg_dump）。"
                    ),
                )
            if not backup(version):
                raise ApiError(
                    ErrorCode.CONFIG_ERROR,
                    internal_detail=(
                        f"拒绝回滚 {version}：备份失败或产物为空。"
                        "回滚失败只是原地不动，回滚丢数据不可逆——先修好备份再回滚。"
                    ),
                )
            logger.info("rollback_backup_succeeded", version=version)
        sql = scripts[version].read_text(encoding="utf-8")
        logger.info("rollback_applying", version=version, path=str(scripts[version]))
        executed = await database.apply_rollback(version, sql)
        if executed:
            rolled_back.append(version)
            applied.pop(version, None)
            logger.info("rollback_applied", version=version)
    return rolled_back


def _backup_before_rollback(settings: Settings, sql_dir: Path, version: str) -> bool:
    """回滚前整库备份（R-4，`pg_dump -Fc`）。

    为什么是**整库**而不是只 dump 目标表：down 脚本里的 DDL 可能连带影响索引、
    序列、策略；只 dump 两张表看起来更"精准"，实际恢复时经常缺东西。整库 dump
    在私有化规模（单库几十 GB 以内）完全可接受，且恢复路径唯一。

    Args:
        settings: 配置实例（取 DSN）。
        sql_dir: SQL 根目录（备份落在 `migrations/rollback/<ts>/`）。
        version: 即将回滚的版本号（用于文件名）。

    Returns:
        bool: 备份成功且产物非空返回 True。
    """
    directory = (
        sql_dir
        / MIGRATIONS_DIRNAME
        / ROLLBACK_DIRNAME
        / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"before_{version}.dump"
    dsn = normalize_database_url(settings.database_url).replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    # 用 `shutil.which` 解析成绝对路径：容器与客户机上的安装位置不同（写死绝对路径
    # 会让同一份代码在两种环境里各挂一半），而 `which` 的结果作为**绝对路径**传给
    # subprocess，既跨环境可用，又不留"靠 PATH 执行任意同名程序"的口子。
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        logger.error("rollback_backup_pg_dump_missing", version=version)
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - 可执行路径已解析为绝对路径
            [pg_dump, "--no-owner", "-Fc", "-f", str(target), dsn],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        logger.error("rollback_backup_pg_dump_missing", version=version)
        return False
    if completed.returncode != 0:
        logger.error(
            "rollback_backup_failed", version=version, detail=(completed.stderr or "")[:400]
        )
        return False
    if not target.is_file() or target.stat().st_size == 0:
        logger.error("rollback_backup_empty", version=version, path=str(target))
        return False
    logger.info("rollback_backup_written", version=version, path=str(target))
    return True


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
    group.add_argument(
        "--rollback",
        nargs="+",
        metavar="VERSION",
        help=(
            "回滚指定版本（倒序执行 migrations/rollback/NNNN_*.down.sql 并删台账行）；"
            "不自动级联（更晚版本须显式列出）；0008/0009 回滚前强制 pg_dump 备份"
        ),
    )
    parser.add_argument(
        "--prune-orphans",
        action="store_true",
        help="清理台账里「已无对应脚本」的孤儿行（默认 dry-run，需配 --yes 才真删）",
    )
    parser.add_argument("--yes", action="store_true", help="与 --prune-orphans 配合：确认执行删除")
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

    rolled_back: list[str] | None = None
    plan: ApplyPlan | None
    try:
        if args.rollback:
            # 回滚与 check/apply 是互斥入口：它不产出 ApplyPlan（不需要比对漂移）
            rolled_back = await rollback_migrations(
                database,
                args.rollback,
                directory,
                backup=lambda item: _backup_before_rollback(settings, directory, item),
            )
            plan = None
        elif args.check:
            plan = await check_migrations(database, directory)
        else:
            plan = await apply_migrations(database, directory)
    except ApiError as exc:
        print(f"[FAIL] {exc.internal_detail or exc.log_message}")
        return 1
    finally:
        await database.close()

    if rolled_back is not None:
        if args.json:
            import json

            print(
                json.dumps(
                    {"sql_dir": str(directory), "rolled_back": rolled_back}, ensure_ascii=False
                )
            )
            return 0
        print(f"[OK]   SQL 目录: {directory}")
        if rolled_back:
            print(f"[OK]   已回滚: {rolled_back}（台账行已删除）")
        else:
            print("[OK]   没有需要回滚的版本（均未应用）")
        return 0

    assert plan is not None, "check/apply 分支必然产出计划（rollback 已在上面返回）"

    for old, new in plan.aliased:
        print(f"[INFO] 版本别名 {old} → {new}（旧台账行已由新脚本认领，可 prune）")
    if plan.orphans:
        # 孤儿必须**每次都说出来**：沉默通过会让"另一台机器上不一致"变成谜案
        print(f"[WARN] 台账有 {len(plan.orphans)} 个孤儿版本（磁盘已无对应脚本）：{plan.orphans}")
    if args.prune_orphans:
        candidates = sorted(set(plan.orphans) | {old for old, _ in plan.aliased})
        if not candidates:
            print("[OK]   没有需要清理的孤儿版本")
        elif not args.yes:
            print(f"[DRYRUN] 将删除以下台账行：{candidates}（加 --yes 执行）")
        else:
            removed = await database.delete_versions(candidates)
            print(f"[OK]   已删除 {removed} 条孤儿台账行：{candidates}")

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
