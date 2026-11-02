"""服务端 PKCE 与令牌交换（P0-5 路线 A：令牌不进 JavaScript）。

职责边界
--------

**只做协议，不碰 HTTP 层**。本模块不读 `Request`、不写 `Set-Cookie`、不判断
`Sec-Fetch-Site`——那些属于 `app/api/sso_routes.py` 与 `app/core/csrf.py`。
分开的理由是这类代码必须能被纯单测穷举：token endpoint 的失败形态（4xx、
5xx、超时、连不上、200 但没有 `access_token`）在集成测试里很难全都造出来，
而在 `httpx.MockTransport` 下逐个构造只要几行。

为什么在服务端做 PKCE（而不是浏览器）
--------------------------------------

路线 A 的前提是**令牌从不经过 JavaScript**。若 PKCE 仍放在浏览器里跑，
`code_verifier` 与兑换回来的 `access_token` 都必须先落在 JS 变量里——那就回到
了原来要解决的问题。因此 `state` / `verifier` / `challenge` 全在本层生成，
`verifier` 经 `oa_oidc_pending` Cookie 暂存（HttpOnly），浏览器只经手 `code`。

失败一律 fail-closed
--------------------

token endpoint 返回 **4xx**、响应不是 JSON、或 JSON 里没有 `access_token`，
**全部抛 `ApiError(20002)`** 并附内部细节（只写状态码与端点路径，不写响应体：
响应体里可能含有令牌，日志落地即等于二次泄露）。绝不"降级"为下发空 Cookie——
空的 `oa_session` 会让后续每个请求都走到验签失败，症状看起来像"登录成功但立刻
掉线"，比一次明确的登录失败难查得多。

**5xx 单独归成 30001（上游不可用），不是 20002**（QA-06）：真实现场里"LB/ingress
后面的身份平台挂了"多数表现为 5xx 响应而不是 TCP 连不上。这个区分决定的是
`/sso/refresh` 会不会把**仍然有效**的刷新令牌当"已死"清掉——
临时故障应当让用户重试，而不是把他踢去重新登录。判据只有一条：
**4xx = 这份凭证/这次请求有问题；5xx = 这一次问不到答案**。

不做重试
--------

`authorization_code` 与 `refresh_token` 两种 grant 都是**一次性凭证**：
Keycloak 在第一次兑换成功后就作废它们。重试只会在"第一次其实已成功、只是响应丢了"
的情况下把一次成功变成一次 `invalid_grant`，并把真实的令牌留在 IdP 侧无人回收。
故 `_post_form` 只发一次请求（对比 `KeycloakClient._request` 的幂等重试策略，
那里读的是自省/资料接口，重试是安全的）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import httpx

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import TRACE_HEADER

logger = get_logger(__name__)

#: 授权码模式的 `response_type`
RESPONSE_TYPE_CODE: Final[str] = "code"

#: 申请的作用域：`openid` 是 OIDC 必需，`profile`/`email` 供用户名与邮箱声明使用
AUTHORIZE_SCOPE: Final[str] = "openid profile email"

#: PKCE 的挑战算法（RFC 7636 §4.2）
PKCE_CHALLENGE_METHOD: Final[str] = "S256"

#: `secrets.token_urlsafe(32)` 的熵（32 字节 = 256 位）
PKCE_ENTROPY_BYTES: Final[int] = 32

#: 登录失败时对终端用户的统一文案。
#:
#: 刻意**不区分**"授权码无效 / 已用过 / 过期 / 身份平台不可达"：这些差异对终端用户
#: 没有可操作价值，却会告诉攻击者"这个 code 到底是被重放了还是本来就不存在"。
#: 具体原因只进日志（`internal_detail`）。
LOGIN_FAILED_MESSAGE: Final[str] = "登录失败，请重新发起登录"


@dataclass(frozen=True, slots=True)
class TokenSet:
    """一次令牌交换的结果。

    **只在本模块与路由层之间传递，不得进入任何响应体**（P0-5 的核心不变式）。

    Attributes:
        access_token: 访问令牌原文（放进 httpOnly 的 `oa_session` Cookie）。
        refresh_token: 刷新令牌原文（放进 httpOnly 的 `oa_refresh` Cookie）；
            public client 或未申请 offline 作用域时可能为空串。
        expires_in: 访问令牌有效期（秒）；缺失或非法时为 0。
        refresh_expires_in: 刷新令牌有效期（秒）；缺失或非法时为 0。
    """

    access_token: str
    refresh_token: str = ""
    expires_in: int = 0
    refresh_expires_in: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TokenSet:
        """从 token endpoint 的 JSON 响应构造。

        Args:
            payload: token endpoint 返回的 JSON 对象。

        Returns:
            TokenSet: 解析结果。

        Raises:
            ApiError: 响应缺少 `access_token`（20002，fail-closed）。
        """
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            # 200 但没有 access_token：IdP 行为异常或中间有代理改写了响应。
            # 这里必须炸——下发空 Cookie 等于"登录成功但每个请求都 401"。
            raise ApiError(
                ErrorCode.UNAUTHORIZED,
                message=LOGIN_FAILED_MESSAGE,
                internal_detail="token response missing access_token",
            )
        return cls(
            access_token=access_token,
            refresh_token=str(payload.get("refresh_token") or "").strip(),
            expires_in=_as_int(payload.get("expires_in")),
            refresh_expires_in=_as_int(payload.get("refresh_expires_in")),
        )


def _as_int(value: Any) -> int:
    """把任意输入转成非负整数（失败即 0）。

    IdP 返回的 `expires_in` 通常是数字，但也见过字符串形态。解析失败退化成 0 而
    不是抛错：有效期只用于 Cookie 的 `Max-Age` 参考值，不值得让整次登录失败。

    Args:
        value: 待转换的值。

    Returns:
        int: 非负整数。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def generate_pkce() -> tuple[str, str, str]:
    """生成 PKCE 三元组。

    Returns:
        tuple[str, str, str]: `(state, code_verifier, code_challenge)`。

    Notes:
        `state` 与 `verifier` 都用 `secrets.token_urlsafe(32)`：256 位熵，且输出本就是
        `A-Za-z0-9-_`（可直接放进 Cookie 与查询串，无需再转义）。
        `challenge = base64url(sha256(verifier))` **去掉 padding**——RFC 7636 要求
        不带 `=`，带上会被部分 IdP 判为非法。
    """
    state = secrets.token_urlsafe(PKCE_ENTROPY_BYTES)
    verifier = secrets.token_urlsafe(PKCE_ENTROPY_BYTES)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return state, verifier, challenge


def states_match(expected: str, received: str) -> bool:
    """常量时间比对 `state`。

    **必须用常量时间比较**：`state` 是本流程里唯一天然"值未知但可猜"的字符串，
    普通 `==` 会在首字节不同时提前返回，理论上可被计时侧信道逐字节猜出。

    Args:
        expected: 暂存 Cookie 里的 `state`。
        received: 回调带回的 `state`。

    Returns:
        bool: 两者非空且逐字节相等返回 True。
    """
    left = (expected or "").strip()
    right = (received or "").strip()
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)


def _realm_base(conf: Settings) -> str:
    """推导 realm 下的 OIDC 端点基址。

    Args:
        conf: 配置实例。

    Returns:
        str: 形如 `http://keycloak:8080/realms/officeai/protocol/openid-connect`。

    Raises:
        ApiError: `keycloak_base_url` 为空（30004，配置错误）。
    """
    base = conf.keycloak_base_url.strip().rstrip("/")
    if not base:
        # 正常由 `validate_auth_startup` 在启动期拦住（生产/预发），
        # 这里兜底：拼一个 `/realms/...` 相对地址只会得到与身份无关的 404。
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail="identity base url is empty: OA_KEYCLOAK_BASE_URL is required",
        )
    realm = conf.keycloak_realm.strip().strip("/")
    return f"{base}/realms/{realm}/protocol/openid-connect"


def authorize_endpoint(conf: Settings) -> str:
    """授权端点地址。

    Args:
        conf: 配置实例。

    Returns:
        str: `.../protocol/openid-connect/auth`。
    """
    return f"{_realm_base(conf)}/auth"


def token_endpoint(conf: Settings) -> str:
    """令牌端点地址。

    Args:
        conf: 配置实例。

    Returns:
        str: `.../protocol/openid-connect/token`。
    """
    return f"{_realm_base(conf)}/token"


def logout_endpoint(conf: Settings) -> str:
    """登出端点地址。

    Args:
        conf: 配置实例。

    Returns:
        str: `.../protocol/openid-connect/logout`。
    """
    return f"{_realm_base(conf)}/logout"


def build_authorize_url(
    conf: Settings,
    state: str,
    challenge: str,
    redirect_uri: str,
) -> str:
    """拼装授权请求地址（浏览器 302 的目标）。

    **入参只有 `challenge`，没有 `verifier`**：签名上就不可能把 verifier 拼进 URL
    （它一旦进了浏览器地址栏，PKCE 提供的保护即归零）。

    Args:
        conf: 配置实例。
        state: 随机状态串。
        challenge: S256 挑战值。
        redirect_uri: 回调地址（必须与 token 请求里的 `redirect_uri` 逐字符相同）。

    Returns:
        str: 完整授权地址。

    Raises:
        ApiError: 身份底座基址缺失（30004）。
    """
    query = urlencode(
        {
            "response_type": RESPONSE_TYPE_CODE,
            "client_id": conf.keycloak_client_id,
            "redirect_uri": redirect_uri,
            "scope": AUTHORIZE_SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": PKCE_CHALLENGE_METHOD,
        }
    )
    return f"{authorize_endpoint(conf)}?{query}"


def _client_form(conf: Settings) -> dict[str, str]:
    """客户端身份参数（`client_id` + 可选 `client_secret`）。

    Args:
        conf: 配置实例。

    Returns:
        dict[str, str]: 表单字段。`client_secret` 为空时**不带这个键**——
            带空值会让 confidential client 的校验失败，而 public client（配合 PKCE）
            本就不该有这个字段。
    """
    form = {"client_id": conf.keycloak_client_id}
    secret = conf.keycloak_client_secret.strip()
    if secret:
        form["client_secret"] = secret
    return form


def _headers() -> dict[str, str]:
    """构造表单请求头（含链路透传）。

    Returns:
        dict[str, str]: 请求头。
    """
    import structlog.contextvars

    return {
        "Content-Type": "application/x-www-form-urlencoded",
        TRACE_HEADER: structlog.contextvars.get_contextvars().get("trace_id", "-"),
    }


def _build_client(conf: Settings) -> httpx.AsyncClient:
    """构造一次性 HTTP 客户端。

    与 `KeycloakClient` 常驻连接池不同：令牌端点只在登录/刷新/登出时各调一次
    （低频，分钟级），为它维护常驻池反而多一个生命周期要管。超时口径沿用
    `keycloak_*_timeout_seconds`，与身份底座的其余调用保持一致。

    Args:
        conf: 配置实例。

    Returns:
        httpx.AsyncClient: 异步客户端（调用方负责 `aclose`）。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            timeout=conf.keycloak_read_timeout_seconds,
            connect=conf.keycloak_connect_timeout_seconds,
        )
    )


def _endpoint_label(url: str) -> str:
    """取端点路径用于内部诊断（**不含查询串，绝不含表单体**）。

    Args:
        url: 完整地址。

    Returns:
        str: 形如 `/realms/officeai/protocol/openid-connect/token`。
    """
    return urlsplit(url).path or url


async def _post_form(
    conf: Settings,
    url: str,
    form: dict[str, str],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST 表单并解析 JSON（**不重试**，理由见模块 docstring）。

    Args:
        conf: 配置实例。
        url: 目标地址。
        form: 表单字段。
        client: 可选的外部客户端（测试注入 `httpx.MockTransport` 用）。

    Returns:
        dict[str, Any]: 解析后的 JSON 对象。

    Raises:
        ApiError: 超时（30002）、连接失败（30001）、**上游 5xx**（30001）、
            上游 4xx 或非 JSON（20002）。
    """
    owns_client = client is None
    http = client if client is not None else _build_client(conf)
    label = _endpoint_label(url)
    try:
        response = await http.post(url, data=form, headers=_headers())
    except httpx.TimeoutException as exc:
        logger.warning("identity_token_timeout", endpoint=label)
        raise ApiError(ErrorCode.DOWNSTREAM_TIMEOUT, internal_detail=f"endpoint={label}") from exc
    except httpx.HTTPError as exc:
        logger.warning("identity_token_unreachable", endpoint=label)
        raise ApiError(
            ErrorCode.DOWNSTREAM_UNAVAILABLE, internal_detail=f"endpoint={label}"
        ) from exc
    finally:
        if owns_client:
            await http.aclose()

    status = response.status_code
    if status >= httpx.codes.INTERNAL_SERVER_ERROR:
        # **5xx 是上游自己的故障，不是"令牌已死"**（QA-06）。真实现场里"LB/ingress
        # 后面的身份平台挂了"多数就表现为 5xx 响应，而不是 TCP 连不上；若把它归成
        # 20002，`/sso/refresh` 会顺手清掉仍然有效的刷新令牌 —— 身份平台恢复后用户
        # 仍要重新输密码，而本来靠刷新就能自愈。故与"连不上/超时"同码（30001），
        # 让 `_refresh_or_clear_dead_token` 的"临时故障不清 Cookie"逻辑天然生效。
        # 状态码必须进日志/内部细节：那是现场判断"是网关挂了还是令牌坏了"的唯一线索。
        logger.warning("identity_token_upstream_error", endpoint=label, status_code=status)
        raise ApiError(
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            internal_detail=f"endpoint={label} idp_status={status}",
        )
    if status != httpx.codes.OK:
        # 4xx 才是**凭证类**问题（`invalid_grant` / `invalid_client` / `code` 已用过）。
        # 只记状态码：响应体里可能带 `error_description`，也可能（异常情况下）带回
        # 令牌片段，写进日志等于二次泄露。
        logger.warning("identity_token_rejected", endpoint=label, status_code=status)
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=LOGIN_FAILED_MESSAGE,
            internal_detail=f"endpoint={label} status={status}",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        logger.error("identity_token_response_not_json", endpoint=label)
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=LOGIN_FAILED_MESSAGE,
            internal_detail=f"endpoint={label} response is not json",
        ) from exc
    if not isinstance(payload, dict):
        logger.error("identity_token_response_not_object", endpoint=label)
        raise ApiError(
            ErrorCode.UNAUTHORIZED,
            message=LOGIN_FAILED_MESSAGE,
            internal_detail=f"endpoint={label} response is not an object",
        )
    return payload


async def exchange_code(
    conf: Settings,
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> TokenSet:
    """用授权码换取令牌（`grant_type=authorization_code`）。

    Args:
        conf: 配置实例。
        code: 回调带回的授权码。
        verifier: 与服务端暂存一致的 `code_verifier`。
        redirect_uri: 与授权请求里逐字符相同的回调地址。
        client: 可选的外部客户端（测试注入用）。

    Returns:
        TokenSet: 令牌集合。

    Raises:
        ApiError: 授权码非法/已用过/过期（20002）、下游不可用（30001/30002）。
    """
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        **_client_form(conf),
    }
    payload = await _post_form(conf, token_endpoint(conf), form, client=client)
    return TokenSet.from_payload(payload)


async def refresh_tokens(
    conf: Settings,
    refresh_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> TokenSet:
    """用刷新令牌续期（`grant_type=refresh_token`）。

    刷新令牌是**轮换**的：成功一次后旧值即失效，调用方必须用响应里的新值覆写
    `oa_refresh` Cookie（见 `app/api/sso_routes.py::refresh`）。不覆写的话下一次
    刷新必然 `invalid_grant`，症状是"登录 5 分钟后掉线一次、再登录 5 分钟又掉"。

    Args:
        conf: 配置实例。
        refresh_token: 当前刷新令牌原文。
        client: 可选的外部客户端（测试注入用）。

    Returns:
        TokenSet: 新的令牌集合（`refresh_token` 通常已轮换）。

    Raises:
        ApiError: 刷新令牌失效（20002）、下游不可用（30001/30002）。
    """
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        **_client_form(conf),
    }
    payload = await _post_form(conf, token_endpoint(conf), form, client=client)
    return TokenSet.from_payload(payload)


async def logout(
    conf: Settings,
    refresh_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """撤销刷新令牌（登出的服务端半边）。

    **失败不抛**：登出是"尽力而为"的动作，本地 Cookie 该删还是要删。若这里抛错，
    `/sso/logout` 会返回 5xx 且**清 Cookie 的响应头一并丢失**——用户被卡在
    "登不出去"的状态，比"IdP 侧刷新令牌多活一会儿"更糟（后者实际无害：浏览器已
    不再持有它，且它有 `ssoSessionIdleTimeout` 约束）。

    Args:
        conf: 配置实例。
        refresh_token: 待撤销的刷新令牌；为空时直接返回（无事可做）。
        client: 可选的外部客户端（测试注入用）。
    """
    token = (refresh_token or "").strip()
    if not token:
        logger.info("identity_logout_skipped", reason="no refresh token")
        return
    try:
        url = logout_endpoint(conf)
    except ApiError as exc:
        logger.warning(
            "identity_logout_skipped",
            reason="endpoint unconfigured",
            detail=exc.internal,
        )
        return

    form = {"refresh_token": token, **_client_form(conf)}
    owns_client = client is None
    http = client if client is not None else _build_client(conf)
    label = _endpoint_label(url)
    try:
        response = await http.post(url, data=form, headers=_headers())
        if response.status_code >= httpx.codes.BAD_REQUEST:
            logger.warning(
                "identity_logout_rejected",
                endpoint=label,
                status_code=response.status_code,
            )
    except httpx.HTTPError as exc:
        logger.warning("identity_logout_unreachable", endpoint=label, error=type(exc).__name__)
    finally:
        if owns_client:
            await http.aclose()


__all__: list[str] = [
    "AUTHORIZE_SCOPE",
    "LOGIN_FAILED_MESSAGE",
    "PKCE_CHALLENGE_METHOD",
    "RESPONSE_TYPE_CODE",
    "TokenSet",
    "authorize_endpoint",
    "build_authorize_url",
    "exchange_code",
    "generate_pkce",
    "logout",
    "logout_endpoint",
    "refresh_tokens",
    "states_match",
    "token_endpoint",
]
