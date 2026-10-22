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
from datetime import datetime
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
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
from app.plugins.sanitizer import ArtifactSanitizer
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

#: R1：带单位的金额（`8000 元` / `1.2 万元` / `15k` / `8000/月`）
OFFER_AMOUNT_UNIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?:[1-9]\d{0,2}(?:,\d{3})+|\d{3,})(?:\.\d{1,2})?\s*"
    r"(?:元|万元|万|k|K|/月|每月|/年|年薪)"
)

#: R2：金额上下文关键词（窗口内出现裸数字即违规）
OFFER_AMOUNT_CONTEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"薪|工资|待遇|补贴|奖金|年终|期权|股权|月收入|总包"
)

#: R2：裸数字（3-7 位，允许两位小数；排除更长的数字串与负数）
OFFER_BARE_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\d-])\d{3,7}(?:\.\d{1,2})?(?![\d-])"
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

        校验顺序固定：**必填输入（20001）→ 能力（16005）**。
        先查输入是有意的：参数错了让用户改输入即可，不该先弹"缺能力"把
        用户引到授权页去白跑一趟。

        Args:
            manifest: 插件声明。
            inputs: 业务输入。
            granted: 已授能力集合。

        Raises:
            ApiError: 必填输入缺失/非法（20001）、缺少所需能力（16005）。
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
    # 护栏（B20）：执行路径 ⊆ 声明清单 / Offer 金额三规则 / 排期时间
    # ------------------------------------------------------------------
    def guard(self, spec: StepSpec, outcome: StepOutcome) -> StepOutcome:
        """对成功完成的步骤跑三道护栏（**作用域写死，不接受调用方传参**）。

        三道护栏都只改"这一步算不算完成"，**不改任务状态机**：
        命中即该步 `failed` + `retryable=false`，后续由 `on_failure` 决定任务走向
        （Offer 步是 `continue`，排期步是 `abort`）。

        为什么护栏放在引擎而不是网关：网关只认"下游返回了什么"，护栏要认
        "**声明允许什么**"——声明是插件的契约，判据必须落在契约这一层。

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


def scan_offer_amount(text: str = "", fields: dict[str, Any] | None = None) -> AmountScan:
    """对 Offer 文案与其金额字段跑 R1 + R2 + R3 三规则。

    **只处理传入的这一份 Offer**：调用方（引擎）已经按 `output_key=offer`
    取好了产物，本函数不再自己去扫别的产物——作用域写在引擎里，不靠传参。

    三条规则的判据（与追溯表 §3.4 逐字对齐）：

    - **R1** 带单位的金额：`8000 元` / `1.2 万元` / `15k` / `8000/月`；
    - **R2** 金额上下文（`薪|工资|待遇|补贴|奖金|年终|期权|股权|月收入|总包`）
      前后 20 字符窗口内的裸数字：`8000` / `8,000` 之类绕过单位的写法；
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
    r2_hits: list[str] = []
    for context in OFFER_AMOUNT_CONTEXT_PATTERN.finditer(body):
        start = max(0, context.start() - OFFER_AMOUNT_CONTEXT_WINDOW)
        end = min(len(body), context.end() + OFFER_AMOUNT_CONTEXT_WINDOW)
        for match in OFFER_BARE_NUMBER_PATTERN.finditer(body[start:end]):
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

    Args:
        rows: 排期表行。
        now: 参照时间（缺省取当前时刻；测试注入固定值以保证可复现）。

    Returns:
        ScheduleScan: 校验结果。
    """
    reference = now or datetime.now()
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
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
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
]
