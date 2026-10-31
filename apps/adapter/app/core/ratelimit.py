"""请求限流（架构文档 3.0.3 错误码 20006）。

策略：
- 固定窗口计数，按「租户 + 用户 + 路径」维度限流；
- 适配层无状态，**内存实现仅适用于单副本**（V1.0 三套编排均无 replicas，故默认正确）；
- 多副本必须切 `OA_RATELIMIT_BACKEND=redis`（R2-ENG-01 起该配置项真实生效，
  装配点见 `build_ratelimit_store`；此前的注释承诺的能力并不存在）；
- 触发限流统一抛 `ErrorCode.RATE_LIMITED`，前端展示业务语言文案。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Final, Protocol

from fastapi import Request

from app.core.cache_backend import get_shared_redis
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认限流键前缀
DEFAULT_KEY_PREFIX: Final[str] = "oa:ratelimit"

#: 内存窗口上限（防长跑进程无界增长；超出后优先淘汰最旧窗口）
DEFAULT_MAX_WINDOWS: Final[int] = 20_000


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
    """进程内固定窗口限流存储（T01 默认实现）。

    Attributes:
        _windows: 窗口表。
        _max_windows: 窗口上限（超出后优先淘汰已过期/最旧窗口）。
        _lock: 异步锁，保证并发安全。
    """

    def __init__(self, max_windows: int = DEFAULT_MAX_WINDOWS) -> None:
        """初始化内存限流存储。

        Args:
            max_windows: 窗口上限，防止长跑进程内存无界增长。
        """
        self._windows: dict[str, _Window] = {}
        self._max_windows = max_windows
        self._lock = asyncio.Lock()

    def _prune_locked(self, now: float, window_seconds: int) -> None:
        """容量达到上限时清理过期窗口，仍不足则淘汰最旧窗口（调用方须持锁）。

        Args:
            now: 当前单调时钟。
            window_seconds: 窗口长度（秒）。
        """
        if len(self._windows) < self._max_windows:
            return
        expired = [
            key
            for key, window in self._windows.items()
            if now - window.window_start >= window_seconds
        ]
        for key in expired:
            self._windows.pop(key, None)
        while len(self._windows) >= self._max_windows and self._windows:
            oldest = min(self._windows, key=lambda key: self._windows[key].window_start)
            self._windows.pop(oldest, None)

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
                if window is None:
                    self._prune_locked(now, window_seconds)
                window = _Window(count=0, window_start=now)
                self._windows[key] = window
            window.count += 1
            remaining = max(limit - window.count, 0)
            retry_after = max(int(window_seconds - (now - window.window_start)) + 1, 1)
            allowed = window.count <= limit
            return allowed, remaining, retry_after


class RedisRateLimitStore:
    """Redis 固定窗口限流存储（T02 启用，基于 `SET NX EX` + `INCR`）。

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
        # 窗口锚点必须固定在**首次命中**：`SET NX EX` 只在键不存在时写 TTL，
        # 后续命中只 INCR 不刷新 TTL。若每次命中都 EXPIRE，持续流量会把窗口
        # 不断往后推、计数永不归零（限流越收越紧直至全员 429，审计 2026-10-31 M1）。
        pipeline.set(redis_key, 0, nx=True, ex=window_seconds)
        pipeline.incr(redis_key)
        pipeline.ttl(redis_key)
        results = await pipeline.execute()
        count = int(results[1]) if len(results) > 1 else 1
        ttl = int(results[2]) if len(results) > 2 else window_seconds
        allowed = count <= limit
        retry_after = ttl if ttl and ttl > 0 else window_seconds
        return allowed, max(limit - count, 0), retry_after


def build_ratelimit_store(conf: Settings) -> RateLimitStore:
    """按配置构造限流存储。

    R2-ENG-01（审计 2026-10-29）：修复前 `RateLimiter()` 恒用
    `InMemoryRateLimitStore`，而 `RedisRateLimitStore` 全仓零实例化——
    文件头注释却声称「通过 `OA_RATELIMIT_BACKEND=redis` 切换」，
    且 `config.py` 里**根本没有**这个配置项。注释承诺的能力不存在，
    比"没实现"更危险：接手者会以为只差一个环境变量。

    Args:
        conf: 配置实例。

    Returns:
        RateLimitStore: 进程内或 Redis 实现。

    Raises:
        ApiError: 声明 redis 后端但无法取得客户端（30004）。
    """
    if conf.ratelimit_backend == "redis":
        client = get_shared_redis(conf)
        if client is None:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    "OA_RATELIMIT_BACKEND=redis 但未能取得 Redis 客户端"
                    "（OA_REDIS_URL 为空或依赖缺失）"
                ),
            )
        return RedisRateLimitStore(client)
    return InMemoryRateLimitStore()


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
            store: 限流存储，缺省按配置选择（`OA_RATELIMIT_BACKEND`）。
            settings: 可选配置实例。
        """
        conf = settings or get_settings()
        self._store: RateLimitStore = store or build_ratelimit_store(conf)
        self._enabled: bool = conf.ratelimit_enabled
        self._default_limit: int = conf.ratelimit_default_per_minute

    @property
    def store_name(self) -> str:
        """当前**实际**装配的存储实现类名（启动期断言用，见 R2-ENG-01）。"""
        return type(self._store).__name__

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
