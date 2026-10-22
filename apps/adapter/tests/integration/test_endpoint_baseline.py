"""端点基线门禁测试（阶段二 B02，架构文档 2.2 / D-09）。

验收要点（逐条对应架构文档 9.2 B02「完成后可验证」）：

1. 当前 30 个核心端点全部通过 G1，且集合与 `docs/api/baseline-core-30.json` **完全一致**；
2. 故意删一个端点 → 门禁失败且打印缺失路径；
3. 故意改响应字段类型 → G4 报 type changed；
4. `--allow-missing-phase2` 期间 CI 绿（新域端点按批次补齐，不允许写假端点）；
5. **B21 收口**：阶段三 `phase3 == 16` 精确相等，且**没有** `--allow-missing-phase3`
   这类放宽参数——删一个插件域端点必须立刻变红。
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
PHASE3_BASELINE_PATH = REPO_ROOT / "docs" / "api" / "baseline-phase3-16.json"
PHASE2_BASELINE_PATH = REPO_ROOT / "docs" / "api" / "baseline-phase2-38.json"
PHASE4_BASELINE_PATH = REPO_ROOT / "docs" / "api" / "baseline-phase4-10.json"
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
    phase3 = validator.load_baseline(PHASE3_BASELINE_PATH)
    # 阶段四登记过的端点必须先扣掉，否则会被算进 core（与 validator 的扣除口径一致）
    phase4 = validator.load_baseline(PHASE4_BASELINE_PATH)
    core_actual = operations - phase2 - phase3 - phase4
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
    """当前仓库状态在 `--allow-missing-phase2` 下 G1-G5 全绿。

    阶段三自 B21 起无放宽参数，故这里断言 `phase3 == 16`（精确值）。
    """
    failures, stats = validator.collect_failures(
        validator.build_schema(),
        core_baseline=validator.load_baseline(CORE_BASELINE_PATH),
        phase2_baseline=validator.load_baseline(PHASE2_BASELINE_PATH),
        phase3_baseline=validator.load_baseline(PHASE3_BASELINE_PATH),
        # 阶段四基线必须一并传入：它是 core 的扣除项之一，漏传会把 10 条登记
        # 端点算进 core（G1 报"多出端点"），也会让 G6 判"基线不足 10 条"。
        phase4_baseline=validator.load_baseline(PHASE4_BASELINE_PATH),
        snapshot=_load_snapshot(),
        allow_missing_phase2=True,
        skip_diff=False,
    )
    assert failures == [], [item.render() for item in failures]
    assert stats["core"] == 30
    assert stats["phase2"] == 38
    assert stats["phase3"] == 16, "阶段三端点必须精确 16 条（PL1-PL14 + WF1/WF2）"
    assert stats["phase4_declared"] == 10


def _load_snapshot() -> dict[str, Any]:
    """读取冻结快照。

    Returns:
        dict[str, Any]: 快照文档。
    """
    import json

    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_cli_returns_zero_with_allow_missing_phase2() -> None:
    """CLI 在 `--allow-missing-phase2` 下退出码为 0（阶段二历史口径）。"""
    assert validator.main(["--allow-missing-phase2", "--no-write-openapi"]) == 0


def test_cli_returns_zero_in_strict_mode_after_b10() -> None:
    """B10 收尾后严格模式必须通过（新域 38 条已全部落地）。

    历史：B02 阶段此用例断言「严格模式非 0」（当时 38 条新域端点尚未实现）。
    B10 落地最后 8 条（OPS1-OPS8）后，严格模式从「预期失败」变为「必须通过」——
    这正是该门禁的设计意图：放宽期结束，端点基线进入冻结状态。
    B21 又把阶段三的放宽参数一并删掉（phase3 16/16 已收口），所以**现在
    `--no-write-openapi` 就是全量严格模式**，不带任何容差参数。
    缺失检测能力由 `test_cli_returns_nonzero_when_endpoint_missing` 用合成 schema 兜住。
    """
    assert validator.main(["--no-write-openapi"]) == 0


def test_cli_returns_nonzero_when_endpoint_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """严格模式下**少一条新域端点**必须非 0（防止门禁被改宽松后失去信号）。"""
    schema = validator.build_schema()
    paths = dict(schema["paths"])
    victim = "/api/v1/ops/settings"
    removed = {method: value for method, value in paths[victim].items() if method != "patch"}
    if removed:
        paths[victim] = removed
    else:  # pragma: no cover - 端点被整体移除时才走到这里
        paths.pop(victim)
    monkeypatch.setattr(validator, "build_schema", lambda: {**schema, "paths": paths})
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
    assert validator.gate_counts(30, 0, 16, allow_missing=True) == []
    assert validator.gate_counts(29, 0, 16, allow_missing=True)


def test_g3_phase2_upper_bound_in_allow_mode() -> None:
    """放宽模式下新域端点不得超过冻结上限 38。"""
    assert validator.gate_counts(30, 39, 16, allow_missing=True)


def test_g3_phase3_count_is_always_strict() -> None:
    """阶段三**没有放宽模式**：少一条、多一条都必须红（B21 收口判据）。

    这条是"删端点让门禁变绿"的机械钉子：即便阶段二仍在放宽，
    `phase3_count != 16` 也必须让 G3 失败。
    """
    assert validator.gate_counts(30, 38, 15, allow_missing=True)
    assert validator.gate_counts(30, 38, 17, allow_missing=True)
    assert validator.gate_counts(30, 38, 16, allow_missing=True) == []


# ===========================================================================
# 3b. G6：阶段四「登记先于实现」口径（T4-01.3）
# ===========================================================================
def test_phase4_baseline_declares_exactly_10_endpoints() -> None:
    """阶段四基线必须精确 10 条（MC1-MC4 / MM1-MM4 / TD1-TD2）。"""
    declared = validator.load_baseline(PHASE4_BASELINE_PATH)
    assert len(declared) == validator.PHASE4_BASELINE_COUNT == 10


def test_phase4_missing_is_pending_but_not_red_by_default() -> None:
    """`declared - actual` 非空时默认只报 `[PENDING]`，不判红（实现侧宽容）。"""
    declared = validator.load_baseline(PHASE4_BASELINE_PATH)
    assert validator.gate_phase4_baseline(set(), declared, strict=False) == []


def test_phase4_missing_is_red_under_strict() -> None:
    """`--strict` 时同一条件必须判红（W2 起的 CI 口径）。"""
    declared = validator.load_baseline(PHASE4_BASELINE_PATH)
    failures = validator.gate_phase4_baseline(set(), declared, strict=True)
    assert failures and failures[0].gate == "G6"


def test_phase4_baseline_shrink_is_red() -> None:
    """把基线删成 9 条（"让 missing 变 0"的常见做法）→ G6 立刻红。"""
    declared = validator.load_baseline(PHASE4_BASELINE_PATH)
    shrunk = set(sorted(declared, key=validator.operation_sort_key)[:9])
    failures = validator.gate_phase4_baseline(shrunk, shrunk, strict=False)
    assert failures, "基线少于 10 条必须判红（删基线不算修复）"
    assert "10" in failures[0].detail


def test_unregistered_endpoint_is_red_without_strict_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未登记的端点必须**立刻**红——G6 的宽容只针对"已登记未实现"，不针对"偷偷加端点"。

    这条是整批判据里最不能松的一条：不加它，阶段四期间任何人往 `/api/v1` 里塞
    端点都能过门禁（因为 core 精确相等会被新端点打破，但有人可能顺手改基线）。
    """
    import json

    schema = validator.build_schema()
    paths = json.loads(json.dumps(schema["paths"]))
    paths["/api/v1/model/rogue"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    monkeypatch.setattr(validator, "build_schema", lambda: {**schema, "paths": paths})
    assert validator.main(["--no-write-openapi"]) == 1


#: 当前批次应已实现的阶段四端点数（**防遗忘轻门禁**）。
#:
#: T4-02 收口 → 4（MC1-MC4）；W3 记忆端点落地 → 8（+MM1-MM4）；
#: TD1/TD2 落地 → 10。改这个数字必须同时改本注释，否则下一个人不知道该不该改。
PHASE4_PRESENT_EXPECTED = 8


def test_phase4_present_matches_current_batch() -> None:
    """`phase4_present` 必须等于**当前批次**的预期值（少做两个端点当批就红）。

    为什么需要这条：`--strict` 要到终验才开，若无本断言，"少实现两个端点"
    会一路绿到最后一次评审——那时再补，排期已经来不及。
    """
    _failures, stats = validator.collect_failures(
        validator.build_schema(),
        core_baseline=validator.load_baseline(CORE_BASELINE_PATH),
        phase2_baseline=validator.load_baseline(PHASE2_BASELINE_PATH),
        phase3_baseline=validator.load_baseline(PHASE3_BASELINE_PATH),
        phase4_baseline=validator.load_baseline(PHASE4_BASELINE_PATH),
        snapshot=_load_snapshot(),
        allow_missing_phase2=False,
        skip_diff=False,
        strict_phase4=False,
    )
    assert stats["phase4_declared"] == 10
    assert stats["phase4_present"] == PHASE4_PRESENT_EXPECTED, (
        f"本批应已实现 {PHASE4_PRESENT_EXPECTED} 个阶段四端点，"
        f"实际 {stats['phase4_present']}（未实现：{stats['phase4_missing']}）"
    )


def test_strict_flag_only_changes_verdict_when_phase4_missing() -> None:
    """`--strict` 的判定只由「是否有缺失」决定，避免端点补齐后这条用例变成假阳性。"""
    _failures, stats = validator.collect_failures(
        validator.build_schema(),
        core_baseline=validator.load_baseline(CORE_BASELINE_PATH),
        phase2_baseline=validator.load_baseline(PHASE2_BASELINE_PATH),
        phase3_baseline=validator.load_baseline(PHASE3_BASELINE_PATH),
        phase4_baseline=validator.load_baseline(PHASE4_BASELINE_PATH),
        snapshot=_load_snapshot(),
        allow_missing_phase2=False,
        skip_diff=False,
        strict_phase4=False,
    )
    expected = 1 if stats["phase4_missing"] else 0
    assert validator.main(["--no-write-openapi", "--strict"]) == expected


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
