"""插件 `tool_type="mcp"` 的执行桥接（矩阵第 3 项，2026-11-06）。

**只做三件事**：

1. **再复核一次授权**：`RunContext.granted` 必须含 `personal.tool.use`。
   引擎每步入口已复核过，这里再查一次是纵深防御——桥接是唯一能触达外部代码
   的地方，不能依赖上游"应该已经查过"。
2. **约束输入形态**：只允许 JSON 标量/数组/对象，键名白名单正则，序列化 ≤32KB、
   深度 ≤6；超限直接判失败，不把畸形数据交给外部进程。
3. **把失败收敛成 `ActionResult`**：外部超时/崩溃/协议错/超大返回都变成
   `status="failed"`，由引擎按步骤 `on_failure` 处置，**永不向上抛**。
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.plugins.actions import (
    MCP_OPERATION_PATTERN,
    MCP_TOOL_TYPE,
    ActionResult,
    ArtifactDraft,
    RunContext,
)
from app.plugins.sanitizer import ArtifactSanitizer
from app.services.mcp_runner import McpRunner

logger = get_logger(__name__)

#: 工具参数序列化上限（字符数按 UTF-8 字节算）
MAX_ARGUMENT_BYTES: Final[int] = 32 * 1024

#: 参数键名（避免把奇怪的键当命令/路径传给外部）
ARGUMENT_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: 参数最大嵌套深度
MAX_ARGUMENT_DEPTH: Final[int] = 6

#: 失败时的建议动作（业务语言，禁止"重跑/堆栈/日志"）
OPERATOR_ACTION: Final[str] = "可以稍后重试这一步"


class McpToolBridge:
    """把插件步骤接到 MCP 服务。

    Attributes:
        _runner: MCP 受控调用器。
        _sanitizer: 产物脱敏器。
    """

    def __init__(
        self,
        runner: McpRunner | None = None,
        sanitizer: ArtifactSanitizer | None = None,
    ) -> None:
        """初始化。

        Args:
            runner: MCP 调用器。
            sanitizer: 脱敏器。
        """
        self._runner = runner or McpRunner()
        self._sanitizer = sanitizer or ArtifactSanitizer()

    async def execute(self, ctx: RunContext, params: dict[str, Any]) -> ActionResult:
        """执行一个 MCP 工具步骤。

        Args:
            ctx: 执行上下文（租户/已授能力/前序产物）。
            params: 原语参数（`tool_type` / `operation` / `inputs` / `needs` / `produces`）。

        Returns:
            ActionResult: 成功带文本产物；失败带业务文案。
        """
        try:
            if not ctx.has("personal.tool.use"):
                return self._fail(
                    ErrorCode.CAPABILITY_NOT_GRANTED,
                    "缺少所需能力，请重新授权后再试",
                )

            tool_type = str(params.get("tool_type") or "")
            operation = str(params.get("operation") or "")
            if tool_type != MCP_TOOL_TYPE:
                return self._fail(ErrorCode.TOOL_TYPE_UNSUPPORTED, "暂不支持该类工具")
            if not MCP_OPERATION_PATTERN.match(operation):
                return self._fail(ErrorCode.PARAM_INVALID, "外部工具动作标识不正确")

            arguments = _assemble_arguments(ctx, params)
            problem = _validate_arguments(arguments)
            if problem:
                return self._fail(ErrorCode.PARAM_INVALID, problem)

            outcome = await self._runner.execute(ctx.tenant_id, operation, arguments)
            if not outcome.ok:
                return self._fail(outcome.error_code, outcome.reason_text)

            text = self._sanitizer.mask_pii(outcome.text)
            artifacts = _build_artifacts(ctx, params, text)
            data: dict[str, Any] = {
                "mcp_operation": operation,
                "mcp_duration_ms": outcome.duration_ms,
                "mcp_size_bytes": outcome.size_bytes,
            }
            for draft in artifacts:
                data.setdefault(draft.output_key, draft.inline_text)
            return ActionResult(status="ok", artifacts=artifacts, data=data)

        except ApiError as exc:
            return self._fail(exc.code, exc.message)
        except Exception as exc:  # 外部代码的任何意外都收敛成步骤失败
            logger.error("mcp_bridge_failed", reason=type(exc).__name__)
            return self._fail(ErrorCode.TOOL_UNRESPONSIVE, "外部工具暂时没有响应，请稍后再试")

    def _fail(self, code: Any, message: str) -> ActionResult:
        """构造失败结果（可重试恒 False：外部工具不自动重放）。

        Args:
            code: 业务码。
            message: 用户可见文案。

        Returns:
            ActionResult: 失败结果。
        """
        return ActionResult(
            status="failed",
            error_code=code,
            reason_text=message,
            operator_action=OPERATOR_ACTION,
            retryable=False,
        )


def _assemble_arguments(ctx: RunContext, params: dict[str, Any]) -> dict[str, Any]:
    """装配工具参数（输入 + `needs` 指定的前序产物）。

    Args:
        ctx: 执行上下文。
        params: 原语参数。

    Returns:
        dict[str, Any]: 参数。
    """
    arguments: dict[str, Any] = {}
    raw_inputs = params.get("inputs")
    if isinstance(raw_inputs, dict):
        arguments.update(raw_inputs)
    elif ctx.inputs:
        arguments.update(ctx.inputs)
    needs = params.get("needs")
    if isinstance(needs, list | tuple):
        arguments.update(ctx.needs(tuple(str(key) for key in needs)))
    return arguments


def _validate_arguments(arguments: dict[str, Any]) -> str:
    """校验参数形态与大小。

    Args:
        arguments: 参数。

    Returns:
        str: 空串表示通过；否则是用户可见的失败文案。
    """
    payload = json.dumps(arguments, ensure_ascii=False, default=str)
    if len(payload.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        return "这次要交给外部工具的内容过多，请减少后再试"
    for key in arguments:
        if not ARGUMENT_KEY_PATTERN.match(str(key)):
            return "外部工具的动作内容格式不正确"
    if _depth(arguments) > MAX_ARGUMENT_DEPTH:
        return "外部工具的动作内容过于复杂，请简化后再试"
    try:
        json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "外部工具的动作内容无法识别"
    return ""


def _depth(value: Any, level: int = 0) -> int:
    """计算 JSON 值最大嵌套深度。

    Args:
        value: 任意值。
        level: 当前层级。

    Returns:
        int: 最大深度。
    """
    if level > MAX_ARGUMENT_DEPTH:
        return level
    if isinstance(value, dict):
        return max((_depth(item, level + 1) for item in value.values()), default=level)
    if isinstance(value, list | tuple):
        return max((_depth(item, level + 1) for item in value), default=level)
    return level


def _build_artifacts(
    ctx: RunContext, params: dict[str, Any], text: str
) -> tuple[ArtifactDraft, ...]:
    """按 `produces` 生成文本产物草稿。

    Args:
        ctx: 执行上下文（标题兜底用）。
        params: 原语参数。
        text: 已脱敏的文本结果。

    Returns:
        tuple[ArtifactDraft, ...]: 产物草稿。
    """
    keys = [str(key) for key in (params.get("produces") or [])]
    if not keys:
        keys = [str(params.get("output_key") or "mcp_result")]
    title = str(params.get("title") or "") or "外部工具结果"
    drafts: list[ArtifactDraft] = []
    for key in keys:
        drafts.append(
            ArtifactDraft(
                output_key=key,
                type="text",
                name=title,
                inline_text=text,
                size_bytes=len(text.encode("utf-8")),
            )
        )
    del ctx
    return tuple(drafts)


__all__: list[str] = ["MAX_ARGUMENT_BYTES", "McpToolBridge"]
