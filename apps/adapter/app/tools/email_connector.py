"""邮箱工具连接器（示例实现）。

用途：体现「工具类型注册表模式」的连接器形态——
本文件是**新增工具时唯一需要新增的代码位置**，T1-T7 端点与错误码零改动。

T01 阶段说明：所有方法返回业务化 mock 结果，**不发起任何真实第三方调用**；
T02 起在此处接入真实实现，仍不改变接口契约。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.tools.base import ToolConnector, register_connector

logger = get_logger(__name__)

#: 授权有效期（天）
_TOKEN_VALID_DAYS: Final[int] = 180


@register_connector
class EmailConnector(ToolConnector):
    """邮箱工具连接器。

    能力清单（与 `registry/tool_registry.yaml` 中 `email` 条目严格一致）：
    `send_message` / `list_messages` / `sync`。
    """

    tool_type: ClassVar[str] = "email"
    connector_impl: ClassVar[str] = "email_connector"
    display_name: ClassVar[str] = "邮箱"

    #: 本连接器支持的操作标识
    SUPPORTED_OPERATIONS: ClassVar[tuple[str, ...]] = (
        "send_message",
        "list_messages",
        "sync",
    )

    @staticmethod
    def _now() -> datetime:
        """获取当前 UTC 时间。

        Returns:
            datetime: 当前 UTC 时间。
        """
        return datetime.now(UTC)

    async def connect(
        self,
        *,
        auth_code: str,
        redirect_uri: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """发起邮箱授权连接（T01 为桩实现）。

        Args:
            auth_code: 授权码（严禁写入日志）。
            redirect_uri: 授权回调地址。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 含 `connection_id` 与 `expires_at` 的连接结果。

        Raises:
            ApiError: 授权码无效（13003 授权过期）。
        """
        if not auth_code.strip():
            raise ApiError(
                ErrorCode.TOOL_AUTH_EXPIRED, internal_detail="empty auth_code for email tool"
            )
        expires_at = self._now() + timedelta(days=_TOKEN_VALID_DAYS)
        logger.info(
            "tool_connect_stub",
            tool_type=self.tool_type,
            tenant_id=tenant_id,
            user_id=user_id,
            has_redirect_uri=bool(redirect_uri),
        )
        return {
            "connection_id": "",
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

    async def disconnect(self, *, connection_id: str, tenant_id: str, user_id: str) -> None:
        """断开邮箱连接（T01 为桩实现）。

        Args:
            connection_id: 连接 ID。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Raises:
            ApiError: 连接不存在（13002）。
        """
        if not connection_id:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED, internal_detail="empty connection_id for email tool"
            )
        logger.info(
            "tool_disconnect_stub",
            tool_type=self.tool_type,
            tenant_id=tenant_id,
            user_id=user_id,
        )

    async def execute(
        self,
        *,
        connection_id: str,
        operation: str,
        params: dict[str, Any],
        timeout_ms: int,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """执行邮箱操作（T01 为桩实现）。

        Args:
            connection_id: 连接 ID。
            operation: 操作标识。
            params: 操作参数。
            timeout_ms: 超时声明。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 业务化执行结果。

        Raises:
            ApiError: 连接不存在（13002）、操作不支持（13005）。
        """
        self.validate_operation(operation, self.SUPPORTED_OPERATIONS)
        if not connection_id:
            raise ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail="email tool not connected")
        logger.info(
            "tool_execute_stub",
            tool_type=self.tool_type,
            operation=operation,
            tenant_id=tenant_id,
            user_id=user_id,
            timeout_ms=timeout_ms,
        )

        if operation == "send_message":
            return self._stub_send_message(params)
        return self._stub_list_messages(params)

    async def sync(
        self,
        *,
        connection_id: str,
        scope: str,
        params: dict[str, Any],
        timeout_ms: int,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """创建收件箱同步任务（T01 为桩实现）。

        Args:
            connection_id: 连接 ID。
            scope: 同步范围。
            params: 同步参数。
            timeout_ms: 超时声明。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 含 `total_count` 的同步任务初始信息。

        Raises:
            ApiError: 连接不存在（13002）、操作不支持（13005）。
        """
        self.validate_operation("sync", self.SUPPORTED_OPERATIONS)
        if not connection_id:
            raise ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail="email tool not connected")
        logger.info(
            "tool_sync_stub",
            tool_type=self.tool_type,
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        return {"total_count": 0, "scope": scope or "inbox"}

    def _stub_send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造发送邮件的桩结果。

        Args:
            params: 操作参数，含 `to` / `subject` / `body`。

        Returns:
            dict[str, Any]: 含 `message_id` 与 `sent_at` 的业务化结果。

        Raises:
            ApiError: 缺少必填参数（20001）。
        """
        recipients = params.get("to") or []
        if not isinstance(recipients, list) or not recipients:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="email send_message missing recipients"
            )
        sent_at = self._now().isoformat().replace("+00:00", "Z")
        return {
            "message_id": "",
            "recipient_count": len(recipients),
            "sent_at": sent_at,
        }

    def _stub_list_messages(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造邮件列表的桩结果。

        Args:
            params: 操作参数，含 `folder` / `limit`。

        Returns:
            dict[str, Any]: 含 `messages` 列表的业务化结果。
        """
        limit = params.get("limit", 20)
        folder = params.get("folder", "inbox")
        return {"folder": str(folder), "messages": [], "limit": int(limit)}
