"""出站目标安全判定（H-02，审计 2026-11-07）。

只回答两个问题，不做业务翻译：

1. **这个 IP 允许连接吗？** —— 拒绝回环、私网、链路本地、CGNAT、保留段、
   组播、未指定地址；IPv6 同时拒绝 `fc00::/7`（含 `fd00::/8`）、`fe80::/10`、
   `ff00::/8`，并**先拆掉 `::ffff:` IPv4 映射再判定**（否则
   `::ffff:169.254.169.254` 会绕过 IPv4 规则）。
2. **这个主机名在白名单里吗？** —— 精确匹配或 `*.example.com` 通配；
   **空白名单 = 全拒绝**（安全默认，见 `docs/engineering-standards.md`）。

为什么解析后按 IP 判定：`https://evil.example.com` 在登记时可以是公网域名，
下一次解析却返回 `127.0.0.1`（DNS rebinding）。所以调用方必须
**先解析一次 → 用返回的 IP 判定 → 直接连这个 IP**，不能在"判定"和"连接"之间
再解析第二次。本模块的 `resolve_host_ip()` 就返回那一个被钉住的 IP。

本模块在 `core` 层，不依赖任何业务模块；业务侧只负责把
`UnsafeTargetError.reason` 翻译成对用户安全的文案。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Final

#: 自定义解析器（测试注入；生产用 `_default_resolver`）
Resolver = Callable[[str, int], Awaitable[Iterable[str]]]

#: RFC 6890 中"不能作为公网出站目标"的 IPv4 段。
#: 100.64.0.0/10 是运营商级 NAT（云厂商元数据/内部服务常用），必须拒绝。
_BLOCKED_V4: Final[tuple[ipaddress.IPv4Network, ...]] = tuple(
    ipaddress.IPv4Network(item)
    for item in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)

#: IPv6 拒绝段：未指定 / 回环 / 唯一本地（含 fd00::/8）/ 链路本地 / 组播 /
#: 文档段 / NAT64 合成段（`64:ff9b::/96` 会把 IPv4 目标藏进 IPv6 形态）。
_BLOCKED_V6: Final[tuple[ipaddress.IPv6Network, ...]] = tuple(
    ipaddress.IPv6Network(item)
    for item in (
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
        "2001:db8::/32",
        "64:ff9b::/96",
    )
)


class UnsafeTargetError(Exception):
    """目标不安全（原因码只用于内部诊断，不直接展示给用户）。

    Attributes:
        reason: `blocked_address` / `not_allowlisted` / `unresolvable` /
            `empty_resolution`。
    """

    def __init__(self, reason: str) -> None:
        """初始化。

        Args:
            reason: 原因码。
        """
        self.reason = reason
        super().__init__(reason)


def is_blocked_ip(value: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断 IP 是否属于禁止出站的地址。

    Args:
        value: 已解析的 IP（不接受字符串，避免调用方把主机名混进来）。

    Returns:
        bool: True 表示必须拒绝。
    """
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped is not None:
        # `::ffff:10.0.0.5` 是 IPv4 的另一种写法；不拆会绕过所有 IPv4 规则。
        return is_blocked_ip(value.ipv4_mapped)
    if isinstance(value, ipaddress.IPv4Address):
        return any(value in network for network in _BLOCKED_V4)
    return any(value in network for network in _BLOCKED_V6)


def parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """把字面量 IP 解析出来；主机名返回 None。

    Args:
        host: 主机名或 IP 字面量（可带方括号）。

    Returns:
        IPv4Address | IPv6Address | None: 解析结果。
    """
    text = host.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def host_matches(host: str, patterns: Iterable[str]) -> bool:
    """判断主机名是否命中白名单。

    规则（大小写不敏感）：
    - 精确匹配：`mcp.example.com`；
    - 子域通配：`*.example.com` 命中 `a.example.com`，**不命中** `example.com`
      （与 TLS 通配语义一致，避免把根域一起放开）；
    - 空项忽略；空列表不命中任何主机。

    Args:
        host: 主机名（不含端口）。
        patterns: 白名单项。

    Returns:
        bool: 是否命中。
    """
    target = host.strip().rstrip(".").lower()
    if not target:
        return False
    for raw in patterns:
        pattern = raw.strip().rstrip(".").lower()
        if not pattern:
            continue
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.com"
            if target.endswith(suffix) and target != suffix[1:]:
                return True
            continue
        if target == pattern:
            return True
    return False


def ensure_allowlisted(host: str, patterns: Iterable[str]) -> None:
    """空白名单/不命中一律拒绝（fail-closed）。

    Args:
        host: 主机名。
        patterns: 白名单项。

    Raises:
        UnsafeTargetError: `not_allowlisted`。
    """
    if not host_matches(host, patterns):
        raise UnsafeTargetError("not_allowlisted")


async def resolve_host_ip(
    host: str,
    port: int,
    *,
    resolver: Resolver | None = None,
    allow_loopback: bool = False,
) -> str:
    """解析主机名并返回**唯一被钉住的 IP**。

    安全口径：
    - 字面量 IP 直接判定，不做 DNS；
    - 解析出的**任一**地址被禁止 → 整体拒绝（fail-closed，防止"多 A 记录
      里混一个内网地址"绕过白名单）；
    - 优先返回 IPv4（连接行为更可预期），否则 IPv6；
    - `allow_loopback=True` 只放行回环，**不放行**其它私网/保留段；
      该开关由业务层限制在 `development/test` 且显式配置时才生效。

    Args:
        host: 主机名或 IP 字面量。
        port: 端口（仅用于解析）。
        resolver: 解析器（测试注入）。
        allow_loopback: 是否放行回环（本地开发用，默认关）。

    Returns:
        str: 可直接连接的 IP。

    Raises:
        UnsafeTargetError: 禁止地址/解析失败/解析为空。
    """
    literal = parse_ip(host)
    if literal is not None:
        if is_blocked_ip(literal) and not (allow_loopback and literal.is_loopback):
            raise UnsafeTargetError("blocked_address")
        return str(literal)

    try:
        raw_addresses = await (resolver or _default_resolver)(host, port)
    except (OSError, socket.gaierror) as exc:
        raise UnsafeTargetError("unresolvable") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for item in raw_addresses:
        parsed = parse_ip(str(item))
        if parsed is None:
            raise UnsafeTargetError("unresolvable")
        addresses.append(parsed)
    if not addresses:
        raise UnsafeTargetError("empty_resolution")
    for parsed in addresses:
        if is_blocked_ip(parsed) and not (allow_loopback and parsed.is_loopback):
            raise UnsafeTargetError("blocked_address")
    for parsed in addresses:
        if isinstance(parsed, ipaddress.IPv4Address):
            return str(parsed)
    return str(addresses[0])


async def _default_resolver(host: str, port: int) -> list[str]:
    """进程默认解析器（线程池执行，避免阻塞事件循环）。

    Args:
        host: 主机名。
        port: 端口。

    Returns:
        list[str]: 去重后的 IP 字符串。
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    return seen


__all__: list[str] = [
    "Resolver",
    "UnsafeTargetError",
    "ensure_allowlisted",
    "host_matches",
    "is_blocked_ip",
    "parse_ip",
    "resolve_host_ip",
]
