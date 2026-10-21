"""限流单元测试（架构文档 3.0.3 错误码 20006）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.ratelimit import (
    InMemoryRateLimitStore,
    RateLimiter,
    RedisRateLimitStore,
)

pytestmark = pytest.mark.unit


class _FakePipeline:
    """最小 Redis pipeline 替身。"""

    def __init__(self, client: _FakeRedis) -> None:
        """初始化 pipeline。

        Args:
            client: Redis 替身实例。
        """
        self._client = client
        self._queued: list[tuple[str, str]] = []

    def incr(self, key: str) -> _FakePipeline:
        """排队 INCR。

        Args:
            key: 键名。

        Returns:
            _FakePipeline: 自身。
        """
        self._queued.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int) -> _FakePipeline:
        """排队 EXPIRE。

        Args:
            key: 键名。
            seconds: 过期秒数。

        Returns:
            _FakePipeline: 自身。
        """
        self._queued.append(("expire", key))
        return self

    async def execute(self) -> list[int]:
        """执行排队命令。

        Returns:
            list[int]: 每条命令的执行结果。
        """
        results: list[int] = []
        for command, key in self._queued:
            if command == "incr":
                current = int(self._client.store.get(key, "0"))
                current += 1
                self._client.store[key] = str(current)
                results.append(current)
            else:
                results.append(1)
        self._queued.clear()
        return results


class _FakeRedis:
    """最小 Redis 替身。"""

    def __init__(self) -> None:
        """初始化内存字典。"""
        self.store: dict[str, str] = {}

    def pipeline(self) -> _FakePipeline:
        """创建 pipeline。

        Returns:
            _FakePipeline: pipeline 实例。
        """
        return _FakePipeline(self)


async def test_allows_within_limit() -> None:
    """限额内请求全部放行。"""
    limiter = RateLimiter(settings=Settings(ratelimit_enabled=True))
    for _ in range(3):
        await limiter.check("tnt_1", "usr_1", "/api/v1/model/generate", limit=3)


async def test_blocks_over_limit() -> None:
    """超限请求返回 20006。"""
    limiter = RateLimiter(settings=Settings(ratelimit_enabled=True))
    await limiter.check("tnt_1", "usr_1", "/api/v1/model/generate", limit=1)
    with pytest.raises(ApiError) as exc_info:
        await limiter.check("tnt_1", "usr_1", "/api/v1/model/generate", limit=1)
    assert exc_info.value.code == ErrorCode.RATE_LIMITED


async def test_isolates_by_tenant_and_path() -> None:
    """不同租户/路径互不影响。"""
    limiter = RateLimiter(settings=Settings(ratelimit_enabled=True))
    await limiter.check("tnt_1", "usr_1", "/api/v1/model/generate", limit=1)
    await limiter.check("tnt_2", "usr_1", "/api/v1/model/generate", limit=1)
    await limiter.check("tnt_1", "usr_1", "/api/v1/model/catalog", limit=1)


async def test_disabled_limiter_passes_everything() -> None:
    """限流关闭时不做任何判定。"""
    limiter = RateLimiter(settings=Settings(ratelimit_enabled=False))
    assert limiter.enabled is False
    for _ in range(50):
        await limiter.check("tnt_1", "usr_1", "/api/v1/model/generate", limit=1)


async def test_default_limit_from_settings() -> None:
    """未显式指定限额时取配置默认值。"""
    limiter = RateLimiter(settings=Settings(ratelimit_enabled=True, ratelimit_default_per_minute=2))
    await limiter.check("tnt_1", "usr_1", "/x")
    await limiter.check("tnt_1", "usr_1", "/x")
    with pytest.raises(ApiError):
        await limiter.check("tnt_1", "usr_1", "/x")


async def test_memory_store_reports_remaining_and_retry() -> None:
    """内存存储返回剩余次数与重试等待时间。"""
    store = InMemoryRateLimitStore()
    allowed, remaining, retry_after = await store.hit("k", 2, 60)
    assert allowed is True
    assert remaining == 1
    assert retry_after >= 1


async def test_memory_store_resets_window() -> None:
    """窗口过期后计数重置。"""
    store = InMemoryRateLimitStore()
    await store.hit("k", 1, 60)
    allowed, _, _ = await store.hit("k", 1, 60)
    assert allowed is False
    # 人为推进时间，模拟窗口切换
    store._windows["k"].window_start -= 120  # noqa: SLF001 - 测试需要构造窗口过期场景
    allowed, remaining, _ = await store.hit("k", 1, 60)
    assert allowed is True
    assert remaining == 0


async def test_redis_store_counts() -> None:
    """Redis 后端计数与放行判定正确。"""
    store = RedisRateLimitStore(_FakeRedis())  # type: ignore[arg-type]
    allowed, remaining, window = await store.hit("k", 2, 60)
    assert allowed is True
    assert remaining == 1
    assert window == 60
    allowed, remaining, _ = await store.hit("k", 2, 60)
    assert allowed is True
    assert remaining == 0
    allowed, _, _ = await store.hit("k", 2, 60)
    assert allowed is False


def test_limiter_uses_injected_store() -> None:
    """注入自定义存储后不再创建默认内存存储。"""
    store: Any = InMemoryRateLimitStore()
    limiter = RateLimiter(store=store, settings=Settings(ratelimit_enabled=True))
    assert limiter.enabled is True
