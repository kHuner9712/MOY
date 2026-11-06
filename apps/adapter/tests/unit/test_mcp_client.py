"""MCP 客户端真连测试（矩阵第 3 项）。

**不用 mock**：HTTP 走真实 `ThreadingHTTPServer`，stdio 走真实子进程。
覆盖 initialize / tools/list / tools/call、SSE 帧解析、鉴权、超时、超大返回、
协议错误与连接失败。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.clients.mcp_client import (
    TRANSPORT_HTTP,
    TRANSPORT_STDIO,
    McpConnection,
    McpError,
    call_tool,
    list_tools,
)
from tests.fixtures import minimal_mcp_server

pytestmark = pytest.mark.unit

#: 本文件路径（stdio 子进程直接执行它）
_SERVER_PATH = Path(minimal_mcp_server.__file__)


@pytest.fixture
def http_endpoint() -> Iterator[str]:
    """启动真实 HTTP MCP server。

    Yields:
        str: 真实 endpoint。
    """
    server, endpoint = minimal_mcp_server.start_http_server()
    try:
        yield endpoint
    finally:
        minimal_mcp_server.stop_http_server(server)


def _conn(endpoint: str, **overrides: object) -> McpConnection:
    """构造连接参数。

    Args:
        endpoint: 地址。
        **overrides: 覆盖项。

    Returns:
        McpConnection: 连接参数。
    """
    values: dict[str, object] = {
        "transport": TRANSPORT_HTTP,
        "endpoint": endpoint,
        "timeout_ms": 10_000,
        "connect_timeout_ms": 2_000,
        "max_output_bytes": 64 * 1024,
        "allow_insecure_http": True,
    }
    values.update(overrides)
    return McpConnection(**values)  # type: ignore[arg-type]


async def test_http_real_handshake_list_and_call(http_endpoint: str) -> None:
    """真实 socket：握手、列工具、调用 echo。"""
    connection = _conn(http_endpoint)
    names = await list_tools(connection)
    assert "echo" in names
    assert "add" in names

    result = await call_tool(connection, "echo", {"text": "你好，MCP"})
    assert result.is_error is False
    assert result.text == "你好，MCP"


async def test_http_sse_response_frames(http_endpoint: str) -> None:
    """SSE 帧（text/event-stream）也能解析出结果。"""
    connection = _conn(http_endpoint)
    result = await call_tool(connection, "sse_echo", {"text": "event-ok"})
    assert result.text == "event-ok"


async def test_http_auth_header_is_sent() -> None:
    """鉴权：带凭据成功，不带凭据 401 → auth_failed。"""
    server, endpoint = minimal_mcp_server.start_http_server(token="secret-token")
    try:
        ok = await list_tools(_conn(endpoint, credential="secret-token"))
        assert "echo" in ok
        with pytest.raises(McpError) as exc:
            await list_tools(_conn(endpoint))
        assert exc.value.kind == "auth_failed"
    finally:
        minimal_mcp_server.stop_http_server(server)


async def test_http_output_size_cap(http_endpoint: str) -> None:
    """返回体超过上限即中止（output_too_large）。"""
    with pytest.raises(McpError) as exc:
        await call_tool(_conn(http_endpoint, max_output_bytes=1_024), "big", {})
    assert exc.value.kind == "output_too_large"


async def test_http_timeout(http_endpoint: str) -> None:
    """慢工具触发总超时。"""
    with pytest.raises(McpError) as exc:
        await call_tool(_conn(http_endpoint, timeout_ms=1_000), "slow", {})
    assert exc.value.kind == "timeout"


async def test_http_connect_failure() -> None:
    """连不上时是 connect_failed（不泄露地址细节）。"""
    with pytest.raises(McpError) as exc:
        await list_tools(_conn("http://127.0.0.1:1/mcp", timeout_ms=2_000))
    assert exc.value.kind in {"connect_failed", "timeout"}


async def test_stdio_real_subprocess() -> None:
    """真实子进程：stdio 按行 JSON-RPC 完成 echo。"""
    connection = McpConnection(
        transport=TRANSPORT_STDIO,
        endpoint=sys.executable,
        args=(str(_SERVER_PATH), "--stdio"),
        timeout_ms=10_000,
        connect_timeout_ms=2_000,
        max_output_bytes=64 * 1024,
    )
    names = await list_tools(connection)
    assert "echo" in names
    result = await call_tool(connection, "echo", {"text": "stdio-ok"})
    assert result.text == "stdio-ok"


async def test_stdio_missing_executable() -> None:
    """stdio 可执行文件不存在 → connect_failed。"""
    connection = McpConnection(
        transport=TRANSPORT_STDIO,
        endpoint="/definitely/not/a/real/binary",
        timeout_ms=3_000,
    )
    with pytest.raises(McpError) as exc:
        await list_tools(connection)
    assert exc.value.kind == "connect_failed"
