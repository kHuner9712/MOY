"""Cookie 轨的 CSRF 判定（P0-5 路线 A）。

为什么 Cookie 轨需要它、Bearer 轨不需要
--------------------------------------

CSRF 的成立前提是「浏览器会自动附加凭证」。Bearer 令牌放在 `Authorization` 头里，
跨站页面**无法**替用户设置该头（自定义头受同源策略约束），所以 Bearer 轨天然免疫。
而 Cookie 会被浏览器自动附加，故 Cookie 轨必须独立判定请求来源。

判定顺序与理由
--------------

1. **`Sec-Fetch-Site` 主判定**（现代浏览器默认发送，不可被页面脚本伪造）：
   ``same-origin`` / ``same-site`` 放行，``cross-site`` 拒绝。
2. **`Origin` 兜底**（`Sec-Fetch-Site` 缺失时的老浏览器）：把 `Origin` 的 host
   与请求的 `Host` 比对。
3. **两者都缺**：读请求放行、写请求**拒绝**。

第 3 条为什么写请求要拒绝：`Origin` 缺失 + 写方法 + 带 Cookie，这个组合在现代
浏览器里不该出现；拒绝的代价是"某些非浏览器客户端需要显式带 Origin"（而这本来就
是它们该做的），放行的代价是"一条不设防的写路径"。宁可误拒，不可误放。

为什么不用双提交 Cookie（double-submit）
----------------------------------------

双提交需要一个 JS 可读的 CSRF Cookie + 请求头回传。但本方案的整个目的是**减少
JS 与凭证的接触面**，再加一个 JS 可读的令牌会部分抵消收益。`Sec-Fetch-Site` 是
浏览器强制的、页面脚本改不了的头，强度不低于双提交，且零额外状态。
"""

from __future__ import annotations

from typing import Protocol

#: 不做 CSRF 判定的方法：读请求不改状态，跨站读的危害由 `SameSite` 与 CORS 兜。
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

#: `Sec-Fetch-Site` 中视为"同站"的取值。
#:
#: `same-site` 放行是有意的：私有化现场前端与适配层可能分属不同子域
#: （如 `app.example.com` 与 `api.example.com`），此时 `Sec-Fetch-Site` 报
#: `same-site` 而非 `same-origin`。拒绝它会把正常部署打死。
SAME_SITE_VALUES: frozenset[str] = frozenset({"same-origin", "same-site"})


class RequestLike(Protocol):
    """判定所需的最小请求契约（便于单测不构造真实 `Request`）。"""

    @property
    def headers(self) -> object: ...

    @property
    def method(self) -> str: ...


def _header(headers: object, name: str) -> str | None:
    """从 headers 映射里取头（大小写不敏感）。

    Args:
        headers: 类字典对象（Starlette `Headers` 或普通 dict）。
        name: 头名（小写）。

    Returns:
        str | None: 头值。
    """
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    return str(value) if value is not None else None


def origin_host(origin: str | None) -> str | None:
    """从 `Origin` 头取出 host:port。

    Args:
        origin: 形如 `https://app.example.com` 的值。

    Returns:
        str | None: `app.example.com` 形式；无法解析返回 None。
    """
    if not origin:
        return None
    text = origin.strip()
    if "://" not in text:
        return None
    _, _, rest = text.partition("://")
    host = rest.split("/", 1)[0].strip().lower()
    return host or None


def is_write_method(method: str) -> bool:
    """判断是否写方法。

    Args:
        method: HTTP 方法。

    Returns:
        bool: 非安全方法返回 True。
    """
    return method.upper() not in SAFE_METHODS


def check(
    *,
    method: str,
    sec_fetch_site: str | None,
    origin: str | None,
    host: str | None,
) -> bool:
    """判定该请求是否允许携带 Cookie 凭证。

    纯函数：所有输入显式传入，因此可以在单测里穷举矩阵，不依赖请求对象。

    Args:
        method: HTTP 方法。
        sec_fetch_site: `Sec-Fetch-Site` 头（可为 None）。
        origin: `Origin` 头（可为 None）。
        host: `Host` 头（可为 None）。

    Returns:
        bool: 允许返回 True。
    """
    if not is_write_method(method):
        return True

    site = (sec_fetch_site or "").strip().lower()
    if site in SAME_SITE_VALUES:
        return True
    if site == "cross-site":
        return False

    # 走到这里：`Sec-Fetch-Site` 缺失或取值异常（含 `none`）→ 用 Origin 兜底。
    source_host = origin_host(origin)
    if source_host and host:
        return source_host == host.strip().lower()
    # 两者都不可用：写请求拒绝（见模块 docstring 第 3 条）。
    return False


def assert_same_site(request: RequestLike) -> bool:
    """对真实请求做 CSRF 判定。

    Args:
        request: FastAPI / Starlette 请求对象（或任何符合 `RequestLike` 的替身）。

    Returns:
        bool: 允许携带 Cookie 凭证返回 True；调用方在 False 时应抛 20003。

    Notes:
        不在这里抛 `ApiError` 是为了让 `security.py` 决定错误码与文案——
        csrf 模块只回答"允不允许"，错误语义归鉴权层。
    """
    headers = request.headers
    return check(
        method=request.method,
        sec_fetch_site=_header(headers, "sec-fetch-site"),
        origin=_header(headers, "origin"),
        host=_header(headers, "host"),
    )


__all__: list[str] = [
    "SAFE_METHODS",
    "SAME_SITE_VALUES",
    "RequestLike",
    "assert_same_site",
    "check",
    "is_write_method",
    "origin_host",
]
