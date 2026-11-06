"""MCP 客户端（矩阵第 3 项，2026-11-06）。

只实现**任务步骤需要的三件事**：`initialize` → `tools/list` → `tools/call`。

支持的传输与档位（取舍见 `docs/delivery-2026-11-06-mcp-client.md`）：

| 传输 | 档位 | 说明 |
| --- | --- | --- |
| streamable HTTP | A（生产默认） | POST JSON-RPC；`Mcp-Session-Id` 维持会话；响应 JSON 或 SSE |
| stdio | B（本地可信/测试） | 起子进程、按行 JSON-RPC；生产由业务层拒绝登记 |
| legacy SSE | 0（不支持） | 登记层直接拒绝，避免第二套长连接实现 |

安全口径：

- **凭据不进日志、不进异常文案**：`McpConnection.credential` 的 `repr=False`，
  错误只带分类（`timeout` / `auth_failed` / `protocol_error` / `output_too_large`）。
- **超时是总时长**：建连 + 握手 + 调用共用一个预算，超时即中止。
- **返回体硬上限**：逐块累计，超过 `max_output_bytes` 立刻中止（不落库大报文）。
- **stdio 子进程不继承业务环境**：只给最小系统环境 + 可选 `MCP_AUTH_TOKEN`。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

#: 协议版本（streamable HTTP 所属版本）
PROTOCOL_VERSION: Final[str] = "2025-03-26"

#: 客户端自报信息（不含任何版本号敏感信息）
CLIENT_INFO: Final[dict[str, str]] = {"name": "moy-adapter", "version": "1.0.0"}

#: 传输名
TRANSPORT_HTTP: Final[str] = "http"
TRANSPORT_STDIO: Final[str] = "stdio"

#: 单次通知与请求的 JSON-RPC id
_INIT_ID: Final[int] = 1
_CALL_ID: Final[int] = 2

#: SSE 事件分隔
_SSE_EVENT_SEP: Final[re.Pattern[str]] = re.compile(r"\r?\n\r?\n")


class McpError(Exception):
    """MCP 调用失败（分类 + 安全文案）。

    Attributes:
        kind: 失败分类（可直接对外展示）。
    """

    def __init__(self, kind: str, detail: str = "") -> None:
        """初始化。

        Args:
            kind: 分类（`connect_failed` / `timeout` / `auth_failed` /
                `protocol_error` / `output_too_large` / `tool_error`）。
            detail: 内部诊断（**不得含凭据/返回体**）。
        """
        self.kind = kind
        super().__init__(f"{kind}: {detail}" if detail else kind)


@dataclass(frozen=True, slots=True)
class McpConnection:
    """一次调用的连接参数（凭据只在内存中）。

    Attributes:
        transport: `http` / `stdio`。
        endpoint: URL 或可执行文件路径。
        args: stdio 启动参数。
        credential: 凭据明文（`repr=False`，不进日志）。
        timeout_ms: 建连 + 握手 + 调用总超时。
        max_output_bytes: 返回体上限。
        connect_timeout_ms: 建连超时。
        allow_insecure_http: 是否允许非 loopback 的 `http://`。
    """

    transport: str
    endpoint: str
    args: tuple[str, ...] = ()
    credential: str = field(default="", repr=False)
    timeout_ms: int = 15_000
    connect_timeout_ms: int = 5_000
    max_output_bytes: int = 64 * 1024
    allow_insecure_http: bool = False


@dataclass(frozen=True, slots=True)
class McpToolResult:
    """工具调用结果（已转成文本，非文本内容只留占位说明）。

    Attributes:
        text: 文本结果（供产物落库，后续再脱敏）。
        is_error: 工具自报失败（MCP `isError=true`）。
        size_bytes: 返回体大小。
    """

    text: str
    is_error: bool = False
    size_bytes: int = 0


async def list_tools(conn: McpConnection) -> list[str]:
    """读取工具列表（只回工具名，不回 schema）。

    Args:
        conn: 连接参数。

    Returns:
        list[str]: 工具名列表。

    Raises:
        McpError: 连接/协议/超时失败。
    """
    result = await _session(conn, method="tools/list", params={})
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise McpError("protocol_error", "tools/list missing tools")
    names: list[str] = []
    for item in tools:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


async def call_tool(conn: McpConnection, name: str, arguments: dict[str, Any]) -> McpToolResult:
    """调用一个工具。

    Args:
        conn: 连接参数。
        name: 工具名（已由业务层按白名单校验）。
        arguments: 工具参数（已由业务层做大小/形态约束）。

    Returns:
        McpToolResult: 文本结果。

    Raises:
        McpError: 连接/协议/超时/超限失败。
    """
    result = await _session(
        conn,
        method="tools/call",
        params={"name": name, "arguments": arguments},
    )
    text = _content_text(result)
    encoded = len(text.encode("utf-8"))
    if encoded > conn.max_output_bytes:
        raise McpError("output_too_large", f"bytes={encoded}")
    return McpToolResult(text=text, is_error=bool(result.get("isError")), size_bytes=encoded)


async def _session(conn: McpConnection, *, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """完成握手并执行一次请求。

    Args:
        conn: 连接参数。
        method: `tools/list` 或 `tools/call`。
        params: 请求参数。

    Returns:
        dict[str, Any]: `result` 对象。

    Raises:
        McpError: 任意失败分类。
    """
    timeout_seconds = max(1.0, conn.timeout_ms / 1000.0)
    try:
        async with asyncio.timeout(timeout_seconds):
            if conn.transport == TRANSPORT_STDIO:
                return await _stdio_session(conn, method, params)
            if conn.transport == TRANSPORT_HTTP:
                return await _http_session(conn, method, params)
            raise McpError("protocol_error", "unsupported transport")
    except TimeoutError as exc:
        raise McpError("timeout", f"timeout_ms={conn.timeout_ms}") from exc
    except httpx.ConnectError as exc:
        raise McpError("connect_failed", type(exc).__name__) from exc
    except httpx.TimeoutException as exc:
        raise McpError("timeout", type(exc).__name__) from exc
    except httpx.HTTPError as exc:
        raise McpError("connect_failed", type(exc).__name__) from exc


# ---------------------------------------------------------------------------
# streamable HTTP
# ---------------------------------------------------------------------------
async def _http_session(conn: McpConnection, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """streamable HTTP 会话。

    Args:
        conn: 连接参数。
        method: 方法名。
        params: 参数。

    Returns:
        dict[str, Any]: `result`。

    Raises:
        McpError: 鉴权/协议/超限失败。
    """
    timeout = httpx.Timeout(
        connect=min(conn.connect_timeout_ms / 1000.0, conn.timeout_ms / 1000.0),
        read=conn.timeout_ms / 1000.0,
        write=conn.timeout_ms / 1000.0,
        pool=min(conn.connect_timeout_ms / 1000.0, conn.timeout_ms / 1000.0),
    )
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if conn.credential:
        headers["Authorization"] = f"Bearer {conn.credential}"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        init_payload = {
            "jsonrpc": "2.0",
            "id": _INIT_ID,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        }
        init_result, session_id = await _http_rpc(
            client, conn.endpoint, init_payload, headers, conn.max_output_bytes
        )
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        _assert_result(init_result, _INIT_ID)
        await _http_notify(client, conn.endpoint, headers, "notifications/initialized")

        payload = {"jsonrpc": "2.0", "id": _CALL_ID, "method": method, "params": params}
        response, _ = await _http_rpc(
            client, conn.endpoint, payload, headers, conn.max_output_bytes
        )
        return _result_of(response, _CALL_ID)


async def _http_rpc(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    max_bytes: int,
) -> tuple[dict[str, Any], str]:
    """发送一条 JSON-RPC 请求并读取响应。

    Args:
        client: HTTP 客户端。
        url: 地址。
        payload: JSON-RPC 载荷。
        headers: 请求头。
        max_bytes: 响应体上限。

    Returns:
        tuple[dict[str, Any], str]: `(响应 JSON, session_id)`。

    Raises:
        McpError: 鉴权失败/协议错误/超限。
    """
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        if response.status_code in {401, 403}:
            raise McpError("auth_failed", f"status={response.status_code}")
        if response.status_code >= 400:
            raise McpError("protocol_error", f"status={response.status_code}")
        session_id = response.headers.get("mcp-session-id", "")
        if response.status_code in {202, 204}:
            return {}, session_id
        content_type = response.headers.get("content-type", "")
        body = await _read_capped(response, max_bytes)
        text = body.decode("utf-8", errors="replace")
        request_id = payload.get("id")
        if "text/event-stream" in content_type:
            message = _pick_sse_message(text, request_id)
        else:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                raise McpError("protocol_error", "invalid json") from exc
            message = parsed if isinstance(parsed, dict) else {}
        return message, session_id


async def _http_notify(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], method: str
) -> None:
    """发送一条 JSON-RPC 通知（无 id、无响应体）。

    Args:
        client: HTTP 客户端。
        url: 地址。
        headers: 请求头。
        method: 通知方法名。
    """
    payload = {"jsonrpc": "2.0", "method": method}
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        if response.status_code in {401, 403}:
            raise McpError("auth_failed", f"status={response.status_code}")
        if response.status_code >= 400:
            raise McpError("protocol_error", f"status={response.status_code}")


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """流式读取响应体并执行字节上限。

    Args:
        response: 流式响应。
        max_bytes: 上限。

    Returns:
        bytes: 响应体。

    Raises:
        McpError: 超限（`output_too_large`）。
    """
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise McpError("output_too_large", f"bytes>{max_bytes}")
    return bytes(buffer)


def _pick_sse_message(text: str, request_id: Any) -> dict[str, Any]:
    """从 SSE 响应里挑出与请求 id 对应的 JSON-RPC 消息。

    Args:
        text: SSE 文本。
        request_id: 期望的 JSON-RPC id。

    Returns:
        dict[str, Any]: 消息。

    Raises:
        McpError: 没有可用的 JSON-RPC 消息。
    """
    last: dict[str, Any] | None = None
    for event in _SSE_EVENT_SEP.split(text):
        data_lines = [
            line[5:].strip() for line in event.splitlines() if line.strip().startswith("data:")
        ]
        if not data_lines:
            continue
        raw = "\n".join(data_lines)
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("id") == request_id:
            return message
        last = message
    if last is None:
        raise McpError("protocol_error", "empty sse response")
    return last


def _assert_result(message: dict[str, Any], request_id: Any) -> None:
    """确认 JSON-RPC 响应是成功结果。

    Args:
        message: 响应。
        request_id: 期望 id。

    Raises:
        McpError: 错误响应或 id 不匹配。
    """
    if message.get("error"):
        raise McpError("protocol_error", "jsonrpc error")
    if message.get("id") != request_id:
        raise McpError("protocol_error", "id mismatch")


def _result_of(message: dict[str, Any], request_id: Any) -> dict[str, Any]:
    """取响应中的 `result` 对象。

    Args:
        message: 响应。
        request_id: 期望 id。

    Returns:
        dict[str, Any]: result。

    Raises:
        McpError: 错误响应/缺 result/id 不匹配。
    """
    if message.get("error"):
        raise McpError("tool_error", "jsonrpc error")
    if message.get("id") != request_id:
        raise McpError("protocol_error", "id mismatch")
    result = message.get("result")
    if not isinstance(result, dict):
        raise McpError("protocol_error", "missing result")
    return result


# ---------------------------------------------------------------------------
# stdio
# ---------------------------------------------------------------------------
async def _stdio_session(
    conn: McpConnection, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """stdio 会话（起子进程、按行 JSON-RPC）。

    Args:
        conn: 连接参数。
        method: 方法名。
        params: 参数。

    Returns:
        dict[str, Any]: `result`。

    Raises:
        McpError: 启动/协议/超限失败。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            conn.endpoint,
            *conn.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_stdio_env(conn.credential),
        )
    except OSError as exc:
        raise McpError("connect_failed", type(exc).__name__) from exc
    stderr_task = asyncio.create_task(_drain_stderr(process.stderr))
    try:
        init_payload = {
            "jsonrpc": "2.0",
            "id": _INIT_ID,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        }
        init_result = await _stdio_rpc(process, init_payload, conn.max_output_bytes)
        _assert_result(init_result, _INIT_ID)
        await _stdio_write(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        payload = {"jsonrpc": "2.0", "id": _CALL_ID, "method": method, "params": params}
        response = await _stdio_rpc(process, payload, conn.max_output_bytes)
        return _result_of(response, _CALL_ID)
    finally:
        stderr_task.cancel()
        await _terminate(process)


def _stdio_env(credential: str) -> dict[str, str]:
    """构造 stdio 子进程的最小环境（不继承业务配置）。

    Args:
        credential: 可选凭据（只以 `MCP_AUTH_TOKEN` 传入）。

    Returns:
        dict[str, str]: 环境变量。
    """
    keep = (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
    )
    env = {key: os.environ[key] for key in keep if key in os.environ}
    if credential:
        env["MCP_AUTH_TOKEN"] = credential
    return env


async def _stdio_write(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
    """向子进程写一行 JSON。

    Args:
        process: 子进程。
        payload: JSON-RPC 载荷。

    Raises:
        McpError: 子进程已退出。
    """
    if process.stdin is None:
        raise McpError("connect_failed", "stdin unavailable")
    try:
        process.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError) as exc:
        raise McpError("connect_failed", type(exc).__name__) from exc


async def _stdio_rpc(
    process: asyncio.subprocess.Process, payload: dict[str, Any], max_bytes: int
) -> dict[str, Any]:
    """发送请求并读取对应响应。

    Args:
        process: 子进程。
        payload: JSON-RPC 载荷。
        max_bytes: 单行响应上限。

    Returns:
        dict[str, Any]: 响应。

    Raises:
        McpError: 超限/协议错误/子进程退出。
    """
    await _stdio_write(process, payload)
    if process.stdout is None:
        raise McpError("connect_failed", "stdout unavailable")
    request_id = payload.get("id")
    while True:
        line = await process.stdout.readline()
        if not line:
            raise McpError("connect_failed", "process exited")
        if len(line) > max_bytes * 2:
            raise McpError("output_too_large", f"line_bytes>{max_bytes}")
        try:
            message = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            raise McpError("protocol_error", "invalid json line") from None
        if not isinstance(message, dict):
            raise McpError("protocol_error", "invalid message")
        if message.get("id") == request_id or message.get("method"):
            return message


async def _drain_stderr(stream: asyncio.StreamReader | None) -> None:
    """排空 stderr（只计字节，不记内容——可能含凭据）。

    Args:
        stream: stderr 流。
    """
    if stream is None:
        return
    total = 0
    while True:
        chunk = await stream.read(1024)
        if not chunk:
            return
        total += len(chunk)
        if total > 1024 * 1024:
            return


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """终止子进程（先 terminate，再 kill）。

    Args:
        process: 子进程。
    """
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


def _content_text(result: dict[str, Any]) -> str:
    """把 MCP 工具结果转成文本。

    Args:
        result: `tools/call` 的 result。

    Returns:
        str: 文本（非文本内容只留占位说明）。
    """
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif item.get("type") == "image":
                parts.append("[图片结果已省略]")
            elif item.get("type") == "resource":
                parts.append("[资源结果已省略]")
    if parts:
        return "\n".join(parts)
    structured = result.get("structuredContent")
    if isinstance(structured, dict | list):
        return json.dumps(structured, ensure_ascii=False)
    return ""


__all__: list[str] = [
    "CLIENT_INFO",
    "PROTOCOL_VERSION",
    "TRANSPORT_HTTP",
    "TRANSPORT_STDIO",
    "McpConnection",
    "McpError",
    "McpToolResult",
    "call_tool",
    "list_tools",
]
