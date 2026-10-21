"""飞书工具连接器（阶段二 B06，PRD P2-TL-02）。

能力清单（与注册表 `feishu` 条目严格一致）：
`send_message` 发送消息 / `create_calendar_event` 创建日程 /
`list_members` 查看成员 / `create_doc` 新建云文档。

两条业务红线（PRD P2-TL-02 第 3-4 条）：

1. **权限不足**（应用未开通对应权限）→ `13005`「该操作需要管理员在飞书后台开通对应权限」；
2. **部分成功**（日程创建成功但个别参与人邀请失败）→ `status=success` +
   `result.warnings`「有 1 位参与人未找到，请核对后重试」，**不得**判整单失败。

桩实现说明：`params["simulate"]` 为唯一异常注入入口（`forbidden` / `unresponsive` /
`auth_expired`），`params["invalid_participants"]` 用于构造部分成功场景。
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

#: 单次日程参与人上限（PRD P2-TL-02 第 3 条）
MAX_PARTICIPANTS: Final[int] = 50

#: 参与人超限文案
TOO_MANY_PARTICIPANTS_MESSAGE: Final[str] = "一次最多邀请 50 位参与人，请分批处理"

#: 权限不足文案（PRD 原文）
PERMISSION_REQUIRED_MESSAGE: Final[str] = "该操作需要管理员在飞书后台开通对应权限"


def _now() -> datetime:
    """获取当前 UTC 时间。

    Returns:
        datetime: 当前 UTC 时间。
    """
    return datetime.now(UTC)


@register_connector
class FeishuConnector(ToolConnector):
    """飞书工具连接器。"""

    tool_type: ClassVar[str] = "feishu"
    connector_impl: ClassVar[str] = "feishu_connector"
    display_name: ClassVar[str] = "飞书"

    #: 本连接器支持的操作标识
    SUPPORTED_OPERATIONS: ClassVar[tuple[str, ...]] = (
        "send_message",
        "create_calendar_event",
        "list_members",
        "create_doc",
    )

    async def connect(
        self,
        *,
        auth_code: str,
        redirect_uri: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """发起飞书授权连接（桩实现）。

        Args:
            auth_code: 授权码（严禁写入日志）。
            redirect_uri: 授权回调地址。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 含 `connection_id` 与 `expires_at` 的连接结果。

        Raises:
            ApiError: 授权码为空（13003）。
        """
        if not auth_code.strip():
            raise ApiError(
                ErrorCode.TOOL_AUTH_EXPIRED, internal_detail="empty auth_code for feishu tool"
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
        """断开飞书连接（桩实现）。

        Args:
            connection_id: 连接 ID。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Raises:
            ApiError: 连接 ID 为空（13002）。
        """
        if not connection_id:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED, internal_detail="empty connection_id for feishu"
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
        """执行飞书操作（桩实现）。

        Args:
            connection_id: 连接 ID。
            operation: 操作标识。
            params: 操作参数。
            timeout_ms: 超时声明。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 业务化执行结果（部分成功时含 `warnings`）。

        Raises:
            ApiError: 连接不存在（13002）、操作不支持（13005）、授权过期（13003）、
                工具无响应（13004）、第三方业务错误（13006）。
        """
        self.validate_operation(operation, self.SUPPORTED_OPERATIONS)
        if not connection_id:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED, internal_detail="feishu tool not connected"
            )
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
        if operation == "create_calendar_event":
            return self._create_event(params)
        if operation == "list_members":
            return {"members": [], "total": 0}
        return self._create_doc(params)

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
        """飞书不支持数据同步任务（能力清单外）。

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
            params: 操作参数（`receivers` / `content`）。

        Returns:
            dict[str, Any]: 含 `message_id` 与接收人数的结果。

        Raises:
            ApiError: 收件人为空（20001）。
        """
        receivers = _as_list(params.get("receivers")) or _as_list(params.get("to"))
        if not receivers:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="feishu send_message has no receiver"
            )
        return {
            "message_id": new_resource_id("msg_"),
            "receiver_count": len(receivers),
            "sent_at": _now().isoformat().replace("+00:00", "Z"),
        }

    def _create_event(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造「创建日程」的桩结果（含部分成功语义）。

        Args:
            params: 操作参数（`title` / `start_time` / `duration_minutes` /
                `participants` / `invalid_participants`）。

        Returns:
            dict[str, Any]: 日程结果；部分参与人失败时含 `warnings`。

        Raises:
            ApiError: 缺少标题/开始时间（20001）、参与人超过 50（20001）、
                全部参与人都未找到（13006）。
        """
        title = str(params.get("title", "")).strip()
        start_time = str(params.get("start_time", "")).strip()
        if not title or not start_time:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail="feishu create_calendar_event requires title and start_time",
            )
        participants = _as_list(params.get("participants"))
        if len(participants) > MAX_PARTICIPANTS:
            raise ApiError(ErrorCode.PARAM_INVALID, message=TOO_MANY_PARTICIPANTS_MESSAGE)
        invalid = _as_list(params.get("invalid_participants"))
        if invalid and len(invalid) >= len(participants):
            # 全部参与人都找不到：这不是"部分成功"，按第三方业务错误处理
            raise ApiError(
                ErrorCode.TOOL_THIRD_PARTY_ERROR,
                message=f"有 {len(invalid)} 位参与人未找到，请核对后重试",
            )
        duration = int(params.get("duration_minutes", 30) or 30)
        end_time = (_now() + timedelta(minutes=duration)).isoformat().replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "event_id": new_resource_id("evt_"),
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
            "participant_count": len(participants),
        }
        if invalid:
            payload = build_partial_result(
                **payload,
                warnings=[f"有 {len(invalid)} 位参与人未找到，请核对后重试"],
            )
        return payload

    def _create_doc(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造「新建云文档」的桩结果。

        Args:
            params: 操作参数（`title` / `content`）。

        Returns:
            dict[str, Any]: 含 `doc_id` 与访问地址的结果。

        Raises:
            ApiError: 缺少标题（20001）。
        """
        title = str(params.get("title", "")).strip()
        if not title:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="feishu create_doc requires title"
            )
        doc_id = new_resource_id("fdoc_")
        return {"doc_id": doc_id, "title": title, "url": f"/docs/{doc_id}"}


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
        ApiError: 授权的异常分支（13003 / 13004 / 13005）。
    """
    simulate = str(params.get("simulate", "")).strip()
    if simulate == "auth_expired":
        raise ApiError(ErrorCode.TOOL_AUTH_EXPIRED, internal_detail="simulated auth expired")
    if simulate == "unresponsive":
        raise ApiError(ErrorCode.TOOL_UNRESPONSIVE, internal_detail="simulated unresponsive")
    if simulate == "forbidden":
        raise ApiError(ErrorCode.TOOL_OPERATION_UNSUPPORTED, message=PERMISSION_REQUIRED_MESSAGE)
