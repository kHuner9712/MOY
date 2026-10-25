# ruff: noqa: S603 - 本脚本只在 CI/本地被 `python` 直接执行，不接收用户输入拼命令行。
"""分层依赖方向门禁（审计 P1-3 ~ P1-7 / P2-9~P2-11，架构文档 §9.6 的 G-37）。

编号说明：本门禁原计划登记为 G-36，但 G-36 已被
`deploy/validate_config_names.py` 的「前端构建参数同名 ARG」占用，故取 **G-37**。

## 为什么需要它

`apps/adapter/app` 下的二级包就是**分层**。架构文档 §9.6 把依赖方向写成
一张允许矩阵，但矩阵此前只是文档——没有任何机械校验。于是「顺手 import 一下」
就能把方向反过来，而且**测试全绿**：

- `db/repositories/audit_repo.py` 反向依赖 `business/audit_service.py`
  （审计 P1-4）：仓储层反过来依赖业务层，业务层再改动作常量，仓储就跟着崩；
- `schemas/tenant_data.py` 依赖 `business/tenant_data_service.py`（P1-5）：
  DTO 依赖服务实现，序列化契约被业务实现细节绑住；
- `business/task_service.py` 依赖 `api/v1/plugin_deps.py`（P1-6）：业务层
  依赖 HTTP 装配层，「业务层可独立启动」这条承诺当场失效；
- `core/security.py` 依赖 `business/policy_service.py`（P1-7）：权限放行的
  最小内核反过来依赖业务服务，`core` 不再是可独立复用的底。

这类缺陷不会让任何一条用例变红，只会让「分层」慢慢退化成一句注释。
本脚本把矩阵变成**可执行**的：违规即红，除非在基线里登记了消除计划。

## 只自写 AST，不引 import-linter

理由与 `deploy/validate_pg_markers.py` 一致：CI 里已装的是 ruff/mypy/pytest，
再引一个第三方 lint 框架会引入版本与配置漂移；本脚本零依赖（只用标准库，
含 `--self-test` 可红证明），与其余 `deploy/validate_*.py` 同风格。

## 规则

层 = `app/` 的二级包（12 个）：`api` / `services` / `business` / `db` / `core` /
`schemas` / `plugins` / `registry` / `tools` / `clients` / `middleware` / `workers`。
同层自引用恒允许。允许矩阵见 `ALLOWED`，**违规 = 矩阵的补集**，
rule_id 形如 `R-<源层缩写>-<目标层缩写>`（如 `R-DB-BIZ` = `db → business`）。

## 三种导入形态（一次 `ast.parse`，三分类）

1. `module`：模块级导入，含被 `if` / `try` / `with` 包裹的（逐层下钻，
   但**不**下钻函数体与类体）；
2. `deferred`：函数体内（含类体内）的延迟导入——运行期才生效，
   但方向错了照样是错，只是审计时可按「延迟」等级单列；
3. `type_checking`：`if TYPE_CHECKING:` 内——只参与 `--show-edges` 统计，
   **永不判红**（它不产生运行时依赖，写进基线只会腐坏基线）。

## 归一化细节

- 相对导入按 `node.level` 回退拼前缀（`from ..business import x` 在
  `app/db/**` 里解析为 `app.business`）；
- `from app.core import config` 与 `from app.core.config import X` 的**层**
  都按 `core` 判定；基线里保留**完整 target 名**（前者记 `app.core`，
  后者记 `app.core.config`），做到「一行对应一处 import」；
- `import a, b` 逐别名各产一条边；
- `__init__.py` 去尾段（`app/db/__init__.py` 的模块名是 `app.db`）。

## 基线（`--baseline`）

格式：`<rule_id> <file> <imported_module> <mode> -- <审计编号> <理由>`

- 基线内的违规 → `[WARN]`，不阻断（这是「已登记、有消除计划」的口径）；
- 基线外的违规 → `[FAIL]`，exit 1；
- 基线条目在本轮扫描里已消失 → `[FAIL] stale`，exit 1（**防腐烂**：
  修完了必须把基线行删掉，否则基线会慢慢变成一张免死金牌）；
- 基线格式错误（列数不足 / 缺审计编号 / rule_id 与实测不符 / 重复行）→ exit 2；
- **不提供 `--update-baseline`**：基线只许减不许增，新增豁免必须走评审。

## 退出码

- 0：无违规，或全部违规都已在基线登记；
- 1：存在基线外违规，或基线条目已 stale；
- 2：矩阵未完整登记（12×12 有缺口）/ 出现未登记顶层包 / 基线格式错误 / 自检失败。

## 已知盲区：动态导入

`importlib.import_module("app.business.x")` 与 `__import__("app...")` 是**运行期
字符串**，AST 看不到 `Import` / `ImportFrom` 节点，本门禁的判定**覆盖不到**。
兜底：只在这类文件里做文本扫描，一旦出现 `"app.<层>.` 形态的字符串常量就打
`[WARN]`（**不判红**），提示「此处可能有矩阵外的方向依赖，需人工确认」。
真正收口要靠 code review + 尽量不用动态导入跨层。

## fail-open 护栏（`MIN_SCANNED_FILES`）

门禁最大的失效方式不是判错，而是**什么都没判就变绿**：`--app-root` 指错、
目录被改名/移动、checkout 不完整时，`rglob("*.py")` 返回空集，脚本会打印
「扫描 0 个文件 / 违规 0 条」并 `[OK] exit 0`——恰恰在最该红的那一刻放行。
因此扫描前先校验根目录：不存在 / 不是目录 / `.py` 数低于 `MIN_SCANNED_FILES`
（默认 100，实测 152）→ `[FAIL]` + **exit 2**。

## 自检（门禁可红证明）

`python deploy/validate_import_directions.py --self-test`
——在临时目录合成最小样本树，覆盖：模块级检出 / 延迟导入检出（含函数内嵌套）/
`for`-`while`-`match` 分支体内检出（形态必须是 `module`）/ `services→business` 放行 /
相对导入检出 / `TYPE_CHECKING` 不判红 / 基线命中 WARN 且 exit 0 / 基线外违规 exit 1 /
stale exit 1 / 格式错误 exit 2 / `rule_id` 与实测不符 exit 2 /
**app-root 不存在-非目录-空目录-文件数低于下限 exit 2**。
"""

from __future__ import annotations

import argparse
import ast
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: 层清单（`app/` 的二级包，顺序即 `--show-edges` 的列序）
LAYERS: Final[tuple[str, ...]] = (
    "api",
    "services",
    "business",
    "db",
    "core",
    "schemas",
    "plugins",
    "registry",
    "tools",
    "clients",
    "middleware",
    "workers",
)

#: 层 → rule_id 用的缩写（`R-DB-BIZ` 里的 `DB` / `BIZ`）
LAYER_ABBREV: Final[dict[str, str]] = {
    "api": "API",
    "services": "SVC",
    "business": "BIZ",
    "db": "DB",
    "core": "CORE",
    "schemas": "SCH",
    "plugins": "PLG",
    "registry": "REG",
    "tools": "TLS",
    "clients": "CLI",
    "middleware": "MW",
    "workers": "WRK",
}

#: 允许矩阵：键 = 源层，值 = **允许**依赖的目标层（同层自引用恒允许，不在此列）
ALLOWED: Final[dict[str, frozenset[str]]] = {
    "api": frozenset({"services", "business", "schemas", "core", "plugins", "registry"}),
    "services": frozenset(
        {"business", "clients", "core", "schemas", "plugins", "registry", "tools"}
    ),
    "business": frozenset({"db", "core", "schemas", "plugins"}),
    "db": frozenset({"core"}),
    "schemas": frozenset({"core"}),
    "plugins": frozenset({"core", "registry"}),
    "registry": frozenset({"core"}),
    "core": frozenset(),
    "clients": frozenset({"core"}),
    "middleware": frozenset({"core", "schemas"}),
    "tools": frozenset({"core"}),
    "workers": frozenset({"business", "db", "core"}),
}

#: 导入形态
MODE_MODULE: Final[str] = "module"
MODE_DEFERRED: Final[str] = "deferred"
MODE_TYPE_CHECKING: Final[str] = "type_checking"

#: 参与判定/基线的形态（`type_checking` 永不判红）
JUDGED_MODES: Final[frozenset[str]] = frozenset({MODE_MODULE, MODE_DEFERRED})

#: 仓库根（deploy/ 的上一级）
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 默认被扫描的 `app` 包根目录
DEFAULT_APP_ROOT: Final[Path] = REPO_ROOT / "apps" / "adapter" / "app"

#: 默认基线文件
DEFAULT_BASELINE: Final[Path] = Path(__file__).resolve().parent / "import_direction_baseline.txt"

#: 扫描文件数下限（**fail-open 护栏**）。
#:
#: 门禁最大的失效方式不是判错，而是**什么都没判就变绿**：`--app-root` 指错、
#: 目录被改名/移动、或 checkout 不完整时，`rglob("*.py")` 返回空集，脚本会
#: 打印「扫描 0 个文件」「违规 0 条」并 `[OK] exit 0` —— 恰恰在最该拦的那一刻
#: 放行。当前仓库 `apps/adapter/app` 实测 152 个 `.py`，取下限 100（约 2/3），
#: 既能拦住"扫了 0 个/扫了个空壳目录"，也不会因为正常的文件增删而误红。
#:
#: 仅 `--self-test` 的样本树会显式传 `--min-files 5`（样本只有 5 个文件）；
#: **CI 不得传该参数**，否则这条护栏等于没装。
MIN_SCANNED_FILES: Final[int] = 100

#: 动态导入探测：这些调用说明本文件可能在运行期拼模块名
DYNAMIC_IMPORT_MARKERS: Final[tuple[str, ...]] = ("importlib.import_module(", "__import__(")


class MatrixError(RuntimeError):
    """允许矩阵未完整登记（12×12 有缺口）。"""


class UnregisteredPackageError(RuntimeError):
    """出现未登记的 `app/` 顶层包（新增分层必须先进矩阵）。"""


class BaselineFormatError(RuntimeError):
    """基线文件格式错误。"""


class ScanRootError(RuntimeError):
    """扫描根目录不可用（不存在 / 不是目录 / 文件数低于下限）。"""


@dataclass(frozen=True, slots=True)
class Violation:
    """一条方向违规（同一 `(file, imported_module, mode)` 只记一条）。

    Attributes:
        rule_id: 形如 `R-DB-BIZ`。
        file: 相对仓库根的文件路径（posix 分隔）。
        imported_module: 被导入模块的完整名（`app.business.audit_service`）。
        mode: `module` / `deferred` / `type_checking`。
        source_layer: 源层。
        target_layer: 目标层。
        lines: 命中行号（去重升序）。
    """

    rule_id: str
    file: str
    imported_module: str
    mode: str
    source_layer: str
    target_layer: str
    lines: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """一条基线豁免。

    Attributes:
        rule_id: 形如 `R-DB-BIZ`。
        file: 相对仓库根的文件路径。
        imported_module: 被导入模块的完整名。
        mode: 导入形态。
        audit_id: 审计编号（如 `P1-4`）。
        reason: 消除计划/理由。
        lineno: 该条目在基线文件里的行号（报错定位用）。
    """

    rule_id: str
    file: str
    imported_module: str
    mode: str
    audit_id: str
    reason: str
    lineno: int

    @property
    def key(self) -> tuple[str, str, str]:
        """基线匹配键（不含 rule_id：rule_id 由实测推导后校验一致性）。

        Returns:
            tuple[str, str, str]: `(file, imported_module, mode)`。
        """
        return (self.file, self.imported_module, self.mode)


@dataclass
class ScanResult:
    """一次扫描的结构化结果。

    Attributes:
        violations: 判红口径的违规（`module` + `deferred`），键为 `(file, module, mode)`。
        type_checking_edges: 仅统计的 `TYPE_CHECKING` 边（`(rule_id, file, module)`）。
        edges: 12×12 实边计数，键为 `(源层, 目标层)`，值为去重后的边数。
        dynamic_imports: 动态导入盲区提示（`(file, lineno, 摘要)`）。
        scanned_files: 实际扫描的文件数。
    """

    violations: dict[tuple[str, str, str], Violation] = field(default_factory=dict)
    type_checking_edges: list[tuple[str, str, str]] = field(default_factory=list)
    edges: dict[tuple[str, str], int] = field(default_factory=dict)
    dynamic_imports: list[tuple[str, int, str]] = field(default_factory=list)
    scanned_files: int = 0


def rule_id(source_layer: str, target_layer: str) -> str:
    """拼出违规编号。

    Args:
        source_layer: 源层。
        target_layer: 目标层。

    Returns:
        str: 形如 `R-DB-BIZ`。
    """
    return f"R-{LAYER_ABBREV[source_layer]}-{LAYER_ABBREV[target_layer]}"


def build_decision_matrix() -> dict[tuple[str, str], bool]:
    """物化 12×12 的完整判定矩阵（启动期自检）。

    允许矩阵用「白名单」表达，补集即违规。但白名单表达**必须**证明自己覆盖了
    全部 144 对：漏登记一个层、或矩阵里拼错一个层名，都会让对应方向静默放行。
    因此这里显式物化 144 条判定，任何缺口直接抛错（调用方转成 exit 2）。

    Returns:
        dict[tuple[str, str], bool]: `(源层, 目标层) -> 是否允许`，恰好 144 条。

    Raises:
        MatrixError: 矩阵未完整登记（缺层 / 层名拼错 / 对数不足 144）。
    """
    missing_rows = [layer for layer in LAYERS if layer not in ALLOWED]
    if missing_rows:
        raise MatrixError(f"允许矩阵缺少源层：{missing_rows}")
    unknown_targets = sorted(
        {target for targets in ALLOWED.values() for target in targets} - set(LAYERS)
    )
    if unknown_targets:
        raise MatrixError(f"允许矩阵出现未登记的层名：{unknown_targets}")
    unknown_rows = sorted(set(ALLOWED) - set(LAYERS))
    if unknown_rows:
        raise MatrixError(f"允许矩阵出现未登记的源层：{unknown_rows}")

    decisions: dict[tuple[str, str], bool] = {}
    for source_layer in LAYERS:
        for target_layer in LAYERS:
            # 同层自引用恒允许；其余按白名单，补集即违规。
            decisions[(source_layer, target_layer)] = (
                source_layer == target_layer or target_layer in ALLOWED[source_layer]
            )
    expected = len(LAYERS) * len(LAYERS)
    if len(decisions) != expected:
        raise MatrixError(f"判定矩阵不完整：{len(decisions)} / {expected}")
    return decisions


def _module_name(path: Path, app_root: Path) -> tuple[str, bool]:
    """把文件路径换算成点分模块名。

    Args:
        path: `.py` 文件路径。
        app_root: `app` 包根目录。

    Returns:
        tuple[str, bool]: `(模块名, 是否包内 __init__)`。
    """
    relative = path.relative_to(app_root)
    parts = list(relative.with_suffix("").parts)
    is_package = bool(parts) and parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(["app", *parts]) if parts else "app", is_package


def _source_layer(path: Path, app_root: Path) -> str | None:
    """取源文件所属层（`app/` 根下的 `main.py`、`__init__.py` 不属于任何层）。

    Args:
        path: `.py` 文件路径。
        app_root: `app` 包根目录。

    Returns:
        str | None: 层名；不属于任何层返回 None。

    Raises:
        UnregisteredPackageError: 文件位于未登记的顶层包内。
    """
    parts = path.relative_to(app_root).parts
    if len(parts) < 2:
        return None
    top = parts[0]
    if top in LAYERS:
        return top
    if (app_root / top).is_dir():
        raise UnregisteredPackageError(f"{top} 未在 LAYERS 登记（新增分层必须先进矩阵）")
    return None


def _target_layer(module: str, app_root: Path) -> str | None:
    """取被导入模块所属层。

    Args:
        module: 被导入模块的完整名（`app.business.audit_service`）。
        app_root: `app` 包根目录。

    Returns:
        str | None: 层名；`app` 根模块（如 `app.main`）返回 None。

    Raises:
        UnregisteredPackageError: 目标是未登记的顶层包。
    """
    segments = module.split(".")
    if len(segments) < 2 or segments[0] != "app":
        return None
    top = segments[1]
    if top in LAYERS:
        return top
    if (app_root / top).is_dir():
        raise UnregisteredPackageError(f"{module} 指向未登记的顶层包 {top}")
    return None


def _is_type_checking_test(node: ast.expr) -> bool:
    """判断 `if` 条件是否为 `TYPE_CHECKING` / `typing.TYPE_CHECKING`。

    Args:
        node: `If` 的测试表达式。

    Returns:
        bool: 是 TYPE_CHECKING 守卫返回 True。
    """
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _walk_imports(
    node: ast.AST, mode: str, found: list[tuple[ast.Import | ast.ImportFrom, str]]
) -> None:
    """递归收集导入并按三分类打标（**通用遍历，不对任何语句类型做白名单**）。

    形态规则（固定，且与语句类型无关）：

    1. `FunctionDef` / `AsyncFunctionDef` 体内的一切导入 → `deferred`
       （含多层嵌套函数；运行期调用才生效，但方向错了照样是错）；
    2. `if TYPE_CHECKING:` 体内 → `type_checking`（只统计、**永不判红**；
       它不产生运行时依赖，写进基线只会腐坏基线）。该守卫嵌在函数内时同样优先
       于规则 1（TYPE_CHECKING 的语义与嵌套位置无关）；
    3. **其余一切位置 → 当前形态**：模块级语句、`class` 体、`if` / `try` /
       `except` / `except*` / `with` / `for` / `while` / `match` 的体内。

    为什么不用「下钻 If/Try/With」的白名单式递归（第一版就是这么写的，QA 实测
    漏检）：白名单一旦漏掉 `for` / `while` / `match` / `except*`，那些体内
    藏着的 `from app.business import ...` 就**完全不进判定**，门禁变成"看起来
    在守、实际有洞"。改成 `ast.iter_child_nodes` 通用递归后，新增语句类型
    （未来还有 `try*` 之类）自动被覆盖，**没有白名单可漏**。

    Args:
        node: 当前节点。
        mode: 当前形态（`module` / `deferred` / `type_checking`）。
        found: 收集结果（就地追加）。
    """
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        found.append((node, mode))
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for child in ast.iter_child_nodes(node):
            _walk_imports(child, MODE_DEFERRED, found)
        return
    if isinstance(node, ast.If) and _is_type_checking_test(node.test):
        for statement in node.body:
            _walk_imports(statement, MODE_TYPE_CHECKING, found)
        for statement in node.orelse:
            _walk_imports(statement, mode, found)
        return
    for child in ast.iter_child_nodes(node):
        _walk_imports(child, mode, found)


def _iter_nested_imports(tree: ast.Module) -> list[tuple[ast.Import | ast.ImportFrom, str]]:
    """收集模块内的全部导入并按三分类打标。

    Args:
        tree: 模块 AST。

    Returns:
        list[tuple[ast.Import | ast.ImportFrom, str]]: `(节点, 形态)` 列表。
    """
    found: list[tuple[ast.Import | ast.ImportFrom, str]] = []
    for statement in tree.body:
        _walk_imports(statement, MODE_MODULE, found)
    return found


def _resolve_import_from(
    module_name: str, is_package: bool, node: ast.ImportFrom
) -> list[str]:
    """解析 `from ... import ...` 的目标模块名。

    Args:
        module_name: 源文件模块名（`app.db.repositories.audit_repo`）。
        is_package: 源文件是否为包内 `__init__`。
        node: `ImportFrom` 节点。

    Returns:
        list[str]: 目标模块完整名列表（`from app import x` 会按别名展开）。
    """
    if node.level:
        parts = module_name.split(".")
        package_parts = parts if is_package else parts[:-1]
        keep = len(package_parts) - (node.level - 1)
        base_parts = package_parts[:keep] if keep > 0 else []
        if node.module:
            base_parts = [*base_parts, *node.module.split(".")]
        base = ".".join(base_parts)
    else:
        base = node.module or ""

    if not base:
        return []
    if base == "app":
        # `from app import business` → 逐别名展开成 `app.business`
        return [f"app.{alias.name}" for alias in node.names if alias.name != "*"]
    return [base]


def _dynamic_import_warnings(path: Path, text: str, shown: str) -> list[tuple[str, int, str]]:
    """在「可能动态导入」的文件里做文本兜底扫描。

    Args:
        path: 文件路径（仅用于读判定以外的信息）。
        text: 源码文本。
        shown: 展示用相对路径。

    Returns:
        list[tuple[str, int, str]]: `(文件, 行号, 摘要)` 列表。
    """
    del path
    if not any(marker in text for marker in DYNAMIC_IMPORT_MARKERS):
        return []
    warnings: list[tuple[str, int, str]] = []
    prefixes = tuple(f"app.{layer}." for layer in LAYERS)
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not any(f'"{prefix}' in line or f"'{prefix}" in line for prefix in prefixes):
            continue
        warnings.append((shown, lineno, "出现层前缀字符串常量，可能有矩阵外的动态导入方向"))
    return warnings


def effective_repo_root(app_root: Path) -> Path:
    """推断基线用的相对路径基准目录。

    Args:
        app_root: `app` 包根目录。

    Returns:
        Path: `app_root` 在仓库内时返回仓库根，否则返回 `app_root` 的父目录
        （自检临时目录走这条分支，基线里仍写成 `app/...`）。
    """
    resolved = app_root.resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved.parent
    return REPO_ROOT


def assert_app_root(app_root: Path, min_files: int = MIN_SCANNED_FILES) -> int:
    """校验被扫描的 `app` 根目录合理（存在、是目录、文件数不低于下限）。

    这是**fail-open 护栏**：没有它，`--app-root` 写错就得到「扫描 0 个文件 / 违规
    0 条 / [OK] exit 0」——门禁在最该红的场景下变绿，而且没人会注意到。

    Args:
        app_root: `app` 包根目录。
        min_files: `.py` 文件数下限（CI 用默认值；`--self-test` 因样本树很小会显式下调）。

    Returns:
        int: 实际可见的 `.py` 文件数。

    Raises:
        ScanRootError: 目录不存在 / 不是目录 / 文件数低于下限。
    """
    if not app_root.exists():
        raise ScanRootError(f"app-root 不存在：{app_root}")
    if not app_root.is_dir():
        raise ScanRootError(f"app-root 不是目录：{app_root}")
    count = sum(
        1 for path in app_root.rglob("*.py") if "__pycache__" not in path.parts
    )
    if count < min_files:
        raise ScanRootError(
            f"扫描文件数异常（{count}，下限 {min_files}），疑似 app-root 错误或目录被移动"
        )
    return count


def scan(app_root: Path, *, repo_root: Path = REPO_ROOT) -> ScanResult:
    """扫描 `app` 包，产出违规与实边统计。

    Args:
        app_root: `app` 包根目录。
        repo_root: 仓库根（用于生成基线的相对路径）。

    Returns:
        ScanResult: 扫描结果。

    Raises:
        UnregisteredPackageError: 出现未登记顶层包。
        MatrixError: 允许矩阵未完整登记。
    """
    decisions = build_decision_matrix()
    result = ScanResult()

    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source_layer = _source_layer(path, app_root)
        if source_layer is None:
            continue
        module_name, is_package = _module_name(path, app_root)
        try:
            shown = path.relative_to(repo_root).as_posix()
        except ValueError:  # pragma: no cover - 仅自检临时目录会走这里
            shown = path.as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            print(f"[FAIL] 无法解析 {shown}: {exc}")
            raise
        result.scanned_files += 1
        result.dynamic_imports.extend(_dynamic_import_warnings(path, text, shown))

        for node, mode in _iter_nested_imports(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                targets = _resolve_import_from(module_name, is_package, node)
            for target in targets:
                if not target.startswith("app"):
                    continue
                target_layer = _target_layer(target, app_root)
                if target_layer is None:
                    continue
                key_pair = (source_layer, target_layer)
                result.edges[key_pair] = result.edges.get(key_pair, 0) + 1
                if mode == MODE_TYPE_CHECKING:
                    result.type_checking_edges.append(
                        (rule_id(source_layer, target_layer), shown, target)
                    )
                    continue
                if decisions[(source_layer, target_layer)]:
                    continue
                dedupe_key = (shown, target, mode)
                existing = result.violations.get(dedupe_key)
                if existing is None:
                    result.violations[dedupe_key] = Violation(
                        rule_id=rule_id(source_layer, target_layer),
                        file=shown,
                        imported_module=target,
                        mode=mode,
                        source_layer=source_layer,
                        target_layer=target_layer,
                        lines=(node.lineno,),
                    )
                else:
                    merged = tuple(sorted({*existing.lines, node.lineno}))
                    result.violations[dedupe_key] = Violation(
                        rule_id=existing.rule_id,
                        file=existing.file,
                        imported_module=existing.imported_module,
                        mode=existing.mode,
                        source_layer=existing.source_layer,
                        target_layer=existing.target_layer,
                        lines=merged,
                    )
    return result


def load_baseline(path: Path) -> list[BaselineEntry]:
    """读取并校验基线文件。

    Args:
        path: 基线文件路径。

    Returns:
        list[BaselineEntry]: 基线条目（按文件行序）。

    Raises:
        BaselineFormatError: 格式错误（列数不足 / 缺审计编号 / 未知形态 / 重复行）。
    """
    entries: list[BaselineEntry] = []
    seen: dict[tuple[str, str, str], int] = {}
    text = path.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, tail = line.partition("--")
        if not separator:
            raise BaselineFormatError(
                f"{path.name}:{lineno} 缺少 `--` 审计编号段：{raw.strip()}"
            )
        head_parts = head.split()
        tail_parts = tail.split(maxsplit=1)
        if len(head_parts) < 4:
            raise BaselineFormatError(
                f"{path.name}:{lineno} 列数不足（需 rule_id file module mode）：{raw.strip()}"
            )
        if not tail_parts:
            raise BaselineFormatError(f"{path.name}:{lineno} 缺少审计编号：{raw.strip()}")
        rule, file_name, imported_module, mode = head_parts[:4]
        audit_id = tail_parts[0]
        reason = tail_parts[1].strip() if len(tail_parts) > 1 else ""
        if mode not in {MODE_MODULE, MODE_DEFERRED, MODE_TYPE_CHECKING}:
            raise BaselineFormatError(f"{path.name}:{lineno} 未知导入形态 `{mode}`")
        if not audit_id.startswith(("P1-", "P2-", "P0-")):
            raise BaselineFormatError(
                f"{path.name}:{lineno} 审计编号不合规 `{audit_id}`（应形如 P1-4）"
            )
        entry = BaselineEntry(
            rule_id=rule,
            file=file_name,
            imported_module=imported_module,
            mode=mode,
            audit_id=audit_id,
            reason=reason,
            lineno=lineno,
        )
        if entry.key in seen:
            raise BaselineFormatError(
                f"{path.name}:{lineno} 与第 {seen[entry.key]} 行重复（同一 file+module+mode）"
            )
        seen[entry.key] = lineno
        entries.append(entry)
    return entries


def evaluate(
    result: ScanResult, baseline: list[BaselineEntry]
) -> tuple[list[tuple[BaselineEntry, Violation]], list[Violation], list[BaselineEntry]]:
    """把扫描结果与基线对齐。

    Args:
        result: 扫描结果。
        baseline: 基线条目。

    Returns:
        tuple: `(基线内 WARN, 基线外 FAIL, 已消失 stale)`。
    """
    by_key = {entry.key: entry for entry in baseline}
    warnings: list[tuple[BaselineEntry, Violation]] = []
    failures: list[Violation] = []
    matched: set[tuple[str, str, str]] = set()
    for key, violation in sorted(result.violations.items()):
        entry = by_key.get(key)
        if entry is None:
            failures.append(violation)
            continue
        if entry.rule_id != violation.rule_id:
            raise BaselineFormatError(
                f"{entry.file}:{entry.lineno} rule_id 与实测不符"
                f"（基线 {entry.rule_id} / 实测 {violation.rule_id}）"
            )
        matched.add(key)
        warnings.append((entry, violation))
    stales = [entry for entry in baseline if entry.key not in matched]
    return warnings, failures, stales


def print_edges(result: ScanResult) -> None:
    """打印 12×12 实边计数矩阵（不参与判定）。

    Args:
        result: 扫描结果。
    """
    width = 6
    header = "源层\\目标层".ljust(12) + "".join(layer[:5].rjust(width) for layer in LAYERS)
    print(header)
    print("-" * len(header))
    for source_layer in LAYERS:
        cells = []
        for target_layer in LAYERS:
            count = result.edges.get((source_layer, target_layer), 0)
            cells.append((str(count) if count else ".").rjust(width))
        print(source_layer.ljust(12) + "".join(cells))
    print()
    print(f"扫描文件 {result.scanned_files} 个；TYPE_CHECKING 边 {len(result.type_checking_edges)} 条（只统计不判红）")


def _format_violation(violation: Violation) -> str:
    """格式化一条违规用于打印。

    Args:
        violation: 违规记录。

    Returns:
        str: 单行文本。
    """
    lines = ",".join(str(item) for item in violation.lines)
    return (
        f"{violation.rule_id} {violation.file}:{lines} "
        f"{violation.source_layer} -> {violation.target_layer} "
        f"`{violation.imported_module}` ({violation.mode})"
    )


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    Args:
        argv: 命令行参数（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 0 通过 / 1 基线外违规或 stale / 2 环境或自检失败。
    """
    parser = argparse.ArgumentParser(description="校验 app/ 分层依赖方向（G-37）")
    parser.add_argument("--app-root", type=Path, default=DEFAULT_APP_ROOT)
    parser.add_argument("--baseline", type=Path, default=None, help="基线豁免文件")
    parser.add_argument("--show-edges", action="store_true", help="打印 12×12 实边计数矩阵")
    parser.add_argument(
        "--min-files",
        type=int,
        default=MIN_SCANNED_FILES,
        help=(
            "扫描文件数下限（默认 100）。**仅供 --self-test 的样本树下调；"
            "CI 与其他调用一律用默认值**，否则 fail-open 护栏等于没装"
        ),
    )
    parser.add_argument("--self-test", action="store_true", help="门禁可红证明")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        assert_app_root(args.app_root, args.min_files)
    except ScanRootError as exc:
        print(f"[FAIL] {exc}")
        return 2

    try:
        result = scan(args.app_root, repo_root=effective_repo_root(args.app_root))
    except MatrixError as exc:
        print(f"[FAIL] 允许矩阵未完整登记：{exc}")
        return 2
    except UnregisteredPackageError as exc:
        print(f"[FAIL] {exc}")
        return 2
    except SyntaxError:
        return 2

    if args.show_edges:
        print_edges(result)
        print()

    baseline: list[BaselineEntry] = []
    baseline_path = args.baseline
    if baseline_path is not None:
        if not baseline_path.is_file():
            print(f"[FAIL] 基线文件不存在：{baseline_path}")
            return 2
        try:
            baseline = load_baseline(baseline_path)
        except BaselineFormatError as exc:
            print(f"[FAIL] 基线格式错误：{exc}")
            return 2

    try:
        warnings, failures, stales = evaluate(result, baseline)
    except BaselineFormatError as exc:
        print(f"[FAIL] 基线格式错误：{exc}")
        return 2

    for entry, violation in warnings:
        print(f"[WARN] {_format_violation(violation)} -- {entry.audit_id} {entry.reason}".rstrip())
    for violation in failures:
        print(f"[FAIL] {_format_violation(violation)}（未登记基线：方向违规必须修或登记消除计划）")
    for entry in stales:
        print(
            f"[FAIL] stale {entry.rule_id} {entry.file} `{entry.imported_module}` ({entry.mode})"
            f" -- {entry.audit_id}：该违规已消失，请删掉这条基线（基线只许减不许增）"
        )

    if result.dynamic_imports:
        print(f"[WARN] 动态导入盲区（共 {len(result.dynamic_imports)} 处，不判红，需人工确认）：")
        for shown, lineno, message in result.dynamic_imports[:20]:
            print(f"  - {shown}:{lineno} {message}")

    total = len(result.violations)
    print()
    print(
        f"结论：违规 {total} 条（基线内 {len(warnings)} / 基线外 {len(failures)} / "
        f"stale {len(stales)}），扫描 {result.scanned_files} 个文件"
    )
    if failures or stales:
        return 1
    print("[OK] 分层依赖方向合规")
    return 0


# ===========================================================================
# 自检：门禁必须能红
# ===========================================================================
_SELF_TEST_FILES: Final[dict[str, str]] = {
    # 用例 1：模块级 db → business 必须检出
    "db/probe_module_level.py": """
from __future__ import annotations

from app.business.audit_service import ACTION_KB_CREATE

__all__ = ["ACTION_KB_CREATE"]
""",
    # 用例 2：函数体内延迟导入也必须检出（形态记为 deferred）
    "db/probe_deferred.py": """
from __future__ import annotations


def load():
    from app.business.policy_service import permissions_for_roles

    return permissions_for_roles
""",
    # 用例 3：services → business 属于矩阵内，必须放行
    "services/probe_allowed.py": """
from __future__ import annotations

from app.business.audit_service import AuditService

__all__ = ["AuditService"]
""",
    # 用例 4：相对导入（..business）必须检出
    "db/probe_relative.py": """
from __future__ import annotations

try:
    from ..business import audit_service
except ImportError:  # pragma: no cover
    audit_service = None

__all__ = ["audit_service"]
""",
    # 用例 6：`for` / `while` / `match` / `except*` 体内的导入同样是**模块级**
    # （QA 实测第一版白名单式递归漏检这四类；样本里一处一个，漏一个就红）
    "db/probe_branches.py": """
from __future__ import annotations

for _name in ():
    from app.business.audit_service import ACTION_KB_CREATE

while False:
    from app.business.policy_service import ROLE_OWNER

match 0:
    case 0:
        from app.business.tenant_data_service import EXPORT_SCOPES

try:
    pass
except* ValueError:
    from app.business.order_service import ACTION_ORDER_PAID


def _deferred() -> None:
    for _inner in ():
        from app.business.usage_service import UsageService

    return None
""",
    # 用例 5：TYPE_CHECKING 内只统计，不判红
    "core/probe_type_checking.py": """
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.business.audit_service import AuditEntry

__all__: list[str] = []
""",
}

_SELF_TEST_EXPECTED: Final[set[tuple[str, str, str]]] = {
    ("app/db/probe_module_level.py", "app.business.audit_service", MODE_MODULE),
    ("app/db/probe_deferred.py", "app.business.policy_service", MODE_DEFERRED),
    ("app/db/probe_relative.py", "app.business", MODE_MODULE),
    # 分支体（模块级 for / while / match）→ module
    ("app/db/probe_branches.py", "app.business.audit_service", MODE_MODULE),
    ("app/db/probe_branches.py", "app.business.policy_service", MODE_MODULE),
    ("app/db/probe_branches.py", "app.business.tenant_data_service", MODE_MODULE),
    # `except*`（ExceptStarHandler）也是"其余一切位置"——通用递归自动覆盖
    ("app/db/probe_branches.py", "app.business.order_service", MODE_MODULE),
    # 函数体内的 for → deferred（嵌套也认）
    ("app/db/probe_branches.py", "app.business.usage_service", MODE_DEFERRED),
}

#: `--self-test` 的样本树只有 7 个 `.py`（6 个样本 + `__init__.py`），因此显式下调
#: 文件数下限；真实仓库（152 个文件）用默认值 100，**CI 不得传 `--min-files`**。
_SELF_TEST_MIN_FILES: Final[str] = "7"


def _write_sample_tree(root: Path) -> Path:
    """在临时目录写出最小样本树。

    Args:
        root: 临时目录。

    Returns:
        Path: 样本 `app` 包根目录。
    """
    app_root = root / "app"
    (app_root / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
    (app_root / "__init__.py").write_text("", encoding="utf-8")
    for relative, content in _SELF_TEST_FILES.items():
        target = app_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.lstrip("\n"), encoding="utf-8")
    return app_root


def _self_test() -> int:
    """门禁自检：全部用例符合预期才算通过（含 fail-open 护栏与分支体检出）。

    Returns:
        int: 0 自检通过 / 2 自检失败。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app_root = _write_sample_tree(root)

        # 用例 1/2/4：三类违规必须被检出，形态分类正确
        try:
            result = scan(app_root, repo_root=root)
        except (MatrixError, UnregisteredPackageError, SyntaxError) as exc:  # pragma: no cover
            print(f"[FAIL] 自检扫描异常：{exc}")
            return 2

        found = set(result.violations)
        if found != _SELF_TEST_EXPECTED:
            print(f"[FAIL] 用例 1/2/4：检出结果 {sorted(found)}")
            print(f"       期望 {sorted(_SELF_TEST_EXPECTED)}")
            return 2

        # 用例 3：services → business 放行（不在违规集合里即通过）
        allowed_keys = {key for key in found if key[0].startswith("app/services/")}
        if allowed_keys:
            print(f"[FAIL] 用例 3：services → business 被误判为违规 {sorted(allowed_keys)}")
            return 2

        # 用例 5：TYPE_CHECKING 只统计，不判红
        if any(key[0].startswith("app/core/") for key in found):
            print("[FAIL] 用例 5：TYPE_CHECKING 内的导入被误判为违规")
            return 2
        if not any(item[2] == "app.business.audit_service" for item in result.type_checking_edges):
            print("[FAIL] 用例 5：TYPE_CHECKING 边未被统计")
            return 2

        # 用例 6：基线命中 → WARN 且 exit 0
        # 基线条目由「期望集合」直接生成：样本一变，基线跟着变，不会写漏写错。
        good_baseline = root / "baseline_good.txt"
        good_baseline.write_text(
            "\n".join(
                [
                    "# 自检样本：只许减不许增",
                    *(
                        f"R-DB-BIZ {file} {module} {mode} -- P1-4 自检样本"
                        for file, module, mode in sorted(_SELF_TEST_EXPECTED)
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--app-root",
                str(app_root),
                "--baseline",
                str(good_baseline),
                "--min-files",
                _SELF_TEST_MIN_FILES,
            ]
        )
        if code != 0:
            print(f"[FAIL] 用例 6：基线命中应 exit 0，实际 {code}")
            return 2

        # 用例 7：基线缺一条 → 该条 FAIL，exit 1
        partial_baseline = root / "baseline_partial.txt"
        partial_baseline.write_text(
            "R-DB-BIZ app/db/probe_module_level.py app.business.audit_service "
            "module -- P1-4 自检样本\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--app-root",
                str(app_root),
                "--baseline",
                str(partial_baseline),
                "--min-files",
                _SELF_TEST_MIN_FILES,
            ]
        )
        if code != 1:
            print(f"[FAIL] 用例 7：基线外违规应 exit 1，实际 {code}")
            return 2

        # 用例 7b：基线条目已消失 → stale，exit 1
        stale_baseline = root / "baseline_stale.txt"
        stale_baseline.write_text(
            good_baseline.read_text(encoding="utf-8")
            + "R-DB-BIZ app/db/probe_gone.py app.db.x module -- P1-4 已修复未删\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--app-root",
                str(app_root),
                "--baseline",
                str(stale_baseline),
                "--min-files",
                _SELF_TEST_MIN_FILES,
            ]
        )
        if code != 1:
            print(f"[FAIL] 用例 7b：stale 应 exit 1，实际 {code}")
            return 2

        # 用例 8：格式错误（列数不足）→ exit 2
        broken_baseline = root / "baseline_broken.txt"
        broken_baseline.write_text(
            "R-DB-BIZ app/db/probe_module_level.py -- P1-4 列数不足\n", encoding="utf-8"
        )
        code = main(
            [
                "--app-root",
                str(app_root),
                "--baseline",
                str(broken_baseline),
                "--min-files",
                _SELF_TEST_MIN_FILES,
            ]
        )
        if code != 2:
            print(f"[FAIL] 用例 8：格式错误应 exit 2，实际 {code}")
            return 2

        # 用例 8b：rule_id 与实测不符 → exit 2（防「登记了错的编号」掩盖真实方向）
        wrong_rule_baseline = root / "baseline_wrong_rule.txt"
        wrong_rule_baseline.write_text(
            "R-BIZ-DB app/db/probe_module_level.py app.business.audit_service "
            "module -- P1-4 编号写反\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--app-root",
                str(app_root),
                "--baseline",
                str(wrong_rule_baseline),
                "--min-files",
                _SELF_TEST_MIN_FILES,
            ]
        )
        if code != 2:
            print(f"[FAIL] 用例 8b：rule_id 不符应 exit 2，实际 {code}")
            return 2

        # 用例 9：**fail-open 护栏** —— app-root 不存在 / 不是目录 / 空目录 /
        # 文件数低于默认下限，都必须 exit 2。没有这条护栏时，前三种会打印
        # 「扫描 0 个文件 / 违规 0 条 / [OK] exit 0」，门禁在最该红的场景变绿。
        missing_root = root / "no_such_app"
        code = main(["--app-root", str(missing_root)])
        if code != 2:
            print(f"[FAIL] 用例 9a：app-root 不存在应 exit 2，实际 {code}")
            return 2

        file_as_root = root / "not_a_dir.py"
        file_as_root.write_text("x = 1\n", encoding="utf-8")
        code = main(["--app-root", str(file_as_root)])
        if code != 2:
            print(f"[FAIL] 用例 9b：app-root 是文件应 exit 2，实际 {code}")
            return 2

        empty_root = root / "empty_app"
        empty_root.mkdir()
        code = main(["--app-root", str(empty_root)])
        if code != 2:
            print(f"[FAIL] 用例 9c：空目录应 exit 2，实际 {code}")
            return 2

        # 用例 9d：样本树只有 6 个文件 < 默认下限 100 → 不传 --min-files 必须 exit 2，
        # 证明「文件数下限」真的在起作用（而不是只在测试里被绕过）。
        code = main(["--app-root", str(app_root)])
        if code != 2:
            print(f"[FAIL] 用例 9d：文件数低于默认下限应 exit 2，实际 {code}")
            return 2

    print(
        "[OK] 自检通过：模块级检出 / 延迟检出 / for-while-match 分支体检出（module）/ "
        "函数内嵌套导入检出（deferred）/ services→business 放行 / 相对导入检出 / "
        "TYPE_CHECKING 不判红 / 基线 WARN exit 0 / 基线外 exit 1 / stale exit 1 / "
        "格式错误与 rule_id 不符 exit 2 / app-root 不存在-非目录-空目录-文件数不足 exit 2"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
