"""幂等键管理（架构文档 3.0.4 幂等条）。

规则：
- 所有写接口必须携带 `Idempotency-Key` 头（UUID v4，客户端生成，24h 内有效）；
- 首次请求正常执行并缓存结果；重复请求命中缓存，返回首次结果并置 `code=20007`；
- 本期（T01）默认内存实现，`redis` 后端已实现但默认关闭，T02/T04 切换。

接口预留：`IdempotencyStore` 抽象出存储契约，新增后端无需改动调用方。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 幂等键请求头
IDEMPOTENCY_HEADER: Final[str] = "Idempotency-Key"

#: 默认 TTL：24 小时（架构文档 3.0.4）
DEFAULT_TTL_SECONDS: Final[int] = 86_400


@dataclass(slots=True)
class _Entry:
    """幂等缓存条目。

    Attributes:
        fingerprint: 请求指纹（方法 + 路径 + 租户 + 用户），用于识别键复用。
        result: 首次执行结果，缓存用于重复请求回放。
        created_at: 创建时间戳。
        completed: 是否已完成（未完成时重复请求返回 20005）。
    """

    fingerprint: str
    result: Any = None
    created_at: float = field(default_factory=time.monotonic)
    completed: bool = False


class IdempotencyStore(Protocol):
    """幂等存储契约。

    抽象目的：内存实现（T01）与 Redis 实现（T02）行为一致，
    更换后端不改动服务层与路由层代码。
    """

    async def get(self, key: str) -> Any | None:
        """读取已完成的首次执行结果。

        Args:
            key: 幂等键。

        Returns:
            Any | None: 首次结果；无记录时返回 `None`。
        """
        ...

    async def peek(self, key: str) -> str | None:
        """读取已占用的请求指纹（用于键复用冲突检测）。

        Args:
            key: 幂等键。

        Returns:
            str | None: 指纹；无记录或已过期时返回 `None`。
        """
        ...

    async def acquire(self, key: str, fingerprint: str, ttl_seconds: int) -> bool:
        """尝试占用幂等键。

        Args:
            key: 幂等键。
            fingerprint: 请求指纹。
            ttl_seconds: 过期秒数。

        Returns:
            bool: 占用成功返回 True（首次）；已被占用返回 False（重复）。
        """
        ...

    async def store_result(self, key: str, result: Any, ttl_seconds: int) -> None:
        """缓存首次执行结果。

        Args:
            key: 幂等键。
            result: 执行结果。
            ttl_seconds: 过期秒数。
        """
        ...

    async def release(self, key: str) -> None:
        """释放执行失败的幂等键，允许客户端重试。

        Args:
            key: 幂等键。
        """
        ...


class InMemoryIdempotencyStore:
    """进程内幂等存储（T01 默认实现）。

    注意：适配层无状态、可水平扩展，本实现仅适用于单实例；
    多副本部署必须切换 `OA_IDEMPOTENCY_BACKEND=redis`。

    Attributes:
        _entries: 幂等条目表。
        _lock: 异步锁，保证并发安全。
    """

    def __init__(self) -> None:
        """初始化内存存储。"""
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """读取已完成结果，并惰性清理过期条目。

        Args:
            key: 幂等键。

        Returns:
            Any | None: 首次结果；无记录或未完成时返回 `None`。
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry.created_at > DEFAULT_TTL_SECONDS:
                self._entries.pop(key, None)
                return None
            if not entry.completed:
                return None
            return entry.result

    async def peek(self, key: str) -> str | None:
        """读取已占用的请求指纹，并惰性清理过期条目。

        Args:
            key: 幂等键。

        Returns:
            str | None: 指纹；无记录或已过期时返回 `None`。
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry.created_at > DEFAULT_TTL_SECONDS:
                self._entries.pop(key, None)
                return None
            return entry.fingerprint

    async def acquire(self, key: str, fingerprint: str, ttl_seconds: int) -> bool:
        """尝试占用幂等键。

        Args:
            key: 幂等键。
            fingerprint: 请求指纹。
            ttl_seconds: 过期秒数。

        Returns:
            bool: 首次占用返回 True。
        """
        async with self._lock:
            existing = self._entries.get(key)
            if existing is not None and time.monotonic() - existing.created_at <= ttl_seconds:
                return False
            self._entries[key] = _Entry(fingerprint=fingerprint)
            return True

    async def store_result(self, key: str, result: Any, ttl_seconds: int) -> None:
        """缓存首次执行结果。

        Args:
            key: 幂等键。
            result: 执行结果。
            ttl_seconds: 过期秒数，当前实现不依赖该参数（惰性清理）。
        """
        del ttl_seconds  # 内存实现由 DEFAULT_TTL_SECONDS 惰性清理，无需精确 TTL
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:  # pragma: no cover - 正常流程不会发生
                return
            entry.result = result
            entry.completed = True

    async def release(self, key: str) -> None:
        """释放幂等键。

        Args:
            key: 幂等键。
        """
        async with self._lock:
            self._entries.pop(key, None)


class RedisIdempotencyStore:
    """Redis 幂等存储（T02 启用）。

    键设计：`oa:idem:{tenant_id}:{user_id}:{idempotency_key}`，与架构文档 9.5 一致。

    Attributes:
        _client: redis.asyncio 客户端。
        _prefix: 键前缀。
    """

    def __init__(self, client: Any, prefix: str = "oa:idem") -> None:
        """初始化 Redis 存储。

        Args:
            client: `redis.asyncio.Redis` 实例。
            prefix: 键前缀。
        """
        self._client = client
        self._prefix = prefix

    def _build_key(self, key: str) -> str:
        """拼接完整 Redis 键。

        Args:
            key: 幂等键。

        Returns:
            str: 完整键。
        """
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> Any | None:
        """读取已完成结果。

        Args:
            key: 幂等键。

        Returns:
            Any | None: 首次结果 JSON；无记录时返回 `None`。
        """
        import json

        raw = await self._client.get(self._build_key(key))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):  # pragma: no cover - 防御性分支
            return None
        if not isinstance(payload, dict) or not payload.get("done"):
            return None
        return payload.get("result")

    async def peek(self, key: str) -> str | None:
        """读取已占用的请求指纹。

        Args:
            key: 幂等键。

        Returns:
            str | None: 指纹；无记录或未完成时返回 `None`。
        """
        import json

        raw = await self._client.get(self._build_key(key))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):  # pragma: no cover - 防御性分支
            return None
        if not isinstance(payload, dict):
            return None
        fingerprint = payload.get("fingerprint")
        return str(fingerprint) if isinstance(fingerprint, str) else None

    async def acquire(self, key: str, fingerprint: str, ttl_seconds: int) -> bool:
        """基于 `SET NX EX` 原子占用幂等键。

        Args:
            key: 幂等键。
            fingerprint: 请求指纹。
            ttl_seconds: 过期秒数。

        Returns:
            bool: 占用成功返回 True。
        """
        import json

        payload = json.dumps({"fingerprint": fingerprint, "done": False}, ensure_ascii=False)
        acquired = await self._client.set(self._build_key(key), payload, nx=True, ex=ttl_seconds)
        return bool(acquired)

    async def store_result(self, key: str, result: Any, ttl_seconds: int) -> None:
        """回写首次执行结果（保留指纹，供键复用冲突检测）。

        Args:
            key: 幂等键。
            result: 执行结果。
            ttl_seconds: 过期秒数。
        """
        import json

        raw = await self._client.get(self._build_key(key))
        fingerprint = ""
        if raw is not None:
            try:
                existing = json.loads(raw)
                if isinstance(existing, dict):
                    fingerprint = str(existing.get("fingerprint", ""))
            except (TypeError, ValueError):  # pragma: no cover - 防御性分支
                fingerprint = ""
        payload = json.dumps(
            {"fingerprint": fingerprint, "done": True, "result": result},
            ensure_ascii=False,
            default=str,
        )
        await self._client.set(self._build_key(key), payload, ex=ttl_seconds)

    async def release(self, key: str) -> None:
        """释放幂等键。

        Args:
            key: 幂等键。
        """
        await self._client.delete(self._build_key(key))


class IdempotencyManager:
    """幂等键管理器（架构文档 3.6 类图）。

    统一封装「检查 → 执行 → 回写 / 释放」流程，路由层无需感知存储实现。
    """

    def __init__(
        self,
        store: IdempotencyStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        """初始化管理器。

        Args:
            store: 幂等存储，缺省按配置选择内存实现。
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()
        self._store: IdempotencyStore = store or InMemoryIdempotencyStore()

    @property
    def ttl_seconds(self) -> int:
        """幂等键有效期（秒）。"""
        return self._settings.idempotency_ttl_seconds

    async def check_or_set(self, key: str, fingerprint: str) -> bool:
        """检查并占用幂等键。

        同一幂等键被用于**不同请求**时视为键复用冲突（20005，架构文档 3.0.3 冲突语义），
        避免不同接口的首次结果被交叉回放。

        Args:
            key: `Idempotency-Key` 头值。
            fingerprint: 请求指纹（方法 + 路径 + 租户 + 用户）。

        Returns:
            bool: 首次请求返回 True；重复请求返回 False。

        Raises:
            ApiError: 幂等键为空（20001）；同键不同请求（20005）。
        """
        if not key or not key.strip():
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="missing Idempotency-Key header"
            )
        normalized = key.strip()
        existing = await self._store.peek(normalized)
        if existing is not None and existing != fingerprint:
            raise ApiError(
                ErrorCode.CONFLICT,
                internal_detail="Idempotency-Key reused for a different request",
            )
        acquired = await self._store.acquire(normalized, fingerprint, self.ttl_seconds)
        logger.debug("idempotency_check", key_suffix=normalized[-8:], acquired=acquired)
        return acquired

    async def get_result(self, key: str) -> Any | None:
        """读取首次执行结果。

        Args:
            key: `Idempotency-Key` 头值。

        Returns:
            Any | None: 首次结果；未命中返回 `None`。
        """
        return await self._store.get(key.strip())

    async def store_result(self, key: str, result: Any) -> None:
        """回写首次执行结果。

        Args:
            key: `Idempotency-Key` 头值。
            result: 执行结果。
        """
        await self._store.store_result(key.strip(), result, self.ttl_seconds)

    async def release(self, key: str) -> None:
        """释放幂等键（执行失败时调用，允许客户端重试）。

        Args:
            key: `Idempotency-Key` 头值。
        """
        await self._store.release(key.strip())

    async def execute(self, key: str, fingerprint: str, factory: Any) -> tuple[Any, bool]:
        """执行带幂等保护的写操作。

        Args:
            key: `Idempotency-Key` 头值。
            fingerprint: 请求指纹。
            factory: 无参异步可调用对象，返回首次执行结果。

        Returns:
            tuple[Any, bool]: `(结果, 是否重复请求)`。重复请求时结果为首次缓存值。

        Raises:
            ApiError: 执行失败时释放幂等键并向上抛出。
        """
        acquired = await self.check_or_set(key, fingerprint)
        if not acquired:
            cached = await self.get_result(key)
            logger.debug("idempotency_replayed", key_suffix=key.strip()[-8:])
            return cached, True
        try:
            result = await factory()
        except ApiError:
            await self.release(key)
            raise
        except Exception:
            await self.release(key)
            raise
        await self.store_result(key, result)
        return result, False


_manager: IdempotencyManager | None = None


def get_idempotency_manager() -> IdempotencyManager:
    """获取幂等管理器单例。

    Returns:
        IdempotencyManager: 进程内唯一实例。
    """
    global _manager
    if _manager is None:
        _manager = IdempotencyManager()
    return _manager


def reset_idempotency_manager() -> None:
    """重置幂等管理器单例，供测试隔离用例。"""
    global _manager
    _manager = None


def build_fingerprint(method: str, path: str, tenant_id: str, user_id: str) -> str:
    """构造请求指纹。

    同一幂等键若被用于不同请求（指纹不一致），视为键复用冲突（20005）。

    Args:
        method: HTTP 方法。
        path: 请求路径。
        tenant_id: 租户 ID。
        user_id: 用户 ID。

    Returns:
        str: 指纹字符串。
    """
    return f"{method.upper()}:{path}:{tenant_id}:{user_id}"
