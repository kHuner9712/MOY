#!/usr/bin/env python
"""OpenAPI 端点基线门禁（阶段二 D-09，架构文档 2.2 第 3 条）。

五道门禁，**任一失败即退出码非 0**，并明确指出缺失/变更的 `method + path`：

| 门禁 | 规则 |
| --- | --- |
| G1 核心基线 | `core_actual` 与 `baseline-core-30.json` **精确集合相等**（新增/删除/改路径都红） |
| G2 新域基线 | 已实现的新域端点必须全部登记在 `baseline-phase2-38.json`；未实现部分由 `--allow-missing-phase2` 放宽 |
| G3 计数 | `len(core) == 30`；未放宽时 `len(phase2) == 38` |
| G4 加性 diff | 与 `openapi.snapshot.json` 比对：允许新增属性/新增枚举值；禁止删属性、改类型、`required` 变长、enum 收窄、新增必填请求字段、删除既有响应码 |
| G5 泄漏扫描 | 对外文档出现底座/基础设施术语即红（保留 CI 原有 4 个禁词并扩充） |

用法：

```bash
python deploy/validate_openapi.py                        # 严格模式（阶段二收尾后）
python deploy/validate_openapi.py --allow-missing-phase2 # 现阶段（B02-B10 逐步补齐）
python deploy/validate_openapi.py --update-snapshot      # 端点变更后更新快照（PR 需贴 diff）
```

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

#: 阶段三插件域端点基线数量（PL1-PL13 + WF1）
PHASE3_BASELINE_COUNT: Final[int] = 14

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

    return create_app().openapi()  # type: ignore[no-any-return]


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
    return ", ".join(f"{method} {path}" for method, path in sorted(operations, key=operation_sort_key))


def operation_sort_key(operation: tuple[str, str]) -> tuple[str, str]:
    """端点排序键（先路径后方法，输出稳定）。"""
    return operation[1], operation[0]


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
                "出现未登记的核心端点（新增端点必须登记到基线文件）: "
                f"{_format_operations(extra)}",
            )
        )
    return failures


def gate_phase2_baseline(
    actual: set[tuple[str, str]],
    declared: set[tuple[str, str]],
    *,
    allow_missing: bool,
) -> list[GateFailure]:
    """G2：新域端点必须全部登记在 `baseline-phase2-38.json`。

    Args:
        actual: 当前实现的新域端点（`全部端点 ∩ 已登记集合`）。
        declared: 基线登记的端点。
        allow_missing: 是否允许基线中存在尚未实现的端点。

    Returns:
        list[GateFailure]: 失败明细。
    """
    failures: list[GateFailure] = []
    missing = declared - actual
    extra = actual - declared
    if extra:
        failures.append(
            GateFailure("G2", f"新域端点未登记（禁止先写端点后补基线）: {_format_operations(extra)}")
        )
    if missing and not allow_missing:
        failures.append(
            GateFailure("G2", f"新域端点未实现: {_format_operations(missing)}（可用 --allow-missing-phase2）")
        )
    return failures


def gate_counts(
    core_count: int,
    phase2_count: int,
    phase3_count: int = 0,
    *,
    allow_missing: bool,
    allow_missing_phase3: bool = True,
) -> list[GateFailure]:
    """G3：端点数门禁（三套基线：core=30 / phase2=38 / phase3=14）。

    Args:
        core_count: 核心端点实际数量。
        phase2_count: 阶段二新域端点实际数量。
        phase3_count: 阶段三插件域端点实际数量。
        allow_missing: 是否允许阶段二端点未实现。
        allow_missing_phase3: 是否允许阶段三端点未实现（B17-B20 开发期）。

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
    if not allow_missing_phase3 and phase3_count != PHASE3_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G3",
                f"插件域端点数量必须为 {PHASE3_BASELINE_COUNT}，实际 {phase3_count}",
            )
        )
    if allow_missing_phase3 and phase3_count > PHASE3_BASELINE_COUNT:
        failures.append(
            GateFailure(
                "G3",
                f"插件域端点数量超出冻结上限 {PHASE3_BASELINE_COUNT}，实际 {phase3_count}",
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
            violations.append(f"{path}: 结构类型变化（{type(old).__name__} → {type(new).__name__}）")
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


def gate_additive_diff(
    snapshot: dict[str, Any], current: dict[str, Any]
) -> list[GateFailure]:
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
                        failures.append(
                            GateFailure("G4", f"{label}: 删除既有错误码响应 {status}")
                        )

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
                    for violation in diff_schema(
                        old_request, new_request, f"{label} request"
                    ):
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
    snapshot: dict[str, Any] | None = None,
    allow_missing_phase2: bool = False,
    allow_missing_phase3: bool = True,
    skip_diff: bool = False,
) -> tuple[list[GateFailure], dict[str, Any]]:
    """执行五道门禁并汇总结果。

    Args:
        schema: 当前 OpenAPI 文档。
        core_baseline: 核心基线端点。
        phase2_baseline: 阶段二基线端点。
        phase3_baseline: 阶段三插件域基线端点（None 视为空集）。
        snapshot: 冻结快照（None 表示跳过 G4）。
        allow_missing_phase2: 是否允许阶段二端点未实现。
        allow_missing_phase3: 是否允许阶段三端点未实现。
        skip_diff: 是否跳过 G4（仅 hotfix 分支允许）。

    Returns:
        tuple[list[GateFailure], dict[str, Any]]: 失败清单与统计信息。
    """
    declared_phase3 = phase3_baseline or set()
    operations = collect_operations(schema)
    phase2_actual = operations & phase2_baseline
    phase3_actual = operations & declared_phase3
    core_actual = operations - phase2_baseline - declared_phase3

    failures: list[GateFailure] = []
    failures.extend(gate_core_baseline(core_actual, core_baseline))
    failures.extend(
        gate_phase2_baseline(phase2_actual, phase2_baseline, allow_missing=allow_missing_phase2)
    )
    failures.extend(
        gate_phase2_baseline(phase3_actual, declared_phase3, allow_missing=allow_missing_phase3)
    )
    failures.extend(
        gate_counts(
            len(core_actual),
            len(phase2_actual),
            len(phase3_actual),
            allow_missing=allow_missing_phase2,
            allow_missing_phase3=allow_missing_phase3,
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
        help="允许 baseline-phase2-38.json 中尚未实现的端点（B02-B10 期间 CI 使用）",
    )
    parser.add_argument(
        "--allow-missing-phase3",
        action="store_true",
        help=(
            "允许 baseline-phase3-14.json 中尚未实现的端点（B17-B20 期间 CI 使用）；"
            "B21 收口后必须去掉，届时插件域 14 条全部落地"
        ),
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
    # 阶段三插件域基线（B17 起逐步补齐至 14 条，B21 收口）
    phase3_path = docs_dir / "baseline-phase3-14.json"
    snapshot_path = docs_dir / "openapi.snapshot.json"
    openapi_path = docs_dir / "openapi.json"

    try:
        schema = build_schema()
    except Exception as exc:  # noqa: BLE001 - 脚本入口，任何导入/构建失败都属环境问题
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
    if not core_baseline:
        print(f"[FAIL] 核心基线文件缺失或为空: {core_path.relative_to(REPO_ROOT)}")
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
        snapshot=snapshot,
        allow_missing_phase2=args.allow_missing_phase2,
        allow_missing_phase3=args.allow_missing_phase3,
        skip_diff=args.skip_diff,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "passed": not failures,
                    "stats": stats,
                    "failures": [{"gate": item.gate, "detail": item.detail} for item in failures],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"[INFO] 端点统计: core={stats['core']} "
            f"phase2={stats['phase2']}/{PHASE2_BASELINE_COUNT} "
            f"（未实现 {stats['phase2_missing']}） "
            f"phase3={stats['phase3']}/{PHASE3_BASELINE_COUNT} "
            f"（未实现 {stats['phase3_missing']}）"
        )
        for failure in failures:
            print(failure.render())
        if not failures:
            print("[OK]   G1-G5 全部通过")

    return 0 if not failures else 1


if __name__ == "__main__":  # pragma: no cover - 进程入口
    sys.exit(main())
