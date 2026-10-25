"""插件任务步骤引擎（阶段三 B19，`architecture-phase3.md` §3.3 / §3.5 / D-19）。

**引擎只负责"怎么推进一步"**，不碰数据库、不碰 SQL、不 import `app.services.*`：

- 输入：声明（`PluginManifest`）+ 发起人身份（`RunContext`）+ 前序产物；
- 输出：`StepOutcome`（状态 / 产物草稿 / 失败文案）交回 `TaskService` 落库。

三条与契约强绑定的口径：

1. **步骤失败走状态不走错误码**（PRD E-9）：失败步骤的 HTTP 响应仍是 `code=0`，
   失败信息放 `steps[].failure{reason_text, retryable, operator_action}`。
   内部汇总码（见 §5.4 的 `内部` 行）**永不**出现在 A3/A4 响应里。
2. **接管点永不自动推进**（PRD P0-P3-13 验收③）：`confirm` 步骤未确认时任务置
   `waiting_confirm` 并**停住**；只有 WF1 `decision=continue|redo` 才会继续。
   引擎不设"超时自动继续"——自动继续等于用户没看就替他做了对外动作。
3. **`on_failure` 四态各有确定语义**（§3.5）：`abort` 任务失败 + 后续 skipped；
   `continue` 该步 failed 但任务继续并进 `warnings[]`；`skip_and_mark` 该步 skipped
   且在核查清单标"待人工确认"；`retry` 最多重试 1 次（仅可重试错误码）。
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.plugins import sanitizer as sanitizer_rules
from app.plugins.actions import (
    ACTION_DEFAULT_TIMEOUT,
    ACTION_RETRYABLE,
    ACTION_WHITELIST,
    RETRIEVE_CAPABILITIES,
    ActionGateway,
    ActionResult,
    ArtifactDraft,
    RunContext,
    assert_execution_path,
    capabilities_for_tool,
    capability_display_name,
    declared_tool_paths,
)
from app.plugins.renderer import RENDER_FORMATS
from app.plugins.sanitizer import (
    OFFER_AMOUNT_RANGE_PATTERN,
    OFFER_AMOUNT_UNIT_PATTERN,
    OFFER_BARE_NUMBER_PATTERN,
    ArtifactSanitizer,
    strip_amount_exemptions,
)
from app.plugins.schema import PluginManifest, StepSpec

logger = get_logger(__name__)

#: 步骤状态机（与 `plugin_task_step.status` CHECK 完全一致）
STEP_PENDING: Final[str] = "pending"
STEP_RUNNING: Final[str] = "running"
STEP_COMPLETED: Final[str] = "completed"
STEP_FAILED: Final[str] = "failed"
STEP_SKIPPED: Final[str] = "skipped"

#: 任务状态（与 `plugin_task.status` CHECK 完全一致）
TASK_QUEUED: Final[str] = "queued"
TASK_PENDING: Final[str] = "pending"
TASK_RUNNING: Final[str] = "running"
TASK_WAITING_CONFIRM: Final[str] = "waiting_confirm"
TASK_COMPLETED: Final[str] = "completed"
TASK_FAILED: Final[str] = "failed"
TASK_CANCELLED: Final[str] = "cancelled"

#: 可自动重试的失败码（§3.5：只有这五类；写外部动作的失败一律不可重试）
RETRYABLE_ERROR_CODES: Final[frozenset[int]] = frozenset({10004, 11005, 13004, 30001, 30002})

#: 失败时给用户的建议动作（业务语言，禁止出现"重跑/堆栈/日志"）
DEFAULT_OPERATOR_ACTION: Final[str] = "可以重试这一步"

#: 说明性产物（`understand` / `compose` / `summarize`）失败不影响其他步骤时的提示后缀
WARNING_TEMPLATE: Final[str] = "第 {seq} 步「{title}」没有完成，结果里会缺少这部分"

# ----------------------------------------------------------------------
# Offer 金额三规则（PRD P0-P3-23 ② / 追溯表 C-13 / 架构 §7.3 + §11 勘误 #3）
# ----------------------------------------------------------------------
#: **作用域写死**：只扫这一个产物键。`screening` 的结论枚举**合法包含**「待定」，
#: 一旦把扫描范围放到"任务级"，每份「待定」简历都会误报成 P0 缺陷——而且方向是
#: "看起来更严"，最容易被当成好事放过。所以这里不接受调用方传产物键。
OFFER_OUTPUT_KEY: Final[str] = "offer"

#: Offer 的三个金额字段（`bonus` 与 §7.3 的「股权」按同义处理，两边都要挡）。
OFFER_AMOUNT_FIELDS: Final[tuple[str, ...]] = (
    "salary",
    "probation_salary",
    "bonus",
    "equity",
)

#: 金额位置**唯一允许**的两个取值（空串 = 由人事手填；或明确的「按公司标准执行」）。
OFFER_AMOUNT_ALLOWED_VALUES: Final[tuple[str, ...]] = ("", "按公司标准执行")

#: 金额违规时给用户的建议动作（**固定文案**，且不得指向"重新发起任务"）。
#:
#: 合规问题不能靠重跑碰运气，也不该让用户为模型的错付一整单成本；
#: Offer 是可编辑草稿，就地改两个字段再导出即可。
OFFER_AMOUNT_OPERATOR_ACTION: Final[str] = "清空或改为「按公司标准执行」后重新导出"

#: 金额规则集（R1 / R2-a / R2-b + 豁免）**住 在 `sanitizer`**，此处只做引用。
#:
#: 为什么不再在这里定义一份：同一套正则要同时服务「判定」（命中即判该步失败）与
#: 「脱敏」（`sanitizer.mask_amounts` 整段遮掉）。两份实现必然漂移，而
#: **判定严 + 脱敏松** 会留下"判不出来、也没遮掉"的产物——这是合规红线。
#: 依赖方向仍是 `engine → sanitizer`（sanitizer 不反向依赖引擎），不会成环。
OFFER_AMOUNT_RANGE_SEPARATORS: Final[str] = sanitizer_rules.OFFER_AMOUNT_RANGE_SEPARATORS

#: R2：金额上下文关键词（**只在"判定"里用**——脱敏按形状遮，不需要上下文）
OFFER_AMOUNT_CONTEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"薪|工资|待遇|补贴|奖金|年终|期权|股权|月收入|总包"
)

#: R2 的上下文窗口半径（字符）
OFFER_AMOUNT_CONTEXT_WINDOW: Final[int] = 20

#: R3：金额位置的禁用表述（把金额决策甩给用户，且用户会误以为是模型给的结论）
OFFER_AMOUNT_FORBIDDEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"面议|待定|可谈|可议|根据经验|视情况|依能力|面谈|另议|按市场水平"
)

# ----------------------------------------------------------------------
# 发布排期时间校验（架构 §7.2 第 5 步）
# ----------------------------------------------------------------------
#: 排期表产物键（作用域同样写死）
SCHEDULE_OUTPUT_KEY: Final[str] = "schedule"

#: 排期表里「平台」列的候选键名（声明侧不固定表头，这里给一组常见写法）
SCHEDULE_PLATFORM_KEYS: Final[tuple[str, ...]] = ("平台", "platform", "channel")

#: 排期表里「计划时间」列的候选键名
SCHEDULE_TIME_KEYS: Final[tuple[str, ...]] = (
    "计划时间",
    "planned_at",
    "计划发布时间",
    "时间",
    "time",
)

#: 排期时间的**业务时区**（Asia/Shanghai，固定 UTC+8）。
#:
#: 为什么需要它：`strptime` 解析出的是 **naive** datetime（无时区），而
#: 无参 `datetime.now` 也给 naive 本地时间。两者相减/相比只在「进程本地时区恰好
#: 就是业务时区」时才对——容器默认 `TZ=UTC` 时，用户写的「2026-11-02 10:00」
#: 会被当成 UTC 10:00，也就是北京时间 18:00，**过去 8 小时的排期会被判成未来**，
#: 反之亦然；更糟的是把 tz-aware 的 `now`（如 `datetime.now(UTC)`）传进来会直接
#: `TypeError: can't compare offset-naive and offset-aware datetimes`。
#:
#: 口径：排期表是给人看的**北京时间**，因此解析结果一律按 `SCHEDULE_TZ` 解释，
#: 参照时间统一折算到 aware 后再比较。中国全境单一时区、无夏令时，固定偏移
#: 是安全且免依赖 `zoneinfo`（tzdata 在精简镜像里未必存在）的选择。
SCHEDULE_TZ: Final[timezone] = timezone(timedelta(hours=8))

#: 可识别的时间写法（识别不了的**不判失败**——宁可放过也不误伤用户的自由格式）
SCHEDULE_TIME_FORMATS: Final[tuple[str, ...]] = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
)

#: 排期违规时给用户的建议动作
SCHEDULE_OPERATOR_ACTION: Final[str] = "修改排期表里的计划时间后重新导出"

# ----------------------------------------------------------------------
# 简历评分表的行级质量门（PRD P0-P3-21 / QA TC-P3-HR21-01）
# ----------------------------------------------------------------------
#: 评分表产物键（作用域同样写死：只判 `screening` 这一份表）
SCREENING_OUTPUT_KEY: Final[str] = "screening"

#: 「风险」列的表头候选。**必须容忍别名**：评分表的表头由模型产出、
#: 不是声明里写死的列名（`_render_rows` 把 `prior` 里的 dict 直接当行），
#: 只认字面「风险」会在模型写 `Risk` / `风险点` 时漏判——**漏判比误判更危险**，
#: 因为它让这张表"看起来验过了"。
SCREENING_RISK_HEADERS: Final[tuple[str, ...]] = ("风险", "risk", "risks")

#: 违规时用来点名「是哪一份简历」的列（取不到就退回"第 N 行"）
SCREENING_ROW_ID_KEYS: Final[tuple[str, ...]] = ("姓名", "候选人", "name")

#: 执行路径越界时给用户的建议动作
PATH_OPERATOR_ACTION: Final[str] = "联系管理员确认这个插件可以做什么"


@dataclass(frozen=True, slots=True)
class StepPlan:
    """一步的执行计划（`when` 判定后的结果）。

    Attributes:
        spec: 声明里的步骤定义。
        will_skip: 是否因 `when` 条件不满足而跳过。
        skip_reason: 跳过原因（业务语言）。
    """

    spec: StepSpec
    will_skip: bool = False
    skip_reason: str = ""


@dataclass(slots=True)
class StepOutcome:
    """单步执行结果（引擎 → `TaskService`）。

    Attributes:
        status: `completed` / `failed` / `skipped`。
        artifacts: 产物草稿（**已脱敏**，可直接落库）。
        data: 供后续 `needs` 使用的结果（已脱敏）。
        error_code: 失败时的业务码（只进内部汇总，不进响应体）。
        reason_text: 失败原因（业务语言，直接给用户看）。
        operator_action: 建议动作（业务语言）。
        retryable: 是否属于可重试失败。
        usage_units: 本步消耗额度。
        attempts: 实际尝试次数（0 表示未执行）。
    """

    status: str = STEP_COMPLETED
    artifacts: tuple[ArtifactDraft, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    error_code: int = 0
    reason_text: str = ""
    operator_action: str = ""
    retryable: bool = False
    usage_units: int = 0
    attempts: int = 0

    @property
    def ok(self) -> bool:
        """是否成功完成。

        Returns:
            bool: `completed` 返回 True。
        """
        return self.status == STEP_COMPLETED


@dataclass(slots=True)
class TaskOutcome:
    """整任务执行结果（供 `TaskService` 汇总任务状态）。

    Attributes:
        status: 任务状态。
        warnings: 未完成但未中断的步骤提示（业务语言）。
        usage_units: 任务累计额度。
        failed_step_seq: 首个失败步骤序号（0 表示无）。
        confirm_step_seq: 待确认步骤序号（0 表示无）。
    """

    status: str = TASK_RUNNING
    warnings: list[str] = field(default_factory=list)
    usage_units: int = 0
    failed_step_seq: int = 0
    confirm_step_seq: int = 0


@dataclass(frozen=True, slots=True)
class AmountScan:
    """Offer 金额三规则的扫描结果（R1 / R2 / R3 + 字段取值）。

    Attributes:
        r1: R1（带单位金额）命中的原文片段。
        r2: R2（金额上下文内的裸数字）命中的原文片段。
        r3: R3（金额位置的禁用表述）命中的原文片段。
        fields: 取值不在白名单内的金额字段键。
    """

    r1: tuple[str, ...] = ()
    r2: tuple[str, ...] = ()
    r3: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """三条规则是否全部 0 命中且字段取值合规。

        Returns:
            bool: 全部合规返回 True。
        """
        return not (self.r1 or self.r2 or self.r3 or self.fields)

    def reasons(self) -> list[str]:
        """把命中翻译成业务语言（**不出现正则、字段名、错误码**）。

        Returns:
            list[str]: 逐条原因（空列表表示合规）。
        """
        reasons: list[str] = []
        if self.fields:
            reasons.append("金额相关的几处还没有确认，需要人事来填或选择「按公司标准执行」")
        if self.r1:
            reasons.append("录用通知里出现了具体金额数字")
        if self.r2:
            reasons.append("录用通知在薪资相关段落里出现了具体数字")
        if self.r3:
            reasons.append("录用通知在金额位置写了「面议」「待定」这类说法")
        return reasons


@dataclass(frozen=True, slots=True)
class ScheduleScan:
    """发布排期表的校验结果。

    Attributes:
        problems: 业务语言的问题清单（空表示通过）。
        checked_rows: 实际解析出计划时间的行数（0 表示没有可校验的时间）。
    """

    problems: tuple[str, ...] = ()
    checked_rows: int = 0

    @property
    def clean(self) -> bool:
        """是否通过校验。

        Returns:
            bool: 无问题返回 True。
        """
        return not self.problems


class PluginEngine:
    """步骤推进引擎（无状态）。

    Attributes:
        _gateway: 原语门面（Protocol，实现注入）。
        _sanitizer: 产物脱敏器。
        _retry_backoff_ms: 重试退避毫秒数（§3.5，最多重试 1 次）。
    """

    def __init__(
        self,
        gateway: ActionGateway,
        sanitizer: ArtifactSanitizer | None = None,
        *,
        retry_backoff_ms: int = 800,
    ) -> None:
        """初始化引擎。

        Args:
            gateway: 原语门面（`ActionGateway` Protocol 实现）。
            sanitizer: 产物脱敏器（缺省新建一个）。
            retry_backoff_ms: 重试退避毫秒数。
        """
        self._gateway = gateway
        self._sanitizer = sanitizer or ArtifactSanitizer()
        self._retry_backoff_ms = max(0, retry_backoff_ms)

    # ------------------------------------------------------------------
    # 预检（A1 建任务前）
    # ------------------------------------------------------------------
    def preflight(
        self, manifest: PluginManifest, inputs: dict[str, Any], granted: frozenset[str]
    ) -> None:
        """建任务前的声明级预检（不调下游）。

        校验顺序固定：**必填输入（20001）→ 长度（20001）→ 选项（20001）→
        数量（20001）→ 能力（16005）**。
        先查输入是有意的：参数错了让用户改输入即可，不该先弹"缺能力"把
        用户引到授权页去白跑一趟；数量也排在能力之前——"一次最多 20 份"是用户
        在上传框里就能改的事，没有理由先让他去授权。

        Args:
            manifest: 插件声明。
            inputs: 业务输入。
            granted: 已授能力集合。

        Raises:
            ApiError: 必填输入缺失/非法（20001）、数量超上限（20001）、
                缺少所需能力（16005）。
        """
        for spec in manifest.inputs:
            value = inputs.get(spec.key)
            if spec.required and _is_blank(value):
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    message=f"请填写「{spec.label}」",
                    internal_detail=f"missing input {spec.key}",
                )
            if spec.max_length is not None and isinstance(value, str):
                if len(value) > spec.max_length:
                    raise ApiError(
                        ErrorCode.PARAM_INVALID,
                        message=f"「{spec.label}」不能超过 {spec.max_length} 个字",
                        internal_detail=f"input {spec.key} too long",
                    )
            if spec.options and isinstance(value, str) and value and value not in spec.options:
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    message=f"「{spec.label}」的取值不在可选范围内",
                    internal_detail=f"input {spec.key}={value}",
                )
            if spec.type == "multi_select" and isinstance(value, list):
                unknown = [item for item in value if spec.options and item not in spec.options]
                if unknown:
                    raise ApiError(
                        ErrorCode.PARAM_INVALID,
                        message=f"「{spec.label}」的取值不在可选范围内",
                        internal_detail=f"input {spec.key} unknown={unknown}",
                    )
            # 数量上限（PRD P0-P3-21 验收②，「一次最多 20 份」）。
            # 只对列表/元组计数：单个字符串按 1 份算——用户只传一份简历时
            # 可能给的是字符串而不是列表，那不该被判成"超量"。
            if (
                spec.max_items is not None
                and isinstance(value, list | tuple)
                and len(value) > spec.max_items
            ):
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    # 「份」是 PRD 原文的用词（用户可见文案逐字对齐验收标准）
                    message=f"一次最多 {spec.max_items} 份，请分批上传",
                    internal_detail=f"input {spec.key} exceeds max_items",
                )

        declared = set(manifest.capability_keys)
        required = set(manifest.required_capability_keys)
        missing = sorted(required - granted)
        if missing:
            names = "、".join(capability_display_name(item) for item in missing)
            raise ApiError(
                ErrorCode.CAPABILITY_NOT_GRANTED,
                message="缺少所需能力，请重新授权后再试",
                data={"missing_capabilities": missing},
                internal_detail=f"ungranted: {names}",
            )
        # 声明里非必需的能力若已被撤销，不阻断（用户明确同意了必需项即可运行）
        del declared

    # ------------------------------------------------------------------
    # 计划
    # ------------------------------------------------------------------
    def plan(self, manifest: PluginManifest, inputs: dict[str, Any]) -> list[StepPlan]:
        """把声明步骤转成执行计划（按 `when` 判定跳过）。

        Args:
            manifest: 插件声明。
            inputs: 业务输入。

        Returns:
            list[StepPlan]: 计划列表（顺序即 `seq`）。
        """
        plans: list[StepPlan] = []
        for spec in manifest.steps:
            will_skip = False
            reason = ""
            if spec.when is not None:
                actual = inputs.get(spec.when.input)
                allowed = list(spec.when.in_values)
                if not isinstance(actual, str) or actual not in allowed:
                    will_skip = True
                    reason = "本次不需要这一步"
            plans.append(StepPlan(spec=spec, will_skip=will_skip, skip_reason=reason))
        return plans

    # ------------------------------------------------------------------
    # 单步执行
    # ------------------------------------------------------------------
    async def run_step(
        self, ctx: RunContext, plan: StepPlan, inputs: dict[str, Any]
    ) -> StepOutcome:
        """执行一步（含 `on_failure: retry` 的一次重试）。

        Args:
            ctx: 执行上下文（身份 + 已授能力 + 前序产物）。
            plan: 步骤计划。
            inputs: 业务输入。

        Returns:
            StepOutcome: 执行结果（不会抛异常——失败一律翻译成状态与文案）。
        """
        spec = plan.spec
        if plan.will_skip:
            return StepOutcome(status=STEP_SKIPPED, reason_text=plan.skip_reason)

        blocked = self._missing_capabilities(spec, ctx)
        if blocked:
            names = "、".join(capability_display_name(item) for item in blocked)
            return StepOutcome(
                status=STEP_FAILED,
                error_code=int(ErrorCode.CAPABILITY_NOT_GRANTED),
                reason_text=f"缺少「{names}」的授权，这一步没有执行",
                operator_action="重新授权后再试，或联系管理员",
                retryable=False,
            )

        declared = self._declaration_check(spec)
        if declared is not None:
            return declared

        params = self._build_params(spec, ctx, inputs)
        outcome = await self._invoke(ctx, spec, params)
        attempts = 1
        if (
            not outcome.ok
            and spec.on_failure == "retry"
            and outcome.retryable
            and ACTION_RETRYABLE.get(spec.action, False)
        ):
            if self._retry_backoff_ms:
                await asyncio.sleep(self._retry_backoff_ms / 1000)
            logger.info(
                "plugin_step_retry", plugin_id=ctx.plugin_id, step_seq=spec.seq, task_id=ctx.task_id
            )
            outcome = await self._invoke(ctx, spec, params)
            attempts = 2
        outcome.attempts = attempts
        return self.guard(spec, outcome)

    def _declaration_check(self, spec: StepSpec) -> StepOutcome | None:
        """运行期再查一次「原语与执行路径 ⊆ 声明清单」（防运行期篡改）。

        加载期已查过（`loader._check_actions`），但 `plugin_task.plugin_version`
        存的是**声明快照**；快照一旦被人改过，运行期就不该照着跑。

        Args:
            spec: 步骤声明。

        Returns:
            StepOutcome | None: 声明不自洽返回失败结果；否则返回 None。
        """
        try:
            assert_execution_path(spec)
        except ValueError as exc:
            logger.error(
                "plugin_step_declaration_invalid",
                step_seq=spec.seq,
                detail=str(exc),
            )
            return StepOutcome(
                status=STEP_FAILED,
                error_code=int(ErrorCode.INTERNAL_ERROR),
                reason_text="这一步的说明与实际做法不一致，已经停下",
                operator_action=PATH_OPERATOR_ACTION,
                retryable=False,
            )
        return None

    async def _invoke(self, ctx: RunContext, spec: StepSpec, params: dict[str, Any]) -> StepOutcome:
        """调用原语并把结果/异常翻译成 `StepOutcome`。

        Args:
            ctx: 执行上下文。
            spec: 步骤声明。
            params: 原语参数。

        Returns:
            StepOutcome: 执行结果。
        """
        step_ctx = RunContext(
            tenant_id=ctx.tenant_id,
            task_id=ctx.task_id,
            step_seq=spec.seq,
            plugin_id=ctx.plugin_id,
            installation_id=ctx.installation_id,
            initiator_id=ctx.initiator_id,
            trace_id=ctx.trace_id,
            granted=ctx.granted,
            settings=dict(ctx.settings),
            inputs=dict(ctx.inputs),
            outputs=dict(ctx.outputs),
            auth=ctx.auth,
        )
        try:
            result = await self._gateway.invoke(step_ctx, spec.action, params)
        except ApiError as exc:
            return self.degrade(spec, exc)
        except Exception as exc:
            logger.error(
                "plugin_step_crashed",
                plugin_id=ctx.plugin_id,
                step_seq=spec.seq,
                detail=type(exc).__name__,
            )
            return StepOutcome(
                status=STEP_FAILED,
                error_code=int(ErrorCode.INTERNAL_ERROR),
                reason_text="这一步没有完成，可以重试",
                operator_action=DEFAULT_OPERATOR_ACTION,
                retryable=False,
            )
        return self._to_outcome(result)

    def _to_outcome(self, result: ActionResult) -> StepOutcome:
        """把原语结果转成步骤结果（统一脱敏闸口）。

        Args:
            result: 原语执行结果。

        Returns:
            StepOutcome: 步骤结果。
        """
        if result.skipped:
            return StepOutcome(
                status=STEP_SKIPPED,
                reason_text=result.reason_text or "这一步跳过了",
                usage_units=result.usage_units,
            )
        if not result.ok:
            return StepOutcome(
                status=STEP_FAILED,
                artifacts=self._sanitize_artifacts(result.artifacts),
                data=dict(result.data),
                error_code=int(result.error_code or ErrorCode.INTERNAL_ERROR),
                reason_text=result.reason_text or "这一步没有完成，可以重试",
                operator_action=result.operator_action or DEFAULT_OPERATOR_ACTION,
                retryable=bool(result.retryable),
                usage_units=result.usage_units,
            )
        return StepOutcome(
            status=STEP_COMPLETED,
            artifacts=self._sanitize_artifacts(result.artifacts),
            data=dict(result.data),
            usage_units=result.usage_units,
        )

    def _sanitize_artifacts(self, drafts: tuple[ArtifactDraft, ...]) -> tuple[ArtifactDraft, ...]:
        """落库前统一脱敏（**唯一闸口**：WF2 编辑也走这里）。

        Args:
            drafts: 原语产出的产物草稿。

        Returns:
            tuple[ArtifactDraft, ...]: 脱敏后的产物草稿。
        """
        cleaned: list[ArtifactDraft] = []
        for draft in drafts:
            if draft.inline_text:
                report = self._sanitizer.sanitize(draft.inline_text)
                cleaned.append(
                    ArtifactDraft(
                        output_key=draft.output_key,
                        type=draft.type,
                        name=draft.name,
                        inline_text=report.text,
                        external_url=draft.external_url,
                        storage_key=draft.storage_key,
                        content=draft.content,
                        preview_text=self._sanitizer.mask_pii(draft.preview_text),
                        size_bytes=draft.size_bytes or len(draft.inline_text.encode("utf-8")),
                    )
                )
                continue
            cleaned.append(
                ArtifactDraft(
                    output_key=draft.output_key,
                    type=draft.type,
                    name=draft.name,
                    preview_text=self._sanitizer.mask_pii(draft.preview_text),
                    external_url=draft.external_url,
                    storage_key=draft.storage_key,
                    content=draft.content,
                    size_bytes=draft.size_bytes,
                )
            )
        return tuple(cleaned)

    def degrade(self, spec: StepSpec, error: ApiError) -> StepOutcome:
        """把下游 `ApiError` 翻译成步骤失败（业务语言 + 建议动作）。

        Args:
            spec: 步骤声明。
            error: 下游异常。

        Returns:
            StepOutcome: 失败结果（`status=failed`）。
        """
        code = int(error.code)
        retryable = code in RETRYABLE_ERROR_CODES
        operator = DEFAULT_OPERATOR_ACTION
        if code == int(ErrorCode.TOOL_NOT_CONNECTED):
            operator = "先在设置里连接这个办公工具，再重试这一步"
            retryable = False
        elif code == int(ErrorCode.TOOL_AUTH_EXPIRED):
            operator = "重新连接这个办公工具后再试"
            retryable = False
        elif code == int(ErrorCode.CAPABILITY_NOT_GRANTED):
            operator = "重新授权后再试，或联系管理员"
            retryable = False
        elif code == int(ErrorCode.CAPABILITY_POLICY_BLOCKED):
            # 企业策略关闭：用户自己改不了，必须指向"找谁开"
            operator = "请团队管理员在团队设置里开启这项能力后再试"
            retryable = False
        elif code == int(ErrorCode.PLUGIN_NOT_INSTALLED):
            operator = "先在插件页面添加这个插件，再重试这一步"
            retryable = False
        elif code == int(ErrorCode.PLUGIN_DISABLED):
            operator = "先在插件页面启用这个插件，再重试这一步"
            retryable = False
        elif code == int(ErrorCode.PLUGIN_DEPENDENCY_MISSING):
            # 「依赖」是禁词：对用户要说"需要一起使用的插件"
            operator = "先在插件页面启用它需要一起使用的插件，再重试这一步"
            retryable = False
        logger.info(
            "plugin_step_failed",
            action=spec.action,
            step_seq=spec.seq,
            code=code,
            retryable=retryable,
        )
        return StepOutcome(
            status=STEP_FAILED,
            error_code=code,
            reason_text=error.message,
            operator_action=operator,
            retryable=retryable,
        )

    # ------------------------------------------------------------------
    # 护栏（B20）：执行路径 ⊆ 声明清单 / Offer 金额三规则 / 排期时间 / 评分表风险
    # ------------------------------------------------------------------
    def guard(self, spec: StepSpec, outcome: StepOutcome) -> StepOutcome:
        """对成功完成的步骤跑四道护栏（**作用域写死，不接受调用方传参**）。

        四道护栏都只改"这一步算不算完成"，**不改任务状态机**：
        命中即该步 `failed`，后续由 `on_failure` 决定任务走向
        （Offer 步是 `continue`，排期步是 `abort`，评分表步是 `continue`）。

        可重试性按"用户能不能靠自己补好"分两档：Offer 金额、排期时间、执行路径
        越界命中时 `retryable=False`（重跑一遍还是同样的产物，白烧一次额度）；
        **评分表风险为空是 `retryable=True`**——PRD P0-P3-21 的口径是"该行判失败
        重跑"，模型换一次采样就可能补上风险，重试是有意义的动作。

        为什么护栏放在引擎而不是网关：网关只认"下游返回了什么"，护栏要认
        "**声明允许什么**"——声明是插件的契约，判据必须落在契约这一层。

        为什么作用域写死在引擎里、不接受 `output_key` 入参：护栏的"扫哪些产物"
        一旦变成调用方决定，就必然出现"某个调用点忘了传 / 传错"的漏判，而漏判的
        表现是**看起来全绿**（§7.3 与 §11 勘误 #3 的同一条理由）。

        Args:
            spec: 步骤声明。
            outcome: 步骤执行结果。

        Returns:
            StepOutcome: 原结果，或命中护栏后的失败结果。
        """
        if not outcome.ok:
            return outcome
        path_blocked = self._guard_tool_path(spec, outcome)
        if path_blocked is not None:
            return path_blocked
        for draft in outcome.artifacts:
            if draft.output_key == OFFER_OUTPUT_KEY:
                blocked = self._guard_offer(outcome, draft)
                if blocked is not None:
                    return blocked
            elif draft.output_key == SCHEDULE_OUTPUT_KEY:
                blocked = self._guard_schedule(outcome, draft)
                if blocked is not None:
                    return blocked
            elif draft.output_key == SCREENING_OUTPUT_KEY:
                blocked = self._guard_screening(outcome, draft)
                if blocked is not None:
                    return blocked
        return outcome

    def _guard_tool_path(self, spec: StepSpec, outcome: StepOutcome) -> StepOutcome | None:
        """校验实际调用的工具 ∈ 声明清单（「执行路径 ⊆ 声明清单」运行期半段）。

        Args:
            spec: 步骤声明。
            outcome: 步骤执行结果（`tool` 步骤的 `data` 带 `tool_type`/`operation`）。

        Returns:
            StepOutcome | None: 越界返回失败结果；否则返回 None。
        """
        if spec.action != "tool":
            return None
        actual_tool = str(outcome.data.get("tool_type") or "")
        actual_operation = str(outcome.data.get("operation") or "")
        if not actual_tool or not actual_operation:
            return None
        if (actual_tool, actual_operation) in declared_tool_paths(spec):
            return None
        logger.error(
            "plugin_step_path_out_of_manifest",
            plugin_id=spec.action,
            step_seq=spec.seq,
            tool=actual_tool,
            operation=actual_operation,
        )
        return StepOutcome(
            status=STEP_FAILED,
            error_code=int(ErrorCode.INTERNAL_ERROR),
            reason_text="这一步的实际做法超出了这个插件的声明范围，已经停下",
            operator_action=PATH_OPERATOR_ACTION,
            retryable=False,
            usage_units=outcome.usage_units,
        )

    def _guard_offer(self, outcome: StepOutcome, draft: ArtifactDraft) -> StepOutcome | None:
        """对 `output_key=offer` **这一个产物**跑 R1 + R2 + R3（P0-P3-23 ②）。

        Args:
            outcome: 步骤执行结果。
            draft: `offer` 产物草稿。

        Returns:
            StepOutcome | None: 违规返回失败结果；否则返回 None。
        """
        scan = scan_offer_amount(draft.inline_text)
        if scan.clean:
            return None
        logger.warning(
            "plugin_offer_amount_blocked",
            r1=len(scan.r1),
            r2=len(scan.r2),
            r3=len(scan.r3),
            fields=list(scan.fields),
        )
        reasons = scan.reasons()
        return StepOutcome(
            status=STEP_FAILED,
            # **不把违规内容交出去**：产出为空的失败，用户只会看到"没有产出 + 怎么补"，
            # 而不是一份需要自己找哪里违规的草稿。
            artifacts=(),
            data=dict(outcome.data),
            error_code=int(ErrorCode.INTERNAL_ERROR),
            reason_text="；".join(reasons) or "录用通知里出现了不该有的金额信息",
            operator_action=OFFER_AMOUNT_OPERATOR_ACTION,
            retryable=False,
            usage_units=outcome.usage_units,
        )

    def _guard_schedule(self, outcome: StepOutcome, draft: ArtifactDraft) -> StepOutcome | None:
        """校验排期表时间（过去时间 / 同平台同刻重复）。

        Args:
            outcome: 步骤执行结果。
            draft: `schedule` 产物草稿（CSV）。

        Returns:
            StepOutcome | None: 违规返回失败结果；否则返回 None。
        """
        rows = parse_schedule_rows(draft.content)
        scan = scan_schedule(rows)
        if scan.clean:
            return None
        logger.warning("plugin_schedule_blocked", problems=list(scan.problems))
        return StepOutcome(
            status=STEP_FAILED,
            artifacts=(),
            data=dict(outcome.data),
            error_code=int(ErrorCode.INTERNAL_ERROR),
            reason_text="；".join(scan.problems),
            operator_action=SCHEDULE_OPERATOR_ACTION,
            retryable=False,
            usage_units=outcome.usage_units,
        )

    def _guard_screening(self, outcome: StepOutcome, draft: ArtifactDraft) -> StepOutcome | None:
        """校验简历评分表：**每行「风险」非空**（PRD P0-P3-21 的一票否决项）。

        PRD 原文：「风险」为空判该行失败。风险这一列是"只夸不批"的唯一刹车——
        没有它，一张全绿的评分表会把不合格候选人送进面试。

        **零行不判失败**：没有简历就没有可判的行（`resumes` 是 `required=false`，
        "本次不做筛选"是合法输入），把"没行"当成"风险为空"会让没传简历的用户
        收到一条无从修改的报错。

        **整张表没有风险列同样判失败**：缺列时逐行检查会一行都不报，
        于是这张表会以"全部通过"的姿态交到用户手里——漏判比误判更危险。

        Args:
            outcome: 步骤执行结果。
            draft: `screening` 产物草稿（CSV，可能是 `content` 字节或 `inline_text`）。

        Returns:
            StepOutcome | None: 违规返回失败结果；否则返回 None。
        """
        rows = _screening_rows(draft)
        if not rows:
            logger.info("plugin_screening_skipped", reason="no_rows")
            return None
        problems = scan_screening(rows)
        if not problems:
            return None
        logger.warning("plugin_screening_blocked", problems=list(problems))
        return StepOutcome(
            status=STEP_FAILED,
            # 与 Offer/排期一致：违规产物不交出（否则用户要先自己找出哪一行不合格）
            artifacts=(),
            data=dict(outcome.data),
            error_code=int(ErrorCode.INTERNAL_ERROR),
            # PRD 说"判该行失败重跑"，所以可重试（唯一一道 retryable=True 的产物护栏）
            retryable=True,
            reason_text="；".join(problems),
            operator_action=DEFAULT_OPERATOR_ACTION,
            usage_units=outcome.usage_units,
        )

    # ------------------------------------------------------------------
    # 降级策略 / 进度 / 核查清单
    # ------------------------------------------------------------------
    @staticmethod
    def should_abort(spec: StepSpec, outcome: StepOutcome) -> bool:
        """失败后是否中止整个任务（`on_failure=abort`）。

        Args:
            spec: 步骤声明。
            outcome: 步骤结果。

        Returns:
            bool: 需要中止返回 True。
        """
        return outcome.status == STEP_FAILED and spec.on_failure == "abort"

    @staticmethod
    def mark_skipped_on_failure(spec: StepSpec, outcome: StepOutcome) -> bool:
        """失败是否按"跳过并标待人工确认"处理（`on_failure=skip_and_mark`）。

        Args:
            spec: 步骤声明。
            outcome: 步骤结果。

        Returns:
            bool: 需要标待人工确认返回 True。
        """
        return outcome.status == STEP_FAILED and spec.on_failure == "skip_and_mark"

    @staticmethod
    def warning_of(spec: StepSpec) -> str:
        """生成任务级告警文案（`on_failure=continue`）。

        Args:
            spec: 步骤声明。

        Returns:
            str: 业务语言告警。
        """
        return WARNING_TEMPLATE.format(seq=spec.seq, title=spec.title)

    @staticmethod
    def progress(statuses: list[str]) -> int:
        """按步骤状态计算任务进度（**单调不减**）。

        失败/跳过也计入"已处理"，否则任务失败后进度会永远停在中间，
        前端时间线看起来像"卡住了"。

        Args:
            statuses: 步骤状态列表。

        Returns:
            int: 0..100 的进度百分比。
        """
        if not statuses:
            return 0
        done = sum(
            1 for status in statuses if status in {STEP_COMPLETED, STEP_FAILED, STEP_SKIPPED}
        )
        return min(100, round(done * 100 / len(statuses)))

    def checklist(
        self,
        manifest: PluginManifest,
        *,
        artifact_keys: set[str],
        skipped_keys: set[str],
    ) -> list[dict[str, Any]]:
        """生成结果物核查清单（A3 `result.checklist[]`）。

        三态（PRD P0-P3-14）：`checked`（已核查）/ `pending`（待人工确认）/
        `missing`（没有产出）。**不把 missing 说成 checked**——核查清单的全部意义
        是"用户能照着核对"，把缺的标成有等于伪造结果物。

        Args:
            manifest: 插件声明。
            artifact_keys: 已产出产物键集合。
            skipped_keys: 被跳过（待人工确认）的产物键集合。

        Returns:
            list[dict[str, Any]]: 核查项列表。
        """
        output_types = {item.key: item.type for item in manifest.outputs}
        labels = {item.key: item.label for item in manifest.outputs}
        items: list[dict[str, Any]] = []
        for entry in manifest.checklist:
            key = entry.key
            if key in artifact_keys:
                status = "checked"
            elif key in skipped_keys:
                status = "pending"
            else:
                status = "missing"
            items.append(
                {
                    "key": key,
                    "name": entry.name,
                    "type": output_types.get(key, "text"),
                    "status": status,
                    "source_step": entry.source_step,
                    "label": labels.get(key, entry.name),
                }
            )
        return items

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _missing_capabilities(self, spec: StepSpec, ctx: RunContext) -> list[str]:
        """计算某步实际需要但未获授的能力（G-28 运行时半段）。

        Args:
            spec: 步骤声明。
            ctx: 执行上下文。

        Returns:
            list[str]: 缺失能力键列表（空表示通过）。
        """
        if spec.action not in ACTION_WHITELIST:  # 加载期已校验，运行时再兜一次
            return []
        required: list[str] = []
        if spec.action == "retrieve":
            if not any(capability in ctx.granted for capability in RETRIEVE_CAPABILITIES):
                required.extend(RETRIEVE_CAPABILITIES[:1])
        elif spec.action == "tool":
            if "personal.tool.use" not in ctx.granted:
                required.append("personal.tool.use")
            required.extend(capabilities_for_tool(spec.tool_type or "", spec.operation or ""))
        return [item for item in required if item not in ctx.granted]

    def _build_params(
        self, spec: StepSpec, ctx: RunContext, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """按 `needs` 装配原语参数（不把未声明的输入透给原语）。

        Args:
            spec: 步骤声明。
            ctx: 执行上下文。
            inputs: 业务输入。

        Returns:
            dict[str, Any]: 原语参数。
        """
        params: dict[str, Any] = {
            "action": spec.action,
            "step_seq": spec.seq,
            "title": spec.title,
            "produces": list(spec.produces),
            "inputs": dict(inputs),
            "prior": ctx.needs(spec.needs),
        }
        if spec.action == "tool":
            params["tool_type"] = spec.tool_type or ""
            params["operation"] = spec.operation or ""
            params["alternatives"] = [
                {"tool_type": item.tool_type, "operation": item.operation}
                for item in spec.tool_alternatives
            ]
        if spec.action == "render_file":
            params["render_format"] = _declared_render_format(spec)
        params["timeout_seconds"] = spec.timeout_seconds or ACTION_DEFAULT_TIMEOUT.get(
            spec.action, 120
        )
        return params


def _declared_render_format(spec: StepSpec) -> str:
    """取声明里 `render_file` 的格式（**声明是唯一来源**）。

    B20 起 `render_format` 是 `StepSpec` 的必填字段（`action="render_file"` 时），
    加载期已由 schema 校验过；这里再兜一次"声明缺失就报错"，**不静默回落 md**
    ——悄悄降级会让"公文必须是 docx"这条验收变成一句空话。

    调用方无法覆盖：本函数的入参只有步骤声明，没有任何请求参数能参与。

    Args:
        spec: 步骤声明。

    Returns:
        str: 渲染格式（`docx` / `md` / `csv`）。

    Raises:
        ApiError: 声明未给出格式（20001，属加载期本应拦住的声明缺陷）。
    """
    declared = getattr(spec, "render_format", "") or ""
    if declared not in RENDER_FORMATS:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            internal_detail=f"step {spec.seq} render_format={declared!r} 不在白名单内",
        )
    return declared


def _is_blank(value: Any) -> bool:
    """判断输入是否为空（`None` / 空串 / 空列表）。

    Args:
        value: 输入值。

    Returns:
        bool: 空返回 True。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | tuple | set | dict):
        return len(value) == 0
    return False


def strip_offer_exemptions(text: str) -> str:
    """把不构成金额的数字串挖成空格（日期 / 业务编号 / 百分比）。

    实现在 `sanitizer.strip_amount_exemptions`（与脱敏共用同一份豁免规则）；
    这里保留旧名，是因为判定侧的历史调用点与用例都按这个名字读。

    Args:
        text: Offer 原文。

    Returns:
        str: 等长的去豁免文本（位置仍对齐原文）。
    """
    return strip_amount_exemptions(text)


def scan_offer_amount(text: str = "", fields: dict[str, Any] | None = None) -> AmountScan:
    """对 Offer 文案与其金额字段跑 R1 + R2 + R3 三规则。

    **只处理传入的这一份 Offer**：调用方（引擎）已经按 `output_key=offer`
    取好了产物，本函数不再自己去扫别的产物——作用域写在引擎里，不靠传参。

    R2 的三步顺序**不可颠倒**（颠倒就会出新假红或漏检）：

    1. **先剥豁免**（日期 / 业务编号 / 百分比）；
    2. **再找区间**（R2-a）：`8000-10000`、`8000~10000`、`8000至10000`；
    3. **最后找裸数字**（R2-b）：`8000`、`8,000`。

    为什么 R2-a 必须在 R2-b 之前、且独立成支：区间写法正好落在
    「R2-b 排除连字符（怕误伤日期）」与「R1 要求带单位」的空隙里，两边都抓不到；
    而如果把豁免放在区间之后，`2026-11-01` 会先被区间分支抓走变成假阳性。

    三条规则的判据（与追溯表 §3.4 对齐）：

    - **R1** 带单位的金额：`8000 元` / `1.2 万元` / `15k` / `3 亿元` / `8000/月`；
    - **R2** 金额上下文（`薪|工资|待遇|补贴|奖金|年终|期权|股权|月收入|总包`）
      前后 20 字符窗口内的区间或裸数字；
    - **R3** 金额位置的禁用表述：`面议|待定|可谈|可议|根据经验|视情况|依能力|面谈|另议|按市场水平`
      ——这些说法等于把金额决策甩给用户，而用户会误以为结论是模型给的。

    Args:
        text: Offer 正文。
        fields: 金额字段键值（键 ⊆ `OFFER_AMOUNT_FIELDS`）。

    Returns:
        AmountScan: 扫描结果。
    """
    body = text or ""
    r1 = _unique_matches(OFFER_AMOUNT_UNIT_PATTERN, body)
    r3 = _unique_matches(OFFER_AMOUNT_FORBIDDEN_PATTERN, body)
    masked = strip_offer_exemptions(body)
    r2_hits: list[str] = []
    for context in OFFER_AMOUNT_CONTEXT_PATTERN.finditer(masked):
        start = max(0, context.start() - OFFER_AMOUNT_CONTEXT_WINDOW)
        end = min(len(masked), context.end() + OFFER_AMOUNT_CONTEXT_WINDOW)
        window = masked[start:end]
        # 先区间（R2-a），再裸数字（R2-b）：两者独立命中，不互相短路
        for pattern in (OFFER_AMOUNT_RANGE_PATTERN, OFFER_BARE_NUMBER_PATTERN):
            for match in pattern.finditer(window):
                hit = match.group()
                if hit not in r2_hits:
                    r2_hits.append(hit)
    bad_fields: list[str] = []
    for key in OFFER_AMOUNT_FIELDS:
        if fields is None or key not in fields:
            continue
        value = fields[key]
        normalized = "" if value is None else str(value).strip()
        if normalized not in OFFER_AMOUNT_ALLOWED_VALUES:
            bad_fields.append(key)
    return AmountScan(
        r1=tuple(r1),
        r2=tuple(r2_hits),
        r3=tuple(r3),
        fields=tuple(bad_fields),
    )


def _unique_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    """取正则命中的去重片段（保持出现顺序，便于日志与断言）。

    Args:
        pattern: 已编译的正则。
        text: 待扫文本。

    Returns:
        list[str]: 去重后的命中片段。
    """
    hits: list[str] = []
    for match in pattern.finditer(text or ""):
        value = match.group().strip()
        if value and value not in hits:
            hits.append(value)
    return hits


def parse_schedule_rows(content: bytes) -> list[dict[str, str]]:
    """把排期表 CSV 解析回行（护栏要读的是**已渲染出来**的那份表）。

    为什么不从 `params` 里取原始行：排期表的用户可见内容就是导出的 CSV，
    校验对象必须与实际交付物一致；只校验"打算写什么"会放过渲染期的偏差。

    Args:
        content: CSV 字节（带 BOM 与否都可）。

    Returns:
        list[dict[str, str]]: 行列表（解析不出表头时返回空列表）。
    """
    if not content:
        return []
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:  # pragma: no cover - 渲染器只产出 UTF-8
        return []
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({str(key or ""): str(value or "") for key, value in row.items()})
    return rows


def scan_schedule(rows: list[dict[str, str]], *, now: datetime | None = None) -> ScheduleScan:
    """校验排期表：计划时间不得早于当前，同平台同刻不得重复。

    识别不了的时间写法**不判失败**——排期表的时间列允许用户自由填写，
    把"没认出来"当成违规会让守规矩的用户反复被拦（比漏判更伤）。

    时区口径（审计 P2-4）：排期时间是**北京时间**（`SCHEDULE_TZ` = UTC+8）。
    解析结果与参照时间都折算到 aware 后比较；naive 的 `now` 按 `SCHEDULE_TZ`
    解释，缺省参照取 `datetime.now(UTC)`。这样容器 `TZ=UTC` 时也不会错 8 小时，
    传 aware `now` 也不会 `TypeError`。

    Args:
        rows: 排期表行。
        now: 参照时间（缺省取当前时刻；测试注入固定值以保证可复现）。
            naive 值按 `SCHEDULE_TZ` 解释，aware 值按绝对时间参与比较。

    Returns:
        ScheduleScan: 校验结果。
    """
    # 参照时间必须与解析结果**同时区**（见 SCHEDULE_TZ 说明）：naive 入参按
    # 业务时区解释，缺省取 UTC 当前时刻（aware），比较的是绝对时间。
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=SCHEDULE_TZ)
    problems: list[str] = []
    seen: dict[tuple[str, datetime], int] = {}
    checked = 0
    for index, row in enumerate(rows, start=1):
        raw_time = _pick(row, SCHEDULE_TIME_KEYS)
        platform = _pick(row, SCHEDULE_PLATFORM_KEYS)
        parsed = _parse_schedule_time(raw_time)
        if parsed is None:
            continue
        checked += 1
        if parsed < reference:
            problems.append(f"第 {index} 行计划时间（{raw_time}）已经过去了")
            continue
        key = (platform.strip(), parsed)
        if key in seen:
            problems.append(f"第 {seen[key]} 行与第 {index} 行是同一个平台同一个时间，发布会重复")
            continue
        seen[key] = index
    return ScheduleScan(problems=tuple(problems), checked_rows=checked)


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str:
    """按候选键名取列值（命中即返回，去空白）。

    Args:
        row: 行数据。
        keys: 候选键名。

    Returns:
        str: 列值；未命中返回空串。
    """
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_schedule_time(value: str) -> datetime | None:
    """按声明支持的几种写法解析时间（都不匹配返回 None）。

    Args:
        value: 原始时间文本。

    Returns:
        datetime | None: 解析结果；无法识别返回 None。
    """
    text = (value or "").strip()
    if not text:
        return None
    for fmt in SCHEDULE_TIME_FORMATS:
        try:
            # 解析结果按**业务时区**解释（naive + tzinfo），否则与 aware 的参照
            # 时间比较会 TypeError，与 naive 本地时间比较会错 8 小时。
            return datetime.strptime(text, fmt).replace(tzinfo=SCHEDULE_TZ)
        except ValueError:
            continue
    return None


def _screening_rows(draft: ArtifactDraft) -> list[dict[str, str]]:
    """取评分表的行（CSV 或**模型直接产出的 markdown 表格**）。

    为什么先看 `content`：`content` 就是用户下载得到的那份表，校验对象必须与
    交付物一致（与排期表同一口径）。没有字节时回落到 `inline_text`，
    但**不因为回落就跳过校验**。

    为什么还要认 markdown：真实链路里 `understand` 的产物是**模型生成的文本**
    （`ServicesActionGateway._run_generate` 把 `data.content` 原样落成
    `inline_text`），而模型几乎总是用 markdown 表格交付评分表。只认 CSV 会把
    "表头形态不同"误判成"缺少「风险」列"，让每一次真实筛选都失败——
    误判和漏判一样会让用户不再信任这道门，只是方向相反。

    判定顺序：先按 CSV 解析，**解析出的列数 > 1** 说明确实是 CSV；否则再试
    markdown 表格；都不成立时把 CSV 的结果原样返回（交给缺列判据处理）。

    Args:
        draft: `screening` 产物草稿。

    Returns:
        list[dict[str, str]]: 行列表（无可判行时返回空列表）。
    """
    text = _screening_text(draft)
    if not text.strip():
        return []
    csv_rows = parse_schedule_rows(text.encode("utf-8"))
    if _column_count(csv_rows) > 1:
        return csv_rows
    markdown_rows = _markdown_table_rows(text)
    if markdown_rows:
        return markdown_rows
    return csv_rows


def _screening_text(draft: ArtifactDraft) -> str:
    """取评分表原文（优先 `content` 字节，缺了用 `inline_text`）。

    Args:
        draft: 产物草稿。

    Returns:
        str: 表原文（空串表示没有内容）。
    """
    if draft.content:
        try:
            return draft.content.decode("utf-8-sig")
        except UnicodeDecodeError:  # pragma: no cover - 渲染器只产出 UTF-8
            return ""
    return draft.inline_text or ""


def _column_count(rows: list[dict[str, str]]) -> int:
    """取行里的最大列数（用来判断 CSV 解析是否退化成一列）。

    Args:
        rows: 行列表。

    Returns:
        int: 最大列数（空列表返回 0）。
    """
    return max((len(row) for row in rows), default=0)


def _markdown_table_rows(text: str) -> list[dict[str, str]]:
    """把 markdown 表格解析成行字典（不是表格时返回空列表）。

    只认以 `|` 开头的行；首行当表头，紧随的 `| --- | --- |` 分隔行跳过，
    其余行按表头顺序映射成 `{列名: 单元格}`。单元格数少于表头时补空串
    （缺列不能变成"这一行不存在"）。

    Args:
        text: 含 markdown 表格的文本。

    Returns:
        list[dict[str, str]]: 行列表。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return []
    header = _split_markdown_row(lines[0])
    if len(header) < 2:
        return []
    body = lines[1:]
    if body and _is_markdown_separator(body[0]):
        body = body[1:]
    rows: list[dict[str, str]] = []
    for line in body:
        cells = _split_markdown_row(line)
        if not cells:
            continue
        rows.append(
            {
                name: (cells[index] if index < len(cells) else "")
                for index, name in enumerate(header)
            }
        )
    return rows


def _split_markdown_row(line: str) -> list[str]:
    """按 `|` 切分 markdown 表格的一行（去掉首尾竖线后逐格去空白）。

    Args:
        line: 表格行原文。

    Returns:
        list[str]: 单元格列表（空行返回空列表）。
    """
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    if not text.strip():
        return []
    return [cell.strip() for cell in text.split("|")]


def _is_markdown_separator(line: str) -> bool:
    """判断是否 `| --- | :---: |` 这类表头分隔行。

    Args:
        line: 表格行原文。

    Returns:
        bool: 是分隔行返回 True。
    """
    cells = _split_markdown_row(line)
    if not cells:
        return False
    return all(cell and set(cell) <= set("-: ") for cell in cells)


def scan_screening(rows: list[dict[str, str]]) -> list[str]:
    """按「风险」列逐行判定，返回**业务语言**问题清单（空表示通过）。

    判据两条（PRD P0-P3-21）：

    1. 整张表必须有「风险」列（容忍 `Risk` / `风险点` 这类别名）；
    2. 每一行的「风险」去空白后必须非空。

    **一次报全**所有违规行，不在第一条就 `return`：用户拿到的应该是一份
    "要补哪几行"的清单，而不是挤牙膏式的逐次重跑。

    Args:
        rows: 评分表行。

    Returns:
        list[str]: 问题清单（每条都带可识别的行标识，用户能照着改）。
    """
    if not rows:
        return []
    risk_key = _risk_column(rows)
    if risk_key is None:
        return ["评分表缺少「风险」列，没法逐份判断候选人的风险"]
    problems: list[str] = []
    for index, row in enumerate(rows, start=1):
        if str(row.get(risk_key) or "").strip():
            continue
        who = _pick(row, SCREENING_ROW_ID_KEYS)
        where = f"第 {index} 行（{who}）" if who else f"第 {index} 行"
        problems.append(f"{where}的「风险」是空的，请补上这份简历的具体风险后再导出评分表")
    return problems


def _risk_column(rows: list[dict[str, str]]) -> str | None:
    """找出「风险」列的**表头原文**（找不到返回 None）。

    表头来自模型产出的行字典，不是声明里的固定列名，因此按
    `SCREENING_RISK_HEADERS` 做「去空白 + 小写 + 子串包含」匹配：
    `风险` / `风险点` / `Risk` / `Risks` 都能认出来。子串而非全等，
    是因为模型常写「风险点」「主要风险」这类更自然的表头。

    Args:
        rows: 评分表行。

    Returns:
        str | None: 命中的表头原文；整张表没有风险列时返回 None。
    """
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    for header in headers:
        normalized = str(header or "").strip().lower()
        if not normalized:
            continue
        if any(candidate in normalized for candidate in SCREENING_RISK_HEADERS):
            return header
    return None


__all__: list[str] = [
    "DEFAULT_OPERATOR_ACTION",
    "OFFER_AMOUNT_ALLOWED_VALUES",
    "OFFER_AMOUNT_FIELDS",
    "OFFER_AMOUNT_OPERATOR_ACTION",
    "OFFER_OUTPUT_KEY",
    "PATH_OPERATOR_ACTION",
    "RETRYABLE_ERROR_CODES",
    "SCHEDULE_OPERATOR_ACTION",
    "SCHEDULE_OUTPUT_KEY",
    "SCREENING_OUTPUT_KEY",
    "SCREENING_RISK_HEADERS",
    "SCREENING_ROW_ID_KEYS",
    "STEP_COMPLETED",
    "STEP_FAILED",
    "STEP_PENDING",
    "STEP_RUNNING",
    "STEP_SKIPPED",
    "TASK_CANCELLED",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_PENDING",
    "TASK_QUEUED",
    "TASK_RUNNING",
    "TASK_WAITING_CONFIRM",
    "AmountScan",
    "PluginEngine",
    "ScheduleScan",
    "StepOutcome",
    "StepPlan",
    "TaskOutcome",
    "parse_schedule_rows",
    "scan_offer_amount",
    "scan_schedule",
    "scan_screening",
]
