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
from dataclasses import dataclass, field
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
    capabilities_for_tool,
    capability_display_name,
)
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

        params = self._build_params(spec, ctx, inputs)
        attempts = 0
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
        return outcome

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
    """取声明里 `render_file` 的格式（声明缺省时回落 `md`）。

    `StepSpec` 未单独建模 `render_format` 字段时（B20 补声明校验），
    B19 以 `md` 为默认；**调用方无法覆盖**（不允许请求参数改格式）。

    Args:
        spec: 步骤声明。

    Returns:
        str: 渲染格式。
    """
    declared = getattr(spec, "render_format", "") or ""
    return declared if declared in {"docx", "md", "csv"} else "md"


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


__all__: list[str] = [
    "DEFAULT_OPERATOR_ACTION",
    "RETRYABLE_ERROR_CODES",
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
    "PluginEngine",
    "StepOutcome",
    "StepPlan",
    "TaskOutcome",
]
