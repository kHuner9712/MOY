"""统一异常 → `{code, message, data}` 兜底（架构文档 3.0.1 / 9.2）。

铁律：
- 任何异常对上层只暴露业务语言 `message`；
- 堆栈、异常类型、SQL/下游原始报文**一律只进日志**（level=ERROR，触发告警）；
- 未知异常统一收敛为 30003，禁止把 `str(exc)` 直接返回给前端。
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    ERROR_REGISTRY,
    ApiError,
    ErrorCode,
    error_response,
    http_status_of,
)
from app.core.logging import get_logger
from app.core.metrics import BUSINESS_ERRORS_TOTAL
from app.schemas.common import ApiResponse

logger = get_logger(__name__)

#: 未在注册表中登记的 HTTP 状态码 → 默认系统错误码
_FALLBACK_CODE: Final[ErrorCode] = ErrorCode.INTERNAL_ERROR

#: **免鉴权白名单**（架构文档 3.0.2 + 阶段二 §4.5 BL10）
#:
#: 除下列三项外，**所有**端点都必须带 `Authorization: Bearer`：
#:
#: | 路径 | 理由 |
#: | --- | --- |
#: | `/healthz` | 容器存活探针（编排层调用，无令牌） |
#: | `/readyz` | 容器就绪探针（编排层调用，无令牌） |
#: | `/api/v1/billing/payments/callback` | 支付渠道服务端回调（**验签代替鉴权**） |
#:
#: **新增任何免鉴权端点都必须先在此登记**，否则
#: `tests/integration/test_billing_and_callback.py::test_only_callback_is_public`
#: 会失败——这条断言是「不许悄悄开洞」的机械防线（G-24）。
AUTH_EXEMPT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/healthz",
        "/readyz",
        # 阶段四 T4-05.1：`/metrics` 不鉴权（Prometheus 抓取无令牌），
        # 且**不映射到宿主机公网端口**（compose 仅内网暴露）。
        "/metrics",
        "/api/v1/billing/payments/callback",
    }
)


def is_auth_exempt(path: str) -> bool:
    """判断路径是否免鉴权。

    Args:
        path: 请求路径（不含查询串）。

    Returns:
        bool: 命中白名单时返回 True。
    """
    normalized = (path or "").rstrip("/") or "/"
    return normalized in AUTH_EXEMPT_PATHS


def _json_error(
    code: ErrorCode, *, message: str | None = None, data: Any = None, status: int | None = None
) -> JSONResponse:
    """构造统一失败 JSON 响应。

    Args:
        code: 业务错误码。
        message: 覆盖默认业务文案。
        data: 附加业务数据。
        status: 覆盖默认 HTTP 状态码。

    Returns:
        JSONResponse: 带正确 HTTP 状态码的失败响应。
    """
    payload = error_response(code, message=message, data=data)
    http_status = status if status is not None else http_status_of(code)
    return JSONResponse(status_code=http_status, content=payload.model_dump())


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """业务异常处理器。

    Args:
        _request: FastAPI 请求对象（本处理器不使用）。
        exc: 业务异常。

    Returns:
        JSONResponse: 统一失败响应。
    """
    assert isinstance(exc, ApiError)
    # 阶段四 T4-05.1：业务错误按码计数（码是有限枚举，可安全作标签）
    BUSINESS_ERRORS_TOTAL.labels(code=str(int(exc.code))).inc()
    if exc.code in {ErrorCode.INTERNAL_ERROR, ErrorCode.CONFIG_ERROR}:
        logger.error("api_error", error_code=int(exc.code), detail=exc.log_message)
    else:
        logger.warning("api_error", error_code=int(exc.code), detail=exc.log_message)
    return _json_error(exc.code, message=exc.message, data=exc.data)


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """请求参数校验失败处理器 → 20001。

    校验明细可能包含字段名等技术信息，**不透出**，仅记录日志。

    Args:
        _request: FastAPI 请求对象。
        exc: `RequestValidationError` 或 Pydantic `ValidationError`。

    Returns:
        JSONResponse: 20001 统一失败响应。
    """
    if isinstance(exc, RequestValidationError):
        fields = [str(err.get("loc", ())) for err in exc.errors()]
    elif isinstance(exc, ValidationError):  # pragma: no cover - 由路由显式抛出时命中
        fields = [str(err.get("loc", ())) for err in exc.errors()]
    else:  # pragma: no cover - 防御性分支
        fields = []
    logger.warning("request_validation_failed", invalid_fields=fields)
    return _json_error(ErrorCode.PARAM_INVALID)


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """HTTP 异常处理器：将 Starlette/FastAPI HTTP 异常收敛为统一响应。

    Args:
        _request: FastAPI 请求对象。
        exc: `StarletteHTTPException`。

    Returns:
        JSONResponse: 统一失败响应。
    """
    assert isinstance(exc, StarletteHTTPException)
    mapped = {
        400: ErrorCode.PARAM_INVALID,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.RESOURCE_NOT_FOUND,
        405: ErrorCode.RESOURCE_NOT_FOUND,
        409: ErrorCode.CONFLICT,
        413: ErrorCode.PAYLOAD_TOO_LARGE,
        429: ErrorCode.RATE_LIMITED,
    }.get(exc.status_code, _FALLBACK_CODE)
    logger.warning("http_exception", status_code=exc.status_code, mapped_code=int(mapped))
    # 405（方法不允许）语义上更接近 20001 参数/请求格式问题，此处保留统一语义
    return _json_error(mapped, status=exc.status_code)


def _sqlstate_of(exc: BaseException) -> str:
    """沿异常链尽力取出 PostgreSQL SQLSTATE。

    数据库错误经仓储层抛出后，原始异常可能被 `asyncpg` / `SQLAlchemy` 包装一层，
    故依次检查自身与 `__cause__` / `__context__`，以及 SQLAlchemy 的 `.orig`。

    Args:
        exc: 未捕获异常。

    Returns:
        SQLSTATE 字符串；取不到时返回空串。

    Notes:
        只提取 SQLSTATE 这类**定位用**元数据，不提取 SQL 参数值，避免业务数据入日志。
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidates = (
            getattr(current, "sqlstate", None),
            getattr(getattr(current, "orig", None), "sqlstate", None),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
        current = current.__cause__ or current.__context__
    return ""


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常处理器 → 30003。

    只记录异常类型、SQLSTATE 与堆栈到日志，对外返回「系统繁忙，请稍后再试」。

    Args:
        _request: FastAPI 请求对象。
        exc: 任意未捕获异常。

    Returns:
        JSONResponse: 30003 统一失败响应。

    Notes:
        对外响应体**不得**包含列名、表名、SQLSTATE（`test_task_repo_pg.py` 有断言）。
        SQLSTATE 只进日志，用于定位「列不存在 / 权限不足」这类现场问题——
        缺了它，30003 在排障时等于没有信息。
    """
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        error_code=int(ErrorCode.INTERNAL_ERROR),
        exception_type=type(exc).__name__,
        sqlstate=_sqlstate_of(exc) or None,
    )
    return _json_error(ErrorCode.INTERNAL_ERROR)


def register_exception_handlers(app: FastAPI) -> None:
    """注册全部异常处理器。

    Args:
        app: FastAPI 应用实例。
    """
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)


def not_found_response() -> JSONResponse:
    """构造 404 统一响应（供自定义路由使用）。

    Returns:
        JSONResponse: 20004 统一失败响应。
    """
    return _json_error(ErrorCode.RESOURCE_NOT_FOUND)


__all__ = [
    "AUTH_EXEMPT_PATHS",
    "ERROR_REGISTRY",
    "ApiResponse",
    "is_auth_exempt",
    "not_found_response",
    "register_exception_handlers",
]
