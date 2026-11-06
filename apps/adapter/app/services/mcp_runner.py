"""MCP 传输执行层（矩阵第 3 项，2026-11-06）。

`business/mcp_service.py` 负责登记校验与白名单判定，本模块负责**真正发起连接**：

- 依赖矩阵只允许 `services → clients`，因此 `mcp_client` 只能在这里被调用；
- 外部代码的所有失败（超时/连不上/鉴权/协议/超大/自报失败）都翻译成
  `McpOutcome` 或探测结果字典，**不向上抛**；
- 日志只记 `server_id/name/tool/reason/duration_ms/size_bytes`，不记地址与凭据。

`probe` 的 `ApiError`（未开启/资源不存在/凭据不可用）中，前两者继续抛出（运营端点
需要 20003/20004），凭据不可用归一为 `reason="auth_failed"`，与修复前行为一致。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

from app.business.mcp_service import McpServerService, McpTarget
from app.clients import mcp_client
from app.clients.mcp_client import McpConnection, McpError
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 失败分类 → 业务码与用户文案（内部诊断不进响应）
FAILURE_MAP: Final[dict[str, tuple[int, str]]] = {
    "timeout": (ErrorCode.TOOL_UNRESPONSIVE, "外部工具暂时没有响应，请稍后再试"),
    "connect_failed": (ErrorCode.TOOL_UNRESPONSIVE, "这个外部工具暂时无法连接，请稍后再试"),
    "protocol_error": (
        ErrorCode.TOOL_THIRD_PARTY_ERROR,
        "外部工具返回的内容无法识别，请稍后再试",
    ),
    "auth_failed": (ErrorCode.TOOL_AUTH_EXPIRED, "外部工具凭证不可用，请联系管理员"),
    "output_too_large": (ErrorCode.TOOL_THIRD_PARTY_ERROR, "外部工具返回内容过大，本次已中止"),
    "tool_error": (ErrorCode.TOOL_THIRD_PARTY_ERROR, "外部工具没有完成这次操作，请稍后再试"),
}


@dataclass(frozen=True, slots=True)
class McpOutcome:
    """一次工具调用的结果（失败也是结果，不抛异常）。

    Attributes:
        ok: 是否成功。
        text: 文本结果（成功时非空）。
        error_code: 失败时的业务码（内部汇总用）。
        reason_text: 失败文案（可直接给用户看）。
        retryable: 是否可重试（外部工具一律不自动重试）。
        duration_ms: 耗时。
        size_bytes: 返回体大小。
    """

    ok: bool
    text: str = ""
    error_code: int = 0
    reason_text: str = ""
    retryable: bool = False
    duration_ms: int = 0
    size_bytes: int = 0


class McpRunner:
    """受控连接与调用（唯一能触达外部代码的运行时入口）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        service: McpServerService | None = None,
    ) -> None:
        """初始化。

        Args:
            settings: 配置实例。
            service: 业务服务（缺省按配置构造）。
        """
        self._settings = settings or get_settings()
        self._service = service or McpServerService(self._settings)

    async def probe(self, tenant_id: str, server_id: str) -> dict[str, Any]:
        """连接一次并读取工具列表（结果不含地址与凭据）。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            dict[str, Any]: `{ok, reason, duration_ms, tool_count, tools}`。

        Raises:
            ApiError: 能力未开启（20003）或资源不存在（20004）。
        """
        started = time.monotonic()
        try:
            target = await self._service.prepare_probe(tenant_id, server_id)
        except ApiError as exc:
            if exc.code != ErrorCode.KB_CREDENTIAL_UNAVAILABLE:
                raise
            # 凭据不可用是"这个服务连不上"的一种，归一成 auth_failed；
            # 但不返回引用/明文，也不抛 500。
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "mcp_probe_failed",
                server_id=server_id,
                reason="auth_failed",
                duration_ms=duration_ms,
            )
            return {
                "ok": False,
                "reason": "auth_failed",
                "duration_ms": duration_ms,
                "tool_count": 0,
                "tools": [],
            }
        try:
            names = await mcp_client.list_tools(self._connection(target))
            allowed = sorted(set(names) & set(target.allowed_tools))
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "mcp_probe_succeeded",
                server_id=target.server_id,
                name=target.name,
                duration_ms=duration_ms,
                tool_count=len(allowed),
            )
            return {
                "ok": True,
                "reason": "",
                "duration_ms": duration_ms,
                "tool_count": len(allowed),
                "tools": allowed,
            }
        except (McpError, ApiError) as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            reason = exc.kind if isinstance(exc, McpError) else "auth_failed"
            logger.warning(
                "mcp_probe_failed",
                server_id=target.server_id,
                name=target.name,
                reason=reason,
                duration_ms=duration_ms,
            )
            return {
                "ok": False,
                "reason": reason,
                "duration_ms": duration_ms,
                "tool_count": 0,
                "tools": [],
            }

    async def execute(
        self, tenant_id: str, operation: str, arguments: dict[str, Any]
    ) -> McpOutcome:
        """执行一次白名单内的工具调用。

        Args:
            tenant_id: 租户 ID（来自已鉴权任务上下文）。
            operation: `<server_name>.<tool_name>`。
            arguments: 工具参数（已由桥接层做形态/大小约束）。

        Returns:
            McpOutcome: 成功或失败结果（**不抛外部异常**）。

        Raises:
            ApiError: 能力未开启 / 服务器未登记 / 工具不在白名单 / 凭据不可用。
        """
        target, tool_name = await self._service.prepare_execute(tenant_id, operation)
        started = time.monotonic()
        try:
            result = await mcp_client.call_tool(self._connection(target), tool_name, arguments)
        except ApiError:
            raise
        except McpError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            code, message = FAILURE_MAP.get(
                exc.kind, (ErrorCode.TOOL_THIRD_PARTY_ERROR, "外部工具暂时不可用，请稍后再试")
            )
            logger.warning(
                "mcp_tool_failed",
                server_id=target.server_id,
                name=target.name,
                tool=tool_name,
                reason=exc.kind,
                duration_ms=duration_ms,
            )
            return McpOutcome(
                ok=False,
                error_code=code,
                reason_text=message,
                duration_ms=duration_ms,
            )
        except Exception as exc:  # 外部代码的任何意外都收敛成步骤失败
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.error(
                "mcp_tool_failed",
                server_id=target.server_id,
                name=target.name,
                tool=tool_name,
                reason=type(exc).__name__,
                duration_ms=duration_ms,
            )
            return McpOutcome(
                ok=False,
                error_code=ErrorCode.TOOL_UNRESPONSIVE,
                reason_text="外部工具暂时没有响应，请稍后再试",
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        if result.is_error:
            logger.warning(
                "mcp_tool_failed",
                server_id=target.server_id,
                name=target.name,
                tool=tool_name,
                reason="tool_error",
                duration_ms=duration_ms,
            )
            return McpOutcome(
                ok=False,
                error_code=ErrorCode.TOOL_THIRD_PARTY_ERROR,
                reason_text="外部工具没有完成这次操作，请稍后再试",
                duration_ms=duration_ms,
            )
        logger.info(
            "mcp_tool_called",
            server_id=target.server_id,
            name=target.name,
            tool=tool_name,
            duration_ms=duration_ms,
            size_bytes=result.size_bytes,
        )
        return McpOutcome(
            ok=True,
            text=result.text,
            duration_ms=duration_ms,
            size_bytes=result.size_bytes,
        )

    def _connection(self, target: McpTarget) -> McpConnection:
        """把业务层解析结果转成客户端连接参数。

        Args:
            target: 已解析的连接参数。

        Returns:
            McpConnection: 客户端连接参数。
        """
        return McpConnection(
            transport=target.transport,
            endpoint=target.endpoint,
            args=target.args,
            credential=target.credential,
            timeout_ms=target.timeout_ms,
            connect_timeout_ms=target.connect_timeout_ms,
            max_output_bytes=target.max_output_bytes,
            allow_insecure_http=target.allow_insecure_http,
        )


__all__: list[str] = ["FAILURE_MAP", "McpOutcome", "McpRunner"]
