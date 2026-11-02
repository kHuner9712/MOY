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
from urllib.parse import urlsplit

from fastapi import Depends, Header, Request

from app.core import csrf
from app.core.config import Settings, get_request_settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import TENANT_ID_KEY, USER_ID_KEY, bind_context, get_logger
from app.core.ratelimit import get_request_rate_limiter
from app.core.session_cookie import SESSION_COOKIE_NAME, is_secure_from_base_url, read_cookie

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


#: JWKS 拉取超时（秒）。与 `keycloak_read_timeout_seconds` 同量级：
#: JWKS 是热路径，卡住请求比拉不到更糟（拉不到只是 20002，卡住会拖垮整个 worker）。
JWKS_TIMEOUT_SECONDS: Final[float] = 5.0

#: asymmetry 判定：算法名以 `RS` 开头即走 JWKS 公钥验签（RS256/RS384/RS512）。
#: 刻意**只认 `RS`**（K6 规格）：`PS*` / `ES*` / `EdDSA` 当前无现场，多开一条分支
#: 就多一条没人跑过的路径。将来要支持时在这里加前缀并同时补 JWKS 用例。
ASYMMETRIC_ALGORITHM_PREFIX: Final[str] = "RS"

#: JWKS 客户端进程内缓存（按 URL 缓存实例；实例内部再按 `kid` 缓存公钥）
_JWKS_CLIENTS: dict[str, Any] = {}


def uses_asymmetric_algorithm(algorithm: str) -> bool:
    """判断验签算法是否走 JWKS 公钥路径。

    Args:
        algorithm: `jwt_algorithm` 配置值（如 `RS256` / `HS256`）。

    Returns:
        bool: 以 `RS` 开头返回 True（走 JWKS），否则 False（走 `jwt_secret`）。
    """
    return algorithm.strip().upper().startswith(ASYMMETRIC_ALGORITHM_PREFIX)


def resolve_jwks_url(conf: Settings) -> str:
    """推导 JWKS 地址（**纯配置态、单一路径、无分支、无探测**）。

    JWKS 拉取与 `iss` 校验用的是**两个不同基准**：

    - `iss` 校验用 `jwt_issuer`（公网值，必须与 Keycloak 实际发出的 `iss` 逐字符相等）；
    - JWKS 拉取用 `keycloak_base_url`（**内网**地址）。

    为什么不能都走公网 issuer（私有化现场必踩）：

    - 内网 DNS 通常没有公网域名的记录，容器解析直接失败；
    - 即便能解析，也要回环绕到边缘，并信任边缘证书——自签现场必然失败；
    - JWKS 是每个请求都要走的热路径，多一跳就多一个失败面。

    这样拆不会导致 issuer 不一致：设了 `KC_HOSTNAME` 后，从内网拉 discovery 得到的
    `issuer` 仍是公网值。

    **为什么没有「外部 IdP 则从 issuer 派生」的回落分支——这是范围决定，不是漏写。**

    路径模板 `{base}/realms/{realm}/protocol/openid-connect/certs` 里的
    `/realms/{realm}` 是 **Keycloak 专有**的：Auth0 / Okta / Azure AD 的 JWKS 在
    `{issuer}/.well-known` 或 `{issuer}/...` 下，没有这一段。所以「支持任意 OIDC IdP」
    不是一个分支能解决的——本系统整套身份骨架都假设 IdP 是 Keycloak 形态：
    `keycloak_realm` 是独立配置项、令牌自省路径是
    `/realms/{realm}/protocol/openid-connect/token/introspect`
    （`KeycloakClient.INTROSPECT_PATH`）、角色映射读 `realm_access.roles`。
    只把 JWKS 改成"跟着 issuer 走"，会让 JWKS 能拉、自省/资料/角色三条链路照样 404，
    故障从一个 401 变成四个，更难查。

    因此 **V1.0 的 IdP 固定为 Keycloak 形态**（含旧版 `/auth/realms/{realm}` 前缀），
    非 Keycloak IdP（Auth0/Okta/Azure AD）留到 V1.1：届时走显式 `OA_JWKS_URL`
    覆盖项，或做整条链路的完整分流（自省 + 资料 + 角色映射一起）。

    也**不做**「拉取失败就换数据源」：那会把「网络抖动 → 悄悄切到公网地址 →
    自签证书不受信 → 401」变成一个新的隐性故障源，症状与本项要修的问题完全同款。

    `keycloak_base_url` 为空属配置错误，由 `validate_auth_startup` 在启动时拦
    （仅 `jwt_algorithm` 以 `RS` 开头时检查——HS256 对称签发现场不依赖它）。

    Args:
        conf: 配置实例。

    Returns:
        str: JWKS 地址；`keycloak_base_url` 为空时返回空串（调用方须已 fail-fast）。
    """
    base = conf.keycloak_base_url.strip().rstrip("/")
    realm = conf.keycloak_realm.strip().strip("/")
    if not base:
        # 正常不可达：`validate_auth_startup` 已在启动期拦下 RS* + 空 base。
        # 走到这里说明有人绕过了启动校验（如直接构造 Settings 的测试），
        # 返回空串让上层按"拉不到公钥"fail-closed，而不是拼出一个畸形的相对 URL。
        return ""
    return f"{base}/realms/{realm}/protocol/openid-connect/certs"


def get_jwks_client(jwks_url: str) -> Any:
    """取得（并缓存）JWKS 客户端。

    为什么必须缓存实例：`PyJWKClient` 把公钥按 `kid` 缓存在实例里。每次请求新建实例
    等于每个请求都打一次 Keycloak——把对方的 JWKS 端点变成我方可用性的单点。

    Args:
        jwks_url: JWKS 地址。

    Returns:
        Any: `jwt.PyJWKClient` 实例（返回类型用 Any，避免把 PyJWT 类型泄漏到模块接口）。
    """
    import jwt

    client = _JWKS_CLIENTS.get(jwks_url)
    if client is None:
        client = jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            # 允许缓存足够多的 key：Keycloak 轮换期间新旧 key 并存
            max_cached_keys=32,
            timeout=JWKS_TIMEOUT_SECONDS,
        )
        _JWKS_CLIENTS[jwks_url] = client
    return client


def reset_jwks_cache() -> None:
    """清空 JWKS 客户端缓存（测试与密钥轮换后的紧急处置用）。"""
    _JWKS_CLIENTS.clear()


def _load_signing_key(token: str, conf: Settings) -> Any:
    """按 `kid` 从 JWKS 取验签公钥。

    两个极端都要避免，故实现为「先查缓存，`kid` 未命中时强制刷新**一次**」：

    - 只缓存不刷新 → Keycloak 轮换密钥后永久 401（缓存里永远没有新 `kid`）；
    - 只刷新不缓存 → 每个请求都打一次 Keycloak（热路径上自造单点）。

    Args:
        token: 待验签的令牌原文（用于读 `kid`）。
        conf: 配置实例。

    Returns:
        Any: `cryptography` 公钥对象，可直接交给 `jwt.decode`。

    Raises:
        ApiError: JWKS 不可达或取不到对应 `kid` 的公钥（20002，fail-closed）。
    """
    import jwt

    jwks_url = resolve_jwks_url(conf)
    if not jwks_url:
        # 正常由 `validate_auth_startup` 在启动期拦住（RS* + 空 base → 30004）。
        # 这里再兜一次：宁可 20002，也不要拼出 `/realms/...` 这种相对地址去打
        # 自己（会得到一个与鉴权无关的 404，排查方向直接被带偏）。
        logger.error("jwks_source_unconfigured", keycloak_base_url="")
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            internal_detail="jwks source unconfigured: OA_KEYCLOAK_BASE_URL is empty",
        )

    # 现场排查"到底走了哪个源"就看这一条：只打 scheme+host，不打完整 URL
    # （完整 URL 含 realm 名与路径，日志里的价值不如一行 host 直观）。
    _url = urlsplit(jwks_url)
    logger.info("jwks_source_selected", jwks_source=f"{_url.scheme}://{_url.netloc}")

    client = get_jwks_client(jwks_url)

    try:
        return client.get_signing_key_from_jwt(token).key
    except jwt.exceptions.PyJWKClientConnectionError as exc:
        # fail-closed：拉不到公钥就拒绝，**绝不**退化成不验签
        logger.error("jwks_unreachable", jwks_url=jwks_url, error=type(exc).__name__)
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            internal_detail=f"jwks unreachable: {jwks_url}",
        ) from exc
    except Exception:
        # 落在「kid 不在缓存」上：可能是对方轮换了密钥，强制刷新一次再试
        logger.info("jwks_kid_miss_refresh", jwks_url=jwks_url)

    try:
        client.get_signing_keys(refresh=True)
        return client.get_signing_key_from_jwt(token).key
    except jwt.exceptions.PyJWKClientConnectionError as exc:
        logger.error("jwks_unreachable_on_refresh", jwks_url=jwks_url)
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            internal_detail=f"jwks unreachable: {jwks_url}",
        ) from exc
    except Exception as exc:
        logger.error("jwks_signing_key_not_found", jwks_url=jwks_url, error=type(exc).__name__)
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            internal_detail="jwks has no key matching token kid",
        ) from exc


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
        import jwt
        from jwt.types import Options as JwtOptions
    except ImportError as exc:  # pragma: no cover - 依赖缺失属环境问题
        raise ApiError(ErrorCode.CONFIG_ERROR, internal_detail="PyJWT not installed") from exc

    if conf.auth_mode == "stub":
        # 桩模式（仅开发态）：不验签、不校验 aud/iss。
        # P0（审计 2026-10-30）：PyJWT 语义与 python-jose 相反——`verify_signature=False`
        # 时 `verify_exp` 默认**一并放行**（jwt/api_jwt.py::_merge_options 防御性默认），
        # 而过期拒绝是既有红线（tests/unit/test_security.py::
        # test_resolve_rejects_expired_token），故必须显式置回 True。
        options: JwtOptions = {
            "verify_signature": False,
            "verify_exp": True,
            "verify_aud": False,
            "verify_iss": False,
        }
        key: Any = conf.jwt_secret
    elif uses_asymmetric_algorithm(conf.jwt_algorithm):
        # K6（R8）：Keycloak 默认签 RS256，而本模块此前只支持 HS256 + 共享密钥。
        # 症状是「登录看起来成功了，但每个业务接口 401」，且极易被误判成 Cookie 问题。
        # 公钥从 JWKS 取（内网地址、按 kid 缓存、未命中刷新一次、不可达 fail-closed）。
        options = {"verify_signature": True}
        key = _load_signing_key(token, conf)
    else:
        # 对称签发现场（私有化自签）：继续用共享密钥，行为不变
        options = {"verify_signature": True}
        key = conf.jwt_secret

    try:
        claims = jwt.decode(
            token,
            key,
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

    if not isinstance(claims, dict):  # pragma: no cover - PyJWT 保证返回 dict
        raise ApiError(ErrorCode.UNAUTHORIZED, internal_detail="token claims not a mapping")
    return claims


def _credentials_settings(request: Request) -> Settings:
    """取凭证解析所需的配置（容忍"非请求链路"构造的 Request）。

    正常路径上 `get_auth_context` 会显式把配置传进来，本函数只是兜底：
    单测里常用 `Request({"type": "http", "headers": ...})` 直接构造请求对象，
    其 `scope` 里没有 `app`，`request.app` 会抛 `KeyError`。这种请求不会出现在
    生产链路上，因此退化成全局单例是安全的。

    Args:
        request: 请求对象。

    Returns:
        Settings: 生效配置。
    """
    scope = getattr(request, "scope", None)
    app = scope.get("app") if isinstance(scope, dict) else None
    state_settings = getattr(getattr(app, "state", None), "settings", None)
    if isinstance(state_settings, Settings):
        return state_settings
    return get_settings()


def extract_credential(request: Request, settings: Settings | None = None) -> str:
    """从请求中提取凭证（**双轨**：Authorization 头优先，其次会话 Cookie）。

    判据顺序与理由：

    1. **有非空 `Authorization` 头 → 走 Bearer 轨，逐字沿用旧行为**（含"非 Bearer
       前缀即 20002"）。这条轨**不做 CSRF 判定**：跨站页面无法替用户设置自定义头，
       没有 Cookie 参与也就没有 CSRF（`docs` 里 P0-5 §2.4.8 的双轨约定）。
       先看头再读 Cookie，是为了让双轨期内旧前端（Bearer）与浏览器自身携带的
       残留 Cookie 不互相干扰——头在就有明确意图。空值的头等同于没有该头（它不是
       一份凭证），此时仍可回落 Cookie 轨。
    2. **无该头且 `auth_cookie_enabled=true` → 读 `oa_session` Cookie**。命中后必须
       过 `csrf.assert_same_site()`：Cookie 会被浏览器自动附加，跨站写请求可以
       带上来，这一判是 L2 防线（L1 是 `SameSite=Lax`，见 `app/core/csrf.py`）。
    3. 都不满足 → 20002（与旧行为同一错误码与同一句终端文案）。

    Args:
        request: FastAPI 请求对象。
        settings: 可选配置实例；缺省从请求注入的配置读取。

    Returns:
        str: 令牌原文（不含 `Bearer ` 前缀）。

    Raises:
        ApiError: 缺少凭证（20002）、Authorization 头格式非法（20002）、
            Cookie 轨来源校验未通过（20003）。
    """
    conf = settings or _credentials_settings(request)

    header = request.headers.get("Authorization", "")
    if header:
        lowered = header.lower()
        if not lowered.startswith(_BEARER_PREFIX):
            raise ApiError(
                ErrorCode.UNAUTHORIZED, internal_detail="Authorization scheme must be Bearer"
            )
        return header[len(_BEARER_PREFIX) :].strip()

    if not conf.auth_cookie_enabled:
        raise ApiError(ErrorCode.UNAUTHORIZED, internal_detail="missing Authorization header")

    cookie_token = read_cookie(request.headers.get("cookie"), SESSION_COOKIE_NAME)
    if not cookie_token:
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            internal_detail="missing Authorization header and session cookie",
        )
    if not csrf.assert_same_site(request):
        # 20003 而不是 20002：凭证本身可能是好的，拒绝的理由是"来源不可信"。
        # 混成 20002 会让前端把用户踢去重新登录，而正确动作是重发同源请求。
        logger.warning("cookie_credential_csrf_rejected", path=request.url.path)
        raise ApiError(
            ErrorCode.FORBIDDEN,
            message="请求来源校验未通过，请刷新页面后重试",
            internal_detail=f"cross-site {request.method} rejected for cookie credential",
        )
    return cookie_token


def extract_bearer_token(request: Request) -> str:
    """历史函数名（保留以兼容既有调用点与用例）。

    语义已由 `extract_credential` 接管（Bearer 头优先，无头时回落 Cookie）。
    新代码请直接用 `extract_credential`。

    Args:
        request: FastAPI 请求对象。

    Returns:
        str: 令牌原文（不含 `Bearer ` 前缀）。

    Raises:
        ApiError: 缺少凭证（20002）、Authorization 头格式非法（20002）、
            Cookie 轨来源校验未通过（20003）。
    """
    return extract_credential(request)


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

    0. **空权限点 → 直接抛 `CONFIG_ERROR`（30004）**：这是编码错误，不是"该端点
       不校验"的合理形态（此前是静默 `return`，等于把漏传权限点变成永久裸奔）；
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
        ApiError: 权限点为空（30004，编码错误）或权限不足（20003）。
    """
    if not permission.strip():
        # 空权限点是**编码错误**，不是用户错误：调用方忘了传权限点，
        # 不能退化成"该端点不校验"。静默放行会让一个漏传的端点永久裸奔，
        # 且测试期看不出来（请求照常 200）。这里必须 fail-loud，
        # 并在日志里指出是我们的 bug——用 CONFIG_ERROR（500）而非 FORBIDDEN（403），
        # 避免把责任错记到用户身上。
        #
        # N2：判空用 `strip()`（`" "` / `"\t"` 与空串同属"漏传"，归因必须一致，
        # 否则空白串会落到下面的 20003"权限不足"——把编码错误报成用户没权限）。
        # **只用于判空**：后续权限比对仍用原值，`strip()` 后的值不参与任何比较。
        logger.error(
            "permission_point_empty",
            method=method.upper(),
            user_id=auth.user_id,
            tenant_id=auth.tenant_id,
            hint="调用方未提供权限点：属编码错误，需修正 require_permission 调用",
        )
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"权限点为空（method={method.upper()}）：require_permission 必须传入非空权限点，"
                "空值是编码错误而非用户无权限。"
            ),
        )
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

    凭证来源是**双轨**的（P0-5 §2.4.8）：`Authorization: Bearer` 头优先，
    无该头时回落 `oa_session` Cookie（`extract_credential`）。两条轨最终喂给
    同一个 `AuthContext.resolve`，因此验签、租户与角色的判定**只有一套**。

    配置来源：`app.state.settings`（`create_app(settings=...)` 注入），
    而非全局单例——否则注入 `auth_mode=oidc` 会被降级为 stub，
    伪造令牌即可登录（QA BUG-01）。这里把配置**显式**传给 `extract_credential`：
    否则 Cookie 轨会去读全局单例的 `auth_cookie_enabled`，注入式构造的应用
    在关掉 Cookie 后仍会接受 Cookie 凭证。

    Args:
        request: FastAPI 请求对象。

    Returns:
        AuthContext: 鉴权上下文。

    Raises:
        ApiError: 凭证缺失或校验失败（20002）；Cookie 轨来源校验未通过（20003）。
    """
    settings = get_request_settings(request)
    auth = AuthContext.resolve(extract_credential(request, settings), settings)
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

#: 「会话 Cookie 不带 `Secure`」必须告警的环境（P0-5 §2.4.6）。
#:
#: 为什么**只告警、不拦启动**：私有化现场纯 HTTP 是**合法形态**（客户内网、临时
#: 联调、没有域名也就没有证书），判 fail 会把这类部署直接挡在门外——而它们的
#: 实际风险是"同网段可嗅探"，与"平台不能用"不是一回事。是否必须上 TLS 由交付
#: 场景决定，代码不替客户拍板，但要**大声告警**，别让它在生产里静默存在。
_COOKIE_SECURE_WARN_ENVS: Final[frozenset[str]] = frozenset({"staging", "production"})

#: `OA_JWT_SECRET` 的最小长度（P1-10）。
#:
#: HS256 的密钥短于 SHA-256 分组长度（32 字节）时，爆破成本随长度骤降，
#: 而"短密钥"往往就是模板占位值或手敲的 `secret123`——长度是最便宜的一道筛。
MIN_JWT_SECRET_LENGTH: Final[int] = 32

#: 模板占位值特征（大小写不敏感，任一命中即判红）。
#:
#: `deploy/.env.example` 里是 `CHANGE_ME_generate_with_openssl_rand_hex_32`：
#: 手抄模板不改就是"**仓库公开常量成了 JWT 签名密钥**"——任何人可据此自造任意
#: 租户/角色的令牌（P1-10）。因此占位值必须与"空密钥"同等对待。
#: 模板里的占位值长度刻意 ≥`MIN_JWT_SECRET_LENGTH`，让报错落在"命中占位值"
#: （准确、可操作）而不是"长度不足"（会把人引去凑长度，实际该做的是换密钥）。
JWT_SECRET_PLACEHOLDER_MARKERS: Final[tuple[str, ...]] = (
    "change_me",
    "changeme",
    "password",
)


def _jwt_secret_placeholder_reason(secret: str) -> str:
    """判定 `jwt_secret` 是否仍是占位值/过短，并给出具体原因。

    只描述**原因**，绝不回显密钥本身（日志与错误文案里出现密钥等于二次泄露）。

    Args:
        secret: `OA_JWT_SECRET` 的取值。

    Returns:
        str: 不可用的原因；可用时返回空串（空密钥由调用方的独立判据处理，
            避免两处文案重复）。
    """
    text = secret.strip()
    if not text:
        return ""
    lowered = text.lower()
    for marker in JWT_SECRET_PLACEHOLDER_MARKERS:
        if marker in lowered:
            return f"命中模板占位值特征 {marker!r}"
    if len(text) < MIN_JWT_SECRET_LENGTH:
        return f"长度 {len(text)} < 最低要求 {MIN_JWT_SECRET_LENGTH}"
    return ""


def validate_auth_startup(settings: Settings | None = None) -> None:
    """启动期鉴权防呆（OA-REVIEW-002 / S1）。

    **为什么必须在启动时拦**：`auth_mode` 的默认值是 `stub`，而 `stub` 下
    `verify_signature=False`——任何人都能自造 JWT 声明任意租户与角色。
    这不是「有人配错了」，而是「不配就是关闭鉴权」，属于默认态风险。

    判据：
    1. `env in {staging, production}` 且 `auth_mode == "stub"` → 30004 启动失败；
    2. 同一环境下 `pay_channel == "fake"` → 30004（P1-09：fake 通道**不验回调签名**，
       与 `auth_mode=stub` 是同一类"测试开关"，必须有同样的生产守卫）；
    3. 同一环境下 `jwt_secret` 为空 → 30004（OIDC 校验与产物下载令牌都依赖它，
       空密钥会让签名退化为可预测常量）；
    4. 同一环境下 `jwt_secret` 仍是模板占位值或过短 → 30004（P1-10：
       `deploy/.env.example` 的 `CHANGE_ME_generate_with_openssl_rand_hex_32`
       是仓库公开常量，手抄不改等于把签发权交给所有能读仓库的人；
       长度下限见 `MIN_JWT_SECRET_LENGTH`）；
    5. 同一环境下 `tenant_membership_check == "off"` 且未显式放行
       `allow_security_downgrade` → 30004（QA-V04）；
    6. 同一环境下 `public_base_url` 为空 → 30004（K1 / R4：对外基址同时决定
       `KC_HOSTNAME` 与 Cookie `Secure`，缺它两者静默退化）；
    7. 同一环境下 `jwt_algorithm` 以 `RS` 开头但 `keycloak_base_url` 为空 →
       30004（K6 / R8：JWKS 地址只能由它推导，否则每个请求都 20002，而真因是配置）；
    8. 同一环境下 `OA_PUBLIC_BASE_URL` 不是 `https://` → **只告警不阻断**
       （P0-5 §2.4.6：会话 Cookie 会丢掉 `Secure`，会话标识在明文 HTTP 上传输）。

    第 8 条为什么**不是**判据：私有化纯 HTTP 是合法现场（客户内网、无域名无证书），
    屏蔽它等于把这类部署挡在门外。但"能部署"不等于"可以没人知道"——故用
    `cookie_secure_disabled` 这条 WARNING 留痕，SaaS 环境据此必须修正。
    判定只读配置（`OA_PUBLIC_BASE_URL` 的 scheme），**不读转发头**：
    `X-Forwarded-Proto` 可被任何客户端伪造，采信它等于让攻击者决定会话是否加密。

    第 5 条的理由：`off` 会跳过跨租户成员校验，等于允许「持 A 租户令牌 +
    `X-Tenant-Id: B`」跨租户读写。它必须保留（线上卡死时要能止血），但**不能
    「配一行就生效」**——否则它从止血手段变成漏洞：谁都能在 .env 里加一行关掉隔离，
    系统只打一条 WARNING 就放行。所以生产/预发要它生效必须**两处同时显式声明**
    （`off` + `allow_security_downgrade=true`），让"我确实要降级"成为一个
    可审计的动作而不是一次手滑。

    development/test 不拦：离线单测与本地演示需要桩模式与降级态，且这两个环境不对外。

    Args:
        settings: 可选配置实例（注入优先）。

    Raises:
        ApiError: 生产/预发环境使用桩鉴权、启用 fake 支付通道、缺少/占位/过短的
            签名密钥，或未经放行就关闭跨租户成员校验（30004）。
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
    if conf.pay_channel == "fake":
        # P1-09：`pay_channel=fake` 会**关掉支付回调验签**（回调不验签 = 谁都能
        # 伪造"支付成功"把订单置为已付）。它与 `auth_mode=stub` 是同一类测试开关，
        # 却此前没有生产守卫——同一类开关两套待遇，等于给"配一行就关验签"留门。
        logger.error("pay_channel_fake_in_production", env=conf.env)
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"当前环境为 {conf.env}，但 OA_PAY_CHANNEL=fake（支付回调不验签、不产生真实扣款）。"
                "生产/预发环境必须设置 OA_PAY_CHANNEL=wechat_pay 并配置商户号与 APIv3 密钥；"
                "如确需以 fake 通道做本地联调，请显式设置 OA_ENV=development。"
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
    secret_reason = _jwt_secret_placeholder_reason(conf.jwt_secret)
    if secret_reason:
        # P1-10：只判"非空"挡不住"手抄模板"——`deploy/.env.example` 的
        # `CHANGE_ME_generate_with_openssl_rand_hex_32` 是一个**仓库公开常量**，
        # 用它当签名密钥等于把签发权交给所有能读仓库的人。故占位值特征与最短
        # 长度也必须拦。
        logger.error(
            "jwt_secret_placeholder_in_production",
            env=conf.env,
            reason=secret_reason,
        )
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"当前环境为 {conf.env}，OA_JWT_SECRET 不可用（{secret_reason}）："
                "模板里的 CHANGE_ME_* 是仓库公开常量，拿它当签名密钥等于让任何人都能自造令牌。"
                f"请用 `openssl rand -hex 32` 生成不少于 {MIN_JWT_SECRET_LENGTH} 字符的"
                "随机密钥写入 OA_JWT_SECRET 后重启（`bash deploy/install.sh` 会自动生成）。"
            ),
        )
    if uses_asymmetric_algorithm(conf.jwt_algorithm) and not conf.keycloak_base_url.strip():
        # K6（R8）：RS* 验签的公钥只能从 JWKS 取，而 JWKS 地址由
        # `keycloak_base_url` 推导（**内网**地址，不用公网 issuer——理由见
        # `resolve_jwks_url`）。为空时若放行，运行期会拼出一个
        # `/realms/...` 相对地址，表现为 404 或连接失败，而错误码是 20002
        # 「令牌非法」——排障会被引向令牌而不是配置。
        # 只拦 RS*：HS256 的私有化对称签发现场不依赖它，别把那些人挡在门外。
        logger.error(
            "jwks_base_url_missing_in_production",
            env=conf.env,
            jwt_algorithm=conf.jwt_algorithm,
        )
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"当前环境为 {conf.env}，OA_JWT_ALGORITHM={conf.jwt_algorithm}（非对称）"
                "但 OA_KEYCLOAK_BASE_URL 为空：JWKS 公钥地址只能由它推导，"
                "缺失时每个请求都会 20002（令牌非法），而真正的原因是配置。"
                "请设置为**适配层视角的 IdP 地址**（Keycloak 现场即 http://keycloak:8080；"
                "客户自带外部 IdP 时填客户 IdP 地址）后重启。"
            ),
        )
    if not conf.public_base_url.strip():
        # K1 / R4（2026-11-02）：对外基址是**单一真源**——它同时决定
        # `KC_HOSTNAME`（→ 令牌 iss 的域名）与会话 Cookie 的 `Secure` 属性。
        # 空值不会立刻报错，而是表现为两件更难定位的事：Cookie 静默丢掉 `Secure`
        # （会话 id 走明文），以及 realm 渲染出 `/auth/callback` 这种非法
        # `redirectUris`（登录被 Keycloak 以 `Invalid parameter: redirect_uri` 拒绝）。
        # 故与其余"配错了才发现"的项一样处理：生产/预发直接启动失败。
        # 之所以放在密钥校验**之后**：缺密钥是更紧的阻断（连校验都做不了），
        # 报错顺序应反映修复顺序。
        logger.error("public_base_url_missing_in_production", env=conf.env)
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"当前环境为 {conf.env}，OA_PUBLIC_BASE_URL 为空："
                "对外基址同时决定 Keycloak 的 KC_HOSTNAME（令牌 iss 的域名）"
                "与会话 Cookie 的 Secure 属性，缺它时两者都会静默退化。"
                "请设置形如 https://moy.example.com 的地址（不带尾斜杠）后重启；"
                "改完还需重跑 `sh deploy/keycloak/render-realm.sh` 并重建 web 镜像。"
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
    if conf.env in _COOKIE_SECURE_WARN_ENVS and not is_secure_from_base_url(conf.public_base_url):
        # P0-5 §2.4.6：`Secure` 只由 `OA_PUBLIC_BASE_URL` 的 scheme 派生，**不读
        # `X-Forwarded-Proto`**（那个头可以让任何客户端决定会话是否加密）。
        # 走到这里说明现场显式配了 http://（或省了协议），会话 Cookie 会明文传输。
        # 不抛错的理由：私有化 HTTP 是合法形态；但这条 WARNING 必须能被检索到，
        # 否则"会话在明文里漂"就会成为交付后才被发现的隐性问题。
        logger.warning(
            "cookie_secure_disabled",
            env=conf.env,
            public_base_url=conf.public_base_url,
            hint=(
                "会话 Cookie 不带 Secure：会话标识将在明文 HTTP 上传输。"
                "私有化 HTTP 现场可接受；SaaS 环境必须改用 https:// 对外基址并重建镜像。"
                "（本项不阻断启动，属显式告警）"
            ),
        )
    logger.info("auth_startup_validated", env=conf.env, auth_mode=conf.auth_mode)
