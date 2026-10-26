"""SQL ↔ DDL 静态一致性（G-SQL）：`INSERT` 的列数/值数/列名/表名必须与建表语句一致。

**为什么需要它**（B19 的 `P3-B19-04` 就是这么活下来的）：
`create_task` 的 INSERT 写成 25 列 / 24 值、并把派生字段 `queue_position` 当存储列，
PG 侧 100% 失败，而**内存后端全绿**——覆盖率与门禁都看不见它。

**判据（三层，缺一层不成立）**：
- 0 输入：读**仓库源码**（不经工作区、不连库）；
- 1 作用域：只看 `INSERT INTO <表> (列) VALUES (值)` 这一段；
- 2 实现：**AST 取字符串 + 配对括号扫描**——不用"跳过括号内逗号"的正则
  （`COALESCE(:x, now())` 会被切成两段 → 值数多算 1 → 假绿），
  也不能用"按行读字面量"的正则（相邻字符串字面量在解析期被折叠，正则看不见）；
- 3 自检：`test_splitter_counterexample` / `test_adjacent_literal_is_folded` /
  `test_table_constant_is_resolved` 三条反例把实现钉死。

**扫描范围**：**全 `app/`**。2C-6 之前只扫 `app/db/repositories/**` 的顶层文件，
于是 ① 拆包后 8 个包的 SQL 全在子目录（37 条漏 25 条）；② 业务层 4 条写路径
（`audit_log` / `integration_outbox` / `plugin_task` / `schema_migration`）完全在视野外，
其中 `audit_log` 正是 R1/R4 对 `audit` 单元豁免所指的那条。

**DDL 解析复用门禁实现**（`deploy/validate_repository_parity.py::load_ddl_tables`）：
表结构只允许有一份解析逻辑，否则测试与门禁会各说各话。
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import pytest

pytestmark = pytest.mark.integration

#: `apps/adapter/tests/integration/test_x.py` → parents[4] 才是仓库根
REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = REPO_ROOT / "apps" / "adapter" / "app"
DDL_ROOT = REPO_ROOT / "deploy"

#: INSERT 语句数下限（**fail-closed**）。
#:
#: 为什么要有它：扫描范围缩水不会报错、只会让结论变空。实测 2026-10-27：
#: 仓储层 37 条 + 业务层 3 条 + 迁移 1 条 = **41 条**（迁移那条不写列清单，见下）。
#: 低于这个数就是"没看"，不是"没问题"。
MIN_INSERT_STATEMENTS: Final[int] = 38

#: `INSERT INTO <表> (` 的头部（表名只认标识符，表名由 f-string 常量或字面量给出）。
_INSERT_HEAD: Final[re.Pattern[str]] = re.compile(
    r"INSERT\s+INTO\s+([a-z_][\w.]*)\s*\(", re.IGNORECASE
)

#: 纯字面量 SQL 里的表名（`INSERT INTO schema_migration (...)`）。
_LITERAL_TABLE: Final[re.Pattern[str]] = re.compile(r"INSERT\s+INTO\s+([a-z_][\w.]*)\s*\(")


@dataclass(frozen=True, slots=True)
class InsertStatement:
    """一条可静态判定的 INSERT。

    Attributes:
        file: 相对 `app/` 的文件路径。
        lineno: 语句所在行（字符串起点）。
        table: 目标表名（f-string 常量已解析成真实表名）。
        columns: 列清单。
        values: 值清单。
    """

    file: str
    lineno: int
    table: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


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


def _balanced(text: str, start: int) -> tuple[str, int] | None:
    """从 `text[start] == '('` 起取配对括号内的内容。

    Args:
        text: 全文。
        start: 左括号下标。

    Returns:
        tuple[str, int] | None: `(括号内文本, 右括号之后的下标)`；不配对返回 None。
    """
    if start >= len(text) or text[start] != "(":
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
    return None


@lru_cache(maxsize=1)
def _gate_module() -> Any:
    """加载 `deploy/validate_repository_parity.py`（DDL 解析的单一真源）。

    Returns:
        Any: 已加载的模块对象。
    """
    path = DDL_ROOT / "validate_repository_parity.py"
    name = "_parity_gate_for_sql_tests"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"无法加载门禁脚本：{path}"
    module = importlib.util.module_from_spec(spec)
    # 必须先注册再 exec：门禁用 `from __future__ import annotations` + dataclass，
    # 字符串注解要靠 `sys.modules[cls.__module__]` 解析，未注册会 AttributeError。
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def ddl_tables() -> dict[str, Any]:
    """表名 → DDL 形状（列集合 + 可省略列集合）。

    Returns:
        dict[str, Any]: `DdlTable` 映射。
    """
    return _gate_module().load_ddl_tables(DDL_ROOT)


def ddl_columns() -> set[str]:
    """全部 DDL 列名的并集（保留给"列名是否存在"的粗判据）。

    Returns:
        set[str]: 列名集合。
    """
    return set().union(*(shape.columns for shape in ddl_tables().values()))


@lru_cache(maxsize=1)
def _global_constants() -> tuple[dict[str, str], set[str]]:
    """全仓模块级字符串常量（`AUDIT_TABLE` 这类表名可能定义在别的模块里）。

    **同名不同值不算错误**：仓里确实有两个 `GRANT_TABLE`（`knowledge_base_grant` 与
    `plugin_grant`）、两个 `CATALOG_TABLE`。这类名字只允许**本模块**解析；本模块没有时
    一律视为"解析不出"，交给 `test_scan_scope_and_resolvability` 指名报错——而不是让
    一个"全局最后写入者"悄悄决定表名。

    Returns:
        tuple[dict[str, str], set[str]]: `(唯一名 → 值, 有歧义的名字集合)`。
    """
    values: dict[str, str] = {}
    ambiguous: set[str] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        for name, value in _module_constants(tree).items():
            if name in values and values[name] != value:
                ambiguous.add(name)
            values.setdefault(name, value)
    return ({name: value for name, value in values.items() if name not in ambiguous}, ambiguous)


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """收集模块级字符串常量。

    Args:
        tree: 语法树。

    Returns:
        dict[str, str]: 常量名 → 值。
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.target.id] = node.value.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.targets[0].id] = node.value.value
    return constants


def _resolve_fstring(node: ast.JoinedStr, local: dict[str, str]) -> str | None:
    """把 f-string 里的 `{CONST}` 换成真实值。

    Args:
        node: f-string 节点。
        local: 本模块常量。

    Returns:
        str | None: 解析后的文本；出现非"模块常量占位符"时返回 None。
    """
    unique, _ = _global_constants()
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
            continue
        if isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
            name = value.value.id
            # 本模块优先（同名不同值的常量只允许本模块解析），全局表只收唯一名。
            resolved = local.get(name, unique.get(name))
            if resolved is None:
                return None
            parts.append(resolved)
            continue
        return None
    return "".join(parts)


def _iter_sql_texts(node: ast.AST, local: dict[str, str]) -> Any:
    """遍历"最外层"字符串表达式，产出 `(文本 | None, 行号, INSERT 提及次数)`。

    只看最外层：f-string 内部的常量片段不再单独产出，否则一次 `INSERT INTO` 会被数两次。
    函数调用不是叶子——SQL 常是 `uow.execute(f"...")` 的实参。

    Args:
        node: 语法树节点。
        local: 本模块常量。

    Yields:
        tuple[str | None, int, int]: `(文本, 行号, 提及次数)`。
    """
    if isinstance(node, ast.JoinedStr):
        text = _resolve_fstring(node, local)
        mentions = sum(
            inner.value.upper().count("INSERT INTO")
            for inner in ast.walk(node)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
        )
        if text is not None:
            mentions = max(mentions, text.upper().count("INSERT INTO"))
        yield text, node.lineno, mentions
        return
    if isinstance(node, ast.BinOp):
        mentions = sum(
            inner.value.upper().count("INSERT INTO")
            for inner in ast.walk(node)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
        )
        yield None, node.lineno, mentions
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value, node.lineno, node.value.upper().count("INSERT INTO")
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_sql_texts(child, local)


def _parse_statements(text: str) -> list[tuple[str, list[str], list[str]]]:
    """从一段 SQL 文本里切出全部 `INSERT INTO <表> (列) VALUES (值)`。

    Args:
        text: 已解析出真实表名的 SQL 文本。

    Returns:
        list[tuple[str, list[str], list[str]]]: `(表名, 列清单, 值清单)`。
    """
    found: list[tuple[str, list[str], list[str]]] = []
    for head in _INSERT_HEAD.finditer(text):
        table = head.group(1).split(".")[-1]
        columns = _balanced(text, head.end() - 1)
        if columns is None:
            continue
        body, after_columns = columns
        rest = text[after_columns:]
        values_match = re.match(r"\s*VALUES\s*", rest, re.IGNORECASE)
        if values_match is None:
            # `INSERT INTO t SELECT ...` 这类没有列清单的形式：不是本文判据的对象。
            continue
        values = _balanced(rest, values_match.end())
        if values is None:
            continue
        found.append((table, split_top(body), split_top(values[0])))
    return found


def insert_statements() -> list[InsertStatement]:
    """扫描**全 `app/`** 的 INSERT 语句（AST 取串 + 配对括号切分）。

    Returns:
        list[InsertStatement]: 逐条语句（按文件、行号排序）。
    """
    statements: list[InsertStatement] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "INSERT INTO" not in source.upper():
            continue
        tree = ast.parse(source)
        local = _module_constants(tree)
        for text, lineno, mentions in _iter_sql_texts(tree, local):
            if not mentions:
                continue
            if text is None:
                continue  # 交给 test_all_inserts_are_statically_resolvable 判红
            for table, columns, values in _parse_statements(text):
                statements.append(
                    InsertStatement(
                        file=str(path.relative_to(APP_ROOT)),
                        lineno=lineno,
                        table=table,
                        columns=tuple(columns),
                        values=tuple(values),
                    )
                )
    return statements


def unresolved_inserts() -> list[tuple[str, int]]:
    """列出"含 INSERT INTO 但表名/形态解析不出"的位置。

    Returns:
        list[tuple[str, int]]: `(文件, 行号)`。
    """
    unresolved: list[tuple[str, int]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "INSERT INTO" not in source.upper():
            continue
        tree = ast.parse(source)
        local = _module_constants(tree)
        for text, lineno, mentions in _iter_sql_texts(tree, local):
            if not mentions:
                continue
            parsed = 0 if text is None else len(_parse_statements(text))
            if parsed < mentions:
                unresolved.append((str(path.relative_to(APP_ROOT)), lineno))
    return unresolved


# ---------------------------------------------------------------------------
# 自检：先证明判据实现本身会红（否则下面的断言都是"看着在查"）
# ---------------------------------------------------------------------------


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


def test_adjacent_literal_is_folded() -> None:
    """反例自检：相邻字符串字面量必须被当成**一条**语句（解析期折叠）。

    `tenant_data_service.py` 的 `plugin_task` 写入就是这种形态：
    `"INSERT INTO plugin_task (" " tenant_id, task_id, ..." ") VALUES (" ...`。
    按行/按字面量做正则的实现会在这里切出带引号的假列名（`'"\\n  tenant_id'`），
    然后判出一堆假红或直接漏掉整条。
    """
    source = (
        "SQL = (\n"
        '    "INSERT INTO widget ("\n'
        '    " a, b, c"\n'
        '    ") VALUES ("\n'
        '    " :a, :b, :c"\n'
        '    ")"\n'
        ")\n"
    )
    tree = ast.parse(source)
    texts = [text for text, _, mentions in _iter_sql_texts(tree, {}) if mentions]
    assert len(texts) == 1, f"相邻字面量应折叠成一条语句，实际 {texts}"
    parsed = _parse_statements(texts[0] or "")
    assert parsed == [("widget", ["a", "b", "c"], [":a", ":b", ":c"])], f"实际 {parsed}"


def test_table_constant_is_resolved() -> None:
    """反例自检：表名是 f-string 常量时也必须解析出真实表名。

    表名常量可能定义在**别的模块**（`AUDIT_TABLE` 在 `core/audit_actions.py`），
    所以解析要落到全仓常量表；只认本模块常量会把 `audit_service` 那条判成"解析不出"。
    """
    source = (
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "def write(x: str) -> str:\n"
        '    return f"INSERT INTO {AUDIT_TABLE} (a, b) VALUES (:a, :b)"\n'
    )
    tree = ast.parse(source)
    texts = [text for text, _, mentions in _iter_sql_texts(tree, {}) if mentions]
    assert len(texts) == 1
    assert texts[0] is not None, "跨模块表名常量未被解析"
    parsed = _parse_statements(texts[0])
    assert parsed and parsed[0][0] == "audit_log", f"实际 {parsed}"


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------


def test_scan_scope_and_resolvability() -> None:
    """扫描范围与可解析性：条数有下限，且不允许"解析不出"的语句存在。"""
    statements = insert_statements()
    assert len(statements) >= MIN_INSERT_STATEMENTS, (
        f"只扫描到 {len(statements)} 条 INSERT（下限 {MIN_INSERT_STATEMENTS}）——扫描范围疑似缩水"
    )
    unresolved = unresolved_inserts()
    assert not unresolved, (
        f"有 {len(unresolved)} 处 INSERT 的形态无法静态解析（表名用局部变量 / `+` 拼接 / "
        f"`.format()`）：{unresolved}——这些语句不参与判定，结论不成立"
    )


def test_insert_column_value_arity_matches() -> None:
    """每条 INSERT 的列数必须等于值数（`P3-B19-04` 的直接判据）。"""
    statements = insert_statements()
    assert statements, "未扫描到任何 INSERT 语句——判据输入为空，结论不成立"

    mismatches = [
        (item.file, item.lineno, item.table, len(item.columns), len(item.values))
        for item in statements
        if len(item.columns) != len(item.values)
    ]
    assert not mismatches, f"INSERT 列数与值数不一致（文件, 行, 表, 列, 值）：{mismatches}"


def test_insert_tables_are_declared_in_ddl() -> None:
    """INSERT 的目标表必须存在于 DDL（否则 SQL 运行期必然失败）。"""
    tables = ddl_tables()
    assert tables, "未能从 deploy/**.sql 解析出任何表——判据输入为空，结论不成立"

    unknown = sorted({item.table for item in insert_statements() if item.table not in tables})
    assert not unknown, f"INSERT 写入了 DDL 里不存在的表：{unknown}"


def test_insert_columns_exist_in_their_table() -> None:
    """INSERT 引用的列必须存在于**该表**（不是"任意表"）。

    旧判据把所有表的列并成一个集合，于是"把 A 表的列写进 B 表"这种错误查不出来
    ——跨表假绿。这里按表逐列核对。
    """
    tables = ddl_tables()
    assert tables, "未能从 deploy/**.sql 解析出任何表——判据输入为空，结论不成立"

    unknown: list[tuple[str, int, str, str]] = []
    for item in insert_statements():
        shape = tables.get(item.table)
        if shape is None:
            continue  # 由 test_insert_tables_are_declared_in_ddl 判定
        for column in item.columns:
            if column not in shape.columns:
                unknown.append((item.file, item.lineno, item.table, column))
    assert not unknown, f"INSERT 引用了该表没有的列（文件, 行, 表, 列）：{unknown}"
