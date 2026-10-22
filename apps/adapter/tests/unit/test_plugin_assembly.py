"""三插件 6 步装配单测（阶段三 B20，`architecture-phase3.md` §7.1 / §7.2 / §7.3）。

钉住"装配"这件事的四条机械判据——它们都**不需要真跑任务**就能验：

1. 三个标杆插件各 6 步，`checklist` 项数 4 / 3 / 4（名称逐字对齐架构）；
2. **`needs` 闭合**：某步依赖的产物必须由更早的步骤产出（否则那一步永远在等空气）；
3. **执行路径 ⊆ 声明清单**：`tool` 步骤的主工具与替代项都在 `tool_registry` 内，
   且对外动作都有 `confirm`；
4. **导出格式由声明给定**：公文是 docx、排期表是 csv，插件目录里
   没有任何"自己会渲染 docx"的代码。

第 5 条是**降级矩阵四格**（能力未授权 / 企业策略关闭 / 工具未连接 / 前置插件未启用），
断言的是"用户看得懂 + 有下一步"，不是错误码本身。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import ApiError, ErrorCode
from app.plugins.actions import (
    CAPABILITY_CATALOG,
    CAPABILITY_KEYS,
    ActionResult,
    RunContext,
    declared_tool_paths,
    is_outbound_operation,
    render_file_violations,
)
from app.plugins.engine import (
    OFFER_OUTPUT_KEY,
    SCHEDULE_OUTPUT_KEY,
    STEP_FAILED,
    PluginEngine,
)
from app.plugins.schema import StepSpec
from tests.unit.task_helpers import load_manifest

#: 三个标杆插件 → 期望的核查清单项数（架构 §7.1/§7.2/§7.3）
EXPECTED_CHECKLIST: dict[str, int] = {
    "plg_dev_workbench": 4,
    "plg_content_ops": 3,
    "plg_hr_admin": 4,
}

#: 三个标杆插件 → 期望步骤数（PRD Q3-7：三插件都是 6 步）
EXPECTED_STEPS: int = 6


@pytest.mark.parametrize("plugin_id", sorted(EXPECTED_CHECKLIST))
def test_three_plugins_have_six_steps(plugin_id: str) -> None:
    """三插件各 6 步，序号 1..6 连续。"""
    manifest = load_manifest(plugin_id)
    assert len(manifest.steps) == EXPECTED_STEPS
    assert [step.seq for step in manifest.steps] == list(range(1, EXPECTED_STEPS + 1))


@pytest.mark.parametrize("plugin_id", sorted(EXPECTED_CHECKLIST))
def test_checklist_size_matches_architecture(plugin_id: str) -> None:
    """核查清单项数 = 4 / 3 / 4（用户照着核对的东西不能少）。"""
    manifest = load_manifest(plugin_id)
    assert len(manifest.checklist) == EXPECTED_CHECKLIST[plugin_id]


def test_hr_checklist_names_are_verbatim() -> None:
    """人事的核查清单名称逐字对齐架构 §7.3（改名字等于改了验收口径）。"""
    manifest = load_manifest("plg_hr_admin")
    names = [item.name for item in manifest.checklist]
    assert names == ["招聘说明", "筛选评分表", "面试提纲", "公文或录用通知"]


@pytest.mark.parametrize("plugin_id", sorted(EXPECTED_CHECKLIST))
def test_needs_are_produced_by_earlier_steps(plugin_id: str) -> None:
    """`needs` 闭合：依赖的产物必须由**更早**的步骤产出。"""
    manifest = load_manifest(plugin_id)
    produced: set[str] = set()
    for step in manifest.steps:
        for key in step.needs:
            assert key in produced, f"{plugin_id} 第 {step.seq} 步依赖了尚未产出的 {key}"
        produced.update(step.produces)


@pytest.mark.parametrize("plugin_id", sorted(EXPECTED_CHECKLIST))
def test_tool_steps_are_within_declared_paths(plugin_id: str) -> None:
    """`tool` 步骤的执行路径集合非空，且对外动作都有 `confirm`。"""
    manifest = load_manifest(plugin_id)
    for step in manifest.steps:
        if step.action != "tool":
            continue
        paths = declared_tool_paths(step)
        assert paths, f"{plugin_id} 第 {step.seq} 步是工具调用却没有路径"
        for tool_type, operation in paths:
            if is_outbound_operation(tool_type, operation):
                assert step.confirm is not None, (
                    f"{plugin_id} 第 {step.seq} 步会对外发送内容，必须先请用户确认"
                )


@pytest.mark.parametrize("plugin_id", sorted(EXPECTED_CHECKLIST))
def test_render_steps_declare_format_and_are_not_coupled_to_file_read(
    plugin_id: str,
) -> None:
    """导出步骤声明了格式，且"读个人文件"能力有真正的读文件支撑。"""
    manifest = load_manifest(plugin_id)
    assert render_file_violations(manifest) == []


def test_human_resources_document_is_docx_and_schedule_is_csv() -> None:
    """公文必须交 docx（P0-P3-23 ③）、排期表必须交 csv（§7.2 第 5 步）。"""
    hr = load_manifest("plg_hr_admin")
    document_step = next(step for step in hr.steps if "document" in step.produces)
    assert document_step.render_format == "docx"

    content = load_manifest("plg_content_ops")
    schedule_step = next(step for step in content.steps if SCHEDULE_OUTPUT_KEY in step.produces)
    assert schedule_step.render_format == "csv"


def test_hr_offer_step_produces_exactly_the_guarded_key() -> None:
    """Offer 金额三规则的作用域锚点：`offer` 只有一个产出步骤（第 5 步）。"""
    hr = load_manifest("plg_hr_admin")
    producers = [step.seq for step in hr.steps if OFFER_OUTPUT_KEY in step.produces]
    assert producers == [5]


def test_hr_conditional_document_step_is_declared_with_when() -> None:
    """公文步骤受 `doc_type` 条件控制（HR 场景互斥，§7.3 第 6 步）。"""
    hr = load_manifest("plg_hr_admin")
    document_step = next(step for step in hr.steps if "document" in step.produces)
    assert document_step.when is not None
    assert document_step.when.input == "doc_type"
    assert len(document_step.when.in_values) == 4


# ---------------------------------------------------------------------------
# 降级矩阵四格：每一格都要有「用户看得懂的原因 + 下一步做什么」
# ---------------------------------------------------------------------------
#: 四格降级：错误码 → 用户可见下一步里必须出现的关键词。
#:
#: 这条判据的意义：降级文案最容易退化成"操作失败，请重试"。用户需要知道的是
#: **缺什么、去哪补**——尤其"企业策略关闭"这格，用户自己改不了，文案必须指向管理员。
DEGRADE_MATRIX: dict[str, tuple[int, str]] = {
    "能力未授权": (int(ErrorCode.CAPABILITY_NOT_GRANTED), "授权"),
    "企业策略关闭": (int(ErrorCode.CAPABILITY_POLICY_BLOCKED), "管理员"),
    "工具未连接": (int(ErrorCode.TOOL_NOT_CONNECTED), "设置"),
    "前置插件未启用": (int(ErrorCode.PLUGIN_DEPENDENCY_MISSING), "插件页面"),
}

#: 用户可见文案里不得出现的技术词（AC-06）
TECH_WORDS: tuple[str, ...] = (
    "编排",
    "节点",
    "回滚",
    "依赖",
    "token",
    "api",
    "json",
    "schema",
    "堆栈",
    "重跑",
    "日志",
)


@pytest.mark.parametrize("cell", sorted(DEGRADE_MATRIX))
def test_degradation_matrix_cells_have_actionable_copy(cell: str) -> None:
    """四格降级各有确定的「下一步做什么」，且文案里没有技术词。"""
    code, keyword = DEGRADE_MATRIX[cell]
    engine = PluginEngine(gateway=_NeverGateway())  # type: ignore[arg-type]
    spec = StepSpec(seq=1, title="生成内容", action="compose", produces=["draft"])
    outcome = engine.degrade(spec, ApiError(code, message="这一步没有完成"))
    assert outcome.status == STEP_FAILED
    assert outcome.retryable is False, f"{cell} 不该被自动重试"
    assert keyword in outcome.operator_action, (
        f"{cell} 的下一步文案没指向「{keyword}」：{outcome.operator_action}"
    )
    lowered = outcome.operator_action.lower()
    for word in TECH_WORDS:
        assert word.lower() not in lowered, (
            f"{cell} 的文案出现了技术词「{word}」：{outcome.operator_action}"
        )


class _NeverGateway:
    """降级用例用的空网关（这些用例只调 `degrade()`，不该碰原语）。"""

    def resolve(self, action: str) -> str:
        """解析原语。

        Args:
            action: 原语名。

        Returns:
            str: 固定标识。
        """
        return f"test.{action}"

    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """不应被调用。

        Args:
            ctx: 执行上下文。
            action: 原语名。
            params: 原语参数。

        Returns:
            ActionResult: 恒抛错（用例不该走到这里）。

        Raises:
            AssertionError: 恒抛出。
        """
        del ctx, action, params
        raise AssertionError("降级用例不该调用原语")


def test_capability_catalog_covers_all_capabilities() -> None:
    """6 项能力都有「名称 / 为什么需要 / 不同意会怎样」三要素（授权抽屉要展示）。"""
    for key in CAPABILITY_KEYS:
        entry = CAPABILITY_CATALOG[key]
        assert entry["display_name"]
        assert entry["reason"]
        assert entry["denial_effect"]
        for value in entry.values():
            # 授权抽屉里的文案不得出现技术词（AC-06）
            assert "token" not in value.lower()
            assert "api" not in value.lower()
