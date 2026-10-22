"""插件声明加载器单测（阶段三 B16，`architecture-phase3.md` §2.1 / §10 G-26）。

**为什么这个文件几乎全是负样本**：声明加载是"契约入口"，
它真正需要被证明的能力是**拒绝**——一份坏声明若被放进来，后面所有环节
（市场展示、授权抽屉、步骤执行）都会带着错误契约跑。所以本文件的主体是
"各种坏声明必须被挡在哪一层"，并逐条断言**失败阶段**（不是只断言"失败了"），
因为阶段决定了运维该改哪里（`schema` 改结构、`capability` 改能力、
`banned_term` 改文案）。

覆盖的负样本（对齐 B16 验收清单）：缺步骤段 / 版本 `abc` / 序号 `1,3,4` /
名称含「RAG」/ 多字段（`entry_point`）/ `token` 键 / 能力不在枚举 / 工具操作未登记 /
对外动作缺确认 / 不可重试原语用了 retry / 同版本重复注册（且原条目不变）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.plugins.actions import ACTION_WHITELIST, CAPABILITY_KEYS
from app.plugins.loader import (
    STAGE_ACTION,
    STAGE_BANNED_TERM,
    STAGE_CAPABILITY,
    STAGE_CONFIRM,
    STAGE_DUPLICATE,
    STAGE_FORBIDDEN_KEY,
    STAGE_IO,
    STAGE_RETRY,
    STAGE_SCHEMA,
    STAGE_TOOL,
    PluginRegistry,
    default_manifests_dir,
    manifest_digest,
    version_tuple,
)
from app.plugins.schema import find_banned_terms, find_forbidden_keys

pytestmark = pytest.mark.unit

#: 内置 6 个插件标识（3 标杆 + 3 轻量）
EXPECTED_IDS = (
    "plg_content_ops",
    "plg_dev_workbench",
    "plg_hr_admin",
    "plg_mail_draft",
    "plg_meeting_minutes",
    "plg_weekly_report",
)


def _baseline() -> dict[str, Any]:
    """取一份真实声明作为变异基座（保证样本贴近生产形态）。

    Returns:
        dict[str, Any]: 声明原文的深拷贝。
    """
    path = default_manifests_dir() / "plg_weekly_report.json"
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
# 正向：内置 6 份声明必须全部可加载
# ===========================================================================
def test_bundled_manifests_all_load() -> None:
    """内置 6 份声明全部通过校验，且插件标识与预期一致。"""
    registry = PluginRegistry()
    failures = registry.load_all()
    assert [item.render() for item in failures] == []
    assert tuple(registry.plugin_ids()) == EXPECTED_IDS


def test_manifest_digest_is_stable_and_order_insensitive() -> None:
    """摘要对键顺序不敏感、对内容敏感（写库与 CI 必须用同一口径）。"""
    payload = _baseline()
    reordered = {key: payload[key] for key in reversed(list(payload))}
    assert manifest_digest(payload) == manifest_digest(reordered)

    changed = dict(payload, summary="换了一句说明")
    assert manifest_digest(payload) != manifest_digest(changed)
    assert len(manifest_digest(payload)) == 64


def test_registry_latest_uses_numeric_version_order() -> None:
    """`latest()` 走数值比较：`1.10.0` 要大于 `1.9.0`（字符串比较会判错）。"""
    registry = PluginRegistry()
    for version in ("1.9.0", "1.10.0", "1.2.0"):
        payload = dict(_baseline(), version=version)
        assert registry.load_manifest(payload, source=f"v{version}") is None
    assert registry.latest("plg_weekly_report") == "1.10.0"
    assert registry.versions("plg_weekly_report") == ["1.2.0", "1.9.0", "1.10.0"]
    assert version_tuple("1.10.0") > version_tuple("1.9.0")


def test_registry_reports_missing_directory_as_failure(tmp_path: Path) -> None:
    """声明目录不存在要留档失败（反例护栏：绝不"找不到就通过"）。"""
    registry = PluginRegistry()
    failures = registry.load_all(tmp_path / "not_exists")
    assert len(failures) == 1
    assert failures[0].stage == STAGE_IO


# ===========================================================================
# 负样本：形态
# ===========================================================================
def test_missing_steps_segment_is_rejected() -> None:
    """缺步骤段 → `schema`。"""
    payload = _baseline()
    payload.pop("steps")
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_SCHEMA


def test_invalid_version_is_rejected() -> None:
    """版本写成 `abc` → `schema`。"""
    failure = _load(dict(_baseline(), version="abc"))
    assert failure is not None
    assert failure.stage == STAGE_SCHEMA
    assert "版本号" in failure.detail


def test_non_contiguous_step_seq_is_rejected() -> None:
    """步骤序号 `1,3,4` → `schema`（PRD P0-P3-01 验收：不连续不加载）。"""
    payload = _baseline()
    payload["steps"][1]["seq"] = 3
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_SCHEMA
    assert "序号" in failure.detail


def test_step_depending_on_unknown_output_is_rejected() -> None:
    """`needs` 依赖前序未产出的键 → `schema`（参数装配不能悬空）。"""
    payload = _baseline()
    payload["steps"][1]["needs"] = ["not_exists_output"]
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_SCHEMA
    assert "前序未产出" in failure.detail


def test_extra_field_is_rejected() -> None:
    """多出 `entry_point`（可执行载荷）→ `forbidden_key`（PRD B-2 红线）。"""
    failure = _load(dict(_baseline(), entry_point="main.py"))
    assert failure is not None
    assert failure.stage == STAGE_FORBIDDEN_KEY
    assert "entry_point" in failure.detail


def test_token_key_is_rejected() -> None:
    """声明里出现 `token` 键 → `forbidden_key`（凭据零字段，§3.6）。"""
    payload = _baseline()
    payload["settings_schema"][0]["token"] = "abc"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_FORBIDDEN_KEY
    assert "token" in failure.detail


# ===========================================================================
# 负样本：能力 / 原语 / 工具
# ===========================================================================
def test_unknown_capability_is_rejected() -> None:
    """能力不在 6 项枚举内 → `capability`。"""
    payload = _baseline()
    payload["capabilities"][0]["key"] = "model.invoke"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_CAPABILITY
    assert "model.invoke" in failure.detail


def test_unknown_action_is_rejected() -> None:
    """原语不在 6 项白名单内 → `action`。"""
    payload = _baseline()
    payload["steps"][1]["action"] = "execute_shell"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_ACTION


def test_unregistered_tool_operation_is_rejected() -> None:
    """工具操作未在 `tool_registry.yaml` 登记 → `tool`。"""
    payload = _baseline()
    for step in payload["steps"]:
        if step.get("action") == "tool":
            step["operation"] = "delete_repo"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_TOOL
    assert "delete_repo" in failure.detail


def test_unregistered_tool_type_is_rejected() -> None:
    """工具类型未登记 → `tool`。"""
    payload = _baseline()
    for step in payload["steps"]:
        if step.get("action") == "tool":
            step["tool_type"] = "jira"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_TOOL


def test_tool_alternative_must_be_registered() -> None:
    """`tool_alternatives` 同样受校验（替代项不能成为绕过口）。"""
    payload = _baseline()
    for step in payload["steps"]:
        if step.get("action") == "tool":
            step["tool_alternatives"] = [{"tool_type": "jira", "operation": "create_issue"}]
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_TOOL


# ===========================================================================
# 负样本：文案 / 人工接管 / 重试
# ===========================================================================
def test_banned_term_in_name_is_rejected() -> None:
    """名称含「RAG」→ `banned_term`（AC-06 无技术暴露）。"""
    failure = _load(dict(_baseline(), summary="RAG 助手"))
    assert failure is not None
    assert failure.stage == STAGE_BANNED_TERM
    assert "RAG" in failure.reason


def test_banned_term_in_step_title_is_rejected() -> None:
    """步骤标题含「编排」→ `banned_term`（步骤标题是用户可见文案）。"""
    payload = _baseline()
    payload["steps"][0]["title"] = "编排任务"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_BANNED_TERM
    assert "steps[1].title" in failure.detail


def test_outbound_tool_step_requires_confirm() -> None:
    """对外发送类工具步骤缺 `confirm` → `confirm`（防"未经确认就发出去"）。"""
    payload = _baseline()
    for step in payload["steps"]:
        if step.get("action") == "tool":
            step.pop("confirm", None)
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_CONFIRM


def test_retry_on_tool_step_is_rejected() -> None:
    """工具步骤声明 `retry` → `retry`（写外部动作不允许自动重试）。"""
    payload = _baseline()
    for step in payload["steps"]:
        if step.get("action") == "tool":
            step["on_failure"] = "retry"
    failure = _load(payload)
    assert failure is not None
    assert failure.stage == STAGE_RETRY


# ===========================================================================
# 负样本：版本不可变
# ===========================================================================
def test_duplicate_registration_is_rejected_and_keeps_original() -> None:
    """同 `(plugin_id, version)` 注册两次 → `duplicate`，且原条目**字段不变**。"""
    registry = PluginRegistry()
    original = _baseline()
    assert registry.load_manifest(original, source="first.json") is None
    before = registry.get(str(original["plugin_id"]), str(original["version"]))
    assert before is not None

    modified = dict(original, summary="被篡改的说明")
    failure = registry.load_manifest(modified, source="second.json")
    assert failure is not None
    assert failure.stage == STAGE_DUPLICATE

    after = registry.get(str(original["plugin_id"]), str(original["version"]))
    assert after is not None
    assert after.summary == before.summary, "重复注册不得覆盖已注册的字段"
    assert len(registry) == 1


def test_same_plugin_new_version_is_allowed() -> None:
    """同插件新版本允许注册（版本不可变 ≠ 不能升级）。"""
    registry = PluginRegistry()
    base = _baseline()
    assert registry.load_manifest(base, source="1.0.0") is None
    assert registry.load_manifest(dict(base, version="1.0.1"), source="1.0.1") is None
    assert registry.versions(str(base["plugin_id"])) == ["1.0.0", "1.0.1"]


# ===========================================================================
# 工具函数
# ===========================================================================
def test_find_banned_terms_uses_word_boundary_for_ascii() -> None:
    """ASCII 禁词按词边界匹配：`spring`/`share` 不该被判违规。

    这是阶段二踩过的坑（短词按子串匹配会把正常英文误判成违规，
    最后逼得人把门禁关掉）——用样例把这条口径钉住。
    """
    assert find_banned_terms("spring share shape") == []
    assert "PR" in find_banned_terms("合并请求 PR 已创建")
    assert "sha" in find_banned_terms("校验 sha 值")
    assert "编排" in find_banned_terms("任务编排")


def test_find_forbidden_keys_reports_path() -> None:
    """禁用键扫描要给出路径，便于直接定位到声明里的位置。"""
    payload = {"steps": [{"seq": 1, "api_key": "x"}]}
    assert find_forbidden_keys(payload) == ["steps[0].api_key"]


def test_action_and_capability_enums_are_closed() -> None:
    """能力与原语枚举必须仍是 6 项（扩张要走架构变更，不能在代码里悄悄加）。"""
    assert len(ACTION_WHITELIST) == 6
    assert len(CAPABILITY_KEYS) == 6
    assert set(ACTION_WHITELIST) == {
        "understand",
        "compose",
        "retrieve",
        "tool",
        "render_file",
        "summarize",
    }
