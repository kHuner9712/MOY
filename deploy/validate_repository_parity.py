# ruff: noqa: S603 - 本脚本只在 CI/本地被 `python` 直接执行，不接收用户输入拼命令行。
"""仓储双实现一致性门禁（G-38，架构文档 §9.6）。

## 为什么需要它

`app/db/repositories/` 下每个存储单元都是**双实现**：`InMemoryXxxStore`（development /
test）与 `PostgresXxxStore`（生产）背后是同一份 `XxxStore` Protocol。测试跑的是内存实现，
生产跑的是 PG 实现——**两条路径只在生产才分叉，本地永远只验一条**。于是出现了一类
"测试全绿、上线就坏"的缺陷：

- 协议新增一个方法，只补了内存侧，PG 侧忘了 → `AttributeError` 只在上线后出现；
- 两侧同名方法参数名/默认值不一致 → 关键字调用在一侧静默走默认值；
- 同一 record 在内存侧构造时少写一个字段 → 该字段在内存后端恒为默认值（本项目实测
  就是 `RoleRecord.member_count`：内存 `get_role` 恒 0、PG 返回真实计数，见 P1-8b）。

这类缺陷的共同点是**没有任何用例会红**：单测只跑内存实现，集成用例跑 PG 但不覆盖
"两侧形状是否一致"。本脚本把"一致性"变成**可执行**的：形状不一致即红，除非在基线里
登记了已评审的消除计划。

## 与 G-37（`validate_import_directions.py`）的关系

两者都由 `deploy/` 下的独立脚本承载，但**判定对象不同**：G-37 管"层与层之间的方向"，
G-38 管"同一层内两个实现的形状"。本脚本**不 import** G-37 的任何内部符号——只沿用它
已被评审过的外部契约（`--baseline` / `--self-test` / 退出码语义），避免两个门禁在代码层
互相耦合（改一个的解析器不该能弄红另一个）。

## 单元（unit）的两种形态

1. **文件形态**：`app/db/repositories/<name>_repo.py`（本轮 19 个）；
2. **包形态**：`app/db/repositories/<name>/`，必须同时含 `memory.py` 与 `postgres.py`
   （可选 `records.py` / `protocol.py` / 其他）。

同名并存时（拆分过渡期）取**包形态**，此时 `<name>_repo.py` 必须是**纯 re-export shim**：
只允许 docstring / `import` / `__all__` 与常量赋值；出现任何 `ClassDef` / `FunctionDef`
或其它顶层语句即 exit 2（否则"薄壳"会重新长成第二份实现，拆分等于没做）。

单元名 = 文件名去掉 `_repo` 后缀，或包目录名。

## 五条规则（unit 粒度；`[FAIL]` / `[WARN]` / `[INFO]` 见"退出码"一节）

实现三元组按**协议名**配对：`XxxStore` → `InMemoryXxxStore` / `PostgresXxxStore`；
协议名带 `Protocol` 后缀时先去掉（`DocumentStoreProtocol` → `InMemoryDocumentStore`）。
配不上三元组即 exit 2，例外只有 `NO_PROTOCOL_UNITS`（`audit`：该单元只有查询函数，
PG 写入实现故意留在 `business/audit_service.py`，见其模块注释）。

| 规则 | 判据 | 级别 |
| --- | --- | --- |
| **R1 `proto_missing`** | 协议的公开方法必须 ⊆ 内存实现且 ⊆ PG 实现，缺一个即违规 | FAIL |
| **R2 `impl_asym`** | `(内存公开 − TEST_HOOKS) == (PG 公开 − TEST_HOOKS)`，不等即违规；`TEST_HOOKS` 出现在 PG 侧单独违规 | FAIL |
| **R3 `sig_mismatch`** | 同名方法：位置参数名序列、keyword-only 名序列、`*args`/`**kwargs` 名、共同参数默认值（`ast.unparse` 文本）、同步/异步、`@property` 属性性全部相同 | FAIL |
| **R4 `record_ctor_asym`** | 每侧把 record 的**直接构造点**关键字集合收成 `set[frozenset[str]]`（同形构造自动去重），两侧**逐集合相等**才通过；任一侧没有任何直接构造点 → 只 `[INFO]` 跳过（该侧经映射器/服务层构造，无可比对象） | FAIL |
| **R5 `proto_undeclared`** | 双实现都有、协议未声明的方法 | 只 `[INFO]`（不判红、不入基线） |

**公开方法口径**：非 `_` 开头、非 dunder；`def` / `async def` / `@property` 都计入
（property 也是公开面，只是调用形态不同，所以 R3 里单独比"属性性"）。

### R4 为什么按「形状集合」逐点对齐、且**不给映射器豁免**（v2 收紧）

v1 有两条洗白通道，QA 用副本各实证了一次，两条都能让真实漂移变绿：

1. **按名字豁免**：v1 对「存在模块级 `_to_<stem>` / `_row_to_<stem>` 映射器」的 record 整体
   跳过。实测只要在实现里加一个与实现毫无关系的空 `_to_role`，同一条真实漂移
   （内存侧少 `member_count`）就从 `[FAIL]` 变成 `[INFO] 跳过`、exit 0。
   **豁免由一个自由命名的函数决定 = 给门禁开后门**，故整段映射器识别逻辑删除。
2. **并集比对**：v1 把同侧多个构造点的关键字取并集再和对侧比。实测「PG 有两个构造点、
   删掉其中一个的 `member_count`」并集仍含该字段 → 假绿。v2 改为**形状集合逐一对齐**：
   一侧多出/缺少任何一种形状都判红，因为"某个代码路径少写字段"正是在**单个构造点**上发生的。

代价与边界：只在一侧直接构造、另一侧走映射器/服务层的 record 无法比对 → `[INFO]` 跳过
（这是"看不到"，不是"没问题"，所以 INFO 会逐条打印出来，方便评审时人工确认）。

**`TEST_HOOKS = {"clear", "counts"}`**：测试隔离钩子只允许存在于内存侧——PG 侧出现
同名方法说明有人把测试钩子带进了生产实现。

**R3 不判返回注解**：`-> list[X]` 与 `-> Sequence[X]` 都是合法收敛，判红只会制造噪声；
真正要防的是"参数形状不一致导致调用在一侧静默走默认值"。

## 基线（`deploy/repository_parity_baseline.txt`）

格式：`<rule> <unit> <subject> -- <审计编号> <理由>`

- `subject` 是**方法名或 record 类名**（R1/R2/R3 用方法名，R4 用 record 类名）。
  R2 即使一次涉及多个方法也**逐方法各报一条**，不把一组方法压成一行——基线的粒度
  就是 stale 检测的粒度，"修好两个、还剩一个"必须能被表达出来。
- 基线内的违规 → `[WARN]`，不阻断（"已评审、有消除计划"）；
- 基线外的违规 → `[FAIL]`，exit 1；
- 基线条目在本轮扫描里已消失 → `[FAIL] stale`，exit 1（**防腐烂**：修完必须删行）；
- 基线格式错误（列数不是 3 / rule 不在 `{R1,R2,R3,R4}` / unit 不在实测集 / 审计编号不以
  `P0-`/`P1-`/`P2-` 开头 / 缺理由 / 重复行）→ exit 2；
- 基线文件**不存在** → 按空基线处理并打 `[INFO]`（**fail-closed**：不豁免任何违规，
  所以 `--baseline /dev/null` 必然在存在存量违规时 exit 1，这是"门禁不是空转"的机械证明）；
- **不提供 `--update-baseline`**：只许减不许增，新增豁免必须走评审。

## fail-closed 护栏

扫描前校验 `--repositories-root` 存在且是目录；可判定单元数 < `MIN_UNITS`（默认 15）即
exit 2。没有这条护栏时，目录被移动/改名会打印「扫描 0 个单元 / 违规 0 条 / [OK] exit 0」——
门禁在最该红的场景变绿。`--min-units` **仅供 `--self-test`** 下调，CI 不得传。

## 已知盲区

AST 只看**语法**：`setattr(self, name, ...)`、`__getattr__` 动态代理、`typing.cast`
绕过签名检查的实现都判不出来；跨文件的 record 构造（比如业务层直接构造仓储 record）
也不在 R4 范围内——R4 只对比"本单元内存区 vs PG 区"。这些需要 code review 收口，
本门禁的定位是"把机械可判的那 90% 钉死"。

## 退出码

- 0：无违规，或全部违规都已在基线登记；
- 1：存在基线外违规，或基线条目已 stale；
- 2：结构性问题（目录不存在/不是目录/单元数不足/三元组配不上/shim 不纯/基线格式错误/自检失败）。
"""

from __future__ import annotations

import argparse
import ast
import sys
import tempfile
import textwrap
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# ===========================================================================
# 常量
# ===========================================================================

#: 默认被扫描的仓储目录（相对本脚本定位，避免"从哪个 cwd 跑"影响结果）。
DEFAULT_REPOSITORIES_ROOT: Final[Path] = (
    Path(__file__).resolve().parent.parent / "apps" / "adapter" / "app" / "db" / "repositories"
)

#: 默认基线文件。
DEFAULT_BASELINE: Final[Path] = Path(__file__).resolve().parent / "repository_parity_baseline.txt"

#: 可判定单元数下限（**fail-closed**：低于它就是门禁失效，不是"没违规"）。
#: 实测 20 个：19 个 `*_repo.py` 文件单元 + 1 个 `role/` 包单元。
MIN_UNITS: Final[int] = 15

#: 测试隔离钩子：只允许出现在内存侧。
TEST_HOOKS: Final[frozenset[str]] = frozenset({"clear", "counts"})

#: 允许"没有协议"的单元（有实现必有协议之外的唯一例外）。
NO_PROTOCOL_UNITS: Final[frozenset[str]] = frozenset({"audit"})

#: 可登记进基线的规则（R5 只报 INFO，永不判红，因此不可登记）。
BASELINE_RULES: Final[frozenset[str]] = frozenset({"R1", "R2", "R3", "R4"})

#: 审计编号前缀白名单。
AUDIT_ID_PREFIXES: Final[tuple[str, ...]] = ("P0-", "P1-", "P2-")

#: 内存实现类名前缀。
MEMORY_PREFIX: Final[str] = "InMemory"

#: PG 实现类名前缀。
POSTGRES_PREFIX: Final[str] = "Postgres"

#: 协议类名可选后缀（`DocumentStoreProtocol` 与 `DocumentStore` 两种命名都要认）。
PROTOCOL_SUFFIX: Final[str] = "Protocol"

#: 包目录里不参与扫描的名字。
SKIP_PACKAGE_DIRS: Final[frozenset[str]] = frozenset({"__pycache__"})

#: 包形态必须具备的两个文件（缺一即"配不上三元组"）。
MEMORY_MODULE: Final[str] = "memory.py"

#: PG 形态必须具备的文件。
POSTGRES_MODULE: Final[str] = "postgres.py"

#: R6 豁免表：`(unit, record, field) → (原因, 失效条件)`。
#:
#: 只登记**已核实无读写点**的字段。每条都必须写清"什么情况下必须删掉本豁免"，
#: 否则豁免会变成永久后门。新增条目必须同时给出"为什么不能补映射"的证据。
R6_EXEMPTIONS: Final[dict[tuple[str, str, str], tuple[str, str]]] = {
    ("conversation", "MessageRecord", "history"): (
        "预留字段（消息编辑/多轮回放待落地）：app/ 与 tests/ 里对 `.history` 的读写为 0 处，"
        "`conversation_service` 两处构造均未传该参数，`app/schemas/` 也无对应字段",
        "消息编辑或多轮回放功能合入时必须补 `_to_message` 映射并删掉本豁免",
    ),
}

#: R6 只看 PG 侧的映射器（内存侧直接构造，由 R4 的形状比对覆盖）。
R6_MAPPER_PREFIX: Final[str] = "_to_"


# ===========================================================================
# 数据结构
# ===========================================================================


@dataclass(frozen=True, slots=True)
class MethodInfo:
    """一个方法的签名快照（只留 R3 需要的形状信息）。

    Attributes:
        name: 方法名。
        is_async: 是否 `async def`。
        is_property: 是否被 `@property` 装饰。
        positional: 位置参数名（posonly + normal，已剔除 `self`/`cls`）。
        keyword_only: keyword-only 参数名。
        vararg: `*args` 的名字，无则 None。
        kwarg: `**kwargs` 的名字，无则 None。
        defaults: `(参数名, 默认值源码文本)` 序列（无默认值的参数不出现）。
        lineno: 定义行号（报错定位用）。
    """

    name: str
    is_async: bool
    is_property: bool
    positional: tuple[str, ...]
    keyword_only: tuple[str, ...]
    vararg: str | None
    kwarg: str | None
    defaults: tuple[tuple[str, str], ...]
    lineno: int


@dataclass(frozen=True, slots=True)
class ClassInfo:
    """一个类的形状快照。

    Attributes:
        name: 类名。
        path: 定义所在文件。
        lineno: 定义行号。
        is_protocol: 是否继承 `typing.Protocol`。
        is_dataclass: 是否被 `@dataclass(...)` 装饰。
        bases: 基类源码文本序列（`ast.unparse`）。
        methods: 类体内的方法（不含嵌套类的方法）。
    """

    name: str
    path: Path
    lineno: int
    is_protocol: bool
    is_dataclass: bool
    bases: tuple[str, ...]
    methods: tuple[MethodInfo, ...]


@dataclass(frozen=True, slots=True)
class Unit:
    """一个仓储单元（文件形态或包形态）。

    Attributes:
        name: 单元名（`artifact` / `role` / ...）。
        kind: `"file"` 或 `"package"`。
        files: 参与判定的全部 `.py` 文件。
        memory_files: 内存实现所在文件。
        postgres_files: PG 实现所在文件。
        shim: 包形态并存时被保留的 `<name>_repo.py` 兼容层（文件形态为 None）。
    """

    name: str
    kind: str
    files: tuple[Path, ...]
    memory_files: tuple[Path, ...]
    postgres_files: tuple[Path, ...]
    shim: Path | None


@dataclass(frozen=True, slots=True)
class Violation:
    """一条判红（或基线内 WARN）的违规。

    Attributes:
        rule: `R1` ~ `R4`。
        unit: 单元名。
        subject: 方法名（R1/R2/R3）或 record 类名（R4）。
        detail: 给人看的具体差异。
    """

    rule: str
    unit: str
    subject: str
    detail: str

    def key(self) -> tuple[str, str, str]:
        """返回基线匹配键。

        Returns:
            tuple[str, str, str]: `(rule, unit, subject)`。
        """
        return (self.rule, self.unit, self.subject)


@dataclass(frozen=True, slots=True)
class MapperFinding:
    """R6 的一条发现：`_to_*` 映射器没有覆盖 record 的某个字段。

    Attributes:
        unit: 单元名。
        record: record 类名。
        field: 未被任何构造点写入的字段名。
        mapper: 映射器函数名。
        path: 映射器所在文件。
        lineno: 映射器定义行号。
        exempt: 豁免原因（None 表示未豁免，属真 WARN）。
        expire_when: 豁免失效条件（未豁免时为 None）。
    """

    unit: str
    record: str
    field: str
    mapper: str
    path: Path
    lineno: int
    exempt: str | None = None
    expire_when: str | None = None

    @property
    def is_exempt(self) -> bool:
        """是否命中豁免表。

        Returns:
            bool: 命中豁免返回 True。
        """
        return self.exempt is not None

    def describe(self) -> str:
        """渲染成一行人类可读说明。

        Returns:
            str: `R6 <unit> <record>.<field>（file:line，mapper）`。
        """
        where = f"{self.path.name}:{self.lineno}"
        return f"R6 {self.unit} `{self.record}.{self.field}`（{where}，mapper `{self.mapper}`）"


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """基线条目。

    Attributes:
        rule: `R1` ~ `R4`。
        unit: 单元名。
        subject: 方法名（R1/R2/R3）或 record 类名（R4）。
        audit_id: 审计编号（`P0-`/`P1-`/`P2-` 开头）。
        reason: 消除计划。
        lineno: 基线文件行号（报错定位用）。
    """

    rule: str
    unit: str
    subject: str
    audit_id: str
    reason: str
    lineno: int

    def key(self) -> tuple[str, str, str]:
        """返回基线匹配键。

        Returns:
            tuple[str, str, str]: `(rule, unit, subject)`。
        """
        return (self.rule, self.unit, self.subject)


@dataclass(slots=True)
class ScanResult:
    """一次扫描的全部产出。

    Attributes:
        units: 发现的单元（已排序）。
        violations: 判红级违规（R1 ~ R4）。
        infos: 只提示不判红的说明（R5 / 映射器跳过 / 单侧无构造点）。
        problems: 结构性问题（出现任一即 exit 2）。
        r6: R6 的映射器字段覆盖发现（默认 WARN-only，`--strict-r6` 才计入退出码）。
    """

    units: list[Unit]
    violations: list[Violation]
    infos: list[str]
    problems: list[str]
    r6: list[MapperFinding] = field(default_factory=list)


# ===========================================================================
# AST 解析与形状提取
# ===========================================================================


def _parse(path: Path) -> ast.Module:
    """解析一个 Python 文件。

    Args:
        path: 文件路径。

    Returns:
        ast.Module: 语法树。
    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _decorator_names(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """提取装饰器的源码文本（`@dataclass(frozen=True)` 取 `dataclass(...)`）。

    Args:
        node: 类或函数节点。

    Returns:
        tuple[str, ...]: 装饰器文本序列。
    """
    return tuple(ast.unparse(decorator) for decorator in node.decorator_list)


def _has_decorator(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """判断是否带某个装饰器（`@name` 或 `@name(...)` 都算）。

    Args:
        node: 类或函数节点。
        name: 装饰器名字（不含 `@`）。

    Returns:
        bool: 带该装饰器返回 True。
    """
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == name:
            return True
        if isinstance(target, ast.Attribute) and target.attr == name:
            return True
    return False


def _method_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodInfo:
    """把一个方法节点压成 `MethodInfo`。

    Args:
        node: 方法节点。

    Returns:
        MethodInfo: 签名快照。
    """
    args = node.args
    all_positional = [*args.posonlyargs, *args.args]
    names = [item.arg for item in all_positional]
    # `self` / `cls` 是绑定语义的一部分，不参与"跨实现比对"（`@staticmethod` 另说，但
    # 本项目的仓储实现没有 staticmethod；即使有，`self` 也不在参数表里）。
    if names and names[0] in {"self", "cls"}:
        names = names[1:]
        all_positional = all_positional[1:]

    raw_defaults: dict[str, str] = {}
    paired = zip(all_positional[len(all_positional) - len(args.defaults) :], args.defaults)
    for arg_node, default_node in paired:
        raw_defaults[arg_node.arg] = ast.unparse(default_node)
    for arg_node, default_node in zip(args.kwonlyargs, args.kw_defaults):
        # keyword-only 的 `None` 默认表示"必填"（`**` 之后不能有位置默认值），
        # 用哨兵文本与真实 `None` 默认值区分开。
        raw_defaults[arg_node.arg] = (
            "<required>" if default_node is None else ast.unparse(default_node)
        )

    ordered_defaults = tuple(
        (item.arg, raw_defaults[item.arg]) for item in [*all_positional, *args.kwonlyargs]
        if item.arg in raw_defaults
    )
    return MethodInfo(
        name=node.name,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_property=_has_decorator(node, "property"),
        positional=tuple(names),
        keyword_only=tuple(item.arg for item in args.kwonlyargs),
        vararg=args.vararg.arg if args.vararg is not None else None,
        kwarg=args.kwarg.arg if args.kwarg is not None else None,
        defaults=ordered_defaults,
        lineno=node.lineno,
    )


def _class_info(node: ast.ClassDef, path: Path) -> ClassInfo:
    """把一个类节点压成 `ClassInfo`。

    Args:
        node: 类节点。
        path: 定义所在文件。

    Returns:
        ClassInfo: 类形状快照。
    """
    bases = tuple(ast.unparse(base) for base in node.bases)
    is_protocol = any(base == "Protocol" or base.endswith(".Protocol") for base in bases)
    methods = tuple(
        _method_info(item)
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    return ClassInfo(
        name=node.name,
        path=path,
        lineno=node.lineno,
        is_protocol=is_protocol,
        is_dataclass=_has_decorator(node, "dataclass"),
        bases=bases,
        methods=methods,
    )


def _public_methods(info: ClassInfo) -> tuple[MethodInfo, ...]:
    """提取公开方法（非 `_` 开头、非 dunder；property 与 async 都算）。

    Args:
        info: 类形状快照。

    Returns:
        tuple[MethodInfo, ...]: 按源码声明序排列的公开方法。
    """
    return tuple(item for item in info.methods if not item.name.startswith("_"))


def _method_map(info: ClassInfo) -> dict[str, MethodInfo]:
    """把公开方法按名字索引（保留声明序）。

    Args:
        info: 类形状快照。

    Returns:
        dict[str, MethodInfo]: 方法名 → 快照。
    """
    return {item.name: item for item in _public_methods(info)}


# ===========================================================================
# 单元发现
# ===========================================================================


def _check_shim(path: Path) -> list[str]:
    """校验"同名并存时的 `*_repo.py`"是否是纯 re-export 壳。

    只允许：文件 docstring / `import` / `from ... import ...` / `__all__` 赋值 /
    常量赋值（大写名）/ 其它字面量表达式语句。出现 `ClassDef` / `FunctionDef` / 循环 /
    调用等任何"会长出第二份实现"的语句即违规。

    Args:
        path: shim 文件路径。

    Returns:
        list[str]: 违规说明（空表示合格）。
    """
    problems: list[str] = []
    try:
        module = _parse(path)
    except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover - 语法错误极少见
        return [f"shim {path} 无法解析：{exc}"]

    imported: set[str] = set()
    exported: list[str] = []
    seen_all = False
    for node in module.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
            continue
        if isinstance(node, ast.ClassDef):
            problems.append(
                f"shim {path} 定义了类 `{node.name}`（第 {node.lineno} 行）：包形态并存时 "
                f"`*_repo.py` 必须是纯 re-export，不得出现类/函数定义"
            )
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            problems.append(
                f"shim {path} 定义了函数 `{node.name}`（第 {node.lineno} 行）：包形态并存时 "
                f"`*_repo.py` 必须是纯 re-export，不得出现类/函数定义"
            )
            continue
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    problems.append(
                        f"shim {path} 第 {node.lineno} 行出现非名字赋值的顶层语句："
                        f"re-export 壳不允许计算型赋值"
                    )
                    continue
                if target.id == "__all__":
                    seen_all = True
                    exported.extend(_literal_str_sequence(node.value))
                elif not target.id.isupper():
                    problems.append(
                        f"shim {path} 第 {node.lineno} 行赋值了非常量名 `{target.id}`："
                        f"re-export 壳只允许 `__all__` 与大写常量"
                    )
            continue
        if isinstance(node, ast.Expr):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                continue
            problems.append(
                f"shim {path} 第 {node.lineno} 行有非 docstring 的表达式语句：re-export 壳不允许"
            )
            continue
        problems.append(
            f"shim {path} 第 {node.lineno} 行出现不被允许的顶层语句 "
            f"`{type(node).__name__}`：re-export 壳只允许 import 与常量赋值"
        )

    if not seen_all:
        problems.append(f"shim {path} 没有 `__all__`：无法证明它是纯 re-export")
    missing = [name for name in exported if name not in imported]
    if missing:
        problems.append(f"shim {path} 的 `__all__` 含未导入的名字：{sorted(missing)}")
    return problems


def _literal_str_sequence(node: ast.expr) -> list[str]:
    """把 `["a", "b"]` / `("a",)` 这类字面量取成字符串列表。

    Args:
        node: 表达式节点（通常是 `__all__` 的右值）。

    Returns:
        list[str]: 字面量字符串序列；非字面量返回空列表。
    """
    if not isinstance(node, ast.List | ast.Tuple):
        return []
    names: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            names.append(element.value)
    return names


def discover_units(repositories_root: Path) -> tuple[list[Unit], list[str]]:
    """发现所有仓储单元（文件形态 + 包形态，同名并存取包形态）。

    Args:
        repositories_root: 仓储目录（`app/db/repositories`）。

    Returns:
        tuple[list[Unit], list[str]]: `(单元列表, 结构性问题列表)`。
    """
    problems: list[str] = []
    packages: dict[str, Unit] = {}

    for entry in sorted(repositories_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in SKIP_PACKAGE_DIRS or entry.name.startswith("."):
            continue
        memory = entry / MEMORY_MODULE
        postgres = entry / POSTGRES_MODULE
        if not memory.is_file() and not postgres.is_file():
            # 不是仓储包（如只有 __init__.py 的杂项目录），不参与判定。
            continue
        if not memory.is_file() or not postgres.is_file():
            problems.append(
                f"包形态单元 `{entry.name}` 缺 `{MEMORY_MODULE if not memory.is_file() else POSTGRES_MODULE}`："
                f"无法配对双实现（包形态必须同时含 memory.py 与 postgres.py）"
            )
            continue
        files = tuple(sorted(item for item in entry.glob("*.py") if item.is_file()))
        packages[entry.name] = Unit(
            name=entry.name,
            kind="package",
            files=files,
            memory_files=(memory,),
            postgres_files=(postgres,),
            shim=None,
        )

    units: list[Unit] = []
    for entry in sorted(repositories_root.glob("*_repo.py")):
        name = entry.stem[: -len("_repo")]
        if name in packages:
            shim_problems = _check_shim(entry)
            problems.extend(shim_problems)
            base = packages.pop(name)
            units.append(
                Unit(
                    name=base.name,
                    kind=base.kind,
                    files=base.files,
                    memory_files=base.memory_files,
                    postgres_files=base.postgres_files,
                    shim=entry,
                )
            )
            continue
        units.append(
            Unit(
                name=name,
                kind="file",
                files=(entry,),
                memory_files=(entry,),
                postgres_files=(entry,),
                shim=None,
            )
        )

    # 只有包形态、没有 `*_repo.py` 的单元（拆分完成后的终态）也要收进来。
    units.extend(packages.values())
    units.sort(key=lambda unit: unit.name)
    return units, problems


# ===========================================================================
# 双实现配对与逐单元判定
# ===========================================================================


def _load_classes(unit: Unit) -> tuple[dict[str, ClassInfo], dict[Path, ast.Module], list[str]]:
    """加载单元内全部类形状与语法树。

    Args:
        unit: 仓储单元。

    Returns:
        tuple[dict[str, ClassInfo], dict[Path, ast.Module], list[str]]:
        `(类名→形状, 文件→语法树, 问题列表)`。
    """
    classes: dict[str, ClassInfo] = {}
    modules: dict[Path, ast.Module] = {}
    problems: list[str] = []
    for path in unit.files:
        try:
            module = _parse(path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"单元 `{unit.name}` 的文件 {path} 无法解析：{exc}")
            continue
        modules[path] = module
        for node in module.body:
            if isinstance(node, ast.ClassDef):
                if node.name in classes:
                    problems.append(
                        f"单元 `{unit.name}` 的类名 `{node.name}` 重复定义"
                        f"（{classes[node.name].path} 与 {path}）"
                    )
                classes[node.name] = _class_info(node, path)
    return classes, modules, problems


def _pair_triples(
    unit: Unit, classes: dict[str, ClassInfo]
) -> tuple[list[tuple[ClassInfo, ClassInfo, ClassInfo]], list[str]]:
    """把协议与双实现配对成三元组。

    Args:
        unit: 仓储单元。
        classes: 单元内全部类。

    Returns:
        tuple[list[tuple[ClassInfo, ClassInfo, ClassInfo]], list[str]]:
        `(三元组列表, 问题列表)`。
    """
    problems: list[str] = []
    triples: list[tuple[ClassInfo, ClassInfo, ClassInfo]] = []
    consumed: set[str] = set()
    protocols = [info for info in classes.values() if info.is_protocol]
    for protocol in sorted(protocols, key=lambda item: item.name):
        stem = protocol.name
        if stem.endswith(PROTOCOL_SUFFIX) and len(stem) > len(PROTOCOL_SUFFIX):
            stem = stem[: -len(PROTOCOL_SUFFIX)]
        memory_name = f"{MEMORY_PREFIX}{stem}"
        postgres_name = f"{POSTGRES_PREFIX}{stem}"
        memory = classes.get(memory_name)
        postgres = classes.get(postgres_name)
        missing = [name for name, found in ((memory_name, memory), (postgres_name, postgres)) if found is None]
        if missing:
            problems.append(
                f"单元 `{unit.name}`：协议 `{protocol.name}` 无法配对三元组（缺 {missing}）"
                f"——双实现门禁的前提是 `(Protocol, InMemory*, Postgres*)` 三者齐备"
            )
            continue
        consumed.update({protocol.name, memory_name, postgres_name})
        triples.append((protocol, memory, postgres))  # type: ignore[arg-type]

    if not triples and unit.name not in NO_PROTOCOL_UNITS:
        problems.append(
            f"单元 `{unit.name}`：找不到任何 `(Protocol, InMemory*, Postgres*)` 三元组"
            f"（实测协议 {sorted(p.name for p in protocols)}）；若该单元本就没有协议，"
            f"须登记进 NO_PROTOCOL_UNITS 并写明理由"
        )
    leftovers = sorted(
        name
        for name, info in classes.items()
        if name not in consumed
        and (name.startswith(MEMORY_PREFIX) or name.startswith(POSTGRES_PREFIX))
    )
    if leftovers:
        problems.append(
            f"单元 `{unit.name}`：实现类 {leftovers} 找不到对应协议（应为 "
            f"`{'/'.join([MEMORY_PREFIX, POSTGRES_PREFIX])}<协议名>`）"
        )
    return triples, problems


def _region_calls(
    modules: dict[Path, ast.Module], paths: Iterable[Path], prefix: str, records: frozenset[str]
) -> dict[str, set[frozenset[str]]]:
    """统计某个区内对 record 的**直接构造点**关键字集合。

    区 = 该文件里 `<prefix>*` 类的方法体 + 模块级函数体。两侧都包含模块级函数，
    是因为 PG 侧的行映射器（`_to_*`）就写在模块级——v2 **不**对它做名字豁免，
    它的构造点与类方法里的构造点一视同仁地进入形状集合（这样"映射器里少写字段"
    同样判红）。

    Args:
        modules: 文件 → 语法树。
        paths: 该区的文件。
        prefix: 类名前缀（`InMemory` / `Postgres`）。
        records: 单元内 record 类名集合。

    Returns:
        dict[str, set[frozenset[str]]]: record 名 → 关键字集合的集合。
    """
    found: dict[str, set[frozenset[str]]] = {}
    bodies: list[ast.AST] = []
    for path in paths:
        module = modules.get(path)
        if module is None:
            continue
        for node in module.body:
            if isinstance(node, ast.ClassDef) and node.name.startswith(prefix):
                bodies.extend(
                    item
                    for item in node.body
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                )
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                bodies.append(node)
    for body in bodies:
        for node in ast.walk(body):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id not in records:
                continue
            keywords = frozenset(item.arg for item in node.keywords if item.arg is not None)
            found.setdefault(node.func.id, set()).add(keywords)
    return found


def _signature_diff(memory: MethodInfo, postgres: MethodInfo) -> list[str]:
    """比较两侧同名方法的签名形状（R3）。

    Args:
        memory: 内存侧方法。
        postgres: PG 侧方法。

    Returns:
        list[str]: 差异说明（空表示一致）。
    """
    issues: list[str] = []
    if memory.positional != postgres.positional:
        issues.append(f"位置参数 {list(memory.positional)} != {list(postgres.positional)}")
    if memory.keyword_only != postgres.keyword_only:
        issues.append(f"keyword-only 参数 {list(memory.keyword_only)} != {list(postgres.keyword_only)}")
    if memory.vararg != postgres.vararg:
        issues.append(f"`*args` 名 {memory.vararg} != {postgres.vararg}")
    if memory.kwarg != postgres.kwarg:
        issues.append(f"`**kwargs` 名 {memory.kwarg} != {postgres.kwarg}")
    if memory.is_async != postgres.is_async:
        issues.append(
            f"同步性不一致（内存侧 {'async' if memory.is_async else 'sync'} / "
            f"PG 侧 {'async' if postgres.is_async else 'sync'}）"
        )
    if memory.is_property != postgres.is_property:
        issues.append("`@property` 属性性不一致")
    memory_defaults = dict(memory.defaults)
    postgres_defaults = dict(postgres.defaults)
    for name in sorted(set(memory_defaults) & set(postgres_defaults)):
        if memory_defaults[name] != postgres_defaults[name]:
            issues.append(
                f"默认值 `{name}`={memory_defaults[name]} != {postgres_defaults[name]}"
            )
    return issues


def _check_impl_pair(
    unit: Unit, protocol: ClassInfo, memory: ClassInfo, postgres: ClassInfo
) -> tuple[list[Violation], list[str]]:
    """对一个三元组跑 R1 / R2 / R3 / R5。

    Args:
        unit: 仓储单元。
        protocol: 协议类。
        memory: 内存实现类。
        postgres: PG 实现类。

    Returns:
        tuple[list[Violation], list[str]]: `(违规列表, INFO 列表)`。
    """
    violations: list[Violation] = []
    infos: list[str] = []
    protocol_methods = _method_map(protocol)
    memory_methods = _method_map(memory)
    postgres_methods = _method_map(postgres)

    # R1：协议公开方法 ⊆ 两侧。
    for name in protocol_methods:
        if name not in memory_methods:
            violations.append(
                Violation(
                    rule="R1",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"协议 `{protocol.name}` 声明了 `{name}`，内存实现 `{memory.name}` 缺失"
                        f"（{memory.path.name}）"
                    ),
                )
            )
        if name not in postgres_methods:
            violations.append(
                Violation(
                    rule="R1",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"协议 `{protocol.name}` 声明了 `{name}`，PG 实现 `{postgres.name}` 缺失"
                        f"（{postgres.path.name}）"
                    ),
                )
            )

    # R2：两侧公开面（去掉测试钩子）必须逐字相同；测试钩子出现在 PG 侧单独判红。
    pg_hooks = [name for name in postgres_methods if name in TEST_HOOKS]
    for name in pg_hooks:
        violations.append(
            Violation(
                rule="R2",
                unit=unit.name,
                subject=name,
                detail=(
                    f"测试钩子 `{name}` 出现在 PG 实现 `{postgres.name}` 上"
                    f"（TEST_HOOKS 只允许内存侧，PG 侧出现即视为测试钩子漏进生产实现）"
                ),
            )
        )
    memory_only = [
        name
        for name in memory_methods
        if name not in postgres_methods and name not in TEST_HOOKS
    ]
    postgres_only = [
        name
        for name in postgres_methods
        if name not in memory_methods and name not in TEST_HOOKS
    ]
    if memory_only or postgres_only:
        # 逐方法各发一条（而不是把一组方法压成一个 `/` 连接的 subject）：基线的粒度
        # 决定了 stale 检测的粒度——"修好两个、还剩一个"必须能表达出来，否则只能
        # 全有或全无地挂账。
        for name in memory_only:
            violations.append(
                Violation(
                    rule="R2",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"`{name}` 只在内存实现 `{memory.name}` 上存在（第 "
                        f"{memory_methods[name].lineno} 行），PG 实现 `{postgres.name}` 无同名方法"
                    ),
                )
            )
        for name in postgres_only:
            violations.append(
                Violation(
                    rule="R2",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"`{name}` 只在 PG 实现 `{postgres.name}` 上存在（第 "
                        f"{postgres_methods[name].lineno} 行），内存实现 `{memory.name}` 无同名方法"
                    ),
                )
            )

    # R3：同名方法签名形状一致（返回注解按设计不判）。
    for name, memory_method in memory_methods.items():
        postgres_method = postgres_methods.get(name)
        if postgres_method is None:
            continue
        issues = _signature_diff(memory_method, postgres_method)
        if issues:
            violations.append(
                Violation(
                    rule="R3",
                    unit=unit.name,
                    subject=name,
                    detail=(
                        f"`{name}` 签名不一致（内存 {memory.name}:{memory_method.lineno} / "
                        f"PG {postgres.name}:{postgres_method.lineno}）：{'；'.join(issues)}"
                    ),
                )
            )

    # R5：双实现都有、协议未声明 —— 只提示。
    undeclared = [
        name
        for name in memory_methods
        if name in postgres_methods
        and name not in protocol_methods
        and name not in TEST_HOOKS
    ]
    if undeclared:
        infos.append(
            f"R5 {unit.name} `{protocol.name}`：双实现都有但协议未声明 {undeclared}"
            f"（只提示，不判红：补协议或删实现都由评审决定）"
        )
    return violations, infos


def _check_records(
    unit: Unit,
    classes: dict[str, ClassInfo],
    modules: dict[Path, ast.Module],
) -> tuple[list[Violation], list[str]]:
    """对一个单元跑 R4 v2（record 构造点形状**逐点对点**一致）。

    口径（v2 收紧，见模块 docstring 的「R4 为什么不用映射器豁免」）：

    - 每侧收集直接构造点的关键字集合的**集合**（`set[frozenset[str]]`，同形构造自动去重）；
    - 两侧**逐集合相等**才通过——任一侧多一个"少了某个字段"的形状即判红；
    - 任一侧**没有**任何直接构造点 → 只 `[INFO]` 跳过（该侧经映射器/服务层构造，无可比对象）；
    - **不再识别映射器**（旧 v1 的 `_to_<stem>` 豁免）：QA 实测"在实现里加一个与实现无关的
      空 `_to_role` 就能把真实漂移洗成 INFO 跳过"，名字豁免等于给门禁开后门。

    Args:
        unit: 仓储单元。
        classes: 单元内全部类。
        modules: 文件 → 语法树。

    Returns:
        tuple[list[Violation], list[str]]: `(违规列表, INFO 列表)`。
    """
    violations: list[Violation] = []
    infos: list[str] = []
    records = frozenset(name for name, info in classes.items() if info.is_dataclass)
    if not records:
        return violations, infos
    memory_calls = _region_calls(modules, unit.memory_files, MEMORY_PREFIX, records)
    postgres_calls = _region_calls(modules, unit.postgres_files, POSTGRES_PREFIX, records)
    for record in sorted(records):
        memory_shapes = memory_calls.get(record, set())
        postgres_shapes = postgres_calls.get(record, set())
        if not memory_shapes or not postgres_shapes:
            side = "内存区" if not memory_shapes else "PG 区"
            infos.append(
                f"R4 {unit.name} `{record}`：{side}没有直接构造点"
                f"（该侧经映射器/服务层构造），无可比对象，跳过"
            )
            continue
        if memory_shapes == postgres_shapes:
            continue
        memory_only = [sorted(shape) for shape in sorted(memory_shapes - postgres_shapes, key=sorted)]
        postgres_only = [sorted(shape) for shape in sorted(postgres_shapes - memory_shapes, key=sorted)]
        violations.append(
            Violation(
                rule="R4",
                unit=unit.name,
                subject=record,
                detail=(
                    f"`{record}` 的构造点形状两侧不一致（每侧按**形状集合**逐一对齐，"
                    f"不接受「并集含该字段」这种粗口径）：仅内存区的形状 {memory_only}；"
                    f"仅 PG 区的形状 {postgres_only}"
                    f"（形状里缺的字段在该后端恒为默认值——P1-8b 就是这么来的）"
                ),
            )
        )
    return violations, infos


def _dataclass_field_names(modules: dict[Path, ast.Module]) -> dict[str, tuple[str, ...]]:
    """收集单元内全部 dataclass 的字段名（只取类体里的 `AnnAssign`）。

    Args:
        modules: 文件 → 语法树。

    Returns:
        dict[str, tuple[str, ...]]: `类名 → 字段名序列`（保持声明顺序）。
    """
    result: dict[str, tuple[str, ...]] = {}
    for module in modules.values():
        for node in ast.walk(module):
            if not isinstance(node, ast.ClassDef) or not _has_decorator(node, "dataclass"):
                continue
            names = tuple(
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            )
            if names:
                result[node.name] = names
    return result


def _annotation_record_name(annotation: ast.expr | None) -> str | None:
    """取返回注解里的 record 类名。

    映射器常用三种写法：`-> PlanRecord`、`-> "PlanRecord"`、`-> records.PlanRecord`。
    三种都归一化成裸类名，**不看函数名**——`memory._to_setting → MemorySettingRecord`
    证明按名字配对会漏也会错。

    Args:
        annotation: 返回注解节点（可能为 None）。

    Returns:
        str | None: 类名；无法识别时返回 None。
    """
    if annotation is None:
        return None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.split(".")[-1].strip("'\"[] ")
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        inner = annotation.value
        return _annotation_record_name(inner) if isinstance(inner, ast.Attribute) else None
    return None


def _mapper_kwarg_shapes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    record: str,
) -> list[set[str]]:
    """收集映射器函数体里对 `record(...)` 的每个构造点的关键字集合。

    Args:
        function: 映射器函数节点。
        record: record 类名。

    Returns:
        list[set[str]]: 每个构造点的 kwarg 集合（保持出现顺序）。
    """
    shapes: list[set[str]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        called = target.id if isinstance(target, ast.Name) else None
        if called != record:
            continue
        shapes.append({keyword.arg for keyword in node.keywords if keyword.arg is not None})
    return shapes


def _check_mapper_coverage(
    unit: Unit,
    classes: dict[str, ClassInfo],
    modules: dict[Path, ast.Module],
) -> list[MapperFinding]:
    """R6：`_to_*` 映射器必须覆盖其返回 record 的全部字段。

    为什么需要它：R4 只比对**两侧直接构造点**，一侧走映射器时整条 record 会被
    `[INFO]` 跳过；映射器少写一个字段，两侧都不会报，运行期表现为"某字段恒为默认值"。

    判据（三条，缺都会漏）：

    1. **按返回注解配对**，不按函数名——`_to_setting → MemorySettingRecord` 是反例；
    2. 取该函数体内所有 `record(...)` 构造点的 kwarg **并集**；
    3. **排除 kwarg 为空的零值构造**（`TaskStats()` 这类"全默认"形状与字段集合不相交，
       不排除会把"某字段只在另一处构造点写"误判成已覆盖）。

    已知边界：**并集口径抓不到"某条分支少写字段"**（与 R4 逐形状不同）。这是刻意取舍——
    R6 的目标是"字段级完全失联"，分支级覆盖由 R4 的形状集合负责。

    Args:
        unit: 仓储单元。
        classes: 单元内全部类（用于确认 record 是 dataclass）。
        modules: 文件 → 语法树。

    Returns:
        list[MapperFinding]: 未覆盖字段的发现列表（含豁免项）。
    """
    record_fields = {
        name: names
        for name, names in _dataclass_field_names(modules).items()
        if name in classes and classes[name].is_dataclass
    }
    if not record_fields:
        return []

    targets = set(unit.postgres_files)
    findings: list[MapperFinding] = []
    for path, module in modules.items():
        if path not in targets:
            continue
        for node in module.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            returned = _annotation_record_name(node.returns)
            if returned is None or returned not in record_fields:
                continue
            shapes = _mapper_kwarg_shapes(node, returned)
            if not shapes:
                # 完全没有直接构造点（如 `default_ops_settings` 逐个字段拼默认值、或委托给
                # 另一个 helper）：与 R4 的"单侧无构造点"同口径，不制造噪声。
                continue
            covered: set[str] = set()
            for shape in shapes:
                covered |= shape
            # 零值构造（`X()`）的 kwarg 集为空，对并集无贡献，天然被排除；
            # 但若**全部**构造点都是零值构造，covered 为空 → 全部字段都会被报出来，
            # 这是正确的（该映射器把整个 record 留成默认值，属最严重的字段失联）。
            for name in record_fields[returned]:
                if name in covered:
                    continue
                reason, expire_when = R6_EXEMPTIONS.get((unit.name, returned, name), (None, None))
                findings.append(
                    MapperFinding(
                        unit=unit.name,
                        record=returned,
                        field=name,
                        mapper=node.name,
                        path=path,
                        lineno=node.lineno,
                        exempt=reason,
                        expire_when=expire_when,
                    )
                )
    findings.sort(key=lambda item: (item.unit, item.record, item.field))
    return findings


def scan(repositories_root: Path) -> ScanResult:
    """扫描仓储目录，产出全部判定结果。

    Args:
        repositories_root: 仓储目录。

    Returns:
        ScanResult: 扫描结果。
    """
    result = ScanResult(units=[], violations=[], infos=[], problems=[])
    units, problems = discover_units(repositories_root)
    result.units = units
    result.problems.extend(problems)
    for unit in units:
        classes, modules, load_problems = _load_classes(unit)
        result.problems.extend(load_problems)
        if load_problems:
            continue
        triples, pair_problems = _pair_triples(unit, classes)
        result.problems.extend(pair_problems)
        for protocol, memory, postgres in triples:
            violations, infos = _check_impl_pair(unit, protocol, memory, postgres)
            result.violations.extend(violations)
            result.infos.extend(infos)
        if triples:
            violations, infos = _check_records(unit, classes, modules)
            result.violations.extend(violations)
            result.infos.extend(infos)
            result.r6.extend(_check_mapper_coverage(unit, classes, modules))
    result.violations.sort(key=lambda item: item.key())
    return result


# ===========================================================================
# 基线
# ===========================================================================


def load_baseline(
    path: Path, unit_names: set[str]
) -> tuple[dict[tuple[str, str, str], BaselineEntry], list[str], list[str]]:
    """读取并校验基线文件。

    Args:
        path: 基线路径。
        unit_names: 本轮实测的单元名集合（用于校验"unit 不在实测集"）。

    Returns:
        tuple[dict, list[str], list[str]]: `(基线条目, 格式错误, 提示)`。
    """
    entries: dict[tuple[str, str, str], BaselineEntry] = {}
    errors: list[str] = []
    notices: list[str] = []
    if not path.exists():
        notices.append(
            f"基线文件不存在（{path}），按**空基线**处理：任何违规都判红"
            f"（fail-closed；`--baseline /dev/null` 的 exit 1 就靠这条生效）"
        )
        return entries, errors, notices
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 权限问题极少见
        errors.append(f"基线文件 {path} 读取失败：{exc}")
        return entries, errors, notices

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, tail = line.partition("--")
        if not separator:
            errors.append(
                f"基线第 {lineno} 行：缺少 `--` 分隔符"
                f"（格式 `<rule> <unit> <subject> -- <审计编号> <理由>`）"
            )
            continue
        head_tokens = head.split()
        if len(head_tokens) != 3:
            errors.append(
                f"基线第 {lineno} 行：`--` 前应为 3 列（rule / unit / subject），"
                f"实际 {len(head_tokens)} 列：{head_tokens}"
            )
            continue
        rule, unit, subject = head_tokens
        tail_tokens = tail.split(maxsplit=1)
        if not tail_tokens:
            errors.append(f"基线第 {lineno} 行：`--` 后缺审计编号与理由")
            continue
        audit_id = tail_tokens[0]
        reason = tail_tokens[1].strip() if len(tail_tokens) > 1 else ""
        if rule not in BASELINE_RULES:
            errors.append(
                f"基线第 {lineno} 行：rule `{rule}` 不在白名单 {sorted(BASELINE_RULES)}"
                f"（R5 只报 INFO，永不判红，因此不可登记）"
            )
            continue
        if unit not in unit_names:
            errors.append(
                f"基线第 {lineno} 行：unit `{unit}` 不在本轮实测单元集"
                f"（{sorted(unit_names)}）——拼错的基线条目会静默失效，故直接判错"
            )
            continue
        if not audit_id.startswith(AUDIT_ID_PREFIXES):
            errors.append(
                f"基线第 {lineno} 行：审计编号 `{audit_id}` 必须以 "
                f"{'/'.join(AUDIT_ID_PREFIXES)} 开头"
            )
            continue
        if not reason:
            errors.append(f"基线第 {lineno} 行：缺理由（必须写消除计划，否则基线就是免死金牌）")
            continue
        entry = BaselineEntry(
            rule=rule,
            unit=unit,
            subject=subject,
            audit_id=audit_id,
            reason=reason,
            lineno=lineno,
        )
        if entry.key() in entries:
            errors.append(
                f"基线第 {lineno} 行：与第 {entries[entry.key()].lineno} 行重复"
                f"（{rule} {unit} {subject}）"
            )
            continue
        entries[entry.key()] = entry
    return entries, errors, notices


# ===========================================================================
# 主流程
# ===========================================================================


def _format_unit_summary(units: Sequence[Unit]) -> str:
    """生成单元形态摘要（包形态必须逐一点名，这是拆分进度的机械可读证据）。

    Args:
        units: 单元列表。

    Returns:
        str: 摘要文本。
    """
    packages = [unit.name for unit in units if unit.kind == "package"]
    files = [unit.name for unit in units if unit.kind == "file"]
    return (
        f"单元 {len(units)} 个（包形态 {len(packages)}：{packages or '无'}；"
        f"文件形态 {len(files)}：{files}）"
    )


def _print_unit_table(units: Sequence[Unit]) -> None:
    """打印单元明细表（`--show-units`）。

    Args:
        units: 单元列表。
    """
    for unit in units:
        shim = f" + shim {unit.shim.name}" if unit.shim is not None else ""
        print(
            f"[INFO] 单元 {unit.name}（{unit.kind}）"
            f" 文件 {[item.name for item in unit.files]}{shim}"
        )


def run(
    repositories_root: Path,
    baseline_path: Path,
    min_units: int,
    show_units: bool = False,
    strict_r6: bool = False,
) -> int:
    """执行一次完整门禁。

    Args:
        repositories_root: 仓储目录。
        baseline_path: 基线路径。
        min_units: 可判定单元数下限。
        show_units: 是否打印单元明细。
        strict_r6: 是否把 R6 的 WARN 计入退出码（默认关闭，WARN-only）。

    Returns:
        int: 退出码（0 / 1 / 2）。
    """
    if not repositories_root.exists():
        print(f"[FAIL] 仓储目录不存在：{repositories_root}（fail-closed：目录被移动必须判红）")
        return 2
    if not repositories_root.is_dir():
        print(f"[FAIL] 仓储路径不是目录：{repositories_root}（fail-closed）")
        return 2

    result = scan(repositories_root)
    if result.problems:
        for problem in result.problems:
            print(f"[FAIL] {problem}")
        print(f"[FAIL] 结构性问题 {len(result.problems)} 条 → exit 2")
        return 2
    if show_units:
        _print_unit_table(result.units)
    for info in result.infos:
        print(f"[INFO] {info}")

    # R6：映射器字段覆盖（默认 WARN-only；只有 --strict-r6 才计入退出码）。
    r6_warn = 0
    for finding in result.r6:
        if finding.is_exempt:
            print(
                f"[INFO] {finding.describe()} -- 豁免：{finding.exempt}"
                f"（失效条件：{finding.expire_when}）"
            )
            continue
        r6_warn += 1
        print(
            f"[WARN] {finding.describe()} -- 映射器未写该字段，"
            f"读回时恒为 record 默认值（R6 只报不判红；要判红请用 --strict-r6）"
        )

    if len(result.units) < min_units:
        print(
            f"[FAIL] 可判定单元数 {len(result.units)} < 下限 {min_units}"
            f"（fail-closed：单元数骤降通常意味着目录被移动/重命名，而不是'没违规'）"
        )
        return 2

    unit_names = {unit.name for unit in result.units}
    entries, errors, notices = load_baseline(baseline_path, unit_names)
    for notice in notices:
        print(f"[INFO] {notice}")
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        print(f"[FAIL] 基线格式错误 {len(errors)} 条 → exit 2")
        return 2

    matched: set[tuple[str, str, str]] = set()
    warn = 0
    fail = 0
    for violation in result.violations:
        entry = entries.get(violation.key())
        if entry is None:
            fail += 1
            print(
                f"[FAIL] {violation.rule} {violation.unit} {violation.subject} -- "
                f"{violation.detail}（未登记：请修复，或评审后进基线）"
            )
            continue
        matched.add(violation.key())
        warn += 1
        print(
            f"[WARN] {violation.rule} {violation.unit} {violation.subject} -- "
            f"{entry.audit_id} {entry.reason} | {violation.detail}"
        )

    stale = 0
    for key in sorted(entries):
        if key in matched:
            continue
        entry = entries[key]
        stale += 1
        print(
            f"[FAIL] stale {entry.rule} {entry.unit} {entry.subject} -- "
            f"基线第 {entry.lineno} 行登记的违规本轮已消失，请删掉该行"
            f"（基线只许减不许增）"
        )

    print(f"[INFO] {_format_unit_summary(result.units)}")
    code = 0 if fail == 0 and stale == 0 else 1
    if strict_r6 and r6_warn:
        code = 1
    tag = "[OK]" if code == 0 else "[FAIL]"
    print(
        f"{tag} 仓储双实现一致性：{len(result.units)} 单元 / "
        f"{len(result.violations)} 条判定 / {warn} WARN / {fail} FAIL / {stale} stale / "
        f"R6 WARN {r6_warn} → exit {code}"
    )
    return code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 参数列表（None 表示 sys.argv）。

    Returns:
        argparse.Namespace: 解析结果。
    """
    parser = argparse.ArgumentParser(
        description="仓储双实现一致性门禁（G-38）：内存实现与 PG 实现的公开面/签名/record 构造点必须一致。",
    )
    parser.add_argument(
        "--repositories-root",
        type=Path,
        default=DEFAULT_REPOSITORIES_ROOT,
        help="仓储目录（默认 apps/adapter/app/db/repositories）",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="基线文件（默认 deploy/repository_parity_baseline.txt）",
    )
    parser.add_argument(
        "--min-units",
        type=int,
        default=MIN_UNITS,
        help=f"可判定单元数下限（默认 {MIN_UNITS}；**仅供 --self-test 下调**，CI 不得传）",
    )
    parser.add_argument(
        "--show-units",
        action="store_true",
        help="打印单元明细（形态 / 文件 / 兼容层）",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="在临时目录合成最小仓储样本，逐条证明门禁能红、能白、会 fail-closed",
    )
    parser.add_argument(
        "--strict-r6",
        action="store_true",
        help="把 R6（映射器字段覆盖）的 WARN 计入退出码（默认 WARN-only，不入基线）",
    )
    return parser.parse_args(argv)


# ===========================================================================
# 自检（可红证明）
# ===========================================================================

#: 自检样本里的"三方一致"方法体。
_SAMPLE_GET: Final[str] = """
async def get(self, key: str) -> str | None:
    return None
"""

#: 自检样本里的 `put`。
_SAMPLE_PUT: Final[str] = """
async def put(self, key: str, value: str) -> None:
    return None
"""

#: R4 v2 用例 13：内存侧少写 `value`，同时模块级存在一个**与实现无关的空映射器**
#: `_to_widget`。v1 按映射器名字豁免，这条真实漂移会被洗成 `[INFO] 跳过`、exit 0；
#: v2 不做名字豁免，必须仍判红。
_R4_MAPPER_SAMPLE: Final[str] = (
    "# 自检样例：内存侧少写字段 + 一个与实现无关的空映射器（v1 会误跳过）。\n"
    "from __future__ import annotations\n"
    "\n"
    "from dataclasses import dataclass\n"
    "from typing import Protocol\n"
    "\n"
    "\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class WidgetRecord:\n"
    "    key: str\n"
    "    value: str = \"\"\n"
    "\n"
    "\n"
    "class WidgetStore(Protocol):\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        ...\n"
    "\n"
    "\n"
    "class InMemoryWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key)\n"
    "\n"
    "\n"
    "class PostgresWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n"
    "def _to_widget(row: object) -> None:\n"
    "    del row\n"
    "    return None\n"
)

#: R4 v2 用例 14：PG 侧有**两个**构造点，其中一个少写 `value`（并集仍含该字段）。
#: v1 用并集比对 → 假绿；v2 逐形状对齐 → 必红。
_R4_PARTIAL_SITE_SAMPLE: Final[str] = (
    "# 自检样例：同侧两个构造点，其中一个少写字段（并集仍含该字段）。\n"
    "from __future__ import annotations\n"
    "\n"
    "from dataclasses import dataclass\n"
    "from typing import Protocol\n"
    "\n"
    "\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class WidgetRecord:\n"
    "    key: str\n"
    "    value: str = \"\"\n"
    "\n"
    "\n"
    "class WidgetStore(Protocol):\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        ...\n"
    "\n"
    "\n"
    "class InMemoryWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n"
    "class PostgresWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        cached = WidgetRecord(key=key)\n"
    "        del cached\n"
    "        return WidgetRecord(key=key, value=key)\n"
)


#: R6 自检样例：PG 侧映射器 `_to_widget` 覆盖 `WidgetRecord` 的全部字段。
#: 变体由替换 `_R6_MAPPER_FULL_BODY` 得到，保证「只改映射器」这一处变量。
_R6_MAPPER_FULL_BODY: Final[str] = (
    "def _to_widget(key: str) -> WidgetRecord:\n    return WidgetRecord(key=key, value=key)\n"
)

#: R6 变体 1：映射器少写 `value`（字段失联的最常见形态）。
_R6_MAPPER_PARTIAL_BODY: Final[str] = (
    "def _to_widget(key: str) -> WidgetRecord:\n    return WidgetRecord(key=key)\n"
)

#: R6 变体 2：映射器只做零值构造（最严重的失联，全部字段都会被报）。
_R6_MAPPER_ZERO_BODY: Final[str] = (
    "def _to_widget(key: str) -> WidgetRecord:\n    del key\n    return WidgetRecord()\n"
)

#: R6 自检样例的完整模块（record + 协议 + 双实现 + 映射器）。
_R6_MAPPER_SAMPLE: Final[str] = (
    "# 自检样例：PG 映射器的字段覆盖（R6）。\n"
    "from __future__ import annotations\n"
    "\n"
    "from dataclasses import dataclass\n"
    "from typing import Protocol\n"
    "\n"
    "\n"
    "@dataclass(frozen=True, slots=True)\n"
    "class WidgetRecord:\n"
    "    key: str\n"
    '    value: str = ""\n'
    "\n"
    "\n"
    "class WidgetStore(Protocol):\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        ...\n"
    "\n"
    "\n"
    "class InMemoryWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n"
    "class PostgresWidgetStore:\n"
    "    async def get(self, key: str) -> WidgetRecord | None:\n"
    "        return WidgetRecord(key=key, value=key)\n"
    "\n"
    "\n" + _R6_MAPPER_FULL_BODY
)


def _write_module(path: Path, body: str) -> None:
    """写一个自检样例模块（自动去公共缩进）。

    Args:
        path: 目标路径。
        body: 源码文本。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).strip("\n") + "\n", encoding="utf-8")


def _copy_module(source: Path, target: Path) -> None:
    """把已写好的自检样例原样复制到另一处（避免二次 dedent 改坏样例）。

    Args:
        source: 源文件。
        target: 目标文件。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _repo_source(protocol_body: str, memory_body: str, postgres_body: str) -> str:
    """拼一个"文件形态"的自检仓储模块。

    Args:
        protocol_body: 协议方法体。
        memory_body: 内存实现方法体。
        postgres_body: PG 实现方法体。

    Returns:
        str: 模块源码。
    """
    return (
        '"""自检样例仓储。"""\n'
        "from __future__ import annotations\n"
        "\n"
        "from typing import Protocol\n"
        "\n"
        "\n"
        "class WidgetStore(Protocol):\n"
        f"{textwrap.indent(textwrap.dedent(protocol_body).strip(), '    ')}\n"
        "\n"
        "\n"
        "class InMemoryWidgetStore:\n"
        f"{textwrap.indent(textwrap.dedent(memory_body).strip(), '    ')}\n"
        "\n"
        "\n"
        "class PostgresWidgetStore:\n"
        f"{textwrap.indent(textwrap.dedent(postgres_body).strip(), '    ')}\n"
    )


def _rules(result: ScanResult) -> list[tuple[str, str, str]]:
    """把扫描结果压成 `(rule, unit, subject)` 列表（自检断言用）。

    Args:
        result: 扫描结果。

    Returns:
        list[tuple[str, str, str]]: 违规键列表。
    """
    return [violation.key() for violation in result.violations]


def _case(name: str, ok: bool, detail: str) -> list[str]:
    """生成一条自检结论。

    Args:
        name: 用例名。
        ok: 是否通过。
        detail: 失败时的说明。

    Returns:
        list[str]: 失败时返回一行 `[FAIL]`，通过时返回空列表。
    """
    if ok:
        return []
    return [f"[FAIL] 自检用例「{name}」不通过：{detail}"]


def _self_test() -> int:  # noqa: C901 - 自检是一个"用例清单"，拆函数只会让清单更难核对
    """在临时目录合成样本，逐条证明门禁会红、会白、会 fail-closed。

    Returns:
        int: 0 表示全部用例通过；2 表示有用例不通过（与 pytest 不同，自检失败即门禁失效）。
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="oa-parity-selftest-") as tmp_name:
        tmp = Path(tmp_name)

        # ---------------- 用例 1：三方一致 → exit 0 ----------------
        clean_root = tmp / "clean"
        _write_module(
            clean_root / "widget_repo.py",
            _repo_source(_SAMPLE_GET + _SAMPLE_PUT, _SAMPLE_GET + _SAMPLE_PUT, _SAMPLE_GET + _SAMPLE_PUT),
        )
        clean_baseline = tmp / "clean-baseline.txt"
        clean_baseline.write_text("", encoding="utf-8")
        code = run(clean_root, clean_baseline, 0)
        failures += _case("三方一致 exit 0", code == 0, f"实际 exit {code}")
        failures += _case(
            "三方一致无违规",
            _rules(scan(clean_root)) == [],
            f"实际违规 {_rules(scan(clean_root))}",
        )

        # ---------------- 用例 2：协议缺方法（内存侧未实现）→ R1 ----------------
        mem_missing_root = tmp / "mem-missing"
        _write_module(
            mem_missing_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT + "\nasync def delete(self, key: str) -> None:\n    return None\n",
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT + "\nasync def delete(self, key: str) -> None:\n    return None\n",
            ),
        )
        mem_missing = scan(mem_missing_root)
        failures += _case(
            "协议缺方法（内存侧）→ R1",
            ("R1", "widget", "delete") in _rules(mem_missing),
            f"实际违规 {_rules(mem_missing)}",
        )
        failures += _case(
            "内存侧缺方法的说明指向内存实现",
            any("内存实现" in violation.detail for violation in mem_missing.violations),
            "detail 未指明是哪一侧缺失",
        )

        # ---------------- 用例 3：协议缺方法（PG 侧未实现）→ R1 ----------------
        pg_missing_root = tmp / "pg-missing"
        _write_module(
            pg_missing_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT + "\nasync def delete(self, key: str) -> None:\n    return None\n",
                _SAMPLE_GET + _SAMPLE_PUT + "\nasync def delete(self, key: str) -> None:\n    return None\n",
                _SAMPLE_GET + _SAMPLE_PUT,
            ),
        )
        pg_missing = scan(pg_missing_root)
        failures += _case(
            "协议缺方法（PG 侧）→ R1",
            ("R1", "widget", "delete") in _rules(pg_missing),
            f"实际违规 {_rules(pg_missing)}",
        )

        # ---------------- 用例 4：PG 多一个公开方法 → R2 ----------------
        pg_extra_root = tmp / "pg-extra"
        _write_module(
            pg_extra_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET
                + _SAMPLE_PUT
                + "\nasync def archive(self, key: str) -> None:\n    return None\n",
            ),
        )
        pg_extra = scan(pg_extra_root)
        failures += _case(
            "PG 多公开方法 → R2",
            ("R2", "widget", "archive") in _rules(pg_extra),
            f"实际违规 {_rules(pg_extra)}",
        )

        # ---------------- 用例 5：内存多 `clear` → 白名单放行 ----------------
        mem_hook_root = tmp / "mem-hook"
        _write_module(
            mem_hook_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT + "\ndef clear(self) -> None:\n    return None\n",
                _SAMPLE_GET + _SAMPLE_PUT,
            ),
        )
        mem_hook = scan(mem_hook_root)
        failures += _case(
            "内存多 clear → 白名单放行（0 违规）",
            _rules(mem_hook) == [],
            f"实际违规 {_rules(mem_hook)}",
        )

        # ---------------- 用例 6：PG 出现 `clear` → R2 ----------------
        pg_hook_root = tmp / "pg-hook"
        _write_module(
            pg_hook_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT + "\ndef clear(self) -> None:\n    return None\n",
            ),
        )
        pg_hook = scan(pg_hook_root)
        failures += _case(
            "PG 出现 clear → R2",
            ("R2", "widget", "clear") in _rules(pg_hook),
            f"实际违规 {_rules(pg_hook)}",
        )

        # ---------------- 用例 7：参数名不同 → R3 ----------------
        rename_root = tmp / "sig-rename"
        _write_module(
            rename_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + "\nasync def put(self, name: str, value: str) -> None:\n    return None\n",
            ),
        )
        rename = scan(rename_root)
        failures += _case(
            "参数名不同 → R3",
            ("R3", "widget", "put") in _rules(rename),
            f"实际违规 {_rules(rename)}",
        )

        # ---------------- 用例 8：默认值不同 → R3 ----------------
        default_root = tmp / "sig-default"
        _write_module(
            default_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + "\nasync def put(self, key: str, value: str = \"\") -> None:\n    return None\n",
                _SAMPLE_GET + "\nasync def put(self, key: str, value: str = \"x\") -> None:\n    return None\n",
            ),
        )
        default_diff = scan(default_root)
        failures += _case(
            "默认值不同 → R3",
            ("R3", "widget", "put") in _rules(default_diff),
            f"实际违规 {_rules(default_diff)}",
        )

        # ---------------- 用例 9：sync / async 不一致 → R3 ----------------
        async_root = tmp / "sig-async"
        _write_module(
            async_root / "widget_repo.py",
            _repo_source(
                _SAMPLE_GET + _SAMPLE_PUT,
                _SAMPLE_GET + _SAMPLE_PUT,
                "def get(self, key: str) -> str | None:\n    return None\n" + _SAMPLE_PUT,
            ),
        )
        async_diff = scan(async_root)
        failures += _case(
            "sync↔async 不一致 → R3",
            ("R3", "widget", "get") in _rules(async_diff),
            f"实际违规 {_rules(async_diff)}",
        )

        # ---------------- 用例 10：基线三态（WARN / FAIL / stale） ----------------
        warn_baseline = tmp / "warn-baseline.txt"
        warn_baseline.write_text(
            "R2 widget archive -- P2-99 自检样例：登记后应降级为 WARN\n", encoding="utf-8"
        )
        code = run(pg_extra_root, warn_baseline, 0)
        failures += _case("基线命中 → WARN 且 exit 0", code == 0, f"实际 exit {code}")

        empty_baseline = tmp / "empty-baseline.txt"
        empty_baseline.write_text("", encoding="utf-8")
        code = run(pg_extra_root, empty_baseline, 0)
        failures += _case("基线外违规 → exit 1", code == 1, f"实际 exit {code}")

        stale_baseline = tmp / "stale-baseline.txt"
        stale_baseline.write_text(
            "R2 widget archive -- P2-99 自检样例：登记后应降级为 WARN\n"
            "R2 widget ghost -- P2-99 自检样例：本条应判 stale\n",
            encoding="utf-8",
        )
        code = run(pg_extra_root, stale_baseline, 0)
        failures += _case("基线条目消失 → stale 且 exit 1", code == 1, f"实际 exit {code}")

        # 缺失基线文件（等价于 `--baseline /dev/null`）：按空基线处理 → 存量违规必红。
        code = run(pg_extra_root, tmp / "no-such-baseline.txt", 0)
        failures += _case(
            "基线文件缺失 → 空基线 → exit 1（不是空转）", code == 1, f"实际 exit {code}"
        )

        # ---------------- 用例 11：基线格式错误（5 态）→ exit 2 ----------------
        format_cases: list[tuple[str, str]] = [
            ("列数不足", "R2 widget -- P1-1 只有两列\n"),
            ("rule 不在白名单", "R9 widget archive -- P1-1 rule 白名单之外\n"),
            ("unit 不在实测集", "R2 widget2 archive -- P1-1 单元名拼错\n"),
            ("审计编号前缀不合规", "R2 widget archive -- AUDIT-1 编号前缀不对\n"),
            (
                "重复行",
                "R2 widget archive -- P1-1 第一行\nR2 widget archive -- P1-1 重复行\n",
            ),
        ]
        for label, content in format_cases:
            bad_baseline = tmp / f"bad-baseline-{abs(hash(label))}.txt"
            bad_baseline.write_text(content, encoding="utf-8")
            code = run(pg_extra_root, bad_baseline, 0)
            failures += _case(f"基线格式错误（{label}）→ exit 2", code == 2, f"实际 exit {code}")

        # ---------------- 用例 12a：fail-closed 3 态 ----------------
        code = run(tmp / "no-such-root", clean_baseline, 0)
        failures += _case("仓储目录不存在 → exit 2", code == 2, f"实际 exit {code}")
        not_a_dir = tmp / "not-a-dir.py"
        not_a_dir.write_text("x = 1\n", encoding="utf-8")
        code = run(not_a_dir, clean_baseline, 0)
        failures += _case("仓储路径是文件 → exit 2", code == 2, f"实际 exit {code}")
        empty_root = tmp / "empty-root"
        empty_root.mkdir()
        code = run(empty_root, clean_baseline, MIN_UNITS)
        failures += _case("单元数低于下限 → exit 2", code == 2, f"实际 exit {code}")

        # ---------------- 用例 12b：包形态召回（role/ 三文件 + 纯 shim = 1 单元） ----------------
        package_root = tmp / "package"
        package_dir = package_root / "widget"
        _write_module(
            package_dir / "records.py",
            """
            '''自检样例 record。'''

            from __future__ import annotations

            from dataclasses import dataclass


            @dataclass(frozen=True, slots=True)
            class WidgetRecord:
                '''自检样例记录。'''

                key: str
                value: str = ""
            """,
        )
        _write_module(
            package_dir / "protocol.py",
            """
            '''自检样例协议。'''

            from __future__ import annotations

            from typing import Protocol

            from app.db.repositories.widget.records import WidgetRecord


            class WidgetStore(Protocol):
                '''自检样例契约。'''

                async def get(self, key: str) -> WidgetRecord | None:
                    '''读。'''
                    ...
            """,
        )
        _write_module(
            package_dir / "memory.py",
            """
            '''自检样例内存实现。'''

            from __future__ import annotations

            from app.db.repositories.widget.records import WidgetRecord


            class InMemoryWidgetStore:
                '''自检样例内存实现。'''

                async def get(self, key: str) -> WidgetRecord | None:
                    '''读。'''
                    del key
                    return WidgetRecord(key="k", value="v")

                def clear(self) -> None:
                    '''测试钩子。'''
                    return None
            """,
        )
        _write_module(
            package_dir / "postgres.py",
            """
            '''自检样例 PG 实现。'''

            from __future__ import annotations

            from app.db.repositories.widget.records import WidgetRecord


            class PostgresWidgetStore:
                '''自检样例 PG 实现。'''

                async def get(self, key: str) -> WidgetRecord | None:
                    '''读。'''
                    del key
                    return WidgetRecord(key="k", value="v")
            """,
        )
        _write_module(
            package_dir / "__init__.py",
            """
            '''自检样例公开出口。'''

            from __future__ import annotations

            from app.db.repositories.widget.memory import InMemoryWidgetStore
            from app.db.repositories.widget.postgres import PostgresWidgetStore
            from app.db.repositories.widget.protocol import WidgetStore
            from app.db.repositories.widget.records import WidgetRecord

            __all__ = ["InMemoryWidgetStore", "PostgresWidgetStore", "WidgetRecord", "WidgetStore"]
            """,
        )
        _write_module(
            package_root / "widget_repo.py",
            """
            '''兼容层：纯 re-export（自检样例）。'''

            from __future__ import annotations

            from app.db.repositories.widget import (
                InMemoryWidgetStore,
                PostgresWidgetStore,
                WidgetRecord,
                WidgetStore,
            )

            __all__ = ["InMemoryWidgetStore", "PostgresWidgetStore", "WidgetRecord", "WidgetStore"]
            """,
        )
        package_scan = scan(package_root)
        failures += _case(
            "包形态召回：文件 + 包并存识别为 1 单元",
            len(package_scan.units) == 1 and package_scan.units[0].kind == "package",
            f"实际 {[(unit.name, unit.kind) for unit in package_scan.units]}",
        )
        failures += _case(
            "包形态判定生效（无违规）",
            package_scan.violations == [] and package_scan.problems == [],
            f"实际违规 {_rules(package_scan)} / 问题 {package_scan.problems}",
        )
        code = run(package_root, clean_baseline, 0)
        failures += _case("包形态 + 纯 shim → exit 0", code == 0, f"实际 exit {code}")

        # 包形态下 R2 依然生效：给 PG 侧加一个内存侧没有的方法。
        drift_root = tmp / "package-drift"
        drift_package = drift_root / "widget"
        for source_file in sorted(package_dir.glob("*.py")):
            _copy_module(source_file, drift_package / source_file.name)
        _copy_module(package_root / "widget_repo.py", drift_root / "widget_repo.py")
        _write_module(
            drift_package / "postgres.py",
            (package_dir / "postgres.py").read_text(encoding="utf-8")
            + "\n    async def archive(self, key: str) -> None:\n        '''多出来的方法。'''\n"
            "        del key\n        return None\n",
        )
        drift_scan = scan(drift_root)
        failures += _case(
            "包形态下 R2 依然生效（PG 多方法）",
            ("R2", "widget", "archive") in _rules(drift_scan),
            f"实际违规 {_rules(drift_scan)}",
        )

        # ---------------- 用例 12c：shim 内含 ClassDef → exit 2 ----------------
        impure_root = tmp / "package-impure"
        impure_package = impure_root / "widget"
        for source_file in sorted(package_dir.glob("*.py")):
            _copy_module(source_file, impure_package / source_file.name)
        _write_module(
            impure_root / "widget_repo.py",
            """
            '''自检样例：会长出第二份实现的壳。'''

            from __future__ import annotations

            from app.db.repositories.widget.protocol import WidgetStore

            __all__ = ["WidgetStore"]


            class LegacyWidgetStore:
                '''模拟"薄壳重新长成第二份实现"。'''
            """,
        )
        code = run(impure_root, clean_baseline, 0)
        failures += _case("shim 内含 ClassDef → exit 2", code == 2, f"实际 exit {code}")

        # ---------------- 用例 13：空 `_to_xxx` 不得洗白 R4（v1 的假绿通道） ----------------
        mapper_root = tmp / "r4-empty-mapper"
        _write_module(mapper_root / "widget_repo.py", _R4_MAPPER_SAMPLE)
        mapper_scan = scan(mapper_root)
        failures += _case(
            "R4 v2：空 `_to_widget` 不再导致跳过（漂移仍判红）",
            ("R4", "widget", "WidgetRecord") in _rules(mapper_scan),
            f"实际违规 {_rules(mapper_scan)} / INFO {mapper_scan.infos}",
        )
        failures += _case(
            "R4 v2：空映射器场景下不得出现该 record 的跳过 INFO",
            not any("WidgetRecord" in info for info in mapper_scan.infos),
            f"实际 INFO {mapper_scan.infos}",
        )
        code = run(mapper_root, clean_baseline, 0)
        failures += _case("R4 v2：空映射器场景 exit 1", code == 1, f"实际 exit {code}")

        # ------- 用例 14：同侧两个构造点其中一处少字段（并集仍含该字段）→ 必红 -------
        partial_root = tmp / "r4-partial-site"
        _write_module(partial_root / "widget_repo.py", _R4_PARTIAL_SITE_SAMPLE)
        partial_scan = scan(partial_root)
        failures += _case(
            "R4 v2：同侧一处少字段（并集仍含该字段）→ 判红",
            ("R4", "widget", "WidgetRecord") in _rules(partial_scan),
            f"实际违规 {_rules(partial_scan)}",
        )
        failures += _case(
            "R4 v2：detail 必须列出那一处少字段的形状",
            any(
                "仅 PG 区的形状" in violation.detail
                for violation in partial_scan.violations
                if violation.rule == "R4"
            ),
            f"实际 detail {[v.detail for v in partial_scan.violations if v.rule == 'R4']}",
        )
        code = run(partial_root, clean_baseline, 0)
        failures += _case("R4 v2：同侧少字段场景 exit 1", code == 1, f"实际 exit {code}")

        # ---------------- 用例 15：R6 映射器全覆盖 → 零发现 ----------------
        r6_full_root = tmp / "r6-full"
        _write_module(r6_full_root / "widget_repo.py", _R6_MAPPER_SAMPLE)
        r6_full = scan(r6_full_root)
        failures += _case(
            "R6：映射器覆盖全部字段 → 零发现",
            r6_full.r6 == [],
            f"实际 {[finding.describe() for finding in r6_full.r6]}",
        )
        code = run(r6_full_root, clean_baseline, 0)
        failures += _case("R6：全覆盖场景 exit 0", code == 0, f"实际 exit {code}")

        # ---------------- 用例 16：映射器少写一个字段 → 必须报（可红） ----------------
        r6_partial_root = tmp / "r6-partial"
        _write_module(
            r6_partial_root / "widget_repo.py",
            _R6_MAPPER_SAMPLE.replace(_R6_MAPPER_FULL_BODY, _R6_MAPPER_PARTIAL_BODY),
        )
        r6_partial = scan(r6_partial_root)
        failures += _case(
            "R6：映射器少写 value → 报出 (WidgetRecord, value)",
            [(item.record, item.field) for item in r6_partial.r6]
            == [("WidgetRecord", "value")],
            f"实际 {[(item.record, item.field) for item in r6_partial.r6]}",
        )
        failures += _case(
            "R6：发现里必须带 mapper 名与行号（可定位）",
            bool(r6_partial.r6)
            and r6_partial.r6[0].mapper == "_to_widget"
            and r6_partial.r6[0].lineno > 0,
            f"实际 {[item.describe() for item in r6_partial.r6]}",
        )
        code = run(r6_partial_root, clean_baseline, 0)
        failures += _case("R6：WARN-only 默认不改变 exit（0）", code == 0, f"实际 exit {code}")
        code = run(r6_partial_root, clean_baseline, 0, strict_r6=True)
        failures += _case("R6：--strict-r6 计入 exit（1）", code == 1, f"实际 exit {code}")

        # ---------------- 用例 17：只做零值构造 → 全部字段都报 ----------------
        r6_zero_root = tmp / "r6-zero"
        _write_module(
            r6_zero_root / "widget_repo.py",
            _R6_MAPPER_SAMPLE.replace(_R6_MAPPER_FULL_BODY, _R6_MAPPER_ZERO_BODY),
        )
        r6_zero = scan(r6_zero_root)
        failures += _case(
            "R6：只做零值构造 → 全部字段都报（不是「无可判定」）",
            [(item.record, item.field) for item in r6_zero.r6]
            == [("WidgetRecord", "key"), ("WidgetRecord", "value")],
            f"实际 {[(item.record, item.field) for item in r6_zero.r6]}",
        )

        # ---------------- 用例 18：命中豁免 → 降级为 INFO（不是消失） ----------------
        R6_EXEMPTIONS[("widget", "WidgetRecord", "value")] = (
            "自检注入的豁免",
            "自检结束立即删除",
        )
        try:
            r6_exempt = scan(r6_partial_root)
        finally:
            R6_EXEMPTIONS.pop(("widget", "WidgetRecord", "value"), None)
        failures += _case(
            "R6：命中豁免 → 发现仍在但标记为豁免",
            len(r6_exempt.r6) == 1 and r6_exempt.r6[0].is_exempt,
            f"实际 {[(item.describe(), item.is_exempt) for item in r6_exempt.r6]}",
        )
        failures += _case(
            "R6：豁免失效后恢复为真 WARN（不是永久后门）",
            bool(r6_partial.r6) and not r6_partial.r6[0].is_exempt,
            f"实际 {[(item.describe(), item.is_exempt) for item in r6_partial.r6]}",
        )

    if failures:
        for failure in failures:
            print(failure)
        print(f"[FAIL] 自检未通过：{len(failures)} 条 → exit 2")
        return 2

    print(
        "[OK] 自检通过：三方一致 exit 0 / 协议缺方法（内存侧·PG 侧）R1 / PG 多公开方法 R2 / "
        "内存多 clear 白名单放行 / PG 出现 clear R2 / 参数名不同 R3 / 默认值不同 R3 / "
        "sync↔async R3 / 基线命中 WARN exit 0 / 基线外 exit 1 / 条目消失 stale exit 1 / "
        "基线缺失=空基线 exit 1 / 基线格式错误 5 态 exit 2 / 目录不存在-非目录-单元数不足 exit 2 / "
        "包形态召回（三文件 + 纯 shim 识别为 1 单元且判定生效）/ shim 内含 ClassDef exit 2 / "
        "R4 v2：空 `_to_xxx` 不洗白（仍红）/ R4 v2：同侧一处少字段（并集含该字段）仍红 / "
        "R6：全覆盖零发现 / R6：少写字段可红（含 mapper 与行号）/ R6：WARN-only 不改 exit、"
        "--strict-r6 计入 exit / R6：只做零值构造时全字段都报 / R6：豁免降级为 INFO 且可恢复为 WARN"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """入口。

    Args:
        argv: 参数列表（None 表示 sys.argv）。

    Returns:
        int: 退出码。
    """
    args = _parse_args(argv)
    if args.self_test:
        return _self_test()
    return run(
        repositories_root=args.repositories_root,
        baseline_path=args.baseline,
        min_units=args.min_units,
        show_units=args.show_units,
        strict_r6=args.strict_r6,
    )


if __name__ == "__main__":
    sys.exit(main())
