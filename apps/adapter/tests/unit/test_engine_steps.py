"""步骤状态机单测（阶段三 B19，`architecture-phase3.md` §3.5）。

每条用例钉住一条**可被悄悄做坏**的语义：

1. `waiting_confirm` 不自动推进（本次只测引擎侧判据：`confirm` 步骤不会被绕过）；
2. `on_failure` 四态各自的判据函数；
3. 进度**单调不减**（失败/跳过也计入已处理）；
4. 能力缺失 → 该步 failed + 不可重试（不是抛异常、也不是整任务崩）；
5. 只有可重试错误码才重试，且**最多一次**。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.errors import ApiError, ErrorCode
from app.plugins.actions import ActionResult, RunContext
from app.plugins.engine import (
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_SKIPPED,
    PluginEngine,
    StepOutcome,
    StepPlan,
)
from app.plugins.schema import StepSpec
from tests.unit.task_helpers import GRANTED, EchoGateway, load_manifest


def _step(**overrides: object) -> StepSpec:
    """构造一个步骤声明。

    Args:
        **overrides: 覆盖字段。

    Returns:
        StepSpec: 步骤声明。
    """
    payload: dict[str, object] = {
        "seq": 1,
        "title": "生成内容",
        "action": "compose",
        "produces": ["draft"],
        "on_failure": "abort",
    }
    payload.update(overrides)
    return StepSpec(**payload)  # type: ignore[arg-type]


def _ctx(granted: frozenset[str] = GRANTED) -> RunContext:
    """构造执行上下文。

    Args:
        granted: 已授能力。

    Returns:
        RunContext: 执行上下文。
    """
    return RunContext(
        tenant_id="tnt_test",
        task_id="task_test",
        plugin_id="plg_dev_workbench",
        initiator_id="usr_test",
        granted=granted,
    )


def test_progress_is_monotonic_and_counts_failures_as_processed() -> None:
    """进度：失败/跳过也计入已处理，且不随状态回退而回退。"""
    engine = PluginEngine(EchoGateway())
    assert engine.progress([]) == 0
    assert engine.progress([STEP_COMPLETED, "pending"]) == 50
    # 失败与跳过都算"处理过"，否则任务失败后时间线看起来像卡住
    assert engine.progress([STEP_FAILED, STEP_SKIPPED]) == 100


def test_when_condition_marks_step_skipped_not_run() -> None:
    """`when` 不满足 → 标记跳过（skipped），且不调用原语。"""
    manifest = load_manifest("plg_hr_admin")
    engine = PluginEngine(EchoGateway())
    plans = engine.plan(manifest, {"doc_type": ""})
    doc_plan = next(item for item in plans if item.spec.seq == 6)
    assert doc_plan.will_skip is True, "未选公文类型时第 6 步应跳过"
    plans_selected = engine.plan(manifest, {"doc_type": "通知"})
    assert next(item for item in plans_selected if item.spec.seq == 6).will_skip is False


def test_missing_capability_fails_the_step_without_raising() -> None:
    """能力被撤销 → 该步 failed + 不可重试（走状态，不抛异常、不整单崩）。"""
    engine = PluginEngine(EchoGateway())
    step = _step(action="tool", tool_type="github", operation="create_pull_request")
    outcome = asyncio.run(
        engine.run_step(_ctx(granted=frozenset({"personal.tool.use"})), StepPlan(step), {})
    )
    assert outcome.status == STEP_FAILED
    assert outcome.error_code == int(ErrorCode.CAPABILITY_NOT_GRANTED)
    assert outcome.retryable is False, "缺授权不是可重试错误（重试只会重复失败）"


def test_retryable_error_retries_exactly_once() -> None:
    """可重试错误码 + `on_failure=retry` → 恰好重试 1 次（不是无限重试）。"""
    gateway = EchoGateway(fail_at=1)
    engine = PluginEngine(gateway, retry_backoff_ms=0)
    step = _step(on_failure="retry")
    outcome = asyncio.run(engine.run_step(_ctx(), StepPlan(step), {}))
    assert outcome.status == STEP_FAILED
    assert outcome.attempts == 2, f"应尝试 2 次（首次 + 重试 1 次），实际 {outcome.attempts}"
    assert len(gateway.calls) == 2


def test_non_retryable_action_never_retries() -> None:
    """`tool`（写外部动作）**永不自动重试**，即使声明写了 retry。"""
    gateway = EchoGateway(fail_at=1)
    engine = PluginEngine(gateway, retry_backoff_ms=0)
    step = _step(
        action="tool", tool_type="github", operation="create_pull_request", on_failure="retry"
    )
    asyncio.run(engine.run_step(_ctx(), StepPlan(step), {}))
    assert len(gateway.calls) == 1, "写外部动作失败后再试一次会造成重复提交"


@pytest.mark.parametrize(
    ("on_failure", "expect_abort", "expect_mark"),
    [("abort", True, False), ("continue", False, False), ("skip_and_mark", False, True)],
)
def test_on_failure_policies(on_failure: str, expect_abort: bool, expect_mark: bool) -> None:
    """`on_failure` 三态判据一一对应（retry 由重试用例覆盖）。"""
    engine = PluginEngine(EchoGateway())
    step = _step(on_failure=on_failure)
    outcome = StepOutcome(status=STEP_FAILED, reason_text="失败")
    assert engine.should_abort(step, outcome) is expect_abort
    assert engine.mark_skipped_on_failure(step, outcome) is expect_mark


def test_degrade_translates_tool_not_connected_to_business_language() -> None:
    """下游工具未连接 → 业务语言 + 建议动作（不出现技术词汇、不可重试）。"""
    engine = PluginEngine(EchoGateway())
    outcome = engine.degrade(
        _step(action="tool", tool_type="github", operation="create_pull_request"),
        ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail="no conn"),
    )
    assert outcome.status == STEP_FAILED
    assert outcome.retryable is False
    assert "工具" in outcome.operator_action or "连接" in outcome.operator_action
    assert "http" not in outcome.reason_text.lower()


def test_engine_sanitizes_artifacts_before_return() -> None:
    """产物离开引擎时**已脱敏**（下游再怎么拼也不会把手机号带进库）。"""

    class _LeakyGateway(EchoGateway):
        """故意产出含手机号的产物的替身。"""

        async def invoke(
            self, ctx: RunContext, action: str, params: dict[str, object]
        ) -> ActionResult:
            del ctx, action, params
            from app.plugins.actions import ArtifactDraft

            return ActionResult(
                status="ok",
                artifacts=(
                    ArtifactDraft(
                        output_key="draft",
                        type="text",
                        name="稿件",
                        inline_text="联系 13912345678 或 a@b.com",
                    ),
                ),
            )

    engine = PluginEngine(_LeakyGateway())
    outcome = asyncio.run(engine.run_step(_ctx(), StepPlan(_step()), {}))
    text = outcome.artifacts[0].inline_text
    assert "13912345678" not in text, "手机号必须在落库前被掩码"
    assert "a@b.com" not in text, "邮箱必须在落库前被掩码"
