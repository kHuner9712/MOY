# ruff: noqa: S608 - 本模块所有 SQL 的表名/列名均为类常量（经 assert_identifier 校验），
# 值一律走命名参数绑定；不用 f-string 无法表达「表名由子类常量决定」这一层。
"""知识库元数据仓储（阶段二 B04，架构文档 3.3 / P2-KB-01）。

`knowledge_base_meta` 是知识库库级设置的**权威源**：名称、描述、可见范围、
内容切分精细度、所有者、软删除标记全部落在这张表（底座无这些概念）。

双后端（`OA_BUSINESS_STORE`）：

- `memory`：进程内实现，供 development/test 与离线演示；
  **无 PostgreSQL 时全部单测必须能跑通**，因此本文件不引入任何引擎依赖；
- `postgres`：短事务读写，SQL 一律带 `tenant_id = :tenant_id`（RLS 之外的应用层双保险）。

删除一律**软删除**（`deleted_at`）：列表立即不可见、审计保留、文档行级联标记。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository, assert_identifier
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 知识库元数据表名
KNOWLEDGE_BASE_TABLE: Final[str] = "knowledge_base_meta"

#: 文档元数据表名（统计文档数量用）
DOCUMENT_TABLE: Final[str] = "document_meta"

#: 默认可见范围
DEFAULT_VISIBILITY: Final[str] = "organization"

#: 默认内容切分精细度
DEFAULT_CHUNK_POLICY: Final[str] = "default"

#: 列表查询的文档计数子查询（一次查询带出文档数，避免 N+1 短事务）
_DOCUMENT_COUNT_JOIN: Final[str] = (
    "LEFT JOIN ("
    f"SELECT base_id, COUNT(*) AS cnt FROM {DOCUMENT_TABLE} "
    "WHERE tenant_id = :tenant_id AND deleted_at IS NULL GROUP BY base_id"
    ") dc ON dc.base_id = kb.base_id"
)


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class KnowledgeBaseRecord:
    """知识库元数据记录。

    Attributes:
        tenant_id: 租户 ID。
        base_id: 知识库 ID。
        name: 名称。
        description: 描述。
        visibility: 可见范围（`organization` / `private` / `restricted`）。
        chunk_policy: 内容切分精细度（`default` / `fine`）。
        owner_user_id: 所有者用户 ID（删除判权依据）。
        document_count: 文档数量快照。
        deleted_at: 软删除时间（ISO 8601）；未删除为 None。
        created_by: 创建人用户 ID。
        created_at: 创建时间（ISO 8601）。
        updated_at: 最近更新时间（ISO 8601）。
    """

    tenant_id: str
    base_id: str
    name: str = ""
    description: str = ""
    visibility: str = DEFAULT_VISIBILITY
    chunk_policy: str = DEFAULT_CHUNK_POLICY
    owner_user_id: str = ""
    document_count: int = 0
    deleted_at: str | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_deleted(self) -> bool:
        """是否已软删除。"""
        return bool(self.deleted_at)


@dataclass(frozen=True, slots=True)
class KnowledgeBasePatch:
    """知识库更新补丁（仅含允许修改的四个字段）。

    Attributes:
        name: 新名称。
        description: 新描述。
        chunk_policy: 新内容切分精细度。
        visibility: 新可见范围。
    """

    name: str | None = None
    description: str | None = None
    chunk_policy: str | None = None
    visibility: str | None = None

    def is_empty(self) -> bool:
        """是否没有任何修改项。

        Returns:
            bool: 四项全为 None 时返回 True。
        """
        return all(
            value is None
            for value in (self.name, self.description, self.chunk_policy, self.visibility)
        )


@dataclass(frozen=True, slots=True)
class KnowledgeBaseViewer:
    """列表查询的「观察者」上下文（可见范围判定下沉到存储层，保证分页总数正确）。

    可见规则（B05 起由 `business/policy_service.py` 统一计算后传入）：

    - `organization`：全员可见；
    - `private` / `restricted`：所有者、组织管理员，或 `allowed_base_ids` 命中
      （授权表判定结果，由 `PolicyService` 计算，存储层只做过滤，不判权）。

    Attributes:
        user_id: 当前用户 ID。
        is_admin: 是否组织管理员（含平台超级角色）。
        allowed_base_ids: 授权可见的知识库 ID（仅对非 `organization` 库生效）。
    """

    user_id: str = ""
    is_admin: bool = False
    allowed_base_ids: tuple[str, ...] = ()

    def can_see(self, record: KnowledgeBaseRecord) -> bool:
        """判断该记录对观察者是否可见。

        Args:
            record: 知识库记录。

        Returns:
            bool: 可见返回 True。
        """
        if record.visibility == "organization":
            return True
        if self.is_admin:
            return True
        if self.user_id and record.owner_user_id == self.user_id:
            return True
        return record.base_id in self.allowed_base_ids


class KnowledgeBaseStore(Protocol):
    """知识库元数据存储契约（内存 / PostgreSQL 双实现）。"""

    async def create(self, record: KnowledgeBaseRecord) -> KnowledgeBaseRecord:
        """写入知识库元数据（幂等 upsert）。"""
        ...

    async def get(
        self, tenant_id: str, base_id: str, *, include_deleted: bool = False
    ) -> KnowledgeBaseRecord | None:
        """按 ID 读取知识库元数据。"""
        ...

    async def list(
        self,
        tenant_id: str,
        *,
        keyword: str = "",
        viewer: KnowledgeBaseViewer | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[KnowledgeBaseRecord], int]:
        """分页查询未删除的知识库（关键字过滤 + 可见范围过滤，创建时间倒序）。"""
        ...

    async def update(
        self, tenant_id: str, base_id: str, patch: KnowledgeBasePatch
    ) -> KnowledgeBaseRecord | None:
        """应用补丁；知识库不存在时返回 None。"""
        ...

    async def soft_delete(self, tenant_id: str, base_id: str) -> bool:
        """软删除知识库；不存在或已删除返回 False。"""
        ...

    async def count_documents(self, tenant_id: str, base_id: str) -> int:
        """统计该库下未删除的文档数量。"""
        ...


class InMemoryKnowledgeBaseStore:
    """进程内知识库元数据存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._records: dict[tuple[str, str], KnowledgeBaseRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: KnowledgeBaseRecord) -> KnowledgeBaseRecord:
        """写入知识库元数据。

        Args:
            record: 知识库记录。

        Returns:
            KnowledgeBaseRecord: 落库后的记录（补齐时间戳）。
        """
        stamp = _now_iso()
        payload = record if record.created_at else replace(record, created_at=stamp)
        payload = payload if payload.updated_at else replace(payload, updated_at=stamp)
        async with self._lock:
            self._records[(record.tenant_id, record.base_id)] = payload
        return payload

    async def get(
        self, tenant_id: str, base_id: str, *, include_deleted: bool = False
    ) -> KnowledgeBaseRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            include_deleted: 是否包含已软删除的记录。

        Returns:
            KnowledgeBaseRecord | None: 记录；不存在（或已删除且未放行）返回 None。
        """
        async with self._lock:
            record = self._records.get((tenant_id, base_id))
        if record is None:
            return None
        if record.is_deleted and not include_deleted:
            return None
        return record

    async def list(
        self,
        tenant_id: str,
        *,
        keyword: str = "",
        viewer: KnowledgeBaseViewer | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[KnowledgeBaseRecord], int]:
        """分页查询（创建时间倒序）。

        Args:
            tenant_id: 租户 ID。
            keyword: 名称关键字（大小写不敏感）。
            viewer: 观察者上下文（可见范围过滤）。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[KnowledgeBaseRecord], int]: 当前页记录与总条数。
        """
        needle = keyword.strip().lower()
        async with self._lock:
            candidates = [
                record
                for record in self._records.values()
                if record.tenant_id == tenant_id and not record.is_deleted
            ]
        if viewer is not None:
            candidates = [record for record in candidates if viewer.can_see(record)]
        if needle:
            candidates = [record for record in candidates if needle in record.name.lower()]
        candidates.sort(key=lambda item: (item.created_at, item.base_id), reverse=True)
        total = len(candidates)
        page = candidates[offset : offset + limit]
        # 内存后端同样补齐文档数，使上层不再逐条计数（与 postgres 的 JOIN 口径一致）
        enriched: list[KnowledgeBaseRecord] = []
        for record in page:
            count = await self.count_documents(tenant_id, record.base_id)
            enriched.append(replace(record, document_count=count))
        return enriched, total

    async def update(
        self, tenant_id: str, base_id: str, patch: KnowledgeBasePatch
    ) -> KnowledgeBaseRecord | None:
        """应用补丁。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            patch: 更新补丁。

        Returns:
            KnowledgeBaseRecord | None: 更新后的记录；不存在返回 None。
        """
        async with self._lock:
            key = (tenant_id, base_id)
            current = self._records.get(key)
            if current is None or current.is_deleted:
                return None
            changes: dict[str, Any] = {"updated_at": _now_iso()}
            if patch.name is not None:
                changes["name"] = patch.name
            if patch.description is not None:
                changes["description"] = patch.description
            if patch.chunk_policy is not None:
                changes["chunk_policy"] = patch.chunk_policy
            if patch.visibility is not None:
                changes["visibility"] = patch.visibility
            updated = replace(current, **changes)
            self._records[key] = updated
        return updated

    async def soft_delete(self, tenant_id: str, base_id: str) -> bool:
        """软删除。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            bool: 删除成功返回 True；不存在或已删除返回 False。
        """
        async with self._lock:
            key = (tenant_id, base_id)
            current = self._records.get(key)
            if current is None or current.is_deleted:
                return False
            self._records[key] = replace(current, deleted_at=_now_iso(), updated_at=_now_iso())
        return True

    async def count_documents(self, tenant_id: str, base_id: str) -> int:
        """统计文档数量（内存模式下由文档仓储维护，此处读记录快照）。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            int: 文档数量。
        """
        # 延迟导入：文档仓储与知识库仓储互相可见（计数），但模块级互引会成环。
        from app.db.repositories.document_repo import count_by_base_in_memory

        return await count_by_base_in_memory(tenant_id, base_id)

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._records.clear()


class _KnowledgeBaseTableRepository(BaseRepository):
    """`knowledge_base_meta` 表仓储（强制租户条件）。"""

    table = KNOWLEDGE_BASE_TABLE


class PostgresKnowledgeBaseStore:
    """PostgreSQL 知识库元数据存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def create(self, record: KnowledgeBaseRecord) -> KnowledgeBaseRecord:
        """写入知识库元数据（名称冲突按幂等处理，不抛异常）。

        Args:
            record: 知识库记录。

        Returns:
            KnowledgeBaseRecord: 落库后的记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _KnowledgeBaseTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {KNOWLEDGE_BASE_TABLE} (
                    tenant_id, base_id, name, description, visibility, chunk_policy,
                    owner_user_id, document_count, created_by, created_at, updated_at
                ) VALUES (
                    :tenant_id, :base_id, :name, :description, :visibility, :chunk_policy,
                    :owner_user_id, :document_count, :created_by, now(), now()
                )
                ON CONFLICT (tenant_id, base_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    visibility = EXCLUDED.visibility,
                    chunk_policy = EXCLUDED.chunk_policy,
                    updated_at = now()
                RETURNING tenant_id, base_id, name, description, visibility, chunk_policy,
                          owner_user_id, document_count, deleted_at, created_by,
                          created_at, updated_at
                """,
                repo.params(
                    base_id=record.base_id,
                    name=record.name,
                    description=record.description,
                    visibility=record.visibility,
                    chunk_policy=record.chunk_policy,
                    owner_user_id=record.owner_user_id,
                    document_count=record.document_count,
                    created_by=record.created_by,
                ),
            )
        return _to_record(row) if row is not None else record

    async def get(
        self, tenant_id: str, base_id: str, *, include_deleted: bool = False
    ) -> KnowledgeBaseRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            include_deleted: 是否包含已软删除的记录。

        Returns:
            KnowledgeBaseRecord | None: 记录；不存在返回 None。
        """
        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _KnowledgeBaseTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, base_id, name, description, visibility, chunk_policy,
                       owner_user_id, document_count, deleted_at, created_by,
                       created_at, updated_at
                FROM {KNOWLEDGE_BASE_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id {deleted_filter}
                """,
                repo.params(base_id=base_id),
            )
        return _to_record(row) if row is not None else None

    async def list(
        self,
        tenant_id: str,
        *,
        keyword: str = "",
        viewer: KnowledgeBaseViewer | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[KnowledgeBaseRecord], int]:
        """分页查询（可见范围过滤在 SQL 内完成，保证 `total` 与分页一致）。

        Args:
            tenant_id: 租户 ID。
            keyword: 名称关键字（大小写不敏感）。
            viewer: 观察者上下文。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[KnowledgeBaseRecord], int]: 当前页记录与总条数。
        """
        keyword_filter = "AND kb.name ILIKE :keyword" if keyword.strip() else ""
        viewer_filter = (
            f"AND ({assert_identifier('kb')}.visibility = 'organization' "
            f"OR {assert_identifier('kb')}.owner_user_id = :viewer_user_id "
            "OR :viewer_is_admin "
            "OR kb.base_id = ANY(string_to_array(CAST(:viewer_allowed_ids AS text), ','))"
            ")"
            if viewer is not None
            else ""
        )
        viewer_params: dict[str, Any] = (
            {
                "viewer_user_id": viewer.user_id,
                "viewer_is_admin": bool(viewer.is_admin),
                "viewer_allowed_ids": ",".join(viewer.allowed_base_ids),
            }
            if viewer is not None
            else {}
        )
        columns = (
            "kb.tenant_id, kb.base_id, kb.name, kb.description, kb.visibility, kb.chunk_policy, "
            "kb.owner_user_id, kb.deleted_at, kb.created_by, kb.created_at, kb.updated_at, "
            "COALESCE(dc.cnt, 0) AS document_count"
        )
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _KnowledgeBaseTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT {columns}
                FROM {KNOWLEDGE_BASE_TABLE} kb
                {_DOCUMENT_COUNT_JOIN}
                WHERE {repo.tenant_clause("kb")} AND kb.deleted_at IS NULL
                  {keyword_filter} {viewer_filter}
                ORDER BY kb.created_at DESC, kb.base_id DESC
                LIMIT :limit OFFSET :offset
                """,
                repo.params(
                    keyword=f"%{keyword.strip()}%" if keyword.strip() else "",
                    limit=limit,
                    offset=offset,
                    **viewer_params,
                ),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*)
                FROM {KNOWLEDGE_BASE_TABLE} kb
                WHERE {repo.tenant_clause("kb")} AND kb.deleted_at IS NULL
                  {keyword_filter} {viewer_filter}
                """,
                repo.params(
                    keyword=f"%{keyword.strip()}%" if keyword.strip() else "",
                    **viewer_params,
                ),
            )
        return [_to_record(row) for row in rows], int(total or 0)

    async def update(
        self, tenant_id: str, base_id: str, patch: KnowledgeBasePatch
    ) -> KnowledgeBaseRecord | None:
        """应用补丁（只更新非空字段，列名为常量白名单）。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            patch: 更新补丁。

        Returns:
            KnowledgeBaseRecord | None: 更新后的记录；不存在返回 None。
        """
        assignments = ["updated_at = now()"]
        parameters: dict[str, Any] = {"base_id": base_id}
        for column, value in (
            (NAME_COLUMN, patch.name),
            (DESCRIPTION_COLUMN, patch.description),
            (CHUNK_POLICY_COLUMN, patch.chunk_policy),
            (VISIBILITY_COLUMN, patch.visibility),
        ):
            if value is None:
                continue
            assignments.append(f"{assert_identifier(column)} = :{column}")
            parameters[column] = value

        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _KnowledgeBaseTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {KNOWLEDGE_BASE_TABLE}
                SET {", ".join(assignments)}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND deleted_at IS NULL
                RETURNING tenant_id, base_id, name, description, visibility, chunk_policy,
                          owner_user_id, document_count, deleted_at, created_by,
                          created_at, updated_at
                """,
                repo.params(**parameters),
            )
        return _to_record(row) if row is not None else None

    async def soft_delete(self, tenant_id: str, base_id: str) -> bool:
        """软删除。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _KnowledgeBaseTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {KNOWLEDGE_BASE_TABLE}
                SET deleted_at = now(), updated_at = now()
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND deleted_at IS NULL
                RETURNING base_id
                """,
                repo.params(base_id=base_id),
            )
        return row is not None

    async def count_documents(self, tenant_id: str, base_id: str) -> int:
        """统计该库下未删除的文档数量。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            int: 文档数量。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _DocumentCountRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*)
                FROM {DOCUMENT_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND deleted_at IS NULL
                """,
                repo.params(base_id=base_id),
            )
        return int(total or 0)


class _DocumentCountRepository(BaseRepository):
    """`document_meta` 表仓储（仅用于文档计数）。"""

    table = DOCUMENT_TABLE


#: 允许更新的列常量（值与列名同名，便于参数绑定）
NAME_COLUMN: Final[str] = "name"
DESCRIPTION_COLUMN: Final[str] = "description"
CHUNK_POLICY_COLUMN: Final[str] = "chunk_policy"
VISIBILITY_COLUMN: Final[str] = "visibility"


def _to_timestamp(value: Any) -> str:
    """把数据库时间值规范化为 ISO 8601 字符串。

    Args:
        value: 时间值（`datetime` 或字符串）。

    Returns:
        str: ISO 8601 字符串；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _to_optional_timestamp(value: Any) -> str | None:
    """把数据库时间值规范化为可空 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str | None: ISO 8601 字符串；空值返回 None。
    """
    if value is None:
        return None
    return _to_timestamp(value)


def _to_record(row: Mapping[str, Any]) -> KnowledgeBaseRecord:
    """把数据库行映射为知识库记录。

    Args:
        row: 行映射。

    Returns:
        KnowledgeBaseRecord: 知识库记录。
    """
    document_count = row.get("document_count")
    return KnowledgeBaseRecord(
        tenant_id=str(row.get("tenant_id", "")),
        base_id=str(row.get("base_id", "")),
        name=str(row.get("name", "")),
        description=str(row.get("description", "") or ""),
        visibility=str(row.get("visibility", DEFAULT_VISIBILITY)),
        chunk_policy=str(row.get("chunk_policy", DEFAULT_CHUNK_POLICY)),
        owner_user_id=str(row.get("owner_user_id", "")),
        document_count=int(document_count or 0),
        deleted_at=_to_optional_timestamp(row.get("deleted_at")),
        created_by=str(row.get("created_by", "")),
        created_at=_to_timestamp(row.get("created_at")),
        updated_at=_to_timestamp(row.get("updated_at")),
    )


# ===========================================================================
# 进程级单例（内存后端跨模块共享，postgres 后端无状态）
# ===========================================================================
_memory_store: InMemoryKnowledgeBaseStore | None = None


def get_knowledge_base_store(settings: Settings | None = None) -> KnowledgeBaseStore:
    """获取知识库元数据存储。

    Args:
        settings: 可选配置实例。

    Returns:
        KnowledgeBaseStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresKnowledgeBaseStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryKnowledgeBaseStore()
    return _memory_store


def reset_knowledge_base_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "CHUNK_POLICY_COLUMN",
    "DEFAULT_CHUNK_POLICY",
    "DEFAULT_VISIBILITY",
    "DESCRIPTION_COLUMN",
    "DOCUMENT_TABLE",
    "KNOWLEDGE_BASE_TABLE",
    "NAME_COLUMN",
    "VISIBILITY_COLUMN",
    "InMemoryKnowledgeBaseStore",
    "KnowledgeBasePatch",
    "KnowledgeBaseRecord",
    "KnowledgeBaseStore",
    "KnowledgeBaseViewer",
    "PostgresKnowledgeBaseStore",
    "get_knowledge_base_store",
    "reset_knowledge_base_store",
]
