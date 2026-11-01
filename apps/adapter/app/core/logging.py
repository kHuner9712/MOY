"""结构化日志（架构文档 9.3 日志规范）。

要点：
- 使用 structlog 输出 JSON 日志，字段固定包含
  `timestamp / level / trace_id / tenant_id / user_id / event`；
- `trace_id` 全链路透传：接入层生成 → 响应头回传 → 转发下游时写入 `X-Trace-Id`；
- **严禁记录**：用户令牌、工具授权凭据、文档正文全文。

脱敏策略：`redact()` 对令牌、密码、密钥、凭据类键做统一掩码，并由
`_redact_event_dict` 作为 **processor 挂进链路**（位于上下文注入之前）——
因此掩码是**默认生效**的，不依赖调用方自觉（P2-02：此前 `redact()` 零引用，
"自动掩码"实际不存在）。
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

#: 已派生/已截断字段的后缀白名单：这类键的**值本身**不是原值（后缀、摘要、布尔），
#: 掩码它们只会毁掉排障信息，且不会泄露任何原文。
#:
#: 为什么必须存在：`credential_ref_suffix`（`ref_suffix()` 刻意只保留后 6 位，
#: 架构文档 11.4 要求日志里留这个后缀以便定位）含有 `credential` 子串，
#: 若只用 `_SENSITIVE_KEYS` 的包含匹配，它会被整段掩成 `***`——
#: 既没多一分安全（本来就不是原文），又让"绑错了哪个凭据"彻底查不出来。
#:
#: **只收「尾部截断 + 摘要」两类**：`_prefix` / `_tail` 被明确排除——
#: 密钥的**前半段同样是密钥**（`api_key_prefix` / `client_secret_tail` 落盘
#: 等于部分泄露），"名字像派生"不能成为豁免原文的理由（F7 收窄）。
_DERIVED_KEY_SUFFIXES: Final[tuple[str, ...]] = (
    "_suffix",
    "_hash",
    "_digest",
    "_masked",
    "_present",
    "_length",
)

# 日志上下文字段名（与架构文档 9.3 完全一致）
TRACE_ID_KEY: Final[str] = "trace_id"
TENANT_ID_KEY: Final[str] = "tenant_id"
USER_ID_KEY: Final[str] = "user_id"

_configured: bool = False


def is_sensitive(key: str) -> bool:
    """判断键名是否属于敏感字段。

    判定顺序：**先认「已派生」后缀**（`_suffix` / `_hash` / `_present` 等，
    其值不是原文，掩码只会丢排障信息），再走包含匹配。

    Args:
        key: 待判断的字段名。

    Returns:
        bool: 命中敏感字段返回 True。
    """
    lowered = key.lower()
    if lowered.endswith(_DERIVED_KEY_SUFFIXES):
        return False
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


def _redact_event_dict(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """processor 形态的脱敏：对 `event_dict` 的敏感键做掩码（架构文档 9.3）。

    为什么必须是 processor 而不是"请调用方记得调 `redact()`"：`app/core/logging.py`
    此前只**提供** `redact()` 却零处调用，于是"敏感字段自动掩码"实际不存在——
    一旦有人写 `logger.info("callback", client_secret=...)`，密钥就原样落盘。
    挂进 processors 链后是**默认生效**，不依赖任何调用方自觉。

    浅层 mask 即可：`event_dict` 的顶层键就是一次日志调用的全部字段
    （嵌套值仍由 `redact()` 递归处理 dict / list，避免 `{"payload": {"password": ...}}`
    这类嵌套结构漏网）。原地修改 `event_dict`，保持 structlog processor 的常规语义。

    Args:
        _logger: structlog 传入的 logger（未使用）。
        _method_name: 日志方法名（未使用）。
        event_dict: 本条日志的字段字典。

    Returns:
        dict[str, Any]: 脱敏后的字段字典。
    """
    for key in list(event_dict):
        event_dict[key] = redact(event_dict[key], key=str(key))
    return event_dict


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
        # P2-02：脱敏必须**先于**上下文注入——注入会补 trace_id/tenant_id 等字段，
        # 放在注入之后会让"补齐的字段"绕开掩码规则（日后若有同名敏感键即漏）。
        _redact_event_dict,
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
