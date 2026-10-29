"""幂等键管理（架构文档 3.0.4 幂等条）。

规则：
- 所有写接口必须携带 `Idempotency-Key` 头（UUID v4，客户端生成，24h 内有效）；
- 首次请求正常执行并缓存结果；重复请求命中缓存，返回首次结果并置 `code=20007`；
- 本期（T01）默认内存实现，`redis` 后端已实现但默认关闭，T02/T04 切换。

接口预留：`IdempotencyStore` 抽象出存储契约，新增后端无需改动调用方。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.core.cache_backend import get_shared_redis
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 幂等键请求头
IDEMPOTENCY_HEADER: Final[str] = "Idempotency-Key"

#: 默认 TTL：24 小时（架构文档 3.0.4）
DEFAULT_TTL_SECONDS: Final[int] = 86_400

#: 空请求体的规范化固定摘要（架构文档 3.0.5：不得跳过请求体摘要项）
EMPTY_BODY_SHA256: Final[str] = hashlib.sha256(b"").hexdigest()

#: 内存幂等条目上限（防长跑进程无界增长；超出后优先淘汰最旧的已完成条目）
DEFAULT_MAX_ENTRIES: Final[int] = 20_000

#: 存储键里的租户命名空间分隔符（L-16）
TENANT_KEY_SEPARATOR: Final[str] = ":"


def build_storage_key(key: str, tenant_id: str = "") -> str:
    """把 `Idempotency-Key` 拼成**带租户命名空间**的实际存储键（L-16）。

    为什么存储键必须带租户：`Idempotency-Key` 是**客户端生成**的 UUID，
    服务端无法假设它全局唯一。两个租户的客户端各自生成同一个 UUID 是完全合法的
    （弱随机源、批量灌数据的脚本、同一套模板复制的客户端都会撞）。
    修复前存储键只有 `oa:idem:{key}`，于是：

    - **跨租户结果回放**：租户 B 的首个写请求会命中租户 A 缓存的响应体并原样返回
      ——越权读，且被 `code=20007`（"重复请求"）包装成一次正常成功；
    - **跨租户误冲突**：即使指纹不同（指纹含 tenant），`peek` 命中后也只会抛
      `20005`，让租户 B 的合法请求永久失败，直到 24h TTL 到期。

    两条都源于"存储键比指纹少了租户维度"。指纹**已经**含租户（架构 3.0.5），
    这里补的是存储键那一侧：`{tenant_id}:{key}`。

    `tenant_id` 为空时**原样返回**，用于两种合法场景：
    ① 单测直接驱动存储层（不关心租户）；② 私有化单租户部署里调用方没解析出租户
    （此时空命名空间等价于唯一的默认租户，行为与修复前一致，不静默降级为"跨租户共享"）。

    Args:
        key: `Idempotency-Key` 头值（会去除首尾空白）。
        tenant_id: 生效租户 ID；空串表示不加命名空间。

    Returns:
        str: 实际存储键。内存实现直接用它做字典键；Redis 实现再拼前缀，
            最终形如 `oa:idem:{tenant_id}:{key}`。
    """
    normalized_key = key.strip()
    tenant = (tenant_id or "").strip()
    if not tenant:
        return normalized_key
    return f"{tenant}{TENANT_KEY_SEPARATOR}{normalized_key}"


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

    **`key` 的语义（L-16）**：存储层拿到的是 `IdempotencyManager` 已经过
    `build_storage_key()` 处理、带租户命名空间的**实际存储键**，不是原始
    `Idempotency-Key`。存储层不再自己拼租户维度——两处各拼一次必然漂移。
    """

    async def get(self, key: str) -> Any | None:
        """读取已完成的首次执行结果。

        Args:
            key: 幂等键。

        Returns:
            Any | None: 首次结果；无记录时返回 `None`。
        """
        ...

    async def is_completed(self, key: str) -> bool:
        """判断幂等键是否已完成首次执行。

        **为什么需要它**：`get()` 对「无记录」「未完成」都返回 `None`，
        重复请求无法区分「首次请求仍在执行」与「首次结果为空」。
        返回空结果冒充成功会让客户端误以为写操作已完成（审计 P2-3）。

        Args:
            key: 幂等键。

        Returns:
            bool: 已完成返回 True；无记录、已过期或仍在执行返回 False。
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
        _max_entries: 条目上限（超出后优先淘汰最旧的已完成条目）。
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        """初始化内存存储。

        Args:
            max_entries: 条目上限，防止长跑进程内存无界增长。
        """
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._max_entries = max_entries

    def _prune_locked(self, now: float) -> None:
        """清理过期条目；仍超上限时淘汰最旧的已完成条目（调用方须持锁）。

        未完成的条目（首次请求仍在执行）**不得淘汰**，否则重复请求会失去
        幂等保护；并发数本身有界，允许在极端情况下短暂超出上限。

        Args:
            now: 当前单调时钟。
        """
        if len(self._entries) < self._max_entries:
            return
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.created_at > DEFAULT_TTL_SECONDS
        ]
        for key in expired:
            self._entries.pop(key, None)
        if len(self._entries) < self._max_entries:
            return
        for key in list(self._entries):
            if len(self._entries) < self._max_entries:
                return
            if self._entries[key].completed:
                self._entries.pop(key, None)

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

    async def is_completed(self, key: str) -> bool:
        """判断幂等键是否已完成首次执行，并惰性清理过期条目。

        Args:
            key: 幂等键。

        Returns:
            bool: 已完成返回 True；无记录、已过期或仍在执行返回 False。
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            if time.monotonic() - entry.created_at > DEFAULT_TTL_SECONDS:
                self._entries.pop(key, None)
                return False
            return entry.completed

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
            if existing is None:
                self._prune_locked(time.monotonic())
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

    键设计：`oa:idem:{tenant_id}:{idempotency_key}`（架构文档 9.5 / L-16）。
    其中 `{tenant_id}:{idempotency_key}` 由 `IdempotencyManager` 通过
    `build_storage_key()` 拼好传入，本类只负责加前缀——租户维度的唯一真源
    留在管理器一侧，避免"存储层也拼一次"导致两处口径漂移。

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

    async def is_completed(self, key: str) -> bool:
        """判断幂等键是否已完成首次执行。

        Args:
            key: 幂等键。

        Returns:
            bool: 已完成返回 True；无记录或仍在执行返回 False。
        """
        import json

        raw = await self._client.get(self._build_key(key))
        if raw is None:
            return False
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):  # pragma: no cover - 防御性分支
            return False
        return isinstance(payload, dict) and bool(payload.get("done"))

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

    @property
    def store_name(self) -> str:
        """当前**实际**装配的存储实现类名。

        R2-ENG-01（审计 2026-10-29）：启动期断言用它，而不是再比一次配置字符串。
        比配置只能证明"配置写了什么"，比类名才能证明"装配成了什么"——本轮缺陷
        正是"配置写 redis、装配成内存"。
        """
        return type(self._store).__name__

    async def check_or_set(self, key: str, fingerprint: str, tenant_id: str = "") -> bool:
        """检查并占用幂等键。

        同一幂等键被用于**不同请求**时视为键复用冲突（20005，架构文档 3.0.3 冲突语义），
        避免不同接口的首次结果被交叉回放。

        Args:
            key: `Idempotency-Key` 头值。
            fingerprint: 请求指纹（方法 + 路径 + 租户 + 用户）。
            tenant_id: 生效租户 ID（L-16）；传入后存储键变成
                `{tenant_id}:{key}`，不同租户的同一个 UUID 互不可见。

        Returns:
            bool: 首次请求返回 True；重复请求返回 False。

        Raises:
            ApiError: 幂等键为空（20001）；同键不同请求（20005）。
        """
        if not key or not key.strip():
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="missing Idempotency-Key header"
            )
        normalized = build_storage_key(key, tenant_id)
        existing = await self._store.peek(normalized)
        if existing is not None and existing != fingerprint:
            raise ApiError(
                ErrorCode.CONFLICT,
                internal_detail="Idempotency-Key reused for a different request",
            )
        acquired = await self._store.acquire(normalized, fingerprint, self.ttl_seconds)
        logger.debug("idempotency_check", key_suffix=normalized[-8:], acquired=acquired)
        return acquired

    async def get_result(self, key: str, tenant_id: str = "") -> Any | None:
        """读取首次执行结果。

        Args:
            key: `Idempotency-Key` 头值。
            tenant_id: 生效租户 ID（L-16，需与占用时一致）。

        Returns:
            Any | None: 首次结果；未命中返回 `None`。
        """
        return await self._store.get(build_storage_key(key, tenant_id))

    async def store_result(self, key: str, result: Any, tenant_id: str = "") -> None:
        """回写首次执行结果。

        Args:
            key: `Idempotency-Key` 头值。
            result: 执行结果。
            tenant_id: 生效租户 ID（L-16，需与占用时一致）。
        """
        await self._store.store_result(build_storage_key(key, tenant_id), result, self.ttl_seconds)

    async def release(self, key: str, tenant_id: str = "") -> None:
        """释放幂等键（执行失败时调用，允许客户端重试）。

        Args:
            key: `Idempotency-Key` 头值。
            tenant_id: 生效租户 ID（L-16，需与占用时一致）。
        """
        await self._store.release(build_storage_key(key, tenant_id))

    async def execute(
        self,
        key: str,
        fingerprint: str,
        factory: Any,
        tenant_id: str = "",
    ) -> tuple[Any, bool]:
        """执行带幂等保护的写操作。

        Args:
            key: `Idempotency-Key` 头值。
            fingerprint: 请求指纹。
            factory: 无参异步可调用对象，返回首次执行结果。
            tenant_id: 生效租户 ID（L-16）。**必须**与本次请求的租户一致，
                否则占用与回写会落在两个不同的存储键上（表现为重复请求拿不到结果）。

        Returns:
            tuple[Any, bool]: `(结果, 是否重复请求)`。重复请求时结果为首次缓存值。

        Raises:
            ApiError: 执行失败时释放幂等键并向上抛出。
        """
        storage_key = build_storage_key(key, tenant_id)
        acquired = await self.check_or_set(key, fingerprint, tenant_id)
        if not acquired:
            # 首个请求仍在执行：返回空结果会被客户端当成「写成功」（审计 P2-3）。
            # 20005 的既有语义是「操作冲突，请刷新后重试」，与此场景一致。
            if not await self._store.is_completed(storage_key):
                raise ApiError(
                    ErrorCode.CONFLICT,
                    internal_detail="idempotent request still in progress",
                )
            cached = await self.get_result(key, tenant_id)
            logger.debug("idempotency_replayed", key_suffix=storage_key[-8:])
            return cached, True
        try:
            result = await factory()
        except ApiError:
            await self.release(key, tenant_id)
            raise
        except Exception:
            await self.release(key, tenant_id)
            raise
        await self.store_result(key, result, tenant_id)
        return result, False


_manager: IdempotencyManager | None = None


def build_idempotency_store(conf: Settings) -> IdempotencyStore:
    """按配置构造幂等存储。

    R2-ENG-01（审计 2026-10-29）：修复前 `IdempotencyManager()` 是**无参构造**，
    恒用 `InMemoryIdempotencyStore`，而 `RedisIdempotencyStore` 全仓只在 `tests/`
    里被实例化过——`OA_IDEMPOTENCY_BACKEND=redis` 是纯粹的配置幻觉。

    Args:
        conf: 配置实例。

    Returns:
        IdempotencyStore: 进程内或 Redis 实现。

    Raises:
        ApiError: 声明 redis 后端但无法取得客户端（30004）。
    """
    if conf.idempotency_backend == "redis":
        client = get_shared_redis(conf)
        if client is None:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    "OA_IDEMPOTENCY_BACKEND=redis 但未能取得 Redis 客户端"
                    "（OA_REDIS_URL 为空或依赖缺失）"
                ),
            )
        return RedisIdempotencyStore(client)
    return InMemoryIdempotencyStore()


def get_idempotency_manager() -> IdempotencyManager:
    """获取幂等管理器单例（按进程级配置装配存储实现）。

    ⚠️ **签名必须保持无参**：本函数被 20+ 个端点以
    `Depends(get_idempotency_manager)` 使用（`api/v1/agent.py`、`billing.py` 等）。
    FastAPI 会解析被 `Depends` 调用的函数的**所有参数**——一旦加一个
    `settings: Settings | None = None`，FastAPI 会把 `Settings` 当作可选 **request body**
    来解析，导致**全站每个 POST 端点凭空多出一个 `Body_*` schema**、
    请求体引用从具名模型退化成匿名包装（2026-10-29 实测：92 处 schema 漂移）。
    需要注入配置时用 `configure_idempotency_manager()`。

    Returns:
        IdempotencyManager: 进程内唯一实例。
    """
    global _manager
    if _manager is None:
        conf = get_settings()
        _manager = IdempotencyManager(store=build_idempotency_store(conf), settings=conf)
    return _manager


def configure_idempotency_manager(settings: Settings) -> IdempotencyManager:
    """按**显式配置**装配幂等管理器（应用启动期与测试使用）。

    与 `get_idempotency_manager()` 的区别：本函数不参与 FastAPI 的依赖解析，
    因此可以安全地接收参数。启动期用它装配，可保证「装配结果」与
    `app.state.settings` 完全一致——而 `get_settings()` 在测试注入场景下
    未必等同于 `app.state.settings`。

    Args:
        settings: 配置实例。

    Returns:
        IdempotencyManager: 新装配的实例（会覆盖进程级单例）。
    """
    global _manager
    _manager = IdempotencyManager(store=build_idempotency_store(settings), settings=settings)
    return _manager


def reset_idempotency_manager() -> None:
    """重置幂等管理器单例，供测试隔离用例。"""
    global _manager
    _manager = None


def build_fingerprint(
    method: str,
    path: str,
    tenant_id: str,
    user_id: str,
    canonical_body_sha256: str = "",
) -> str:
    """构造幂等指纹（架构文档 3.0.5 实现强制口径）。

    口径为**唯一正确实现**，任何偏离即视为契约违规：

    ```
    fingerprint = SHA256(
        method + "\\n" +
        path + "\\n" +
        tenant_id + "\\n" +
        user_id + "\\n" +
        canonical_body_sha256
    )
    ```

    其中 `canonical_body_sha256` 为**请求体规范化 JSON 的 SHA-256 十六进制摘要**
    （键按字典序排序、无多余空白、UTF-8 编码）。

    指纹必须包含请求体摘要（QA BUG-05）：否则同一 `Idempotency-Key` 配同 method/path/
    tenant/user 但不同请求体时会被误判为重复请求并**静默回放首次结果**，
    造成「用户看到成功但数据未变更」的静默数据丢失。

    Args:
        method: HTTP 方法（大写参与计算）。
        path: 规范化后的请求路径（去除查询串）。
        tenant_id: 解析后的租户 ID（私有化模式恒为 `default`）。
        user_id: 解析后的用户 ID。
        canonical_body_sha256: 请求体规范化摘要；缺省为空体固定摘要。

    Returns:
        str: 指纹十六进制串。
    """
    payload = "\n".join(
        [method.upper(), path, tenant_id, user_id, canonical_body_sha256 or EMPTY_BODY_SHA256]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_request_body(body: bytes | str | None) -> str:
    """计算请求体的**规范化** SHA-256 摘要（架构文档 3.0.5）。

    规范化规则：JSON 解析 → 键按字典序排序 → 去除多余空白 → UTF-8 编码。
    请求体为空时返回空体固定摘要 `EMPTY_BODY_SHA256`（**不得跳过该项**）。

    采用摘要而非原文，避免敏感内容进入日志或内存驻留。

    Args:
        body: 请求体字节或字符串。

    Returns:
        str: 摘要十六进制串。
    """
    if not body:
        return EMPTY_BODY_SHA256
    raw = body.encode("utf-8") if isinstance(body, str) else body
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # 非 JSON 请求体（如 multipart）：退化为原始字节摘要
        return hashlib.sha256(raw).hexdigest()
    canonical = json.dumps(
        parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
