"""缓存后端**装配**用例（R2-ENG-01，审计 2026-10-29）。

这组用例验证的是「装配」，不是「Store 的行为」——两者的区别正是本轮缺陷的本质：

- `tests/unit/test_idempotency.py:174+` 早已直接构造
  `RedisIdempotencyStore(_FakeRedis())`，证明"Redis 实现能工作"；
- 但**生产代码从不构造它**：`IdempotencyManager()` 无参、`RateLimiter()` 走默认内存，
  而 `deploy/.env.example` 却写着 `OA_IDEMPOTENCY_BACKEND=redis`。

所以「Store 有单测」不代表「能力已接通」。本文件断言的是：
用给定配置创建出来的对象，其内部 store 的真实类型是什么。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.cache_backend import reset_shared_redis, validate_cache_backend_startup
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.idempotency import (
    build_idempotency_store,
    configure_idempotency_manager,
    get_idempotency_manager,
    reset_idempotency_manager,
)
from app.core.ratelimit import RateLimiter, build_ratelimit_store


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    """每个用例前后重置单例，避免装配结果在用例间串味。"""
    reset_idempotency_manager()
    reset_shared_redis()
    yield
    reset_idempotency_manager()
    reset_shared_redis()


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "test",
        "redis_url": "redis://127.0.0.1:6379/0",
    }
    base.update(overrides)
    return Settings(**base)


class TestDefaultBackends:
    """默认配置必须仍是进程内实现（保证离线单测与单副本交付的行为不变）。"""

    def test_idempotency_defaults_to_memory(self) -> None:
        store = build_idempotency_store(_settings())
        assert type(store).__name__ == "InMemoryIdempotencyStore"

    def test_ratelimit_defaults_to_memory(self) -> None:
        store = build_ratelimit_store(_settings())
        assert type(store).__name__ == "InMemoryRateLimitStore"

    def test_rate_limiter_default_store_name(self) -> None:
        assert RateLimiter(settings=_settings()).store_name == "InMemoryRateLimitStore"


class TestRedisAssembly:
    """声明 redis 时必须真的装配出 Redis 实现（本轮缺陷的核心断言）。"""

    def test_idempotency_store_is_redis(self) -> None:
        store = build_idempotency_store(_settings(idempotency_backend="redis"))
        assert type(store).__name__ == "RedisIdempotencyStore"

    def test_ratelimit_store_is_redis(self) -> None:
        store = build_ratelimit_store(_settings(ratelimit_backend="redis"))
        assert type(store).__name__ == "RedisRateLimitStore"

    def test_rate_limiter_store_name_reflects_config(self) -> None:
        limiter = RateLimiter(settings=_settings(ratelimit_backend="redis"))
        assert limiter.store_name == "RedisRateLimitStore"

    def test_idempotency_manager_reflects_config(self) -> None:
        manager = configure_idempotency_manager(_settings(idempotency_backend="redis"))
        assert manager.store_name == "RedisIdempotencyStore"

    def test_depends_entrypoint_stays_zero_arg(self) -> None:
        """`get_idempotency_manager` **必须保持无参**——它是 FastAPI 依赖。

        2026-10-29 实测教训：给它加一个 `settings: Settings | None = None`，
        FastAPI 会把 `Settings` 当作可选 **request body** 解析，于是全站每个
        POST 端点的请求体引用从具名模型（如 `CreateTaskRequest`）退化成
        `Body_create_task_...` 匿名包装，**92 处 OpenAPI schema 漂移**，
        `test_snapshot_is_in_sync_with_generated_schema` 立刻变红。

        装配期需要注入配置时，用 `configure_idempotency_manager(settings)`。
        """
        import inspect

        params = inspect.signature(get_idempotency_manager).parameters
        assert not params, (
            f"get_idempotency_manager 必须无参（它被 Depends 调用），实际参数：{list(params)}"
        )

    def test_shared_client_has_socket_timeout(self) -> None:
        """共享客户端必须带超时——redis-py 默认 `None` 表示永久阻塞。

        这条断言防的是「有人改动 `from_url` 参数时把超时丢掉」：那个改动在单测里
        完全看不出来（不连真 Redis），却会把故障形态从"多副本失效"放大成
        "Valkey 一 hang、全线写请求挂死"。
        """
        from app.core.cache_backend import get_shared_redis

        client = get_shared_redis(_settings())
        assert client is not None
        pool = getattr(client, "connection_pool", None)
        assert pool is not None
        assert pool.connection_kwargs.get("socket_timeout") is not None
        assert pool.connection_kwargs.get("socket_connect_timeout") is not None


class TestFailFast:
    """声明 redis 但缺 url 时必须启动失败，不得静默回落内存。"""

    def test_idempotency_redis_without_url_raises(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            validate_cache_backend_startup(_settings(idempotency_backend="redis", redis_url=""))
        assert exc_info.value.code == ErrorCode.CONFIG_ERROR

    def test_ratelimit_redis_without_url_raises(self) -> None:
        with pytest.raises(ApiError) as exc_info:
            validate_cache_backend_startup(_settings(ratelimit_backend="redis", redis_url=""))
        assert exc_info.value.code == ErrorCode.CONFIG_ERROR

    def test_memory_backend_without_url_is_fine(self) -> None:
        validate_cache_backend_startup(_settings(redis_url=""))

    def test_build_store_redis_without_url_raises(self) -> None:
        with pytest.raises(ApiError):
            build_idempotency_store(_settings(idempotency_backend="redis", redis_url=""))
