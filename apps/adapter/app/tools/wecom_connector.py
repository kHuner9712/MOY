"""企业微信工具连接器（阶段二 B06，PRD P2-TL-01）。

能力清单（与 `registry/tool_registry.yaml` 中 `wecom` 条目**逐字一致**）：
`send_message` 发送消息 / `list_departments` 查看通讯录部门 /
`list_members` 查看通讯录成员 / `create_group` 创建群聊。

红线（架构文档 9.3 / PRD 第 7 章）：

- 响应与日志**禁止**出现 `access_token` / `corp_id` / `secret` 等第三方术语；
- 凭据只以"授权码"形式进入本层，用完即弃，不写日志、不进返回值；
- 错误一律映射为自研错误码 + 业务语言文案（`13002` / `13003` / `13004` / `13006`）。

本阶段为桩实现：不发起任何真实第三方调用；`params["simulate"]` 是**唯一**的
异常注入入口（供单测覆盖 13003/13004/13006 映射），取值：
`auth_expired` / `unresponsive` / `member_missing`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Final

from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.tools.base import ToolConnector, build_partial_result, register_connector

logger = get_logger(__name__)

#: 授权有效期（天）
_TOKEN_VALID_DAYS: Final[int] = 180

#: 单次发送人数上限（PRD P2-TL-01 第 3 条）
MAX_RECIPIENTS: Final[int] = 200

#: 超限文案（PRD 原文，不得改写）
TOO_MANY_RECIPIENTS_MESSAGE: Final[str] = "一次最多发送给 200 人，请分批发送"

#: 成员不存在文案（PRD 原文）
MEMBER_MISSING_MESSAGE: Final[str] = "未找到该成员，请确认后在通讯录中核对"


def _now() -> datetime:
    """获取当前 UTC 时间。

    Returns:
        datetime: 当前 UTC 时间。
    """
    return datetime.now(UTC)


@register_connector
class WecomConnector(ToolConnector):
    """企业微信工具连接器。

    能力清单见类属性 `SUPPORTED_OPERATIONS`（必须与注册表一致，
    `ToolService` 启动时会做一致性校验）。
    """

    tool_type: ClassVar[str] = "wecom"
    connector_impl: ClassVar[str] = "wecom_connector"
    display_name: ClassVar[str] = "企业微信"

    #: 本连接器支持的操作标识
    SUPPORTED_OPERATIONS: ClassVar[tuple[str, ...]] = (
        "send_message",
        "list_departments",
        "list_members",
        "create_group",
    )

    async def connect(
        self,
        *,
        auth_code: str,
        redirect_uri: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """发起企业微信授权连接（桩实现）。

        Args:
            auth_code: 授权码（严禁写入日志）。
            redirect_uri: 授权回调地址。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 含 `connection_id` 与 `expires_at` 的连接结果。

        Raises:
            ApiError: 授权码为空（13003 授权过期）。
        """
        if not auth_code.strip():
            raise ApiError(
                ErrorCode.TOOL_AUTH_EXPIRED, internal_detail="empty auth_code for wecom tool"
            )
        expires_at = _now() + timedelta(days=_TOKEN_VALID_DAYS)
        logger.info(
            "tool_connect_stub",
            tool_type=self.tool_type,
            tenant_id=tenant_id,
            user_id=user_id,
            has_redirect_uri=bool(redirect_uri),
        )
        return {
            "connection_id": new_resource_id("conn_"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }

    async def disconnect(self, *, connection_id: str, tenant_id: str, user_id: str) -> None:
        """断开企业微信连接（桩实现）。

        Args:
            connection_id: 连接 ID。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Raises:
            ApiError: 连接 ID 为空（13002）。
        """
        if not connection_id:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED, internal_detail="empty connection_id for wecom"
            )
        logger.info(
            "tool_disconnect_stub", tool_type=self.tool_type, tenant_id=tenant_id, user_id=user_id
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
        """执行企业微信操作（桩实现）。

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
            ApiError: 连接不存在（13002）、操作不支持（13005）、
                授权过期（13003）、工具无响应（13004）、第三方业务错误（13006）。
        """
        self.validate_operation(operation, self.SUPPORTED_OPERATIONS)
        if not connection_id:
            raise ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail="wecom tool not connected")
        _raise_simulated(params)
        logger.info(
            "tool_execute_stub",
            tool_type=self.tool_type,
            operation=operation,
            tenant_id=tenant_id,
            user_id=user_id,
            timeout_ms=timeout_ms,
        )
        if operation == "send_message":
            return self._send_message(params)
        if operation == "list_departments":
            return {"departments": [], "total": 0}
        if operation == "list_members":
            return {"members": [], "total": 0}
        return self._create_group(params)

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
        """企业微信不支持数据同步任务（能力清单外）。

        Args:
            connection_id: 连接 ID。
            scope: 同步范围。
            params: 同步参数。
            timeout_ms: 超时声明。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Raises:
            ApiError: 操作不支持（13005）。
        """
        del connection_id, scope, params, timeout_ms, tenant_id, user_id
        raise ApiError(
            ErrorCode.TOOL_OPERATION_UNSUPPORTED,
            internal_detail=f"tool_type={self.tool_type} does not support sync",
        )

    def _send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造「发送消息」的桩结果。

        Args:
            params: 操作参数（`members` / `departments` / `chat_ids` / `content`）。

        Returns:
            dict[str, Any]: 含 `message_id` 与接收人数的业务化结果。

        Raises:
            ApiError: 收件人为空（20001）或超过 200 人（20001，PRD 原文文案）。
        """
        members = _as_list(params.get("members"))
        departments = _as_list(params.get("departments"))
        chats = _as_list(params.get("chat_ids"))
        recipients = len(members) + len(departments) + len(chats)
        if recipients == 0:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="wecom send_message has no recipient"
            )
        if recipients > MAX_RECIPIENTS:
            raise ApiError(ErrorCode.PARAM_INVALID, message=TOO_MANY_RECIPIENTS_MESSAGE)
        if params.get("simulate") == "member_missing":
            raise ApiError(ErrorCode.TOOL_THIRD_PARTY_ERROR, message=MEMBER_MISSING_MESSAGE)
        return {
            "message_id": new_resource_id("msg_"),
            "recipient_count": recipients,
            "sent_at": _now().isoformat().replace("+00:00", "Z"),
        }

    def _create_group(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造「创建群聊」的桩结果。

        Args:
            params: 操作参数（`name` / `member_ids`）。

        Returns:
            dict[str, Any]: 含 `chat_id` 与成员数的结果。

        Raises:
            ApiError: 缺少群名或成员（20001）。
        """
        name = str(params.get("name", "")).strip()
        member_ids = _as_list(params.get("member_ids"))
        if not name or not member_ids:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail="wecom create_group requires name and member_ids",
            )
        invalid = _as_list(params.get("invalid_member_ids"))
        if invalid:
            return build_partial_result(
                chat_id=new_resource_id("chat_"),
                member_count=len(member_ids),
                warnings=[f"有 {len(invalid)} 位成员未找到，请核对后重试"],
            )
        return {
            "chat_id": new_resource_id("chat_"),
            "member_count": len(member_ids),
        }


def _as_list(value: Any) -> list[Any]:
    """把参数值安全转换为列表。

    Args:
        value: 原始参数值。

    Returns:
        list[Any]: 列表；非列表值返回空列表。
    """
    return list(value) if isinstance(value, list) else []


def _raise_simulated(params: dict[str, Any]) -> None:
    """按 `params["simulate"]` 注入异常分支（桩实现专用）。

    Args:
        params: 操作参数。

    Raises:
        ApiError: 授权的异常分支（13003 / 13004）。
    """
    simulate = str(params.get("simulate", "")).strip()
    if simulate == "auth_expired":
        raise ApiError(ErrorCode.TOOL_AUTH_EXPIRED, internal_detail="simulated auth expired")
    if simulate == "unresponsive":
        raise ApiError(ErrorCode.TOOL_UNRESPONSIVE, internal_detail="simulated unresponsive")
