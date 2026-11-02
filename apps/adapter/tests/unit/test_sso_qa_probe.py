"""P0-5 服务端实现的**独立验证**（QA 探针）：7 条盲区的端到端用例。

与 `test_sso_routes.py` 的分工（**刻意不重复**）
----------------------------------------------

工程师的 76 条用例已经把「五端点的成功/失败分支、state 校验、成功后响应体不含令牌、
CSRF 三态、Bearer 轨回归」钉住了。本文件只覆盖**那些没被覆盖、但会决定用户是否被
每 5 分钟踢一次**的路径：

1. token 端点返回 **200 但响应不可解析**（畸形 JSON / HTML / 缺 `access_token` /
   非 200 带 `error_description`）时，`/sso/complete` 的**错误响应体**会不会把上游
   片段带出来；
2. `/sso/refresh` 在 `invalid_grant` 时对两个 Cookie 的处置；
3. **Cookie 体积**：真实长度 JWT 经 base64 膨胀 33% 后的 `Set-Cookie` 长度，
   以及 4KB 边界对应的令牌长度（超限会被浏览器静默丢弃）；
4. access TTL（300 s）与 Cookie Max-Age（8 h）不一致时，**过期令牌打业务接口**与
   `GET /sso/session` 的实际表现——这条决定"会不会每 5 分钟被踢一次"；
5. `Authorization:` **空值**是否真的等同于"没有该头"（回落 Cookie 轨）；
6. `/sso/complete` 的**并发重放**：成功后 pending 是否已被本地清掉（不再打 IdP）、
   失败后 pending 是否还在（用户重试能否成功）；
7. `GET /sso/session` 的四态语义边界（未登录 / 有效 / 过期 / 垃圾值）。

为什么自带一个"迷你浏览器 Cookie 罐"
------------------------------------

本机 `starlette 1.10 + httpx 0.28` 组合下 `TestClient.cookies` **不会**把 Cookie
随请求发出（实测 `Cookie` 请求头为 `None`）。若用它写"浏览器会删掉这个 Cookie"这类
断言，会得到**假绿**——测试通过，而现场行为没有被验证。故本文件用一个显式的
`_BrowserJar`：按 `Path` 过滤、尊重 `Max-Age=0` 删除，并且**能证明 `oa_refresh` 不会
被业务路径的请求带上**（`Path=/sso` 的真实效果）。

QA-01 / QA-03 已修复（2026-11-02 第二轮）
-----------------------------------------

本文件最初用 `xfail(strict=True)` 标了两条**期望行为**：
`test_session_probe_leaves_refresh_token_intact`（QA-01）与
`test_session_payload_distinguishes_expired_from_never_logged_in`（QA-03）。
`strict=True` 的作用就在这里：工程师修好后它们变成 XPASS → 严格模式下变红 →
强制摘掉标记，不会被遗忘。**两个标记现已摘除，两条都是普通回归用例。**

同批修复的还有 QA-02（刷新失败清死令牌）、QA-04（`oa_session` 不再 base64 膨胀）、
QA-05（僵尸 Cookie 与空 `Authorization`）。凡是原先钉住"修复前行为"的断言都已按新
契约改写，改写处均留 `已修（QA-xx）` 标记，便于与主报告逐条对照。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request, Response
from fastapi.testclient import TestClient

from app.api import sso_routes
from app.core import oidc_server
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.oidc_server import LOGIN_FAILED_MESSAGE
from app.core.session_cookie import (
    PENDING_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    build_set_cookie,
)
from app.main import create_app
from tests.conftest import make_expired_token, make_token

pytestmark = pytest.mark.unit

#: 同源请求的 Fetch Metadata（Cookie 轨的 CSRF 判定依赖它）
SAME_ORIGIN: dict[str, str] = {"Sec-Fetch-Site": "same-origin"}

#: 响应体里绝不能出现的字段名（P0-5 不变式）
FORBIDDEN_TOKEN_KEYS: tuple[str, ...] = ("access_token", "refresh_token", "id_token")

#: 业务接口（走 `get_request_context`，用来验"过期 Cookie 打业务接口"的行为）
BUSINESS_PATH: str = "/api/v1/identity/profile"

#: 浏览器单 Cookie 上限（Chrome/Firefox 均为 4096 字节；超限静默丢弃）
BROWSER_COOKIE_LIMIT: int = 4096


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
def sso_client(sso_settings: Settings) -> TestClient:
    """https 基址的测试客户端（`Secure` 语义与现场一致）。

    Args:
        sso_settings: 配置。

    Returns:
        TestClient: 测试客户端（调用方负责 `with`）。
    """
    return TestClient(create_app(sso_settings), base_url="https://testserver")


# ===========================================================================
# 迷你浏览器 Cookie 罐
# ===========================================================================
class _BrowserJar:
    """最小化的浏览器 Cookie 罐（按 `Path` 过滤 + 尊重 `Max-Age=0`）。

    只实现本文件用到的语义，但都是 RFC 6265 里**会改变结论**的那几条：
    `Path` 前缀匹配（决定刷新令牌会不会被业务请求带上）与 `Max-Age=0` 立即失效
    （决定"服务端清了 Cookie"到底有没有在浏览器侧生效）。
    """

    def __init__(self) -> None:
        """初始化空罐。"""
        self._entries: dict[str, tuple[str, str]] = {}

    def put(self, name: str, value: str, path: str = "/") -> None:
        """直接写入一条 Cookie（造数用，相当于用户上次登录留下）。

        Args:
            name: Cookie 名。
            value: **已编码**的值（`build_set_cookie` 产出的形态）。
            path: 作用域。
        """
        self._entries[name] = (value, path)

    def absorb(self, response: httpx.Response) -> None:
        """吸收响应里的 `Set-Cookie`（`Max-Age=0` 视为删除）。

        Args:
            response: httpx 响应。
        """
        for header in _set_cookie_values(response):
            pair, *attrs = [part.strip() for part in header.split(";")]
            name, _, value = pair.partition("=")
            if not name:
                continue
            path = "/"
            max_age: int | None = None
            for attr in attrs:
                key, _, raw = attr.partition("=")
                if key.strip().lower() == "path":
                    path = raw.strip() or "/"
                elif key.strip().lower() == "max-age":
                    try:
                        max_age = int(raw.strip())
                    except ValueError:
                        max_age = None
            if max_age is not None and max_age <= 0:
                self._entries.pop(name, None)
            else:
                self._entries[name] = (value, path)

    def header_for(self, path: str) -> str:
        """构造发往 `path` 的 `Cookie` 头。

        Args:
            path: 请求路径。

        Returns:
            str: `Cookie` 头值（无匹配时为空串）。
        """
        pairs = [
            f"{name}={value}"
            for name, (value, cookie_path) in self._entries.items()
            if _path_matches(cookie_path, path)
        ]
        return "; ".join(pairs)

    def names(self) -> set[str]:
        """当前持有的 Cookie 名集合。

        Returns:
            set[str]: 名称集合。
        """
        return set(self._entries)


def _path_matches(cookie_path: str, request_path: str) -> bool:
    """判断 Cookie 的 `Path` 是否覆盖请求路径（RFC 6265 §5.1.4 的简化版）。

    Args:
        cookie_path: Cookie 的 `Path` 属性。
        request_path: 请求路径。

    Returns:
        bool: 覆盖返回 True。
    """
    if cookie_path == "/":
        return True
    if request_path == cookie_path:
        return True
    prefix = cookie_path if cookie_path.endswith("/") else f"{cookie_path}/"
    return request_path.startswith(prefix)


# ===========================================================================
# 通用辅助
# ===========================================================================
def _set_cookie_values(response: httpx.Response) -> list[str]:
    """取出响应里的全部 `Set-Cookie`。

    Args:
        response: 响应对象。

    Returns:
        list[str]: 头值列表（保持顺序）。
    """
    return [value for key, value in response.headers.multi_items() if key.lower() == "set-cookie"]


def _cookie_pair(set_cookie: str) -> str:
    """把一条 `Set-Cookie` 截成可放进 `Cookie` 请求头的 `name=value`。

    Args:
        set_cookie: `Set-Cookie` 头值。

    Returns:
        str: `name=value` 片段。
    """
    return set_cookie.split(";", 1)[0]


def _encoded_pair(name: str, value: str) -> str:
    """把原始值按 Cookie 值编码规则包成 `name=value`（造数用）。

    Args:
        name: Cookie 名。
        value: 原始值。

    Returns:
        str: `name=value` 片段。
    """
    return _cookie_pair(build_set_cookie(name, value, max_age=28800, path="/", secure=True))


def _encoded_value(name: str, value: str) -> str:
    """只取编码后的 Cookie 值（喂给 `_BrowserJar.put` 用）。

    Args:
        name: Cookie 名。
        value: 原始值。

    Returns:
        str: 编码后的值。
    """
    return _encoded_pair(name, value).split("=", 1)[1]


def _session_set_cookie(response: httpx.Response) -> str:
    """取出响应里 `oa_session` 的那条 `Set-Cookie`。

    Args:
        response: 响应对象。

    Returns:
        str: 头值。

    Raises:
        AssertionError: 没有下发该 Cookie（用例应失败，不静默跳过）。
    """
    prefix = f"{SESSION_COOKIE_NAME}="
    for value in _set_cookie_values(response):
        if value.startswith(prefix):
            return value
    raise AssertionError(
        f"响应里没有 {SESSION_COOKIE_NAME} 的 Set-Cookie：{_set_cookie_values(response)}"
    )


def _assert_no_token_leak(text: str) -> None:
    """断言响应体不含令牌字段名。

    Args:
        text: 响应体原文。
    """
    lowered = text.lower()
    for key in FORBIDDEN_TOKEN_KEYS:
        assert key not in lowered, f"响应体泄露了字段名 {key}：{text}"


def _state_of(authorize_url: str) -> str:
    """从授权地址里取出 `state`。

    Args:
        authorize_url: `/sso/start` 返回的地址。

    Returns:
        str: state 值。
    """
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


def _start_login(client: TestClient) -> tuple[str, str]:
    """发起登录，返回 `(state, pending cookie 的 name=value)`。

    不用客户端的 Cookie 罐（见模块 docstring）：显式把暂存 Cookie 拿在手里，
    才能断言"哪一次响应清掉了它"。

    Args:
        client: 测试客户端。

    Returns:
        tuple[str, str]: state 与 `oa_oidc_pending=...`。
    """
    start = client.post("/sso/start", json={}, headers=SAME_ORIGIN)
    assert start.status_code == 200, start.text
    pending = [h for h in _set_cookie_values(start) if h.startswith(f"{PENDING_COOKIE_NAME}=")]
    assert pending, "start 必须下发登录暂存 Cookie"
    return _state_of(start.json()["data"]["authorize_url"]), _cookie_pair(pending[0])


#: Mock 的 IdP 回包 `(状态码, 响应体, content-type)`
IdpReply = tuple[int, bytes, str]


def _patch_idp(monkeypatch: pytest.MonkeyPatch, replies: list[IdpReply]) -> list[str]:
    """把 `oidc_server._build_client` 换成 `MockTransport` 客户端。

    **替换的是 HTTP 客户端，不是业务函数**：`/sso/complete` → `exchange_code` →
    `_post_form` → httpx 的整条真实路径都会跑到，因此能验"上游畸形响应会怎样"。
    按顺序消费 `replies`，最后一条重复使用。

    Args:
        monkeypatch: pytest 补丁器。
        replies: 依次返回的 IdP 响应。

    Returns:
        list[str]: 实际发往 IdP 的表单体（用于断言"有没有打 IdP"）。
    """
    calls: list[str] = []
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content.decode("utf-8"))
        status, content, content_type = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, content=content, headers={"content-type": content_type})

    def fake_build_client(_conf: Settings) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(oidc_server, "_build_client", fake_build_client)
    return calls


def _token_payload(settings: Settings, *, refresh: str = "rt") -> bytes:
    """构造一份正常的 token 端点响应体（access_token 可被本服务验签）。

    Args:
        settings: 配置（决定验签密钥与 issuer/audience）。
        refresh: 刷新令牌值。

    Returns:
        bytes: 响应体。
    """
    body = {
        "access_token": make_token(settings=settings),
        "refresh_token": refresh,
        "expires_in": 300,
    }
    return json.dumps(body).encode()


def _patch_exchange_always(
    monkeypatch: pytest.MonkeyPatch, factory: Callable[[], Any]
) -> list[Any]:
    """替换 `sso_routes.exchange_code`（只用于"不关心协议层"的分支）。

    Args:
        monkeypatch: pytest 补丁器。
        factory: 返回 `TokenSet` 或抛异常的可调用对象。

    Returns:
        list[Any]: 调用记录（收到的 `code`）。
    """
    calls: list[Any] = []

    async def fake_exchange(conf: Settings, code: str, verifier: str, redirect_uri: str) -> Any:
        calls.append(code)
        return factory()

    monkeypatch.setattr(sso_routes, "exchange_code", fake_exchange)
    return calls


def _patch_refresh_always(monkeypatch: pytest.MonkeyPatch, factory: Callable[[], Any]) -> list[str]:
    """替换 `sso_routes.refresh_tokens`。

    Args:
        monkeypatch: pytest 补丁器。
        factory: 返回 `TokenSet` 或抛异常的可调用对象。

    Returns:
        list[str]: 收到的刷新令牌。
    """
    calls: list[str] = []

    async def fake_refresh(conf: Settings, refresh_token: str) -> Any:
        calls.append(refresh_token)
        return factory()

    monkeypatch.setattr(sso_routes, "refresh_tokens", fake_refresh)
    return calls


@lru_cache(maxsize=1)
def _rsa_key() -> Any:
    """生成一把 2048 位 RSA 私钥（进程内复用，避免每个用例都生成）。

    Returns:
        Any: RSA 私钥对象。
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _keycloak_like_token(*, now: int | None = None, role_count: int = 12) -> str:
    """签发一个**接近现场长度**的 Keycloak 风格 access token（RS256）。

    刻意包含 Keycloak 真实会带的大块声明：`realm_access.roles`、`resource_access`、
    `name`/`email`/`preferred_username`、`allowed-origins`、`sid`、`session_state`、
    `jti` 等。长度是 Cookie 体积风险的**唯一**变量，用 HS256 的短签名会低估
    （RS256 签名 256 字节 = 342 个 base64 字符，HS256 只有 43）。

    Args:
        now: 基准时间戳（缺省当前时间）。
        role_count: `realm_access.roles` 的角色个数。

    Returns:
        str: JWT 原文。
    """
    issued = int(time.time()) if now is None else now
    roles = [f"oa_role_{index:02d}_with_a_long_name" for index in range(role_count)]
    claims: dict[str, Any] = {
        "exp": issued + 300,
        "iat": issued,
        "auth_time": issued - 120,
        "jti": "3f19b2f0-6f2b-4d5c-9c1e-8a7d6b5c4a31",
        "iss": "https://moy.example.com/realms/officeai",
        "aud": ["officeai-adapter", "account"],
        "sub": "a1b2c3d4-0000-4000-8000-9f8e7d6c5b4a",
        "typ": "Bearer",
        "azp": "officeai-adapter",
        "sid": "5c4d3e2f-1111-4222-8333-444455556666",
        "session_state": "5c4d3e2f-1111-4222-8333-444455556666",
        "acr": "1",
        "scope": "openid profile email",
        "email_verified": True,
        "name": "Zhang Wei",
        "preferred_username": "zhangwei",
        "given_name": "Wei",
        "family_name": "Zhang",
        "email": "zhangwei@example.com",
        "tenant_id": "tnt_01HF8ZZZZZZZZZZZZZZZZZZZZZ",
        "realm_access": {"roles": roles},
        "resource_access": {
            "officeai-adapter": {"roles": roles[:6]},
            "account": {"roles": ["manage-account", "manage-account-links", "view-profile"]},
        },
        "allowed-origins": ["https://moy.example.com"],
    }
    return jwt.encode(claims, _rsa_key(), algorithm="RS256", headers={"kid": "qa-probe-kid"})


# ===========================================================================
# 盲区 1：畸形/异常的上游响应，错误响应体会不会带出令牌片段
# ===========================================================================
@pytest.mark.parametrize(
    ("label", "reply"),
    [
        (
            "malformed-json",
            (200, b'{"access_token": "EYEBALL-SECRET-1", "expires_in": 300', "application/json"),
        ),
        (
            "html-body",
            (200, b"<html><body>access_token=EYEBALL-SECRET-2</body></html>", "text/html"),
        ),
        (
            "json-without-access-token",
            (
                200,
                json.dumps(
                    {
                        "error": "invalid_grant",
                        "error_description": "Code not valid: EYEBALL-SECRET-3",
                        "refresh_token": "EYEBALL-SECRET-3",
                    }
                ).encode(),
                "application/json",
            ),
        ),
        (
            "non-200-with-error-description",
            (
                400,
                json.dumps(
                    {
                        "error": "invalid_grant",
                        "error_description": "Realm does not allow: EYEBALL-SECRET-4",
                        "access_token": "EYEBALL-SECRET-4",
                    }
                ).encode(),
                "application/json",
            ),
        ),
        (
            "json-array",
            (200, b'["EYEBALL-SECRET-5", "access_token"]', "application/json"),
        ),
    ],
)
def test_complete_error_body_never_echoes_upstream_payload(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    reply: IdpReply,
) -> None:
    """上游响应畸形时，`/sso/complete` 的错误响应体不得带出任何上游片段。

    工程师只测了成功路径的"响应体不含令牌"。这条补的是**失败路径**：`ApiError` 的
    `internal_detail` 会进日志，只要有人把上游片段塞进 `message`/`data`，浏览器就会
    收到它。故逐字断言"上游那段原文与字段名都不在响应体里"，并要求零 Cookie 下发。

    Args:
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
        label: 用例标签（仅用于失败信息）。
        reply: 模拟的 IdP 回包。
    """
    calls = _patch_idp(monkeypatch, [reply])
    state, pending = _start_login(sso_client)

    response = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )

    assert len(calls) == 1, f"[{label}] 应当且只应当打一次 IdP"
    assert response.status_code == 401, f"[{label}] {response.text}"
    body = response.json()
    assert body["code"] == int(ErrorCode.UNAUTHORIZED), f"[{label}] {body}"
    assert body["message"] == LOGIN_FAILED_MESSAGE, f"[{label}] 用户文案必须是统一的那句"
    assert body["data"] is None, f"[{label}] 失败响应不得携带任何业务数据：{body['data']}"

    _assert_no_token_leak(response.text)
    for secret in ("EYEBALL-SECRET", "Code not valid", "Realm does not allow", "<html>"):
        assert secret not in response.text, f"[{label}] 响应体带出了上游片段：{response.text}"

    assert _set_cookie_values(response) == [], f"[{label}] 兑换失败时不得下发任何 Cookie"


# ===========================================================================
# 盲区 2：刷新令牌已失效（invalid_grant）时的 Cookie 处置
# ===========================================================================
def test_refresh_invalid_grant_issues_no_cookies_and_keeps_state(
    sso_settings: Settings,
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**已修（QA-02）**：`invalid_grant` 时 `/sso/refresh` 清掉**已死**的 `oa_refresh`。

    不清的话浏览器会一直带着这个死令牌，前端每次重试都白打一次 IdP，而用户看不到
    "刷新已死"。清掉后 `/sso/session` 立即答 `refreshable=false`，前端走"重新登录"。
    `oa_session` **不动**——它可能仍然有效，删了等于提前踢人。
    网络类失败（30001/30002）仍然 `Set-Cookie` 为空（见 `test_refresh_*` 系列）。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    _patch_refresh_always(
        monkeypatch,
        lambda: (_ for _ in ()).throw(
            ApiError(ErrorCode.UNAUTHORIZED, internal_detail="invalid_grant")
        ),
    )
    cookies = "; ".join(
        [
            _encoded_pair(SESSION_COOKIE_NAME, make_token(settings=sso_settings, expires_in=300)),
            _encoded_pair(REFRESH_COOKIE_NAME, "rt-already-dead"),
        ]
    )

    response = sso_client.post("/sso/refresh", headers={"Cookie": cookies, **SAME_ORIGIN})

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
    _assert_no_token_leak(response.text)
    cleared = {header.split("=", 1)[0] for header in _set_cookie_values(response)}
    assert cleared == {REFRESH_COOKIE_NAME}, (
        "已死的刷新令牌必须清掉，且不得连带删除仍可能有效的 oa_session（QA-02）；"
        f"实际 Set-Cookie={_set_cookie_values(response)}"
    )


def test_session_still_reports_authenticated_after_refresh_died(
    sso_settings: Settings,
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """刷新令牌已死、access 仍有效时，`/sso/session` 报 `authenticated=true`。

    这是**当前口径**（access 本身还能用几分钟，不算错），但要写进交付说明：
    `/sso/session` **不能**当作"刷新能力探针"——它从不检查 `oa_refresh`。
    前端若把 session 当刷新探针，会在 access 过期后才第一次发现刷新已死。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    _patch_refresh_always(
        monkeypatch,
        lambda: (_ for _ in ()).throw(
            ApiError(ErrorCode.UNAUTHORIZED, internal_detail="invalid_grant")
        ),
    )
    cookies = "; ".join(
        [
            _encoded_pair(SESSION_COOKIE_NAME, make_token(settings=sso_settings, expires_in=300)),
            _encoded_pair(REFRESH_COOKIE_NAME, "rt-already-dead"),
        ]
    )

    refresh = sso_client.post("/sso/refresh", headers={"Cookie": cookies, **SAME_ORIGIN})
    session = sso_client.get("/sso/session", headers={"Cookie": cookies, **SAME_ORIGIN})

    assert refresh.status_code == 401
    assert session.status_code == 200
    assert session.json()["data"]["authenticated"] is True, (
        "session 不读 oa_refresh：刷新已失效也不会体现在 authenticated 上"
    )


# ===========================================================================
# 盲区 3：Cookie 体积（真实长度 JWT → base64 膨胀 33% → 4KB 上限）
# ===========================================================================
def test_set_cookie_for_realistic_keycloak_token_fits_4kb(
    sso_settings: Settings,
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实长度（Keycloak 风格 RS256 + 多角色）令牌的 `Set-Cookie` 不超过 4KB。

    走**真实兑换路径**（MockTransport 提供 IdP 回包），度量最终下发的
    `oa_session` 头长度。超 4KB 的后果不是报错，而是浏览器静默丢弃 →
    "登录成功但下一页又未登录"，属于最难排查的一类现场故障。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    token = _keycloak_like_token()
    payload = json.dumps(
        {"access_token": token, "refresh_token": "rt-rotated", "expires_in": 300}
    ).encode()
    _patch_idp(monkeypatch, [(200, payload, "application/json")])
    state, pending = _start_login(sso_client)

    response = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )

    assert response.status_code == 200, response.text
    session_cookie = _session_set_cookie(response)
    value = session_cookie.split(";", 1)[0].split("=", 1)[1]
    assert value == token, (
        "**已修（QA-04）**：Cookie 值是令牌原文，不再套 base64（省 33%）；"
        f"实际长度 {len(value)} vs 令牌 {len(token)}"
    )
    assert len(session_cookie) < BROWSER_COOKIE_LIMIT, (
        f"真实长度令牌下 Set-Cookie 已达 {len(session_cookie)} 字节"
        f"（令牌 {len(token)} 字符）：{session_cookie[:80]}…"
    )


@pytest.mark.parametrize(
    ("token_chars", "expected_over"),
    [
        (2000, False),
        (3000, False),
        # 3100 是**修复前**的越界点（base64 膨胀 4/3）：留着它是为了守住"没有再退回编码"
        (3100, False),
        (4000, False),
        (4100, True),
    ],
)
def test_cookie_size_boundary_sits_above_4kb_token(
    sso_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    token_chars: int,
    expected_over: bool,
) -> None:
    """`Set-Cookie` 越过 4KB 的**令牌长度阈值**已从 ≈3.0 KB 推到 ≈4.0 KB（QA-04）。

    修复前：`oa_session=<base64url(token)>`，4/3 膨胀 → 令牌 3023 字符即越界。
    修复后：值就是令牌原文 → 固定开销 66 字节 + 令牌长度，边界在 4030 字符附近。
    3100 这一档专门守着"别再退回 base64"：一旦有人把 `RAW_VALUE_COOKIES` 改回去，
    它会立刻变红。

    Args:
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
        token_chars: 构造的令牌字符数。
        expected_over: 该长度下是否应当越过 4KB。
    """
    fake_token = "A" * token_chars
    payload = json.dumps({"access_token": fake_token, "expires_in": 300}).encode()
    _patch_idp(monkeypatch, [(200, payload, "application/json")])
    state, pending = _start_login(sso_client)

    response = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )
    assert response.status_code == 200, response.text

    header = _session_set_cookie(response)
    over = len(header) > BROWSER_COOKIE_LIMIT
    assert over is expected_over, (
        f"令牌 {token_chars} 字符 → Set-Cookie {len(header)} 字节 (超 4KB={over})；"
        f"预期 {expected_over}（不编码后边界在 4030 字符附近）"
    )


# ===========================================================================
# 盲区 4：access TTL 300s vs Cookie Max-Age 8h —— 会不会每 5 分钟被踢一次
# ===========================================================================
def test_expired_access_cookie_on_business_endpoint_returns_20002(
    sso_settings: Settings, sso_client: TestClient
) -> None:
    """拿**已过期**的 `oa_session` 打业务接口 → `20002`（前端据此触发刷新）。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    expired = make_expired_token(settings=sso_settings)

    response = sso_client.get(
        BUSINESS_PATH,
        headers={"Cookie": _encoded_pair(SESSION_COOKIE_NAME, expired), **SAME_ORIGIN},
    )

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
    assert response.json()["data"] is None
    _assert_no_token_leak(response.text)


def test_expired_access_cookie_then_refresh_succeeds(
    sso_settings: Settings, sso_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过期令牌 + **走刷新**：`/sso/refresh` 成功并覆写两个 Cookie（正确路径）。

    这条是"每 5 分钟掉线"的**对照组**：只要前端在 401 后直接刷新、**不先探 session**，
    链路是通的（工程师已验证 refresh 成功分支，这里只验证它在"access 已过期"这个
    真实现场下仍然成立）。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    from app.core.oidc_server import TokenSet

    _patch_refresh_always(
        monkeypatch,
        lambda: TokenSet(
            access_token=make_token(settings=sso_settings, expires_in=300),
            refresh_token="rt-new",
            expires_in=300,
        ),
    )
    cookies = "; ".join(
        [
            _encoded_pair(SESSION_COOKIE_NAME, make_expired_token(settings=sso_settings)),
            _encoded_pair(REFRESH_COOKIE_NAME, "rt-good"),
        ]
    )

    response = sso_client.post("/sso/refresh", headers={"Cookie": cookies, **SAME_ORIGIN})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"ok": True, "expires_in": 300}
    _assert_no_token_leak(response.text)
    issued = {value.split("=", 1)[0] for value in _set_cookie_values(response)}
    assert issued == {SESSION_COOKIE_NAME, REFRESH_COOKIE_NAME}, (
        "刷新成功必须同时覆写访问与刷新令牌（刷新令牌是轮换的）"
    )


def test_session_probe_leaves_refresh_token_intact(
    sso_settings: Settings, sso_client: TestClient
) -> None:
    """**已修（QA-01）**：access 过期时 `GET /sso/session` **只清 `oa_session`**。

    `oa_refresh` 此刻可能完全有效：access TTL 是 300 秒、会话上限 8 小时，"access 过期、
    refresh 仍有效"是会话期内的**正常状态**。修复前这条探测会把刷新令牌一并删掉，
    等于把"刷新即可续期"降级成"重新登录"（用户每 300 秒被踢一次）。
    本用例即该缺陷的回归防线。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    jar = _BrowserJar()
    jar.put(
        SESSION_COOKIE_NAME,
        _encoded_value(SESSION_COOKIE_NAME, make_expired_token(settings=sso_settings)),
    )
    jar.put(REFRESH_COOKIE_NAME, _encoded_value(REFRESH_COOKIE_NAME, "rt-still-good"))

    session = sso_client.get("/sso/session", headers={"Cookie": jar.header_for("/sso/session")})
    assert session.status_code == 200
    jar.absorb(session)

    cleared = [header for header in _set_cookie_values(session) if header.startswith("oa_refresh=")]
    assert cleared == [], (
        "探测会话不该删除刷新令牌（QA-01）——现在它被删了，"
        f"随后 /sso/refresh 必然 20002；实际 Set-Cookie={_set_cookie_values(session)}"
    )
    assert REFRESH_COOKIE_NAME in jar.names(), "浏览器侧刷新令牌已被这条探测请求删掉"


def test_session_probe_then_refresh_still_works(
    sso_settings: Settings, sso_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**已修（QA-01）的用户路径**：先探 session，再刷新 → 仍然成功。

    现场场景（很常见，不是边角）：用户登录后离开 6 分钟，回来刷新页面 → 前端启动必问
    一句 `GET /sso/session`（"我还登录着吗"）→ 业务请求 401 → 调 `/sso/refresh`。
    修复前第二步就把刷新令牌删了，第三步必然 20002，8 小时会话实际只活 300 秒。

    这里替换 `refresh_tokens` 本身（已由本文件其他用例证明它真的会打 IdP），
    只验"刷新令牌还在、且真的被送去续期"。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    from app.core.oidc_server import TokenSet

    calls = _patch_refresh_always(
        monkeypatch,
        lambda: TokenSet(
            access_token=make_token(settings=sso_settings, expires_in=300),
            refresh_token="rt-rotated",
            expires_in=300,
        ),
    )
    jar = _BrowserJar()
    jar.put(
        SESSION_COOKIE_NAME,
        _encoded_value(SESSION_COOKIE_NAME, make_expired_token(settings=sso_settings)),
    )
    jar.put(REFRESH_COOKIE_NAME, _encoded_value(REFRESH_COOKIE_NAME, "rt-still-good"))

    probed = sso_client.get("/sso/session", headers={"Cookie": jar.header_for("/sso/session")})
    jar.absorb(probed)
    refresh = sso_client.post(
        "/sso/refresh", headers={"Cookie": jar.header_for("/sso/refresh"), **SAME_ORIGIN}
    )

    assert refresh.status_code == 200, refresh.text
    assert calls == ["rt-still-good"], "刷新令牌必须还在，并被原样送去续期"
    assert REFRESH_COOKIE_NAME in {value.split("=", 1)[0] for value in _set_cookie_values(refresh)}


# ===========================================================================
# 盲区 5：空 Authorization 头是否回落 Cookie 轨
# ===========================================================================
def test_empty_authorization_header_falls_back_to_cookie(
    sso_settings: Settings,
) -> None:
    """`Authorization:`（空值）等同"没有该头"→ 回落 Cookie 轨并放行。

    必须证明**服务端真的收到了这个空头**，否则"回落成功"与"头被丢弃"无法区分
    （两者都返回 200，等于假绿）。故用一层中间件把服务端看到的头记下来。

    Args:
        sso_settings: 配置。
    """
    seen: list[str | None] = []

    async def recorder(request: Request, call_next: Callable[[Request], Any]) -> Response:
        seen.append(request.headers.get("authorization"))
        return await call_next(request)

    app = create_app(sso_settings)
    app.middleware("http")(recorder)
    token = make_token(settings=sso_settings, expires_in=300)

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(
            BUSINESS_PATH,
            headers={
                "Authorization": "",
                "Cookie": _encoded_pair(SESSION_COOKIE_NAME, token),
                **SAME_ORIGIN,
            },
        )

    assert seen == [""], f"服务端收到的 Authorization 头是 {seen!r}，空头没有真的发出去"
    assert response.status_code == 200, response.text
    assert response.json()["data"]["user_id"] == "usr_test_user"


def test_whitespace_authorization_header_falls_back_to_cookie(
    sso_settings: Settings, sso_client: TestClient
) -> None:
    """**已修（QA-05）**：`Authorization: " "`（纯空白）等同"没有该头" → 回落 Cookie。

    修复前判据是 `if header:`，对 `" "` 为真 → 走 Bearer 轨并因缺前缀被拒（20002），
    与"空值等同于没有该头"的承诺有缝，现场会被误判成令牌损坏。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    token = make_token(settings=sso_settings, expires_in=300)

    response = sso_client.get(
        BUSINESS_PATH,
        headers={
            "Authorization": " ",
            "Cookie": _encoded_pair(SESSION_COOKIE_NAME, token),
            **SAME_ORIGIN,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["user_id"] == "usr_test_user"


def test_bearer_prefix_without_token_falls_back_to_cookie(
    sso_settings: Settings, sso_client: TestClient
) -> None:
    """**已修（QA-05）**：`Authorization: Bearer `（只有前缀）→ 回落 Cookie。

    判据统一成"这个头有没有携带**令牌**"：`Bearer ` 后面什么都没有，就不是凭证，
    与空头同等处理。带**别的前缀**（`Basic`/`Token`）仍然 20002——那是"用错了鉴权方式"，
    不该被 Cookie 静默兜住。

    **残留风险（有意接受，记录在此）**：双轨期内若旧前端在令牌过期后发 `Bearer `+空串，
    而浏览器里恰好有有效 Cookie，请求会以 Cookie 身份通过——这会把"前端漏传令牌"的 bug
    掩盖成"看起来能用"。接受的理由：Cookie 在目标态**就是**主凭证，把它当"没有头"处理
    才是自洽语义；要发现这类 bug 靠日志（`cookie_credential` 相关事件）而不是靠 401。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    token = make_token(settings=sso_settings, expires_in=300)

    response = sso_client.get(
        BUSINESS_PATH,
        headers={
            "Authorization": "Bearer ",
            "Cookie": _encoded_pair(SESSION_COOKIE_NAME, token),
            **SAME_ORIGIN,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["user_id"] == "usr_test_user"


# ===========================================================================
# 盲区 6：`/sso/complete` 的重放（同一 code 提交两次）
# ===========================================================================
def test_complete_replay_after_success_never_reaches_idp_twice(
    sso_settings: Settings, sso_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功后重放（合规浏览器）：pending 已删 → 第二次 20002，且**不打第二次 IdP**。

    注意**能力边界**：服务端无状态，"用完即删"靠的是让浏览器删掉暂存 Cookie。
    因此这条只用 `_BrowserJar`（尊重 `Max-Age=0`）才能验证；攻击者若抓住暂存 Cookie
    原文自然不受约束——那一路由 IdP 的一次性 `code` 兜底，见下一条用例。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    payload = _token_payload(sso_settings)
    calls = _patch_idp(monkeypatch, [(200, payload, "application/json")])
    state, pending = _start_login(sso_client)
    jar = _BrowserJar()
    name, _, value = pending.partition("=")
    jar.put(name, value, path="/sso")

    first = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": jar.header_for("/sso/complete"), **SAME_ORIGIN},
    )
    assert first.status_code == 200, first.text
    assert any(
        header.startswith(f"{PENDING_COOKIE_NAME}=") and "Max-Age=0" in header
        for header in _set_cookie_values(first)
    ), "登录成功后必须清掉暂存（防重放）"
    jar.absorb(first)
    assert PENDING_COOKIE_NAME not in jar.names(), "浏览器侧暂存 Cookie 应已被删除"

    second = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": jar.header_for("/sso/complete"), **SAME_ORIGIN},
    )

    assert second.status_code == 401
    assert second.json()["code"] == int(ErrorCode.UNAUTHORIZED)
    assert second.json()["message"] == sso_routes.PENDING_INVALID_MESSAGE
    assert len(calls) == 1, f"重放不应再打 IdP，实际打了 {len(calls)} 次"
    assert _set_cookie_values(second) == [], "重放不得下发任何会话 Cookie"


def test_complete_replay_with_captured_pending_is_only_stopped_by_idp(
    sso_settings: Settings, sso_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """持有暂存 Cookie **原文**强行重放：服务端会再打一次 IdP，靠 `invalid_grant` 挡住。

    这是"并发重放"的真实边界，必须写进交付说明：本地没有"已用过的 code"记录，
    `/sso/complete` 的防重放是**让浏览器删 Cookie**级别的，不是服务端幂等级别的。
    后果可接受（`code` 一次性、10 分钟过期、`state` 一次性），但不能声称"服务端拒绝了
    重复兑换"——真正拒绝的是 IdP。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    ok_payload = _token_payload(sso_settings)
    invalid_grant = json.dumps({"error": "invalid_grant"}).encode()
    calls = _patch_idp(
        monkeypatch,
        [
            (200, ok_payload, "application/json"),
            (400, invalid_grant, "application/json"),
        ],
    )
    state, pending = _start_login(sso_client)

    first = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )
    second = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )

    assert first.status_code == 200
    assert second.status_code == 401, second.text
    assert second.json()["message"] == LOGIN_FAILED_MESSAGE, "第二次是 IdP 拒绝，走兑换失败文案"
    assert len(calls) == 2, "服务端无已用 code 记录：抓包重放会被再次送往 IdP"


def test_complete_failure_keeps_pending_so_user_can_retry(
    sso_settings: Settings, sso_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第一次兑换失败后 pending **保留**，用户用同一份暂存重试可以成功。

    这是"失败时不清暂存"的设计收益（fail-closed 但不 fail-stuck）：IdP 抖一下
    （超时/5xx）不该让用户回到登录页重来。
    **同时也要看到代价**：`code` 若已在 IdP 侧被消费（响应丢了），重试会得到
    `invalid_grant`——用户的正确动作仍是重新发起登录，这一点前端要能兜住。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
        monkeypatch: pytest 补丁器。
    """
    ok_payload = _token_payload(sso_settings)
    calls = _patch_idp(
        monkeypatch,
        [
            (503, b"upstream busy", "text/plain"),
            (200, ok_payload, "application/json"),
        ],
    )
    state, pending = _start_login(sso_client)

    failed = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )
    assert failed.status_code == 401
    assert _set_cookie_values(failed) == [], "失败时不得下发 Cookie，也不得清暂存"

    retried = sso_client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )

    assert retried.status_code == 200, retried.text
    assert len(calls) == 2, "重试必须真的再打一次 IdP（而不是被本地拦掉）"
    assert "grant_type=authorization_code" in calls[1]


# ===========================================================================
# 盲区 7：`GET /sso/session` 的四态语义
# ===========================================================================
def test_session_without_any_cookie(sso_client: TestClient) -> None:
    """未登录：`authenticated=false`、`expires_in=null`，且**不写任何 Cookie**。

    Args:
        sso_client: 测试客户端。
    """
    response = sso_client.get("/sso/session", headers=SAME_ORIGIN)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": False,
        "expired": False,
    }
    assert _set_cookie_values(response) == [], "没有 Cookie 时不该下发清除头（无意义）"
    _assert_no_token_leak(response.text)


def test_session_with_valid_token(sso_settings: Settings, sso_client: TestClient) -> None:
    """有效令牌：`authenticated=true`，`expires_in` 反映剩余秒数。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    token = make_token(settings=sso_settings, expires_in=300)

    response = sso_client.get(
        "/sso/session",
        headers={"Cookie": _encoded_pair(SESSION_COOKIE_NAME, token), **SAME_ORIGIN},
    )

    data = response.json()["data"]
    assert data["authenticated"] is True
    assert isinstance(data["expires_in"], int)
    assert 250 <= data["expires_in"] <= 300, f"剩余秒数应接近 300，实际 {data['expires_in']}"
    assert _set_cookie_values(response) == [], "有效会话不该被覆写或清除"
    assert token not in response.text, "session 绝不能回显令牌"


def test_session_with_expired_token(sso_settings: Settings, sso_client: TestClient) -> None:
    """过期令牌：`authenticated=false` + 清掉废 Cookie。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    expired = make_expired_token(settings=sso_settings)

    response = sso_client.get(
        "/sso/session",
        headers={"Cookie": _encoded_pair(SESSION_COOKIE_NAME, expired), **SAME_ORIGIN},
    )

    assert response.json()["data"] == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": False,
        "expired": True,
    }
    cleared = {header.split("=", 1)[0] for header in _set_cookie_values(response)}
    assert SESSION_COOKIE_NAME in cleared, "失效的访问令牌 Cookie 应被清掉"
    _assert_no_token_leak(response.text)


def test_session_with_undecodable_garbage_cookie(sso_client: TestClient) -> None:
    """**已修（QA-05）**：垃圾值（如 `%%%garbage`）→ 按不可用处理并**清掉**这条 Cookie。

    修复前 `read_cookie` 对它返回 None，与"根本没有这条 Cookie"无法区分，于是这条
    Cookie 会永远挂在每个请求上（僵尸 Cookie）；而 base64 可解的垃圾值却会被清除——
    同类输入两种处置。现在两者一致：清 Cookie + `expired=true`。

    Args:
        sso_client: 测试客户端。
    """
    response = sso_client.get(
        "/sso/session",
        headers={"Cookie": f"{SESSION_COOKIE_NAME}=%%%garbage", **SAME_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": False,
        "expired": True,
    }
    cleared = {header.split("=", 1)[0] for header in _set_cookie_values(response)}
    assert SESSION_COOKIE_NAME in cleared, "僵尸 Cookie 必须被清掉（QA-05）"
    _assert_no_token_leak(response.text)


def test_session_with_base64_garbage_token(sso_settings: Settings, sso_client: TestClient) -> None:
    """可解码但不是 JWT 的字符串 → 验签失败 → `authenticated=false` + 清 Cookie。

    与上一条（`%%%garbage`）现在处置一致：都清 Cookie、都回 `expired=true`（QA-05）。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    encoded = build_set_cookie(
        SESSION_COOKIE_NAME, "not-a-jwt", max_age=28800, path="/", secure=True
    ).split(";", 1)[0]

    response = sso_client.get("/sso/session", headers={"Cookie": encoded, **SAME_ORIGIN})

    assert response.status_code == 200
    assert response.json()["data"] == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": False,
        "expired": True,
    }
    cleared = {header.split("=", 1)[0] for header in _set_cookie_values(response)}
    assert SESSION_COOKIE_NAME in cleared


def test_session_payload_distinguishes_expired_from_never_logged_in(
    sso_settings: Settings, sso_client: TestClient
) -> None:
    """**已修（QA-03）**：`/sso/session` 的 data 能区分"未登录"与"登录已过期"。

    前端需要两个完全不同的动作："令牌过期了，去刷新（还能救）" 与 "没登录/刷新令牌也没了，
    去重新登录"。修复前两者响应逐字相同，而唯一能区分的判据（浏览器里还有没有
    `oa_refresh`）在 httpOnly Cookie 里，前端读不到。现在由 `refreshable` / `expired`
    两个布尔直接回答。

    Args:
        sso_settings: 配置。
        sso_client: 测试客户端。
    """
    missing = sso_client.get("/sso/session", headers=SAME_ORIGIN).json()["data"]
    expired = sso_client.get(
        "/sso/session",
        headers={
            "Cookie": "; ".join(
                [
                    _encoded_pair(SESSION_COOKIE_NAME, make_expired_token(settings=sso_settings)),
                    _encoded_pair(REFRESH_COOKIE_NAME, "rt-still-good"),
                ]
            ),
            **SAME_ORIGIN,
        },
    ).json()["data"]

    assert expired != missing, (
        "未登录与登录过期返回完全相同的 data "
        f"({missing})：前端无法区分「该刷新」与「该重新登录」，"
        "建议增加 refreshable/expired 之类的字段"
    )
