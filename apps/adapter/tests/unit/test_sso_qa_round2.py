"""P0-5 修复轮的**独立复验**（QA 第二轮）：逐条验 `6d040bc` 的修法是否真的生效。

为什么另起一个文件、且**不 import** `test_sso_qa_probe.py` 的辅助
--------------------------------------------------------------

`test_sso_qa_probe.py` 在本轮被工程师改写了 9 处（都是"原先钉的是修复前行为，不改就红"）。
复验若复用它里面的 `_patch_idp` / `_BrowserJar`，等于用**被审对象**当量具——只要那边
不小心把辅助函数改松（例如 jar 不再处理 `Max-Age=0`），本文件会跟着一起绿。
故本文件自带一份最小量具，与第一轮**独立重写**，结论才能互相印证。

本轮要回答的 6 个问题
--------------------

1. QA-01 用户路径端到端：过期 access + 有效 refresh → `GET /sso/session`（refresh 必须还在）
   → `POST /sso/refresh`（必须 200）。走**真实兑换链路**（MockTransport 供 IdP 回包），
   不 stub `refresh_tokens`。
2. QA-02 边界：`invalid_grant` 清 `oa_refresh`；**连接失败 / 超时（30001/30002）绝不清**。
   另外补一条工程师没画到的边界：**身份平台自己回 503**（有 HTTP 响应、不是连接失败）
   会被 `oidc_server._post_form` 归成 20002——此时清不清？见 QA-06。
3. QA-03 四态：`{F,F,F}` / `{F,T,T}` / `{F,F,T}` / `{T,*,F}` 四个响应体必须**两两不同**
   且各自逐字段正确。
4. QA-04 边界：3100 与 4000 字符令牌的 `Set-Cookie` 都在 4KB 内，4100 越界；值就是令牌
   原文（不再 base64）。并测出**精确阈值**（4030 / 4031）。
5. QA-05：僵尸 Cookie 会被清且 `expired=true`；`Authorization: " "` 与 `"Bearer "` 回落
   Cookie 轨（用中间件证明服务端真的收到了非空头）；`Authorization: Basic ...` 仍 20002。
6. **错误路径的 `Set-Cookie` 是否真的发出去了**：从 `response.headers.get_list("set-cookie")`
   逐条读，确认是**独立的多条头**、没有被合并成一条带逗号的；并用 `/sso/logout` 的
   双清除头做"多条不合并"的正向对照。
"""

from __future__ import annotations

import base64
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

from app.core import oidc_server
from app.core.config import Settings
from app.core.errors import ErrorCode
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

#: 同源请求的 Fetch Metadata
SAME_ORIGIN: dict[str, str] = {"Sec-Fetch-Site": "same-origin"}

#: 业务接口（走 `get_request_context`）
BUSINESS_PATH: str = "/api/v1/identity/profile"

#: 浏览器单 Cookie 上限
BROWSER_COOKIE_LIMIT: int = 4096


# ===========================================================================
# 夹具
# ===========================================================================
@pytest.fixture
def conf() -> Settings:
    """OIDC 形态配置。

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
def client(conf: Settings) -> TestClient:
    """https 基址的测试客户端。

    Args:
        conf: 配置。

    Returns:
        TestClient: 客户端。
    """
    return TestClient(create_app(conf), base_url="https://testserver")


# ===========================================================================
# 量具（与第一轮独立重写）
# ===========================================================================
class _Jar:
    """最小浏览器 Cookie 罐：`Path` 前缀匹配 + `Max-Age=0` 删除。"""

    def __init__(self) -> None:
        """初始化空罐。"""
        self._entries: dict[str, tuple[str, str]] = {}

    def put(self, name: str, value: str, path: str = "/") -> None:
        """写入一条 Cookie。

        Args:
            name: Cookie 名。
            value: **编码后**的值。
            path: 作用域。
        """
        self._entries[name] = (value, path)

    def absorb(self, response: httpx.Response) -> None:
        """吸收响应里的 `Set-Cookie`（`Max-Age=0` 视为删除）。

        Args:
            response: 响应。
        """
        for header in _set_cookies(response):
            pair, *attrs = [part.strip() for part in header.split(";")]
            name, _, value = pair.partition("=")
            path, max_age = "/", None
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
            elif name:
                self._entries[name] = (value, path)

    def header(self, path: str) -> str:
        """构造发往 `path` 的 `Cookie` 头。

        Args:
            path: 请求路径。

        Returns:
            str: 头值。
        """
        return "; ".join(
            f"{name}={value}"
            for name, (value, cookie_path) in self._entries.items()
            if cookie_path == "/" or path == cookie_path or path.startswith(f"{cookie_path}/")
        )

    def has(self, name: str) -> bool:
        """是否持有该 Cookie。

        Args:
            name: Cookie 名。

        Returns:
            bool: 持有返回 True。
        """
        return name in self._entries


def _set_cookies(response: httpx.Response) -> list[str]:
    """取全部 `Set-Cookie`（顺序保留）。

    Args:
        response: 响应。

    Returns:
        list[str]: 头值列表。
    """
    return response.headers.get_list("set-cookie")


def _pair(name: str, value: str) -> str:
    """把原始值按该 Cookie 的编码规则包成 `name=value`。

    Args:
        name: Cookie 名。
        value: 原始值。

    Returns:
        str: `name=value`。
    """
    return build_set_cookie(name, value, max_age=28800, path="/", secure=True).split(";", 1)[0]


def _cookie_value(name: str, value: str) -> str:
    """只取编码后的值（喂 `_Jar.put`）。

    Args:
        name: Cookie 名。
        value: 原始值。

    Returns:
        str: 编码后的值。
    """
    return _pair(name, value).split("=", 1)[1]


def _state_of(authorize_url: str) -> str:
    """取授权地址里的 state。

    Args:
        authorize_url: 授权地址。

    Returns:
        str: state。
    """
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


def _start(client: TestClient) -> tuple[str, str]:
    """发起登录，返回 `(state, pending 的 name=value)`。

    Args:
        client: 客户端。

    Returns:
        tuple[str, str]: state 与暂存 Cookie。
    """
    start = client.post("/sso/start", json={}, headers=SAME_ORIGIN)
    assert start.status_code == 200, start.text
    pending = [h for h in _set_cookies(start) if h.startswith(f"{PENDING_COOKIE_NAME}=")]
    assert pending
    return _state_of(start.json()["data"]["authorize_url"]), pending[0].split(";", 1)[0]


def _patch_idp(monkeypatch: pytest.MonkeyPatch, plan: list[Any]) -> list[str]:
    """用 `MockTransport` 顶替真实 IdP；`plan` 每项是 `(status, body, ctype)` 或异常类。

    替的是**HTTP 客户端**而不是业务函数：`exchange_code` / `refresh_tokens` →
    `_post_form` → httpx 的整条真实路径都会跑到，这样"上游回 503"与"连不上"才能
    分别造出来（它们在代码里走的是不同分支）。

    Args:
        monkeypatch: pytest 补丁器。
        plan: 依次消费的计划，最后一项重复。

    Returns:
        list[str]: 实际发出的表单体。
    """
    calls: list[str] = []
    queue = list(plan)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content.decode("utf-8"))
        step = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(step, type) and issubclass(step, Exception):
            raise step("qa-round2 forced transport failure")
        status, body, content_type = step
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    def fake_client(_conf: Settings) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(oidc_server, "_build_client", fake_client)
    return calls


def _token_payload(conf: Settings, *, expires_in: int = 300) -> bytes:
    """构造一份可验签的 token 端点响应体。

    Args:
        conf: 配置。
        expires_in: 有效期。

    Returns:
        bytes: 响应体。
    """
    body = {
        "access_token": make_token(settings=conf, expires_in=expires_in),
        "refresh_token": "rt-rotated",
        "expires_in": expires_in,
    }
    return json.dumps(body).encode()


# ===========================================================================
# 1. QA-01 用户路径端到端（真实兑换链路，不 stub refresh_tokens）
# ===========================================================================
def test_user_path_probe_session_then_refresh_round_trip(
    conf: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过期 access + 有效 refresh → 探 session → 刷新 200 → 业务接口 200。

    完整复现现场序列（用户离开 6 分钟回来刷新页面），每一步都断言：
    1. `GET /sso/session` → `{authenticated:false, refreshable:true, expired:true}`；
    2. 这一步之后 `oa_refresh` **仍在浏览器里**（QA-01 的回归点）；
    3. `POST /sso/refresh` → 200，且 IdP 收到的 `refresh_token` 就是那一枚；
    4. 用刷新后的新 Cookie 打业务接口 → 200。

    Args:
        conf: 配置。
        client: 客户端。
        monkeypatch: pytest 补丁器。
    """
    calls = _patch_idp(monkeypatch, [(200, _token_payload(conf), "application/json")])
    jar = _Jar()
    jar.put(
        SESSION_COOKIE_NAME, _cookie_value(SESSION_COOKIE_NAME, make_expired_token(settings=conf))
    )
    jar.put(REFRESH_COOKIE_NAME, _cookie_value(REFRESH_COOKIE_NAME, "rt-still-good"))

    session = client.get("/sso/session", headers={"Cookie": jar.header("/sso/session")})
    assert session.status_code == 200, session.text
    assert session.json()["data"] == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": True,
        "expired": True,
    }, f"四态判据错：{session.json()['data']}"
    jar.absorb(session)
    assert jar.has(REFRESH_COOKIE_NAME), f"探 session 删掉了刷新令牌：{_set_cookies(session)}"

    refresh = client.post(
        "/sso/refresh", headers={"Cookie": jar.header("/sso/refresh"), **SAME_ORIGIN}
    )
    assert refresh.status_code == 200, f"用户路径第二步就断了：{refresh.text}"
    assert len(calls) == 1 and "grant_type=refresh_token" in calls[0]
    assert "refresh_token=rt-still-good" in calls[0], f"送去续期的不是那枚令牌：{calls[0]}"
    jar.absorb(refresh)

    profile = client.get(
        BUSINESS_PATH, headers={"Cookie": jar.header(BUSINESS_PATH), **SAME_ORIGIN}
    )
    assert profile.status_code == 200, f"刷新后的会话打业务接口仍是 {profile.status_code}"
    assert profile.json()["data"]["user_id"] == "usr_test_user"


# ===========================================================================
# 2. QA-02 边界：谁该清、谁绝不能清
# ===========================================================================
def _refresh_with(
    conf: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch, plan: list[Any]
) -> httpx.Response:
    """用给定的 IdP 计划打一次 `/sso/refresh`（access 仍有效）。

    Args:
        conf: 配置。
        client: 客户端。
        monkeypatch: pytest 补丁器。
        plan: IdP 计划。

    Returns:
        httpx.Response: 刷新响应。
    """
    _patch_idp(monkeypatch, plan)
    cookies = "; ".join(
        [
            _pair(SESSION_COOKIE_NAME, make_token(settings=conf, expires_in=300)),
            _pair(REFRESH_COOKIE_NAME, "rt-still-good"),
        ]
    )
    return client.post("/sso/refresh", headers={"Cookie": cookies, **SAME_ORIGIN})


def test_refresh_invalid_grant_clears_only_refresh_cookie(
    conf: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IdP 回 400 `invalid_grant` → 401 20002，且**只清 `oa_refresh`**。

    这是错误路径下发 `Set-Cookie` 的唯一场景，也是"注入 Response 的 append 会被
    FastAPI 丢弃"那个坑的正面验证点：头必须从 `response.headers.get_list("set-cookie")`
    逐条读出来，而不是看代码里写没写。

    Args:
        conf: 配置。
        client: 客户端。
        monkeypatch: pytest 补丁器。
    """
    plan = [(400, json.dumps({"error": "invalid_grant"}).encode(), "application/json")]
    response = _refresh_with(conf, client, monkeypatch, plan)

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED), response.text
    assert response.json()["message"] == LOGIN_FAILED_MESSAGE

    headers = _set_cookies(response)
    cleared = {header.split("=", 1)[0] for header in headers}
    assert cleared == {REFRESH_COOKIE_NAME}, f"应当只清 oa_refresh，实际 {headers}"
    assert "Path=/sso" in headers[0] and "Max-Age=0" in headers[0], headers[0]
    assert SESSION_COOKIE_NAME not in cleared, "仍可能有效的 oa_session 不该被删"


@pytest.mark.parametrize(
    ("plan_label", "plan"),
    [
        ("connect-error", [httpx.ConnectError]),
        ("read-timeout", [httpx.ReadTimeout]),
    ],
)
def test_refresh_transient_failure_never_clears_cookies(
    conf: Settings,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    plan_label: str,
    plan: list[Any],
) -> None:
    """连不上（30001）/ 超时（30002）→ **零 `Set-Cookie`**，会话原样保留。

    清了就等于把一次网络抖动升级成强制重新登录。判据必须是"错误码"，不能是
    "抛没抛异常"——两类失败都抛 `ApiError`。

    Args:
        conf: 配置。
        client: 客户端。
        monkeypatch: pytest 补丁器。
        plan_label: 用例标签。
        plan: IdP 计划。
    """
    response = _refresh_with(conf, client, monkeypatch, plan)

    assert response.status_code != 200, f"[{plan_label}] 上游失败却返回 200：{response.text}"
    code = response.json()["code"]
    assert code in {
        int(ErrorCode.DOWNSTREAM_UNAVAILABLE),
        int(ErrorCode.DOWNSTREAM_TIMEOUT),
    }, f"[{plan_label}] 期望 30001/30002，实际 {code}"
    assert _set_cookies(response) == [], (
        f"[{plan_label}] 临时故障不得清 Cookie：{_set_cookies(response)}"
    )


def test_refresh_upstream_5xx_must_not_be_treated_as_dead_token(
    conf: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**已修（QA-06）**：IdP 回 **503**（网关/实例过载，令牌本身没死）→ 不清 `oa_refresh`。

    修复前 `_post_form` 把所有非 200 上游响应都归成 `ApiError(20002)`，于是"IdP 返回
    503"与"令牌 invalid_grant"在 `/sso/refresh` 看来完全一样 → 都清刷新令牌；而 503 是
    **临时故障**，用户此刻去重新登录大概率也登不上，白白丢掉一个还能用的会话。

    修复后判据落在 `_post_form`：**4xx = 这份凭证/这次请求有问题（20002，会被清）；
    5xx = 这一次问不到答案（30001，不会被清）**。这也覆盖了真实现场最常见的形态——
    "LB/ingress 后面的 IdP 挂了"表现为 HTTP 5xx，而不是 TCP 连不上。

    Args:
        conf: 配置。
        client: 客户端。
        monkeypatch: pytest 补丁器。
    """
    plan = [(503, b'{"error": "temporarily_unavailable"}', "application/json")]
    response = _refresh_with(conf, client, monkeypatch, plan)

    assert _set_cookies(response) == [], (
        f"上游 503 属临时故障，不该清刷新令牌；实际 {_set_cookies(response)}"
        f"（响应码 {response.json()['code']}）"
    )


@pytest.mark.parametrize("idp_status", [429, 408])
def test_refresh_upstream_throttling_is_transient_not_dead_token(
    conf: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch, idp_status: int
) -> None:
    """**已修（QA-07）**：IdP 回 **429（限流）/ 408（超时响应）** 时不清 `oa_refresh`。

    QA-06 把判据写成"5xx = 这一次问不到答案，4xx = 这份凭证有问题"，但 `429 Too Many
    Requests` 与 `408 Request Timeout` 落在 4xx 桶里，语义上却是**典型的临时故障**：
    身份平台过载时会主动限流，而不是告诉你"刷新令牌失效"。修复后判据扩成
    "**4xx（除 408/429）= 凭证问题；5xx/408 = 30001；429 = 20006**"。

    为什么 429 比 5xx 更值得单独一格——**放大回路**：access TTL 统一 300 s，一批用户往往在
    同一时刻集中刷新；IdP 扛不住回 429，若实现清掉所有人的刷新令牌，这批人随即全部被推去
    **重新登录**（密码 + 二次验证，比刷新重得多），把 429 放大成登录风暴，再触发一轮 429。
    5xx 那一档没有这个回路（只影响恰好撞上的那一次）。

    为什么 429 用 `20006`（`RATE_LIMITED`，映射 HTTP 429、文案「当前使用人数较多，请稍后再试」）
    而不是塞进 `30001`：前者给前端的信号是**可退避重试**，后者是笼统的"服务不可用"；
    两者都不是 `20002`，所以 `_refresh_or_clear_dead_token` 的"只对 20002 清"天然不动 Cookie。

    Args:
        conf: 配置。
        client: 客户端。
        monkeypatch: pytest 补丁器。
        idp_status: 构造的上游状态码。
    """
    plan = [(idp_status, b'{"error": "temporarily_unavailable"}', "application/json")]
    response = _refresh_with(conf, client, monkeypatch, plan)

    assert _set_cookies(response) == [], (
        f"上游 {idp_status} 属临时故障，不该清刷新令牌；实际 {_set_cookies(response)}"
        f"（响应码 {response.json()['code']}）"
    )
    # 错误码也要钉：429 → 20006（可退避重试）、408 → 30001（服务不可用）。
    # 两者都**不是** 20002，前端据此退避重试而不跳登录页。
    expected = (
        (429, int(ErrorCode.RATE_LIMITED))
        if idp_status == 429
        else (503, int(ErrorCode.DOWNSTREAM_UNAVAILABLE))
    )
    assert (response.status_code, response.json()["code"]) == expected, (
        f"上游 {idp_status} 的错误码不对：{response.status_code} {response.text}"
    )


# ===========================================================================
# 3. QA-03 四态必须两两可区分
# ===========================================================================
def test_session_four_states_are_distinguishable(conf: Settings, client: TestClient) -> None:
    """四种情形的 `data` 逐字段正确，且**两两不同**。

    | 情形 | authenticated | refreshable | expired |
    | --- | --- | --- | --- |
    | 从未登录 | F | F | F |
    | 过期但可刷新 | F | T | T |
    | 过期且刷新也没了 | F | F | T |
    | 有效 | T | * | F |

    只断言"过期 != 未登录"是不够的（`refreshable` 恒为 False 也能满足它），必须逐字段钉。
    前端据此有唯一确定性规则：`authenticated` → 直接用；`refreshable` → 刷新；
    否则 → 登录页。

    Args:
        conf: 配置。
        client: 客户端。
    """
    expired = make_expired_token(settings=conf)
    valid = make_token(settings=conf, expires_in=300)
    refresh_pair = _pair(REFRESH_COOKIE_NAME, "rt-still-good")

    never = client.get("/sso/session", headers=SAME_ORIGIN).json()["data"]
    expired_refreshable = client.get(
        "/sso/session",
        headers={"Cookie": f"{_pair(SESSION_COOKIE_NAME, expired)}; {refresh_pair}", **SAME_ORIGIN},
    ).json()["data"]
    expired_no_refresh = client.get(
        "/sso/session",
        headers={"Cookie": _pair(SESSION_COOKIE_NAME, expired), **SAME_ORIGIN},
    ).json()["data"]
    alive = client.get(
        "/sso/session",
        headers={"Cookie": f"{_pair(SESSION_COOKIE_NAME, valid)}; {refresh_pair}", **SAME_ORIGIN},
    ).json()["data"]

    assert never == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": False,
        "expired": False,
    }
    assert expired_refreshable["authenticated"] is False
    assert expired_refreshable["refreshable"] is True, f"过期但可刷新：{expired_refreshable}"
    assert expired_refreshable["expired"] is True, f"过期但可刷新：{expired_refreshable}"
    assert expired_no_refresh["authenticated"] is False
    assert expired_no_refresh["refreshable"] is False, f"过期且无刷新令牌：{expired_no_refresh}"
    assert expired_no_refresh["expired"] is True, f"过期且无刷新令牌：{expired_no_refresh}"
    assert alive["authenticated"] is True and alive["expired"] is False, f"有效会话：{alive}"
    assert alive["refreshable"] is True, f"有效会话也应报告可刷新：{alive}"
    assert isinstance(alive["expires_in"], int) and alive["expires_in"] > 0

    bodies = [
        json.dumps(item, sort_keys=True)
        for item in (never, expired_refreshable, expired_no_refresh, alive)
    ]
    assert len(set(bodies)) == 4, f"四态里有响应体无法区分：{bodies}"


# ===========================================================================
# 4. QA-04 Cookie 体积边界与"值就是令牌原文"
# ===========================================================================
@lru_cache(maxsize=1)
def _rsa_key() -> Any:
    """2048 位 RSA 私钥（进程内复用）。

    Returns:
        Any: 私钥对象。
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _keycloak_token(role_count: int = 12) -> str:
    """Keycloak 风格 RS256 令牌（含 realm_access/resource_access 等真实大块声明）。

    Args:
        role_count: `realm_access.roles` 的角色数。

    Returns:
        str: JWT 原文。
    """
    now = int(time.time())
    roles = [f"oa_role_{index:02d}_with_a_long_name" for index in range(role_count)]
    return jwt.encode(
        {
            "exp": now + 300,
            "iat": now,
            "auth_time": now - 120,
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
                "account": {"roles": ["manage-account", "manage-account-link", "view-profile"]},
            },
            "allowed-origins": ["https://moy.example.com"],
        },
        _rsa_key(),
        algorithm="RS256",
        headers={"kid": "qa-round2-kid"},
    )


def _session_cookie_for_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, token: str
) -> str:
    """走**真实兑换路径**把给定令牌发下来，返回 `oa_session` 的 `Set-Cookie`。

    Args:
        client: 客户端。
        monkeypatch: pytest 补丁器。
        token: 令牌原文。

    Returns:
        str: `Set-Cookie` 头值。
    """
    payload = json.dumps({"access_token": token, "refresh_token": "rt", "expires_in": 300}).encode()
    _patch_idp(monkeypatch, [(200, payload, "application/json")])
    state, pending = _start(client)
    response = client.post(
        "/sso/complete",
        json={"code": "the-code", "state": state},
        headers={"Cookie": pending, **SAME_ORIGIN},
    )
    assert response.status_code == 200, response.text
    header = next(h for h in _set_cookies(response) if h.startswith(f"{SESSION_COOKIE_NAME}="))
    return header


def test_realistic_keycloak_token_value_is_raw_and_fits(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实长度令牌：值等于令牌原文（未 base64），`Set-Cookie` 在 4KB 内。

    Args:
        client: 客户端。
        monkeypatch: pytest 补丁器。
    """
    token = _keycloak_token()
    header = _session_cookie_for_token(client, monkeypatch, token)
    value = header.split(";", 1)[0].split("=", 1)[1]

    assert value == token, (
        "Cookie 值必须是令牌原文（QA-04 去掉了 base64）；"
        f"实际长度 {len(value)}，令牌 {len(token)}，"
        f"base64 长度 {len(base64.urlsafe_b64encode(token.encode()).decode().rstrip('='))}"
    )
    assert "." in value, "JWT 的三段以点分隔，能直接放进 Cookie 说明不需要再编码"
    assert len(header) == len(f"{SESSION_COOKIE_NAME}=") + len(token) + 55, (
        f"固定开销应只有属性部分，实际头长 {len(header)}"
    )
    assert len(header) < BROWSER_COOKIE_LIMIT, f"{len(header)} 字节已逼近/超过 4KB"


@pytest.mark.parametrize(
    ("token_chars", "expected_over"),
    [(3100, False), (4000, False), (4030, False), (4031, True), (4100, True)],
)
def test_cookie_size_exact_boundary_at_4031(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, token_chars: int, expected_over: bool
) -> None:
    """4KB 的**精确**边界：令牌 4030 字符时头长恰好 4096，4031 起越界。

    修复前（base64）边界在 3023，修复后在 4031。3100 这一档同时是"别退回编码"的哨兵：
    一旦有人把 `oa_raw` 改回 base64，它立刻越界报红。

    Args:
        client: 客户端。
        monkeypatch: pytest 补丁器。
        token_chars: 令牌字符数。
        expected_over: 期望是否越界。
    """
    header = _session_cookie_for_token(client, monkeypatch, "A" * token_chars)
    over = len(header) > BROWSER_COOKIE_LIMIT
    assert over is expected_over, f"令牌 {token_chars} → 头长 {len(header)}，越界={over}"


# ===========================================================================
# 5. QA-05 僵尸 Cookie 与空 Authorization
# ===========================================================================
def test_zombie_cookie_is_cleared_and_marked_expired(client: TestClient) -> None:
    """`oa_session=%%%garbage` → `{F,F,T}` + 清除头，不再是永久僵尸。

    Args:
        client: 客户端。
    """
    response = client.get(
        "/sso/session", headers={"Cookie": f"{SESSION_COOKIE_NAME}=%%%garbage", **SAME_ORIGIN}
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "authenticated": False,
        "expires_in": None,
        "refreshable": False,
        "expired": True,
    }
    cleared = {header.split("=", 1)[0] for header in _set_cookies(response)}
    assert cleared == {SESSION_COOKIE_NAME}, (
        f"僵尸 Cookie 必须被清且只清它：{_set_cookies(response)}"
    )


@pytest.mark.parametrize("authorization", ["", " ", "   ", "Bearer ", "bearer", " Bearer  "])
def test_blank_or_tokenless_authorization_falls_back_to_cookie(
    conf: Settings, monkeypatch: pytest.MonkeyPatch, authorization: str
) -> None:
    """空 / 纯空白 / 只有 `Bearer` 前缀 → 视为"没有该头"，回落 Cookie 轨并放行。

    用中间件证明服务端**真的收到了**这个头（否则"回落成功"与"头被 httpx 丢掉"
    无法区分，等于假绿），再断言请求以 Cookie 身份成功。

    Args:
        conf: 配置。
        monkeypatch: pytest 补丁器。
        authorization: 构造的 Authorization 头值。
    """
    seen: list[str | None] = []

    async def recorder(request: Request, call_next: Callable[[Request], Any]) -> Response:
        seen.append(request.headers.get("authorization"))
        return await call_next(request)

    app = create_app(conf)
    app.middleware("http")(recorder)
    token = make_token(settings=conf, expires_in=300)

    with TestClient(app, base_url="https://testserver") as scoped:
        response = scoped.get(
            BUSINESS_PATH,
            headers={
                "Authorization": authorization,
                "Cookie": _pair(SESSION_COOKIE_NAME, token),
                **SAME_ORIGIN,
            },
        )

    assert seen == [authorization], f"服务端收到的头是 {seen!r}（构造的是 {authorization!r}）"
    assert response.status_code == 200, (
        f"Authorization={authorization!r} 未回落 Cookie：{response.text}"
    )
    assert response.json()["data"]["user_id"] == "usr_test_user"


def test_non_bearer_scheme_still_rejected(client: TestClient, conf: Settings) -> None:
    """`Authorization: Basic ...` 仍 20002（用错鉴权方式不该被 Cookie 静默兜住）。

    Args:
        client: 客户端。
        conf: 配置。
    """
    response = client.get(
        BUSINESS_PATH,
        headers={
            "Authorization": "Basic dXNlcjpwYXNz",
            "Cookie": _pair(SESSION_COOKIE_NAME, make_token(settings=conf, expires_in=300)),
            **SAME_ORIGIN,
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


# ===========================================================================
# 7. 上线兼容性：老用户的 base64 Cookie 会不会被这次改动锁死
# ===========================================================================
def test_old_base64_session_cookie_upgrades_transparently(
    conf: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """升级前写下的 `oa_session=<base64(令牌)>` 仍能被引导到刷新，不需要重新登录。

    QA-04 把 `oa_session` 从"base64(令牌)"改成"令牌原文"，读侧也随之改为不解码。
    存量浏览器里还躺着**旧格式**的值：新代码会把那段 base64 当成令牌去验签 → 失败。
    关键问题是失败之后会怎样——若判成"未登录且不可刷新"，等于上线瞬间把所有人踢下线。
    实测：`{authenticated:false, refreshable:true, expired:true}` → 前端按判据去刷新 →
    200 并拿到新格式的两个 Cookie。**升级是一次静默的凭证格式迁移**，用户无感。

    Args:
        conf: 配置。
        monkeypatch: pytest 补丁器。
    """
    _patch_idp(
        monkeypatch,
        [(200, _token_payload(conf), "application/json")],
    )
    legacy_value = (
        build_set_cookie(
            SESSION_COOKIE_NAME,
            make_token(settings=conf, expires_in=300),
            max_age=28800,
            path="/",
            secure=True,
            encode=True,
        )
        .split(";", 1)[0]
        .split("=", 1)[1]
    )
    refresh_pair = _pair(REFRESH_COOKIE_NAME, "rt-legacy")

    with TestClient(create_app(conf), base_url="https://testserver") as scoped:
        session = scoped.get(
            "/sso/session",
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={legacy_value}; {refresh_pair}",
                **SAME_ORIGIN,
            },
        )
        assert session.json()["data"] == {
            "authenticated": False,
            "expires_in": None,
            "refreshable": True,
            "expired": True,
        }, f"旧格式 Cookie 的引导判据不对：{session.json()['data']}"

        refreshed = scoped.post("/sso/refresh", headers={"Cookie": refresh_pair, **SAME_ORIGIN})
        assert refreshed.status_code == 200, f"存量用户无法通过刷新迁移：{refreshed.text}"
        issued = {header.split("=", 1)[0] for header in _set_cookies(refreshed)}
        assert issued == {SESSION_COOKIE_NAME, REFRESH_COOKIE_NAME}


# ===========================================================================
# 8. 安全兜底：令牌含 Cookie 非法字符时不得静默写出被截断的头
# ===========================================================================
def test_token_with_illegal_cookie_chars_fails_closed_without_cookie(
    conf: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IdP 返回含空格/逗号的 `access_token` → 不下发任何 Cookie（宁可失败也不写坏头）。

    `build_set_cookie` 的 raw 路径会对非法字符抛 `ValueError`（否则写出一条会被浏览器
    截断的头，症状与 4KB 静默丢弃一模一样）。这里钉三件事：**fail-closed**
    （不是 200、零 `Set-Cookie`、响应体不含令牌原文）+ **错误码是 20002 而不是 500**。
    上游给了一份违约的令牌，不是平台内部故障；报 500 会触发错误告警、把排障方向
    引向自身代码（`_issue_session_cookies` 已把 `ValueError` 归成 20002）。

    Args:
        conf: 配置。
        monkeypatch: pytest 补丁器。
    """
    payload = json.dumps({"access_token": "bad token,with;spaces", "expires_in": 300}).encode()
    _patch_idp(monkeypatch, [(200, payload, "application/json")])

    with TestClient(
        create_app(conf), base_url="https://testserver", raise_server_exceptions=False
    ) as scoped:
        state, pending = _start(scoped)
        response = scoped.post(
            "/sso/complete",
            json={"code": "the-code", "state": state},
            headers={"Cookie": pending, **SAME_ORIGIN},
        )

    assert response.status_code != 200, f"违约令牌却返回 200：{response.text}"
    assert response.status_code == 401, (
        f"上游违约应归成 401/20002（登录失败），不是 500/30003（系统繁忙）："
        f"{response.status_code} {response.text}"
    )
    assert response.json()["code"] == 20002, response.text
    assert _set_cookies(response) == [], "不得下发（会被浏览器截断的）会话 Cookie"
    assert "bad token" not in response.text, f"响应体带出了上游令牌：{response.text}"


# ===========================================================================
# 6. 错误路径的 Set-Cookie 是否真的发出去了（多条是否被合并）
# ===========================================================================
def test_error_path_set_cookie_survives_and_is_not_merged(
    conf: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401 响应上必须真的带清除头，且多条时**逐条独立**、不被逗号合并。

    两件事分开验：
    1. `/sso/refresh`（invalid_grant）→ 恰好 1 条 `oa_refresh` 清除头。这条走
       `ApiError(headers=...)` → 异常处理器挂头；若实现退回成"往注入 Response append"，
       这里会读到 0 条（那正是工程师第一版修复的形态）。
    2. `/sso/logout` → 恰好 2 条独立清除头（`oa_session` 与 `oa_refresh` 各一）。
       若有人把多条塞进 `JSONResponse(headers=...)` 的映射里，Starlette 会合并成
       一条带逗号的头，`get_list` 只会返回 1 条，这里立刻报红。

    Args:
        conf: 配置。
        client: 客户端。
        monkeypatch: pytest 补丁器。
    """
    dead = [(400, json.dumps({"error": "invalid_grant"}).encode(), "application/json")]
    failed = _refresh_with(conf, client, monkeypatch, dead)
    error_headers = _set_cookies(failed)

    assert failed.status_code == 401
    assert len(error_headers) == 1, f"401 上应恰好 1 条 Set-Cookie，实际 {error_headers}"
    assert error_headers[0].startswith(f"{REFRESH_COOKIE_NAME}="), error_headers[0]
    assert "," not in error_headers[0].split(";")[0], "Cookie 值里不该出现逗号（会被解析成第二条）"

    logout = client.post(
        "/sso/logout",
        headers={
            "Cookie": "; ".join(
                [
                    _pair(SESSION_COOKIE_NAME, make_token(settings=conf, expires_in=300)),
                    _pair(REFRESH_COOKIE_NAME, "rt-x"),
                ]
            ),
            **SAME_ORIGIN,
        },
    )
    logout_headers = _set_cookies(logout)

    assert logout.status_code == 200, logout.text
    assert len(logout_headers) == 2, f"登出应下发 2 条独立清除头，实际 {logout_headers}"
    assert {h.split("=", 1)[0] for h in logout_headers} == {
        SESSION_COOKIE_NAME,
        REFRESH_COOKIE_NAME,
    }
    assert all("Max-Age=0" in h for h in logout_headers), logout_headers
