"""进程级缓存后端（幂等 / 限流 / 凭据缓存共用）。

R2-ENG-01（审计 2026-10-29）背景：

审计发现「幂等/限流声称支持 Redis 后端」，但事实是——
- `RedisIdempotencyStore` / `RedisRateLimitStore` **全仓只在 `tests/` 里被实例化**；
- `IdempotencyManager()` 与 `RateLimiter()` 都是无参构造，恒用进程内实现；
- `deploy/.env.example:185` 却写着 `OA_IDEMPOTENCY_BACKEND=redis`；
- `main.py` 的告警条件恰好是「配成 memory」→ **配成 redis 反而让告警永久沉默**。

即「配置幻觉 + 告警被幻觉绕过」：运维看到配置是 redis、日志也干净，会以为
多副本已就绪；实际扩容那一刻幂等静默失效、限流按副本数放大 N 倍且零提示。

本模块是**唯一的** Redis 客户端装配点，三条硬约束：

1. **超时不可缺省**。`redis-py` 的 `socket_timeout` 默认是 `None`——语义不是
   "很快超时"，而是**永久阻塞**。一旦把幂等/限流也接到无超时客户端上，
   Valkey 一 hang，故障就从「多副本静默失效」升级为「全线写请求挂死」。
   这个坑在 `credential_service.py` 里已经存在（只有凭据解析用它，影响面小），
   本模块统一收敛，避免复制同一缺陷。
2. **单例复用**。每个组件自建连接池会在多副本下把 Valkey 的连接数打满。
3. **关闭纳入 lifespan**，避免进程退出时连接泄漏。
"""

from __future__ import annotations

from typing import Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 套接字读写/建连超时（秒）。**不可为 None**，理由见模块 docstring 第 1 条。
#: 1.0s 的取值依据：栈内 Valkey 与 adapter 同网段，正常 RTT < 5ms；1s 已是
#: 200 倍余量，超时即说明对端异常，继续等只会把故障扩散到所有写请求。
DEFAULT_SOCKET_TIMEOUT: Final[float] = 1.0

#: 健康检查间隔（秒）：让断线能被主动发现，而不是等某次业务请求撞上。
DEFAULT_HEALTH_CHECK_INTERVAL: Final[int] = 30

_client: object | None = None


def get_shared_redis(settings: Settings | None = None) -> object | None:
    """按配置返回共享 Redis 客户端（单例）。未配置 `redis_url` 时返回 None。

    Args:
        settings: 可选配置实例（注入优先，便于测试）。

    Returns:
        object | None: `redis.asyncio.Redis` 实例；未配置地址时为 None。

    Raises:
        ApiError: 导入 redis 客户端失败（依赖缺失）。
    """
    global _client
    conf = settings or get_settings()
    if not conf.redis_url:
        return None
    if _client is not None:
        return _client
    try:
        import redis.asyncio as redis_asyncio
    except ModuleNotFoundError as exc:  # pragma: no cover - 依赖缺失属环境问题
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail="redis 包缺失，无法装配缓存后端",
        ) from exc

    _client = redis_asyncio.from_url(
        conf.redis_url,
        decode_responses=True,
        # ⚠ 这三个参数是本模块存在的理由，删任意一个都会把故障形态放大
        socket_timeout=DEFAULT_SOCKET_TIMEOUT,
        socket_connect_timeout=DEFAULT_SOCKET_TIMEOUT,
        health_check_interval=DEFAULT_HEALTH_CHECK_INTERVAL,
    )
    return _client


async def aclose_shared_redis() -> None:
    """关闭并丢弃共享客户端（lifespan 关闭期调用）。"""
    global _client
    client = _client
    _client = None
    if client is None:
        return
    aclose = getattr(client, "aclose", None)
    if aclose is None:  # pragma: no cover - 兼容旧版 redis-py 的 close()
        aclose = getattr(client, "close", None)
    if aclose is None:  # pragma: no cover
        return
    try:
        await aclose()
    except Exception:  # pragma: no cover - 关停期异常不得掩盖正常退出
        logger.warning("shared_redis_close_failed", exc_info=True)


def reset_shared_redis() -> None:
    """丢弃单例引用但**不关闭**（测试隔离用：事件循环可能已关闭）。"""
    global _client
    _client = None


def validate_cache_backend_startup(settings: Settings | None = None) -> None:
    """启动期校验：声称用 redis 就必须真的能装配（fail-fast，不静默回落）。

    为什么必须 fail-fast 而不是"回落内存 + 告警"：回落的后果是「配置写着
    redis、行为却是 memory」，与本次审计要修的缺陷完全同型。宁可起不来，
    也不要带着一个行为与配置不符的系统对外服务。

    Args:
        settings: 可选配置实例。

    Raises:
        ApiError: 声明 redis 后端但缺少 `redis_url`（30004）。
    """
    conf = settings or get_settings()
    declared_redis: list[str] = []
    if conf.idempotency_backend == "redis":
        declared_redis.append("OA_IDEMPOTENCY_BACKEND")
    if conf.ratelimit_backend == "redis":
        declared_redis.append("OA_RATELIMIT_BACKEND")
    if declared_redis and not conf.redis_url:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"{'/'.join(declared_redis)}=redis 但 OA_REDIS_URL 为空："
                "无法装配 Redis 缓存后端。要么补 OA_REDIS_URL，要么显式改回 memory。"
            ),
        )
