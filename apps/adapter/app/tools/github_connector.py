"""代码仓库工具连接器（示例实现）。

用途：与 `email_connector.py` 共同体现「新增工具只加 `tools/`」的扩展契约。
本文件不改变 T1-T7 任何端点、请求响应结构与错误码。

T01 阶段说明：所有方法返回业务化 mock 结果，**不发起任何真实第三方调用**。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.tools.base import ToolConnector, register_connector

logger = get_logger(__name__)

#: 授权有效期（天）
_TOKEN_VALID_DAYS: Final[int] = 365

#: 创建类操作所需的必填参数
_REQUIRED_FIELDS: Final[tuple[str, ...]] = ("owner", "repo", "title")


@register_connector
class GitHubConnector(ToolConnector):
    """代码仓库工具连接器。

    能力清单（与 `registry/tool_registry.yaml` 中 `github` 条目严格一致）：
    `create_pull_request` / `create_issue`。
    """

    tool_type: ClassVar[str] = "github"
    connector_impl: ClassVar[str] = "github_connector"
    display_name: ClassVar[str] = "代码仓库"

    #: 本连接器支持的操作标识
    SUPPORTED_OPERATIONS: ClassVar[tuple[str, ...]] = (
        "create_pull_request",
        "create_issue",
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
        """发起代码仓库授权连接（T01 为桩实现）。

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
                ErrorCode.TOOL_AUTH_EXPIRED, internal_detail="empty auth_code for repo tool"
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
        """断开代码仓库连接（T01 为桩实现）。

        Args:
            connection_id: 连接 ID。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Raises:
            ApiError: 连接不存在（13002）。
        """
        if not connection_id:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED, internal_detail="empty connection_id for repo tool"
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
        """执行代码仓库操作（T01 为桩实现）。

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
            ApiError: 连接不存在（13002）、操作不支持（13005）、参数缺失（20001）。
        """
        self.validate_operation(operation, self.SUPPORTED_OPERATIONS)
        if not connection_id:
            raise ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail="repo tool not connected")
        logger.info(
            "tool_execute_stub",
            tool_type=self.tool_type,
            operation=operation,
            tenant_id=tenant_id,
            user_id=user_id,
            timeout_ms=timeout_ms,
        )

        missing = [name for name in _REQUIRED_FIELDS if not params.get(name)]
        if missing:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"repo operation missing fields: {','.join(missing)}",
            )

        created_at = self._now().isoformat().replace("+00:00", "Z")
        if operation == "create_pull_request":
            return {
                "number": 0,
                "title": str(params.get("title", "")),
                "url": "",
                "created_at": created_at,
            }
        return {
            "number": 0,
            "title": str(params.get("title", "")),
            "url": "",
            "state": "open",
            "created_at": created_at,
        }

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
        """代码仓库工具不支持数据同步。

        Args:
            connection_id: 连接 ID。
            scope: 同步范围。
            params: 同步参数。
            timeout_ms: 超时声明。
            tenant_id: 租户 ID。
            user_id: 用户 ID.

        Raises:
            ApiError: 恒抛 13005（该工具不支持此操作）。
        """
        del connection_id, scope, params, timeout_ms, tenant_id, user_id
        raise ApiError(
            ErrorCode.TOOL_OPERATION_UNSUPPORTED,
            internal_detail=f"tool_type={self.tool_type} does not support sync",
        )
