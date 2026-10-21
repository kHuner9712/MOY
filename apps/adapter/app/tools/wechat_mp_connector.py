"""微信公众号工具连接器（阶段二 B06，PRD P2-TL-03）。

能力清单（与注册表 `wechat_mp` 条目严格一致）：
`upload_material` 上传素材 / `create_draft` 保存草稿 /
`publish_article` 发布文章 / `list_articles` 查看文章数据。

两条业务红线：

1. **发布必须两步确认**（商用防误发）：`create_draft` 只生成草稿；
   `publish_article` 必须显式带 `confirmed=true`，否则 `20001`「请先确认发布」；
2. **`draft_id` 幂等**：同一条草稿重复发布返回 `20007`（不产生两篇文章）。

桩实现说明：`params["simulate"]` 为唯一异常注入入口（`auth_expired` /
`unresponsive`）；正文含未授权外链时按 `13006` 处理（PRD 原文文案）。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Final

from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.tools.base import ToolConnector, register_connector

logger = get_logger(__name__)

#: 授权有效期（天）
_TOKEN_VALID_DAYS: Final[int] = 180

#: 素材大小上限（10MB，公众号图文素材限制；PRD P2-TL-03 第 5 条）
MAX_MATERIAL_BYTES: Final[int] = 10 * 1024 * 1024

#: 未确认发布文案
CONFIRM_REQUIRED_MESSAGE: Final[str] = "请先确认发布"

#: 未授权外链文案（PRD 原文）
UNAUTHORIZED_LINK_MESSAGE: Final[str] = "正文含未获授权的外部链接，请调整后再发布"

#: 已发布草稿登记表（`(tenant_id, draft_id)`）：保证同一草稿只发布一次
_PUBLISHED_DRAFTS: set[tuple[str, str]] = set()


def _now() -> datetime:
    """获取当前 UTC 时间。

    Returns:
        datetime: 当前 UTC 时间。
    """
    return datetime.now(UTC)


def reset_publish_registry() -> None:
    """清空发布登记表（测试隔离用）。"""
    _PUBLISHED_DRAFTS.clear()


@register_connector
class WechatMpConnector(ToolConnector):
    """微信公众号工具连接器。"""

    tool_type: ClassVar[str] = "wechat_mp"
    connector_impl: ClassVar[str] = "wechat_mp_connector"
    display_name: ClassVar[str] = "微信公众号"

    #: 本连接器支持的操作标识
    SUPPORTED_OPERATIONS: ClassVar[tuple[str, ...]] = (
        "upload_material",
        "create_draft",
        "publish_article",
        "list_articles",
    )

    async def connect(
        self,
        *,
        auth_code: str,
        redirect_uri: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """绑定公众号（桩实现）。

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
                ErrorCode.TOOL_AUTH_EXPIRED, internal_detail="empty auth_code for wechat_mp tool"
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
        """断开公众号绑定（桩实现）。

        Args:
            connection_id: 连接 ID。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Raises:
            ApiError: 连接 ID 为空（13002）。
        """
        if not connection_id:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED, internal_detail="empty connection_id for wechat_mp"
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
        """执行公众号操作（桩实现）。

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
            ApiError: 连接不存在（13002）、操作不支持（13005）、授权过期（13003）、
                工具无响应（13004）、第三方业务错误（13006）、
                素材超限（20008）、未确认发布（20001）、重复发布（20007）。
        """
        self.validate_operation(operation, self.SUPPORTED_OPERATIONS)
        if not connection_id:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED, internal_detail="wechat_mp tool not connected"
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
        if operation == "upload_material":
            return self._upload_material(params)
        if operation == "create_draft":
            return self._create_draft(params)
        if operation == "publish_article":
            return self._publish_article(params, tenant_id)
        return self._list_articles(params)

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
        """公众号不支持数据同步任务（能力清单外）。

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

    def _upload_material(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造「上传素材」的桩结果。

        Args:
            params: 操作参数（`file_name` / `file_size`）。

        Returns:
            dict[str, Any]: 含素材标识的结果。

        Raises:
            ApiError: 素材超过限制（20008）或缺少文件名（20001）。
        """
        file_name = str(params.get("file_name", "")).strip()
        if not file_name:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="wechat_mp upload_material needs file_name"
            )
        file_size = int(params.get("file_size", 0) or 0)
        if file_size > MAX_MATERIAL_BYTES:
            raise ApiError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                message="素材超出大小限制（图文素材上限 10MB）",
                internal_detail=f"material_size={file_size}",
            )
        return {
            "material_id": new_resource_id("mat_"),
            "file_name": file_name,
            "file_size": max(file_size, 0),
        }

    def _create_draft(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造「保存草稿」的桩结果（`draft_id` 由标题 + 正文派生，天然幂等）。

        Args:
            params: 操作参数（`title` / `content` / `cover_material_id`）。

        Returns:
            dict[str, Any]: 含 `draft_id` 与「已保存为草稿」提示的结果。

        Raises:
            ApiError: 缺少标题或正文（20001）、正文含未授权外链（13006）。
        """
        title = str(params.get("title", "")).strip()
        content = str(params.get("content", "")).strip()
        if not title or not content:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail="wechat_mp create_draft requires title and content",
            )
        _reject_external_link(content)
        digest = hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()[:16]
        return {
            "draft_id": f"draft_{digest}",
            "title": title,
            "cover_material_id": str(params.get("cover_material_id", "")),
            "status": "draft",
            "message": "已保存为草稿，确认后可发布",
        }

    def _publish_article(self, params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        """构造「发布文章」的桩结果（两步确认 + 草稿幂等）。

        Args:
            params: 操作参数（`draft_id` / `confirmed` / `content`）。
            tenant_id: 租户 ID（幂等登记维度）。

        Returns:
            dict[str, Any]: 含 `article_id` 与发布时间的业务化结果。

        Raises:
            ApiError: 缺少草稿 ID（20001）、未确认发布（20001）、重复发布（20007）、
                正文含未授权外链（13006）。
        """
        draft_id = str(params.get("draft_id", "")).strip()
        if not draft_id:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="wechat_mp publish_article needs draft_id"
            )
        if not bool(params.get("confirmed", False)):
            raise ApiError(ErrorCode.PARAM_INVALID, message=CONFIRM_REQUIRED_MESSAGE)
        content = str(params.get("content", "")).strip()
        if content:
            _reject_external_link(content)
        key = (tenant_id, draft_id)
        if key in _PUBLISHED_DRAFTS:
            raise ApiError(
                ErrorCode.IDEMPOTENT_DUPLICATE,
                internal_detail=f"draft already published draft_id={draft_id}",
            )
        _PUBLISHED_DRAFTS.add(key)
        return {
            "article_id": new_resource_id("art_"),
            "draft_id": draft_id,
            "published_at": _now().isoformat().replace("+00:00", "Z"),
        }

    def _list_articles(self, params: dict[str, Any]) -> dict[str, Any]:
        """构造「查看文章数据」的桩结果（缺失统计返回 null，不返回 0）。

        Args:
            params: 操作参数（`limit`）。

        Returns:
            dict[str, Any]: 文章列表（桩实现为空）。
        """
        limit = int(params.get("limit", 20) or 20)
        return {"articles": [], "total": 0, "limit": limit}


def _reject_external_link(content: str) -> None:
    """拒绝含未授权外链的正文。

    Args:
        content: 文章正文。

    Raises:
        ApiError: 正文含外链（13006，PRD 原文文案）。
    """
    lowered = content.lower()
    if "http://" in lowered or "https://" in lowered:
        raise ApiError(ErrorCode.TOOL_THIRD_PARTY_ERROR, message=UNAUTHORIZED_LINK_MESSAGE)


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


__all__: list[str] = [
    "CONFIRM_REQUIRED_MESSAGE",
    "MAX_MATERIAL_BYTES",
    "UNAUTHORIZED_LINK_MESSAGE",
    "WechatMpConnector",
    "reset_publish_registry",
]
