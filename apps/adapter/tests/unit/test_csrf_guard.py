"""Cookie 轨 CSRF 判定的矩阵测试（P0-5 / `app/core/csrf.py`）。

为什么敢用 `Sec-Fetch-Site` 而不做双提交
----------------------------------------

双提交需要一个 JS 可读的 CSRF Cookie + 请求头回传，而本方案的整个目的是**减少
JS 与凭证的接触面**——再加一个 JS 可读令牌会部分抵消收益。`Sec-Fetch-Site` 由
浏览器强制写入、页面脚本改不了，强度不低于双提交，且零额外状态。

矩阵重点在「`Origin` 兜底」与「两者都缺」两段：它们是老浏览器与非常规客户端
唯一会落到的分支，也是"看起来通过、实际不设防"最容易发生的地方。
"""

from __future__ import annotations

import pytest

from app.core.csrf import assert_same_site, check, is_write_method, origin_host

pytestmark = pytest.mark.unit


# ===========================================================================
# 1. 读方法一律放行
# ===========================================================================
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get", "head"])
def test_safe_methods_always_pass(method: str) -> None:
    """读请求不改状态，跨站读的危害由 SameSite 与 CORS 兜，不在这里判。"""
    assert check(method=method, sec_fetch_site="cross-site", origin=None, host="api.example.com")


def test_is_write_method() -> None:
    """非安全方法即写方法。"""
    assert is_write_method("POST") is True
    assert is_write_method("DELETE") is True
    assert is_write_method("PATCH") is True
    assert is_write_method("GET") is False


# ===========================================================================
# 2. Sec-Fetch-Site 主判定
# ===========================================================================
@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("same-origin", True),
        ("same-site", True),  # 私有化现场前后端分属不同子域时会是这个值
        ("cross-site", False),
        ("same-origin ", True),  # 容忍空白
        ("SAME-ORIGIN", True),  # 大小写不敏感
    ],
)
def test_sec_fetch_site_primary_judgement(site: str, expected: bool) -> None:
    """`Sec-Fetch-Site` 命中时以它为准，不再看 Origin。"""
    assert (
        check(
            method="POST",
            sec_fetch_site=site,
            origin="https://evil.example.com",  # 故意给一个不匹配的 Origin
            host="api.example.com",
        )
        is expected
    )


# ===========================================================================
# 3. Origin 兜底（Sec-Fetch-Site 缺失 / 取值 none）
# ===========================================================================
@pytest.mark.parametrize("site", [None, "", "none", "unexpected-value"])
@pytest.mark.parametrize(
    ("origin", "host", "expected"),
    [
        ("https://api.example.com", "api.example.com", True),
        ("http://api.example.com", "api.example.com", True),  # scheme 不参与比对
        ("https://api.example.com:8443", "api.example.com:8443", True),
        ("https://api.example.com", "API.EXAMPLE.COM", True),  # 大小写不敏感
        ("https://evil.example.com", "api.example.com", False),
        ("https://api.example.com.evil.com", "api.example.com", False),  # 后缀伪装
        (None, "api.example.com", False),  # 无 Origin → 写请求拒绝
        ("not-a-url", "api.example.com", False),  # Origin 非法 → 拒绝
        ("https://api.example.com", None, False),  # 无 Host → 拒绝
    ],
)
def test_origin_fallback_matrix(
    site: str | None, origin: str | None, host: str | None, expected: bool
) -> None:
    """`Sec-Fetch-Site` 不可用时用 Origin 与 Host 比对。

    两个"看起来能过但不该过"的用例要特别注意：
      - `api.example.com.evil.com`：后缀伪装，必须靠**全等**而非 `endswith` 拦住；
      - 无 Origin + 写方法：拒绝。放行它就是一条不设防的写路径。
    """
    assert check(method="POST", sec_fetch_site=site, origin=origin, host=host) is expected


# ===========================================================================
# 4. Origin 解析
# ===========================================================================
@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://app.example.com", "app.example.com"),
        ("http://127.0.0.1:3000", "127.0.0.1:3000"),
        ("https://app.example.com/", "app.example.com"),
        ("https://app.example.com/path", "app.example.com"),
        ("HTTPS://APP.EXAMPLE.COM", "app.example.com"),
        ("null", None),  # 沙箱 iframe 会发 "null"
        ("", None),
        (None, None),
    ],
)
def test_origin_host(origin: str | None, expected: str | None) -> None:
    """只取 host:port，丢弃 scheme 与路径。"""
    assert origin_host(origin) == expected


# ===========================================================================
# 5. 真实请求替身
# ===========================================================================
class _StubRequest:
    """最小请求替身：只为验证 `assert_same_site` 的头读取与委托。"""

    def __init__(self, method: str, headers: dict[str, str]) -> None:
        self.method = method
        self.headers = headers


def test_assert_same_site_delegates_to_check() -> None:
    """便捷函数与 `check` 判定一致（避免两处逻辑漂移）。"""
    allowed = _StubRequest(
        "POST",
        {"sec-fetch-site": "same-origin", "host": "api.example.com"},
    )
    refused = _StubRequest(
        "POST",
        {"sec-fetch-site": "cross-site", "host": "api.example.com"},
    )

    assert assert_same_site(allowed) is True
    assert assert_same_site(refused) is False


def test_assert_same_site_works_with_plain_dict_headers() -> None:
    """Starlette 的 `Headers` 与普通 dict 都要能读（get 接口即可）。"""
    request = _StubRequest(
        "DELETE",
        {"origin": "https://api.example.com", "host": "api.example.com"},
    )

    assert assert_same_site(request) is True
