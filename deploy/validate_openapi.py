#!/usr/bin/env python
"""OpenAPI 端点基线门禁（阶段二 D-09，架构文档 2.2 第 3 条）。

五道门禁，**任一失败即退出码非 0**，并明确指出缺失/变更的 `method + path`：

- **G1 核心基线**：`core_actual` 与 `baseline-core-30.json` **精确集合相等**
  （新增/删除/改路径都红）。
- **G2 新域基线**：已实现的新域端点必须全部登记在 `baseline-phase2-38.json` /
  `baseline-phase3-16.json`；登记的端点必须已实现（**恒严格，无任何放宽参数**）。
- **G3 计数**：`len(core) == 30`；`len(phase2) == 38`；
  **`len(phase3) == 16` 恒严格**；阶段四 `declared == 10`。
- **G4 加性 diff**：与 `openapi.snapshot.json` 比对：允许新增属性/新增枚举值；
  禁止删属性、改类型、`required` 变长、enum 收窄、新增必填请求字段、删除既有响应码。
- **G5 泄漏扫描**：对外文档出现底座/基础设施术语即红（保留 CI 原有 4 个禁词并扩充）。
- **G6 阶段四登记**：基线登记的 10 条里**未实现**的部分按 `[PENDING]` 打印；
  默认**不判红**（登记先于实现是这一批的正常状态），`--strict` 才判红
  （W2 起 CI 用 `--strict`）。
- **G7 W1 登记**：`baseline-w1-2.json` 必须精确 2 条
  （`GET /api/v1/stats/tasks` / `GET /api/v1/capabilities/recommend`）；
  W1 端点实现前同样按 `[PENDING]` 打印、默认不判红，`--strict` 判红；
  **未登记的端点仍立即红**（会留在我 core 里被 G1 抓出）。
- **G8 矩阵第 2 项登记**：`baseline-w2-2.json` 必须精确 2 条
  （`PUT /api/v1/agent/tasks/{task_id}/plan` / `POST .../plan/confirm`）。
  这两个端点是**本期新增且已实现**，登记侧与实现侧都严格（无 `[PENDING]`）。
- **G9 矩阵第 3 项登记**：`baseline-b3-4.json` 必须精确 4 条
  （`/api/v1/ops/mcp-servers` 的列表 / 登记 / 删除 / 探测）。
  与 G8 同口径：本期新增且已实现，双向严格（无 `[PENDING]`）。

**阶段四为什么允许「登记了但还没实现」（G6）**：阶段四的 10 个端点（MC/MM/TD）
要跨 T4-02~T4-05 四周落地，如果登记当天就让门禁红，唯一"解法"就是把基线删掉——
那正好把「新增端点必须登记」这条纪律废掉。所以这一批的口径是：**登记侧严格**
（`declared` 必须精确 10，多一条少一条都红；未登记的端点仍然立刻红），
**实现侧可见**（`present` 与 `missing` 每次打印），收口时切 `--strict`。

**为什么没有任何放宽参数（B21 / 2026-10-29 收口决定）**：
B19 起 phase3 已 16/16 全部落地，"已登记但未实现"这个中间态不再存在。
留一个放宽开关，等于给"删掉端点让 CI 变绿"留了一条合法通道——**门禁的放宽参数
一旦常态化，红与绿就不再说明任何事**。阶段二的历史放宽参数 `--allow-missing-phase2`
已于 2026-10-29 审计清理中从 CLI 与门禁逻辑整体移除（38/38 早已交付，放宽通道已死）。
确需临时放宽请在 PR 里显式删除端点并登记缺陷，而不是加参数。

用法：

```bash
python deploy/validate_openapi.py                        # 严格模式（阶段四/W1 实现侧不判红）
python deploy/validate_openapi.py --strict               # 阶段四/W1 missing>0 亦判红（W2 起 CI 用）
python deploy/validate_openapi.py --update-snapshot      # 端点变更后更新快照（PR 需贴 diff）
python deploy/validate_openapi.py --self-test            # 负样本自检（命中 = 退出码 1）
```

**为什么需要 `--self-test`（L-20）**：本脚本是最重要的契约门禁（端点基线），
但此前是 14 个门禁里**唯一**没有自检的——它只能靠"CI 一直是绿的"来证明自己，
而"一直是绿的"恰恰是门禁变哑时最典型的表现。`--self-test` 注入三类内联负样本
（删除 core 端点 / 改 method / 收窄 enum），全部命中才返回 1，用机械方式证明
G1 与 G4 在该红的场景**确实会红**，且不在磁盘上留下任何改动（合成 schema 在内存里构造，
不碰 `openapi.snapshot.json`）。

**W1 增量为什么照 phase4 先例（G7）**：W1 的两个端点（任务统计 / 能力推荐）
跨 T01（登记）→ T02/T03（实现）落地，登记当天就让门禁红，唯一"解法"就是删基线。
所以口径与阶段四一致：**登记侧严格**（`declared` 必须精确 2，未登记端点立即红），
**实现侧可见**（`[PENDING]` 只报告不判红，收口时切 `--strict`）。

**禁止**用本脚本改基线文件：`baseline-core-30.json` 受 CODEOWNERS 保护，
`baseline-phase2-38.json` 只能人工评审后追加（防止「为了过门禁而改基线」）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final

#: 仓库根目录（本脚本位于 <repo>/deploy/）
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 适配层源码目录（导入 `app.main` 需要）
ADAPTER_DIR: Final[Path] = REPO_ROOT / "apps" / "adapter"

#: 校验的 API 前缀
API_PREFIX: Final[str] = "/api/v1"

#: 核心端点基线数量（冻结，任何人不得修改）
CORE_BASELINE_COUNT: Final[int] = 30

#: 阶段二新域端点基线数量
PHASE2_BASELINE_COUNT: Final[int] = 38

#: 阶段三插件域端点基线数量（PL1-PL14 + WF1 + WF2，架构 v2 增补 PL14/WF2）
PHASE3_BASELINE_COUNT: Final[int] = 16

#: 阶段四端点基线数量（架构 §4.2：MC1-MC4 / MM1-MM4 / TD1-TD2）
PHASE4_BASELINE_COUNT: Final[int] = 10

#: W1 增量端点基线数量（`baseline-w1-2.json`：W1-A1 任务统计 / W1-B1 能力推荐）
W1_BASELINE_COUNT: Final[int] = 2

#: 矩阵第 2 项增量端点基线数量（`baseline-w2-2.json`：计划编辑 / 计划确认）
W2_BASELINE_COUNT: Final[int] = 2

#: 矩阵第 3 项增量端点基线数量（`baseline-b3-4.json`：MCP server 登记 / 删除 / 探测 / 列表）
B3_BASELINE_COUNT: Final[int] = 4

#: 对外文档禁词（G5）：任何底座/中间件/内部实现的术语都不得外泄
FORBIDDEN_TERMS: Final[tuple[str, ...]] = (
    "dify",
    "milvus",
    "litellm",
    "chat-messages",
    "datasets",
    "workflow_run",
    "qdrant",
    "weaviate",
    "keycloak",
    "asyncpg",
    "postgresql",
)

#: HTTP 方法白名单（OpenAPI 文档中的操作键）
HTTP_METHODS: Final[tuple[str, ...]] = (
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "head",
    "options",
    "trace",
)

#: 视为「既有错误响应删除」的响应码（4xx/5xx 不得整体消失）
ERROR_STATUS_PREFIXES: Final[tuple[str, ...]] = ("4", "5")


class GateFailure:
    """一条门禁失败记录。"""

    def __init__(self, gate: str, detail: str) -> None:
        """初始化。

        Args:
            gate: 门禁编号（G1-G5）。
            detail: 失败明细（必须含 method + path）。
        """
        self.gate = gate
        self.detail = detail

    def render(self) -> str:
        """渲染为一行文本。

        Returns:
            str: `[FAIL] G1 ...` 形式。
        """
        return f"[FAIL] {self.gate} {self.detail}"


def _ensure_adapter_on_path() -> None:
    """把适配层源码目录加入 `sys.path`（脚本可从任意目录运行）。"""
    adapter = str(ADAPTER_DIR)
    if adapter not in sys.path:
        sys.path.insert(0, adapter)


def build_schema() -> dict[str, Any]:
    """构建当前应用的 OpenAPI 文档。

    Returns:
        dict[str, Any]: OpenAPI 文档（FastAPI 生成）。
    """
    _ensure_adapter_on_path()
    from app.main import create_app

    # FastAPI 的 `openapi()` 已标注为 `dict[str, Any]`，早前那处
    # `# type: ignore[no-any-return]` 已经过期（`warn_unused_ignores` 会报它）。
    return create_app().openapi()


def collect_operations(schema: dict[str, Any]) -> set[tuple[str, str]]:
    """收集 `/api/v1` 下的全部操作（方法大写 + 路径）。

    Args:
        schema: OpenAPI 文档。

    Returns:
        set[tuple[str, str]]: `(METHOD, path)` 集合。
    """
    operations: set[tuple[str, str]] = set()
    for path, methods in schema.get("paths", {}).items():
        if not path.startswith(API_PREFIX):
            continue
        if not isinstance(methods, dict):
            continue
        for method in methods:
            if method.lower() in HTTP_METHODS:
                operations.add((method.upper(), path))
    return operations


def load_baseline(path: Path) -> set[tuple[str, str]]:
    """读取基线文件中的端点集合。

    Args:
        path: 基线文件路径。

    Returns:
        set[tuple[str, str]]: 端点集合；文件缺失时为空集合。
    """
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    endpoints: set[tuple[str, str]] = set()
    for item in payload.get("endpoints", []):
        method = str(item.get("method", "")).upper()
        path_value = str(item.get("path", ""))
        if method and path_value:
            endpoints.add((method, path_value))
    return endpoints


def _format_operations(operations: Iterable[tuple[str, str]]) -> str:
    """把端点集合渲染为易读列表。

    Args:
        operations: 端点集合。

    Returns:
        str: 形如 `POST /api/v1/x, GET /api/v1/y` 的文本。
    """
    return ", ".join(
        f"{method} {path}" for method, path in sorted(operations, key=operation_sort_key)
    )


def operation_sort_key(operation: tuple[str, str]) -> tuple[str, str]:
    """端点排序键（先路径后方法，输出稳定）。"""
    return operation[1], operation[0]


def _format_pending(pending: Iterable[tuple[str, str]]) -> list[str]:
    """把「已登记未实现」端点渲染成 `METHOD path` 列表（供 JSON 输出）。

    Args:
        pending: 端点集合。

    Returns:
        list[str]: 形如 `["GET /api/v1/model/conversations"]` 的列表。
    """
    return [f"{method} {path}" for method, path in sorted(pending, key=operation_sort_key)]


# ===========================================================================
# G1 / G2 / G3：集合与计数
# ===========================================================================
def gate_core_baseline(
    actual: set[tuple[str, str]], baseline: set[tuple[str, str]]
) -> list[GateFailure]:
    """G1：核心端点集合必须与冻结基线**精确相等**。

    Args:
        actual: 当前实现的核心端点。
        baseline: 冻结基线端点。

    Returns:
        list[GateFailure]: 失败明细（缺失 / 多出）。
    """
    failures: list[GateFailure] = []
    missing = baseline - actual
    extra = actual - baseline
    if missing:
        failures.append(
            GateFailure("G1", f"核心端点缺失（被删除或改路径）: {_format_operations(missing)}")
        )
    if extra:
        failures.append(
            GateFailure(
                "G1",
                f"出现未登记的核心端点（新增端点必须登记到基线文件）: {_format_operations(extra)}",
            )
        )
    return failures


def gate_phase2_baseline(
    actual: set[tuple[str, str]],
    declared: set[tuple[str, str]],
) -> list[GateFailure]:
    """G2：新域端点必须全部登记在对应基线文件里，且登记的端点必须已实现（恒严格）。

    阶段二/阶段三均已收口，**没有任何放宽参数**——「已登记但未实现」也判红。
    缺失时的处置只有两条路：把端点补回来，或从基线撤销登记并登记缺陷。

    Args:
        actual: 当前实现的新域端点（`全部端点 ∩ 已登记集合`）。
        declared: 基线登记的端点。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    missing = declared - actual
    extra = actual - declared
    if extra:
        failures.append(
            GateFailure(
                "G2",
                f"新域端点未登记（禁止先写端点后补基线）: {_format_operations(extra)}",
            )
        )
    if missing:
        failures.append(
            GateFailure(
                "G2",
                f"新域端点未实现: {_format_operations(missing)}"
                "（要么把端点补回来，要么从基线撤销登记并登记缺陷）",
            )
        )
    return failures


def gate_phase4_baseline(
    actual: set[tuple[str, str]],
    declared: set[tuple[str, str]],
    *,
    strict: bool,
) -> list[GateFailure]:
    """G6：阶段四端点「登记先于实现」的口径。

    两条判据方向相反，这是**故意**的：

    - **登记侧严格**：`declared` 必须精确等于 `PHASE4_BASELINE_COUNT`（10）。
      少一条说明基线被人删了（为了让 missing 变 0），多一条说明有人偷偷加端点；
    - **实现侧宽容**：`declared - actual` 非空时只打印 `[PENDING]`，不判红。
      这一批要跨四周落地，登记当天就红只会逼人删基线。

    `strict=True`（W2 起的 CI 口径）时实现侧也判红——那时端点应该全在了。

    Args:
        actual: 当前实际实现的阶段四端点（`全部端点 ∩ 已登记集合`）。
        declared: 基线登记的 10 条。
        strict: 是否把「未实现」也判红。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    if len(declared) != PHASE4_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G6",
                f"阶段四基线必须登记 {PHASE4_BASELINE_COUNT} 条，实际 {len(declared)} 条"
                "（删基线让门禁变绿不算修复）",
            )
        )
    extra = actual - declared
    if extra:
        # 构造上不可能发生（actual 是交集），留着是为了有人改实现时立刻炸出来
        failures.append(
            GateFailure("G6", f"阶段四端点未登记: {_format_operations(extra)}")
        )  # pragma: no cover - 交集运算下不可达
    missing = declared - actual
    if missing and strict:
        failures.append(
            GateFailure(
                "G6",
                f"阶段四端点未实现（--strict）: {_format_operations(missing)}",
            )
        )
    return failures


def gate_w1_baseline(
    actual: set[tuple[str, str]],
    declared: set[tuple[str, str]],
    *,
    strict: bool,
) -> list[GateFailure]:
    """G7：W1 增量端点「登记先于实现」的口径（照 phase4 先例）。

    两条判据方向相反，与 G6 同理：

    - **登记侧严格**：`declared` 必须精确等于 `W1_BASELINE_COUNT`（2）。
      少一条说明基线被人删了，多一条说明有人偷偷加端点；
    - **实现侧宽容**：`declared - actual` 非空时只打印 `[PENDING]`，不判红；
      `strict=True`（W2 起的 CI 口径）时实现侧也判红。

    注意：**未登记**的端点不经过本门禁宽容——它会留在 `core_actual` 里，
    由 G1「出现未登记的核心端点」立即判红（见 `collect_failures` 的扣除口径）。

    Args:
        actual: 当前实际实现的 W1 端点（`全部端点 ∩ 已登记集合`）。
        declared: 基线登记的 2 条。
        strict: 是否把「未实现」也判红。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    if len(declared) != W1_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G7",
                f"W1 基线必须登记 {W1_BASELINE_COUNT} 条，实际 {len(declared)} 条"
                "（删基线让门禁变绿不算修复）",
            )
        )
    extra = actual - declared
    if extra:
        # 构造上不可能发生（actual 是交集），留着是为了有人改实现时立刻炸出来
        failures.append(GateFailure("G7", f"W1 端点未登记: {_format_operations(extra)}"))
    missing = declared - actual
    if missing and strict:
        failures.append(
            GateFailure(
                "G7",
                f"W1 端点未实现（--strict）: {_format_operations(missing)}",
            )
        )
    return failures


def gate_w2_baseline(
    actual: set[tuple[str, str]],
    declared: set[tuple[str, str]],
) -> list[GateFailure]:
    """G8：矩阵第 2 项（任务计划）增量端点**双向严格**。

    与 W1 不同：这 2 个端点本期已实现，所以没有 `[PENDING]` 宽容窗口。
    登记数必须精确 2（删基线让门禁变绿不算修复），登记的每条都必须已实现。

    Args:
        actual: 当前实际实现的 W2 端点（`全部端点 ∩ 已登记集合`）。
        declared: 基线登记的 2 条。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    if len(declared) != W2_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G8",
                f"计划端点基线必须登记 {W2_BASELINE_COUNT} 条，实际 {len(declared)} 条",
            )
        )
    missing = declared - actual
    if missing:
        failures.append(GateFailure("G8", f"计划端点未实现: {_format_operations(missing)}"))
    return failures


def gate_b3_baseline(
    actual: set[tuple[str, str]],
    declared: set[tuple[str, str]],
) -> list[GateFailure]:
    """G9：矩阵第 3 项（MCP 客户端）增量端点**双向严格**。

    与 G8 同口径：这 4 个端点本期一次性实现，没有 `[PENDING]` 宽容窗口。
    登记数必须精确 4（删基线让门禁变绿不算修复），登记的每条都必须已实现。

    Args:
        actual: 当前实际实现的 B3 端点（`全部端点 ∩ 已登记集合`）。
        declared: 基线登记的 4 条。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    if len(declared) != B3_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G9",
                f"MCP 端点基线必须登记 {B3_BASELINE_COUNT} 条，实际 {len(declared)} 条",
            )
        )
    missing = declared - actual
    if missing:
        failures.append(GateFailure("G9", f"MCP 端点未实现: {_format_operations(missing)}"))
    return failures


def gate_counts(
    core_count: int,
    phase2_count: int,
    phase3_count: int = 0,
    phase4_present: int = 0,
    w1_present: int = 0,
    *,
    phase4_declared: int = PHASE4_BASELINE_COUNT,
    w1_declared: int = W1_BASELINE_COUNT,
) -> list[GateFailure]:
    """G3：端点数门禁（core=30 / phase2=38 / phase3=16 / phase4=10 / w1=2）。

    新域端点（阶段二/阶段三）**恒严格**：`phase2_count` 必须精确等于 38、
    `phase3_count` 必须精确等于 16。B21 收口阶段三、2026-10-29 审计清理收口阶段二后，
    本函数不再提供任何放宽参数——"已登记但未实现"的中间态已经不存在，
    留着开关只会让门禁变哑。

    阶段四只校验**登记数**（`phase4_declared == 10`）与「已实现数不超过登记数」，
    已实现数本身由 G6 按 `--strict` 决定是否判红（见 `gate_phase4_baseline`）。

    W1 同口径：只校验「已实现数不超过登记数」（`w1_present <= w1_declared`），
    登记数纪律由 G7 守住（`w1_declared == 2`）。

    Args:
        core_count: 核心端点实际数量。
        phase2_count: 阶段二新域端点实际数量。
        phase3_count: 阶段三插件域端点实际数量。
        phase4_present: 阶段四已实现端点数（不参与判红，只做上限校验）。
        w1_present: W1 已实现端点数（不参与判红，只做上限校验）。
        phase4_declared: 阶段四基线登记数。
        w1_declared: W1 基线登记数。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    if core_count != CORE_BASELINE_COUNT:
        failures.append(
            GateFailure("G3", f"核心端点数量必须为 {CORE_BASELINE_COUNT}，实际 {core_count}")
        )
    if phase2_count != PHASE2_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G3",
                f"新域端点数量必须为 {PHASE2_BASELINE_COUNT}，实际 {phase2_count}",
            )
        )
    if phase4_present > phase4_declared:
        failures.append(
            GateFailure(
                "G3",
                f"阶段四已实现端点数 {phase4_present} 超过登记数 {phase4_declared}",
            )
        )
    if w1_present > w1_declared:
        failures.append(
            GateFailure(
                "G3",
                f"W1 已实现端点数 {w1_present} 超过登记数 {w1_declared}",
            )
        )
    if phase3_count != PHASE3_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G3",
                f"插件域端点数量必须为 {PHASE3_BASELINE_COUNT}，实际 {phase3_count}"
                f"（阶段三自 B21 起无放宽参数：少一条就是删了端点，多一条就是没登记基线）",
            )
        )
    return failures


# ===========================================================================
# G4：加性 diff
# ===========================================================================
def _type_of(node: dict[str, Any]) -> str:
    """推断 JSON Schema 节点的类型字符串。

    Args:
        node: schema 节点。

    Returns:
        str: 类型名（缺省 `object`）。
    """
    declared = node.get("type")
    if isinstance(declared, str):
        return declared
    if isinstance(declared, list):
        return "|".join(sorted(str(item) for item in declared))
    for key in ("anyOf", "oneOf", "allOf"):
        branches = node.get(key)
        if isinstance(branches, list) and branches:
            return "|".join(sorted(_type_of(item) for item in branches if isinstance(item, dict)))
    if "$ref" in node:
        return str(node["$ref"])
    return "object"


def diff_schema(old: Any, new: Any, path: str) -> list[str]:
    """比较两个 JSON Schema 节点，返回**破坏性变更**清单。

    允许（加性）：新增属性、新增枚举值、`required` 不变或变短。
    禁止：删除属性、类型变化、`required` 变长、enum 收窄（值被删除）。

    Args:
        old: 快照中的 schema 节点。
        new: 当前 schema 节点。
        path: 节点路径（用于报错定位）。

    Returns:
        list[str]: 违规描述列表。
    """
    violations: list[str] = []

    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            violations.append(
                f"{path}: 结构类型变化（{type(old).__name__} → {type(new).__name__}）"
            )
        return violations

    old_type = _type_of(old)
    new_type = _type_of(new)
    if old_type != new_type:
        violations.append(f"{path}: 类型变化（{old_type} → {new_type}）")

    old_required = set(old.get("required", []) or [])
    new_required = set(new.get("required", []) or [])
    added_required = new_required - old_required
    if added_required:
        violations.append(f"{path}: required 变长（新增 {sorted(added_required)}，属破坏性变更）")

    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed_values = [item for item in old_enum if item not in new_enum]
        if removed_values:
            violations.append(f"{path}: enum 收窄（删除 {removed_values}）")

    old_props = old.get("properties")
    new_props = new.get("properties")
    if isinstance(old_props, dict):
        if not isinstance(new_props, dict):
            violations.append(f"{path}: properties 整体消失")
        else:
            removed = [key for key in old_props if key not in new_props]
            if removed:
                violations.append(f"{path}: 删除属性 {removed}")
            for key, old_child in old_props.items():
                if key in new_props:
                    violations.extend(
                        diff_schema(old_child, new_props[key], f"{path}.properties.{key}")
                    )

    if "items" in old:
        violations.extend(diff_schema(old.get("items"), new.get("items"), f"{path}.items"))

    return violations


def _response_schema(operation: dict[str, Any]) -> Any:
    """取出操作的首个成功响应 schema（`application/json`）。

    Args:
        operation: OpenAPI 操作对象。

    Returns:
        Any: schema 节点；无 JSON 响应时返回 None。
    """
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return None
    for status, response in responses.items():
        if not isinstance(response, dict):
            continue
        content = response.get("content")
        if not isinstance(content, dict):
            continue
        json_content = content.get("application/json")
        if isinstance(json_content, dict) and "schema" in json_content:
            return {"status": status, "schema": json_content["schema"]}
    return None


def _request_schema(operation: dict[str, Any]) -> Any:
    """取出操作的请求体 schema。

    Args:
        operation: OpenAPI 操作对象。

    Returns:
        Any: schema 节点；无请求体时返回 None。
    """
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    if not isinstance(content, dict):
        return None
    json_content = content.get("application/json")
    if isinstance(json_content, dict) and "schema" in json_content:
        return json_content["schema"]
    return None


def gate_additive_diff(snapshot: dict[str, Any], current: dict[str, Any]) -> list[GateFailure]:
    """G4：与快照做加性 diff。

    Args:
        snapshot: 冻结快照（完整 OpenAPI 文档）。
        current: 当前 OpenAPI 文档。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    old_paths = snapshot.get("paths", {})
    new_paths = current.get("paths", {})
    if not isinstance(old_paths, dict) or not isinstance(new_paths, dict):
        return failures

    for path, old_methods in old_paths.items():
        if not isinstance(old_methods, dict):
            continue
        new_methods = new_paths.get(path)
        if not isinstance(new_methods, dict):
            failures.append(GateFailure("G4", f"{path}: 路径消失（既有端点被删除）"))
            continue
        for method, old_operation in old_methods.items():
            if method.lower() not in HTTP_METHODS or not isinstance(old_operation, dict):
                continue
            new_operation = new_methods.get(method)
            label = f"{method.upper()} {path}"
            if not isinstance(new_operation, dict):
                failures.append(GateFailure("G4", f"{label}: 操作消失"))
                continue

            old_responses = old_operation.get("responses", {})
            new_responses = new_operation.get("responses", {})
            if isinstance(old_responses, dict) and isinstance(new_responses, dict):
                for status in old_responses:
                    if status.startswith(ERROR_STATUS_PREFIXES) and status not in new_responses:
                        failures.append(GateFailure("G4", f"{label}: 删除既有错误码响应 {status}"))

            old_response = _response_schema(old_operation)
            new_response = _response_schema(new_operation)
            if isinstance(old_response, dict) and isinstance(new_response, dict):
                for violation in diff_schema(
                    old_response["schema"], new_response["schema"], f"{label} response"
                ):
                    failures.append(GateFailure("G4", violation))

            old_request = _request_schema(old_operation)
            if isinstance(old_request, dict):
                new_request = _request_schema(new_operation)
                if isinstance(new_request, dict):
                    for violation in diff_schema(old_request, new_request, f"{label} request"):
                        failures.append(GateFailure("G4", violation))
                else:
                    failures.append(GateFailure("G4", f"{label}: 请求体 schema 消失"))
    return failures


# ===========================================================================
# G5：泄漏扫描
# ===========================================================================
def gate_leak_scan(schema: dict[str, Any]) -> list[GateFailure]:
    """G5：对外文档不得出现底座/基础设施术语。

    Args:
        schema: OpenAPI 文档。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    text = json.dumps(schema, ensure_ascii=False).lower()
    for term in FORBIDDEN_TERMS:
        if term in text:
            failures.append(GateFailure("G5", f"对外文档泄露禁用术语 `{term}`"))
    return failures


# ===========================================================================
# --self-test：门禁可红证明（全程内存合成，不碰仓库里的真实快照）
# ===========================================================================
#: 挂在自检靶端点 200 响应上的三值枚举（供 G4「enum 收窄」负样本使用）
_SELF_TEST_ENUM: Final[tuple[str, ...]] = ("active", "paused", "retired")

#: 收窄枚举时被删掉的那个值（`_SELF_TEST_ENUM` 的最后一个）
_SELF_TEST_ENUM_REMOVED: Final[str] = "retired"

#: 七套基线文件的文件名（自检按名读取，只读，不改）
_SELF_TEST_BASELINES: Final[tuple[tuple[str, str], ...]] = (
    ("core_baseline", "baseline-core-30.json"),
    ("phase2_baseline", "baseline-phase2-38.json"),
    ("phase3_baseline", "baseline-phase3-16.json"),
    ("phase4_baseline", "baseline-phase4-10.json"),
    ("w1_baseline", "baseline-w1-2.json"),
    ("w2_baseline", "baseline-w2-2.json"),
    ("b3_baseline", "baseline-b3-4.json"),
)


def _self_test_operation(schema: Any | None = None) -> dict[str, Any]:
    """构造一个最小 OpenAPI 操作对象（含 200/422 两个 JSON 响应）。

    Args:
        schema: 200 响应用的 `application/json` schema；缺省为最简 `object`。

    Returns:
        dict[str, Any]: 操作对象。
    """
    response_schema = schema if schema is not None else {"type": "object"}
    empty = {"content": {"application/json": {"schema": {"type": "object"}}}}
    return {
        "responses": {
            "200": {"content": {"application/json": {"schema": response_schema}}},
            "422": dict(empty),
        }
    }


def _self_test_baselines() -> dict[str, set[tuple[str, str]]]:
    """按文件名读取七套基线（只读；自检的端点全集来源）。

    Returns:
        dict[str, set[tuple[str, str]]]: 键与 `collect_failures` 的形参同名。
    """
    docs = REPO_ROOT / "docs" / "api"
    return {name: load_baseline(docs / filename) for name, filename in _SELF_TEST_BASELINES}


def _self_test_schema(
    baselines: dict[str, set[tuple[str, str]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """用基线的端点全集合成一份「应当全绿」的 schema 与同源快照。

    端点集合取自仓库里的七套基线（只读），因此正样本天然满足
    core=30 / phase2=38 / phase3=16 / phase4=10 / w1=2 / w2=2 / b3=4；若基线本身被人为改动，
    正样本会先失败——这正是我们想让它先炸出来的场景。

    Args:
        baselines: `_self_test_baselines()` 的返回值。

    Returns:
        tuple[dict[str, Any], dict[str, Any]]: (当前 schema, 冻结快照)。
    """
    paths: dict[str, Any] = {}
    for operations in baselines.values():
        for method, path in operations:
            paths.setdefault(path, {})[method.lower()] = _self_test_operation()
    # 给「排序第一的 core 端点」的 200 响应挂一个三值 enum，作为 G4 收窄靶子；
    # 其余端点保持最简 object，避免 diff 噪声。
    target = min(baselines["core_baseline"], key=operation_sort_key)
    paths[target[1]][target[0].lower()] = _self_test_operation(
        {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": list(_SELF_TEST_ENUM)}},
        }
    )
    schema: dict[str, Any] = {"openapi": "3.1.0", "paths": paths}
    snapshot = json.loads(json.dumps(schema))  # 深拷贝＝「改动前」的冻结快照
    return schema, snapshot


def _gate_failures_of(
    schema: dict[str, Any],
    snapshot: dict[str, Any],
    baselines: dict[str, set[tuple[str, str]]],
) -> list[GateFailure]:
    """跑一遍 `collect_failures`（`--strict` 口径），供负样本断言命中。

    Args:
        schema: 待判定的 schema。
        snapshot: 冻结快照。
        baselines: 七套基线。

    Returns:
        list[GateFailure]: 失败清单。
    """
    failures, _ = collect_failures(
        schema,
        core_baseline=baselines["core_baseline"],
        phase2_baseline=baselines["phase2_baseline"],
        phase3_baseline=baselines["phase3_baseline"],
        phase4_baseline=baselines["phase4_baseline"],
        w1_baseline=baselines["w1_baseline"],
        w2_baseline=baselines["w2_baseline"],
        b3_baseline=baselines["b3_baseline"],
        snapshot=snapshot,
        strict_phase4=True,
    )
    return failures


def _match_failure(failures: list[GateFailure], gate: str, needle: str) -> GateFailure | None:
    """在失败清单里找「指定门禁 + 明细含指定文本」的第一条。

    Args:
        failures: 失败清单。
        gate: 门禁编号（如 `G1` / `G4`）。
        needle: 需要在 `detail` 里出现的文本（如 `POST /api/v1/x` / `enum 收窄`）。

    Returns:
        GateFailure | None: 命中的失败记录；未命中返回 None。
    """
    for failure in failures:
        if failure.gate == gate and needle in failure.detail:
            return failure
    return None


def _pick_isolated_post(schema: dict[str, Any], core: set[tuple[str, str]]) -> tuple[str, str]:
    """挑一条「独占该 path」的 POST core 端点，供「改 method」负样本使用。

    独占（`len(paths[path]) == 1`）可以保证把 POST 改成 GET 之后，operations
    集合里该 path 上确实只剩一条操作，G1 的「缺失 + 多出」判据不会互相抵消。

    Args:
        schema: 合成 schema（用于判断 path 上的操作数）。
        core: 核心基线端点集合。

    Returns:
        tuple[str, str]: `(METHOD, path)`；若无独占 POST 则退回排序第一条 core 端点。
    """
    ordered = sorted(core, key=operation_sort_key)
    for method, path in ordered:
        if method == "POST" and len(schema["paths"].get(path, {})) == 1:
            return method, path
    return ordered[0]


def run_self_test() -> int:
    """注入三类负样本，证明门禁**确实会红**（全程内存合成，不碰真实快照）。

    三类负样本分别对应门禁最容易"变哑"的判据：

    1. **删除**一条 core 端点 → G1「精确集合相等」必须报「缺失」；
    2. **改 method**（POST → GET）→ G1 必须报「缺失」（改动前的操作消失）；
    3. **收窄 enum**（删一个枚举值）→ G4 加性 diff 必须报「enum 收窄」。

    Returns:
        int: 三类负样本**全部命中**返回 1（本仓库约定：自检成功 = exit 1，
        供 CI 用 `-ne 1` 断言）；任一未命中返回 0。
    """
    baselines = _self_test_baselines()
    if not all(baselines.values()):
        print("[FAIL] 自检无法进行：docs/api 下的基线文件缺失或为空")
        return 0
    schema, snapshot = _self_test_schema(baselines)

    # 0) 正样本必须全绿：否则后面"红了"说明不了任何事
    positive = _gate_failures_of(schema, snapshot, baselines)
    if positive:
        print("[FAIL] 自检正样本不干净，判据本身有问题：")
        for failure in positive:
            print(f"       {failure.render()}")
        return 0
    print("[OK]   正样本（基线全集 + 同源快照）G1-G9 全绿")

    notes: list[str] = []

    # 1) 删除一条 core 端点 → 期望 G1 报「缺失」
    deleted = min(baselines["core_baseline"], key=operation_sort_key)
    deleted_label = f"{deleted[0]} {deleted[1]}"
    mutated = json.loads(json.dumps(schema))
    del mutated["paths"][deleted[1]][deleted[0].lower()]
    if not mutated["paths"][deleted[1]]:
        del mutated["paths"][deleted[1]]
    failure = _match_failure(_gate_failures_of(mutated, snapshot, baselines), "G1", deleted_label)
    if failure is None:
        print(f"[FAIL] 负样本1（删除 core 端点 {deleted_label}）未被 G1 命中")
        return 0
    notes.append(f"[OK]   负样本1（删除 core 端点 {deleted_label}）被拦住：{failure.render()}")

    # 2) 改 method（POST → GET）→ 期望 G1 报「缺失」（原操作消失）
    source_method, method_path = _pick_isolated_post(schema, baselines["core_baseline"])
    target_method = "GET" if source_method != "GET" else "POST"
    source_label = f"{source_method} {method_path}"
    mutated = json.loads(json.dumps(schema))
    operation = mutated["paths"][method_path].pop(source_method.lower())
    mutated["paths"][method_path][target_method.lower()] = operation
    failure = _match_failure(_gate_failures_of(mutated, snapshot, baselines), "G1", source_label)
    if failure is None:
        print(f"[FAIL] 负样本2（把 {source_label} 改为 {target_method}）未被 G1 命中")
        return 0
    notes.append(f"[OK]   负样本2（{source_label} → {target_method}）被拦住：{failure.render()}")

    # 3) 收窄 enum（删一个枚举值）→ 期望 G4 报「enum 收窄」
    enum_target = min(baselines["core_baseline"], key=operation_sort_key)
    mutated = json.loads(json.dumps(schema))
    enum_node = mutated["paths"][enum_target[1]][enum_target[0].lower()]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["properties"]["status"]
    enum_node["enum"] = [value for value in enum_node["enum"] if value != _SELF_TEST_ENUM_REMOVED]
    failure = _match_failure(_gate_failures_of(mutated, snapshot, baselines), "G4", "enum 收窄")
    if failure is None:
        print("[FAIL] 负样本3（收窄 enum）未被 G4 命中")
        return 0
    notes.append(f"[OK]   负样本3（enum 收窄）被拦住：{failure.render()}")

    for note in notes:
        print(note)
    print("[OK]   自检通过：删除端点 / 改 method / 收窄 enum 三类负样本全部被拦截")
    return 1


# ===========================================================================
# 主流程
# ===========================================================================
def collect_failures(
    schema: dict[str, Any],
    *,
    core_baseline: set[tuple[str, str]],
    phase2_baseline: set[tuple[str, str]],
    phase3_baseline: set[tuple[str, str]] | None = None,
    phase4_baseline: set[tuple[str, str]] | None = None,
    w1_baseline: set[tuple[str, str]] | None = None,
    w2_baseline: set[tuple[str, str]] | None = None,
    b3_baseline: set[tuple[str, str]] | None = None,
    snapshot: dict[str, Any] | None = None,
    skip_diff: bool = False,
    strict_phase4: bool = False,
) -> tuple[list[GateFailure], dict[str, Any]]:
    """执行七道门禁并汇总结果。

    阶段三**恒严格**（无放宽参数）：少一条即红。
    阶段四按 G6 口径：登记数严格、实现数默认只报告（`strict_phase4` 时才判红）。
    W1 按 G7 口径：与阶段四相同（登记数严格、实现数默认只报告）。

    Args:
        schema: 当前 OpenAPI 文档。
        core_baseline: 核心基线端点。
        phase2_baseline: 阶段二基线端点。
        phase3_baseline: 阶段三插件域基线端点（None 视为空集）。
        phase4_baseline: 阶段四会话/记忆/租户数据基线端点（None 视为空集）。
        w1_baseline: W1 增量基线端点（None 视为空集，且不参与 G7 判定）。
        w2_baseline: 矩阵第 2 项增量基线端点（None 视为空集，且不参与 G8 判定）。
        b3_baseline: 矩阵第 3 项（MCP 客户端）增量基线端点（None 视为空集，且不参与 G9 判定）。
        snapshot: 冻结快照（None 表示跳过 G4）。
        skip_diff: 是否跳过 G4（仅 hotfix 分支允许）。
        strict_phase4: 阶段四/W1「已登记未实现」是否判红（W2 起 CI 口径）。

    Returns:
        tuple[list[GateFailure], dict[str, Any]]: 失败清单与统计信息。
    """
    declared_phase3 = phase3_baseline or set()
    declared_phase4 = phase4_baseline or set()
    declared_w1 = w1_baseline or set()
    declared_w2 = w2_baseline or set()
    declared_b3 = b3_baseline or set()
    operations = collect_operations(schema)
    phase2_actual = operations & phase2_baseline
    phase3_actual = operations & declared_phase3
    phase4_actual = operations & declared_phase4
    w1_actual = operations & declared_w1
    w2_actual = operations & declared_w2
    b3_actual = operations & declared_b3
    # 阶段四与 W1 登记过的端点必须先扣掉，否则它们会被算进 core → G1 精确相等立刻误报
    core_actual = (
        operations
        - phase2_baseline
        - declared_phase3
        - declared_phase4
        - declared_w1
        - declared_w2
        - declared_b3
    )

    failures: list[GateFailure] = []
    failures.extend(gate_core_baseline(core_actual, core_baseline))
    failures.extend(gate_phase2_baseline(phase2_actual, phase2_baseline))
    failures.extend(gate_phase2_baseline(phase3_actual, declared_phase3))
    if phase4_baseline is not None:
        # 显式传 None = 调用方声明"本次不参与阶段四判定"（合成 schema 的单元用例）；
        # CLI 与仓库级用例一律传真实基线，登记数纪律由它们守住。
        failures.extend(gate_phase4_baseline(phase4_actual, declared_phase4, strict=strict_phase4))
    if w1_baseline is not None:
        # 同 phase4：显式传 None = 本次不参与 W1 判定；仓库级用例一律传真实基线。
        failures.extend(gate_w1_baseline(w1_actual, declared_w1, strict=strict_phase4))
    if w2_baseline is not None:
        # 矩阵第 2 项：双向严格（已实现，无 PENDING 窗口）。
        failures.extend(gate_w2_baseline(w2_actual, declared_w2))
    if b3_baseline is not None:
        # 矩阵第 3 项（MCP 客户端）：双向严格（已实现，无 PENDING 窗口）。
        failures.extend(gate_b3_baseline(b3_actual, declared_b3))
    failures.extend(
        gate_counts(
            len(core_actual),
            len(phase2_actual),
            len(phase3_actual),
            len(phase4_actual),
            len(w1_actual),
            phase4_declared=len(declared_phase4) if phase4_baseline is not None else 0,
            w1_declared=len(declared_w1) if w1_baseline is not None else 0,
        )
    )
    if snapshot is not None and not skip_diff:
        failures.extend(gate_additive_diff(snapshot, schema))
    failures.extend(gate_leak_scan(schema))

    stats = {
        "core": len(core_actual),
        "phase2": len(phase2_actual),
        "phase2_missing": len(phase2_baseline - phase2_actual),
        "phase3": len(phase3_actual),
        "phase3_missing": len(declared_phase3 - phase3_actual),
        "phase4_declared": len(declared_phase4),
        "phase4_present": len(phase4_actual),
        "phase4_missing": sorted(declared_phase4 - phase4_actual, key=operation_sort_key),
        "w1_declared": len(declared_w1),
        "w1_present": len(w1_actual),
        "w1_missing": sorted(declared_w1 - w1_actual, key=operation_sort_key),
        "w2_declared": len(declared_w2),
        "w2_present": len(w2_actual),
        "b3_declared": len(declared_b3),
        "b3_present": len(b3_actual),
        "operations": len(operations),
    }
    return failures, stats


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    Returns:
        argparse.ArgumentParser: 解析器。
    """
    parser = argparse.ArgumentParser(
        prog="validate_openapi.py",
        description="对外接口基线门禁（G1-G5，失败退出码非 0）",
    )
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help="用当前文档覆盖 docs/api/openapi.snapshot.json（PR 必须贴 diff）",
    )
    parser.add_argument(
        "--skip-diff",
        action="store_true",
        help="跳过 G4（仅 hotfix/* 分支允许，且会在日志中告警）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="阶段四/W1「已登记未实现」也判红（W2 起 CI 使用；本地开发默认只报告）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果（供 CI 解析）",
    )
    parser.add_argument(
        "--no-write-openapi",
        action="store_true",
        help="不写出 docs/api/openapi.json（默认会写出，供构建产物归档）",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="负样本自检：注入删除端点/改 method/收窄 enum 三类负样本（命中 = 退出码 1）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数。

    Returns:
        int: 0 通过；1 门禁失败；2 环境问题。
    """
    args = _build_parser().parse_args(argv)

    if args.self_test:
        # 自检在内存里合成 schema，不构建真实 OpenAPI、不写任何文件、不碰快照
        return run_self_test()

    docs_dir = REPO_ROOT / "docs" / "api"
    core_path = docs_dir / "baseline-core-30.json"
    phase2_path = docs_dir / "baseline-phase2-38.json"
    # 阶段三基线（插件域 PL1-PL14 + 任务域 WF1/WF2，共 16 条；B17 起逐个落地，B19 收口）
    phase3_path = docs_dir / "baseline-phase3-16.json"
    # 阶段四基线（会话 MC1-MC4 / 记忆 MM1-MM4 / 租户数据 TD1-TD2，共 10 条；T4-01.3 登记）
    phase4_path = docs_dir / "baseline-phase4-10.json"
    # W1 增量基线（W1-A1 任务统计 / W1-B1 能力推荐，共 2 条；T01 登记）
    w1_path = docs_dir / "baseline-w1-2.json"
    # 矩阵第 2 项增量基线（计划编辑 / 计划确认，共 2 条；2026-11-06 登记）
    w2_path = docs_dir / "baseline-w2-2.json"
    # 矩阵第 3 项增量基线（MCP server 列表/登记/删除/探测，共 4 条；2026-11-06 登记）
    b3_path = docs_dir / "baseline-b3-4.json"
    snapshot_path = docs_dir / "openapi.snapshot.json"
    openapi_path = docs_dir / "openapi.json"

    try:
        schema = build_schema()
    except Exception as exc:  # 脚本入口，任何导入/构建失败都属环境问题
        print(f"[FAIL] 无法构建 OpenAPI 文档: {type(exc).__name__}: {exc}")
        return 2

    docs_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_write_openapi:
        openapi_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if args.update_snapshot:
        snapshot_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"[OK]   已更新快照: {snapshot_path.relative_to(REPO_ROOT)}（PR 描述必须贴出 diff）")

    core_baseline = load_baseline(core_path)
    phase2_baseline = load_baseline(phase2_path)
    phase3_baseline = load_baseline(phase3_path)
    phase4_baseline = load_baseline(phase4_path)
    w1_baseline = load_baseline(w1_path)
    w2_baseline = load_baseline(w2_path)
    b3_baseline = load_baseline(b3_path)
    if not core_baseline:
        print(f"[FAIL] 核心基线文件缺失或为空: {core_path.relative_to(REPO_ROOT)}")
        return 2
    if not phase4_baseline:
        # 阶段四基线缺失必须立刻红（不是"跳过"）：它现在是 core 扣除项的输入，
        # 缺了会把 10 个端点算进 core → 报一堆看不懂的 G1 多出端点。
        print(f"[FAIL] 阶段四基线文件缺失或为空: {phase4_path.relative_to(REPO_ROOT)}")
        return 2
    if not w1_baseline:
        # 同 phase4：W1 基线缺失必须立刻红——缺了会把 2 个端点算进 core，
        # 且 G7 的登记数纪律失效。
        print(f"[FAIL] W1 基线文件缺失或为空: {w1_path.relative_to(REPO_ROOT)}")
        return 2
    if not w2_baseline:
        # 矩阵第 2 项基线缺失同样立刻红：缺了会把这 2 个计划端点算进 core → G1 多出。
        print(f"[FAIL] 计划端点基线文件缺失或为空: {w2_path.relative_to(REPO_ROOT)}")
        return 2
    if not b3_baseline:
        # 矩阵第 3 项基线同口径：缺了会把这 4 个 MCP 端点算进 core → G1 多出，
        # 且 G9 的登记数纪律失效。
        print(f"[FAIL] MCP 端点基线文件缺失或为空: {b3_path.relative_to(REPO_ROOT)}")
        return 2
    snapshot: dict[str, Any] | None = None
    if snapshot_path.is_file() and not args.update_snapshot:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    if args.skip_diff:
        print("[WARN] 已跳过 G4 加性 diff —— 仅允许在 hotfix/* 分支使用，请在变更单注明")

    failures, stats = collect_failures(
        schema,
        core_baseline=core_baseline,
        phase2_baseline=phase2_baseline,
        phase3_baseline=phase3_baseline,
        phase4_baseline=phase4_baseline,
        w1_baseline=w1_baseline,
        w2_baseline=w2_baseline,
        b3_baseline=b3_baseline,
        snapshot=snapshot,
        skip_diff=args.skip_diff,
        strict_phase4=args.strict,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "passed": not failures,
                    "stats": {
                        **stats,
                        "phase4_missing": _format_pending(stats["phase4_missing"]),
                        "w1_missing": _format_pending(stats["w1_missing"]),
                        "w2_declared": stats["w2_declared"],
                        "w2_present": stats["w2_present"],
                        "b3_declared": stats["b3_declared"],
                        "b3_present": stats["b3_present"],
                    },
                    "failures": [{"gate": item.gate, "detail": item.detail} for item in failures],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"[INFO] 端点统计: core={stats['core']} "
            f"phase2={stats['phase2']} "
            f"phase3={stats['phase3']} "
            f"phase4_declared={stats['phase4_declared']} "
            f"phase4_present={stats['phase4_present']} "
            f"w1_declared={stats['w1_declared']} "
            f"w1_present={stats['w1_present']} "
            f"w2_declared={stats['w2_declared']} "
            f"w2_present={stats['w2_present']} "
            f"b3_declared={stats['b3_declared']} "
            f"b3_present={stats['b3_present']}"
            f"（未实现：phase2 {stats['phase2_missing']} / "
            f"phase3 {stats['phase3_missing']} / phase4 {len(stats['phase4_missing'])} / "
            f"w1 {len(stats['w1_missing'])}）"
        )
        pending = [("阶段四", stats["phase4_missing"]), ("W1", stats["w1_missing"])]
        for label, items in pending:
            if items:
                # 只报告、不判红：这一批的口径是「登记先于实现」（见文件头 G6/G7 说明）
                strict_hint = (
                    "（--strict 下将判红）"
                    if args.strict
                    else "（默认不判红，CI 自 W2 起用 --strict）"
                )
                print(
                    f"[PENDING] {label}已登记未实现 {len(items)} 条{strict_hint}: "
                    f"{_format_pending(items)}"
                )
        for failure in failures:
            print(failure.render())
        if not failures:
            print("[OK]   G1-G9 全部通过")

    return 0 if not failures else 1


if __name__ == "__main__":  # pragma: no cover - 进程入口
    sys.exit(main())
