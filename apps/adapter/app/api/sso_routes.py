"""会话端点（P0-5 路线 A：令牌不进 JavaScript）。

**为什么不在 `/api/v1` 下**
--------------------------

`deploy/validate_openapi.py` 挂着 5 份冻结基线（30+38+16+10+2 = 96 条），且
`baseline-core-30.json` 受 CODEOWNERS 保护。产物下载已经为此立过先例并在代码里写明
理由（`app/api/v1/artifact_files.py:3-5`）：塞进 `/api/v1` 会让端点计数与基线门禁
失去意义。本模块照抄：会话端点挂 `/sso/`，**96 条基线一条不动**。

**为什么是"无状态 BFF"，没有服务端会话表**
------------------------------------------

`oa_session` 里放的就是身份平台的 `access_token` 原文。它是一个自包含的 JWT：
每次请求用 JWKS 公钥验签即可，**不需要任何服务端查表**。加一张会话表等于把
「无状态验签」退化成「每请求查一次存储」——多一个故障点、多一份会漂移的状态、
多一次网络往返，却换不来任何额外能力。

- 零服务端状态 → 服务重启不掉登录、多副本天然可用、缓存中间件故障不影响登录；
- 会话寿命由身份平台自己的 `ssoSessionIdleTimeout` / `ssoSessionMaxLifespan` 管理，
  平台再存一份是冗余，且两份状态**必然**漂移；
- 刷新与失效由身份平台负责（`/sso/refresh` 只是把 `refresh_token` 转手给 IdP）。

**代价（必须写在明处，不隐藏）**：无法"服务端强制踢出已下发的 `access_token`"——
令牌一旦签发就在有效期内处处可用。缓解手段是 access token 的有效期很短
（身份平台默认 300 s），窗口很小；真的需要强制下线时走身份平台的 session 管理
（撤销 refresh token + 该用户 session），失效传播的最长延迟等于 access TTL。

五端点与两条硬约束
------------------

============================  ==============================================
端点                          行为
============================  ==============================================
``POST /sso/start``           生成 PKCE → 下发 `oa_oidc_pending` → 回授权地址
``POST /sso/complete``        `{code, state}` → 换令牌 → 下发两个会话 Cookie
``GET  /sso/session``         `{authenticated, expires_in, refreshable, expired}`（不回显令牌）
``POST /sso/refresh``         用 `oa_refresh` 换新 → 覆写两个 Cookie
``POST /sso/logout``          删两个 Cookie + 尽力撤销刷新令牌
============================  ==============================================

**清理边界（容易写错，且错了就是"每 5 分钟被踢一次"）**：

====================  ==========================================================
路径                    清理什么 / 为什么
====================  ==========================================================
`GET /sso/session`     只清 `oa_session`。access TTL 300 s 而会话 8 h，
                       "access 过期、refresh 有效"是正常态；顺手删 refresh
                       会把 8 小时会话压成 300 秒（QA-01）
`POST /sso/refresh`    仅当身份平台判 20002（令牌已死）才清 `oa_refresh`；
                       30001/30002 是网络抖动，**不清**（QA-02）
`POST /sso/logout`     两个都清——登出就是不要这个会话了
====================  ==========================================================

硬约束 1：**任何响应体都不得包含 `access_token` / `refresh_token` / `id_token`**。
这是本方案的**全部意义**——令牌只走 `Set-Cookie`（httpOnly），绝不进 JSON。

硬约束 2：`auth_mode=stub` 或 `auth_cookie_enabled=false` 时五个端点全部 `503`，
且**不下发任何 Cookie**。本地联调没有身份平台，假装登录成功会让"登录可用"这句话
在开发环境里变成假的；静默降级则会让人以为 Cookie 会话已生效。
"""

from __future__ import annotations

import time
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.core import csrf
from app.core.config import Settings, get_request_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.oidc_server import (
    TokenSet,
    build_authorize_url,
    exchange_code,
    generate_pkce,
    logout,
    refresh_tokens,
    states_match,
)
from app.core.security import AuthContext
from app.core.session_cookie import (
    PENDING_COOKIE_NAME,
    PENDING_MAX_AGE_SECONDS,
    REFRESH_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
    SESSION_MAX_AGE_SECONDS,
    SSO_COOKIE_PATH,
    build_clear_cookie,
    build_set_cookie,
    decode_pending,
    encode_pending,
    is_secure_from_base_url,
    parse_cookie_header,
    read_cookie,
)
from app.schemas.common import ApiResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/sso", tags=["sso"])

#: 前端回调页路径。
#:
#: 必须与 `deploy/keycloak/render-realm.sh` 渲染出的 `redirectUris` 一致；它作为
#: `redirect_uri` 参与两处校验（授权请求、换令牌请求），**两次必须逐字符相同**，
#: 因此完整值在 `/sso/start` 时写进 `oa_oidc_pending`，`/sso/complete` 直接复用，
#: 不在两处各自拼一次（少一处漂移面）。
REDIRECT_PATH: Final[str] = "/auth/callback"

#: 对终端用户的统一失效文案。
#:
#: **刻意不暴露实现细节**（不说"state 不匹配"、不说"pending 已过期"）：这些差异
#: 对用户没有可操作价值，却会告诉攻击者"是重放被拦了还是本来就没有这次登录"。
PENDING_INVALID_MESSAGE: Final[str] = "登录请求已失效，请重新发起登录"

#: 会话失效文案（与统一错误码 20002 的默认文案一致，显式写出便于对照）
SESSION_INVALID_MESSAGE: Final[str] = "登录状态已失效，请重新登录"

#: CSRF 拒绝文案
CSRF_REJECTED_MESSAGE: Final[str] = "请求来源校验未通过，请刷新页面后重试"


# ===========================================================================
# 响应契约（`data` 字段；外层统一 `{code, message, data}` 信封）
# ===========================================================================
class SsoStartData(BaseModel):
    """`POST /sso/start` 的业务数据。

    Attributes:
        authorize_url: 浏览器应跳转的授权地址（含 PKCE 挑战，**不含 verifier**）。
    """

    authorize_url: str


class SsoCompleteData(BaseModel):
    """`POST /sso/complete` 的业务数据（**不含任何令牌**）。

    Attributes:
        ok: 登录是否成功（能返回即成功）。
        expires_in: 访问令牌有效期（秒），供前端决定何时主动刷新。
    """

    ok: bool = True
    expires_in: int = 0


class SsoSessionData(BaseModel):
    """`GET /sso/session` 的业务数据（**不含任何令牌**）。

    **为什么是三个布尔而不是一个 `authenticated`**（QA-03）：前端要区分两件完全不同
    的事——「令牌过期了，去刷新（还能救）」和「没登录 / 刷新令牌也没了，去重新登录」。
    只有一个 `authenticated` 时两者的响应体**逐字相同**，而唯一能区分的信息
    （浏览器里还有没有 `oa_refresh`）在 httpOnly Cookie 里，前端读不到。

    Attributes:
        authenticated: 访问令牌有效（Cookie 存在且验签通过）。
        expires_in: 访问令牌剩余有效期（秒）；未登录时为 None。
        refreshable: 浏览器是否还持有刷新令牌（能否续期）。前端据此决定"刷新"还是"重登"。
        expired: **曾带着会话 Cookie 来，但它不可用**（过期 / 签名不符 / 无法解析）。
            与"从未登录"区分：`expired=true` 说明用户确实登录过，只是令牌失效了。
    """

    authenticated: bool = False
    expires_in: int | None = None
    refreshable: bool = False
    expired: bool = False


class SsoRefreshData(BaseModel):
    """`POST /sso/refresh` 的业务数据（**不含任何令牌**）。

    Attributes:
        ok: 刷新是否成功。
        expires_in: 新的访问令牌有效期（秒）。
    """

    ok: bool = True
    expires_in: int = 0


class SsoLogoutData(BaseModel):
    """`POST /sso/logout` 的业务数据。

    Attributes:
        ok: 登出是否完成（本地 Cookie 一定已删）。
    """

    ok: bool = True


class SsoCompleteRequest(BaseModel):
    """`POST /sso/complete` 的请求体。

    Attributes:
        code: 授权码。
        state: 回调带回的 `state`（缺失按"不匹配"处理，统一回 20002）。
    """

    code: str = Field(..., min_length=1, max_length=4096, description="授权码")
    state: str = Field(default="", max_length=512, description="登录往返状态串")


# ===========================================================================
# 内部工具
# ===========================================================================
def _require_sso_available(request: Request) -> Settings:
    """确认会话端点可用（否则 503）。

    两种情况都返回 `503`（`30001`，`服务暂时不可用`）：

    1. `auth_mode=stub`：本地联调没有身份平台，**不能假装登录成功**；
    2. `auth_cookie_enabled=false`：会话 Cookie 被显式关闭，端点没有任何意义。

    为什么不用 `30004 CONFIG_ERROR`（500）：这不是配置写错，而是本部署形态下该能力
    不可用；500 会触发告警与"服务故障"的排查方向，503 才是准确的语义。

    Args:
        request: 请求对象（取注入配置）。

    Returns:
        Settings: 生效配置。

    Raises:
        ApiError: 会话端点不可用（30001）。
    """
    conf = get_request_settings(request)
    if conf.auth_mode == "stub":
        logger.info("sso_unavailable", reason="auth_mode=stub", path=request.url.path)
        raise ApiError(
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            message="统一登录未启用",
            internal_detail="sso endpoints require auth_mode=oidc",
        )
    if not conf.auth_cookie_enabled:
        logger.info("sso_unavailable", reason="auth_cookie_disabled", path=request.url.path)
        raise ApiError(
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            message="统一登录未启用",
            internal_detail="sso endpoints require OA_AUTH_COOKIE_ENABLED=true",
        )
    return conf


def _require_same_site(request: Request) -> None:
    """对会话端点做 CSRF 判定（写方法必须同源）。

    为什么这五个端点也要判：它们**读 Cookie**（`oa_oidc_pending` / `oa_refresh` /
    `oa_session`），而浏览器会自动附加 Cookie。缺了这一判，跨站页面就能替用户发起
    登录往返（登录 CSRF）、触发刷新或把用户登出。

    Args:
        request: 请求对象。

    Raises:
        ApiError: 来源校验未通过（20003）。
    """
    if not csrf.assert_same_site(request):
        logger.warning("sso_csrf_rejected", path=request.url.path, method=request.method)
        raise ApiError(
            ErrorCode.FORBIDDEN,
            message=CSRF_REJECTED_MESSAGE,
            internal_detail=f"cross-site {request.method} rejected for session endpoint",
        )


def _append_cookie(response: Response, value: str) -> None:
    """追加一条 `Set-Cookie`。

    必须用 `headers.append`（而不是 `response.headers[...] = ...`）：一个响应要下发
    多条 Cookie（`complete` 里三个），赋值会互相覆盖。FastAPI 会把注入的
    `Response` 的原始头合并进最终响应，因此这里的追加是生效的。

    Args:
        response: FastAPI 注入的响应对象。
        value: `session_cookie.build_*` 产出的头值。
    """
    response.headers.append("set-cookie", value)


def _secure_flag(conf: Settings) -> bool:
    """会话 Cookie 是否带 `Secure`（**只由配置决定，不读请求头**）。

    Args:
        conf: 配置实例。

    Returns:
        bool: `OA_PUBLIC_BASE_URL` 为 https 时返回 True。
    """
    return is_secure_from_base_url(conf.public_base_url)


def _redirect_uri(conf: Settings) -> str:
    """推导回调地址。

    **不从请求头推导**（`Host` / `X-Forwarded-*`）：回调地址参与 IdP 侧的白名单校验，
    若采信请求头，攻击者伪造 `Host` 就能把授权码引到自己的域上。因此只认
    `OA_PUBLIC_BASE_URL`——与 Cookie `Secure` 的判定基准是同一个真源。

    Args:
        conf: 配置实例。

    Returns:
        str: 形如 `https://moy.example.com/auth/callback`。

    Raises:
        ApiError: 对外基址为空（30004，配置错误）。
    """
    base = conf.public_base_url.strip().rstrip("/")
    if not base:
        logger.error("sso_public_base_url_missing")
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                "OA_PUBLIC_BASE_URL is empty: redirect_uri cannot be derived safely "
                "(host header must not be trusted)"
            ),
        )
    return f"{base}{REDIRECT_PATH}"


def _load_pending(request: Request) -> dict[str, Any]:
    """读取并校验登录暂存（`oa_oidc_pending`）。

    校验三件事：Cookie 存在、内容可解析、未过期。三者**任一不满足都回同一句文案**，
    原因只进日志。

    Args:
        request: 请求对象。

    Returns:
        dict[str, Any]: 暂存内容（含 `state` / `verifier` / `redirect_uri`）。

    Raises:
        ApiError: 暂存缺失、解析失败或已过期（20002）。
    """
    raw = read_cookie(request.headers.get("cookie"), PENDING_COOKIE_NAME)
    if raw is None:
        logger.info("sso_pending_missing")
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=PENDING_INVALID_MESSAGE,
            internal_detail="pending cookie missing or undecodable",
        )
    pending = decode_pending(raw)
    if pending is None:
        logger.warning("sso_pending_malformed")
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=PENDING_INVALID_MESSAGE,
            internal_detail="pending cookie is not valid json object",
        )
    issued_at = pending.get("issued_at")
    if not isinstance(issued_at, int) or _is_expired(issued_at):
        logger.info("sso_pending_expired", issued_at=str(issued_at))
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=PENDING_INVALID_MESSAGE,
            internal_detail="pending cookie expired or has no issued_at",
        )
    verifier = str(pending.get("verifier") or "").strip()
    redirect_uri = str(pending.get("redirect_uri") or "").strip()
    if not verifier or not redirect_uri:
        logger.warning("sso_pending_incomplete")
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=PENDING_INVALID_MESSAGE,
            internal_detail="pending cookie lacks verifier or redirect_uri",
        )
    return pending


def _is_expired(issued_at: int) -> bool:
    """判断登录暂存是否过期。

    Args:
        issued_at: 暂存写入时的 Unix 时间戳（秒）。

    Returns:
        bool: 超过 `PENDING_MAX_AGE_SECONDS` 返回 True。
    """
    return int(time.time()) - issued_at > PENDING_MAX_AGE_SECONDS


def _issue_session_cookies(response: Response, conf: Settings, tokens: TokenSet) -> None:
    """下发两个会话 Cookie。

    Args:
        response: FastAPI 注入的响应对象。
        conf: 配置实例。
        tokens: 令牌集合（**只经 Cookie 下发，绝不进响应体**）。
    """
    secure = _secure_flag(conf)
    _append_cookie(
        response,
        build_set_cookie(
            SESSION_COOKIE_NAME,
            tokens.access_token,
            max_age=SESSION_MAX_AGE_SECONDS,
            path=SESSION_COOKIE_PATH,
            secure=secure,
        ),
    )
    if tokens.refresh_token:
        _append_cookie(
            response,
            build_set_cookie(
                REFRESH_COOKIE_NAME,
                tokens.refresh_token,
                max_age=SESSION_MAX_AGE_SECONDS,
                path=SSO_COOKIE_PATH,
                secure=secure,
            ),
        )
    else:
        # 没有刷新令牌（public client / 未申请 offline 作用域）：明确告警，
        # 不要装作"刷新能力可用"——前端会在 access 过期后收到 401，那时只能重新登录。
        logger.warning(
            "sso_refresh_token_absent",
            hint="identity provider returned no refresh_token",
        )


def _clear_session_cookies(response: Response, conf: Settings) -> None:
    """删除两个会话 Cookie（**属性必须与下发时一致**，否则删不掉）。

    只用于**登出**与"确定要放弃整个会话"的场景。`GET /sso/session` 的探测路径
    **不能**用它：那里 `oa_refresh` 往往仍然有效（QA-01，见 `_clear_session_only`）。

    Args:
        response: FastAPI 注入的响应对象。
        conf: 配置实例。
    """
    secure = _secure_flag(conf)
    _append_cookie(
        response,
        build_clear_cookie(SESSION_COOKIE_NAME, path=SESSION_COOKIE_PATH, secure=secure),
    )
    _append_cookie(
        response,
        build_clear_cookie(REFRESH_COOKIE_NAME, path=SSO_COOKIE_PATH, secure=secure),
    )


def _clear_session_only(response: Response, conf: Settings) -> None:
    """只删除访问令牌 Cookie，**保留 `oa_refresh`**（QA-01）。

    为什么这个区分是必须的：`oa_session`（300 秒）比 `oa_refresh`（8 小时）短得多，
    "访问令牌过期、刷新令牌仍然有效"是**会话期内的正常状态**。若探测会话时把两个
    一起删掉，就等于把"刷新即可续期"降级成"重新登录"——用户每 5 分钟被踢一次，
    8 小时的会话实际只活 300 秒。

    Args:
        response: FastAPI 注入的响应对象。
        conf: 配置实例。
    """
    _append_cookie(
        response,
        build_clear_cookie(
            SESSION_COOKIE_NAME, path=SESSION_COOKIE_PATH, secure=_secure_flag(conf)
        ),
    )


def _clear_refresh_header(conf: Settings) -> str:
    """构造"清掉刷新令牌"的 `Set-Cookie` 头值（QA-02）。

    **返回头值而不是往注入的 `Response` 上 append**：这条清除必须发生在 401 响应上，
    而 FastAPI 在异常路径会**丢弃**注入 `Response` 的头（异常处理器新建响应）。
    把它挂到 `ApiError(headers=...)` 上才会真的发出去——只 append 到 Response 的话，
    代码看起来完全正确，浏览器却什么都收不到（P0-5 / QA-02 实测踩到）。

    Args:
        conf: 配置实例。

    Returns:
        str: `oa_refresh=; Path=/sso; Max-Age=0; ...`。
    """
    return build_clear_cookie(REFRESH_COOKIE_NAME, path=SSO_COOKIE_PATH, secure=_secure_flag(conf))


def _clear_pending_cookie(response: Response, conf: Settings) -> None:
    """删除登录暂存 Cookie（**用完即删，防重放**）。

    Args:
        response: FastAPI 注入的响应对象。
        conf: 配置实例。
    """
    _append_cookie(
        response,
        build_clear_cookie(PENDING_COOKIE_NAME, path=SSO_COOKIE_PATH, secure=_secure_flag(conf)),
    )


# ===========================================================================
# 端点
# ===========================================================================
@router.post(
    "/start",
    summary="发起统一登录",
    description=(
        "生成 PKCE 挑战并下发登录暂存 Cookie，返回应跳转的授权地址。"
        "响应体不含任何令牌，访问令牌只经 httpOnly Cookie 下发。"
    ),
    response_model=ApiResponse[SsoStartData],
)
async def start_login(request: Request, response: Response) -> ApiResponse[SsoStartData]:
    """发起登录往返。

    **已知限制（有意保留，不是缺陷）**：`oa_oidc_pending` 只存**一份**
    登录暂存（state + verifier）。用户同时开两个标签页并各自点"统一登录"时，
    后一次 `/sso/start` 会覆盖前一次；先发起的那个标签页在回调时会收到
    20002「登录请求已失效，请重新发起登录」，**重试一次即可**，不丢数据。

    为什么不做多 state 合并（设计 §2.4.3 曾提"最多 3 条"）：触发场景是
    "两个标签页同时登录"，概率极低；而后果只是重试一次。代价却是把登录暂存的
    结构从"一份 JSON"改成"数组 + 上限 + 淘汰策略"——一套很少用到、但必须
    **永远正确**的复杂度。已由主理人拍板保持单条。

    Args:
        request: 请求对象。
        response: 响应对象（追加 `Set-Cookie`）。

    Returns:
        ApiResponse[SsoStartData]: 含 `authorize_url` 的成功响应。

    Raises:
        ApiError: 会话端点不可用（30001）、来源校验未通过（20003）、
            对外基址缺失（30004）。
    """
    conf = _require_sso_available(request)
    _require_same_site(request)

    state, verifier, challenge = generate_pkce()
    redirect_uri = _redirect_uri(conf)
    _append_cookie(
        response,
        build_set_cookie(
            PENDING_COOKIE_NAME,
            encode_pending(
                {
                    "state": state,
                    "verifier": verifier,
                    "redirect_uri": redirect_uri,
                    "issued_at": int(time.time()),
                }
            ),
            max_age=PENDING_MAX_AGE_SECONDS,
            path=SSO_COOKIE_PATH,
            secure=_secure_flag(conf),
        ),
    )
    authorize_url = build_authorize_url(conf, state, challenge, redirect_uri)
    logger.info("sso_start", redirect_uri=redirect_uri)
    return ApiResponse[SsoStartData].ok(SsoStartData(authorize_url=authorize_url))


@router.post(
    "/complete",
    summary="完成统一登录",
    description=(
        "校验登录状态串后，用授权码在服务端换取令牌，并以 httpOnly Cookie 下发。"
        "响应体只含 `ok` 与 `expires_in`，不含任何令牌。"
    ),
    response_model=ApiResponse[SsoCompleteData],
)
async def complete_login(
    request: Request,
    response: Response,
    body: SsoCompleteRequest,
) -> ApiResponse[SsoCompleteData]:
    """用授权码完成登录。

    Args:
        request: 请求对象。
        response: 响应对象（追加 `Set-Cookie`）。
        body: 授权码与状态串。

    Returns:
        ApiResponse[SsoCompleteData]: `{ok: true, expires_in}`。

    Raises:
        ApiError: 会话端点不可用（30001）、来源校验未通过（20003）、
            登录请求失效（20002）、身份平台拒绝兑换（20002）。
    """
    conf = _require_sso_available(request)
    _require_same_site(request)

    pending = _load_pending(request)
    if not states_match(str(pending.get("state") or ""), body.state):
        # 只记"不匹配"，不记两边的值（state 也是凭证的一部分）。
        logger.warning("sso_state_mismatch")
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=PENDING_INVALID_MESSAGE,
            internal_detail="state mismatch between pending cookie and callback",
        )

    tokens = await exchange_code(
        conf,
        body.code,
        str(pending.get("verifier") or ""),
        str(pending.get("redirect_uri") or ""),
    )

    # 顺序有语义：先清暂存（防重放），再下发会话。若兑换失败会在上面抛出，
    # 此时**不会**下发任何会话 Cookie（fail-closed），暂存也保留到过期由浏览器丢弃。
    _clear_pending_cookie(response, conf)
    _issue_session_cookies(response, conf, tokens)
    logger.info("sso_complete", expires_in=tokens.expires_in)
    return ApiResponse[SsoCompleteData].ok(SsoCompleteData(ok=True, expires_in=tokens.expires_in))


@router.get(
    "/session",
    summary="查询登录状态",
    description=(
        "回答当前请求是否已登录、能否刷新、以及令牌是否曾经失效，响应体不含任何令牌。"
        "这是前端启动时的唯一判据：authenticated=true 直接用；"
        "authenticated=false 且 refreshable=true 去调 /sso/refresh；"
        "两者皆 false 才跳登录页。"
    ),
    response_model=ApiResponse[SsoSessionData],
)
async def session_state(request: Request, response: Response) -> ApiResponse[SsoSessionData]:
    """查询当前会话状态。

    **不通告身份**：返回三个布尔与剩余秒数即可，不回显用户 ID、租户或角色
    （那些由 `/api/v1/*` 的业务端点按各自契约给出）。

    **这个端点只清 `oa_session`，绝不清 `oa_refresh`**（QA-01）：
    access TTL 是 300 秒而会话上限是 8 小时，"access 已过期、refresh 仍有效"是
    会话期内的**正常状态**。前端每次启动都会问一次"我还登录着吗"；若这一问顺手
    删掉刷新令牌，8 小时的会话就退化成 300 秒，用户每 5 分钟被踢一次。

    Args:
        request: 请求对象。
        response: 响应对象（令牌失效时追加清除 Cookie）。

    Returns:
        ApiResponse[SsoSessionData]: 登录状态（见 `SsoSessionData`）。

    Raises:
        ApiError: 会话端点不可用（30001）。
    """
    conf = _require_sso_available(request)
    header = request.headers.get("cookie")
    refreshable = bool(read_cookie(header, REFRESH_COOKIE_NAME))
    # 「有 Cookie」与「Cookie 可用」是两件事（QA-05）：`read_cookie` 对**不可用**的值
    # 返回假值——空值（`oa_session=`）、以及若将来把 `oa_session` 放回编码路径时的
    # 非法 base64。这与"根本没有这条 Cookie"无法区分，而两者的正确处置不同：
    # 前者该被清掉（否则是一条永久挂在每个请求上的僵尸 Cookie），后者什么都不用做。
    present = SESSION_COOKIE_NAME in parse_cookie_header(header)
    token = read_cookie(header, SESSION_COOKIE_NAME)

    if not token:
        if present:
            logger.warning("sso_session_cookie_unusable")
            _clear_session_only(response, conf)
            return ApiResponse[SsoSessionData].ok(
                SsoSessionData(authenticated=False, refreshable=refreshable, expired=True)
            )
        return ApiResponse[SsoSessionData].ok(
            SsoSessionData(authenticated=False, refreshable=refreshable)
        )

    try:
        auth = AuthContext.resolve(token, conf)
    except ApiError:
        # 过期/签名不符/身份平台不可达：一律按"未登录"回答，并清掉废的访问令牌。
        # 这里**不**区分 401 与其他失败——前端只关心"要不要刷新/跳登录页"。
        logger.info("sso_session_invalid")
        _clear_session_only(response, conf)
        return ApiResponse[SsoSessionData].ok(
            SsoSessionData(authenticated=False, refreshable=refreshable, expired=True)
        )

    return ApiResponse[SsoSessionData].ok(
        SsoSessionData(
            authenticated=True,
            expires_in=_remaining_seconds(auth.claims),
            refreshable=refreshable,
        )
    )


def _remaining_seconds(claims: dict[str, Any]) -> int | None:
    """从已验证的令牌声明里算出剩余有效期。

    Args:
        claims: 令牌声明（已通过验签，故 `exp` 可信）。

    Returns:
        int | None: 剩余秒数（不小于 0）；声明里没有 `exp` 时返回 None。
    """
    raw = claims.get("exp")
    if not isinstance(raw, int):
        return None
    return max(0, raw - int(time.time()))


@router.post(
    "/refresh",
    summary="续期会话",
    description=(
        "用 httpOnly 的刷新令牌换发新的访问令牌，并覆写两个会话 Cookie。"
        "响应体不含任何令牌。刷新令牌是轮换的，旧值随即失效。"
        "刷新令牌被身份平台判失效（20002）时同时清掉它；网络类失败（30001/30002）不清。"
    ),
    response_model=ApiResponse[SsoRefreshData],
)
async def refresh(
    request: Request,
    response: Response,
) -> ApiResponse[SsoRefreshData]:
    """刷新会话。

    Args:
        request: 请求对象。
        response: 响应对象（覆写 `Set-Cookie`）。

    Returns:
        ApiResponse[SsoRefreshData]: `{ok: true, expires_in}`。

    Raises:
        ApiError: 会话端点不可用（30001）、来源校验未通过（20003）、
            缺少刷新令牌或刷新令牌失效（20002）。
    """
    conf = _require_sso_available(request)
    _require_same_site(request)

    current = read_cookie(request.headers.get("cookie"), REFRESH_COOKIE_NAME)
    if not current:
        logger.info("sso_refresh_cookie_missing")
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=SESSION_INVALID_MESSAGE,
            internal_detail="refresh cookie missing",
        )

    tokens = await _refresh_or_clear_dead_token(request, conf, current)
    _issue_session_cookies(response, conf, tokens)
    logger.info("sso_refreshed", expires_in=tokens.expires_in)
    return ApiResponse[SsoRefreshData].ok(SsoRefreshData(ok=True, expires_in=tokens.expires_in))


async def _refresh_or_clear_dead_token(
    request: Request,
    conf: Settings,
    current: str,
) -> TokenSet:
    """换发令牌；**只有身份平台明确判失效时才清 `oa_refresh`**（QA-02）。

    区分两类失败的判据是错误码，不是"有没有抛异常"：

    - `20002`（`invalid_grant` / 刷新令牌过期/被撤销）：令牌**已死**。不清掉的话
      浏览器会一直带着它，前端每次重试都白打一次身份平台，而用户看不到"刷新已死"；
      此时清 `oa_refresh`，下一次 `/sso/session` 就会答 `refreshable=false`，
      前端立刻走"重新登录"这条正确路径。`oa_session` 不动（可能仍然有效）。
    - `30001` / `30002`（连不上、超时）与其余错误：**临时故障**，绝不清。
      清掉等于把一次网络抖动升级成"强制重新登录"。

    清除头挂在**新抛出的** `ApiError(headers=...)` 上而不是注入的 `Response` 上，
    理由见 `_clear_refresh_header`。

    Args:
        request: 请求对象（仅用于日志）。
        conf: 配置实例。
        current: 当前刷新令牌。

    Returns:
        TokenSet: 新的令牌集合。

    Raises:
        ApiError: 原样抛出下游错误（20002 时附带清 `oa_refresh` 的响应头）。
    """
    try:
        return await refresh_tokens(conf, current)
    except ApiError as exc:
        if exc.code != ErrorCode.UNAUTHORIZED:
            logger.warning(
                "sso_refresh_transient_failure",
                path=request.url.path,
                error_code=int(exc.code),
            )
            raise
        logger.info("sso_refresh_token_dead", path=request.url.path)
        raise ApiError(
            exc.code,
            message=exc.message,
            data=exc.data,
            internal_detail=exc.internal_detail,
            headers=[("set-cookie", _clear_refresh_header(conf))],
        ) from exc


@router.post(
    "/logout",
    summary="退出登录",
    description=(
        "删除两个会话 Cookie，并尽力在身份平台侧撤销刷新令牌。"
        "撤销失败不影响本地登出结果（Cookie 已删，浏览器不再持有任何令牌）。"
    ),
    response_model=ApiResponse[SsoLogoutData],
)
async def logout_session(request: Request, response: Response) -> ApiResponse[SsoLogoutData]:
    """退出登录。

    Args:
        request: 请求对象。
        response: 响应对象（清除 `Set-Cookie`）。

    Returns:
        ApiResponse[SsoLogoutData]: `{ok: true}`。

    Raises:
        ApiError: 会话端点不可用（30001）、来源校验未通过（20003）。
    """
    conf = _require_sso_available(request)
    _require_same_site(request)

    current = read_cookie(request.headers.get("cookie"), REFRESH_COOKIE_NAME)
    # 先删 Cookie 再撤销：撤销是尽力而为（`logout` 不抛），若顺序反过来，
    # 一次 IdP 慢查询就会让用户在这几秒里仍然"登录着"。
    _clear_session_cookies(response, conf)
    await logout(conf, current or "")
    logger.info("sso_logout", had_refresh_token=bool(current))
    return ApiResponse[SsoLogoutData].ok(SsoLogoutData(ok=True))


__all__: list[str] = ["REDIRECT_PATH", "router"]
