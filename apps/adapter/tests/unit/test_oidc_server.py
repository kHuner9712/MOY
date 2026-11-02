"""服务端 PKCE 与令牌交换的协议级用例（P0-5 / `app/core/oidc_server.py`）。

为什么这一层要单独测
--------------------

令牌端点（token endpoint）的失败形态在现场很难复现：4xx `invalid_grant`、
5xx、超时、连不上、**200 但没有 `access_token`**。最后一种最阴——如果代码
在缺 `access_token` 时"照常返回"，浏览器会收到一个空的 `oa_session` Cookie，
症状是"登录看起来成功，但每个业务请求都 401"，排障方向会被引向鉴权中间件。

故这里用 `httpx.MockTransport` 把每一种形态都造出来，断言只有 `access_token`
存在时才产出 `TokenSet`，其余一律 `ApiError(20002)` 且**不回显授权码/令牌**。
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.oidc_server import (
    LOGIN_FAILED_MESSAGE,
    TokenSet,
    authorize_endpoint,
    build_authorize_url,
    exchange_code,
    generate_pkce,
    logout,
    logout_endpoint,
    refresh_tokens,
    states_match,
    token_endpoint,
)

pytestmark = pytest.mark.unit

#: 测试用身份底座基址（内网形态，与 `keycloak_base_url` 的语义一致）
IDP_BASE: str = "http://idp.internal:8080"

#: 测试用客户端密钥
CLIENT_SECRET: str = "unit-test-client-secret"

#: 成功响应的 JSON 样例
TOKEN_PAYLOAD: dict[str, Any] = {
    "access_token": "at-value",
    "refresh_token": "rt-value",
    "expires_in": 300,
    "refresh_expires_in": 1800,
}

#: 回调地址样例（必须与授权请求逐字符一致）
REDIRECT_URI: str = "https://moy.example.com/auth/callback"


@pytest.fixture
def oidc_settings() -> Settings:
    """OIDC 模式的配置实例（confidential client）。

    Returns:
        Settings: 配置。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        auth_mode="oidc",
        jwt_secret="unit-test-jwt-secret-32-bytes-long",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        public_base_url="https://moy.example.com",
        keycloak_base_url=IDP_BASE,
        keycloak_realm="officeai",
        keycloak_client_id="officeai-adapter",
        keycloak_client_secret=CLIENT_SECRET,
    )


def _mock_client(
    handler: Any,
) -> httpx.AsyncClient:
    """构造带 MockTransport 的异步客户端。

    Args:
        handler: `httpx.MockTransport` 的处理函数。

    Returns:
        httpx.AsyncClient: 异步客户端（测试内 `async with` 使用）。
    """
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _form_of(request: httpx.Request) -> dict[str, str]:
    """把请求体解析成表单字典。

    Args:
        request: 被拦截的请求。

    Returns:
        dict[str, str]: 表单字段（同名多值取最后一个）。
    """
    parsed = parse_qs(request.content.decode("utf-8"))
    return {key: values[-1] for key, values in parsed.items() if values}


# ===========================================================================
# 1. PKCE 三元组
# ===========================================================================
def test_generate_pkce_challenge_is_s256_without_padding() -> None:
    """challenge 必须等于 base64url(sha256(verifier)) 且不带 `=` padding。"""
    state, verifier, challenge = generate_pkce()

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert challenge == expected
    assert "=" not in challenge, "RFC 7636 要求 challenge 不带 padding"
    assert state and state != verifier, "state 与 verifier 必须是两个独立随机值"


def test_generate_pkce_verifier_is_long_enough() -> None:
    """verifier 长度必须 ≥ 43（RFC 7636 §4.1 的下限）。"""
    _, verifier, _ = generate_pkce()
    assert len(verifier) >= 43
    assert all(ch.isalnum() or ch in "-_" for ch in verifier), "verifier 必须是 base64url 字符集"


def test_generate_pkce_is_random_per_call() -> None:
    """每次调用都必须产出不同的 state/verifier（不是常量、不是时间戳派生）。"""
    first = generate_pkce()
    second = generate_pkce()
    assert first[0] != second[0]
    assert first[1] != second[1]


@pytest.mark.parametrize(
    ("expected", "received", "verdict"),
    [
        ("abc", "abc", True),
        ("abc", "abd", False),
        ("abc", "", False),
        ("", "abc", False),
        (" abc ", "abc", True),  # 容忍空白
        ("", "", False),
    ],
)
def test_states_match(expected: str, received: str, verdict: bool) -> None:
    """`state` 比对：非空且逐字节相等才通过（空值一律不通过）。"""
    assert states_match(expected, received) is verdict


# ===========================================================================
# 2. 端点推导与授权地址
# ===========================================================================
def test_endpoints_are_derived_from_config(oidc_settings: Settings) -> None:
    """三个端点都由 `keycloak_base_url` + `keycloak_realm` 推导。"""
    base = f"{IDP_BASE}/realms/officeai/protocol/openid-connect"
    assert authorize_endpoint(oidc_settings) == f"{base}/auth"
    assert token_endpoint(oidc_settings) == f"{base}/token"
    assert logout_endpoint(oidc_settings) == f"{base}/logout"


def test_endpoint_derivation_requires_base_url() -> None:
    """基址为空属配置错误（30004），不能拼出一个相对地址去打自己。"""
    conf = Settings(env="test", auth_mode="oidc", keycloak_base_url="")
    with pytest.raises(ApiError) as exc_info:
        token_endpoint(conf)
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_build_authorize_url_carries_pkce_but_not_verifier(oidc_settings: Settings) -> None:
    """授权地址必须带 S256 挑战与 scope，且**不得**带 `code_verifier`。"""
    _, verifier, challenge = generate_pkce()
    url = build_authorize_url(oidc_settings, "state-1", challenge, REDIRECT_URI)

    assert url.startswith(f"{authorize_endpoint(oidc_settings)}?")
    query = parse_qs(urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["officeai-adapter"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["scope"] == ["openid profile email"]
    assert query["state"] == ["state-1"]
    assert query["code_challenge"] == [challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_verifier" not in query, "verifier 绝不能进浏览器地址栏"
    assert verifier not in url


# ===========================================================================
# 3. 授权码换令牌
# ===========================================================================
async def test_exchange_code_posts_expected_form(oidc_settings: Settings) -> None:
    """`authorization_code` 的表单字段与返回解析都必须正确。"""
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/protocol/openid-connect/token")
        seen.append(_form_of(request))
        return httpx.Response(200, json=TOKEN_PAYLOAD)

    async with _mock_client(handler) as client:
        tokens = await exchange_code(
            oidc_settings, "the-code", "the-verifier", REDIRECT_URI, client=client
        )

    assert seen == [
        {
            "grant_type": "authorization_code",
            "code": "the-code",
            "code_verifier": "the-verifier",
            "redirect_uri": REDIRECT_URI,
            "client_id": "officeai-adapter",
            "client_secret": CLIENT_SECRET,
        }
    ]
    assert tokens == TokenSet(
        access_token="at-value",
        refresh_token="rt-value",
        expires_in=300,
        refresh_expires_in=1800,
    )


async def test_exchange_code_omits_secret_for_public_client(oidc_settings: Settings) -> None:
    """`client_secret` 为空时不带该键（public client + PKCE 形态）。"""
    public = oidc_settings.model_copy(update={"keycloak_client_secret": ""})
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_form_of(request))
        return httpx.Response(200, json=TOKEN_PAYLOAD)

    async with _mock_client(handler) as client:
        await exchange_code(public, "c", "v", REDIRECT_URI, client=client)

    assert "client_secret" not in seen[0]
    assert seen[0]["client_id"] == "officeai-adapter"


async def test_exchange_code_rejects_non_200(oidc_settings: Settings) -> None:
    """4xx 必须炸成 20002，且内部细节**不含**授权码。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await exchange_code(oidc_settings, "secret-code", "v", REDIRECT_URI, client=client)

    error = exc_info.value
    assert error.code == ErrorCode.UNAUTHORIZED
    assert error.message == LOGIN_FAILED_MESSAGE
    assert "secret-code" not in (error.internal_detail or "")
    assert "400" in (error.internal_detail or "")


async def test_exchange_code_rejects_response_without_access_token(
    oidc_settings: Settings,
) -> None:
    """200 但缺 `access_token` → 20002（绝不返回空令牌集）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"refresh_token": "rt-only", "expires_in": 300})

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await exchange_code(oidc_settings, "c", "v", REDIRECT_URI, client=client)

    assert exc_info.value.code == ErrorCode.UNAUTHORIZED
    assert "access_token" in (exc_info.value.internal_detail or "")


async def test_exchange_code_rejects_non_json_response(oidc_settings: Settings) -> None:
    """响应不是 JSON 时同样 fail-closed。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await exchange_code(oidc_settings, "c", "v", REDIRECT_URI, client=client)

    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


async def test_exchange_code_maps_timeout(oidc_settings: Settings) -> None:
    """超时映射为 30002（下游超时），不是"令牌非法"。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await exchange_code(oidc_settings, "c", "v", REDIRECT_URI, client=client)

    assert exc_info.value.code == ErrorCode.DOWNSTREAM_TIMEOUT


async def test_exchange_code_maps_connection_error(oidc_settings: Settings) -> None:
    """连不上映射为 30001（下游不可用）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await exchange_code(oidc_settings, "c", "v", REDIRECT_URI, client=client)

    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE


# ===========================================================================
# 4. 刷新
# ===========================================================================
async def test_refresh_tokens_posts_refresh_grant(oidc_settings: Settings) -> None:
    """`refresh_token` grant 的表单与解析。"""
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(_form_of(request))
        return httpx.Response(
            200,
            json={
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "expires_in": 300,
                "refresh_expires_in": 1800,
            },
        )

    async with _mock_client(handler) as client:
        tokens = await refresh_tokens(oidc_settings, "rt-old", client=client)

    assert seen[0]["grant_type"] == "refresh_token"
    assert seen[0]["refresh_token"] == "rt-old"
    assert tokens.access_token == "at-new"
    assert tokens.refresh_token == "rt-new", "刷新令牌是轮换的，必须带回新值"


async def test_refresh_tokens_rejects_invalid_grant(oidc_settings: Settings) -> None:
    """刷新令牌失效 → 20002（前端据此跳登录页）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await refresh_tokens(oidc_settings, "rt-old", client=client)

    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


# ===========================================================================
# 5. 登出（必须尽力而为：失败不抛）
# ===========================================================================
async def test_logout_posts_refresh_token(oidc_settings: Settings) -> None:
    """登出把 `refresh_token` 交给 IdP 撤销。"""
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/protocol/openid-connect/logout")
        seen.append(_form_of(request))
        return httpx.Response(204)

    async with _mock_client(handler) as client:
        await logout(oidc_settings, "rt-to-revoke", client=client)

    assert seen[0]["refresh_token"] == "rt-to-revoke"
    assert seen[0]["client_id"] == "officeai-adapter"


async def test_logout_swallows_server_error(oidc_settings: Settings) -> None:
    """IdP 返回 5xx 时**不抛**——本地 Cookie 该删还是要删。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    async with _mock_client(handler) as client:
        await logout(oidc_settings, "rt", client=client)  # 不抛即通过


async def test_logout_swallows_transport_error(oidc_settings: Settings) -> None:
    """连不上 IdP 时同样不抛（否则用户"登不出去"）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _mock_client(handler) as client:
        await logout(oidc_settings, "rt", client=client)


async def test_logout_without_token_makes_no_request(oidc_settings: Settings) -> None:
    """没有刷新令牌时直接返回，不产生请求。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    async with _mock_client(handler) as client:
        await logout(oidc_settings, "", client=client)

    assert calls == []


async def test_logout_with_unconfigured_endpoint_does_not_raise() -> None:
    """基址缺失时也走"尽力而为"分支，不把登出变成 500。"""
    conf = Settings(env="test", auth_mode="oidc", keycloak_base_url="")
    await logout(conf, "rt")


# ===========================================================================
# 6. `TokenSet` 的容错
# ===========================================================================
@pytest.mark.parametrize(
    ("payload", "expires_in", "refresh_expires_in"),
    [
        ({"access_token": "a", "expires_in": 300, "refresh_expires_in": 1800}, 300, 1800),
        ({"access_token": "a", "expires_in": "300"}, 300, 0),  # 字符串形态
        ({"access_token": "a"}, 0, 0),  # 缺失
        ({"access_token": "a", "expires_in": None}, 0, 0),
        ({"access_token": "a", "expires_in": -5}, 0, 0),  # 负值归零
        ({"access_token": "a", "expires_in": "abc"}, 0, 0),  # 非法
    ],
)
def test_token_set_tolerates_expiry_variants(
    payload: dict[str, Any], expires_in: int, refresh_expires_in: int
) -> None:
    """有效期字段的各种形态都要能解析（不该让一次登录因它失败）。"""
    tokens = TokenSet.from_payload(payload)
    assert tokens.expires_in == expires_in
    assert tokens.refresh_expires_in == refresh_expires_in


def test_token_set_requires_access_token() -> None:
    """缺 `access_token` 一律 20002（fail-closed 的最后一道闸）。"""
    with pytest.raises(ApiError) as exc_info:
        TokenSet.from_payload({"refresh_token": "rt"})
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


# ===========================================================================
# 7. 上游 5xx 与 4xx 必须分开（QA-06）
# ===========================================================================
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_exchange_code_maps_upstream_5xx_to_unavailable(
    oidc_settings: Settings, status: int
) -> None:
    """上游 5xx → `30001`（临时故障），**不是** `20002`（凭证已死）。

    这个区分决定 `/sso/refresh` 会不会把仍然有效的刷新令牌当"已死"清掉。真实现场里
    "LB/ingress 后面的身份平台挂了"多数就表现为 5xx 响应，而不是 TCP 连不上——
    若归成 20002，身份平台恢复后用户仍要重新输密码。

    Args:
        oidc_settings: 配置。
        status: 上游状态码。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="upstream busy")

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await exchange_code(oidc_settings, "c", "v", REDIRECT_URI, client=client)

    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE
    assert str(status) in (exc_info.value.internal_detail or ""), (
        "上游状态码必须进内部细节——那是现场判断『网关挂了还是令牌坏了』的唯一线索"
    )


async def test_refresh_tokens_maps_upstream_5xx_to_unavailable(oidc_settings: Settings) -> None:
    """刷新路径同样：上游 5xx → 30001（不会被当成死令牌清掉）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream busy")

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await refresh_tokens(oidc_settings, "rt", client=client)

    assert exc_info.value.code == ErrorCode.DOWNSTREAM_UNAVAILABLE


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (408, ErrorCode.DOWNSTREAM_UNAVAILABLE),  # 服务端等请求超时：临时
        (429, ErrorCode.RATE_LIMITED),  # 限流：临时，且要单独一个"可退避"的信号
    ],
)
async def test_exchange_code_maps_408_and_429_to_transient(
    oidc_settings: Settings, status: int, expected: ErrorCode
) -> None:
    """408/429 不是"凭证已死"（QA-07）：408 → 30001，429 → 20006。

    这两个码落在 4xx 桶里，语义上却是**典型临时故障**（身份平台过载时会主动限流）。
    429 尤其危险：access TTL 统一 300 s → 一批用户同时刷新 → IdP 429 → 若归成 20002
    就会清掉这批人的刷新令牌 → 全员被推去重新登录（比刷新重得多）→ 登录风暴 → 再一轮 429。
    用 20006 而不是 30001 是给前端一个**可退避重试**的明确信号。

    Args:
        oidc_settings: 配置。
        status: 上游状态码。
        expected: 期望的业务错误码。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="slow down")

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await exchange_code(oidc_settings, "c", "v", REDIRECT_URI, client=client)

    assert exc_info.value.code == expected
    assert str(status) in (exc_info.value.internal_detail or "")


async def test_refresh_tokens_maps_429_to_rate_limited(oidc_settings: Settings) -> None:
    """刷新路径同样把 429 归成 20006（`/sso/refresh` 因此不会清刷新令牌）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    async with _mock_client(handler) as client:
        with pytest.raises(ApiError) as exc_info:
            await refresh_tokens(oidc_settings, "rt", client=client)

    assert exc_info.value.code == ErrorCode.RATE_LIMITED
