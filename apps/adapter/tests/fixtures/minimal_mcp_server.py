"""本地最小 MCP server（**真实连接**验收用，stdlib only）。

为什么不用 mock：MCP 客户端最容易在「真实 socket / 真实子进程 / 真实换行分帧」
上出错，mock 掉的恰恰是这些。本文件提供两种真实形态：

- `--http`：`ThreadingHTTPServer`，实现 streamable HTTP 的 POST JSON-RPC；
- `--stdio`：按行读写 stdin/stdout，供 stdio 传输真连。

只实现 `initialize` / `notifications/initialized` / `tools/list` / `tools/call`。
测试用工具：`echo` / `add` / `big`（超大返回）/ `slow`（超时）/ `fail`（工具自报失败）/
`sse_echo`（用 SSE 响应体返回）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROTOCOL_VERSION = "2025-03-26"
SESSION_ID = "minimal-session-1"
AUTH_TOKEN_ENV = "MCP_AUTH_TOKEN"

TOOLS: list[dict[str, Any]] = [
    {"name": "echo", "description": "回显文本", "inputSchema": {"type": "object"}},
    {"name": "add", "description": "两数相加", "inputSchema": {"type": "object"}},
    {"name": "big", "description": "返回超大文本", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "慢响应", "inputSchema": {"type": "object"}},
    {"name": "fail", "description": "工具自报失败", "inputSchema": {"type": "object"}},
    {"name": "sse_echo", "description": "以 SSE 帧回显", "inputSchema": {"type": "object"}},
]


def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """构造 JSON-RPC 成功响应。

    Args:
        request_id: 请求 id。
        result: 结果对象。

    Returns:
        dict[str, Any]: 响应。
    """
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    """构造 `tools/call` 的结果。

    Args:
        text: 文本内容。
        is_error: 是否工具自报失败。

    Returns:
        dict[str, Any]: 结果对象。
    """
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(
    payload: dict[str, Any], *, token: str | None = None
) -> tuple[dict[str, Any] | None, bool]:
    """处理一条 JSON-RPC 消息。

    Args:
        payload: 消息。
        token: 可选的静态令牌（用于鉴权测试）。

    Returns:
        tuple[dict[str, Any] | None, bool]: `(响应, 是否用 SSE 帧)`。
    """
    method = payload.get("method")
    request_id = payload.get("id")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    if method == "initialize":
        return (
            _response(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "minimal-mcp", "version": "0.1.0"},
                },
            ),
            False,
        )
    if method == "notifications/initialized":
        return None, False
    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS}), False
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name == "echo":
            return _response(request_id, _tool_result(str(arguments.get("text", "")))), False
        if name == "add":
            total = int(arguments.get("a", 0)) + int(arguments.get("b", 0))
            return _response(request_id, _tool_result(str(total))), False
        if name == "big":
            return _response(request_id, _tool_result("x" * 200_000)), False
        if name == "slow":
            time.sleep(2)
            return _response(request_id, _tool_result("slow")), False
        if name == "fail":
            return _response(request_id, _tool_result("工具内部失败", is_error=True)), False
        if name == "sse_echo":
            return _response(request_id, _tool_result(str(arguments.get("text", "")))), True
        return (
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "unknown tool"},
            },
            False,
        )
    return (
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "unknown method"},
        },
        False,
    )


class _Handler(BaseHTTPRequestHandler):
    """streamable HTTP 处理器。"""

    server_version = "MinimalMCP/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """静默（测试输出里不需要访问日志）。"""
        return

    def do_POST(self) -> None:
        """处理一次 POST JSON-RPC。"""
        expected = getattr(self.server, "token", None)
        if expected:
            header = self.headers.get("Authorization", "")
            if header != f"Bearer {expected}":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if payload.get("method") == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        response, sse = handle(payload)
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if sse:
            text = f"event: message\ndata: {json.dumps(response, ensure_ascii=False)}\n\n"
            encoded = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Mcp-Session-Id", SESSION_ID)
            self.end_headers()
            self.wfile.write(encoded)
            return
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Mcp-Session-Id", SESSION_ID)
        self.end_headers()
        self.wfile.write(encoded)


def start_http_server(token: str | None = None) -> tuple[ThreadingHTTPServer, str]:
    """启动真实 HTTP 服务（随机端口）。

    Args:
        token: 可选静态令牌。

    Returns:
        tuple[ThreadingHTTPServer, str]: `(server, endpoint)`。
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.token = token  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    return server, f"http://127.0.0.1:{port}/mcp"


def stop_http_server(server: ThreadingHTTPServer) -> None:
    """停止 HTTP 服务。

    Args:
        server: 服务实例。
    """
    server.shutdown()
    server.server_close()


def _stdio_loop() -> None:
    """按行处理 stdin 上的 JSON-RPC。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        response, _ = handle(payload)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    if args.stdio:
        _stdio_loop()
        return
    server, endpoint = start_http_server(args.token or None)
    print(endpoint, flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_http_server(server)


if __name__ == "__main__":
    main()
