"""请求限流（架构文档 3.0.3 错误码 20006）。

策略：
- 固定窗口计数，按「租户 + 用户 + 路径」维度限流；
- 适配层无状态、多副本部署，内存实现仅适用于单实例；
  `redis` 后端已实现，T02/T04 通过 `OA_RATELIMIT_BACKEND=redis` 切换；
- 触发限流统一抛 `ErrorCode.RATE_LIMITED`，前端展示业务语言文案。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Final, Protocol

from fastapi import Request

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认限流键前缀
DEFAULT_KEY_PREFIX: Final[str] = "oa:ratelimit"


@dataclass(slots=True)
class _Window:
    """固定窗口计数条目。

    Attributes:
        count: 当前窗口计数。
        window_start: 窗口起始时间戳。
    """

    count: int
    window_start: float


class RateLimitStore(Protocol):
    """限流存储契约。"""

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        """记录一次命中。

        Args:
            key: 限流键。
            limit: 窗口内允许的最大次数。
            window_seconds: 窗口长度（秒）。

        Returns:
            tuple[bool, int, int]: `(是否放行, 剩余次数, 重试等待秒数)`。
        """
        ...


class InMemoryRateLimitStore:
    """进程内固定窗口限流存储（T01 默认实现）。"""

    def __init__(self) -> None:
        """初始化内存限流存储。"""
        self._windows: dict[str, _Window] = {}
        self._lock = asyncio.Lock()

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        """记录一次命中并判断是否放行。

        Args:
            key: 限流键。
            limit: 窗口内允许的最大次数。
            window_seconds: 窗口长度（秒）。

        Returns:
            tuple[bool, int, int]: `(是否放行, 剩余次数, 重试等待秒数)`。
        """
        async with self._lock:
            now = time.monotonic()
            window = self._windows.get(key)
            if window is None or now - window.window_start >= window_seconds:
                window = _Window(count=0, window_start=now)
                self._windows[key] = window
            window.count += 1
            remaining = max(limit - window.count, 0)
            retry_after = max(int(window_seconds - (now - window.window_start)) + 1, 1)
            allowed = window.count <= limit
            return allowed, remaining, retry_after


class RedisRateLimitStore:
    """Redis 固定窗口限流存储（T02 启用，基于 `INCR` + `EXPIRE`）。

    Attributes:
        _client: redis.asyncio 客户端。
        _prefix: 键前缀。
    """

    def __init__(self, client: Any, prefix: str = DEFAULT_KEY_PREFIX) -> None:
        """初始化 Redis 限流存储。

        Args:
            client: `redis.asyncio.Redis` 实例。
            prefix: 键前缀。
        """
        self._client = client
        self._prefix = prefix

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        """记录一次命中并判断是否放行。

        Args:
            key: 限流键。
            limit: 窗口内允许的最大次数。
            window_seconds: 窗口长度（秒）。

        Returns:
            tuple[bool, int, int]: `(是否放行, 剩余次数, 重试等待秒数)`。
        """
        redis_key = f"{self._prefix}:{key}"
        pipeline = self._client.pipeline()
        pipeline.incr(redis_key)
        pipeline.expire(redis_key, window_seconds)
        results = await pipeline.execute()
        count = int(results[0]) if results else 1
        allowed = count <= limit
        return allowed, max(limit - count, 0), window_seconds


class RateLimiter:
    """限流器。

    Attributes:
        _store: 限流存储实现。
        _enabled: 是否启用限流。
        _default_limit: 默认窗口配额。
    """

    def __init__(
        self,
        store: RateLimitStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        """初始化限流器。

        Args:
            store: 限流存储，缺省使用内存实现。
            settings: 可选配置实例。
        """
        conf = settings or get_settings()
        self._store: RateLimitStore = store or InMemoryRateLimitStore()
        self._enabled: bool = conf.ratelimit_enabled
        self._default_limit: int = conf.ratelimit_default_per_minute

    @property
    def enabled(self) -> bool:
        """是否启用限流。"""
        return self._enabled

    async def check(
        self,
        tenant_id: str,
        user_id: str,
        path: str,
        *,
        limit: int | None = None,
        window_seconds: int = 60,
    ) -> None:
        """执行一次限流检查。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            path: 请求路径。
            limit: 窗口配额，缺省取配置默认值。
            window_seconds: 窗口长度（秒），默认 60。

        Raises:
            ApiError: 触发限流（20006）。
        """
        if not self._enabled:
            return
        effective_limit = limit if limit and limit > 0 else self._default_limit
        key = f"{tenant_id}:{user_id}:{path}"
        allowed, _remaining, retry_after = await self._store.hit(
            key, effective_limit, window_seconds
        )
        if not allowed:
            logger.warning(
                "ratelimit_triggered", path=path, limit=effective_limit, retry_after=retry_after
            )
            raise ApiError(
                ErrorCode.RATE_LIMITED,
                internal_detail=f"path={path} limit={effective_limit} retry_after={retry_after}s",
            )


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """获取限流器单例。

    Returns:
        RateLimiter: 进程内唯一实例。
    """
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def get_request_rate_limiter(request: Request) -> RateLimiter:
    """获取当前请求生效的限流器。

    优先使用 `app.state.rate_limiter`（`create_app(settings=...)` 注入时按注入配置构造），
    缺省回落到全局单例。

    Args:
        request: FastAPI 请求对象。

    Returns:
        RateLimiter: 限流器实例。
    """
    app_limiter = getattr(request.app.state, "rate_limiter", None)
    if isinstance(app_limiter, RateLimiter):
        return app_limiter
    return get_rate_limiter()


def reset_rate_limiter() -> None:
    """重置限流器单例，供测试隔离用例。"""
    global _limiter
    _limiter = None
