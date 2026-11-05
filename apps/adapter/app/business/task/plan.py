"""任务执行计划（矩阵第 2 项）· 计划投影与判定。

**为什么计划不新建表**：计划就是 `plugin_step` 骨架 + `PluginManifest` 声明的投影。
用户能编辑的只有"某一步是否纳入本次执行"，落库仍是既有 `plugin_task_step.status`
（`skipped` = 未纳入）。这样：

- 确认前后**同一份数据**，不需要在两个地方同步（避免"计划表说 5 步、步骤表跑 4 步"）；
- 断点恢复、幂等、审计全部复用既有步骤状态机；
- 没有新增表 = 没有新增 RLS 策略面。

**必做步骤不可取消**（`skippable=False`）：`seq=1` 是入口步骤；任何"后续步骤
`needs` 的产物"的产出步骤也不能取消——否则确认后必然在运行期失败，
用户会以为"是系统坏了"而不是"是我取消错了"。

**副作用边界**：`waiting_plan` 不在 `runner._LIVE_TASK_STATUSES` 里，`advance` 的
CAS 拿不到它，因此**确认前零执行**。本模块只做只读投影，不执行任何步骤。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.repositories.task.records import StepRecord, TaskRecord
from app.plugins.engine import STEP_SKIPPED, TASK_CANCELLED, TASK_WAITING_PLAN, StepPlan
from app.plugins.schema import PluginManifest, StepSpec
from app.schemas.task import TaskPlan, TaskPlanStep

logger = get_logger(__name__)

#: 计划超时后的取消原因（业务语言，逐字进 `error_message`）
PLAN_EXPIRED_MESSAGE: str = "计划已过期，未执行"

#: 用户主动放弃（取消整个任务）时的原因
PLAN_ABANDONED_MESSAGE: str = "用户已放弃本次执行计划"

#: 用户取消勾选某步时的步骤说明
PLAN_DESELECTED_NOTE: str = "未纳入本次计划"


def plan_enabled(settings: Settings, requested: bool | None) -> bool:
    """解析"本次请求是否走计划模式"。

    - `None` → 跟随服务端开关（默认关，存量行为逐字不变）；
    - `False` → 明确不走计划；
    - `True` → 要求走计划；服务端开关关闭时**不允许**（否则开关就形同虚设）。

    Args:
        settings: 配置。
        requested: 请求里的 `plan_first`。

    Returns:
        bool: 是否走计划模式。
    """
    if requested is None:
        return settings.task_plan_enabled
    return bool(requested)


def plan_expires_at(settings: Settings, *, now: datetime | None = None) -> str:
    """计算计划确认截止时间（ISO 8601，UTC，`Z` 结尾）。

    Args:
        settings: 配置（读 `task_plan_timeout_seconds`）。
        now: 基准时间（测试注入）。

    Returns:
        str: 截止时间。
    """
    base = (now or datetime.now(UTC)).astimezone(UTC)
    moment = base + timedelta(seconds=settings.task_plan_timeout_seconds)
    return moment.isoformat().replace("+00:00", "Z")


def is_plan_expired(value: str, *, now: datetime | None = None) -> bool:
    """计划是否已过确认截止时间。

    Args:
        value: `plan_expires_at`（空串表示无截止时间 → 不判过期）。
        now: 基准时间。

    Returns:
        bool: 已过期返回 True。
    """
    if not value:
        return False
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    base = (now or datetime.now(UTC)).astimezone(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment < base


def step_skippable(manifest: PluginManifest, spec: StepSpec) -> bool:
    """判断某步是否允许用户取消勾选。

    Args:
        manifest: 插件声明。
        spec: 步骤声明。

    Returns:
        bool: 允许取消返回 True。
    """
    if spec.seq == 1:
        return False
    produced = set(spec.produces or ())
    if not produced:
        return True
    for other in manifest.steps:
        if other.seq <= spec.seq:
            continue
        if produced & set(other.needs or ()):
            return False
    return True


def build_plan(
    record: TaskRecord,
    manifest: PluginManifest,
    steps: list[StepRecord],
    plans: list[StepPlan],
) -> TaskPlan:
    """把步骤骨架 + 声明投影成前端可读的结构化计划。

    Args:
        record: 任务行（提供版本、截止时间、确认时间）。
        manifest: 插件声明（`produces/needs/tool_*` 的唯一来源）。
        steps: 已落库的步骤骨架（提供 `selected` 与取消说明）。
        plans: `engine.plan()` 的结果（提供 `when` 条件不满足的提示）。

    Returns:
        TaskPlan: 计划契约。
    """
    by_seq = {step.seq: step for step in steps}
    will_skip = {item.spec.seq: item for item in plans if item.will_skip}
    items: list[TaskPlanStep] = []
    for spec in manifest.steps:
        step = by_seq.get(spec.seq)
        selected = step.status != STEP_SKIPPED if step is not None else True
        note = ""
        if spec.seq in will_skip:
            note = will_skip[spec.seq].skip_reason or "当前条件不满足，将跳过"
        elif step is not None and step.status == STEP_SKIPPED:
            note = step.reason_text or PLAN_DESELECTED_NOTE
        elif spec.confirm:
            note = "执行前需你确认"
        items.append(
            TaskPlanStep(
                seq=spec.seq,
                title=spec.title,
                action=spec.action,
                tool_type=spec.tool_type or None,
                tool_operation=spec.operation or None,
                expected_artifacts=list(spec.produces or ()),
                needs=list(spec.needs or ()),
                skippable=step_skippable(manifest, spec),
                selected=selected,
                note=note,
            )
        )
    return TaskPlan(
        task_id=record.task_id,
        plugin_id=record.plugin_id,
        version=record.plan_version,
        expires_at=record.plan_expires_at,
        confirmed_at=record.plan_confirmed_at or None,
        steps=items,
    )


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    """一次计划编辑的校验结果（未落库）。

    Attributes:
        seqs: 本次请求涉及的步骤序号。
        deselect: 需要置 `skipped` 的步骤。
        reselect: 需要恢复 `pending` 的步骤。
    """

    seqs: frozenset[int]
    deselect: frozenset[int]
    reselect: frozenset[int]


def validate_selection(
    manifest: PluginManifest,
    steps: list[StepRecord],
    wanted: dict[int, bool],
) -> SelectionDecision:
    """校验并归一"勾选/取消勾选"请求（不落库）。

    Args:
        manifest: 插件声明。
        steps: 已落库步骤骨架。
        wanted: 请求里的 `seq -> selected`。

    Returns:
        SelectionDecision: 需要写入的步骤集合。

    Raises:
        ValueError: 序号不存在，或取消了不可取消的步骤（调用方转 20001）。
    """
    specs = {spec.seq: spec for spec in manifest.steps}
    existing = {step.seq for step in steps}
    unknown = sorted(set(wanted) - existing)
    if unknown:
        raise ValueError(f"未知步骤序号: {', '.join(str(item) for item in unknown)}")
    blocked = sorted(
        seq
        for seq, selected in wanted.items()
        if not selected and not step_skippable(manifest, specs[seq])
    )
    if blocked:
        raise ValueError(f"这些步骤不可取消: {', '.join(str(item) for item in blocked)}")
    deselect = {
        seq
        for seq, selected in wanted.items()
        if not selected and any(step.seq == seq and step.status != STEP_SKIPPED for step in steps)
    }
    reselect = {
        seq
        for seq, selected in wanted.items()
        if selected and any(step.seq == seq and step.status == STEP_SKIPPED for step in steps)
    }
    return SelectionDecision(
        seqs=frozenset(wanted),
        deselect=frozenset(deselect),
        reselect=frozenset(reselect),
    )


def is_waiting_plan(record: TaskRecord) -> bool:
    """任务当前是否停在计划确认态。

    Args:
        record: 任务行。

    Returns:
        bool: 是则 True。
    """
    return record.status == TASK_WAITING_PLAN


async def expire_plan(tasks: Any, record: TaskRecord) -> bool:
    """把一张过期的计划任务置为 `cancelled`（**幂等、条件写**）。

    条件写同时校验"仍是 `waiting_plan`"与"计划版本未变"：
    - 用户刚确认 → 状态已变，条件不满足 → 不改（不会把正在跑的任务取消）；
    - 用户刚编辑 → 版本 +1 且截止时间已刷新，条件不满足 → 不改。

    `cancel_requested` **保持 False**：这是系统超时清理，不是用户取消，
    完成率统计的分母口径不会被它改变（用户取消才算 cancelled 分子）。

    Args:
        tasks: 任务仓储（协议见 `TaskStore`）。
        record: 过期计划任务行。

    Returns:
        bool: 确实写入了取消态返回 True。
    """
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    updated = await tasks.update_task(
        record.tenant_id,
        record.task_id,
        status=TASK_CANCELLED,
        finished_at=now,
        error_message=PLAN_EXPIRED_MESSAGE,
        expected_status=TASK_WAITING_PLAN,
        expected_plan_version=record.plan_version,
    )
    if updated is None:
        return False
    logger.info(
        "plugin_task_plan_expired",
        tenant_id=record.tenant_id,
        task_id=record.task_id,
        plan_version=record.plan_version,
    )
    return True
