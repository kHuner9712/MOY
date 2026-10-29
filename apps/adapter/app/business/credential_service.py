"""per-kb 凭据解析（阶段二 D-10，架构文档 2.3 结论第 2-5 条）。

解析链路：

```
resolve(tenant_id, base_id):
  meta = 绑定存储.get(tenant_id, base_id)          # 内存后端跨模块共享 / 数据库短查询
  if meta is None:  → 全局凭据（兼容现状）
  # 已绑定：以下任何一步失败都**不得回落**
  try:
      record = 缓存(密文) 或 credential_store.get(...)
      if record is None or not active: raise CredentialUnavailable
      plaintext = AES-GCM.decrypt(record)
      return Resolved(api_key=plaintext, source="bound")
  except Exception:
      raise ApiError(11007)                        # 绝不 fallback
```

三条不可妥协的约束：

1. **禁止回落**：已绑定的库解析失败必须 `11007`。回落意味着 A 库的请求打到 B 库
   的应用上，等于跨知识库数据泄露。代码级防线：`dify_default_app_key` 在
   `resolve()` 中**只出现在绑定判定之前**（两个 `source="global"` 返回点），
   `except` 分支内不得引用——单元测试用「模块内该标识符仅出现一次」的结构断言固化。
2. **缓存只存密文**：缓存对象是 `cipher_text + nonce + key_version`（base64），
   不是明文；TTL 由 `OA_CREDENTIAL_CACHE_TTL_SECONDS` 控制（默认 300s）。
3. **不做负缓存**：解析失败不写缓存，运维修好凭据后下一个请求即恢复。

换绑即生效（PRD P2-KB-05 第 3 条）：`bind()` 成功后立即删除
`oa:cred:v1:{tenant}:{ref}` 与 `oa:kbcache:v1:{tenant}:{base}` → 下一请求重新解析，无需重启。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol

from app.core.cache_backend import DEFAULT_HEALTH_CHECK_INTERVAL, DEFAULT_SOCKET_TIMEOUT
from app.core.config import Settings, get_settings
from app.core.crypto import CredentialCipher, CredentialDecryptError
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.credential_repo import (
    CredentialBindingStore,
    CredentialRecord,
    CredentialStore,
    get_binding_store,
    get_credential_store,
)

logger = get_logger(__name__)

#: 凭据来源：`global` = 未绑定走全局凭据；`bound` = 使用库级凭据
CredentialSource = Literal["global", "bound"]

#: 凭据密文缓存键前缀
CREDENTIAL_CACHE_PREFIX: Final[str] = "oa:cred:v1"

#: 知识库元数据缓存键前缀（B04 起使用；换绑时一并失效）
KB_CACHE_PREFIX: Final[str] = "oa:kbcache:v1"

#: 日志中允许暴露的引用后缀长度（架构文档 11.4）
REF_SUFFIX_LENGTH: Final[int] = 6


def ref_suffix(credential_ref: str, keep: int = REF_SUFFIX_LENGTH) -> str:
    """取凭据引用的后 N 位（用于日志，避免全量引用出现在日志里）。

    Args:
        credential_ref: 凭据引用。
        keep: 保留位数。

    Returns:
        str: 后缀；引用为空时返回空串。
    """
    normalized = (credential_ref or "").strip()
    if not normalized:
        return ""
    return normalized[-keep:]


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """解析结果（frozen：避免下游意外修改）。

    Attributes:
        api_key: 实际下发给底座的密钥（**只允许传给 `DifyClient`**）。
        source: `global` 或 `bound`。
        credential_ref: 绑定的凭据引用（`global` 时为 None）。
    """

    api_key: str
    source: CredentialSource = "global"
    credential_ref: str | None = None

    def __repr__(self) -> str:
        """脱敏表示（防止明文随异常/日志外泄）。

        Returns:
            str: 不含 `api_key` 的表示。
        """
        return (
            f"ResolvedCredential(source={self.source!r}, "
            f"credential_ref={ref_suffix(self.credential_ref or '')!r}, api_key=<redacted>)"
        )


class CredentialCache(Protocol):
    """凭据密文缓存契约。"""

    async def get(self, key: str) -> dict[str, Any] | None:
        """读取缓存（值为密文三要素）。"""
        ...

    async def set(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        """写入缓存。"""
        ...

    async def delete(self, key: str) -> None:
        """删除缓存。"""
        ...


class InMemoryCredentialCache:
    """进程内缓存（development/test，以及 Valkey 不可达时的降级实现）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict[str, Any] | None:
        """读取缓存。

        Args:
            key: 缓存键。

        Returns:
            dict[str, Any] | None: 未过期时返回缓存值。
        """
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                self._entries.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        """写入缓存。

        Args:
            key: 缓存键。
            value: 密文三要素。
            ttl_seconds: 过期秒数（0 表示不缓存）。
        """
        if ttl_seconds <= 0:
            return
        async with self._lock:
            self._entries[key] = (time.monotonic() + ttl_seconds, value)

    async def delete(self, key: str) -> None:
        """删除缓存。

        Args:
            key: 缓存键。
        """
        async with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._entries.clear()


class RedisCredentialCache:
    """Valkey / Redis 缓存实现（只存密文）。

    Valkey 不可达时**降级为进程内缓存并打 WARN**（架构文档 2.3 结论第 3 条：
    缓存缺失不阻断功能，退化为每请求直连数据库/内存）。
    """

    def __init__(self, redis_url: str, *, fallback: CredentialCache | None = None) -> None:
        """初始化。

        Args:
            redis_url: `redis://` 连接串。
            fallback: 降级使用的缓存实现。
        """
        self._redis_url = redis_url
        self._fallback: CredentialCache = fallback or InMemoryCredentialCache()
        self._client: Any | None = None
        self._degraded = False

    async def _get_client(self) -> Any | None:
        """惰性建立连接；失败即降级。

        Returns:
            Any | None: redis 客户端；降级时返回 None。
        """
        if self._degraded:
            return None
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as redis_asyncio

            # R2-ENG-01（审计 2026-10-29）：**必须显式传超时**。
            # redis-py 的 `socket_timeout` 默认是 `None`，语义不是"很快超时"而是
            # **永久阻塞**——Valkey 一旦 hang，所有等待这个客户端的请求会一起挂死。
            # 同一组常量在 `core/cache_backend.py` 里定义了共享客户端用的值，
            # 这里复用，避免"一处加了超时、另一处没加"的分叉。
            self._client = redis_asyncio.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=DEFAULT_SOCKET_TIMEOUT,
                socket_connect_timeout=DEFAULT_SOCKET_TIMEOUT,
                health_check_interval=DEFAULT_HEALTH_CHECK_INTERVAL,
            )
            await self._client.ping()
        except Exception:  # 缓存不可用不得阻断业务
            logger.warning("credential_cache_degraded", reason="redis_unavailable")
            self._degraded = True
            self._client = None
            return None
        return self._client

    async def get(self, key: str) -> dict[str, Any] | None:
        """读取缓存。

        Args:
            key: 缓存键。

        Returns:
            dict[str, Any] | None: 缓存值。
        """
        client = await self._get_client()
        if client is None:
            return await self._fallback.get(key)
        try:
            raw = await client.get(key)
        except Exception:  # 缓存抖动按未命中处理
            logger.warning("credential_cache_read_failed")
            return await self._fallback.get(key)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    async def set(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        """写入缓存。

        Args:
            key: 缓存键。
            value: 密文三要素。
            ttl_seconds: 过期秒数。
        """
        client = await self._get_client()
        if client is None or ttl_seconds <= 0:
            await self._fallback.set(key, value, ttl_seconds)
            return
        try:
            await client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
        except Exception:  # 写缓存失败不影响主流程
            logger.warning("credential_cache_write_failed")
            await self._fallback.set(key, value, ttl_seconds)

    async def delete(self, key: str) -> None:
        """删除缓存。

        Args:
            key: 缓存键。
        """
        client = await self._get_client()
        if client is not None:
            try:
                await client.delete(key)
            except Exception:  # 删除失败由 TTL 兜底
                logger.warning("credential_cache_delete_failed")
        await self._fallback.delete(key)


def build_cache(settings: Settings) -> CredentialCache:
    """按配置构造缓存。

    Args:
        settings: 配置实例。

    Returns:
        CredentialCache: `idempotency_backend=redis` 时用 Valkey，否则用进程内实现。
    """
    if settings.idempotency_backend == "redis" and settings.redis_url:
        return RedisCredentialCache(settings.redis_url, fallback=InMemoryCredentialCache())
    return InMemoryCredentialCache()


class CredentialResolver:
    """凭据解析器（D-10 唯一入口）。

    Attributes:
        _settings: 配置实例。
        _store: 凭据存储（只存密文）。
        _bindings: 库 → 凭据绑定存储。
        _cache: 密文缓存。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: CredentialStore | None = None,
        binding_store: CredentialBindingStore | None = None,
        cache: CredentialCache | None = None,
        cipher: CredentialCipher | None = None,
    ) -> None:
        """初始化解析器。

        Args:
            settings: 可选配置实例。
            store: 可选凭据存储（测试注入）。
            binding_store: 可选绑定存储（测试注入）。
            cache: 可选缓存（测试注入）。
            cipher: 可选加解密器（测试注入；缺省按配置惰性构造）。
        """
        self._settings = settings or get_settings()
        self._store: CredentialStore = store or get_credential_store(self._settings)
        self._bindings: CredentialBindingStore = binding_store or get_binding_store(self._settings)
        self._cache: CredentialCache = cache or build_cache(self._settings)
        self._cipher = cipher
        self._cipher_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    async def resolve(self, base_id: str, *, tenant_id: str) -> ResolvedCredential:
        """解析该知识库应使用的下游密钥。

        Args:
            base_id: 知识库 ID。
            tenant_id: 租户 ID（唯一权威值，来自请求上下文）。

        Returns:
            ResolvedCredential: 解析结果。

        Raises:
            ApiError: 已绑定但解析失败（11007，**绝不回落全局凭据**）。
        """
        if self._settings.credential_mode == "global":
            logger.warning(
                "credential_mode_global_forced",
                base_id=base_id,
                note="应急模式：所有知识库强制使用全局凭据",
            )
            return ResolvedCredential(api_key=self._settings.dify_default_app_key)

        credential_ref = await self._bindings.get_credential_ref(tenant_id, base_id)
        if not credential_ref:
            return ResolvedCredential(api_key=self._settings.dify_default_app_key)

        # ------ 以下为「已绑定」分支：任何异常都不得回落（禁止访问全局凭据） ------
        try:
            record = await self._load_record(tenant_id, credential_ref)
            plaintext = await self._decrypt(record)
        except ApiError:
            raise
        except Exception as exc:  # 统一收敛为 11007，绝不 fallback
            logger.error(
                "credential_resolve_failed",
                base_id=base_id,
                credential_ref_suffix=ref_suffix(credential_ref),
                reason=type(exc).__name__,
            )
            raise ApiError(
                ErrorCode.KB_CREDENTIAL_UNAVAILABLE,
                internal_detail=(
                    f"base_id={base_id} ref_suffix={ref_suffix(credential_ref)} "
                    f"reason={type(exc).__name__}"
                ),
            ) from exc

        logger.info(
            "credential_resolved",
            source="bound",
            credential_ref_suffix=ref_suffix(credential_ref),
            base_id=base_id,
        )
        return ResolvedCredential(api_key=plaintext, source="bound", credential_ref=credential_ref)

    async def _load_record(self, tenant_id: str, credential_ref: str) -> CredentialRecord:
        """读取凭据记录（优先命中密文缓存）。

        Args:
            tenant_id: 租户 ID。
            credential_ref: 凭据引用。

        Returns:
            CredentialRecord: 凭据记录。

        Raises:
            ApiError: 凭据不存在或已吊销（11007）。
        """
        cache_key = self._cache_key(tenant_id, credential_ref)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            record = _record_from_cache(cached, tenant_id, credential_ref)
            if record is not None:
                return record

        record = await self._store.get(tenant_id, credential_ref)
        if record is None or not record.is_active:
            raise ApiError(
                ErrorCode.KB_CREDENTIAL_UNAVAILABLE,
                internal_detail=(
                    f"credential missing or revoked ref_suffix={ref_suffix(credential_ref)}"
                ),
            )
        await self._cache.set(
            cache_key,
            _record_to_cache(record),
            self._settings.credential_cache_ttl_seconds,
        )
        return record

    async def _decrypt(self, record: CredentialRecord) -> str:
        """解密凭据。

        Args:
            record: 凭据记录。

        Returns:
            str: 明文密钥。

        Raises:
            CredentialDecryptError: 密钥缺失/版本不存在/认证失败。
        """
        cipher = await self._get_cipher()
        plaintext = cipher.decrypt(record.cipher_text, record.nonce, record.key_version)
        if not plaintext.strip():
            raise CredentialDecryptError("empty credential plaintext")
        return plaintext

    async def _get_cipher(self) -> CredentialCipher:
        """惰性获取加解密器（密钥缺失时只在真正需要解密时才失败）。

        Returns:
            CredentialCipher: 加解密器。

        Raises:
            ApiError: 密钥缺失/非法（30004，含可操作文案）。
        """
        if self._cipher is None:
            async with self._cipher_lock:
                if self._cipher is None:
                    self._cipher = CredentialCipher.from_settings(self._settings)
        return self._cipher

    # ------------------------------------------------------------------
    # 写入与失效
    # ------------------------------------------------------------------
    async def store_credential(
        self,
        credential_ref: str,
        api_key: str,
        *,
        tenant_id: str,
        provider: str = "knowledge_base",
    ) -> None:
        """加密并写入凭据（运营侧入库入口，OPS 批次复用）。

        Args:
            credential_ref: 凭据引用（`cred_` + ULID）。
            api_key: 明文密钥（只在本方法内以内存形式存在）。
            tenant_id: 租户 ID。
            provider: 凭据用途。

        Raises:
            ApiError: 密钥缺失/非法（30004）。
        """
        cipher = await self._get_cipher()
        secret = cipher.encrypt(api_key)
        await self._store.put(
            CredentialRecord(
                tenant_id=tenant_id,
                credential_ref=credential_ref,
                provider=provider,
                cipher_text=secret.cipher_text,
                nonce=secret.nonce,
                key_version=secret.key_version,
                updated_at=_now_iso(),
            )
        )
        await self.invalidate(tenant_id, credential_ref=credential_ref)
        logger.info(
            "credential_stored",
            credential_ref_suffix=ref_suffix(credential_ref),
            key_version=secret.key_version,
        )

    async def bind(self, base_id: str, credential_ref: str, *, tenant_id: str) -> str:
        """绑定知识库与凭据（KB8 唯一写入路径）。

        Args:
            base_id: 知识库 ID。
            credential_ref: 凭据引用。
            tenant_id: 租户 ID。

        Returns:
            str: 绑定更新时间（ISO 8601）。

        Raises:
            ApiError: 凭据不可用（11007）或知识库不存在（11001）。
        """
        record = await self._store.get(tenant_id, credential_ref)
        if record is None or not record.is_active:
            logger.error(
                "credential_binding_rejected",
                base_id=base_id,
                credential_ref_suffix=ref_suffix(credential_ref),
            )
            raise ApiError(
                ErrorCode.KB_CREDENTIAL_UNAVAILABLE,
                internal_detail=(
                    f"binding target unavailable ref_suffix={ref_suffix(credential_ref)}"
                ),
            )

        updated = await self._bindings.set_credential_ref(tenant_id, base_id, credential_ref)
        if not updated:
            raise ApiError(
                ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                internal_detail=f"base not found base_id={base_id}",
            )

        await self.invalidate(tenant_id, base_id=base_id, credential_ref=credential_ref)
        logger.info(
            "credential_binding_updated",
            base_id=base_id,
            credential_ref_suffix=ref_suffix(credential_ref),
        )
        return _now_iso()

    async def invalidate(
        self,
        tenant_id: str,
        *,
        base_id: str = "",
        credential_ref: str = "",
    ) -> None:
        """失效缓存（换绑后保证「下一请求即生效」）。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID（可选）。
            credential_ref: 凭据引用（可选）。
        """
        if credential_ref:
            await self._cache.delete(self._cache_key(tenant_id, credential_ref))
        if base_id:
            await self._cache.delete(f"{KB_CACHE_PREFIX}:{tenant_id}:{base_id}")

    def _cache_key(self, tenant_id: str, credential_ref: str) -> str:
        """构造凭据密文缓存键。

        Args:
            tenant_id: 租户 ID。
            credential_ref: 凭据引用。

        Returns:
            str: 缓存键。
        """
        return f"{CREDENTIAL_CACHE_PREFIX}:{tenant_id}:{credential_ref}"


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _record_to_cache(record: CredentialRecord) -> dict[str, Any]:
    """把凭据记录序列化为**密文**缓存值。

    Args:
        record: 凭据记录。

    Returns:
        dict[str, Any]: 只含密文三要素的缓存值。
    """
    return {
        "c": base64.b64encode(record.cipher_text).decode("ascii"),
        "n": base64.b64encode(record.nonce).decode("ascii"),
        "v": record.key_version,
    }


def _record_from_cache(
    cached: dict[str, Any], tenant_id: str, credential_ref: str
) -> CredentialRecord | None:
    """从缓存值还原凭据记录。

    Args:
        cached: 缓存值。
        tenant_id: 租户 ID。
        credential_ref: 凭据引用。

    Returns:
        CredentialRecord | None: 记录；缓存结构非法时返回 None（按未命中处理）。
    """
    try:
        cipher_text = base64.b64decode(str(cached.get("c", "")), validate=True)
        nonce = base64.b64decode(str(cached.get("n", "")), validate=True)
        key_version = int(cached.get("v", 1))
    except (ValueError, TypeError):
        return None
    if not cipher_text or not nonce:
        return None
    return CredentialRecord(
        tenant_id=tenant_id,
        credential_ref=credential_ref,
        provider="knowledge_base",
        cipher_text=cipher_text,
        nonce=nonce,
        key_version=key_version,
    )


# ===========================================================================
# 进程级单例
# ===========================================================================
_resolver: CredentialResolver | None = None
_resolver_signature: tuple[Any, ...] | None = None


def get_credential_resolver(settings: Settings | None = None) -> CredentialResolver:
    """获取凭据解析器单例。

    单例签名含「凭据模式 + 主密钥 + 存储后端 + 缓存 TTL」：配置变化（测试注入
    不同 Settings）时自动重建，避免跨用例复用带旧密钥的解析器。

    Args:
        settings: 可选配置实例。

    Returns:
        CredentialResolver: 解析器实例。
    """
    global _resolver, _resolver_signature

    conf = settings or get_settings()
    signature = (
        conf.credential_mode,
        conf.credential_encryption_key,
        conf.credential_encryption_keys_legacy,
        conf.business_store,
        conf.credential_cache_ttl_seconds,
        conf.idempotency_backend,
        conf.redis_url,
    )
    if _resolver is not None and _resolver_signature == signature:
        return _resolver
    _resolver = CredentialResolver(conf)
    _resolver_signature = signature
    return _resolver


def reset_credential_resolver() -> None:
    """重置凭据解析器单例（测试隔离用）。"""
    global _resolver, _resolver_signature
    _resolver = None
    _resolver_signature = None


__all__ = [
    "CREDENTIAL_CACHE_PREFIX",
    "KB_CACHE_PREFIX",
    "CredentialCache",
    "CredentialResolver",
    "InMemoryCredentialCache",
    "RedisCredentialCache",
    "ResolvedCredential",
    "build_cache",
    "get_credential_resolver",
    "ref_suffix",
    "reset_credential_resolver",
]
