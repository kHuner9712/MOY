"""结构化日志（架构文档 9.3 日志规范）。

要点：
- 使用 structlog 输出 JSON 日志，字段固定包含
  `timestamp / level / trace_id / tenant_id / user_id / event`；
- `trace_id` 全链路透传：接入层生成 → 响应头回传 → 转发下游时写入 `X-Trace-Id`；
- **严禁记录**：用户令牌、工具授权凭据、文档正文全文。

脱敏策略：`redact()` 对令牌、密码、密钥、凭据类键做统一掩码，
调用方应在写入日志前显式使用，避免敏感信息落盘。
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Final

import structlog

from app.core.config import Settings, get_settings

# 需要脱敏的敏感键名（大小写不敏感，部分匹配）
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "auth_code",
        "client_secret",
        "credential",
        "credentials",
        "jwt_secret",
        "password",
        "refresh_token",
        "secret",
        "token",
        "access_token",
        "api_key",
        "apikey",
        "app_key",
        "secret_key",
        "access_key",
    }
)

# 固定为「未获取到」的占位值，保证日志字段结构稳定
_UNKNOWN: Final[str] = "-"

_MASK: Final[str] = "***"

# 日志上下文字段名（与架构文档 9.3 完全一致）
TRACE_ID_KEY: Final[str] = "trace_id"
TENANT_ID_KEY: Final[str] = "tenant_id"
USER_ID_KEY: Final[str] = "user_id"

_configured: bool = False


def is_sensitive(key: str) -> bool:
    """判断键名是否属于敏感字段。

    Args:
        key: 待判断的字段名。

    Returns:
        bool: 命中敏感字段返回 True。
    """
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEYS)


def redact(value: Any, *, key: str | None = None) -> Any:
    """对敏感值做脱敏处理。

    Args:
        value: 原始值。
        key: 字段名；给出时按敏感规则判定是否掩码。

    Returns:
        Any: 脱敏后的值（非敏感字段原样返回）。
    """
    if key is not None and is_sensitive(key):
        return _MASK
    if isinstance(value, dict):
        return {k: redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _inject_context(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """补齐日志必填字段，保证结构稳定（架构文档 9.3）。"""
    event_dict.setdefault(TRACE_ID_KEY, _UNKNOWN)
    event_dict.setdefault(TENANT_ID_KEY, _UNKNOWN)
    event_dict.setdefault(USER_ID_KEY, _UNKNOWN)
    event_dict.setdefault("event", _UNKNOWN)
    return event_dict


def configure_logging(settings: Settings | None = None) -> None:
    """初始化 structlog 日志配置（幂等）。

    Args:
        settings: 可选配置实例，缺省读取全局单例。
    """
    global _configured
    if _configured:
        return

    conf = settings or get_settings()
    level = getattr(logging, conf.log_level, logging.INFO)

    # 关闭标准库访问日志噪声，由 uvicorn 自身配置接管
    logging.getLogger("uvicorn.access").disabled = True

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _inject_context,
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Any
    if conf.log_format == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)

    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    """获取结构化日志器。

    Args:
        name: 可选 logger 名称，通常传 `__name__`。

    Returns:
        Any: structlog bound logger。
    """
    configure_logging()
    return structlog.get_logger(name) if name else structlog.get_logger()


def bind_context(**fields: Any) -> None:
    """绑定请求级日志上下文（trace_id / tenant_id / user_id）。

    Args:
        **fields: 上下文字段，键统一由调用方使用 snake_case。
    """
    configure_logging()
    structlog.contextvars.bind_contextvars(**fields)


def unbind_context(*keys: str) -> None:
    """解绑指定日志上下文字段。

    Args:
        *keys: 需要解绑的字段名。
    """
    configure_logging()
    structlog.contextvars.unbind_contextvars(*keys)


def clear_context() -> None:
    """清空全部日志上下文（请求结束时调用）。"""
    configure_logging()
    structlog.contextvars.clear_contextvars()


def reset_logging_for_test() -> None:
    """重置日志初始化标记，供测试重复调用 `configure_logging`。"""
    global _configured
    _configured = False
