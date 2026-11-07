"""出站 SSRF 防护测试（H-02，审计 2026-11-07）。

覆盖三层：
1. `core/net_safety.py` 的地址判定（IPv4/IPv6/`::ffff:` 映射/白名单通配）；
2. 登记接口的默认拒绝（空白名单 = 全拒绝、内网字面量 IP 拒绝）；
3. 运行期钉扎（解析一次 → 连该 IP；禁止地址一律 `connect_failed`，不泄漏细节）。
"""

from __future__ import annotations

import base64

import pytest

from app.business.credential_service import CredentialResolver
from app.business.mcp_service import TRANSPORT_HTTP, McpServerService, McpTarget
from app.clients.mcp_client import McpConnection, McpError, _pinned_request
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.net_safety import (
    UnsafeTargetError,
    host_matches,
    is_blocked_ip,
    parse_ip,
    resolve_host_ip,
)
from app.db.repositories.credential_repo import InMemoryCredentialStore
from app.db.repositories.mcp_repo import InMemoryMcpServerStore
from app.services.mcp_runner import McpRunner

pytestmark = pytest.mark.unit

_TEST_KEY = base64.b64encode(b"0" * 32).decode()


def _settings(**overrides: object) -> Settings:
    """构造测试配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置。
    """
    values: dict[str, object] = {
        "env": "test",
        "business_store": "memory",
        "credential_encryption_key": _TEST_KEY,
        "mcp_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _service(settings: Settings) -> McpServerService:
    """构造服务（独立内存存储）。

    Args:
        settings: 配置。

    Returns:
        McpServerService: 服务。
    """
    resolver = CredentialResolver(settings=settings, store=InMemoryCredentialStore())
    return McpServerService(
        settings=settings,
        store=InMemoryMcpServerStore(),
        credentials=resolver,
    )


def _payload(endpoint: str) -> dict[str, object]:
    """构造登记载荷。

    Args:
        endpoint: 地址。

    Returns:
        dict[str, object]: 载荷。
    """
    return {
        "name": "demo",
        "transport": TRANSPORT_HTTP,
        "endpoint": endpoint,
        "allowed_tools": ["echo"],
        "enabled": True,
    }


@pytest.mark.parametrize(
    ("value", "blocked"),
    [
        ("127.0.0.1", True),
        ("10.0.0.5", True),
        ("169.254.169.254", True),  # 云元数据
        ("100.64.0.1", True),  # CGNAT
        ("172.16.0.1", True),
        ("192.168.1.1", True),
        ("0.0.0.0", True),  # noqa: S104 - 测试数据，不是真实监听地址
        ("224.0.0.1", True),
        ("::1", True),
        ("fe80::1", True),
        ("fd00::1", True),  # 唯一本地（审计点名的段）
        ("ff02::1", True),
        ("::ffff:127.0.0.1", True),  # IPv4 映射必须先拆包
        ("::ffff:169.254.169.254", True),
        ("8.8.8.8", False),
        ("2606:4700:4700::1111", False),
    ],
)
def test_is_blocked_ip(value: str, blocked: bool) -> None:
    """内网/保留段全部拒绝，公网地址放行。"""
    parsed = parse_ip(value)
    assert parsed is not None
    assert is_blocked_ip(parsed) is blocked


def test_host_matches_exact_and_wildcard() -> None:
    """白名单：精确、子域通配、根域不命中、空白名单全拒绝。"""
    assert host_matches("mcp.example.com", ["mcp.example.com"]) is True
    assert host_matches("A.Example.COM.", ["*.example.com"]) is True
    assert host_matches("example.com", ["*.example.com"]) is False
    assert host_matches("evil.example.com.attacker.net", ["*.example.com"]) is False
    assert host_matches("anything", []) is False


async def test_resolve_rejects_mixed_private_answer() -> None:
    """同一主机名解析出公网+内网：整体拒绝（fail-closed）。"""

    async def resolver(host: str, port: int) -> list[str]:
        return ["8.8.8.8", "10.0.0.9"]

    with pytest.raises(UnsafeTargetError) as exc:
        await resolve_host_ip("evil.example.com", 443, resolver=resolver)
    assert exc.value.reason == "blocked_address"


async def test_resolve_pins_first_public_ipv4() -> None:
    """公网多地址时钉住第一个 IPv4，返回的字面量就是连接目标。"""

    async def resolver(host: str, port: int) -> list[str]:
        return ["2606:4700::1111", "8.8.4.4"]

    assert await resolve_host_ip("mcp.example.com", 443, resolver=resolver) == "8.8.4.4"


async def test_resolve_loopback_only_with_explicit_flag() -> None:
    """回环默认拒绝；显式开关（仅测试/开发）才放行。"""
    with pytest.raises(UnsafeTargetError) as exc:
        await resolve_host_ip("127.0.0.1", 80)
    assert exc.value.reason == "blocked_address"
    assert await resolve_host_ip("127.0.0.1", 80, allow_loopback=True) == "127.0.0.1"


async def test_resolve_rejects_empty_and_unresolvable() -> None:
    """解析为空/失败都必须拒绝，不能回落成"随便连"。"""

    async def empty(host: str, port: int) -> list[str]:
        return []

    async def broken(host: str, port: int) -> list[str]:
        raise OSError("dns down")

    with pytest.raises(UnsafeTargetError) as exc:
        await resolve_host_ip("mcp.example.com", 443, resolver=empty)
    assert exc.value.reason == "empty_resolution"
    with pytest.raises(UnsafeTargetError) as exc:
        await resolve_host_ip("mcp.example.com", 443, resolver=broken)
    assert exc.value.reason == "unresolvable"


async def test_blank_allowlist_denies_everything() -> None:
    """空白名单 = 全拒绝（安全默认）。"""
    service = _service(_settings(mcp_allow_insecure_http=True, mcp_allowed_hosts=""))
    with pytest.raises(ApiError) as exc:
        await service.register_server("t1", _payload("https://mcp.example.com/mcp"))
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert "不在允许范围" in exc.value.message


async def test_allowlisted_host_passes() -> None:
    """白名单命中即可登记（https，无需 DNS）。"""
    service = _service(_settings(mcp_allowed_hosts="*.example.com"))
    record = await service.register_server("t1", _payload("https://mcp.example.com/mcp"))
    assert record.endpoint.endswith("/mcp")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1/mcp",
        "https://10.0.0.5/mcp",
        "https://169.254.169.254/mcp",
        "https://[::1]/mcp",
        "https://[fd00::1]/mcp",
        "https://[::ffff:169.254.169.254]/mcp",
        "https://localhost/mcp",
    ],
)
async def test_private_literal_ip_is_rejected_even_if_allowlisted(endpoint: str) -> None:
    """白名单只决定"哪个域"，**不能**把内网字面量 IP 变成合法目标。"""
    settings = _settings(
        mcp_allowed_hosts=(
            "127.0.0.1,10.0.0.5,169.254.169.254,::1,fd00::1,localhost,::ffff:169.254.169.254"
        ),
        mcp_allow_loopback=False,
    )
    service = _service(settings)
    with pytest.raises(ApiError) as exc:
        await service.register_server("t1", _payload(endpoint))
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert "内网或保留地址" in exc.value.message


async def test_loopback_is_opt_in_and_never_in_production() -> None:
    """回环只在 development/test + 显式开关下允许；生产一律拒绝。"""
    dev = _service(_settings(env="test", mcp_allowed_hosts="127.0.0.1", mcp_allow_loopback=True))
    assert await dev.register_server("t1", _payload("http://127.0.0.1:9/mcp"))

    prod = _service(
        _settings(env="production", mcp_allowed_hosts="127.0.0.1", mcp_allow_loopback=True)
    )
    with pytest.raises(ApiError):
        await prod.register_server("t1", _payload("http://127.0.0.1:9/mcp"))


def test_pinned_request_keeps_host_and_sni() -> None:
    """连接用 IP，Host 与 SNI 用原主机名；非默认端口保留。"""
    conn = McpConnection(
        transport=TRANSPORT_HTTP,
        endpoint="https://mcp.example.com:8443/mcp",
        pinned_ip="8.8.4.4",
        host_header="mcp.example.com",
    )
    url, headers, extensions = _pinned_request(conn)
    assert url == "https://8.8.4.4:8443/mcp"
    assert headers == {"Host": "mcp.example.com:8443"}
    assert extensions == {"sni_hostname": "mcp.example.com"}


def test_pinned_request_ipv6_brackets() -> None:
    """IPv6 钉扎必须加方括号，否则 URL 非法。"""
    conn = McpConnection(
        transport=TRANSPORT_HTTP,
        endpoint="https://mcp.example.com/mcp",
        pinned_ip="2606:4700::1111",
        host_header="mcp.example.com",
    )
    url, headers, _ = _pinned_request(conn)
    assert url == "https://[2606:4700::1111]/mcp"
    assert headers == {"Host": "mcp.example.com"}


def _runner(settings: Settings, service: McpServerService) -> McpRunner:
    """构造调用器。

    Args:
        settings: 配置。
        service: 业务服务。

    Returns:
        McpRunner: 调用器。
    """
    return McpRunner(settings=settings, service=service)


async def test_runner_blocks_private_target_without_leaking_reason() -> None:
    """运行期再次判定：内网目标一律 connect_failed，原因只进内部诊断。"""
    settings = _settings()
    service = _service(settings)
    runner = _runner(settings, service)
    target = McpTarget(
        tenant_id="t1",
        server_id="mcp_1",
        name="demo",
        transport=TRANSPORT_HTTP,
        endpoint="https://169.254.169.254/mcp",
    )
    with pytest.raises(McpError) as exc:
        await runner._connection(target)  # noqa: SLF001 - 需绕过登记层直接测运行期判定
    assert exc.value.kind == "connect_failed"
    assert "unsafe_target" in str(exc.value)


async def test_runner_pins_public_literal_ip() -> None:
    """公网字面量 IP 不查 DNS，直接钉住并回填 Host。"""
    settings = _settings()
    service = _service(settings)
    runner = _runner(settings, service)
    target = McpTarget(
        tenant_id="t1",
        server_id="mcp_1",
        name="demo",
        transport=TRANSPORT_HTTP,
        endpoint="https://8.8.8.8/mcp",
    )
    connection = await runner._connection(target)  # noqa: SLF001 - 断言钉扎结果
    assert connection.pinned_ip == "8.8.8.8"
    assert connection.host_header == "8.8.8.8"
    assert connection.proxy_url == ""


async def test_runner_uses_proxy_and_skips_pinning() -> None:
    """配置了 OA_SSRF_PROXY_URL 时由代理解析，不在本地钉 IP。"""
    settings = _settings(
        ssrf_proxy_url="http://ssrf-proxy:3128",
        mcp_allowed_hosts="mcp.example.com",
    )
    service = _service(settings)
    record = await service.register_server("t1", _payload("https://mcp.example.com/mcp"))
    target = await service._target(record)  # noqa: SLF001 - 验证 Settings→Target 全链路
    runner = _runner(settings, service)
    connection = await runner._connection(target)  # noqa: SLF001 - 断言代理模式不钉扎
    assert connection.proxy_url == "http://ssrf-proxy:3128"
    assert connection.pinned_ip == ""


def test_registration_requires_dns_name_not_only_hostname_shape() -> None:
    """`https://user@host` 一类凭据形态仍按原有规则拒绝（回归保护）。"""
    service = _service(_settings(mcp_allowed_hosts="example.com"))
    with pytest.raises(ApiError):
        service._validate_url("https://user:pass@example.com/mcp")  # noqa: SLF001 - 直测校验器
