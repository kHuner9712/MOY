"""trace_id 注入与透传（架构文档 9.3 日志规范）。

职责：
- 接收上游 `X-Trace-Id` 或生成新值，写入日志上下文；
- 请求结束前回写 `X-Trace-Id` 响应头，便于全链路对账；
- 转发下游时由 `DifyClient` 读取同一上下文写入请求头，实现端到端透传。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import (
    TENANT_ID_KEY,
    TRACE_ID_KEY,
    USER_ID_KEY,
    bind_context,
    clear_context,
    get_logger,
    unbind_context,
)
from app.core.security import TRACE_HEADER

logger = get_logger(__name__)

#: 日志中的统一链路字段名（与下游请求头同名，便于检索）
DOWNSTREAM_TRACE_HEADER: Final[str] = TRACE_HEADER


def new_trace_id() -> str:
    """生成新的 trace_id。

    Returns:
        str: 32 位十六进制 trace_id。
    """
    return uuid.uuid4().hex


def extract_trace_id(request: Request) -> str:
    """提取或生成 trace_id。

    Args:
        request: FastAPI 请求对象。

    Returns:
        str: 上游透传的 trace_id，或新生成的 trace_id。
    """
    incoming = request.headers.get(TRACE_HEADER, "").strip()
    if not incoming:
        return new_trace_id()
    # 上游值可能包含非法字符，做长度与字符集收敛
    sanitized = "".join(ch for ch in incoming if ch.isalnum() or ch in "-_")[:64]
    return sanitized or new_trace_id()


class TraceMiddleware(BaseHTTPMiddleware):
    """链路追踪中间件。

    为每个请求注入 `trace_id`，并在 `access_log` 事件中记录耗时与状态码。
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """注入 trace_id 并记录访问日志。

        Args:
            request: FastAPI 请求对象。
            call_next: 下一个中间件或路由处理函数。

        Returns:
            Response: 回写 `X-Trace-Id` 的响应对象。
        """
        trace_id = extract_trace_id(request)
        request.state.trace_id = trace_id
        bind_context(**{TRACE_ID_KEY: trace_id})

        try:
            response = await call_next(request)
        finally:
            clear_context()

        response.headers[TRACE_HEADER] = trace_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """访问日志中间件：输出关键业务节点的访问记录。

    日志字段固定包含 `timestamp / level / trace_id / tenant_id / user_id / event`，
    其中 `tenant_id` 与 `user_id` 由路由层解析后写入 `request.state`。
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """记录访问日志。

        Args:
            request: FastAPI 请求对象。
            call_next: 下一个中间件或路由处理函数。

        Returns:
            Response: 原样返回下游响应。
        """
        trace_id = getattr(request.state, "trace_id", "-")
        response = await call_next(request)
        tenant_id = getattr(request.state, "tenant_id", "-")
        user_id = getattr(request.state, "user_id", "-")

        bind_context(
            **{
                TRACE_ID_KEY: trace_id,
                TENANT_ID_KEY: tenant_id,
                USER_ID_KEY: user_id,
            }
        )
        try:
            logger.info(
                "http_access",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            )
        finally:
            unbind_context(TENANT_ID_KEY, USER_ID_KEY)
        return response
