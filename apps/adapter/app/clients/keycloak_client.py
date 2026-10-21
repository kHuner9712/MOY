"""身份底座（Keycloak）HTTP 客户端。

职责（架构文档 3.5.4）：
- 令牌自省（introspect）、用户资料、组织成员、角色管理的下游调用；
- 身份底座原生字段（`realm_access` / `firstName` / `attributes` 等）
  仅在本文件与 `services/identity_service.py` 内出现，
  对外一律转换为自研字段（`display_name` / `tenant` / `roles`）。

超时：连接 5s（架构文档 3.0.4）。
"""

from __future__ import annotations

from typing import Any, Final

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode, ErrorMapper
from app.core.logging import get_logger
from app.core.security import TRACE_HEADER

logger = get_logger(__name__)

#: 令牌自省端点路径模板
INTROSPECT_PATH: Final[str] = "/realms/{realm}/protocol/openid-connect/token/introspect"


class KeycloakClient:
    """身份底座客户端。

    Attributes:
        base_url: 身份底座地址（仅本类可见）。
        realm: 租户 realm 名。
        _client: httpx 异步客户端。
        _settings: 配置实例。
    """

    def __init__(
        self,
        base_url: str,
        realm: str,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化客户端。

        Args:
            base_url: 身份底座地址。
            realm: realm 名。
            settings: 可选配置实例。
            client: 可选的外部 httpx 客户端（测试注入用）。
        """
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                timeout=self._settings.keycloak_read_timeout_seconds,
                connect=self._settings.keycloak_connect_timeout_seconds,
            ),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        """关闭底层连接池（仅在本类自建客户端时生效）。"""
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        """构造请求头。

        Returns:
            dict[str, str]: 含链路透传的请求头。
        """
        import structlog.contextvars

        return {
            "Content-Type": "application/x-www-form-urlencoded",
            TRACE_HEADER: structlog.contextvars.get_contextvars().get("trace_id", "-"),
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        """发起一次身份底座请求并做错误映射。

        Args:
            method: HTTP 方法。
            path: 请求路径。
            data: 表单体。
            params: 查询参数。
            idempotent: 是否为幂等请求（决定是否允许重试）。

        Returns:
            dict[str, Any]: 解析后的 JSON 响应。

        Raises:
            ApiError: 超时（30002）、连接失败（30001）、业务错误（经 ErrorMapper 映射）。
        """
        attempts = self._settings.dify_max_retries + 1 if idempotent else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, path, data=data, params=params, headers=self._headers()
                )
            except httpx.TimeoutException as exc:
                logger.warning("keycloak_timeout", path=path, attempt=attempt + 1)
                if attempt + 1 < attempts:
                    continue
                raise ApiError(
                    ErrorCode.DOWNSTREAM_TIMEOUT, internal_detail=f"path={path}"
                ) from exc
            except httpx.HTTPError as exc:
                logger.warning("keycloak_connect_failed", path=path, attempt=attempt + 1)
                if attempt + 1 < attempts:
                    continue
                raise ApiError(
                    ErrorCode.DOWNSTREAM_UNAVAILABLE, internal_detail=f"path={path}"
                ) from exc

            if response.status_code >= 400:
                logger.warning(
                    "keycloak_business_error", path=path, status_code=response.status_code
                )
                raise ErrorMapper.to_api_error(
                    response.status_code, response.text, internal_detail=f"path={path}"
                )

            try:
                parsed = response.json()
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {"data": parsed}

        raise ApiError(ErrorCode.DOWNSTREAM_UNAVAILABLE, internal_detail=f"path={path}")

    async def introspect(self, token: str) -> dict[str, Any]:
        """令牌自省。

        Args:
            token: 待校验令牌原文（严禁写入日志）。

        Returns:
            dict[str, Any]: 自省结果，含 `active` 标识。

        Raises:
            ApiError: 下游不可用（30001）或超时（30002）。
        """
        logger.debug("keycloak_introspect", realm=self.realm)
        return await self._request(
            "POST",
            INTROSPECT_PATH.format(realm=self.realm),
            data={
                "client_id": self._settings.keycloak_client_id,
                "client_secret": self._settings.keycloak_client_secret,
                "token": token,
            },
            idempotent=False,
        )

    async def get_user(self, user_id: str) -> dict[str, Any]:
        """查询用户资料。

        Args:
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 身份底座原生用户对象（仅服务层消费）。

        Raises:
            ApiError: 下游不可用（30001）或用户不存在（映射为 14001）。
        """
        logger.debug("keycloak_get_user", user_id=user_id)
        try:
            return await self._request("GET", f"/admin/realms/{self.realm}/users/{user_id}")
        except ApiError as exc:
            if exc.code == ErrorCode.RESOURCE_NOT_FOUND:
                raise ApiError(
                    ErrorCode.USER_NOT_FOUND, internal_detail=f"user_id={user_id}"
                ) from exc
            raise

    async def list_group_members(self, org_id: str) -> list[dict[str, Any]]:
        """查询组织成员列表。

        Args:
            org_id: 组织 ID（身份底座 group 名）。

        Returns:
            list[dict[str, Any]]: 成员列表（身份底座原生对象）。

        Raises:
            ApiError: 下游不可用（30001）。
        """
        logger.debug("keycloak_list_group_members", org_id=org_id)
        payload = await self._request("GET", f"/admin/realms/{self.realm}/groups/{org_id}/members")
        members = payload.get("members", [])
        return members if isinstance(members, list) else []

    async def create_role(self, role_key: str, display_name: str) -> dict[str, Any]:
        """创建角色。

        Args:
            role_key: 角色标识。
            display_name: 角色展示名。

        Returns:
            dict[str, Any]: 创建结果。

        Raises:
            ApiError: 下游不可用（30001）。
        """
        logger.debug("keycloak_create_role", role_key=role_key)
        return await self._request(
            "POST",
            f"/admin/realms/{self.realm}/roles",
            data={"name": role_key, "description": display_name},
        )


_client: KeycloakClient | None = None


def get_keycloak_client() -> KeycloakClient:
    """获取身份底座客户端单例。

    Returns:
        KeycloakClient: 进程内唯一实例。
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = KeycloakClient(
            base_url=settings.keycloak_base_url,
            realm=settings.keycloak_realm,
            settings=settings,
        )
    return _client


def reset_keycloak_client() -> None:
    """重置身份底座客户端单例，供测试隔离用例。"""
    global _client
    _client = None
