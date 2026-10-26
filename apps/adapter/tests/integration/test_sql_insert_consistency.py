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

**兜底不变量（fail-closed）**：某个文件里**出现了 `INSERT` 字样**（docstring 除外），却
既没入账一条语句、也没报一处不可解析 → 判红。这条不依赖任何具体拼装形态，因此
`"\n".join([...])` / `+=` / 关键字来自函数返回值 这类"没建模"的写法也不会静默蒸发；
代价是**可能误红**（把合法但拆得太散的写法判红），修法统一为：把 SQL 写成**一个**
f-string（表名取模块常量，列清单与 VALUES 都在同一个串里）。

**仓内约定**：INSERT 必须显式写列清单（`INSERT INTO t (a, b) VALUES (...)`）。
没有列清单的写法（`INSERT INTO t VALUES (...)`）会被 `unresolved_inserts()` 判红——
不是"不支持"，而是"不写列清单就无法与 DDL 对账"，属于要修掉的写法。

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
#: 仓储层 37 + 业务层 3（audit_log / integration_outbox / plugin_task）+ 迁移 1
#: （schema_migration，写的是 `(version, checksum)`，**有**列清单）= **41 条**。
#: 低于这个数就是"没看"，不是"没问题"。
MIN_INSERT_STATEMENTS: Final[int] = 38

#: `INSERT INTO <表> (` 的头部（表名只认标识符，表名由 f-string 常量或字面量给出）。
_INSERT_HEAD: Final[re.Pattern[str]] = re.compile(
    r"INSERT\s+INTO\s+([a-z_][\w.]*)\s*\(", re.IGNORECASE
)

#: 严格形态：与解析器完全同口径（`INSERT` + 任意空白 + `INTO`）。
_STRICT_MENTION: Final[re.Pattern[str]] = re.compile(r"INSERT\s+INTO", re.IGNORECASE)

#: 宽松形态：短语被"任意间隔"切断（换行 / `{}` / `%s` / `{conflict_clause}` / `+` / `join`）。
#: **只在文本里出现 `VALUES` 时启用**——否则英文散文里的 insert-the-row-into 会被误算。
#: 不用"间隔长度上限"：`{conflict_clause}` 这种长占位符会把魔数撑爆（12/13 字符悬崖），
#: 而"两个词都在 + 有 VALUES"本身就是足够强的信号。
_INSERT_WORD: Final[re.Pattern[str]] = re.compile(r"\bINSERT\b", re.IGNORECASE)

#: 宽松形态的另一半。
_INTO_WORD: Final[re.Pattern[str]] = re.compile(r"\bINTO\b", re.IGNORECASE)

#: 宽松形态的启用条件（"这段文本像不像 SQL"）。
_SQL_CONTEXT: Final[re.Pattern[str]] = re.compile(r"\bVALUES\b", re.IGNORECASE)


def _count_mentions(text: str) -> int:
    """数一段文本里"在说一条 INSERT"的次数（与解析器同口径，并容忍短语被切断）。

    口径必须与 `_INSERT_HEAD`（`INSERT\\s+INTO`）对齐：计数若只认字面的 `"INSERT INTO"`
    （单空格），`INSERT\\n INTRO …` 这种排版会被解析器认、却被计数判成 0 提及，
    语句于是**凭空消失**（既不进 `insert_statements()` 也不进 `unresolved_inserts()`）——
    这正是本判据要防的"看不见就没了"。

    Args:
        text: 待检查文本。

    Returns:
        int: 提及次数。
    """
    strict = len(_STRICT_MENTION.findall(text))
    if strict or not _SQL_CONTEXT.search(text):
        return strict
    if _INSERT_WORD.search(text) and _INTO_WORD.search(text):
        return 1
    return 0


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
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue  # 解析不了的骨架文件由 _scan() 记为不可解析，这里跳过即可
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


def _joined_constants(node: ast.AST, local: dict[str, str]) -> str:
    """把节点内的字符串常量与**常量名引用**按出现顺序拼接（用于数提及次数）。

    必须拼起来再数，否则三种写法都会让语句凭空蒸发（漏报）：
    - `"INSERT" + " INTO t (...)"`：短语被拆到两个字面量；
    - `PREFIX + " INTO t (...)"`：关键字在常量里（`PREFIX = "INSERT"`）；
    - `"".join(["INSERT ", "INTO t (...)"])`：短语在列表元素里。

    Args:
        node: 语法树节点。
        local: 本模块常量（再兜底全仓唯一名）。

    Returns:
        str: 拼接后的文本。
    """
    unique, _ = _global_constants()
    pieces: list[tuple[int, int, str]] = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            pieces.append((inner.lineno, inner.col_offset, inner.value))
        elif isinstance(inner, ast.Name):
            resolved = local.get(inner.id, unique.get(inner.id))
            if resolved is not None:
                pieces.append((inner.lineno, inner.col_offset, resolved))
    pieces.sort()
    return "\n".join(piece for _, _, piece in pieces)


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """收集模块/类/函数 docstring 的节点 id（这些文本不是 SQL）。

    Args:
        tree: 模块语法树。

    Returns:
        set[int]: 需要跳过的节点 id 集合。
    """
    skip: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            skip.add(id(body[0].value))
    return skip


def _contains_insert_word(tree: ast.Module, skip: set[int]) -> bool:
    """这个文件里是否出现 `INSERT` 字样（docstring 与注释不算）。

    Args:
        tree: 模块语法树。
        skip: docstring 节点 id。

    Returns:
        bool: 出现返回 True。
    """
    return any(
        _INSERT_WORD.search(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
    )


def _iter_sql_texts(node: ast.AST, local: dict[str, str], skip: set[int]) -> Any:
    """遍历"最外层"字符串表达式，产出 `(文本 | None, 行号, INSERT 提及次数)`。

    只看最外层：f-string 内部的常量片段不再单独产出，否则一次 `INSERT INTO` 会被数两次。
    函数调用不是叶子——SQL 常是 `uow.execute(f"...")` 的实参。
    docstring 不参与（`skip`）：文档里写一句 `INSERT INTO …` 不该让门禁变红。

    Args:
        node: 语法树节点。
        local: 本模块常量。
        skip: 需要跳过的节点 id（docstring）。

    Yields:
        tuple[str | None, int, int]: `(文本, 行号, 提及次数)`。
    """
    if id(node) in skip:
        return
    if isinstance(node, ast.JoinedStr):
        text = _resolve_fstring(node, local)
        mentions = _count_mentions(_joined_constants(node, local))
        if text is not None:
            mentions = max(mentions, _count_mentions(text))
        yield text, node.lineno, mentions
        return
    if isinstance(node, ast.BinOp):
        yield None, node.lineno, _count_mentions(_joined_constants(node, local))
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value, node.lineno, _count_mentions(node.value)
        return
    children = [
        item for child in ast.iter_child_nodes(node) for item in _iter_sql_texts(child, local, skip)
    ]
    yield from children
    if isinstance(node, ast.Call | ast.List | ast.Tuple | ast.Dict | ast.Subscript | ast.AugAssign):
        # `"".join(["INSERT ", "INTO t (...)"])` 这类"短语跨元素"的写法：子树里每个
        # 字面量单独看都不含完整的 INSERT，但拼起来是。用"聚合计数的增量"补一条
        # 不可解析记录——否则语句会从两个入口同时消失。
        own = _count_mentions(_joined_constants(node, local))
        seen = sum(item[2] for item in children)
        if own > seen:
            yield None, node.lineno, own - seen


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
            # 列清单后面不是 VALUES（`INSERT INTO t (a) SELECT ...`）：本函数切不出来，
            # 但**不会被静默放过**——`unresolved_inserts()` 会发现 mentions > parsed 并判红。
            continue
        values = _balanced(rest, values_match.end())
        if values is None:
            continue
        found.append((table, split_top(body), split_top(values[0])))
    return found


@lru_cache(maxsize=1)
def _scan() -> tuple[tuple[InsertStatement, ...], tuple[tuple[str, int], ...]]:
    """全量扫描一次（结果缓存：多个用例共用同一份扫描）。

    **不做文本预筛**：`"INSERT" + " INTO ..."` 这种写法在源码里没有连续的
    `INSERT INTO` 字样，文本预筛会整文件跳过——那正是本判据要抓的形态。

    Returns:
        tuple[tuple[InsertStatement, ...], tuple[tuple[str, int], ...]]:
            `(语句列表, 不可解析位置列表)`。
    """
    statements: list[InsertStatement] = []
    unresolved: list[tuple[str, int]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="ignore")
        relative = str(path.relative_to(APP_ROOT))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # 解析不了就没法判定——按"不可解析"计，而不是让整个用例抛异常崩掉
            unresolved.append((relative, 0))
            continue
        local = _module_constants(tree)
        skip = _docstring_nodes(tree)
        file_statements = 0
        file_unresolved = 0
        for text, lineno, mentions in _iter_sql_texts(tree, local, skip):
            if not mentions:
                continue
            parsed = _parse_statements(text) if text is not None else []
            for table, columns, values in parsed:
                statements.append(
                    InsertStatement(
                        file=relative,
                        lineno=lineno,
                        table=table,
                        columns=tuple(columns),
                        values=tuple(values),
                    )
                )
                file_statements += 1
            if len(parsed) < mentions:
                unresolved.append((relative, lineno))
                file_unresolved += 1
        # 文件级兜底不变量：这个文件里**出现了 INSERT 字样**，却既没入账也没报错，
        # 说明它是用某种我们没建模的拼装方式写的（`"\n".join([...])`、`+=`、
        # 函数返回关键字…）。这类形态无法逐个建模，但"有字样、零结果"必须可见——
        # 否则语句就从两个入口同时消失。
        if file_statements == 0 and file_unresolved == 0 and _contains_insert_word(tree, skip):
            unresolved.append((relative, 0))
    statements.sort(key=lambda item: (item.file, item.lineno, item.table))
    return tuple(statements), tuple(unresolved)


def insert_statements() -> list[InsertStatement]:
    """扫描**全 `app/`** 的 INSERT 语句（AST 取串 + 配对括号切分）。

    Returns:
        list[InsertStatement]: 逐条语句（按文件、行号排序）。
    """
    return list(_scan()[0])


def unresolved_inserts() -> list[tuple[str, int]]:
    """列出"含 INSERT INTO 但表名/形态解析不出"的位置。

    Returns:
        list[tuple[str, int]]: `(文件, 行号)`。
    """
    return list(_scan()[1])


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

    **断言必须落在"只由 ALTER 贡献"的列上**。本用例原先断言
    `confirmed_step_seq / version / version_history`，而这三列在 `init-schema-phase3.sql`
    的 `CREATE TABLE` 里**本来就写了**——把 ALTER 解析关掉它仍然绿（独立复核实测），
    属"永不红的装饰性夹具"。现在改用 `audit_log.trace_id` / `usage_event.profile`
    这类**只在 `ALTER TABLE … ADD COLUMN` 里出现**的列（2026-10-27 实测共 10 列，
    全部落在 `audit_log` 与 `usage_event` 上）。

    后补列是迁移的常态；漏解析它会让"写侧引用了必填列"被判成"列不存在"——**假红**。
    """
    tables = ddl_tables()
    alter_only = {
        "audit_log": ("trace_id", "actor_role", "change_summary", "action_display_name"),
        "usage_event": ("profile", "provider", "prompt_tokens", "completion_tokens"),
    }
    for table, columns in alter_only.items():
        shape = tables.get(table)
        assert shape is not None, f"DDL 里找不到表 {table}"
        for column in columns:
            assert column in shape.columns, (
                f"`{table}.{column}` 只由 `ALTER TABLE … ADD COLUMN` 贡献，未被解析——"
                f"关闭 ALTER 解析时本用例必须红"
            )
    assert "queue_position" not in ddl_columns(), (
        "`queue_position` 不在任何建表/迁移里（它是派生值），不该出现在 DDL 列集合里"
    )


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
    texts = [text for text, _, mentions in _iter_sql_texts(tree, {}, set()) if mentions]
    assert len(texts) == 1, f"相邻字面量应折叠成一条语句，实际 {texts}"
    parsed = _parse_statements(texts[0] or "")
    assert parsed == [("widget", ["a", "b", "c"], [":a", ":b", ":c"])], f"实际 {parsed}"

    # 再钉住**真实**那条：`tenant_data_service.py` 的 plugin_task 写入同样是相邻字面量
    real = [
        item
        for item in insert_statements()
        if item.table == "plugin_task"
        and item.file.replace("\\", "/").endswith("tenant_data_service.py")
    ]
    assert len(real) == 1, f"未找到 tenant_data_service 的 plugin_task 写入：{real}"
    statement = real[0]
    assert len(statement.columns) == 17, f"列数应为 17，实际 {len(statement.columns)}"
    dirty = [c for c in statement.columns if '"' in c or "\n" in c or "'" in c]
    assert not dirty, f"列名被切碎（含引号/换行）：{dirty}"


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
    texts = [text for text, _, mentions in _iter_sql_texts(tree, {}, set()) if mentions]
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
        f"有 {len(unresolved)} 处 INSERT 的形态无法静态解析"
        f'（表名用局部变量 / `+` 拼接 / `.format()` / `"".join([...])` / 关键字存在常量里 / '
        f"文件本身语法错）：{unresolved}——这些语句不参与判定，结论不成立。"
        f"请改成「表名取模块常量、列清单写在同一个 f-string 里」的形态"
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
