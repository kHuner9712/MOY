# ruff: noqa: S608 - 本模块所有 SQL 的表名/列名均为类常量（经 assert_identifier 校验），
# 值一律走命名参数绑定；不用 f-string 无法表达「表名由子类常量决定」这一层。
"""文档元数据仓储（阶段二 B04，架构文档 3.4 / PRD P2-KB-02）。

`document_meta` 是文档清单与处理阶段的**权威源**：

- 五态 `stage`（uploaded / parsing / preparing / completed / failed）给用户看；
- 三态 `index_status`（processing / completed / failed）为兼容字段；
- 两者由数据库 CHECK 约束保证同真同假，写入时**必须成对更新**。

分页是硬要求（PRD 验收 4）：单库 5000 条文档时第 250 页必须稳定返回，
因此 `list()` 一律 `LIMIT / OFFSET` + `COUNT(*)`，禁止全量捞取。

双后端（`OA_BUSINESS_STORE`）：`memory`（进程内）/ `postgres`（短事务 + 租户条件）。
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

#: 文档元数据表名
DOCUMENT_TABLE: Final[str] = "document_meta"

#: 默认内容切分精细度
DEFAULT_CHUNK_POLICY: Final[str] = "default"

#: 初始阶段（已上传）
STAGE_UPLOADED: Final[str] = "uploaded"

#: 处理中三态
INDEX_STATUS_PROCESSING: Final[str] = "processing"

#: 已完成三态（与 `stage='completed'` 同真同假，由 DB CHECK 保证）
INDEX_STATUS_COMPLETED: Final[str] = "completed"

#: 处理失败三态
INDEX_STATUS_FAILED: Final[str] = "failed"


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """文档元数据记录。

    Attributes:
        tenant_id: 租户 ID。
        document_id: 文档 ID。
        base_id: 所属知识库 ID。
        file_name: 文档名称。
        file_size: 文档大小（字节）。
        chunk_policy: 本次使用的内容切分精细度。
        stage: 处理阶段（五态）。
        index_status: 处理进度（三态，兼容字段）。
        progress: 进度百分比（0-100）。
        estimated_remaining_seconds: 预估剩余秒数。
        segment_count: 内容片段数。
        error_message: 处理未成功的原因（业务语言）。
        uploader_user_id: 上传人 ID。
        uploader_name: 上传人展示名（身份底座成员目录接入前以用户 ID 兜底）。
        uploaded_at: 上传时间（ISO 8601）。
        run_id: 处理批次号（R5 重新处理时换新批次，用于片段映射的新旧判定）。
        deleted_at: 软删除时间；未删除为 None。
    """

    tenant_id: str
    document_id: str
    base_id: str = ""
    file_name: str = ""
    file_size: int = 0
    chunk_policy: str = DEFAULT_CHUNK_POLICY
    stage: str = STAGE_UPLOADED
    index_status: str = INDEX_STATUS_PROCESSING
    progress: int = 0
    estimated_remaining_seconds: int | None = None
    segment_count: int = 0
    error_message: str | None = None
    uploader_user_id: str = ""
    uploader_name: str = ""
    uploaded_at: str = ""
    run_id: str = ""
    deleted_at: str | None = None

    @property
    def is_deleted(self) -> bool:
        """是否已软删除。"""
        return bool(self.deleted_at)


@dataclass(frozen=True, slots=True)
class DocumentProgressPatch:
    """文档处理进度补丁（`stage` 与 `index_status` 成对写入）。

    Attributes:
        stage: 五态阶段。
        index_status: 三态进度。
        progress: 进度百分比。
        estimated_remaining_seconds: 预估剩余秒数。
        segment_count: 内容片段数。
        error_message: 失败原因（业务语言）。
        run_id: 处理批次号（为 None 表示不改动当前批次）。
    """

    stage: str = STAGE_UPLOADED
    index_status: str = INDEX_STATUS_PROCESSING
    progress: int = 0
    estimated_remaining_seconds: int | None = None
    segment_count: int = 0
    error_message: str | None = None
    run_id: str | None = None


class DocumentStoreProtocol(Protocol):
    """文档元数据存储契约（内存 / PostgreSQL 双实现）。"""

    async def create(self, record: DocumentRecord) -> DocumentRecord:
        """写入文档元数据（幂等 upsert）。"""
        ...

    async def get(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        """按 ID 读取未删除的文档。"""
        ...

    async def list(
        self,
        tenant_id: str,
        base_id: str,
        *,
        keyword: str = "",
        stage: str | None = None,
        sort_field: str = "uploaded_at",
        sort_direction: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DocumentRecord], int]:
        """分页查询文档（关键字 + 阶段过滤 + 排序）。"""
        ...

    async def update_progress(
        self, tenant_id: str, document_id: str, patch: DocumentProgressPatch
    ) -> DocumentRecord | None:
        """更新处理进度；文档不存在返回 None。"""
        ...

    async def soft_delete(self, tenant_id: str, document_id: str) -> bool:
        """软删除单个文档。"""
        ...

    async def soft_delete_by_base(self, tenant_id: str, base_id: str) -> int:
        """按知识库批量软删除文档，返回被删除条数。"""
        ...

    async def count_by_base(self, tenant_id: str, base_id: str) -> int:
        """统计知识库下未删除的文档数量。"""
        ...


class InMemoryDocumentStore:
    """进程内文档元数据存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._records: dict[tuple[str, str], DocumentRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: DocumentRecord) -> DocumentRecord:
        """写入文档元数据。

        Args:
            record: 文档记录。

        Returns:
            DocumentRecord: 落库后的记录（补齐上传时间）。
        """
        payload = record if record.uploaded_at else replace(record, uploaded_at=_now_iso())
        async with self._lock:
            self._records[(record.tenant_id, record.document_id)] = payload
        return payload

    async def get(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            DocumentRecord | None: 记录；不存在或已删除返回 None。
        """
        async with self._lock:
            record = self._records.get((tenant_id, document_id))
        if record is None or record.is_deleted:
            return None
        return record

    async def list(
        self,
        tenant_id: str,
        base_id: str,
        *,
        keyword: str = "",
        stage: str | None = None,
        sort_field: str = "uploaded_at",
        sort_direction: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DocumentRecord], int]:
        """分页查询。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            keyword: 文档名称关键字（大小写不敏感）。
            stage: 阶段过滤。
            sort_field: 排序字段（`uploaded_at` / `file_name`）。
            sort_direction: 排序方向。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[DocumentRecord], int]: 当前页记录与总条数。
        """
        needle = keyword.strip().lower()
        async with self._lock:
            candidates = [
                record
                for record in self._records.values()
                if record.tenant_id == tenant_id
                and record.base_id == base_id
                and not record.is_deleted
            ]
        if needle:
            candidates = [item for item in candidates if needle in item.file_name.lower()]
        if stage:
            candidates = [item for item in candidates if item.stage == stage]
        reverse = sort_direction.lower() != "asc"
        candidates.sort(key=_sort_key(sort_field), reverse=reverse)
        total = len(candidates)
        return candidates[offset : offset + limit], total

    async def update_progress(
        self, tenant_id: str, document_id: str, patch: DocumentProgressPatch
    ) -> DocumentRecord | None:
        """更新处理进度。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            patch: 进度补丁。

        Returns:
            DocumentRecord | None: 更新后的记录；不存在返回 None。
        """
        async with self._lock:
            key = (tenant_id, document_id)
            current = self._records.get(key)
            if current is None or current.is_deleted:
                return None
            updated = replace(
                current,
                stage=patch.stage,
                index_status=patch.index_status,
                progress=patch.progress,
                estimated_remaining_seconds=patch.estimated_remaining_seconds,
                segment_count=patch.segment_count,
                error_message=patch.error_message,
                run_id=patch.run_id if patch.run_id is not None else current.run_id,
            )
            self._records[key] = updated
        return updated

    async def soft_delete(self, tenant_id: str, document_id: str) -> bool:
        """软删除单个文档。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with self._lock:
            key = (tenant_id, document_id)
            current = self._records.get(key)
            if current is None or current.is_deleted:
                return False
            self._records[key] = replace(current, deleted_at=_now_iso())
        return True

    async def soft_delete_by_base(self, tenant_id: str, base_id: str) -> int:
        """按知识库批量软删除。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            int: 被删除的文档条数。
        """
        stamp = _now_iso()
        removed = 0
        async with self._lock:
            for key, record in list(self._records.items()):
                if (
                    record.tenant_id == tenant_id
                    and record.base_id == base_id
                    and not record.is_deleted
                ):
                    self._records[key] = replace(record, deleted_at=stamp)
                    removed += 1
        return removed

    async def count_by_base(self, tenant_id: str, base_id: str) -> int:
        """统计文档数量。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            int: 未删除的文档数量。
        """
        async with self._lock:
            return sum(
                1
                for record in self._records.values()
                if record.tenant_id == tenant_id
                and record.base_id == base_id
                and not record.is_deleted
            )

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._records.clear()


class _DocumentTableRepository(BaseRepository):
    """`document_meta` 表仓储（强制租户条件）。"""

    table = DOCUMENT_TABLE


class PostgresDocumentStore:
    """PostgreSQL 文档元数据存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def create(self, record: DocumentRecord) -> DocumentRecord:
        """写入文档元数据。

        Args:
            record: 文档记录。

        Returns:
            DocumentRecord: 落库后的记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _DocumentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {DOCUMENT_TABLE} (
                    tenant_id, document_id, base_id, file_name, file_size, chunk_policy,
                    stage, index_status, progress, estimated_remaining_seconds,
                    segment_count, error_message, uploader_user_id, run_id, uploaded_at
                ) VALUES (
                    :tenant_id, :document_id, :base_id, :file_name, :file_size, :chunk_policy,
                    :stage, :index_status, :progress, :estimated_remaining_seconds,
                    :segment_count, :error_message, :uploader_user_id,
                    NULLIF(:run_id, ''), now()
                )
                ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                    file_name = EXCLUDED.file_name,
                    stage = EXCLUDED.stage,
                    index_status = EXCLUDED.index_status,
                    progress = EXCLUDED.progress
                RETURNING tenant_id, document_id, base_id, file_name, file_size, chunk_policy,
                          stage, index_status, progress, estimated_remaining_seconds,
                          segment_count, error_message, uploader_user_id,
                          uploaded_at, run_id, deleted_at
                """,
                repo.params(
                    document_id=record.document_id,
                    base_id=record.base_id,
                    file_name=record.file_name,
                    file_size=record.file_size,
                    chunk_policy=record.chunk_policy,
                    stage=record.stage,
                    index_status=record.index_status,
                    progress=record.progress,
                    estimated_remaining_seconds=record.estimated_remaining_seconds,
                    segment_count=record.segment_count,
                    error_message=record.error_message,
                    uploader_user_id=record.uploader_user_id,
                    run_id=record.run_id,
                ),
            )
        return _to_record(row) if row is not None else record

    async def get(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            DocumentRecord | None: 记录；不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _DocumentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, document_id, base_id, file_name, file_size, chunk_policy,
                       stage, index_status, progress, estimated_remaining_seconds,
                       segment_count, error_message, uploader_user_id, uploaded_at,
                       run_id, deleted_at
                FROM {DOCUMENT_TABLE}
                WHERE {repo.tenant_clause()} AND document_id = :document_id
                  AND deleted_at IS NULL
                """,
                repo.params(document_id=document_id),
            )
        return _to_record(row) if row is not None else None

    async def list(
        self,
        tenant_id: str,
        base_id: str,
        *,
        keyword: str = "",
        stage: str | None = None,
        sort_field: str = "uploaded_at",
        sort_direction: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DocumentRecord], int]:
        """分页查询（5000 条下第 250 页也必须稳定，禁止全量返回）。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            keyword: 文档名称关键字（大小写不敏感）。
            stage: 阶段过滤。
            sort_field: 排序字段（白名单常量）。
            sort_direction: 排序方向（白名单常量）。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[DocumentRecord], int]: 当前页记录与总条数。
        """
        keyword_filter = "AND file_name ILIKE :keyword" if keyword.strip() else ""
        stage_filter = "AND stage = :stage" if stage else ""
        order_by = _order_by(sort_field, sort_direction)
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _DocumentTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, document_id, base_id, file_name, file_size, chunk_policy,
                       stage, index_status, progress, estimated_remaining_seconds,
                       segment_count, error_message, uploader_user_id, uploaded_at,
                       run_id, deleted_at
                FROM {DOCUMENT_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND deleted_at IS NULL {keyword_filter} {stage_filter}
                ORDER BY {order_by}, document_id DESC
                LIMIT :limit OFFSET :offset
                """,
                repo.params(
                    base_id=base_id,
                    keyword=f"%{keyword.strip()}%" if keyword.strip() else "",
                    stage=stage or "",
                    limit=limit,
                    offset=offset,
                ),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*)
                FROM {DOCUMENT_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND deleted_at IS NULL {keyword_filter} {stage_filter}
                """,
                repo.params(
                    base_id=base_id,
                    keyword=f"%{keyword.strip()}%" if keyword.strip() else "",
                    stage=stage or "",
                ),
            )
        return [_to_record(row) for row in rows], int(total or 0)

    async def update_progress(
        self, tenant_id: str, document_id: str, patch: DocumentProgressPatch
    ) -> DocumentRecord | None:
        """更新处理进度（五态与三态成对写入）。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            patch: 进度补丁。

        Returns:
            DocumentRecord | None: 更新后的记录；不存在返回 None。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _DocumentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {DOCUMENT_TABLE}
                SET stage = :stage,
                    index_status = :index_status,
                    progress = :progress,
                    estimated_remaining_seconds = :estimated_remaining_seconds,
                    segment_count = :segment_count,
                    error_message = :error_message,
                    run_id = COALESCE(:run_id, run_id),
                    processed_at = CASE WHEN :stage = 'completed' THEN now() ELSE processed_at END
                WHERE {repo.tenant_clause()} AND document_id = :document_id
                  AND deleted_at IS NULL
                RETURNING tenant_id, document_id, base_id, file_name, file_size, chunk_policy,
                          stage, index_status, progress, estimated_remaining_seconds,
                          segment_count, error_message, uploader_user_id,
                          uploaded_at, run_id, deleted_at
                """,
                repo.params(
                    document_id=document_id,
                    stage=patch.stage,
                    index_status=patch.index_status,
                    progress=patch.progress,
                    estimated_remaining_seconds=patch.estimated_remaining_seconds,
                    segment_count=patch.segment_count,
                    error_message=patch.error_message,
                    run_id=patch.run_id,
                ),
            )
        return _to_record(row) if row is not None else None

    async def soft_delete(self, tenant_id: str, document_id: str) -> bool:
        """软删除单个文档。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _DocumentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {DOCUMENT_TABLE}
                SET deleted_at = now()
                WHERE {repo.tenant_clause()} AND document_id = :document_id
                  AND deleted_at IS NULL
                RETURNING document_id
                """,
                repo.params(document_id=document_id),
            )
        return row is not None

    async def soft_delete_by_base(self, tenant_id: str, base_id: str) -> int:
        """按知识库批量软删除。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            int: 被删除的文档条数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _DocumentTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                WITH removed AS (
                    UPDATE {DOCUMENT_TABLE}
                    SET deleted_at = now()
                    WHERE {repo.tenant_clause()} AND base_id = :base_id
                      AND deleted_at IS NULL
                    RETURNING document_id
                )
                SELECT COUNT(*) FROM removed
                """,
                repo.params(base_id=base_id),
            )
        return int(total or 0)

    async def count_by_base(self, tenant_id: str, base_id: str) -> int:
        """统计文档数量。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            int: 未删除的文档数量。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _DocumentTableRepository(uow)
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


def _sort_key(sort_field: str) -> Any:
    """构造内存排序键。

    Args:
        sort_field: 排序字段（白名单外一律按上传时间）。

    Returns:
        Any: 排序键函数。
    """
    if sort_field == "file_name":
        return lambda record: (record.file_name, record.document_id)
    return lambda record: (record.uploaded_at, record.document_id)


def _order_by(sort_field: str, sort_direction: str) -> str:
    """构造 ORDER BY 片段（列名与方向均取自白名单常量）。

    Args:
        sort_field: 排序字段。
        sort_direction: 排序方向。

    Returns:
        str: 可直接拼接的 ORDER BY 片段。
    """
    column = assert_identifier("file_name" if sort_field == "file_name" else "uploaded_at")
    direction = "ASC" if sort_direction.lower() == "asc" else "DESC"
    return f"{column} {direction}"


def _to_timestamp(value: Any) -> str:
    """把数据库时间值规范化为 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str: ISO 8601 字符串；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _to_record(row: Mapping[str, Any]) -> DocumentRecord:
    """把数据库行映射为文档记录。

    Args:
        row: 行映射。

    Returns:
        DocumentRecord: 文档记录。
    """
    remaining = row.get("estimated_remaining_seconds")
    return DocumentRecord(
        tenant_id=str(row.get("tenant_id", "")),
        document_id=str(row.get("document_id", "")),
        base_id=str(row.get("base_id", "")),
        file_name=str(row.get("file_name", "")),
        file_size=int(row.get("file_size", 0) or 0),
        chunk_policy=str(row.get("chunk_policy", DEFAULT_CHUNK_POLICY)),
        stage=str(row.get("stage", STAGE_UPLOADED)),
        index_status=str(row.get("index_status", INDEX_STATUS_PROCESSING)),
        progress=int(row.get("progress", 0) or 0),
        estimated_remaining_seconds=int(remaining) if remaining is not None else None,
        segment_count=int(row.get("segment_count", 0) or 0),
        error_message=_optional_text(row.get("error_message")),
        uploader_user_id=str(row.get("uploader_user_id", "")),
        uploader_name=str(row.get("uploader_user_id", "")),
        uploaded_at=_to_timestamp(row.get("uploaded_at")),
        run_id=str(row.get("run_id", "") or ""),
        deleted_at=_to_timestamp(row.get("deleted_at")) or None,
    )


def _optional_text(value: Any) -> str | None:
    """把可空文本字段规范化。

    Args:
        value: 原始值。

    Returns:
        str | None: 去空白后的文本；空值返回 None。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ===========================================================================
# 进程级单例（内存后端跨模块共享，postgres 后端无状态）
# ===========================================================================
_memory_store: InMemoryDocumentStore | None = None


def get_document_store(settings: Settings | None = None) -> DocumentStoreProtocol:
    """获取文档元数据存储。

    Args:
        settings: 可选配置实例。

    Returns:
        DocumentStoreProtocol: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresDocumentStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryDocumentStore()
    return _memory_store


def reset_document_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


async def count_by_base_in_memory(tenant_id: str, base_id: str) -> int:
    """内存模式下按知识库统计文档数量（供知识库仓储反向调用）。

    这里直接取本模块的内存单例（而非 `get_document_store()`）：
    调用方已是内存实现，若再按配置取存储会在 `business_store=postgres`
    下拿到数据库实现，造成「内存库 + 数据库文档」的错位计数。

    Args:
        tenant_id: 租户 ID。
        base_id: 知识库 ID。

    Returns:
        int: 未删除的文档数量。
    """
    global _memory_store
    if _memory_store is None:
        _memory_store = InMemoryDocumentStore()
    return await _memory_store.count_by_base(tenant_id, base_id)


__all__: list[str] = [
    "DEFAULT_CHUNK_POLICY",
    "DOCUMENT_TABLE",
    "INDEX_STATUS_COMPLETED",
    "INDEX_STATUS_FAILED",
    "INDEX_STATUS_PROCESSING",
    "STAGE_UPLOADED",
    "DocumentProgressPatch",
    "DocumentRecord",
    "DocumentStoreProtocol",
    "InMemoryDocumentStore",
    "PostgresDocumentStore",
    "count_by_base_in_memory",
    "get_document_store",
    "reset_document_store",
]
