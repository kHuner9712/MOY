# ruff: noqa: S608 - 本模块所有 SQL 的表名/列名均为常量（经 assert_identifier 校验），
# 值一律走命名参数绑定；不用 f-string 无法表达「表名由模块常量决定」这一层。
"""片段稳定映射仓储（阶段二 B05，架构文档 2.4 D-11）。

`segment_map` 是引用回链的**唯一权威源**：引用条携带的 `segment_id` 就是本表的
`segment_key`（`seg_` + 32 位哈希片段），底座重处理后 `native_segment_id` 会变，
但 `segment_key` 不变，因此历史引用仍可定位到「同一份内容的同一位置」。

三态机（架构文档 2.4 第 3 条）：

| 事件 | 处理 |
| --- | --- |
| 检索/引用命中 | `upsert(status='valid', stale_at=NULL)` |
| 重新处理开始 | 生成新 `run_id`，旧行**不动**（处理期间旧引用仍可查看） |
| 新批次成功完成 | `mark_stale(document_id, keep_run_id=新批次)`：旧批次行全部转 `stale` |
| 新批次失败 | 不置 stale（旧行继续可用） |
| 文档删除 | `mark_deleted(document_id)`：抽屉展示「该引用已不可查看」 |

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
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 片段映射表名
SEGMENT_TABLE: Final[str] = "segment_map"

#: 映射状态：可定位
STATUS_VALID: Final[str] = "valid"

#: 映射状态：来自旧版本内容（保留期内仍可展示旧内容）
STATUS_STALE: Final[str] = "stale"

#: 映射状态：文档已删除
STATUS_DELETED: Final[str] = "deleted"

#: 片段摘要保留字数（架构文档 2.4 第 4 条：够抽屉展示，控制表膨胀）
EXCERPT_MAX_CHARS: Final[int] = 512

#: 单次查询邻居片段的最大跨度（上下文只取前后各 1 段）
_NEIGHBOR_SPAN: Final[int] = 1


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_excerpt(content: str) -> str:
    """截取片段摘要（前 512 字）。

    Args:
        content: 片段正文。

    Returns:
        str: 截断后的摘要（不含省略号，避免污染回链内容）。
    """
    return content[:EXCERPT_MAX_CHARS]


@dataclass(frozen=True, slots=True)
class SegmentMapRecord:
    """一条片段稳定映射。

    Attributes:
        tenant_id: 租户 ID。
        segment_key: 稳定标识（`seg_` + 16 位十六进制）。
        base_id: 所属知识库 ID。
        document_id: 自研文档 ID（**不是**底座 ID）。
        run_id: 处理批次 ID（重新处理换批次）。
        segment_index: 文档内片段序号（0 起）。
        native_segment_id: 底座原生片段 ID（仅用于二次取片段，不外泄）。
        content_sha256: 片段正文归一化后的 SHA-256。
        content_excerpt: 片段摘要（≤512 字）。
        position_page: 页码。
        position_paragraph: 段落序号。
        status: `valid` / `stale` / `deleted`。
        created_at: 首次写入时间（ISO 8601）。
        stale_at: 转 stale 的时间（ISO 8601）。
    """

    tenant_id: str
    segment_key: str
    base_id: str = ""
    document_id: str = ""
    run_id: str = ""
    segment_index: int = 0
    native_segment_id: str = ""
    content_sha256: str = ""
    content_excerpt: str = ""
    position_page: int | None = None
    position_paragraph: int | None = None
    status: str = STATUS_VALID
    created_at: str = ""
    stale_at: str | None = None

    @property
    def is_locatable(self) -> bool:
        """是否仍可定位到原文（`valid`）。"""
        return self.status == STATUS_VALID


class SegmentStore(Protocol):
    """片段映射存储契约（内存 / PostgreSQL 双实现）。"""

    async def upsert(self, record: SegmentMapRecord) -> SegmentMapRecord:
        """写入或刷新一条映射（同槽位旧键先移除，再 upsert）。"""
        ...

    async def get(self, tenant_id: str, segment_key: str) -> SegmentMapRecord | None:
        """按稳定标识读取映射。"""
        ...

    async def neighbors(
        self, tenant_id: str, document_id: str, run_id: str, segment_index: int
    ) -> list[SegmentMapRecord]:
        """读取同批次下的前后相邻片段（用于回链上下文）。"""
        ...

    async def mark_stale(self, tenant_id: str, document_id: str, keep_run_id: str = "") -> int:
        """把该文档除当前批次外的有效映射标记为 stale，返回影响行数。"""
        ...

    async def mark_deleted(self, tenant_id: str, document_id: str) -> int:
        """把该文档全部映射标记为 deleted，返回影响行数。"""
        ...

    async def count(self, tenant_id: str, document_id: str) -> int:
        """统计该文档的映射条数（测试与诊断用）。"""
        ...


class InMemorySegmentStore:
    """进程内片段映射存储（development/test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._records: dict[tuple[str, str], SegmentMapRecord] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, record: SegmentMapRecord) -> SegmentMapRecord:
        """写入或刷新一条映射。

        Args:
            record: 映射记录。

        Returns:
            SegmentMapRecord: 落库后的记录（保持首次创建时间）。
        """
        async with self._lock:
            self._drop_conflicting_slot(record)
            existing = self._records.get((record.tenant_id, record.segment_key))
            payload = replace(
                record,
                created_at=existing.created_at if existing is not None else _now_iso(),
                status=STATUS_VALID,
                stale_at=None,
            )
            self._records[(record.tenant_id, record.segment_key)] = payload
        return payload

    async def get(self, tenant_id: str, segment_key: str) -> SegmentMapRecord | None:
        """按稳定标识读取映射。

        Args:
            tenant_id: 租户 ID。
            segment_key: 稳定标识。

        Returns:
            SegmentMapRecord | None: 映射；不存在返回 None。
        """
        async with self._lock:
            return self._records.get((tenant_id, segment_key))

    async def neighbors(
        self, tenant_id: str, document_id: str, run_id: str, segment_index: int
    ) -> list[SegmentMapRecord]:
        """读取同批次下的前后相邻片段。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            run_id: 处理批次。
            segment_index: 当前片段序号。

        Returns:
            list[SegmentMapRecord]: 相邻片段（按序号升序）。
        """
        indexes = {segment_index - offset for offset in range(1, _NEIGHBOR_SPAN + 1)} | {
            segment_index + offset for offset in range(1, _NEIGHBOR_SPAN + 1)
        }
        async with self._lock:
            rows = [
                record
                for record in self._records.values()
                if record.tenant_id == tenant_id
                and record.document_id == document_id
                and record.run_id == run_id
                and record.segment_index in indexes
                and record.status == STATUS_VALID
            ]
        rows.sort(key=lambda item: item.segment_index)
        return rows

    async def mark_stale(self, tenant_id: str, document_id: str, keep_run_id: str = "") -> int:
        """把旧批次的映射标记为 stale。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            keep_run_id: 新批次 ID（为空时全部有效行都转 stale）。

        Returns:
            int: 影响行数。
        """
        stamp = _now_iso()
        affected = 0
        async with self._lock:
            for key, record in list(self._records.items()):
                if record.tenant_id != tenant_id or record.document_id != document_id:
                    continue
                if record.status != STATUS_VALID:
                    continue
                if keep_run_id and record.run_id == keep_run_id:
                    continue
                self._records[key] = replace(record, status=STATUS_STALE, stale_at=stamp)
                affected += 1
        return affected

    async def mark_deleted(self, tenant_id: str, document_id: str) -> int:
        """把文档的全部映射标记为 deleted。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            int: 影响行数。
        """
        stamp = _now_iso()
        affected = 0
        async with self._lock:
            for key, record in list(self._records.items()):
                if record.tenant_id != tenant_id or record.document_id != document_id:
                    continue
                if record.status == STATUS_DELETED:
                    continue
                self._records[key] = replace(
                    record, status=STATUS_DELETED, stale_at=record.stale_at or stamp
                )
                affected += 1
        return affected

    async def count(self, tenant_id: str, document_id: str) -> int:
        """统计该文档的映射条数。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            int: 条数。
        """
        async with self._lock:
            return sum(
                1
                for record in self._records.values()
                if record.tenant_id == tenant_id and record.document_id == document_id
            )

    def _drop_conflicting_slot(self, record: SegmentMapRecord) -> None:
        """移除同槽位（同文档同批次同序号）但键不同的旧行。

        内容变化会产出新键，槽位唯一索引（`uq_seg_slot`）不允许同槽两行并存，
        故写入前先清槽；调用方必须已持有锁。

        Args:
            record: 待写入的记录。
        """
        for key, existing in list(self._records.items()):
            if (
                existing.tenant_id == record.tenant_id
                and existing.document_id == record.document_id
                and existing.run_id == record.run_id
                and existing.segment_index == record.segment_index
                and existing.segment_key != record.segment_key
            ):
                self._records.pop(key, None)

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._records.clear()


class _SegmentTableRepository(BaseRepository):
    """`segment_map` 表仓储（强制租户条件）。"""

    table = SEGMENT_TABLE


class PostgresSegmentStore:
    """PostgreSQL 片段映射存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def upsert(self, record: SegmentMapRecord) -> SegmentMapRecord:
        """写入或刷新一条映射（先清槽再 upsert，与内存实现语义一致）。

        Args:
            record: 映射记录。

        Returns:
            SegmentMapRecord: 落库后的记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _SegmentTableRepository(uow)
            await repo.execute(
                f"""
                DELETE FROM {SEGMENT_TABLE}
                WHERE {repo.tenant_clause()} AND document_id = :document_id
                  AND run_id = :run_id AND segment_index = :segment_index
                  AND segment_key <> :segment_key
                """,
                repo.params(
                    document_id=record.document_id,
                    run_id=record.run_id,
                    segment_index=record.segment_index,
                    segment_key=record.segment_key,
                ),
            )
            row = await repo.fetch_one(
                f"""
                INSERT INTO {SEGMENT_TABLE} (
                    tenant_id, segment_key, base_id, document_id, run_id, segment_index,
                    native_segment_id, content_sha256, content_excerpt,
                    position_page, position_paragraph, status
                ) VALUES (
                    :tenant_id, :segment_key, :base_id, :document_id, :run_id, :segment_index,
                    :native_segment_id, :content_sha256, :content_excerpt,
                    :position_page, :position_paragraph, 'valid'
                )
                ON CONFLICT (tenant_id, segment_key) DO UPDATE SET
                    native_segment_id = EXCLUDED.native_segment_id,
                    content_excerpt = EXCLUDED.content_excerpt,
                    position_page = EXCLUDED.position_page,
                    position_paragraph = EXCLUDED.position_paragraph,
                    status = 'valid',
                    stale_at = NULL
                RETURNING tenant_id, segment_key, base_id, document_id, run_id, segment_index,
                          native_segment_id, content_sha256, content_excerpt,
                          position_page, position_paragraph, status, created_at, stale_at
                """,
                repo.params(
                    segment_key=record.segment_key,
                    base_id=record.base_id,
                    document_id=record.document_id,
                    run_id=record.run_id,
                    segment_index=record.segment_index,
                    native_segment_id=record.native_segment_id,
                    content_sha256=record.content_sha256,
                    content_excerpt=record.content_excerpt,
                    position_page=record.position_page,
                    position_paragraph=record.position_paragraph,
                ),
            )
        return _to_record(row) if row is not None else record

    async def get(self, tenant_id: str, segment_key: str) -> SegmentMapRecord | None:
        """按稳定标识读取映射。

        Args:
            tenant_id: 租户 ID。
            segment_key: 稳定标识。

        Returns:
            SegmentMapRecord | None: 映射；不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _SegmentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, segment_key, base_id, document_id, run_id, segment_index,
                       native_segment_id, content_sha256, content_excerpt,
                       position_page, position_paragraph, status, created_at, stale_at
                FROM {SEGMENT_TABLE}
                WHERE {repo.tenant_clause()} AND segment_key = :segment_key
                """,
                repo.params(segment_key=segment_key),
            )
        return _to_record(row) if row is not None else None

    async def neighbors(
        self, tenant_id: str, document_id: str, run_id: str, segment_index: int
    ) -> list[SegmentMapRecord]:
        """读取同批次下的前后相邻片段。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            run_id: 处理批次。
            segment_index: 当前片段序号。

        Returns:
            list[SegmentMapRecord]: 相邻片段（按序号升序）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _SegmentTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, segment_key, base_id, document_id, run_id, segment_index,
                       native_segment_id, content_sha256, content_excerpt,
                       position_page, position_paragraph, status, created_at, stale_at
                FROM {SEGMENT_TABLE}
                WHERE {repo.tenant_clause()} AND document_id = :document_id
                  AND run_id = :run_id AND status = 'valid'
                  AND segment_index IN (:prev_index, :next_index)
                ORDER BY segment_index ASC
                """,
                repo.params(
                    document_id=document_id,
                    run_id=run_id,
                    prev_index=segment_index - _NEIGHBOR_SPAN,
                    next_index=segment_index + _NEIGHBOR_SPAN,
                ),
            )
        return [_to_record(row) for row in rows]

    async def mark_stale(self, tenant_id: str, document_id: str, keep_run_id: str = "") -> int:
        """把旧批次的映射标记为 stale。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            keep_run_id: 新批次 ID（为空时不加批次条件）。

        Returns:
            int: 影响行数。
        """
        run_filter = "AND run_id <> :keep_run_id" if keep_run_id else ""
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _SegmentTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                WITH affected AS (
                    UPDATE {SEGMENT_TABLE}
                    SET status = 'stale', stale_at = now()
                    WHERE {repo.tenant_clause()} AND document_id = :document_id
                      AND status = 'valid' {run_filter}
                    RETURNING segment_key
                )
                SELECT COUNT(*) FROM affected
                """,
                repo.params(document_id=document_id, keep_run_id=keep_run_id),
            )
        return int(total or 0)

    async def mark_deleted(self, tenant_id: str, document_id: str) -> int:
        """把文档的全部映射标记为 deleted。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            int: 影响行数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _SegmentTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                WITH affected AS (
                    UPDATE {SEGMENT_TABLE}
                    SET status = 'deleted', stale_at = COALESCE(stale_at, now())
                    WHERE {repo.tenant_clause()} AND document_id = :document_id
                      AND status <> 'deleted'
                    RETURNING segment_key
                )
                SELECT COUNT(*) FROM affected
                """,
                repo.params(document_id=document_id),
            )
        return int(total or 0)

    async def count(self, tenant_id: str, document_id: str) -> int:
        """统计该文档的映射条数。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            int: 条数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _SegmentTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {SEGMENT_TABLE}
                WHERE {repo.tenant_clause()} AND document_id = :document_id
                """,
                repo.params(document_id=document_id),
            )
        return int(total or 0)


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


def _to_record(row: Mapping[str, Any]) -> SegmentMapRecord:
    """把数据库行映射为片段记录。

    Args:
        row: 行映射。

    Returns:
        SegmentMapRecord: 片段映射记录。
    """
    stale_at = row.get("stale_at")
    page = row.get("position_page")
    paragraph = row.get("position_paragraph")
    return SegmentMapRecord(
        tenant_id=str(row.get("tenant_id", "")),
        segment_key=str(row.get("segment_key", "")),
        base_id=str(row.get("base_id", "")),
        document_id=str(row.get("document_id", "")),
        run_id=str(row.get("run_id", "")),
        segment_index=int(row.get("segment_index", 0) or 0),
        native_segment_id=str(row.get("native_segment_id", "") or ""),
        content_sha256=str(row.get("content_sha256", "") or ""),
        content_excerpt=str(row.get("content_excerpt", "") or ""),
        position_page=int(page) if page is not None else None,
        position_paragraph=int(paragraph) if paragraph is not None else None,
        status=str(row.get("status", STATUS_VALID)),
        created_at=_to_timestamp(row.get("created_at")),
        stale_at=_to_timestamp(stale_at) if stale_at is not None else None,
    )


# ===========================================================================
# 进程级单例（内存后端跨模块共享，postgres 后端无状态）
# ===========================================================================
_memory_store: InMemorySegmentStore | None = None


def get_segment_store(settings: Settings | None = None) -> SegmentStore:
    """获取片段映射存储。

    Args:
        settings: 可选配置实例。

    Returns:
        SegmentStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresSegmentStore(conf)
    if _memory_store is None:
        _memory_store = InMemorySegmentStore()
    return _memory_store


def reset_segment_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "EXCERPT_MAX_CHARS",
    "SEGMENT_TABLE",
    "STATUS_DELETED",
    "STATUS_STALE",
    "STATUS_VALID",
    "InMemorySegmentStore",
    "PostgresSegmentStore",
    "SegmentMapRecord",
    "SegmentStore",
    "build_excerpt",
    "get_segment_store",
    "reset_segment_store",
]
