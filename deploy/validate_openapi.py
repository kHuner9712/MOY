#!/usr/bin/env python
"""OpenAPI 端点基线门禁（阶段二 D-09，架构文档 2.2 第 3 条）。

五道门禁，**任一失败即退出码非 0**，并明确指出缺失/变更的 `method + path`：

- **G1 核心基线**：`core_actual` 与 `baseline-core-30.json` **精确集合相等**
  （新增/删除/改路径都红）。
- **G2 新域基线**：已实现的新域端点必须全部登记在 `baseline-phase2-38.json` /
  `baseline-phase3-16.json`；未实现部分由 `--allow-missing-phase2` 放宽
  （**阶段三自 B21 起无放宽参数**）。
- **G3 计数**：`len(core) == 30`；未放宽时 `len(phase2) == 38`；
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

**阶段四为什么允许「登记了但还没实现」（G6）**：阶段四的 10 个端点（MC/MM/TD）
要跨 T4-02~T4-05 四周落地，如果登记当天就让门禁红，唯一"解法"就是把基线删掉——
那正好把「新增端点必须登记」这条纪律废掉。所以这一批的口径是：**登记侧严格**
（`declared` 必须精确 10，多一条少一条都红；未登记的端点仍然立刻红），
**实现侧可见**（`present` 与 `missing` 每次打印），收口时切 `--strict`。

**阶段三为什么没有 `--allow-missing-phase3`（B21 收口决定）**：
B19 起 phase3 已 16/16 全部落地，"已登记但未实现"这个中间态不再存在。
留一个放宽开关，等于给"删掉端点让 CI 变绿"留了一条合法通道——**门禁的放宽参数
一旦常态化，红与绿就不再说明任何事**。所以本脚本只保留阶段二（历史批次）的放宽，
阶段三一律严格；确需临时放宽请在 PR 里显式删除端点并登记缺陷，而不是加参数。

用法：

```bash
python deploy/validate_openapi.py                        # 严格模式（阶段四/W1 实现侧不判红）
python deploy/validate_openapi.py --strict               # 阶段四/W1 missing>0 亦判红（W2 起 CI 用）
python deploy/validate_openapi.py --allow-missing-phase2 # 仅阶段二历史批次可用
python deploy/validate_openapi.py --update-snapshot      # 端点变更后更新快照（PR 需贴 diff）
```

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
    *,
    allow_missing: bool,
    hint: str = "（可用 --allow-missing-phase2）",
) -> list[GateFailure]:
    """G2：新域端点必须全部登记在对应基线文件里。

    阶段三调用时请传 `allow_missing=False`，并换掉 `hint`——阶段三**没有**
    放宽参数，提示里出现"可用 --allow-missing-phaseX"会把人引向一条不存在的路。

    Args:
        actual: 当前实现的新域端点（`全部端点 ∩ 已登记集合`）。
        declared: 基线登记的端点。
        allow_missing: 是否允许基线中存在尚未实现的端点。
        hint: 缺失时附在文案后的处置提示。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    missing = declared - actual
    extra = actual - declared
    if extra:
        failures.append(
            GateFailure(
                "G2", f"新域端点未登记（禁止先写端点后补基线）: {_format_operations(extra)}"
            )
        )
    if missing and not allow_missing:
        failures.append(GateFailure("G2", f"新域端点未实现: {_format_operations(missing)}{hint}"))
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


def gate_counts(
    core_count: int,
    phase2_count: int,
    phase3_count: int = 0,
    phase4_present: int = 0,
    w1_present: int = 0,
    *,
    allow_missing: bool,
    phase4_declared: int = PHASE4_BASELINE_COUNT,
    w1_declared: int = W1_BASELINE_COUNT,
) -> list[GateFailure]:
    """G3：端点数门禁（五套基线：core=30 / phase2=38 / phase3=16 / phase4=10 / w1=2）。

    阶段三**恒严格**：`phase3_count` 必须精确等于 16。B21 收口后不再提供
    放宽参数——"已登记但未实现"的中间态已经不存在，留着开关只会让门禁变哑。

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
        allow_missing: 是否允许阶段二端点未实现（仅阶段二历史批次可用）。
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
    if not allow_missing and phase2_count != PHASE2_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G3",
                f"新域端点数量必须为 {PHASE2_BASELINE_COUNT}，实际 {phase2_count}",
            )
        )
    if allow_missing and phase2_count > PHASE2_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G3",
                f"新域端点数量超出冻结上限 {PHASE2_BASELINE_COUNT}，实际 {phase2_count}",
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
    snapshot: dict[str, Any] | None = None,
    allow_missing_phase2: bool = False,
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
        snapshot: 冻结快照（None 表示跳过 G4）。
        allow_missing_phase2: 是否允许阶段二端点未实现（仅历史批次可用）。
        skip_diff: 是否跳过 G4（仅 hotfix 分支允许）。
        strict_phase4: 阶段四/W1「已登记未实现」是否判红（W2 起 CI 口径）。

    Returns:
        tuple[list[GateFailure], dict[str, Any]]: 失败清单与统计信息。
    """
    declared_phase3 = phase3_baseline or set()
    declared_phase4 = phase4_baseline or set()
    declared_w1 = w1_baseline or set()
    operations = collect_operations(schema)
    phase2_actual = operations & phase2_baseline
    phase3_actual = operations & declared_phase3
    phase4_actual = operations & declared_phase4
    w1_actual = operations & declared_w1
    # 阶段四与 W1 登记过的端点必须先扣掉，否则它们会被算进 core → G1 精确相等立刻误报
    core_actual = operations - phase2_baseline - declared_phase3 - declared_phase4 - declared_w1

    failures: list[GateFailure] = []
    failures.extend(gate_core_baseline(core_actual, core_baseline))
    failures.extend(
        gate_phase2_baseline(phase2_actual, phase2_baseline, allow_missing=allow_missing_phase2)
    )
    failures.extend(
        gate_phase2_baseline(
            phase3_actual,
            declared_phase3,
            allow_missing=False,
            hint="（阶段三无放宽参数：要么把端点补回来，要么从基线撤销登记并登记缺陷）",
        )
    )
    if phase4_baseline is not None:
        # 显式传 None = 调用方声明"本次不参与阶段四判定"（合成 schema 的单元用例）；
        # CLI 与仓库级用例一律传真实基线，登记数纪律由它们守住。
        failures.extend(gate_phase4_baseline(phase4_actual, declared_phase4, strict=strict_phase4))
    if w1_baseline is not None:
        # 同 phase4：显式传 None = 本次不参与 W1 判定；仓库级用例一律传真实基线。
        failures.extend(gate_w1_baseline(w1_actual, declared_w1, strict=strict_phase4))
    failures.extend(
        gate_counts(
            len(core_actual),
            len(phase2_actual),
            len(phase3_actual),
            len(phase4_actual),
            len(w1_actual),
            allow_missing=allow_missing_phase2,
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
        "--allow-missing-phase2",
        action="store_true",
        help="允许 baseline-phase2-38.json 中尚未实现的端点（仅阶段二历史批次可用）",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数。

    Returns:
        int: 0 通过；1 门禁失败；2 环境问题。
    """
    args = _build_parser().parse_args(argv)

    docs_dir = REPO_ROOT / "docs" / "api"
    core_path = docs_dir / "baseline-core-30.json"
    phase2_path = docs_dir / "baseline-phase2-38.json"
    # 阶段三基线（插件域 PL1-PL14 + 任务域 WF1/WF2，共 16 条；B17 起逐个落地，B19 收口）
    phase3_path = docs_dir / "baseline-phase3-16.json"
    # 阶段四基线（会话 MC1-MC4 / 记忆 MM1-MM4 / 租户数据 TD1-TD2，共 10 条；T4-01.3 登记）
    phase4_path = docs_dir / "baseline-phase4-10.json"
    # W1 增量基线（W1-A1 任务统计 / W1-B1 能力推荐，共 2 条；T01 登记）
    w1_path = docs_dir / "baseline-w1-2.json"
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
        snapshot=snapshot,
        allow_missing_phase2=args.allow_missing_phase2,
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
            f"w1_present={stats['w1_present']}"
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
            print("[OK]   G1-G7 全部通过")

    return 0 if not failures else 1


if __name__ == "__main__":  # pragma: no cover - 进程入口
    sys.exit(main())
