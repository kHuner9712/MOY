"""HTTP 指标中间件（阶段四 T4-05.1，架构 §8.1）。

三条口径决定了这个中间件必须**在响应之后**记录：

1. **`route` 用路由模板**：原始路径里带 `conv_xxx`、`mem_xxx`，每个 ID 都会变成一条
   时间序列（基数爆炸）。模板取自 Starlette 匹配后的 `request.scope["route"].path`，
   因此必须在 `call_next` **之后**读——之前还没匹配到路由。
2. **`status` 只保留 6 类**（`STATUS_CLASSES`）：同理，避免 404/405/422… 每种一条序列。
3. **未匹配路由**（`route is None`）记成 `unmatched`：404 扫描是真实存在的攻击形态，
   必须能看见"有多少流量打在了不存在的路径上"，但又不能把攻击者构造的随机路径当标签。

`/metrics` 自身**不记录**：否则每次抓取都给自己加一条样本，曲线被自己的噪声主导。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    status_class,
)

#: 不参与指标统计的路径（抓取端点自身）
EXCLUDED_PATHS: frozenset[str] = frozenset({"/metrics"})

#: 未匹配路由的占位标签
UNMATCHED_ROUTE: str = "unmatched"


def route_template(request: Request) -> str:
    """取本次请求命中的**路由模板**。

    Args:
        request: FastAPI 请求对象。

    Returns:
        str: 路由模板（如 `/api/v1/model/conversations/{conversation_id}`）；
        未匹配时返回 `unmatched`。
    """
    route = request.scope.get("route")
    template = getattr(route, "path", "") if route is not None else ""
    return template or UNMATCHED_ROUTE


def record_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    """记录一次请求（供中间件与测试共用）。

    Args:
        method: HTTP 方法。
        route: 路由模板。
        status_code: 状态码。
        duration_seconds: 耗时（秒）。
    """
    HTTP_REQUESTS_TOTAL.labels(
        method=method.upper(), route=route, status=status_class(status_code)
    ).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(route=route).observe(max(duration_seconds, 0.0))


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录 `http_requests_total` 与 `http_request_duration_seconds`。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """记录一次请求的计数与耗时。

        Args:
            request: 请求对象。
            call_next: 下游处理函数。

        Returns:
            Response: 下游响应（原样返回）。
        """
        if request.url.path in EXCLUDED_PATHS:
            return await call_next(request)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 未捕获异常最终会被错误处理器转成 500，但中间件先看到异常：
            # 这里补记一条 5xx，避免"抛异常的那部分流量在指标里消失"。
            record_request(
                request.method, route_template(request), 500, time.perf_counter() - started
            )
            raise
        record_request(
            request.method,
            route_template(request),
            response.status_code,
            time.perf_counter() - started,
        )
        return response


__all__: list[str] = [
    "EXCLUDED_PATHS",
    "UNMATCHED_ROUTE",
    "MetricsMiddleware",
    "record_request",
    "route_template",
]
