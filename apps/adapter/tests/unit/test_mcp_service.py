"""MCP 业务服务测试（矩阵第 3 项）。

覆盖：默认关闭、传输分档、URL/凭据形态、租户隔离、白名单、真实探测、
凭据解析（真实 HTTP 鉴权）与错误分类。
"""

from __future__ import annotations

import base64
import sys
from collections.abc import Iterator

import pytest

from app.business.credential_service import CredentialResolver
from app.business.mcp_service import TRANSPORT_HTTP, TRANSPORT_STDIO, McpServerService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.credential_repo import InMemoryCredentialStore
from app.db.repositories.mcp_repo import InMemoryMcpServerStore
from app.schemas.mcp import McpServerItem
from app.services.mcp_runner import McpRunner
from tests.fixtures import minimal_mcp_server

pytestmark = pytest.mark.unit

#: 测试用 32 字节密钥（base64）
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
        "mcp_allow_stdio": True,
        "mcp_stdio_allowed_commands": sys.executable,
        "mcp_allow_insecure_http": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _service(settings: Settings | None = None) -> McpServerService:
    """构造服务（独立内存存储与凭据存储）。

    Args:
        settings: 可选配置。

    Returns:
        McpServerService: 服务。
    """
    conf = settings or _settings()
    resolver = CredentialResolver(settings=conf, store=InMemoryCredentialStore())
    return McpServerService(
        settings=conf,
        store=InMemoryMcpServerStore(),
        credentials=resolver,
    )


def _runner(service: McpServerService) -> McpRunner:
    """构造调用器（复用同一个业务服务/存储）。

    Args:
        service: 业务服务。

    Returns:
        McpRunner: 调用器。
    """
    return McpRunner(service=service)


@pytest.fixture
def http_endpoint() -> Iterator[str]:
    """启动**不带鉴权**的真实 HTTP server（探测/白名单用例用）。

    Yields:
        str: endpoint。
    """
    server, endpoint = minimal_mcp_server.start_http_server()
    try:
        yield endpoint
    finally:
        minimal_mcp_server.stop_http_server(server)


@pytest.fixture
def http_endpoint_auth() -> Iterator[tuple[str, str]]:
    """启动带 Bearer 鉴权的真实 HTTP server（凭据用例用）。

    Yields:
        tuple[str, str]: `(endpoint, token)`。
    """
    token = "mcp-secret-token"
    server, endpoint = minimal_mcp_server.start_http_server(token=token)
    try:
        yield endpoint, token
    finally:
        minimal_mcp_server.stop_http_server(server)


def _payload(endpoint: str, **overrides: object) -> dict[str, object]:
    """构造登记载荷。

    Args:
        endpoint: 地址。
        **overrides: 覆盖项。

    Returns:
        dict[str, object]: 载荷。
    """
    values: dict[str, object] = {
        "name": "demo",
        "transport": TRANSPORT_HTTP,
        "endpoint": endpoint,
        "allowed_tools": ["echo"],
        "enabled": True,
    }
    values.update(overrides)
    return values


async def test_disabled_by_default() -> None:
    """能力开关关闭时登记直接 20003。"""
    service = _service(_settings(mcp_enabled=False))
    with pytest.raises(ApiError) as exc:
        await service.register_server("t1", _payload("http://127.0.0.1:1/mcp"))
    assert exc.value.code == ErrorCode.FORBIDDEN


async def test_legacy_sse_rejected() -> None:
    """legacy SSE 是 0 档：登记即拒。"""
    service = _service()
    with pytest.raises(ApiError) as exc:
        await service.register_server("t1", _payload("http://127.0.0.1:1/mcp", transport="sse"))
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_endpoint_must_not_embed_credentials() -> None:
    """URL 里的 userinfo / 敏感查询参数被拒绝。"""
    service = _service()
    with pytest.raises(ApiError):
        await service.register_server("t1", _payload("http://user:pass@127.0.0.1:1/mcp"))
    with pytest.raises(ApiError):
        await service.register_server("t1", _payload("http://127.0.0.1:1/mcp?token=abc"))


async def test_plaintext_credential_ref_rejected() -> None:
    """凭据列只接受引用形态，明文密钥写不进去。"""
    service = _service()
    with pytest.raises(ApiError) as exc:
        await service.register_server(
            "t1", _payload("http://127.0.0.1:1/mcp", credential_ref="sk-live-123456")
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_http_non_loopback_requires_tls() -> None:
    """非 loopback 的 http 默认拒绝；显式放开才允许。"""
    service = _service(_settings(mcp_allow_insecure_http=False))
    with pytest.raises(ApiError):
        await service.register_server("t1", _payload("http://example.com/mcp"))
    service = _service(_settings(mcp_allow_insecure_http=True))
    record = await service.register_server("t1", _payload("http://example.com/mcp"))
    assert record.endpoint.endswith("/mcp")


async def test_stdio_forbidden_in_production() -> None:
    """生产环境不允许起本地进程。"""
    service = _service(_settings(env="production"))
    with pytest.raises(ApiError) as exc:
        await service.register_server(
            "t1",
            _payload(sys.executable, transport=TRANSPORT_STDIO, args=["-c", "print(1)"]),
        )
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_stdio_command_allowlist() -> None:
    """stdio 可执行文件必须在白名单里。"""
    service = _service(_settings(mcp_stdio_allowed_commands="python"))
    with pytest.raises(ApiError):
        await service.register_server("t1", _payload("/usr/bin/curl", transport=TRANSPORT_STDIO))


async def test_tenant_isolation() -> None:
    """A 租户登记的服务，B 租户看不到、也调不到。"""
    service = _service()
    record = await service.register_server("tenant-a", _payload("http://127.0.0.1:1/mcp"))
    assert await service.list_servers("tenant-b") == []
    with pytest.raises(ApiError) as exc:
        await _runner(service).execute("tenant-b", f"{record.name}.echo", {})
    assert exc.value.code == ErrorCode.TOOL_NOT_CONNECTED


async def test_allowlist_is_enforced() -> None:
    """工具不在白名单 → 13005。"""
    service = _service()
    await service.register_server("tenant-a", _payload("http://127.0.0.1:1/mcp"))
    with pytest.raises(ApiError) as exc:
        await _runner(service).execute("tenant-a", "demo.add", {})
    assert exc.value.code == ErrorCode.TOOL_OPERATION_UNSUPPORTED


async def test_probe_real_server_and_allowlist_intersection(http_endpoint: str) -> None:
    """真实探测：只回白名单内的工具。"""
    endpoint = http_endpoint
    service = _service()
    record = await service.register_server(
        "tenant-a", _payload(endpoint, allowed_tools=["echo", "add"])
    )
    result = await _runner(service).probe("tenant-a", record.server_id)
    assert result["ok"] is True
    assert result["tools"] == ["add", "echo"]


async def test_probe_failure_is_observable_without_details() -> None:
    """连不上时返回分类，不返回地址/凭据。"""
    service = _service()
    record = await service.register_server(
        "tenant-a", _payload("http://127.0.0.1:1/mcp", timeout_ms=1_500)
    )
    result = await _runner(service).probe("tenant-a", record.server_id)
    assert result["ok"] is False
    assert result["reason"] in {"connect_failed", "timeout"}
    assert "127.0.0.1" not in str(result)


async def test_credential_is_resolved_but_never_returned(
    http_endpoint_auth: tuple[str, str],
) -> None:
    """凭据按引用解密后真实发出，且响应模型里没有引用/明文。"""
    endpoint, token = http_endpoint_auth
    conf = _settings()
    resolver = CredentialResolver(settings=conf, store=InMemoryCredentialStore())
    ref = "cred_abc1234567"
    await resolver.store_credential(ref, token, tenant_id="tenant-a", provider="mcp")
    service = McpServerService(settings=conf, store=InMemoryMcpServerStore(), credentials=resolver)
    record = await service.register_server("tenant-a", _payload(endpoint, credential_ref=ref))
    result = await _runner(service).probe("tenant-a", record.server_id)
    assert result["ok"] is True
    item = McpServerItem.from_record(record)
    dumped = item.model_dump()
    assert "credential_ref" not in dumped
    assert token not in str(dumped)
    assert dumped["credential_configured"] is True


async def test_credential_failure_maps_to_auth_failed() -> None:
    """引用不存在时，探测表现为 auth_failed（不抛 500）。"""
    conf = _settings()
    resolver = CredentialResolver(settings=conf, store=InMemoryCredentialStore())
    service = McpServerService(settings=conf, store=InMemoryMcpServerStore(), credentials=resolver)
    record = await service.register_server(
        "tenant-a",
        _payload("http://127.0.0.1:1/mcp", credential_ref="cred_missing000"),
    )
    result = await _runner(service).probe("tenant-a", record.server_id)
    assert result["ok"] is False


async def test_server_limit_per_tenant() -> None:
    """单租户登记上限。"""
    service = _service(_settings(mcp_max_servers_per_tenant=1))
    await service.register_server("tenant-a", _payload("http://127.0.0.1:1/mcp"))
    with pytest.raises(ApiError) as exc:
        await service.register_server("tenant-a", _payload("http://127.0.0.1:1/mcp", name="demo2"))
    assert exc.value.code == ErrorCode.PARAM_INVALID
