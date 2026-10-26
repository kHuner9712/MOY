"""合规规则注册表与加载期拒载测试（P1-10：`guards` 声明化）。

钉住四件事（漏掉任何一条都会留下"看起来验过了"的假护栏）：

1. **未注册 rule 拒载**：`guards[].rule` 不在 `app.plugins.guards` 注册表
   里 → `STAGE_GUARD`（不能"不认识就跳过"）；
2. **产出受管键却漏声明拒载**：插件产出 `offer` 却不声明 `offer_amount`
   → `STAGE_GUARD`（强制覆盖——漏声明的表现是"看起来全绿"）；
3. **params 非法拒载**：未知键 / 关掉判据的布尔项 → `STAGE_GUARD`；
4. **三规则 `required_output_keys` 就位**：`offer` / `schedule` / `screening`
   各有且仅有对应规则强制覆盖。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.plugins.guards import (
    OFFER_OUTPUT_KEY,
    SCHEDULE_OUTPUT_KEY,
    SCREENING_OUTPUT_KEY,
    all_rule_names,
    required_rules_for,
    resolve_guard,
)
from app.plugins.loader import STAGE_GUARD, PluginRegistry, default_manifests_dir
from tests.unit.task_helpers import load_manifest

pytestmark = pytest.mark.unit


def _baseline() -> dict[str, Any]:
    """取一份真实声明作为变异基座（保证样本贴近生产形态）。

    Returns:
        dict[str, Any]: 声明原文（含 guards 的 plg_hr_admin）。
    """
    path = default_manifests_dir() / "plg_hr_admin.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load(payload: dict[str, Any]) -> Any:
    """把一份声明交给注册表校验。

    Args:
        payload: 声明原文。

    Returns:
        Any: `LoadFailure | None`。
    """
    return PluginRegistry().load_manifest(payload, source="test.json")


# ===========================================================================
# 1. 未注册 rule 拒载
# ===========================================================================
def test_unregistered_guard_rule_is_rejected() -> None:
    """`guards[].rule` 未注册 → `guard` 阶段拒载（不能静默跳过）。"""
    payload = _baseline()
    payload["guards"][0]["rule"] = "not_a_real_rule"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_GUARD
    assert "not_a_real_rule" in failure.detail


# ===========================================================================
# 2. 产出受管键却漏声明拒载（强制覆盖）
# ===========================================================================
def test_producing_offer_without_guard_declaration_is_rejected() -> None:
    """产出 `offer` 却不声明 `offer_amount` → 拒载（漏声明 = 假全绿）。"""
    payload = _baseline()
    payload["guards"] = [g for g in payload["guards"] if g["output_key"] != "offer"]
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_GUARD
    assert "offer" in failure.detail


def test_producing_screening_without_guard_declaration_is_rejected() -> None:
    """产出 `screening` 却不声明 `screening_risk` → 拒载。"""
    payload = _baseline()
    payload["guards"] = [g for g in payload["guards"] if g["output_key"] != "screening"]
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_GUARD
    assert "screening" in failure.detail


# ===========================================================================
# 3. params 非法拒载
# ===========================================================================
def test_unknown_guard_param_is_rejected() -> None:
    """未知参数键 → 拒载（多写一个键说明声明作者在试图改判据）。"""
    payload = _baseline()
    payload["guards"][0]["params"]["backdoor"] = True
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_GUARD
    assert "backdoor" in failure.detail


def test_guard_param_that_disables_criterion_is_rejected() -> None:
    """把判据关掉的布尔项（`forbid_past=false`）→ 拒载。"""
    path = default_manifests_dir() / "plg_content_ops.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["guards"][0]["params"]["forbid_past"] = False
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_GUARD
    assert "forbid_past" in failure.detail


def test_invalid_context_window_is_rejected() -> None:
    """数值参数越界（`context_window=0`）→ 拒载。"""
    payload = _baseline()
    payload["guards"][0]["params"]["context_window"] = 0
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_GUARD
    assert "context_window" in failure.detail


# ===========================================================================
# 4. 三规则 required_output_keys 就位
# ===========================================================================
def test_three_rules_have_expected_required_output_keys() -> None:
    """三规则的强制覆盖产物键一一对应（改了对应关系 = 拆掉强制覆盖）。"""
    assert required_rules_for(OFFER_OUTPUT_KEY) == ("offer_amount",)
    assert required_rules_for(SCHEDULE_OUTPUT_KEY) == ("schedule_timeline",)
    assert required_rules_for(SCREENING_OUTPUT_KEY) == ("screening_risk",)


def test_all_three_rules_are_registered() -> None:
    """三规则都已注册且 `required_output_keys` 非空。"""
    names = all_rule_names()
    for expected in ("offer_amount", "schedule_timeline", "screening_risk"):
        assert expected in names, f"合规规则 {expected} 未注册"
        rule = resolve_guard(expected)
        assert rule is not None
        assert rule.required_output_keys, f"{expected} 未声明强制覆盖的产物键"


def test_bundled_hr_and_content_manifests_declare_required_guards() -> None:
    """真实声明里，受管产物键都带上了对应规则（数据落盘自证）。"""
    hr = load_manifest("plg_hr_admin")
    hr_pairs = {(g.output_key, g.rule) for g in hr.guards}
    assert (OFFER_OUTPUT_KEY, "offer_amount") in hr_pairs
    assert (SCREENING_OUTPUT_KEY, "screening_risk") in hr_pairs

    content = load_manifest("plg_content_ops")
    content_pairs = {(g.output_key, g.rule) for g in content.guards}
    assert (SCHEDULE_OUTPUT_KEY, "schedule_timeline") in content_pairs
