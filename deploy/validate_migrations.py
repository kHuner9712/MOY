#!/usr/bin/env python3
"""迁移门禁：静态自检 + **双路径实跑**（阶段三 B21，`architecture-phase3.md` §10 / G-11）。

**为什么要有这条门禁**（付过两次代价）：

- 阶段三 `0004` 首版收尾写成 `END $$`（与开头的 `DO $guard$` 不配对）→ PostgreSQL
  报 `unterminated dollar-quoted string`，**空库路径整条迁移语法失败**。
  守卫块写坏了反而比不写更脆：加了守卫却不闭合，等于把"跳过"变成"语法崩"。
- 同期 `0004` 无条件 `ALTER TABLE ADD COLUMN` → 已初始化库上应用角色报
  `must be owner of table`（initdb 由超级用户建表）。**只跑空库 = 只验了一条路径**。

由此固化两条纪律，本脚本把它们变成机械判据：

- **路径 A（空库）**：应用角色自建、无任何表 ——
  暴露 **语法崩**、旧版 init 脚本漏列。
- **路径 B（已初始化库）**：initdb 跑过 `init-schema-*.sql`，表属主是超级用户 ——
  暴露 **属主崩**、守卫缺失。
- **路径 C（回滚往返）**：在 B 的库上 `--rollback 0007 0006` → `--apply` ——
  暴露 **down 脚本空转**、回滚后升不回去。

**三条路径都必须绿**——只跑一条等于没验。A 缺库时用 `--run-pg` 自建（需要超级用户
连接串），B/C 直接打 `OA_TEST_DATABASE_URL` 指向的那台库。路径 C 是阶段四新增：
它验证的是「升级出问题 → 回滚 → 修好 → 再升级」这条真实运维链路，
而不是单次的 `--apply`。

用法::

    python deploy/validate_migrations.py                 # 静态自检（无需 PG，CI 默认）
    python deploy/validate_migrations.py --run-pg        # 追加双路径实跑（需 PG）
    python deploy/validate_migrations.py --self-test     # 负样本自检（期望退出码 1）
    python deploy/validate_migrations.py --json          # 机器可读输出

退出码：0 = 通过；1 = 发现违规或自检命中；2 = 门禁自身异常（目录缺失 / 连不上库）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Final

#: 仓库根目录
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 适配层根目录（跑 `python -m app.db.migrate` 的 cwd）
ADAPTER_ROOT: Final[Path] = REPO_ROOT / "apps" / "adapter"

#: 迁移目录
MIGRATIONS_DIR: Final[Path] = REPO_ROOT / "deploy" / "migrations"

#: 基线脚本（initdb 阶段执行，不是迁移）
BASELINE_PHASE2: Final[Path] = REPO_ROOT / "deploy" / "init-schema-phase2.sql"
BASELINE_PHASE3: Final[Path] = REPO_ROOT / "deploy" / "init-schema-phase3.sql"

#: 迁移文件命名（`0002_callback_idempotency_selfcheck.sql`）
MIGRATION_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

#: 美元引号标签（`$$` 或 `$guard$`）
DOLLAR_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")

#: 允许 `/api/v1` 之外的迁移版本号（0001 是基线别名，由 `MIGRATION_ALIASES` 认领）
RESERVED_VERSIONS: Final[frozenset[str]] = frozenset({"0001"})

#: 视为「DDL」的关键字（去掉注释后仍有命中，才要求守卫）
DDL_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("ALTER TABLE", re.compile(r"\bALTER\s+TABLE\b", re.IGNORECASE)),
    ("CREATE TABLE", re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)),
    ("CREATE INDEX", re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b", re.IGNORECASE)),
    ("DROP TABLE", re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE)),
)

#: 守卫写法（出现任一即认为"先判存在再动手"）
GUARD_HINTS: Final[tuple[str, ...]] = (
    "to_regclass",
    "information_schema.columns",
    "IF NOT EXISTS",
    "IF EXISTS",
)

#: `ALTER TABLE` 必须配的异常处理（把 `insufficient_privilege` 翻译成可照做的文案）
PRIVILEGE_HANDLER: Final[str] = "insufficient_privilege"

#: 阶段三必须存在的 9 张表（少一张说明 init 脚本被改漏）
PHASE3_TABLES: Final[tuple[str, ...]] = (
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

#: B19 新增的三列（init 脚本必须含，否则新库会被 0004 判"缺列"）
PHASE3_REQUIRED_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("plugin_task", "confirmed_step_seq"),
    ("plugin_artifact", "version"),
    ("plugin_artifact", "version_history"),
)

#: 应用角色 / 超级用户角色（与 `deploy/init-app-role.sql` 一致）
APP_ROLE: Final[str] = "officeai_app"
SUPERUSER_ROLE: Final[str] = "officeai"

#: 测试环境变量
APP_DSN_ENV: Final[str] = "OA_TEST_DATABASE_URL"
SUPERUSER_DSN_ENV: Final[str] = "OA_TEST_SUPERUSER_DATABASE_URL"


class Violation:
    """一条门禁违规（自带定位信息，便于 CI 输出直接可读）。"""

    def __init__(self, where: str, reason: str) -> None:
        """初始化。

        Args:
            where: 定位（文件名 / 版本号）。
            reason: 业务语言原因。
        """
        self.where = where
        self.reason = reason

    def render(self) -> str:
        """渲染为一行文本。

        Returns:
            str: `[FAIL] where: reason` 形式。
        """
        return f"[FAIL] {self.where}: {self.reason}"

    def as_dict(self) -> dict[str, str]:
        """转成可 JSON 序列化的字典。

        Returns:
            dict[str, str]: 字段字典。
        """
        return {"where": self.where, "reason": self.reason}


# ===========================================================================
# 静态判据（不需要数据库）
# ===========================================================================
#: 单引号字符串字面量（含 `''` 转义）
SQ_LITERAL_PATTERN: Final[re.Pattern[str]] = re.compile(r"'(?:[^']|'')*'")

#: 块注释 `/* ... */`
BLOCK_COMMENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"/\*.*?\*/", re.DOTALL)

#: 行注释 `-- ...`
LINE_COMMENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"--.*$", re.MULTILINE)


def strip_sql_noise(text: str) -> str:
    """去掉注释与字符串字面量，只留下**会真正执行**的 SQL 骨架（行号不变）。

    为什么必须去这两样（两次实测误报）：

    - `0002` 的说明文字里写着 `CREATE UNIQUE INDEX ...`、`0004` 的注释里直接
      引用了那次事故的 `END $$` —— 不去注释，门禁会把"文档"当成"代码"；
    - `0003` 的 `EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t)`
      是**字符串里的** DDL，不涉及表属主问题，不该触发权限判据。

    行号保持不变是有意的：标签不闭合的报告要指到具体行。

    Args:
        text: SQL 原文。

    Returns:
        str: 去注释、去字面量后的骨架（换行保留）。
    """

    def _blank(match: re.Match[str]) -> str:
        """把匹配片段替换为等量换行。"""
        return "\n" * match.group().count("\n")

    skeleton = SQ_LITERAL_PATTERN.sub(lambda m: "''" + "\n" * m.group().count("\n"), text)
    skeleton = BLOCK_COMMENT_PATTERN.sub(_blank, skeleton)
    return LINE_COMMENT_PATTERN.sub("", skeleton)


def check_dollar_quotes(path: Path) -> list[Violation]:
    """校验美元引号标签成对（**按栈配对**，支持 `$guard$ … EXECUTE $ddl$ …`）。

    为什么用栈而不是顺序配对：`0003` 是嵌套结构
    （`DO $guard$ … EXECUTE $ddl$ … $ddl$; … END $guard$;`），顺序配对会把
    合法的嵌套判成违规。栈配对既能通过嵌套，又能抓住 `DO $guard$ … END $$`
    这类"收尾标签写错"的真实事故。

    扫的是去噪后的骨架：注释里引用 `END $$` 是**说明**，不是可执行代码。

    Args:
        path: 迁移文件。

    Returns:
        list[Violation]: 违规清单。
    """
    text = strip_sql_noise(path.read_text(encoding="utf-8"))
    stack: list[tuple[str, int]] = []
    for match in DOLLAR_TAG_PATTERN.finditer(text):
        tag = match.group(1) or ""
        line = text.count("\n", 0, match.start()) + 1
        if stack and stack[-1][0] == tag:
            stack.pop()
            continue
        stack.append((tag, line))
    if stack:
        unclosed = "、".join(f"${tag}$（第 {line} 行）" for tag, line in stack)
        return [Violation(path.name, f"美元引号标签不闭合：{unclosed}")]
    return []


def check_guard_presence(path: Path) -> list[Violation]:
    """校验含 DDL 的迁移必须带守卫，且 `ALTER TABLE` 必须处理权限异常。

    判据（在去噪骨架上判断，注释与字面量里的 DDL 不算）：

    - 出现 `ALTER/CREATE/DROP TABLE`、`CREATE INDEX` → 必须出现守卫写法
      （`to_regclass` / `information_schema.columns` / `IF NOT EXISTS`）；
    - 出现 `ALTER TABLE` → 必须出现 `insufficient_privilege` 处理分支，
      否则应用角色在已初始化库上会拿到裸的 `must be owner of table`。

    Args:
        path: 迁移文件。

    Returns:
        list[Violation]: 违规清单。
    """
    body = strip_sql_noise(path.read_text(encoding="utf-8"))
    present = [label for label, pattern in DDL_PATTERNS if pattern.search(body)]
    if not present:
        return []
    violations: list[Violation] = []
    lowered = body.lower()
    if not any(hint.lower() in lowered for hint in GUARD_HINTS):
        violations.append(
            Violation(
                path.name,
                f"含 DDL（{'/'.join(present)}）但没有存在性守卫"
                "（须先判 to_regclass / information_schema.columns / IF NOT EXISTS）",
            )
        )
    if "ALTER TABLE" in present and PRIVILEGE_HANDLER not in lowered:
        violations.append(
            Violation(
                path.name,
                "含 ALTER TABLE 但没有处理 insufficient_privilege"
                "（已初始化库上应用角色不是表属主，必须给出可照做的替代动作）",
            )
        )
    return violations


def discover_migrations(directory: Path) -> tuple[list[tuple[str, Path]], list[Violation]]:
    """列出迁移脚本并校验版本号。

    Args:
        directory: 迁移目录。

    Returns:
        tuple[list[tuple[str, Path]], list[Violation]]: `(版本, 路径)` 升序列表与违规清单。
    """
    if not directory.is_dir():
        return [], [Violation(str(directory), "迁移目录不存在")]
    found: list[tuple[str, Path]] = []
    violations: list[Violation] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME_PATTERN.match(path.name)
        if match is None:
            violations.append(
                Violation(path.name, "文件名不符合 `NNNN_snake_case.sql`（迁移靠文件名排序）")
            )
            continue
        version = match.group(1)
        if version in RESERVED_VERSIONS:
            violations.append(Violation(path.name, f"版本号 {version} 被基线别名保留"))
            continue
        found.append((version, path))
    versions = [version for version, _ in found]
    if len(set(versions)) != len(versions):
        violations.append(Violation(str(directory), f"迁移版本号重复：{versions}"))
    if versions != sorted(versions):
        violations.append(Violation(str(directory), f"迁移版本号未按升序：{versions}"))
    if not found:
        violations.append(Violation(str(directory), "迁移目录为空（至少要有 0002 起的增量）"))
    return found, violations


def check_init_baseline() -> list[Violation]:
    """校验 init 基线脚本覆盖阶段三的表与 B19 新增列。

    这一条针对的是「新库被判缺列」：`0004` 走"列已存在 → 零 DDL"分支的前提，
    是 `init-schema-phase3.sql` 里真的有这三列。缺一列，新库就会退回"补列"分支，
    而补列在已初始化库上又没有权限——两条路径同时变红，难查。

    Returns:
        list[Violation]: 违规清单。
    """
    violations: list[Violation] = []
    if not BASELINE_PHASE2.is_file():
        violations.append(Violation(BASELINE_PHASE2.name, "阶段二基线脚本缺失"))
    if not BASELINE_PHASE3.is_file():
        violations.append(Violation(BASELINE_PHASE3.name, "阶段三基线脚本缺失"))
        return violations
    text = strip_sql_noise(BASELINE_PHASE3.read_text(encoding="utf-8"))
    for table in PHASE3_TABLES:
        if not re.search(rf"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{table}\b", text, re.I):
            violations.append(Violation(BASELINE_PHASE3.name, f"未建阶段三表 {table}"))
    for table, column in PHASE3_REQUIRED_COLUMNS:
        if not _column_declared(text, table, column):
            violations.append(
                Violation(
                    BASELINE_PHASE3.name,
                    f"表 {table} 缺列 {column}（B19 新增；缺了会让新库被 0004 判「缺列」）",
                )
            )
    return violations


def _column_declared(text: str, table: str, column: str) -> bool:
    """判断 `CREATE TABLE` 语句里是否声明了某列。

    Args:
        text: 已去注释的 SQL。
        table: 表名。
        column: 列名。

    Returns:
        bool: 该表的建表语句里出现过该列返回 True。
    """
    match = re.search(
        rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{table}\s*\((.*?)\n\)\s*;",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return False
    return re.search(rf"^\s*{column}\b", match.group(1), re.IGNORECASE | re.MULTILINE) is not None


def run_static_checks(
    directory: Path = MIGRATIONS_DIR,
) -> tuple[list[Violation], dict[str, object]]:
    """执行全部静态判据。

    Args:
        directory: 迁移目录。

    Returns:
        tuple[list[Violation], dict[str, object]]: 违规清单与统计信息。
    """
    migrations, violations = discover_migrations(directory)
    for _version, path in migrations:
        violations.extend(check_dollar_quotes(path))
        violations.extend(check_guard_presence(path))
    violations.extend(check_init_baseline())
    stats: dict[str, object] = {
        "migrations_dir": str(directory.resolve()),
        "versions": [version for version, _ in migrations],
        "count": len(migrations),
    }
    return violations, stats


# ===========================================================================
# 双路径实跑（需要 PG）
# ===========================================================================
def _swap_dsn(dsn: str, *, database: str = "", user: str = "") -> str:
    """替换 DSN 里的库名 / 用户名（不引第三方库，纯字符串处理）。

    Args:
        dsn: `postgresql://user:pass@host:port/db` 形式。
        database: 目标库名（空串表示不改）。
        user: 目标用户名（空串表示不改）。

    Returns:
        str: 替换后的 DSN。
    """
    head, _, tail = dsn.partition("://")
    credentials, _, host_part = tail.partition("@")
    current_user, sep, password = credentials.partition(":")
    host, _, current_db = host_part.partition("/")
    if user:
        current_user = user
    if database:
        current_db = database
    credential_text = f"{current_user}{sep}{password}" if sep else current_user
    return f"{head}://{credential_text}@{host}/{current_db}"


def _superuser_dsn() -> str:
    """取超级用户 DSN（显式变量优先，否则按"同一口令换用户名"派生）。

    Returns:
        str: 超级用户 DSN；无法派生时返回空串。
    """
    explicit = os.environ.get(SUPERUSER_DSN_ENV, "").strip()
    if explicit:
        return explicit
    app_dsn = os.environ.get(APP_DSN_ENV, "").strip()
    if not app_dsn:
        return ""
    return _swap_dsn(app_dsn, user=SUPERUSER_ROLE)


def _run_cli(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    """在适配层目录执行迁移 CLI。

    Args:
        dsn: 目标库连接串（应用角色）。
        *args: CLI 参数（`--check` / `--apply`）。

    Returns:
        subprocess.CompletedProcess[str]: 进程结果。
    """
    env = {
        **os.environ,
        "OA_DATABASE_URL": dsn,
        "OA_ENV": "test",
        # 迁移 CLI 硬性要求业务库模式为 postgres，否则直接 30004
        "OA_BUSINESS_STORE": "postgres",
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.db.migrate", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ADAPTER_ROOT),
        check=False,
    )


def _run_path_a_empty_database(superuser_dsn: str) -> list[Violation]:
    """路径 A：**空库**（应用角色自建，无任何表）连跑两遍 `--apply`。

    Args:
        superuser_dsn: 超级用户 DSN（建库 + 移交属主）。

    Returns:
        list[Violation]: 违规清单。
    """
    import asyncio

    import asyncpg

    name = f"oa_gate_mig_{uuid.uuid4().hex[:8]}"

    async def _create() -> None:
        connection = await asyncpg.connect(_swap_dsn(superuser_dsn, database="postgres"))
        try:
            await connection.execute(f"CREATE DATABASE {name}")
            await connection.execute(f"ALTER DATABASE {name} OWNER TO {APP_ROLE}")
        finally:
            await connection.close()
        # **扩展必须由超级用户建**：`vector` 的 trusted=false，应用角色即使
        # 是库属主也建不出来（实测 pgvector 0.8.7）。真实部署里这一步由
        # `init-schema-phase4.sql`（initdb 超级用户）或 DBA 完成，
        # 空库路径在这里等价模拟一次，否则 0009 在任何环境都第一步就死。
        installer = await asyncpg.connect(_swap_dsn(superuser_dsn, database=name))
        try:
            await installer.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await installer.close()

    async def _drop() -> None:
        connection = await asyncpg.connect(_swap_dsn(superuser_dsn, database="postgres"))
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                name,
            )
            await connection.execute(f"DROP DATABASE IF EXISTS {name}")
        finally:
            await connection.close()

    violations: list[Violation] = []
    try:
        asyncio.run(_create())
    except Exception as exc:  # 建库失败即环境问题，交回上层判 2
        return [Violation("路径A(空库)", f"建库失败：{type(exc).__name__}: {exc}")]
    app_dsn = _swap_dsn(superuser_dsn, database=name, user=APP_ROLE)
    try:
        for attempt in (1, 2):
            result = _run_cli(app_dsn, "--apply")
            if result.returncode != 0:
                violations.append(
                    Violation(
                        "路径A(空库)",
                        f"第 {attempt} 遍 --apply 失败（退出码 {result.returncode}）："
                        f"{(result.stderr or result.stdout).strip()[:400]}",
                    )
                )
                break
    finally:
        try:
            asyncio.run(_drop())
        except Exception:  # noqa: S110 - 清理失败不应掩盖主结论
            pass
    return violations


def _run_path_b_initialized(app_dsn: str) -> list[Violation]:
    """路径 B：**已初始化库**（initdb 跑过基线，表属主是超级用户）跑 `--check` + `--apply`。

    Args:
        app_dsn: 应用角色连接串（指向已初始化的库）。

    Returns:
        list[Violation]: 违规清单。
    """
    violations: list[Violation] = []
    for command in ("--check", "--apply"):
        result = _run_cli(app_dsn, command)
        if result.returncode != 0:
            violations.append(
                Violation(
                    "路径B(已初始化库)",
                    f"{command} 失败（退出码 {result.returncode}）："
                    f"{(result.stderr or result.stdout).strip()[:400]}",
                )
            )
    return violations


#: 回滚往返用例的固定探针（阶段四新增的两个迁移）
ROUND_TRIP_VERSIONS: Final[tuple[str, ...]] = (
    "0007_usage_event_dimensions",
    "0006_plugin_artifact_content_type",
)


def _column_exists(dsn: str, table: str, column: str) -> bool:
    """查询列是否存在（**应用角色可读的信息模式视图**）。

    Args:
        dsn: 连接串。
        table: 表名。
        column: 列名。

    Returns:
        bool: 存在返回 True；查询失败按"不存在"处理（让外层报违规而不是崩）。
    """
    import asyncio

    import asyncpg

    async def _query() -> bool:
        connection = await asyncpg.connect(dsn)
        try:
            value = await connection.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = $1 AND column_name = $2",
                table,
                column,
            )
            return value is not None
        finally:
            await connection.close()

    try:
        return bool(asyncio.run(_query()))
    except Exception:  # 连接失败由调用方的路径判定负责报错
        return False


def _run_path_c_round_trip(superuser_dsn: str) -> list[Violation]:
    """路径 C：**up → down → up 三轮**（阶段四 T4-01.4，架构 §10.2）。

    为什么必须单独一轮：`--apply` 走「补列」分支，`--rollback` 走「删列」分支——
    两条分支各自绿，并不代表「回滚后能再升回去」。真实运维要的正是后者。

    **为什么在独立空库上跑，而不是在共享测试库上跑**：
      ① 共享库上 `0008`（会话/消息，删表级）已应用，按「不自动级联」规则会挡住
         0006/0007 的回滚——这是**正确的生产行为**，但会让门禁永远红；
      ② 滚动 0008 需要 `pg_dump` 备份（R-4），CI 主机不一定装了 pg_dump；
      ③ 更重要的是：门禁不该为了测试而改动共享库的结构。
    所以这里自建一个临时库，只放 `0002-0007`（列级变更）的副本做往返，与共享库
    完全隔离；`0008` 的 down 由 `test_migration_lock.py` + 用例层覆盖。

    判据：
      1. `--rollback 0007 0006` 退出 0（倒序，两次相减都不报错）；
      2. 回滚后两列**真的不在**（否则 down 脚本是空转）；
      3. 再次 `--apply` 退出 0，且两列**真的回来了**。

    Args:
        superuser_dsn: 超级用户 DSN（用于建/删临时库）。

    Returns:
        list[Violation]: 违规清单。
    """
    import asyncio
    import shutil
    import uuid

    import asyncpg

    violations: list[Violation] = []
    probes = (("plugin_artifact", "content_type"), ("usage_event", "profile"))
    name = f"oa_gate_roundtrip_{uuid.uuid4().hex[:8]}"

    # 只放列级迁移的副本：0008 起是"删表级"，需要 pg_dump 备份，不属本路径范围
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        isolated = root / "deploy"
        (isolated / "migrations" / "rollback").mkdir(parents=True)
        shutil.copy2(BASELINE_PHASE2, isolated / BASELINE_PHASE2.name)
        shutil.copy2(BASELINE_PHASE3, isolated / BASELINE_PHASE3.name)
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name[:4] >= "0008":
                continue
            shutil.copy2(path, isolated / "migrations" / path.name)
            down = MIGRATIONS_DIR / "rollback" / f"{path.stem}.down.sql"
            if down.is_file():
                shutil.copy2(down, isolated / "migrations" / "rollback" / down.name)

        async def _create() -> None:
            connection = await asyncpg.connect(_swap_dsn(superuser_dsn, database="postgres"))
            try:
                await connection.execute(f"CREATE DATABASE {name}")
                await connection.execute(f"ALTER DATABASE {name} OWNER TO {APP_ROLE}")
            finally:
                await connection.close()

        async def _drop() -> None:
            connection = await asyncpg.connect(_swap_dsn(superuser_dsn, database="postgres"))
            try:
                await connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = $1 AND pid <> pg_backend_pid()",
                    name,
                )
                await connection.execute(f"DROP DATABASE IF EXISTS {name}")
            finally:
                await connection.close()

        try:
            asyncio.run(_create())
        except Exception as exc:  # 建库失败即环境问题
            return [Violation("路径C(回滚往返)", f"建库失败：{type(exc).__name__}: {exc}")]

        dsn = _swap_dsn(superuser_dsn, database=name, user=APP_ROLE)
        try:
            first = _run_cli(dsn, "--apply", "--sql-dir", str(isolated))
            if first.returncode != 0:
                return [
                    Violation(
                        "路径C(回滚往返)",
                        f"前置 --apply 失败（退出码 {first.returncode}）："
                        f"{(first.stderr or first.stdout).strip()[:400]}",
                    )
                ]

            rollback = _run_cli(
                dsn,
                "--rollback",
                "0007_usage_event_dimensions",
                "0006_plugin_artifact_content_type",
                "--sql-dir",
                str(isolated),
            )
            if rollback.returncode != 0:
                violations.append(
                    Violation(
                        "路径C(回滚往返)",
                        f"--rollback 失败（退出码 {rollback.returncode}）："
                        f"{(rollback.stderr or rollback.stdout).strip()[:400]}",
                    )
                )
                return violations

            for table, column in probes:
                if _column_exists(dsn, table, column):
                    violations.append(
                        Violation(
                            "路径C(回滚往返)",
                            f"回滚后 {table}.{column} 仍存在（down 脚本空转，等于没有回滚）",
                        )
                    )

            again = _run_cli(dsn, "--apply", "--sql-dir", str(isolated))
            if again.returncode != 0:
                violations.append(
                    Violation(
                        "路径C(回滚往返)",
                        f"回滚后再次 --apply 失败（退出码 {again.returncode}）："
                        f"{(again.stderr or again.stdout).strip()[:400]}",
                    )
                )
                return violations

            for table, column in probes:
                if not _column_exists(dsn, table, column):
                    violations.append(
                        Violation(
                            "路径C(回滚往返)",
                            f"三轮后 {table}.{column} 仍未恢复（up → down → up 不一致）",
                        )
                    )
        finally:
            try:
                asyncio.run(_drop())
            except Exception:  # noqa: S110 - 清理失败不掩盖主结论
                pass
    return violations


def run_pg_paths() -> tuple[int, list[Violation]]:
    """跑两条路径，返回退出码与违规清单。

    Returns:
        tuple[int, list[Violation]]: `(退出码, 违规清单)`；环境缺失/连不上返回 2。
    """
    app_dsn = os.environ.get(APP_DSN_ENV, "").strip()
    superuser_dsn = _superuser_dsn()
    if not app_dsn:
        return 2, [Violation("环境", f"未设置 {APP_DSN_ENV}，无法跑双路径")]
    if not superuser_dsn:
        return 2, [
            Violation("环境", f"未设置 {SUPERUSER_DSN_ENV} 且无法从 {APP_DSN_ENV} 派生超级用户 DSN")
        ]
    violations = _run_path_a_empty_database(superuser_dsn)
    violations.extend(_run_path_b_initialized(app_dsn))
    # 路径 C 依赖路径 B 的前置状态（已初始化库 + 0006/0007 可应用），放在最后跑；
    # 它需要表属主权限（DROP COLUMN），故用超级用户 DSN
    violations.extend(_run_path_c_round_trip(superuser_dsn))
    return (1 if violations else 0), violations


# ===========================================================================
# 自检（证明门禁不是永远绿）
# ===========================================================================
def run_self_test() -> tuple[int, list[str]]:
    """注入负样本，验证判据**能被触发**（退出码 1 表示自检成功）。

    Returns:
        tuple[int, list[str]]: `(退出码, 说明清单)`；2 表示门禁失灵。
    """
    notes: list[str] = []
    undetected: list[str] = []
    samples: dict[str, tuple[str, str]] = {
        # 名称 → (SQL 内容, 期望触发的原因关键词)
        "标签不闭合": ("DO $guard$\nBEGIN\n    RAISE NOTICE 'x';\nEND $$;\n", "美元引号"),
        "ALTER 无守卫": ("ALTER TABLE plugin_task ADD COLUMN extra INT;\n", "存在性守卫"),
        "ALTER 无权限处理": (
            "DO $g$\nBEGIN\n    IF to_regclass('public.plugin_task') IS NULL THEN RETURN; END IF;\n"
            "    ALTER TABLE plugin_task ADD COLUMN extra INT;\nEND $g$;\n",
            "insufficient_privilege",
        ),
    }
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for label, (sql, keyword) in samples.items():
            sample = root / f"{label}.sql"
            sample.write_text(sql, encoding="utf-8")
            found: list[str] = []
            for violation in check_dollar_quotes(sample):
                found.append(violation.reason)
            for violation in check_guard_presence(sample):
                found.append(violation.reason)
            if any(keyword in reason for reason in found):
                notes.append(f"[OK] 已拦截：{label} → {keyword}")
            else:
                undetected.append(f"{label} 未被拦截（期望命中「{keyword}」）")
        # 反向：干净样本不得被误判（防"一律报错"式假门禁）
        clean = root / "clean.sql"
        clean.write_text(
            "DO $guard$\nBEGIN\n    IF to_regclass('public.plugin_task') IS NULL THEN RETURN; "
            "END IF;\n"
            "    BEGIN\n        ALTER TABLE plugin_task ADD COLUMN extra INT;\n"
            "    EXCEPTION WHEN insufficient_privilege THEN\n"
            "        RAISE EXCEPTION '请以表属主身份执行：ALTER TABLE ...';\n"
            "    END;\nEND $guard$;\n",
            encoding="utf-8",
        )
        if check_dollar_quotes(clean) or check_guard_presence(clean):
            undetected.append("干净迁移被误判为违规（门禁过严，会把人逼去关掉它）")
        else:
            notes.append("[OK] 干净迁移通过（门禁不是一律报错）")
    if undetected:
        notes.extend(f"[FAIL] {item}" for item in undetected)
        return 2, notes
    return 1, notes


# ===========================================================================
# 入口
# ===========================================================================
def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 退出码（0 通过 / 1 违规或自检命中 / 2 门禁自身异常）。
    """
    parser = argparse.ArgumentParser(description="迁移门禁（静态 + 双路径实跑）")
    parser.add_argument("--dir", default="", help="迁移目录（缺省 deploy/migrations）")
    parser.add_argument("--run-pg", action="store_true", help="追加空库/已初始化库双路径实跑")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（期望退出码 1）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    if args.self_test:
        code, notes = run_self_test()
        for note in notes:
            print(note)
        print(
            "[OK]   迁移门禁自检通过：负样本全部被拦截（退出码 1 为预期）"
            if code == 1
            else "[FAIL] 迁移门禁自检失败：判据未能发现注入的问题"
        )
        return code

    directory = Path(args.dir).resolve() if args.dir else MIGRATIONS_DIR
    violations, stats = run_static_checks(directory)
    code = 1 if violations else 0
    if args.run_pg:
        pg_code, pg_violations = run_pg_paths()
        violations.extend(pg_violations)
        if pg_code == 2:
            code = 2
        elif pg_code == 1:
            code = 1

    if args.json:
        print(
            json.dumps(
                {
                    "code": code,
                    "stats": stats,
                    "violations": [item.as_dict() for item in violations],
                },
                ensure_ascii=False,
            )
        )
        return code

    print(f"[INFO] 迁移脚本 {stats['count']} 条：{', '.join(stats['versions']) or '（无）'}")
    print(f"[INFO] 迁移目录（绝对路径）= {stats['migrations_dir']}")
    for violation in violations:
        print(violation.render())
    if code == 2:
        print("[FAIL] 迁移门禁自身异常（环境不满足），不视为通过")
        return 2
    if code == 1:
        print(f"[FAIL] 迁移门禁未通过：{len(violations)} 处问题")
        return 1
    print("[OK]   迁移门禁通过：标签成对、DDL 有守卫、init 基线覆盖 B19 三列")
    if args.run_pg:
        print(
            "[OK]   三路径实跑通过：空库 --apply×2 绿、已初始化库 --check/--apply 绿、"
            "回滚往返（0007→0006 down → 再 apply）绿"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
