"""会话端点（`/sso/*`）与 Cookie 凭证轨的行为用例（P0-5 路线 A）。

这组用例钉死三件事
------------------

1. **响应体不含令牌**。这是 P0-5 的**全部意义**：令牌只走 httpOnly Cookie。
   每一条成功路径都用 `assert "access_token" not in response.text` 这类硬断言，
   而不是"看看 code 是不是 0"——后者在把令牌顺手塞进 `data` 里时仍然会绿。
2. **失败一律 fail-closed**：state 不匹配 / 暂存缺失 / 暂存过期 / 兑换失败，
   都不得下发会话 Cookie，且对终端只回同一句「登录请求已失效」。
3. **双轨互不干扰**：有 `Authorization` 头时走 Bearer 轨且**不判 CSRF**；
   无头时才读 Cookie 并判 `Sec-Fetch-Site`。

为什么测试客户端用 `https://testserver`
---------------------------------------

会话 Cookie 带 `Secure`（`OA_PUBLIC_BASE_URL` 是 https）。httpx 的 CookieJar
严格遵守 `Secure`：http 请求**不会**带上它。用 `http://testserver` 跑会得到
"凭证凭空消失"的假象——那其实证明 `Secure` 生效了。用 https 基址才是现场形态。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import sso_routes
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.oidc_server import TokenSet
from app.core.security import extract_credential
from app.core.session_cookie import (
    PENDING_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SSO_COOKIE_PATH,
    build_set_cookie,
    encode_pending,
    read_cookie,
)
from app.main import create_app
from tests.conftest import make_expired_token, make_token

pytestmark = pytest.mark.unit

#: 同源请求的 Fetch Metadata（浏览器对同源 fetch/XHR 的默认值）
SAME_ORIGIN: dict[str, str] = {"Sec-Fetch-Site": "same-origin"}

#: 跨站请求的 Fetch Metadata
CROSS_SITE: dict[str, str] = {"Sec-Fetch-Site": "cross-site"}

#: 五个会话端点（方法, 路径, 请求体）
SSO_CALLS: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("POST", "/sso/start", {}),
    ("POST", "/sso/complete", {"code": "c", "state": "s"}),
    ("GET", "/sso/session", None),
    ("POST", "/sso/refresh", None),
    ("POST", "/sso/logout", None),
)

#: 响应体里绝不能出现的字段名（P0-5 不变式）
FORBIDDEN_TOKEN_KEYS: tuple[str, ...] = ("access_token", "refresh_token", "id_token")


# ===========================================================================
# 夹具
# ===========================================================================
@pytest.fixture
def sso_settings() -> Settings:
    """OIDC 形态的配置（`/sso/*` 只有在此形态下可用）。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        auth_mode="oidc",
        jwt_secret="unit-test-jwt-secret-32-bytes-long",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        public_base_url="https://moy.example.com",
        keycloak_base_url="http://idp.internal:8080",
        keycloak_realm="officeai",
        keycloak_client_id="officeai-adapter",
        keycloak_client_secret="unit-test-client-secret",
        auth_cookie_enabled=True,
    )


@pytest.fixture
def sso_app(sso_settings: Settings) -> FastAPI:
    """注入 OIDC 配置的应用实例。

    Args:
        sso_settings: 配置。

    Returns:
        FastAPI: 应用实例。
    """
    return create_app(sso_settings)


@pytest.fixture
def sso_client(sso_app: FastAPI) -> Iterator[TestClient]:
    """https 基址的测试客户端（`Secure` Cookie 才能被回传）。

    Args:
        sso_app: 应用实例。

    Yields:
        TestClient: 测试客户端。
    """
    with TestClient(sso_app, base_url="https://testserver") as client:
        yield client


# ===========================================================================
# 辅助函数
# ===========================================================================
def _set_cookies(response: Any) -> list[str]:
    """取出响应里的全部 `Set-Cookie`。

    Args:
        response: httpx 响应对象。

    Returns:
        list[str]: 头值列表（保持顺序）。
    """
    return [value for key, value in response.headers.multi_items() if key.lower() == "set-cookie"]


def _cookie_named(headers: list[str], name: str) -> str:
    """从 `Set-Cookie` 列表里取出指定 Cookie 的那一条。

    Args:
        headers: `Set-Cookie` 头值列表。
        name: Cookie 名。

    Returns:
        str: 该 Cookie 的头值。

    Raises:
        AssertionError: 未下发该 Cookie（用例应失败，而不是静默跳过断言）。
    """
    for value in headers:
        if value.startswith(f"{name}="):
            return value
    raise AssertionError(f"响应里没有 {name} 的 Set-Cookie：{headers}")


def _assert_no_token_leak(text: str) -> None:
    """断言响应体不含任何令牌字段。

    Args:
        text: 响应体原文。
    """
    lowered = text.lower()
    for key in FORBIDDEN_TOKEN_KEYS:
        assert key not in lowered, f"响应体泄露了 {key}：{text}"


def _state_of(authorize_url: str) -> str:
    """从授权地址里取出 `state`。

    Args:
        authorize_url: `/sso/start` 返回的地址。

    Returns:
        str: state 值。
    """
    query = parse_qs(urlsplit(authorize_url).query)
    return query["state"][0]


def _patch_exchange(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    refresh_value: str = "rt-value",
    error: ApiError | None = None,
) -> list[tuple[str, str, str]]:
    """替换 `exchange_code`，记录调用参数。

    Args:
        monkeypatch: pytest 补丁器。
        settings: 用于签发**真实可用**测试令牌的配置。
        refresh_token: 返回的刷新令牌。
        error: 非空时抛出该异常（模拟兑换失败）。

    Returns:
        list[tuple[str, str, str]]: 调用记录 `(code, verifier, redirect_uri)`。
    """
    calls: list[tuple[str, str, str]] = []

    async def fake_exchange(
        conf: Settings, code: str, verifier: str, redirect_uri: str
    ) -> TokenSet:
        calls.append((code, verifier, redirect_uri))
        if error is not None:
            raise error
        return TokenSet(
            access_token=make_token(settings=settings),
            refresh_token=refresh_value,
            expires_in=300,
            refresh_expires_in=1800,
        )

    monkeypatch.setattr(sso_routes, "exchange_code", fake_exchange)
    return calls


def _patch_exchange_token(
    monkeypatch: pytest.MonkeyPatch, access_token: str, *, refresh_value: str
) -> None:
    """替换 `exchange_code`，返回**指定的**访问令牌（用于度量 Cookie 体积）。

    Args:
        monkeypatch: pytest 补丁器。
        access_token: 期望下发到 `oa_session` 的令牌原文。
        refresh_value: 刷新令牌值。
    """

    async def fake_exchange(
        conf: Settings, code: str, verifier: str, redirect_uri: str
    ) -> TokenSet:
        return TokenSet(
            access_token=access_token,
            refresh_token=refresh_value,
            expires_in=300,
            refresh_expires_in=1800,
        )

    monkeypatch.setattr(sso_routes, "exchange_code", fake_exchange)


def _patch_refresh(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    rotated_refresh_value: str = "rt-rotated",
    error: ApiError | None = None,
) -> list[str]:
    """替换 `refresh_tokens`，记录收到的刷新令牌。

    Args:
        monkeypatch: pytest 补丁器。
        settings: 配置（签发可用令牌）。
        new_refresh_token: 轮换后的刷新令牌。
        error: 非空时抛出该异常。

    Returns:
        list[str]: 调用时收到的刷新令牌列表。
    """
    calls: list[str] = []

    async def fake_refresh(conf: Settings, refresh_token: str) -> TokenSet:
        calls.append(refresh_token)
        if error is not None:
            raise error
        return TokenSet(
            access_token=make_token(settings=settings),
            refresh_token=rotated_refresh_value,
            expires_in=300,
            refresh_expires_in=1800,
        )

    monkeypatch.setattr(sso_routes, "refresh_tokens", fake_refresh)
    return calls


def _patch_logout(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """替换 `logout`，记录被撤销的刷新令牌。

    Args:
        monkeypatch: pytest 补丁器。

    Returns:
        list[str]: 收到的刷新令牌列表。
    """
    calls: list[str] = []

    async def fake_logout(conf: Settings, refresh_token: str) -> None:
        calls.append(refresh_token)

    monkeypatch.setattr(sso_routes, "logout", fake_logout)
    return calls


def _login(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> str:
    """跑一次完整登录（start → complete），返回回调时的 `code`。

    Args:
        client: 测试客户端。
        monkeypatch: pytest 补丁器。
        settings: 配置（签发令牌）。

    Returns:
        str: 授权码。
    """
    _patch_exchange(monkeypatch, settings)
    start = client.post("/sso/start", json={}, headers=SAME_ORIGIN)
    assert start.status_code == 200
    state = _state_of(start.json()["data"]["authorize_url"])
    complete = client.post(
        "/sso/complete", json={"code": "the-code", "state": state}, headers=SAME_ORIGIN
    )
    assert complete.status_code == 200, complete.text
    return "the-code"


# ===========================================================================
# 1. 503 闸门：stub / Cookie 轨关闭时五个端点一律不可用
# ===========================================================================
@pytest.mark.parametrize(("method", "path", "body"), SSO_CALLS)
def test_stub_mode_returns_503_without_cookies(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """`auth_mode=stub` 时五个端点全部 503，且**不下发任何 Cookie**。"""
    app = create_app(
        Settings(
            env="test",
            deploy_mode="private",
            auth_mode="stub",
            jwt_secret="unit-test-jwt-secret-32-bytes-long",
            ratelimit_enabled=False,
            background_jobs_enabled=False,
            public_base_url="https://moy.example.com",
        )
    )
    with TestClient(app, base_url="https://testserver") as client:
        response = client.request(method, path, json=body, headers=SAME_ORIGIN)

    assert response.status_code == 503
    assert response.json()["code"] == int(ErrorCode.DOWNSTREAM_UNAVAILABLE)
    assert _set_cookies(response) == [], "不可用时绝不能下发 Cookie（否则等于假装登录成功）"


@pytest.mark.parametrize(("method", "path", "body"), SSO_CALLS)
def test_cookie_track_disabled_returns_503(
    method: str, path: str, body: dict[str, Any] | None, sso_settings: Settings
) -> None:
    """`auth_cookie_enabled=false` 时同样 503（显式关闭，不静默降级）。"""
    disabled = sso_settings.model_copy(update={"auth_cookie_enabled": False})
    app = create_app(disabled)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.request(method, path, json=body, headers=SAME_ORIGIN)

    assert response.status_code == 503
    assert response.json()["code"] == int(ErrorCode.DOWNSTREAM_UNAVAILABLE)
    assert _set_cookies(response) == []


# ===========================================================================
# 2. `/sso/start`
# ===========================================================================
def test_start_returns_authorize_url_and_pending_cookie(sso_client: TestClient) -> None:
    """成功分支：返回授权地址 + 下发 `oa_oidc_pending`（HttpOnly/Path=/sso/10min）。"""
    response = sso_client.post("/sso/start", json={}, headers=SAME_ORIGIN)

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    _assert_no_token_leak(response.text)

    authorize_url = body["data"]["authorize_url"]
    query = parse_qs(urlsplit(authorize_url).query)
    assert query["client_id"] == ["officeai-adapter"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://moy.example.com/auth/callback"]
    assert "code_verifier" not in query, "verifier 不能出现在浏览器可见的地址里"

    pending = _cookie_named(_set_cookies(response), PENDING_COOKIE_NAME)
    assert "HttpOnly" in pending, "PKCE 暂存必须禁止 JS 读取"
    assert "Path=/sso" in pending
    assert "Max-Age=600" in pending
    assert "SameSite=Lax" in pending
    assert "Secure" in pending, "https 现场必须带 Secure"


def test_start_rejects_cross_site(sso_client: TestClient) -> None:
    """跨站发起的登录往返被拒（防登录 CSRF），且不下发暂存。"""
    response = sso_client.post("/sso/start", json={}, headers=CROSS_SITE)

    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)
    assert _set_cookies(response) == []


def test_start_rejects_missing_fetch_metadata(sso_client: TestClient) -> None:
    """`Sec-Fetch-Site` 与 `Origin` 都缺的写请求一律拒绝（fail-closed）。"""
    response = sso_client.post("/sso/start", json={})

    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


def test_start_accepts_origin_fallback(sso_client: TestClient) -> None:
    """老浏览器只发 `Origin` 时，按 Origin 与 Host 比对放行。"""
    response = sso_client.post("/sso/start", json={}, headers={"Origin": "https://testserver"})

    assert response.status_code == 200


# ===========================================================================
# 3. `/sso/complete`
# ===========================================================================
def test_complete_issues_session_cookies_without_leaking_tokens(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """成功分支：下发 `oa_session`(Path=/) + `oa_refresh`(Path=/sso)，清暂存。"""
    calls = _patch_exchange(monkeypatch, sso_settings)
    start = sso_client.post("/sso/start", json={}, headers=SAME_ORIGIN)
    state = _state_of(start.json()["data"]["authorize_url"])

    response = sso_client.post(
        "/sso/complete", json={"code": "the-code", "state": state}, headers=SAME_ORIGIN
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"] == {"ok": True, "expires_in": 300}
    _assert_no_token_leak(response.text)

    headers = _set_cookies(response)
    session = _cookie_named(headers, SESSION_COOKIE_NAME)
    assert "Path=/" in session
    assert "Max-Age=28800" in session, "会话 Cookie 上限 8 小时"
    assert "HttpOnly" in session and "SameSite=Lax" in session and "Secure" in session

    refresh = _cookie_named(headers, REFRESH_COOKIE_NAME)
    assert "Path=/sso" in refresh, "刷新令牌只应暴露给 /sso 前缀"
    assert "HttpOnly" in refresh

    pending = _cookie_named(headers, PENDING_COOKIE_NAME)
    assert "Max-Age=0" in pending, "登录暂存必须用完即删（防重放）"

    assert calls == [("the-code", calls[0][1], "https://moy.example.com/auth/callback")]
    assert calls[0][1], "verifier 必须从暂存 Cookie 里取回来传给 token 端点"


def test_complete_rejects_state_mismatch(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """state 不匹配 → 20002，且**不下发会话 Cookie**。"""
    calls = _patch_exchange(monkeypatch, sso_settings)
    sso_client.post("/sso/start", json={}, headers=SAME_ORIGIN)

    response = sso_client.post(
        "/sso/complete", json={"code": "c", "state": "wrong-state"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
    assert response.json()["message"] == sso_routes.PENDING_INVALID_MESSAGE
    assert calls == [], "state 不匹配时绝不能去兑换令牌"
    assert _set_cookies(response) == []


def test_complete_rejects_missing_pending(sso_client: TestClient) -> None:
    """没有暂存 Cookie（直接调 complete）→ 20002。"""
    response = sso_client.post(
        "/sso/complete", json={"code": "c", "state": "s"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
    assert response.json()["message"] == sso_routes.PENDING_INVALID_MESSAGE


def test_complete_rejects_expired_pending(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """暂存超过 10 分钟 → 20002（即使 state 完全一致）。"""
    calls = _patch_exchange(monkeypatch, sso_settings)
    stale = encode_pending(
        {
            "state": "stale-state",
            "verifier": "stale-verifier",
            "redirect_uri": "https://moy.example.com/auth/callback",
            "issued_at": int(time.time()) - 3600,
        }
    )
    headers = {
        "Cookie": build_set_cookie(
            PENDING_COOKIE_NAME, stale, max_age=600, path=SSO_COOKIE_PATH, secure=True
        ).split(";", 1)[0],
        **SAME_ORIGIN,
    }

    response = sso_client.post(
        "/sso/complete", json={"code": "c", "state": "stale-state"}, headers=headers
    )

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
    assert calls == []
    assert _set_cookies(response) == []


def test_complete_propagates_exchange_failure_without_cookies(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """兑换失败 → 20002，且绝不"先下发空 Cookie 再说"。"""
    _patch_exchange(
        monkeypatch,
        sso_settings,
        error=ApiError(ErrorCode.UNAUTHORIZED, internal_detail="invalid_grant"),
    )
    start = sso_client.post("/sso/start", json={}, headers=SAME_ORIGIN)
    state = _state_of(start.json()["data"]["authorize_url"])

    response = sso_client.post(
        "/sso/complete", json={"code": "c", "state": state}, headers=SAME_ORIGIN
    )

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
    assert _set_cookies(response) == []


def test_complete_rejects_cross_site(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """跨站触发的 complete 被拒（防登录 CSRF）。"""
    calls = _patch_exchange(monkeypatch, sso_settings)
    start = sso_client.post("/sso/start", json={}, headers=SAME_ORIGIN)
    state = _state_of(start.json()["data"]["authorize_url"])

    response = sso_client.post(
        "/sso/complete", json={"code": "c", "state": state}, headers=CROSS_SITE
    )

    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)
    assert calls == []


# ===========================================================================
# 4. `/sso/session`
# ===========================================================================
def test_session_reports_anonymous_without_cookie(sso_client: TestClient) -> None:
    """未登录是正常输入：`authenticated=false`，不是 401。"""
    response = sso_client.get("/sso/session")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": False,
        "expired": False,
    }


def test_session_reports_authenticated_after_login(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """登录后 `authenticated=true` + `refreshable=true`，且**不回显任何令牌**。"""
    _login(sso_client, monkeypatch, sso_settings)

    response = sso_client.get("/sso/session")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["authenticated"] is True
    assert isinstance(data["expires_in"], int)
    assert 0 < data["expires_in"] <= 3600
    assert data["refreshable"] is True, "登录成功后浏览器持有刷新令牌"
    assert data["expired"] is False, "有效会话不该被标成已过期"
    _assert_no_token_leak(response.text)


def test_session_clears_expired_token(sso_client: TestClient, sso_settings: Settings) -> None:
    """Cookie 里的令牌已过期 → 按未登录处理并清掉**这条**废 Cookie。"""
    expired = _cookie_header(make_expired_token(settings=sso_settings))

    response = sso_client.get("/sso/session", headers={"Cookie": expired})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["authenticated"] is False
    assert data["expired"] is True, "带了会话 Cookie 但不可用 = expired，不是从未登录"
    cleared = _cookie_named(_set_cookies(response), SESSION_COOKIE_NAME)
    assert "Max-Age=0" in cleared


def test_session_probe_keeps_refresh_token(sso_settings: Settings, sso_client: TestClient) -> None:
    """**QA-01 回归**：探测会话只清 `oa_session`，绝不删 `oa_refresh`。

    access TTL 是 300 秒、会话上限 8 小时，"access 过期但 refresh 有效"是会话期内的
    正常状态。前端每次启动都会问一次"我还登录着吗"；若这一问删掉刷新令牌，
    8 小时会话就退化成 300 秒（用户每 5 分钟被踢一次）。
    """
    cookie = "; ".join(
        [
            _cookie_header(make_expired_token(settings=sso_settings)),
            _cookie_header_for(REFRESH_COOKIE_NAME, "rt-still-good"),
        ]
    )

    response = sso_client.get("/sso/session", headers={"Cookie": cookie})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": True,
        "expired": True,
    }
    headers = _set_cookies(response)
    assert SESSION_COOKIE_NAME in {value.split("=", 1)[0] for value in headers}
    assert REFRESH_COOKIE_NAME not in {value.split("=", 1)[0] for value in headers}, (
        "探测会话不得下发 oa_refresh 的清除头（QA-01）"
    )


def test_session_probe_then_refresh_succeeds(
    sso_settings: Settings,
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**QA-01 的用户可见后果**：探一次会话之后，刷新仍然可用。

    这条用例走的是现场最常见的路径：用户离开几分钟回来刷新页面 → 前端问一次
    `/sso/session` → 业务请求 401 → 调 `/sso/refresh`。修复前第二步就把刷新令牌删了，
    第三步必然 20002。
    """
    cookie = "; ".join(
        [
            _cookie_header(make_expired_token(settings=sso_settings)),
            _cookie_header_for(REFRESH_COOKIE_NAME, "rt-still-good"),
        ]
    )
    sso_client.get("/sso/session", headers={"Cookie": cookie})
    calls = _patch_refresh(monkeypatch, sso_settings)

    refreshed = sso_client.post("/sso/refresh", headers={"Cookie": cookie, **SAME_ORIGIN})

    assert refreshed.status_code == 200, refreshed.text
    assert calls == ["rt-still-good"], "刷新令牌必须还在，并且真的被送去续期"


def test_session_with_undecodable_session_cookie_clears_it(sso_client: TestClient) -> None:
    """**QA-05 回归**：不可解码的 `oa_session` 也要清掉（僵尸 Cookie）。

    此前 `read_cookie` 对该输入返回 None，与"根本没有这条 Cookie"无法区分，
    于是这条 Cookie 会永远挂在每个请求上；而 base64 可解的垃圾值却会被清掉——
    同类输入两种处置。
    """
    response = sso_client.get(
        "/sso/session",
        headers={"Cookie": f"{SESSION_COOKIE_NAME}=%%%garbage", **SAME_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json()["data"]["expired"] is True
    assert SESSION_COOKIE_NAME in {value.split("=", 1)[0] for value in _set_cookies(response)}, (
        "僵尸 Cookie 必须被清掉"
    )


# ===========================================================================
# 5. `/sso/refresh`
# ===========================================================================
def test_refresh_rotates_cookies(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """刷新成功：覆写两个 Cookie，响应体不含令牌。"""
    _login(sso_client, monkeypatch, sso_settings)
    calls = _patch_refresh(monkeypatch, sso_settings)

    response = sso_client.post("/sso/refresh", headers=SAME_ORIGIN)

    assert response.status_code == 200
    assert response.json()["data"] == {"ok": True, "expires_in": 300}
    _assert_no_token_leak(response.text)
    assert calls == ["rt-value"], "必须把 Cookie 里的刷新令牌原样交给身份平台"

    headers = _set_cookies(response)
    assert "Path=/" in _cookie_named(headers, SESSION_COOKIE_NAME)
    assert "Path=/sso" in _cookie_named(headers, REFRESH_COOKIE_NAME)


def test_refresh_without_cookie_is_unauthorized(sso_client: TestClient) -> None:
    """没有刷新令牌 → 20002（前端据此跳登录页）。"""
    response = sso_client.post("/sso/refresh", headers=SAME_ORIGIN)

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


def test_refresh_invalid_grant_clears_only_refresh_cookie(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """刷新令牌被身份平台判失效（20002）→ 只清 `oa_refresh`，不动 `oa_session`（QA-02）。"""
    _login(sso_client, monkeypatch, sso_settings)
    _patch_refresh(
        monkeypatch,
        sso_settings,
        error=ApiError(ErrorCode.UNAUTHORIZED, internal_detail="invalid_grant"),
    )

    response = sso_client.post("/sso/refresh", headers=SAME_ORIGIN)

    assert response.status_code == 401
    cleared = {value.split("=", 1)[0] for value in _set_cookies(response)}
    assert cleared == {REFRESH_COOKIE_NAME}, (
        "已死的刷新令牌必须清掉（否则前端每次重试都白打一次身份平台）；"
        f"而 oa_session 可能仍然有效，不得连带删除；实际 Set-Cookie={_set_cookies(response)}"
    )


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.DOWNSTREAM_UNAVAILABLE, 503),
        (ErrorCode.DOWNSTREAM_TIMEOUT, 504),
    ],
)
def test_refresh_transient_failure_keeps_refresh_cookie(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
    code: ErrorCode,
    status: int,
) -> None:
    """身份平台不可用/超时（30001/30002）→ **绝不清** `oa_refresh`（QA-02）。

    临时故障被当成"令牌已死"清掉，等于把一次网络抖动升级成强制重新登录。
    """
    _login(sso_client, monkeypatch, sso_settings)
    _patch_refresh(monkeypatch, sso_settings, error=ApiError(code))

    response = sso_client.post("/sso/refresh", headers=SAME_ORIGIN)

    assert response.status_code == status
    assert _set_cookies(response) == [], "临期故障不得清任何 Cookie（否则误伤仍可用的会话）"


def test_refresh_rejects_cross_site(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """跨站触发的刷新被拒（否则任意站点都能让用户的会话不断续期）。"""
    _login(sso_client, monkeypatch, sso_settings)
    calls = _patch_refresh(monkeypatch, sso_settings)

    response = sso_client.post("/sso/refresh", headers=CROSS_SITE)

    assert response.status_code == 403
    assert calls == []


# ===========================================================================
# 6. `/sso/logout`
# ===========================================================================
def test_logout_clears_cookies_and_revokes_refresh_token(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """登出：删两个 Cookie + 把刷新令牌交给身份平台撤销。"""
    _login(sso_client, monkeypatch, sso_settings)
    revoked = _patch_logout(monkeypatch)

    response = sso_client.post("/sso/logout", headers=SAME_ORIGIN)

    assert response.status_code == 200
    assert response.json()["data"] == {"ok": True}
    assert revoked == ["rt-value"], "登出必须尽力撤销刷新令牌"

    headers = _set_cookies(response)
    assert "Max-Age=0" in _cookie_named(headers, SESSION_COOKIE_NAME)
    assert "Max-Age=0" in _cookie_named(headers, REFRESH_COOKIE_NAME)
    # 删除时的 Path 必须与下发时一致，否则浏览器删的是另一条 Cookie
    assert "Path=/sso" in _cookie_named(headers, REFRESH_COOKIE_NAME)


def test_logout_without_session_still_succeeds(
    sso_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有会话时登出也必须成功（幂等），否则前端会在过期后卡住。"""
    revoked = _patch_logout(monkeypatch)

    response = sso_client.post("/sso/logout", headers=SAME_ORIGIN)

    assert response.status_code == 200
    assert revoked == [""], "没有刷新令牌时调用方传入空串（下游直接跳过）"


def test_logout_rejects_cross_site(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """跨站触发的登出被拒（防止任意站点把用户踢下线）。"""
    _login(sso_client, monkeypatch, sso_settings)
    revoked = _patch_logout(monkeypatch)

    response = sso_client.post("/sso/logout", headers=CROSS_SITE)

    assert response.status_code == 403
    assert revoked == []


# ===========================================================================
# 7. Cookie 凭证轨（`extract_credential`）与 Bearer 轨回归
# ===========================================================================
def _request(
    headers: dict[str, str],
    *,
    method: str = "POST",
    path: str = "/api/v1/model/generate",
) -> Request:
    """构造最小 Request（不带 `app`，走全局配置兜底）。

    Args:
        headers: 请求头。
        method: HTTP 方法。
        path: 请求路径。

    Returns:
        Request: 请求对象。
    """
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request(
        {
            "type": "http",
            "headers": raw_headers,
            "method": method,
            "path": path,
            "query_string": b"",
        }
    )


def _cookie_header_for(name: str, value: str, *, path: str = "/") -> str:
    """把任意 Cookie 包成请求头里的 `name=value` 片段（编码方式与下发完全一致）。

    Args:
        name: Cookie 名。
        value: 原始值。
        path: 作用域（构造请求头时只影响可读性，不影响匹配）。

    Returns:
        str: `name=value` 片段。
    """
    return build_set_cookie(name, value, max_age=28800, path=path, secure=True).split(";", 1)[0]


def _cookie_header(token: str) -> str:
    """把令牌包成 `oa_session=...` 的 Cookie 片段。

    Args:
        token: 令牌原文。

    Returns:
        str: `oa_session=...` 形式的 Cookie 片段。
    """
    return _cookie_header_for(SESSION_COOKIE_NAME, token)


def test_cookie_credential_passes_for_same_origin(sso_settings: Settings) -> None:
    """同源请求可以用 Cookie 作为凭证。"""
    token = make_token(settings=sso_settings)
    request = _request({"Cookie": _cookie_header(token), "Sec-Fetch-Site": "same-origin"})

    assert extract_credential(request, sso_settings) == token


def test_cookie_credential_rejects_cross_site(sso_settings: Settings) -> None:
    """跨站写请求即使带着合法 Cookie 也拒绝（20003，不是 20002）。"""
    token = make_token(settings=sso_settings)
    request = _request({"Cookie": _cookie_header(token), "Sec-Fetch-Site": "cross-site"})

    with pytest.raises(ApiError) as exc_info:
        extract_credential(request, sso_settings)
    assert exc_info.value.code == ErrorCode.FORBIDDEN


def test_cookie_credential_allows_safe_methods_cross_site(sso_settings: Settings) -> None:
    """读方法不做 CSRF 判定（跨站读的危害由 SameSite 与 CORS 兜）。"""
    token = make_token(settings=sso_settings)
    request = _request(
        {"Cookie": _cookie_header(token), "Sec-Fetch-Site": "cross-site"},
        method="GET",
        path="/api/v1/model/usage",
    )

    assert extract_credential(request, sso_settings) == token


def test_bearer_track_wins_and_skips_csrf(sso_settings: Settings) -> None:
    """有 `Authorization` 头时走 Bearer 轨，**不判 CSRF**（双轨回归的核心断言）。"""
    bearer = make_token(user_id="usr_from_bearer", settings=sso_settings)
    cookie = make_token(user_id="usr_from_cookie", settings=sso_settings)
    request = _request(
        {
            "Authorization": f"Bearer {bearer}",
            "Cookie": _cookie_header(cookie),
            "Sec-Fetch-Site": "cross-site",
        }
    )

    assert extract_credential(request, sso_settings) == bearer


def test_bearer_track_rejects_non_bearer_scheme(sso_settings: Settings) -> None:
    """Bearer 轨的既有行为逐字不变：非 Bearer 前缀即 20002。"""
    request = _request({"Authorization": "Basic abc", "Sec-Fetch-Site": "same-origin"})

    with pytest.raises(ApiError) as exc_info:
        extract_credential(request, sso_settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_cookie_disabled_ignores_cookie_credential(sso_settings: Settings) -> None:
    """`auth_cookie_enabled=false` 时 Cookie 一律不作数（回滚开关）。"""
    token = make_token(settings=sso_settings)
    disabled = sso_settings.model_copy(update={"auth_cookie_enabled": False})
    request = _request({"Cookie": _cookie_header(token), "Sec-Fetch-Site": "same-origin"})

    with pytest.raises(ApiError) as exc_info:
        extract_credential(request, disabled)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_missing_credentials_is_unauthorized(sso_settings: Settings) -> None:
    """既无头也无 Cookie → 20002（与旧行为同一码）。"""
    with pytest.raises(ApiError) as exc_info:
        extract_credential(_request({}), sso_settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


# ===========================================================================
# 8. 端到端：业务端点同时接受两条轨（灰度期能力，不可回归）
# ===========================================================================
def test_business_endpoint_accepts_bearer_track(
    sso_client: TestClient, sso_settings: Settings
) -> None:
    """只用 Bearer 头访问业务端点：行为与改造前一致（不被 CSRF 拦）。"""
    token = make_token(settings=sso_settings)
    response = sso_client.get("/api/v1/model/usage", headers={"Authorization": f"Bearer {token}"})

    assert response.json().get("code") != int(ErrorCode.UNAUTHORIZED)


def test_business_endpoint_accepts_cookie_track(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """登录后不带任何 `Authorization` 头也能访问业务端点（P0-5 的目标态）。"""
    _login(sso_client, monkeypatch, sso_settings)

    response = sso_client.get("/api/v1/model/usage")

    assert response.json().get("code") != int(ErrorCode.UNAUTHORIZED)


def test_business_endpoint_rejects_cross_site_cookie_write(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """带 Cookie 的跨站写请求在**鉴权依赖里**就被拒（20003），业务处理器不会执行。"""
    _login(sso_client, monkeypatch, sso_settings)

    response = sso_client.post("/api/v1/model/generate", json={}, headers=CROSS_SITE)

    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


# ===========================================================================
# 9. Cookie 体积（QA-04）：`oa_session` 不做 base64，边界从 3 KB 推到 4 KB 以上
# ===========================================================================
#: 浏览器单 Cookie 上限（Chrome/Firefox 均 4096 字节；超限**静默丢弃**）
BROWSER_COOKIE_LIMIT: int = 4096


def _cookie_value_of(header: str) -> str:
    """从一条 `Set-Cookie` 里取出值部分。

    Args:
        header: `Set-Cookie` 头值。

    Returns:
        str: `name=value` 里的 `value`。
    """
    return header.split("=", 1)[1].split(";", 1)[0]


def test_session_cookie_value_is_the_raw_token() -> None:
    """`oa_session` 的值就是令牌原文（不再套 base64）。

    套一层 base64 会放大 4/3：QA 实测 12 角色的真实令牌 2202 字符 → 编码后 3002 字节，
    边界（令牌 3023 字符）贴着 4096 上限；角色一多就越界，而**超限是静默丢弃**。
    """
    token = "a" * 4000 + ".signature-part"

    header = build_set_cookie(SESSION_COOKIE_NAME, token, max_age=28800, path="/", secure=True)

    assert _cookie_value_of(header) == token, "访问令牌必须原样写入（QA-04）"
    assert len(header) < BROWSER_COOKIE_LIMIT, (
        f"4000 字符的令牌不该逼近 4 KB 上限；实际 {len(header)} 字节"
    )


def test_pending_cookie_is_still_encoded() -> None:
    """`oa_oidc_pending` 是 JSON（含 `{}"` 与逗号），**必须**继续编码，且能原样读回。"""
    payload = encode_pending({"state": "s", "verifier": "v"})

    header = build_set_cookie(
        PENDING_COOKIE_NAME, payload, max_age=600, path=SSO_COOKIE_PATH, secure=True
    )

    value = _cookie_value_of(header)
    assert "{" not in value and '"' not in value
    assert read_cookie(f"{PENDING_COOKIE_NAME}={value}", PENDING_COOKIE_NAME) == payload


def test_session_cookie_rejects_value_unsafe_for_raw_cookie() -> None:
    """访问令牌若含 Cookie 不允许的字符 → 显式失败，而不是写出一条会被截断的 Cookie。

    访问令牌必须是 base64url 的 JWT（这也是无状态验签的前提）。不静默降级为
    "编码后写入"：写读两侧必须用同一套判据，否则会得到一个写得进、读不出的 Cookie，
    症状是"登录成功但下一页又未登录"。
    """
    with pytest.raises(ValueError, match="Cookie 不允许的字符"):
        build_set_cookie(
            SESSION_COOKIE_NAME, "not a jwt; with separators", max_age=1, path="/", secure=False
        )


def test_login_issues_compact_session_cookie(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sso_settings: Settings,
) -> None:
    """端到端：登录下发的 `oa_session` 与令牌逐字相等，且远小于 4 KB。"""
    token = make_token(settings=sso_settings)
    _patch_exchange_token(monkeypatch, token, refresh_value="rt-value")
    start = sso_client.post("/sso/start", json={}, headers=SAME_ORIGIN)
    state = _state_of(start.json()["data"]["authorize_url"])

    response = sso_client.post(
        "/sso/complete", json={"code": "c", "state": state}, headers=SAME_ORIGIN
    )

    assert response.status_code == 200, response.text
    session = _cookie_named(_set_cookies(response), SESSION_COOKIE_NAME)
    assert _cookie_value_of(session) == token
    assert len(session) < BROWSER_COOKIE_LIMIT


# ===========================================================================
# 10. 空 `Authorization` 头（QA-05）：纯空白 / 只有前缀都要回落 Cookie 轨
# ===========================================================================
@pytest.mark.parametrize(
    "header", ["", " ", "   ", "\t", "Bearer", "Bearer ", "Bearer   ", " bearer "]
)
def test_blank_authorization_header_falls_back_to_cookie(
    header: str, sso_settings: Settings
) -> None:
    """`Authorization` 不构成凭证时（空、纯空白、只有 `Bearer` 前缀）必须回落 Cookie。

    此前只判 `== ""`，`" "` 与 `"Bearer "` 会走 Bearer 轨拿到 20002 而**不回落**——
    与"空值等同于没有该头"的承诺有缝，现场会被误判成令牌损坏。
    """
    token = make_token(settings=sso_settings)
    request = _request(
        {
            "Authorization": header,
            "Cookie": _cookie_header(token),
            "Sec-Fetch-Site": "same-origin",
        }
    )

    assert extract_credential(request, sso_settings) == token


@pytest.mark.parametrize("header", ["Basic abc", "Token xyz", "bearerx"])
def test_non_bearer_authorization_still_rejected(header: str, sso_settings: Settings) -> None:
    """非 Bearer 形态（含只有 `Bearer` 没有分隔空格的写法）仍 20002，不被 Cookie 兜住。

    这类头说明调用方用错了鉴权方式，静默兜住会掩盖问题。
    """
    token = make_token(settings=sso_settings)
    request = _request(
        {"Authorization": header, "Cookie": _cookie_header(token), "Sec-Fetch-Site": "same-origin"}
    )

    with pytest.raises(ApiError) as exc_info:
        extract_credential(request, sso_settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED
