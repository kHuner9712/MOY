"""`/sso/refresh` 并发刷新竞态回归（审计 2026-11-02 M1）。

刷新令牌在身份平台侧是**一次性**的（轮换后旧值即废），多标签页/传输层重试
并发发出同一只令牌时，后到者会拿 `invalid_grant`（20002）。旧实现在此处清
`oa_refresh`，浏览器后写覆盖先写——赢家刚轮换出的新令牌被空 Cookie 抹掉，
会话损毁；后到者前端又判「已死」把用户踢下线。

本文件守住三个行为（三条都是回归红线，改 `/sso/refresh` 前先读）：

1. **单飞 + 幂等重放**：同一只刷新令牌只打一次身份平台，后到者拿到同一结果
   （并发只发一次下游、窗口内重复请求零下游调用）；
2. **空 Cookie 覆盖回归**：被新轮换取代的旧令牌迟到被判 20002 时，响应**不得**
   携带 `oa_refresh` 清除头；
3. **20002 误判回归**：上述场景必须携带 `data.retryable = true`（前端归
   「可重试」而非「会话已死」）；真死令牌仍照常清 Cookie（QA-02 语义不变）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import sso_routes
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.oidc_server import TokenSet
from app.core.session_cookie import REFRESH_COOKIE_NAME, SSO_COOKIE_PATH, build_set_cookie
from app.main import create_app
from tests.conftest import make_token

pytestmark = pytest.mark.unit

#: 同源请求的 Fetch Metadata（浏览器对同源 fetch/XHR 的默认值）
SAME_ORIGIN: dict[str, str] = {"Sec-Fetch-Site": "same-origin"}

#: 测试用刷新令牌（旧值；轮换后应被取代）
OLD_REFRESH: str = "rt-race-old"

#: 轮换出的新刷新令牌（赢家拿到的值）
ROTATED_REFRESH: str = "rt-race-new"


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


def _cookie_header_for(name: str, value: str) -> str:
    """构造请求用的 Cookie 头片段（**值必须与下发同款编码**）。

    会话 Cookie 的值经 `build_set_cookie` 做 base64url 编码，请求侧必须用同款
    编码构造，否则 `read_cookie` 解不出值、被当成"没有刷新令牌"。

    Args:
        name: Cookie 名。
        value: 明文值。

    Returns:
        str: `name=encoded` 片段。
    """
    header = build_set_cookie(name, value, max_age=28800, path=SSO_COOKIE_PATH, secure=True)
    return header.split(";", 1)[0]


def _post_refresh(client: TestClient, token: str = OLD_REFRESH) -> Any:
    """带着指定刷新令牌调一次 `/sso/refresh`。

    Args:
        client: 测试客户端。
        token: 放进 `oa_refresh` 的值。

    Returns:
        Any: httpx 响应。
    """
    return client.post(
        "/sso/refresh",
        headers={
            **SAME_ORIGIN,
            "cookie": _cookie_header_for(REFRESH_COOKIE_NAME, token),
        },
    )


def _set_cookie_headers(response: Any) -> list[str]:
    """取出响应里的全部 `Set-Cookie`。

    Args:
        response: httpx 响应。

    Returns:
        list[str]: 头值列表。
    """
    return [value for key, value in response.headers.multi_items() if key.lower() == "set-cookie"]


def _expire_replay_window(token: str) -> None:
    """把指定令牌的重放窗口手动过期（模拟「迟到很久的请求」）。

    直接改内部时间戳而不是真等待 120 秒：窗口的正确性由 TTL 常量保证，
    用例只需要跨过它。

    Args:
        token: 刷新令牌原文。
    """
    flight = sso_routes._refresh_flights[sso_routes._token_digest(token)]  # noqa: SLF001
    assert flight.finished_at is not None
    flight.finished_at -= sso_routes.REFRESH_REPLAY_TTL_SECONDS + 1


class _RaceFakeRefresh:
    """可控的 `refresh_tokens` 替身：计数、可睡眠、可切换为失败。

    Attributes:
        calls: 收到的刷新令牌列表（下游调用次数 = len(calls)）。
    """

    def __init__(self, settings: Settings, *, delay: float = 0.0) -> None:
        """初始化替身。

        Args:
            settings: 配置（签发可用令牌）。
            delay: 每次调用的模拟耗时（秒），用于制造并发重叠。
        """
        self.calls: list[str] = []
        self._settings = settings
        self._delay = delay
        self.fail_with: ApiError | None = None

    async def __call__(self, conf: Settings, refresh_token: str) -> TokenSet:
        """模拟身份平台的换发。

        Args:
            conf: 配置。
            refresh_token: 提交的刷新令牌。

        Returns:
            TokenSet: 轮换后的令牌集合。

        Raises:
            ApiError: `fail_with` 非空时抛出（模拟 `invalid_grant`）。
        """
        self.calls.append(refresh_token)
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self.fail_with is not None:
            raise self.fail_with
        return TokenSet(
            access_token=make_token(settings=self._settings),
            refresh_token=ROTATED_REFRESH,
            expires_in=300,
            refresh_expires_in=1800,
        )


def test_concurrent_refresh_single_flight_one_downstream_call(
    sso_client: TestClient,
    sso_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多标签页并发刷新：只打一次身份平台，后到者拿到同一结果。"""
    fake = _RaceFakeRefresh(sso_settings, delay=0.4)
    monkeypatch.setattr(sso_routes, "refresh_tokens", fake)

    barrier = Barrier(2)

    def call() -> Any:
        barrier.wait(timeout=5)
        return _post_refresh(sso_client)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(timeout=10) for future in [pool.submit(call), pool.submit(call)]]

    # 单飞：两次请求共享同一次换发，下游只被调用一次
    assert fake.calls == [OLD_REFRESH], f"下游被调用 {len(fake.calls)} 次，期望 1 次"

    # 后到者拿到同一结果：两次都 200，且 Cookie 值逐字相同（重放同一 TokenSet）
    cookies = []
    for response in responses:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == 0
        assert "access_token" not in response.text.lower()
        headers = _set_cookie_headers(response)
        rotated = [h for h in headers if h.startswith(f"{REFRESH_COOKIE_NAME}=")]
        assert rotated, f"没有下发轮换后的刷新 Cookie：{headers}"
        cookies.append(rotated[0])
    assert cookies[0] == cookies[1], "并发两次响应的刷新 Cookie 不一致，不是同一结果"


def test_refresh_replay_within_window_skips_downstream(
    sso_client: TestClient,
    sso_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """幂等窗口内的重复请求：零下游调用，直接重放同一结果。"""
    fake = _RaceFakeRefresh(sso_settings)
    monkeypatch.setattr(sso_routes, "refresh_tokens", fake)

    first = _post_refresh(sso_client)
    second = _post_refresh(sso_client)

    assert first.status_code == 200
    assert second.status_code == 200
    assert fake.calls == [OLD_REFRESH], "窗口内第二次请求不应再打身份平台"
    assert second.json()["data"]["expires_in"] == first.json()["data"]["expires_in"]


def test_superseded_token_late_arrival_keeps_cookie_and_flags_retryable(
    sso_client: TestClient,
    sso_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 Cookie 覆盖回归 + 20002 误判回归：被取代的旧令牌迟到，不清、可重试。"""
    fake = _RaceFakeRefresh(sso_settings)
    monkeypatch.setattr(sso_routes, "refresh_tokens", fake)

    winner = _post_refresh(sso_client)
    assert winner.status_code == 200

    # 迟到者：窗口已过（设备休眠后恢复之类），此时身份平台会判旧令牌 invalid_grant
    _expire_replay_window(OLD_REFRESH)
    fake.fail_with = ApiError(ErrorCode.UNAUTHORIZED, message="刷新令牌已失效")

    late = _post_refresh(sso_client)
    assert late.status_code == 401
    body = late.json()
    assert body["code"] == int(ErrorCode.UNAUTHORIZED)
    assert body["data"] == {"retryable": True, "reason": "superseded"}, (
        "被取代的旧令牌必须携带明确可重试信号，前端不得判「会话已死」"
    )

    # 空 Cookie 覆盖回归：**不得**携带 oa_refresh 清除头（浏览器里躺着的是赢家的新令牌）
    for header in _set_cookie_headers(late):
        assert not header.startswith(f"{REFRESH_COOKIE_NAME}="), (
            f"迟到的 20002 不得动刷新 Cookie，否则抹掉赢家的新令牌：{header}"
        )


def test_dead_token_still_clears_refresh_cookie(
    sso_client: TestClient,
    sso_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真死令牌（从未成功轮换过）语义不变：20002 且清 `oa_refresh`（QA-02）。"""
    fake = _RaceFakeRefresh(sso_settings)
    fake.fail_with = ApiError(ErrorCode.UNAUTHORIZED, message="刷新令牌已失效")
    monkeypatch.setattr(sso_routes, "refresh_tokens", fake)

    response = _post_refresh(sso_client)
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == int(ErrorCode.UNAUTHORIZED)
    assert not (body.get("data") or {}).get("retryable"), "真死令牌不得携带可重试信号"

    clears = [
        header
        for header in _set_cookie_headers(response)
        if header.startswith(f"{REFRESH_COOKIE_NAME}=") and "max-age=0" in header.lower()
    ]
    assert clears, f"真死令牌应清 oa_refresh：{_set_cookie_headers(response)}"


def test_transient_failure_never_clears_cookie(
    sso_client: TestClient,
    sso_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """网络类失败（30001）不清 Cookie（QA-02 语义在重构后保持）。"""
    fake = _RaceFakeRefresh(sso_settings)
    fake.fail_with = ApiError(ErrorCode.DOWNSTREAM_UNAVAILABLE, message="身份平台不可达")
    monkeypatch.setattr(sso_routes, "refresh_tokens", fake)

    response = _post_refresh(sso_client)
    assert response.status_code >= 400
    for header in _set_cookie_headers(response):
        assert not header.startswith(f"{REFRESH_COOKIE_NAME}="), (
            f"临时故障不得动刷新 Cookie：{header}"
        )
