"""SQL ↔ DDL 静态一致性（G-SQL 原型）：`INSERT` 的列数/值数/列名必须与建表语句一致。

**为什么需要它**（B19 的 `P3-B19-04` 就是这么活下来的）：
`create_task` 的 INSERT 写成 25 列 / 24 值、并把派生字段 `queue_position` 当存储列，
PG 侧 100% 失败，而**内存后端全绿**——覆盖率与门禁都看不见它。

**为什么反例要写成断言**：本文件的 `test_splitter_counterexample` 就是那条反例
（值里含 `COALESCE(:x, now())`）。它在文档里只是"说明"，在这里才是**防线**——
因为下一次有人改判据实现时，pytest 会替我们把这句话念一遍。

判据三层（缺一层不成立）：
- 0 输入：读的是**仓库里的源码文件**（不经工作区、不连库）；
- 1 作用域：只看 `INSERT INTO … (cols) VALUES (vals)` 这一段，不看全文；
- 2 实现：**括号深度扫描**切分，不用"跳过括号内逗号"的正则（后者会把
  `COALESCE(:x, now())` 切成两段 → 值数多算 1 → **假绿**）；
- 3 自检：`test_splitter_counterexample` 用带函数调用的值串反向证明实现没错。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

#: `apps/adapter/tests/integration/test_x.py` → parents[4] 才是仓库根
REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = REPO_ROOT / "apps" / "adapter" / "app"
DDL_ROOT = REPO_ROOT / "deploy"

#: 不算列定义的 DDL 行首关键字
_DDL_KEYWORDS = frozenset(
    {"PRIMARY", "UNIQUE", "CONSTRAINT", "CHECK", "FOREIGN", "REFERENCES", "KEY"}
)

_INSERT_RE = re.compile(
    r"INSERT INTO\s+[\w{}\.]+\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*(?:RETURNING|ON CONFLICT)",
    re.S,
)


def split_top(text: str) -> list[str]:
    """按**顶层逗号**切分（括号深度扫描），保留括号内的逗号。

    Args:
        text: 待切分的 SQL 片段（列清单或值清单）。

    Returns:
        list[str]: 去掉空白后的片段；空片段被丢弃。
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return [item.strip() for item in parts if item.strip()]


def ddl_columns() -> set[str]:
    """从 `deploy/**.sql` 抽出全部列名：`CREATE TABLE` 括号体 + `ALTER … ADD COLUMN`。

    **必须包含 `ALTER TABLE … ADD COLUMN`**：本项目"后补列"是常态（`0004` 一次补了
    `confirmed_step_seq` / `version` / `version_history` 三列）。只解析 `CREATE TABLE`
    会把所有后补列判成"不存在"——**那是系统性假红，面积比它想防的真缺陷更大**
    （B19 复盘时本判据就误报过一次 `confirmed_step_seq`）。

    Returns:
        set[str]: 列名集合；用于判断 SQL 是否引用了不存在的列。
    """
    columns: set[str] = set()
    for path in list(DDL_ROOT.glob("*.sql")) + list(DDL_ROOT.glob("migrations/*.sql")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(
            r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+([a-z_][\w.]*)\s*\((.*?)\n\s*\)\s*;",
            text,
            re.S,
        ):
            for line in match.group(2).splitlines():
                head = re.match(r"\s*([a-z_][a-z0-9_]*)\s+", line)
                if head and head.group(1).upper() not in _DDL_KEYWORDS:
                    columns.add(head.group(1))
        # 后补列：ALTER TABLE <表> … ADD COLUMN [IF NOT EXISTS] <列>
        for match in re.finditer(
            r"ADD\s+COLUMN(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_][a-z0-9_]*)",
            text,
            re.I,
        ):
            columns.add(match.group(1))
    return columns


def insert_statements() -> list[tuple[str, list[str], list[str]]]:
    """扫描仓储层全部 INSERT 语句。

    Returns:
        list[tuple[str, list[str], list[str]]]: (文件名, 列清单, 值清单)。
    """
    found: list[tuple[str, list[str], list[str]]] = []
    for path in (APP_ROOT / "db" / "repositories").glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _INSERT_RE.finditer(text):
            found.append((path.name, split_top(match.group(1)), split_top(match.group(2))))
    return found


def test_splitter_counterexample() -> None:
    """反例自检：含函数调用的值串必须切出 3 段，不能切出 4 段。

    这一条就是对"朴素正则"的否定：`re.split(r",(?![^()]*\\))")` 会把
    `COALESCE(:x, now())` 切成 `COALESCE(:x` 与 `now())` 两段，值数因此多算 1，
    进而让"列数 != 值数"的真缺陷**被判成通过**（假绿）。
    """
    assert split_top("a, COALESCE(:x, now()), c") == ["a", "COALESCE(:x, now())", "c"]
    assert split_top("CAST(:v AS jsonb), NULLIF(:w, '')") == [
        "CAST(:v AS jsonb)",
        "NULLIF(:w, '')",
    ]


def test_ddl_parser_includes_alter_add_column() -> None:
    """反例自检：`ALTER TABLE … ADD COLUMN` 的列必须被解析进来。

    B19 复盘时，本判据的第一版只解析 `CREATE TABLE`，于是把 `0004` 里
    `ALTER TABLE plugin_task ADD COLUMN IF NOT EXISTS confirmed_step_seq …`
    补的列判成"不存在"——**假红**。后补列是迁移的常态，漏解析它会让门禁噪声大到被关掉。
    """
    known = ddl_columns()
    for column in ("confirmed_step_seq", "version", "version_history"):
        assert column in known, f"`ALTER TABLE … ADD COLUMN` 补的列未被解析：{column}"
    # `queue_position` 在任何迁移里都不存在（它是派生值）——必须**不**在集合里
    assert "queue_position" not in known, "`queue_position` 不该出现在 DDL 列集合里（它是派生值）"


def test_insert_column_value_arity_matches() -> None:
    """每条 INSERT 的列数必须等于值数（`P3-B19-04` 的直接判据）。"""
    statements = insert_statements()
    # 防空转：扫描为空时"0 处不一致"毫无意义（假绿）
    assert statements, (
        f"未扫描到任何 INSERT 语句——判据输入为空，结论不成立（REPO_ROOT={REPO_ROOT}）"
    )

    mismatches = [
        (name, len(cols), len(vals)) for name, cols, vals in statements if len(cols) != len(vals)
    ]
    assert not mismatches, f"INSERT 列数与值数不一致（文件, 列, 值）：{mismatches}"


def test_insert_columns_exist_in_ddl() -> None:
    """INSERT 引用的列必须存在于 DDL（防"派生字段被当存储列"）。"""
    known = ddl_columns()
    assert known, "未能从 deploy/**.sql 解析出任何列名——判据的输入为空，结论不成立"

    statements = insert_statements()
    assert statements, "未扫描到任何 INSERT 语句——判据输入为空，结论不成立"

    unknown = [
        (name, column) for name, cols, _ in statements for column in cols if column not in known
    ]
    assert not unknown, f"INSERT 引用了 DDL 中不存在的列（文件, 列）：{unknown}"
