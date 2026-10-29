# 本脚本只在 CI/本地被 `python` 直接执行、不接收用户输入拼命令行，故无需 S603 豁免
# （文件首行原有一条 S603 文件级豁免，已删除：它从未命中，属无效指令，会被 RUF100 判红）。
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

格式：`<rule_id> <file> <imported_module> <mode> -- <审计编号> due_batch=<Bn> <理由>`

- 基线内的违规 → `[WARN]`，不阻断（这是「已登记、有消除计划」的口径）；
- 基线外的违规 → `[FAIL]`，exit 1；
- 基线条目在本轮扫描里已消失 → `[FAIL] stale`，exit 1（**防腐烂**：
  修完了必须把基线行删掉，否则基线会慢慢变成一张免死金牌）；
- 基线格式错误（列数不足 / 缺审计编号 / 缺 `due_batch` / rule_id 与实测不符 / 重复行）→ exit 2；
- **不提供 `--update-baseline`**：基线只许减不许增，新增豁免必须走评审。

### `due_batch`（R2-ARC-07：让基线带到期约束）

R2 审计指出：24 条豁免**全部**在基线内，门禁对存量**零约束力**——「登记即永久
免死」。因此每条豁免必须写 `due_batch=B<n>`（与 `apps/adapter/tests/expected_xfail.py`
的 `DEFECT_LEDGER` 同一套批次号，同一套 `OA_BATCH` 时钟，见 `deploy/validate_batch_clock.py`）。

- 到期口径：当前批次 `>=` `due_batch` 即 **exit 1**（到该批次时必须已消除，不是"到期后才开始修"）；
- 缺 `due_batch` 字段 → **exit 2**（不允许"无期限豁免"这条捷径）；
- 当前批次取 `OA_BATCH` 环境变量（与 CI 同源），也可用 `--batch B23` 显式指定；
  两者都没有时打印 `[WARN]` 并**跳过**到期判定（本地随手跑不影响；CI 的 job 级
  `env: OA_BATCH` 恒存在，因此 CI 不会落进这个分支）。

## 文件级循环依赖（SCC 检测，R2-ARC-07）

层矩阵只管**跨层方向**：同层内部（`business ↔ business`、`workers ↔ workers`）
文件之间互相 import 完全在矩阵的盲区里；`core ↔ business` 这类跨层环也会因为
两个方向各自「一个合法、一个已登记」而被拆散到两条记录里，看不出它们其实闭成了环。

因此每轮扫描都把**文件级 import 图**（顶点 = `app/**/*.py`，边 = 解析到的模块文件；
边集 = `module` + `deferred`，`type_checking` 不产生运行时依赖故不计）跑一遍
**Tarjan 强连通分量**：分量内顶点数 `>= 2` 即判定为一个环，环的规范化表示是
**成员路径升序元组**（顺序无关，成员变化即视为不同的环）。

`--cycle-baseline deploy/same_layer_cycle_baseline.txt`（默认即此文件）：

- 格式：`CYCLE <file> <file> [<file>...] -- <审计编号> due_batch=<Bn> <理由>`；
- 扫描到的环不在基线内 → `[FAIL]`，exit 1（**新增环即红**，这是本规则的全部价值）；
- 基线的环在本轮已不存在 → `[FAIL] stale`，exit 1；环内增减成员 → 旧记录 stale + 新环 FAIL；
- 同样受 `due_batch` / `OA_BATCH` 约束。

## 退出码

- 0：无违规，或全部违规/环都已在基线登记且未到期；
- 1：存在基线外违规、stale 条目、基线外的环，或豁免已到期；
- 2：矩阵未完整登记（12×12 有缺口）/ 出现未登记顶层包 / 基线（方向或环）格式错误 / 自检失败。

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
**app-root 不存在-非目录-空目录-文件数低于下限 exit 2** /
**人为新增一处同层环 → SCC 检出并 exit 1** / **基线缺 `due_batch` → exit 2** /
**豁免到期（`--batch` 达到 `due_batch`）→ exit 1**。
"""

from __future__ import annotations

import argparse
import ast
import os
import re
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

#: 默认「文件级循环依赖（SCC）」基线文件（R2-ARC-07）
DEFAULT_CYCLE_BASELINE: Final[Path] = (
    Path(__file__).resolve().parent / "same_layer_cycle_baseline.txt"
)

#: 批次号格式（与 `deploy/validate_batch_clock.py` / `tests/expected_xfail.py` 同一套）
BATCH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^B([1-9][0-9]*)$")

#: `due_batch=` 字段（基线里必须显式写出，禁止「无期限豁免」）
DUE_FIELD_PREFIX: Final[str] = "due_batch="

#: 基线条目允许的审计编号前缀：P0/P1/P2 为历史审计编号，R2-ARC 为本轮架构审计编号
AUDIT_ID_PREFIXES: Final[tuple[str, ...]] = ("P0-", "P1-", "P2-", "R2-")

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
        due_batch: 到期批次（如 `B25`）——到该批次必须已消除。
        reason: 消除计划/理由。
        lineno: 该条目在基线文件里的行号（报错定位用）。
    """

    rule_id: str
    file: str
    imported_module: str
    mode: str
    audit_id: str
    due_batch: str
    reason: str
    lineno: int

    @property
    def key(self) -> tuple[str, str, str]:
        """基线匹配键（不含 rule_id：rule_id 由实测推导后校验一致性）。

        Returns:
            tuple[str, str, str]: `(file, imported_module, mode)`。
        """
        return (self.file, self.imported_module, self.mode)


@dataclass(frozen=True, slots=True)
class CycleEntry:
    """一条「文件级循环依赖」基线豁免（R2-ARC-07）。

    Attributes:
        members: 环内文件路径（升序元组，相对仓库根，posix 分隔）。
        audit_id: 审计编号（如 `R2-ARC-07`）。
        due_batch: 到期批次（如 `B25`）。
        reason: 消除计划/理由。
        lineno: 该条目在基线文件里的行号。
    """

    members: tuple[str, ...]
    audit_id: str
    due_batch: str
    reason: str
    lineno: int


def parse_batch(value: str) -> int:
    """把 `B23` 解析为 23（与 `validate_batch_clock.py` 同口径）。

    Args:
        value: 批次号字符串。

    Returns:
        int: 批次序号。

    Raises:
        BaselineFormatError: 格式不符。
    """
    match = BATCH_PATTERN.match(value.strip())
    if match is None:
        raise BaselineFormatError(f"批次号格式非法 {value!r}（应形如 B23）")
    return int(match.group(1))


def current_batch(explicit: str | None = None) -> str | None:
    """取当前批次号（`--batch` 优先，其次 `OA_BATCH`）。

    Args:
        explicit: 命令行显式指定的批次号。

    Returns:
        str | None: 批次号；两者都没有时返回 None（调用方跳过到期判定）。
    """
    raw = (explicit or os.environ.get("OA_BATCH", "")).strip()
    if not raw:
        return None
    parse_batch(raw)  # 格式非法直接抛 BaselineFormatError
    return raw


def expired(entry_due: str, batch: str | None) -> bool:
    """判断某条豁免是否已到期。

    口径：当前批次 `>=` `due_batch` 即到期（到该批次必须已消除，不是"到期后才开始修"）。

    Args:
        entry_due: 条目登记的到期批次（`B25`）。
        batch: 当前批次；None 表示时钟不可用（跳过判定）。

    Returns:
        bool: 已到期返回 True。
    """
    if batch is None:
        return False
    return parse_batch(batch) >= parse_batch(entry_due)


@dataclass
class ScanResult:
    """一次扫描的结构化结果。

    Attributes:
        violations: 判红口径的违规（`module` + `deferred`），键为 `(file, module, mode)`。
        type_checking_edges: 仅统计的 `TYPE_CHECKING` 边（`(rule_id, file, module)`）。
        edges: 12×12 实边计数，键为 `(源层, 目标层)`，值为去重后的边数。
        file_graph: 文件级 import 图（`module` + `deferred` 边），键/值均为相对路径。
        cycles: Tarjan 检出的强连通分量（成员数 >= 2），成员为升序元组。
        dynamic_imports: 动态导入盲区提示（`(file, lineno, 摘要)`）。
        scanned_files: 实际扫描的文件数。
    """

    violations: dict[tuple[str, str, str], Violation] = field(default_factory=dict)
    type_checking_edges: list[tuple[str, str, str]] = field(default_factory=list)
    edges: dict[tuple[str, str], int] = field(default_factory=dict)
    file_graph: dict[str, set[str]] = field(default_factory=dict)
    cycles: list[tuple[str, ...]] = field(default_factory=list)
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


def _resolve_import_from(module_name: str, is_package: bool, node: ast.ImportFrom) -> list[str]:
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


def _resolve_module_file(module: str, app_root: Path) -> Path | None:
    """把点分模块名解析到磁盘上的 `.py` 文件（`module` 或 `package/__init__.py`）。

    只做**静态路径映射**，不 import 任何东西（与全脚本零依赖同风格）。解析不到
    （第三方模块名、被删的模块、仅存在于 `plugins` 目录下的动态包）返回 None，
    调用方跳过——**宁可漏记一条边，也不能把环算错**。

    Args:
        module: 完整模块名（`app.business.order_service`）。
        app_root: `app` 包根目录（其父目录即包导入根）。

    Returns:
        Path | None: 解析到的文件路径；解析不到返回 None。
    """
    segments = module.split(".")
    if not segments or segments[0] != "app":
        return None
    base = app_root.parent
    candidate = base.joinpath(*segments)
    module_file = candidate.with_suffix(".py")
    if module_file.is_file():
        return module_file
    package_init = candidate / "__init__.py"
    if package_init.is_file():
        return package_init
    return None


def find_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """用 Tarjan 算法找**文件级**强连通分量（成员数 >= 2 即一个环）。

    为什么不用 `networkx`：本脚本零依赖（只用标准库）是刻意的（见模块 docstring），
    而 Tarjan 只有二十来行。递归深度 = 最长简单路径，`app` 树实测 200 余个文件、
    深度远小于 Python 默认递归上限。

    自环（文件 import 自己）在 `scan()` 侧已被剔除（`target == source` 直接跳过），
    这种写法在 Python 里不构成真实的互相依赖。

    Args:
        graph: 邻接表，键/值均为文件相对路径。

    Returns:
        list[tuple[str, ...]]: 每个环的成员（升序元组），列表按首个成员排序。
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = [0]
    components: list[tuple[str, ...]] = []

    def strongconnect(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack[node] = True
        for neighbour in sorted(graph.get(node, ())):
            if neighbour not in index:
                strongconnect(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif on_stack.get(neighbour, False):
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            members: list[str] = []
            while True:
                popped = stack.pop()
                on_stack[popped] = False
                members.append(popped)
                if popped == node:
                    break
            if len(members) > 1:
                components.append(tuple(sorted(members)))

    for vertex in sorted(graph):
        if vertex not in index:
            strongconnect(vertex)

    return sorted(components)


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
    count = sum(1 for path in app_root.rglob("*.py") if "__pycache__" not in path.parts)
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
        if source_layer is not None:
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
                # ---- 文件级 import 图（R2-ARC-07：SCC 判环的输入）----
                # `type_checking` 边不产生运行时依赖，不进图（否则会把「只在类型注解里
                # 互相引用」判成死锁，纯噪声）；`target == path` 的自环同样剔除。
                if mode != MODE_TYPE_CHECKING:
                    target_file = _resolve_module_file(target, app_root)
                    if target_file is not None and target_file != path:
                        try:
                            target_shown = target_file.relative_to(repo_root).as_posix()
                        except ValueError:  # pragma: no cover - 仅自检临时目录
                            target_shown = target_file.as_posix()
                        result.file_graph.setdefault(shown, set()).add(target_shown)
                if source_layer is None:
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
    result.cycles = find_cycles(result.file_graph)
    return result


def load_baseline(path: Path) -> list[BaselineEntry]:
    """读取并校验基线文件。

    Args:
        path: 基线文件路径。

    Returns:
        list[BaselineEntry]: 基线条目（按文件行序）。

    Raises:
        BaselineFormatError: 格式错误（列数不足 / 缺审计编号 / 缺 due_batch / 未知形态 / 重复行）。
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
            raise BaselineFormatError(f"{path.name}:{lineno} 缺少 `--` 审计编号段：{raw.strip()}")
        head_parts = head.split()
        if len(head_parts) < 4:
            raise BaselineFormatError(
                f"{path.name}:{lineno} 列数不足（需 rule_id file module mode）：{raw.strip()}"
            )
        rule, file_name, imported_module, mode = head_parts[:4]
        audit_id, due_batch, reason = _parse_tail(path.name, lineno, tail, raw)
        if mode not in {MODE_MODULE, MODE_DEFERRED, MODE_TYPE_CHECKING}:
            raise BaselineFormatError(f"{path.name}:{lineno} 未知导入形态 `{mode}`")
        if not audit_id.startswith(AUDIT_ID_PREFIXES):
            raise BaselineFormatError(
                f"{path.name}:{lineno} 审计编号不合规 `{audit_id}`（应形如 P1-4 / R2-ARC-07）"
            )
        entry = BaselineEntry(
            rule_id=rule,
            file=file_name,
            imported_module=imported_module,
            mode=mode,
            audit_id=audit_id,
            due_batch=due_batch,
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


def _parse_tail(name: str, lineno: int, tail: str, raw: str) -> tuple[str, str, str]:
    """解析 `--` 之后的 `<审计编号> due_batch=<Bn> <理由>` 三段。

    `due_batch` 是**必填**：R2-ARC-07 的根因就是「登记了、但没有任何到期约束」，
    缺这一字段等于永久免死，因此缺失直接判格式错误（exit 2），而不是告警放行。

    Args:
        name: 文件名（报错定位用）。
        lineno: 行号（报错定位用）。
        tail: `--` 之后的内容。
        raw: 原始行（报错回显用）。

    Returns:
        tuple[str, str, str]: `(审计编号, 批次号, 理由)`。

    Raises:
        BaselineFormatError: 缺审计编号 / 缺 due_batch / 批次号格式非法。
    """
    parts = tail.split()
    if not parts:
        raise BaselineFormatError(f"{name}:{lineno} 缺少审计编号：{raw.strip()}")
    audit_id = parts[0]
    due_batch = ""
    for index, token in enumerate(parts[1:], start=1):
        if token.startswith(DUE_FIELD_PREFIX):
            due_batch = token[len(DUE_FIELD_PREFIX) :].strip()
            parts = [*parts[:index], *parts[index + 1 :]]
            break
    if not due_batch:
        raise BaselineFormatError(
            f"{name}:{lineno} 缺少 `{DUE_FIELD_PREFIX}B<n>` 到期字段"
            f"（无期限豁免 = 永久免死，R2-ARC-07 禁止）：{raw.strip()}"
        )
    parse_batch(due_batch)
    reason = " ".join(parts[1:]).strip()
    return audit_id, due_batch, reason


def load_cycle_baseline(path: Path) -> list[CycleEntry]:
    """读取并校验「文件级循环依赖」基线（R2-ARC-07）。

    格式：`CYCLE <file> <file> [<file>...] -- <审计编号> due_batch=<Bn> <理由>`
    成员顺序不敏感（加载时排序），其余全字段精确匹配。

    Args:
        path: 基线文件路径。

    Returns:
        list[CycleEntry]: 环基线条目（按文件行序）。

    Raises:
        BaselineFormatError: 格式错误（非 CYCLE 开头 / 成员不足 2 个 / 缺审计编号 /
            缺 due_batch / 重复环）。
    """
    entries: list[CycleEntry] = []
    seen: dict[tuple[str, ...], int] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, tail = line.partition("--")
        if not separator:
            raise BaselineFormatError(f"{path.name}:{lineno} 缺少 `--` 审计编号段：{raw.strip()}")
        head_parts = head.split()
        if not head_parts or head_parts[0] != "CYCLE":
            raise BaselineFormatError(f"{path.name}:{lineno} 必须以 `CYCLE` 开头：{raw.strip()}")
        members = tuple(sorted(head_parts[1:]))
        if len(members) < 2:
            raise BaselineFormatError(
                f"{path.name}:{lineno} 环成员不足 2 个（单个文件不构成互相依赖）"
            )
        audit_id, due_batch, reason = _parse_tail(path.name, lineno, tail, raw)
        if not audit_id.startswith(AUDIT_ID_PREFIXES):
            raise BaselineFormatError(
                f"{path.name}:{lineno} 审计编号不合规 `{audit_id}`（应形如 R2-ARC-07）"
            )
        if members in seen:
            raise BaselineFormatError(
                f"{path.name}:{lineno} 与第 {seen[members]} 行重复（同一组成员）"
            )
        seen[members] = lineno
        entries.append(
            CycleEntry(
                members=members,
                audit_id=audit_id,
                due_batch=due_batch,
                reason=reason,
                lineno=lineno,
            )
        )
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


def evaluate_cycles(
    result: ScanResult, baseline: list[CycleEntry]
) -> tuple[list[CycleEntry], list[tuple[str, ...]], list[CycleEntry]]:
    """把检出的环与环基线对齐（只许减不许增）。

    Args:
        result: 扫描结果（含 `cycles`）。
        baseline: 环基线条目。

    Returns:
        tuple: `(基线内, 基线外新增环, 已消失 stale)`。
    """
    by_members = {entry.members: entry for entry in baseline}
    matched: list[CycleEntry] = []
    new_cycles: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for members in result.cycles:
        entry = by_members.get(members)
        if entry is None:
            new_cycles.append(members)
            continue
        seen.add(members)
        matched.append(entry)
    stales = [entry for entry in baseline if entry.members not in seen]
    return matched, new_cycles, stales


def find_expired(entries: list[tuple[str, str, str, int]], batch: str | None) -> list[str]:
    """找出已到期的豁免（当前批次 `>=` due_batch）。

    Args:
        entries: `(审计编号, due_batch, 描述, 行号)` 列表。
        batch: 当前批次；None 表示时钟不可用。

    Returns:
        list[str]: 到期项描述；空列表表示无到期项。
    """
    failures: list[str] = []
    if batch is None:
        return failures
    for audit_id, due_batch, description, lineno in entries:
        if expired(due_batch, batch):
            failures.append(
                f"{description}（基线第 {lineno} 行，{audit_id}）已到期："
                f"当前批次 {batch} >= due_batch {due_batch}，必须在本批次消除或续期评审"
            )
    return failures


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
    print(
        f"扫描文件 {result.scanned_files} 个；TYPE_CHECKING 边 "
        f"{len(result.type_checking_edges)} 条（只统计不判红）"
    )


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
    parser.add_argument(
        "--cycle-baseline",
        type=Path,
        default=DEFAULT_CYCLE_BASELINE,
        help="文件级循环依赖（SCC）基线（默认 deploy/same_layer_cycle_baseline.txt）",
    )
    parser.add_argument(
        "--batch",
        default=None,
        help="当前批次号（默认读 OA_BATCH；两者都没有则跳过到期判定）",
    )
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

    # ---- 环基线（R2-ARC-07）----
    cycle_baseline: list[CycleEntry] = []
    try:
        if not args.cycle_baseline.is_file():
            print(f"[FAIL] 环基线文件不存在：{args.cycle_baseline}")
            return 2
        cycle_baseline = load_cycle_baseline(args.cycle_baseline)
    except BaselineFormatError as exc:
        print(f"[FAIL] 环基线格式错误：{exc}")
        return 2
    cycle_ok, cycle_new, cycle_stale = evaluate_cycles(result, cycle_baseline)

    # ---- 到期判定（当前批次 >= due_batch 即红）----
    try:
        batch = current_batch(args.batch)
        if batch is None:
            print(
                "[WARN] 未设置 OA_BATCH（也未传 --batch）：跳过豁免到期判定。"
                "CI 的 job 级 env 恒有该变量，此分支只应出现在本地随手跑"
            )
        expired_items = find_expired(
            [
                (entry.audit_id, entry.due_batch, f"{entry.rule_id} {entry.file}", entry.lineno)
                for entry in baseline
            ],
            batch,
        )
        expired_items += find_expired(
            [
                (
                    entry.audit_id,
                    entry.due_batch,
                    f"CYCLE {' + '.join(entry.members)}",
                    entry.lineno,
                )
                for entry in cycle_baseline
            ],
            batch,
        )
    except BaselineFormatError as exc:
        print(f"[FAIL] 批次时钟异常：{exc}")
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
    for entry in cycle_ok:
        print(
            f"[WARN] CYCLE {' + '.join(entry.members)} -- {entry.audit_id} {entry.reason}".rstrip()
        )
    for members in cycle_new:
        print(
            f"[FAIL] 新增文件级循环依赖 {' + '.join(members)}"
            "（同层环不在 12×12 矩阵里；必须修，或评审后登记进环基线）"
        )
    for entry in cycle_stale:
        print(
            f"[FAIL] stale CYCLE {' + '.join(entry.members)}"
            f" -- {entry.audit_id}：该环已消失，请删掉这条基线（基线只许减不许增）"
        )
    for item in expired_items:
        print(f"[FAIL] 豁免到期 {item}")

    if result.dynamic_imports:
        print(f"[WARN] 动态导入盲区（共 {len(result.dynamic_imports)} 处，不判红，需人工确认）：")
        for shown, lineno, message in result.dynamic_imports[:20]:
            print(f"  - {shown}:{lineno} {message}")

    total = len(result.violations)
    print()
    print(
        f"结论：违规 {total} 条（基线内 {len(warnings)} / 基线外 {len(failures)} / "
        f"stale {len(stales)}），环 {len(result.cycles)} 个"
        f"（基线内 {len(cycle_ok)} / 新增 {len(cycle_new)} / stale {len(cycle_stale)}），"
        f"扫描 {result.scanned_files} 个文件，批次时钟 {batch or '未启用'}"
    )
    if failures or stales or cycle_new or cycle_stale or expired_items:
        return 1
    print("[OK] 分层依赖方向合规（无新增文件级循环依赖，豁免未到期）")
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
        # `due_batch=B99` 是"永远不到期"，避免自测结果被 CI 的 OA_BATCH 时钟左右。
        empty_cycle_baseline = root / "cycle_baseline_empty.txt"
        empty_cycle_baseline.write_text("# 自检样本：无登记环\n", encoding="utf-8")

        good_baseline = root / "baseline_good.txt"
        good_baseline.write_text(
            "\n".join(
                [
                    "# 自检样本：只许减不许增",
                    *(
                        f"R-DB-BIZ {file} {module} {mode} -- P1-4 due_batch=B99 自检样本"
                        for file, module, mode in sorted(_SELF_TEST_EXPECTED)
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        _base_args = [
            "--app-root",
            str(app_root),
            "--cycle-baseline",
            str(empty_cycle_baseline),
            "--min-files",
            _SELF_TEST_MIN_FILES,
        ]
        code = main([*_base_args, "--baseline", str(good_baseline)])
        if code != 0:
            print(f"[FAIL] 用例 6：基线命中应 exit 0，实际 {code}")
            return 2

        # 用例 7：基线缺一条 → 该条 FAIL，exit 1
        partial_baseline = root / "baseline_partial.txt"
        partial_baseline.write_text(
            "R-DB-BIZ app/db/probe_module_level.py app.business.audit_service "
            "module -- P1-4 due_batch=B99 自检样本\n",
            encoding="utf-8",
        )
        code = main([*_base_args, "--baseline", str(partial_baseline)])
        if code != 1:
            print(f"[FAIL] 用例 7：基线外违规应 exit 1，实际 {code}")
            return 2

        # 用例 7b：基线条目已消失 → stale，exit 1
        stale_baseline = root / "baseline_stale.txt"
        stale_baseline.write_text(
            good_baseline.read_text(encoding="utf-8")
            + "R-DB-BIZ app/db/probe_gone.py app.db.x module -- P1-4 due_batch=B99 已修复未删\n",
            encoding="utf-8",
        )
        code = main([*_base_args, "--baseline", str(stale_baseline)])
        if code != 1:
            print(f"[FAIL] 用例 7b：stale 应 exit 1，实际 {code}")
            return 2

        # 用例 8：格式错误（列数不足）→ exit 2
        broken_baseline = root / "baseline_broken.txt"
        broken_baseline.write_text(
            "R-DB-BIZ app/db/probe_module_level.py -- P1-4 列数不足\n", encoding="utf-8"
        )
        code = main([*_base_args, "--baseline", str(broken_baseline)])
        if code != 2:
            print(f"[FAIL] 用例 8：格式错误应 exit 2，实际 {code}")
            return 2

        # 用例 8b：rule_id 与实测不符 → exit 2（防「登记了错的编号」掩盖真实方向）
        wrong_rule_baseline = root / "baseline_wrong_rule.txt"
        wrong_rule_baseline.write_text(
            "R-BIZ-DB app/db/probe_module_level.py app.business.audit_service "
            "module -- P1-4 due_batch=B99 编号写反\n",
            encoding="utf-8",
        )
        code = main([*_base_args, "--baseline", str(wrong_rule_baseline)])
        if code != 2:
            print(f"[FAIL] 用例 8b：rule_id 不符应 exit 2，实际 {code}")
            return 2

        # 用例 8c：缺少 due_batch → exit 2（R2-ARC-07：不允许无期限豁免）
        no_due_baseline = root / "baseline_no_due.txt"
        no_due_baseline.write_text(
            "\n".join(
                f"R-DB-BIZ {file} {module} {mode} -- P1-4 没有到期字段"
                for file, module, mode in sorted(_SELF_TEST_EXPECTED)
            )
            + "\n",
            encoding="utf-8",
        )
        code = main([*_base_args, "--baseline", str(no_due_baseline)])
        if code != 2:
            print(f"[FAIL] 用例 8c：缺 due_batch 应 exit 2，实际 {code}")
            return 2

        # 用例 8d：豁免到期（当前批次 >= due_batch）→ exit 1
        # 这是 R2-ARC-07 的核心诉求：存量豁免必须带到期约束，否则形同免死金牌。
        expired_baseline = root / "baseline_expired.txt"
        expired_baseline.write_text(
            "\n".join(
                f"R-DB-BIZ {file} {module} {mode} -- P1-4 due_batch=B25 到期样本"
                for file, module, mode in sorted(_SELF_TEST_EXPECTED)
            )
            + "\n",
            encoding="utf-8",
        )
        code = main([*_base_args, "--baseline", str(expired_baseline), "--batch", "B25"])
        if code != 1:
            print(f"[FAIL] 用例 8d：豁免到期应 exit 1，实际 {code}")
            return 2
        # 反向对照：同一份基线，时钟未到（B24 < B25）→ 必须放行（防"恒红"）
        code = main([*_base_args, "--baseline", str(expired_baseline), "--batch", "B24"])
        if code != 0:
            print(f"[FAIL] 用例 8d：时钟未到时不应判红，实际 {code}")
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

        # 用例 10：**人为新增一处同层环 → 必须检出**（R2-ARC-07 负样本）
        # 两个同层文件互相 import（一个 module、一个 deferred），12×12 矩阵对此完全
        # 无感（同层自引用恒允许），只有 SCC 检测能抓到。
        cycle_root = root / "cycle_case"
        cycle_app = cycle_root / "app"
        for relative, content in {
            "__init__.py": "",
            "business/__init__.py": "",
            "business/loop_a.py": (
                "from __future__ import annotations\n\n"
                "from app.business.loop_b import helper\n\n"
                '__all__ = ["helper"]\n'
            ),
            "business/loop_b.py": (
                "from __future__ import annotations\n\n\n"
                "def helper() -> None:\n"
                "    from app.business.loop_a import (  # noqa: F401\n"
                "        helper as _inner,\n"
                "    )\n"
                "    return None\n"
            ),
        }.items():
            target = cycle_app / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        try:
            cycle_result = scan(cycle_app, repo_root=cycle_root)
        except (MatrixError, UnregisteredPackageError, SyntaxError) as exc:  # pragma: no cover
            print(f"[FAIL] 用例 10：扫描异常 {exc}")
            return 2
        expected_cycle = (
            "app/business/loop_a.py",
            "app/business/loop_b.py",
        )
        if cycle_result.cycles != [expected_cycle]:
            print(f"[FAIL] 用例 10：同层环未被正确检出，实际 {cycle_result.cycles}")
            return 2
        # 10b：环不在基线里 → exit 1（新增环即红）
        code = main(
            [
                "--app-root",
                str(cycle_app),
                "--cycle-baseline",
                str(empty_cycle_baseline),
                "--min-files",
                "4",
            ]
        )
        if code != 1:
            print(f"[FAIL] 用例 10b：新增环应 exit 1，实际 {code}")
            return 2
        # 10c：环已登记 → exit 0（登记后不阻断）
        registered_cycle_baseline = root / "cycle_baseline_registered.txt"
        registered_cycle_baseline.write_text(
            "CYCLE app/business/loop_a.py app/business/loop_b.py"
            " -- R2-ARC-07 due_batch=B99 自检样本\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--app-root",
                str(cycle_app),
                "--cycle-baseline",
                str(registered_cycle_baseline),
                "--min-files",
                "4",
            ]
        )
        if code != 0:
            print(f"[FAIL] 用例 10c：已登记环应 exit 0，实际 {code}")
            return 2
        # 10d：环已消失但基线未删 → stale，exit 1（只许减不许增）
        code = main(
            [
                "--app-root",
                str(app_root),
                "--baseline",
                str(good_baseline),
                "--cycle-baseline",
                str(registered_cycle_baseline),
                "--min-files",
                _SELF_TEST_MIN_FILES,
            ]
        )
        if code != 1:
            print(f"[FAIL] 用例 10d：环基线 stale 应 exit 1，实际 {code}")
            return 2

    print(
        "[OK] 自检通过：模块级检出 / 延迟检出 / for-while-match 分支体检出（module）/ "
        "函数内嵌套导入检出（deferred）/ services→business 放行 / 相对导入检出 / "
        "TYPE_CHECKING 不判红 / 基线 WARN exit 0 / 基线外 exit 1 / stale exit 1 / "
        "格式错误-rule_id 不符-缺 due_batch exit 2 / 豁免到期 exit 1（未到期放行）/ "
        "同层环 SCC 检出 exit 1（登记后放行、stale 判红）/ "
        "app-root 不存在-非目录-空目录-文件数不足 exit 2"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
