"""会话 Cookie 的属性决策与序列化（P0-5 路线 A：令牌不进 JavaScript）。

为什么要单独成模块
------------------

Cookie 的 `Secure` / `SameSite` / `Path` / `Max-Age` 决定的是**安全边界**，不是业务
逻辑。把它们集中在一处，才能用纯函数把「HTTPS 现场 / HTTP 现场」两种矩阵穷举测试；
散落在端点里就只能靠集成测试碰运气，而这类属性错一个（尤其是 `Secure` 静默缺席）
不会报错、只会让会话在明文里漂。

三个 Cookie 的职责与作用域
--------------------------

===========================  ==========  ==============  ===================
Cookie                        Path        Max-Age         为什么是这个作用域
===========================  ==========  ==============  ===================
``oa_session``（访问令牌）    ``/``       8 小时          业务请求都要带
``oa_refresh``（刷新令牌）    ``/sso``    8 小时          只给 `/sso/refresh` 用，
                                                          缩小暴露面：任一业务
                                                          路径的请求都不会携带它
``oa_oidc_pending``（PKCE）   ``/sso``    10 分钟         只在一次登录往返内有效
===========================  ==========  ==============  ===================

`oa_refresh` 的 `Path=/sso` 是关键设计：刷新令牌是三者中价值最高的（能换取新的
访问令牌），把它限制在单个路径前缀下，等于把「每个业务请求都顺带暴露一次刷新令牌」
降成「只有刷新接口能看到它」。

`SameSite=Lax` 而不是 `Strict`
------------------------------

OIDC 回跳是**顶级导航**（浏览器从 Keycloak 域跳回本域）。`Strict` 下这条导航不带
Cookie，`oa_oidc_pending` 取不到 → state 校验失败 → 登录必失败。`Lax` 恰好放行
顶级 GET 导航而拦住跨站子请求，是这类流程的标准选择。

CSRF 的防线不靠 `SameSite`
--------------------------

`Lax` 能挡住大部分跨站表单提交，但**不是全部**（同站不同源、以及 `SameSite` 被
老浏览器忽略的情况）。故写接口仍走 `csrf.assert_same_site()` 独立判定，见
`app/core/csrf.py`。两条防线职责不同：`SameSite` 是浏览器侧默认值，`Sec-Fetch-Site`
是服务端显式判据。
"""

from __future__ import annotations

import base64
import json
from typing import Any

#: 访问令牌 Cookie 名（`Path=/`）
SESSION_COOKIE_NAME: str = "oa_session"

#: 刷新令牌 Cookie 名（`Path=/sso`）
REFRESH_COOKIE_NAME: str = "oa_refresh"

#: 登录往返期的 PKCE 暂存（`Path=/sso`）
PENDING_COOKIE_NAME: str = "oa_oidc_pending"

#: 访问令牌 Cookie 的作用域
SESSION_COOKIE_PATH: str = "/"

#: 刷新令牌与 PKCE 暂存的作用域（只覆盖会话端点）
SSO_COOKIE_PATH: str = "/sso"

#: 会话绝对上限（8 小时）：与服务端会话的绝对上限、Keycloak 的
#: `ssoSessionMaxLifespan` 保持同一量级。三者独立生效，取最早到期的那个。
SESSION_MAX_AGE_SECONDS: int = 8 * 60 * 60

#: PKCE 暂存的有效期：一次登录往返通常 < 2 分钟，给 10 分钟是留给用户
#: 在 Keycloak 页面上输密码、过二次验证的时间。
PENDING_MAX_AGE_SECONDS: int = 10 * 60

#: 值**不需要** base64url 编码的 Cookie 名（QA-04）。
#:
#: 为什么 `oa_session` 特殊：它的值是访问令牌，也就是 base64url 的 JWT
#: （`A-Za-z0-9` + `-` + `_` + `.`）。这些字符**全部**落在 RFC 6265 的 cookie-octet
#: 允许集内（`.` = 0x2E ∈ `%x2D-3A`，`-`/`_` 同理），可以直接放进 Cookie 头部，
#: 不需要再包一层编码。
#:
#: 为什么必须去掉这层编码：base64 会把值放大 4/3。QA 实测一份 **12 角色的真实
#: 令牌 2202 字符 → `Set-Cookie` 3002 字节**，边界（令牌 3023 字符）已经逼近浏览器
#: 4096 字节上限；企业客户角色一多就越界，而**越界是静默丢弃**——表现为"登录成功
#: 但下一页又未登录"，是现场最难查的一类故障。不编码后同一令牌 2256 字节，边界
#: 推到 4 KB 以上。
#:
#: `oa_oidc_pending` **必须**编码：它是 JSON，含 `{` `}` `"` `:` 与逗号，直接写会
#: 破坏 `Set-Cookie` 头部（逗号在 RFC 6265 里是分隔符）。`oa_refresh` 的值来自
#: 身份平台、格式不受我们控制，保守起见也走编码。
RAW_VALUE_COOKIES: frozenset[str] = frozenset({SESSION_COOKIE_NAME})


def _should_encode(name: str, encode: bool | None) -> bool:
    """判断某个 Cookie 的值是否需要 base64url 编码。

    默认（`encode is None`）**按 Cookie 名判定**，而不是让每个调用点自己传：
    编码方式与读取方式必须由**同一处**决定，否则"写入时编码、读取时当原文"
    （或反过来）会得到一个能登录但读不出值的 Cookie——而症状只是"登录后又未登录"。
    显式传 `encode=True/False` 只用于构造畸形输入的测试。

    Args:
        name: Cookie 名。
        encode: 显式指定；None 表示按名字判定。

    Returns:
        bool: 需要编码返回 True。
    """
    if encode is not None:
        return encode
    return name not in RAW_VALUE_COOKIES


def _is_cookie_octet_safe(value: str) -> bool:
    """判断文本能否**原样**放进 Cookie 值（RFC 6265 §4.1.1 cookie-octet）。

    允许集：`%x21` / `%x23-2B` / `%x2D-3A` / `%x3C-5B` / `%x5D-7E`。
    被排除的正是会破坏头部的字符：`"` `,` `;` `\\`、空格与控制字符。

    Args:
        value: 原始值。

    Returns:
        bool: 全部字符都在允许集内返回 True。
    """
    for char in value:
        code = ord(char)
        allowed = (
            code == 0x21
            or 0x23 <= code <= 0x2B
            or 0x2D <= code <= 0x3A
            or 0x3C <= code <= 0x5B
            or 0x5D <= code <= 0x7E
        )
        if not allowed:
            return False
    return True


def is_secure_from_base_url(public_base_url: str) -> bool:
    """判断会话 Cookie 是否应带 `Secure`。

    **只读配置，不读请求头**：若采信 `X-Forwarded-Proto`，任何能伪造该头的客户端
    都能决定 `Secure` 是否生效——而它恰恰是"会话能否在明文 HTTP 上被嗅探"的开关。

    这也是「前置网关终止 TLS」（部署指南 12.5 形态 A）下必须把
    `OA_PUBLIC_BASE_URL` 写成 `https://...` 的原因：容器内看到的是 http，
    但浏览器实际走的是 https，判定必须以后者为准。

    Args:
        public_base_url: 对外基址（浏览器地址栏里的那个地址）。

    Returns:
        bool: 基址以 `https://` 开头返回 True。
    """
    return public_base_url.strip().lower().startswith("https://")


def _encode_value(value: str) -> str:
    """把任意文本编码成可安全放进 Cookie 值的 ASCII 串。

    RFC 6265 的 cookie-value 不允许 `;`、`,`、空格与控制字符。PKCE 的 verifier 是
    base64url（本身安全），但 `oa_oidc_pending` 存的是 JSON（含 `{}`、`:`、引号），
    故走 base64url。

    哪些 Cookie **不走**这里由 `RAW_VALUE_COOKIES` 决定（`oa_session` 的 JWT 本身就是
    允许字符集，再编码只会放大 4/3 并逼近浏览器 4 KB 上限，见 QA-04）。

    Args:
        value: 原始文本。

    Returns:
        str: 只含 `A-Za-z0-9-_` 的串。
    """
    raw = value.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_value(encoded: str) -> str | None:
    """还原 `_encode_value` 的编码。

    Args:
        encoded: 编码后的串。

    Returns:
        str | None: 解码结果；格式非法时返回 None（调用方按"没有该 Cookie"处理，
            不要抛异常——攻击者可以随意构造 Cookie 值，抛异常等于给了一条 DoS 面）。
    """
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def build_set_cookie(
    name: str,
    value: str,
    *,
    max_age: int,
    path: str,
    secure: bool,
    http_only: bool = True,
    same_site: str = "Lax",
    encode: bool | None = None,
) -> str:
    """构造 `Set-Cookie` 响应头值。

    Args:
        name: Cookie 名。
        value: 原始值。
        max_age: 有效期（秒）。
        path: 作用域路径。
        secure: 是否带 `Secure`（由 `is_secure_from_base_url` 决定）。
        http_only: 是否禁止 JS 读取。**默认 True**，只有需要 JS 读的 Cookie 才关。
        same_site: `Lax` / `Strict` / `None`。
        encode: 是否 base64url 编码值。默认 `None` = **按 Cookie 名判定**：
            `oa_session` 不编码（值是 JWT，本身符合 cookie-octet，编码会膨胀 33%
            并逼近 4 KB 上限，见 `RAW_VALUE_COOKIES`），其余 Cookie 编码。

    Returns:
        str: 可直接放进响应头的字符串。

    Raises:
        ValueError: 该 Cookie 走不编码路径，但值里含 cookie-octet 之外的字符。
            这是**格式违约**（访问令牌必须是 base64url 的 JWT，这也是无状态验签的
            前提）。宁可写日志/抛错，也不要写出一个会被浏览器截断或静默丢弃的
            Cookie——那种失败没有任何错误码，只会表现为"登录成功但下一页未登录"。
    """
    if _should_encode(name, encode):
        encoded = _encode_value(value)
    elif _is_cookie_octet_safe(value):
        encoded = value
    else:
        raise ValueError(
            f"{name} 的值含 Cookie 不允许的字符（令牌必须是 base64url 的 JWT；"
            "若换成不透明令牌，请把它加回编码路径）"
        )
    parts = [
        f"{name}={encoded}",
        f"Path={path}",
        f"Max-Age={max_age}",
        f"SameSite={same_site}",
    ]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie(name: str, *, path: str, secure: bool) -> str:
    """构造清除 Cookie 的 `Set-Cookie`（`Max-Age=0` 立即失效）。

    属性必须与下发时**一致**（尤其 `Path`）：浏览器按 (name, domain, path) 三元组
    匹配，`Path` 写错等于删了个不存在的 Cookie，原 Cookie 继续有效。

    Args:
        name: Cookie 名。
        path: 与下发时相同的路径。
        secure: 与下发时相同的 `Secure`。

    Returns:
        str: 清除用的响应头值。
    """
    return build_set_cookie(name, "", max_age=0, path=path, secure=secure)


def parse_cookie_header(header: str | None) -> dict[str, str]:
    """解析 `Cookie` 请求头。

    只做最朴素的分割，不做 URI 解码：我们的值都是 `_encode_value` 产出的
    base64url，解码由 `_decode_value` 负责。这样职责清晰——这一层只关心
    "有哪些键值对"，不关心值的语义。

    Args:
        header: `Cookie` 头原文（可为 None）。

    Returns:
        dict[str, str]: 键值对；同名键以**首次出现**为准（RFC 6265 §5.4 的
            处理顺序，也让攻击者无法用重复键覆盖合法值）。
    """
    if not header:
        return {}
    result: dict[str, str] = {}
    for chunk in header.split(";"):
        name, sep, raw_value = chunk.partition("=")
        key = name.strip()
        if not sep or not key:
            continue
        result.setdefault(key, raw_value.strip())
    return result


def encode_pending(payload: dict[str, Any]) -> str:
    """把登录暂存（state / verifier / 回跳路径）编码成 Cookie 值。

    Args:
        payload: 待暂存的数据。

    Returns:
        str: 编码后的串。
    """
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decode_pending(encoded: str) -> dict[str, Any] | None:
    """解码登录暂存。

    Args:
        encoded: `_decode_value` 还原出的原文。

    Returns:
        dict[str, Any] | None: 解析成功返回 dict；格式非法返回 None
            （同 `_decode_value` 的理由：不可信输入不抛异常）。
    """
    try:
        loaded = json.loads(encoded)
    except (json.JSONDecodeError, TypeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def read_cookie(header: str | None, name: str, *, encode: bool | None = None) -> str | None:
    """从 `Cookie` 头取出指定 Cookie 的**原始**值（必要时先解码）。

    Args:
        header: `Cookie` 头原文。
        name: 目标 Cookie 名。
        encode: 是否按 base64url 解码。默认 `None` = 按 Cookie 名判定（与
            `build_set_cookie` 同一套判据，避免"写时编码、读时当原文"）。

    Returns:
        str | None: 解码后的值；不存在或编码非法时返回 None。

    Notes:
        编码非法返回 None 而不是抛异常：攻击者可以随意构造 Cookie 值，
        抛异常等于给了一条 DoS 面。调用方如需区分"没有这个 Cookie"与"有但不可用"，
        用 `parse_cookie_header` 看键是否存在。
    """
    raw = parse_cookie_header(header).get(name)
    if raw is None:
        return None
    if not _should_encode(name, encode):
        return raw
    return _decode_value(raw)


__all__: list[str] = [
    "PENDING_COOKIE_NAME",
    "PENDING_MAX_AGE_SECONDS",
    "RAW_VALUE_COOKIES",
    "REFRESH_COOKIE_NAME",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_PATH",
    "SESSION_MAX_AGE_SECONDS",
    "SSO_COOKIE_PATH",
    "build_clear_cookie",
    "build_set_cookie",
    "decode_pending",
    "encode_pending",
    "is_secure_from_base_url",
    "parse_cookie_header",
    "read_cookie",
]
