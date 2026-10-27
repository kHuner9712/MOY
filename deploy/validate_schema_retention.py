#!/usr/bin/env python3
"""产物留存门禁（阶段三 B28 第 7 项，`architecture-phase3.md` G-31）。

**判据只有一条，但必须能红**：

| 检查项 | 期望 | 为什么是这条 |
| --- | --- | --- |
| 列存在 | `plugin_task.expires_at` 在（产物表同项） | 留存期靠这一列表达；没有它"180
天"只能写在文档里 |
| 列类型 | `timestamp with time zone` | 跨时区时 `timestamp` 会让"到期"偏移一小时，清理会早/晚跑 |
| 默认值 | `now() + INTERVAL '180 days'`（180 天） | PRD Q3-5 / ADJ-8 唯一口径；改天数属法规变更，
须评审 |

**为什么默认值要单独查**：`expires_at` 存在但默认值写错（例如抄了 `plugin_artifact`
的 90 天）是**最可能发生且最难发现**的漂移——列在、类型对、代码能跑，
只有"到期日算错了"这一条要等到 90 天后才暴露。所以默认值按**天数**比对，不比字符串：
`now() + '180 days'::interval` 与 `now() + INTERVAL '180 days'` 都要算对。

**「改坏即红」的自证过程**（2026-10-22 实跑，输出逐字记录如下，作为门禁可信度证据）：

1. 把 `deploy/migrations/0003_phase3_plugin_tables.sql` 第 188 行（`plugin_task`）的
   `INTERVAL '180 days'` 改成 `'90 days'`；
2. `python deploy/validate_schema_retention.py` → **退出码 1**，输出::

       [FAIL] 0003_phase3_plugin_tables.sql:plugin_task.expires_at:
              默认留存期应为 180 天，实际 90 天（原文 90 days）

3. 还原后重跑 → **退出码 0**（`[OK] 留存门禁通过…`）；
4. 在**真实库**上再走一遍（`--run-pg`）：把库里的默认值 `ALTER` 成 90 天 →
   **退出码 1**，输出::

       [FAIL] pg:plugin_task.expires_at: 默认留存期应为 180 天，实际 90
              （默认值表达式 "(now() + '90 days'::interval)"）

   改回 180 天 → **退出码 0**。

第 4 步是必要的：静态解析只能证明"脚本里写对了"，证明不了"库里的列真的长那样"
（迁移没跑、或有人手工 `ALTER` 过，静态检查照样全绿）。
注意 `ALTER` 需要**超级用户**（应用角色 `officeai_app` 会报
`must be owner of table plugin_task`）——这也顺带证明了库上的属主模型是生效的。

`--self-test` 是上述过程的**自动化版**（在内存里改文本，不碰磁盘与库，CI 可随时跑）。

用法::

    python deploy/validate_schema_retention.py              # 静态（无需 PG，CI 默认）
    python deploy/validate_schema_retention.py --run-pg     # 追加真实库校验
    python deploy/validate_schema_retention.py --self-test  # 负样本自检（期望退出码 1）
    python deploy/validate_schema_retention.py --json       # 机器可读

退出码：0 = 通过；1 = 发现违规或自检命中；2 = 门禁自身异常（目录缺失 / 连不上库）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Final

#: 仓库根目录
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 迁移目录
MIGRATIONS_DIR: Final[Path] = REPO_ROOT / "deploy" / "migrations"

#: 基线脚本（initdb 阶段执行，不是迁移，但同样定义这些列）
BASELINE_PHASE3: Final[Path] = REPO_ROOT / "deploy" / "init-schema-phase3.sql"

#: 必须带留存期的表（`plugin_task` = 任务级；`plugin_artifact` = 产物级）
TARGET_TABLES: Final[tuple[str, ...]] = ("plugin_task", "plugin_artifact")

#: 留存期唯一口径（天数）——PRD Q3-5 / ADJ-8
RETENTION_DAYS: Final[int] = 180

#: 期望的列类型（`information_schema.columns.data_type`）
EXPECTED_DATA_TYPE: Final[str] = "timestamp with time zone"

#: 测试环境变量（与应用角色一致）
APP_DSN_ENV: Final[str] = "OA_TEST_DATABASE_URL"

#: `CREATE TABLE <name> (...)` 的定位（表体由括号深度扫描，不靠正则吃括号）
CREATE_TABLE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>[a-z_][a-z0-9_]*)\s*\(",
    re.IGNORECASE,
)

#: 行注释与块注释（静态解析前必须去掉，否则说明文字会被当成 DDL）
LINE_COMMENT: Final[re.Pattern[str]] = re.compile(r"--.*$", re.MULTILINE)
BLOCK_COMMENT: Final[re.Pattern[str]] = re.compile(r"/\*.*?\*/", re.DOTALL)

#: `expires_at` 列定义（类型 + 默认值都要能取到）
EXPIRES_COLUMN: Final[re.Pattern[str]] = re.compile(
    r"\bexpires_at\s+(?P<type>[A-Za-z][A-Za-z0-9_ ]*?)\s+NOT\s+NULL\s+DEFAULT\s+"
    r"(?P<default>now\(\)\s*\+\s*(?:INTERVAL\s*)?"
    r"'(?P<amount>\d+)\s*(?P<unit>day|days|month|months)'"
    r"(?:::interval)?)",
    re.IGNORECASE,
)

#: 默认值里允许的时间单位与折算（只支持日历天数与月，月按 30 天折算并单独提示）
UNIT_DAYS: Final[dict[str, int]] = {
    "day": 1,
    "days": 1,
    "month": 30,
    "months": 30,
}


class Violation:
    """一条门禁违规（自带定位，CI 输出直接可读）。"""

    def __init__(self, where: str, reason: str) -> None:
        """初始化。

        Args:
            where: 定位（`表.列` 或文件路径）。
            reason: 业务语言原因。
        """
        self.where = where
        self.reason = reason

    def render(self) -> str:
        """渲染为一行文本。

        Returns:
            str: `[FAIL] where: reason`。
        """
        return f"[FAIL] {self.where}: {self.reason}"

    def as_dict(self) -> dict[str, str]:
        """转成可 JSON 序列化的字典。

        Returns:
            dict[str, str]: 字段字典。
        """
        return {"where": self.where, "reason": self.reason}


def strip_comments(text: str) -> str:
    """去掉 SQL 注释（保留换行，行号不乱）。

    Args:
        text: SQL 原文。

    Returns:
        str: 去注释后的文本。
    """
    without_block = BLOCK_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), text)
    return LINE_COMMENT.sub("", without_block)


def iter_create_tables(text: str) -> list[tuple[str, str]]:
    """把 SQL 里每个 `CREATE TABLE` 的**表体**切出来。

    按括号深度扫描而不是用正则吃到 `)`：表体里有 `CHECK (...)`、`PRIMARY KEY (...)`
    和 `REFERENCES`，用 `[^)]*` 会在第一个 `)` 处截断，于是 `expires_at`
    （通常写在表体中后段）永远读不到——这正是"门禁永远绿"的经典成因。

    Args:
        text: 去注释后的 SQL。

    Returns:
        list[tuple[str, str]]: `(表名, 表体)` 列表。
    """
    tables: list[tuple[str, str]] = []
    for match in CREATE_TABLE_PATTERN.finditer(text):
        start = match.end()
        depth = 1
        index = start
        while index < len(text) and depth > 0:
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        tables.append((match.group("table").lower(), text[start : index - 1]))
    return tables


def check_text(label: str, text: str) -> list[Violation]:
    """对一段 SQL 文本跑留存期判据（**只检查这段里真的建了的表**）。

    为什么按"建了的表"而不是"每份脚本都必须建这两张表"：`0002` 只建回调日志表、
    `0004` 只加列，要求它们也建 `plugin_task` 会得到 4 条**必然存在**的红，
    门禁就会变成"人人都知道它是红的"——那是比没有门禁更差的状态。
    "这两张表至少在一处被定义过"由 `run_static_checks` 统一保证。

    Args:
        label: 定位用的来源名（文件名）。
        text: SQL 原文。

    Returns:
        list[Violation]: 违规清单。
    """
    violations: list[Violation] = []
    found = dict(iter_create_tables(strip_comments(text)))
    for table in TARGET_TABLES:
        body = found.get(table)
        if body is None:
            continue
        where = f"{label}:{table}.expires_at"
        match = EXPIRES_COLUMN.search(body)
        if match is None:
            violations.append(
                Violation(
                    where,
                    "缺少 expires_at 列（或没写 NOT NULL DEFAULT now() + INTERVAL '180 days'）",
                )
            )
            continue
        column_type = " ".join(match.group("type").split()).lower()
        if column_type != "timestamptz":
            violations.append(
                Violation(
                    where, f"列类型必须是 timestamptz，实际 {column_type!r}（跨时区会算错到期日）"
                )
            )
        amount = int(match.group("amount"))
        unit = match.group("unit").lower()
        days = amount * UNIT_DAYS[unit]
        if days != RETENTION_DAYS:
            violations.append(
                Violation(
                    where,
                    f"默认留存期应为 {RETENTION_DAYS} 天，实际 {days} 天（原文 {amount} {unit}）",
                )
            )
    return violations


def collect_sql_files(directory: Path) -> list[Path]:
    """收集要检查的 SQL 文件（迁移 + 阶段三基线）。

    Args:
        directory: 迁移目录。

    Returns:
        list[Path]: 文件列表（不存在的不计入）。
    """
    files = sorted(directory.glob("*.sql")) if directory.is_dir() else []
    if BASELINE_PHASE3.is_file():
        files.append(BASELINE_PHASE3)
    return files


def run_static_checks(directory: Path) -> tuple[list[Violation], dict[str, object]]:
    """静态判据：迁移脚本与基线脚本里的列定义。

    Args:
        directory: 迁移目录。

    Returns:
        tuple[list[Violation], dict[str, object]]: 违规清单与统计信息。
    """
    files = collect_sql_files(directory)
    if not files:
        return (
            [Violation(str(directory), "找不到任何 SQL 文件（--dir 指错或仓库不完整）")],
            {"files": [], "migrations_dir": str(directory)},
        )
    violations: list[Violation] = []
    defined: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8")
        violations.extend(check_text(path.name, text))
        defined.update(name for name, _ in iter_create_tables(strip_comments(text)))
    # 「这两张表至少被定义过一次」——否则本次扫描等于什么都没验（目录指错/脚本被删）
    for table in TARGET_TABLES:
        if table not in defined:
            violations.append(
                Violation(str(directory), f"整个迁移+基线里都没有建 {table} 表（脚本缺失？）")
            )
    return violations, {
        "files": [path.name for path in files],
        "migrations_dir": str(directory.resolve()),
        "retention_days": RETENTION_DAYS,
    }


async def run_pg_check(dsn: str) -> list[Violation]:
    """真实库判据：`information_schema` + `pg_get_expr` 读实际列与默认值。

    Args:
        dsn: 应用角色连接串。

    Returns:
        list[Violation]: 违规清单。
    """
    import asyncpg

    raw = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(raw)
    try:
        rows = await connection.fetch(
            """
            SELECT c.relname AS table_name,
                   a.attname AS column_name,
                   format_type(a.atttypid, a.atttypmod) AS column_type,
                   pg_get_expr(d.adbin, d.adrelid) AS default_expr,
                   a.attnotnull AS not_null
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
              LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
             WHERE n.nspname = 'public'
               AND c.relkind = 'r'
               AND c.relname = ANY($1::text[])
               AND a.attname = 'expires_at'
            """,
            list(TARGET_TABLES),
        )
    finally:
        await connection.close()

    by_table = {str(row["table_name"]): row for row in rows}
    violations: list[Violation] = []
    for table in TARGET_TABLES:
        where = f"pg:{table}.expires_at"
        row = by_table.get(table)
        if row is None:
            violations.append(Violation(where, "库中该表没有 expires_at 列（迁移没跑？）"))
            continue
        if str(row["column_type"]) != EXPECTED_DATA_TYPE:
            violations.append(
                Violation(where, f"列类型必须是 {EXPECTED_DATA_TYPE}，实际 {row['column_type']}")
            )
        if not bool(row["not_null"]):
            violations.append(Violation(where, "列必须是 NOT NULL（否则到期日可以缺失）"))
        expression = str(row["default_expr"] or "")
        days = parse_default_days(expression)
        if days != RETENTION_DAYS:
            violations.append(
                Violation(
                    where,
                    f"默认留存期应为 {RETENTION_DAYS} 天，"
                    f"实际 {days if days is not None else '无法解析'}"
                    f"（默认值表达式 {expression!r}）",
                )
            )
    return violations


def parse_default_days(expression: str) -> int | None:
    """从 PostgreSQL 的默认值表达式里解析天数。

    只认两种形态（其余返回 None，让门禁**报"无法解析"而不是放过**）：

    - `now() + '180 days'::interval`（PG 归一化后的写法）
    - `now() + INTERVAL '180 days'`

    Args:
        expression: `pg_get_expr` 给出的默认值表达式。

    Returns:
        int | None: 折算后的天数；无法解析返回 None。
    """
    normalized = expression.replace("::interval", "").replace('"', "'")
    match = re.search(
        r"'\s*(?P<amount>\d+)\s*(?P<unit>day|days|month|months)\s*'",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return int(match.group("amount")) * UNIT_DAYS[match.group("unit").lower()]


def run_self_test() -> tuple[int, list[str]]:
    """负样本自检：把默认值改坏，判据**必须**报出来。

    自检不碰磁盘上的真实脚本（在内存里替换文本），因此可以随时在 CI 里跑，
    也不会留下"改坏的文件"。

    Returns:
        tuple[int, list[str]]: 期望退出码（1 表示判据有效）与说明行。
    """
    notes: list[str] = []
    sample = (
        "CREATE TABLE plugin_task (\n"
        "    tenant_id TEXT NOT NULL,\n"
        "    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '180 days',\n"
        "    PRIMARY KEY (tenant_id)\n"
        ");\n"
        "CREATE TABLE plugin_artifact (\n"
        "    tenant_id TEXT NOT NULL,\n"
        "    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '180 days'\n"
        ");\n"
    )
    good = check_text("self-test", sample)
    if good:
        notes.append(f"[FAIL] 自检基线不干净：正样本被判出 {[item.render() for item in good]}")
        return 0, notes
    notes.append("[OK]   正样本（180 天）通过")

    broken = sample.replace("plugin_task (\n", "plugin_task (\n", 1).replace(
        "INTERVAL '180 days',", "INTERVAL '90 days',", 1
    )
    bad = check_text("self-test", broken)
    if not any("90 天" in item.reason for item in bad):
        notes.append("[FAIL] 负样本未被拦住：把 180 天改成 90 天后判据仍然全绿")
        return 0, notes
    notes.append("[OK]   负样本（90 天）被拦住：" + bad[0].render())

    missing = "CREATE TABLE plugin_task (\n    tenant_id TEXT NOT NULL\n);\n"
    gone = check_text("self-test", missing)
    if not any("expires_at" in item.reason or "缺少" in item.reason for item in gone):
        notes.append("[FAIL] 负样本未被拦住：删掉 expires_at 后判据仍然全绿")
        return 0, notes
    notes.append("[OK]   负样本（缺列）被拦住：" + gone[0].render())

    wrong_type = sample.replace(
        "TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '180 days',",
        "TIMESTAMP NOT NULL DEFAULT now() + INTERVAL '180 days',",
        1,
    )
    typed = check_text("self-test", wrong_type)
    if not any("timestamptz" in item.reason for item in typed):
        notes.append("[FAIL] 负样本未被拦住：把 timestamptz 改成 timestamp 后判据仍然全绿")
        return 0, notes
    notes.append("[OK]   负样本（类型写成 timestamp）被拦住：" + typed[0].render())
    return 1, notes


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 退出码（0 通过 / 1 违规或自检命中 / 2 门禁自身异常）。
    """
    parser = argparse.ArgumentParser(description="产物留存门禁 G-31（expires_at 列 + 180 天）")
    parser.add_argument("--dir", default="", help="迁移目录（缺省 deploy/migrations）")
    parser.add_argument("--run-pg", action="store_true", help="追加真实库校验")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（期望退出码 1）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    if args.self_test:
        code, notes = run_self_test()
        for note in notes:
            print(note)
        print(
            "[OK]   留存门禁自检通过：负样本全部被拦截（退出码 1 为预期）"
            if code == 1
            else "[FAIL] 留存门禁自检失败：判据未能发现注入的问题"
        )
        return code

    directory = Path(args.dir).resolve() if args.dir else MIGRATIONS_DIR
    violations, stats = run_static_checks(directory)
    code = 1 if violations else 0

    if args.run_pg:
        dsn = os.environ.get(APP_DSN_ENV, "").strip()
        if not dsn:
            print(f"[FAIL] 未提供 {APP_DSN_ENV}，无法执行 --run-pg（不视为通过）")
            return 2
        import asyncio

        try:
            pg_violations = asyncio.run(run_pg_check(dsn))
        except Exception as exc:  # CLI 入口：连接类异常一律转成可读失败信息并以非 0 退出
            print(f"[FAIL] 连接数据库失败：{type(exc).__name__}: {exc}")
            return 2
        violations.extend(pg_violations)
        code = 1 if violations else 0

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

    print(f"[INFO] 检查文件 {len(stats['files'])} 个：{', '.join(stats['files'])}")
    print(f"[INFO] 迁移目录（绝对路径）= {stats['migrations_dir']}")
    print(f"[INFO] 留存期口径 = {stats['retention_days']} 天（PRD Q3-5 / ADJ-8）")
    for violation in violations:
        print(violation.render())
    if code == 0:
        print("[OK]   留存门禁通过：expires_at 列、类型与 180 天默认值均符合口径")
        return 0
    print(f"[FAIL] 留存门禁未通过：{len(violations)} 处问题")
    return 1


if __name__ == "__main__":
    sys.exit(main())
