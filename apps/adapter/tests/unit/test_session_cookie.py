"""会话 Cookie 属性的矩阵测试（P0-5 / `app/core/session_cookie.py`）。

为什么这组测试必须穷举
----------------------

Cookie 属性错一个**不会报错**：`Secure` 少了只是"会话可被明文嗅探"，`Path` 宽了
只是"刷新令牌被更多请求顺带发送"，`SameSite` 严了只是"登录悄悄失败"。它们全都
不抛异常、不写日志，只能靠断言钉住。

因此这里不用 happy path，而是把「HTTPS / HTTP 现场 × 三个 Cookie」的矩阵摊开测。
"""

from __future__ import annotations

import pytest

from app.core.session_cookie import (
    PENDING_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
    SSO_COOKIE_PATH,
    build_clear_cookie,
    build_set_cookie,
    decode_pending,
    encode_pending,
    is_secure_from_base_url,
    parse_cookie_header,
    read_cookie,
)

pytestmark = pytest.mark.unit


# ===========================================================================
# 1. Secure 的判定基准
# ===========================================================================
@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://moy.example.com", True),
        ("HTTPS://moy.example.com", True),  # 大小写不敏感
        ("https://moy.example.com/", True),
        ("  https://moy.example.com  ", True),  # 前后空白
        ("http://moy.example.com", False),
        ("http://192.168.1.50:8080", False),
        ("http://localhost:3000", False),
        ("", False),
        ("   ", False),
    ],
)
def test_secure_derived_from_base_url_scheme(base_url: str, expected: bool) -> None:
    """`Secure` 只由对外基址的 scheme 决定。

    这是"不读请求头"的落地：若采信 `X-Forwarded-Proto`，任何能伪造该头的客户端
    就能决定会话能否在明文 HTTP 上被嗅探。
    """
    assert is_secure_from_base_url(base_url) is expected


# ===========================================================================
# 2. Set-Cookie 的属性矩阵
# ===========================================================================
def test_session_cookie_has_httponly_and_root_path() -> None:
    """访问令牌 Cookie：HttpOnly + Path=/ + SameSite=Lax。"""
    header = build_set_cookie(
        SESSION_COOKIE_NAME,
        "token-value",
        max_age=28800,
        path=SESSION_COOKIE_PATH,
        secure=True,
    )

    assert header.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "HttpOnly" in header, "访问令牌必须 HttpOnly，否则 JS 可读（P0-5 的全部意义所在）"
    assert "Path=/" in header
    assert "SameSite=Lax" in header, "Lax 是 OIDC 顶级导航回跳的必需值"
    assert "Secure" in header
    assert "Max-Age=28800" in header


def test_refresh_cookie_is_scoped_to_sso_path() -> None:
    """刷新令牌 Cookie 必须限制在 `/sso` 下。

    它是三个 Cookie 里价值最高的（能换新的访问令牌）。`Path=/sso` 让每个业务请求
    都不携带它——把"每次请求顺带暴露刷新令牌"降成"只有刷新接口能看到"。
    """
    header = build_set_cookie(
        REFRESH_COOKIE_NAME,
        "refresh-value",
        max_age=28800,
        path=SSO_COOKIE_PATH,
        secure=True,
    )

    assert "Path=/sso" in header
    assert "HttpOnly" in header


def test_pending_cookie_is_also_scoped_to_sso_path() -> None:
    """PKCE 暂存同样限制在 `/sso`，且有效期短。"""
    header = build_set_cookie(
        PENDING_COOKIE_NAME,
        '{"state":"s","verifier":"v"}',
        max_age=600,
        path=SSO_COOKIE_PATH,
        secure=False,
    )

    assert "Path=/sso" in header
    assert "Max-Age=600" in header
    assert "Secure" not in header, "HTTP 现场不应带 Secure（浏览器会丢弃该 Cookie）"


def test_secure_absent_on_http_site() -> None:
    """HTTP 现场不带 `Secure`：带了浏览器直接丢弃，登录会"无声失败"。"""
    header = build_set_cookie(
        SESSION_COOKIE_NAME,
        "v",
        max_age=1,
        path="/",
        secure=False,
    )

    assert "Secure" not in header


def test_httponly_can_be_disabled_explicitly() -> None:
    """需要 JS 读的 Cookie 才关 HttpOnly——本方案里没有这种 Cookie，但保留能力。"""
    header = build_set_cookie(
        SESSION_COOKIE_NAME,
        "v",
        max_age=1,
        path="/",
        secure=True,
        http_only=False,
    )

    assert "HttpOnly" not in header


def test_cookie_value_is_encoded_not_raw() -> None:
    """值必须编码：JSON 里的 `{` `"` `:` 直接写进 Cookie 会破坏头部。"""
    header = build_set_cookie(
        PENDING_COOKIE_NAME,
        '{"a":"b;c"}',
        max_age=1,
        path="/sso",
        secure=False,
    )

    value = header.split("=", 1)[1].split(";", 1)[0]
    assert ";" not in value
    assert "{" not in value
    assert '"' not in value


# ===========================================================================
# 3. 清除 Cookie 必须与下发时的属性一致
# ===========================================================================
def test_clear_cookie_matches_original_path_and_secure() -> None:
    """清除时 Path / Secure 必须与下发一致。

    浏览器按 (name, domain, path) 三元组匹配 Cookie。`Path` 写错等于删了一个
    不存在的 Cookie，原 Cookie 继续有效——"登出了但令牌还在"这类问题的经典成因。
    """
    header = build_clear_cookie(REFRESH_COOKIE_NAME, path=SSO_COOKIE_PATH, secure=True)

    assert "Max-Age=0" in header
    assert "Path=/sso" in header
    assert "Secure" in header
    assert "HttpOnly" in header


# ===========================================================================
# 4. Cookie 头解析
# ===========================================================================
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, {}),
        ("", {}),
        ("a=1", {"a": "1"}),
        ("a=1; b=2", {"a": "1", "b": "2"}),
        ("  a=1 ;  b=2  ", {"a": "1", "b": "2"}),
        ("novalue", {}),  # 没有等号：忽略
        ("=orphan", {}),  # 空键：忽略
    ],
)
def test_parse_cookie_header(header: str | None, expected: dict[str, str]) -> None:
    """只做朴素分割，不做 URI 解码（值的语义由 `read_cookie` 负责）。"""
    assert parse_cookie_header(header) == expected


def test_parse_cookie_header_first_wins_on_duplicate() -> None:
    """同名键以首次出现为准：让攻击者无法用重复键覆盖合法值。"""
    assert parse_cookie_header("a=good; a=evil") == {"a": "good"}


# ===========================================================================
# 5. 值编解码的往返与不可信输入
# ===========================================================================
@pytest.mark.parametrize(
    "value",
    ["simple", "with space", "中文值", '{"json":true}', "a=b;c", ""],
)
def test_value_roundtrip(value: str) -> None:
    """任意文本都能往返（含中文与分隔符）。"""
    header = build_set_cookie("c", value, max_age=1, path="/", secure=False)
    encoded = header.split("=", 1)[1].split(";", 1)[0]
    assert read_cookie(f"c={encoded}", "c") == value


def test_read_cookie_returns_none_for_missing() -> None:
    """不存在的 Cookie 返回 None。"""
    assert read_cookie("a=1", "b") is None
    assert read_cookie(None, "a") is None


def test_read_cookie_returns_none_for_malformed_value() -> None:
    """非法 base64 返回 None 而不是抛异常。

    攻击者可以随意构造 Cookie 值；抛异常等于给了一条 DoS 面。
    """
    assert read_cookie("c=!!!not-base64!!!", "c") is None


# ===========================================================================
# 6. PKCE 暂存的编解码
# ===========================================================================
def test_pending_roundtrip() -> None:
    """state / verifier / 回跳路径要能原样取回。"""
    payload = {"state": "s-1", "verifier": "v-1", "next": "/workbench"}
    restored = decode_pending(encode_pending(payload))

    assert restored == payload


def test_decode_pending_rejects_malformed_json() -> None:
    """非法 JSON（含非对象）返回 None，不抛异常。"""
    assert decode_pending("not json") is None
    assert decode_pending("[1,2,3]") is None
    assert decode_pending('"just a string"') is None
