"""端点基线门禁测试（阶段二 B02，架构文档 2.2 / D-09）。

验收要点（逐条对应架构文档 9.2 B02「完成后可验证」）：

1. 当前 30 个核心端点全部通过 G1，且集合与 `docs/api/baseline-core-30.json` **完全一致**；
2. 故意删一个端点 → 门禁失败且打印缺失路径；
3. 故意改响应字段类型 → G4 报 type changed；
4. `--allow-missing-phase2` 期间 CI 绿（新域端点按批次补齐，不允许写假端点）。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

# P0 红线用例（G-24，测试方案 §7.1）：P0 判据⑤ —— 既有 30 端点契约被破坏
# 映射与实跑见 docs/qa-phase2-w2-r2-report.md §3.1
pytestmark = [pytest.mark.integration, pytest.mark.p0]

#: 仓库根目录（apps/adapter/tests/integration → parents[4]）
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

#: 门禁脚本路径
VALIDATOR_PATH = REPO_ROOT / "deploy" / "validate_openapi.py"

#: 基线文件路径
CORE_BASELINE_PATH = REPO_ROOT / "docs" / "api" / "baseline-core-30.json"
PHASE2_BASELINE_PATH = REPO_ROOT / "docs" / "api" / "baseline-phase2-38.json"
SNAPSHOT_PATH = REPO_ROOT / "docs" / "api" / "openapi.snapshot.json"


def _load_validator() -> ModuleType:
    """按路径加载门禁脚本（`deploy/` 不是 Python 包）。

    Returns:
        ModuleType: 已加载的模块。
    """
    if "validate_openapi" in sys.modules:
        return sys.modules["validate_openapi"]
    spec = importlib.util.spec_from_file_location("validate_openapi", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_openapi"] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _schema_with(paths: dict[str, Any]) -> dict[str, Any]:
    """构造最小 OpenAPI 文档。

    Args:
        paths: paths 字段内容。

    Returns:
        dict[str, Any]: OpenAPI 文档。
    """
    return {"openapi": "3.1.0", "paths": paths}


def _operation(
    response_schema: dict[str, Any], *, required: list[str] | None = None
) -> dict[str, Any]:
    """构造一个带 JSON 响应的操作。

    Args:
        response_schema: 响应 schema。
        required: 响应必填字段。

    Returns:
        dict[str, Any]: 操作对象。
    """
    schema = dict(response_schema)
    if required is not None:
        schema["required"] = required
    return {
        "responses": {
            "200": {"content": {"application/json": {"schema": schema}}},
            "401": {"description": "未鉴权"},
        }
    }


# ===========================================================================
# 1. 真实基线一致性
# ===========================================================================
def test_core_baseline_file_has_exactly_30_endpoints() -> None:
    """核心基线文件必须是 30 条（冻结）。"""
    baseline = validator.load_baseline(CORE_BASELINE_PATH)
    assert len(baseline) == validator.CORE_BASELINE_COUNT == 30


def test_actual_core_endpoints_equal_frozen_baseline() -> None:
    """当前实现的 `/api/v1` 端点集合与冻结基线**完全一致**。"""
    schema = validator.build_schema()
    operations = validator.collect_operations(schema)
    phase2 = validator.load_baseline(PHASE2_BASELINE_PATH)
    core_actual = operations - phase2
    baseline = validator.load_baseline(CORE_BASELINE_PATH)
    assert core_actual == baseline


def test_phase2_baseline_only_lists_implemented_endpoints() -> None:
    """新域基线登记的端点必须是**真实已实现**的（禁止为凑数写假端点）。"""
    schema = validator.build_schema()
    operations = validator.collect_operations(schema)
    declared = validator.load_baseline(PHASE2_BASELINE_PATH)
    assert declared <= operations
    assert len(declared) <= validator.PHASE2_BASELINE_COUNT


def test_snapshot_exists_and_covers_core_endpoints() -> None:
    """快照文件存在并覆盖全部核心端点（G4 的比较对象）。"""
    import json

    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    baseline = validator.load_baseline(CORE_BASELINE_PATH)
    for _method, path in baseline:
        assert path in snapshot["paths"], f"快照缺少路径 {path}"


def test_repository_passes_gates_with_allow_missing_phase2() -> None:
    """当前仓库状态在 `--allow-missing-phase2` 下 G1-G5 全绿。"""
    failures, stats = validator.collect_failures(
        validator.build_schema(),
        core_baseline=validator.load_baseline(CORE_BASELINE_PATH),
        phase2_baseline=validator.load_baseline(PHASE2_BASELINE_PATH),
        snapshot=_load_snapshot(),
        allow_missing_phase2=True,
        skip_diff=False,
    )
    assert failures == [], [item.render() for item in failures]
    assert stats["core"] == 30


def _load_snapshot() -> dict[str, Any]:
    """读取冻结快照。

    Returns:
        dict[str, Any]: 快照文档。
    """
    import json

    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_cli_returns_zero_with_allow_missing_phase2() -> None:
    """CLI 在 `--allow-missing-phase2` 下退出码为 0（CI 现阶段口径）。"""
    assert validator.main(["--allow-missing-phase2", "--no-write-openapi"]) == 0


def test_cli_returns_nonzero_in_strict_mode_now() -> None:
    """严格模式在 B02 阶段必须非 0（新域 38 条未实现）。"""
    assert validator.main(["--no-write-openapi"]) == 1


# ===========================================================================
# 2. G1：缺失 / 多出
# ===========================================================================
def test_g1_reports_missing_endpoint_path() -> None:
    """删除一个端点 → 门禁失败且明确指出缺失的 method + path。"""
    baseline = validator.load_baseline(CORE_BASELINE_PATH)
    victim = sorted(baseline, key=validator.operation_sort_key)[0]
    actual = baseline - {victim}
    failures = validator.gate_core_baseline(actual, baseline)
    assert failures, "删除端点后 G1 必须失败"
    rendered = " ".join(item.render() for item in failures)
    assert victim[0] in rendered and victim[1] in rendered


def test_g1_reports_extra_endpoint_path() -> None:
    """出现未登记端点 → G1 失败（提示必须登记基线）。"""
    baseline = validator.load_baseline(CORE_BASELINE_PATH)
    actual = set(baseline) | {("POST", "/api/v1/rogue")}
    failures = validator.gate_core_baseline(actual, baseline)
    assert failures
    assert "/api/v1/rogue" in failures[0].detail


# ===========================================================================
# 3. G2 / G3：新域基线与计数
# ===========================================================================
def test_g2_tolerates_missing_when_allowed() -> None:
    """`--allow-missing-phase2` 期间，未实现的新域端点不阻断 CI。"""
    declared = {("PUT", "/api/v1/knowledge/bases/{base_id}/credential-binding")}
    assert validator.gate_phase2_baseline(set(), declared, allow_missing=True) == []
    assert validator.gate_phase2_baseline(set(), declared, allow_missing=False)


def test_g2_rejects_unregistered_new_endpoint() -> None:
    """已实现但未登记的新域端点 → G2 失败。"""
    actual = {("PUT", "/api/v1/knowledge/bases/{base_id}/credential-binding")}
    failures = validator.gate_phase2_baseline(actual, set(), allow_missing=True)
    assert failures
    assert "credential-binding" in failures[0].detail


def test_g3_core_count_is_frozen() -> None:
    """核心端点数量漂移即失败。"""
    assert validator.gate_counts(30, 0, allow_missing=True) == []
    assert validator.gate_counts(29, 0, allow_missing=True)


def test_g3_phase2_upper_bound_in_allow_mode() -> None:
    """放宽模式下新域端点不得超过冻结上限 38。"""
    assert validator.gate_counts(30, 39, allow_missing=True)


# ===========================================================================
# 4. G4：加性 diff
# ===========================================================================
def test_g4_detects_type_change() -> None:
    """响应字段类型被改 → 报 type changed。"""
    old = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {"type": "object", "properties": {"visibility": {"type": "string"}}}
                )
            }
        }
    )
    new = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {"type": "object", "properties": {"visibility": {"type": "boolean"}}}
                )
            }
        }
    )
    failures = validator.gate_additive_diff(old, new)
    assert failures
    rendered = " ".join(item.detail for item in failures)
    assert "类型变化" in rendered and "visibility" in rendered


def test_g4_allows_added_property_and_enum_widening() -> None:
    """新增属性、新增枚举值属加性变更，必须放行。"""
    old = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {
                        "type": "object",
                        "properties": {"visibility": {"type": "string", "enum": ["organization"]}},
                    }
                )
            }
        }
    )
    new = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {
                        "type": "object",
                        "properties": {
                            "visibility": {
                                "type": "string",
                                "enum": ["organization", "restricted"],
                            },
                            "chunk_policy": {"type": "string"},
                        },
                    }
                )
            }
        }
    )
    assert validator.gate_additive_diff(old, new) == []


def test_g4_detects_removed_property_and_enum_narrowing() -> None:
    """删除属性、enum 收窄 → 必须失败。"""
    old = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {
                        "type": "object",
                        "properties": {
                            "a": {"type": "string", "enum": ["one", "two"]},
                            "b": {"type": "string"},
                        },
                    }
                )
            }
        }
    )
    new = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {
                        "type": "object",
                        "properties": {"a": {"type": "string", "enum": ["one"]}},
                    }
                )
            }
        }
    )
    details = " ".join(item.detail for item in validator.gate_additive_diff(old, new))
    assert "删除属性" in details
    assert "enum 收窄" in details


def test_g4_detects_required_growth() -> None:
    """既有响应 `required` 变长属破坏性变更。"""
    old = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {"type": "object", "properties": {"a": {"type": "string"}}}, required=["a"]
                )
            }
        }
    )
    new = _schema_with(
        {
            "/api/v1/x": {
                "get": _operation(
                    {
                        "type": "object",
                        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                    },
                    required=["a", "b"],
                )
            }
        }
    )
    details = " ".join(item.detail for item in validator.gate_additive_diff(old, new))
    assert "required 变长" in details


def test_g4_detects_deleted_error_response() -> None:
    """删除既有 4xx 响应 → 失败（前端错误处理依赖）。"""
    old = _schema_with({"/api/v1/x": {"get": _operation({"type": "object"})}})
    new_operation = _operation({"type": "object"})
    new_operation["responses"].pop("401")
    new = _schema_with({"/api/v1/x": {"get": new_operation}})
    details = " ".join(item.detail for item in validator.gate_additive_diff(old, new))
    assert "删除既有错误码响应 401" in details


# ===========================================================================
# 5. G5：泄漏扫描
# ===========================================================================
def test_g5_blocks_base_layer_terms() -> None:
    """对外文档出现底座术语即失败。"""
    schema = _schema_with({"/api/v1/x": {"get": {"summary": "调用 Dify 应用", "responses": {}}}})
    failures = validator.gate_leak_scan(schema)
    assert failures
    assert "dify" in failures[0].detail


def test_g5_passes_on_real_schema() -> None:
    """真实文档不含任何禁用术语。"""
    assert validator.gate_leak_scan(validator.build_schema()) == []
