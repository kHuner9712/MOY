# ruff: noqa: S608 - 本模块所有 SQL 的表名/列名均为类常量（经 assert_identifier 校验），
# 值一律走命名参数绑定；不用 f-string 无法表达「表名由子类常量决定」这一层。
"""凭据与「库-凭据绑定」仓储（阶段二 D-10，架构文档 2.3）。

两张表两条职责：

| 表 | 职责 | 写入方 |
| --- | --- | --- |
| `credential_store` | 存**密文**（`cipher_text`/`nonce`/`key_version`）与状态 | 运营入库（OPS） |
| `knowledge_base_meta.credential_ref` | 知识库 → 凭据的绑定 | 仅 KB8（`knowledge:base:update`） |

**明文永不入库**：本模块只搬运 `bytes`，不接触明文；
加解密与「解密失败 → 11007」的判定统一在 `business/credential_service.py`。

双后端（`business_store`）：

- `memory`：进程内实现，供 development/test 与离线演示；
- `postgres`：短事务读写，SQL 一律带 `tenant_id = :tenant_id`
  （RLS 之外的应用层双保险）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 凭据表名
CREDENTIAL_TABLE: Final[str] = "credential_store"

#: 知识库元数据表名
KNOWLEDGE_BASE_TABLE: Final[str] = "knowledge_base_meta"

#: 生效状态
STATUS_ACTIVE: Final[str] = "active"

#: 已吊销状态
STATUS_REVOKED: Final[str] = "revoked"


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601，秒级足够）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    """凭据记录（只含密文，永不含明文）。

    Attributes:
        tenant_id: 租户 ID。
        credential_ref: 凭据引用，形如 `cred_01K...`。
        provider: 凭据用途（当前固定 `knowledge_base`）。
        cipher_text: 密文（含 GCM 认证 tag）。
        nonce: 12 字节随机 nonce。
        key_version: 加密所用密钥版本号。
        status: `active` / `revoked`。
        updated_at: 最近更新时间（ISO 8601）。
    """

    tenant_id: str
    credential_ref: str
    provider: str
    cipher_text: bytes
    nonce: bytes
    key_version: int
    status: str = STATUS_ACTIVE
    updated_at: str = ""

    @property
    def is_active(self) -> bool:
        """凭据是否可用。"""
        return self.status == STATUS_ACTIVE


class CredentialStore(Protocol):
    """凭据存储契约。"""

    async def get(self, tenant_id: str, credential_ref: str) -> CredentialRecord | None:
        """按引用读取凭据（只返回密文）。"""
        ...

    async def put(self, record: CredentialRecord) -> None:
        """写入/覆盖凭据（幂等 upsert）。"""
        ...

    async def revoke(self, tenant_id: str, credential_ref: str) -> None:
        """吊销凭据（保留行为 `revoked`，不物理删除）。"""
        ...


class CredentialBindingStore(Protocol):
    """知识库 → 凭据绑定契约。"""

    async def get_credential_ref(self, tenant_id: str, base_id: str) -> str | None:
        """读取知识库绑定的凭据引用（未绑定返回 None）。"""
        ...

    async def set_credential_ref(self, tenant_id: str, base_id: str, credential_ref: str) -> bool:
        """设置绑定；知识库不存在时返回 False。"""
        ...


class InMemoryCredentialStore:
    """进程内凭据存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._records: dict[tuple[str, str], CredentialRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, tenant_id: str, credential_ref: str) -> CredentialRecord | None:
        """按引用读取凭据。

        Args:
            tenant_id: 租户 ID。
            credential_ref: 凭据引用。

        Returns:
            CredentialRecord | None: 记录；不存在返回 None。
        """
        async with self._lock:
            return self._records.get((tenant_id, credential_ref))

    async def put(self, record: CredentialRecord) -> None:
        """写入凭据。

        Args:
            record: 凭据记录。
        """
        async with self._lock:
            payload = CredentialRecord(
                tenant_id=record.tenant_id,
                credential_ref=record.credential_ref,
                provider=record.provider,
                cipher_text=record.cipher_text,
                nonce=record.nonce,
                key_version=record.key_version,
                status=record.status,
                updated_at=record.updated_at or _now_iso(),
            )
            self._records[(record.tenant_id, record.credential_ref)] = payload

    async def revoke(self, tenant_id: str, credential_ref: str) -> None:
        """吊销凭据。

        Args:
            tenant_id: 租户 ID。
            credential_ref: 凭据引用。
        """
        async with self._lock:
            existing = self._records.get((tenant_id, credential_ref))
            if existing is None:
                return
            self._records[(tenant_id, credential_ref)] = CredentialRecord(
                tenant_id=existing.tenant_id,
                credential_ref=existing.credential_ref,
                provider=existing.provider,
                cipher_text=existing.cipher_text,
                nonce=existing.nonce,
                key_version=existing.key_version,
                status=STATUS_REVOKED,
                updated_at=_now_iso(),
            )

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._records.clear()


class InMemoryCredentialBindingStore:
    """进程内绑定存储（development/test）。

    说明：内存模式没有 `knowledge_base_meta` 表，`set_credential_ref` 无法校验
    「知识库是否存在」，按 upsert 处理（真实存在性校验在 postgres 后端完成）。
    """

    def __init__(self) -> None:
        """初始化。"""
        self._bindings: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def get_credential_ref(self, tenant_id: str, base_id: str) -> str | None:
        """读取绑定。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            str | None: 凭据引用；未绑定返回 None。
        """
        async with self._lock:
            return self._bindings.get((tenant_id, base_id))

    async def set_credential_ref(self, tenant_id: str, base_id: str, credential_ref: str) -> bool:
        """写入绑定。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            credential_ref: 凭据引用。

        Returns:
            bool: 内存模式恒为 True。
        """
        async with self._lock:
            self._bindings[(tenant_id, base_id)] = credential_ref
        return True

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._bindings.clear()


class _CredentialTableRepository(BaseRepository):
    """`credential_store` 表仓储（强制租户条件）。"""

    table = CREDENTIAL_TABLE


class _KnowledgeBaseTableRepository(BaseRepository):
    """`knowledge_base_meta` 表仓储（强制租户条件）。"""

    table = KNOWLEDGE_BASE_TABLE


class PostgresCredentialStore:
    """PostgreSQL 凭据存储（短事务，一次调用一个事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def get(self, tenant_id: str, credential_ref: str) -> CredentialRecord | None:
        """按引用读取凭据。

        Args:
            tenant_id: 租户 ID。
            credential_ref: 凭据引用。

        Returns:
            CredentialRecord | None: 记录；不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _CredentialTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, credential_ref, provider, cipher_text, nonce,
                       key_version, status,
                       COALESCE(rotated_at, created_at) AS updated_at
                FROM {CREDENTIAL_TABLE}
                WHERE {repo.tenant_clause()} AND credential_ref = :credential_ref
                """,
                repo.params(credential_ref=credential_ref),
            )
        return _to_record(row) if row is not None else None

    async def put(self, record: CredentialRecord) -> None:
        """写入凭据（upsert）。

        Args:
            record: 凭据记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _CredentialTableRepository(uow)
            await repo.execute(
                f"""
                INSERT INTO {CREDENTIAL_TABLE} (
                    tenant_id, credential_ref, provider, cipher_text, nonce,
                    key_version, status, created_at, rotated_at
                ) VALUES (
                    :tenant_id, :credential_ref, :provider, :cipher_text, :nonce,
                    :key_version, :status, now(), now()
                )
                ON CONFLICT (tenant_id, credential_ref) DO UPDATE SET
                    cipher_text = EXCLUDED.cipher_text,
                    nonce = EXCLUDED.nonce,
                    key_version = EXCLUDED.key_version,
                    status = EXCLUDED.status,
                    rotated_at = now()
                """,
                repo.params(
                    credential_ref=record.credential_ref,
                    provider=record.provider,
                    cipher_text=record.cipher_text,
                    nonce=record.nonce,
                    key_version=record.key_version,
                    status=record.status,
                ),
            )

    async def revoke(self, tenant_id: str, credential_ref: str) -> None:
        """吊销凭据。

        Args:
            tenant_id: 租户 ID。
            credential_ref: 凭据引用。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _CredentialTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {CREDENTIAL_TABLE}
                SET status = :status, rotated_at = now()
                WHERE {repo.tenant_clause()} AND credential_ref = :credential_ref
                """,
                repo.params(status=STATUS_REVOKED, credential_ref=credential_ref),
            )


class PostgresCredentialBindingStore:
    """PostgreSQL 绑定存储（写 `knowledge_base_meta.credential_ref`）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def get_credential_ref(self, tenant_id: str, base_id: str) -> str | None:
        """读取绑定。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            str | None: 凭据引用；未绑定或库不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _KnowledgeBaseTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT credential_ref
                FROM {KNOWLEDGE_BASE_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND deleted_at IS NULL
                """,
                repo.params(base_id=base_id),
            )
        if row is None:
            return None
        value = row.get("credential_ref")
        return str(value) if value else None

    async def set_credential_ref(self, tenant_id: str, base_id: str, credential_ref: str) -> bool:
        """写入绑定。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            credential_ref: 凭据引用。

        Returns:
            bool: 知识库存在并更新成功返回 True。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _KnowledgeBaseTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {KNOWLEDGE_BASE_TABLE}
                SET credential_ref = :credential_ref, updated_at = now()
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND deleted_at IS NULL
                RETURNING base_id
                """,
                repo.params(base_id=base_id, credential_ref=credential_ref),
            )
        return row is not None


def _to_record(row: Any) -> CredentialRecord:
    """把数据库行映射为凭据记录。

    Args:
        row: 行映射。

    Returns:
        CredentialRecord: 凭据记录。
    """
    updated = row.get("updated_at")
    return CredentialRecord(
        tenant_id=str(row.get("tenant_id", "")),
        credential_ref=str(row.get("credential_ref", "")),
        provider=str(row.get("provider", "")),
        cipher_text=bytes(row.get("cipher_text") or b""),
        nonce=bytes(row.get("nonce") or b""),
        key_version=int(row.get("key_version", 1) or 1),
        status=str(row.get("status", STATUS_ACTIVE)),
        updated_at=str(updated) if updated is not None else "",
    )


# ===========================================================================
# 进程级单例（内存后端跨模块共享，postgres 后端无状态）
# ===========================================================================
_memory_store: InMemoryCredentialStore | None = None
_memory_bindings: InMemoryCredentialBindingStore | None = None


def get_credential_store(settings: Settings | None = None) -> CredentialStore:
    """获取凭据存储。

    Args:
        settings: 可选配置实例。

    Returns:
        CredentialStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresCredentialStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryCredentialStore()
    return _memory_store


def get_binding_store(settings: Settings | None = None) -> CredentialBindingStore:
    """获取绑定存储。

    Args:
        settings: 可选配置实例。

    Returns:
        CredentialBindingStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_bindings

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresCredentialBindingStore(conf)
    if _memory_bindings is None:
        _memory_bindings = InMemoryCredentialBindingStore()
    return _memory_bindings


def reset_credential_stores() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store, _memory_bindings
    if _memory_store is not None:
        _memory_store.clear()
    if _memory_bindings is not None:
        _memory_bindings.clear()
    _memory_store = None
    _memory_bindings = None


__all__ = [
    "CREDENTIAL_TABLE",
    "KNOWLEDGE_BASE_TABLE",
    "STATUS_ACTIVE",
    "STATUS_REVOKED",
    "CredentialBindingStore",
    "CredentialRecord",
    "CredentialStore",
    "InMemoryCredentialBindingStore",
    "InMemoryCredentialStore",
    "PostgresCredentialBindingStore",
    "PostgresCredentialStore",
    "get_binding_store",
    "get_credential_store",
    "reset_credential_stores",
]
