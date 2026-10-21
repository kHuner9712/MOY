"""令牌解析与租户上下文（架构文档 3.0.2 鉴权方式）。

职责边界（架构文档 1.2 / 3.5.1）：
- 适配层**只做令牌校验与请求转发**，不做登录、不签发令牌；
- 用户/组织/角色权威数据在身份底座，权限策略判断在业务能力层；
- 私有化模式默认租户 `default`，SaaS 模式从令牌声明或 `X-Tenant-Id` 头解析。

安全约束：日志中严禁记录令牌原文（架构文档 9.3）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Final

from fastapi import Depends, Header, Request

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import TENANT_ID_KEY, USER_ID_KEY, bind_context, get_logger

logger = get_logger(__name__)

#: 租户请求头
TENANT_HEADER: Final[str] = "X-Tenant-Id"

#: 幂等键请求头
IDEMPOTENCY_HEADER: Final[str] = "Idempotency-Key"

#: 链路追踪请求头（下游透传同一 trace_id）
TRACE_HEADER: Final[str] = "X-Trace-Id"

_BEARER_PREFIX: Final[str] = "bearer "


@dataclass(frozen=True, slots=True)
class AuthContext:
    """鉴权上下文。

    Attributes:
        user_id: 用户 ID，形如 `usr_01HF8...`。
        tenant_id: 租户 ID，形如 `tnt_01HF8...`；私有化模式为 `default`。
        roles: 角色列表。
        email: 用户邮箱（可为空）。
        claims: 令牌声明原始载荷（已剔除敏感项，仅内部使用）。
    """

    user_id: str
    tenant_id: str
    roles: list[str] = field(default_factory=list)
    email: str = ""
    claims: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def resolve(cls, token: str, settings: Settings | None = None) -> AuthContext:
        """解析 Bearer 令牌，得到鉴权上下文。

        Args:
            token: 去除 `Bearer ` 前缀后的令牌原文。
            settings: 可选配置，缺省读取全局单例。

        Returns:
            AuthContext: 鉴权上下文。

        Raises:
            ApiError: 令牌缺失（20002）、签名/过期校验失败（20002）、
                声明缺少用户标识（20002）。
        """
        conf = settings or get_settings()
        if not token or not token.strip():
            raise ApiError(ErrorCode.UNAUTHORIZED, internal_detail="empty bearer token")

        claims = _decode_token(token.strip(), conf)
        user_id = str(claims.get("sub") or claims.get("user_id") or "").strip()
        if not user_id:
            raise ApiError(ErrorCode.UNAUTHORIZED, internal_detail="token has no subject claim")

        tenant_id = str(claims.get("tenant_id") or "").strip() or _default_tenant(conf)
        roles = _extract_roles(claims)
        email = str(claims.get("email") or "").strip()

        logger.debug(
            "auth_context_resolved",
            user_id=user_id,
            tenant_id=tenant_id,
            roles=roles,
            auth_mode=conf.auth_mode,
        )
        return cls(user_id=user_id, tenant_id=tenant_id, roles=roles, email=email, claims=claims)


def _default_tenant(conf: Settings) -> str:
    """获取默认租户 ID。

    Args:
        conf: 配置实例。

    Returns:
        str: 默认租户 ID。
    """
    return conf.default_tenant_id


def _extract_roles(claims: dict[str, Any]) -> list[str]:
    """从令牌声明中提取角色列表。

    兼容两种常见形态：`roles` 顶层数组、`realm_access.roles` 嵌套数组。

    Args:
        claims: 令牌声明载荷。

    Returns:
        list[str]: 角色列表，未命中时为空列表。
    """
    raw = claims.get("roles")
    if not isinstance(raw, list):
        realm_access = claims.get("realm_access")
        if isinstance(realm_access, dict):
            raw = realm_access.get("roles")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _decode_token(token: str, conf: Settings) -> dict[str, Any]:
    """校验并解码 JWT 令牌。

    `auth_mode=stub` 时仅解码不验签（开发态）；`oidc` 时强制验签并校验
    过期时间、签发者与受众。

    Args:
        token: 令牌原文。
        conf: 配置实例。

    Returns:
        dict[str, Any]: 令牌声明载荷。

    Raises:
        ApiError: 令牌非法或校验失败（20002）。
    """
    try:
        from jose import jwt
    except ImportError as exc:  # pragma: no cover - 依赖缺失属环境问题
        raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="python-jose not installed") from exc

    options: dict[str, Any] = {"verify_signature": conf.auth_mode != "stub"}
    if conf.auth_mode == "stub":
        options["verify_aud"] = False
        options["verify_iss"] = False

    try:
        claims = jwt.decode(
            token,
            conf.jwt_secret,
            algorithms=[conf.jwt_algorithm],
            audience=conf.jwt_audience,
            issuer=conf.jwt_issuer,
            options=options,
        )
    except Exception as exc:
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            internal_detail=f"token validation failed: {type(exc).__name__}",
        ) from exc

    if not isinstance(claims, dict):  # pragma: no cover - jose 保证返回 dict
        raise ApiError(ErrorCode.UNAUTHORIZED, internal_detail="token claims not a mapping")
    return claims


def extract_bearer_token(request: Request) -> str:
    """从请求头提取 Bearer 令牌。

    Args:
        request: FastAPI 请求对象。

    Returns:
        str: 令牌原文（不含 `Bearer ` 前缀）。

    Raises:
        ApiError: 缺少或格式非法的 Authorization 头（20002）。
    """
    header = request.headers.get("Authorization", "")
    if not header:
        raise ApiError(ErrorCode.UNAUTHORIZED, internal_detail="missing Authorization header")
    lowered = header.lower()
    if not lowered.startswith(_BEARER_PREFIX):
        raise ApiError(
            ErrorCode.UNAUTHORIZED, internal_detail="Authorization scheme must be Bearer"
        )
    return header[len(_BEARER_PREFIX) :].strip()


def resolve_tenant_id(request: Request, auth: AuthContext, settings: Settings | None = None) -> str:
    """解析租户 ID。

    优先级：`X-Tenant-Id` 请求头 > 令牌 `tenant_id` 声明 > 配置默认值。
    私有化模式忽略请求头，恒返回 `default`（单租户天然隔离）。

    Args:
        request: FastAPI 请求对象。
        auth: 已解析的鉴权上下文。
        settings: 可选配置实例。

    Returns:
        str: 最终生效的租户 ID。

    Raises:
        ApiError: SaaS 模式下租户头与令牌声明均缺失（20001）。
    """
    conf = settings or get_settings()
    if conf.is_private_mode:
        return _default_tenant(conf)

    header_tenant = request.headers.get(TENANT_HEADER, "").strip()
    if header_tenant:
        return header_tenant
    if auth.tenant_id and auth.tenant_id != _default_tenant(conf):
        return auth.tenant_id
    if conf.deploy_mode == "saas" and not header_tenant and not auth.claims.get("tenant_id"):
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            internal_detail=f"missing {TENANT_HEADER} header and tenant_id claim",
        )
    return _default_tenant(conf)


def require_permission(auth: AuthContext, permission: str) -> None:
    """校验权限点（统一校验出口，架构文档 3.5.1 设计原则）。

    本期（T01）为桩实现：不做业务策略判断，仅在角色显式声明时校验。
    T02 起接入真实权限点映射表，调用方保持不变。

    Args:
        auth: 鉴权上下文。
        permission: 权限点，形如 `resource:action`。

    Raises:
        ApiError: 权限不足（20003）。
    """
    if not permission:
        return
    if "*" in auth.roles:
        return
    if permission in auth.roles:
        return
    # T01 桩模式：无显式角色声明时放行，由业务能力层做最终判权（架构文档 3.5.1）
    if not auth.roles:
        logger.debug("permission_check_deferred", permission=permission, user_id=auth.user_id)
        return
    raise ApiError(
        ErrorCode.FORBIDDEN,
        internal_detail=f"permission={permission} roles={auth.roles}",
    )


# ===========================================================================
# FastAPI 依赖（鉴权 / 租户 / 幂等）
#
# 放在本模块而非 api 层，是为了让「鉴权转发」这一职责有唯一归属：
# 架构文档 3.0.2 规定适配层只做令牌校验与请求转发，不做登录与签发。
# ===========================================================================


class RequestContext:
    """请求上下文（鉴权 + 租户 + 链路）。

    Attributes:
        auth: 鉴权上下文。
        tenant_id: 生效租户 ID。
        trace_id: 链路追踪 ID。
    """

    __slots__ = ("auth", "tenant_id", "trace_id")

    def __init__(self, auth: AuthContext, tenant_id: str, trace_id: str) -> None:
        """初始化请求上下文。

        Args:
            auth: 鉴权上下文。
            tenant_id: 生效租户 ID。
            trace_id: 链路追踪 ID。
        """
        self.auth = auth
        self.tenant_id = tenant_id
        self.trace_id = trace_id

    @property
    def user_id(self) -> str:
        """当前用户 ID。"""
        return self.auth.user_id


async def get_auth_context(request: Request) -> AuthContext:
    """FastAPI 依赖：解析鉴权上下文。

    除 `/healthz`、`/readyz` 外的所有端点都必须声明本依赖。

    Args:
        request: FastAPI 请求对象。

    Returns:
        AuthContext: 鉴权上下文。

    Raises:
        ApiError: 令牌缺失或校验失败（20002）。
    """
    auth = AuthContext.resolve(extract_bearer_token(request))
    request.state.user_id = auth.user_id
    request.state.tenant_id = auth.tenant_id
    bind_context(**{TENANT_ID_KEY: auth.tenant_id, USER_ID_KEY: auth.user_id})
    return auth


async def get_request_context(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> RequestContext:
    """FastAPI 依赖：构造请求上下文（鉴权 + 租户解析）。

    Args:
        request: FastAPI 请求对象。
        auth: 鉴权上下文。

    Returns:
        RequestContext: 请求上下文。

    Raises:
        ApiError: SaaS 模式缺少租户标识（20001）。
    """
    tenant_id = resolve_tenant_id(request, auth)
    request.state.tenant_id = tenant_id
    return RequestContext(
        auth=auth,
        tenant_id=tenant_id,
        trace_id=str(getattr(request.state, "trace_id", "-")),
    )


#: 幂等键请求头依赖（写接口使用）
IdempotencyKey = Annotated[
    str | None,
    Header(alias=IDEMPOTENCY_HEADER, description="写操作幂等键（UUID v4，24h 内有效）"),
]


async def require_idempotency_key(key: IdempotencyKey) -> str:
    """FastAPI 依赖：提取并校验 `Idempotency-Key` 请求头。

    所有写接口按架构文档 3.0.4 必须携带该头。

    Args:
        key: 请求头值。

    Returns:
        str: 规范化后的幂等键。

    Raises:
        ApiError: 缺少幂等键（20001 请求内容不完整）。
    """
    normalized = (key or "").strip()
    if not normalized:
        raise ApiError(
            ErrorCode.PARAM_INVALID, internal_detail=f"missing {IDEMPOTENCY_HEADER} header"
        )
    return normalized


async def run_idempotent(
    request: Request,
    context: RequestContext,
    factory: Callable[[], Awaitable[Any]],
    manager: Any | None = None,
) -> tuple[Any, bool]:
    """执行带幂等保护的写操作。

    Args:
        request: FastAPI 请求对象（用于读取幂等键与构造指纹）。
        context: 请求上下文。
        factory: 无参异步可调用对象，返回业务数据。
        manager: 可选的幂等管理器，缺省取全局单例。

    Returns:
        tuple[Any, bool]: `(结果, 是否重复请求)`。
    """
    from app.core.idempotency import (
        build_fingerprint,
        get_idempotency_manager,
    )

    effective = manager or get_idempotency_manager()
    key = request.headers.get(IDEMPOTENCY_HEADER, "")
    fingerprint = build_fingerprint(
        request.method, request.url.path, context.tenant_id, context.user_id
    )
    return await effective.execute(key, fingerprint, factory)
