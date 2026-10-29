"""令牌解析与租户上下文（架构文档 3.0.2 鉴权方式）。

职责边界（架构文档 1.2 / 3.5.1）：
- 适配层**只做令牌校验与请求转发**，不做登录、不签发令牌；
- 用户/组织/角色权威数据在身份底座，权限策略判断在业务能力层；
- 私有化模式默认租户 `default`，SaaS 模式从令牌声明或 `X-Tenant-Id` 头解析。

安全约束：日志中严禁记录令牌原文（架构文档 9.3）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import Depends, Header, Request

from app.core.config import Settings, get_request_settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import TENANT_ID_KEY, USER_ID_KEY, bind_context, get_logger
from app.core.ratelimit import get_request_rate_limiter

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


#: 延迟判权白名单（**恰好 2 条**，架构 §2.5.2）。
#:
#: 只有这两处允许"无角色令牌"通过 `require_permission` 进入业务能力层做对象级判定；
#: 其余一律 fail-closed。集合由 `docs/security/permission-whitelist.lock` 快照锁定，
#: `tests/security/test_permission_fail_closed.py` 断言其**未扩张**
#: （`test_exactly_two_entries` / `test_lock_file_matches_code` 等 7 条用例）。
#:
#: 键是 `(权限点, HTTP 方法)`，值是"为什么必须委派"的理由。
PERMISSION_WHITELIST: Final[dict[tuple[str, str], str]] = {
    ("knowledge:base:read", "GET"): (
        "W-1：知识库可见性是**对象级**判定的（按 kb 授权与可见范围逐库过滤），"
        "角色矩阵无法表达；通用角色（member）本就没有该权限点，"
        "在这里拒绝会让所有普通成员连自己的库都看不见。"
    ),
    ("tool:connection:manage", "GET"): (
        "W-2：TL1 调用记录要求「连接管理者」视角，但「能不能看这条记录」取决于"
        "该连接是否属于调用者本人（对象级），角色矩阵同样表达不了；"
        "只放开 GET 这一个方法，写操作仍走矩阵。"
    ),
}


def require_permission(auth: AuthContext, permission: str, *, method: str = "") -> None:
    """校验权限点（统一校验出口，架构文档 3.5.1 / 阶段四 §2.5 fail-closed）。

    判定顺序：

    1. 通配角色 `*`（平台运维令牌）放行；
    2. 令牌角色本身就是权限点时放行（阶段一"权限点即角色"形态）；
    3. 按内置角色矩阵展开后判定；
    4. **无角色声明 → 拒绝（fail-closed）**，白名单里那两条除外。

    第 4 条是阶段四的**行为变更**（F-1）：此前"无角色"会被静默放行到业务层，
    等于"令牌里不带角色声明 = 绕过全部端点级权限"。现在只有白名单允许委派，
    且拒绝时由 `guard_permission` 写一条操作记录。

    Args:
        auth: 鉴权上下文。
        permission: 权限点，形如 `resource:action`。
        method: HTTP 方法（大写）。白名单按"权限点 + 方法"精确匹配，
            因此同一个权限点的写操作不会被顺带放开。

    Raises:
        ApiError: 权限不足（20003）。
    """
    if not permission:
        return
    roles = {str(role).strip() for role in auth.roles if str(role).strip()}
    if "*" in roles:
        return
    if permission in roles:
        return
    # 延迟导入：`business.policy_service` 依赖本模块的 `AuthContext`，
    # 模块级互引会成环（导入顺序敏感），故在函数内导入。
    from app.business.policy_service import permissions_for_roles

    if permission in permissions_for_roles(roles):
        return
    # 无角色声明：只有白名单允许进入业务层做对象级判定，其余一律拒绝（fail-closed）
    if not roles:
        if (permission, method.upper()) in PERMISSION_WHITELIST:
            # 全仓**唯一**允许出现 `permission_check_deferred` 的位置
            logger.info(
                "permission_check_deferred",
                permission=permission,
                method=method.upper(),
                user_id=auth.user_id,
                reason="whitelisted",
            )
            return
        logger.warning(
            "permission_denied_no_roles",
            permission=permission,
            method=method.upper(),
            user_id=auth.user_id,
            tenant_id=auth.tenant_id,
        )
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=f"no roles declared; permission={permission} method={method or '-'}",
        )
    raise ApiError(
        ErrorCode.FORBIDDEN,
        internal_detail=f"permission={permission} roles={auth.roles}",
    )


async def guard_permission(
    auth: AuthContext, permission: str, *, method: str = "", audit: Any | None = None
) -> None:
    """权限校验 + **拒绝时写操作记录**（端点统一入口）。

    为什么单独包一层而不是把审计塞进 `require_permission`：后者是**同步**函数，
    被 11 处端点与 `identity_service` 共用；改成异步会波及全部调用点与既有用例。
    审计只在"拒绝"这一条路径上需要，因此用异步包装收口。

    Args:
        auth: 鉴权上下文。
        permission: 权限点。
        method: HTTP 方法（大写）。
        audit: 可选审计服务（缺省取全局单例）。

    Raises:
        ApiError: 权限不足（20003）。
    """
    try:
        require_permission(auth, permission, method=method)
    except ApiError as exc:
        if exc.code == ErrorCode.FORBIDDEN:
            await _record_permission_denied(auth, permission, method, audit)
        raise


async def _record_permission_denied(
    auth: AuthContext, permission: str, method: str, audit: Any | None
) -> None:
    """写一条"权限被拒"的操作记录（失败不阻断，拒绝本身已经生效）。

    Args:
        auth: 鉴权上下文。
        permission: 权限点。
        method: HTTP 方法。
        audit: 可选审计服务。
    """
    from app.business.audit_service import ACTION_PERMISSION_DENIED, AuditService

    service: Any = audit
    if service is None:
        try:
            from app.business.audit_service import get_audit_service

            service = get_audit_service()
        except Exception as exc:  # pragma: no cover - 配置异常时不影响拒绝结果
            logger.warning("permission_audit_unavailable", detail=type(exc).__name__)
            return
    if not isinstance(service, AuditService):
        return
    try:
        await service.record(
            tenant_id=auth.tenant_id or "",
            actor_id=auth.user_id or "",
            action=ACTION_PERMISSION_DENIED,
            resource_type="permission",
            resource_id=permission,
            result="failure",
            change_summary={"method": method.upper(), "roles": list(auth.roles)},
        )
    except Exception as exc:  # 审计失败不改变"已拒绝"这个事实
        logger.warning("permission_audit_failed", detail=type(exc).__name__)


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

    配置来源：`app.state.settings`（`create_app(settings=...)` 注入），
    而非全局单例——否则注入 `auth_mode=oidc` 会被降级为 stub，
    伪造令牌即可登录（QA BUG-01）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        AuthContext: 鉴权上下文。

    Raises:
        ApiError: 令牌缺失或校验失败（20002）。
    """
    settings = get_request_settings(request)
    auth = AuthContext.resolve(extract_bearer_token(request), settings)
    request.state.user_id = auth.user_id
    request.state.tenant_id = auth.tenant_id
    bind_context(**{TENANT_ID_KEY: auth.tenant_id, USER_ID_KEY: auth.user_id})
    return auth


def _has_cross_tenant_privilege(auth: AuthContext) -> bool:
    """判断令牌是否为「可跨租户」的平台运维令牌。

    **直接复用 `is_platform_scope`，不再另写一套条件**：两处口径曾经各自演进
    （这里只看 `__platform__` 哨兵、那里看哨兵或 `*`），结果收紧其中一处时
    另一处仍按旧口径放行，形成「看起来修了、实际还能进」的形态（SEC-02）。
    跨租户特权与平台视角本来就是同一件事，只保留一个判定出口。

    Args:
        auth: 鉴权上下文（**令牌原始值**，不是收敛后的值）。

    Returns:
        bool: 具备跨租户特权返回 True。
    """
    return is_platform_scope(auth, auth.tenant_id)


async def assert_tenant_membership(auth: AuthContext, tenant_id: str, settings: Settings) -> None:
    """校验「令牌持有人确实是目标租户的成员」（AR-017 租户伪造修复）。

    **为什么需要**：`resolve_tenant_id` 让 `X-Tenant-Id` 请求头优先于令牌声明，
    它本身只回答「请求想作用于哪个租户」，不回答「这个人能不能碰这个租户」。
    修复前这个缺口被直接透传：持 A 租户令牌 + `X-Tenant-Id: B` 即可读写 B 的数据。

    成员关系的权威口径与 `identity_service.list_members` 一致：
    `department_member`（组织归属）**并集** `member_role`（角色归属），
    任一非空即视为该租户成员。

    Args:
        auth: 鉴权上下文（令牌原始租户）。
        tenant_id: 请求生效的租户（可能与令牌租户不同）。
        settings: 配置实例（决定内存/PG 后端）。

    Returns:
        None: 校验通过时无返回值。

    Raises:
        ApiError: 用户既非组织成员也无角色（20003）。
    """
    # 延迟导入：仓储层依赖 `app.core`（config/errors/logging），
    # 在 core 层做模块级导入会成环。做法与 `require_permission` 引入
    # `business.policy_service` 一致。
    from app.db.repositories.org import get_org_store
    from app.db.repositories.role import get_role_store

    user_id = (auth.user_id or "").strip()
    if not user_id:  # pragma: no cover - `AuthContext.resolve` 已保证非空
        raise ApiError(ErrorCode.FORBIDDEN, internal_detail="tenant membership required")

    links = await get_org_store(settings).links_of_user(tenant_id, user_id)
    if not links:
        role_ids = await get_role_store(settings).role_ids_of_user(tenant_id, user_id)
        if not role_ids:
            logger.warning(
                "tenant_switch_denied",
                user_id=user_id,
                token_tenant=auth.tenant_id,
                requested_tenant=tenant_id,
            )
            raise ApiError(ErrorCode.FORBIDDEN, internal_detail="tenant membership required")
    logger.info(
        "tenant_switch_allowed",
        user_id=user_id,
        token_tenant=auth.tenant_id,
        requested_tenant=tenant_id,
    )


def _should_check_membership(auth: AuthContext, settings: Settings) -> bool:
    """判断本次跨租户切换是否需要做成员关系校验（回退开关）。

    开关语义见 `Settings.tenant_membership_check`：

    - `off`：跳过（紧急回退，属于安全降级，仅在线上止血时使用）；
    - `always`：总是校验；
    - `auto`：仅 `deploy_mode == "saas"` 且 `auth_mode != "stub"` 时校验。

    `auto` 为什么排除 stub：stub 不验签，本地联调常用手编令牌+任意租户头，
    强校验会让联调流程失效；而 stub 在 staging/production 已被
    `validate_auth_startup` 拦成启动失败，不存在"线上靠自动档漏校验"的窗口。

    Args:
        auth: 鉴权上下文（仅在 `off` 档写告警日志时使用，不参与判定）。
        settings: 配置实例。

    Returns:
        bool: 需要校验返回 True。
    """
    mode = (settings.tenant_membership_check or "auto").strip().lower()
    if mode == "off":
        logger.warning(
            "tenant_membership_check_disabled",
            user_id=auth.user_id,
            token_tenant=auth.tenant_id,
            hint="安全降级：跨租户切换未做成员校验，止血后必须改回 auto",
        )
        return False
    if mode == "always":
        return True
    return settings.deploy_mode == "saas" and settings.auth_mode != "stub"


async def get_request_context(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> RequestContext:
    """FastAPI 依赖：构造请求上下文（鉴权 + 租户解析）。

    租户解析同样使用 `app.state.settings`：私有化模式的单租户隔离
    依赖 `deploy_mode=private` 判定，读取全局单例会导致隔离失效（QA BUG-02）。

    **解析后的租户是唯一权威值**：`AuthContext.tenant_id` 原始值来自令牌声明，
    私有化模式下必须被强制为 `default`；此处以 `dataclasses.replace` 回写，
    避免下游服务（identity_service 等）继续使用未收敛的原始租户。

    **跨租户切换必须验成员关系（AR-017）**：`X-Tenant-Id` 优先于令牌声明，
    若不做成员校验，任何人都能用自己租户的令牌读写任意租户。因此当
    「生效租户 ≠ 令牌租户」时，只允许两种情形：

    1. 平台运维令牌（角色含 `*`，或角色含运营角色且租户声明 `__platform__`）
       ——职责就是跨租户；
    2. 该用户在目标租户里确有组织归属或角色归属（多租户成员的真实场景）。

    私有化模式不参与校验：此时生效租户被强制收敛为 `default`，
    「不等」只反映「令牌声明被忽略」，而非跨租户访问（见 QA BUG-02）。

    **`__platform__` 请求头必须在查库之前拒掉**：平台租户哨兵不是真实租户，
    它一旦流入仓储层（`tenant_scope("__platform__")`）会被 RLS 拒绝并抛成
    HTTP 500——企业令牌伪造该头时应得到明确的 20003，而不是一个"看起来像故障"的 500。

    性能：同租户请求（绝大多数）不触发任何额外查询。

    Args:
        request: FastAPI 请求对象。
        auth: 鉴权上下文。

    Returns:
        RequestContext: 请求上下文。

    Raises:
        ApiError: SaaS 模式缺少租户标识（20001）；跨租户且非成员（20003）；
            非平台令牌伪造 `__platform__` 请求头（20003）。
    """
    settings = get_request_settings(request)
    tenant_id = resolve_tenant_id(request, auth, settings)
    if tenant_id != auth.tenant_id:
        privileged = _has_cross_tenant_privilege(auth)
        if tenant_id == OPS_TENANT_ID and not privileged:
            # 先于任何仓储查询拒绝：`__platform__` 不是真实租户，流入 RLS 会变 500
            logger.warning(
                "tenant_platform_header_denied",
                user_id=auth.user_id,
                token_tenant=auth.tenant_id,
                requested_tenant=tenant_id,
            )
            raise ApiError(ErrorCode.FORBIDDEN, internal_detail="platform tenant header forbidden")
        if not privileged and _should_check_membership(auth, settings):
            await assert_tenant_membership(auth, tenant_id, settings)
        # 收敛为最终生效的租户，杜绝下游绕过隔离
        auth = replace(auth, tenant_id=tenant_id)
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


async def enforce_ratelimit(
    request: Request,
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> RequestContext:
    """限流依赖（架构文档 3.0.3 错误码 20006）。

    作为**路由级依赖**挂在五大业务路由上，位置在鉴权之后：
    租户与用户标识此时已确定，可按「租户 + 用户 + 路径」维度限流。

    限流器取自 `app.state.rate_limiter`（由 `create_app` 按注入配置构造），
    缺省回落到全局单例。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。

    Returns:
        RequestContext: 原样返回请求上下文。

    Raises:
        ApiError: 触发限流（20006）。
    """
    limiter = get_request_rate_limiter(request)
    if limiter.enabled:
        await limiter.check(context.tenant_id, context.user_id, request.url.path)
    return context


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
    body_digest: str | None = None,
) -> tuple[Any, bool]:
    """执行带幂等保护的写操作。

    幂等指纹按架构文档 3.0.5 的**唯一正确口径**构造，含请求体规范化 SHA-256 摘要：
    同一 `Idempotency-Key` 配同 method/path/tenant/user 但不同请求体时，
    会被识别为键复用冲突（20005）而非静默回放首次结果。

    Args:
        request: FastAPI 请求对象（用于读取幂等键与构造指纹）。
        context: 请求上下文。
        factory: 无参异步可调用对象，返回业务数据。
        manager: 可选的幂等管理器，缺省取全局单例。
        body_digest: 可选的请求体摘要覆盖值。文件上传（multipart）场景由路由层
            传入文件内容摘要——multipart 请求体已被表单解析器消费，无法二次读取。

    Returns:
        tuple[Any, bool]: `(结果, 是否重复请求)`。
    """
    from app.core.idempotency import (
        build_fingerprint,
        get_idempotency_manager,
    )

    effective = manager or get_idempotency_manager()
    key = request.headers.get(IDEMPOTENCY_HEADER, "")
    digest = body_digest if body_digest is not None else await _read_body_digest(request)
    fingerprint = build_fingerprint(
        request.method,
        request.url.path,
        context.tenant_id,
        context.user_id,
        digest,
    )
    return await effective.execute(key, fingerprint, factory, tenant_id=context.tenant_id)


async def _read_body_digest(request: Request) -> str:
    """读取请求体并计算规范化摘要（架构文档 3.0.5）。

    请求体在 FastAPI 解析后已被缓存于 `request._body`，此处安全读取。
    文件上传（multipart）场景的请求体已被表单解析器消费，无法二次读取，
    由路由层显式传入 `body_digest`。

    Args:
        request: FastAPI 请求对象。

    Returns:
        str: 规范化摘要十六进制串。
    """
    from app.core.idempotency import hash_request_body

    try:
        body = await request.body()
    except Exception:
        return ""
    return hash_request_body(body)


# ===========================================================================
# 运营后台（B10）：平台模式守卫
#
# 三道防线（缺一即越权，架构文档 §3.4 / §4.6）：
#   1. 本函数：企业令牌一律 20003（**不返回 404**，避免探测运维接口是否存在）；
#   2. 权限点：令牌角色必须能展开出 ops:* 权限点（fail-closed，未知角色不贡献权限）；
#   3. 数据访问：跨租户查询只能走 `db/session.platform_scope()`（allow_platform=True）。
# ===========================================================================

#: 平台租户哨兵（运营令牌的租户声明；与 RLS 策略里的哨兵一致）
OPS_TENANT_ID: Final[str] = "__platform__"

#: 运营后台默认权限点
OPS_CONSOLE_PERMISSION: Final[str] = "ops:console:access"

#: 通配角色（平台运维令牌）
OPS_WILDCARD_ROLE: Final[str] = "*"

#: 平台运维角色键（与 `business.policy_service.ROLE_OPS` 的取值**必须**保持一致）。
#:
#: 为什么在 core 层写字面量而不 import `ROLE_OPS`：`policy_service` 依赖
#: `app.core.security`（`AuthContext` / `require_permission` 的判权出口），
#: 反向导入会形成循环依赖，模块直接 ImportError。代价是「改一处要同步另一处」，
#: 因此 `tests/security/test_attack_agent_task_tenant.py` 里有一条用例
#: 断言本常量与 `ROLE_OPS` 逐字相等，任何一边漂移都会变红。
OPS_ROLE_KEYS: Final[frozenset[str]] = frozenset({OPS_WILDCARD_ROLE, "ops_operator"})


def is_platform_scope(auth: AuthContext, tenant_id: str = "") -> bool:
    """判断当前令牌是否具备平台视角。

    判定口径（两条，缺一不可）：

    1. 令牌带通配角色 `*`（平台运维令牌的显式标记）；
    2. **或** 生效租户为平台哨兵 `__platform__` **且** 令牌角色含平台运维角色
       （`OPS_ROLE_KEYS`：`*` / `ops_operator`）。

    为什么第 2 条要加角色条件（SEC-02）：只认「租户声明 = `__platform__`」等于
    把判权交给一个**可被签发改写**的字段——令牌里写什么租户就获得什么视角。
    平台视角意味着「跨全部租户读数据」，必须额外要求一个运营角色声明，
    与企业令牌要求 `role` 才能进业务接口保持同一强度。

    Args:
        auth: 鉴权上下文。
        tenant_id: 收敛后的生效租户 ID（缺省用 `auth.tenant_id`）。

    Returns:
        bool: 具备平台视角返回 True。
    """
    roles = {str(role).strip().lower() for role in auth.roles if str(role).strip()}
    if OPS_WILDCARD_ROLE in roles:
        return True
    effective = (tenant_id or auth.tenant_id or "").strip()
    if effective != OPS_TENANT_ID:
        return False
    return bool(roles & OPS_ROLE_KEYS)


async def require_ops_access(
    request: Request,
    context: RequestContext,
    permission: str = OPS_CONSOLE_PERMISSION,
) -> str:
    """校验运营后台访问权（`/ops/**` 的统一入口）。

    企业令牌调用任一 `/ops/**` → `20003`（**不返回 404**，避免探测）；
    平台令牌还必须在权限点矩阵里拥有对应 `ops:*` 权限点。

    运营角色来自**平台令牌**而非租户业务库（运营账号不是任何企业的成员，
    D-13 的「业务库权威」只适用于租户内角色），因此这里直接按令牌角色展开
    权限点，不做业务库查询——也避免在平台作用域下读租户表导致的空转。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        permission: 所需 `ops:*` 权限点。

    Returns:
        str: 平台租户哨兵（供 `platform_scope()` 使用）。

    Raises:
        ApiError: 非平台视角或权限点不足（20003）。
    """
    from app.business.policy_service import permissions_for_roles

    del request  # 平台判定不依赖请求头之外的配置，保留参数以便未来接入白名单
    if not is_platform_scope(context.auth, context.tenant_id):
        logger.warning(
            "ops_access_denied",
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            permission=permission,
        )
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=(
                f"ops endpoint requires platform scope, tenant_id={context.tenant_id} "
                f"roles={context.auth.roles}"
            ),
        )
    points = permissions_for_roles(context.auth.roles)
    if permission not in points:
        logger.warning(
            "ops_permission_denied",
            user_id=context.user_id,
            permission=permission,
            roles=context.auth.roles,
        )
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=f"permission={permission} roles={context.auth.roles}",
        )
    return OPS_TENANT_ID


#: 允许使用桩鉴权（不验签）的环境白名单。
#: 只要环境不在其中，`auth_mode=stub` 一律视为配置错误——这是 S1 的收口点。
_INSECURE_AUTH_ENVS: Final[frozenset[str]] = frozenset({"development", "test"})


def validate_auth_startup(settings: Settings | None = None) -> None:
    """启动期鉴权防呆（OA-REVIEW-002 / S1）。

    **为什么必须在启动时拦**：`auth_mode` 的默认值是 `stub`，而 `stub` 下
    `verify_signature=False`——任何人都能自造 JWT 声明任意租户与角色。
    这不是「有人配错了」，而是「不配就是关闭鉴权」，属于默认态风险。

    判据：
    1. `env in {staging, production}` 且 `auth_mode == "stub"` → 30004 启动失败；
    2. 同一环境下 `jwt_secret` 为空 → 30004（OIDC 校验与产物下载令牌都依赖它，
       空密钥会让签名退化为可预测常量）；
    3. 同一环境下 `tenant_membership_check == "off"` 且未显式放行
       `allow_security_downgrade` → 30004（QA-V04）。

    第 3 条的理由：`off` 会跳过跨租户成员校验，等于允许「持 A 租户令牌 +
    `X-Tenant-Id: B`」跨租户读写。它必须保留（线上卡死时要能止血），但**不能
    「配一行就生效」**——否则它从止血手段变成漏洞：谁都能在 .env 里加一行关掉隔离，
    系统只打一条 WARNING 就放行。所以生产/预发要它生效必须**两处同时显式声明**
    （`off` + `allow_security_downgrade=true`），让"我确实要降级"成为一个
    可审计的动作而不是一次手滑。

    development/test 不拦：离线单测与本地演示需要桩模式与降级态，且这两个环境不对外。

    Args:
        settings: 可选配置实例（注入优先）。

    Raises:
        ApiError: 生产/预发环境使用桩鉴权、缺少签名密钥，或未经放行就关闭
            跨租户成员校验（30004）。
    """
    conf = settings or get_settings()
    if conf.env in _INSECURE_AUTH_ENVS:
        logger.debug("auth_startup_check_skipped", env=conf.env, auth_mode=conf.auth_mode)
        return
    if conf.auth_mode == "stub":
        logger.error("auth_mode_stub_in_production", env=conf.env)
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"当前环境为 {conf.env}，但 OA_AUTH_MODE=stub（不校验令牌签名）。"
                "生产/预发环境必须设置 OA_AUTH_MODE=oidc 并配置 OA_JWT_SECRET；"
                "如确需以桩模式做本地演示，请显式设置 OA_ENV=development。"
            ),
        )
    if not conf.jwt_secret.strip():
        logger.error("jwt_secret_missing_in_production", env=conf.env)
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"当前环境为 {conf.env}，OA_JWT_SECRET 为空：令牌校验与产物下载签名都无法进行。"
                "请在 .env 中配置强随机密钥（不少于 32 字符）后重启。"
            ),
        )
    if conf.tenant_membership_check == "off":
        # QA-07（审计 2026-10-28）：时效检查——off 且 deadline 已过 = 安全配置错误，
        # 启动即失败（fail loud）。止血开关不允许"忘了开回来"还一直开着。
        deadline = conf.tenant_membership_check_deadline
        if deadline is not None:
            now = datetime.now(tz=deadline.tzinfo) if deadline.tzinfo else datetime.now()
            if now > deadline:
                logger.error(
                    "security_downgrade_deadline_expired",
                    env=conf.env,
                    deadline=deadline.isoformat(),
                )
                raise ApiError(
                    ErrorCode.CONFIG_ERROR,
                    internal_detail=(
                        f"OA_TENANT_MEMBERSHIP_CHECK=off 的时效已过期"
                        f"（OA_TENANT_MEMBERSHIP_CHECK_DEADLINE={deadline.isoformat()}）："
                        "紧急止血不允许无限期开启。请将 OA_TENANT_MEMBERSHIP_CHECK 改回 auto"
                        "（如确需延长止血，先修校验问题再更新 deadline）。"
                    ),
                )
        if not conf.allow_security_downgrade:
            logger.error("security_downgrade_not_allowed_in_production", env=conf.env)
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    f"当前环境为 {conf.env}，但 OA_TENANT_MEMBERSHIP_CHECK=off："
                    "该开关会跳过跨租户成员校验，等于允许「持 A 租户令牌 + "
                    "X-Tenant-Id: B」跨租户读写。生产/预发禁用安全降级开关；"
                    "如确需临时止血，必须同时显式设置 OA_ALLOW_SECURITY_DOWNGRADE=true，"
                    "并建议配置 OA_TENANT_MEMBERSHIP_CHECK_DEADLINE 限定恢复时限。"
                ),
            )
        if conf.env not in _INSECURE_AUTH_ENVS:
            # QA-07：生产/预发放行的降级必须以 error 横幅出现（不再是 WARNING）——
            # 此刻系统存在IDOR 窗口：跨租户读写不校验成员关系。
            logger.error(
                "security_downgrade_enabled",
                env=conf.env,
                tenant_membership_check=conf.tenant_membership_check,
                deadline=(deadline.isoformat() if deadline else ""),
                hint="IDOR 窗口已开启：跨租户切换未做成员校验；止血后必须立即改回 auto",
            )
        else:
            logger.warning(
                "security_downgrade_enabled",
                env=conf.env,
                tenant_membership_check=conf.tenant_membership_check,
                hint="安全降级已显式放行：跨租户切换未做成员校验，止血后必须改回 auto",
            )
    logger.info("auth_startup_validated", env=conf.env, auth_mode=conf.auth_mode)
