# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。


"""记忆事实仓储 · PostgreSQL 实现（生产）。

三张表：

| 表 | 作用 |
| --- | --- |
| `user_memory` | 事实行（`superseded_by IS NULL` = 活跃） |
| `memory_embedding` | 向量行（pgvector，索引前提是 `vector(1024)`） |
| `user_memory_setting` | 个人开关（全局 + 分类） |

**两条边界**（与拆分前一致，这里只是搬了位置）：

1. **个人边界在这里**：RLS 只保证租户边界，`user_id` 边界必须由本仓储强制——
   所有查询都带 `user_id = :user_id`，该值只来自 `AuthContext`，**不接受请求参数**；
2. **"活跃"的定义**：`superseded_by IS NULL`。被新值取代的历史行**保留可查**，
   但绝不参与召回、不占 200 条上限、也不参与去重比较。

**淘汰是硬删**：删事实行同时删向量行，不留残影。

本文件还承载**行映射器**（`_to_memory` / `_to_setting`）与 PG 专属表示层
（`vector_literal` / `_parse_vector_text` / `_json_switches` / `_timestamp`）：
它们只服务"从 PG 行/列值还原 record"，内存后端直接拿对象，用不到。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.repositories.memory.records import (
    MEMORY_CATEGORIES,
    MemoryRecord,
    MemorySettingRecord,
    new_memory_id,
)
from app.db.session import platform_scope, tenant_scope

logger = get_logger(__name__)


MEMORY_TABLE: Final[str] = "user_memory"
EMBEDDING_TABLE: Final[str] = "memory_embedding"
SETTING_TABLE: Final[str] = "user_memory_setting"


def vector_literal(values: Sequence[float]) -> str:
    """把浮点序列转成 pgvector 字面量（`[0.1,0.2]`）。

    不用 `str(list)`：浮点 repr 在极大/极小值上会带 `e` 记法，pgvector 不接受。

    Args:
        values: 向量分量。

    Returns:
        str: pgvector 字面量。
    """
    return "[" + ",".join(f"{float(item):.8f}" for item in values) + "]"


def _json_switches(value: Any) -> dict[str, bool]:
    """把 JSONB 分类开关读成 `{category: bool}`。

    Args:
        value: 列值（`dict` / JSON 字符串 / None）。

    Returns:
        dict[str, bool]: 开关表（非法键一律忽略，非法类别由调用方兜住）。
    """
    raw: dict[str, Any]
    if isinstance(value, dict):
        raw = dict(value)
    elif isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return {}
        raw = loaded if isinstance(loaded, dict) else {}
    else:
        return {}
    return {str(key): bool(item) for key, item in raw.items() if str(key) in MEMORY_CATEGORIES}


def _to_memory(row: Mapping[str, Any]) -> MemoryRecord:
    """把数据库行转成记忆记录。

    Args:
        row: 行映射。

    Returns:
        MemoryRecord: 记忆记录。
    """
    return MemoryRecord(
        tenant_id=str(row.get("tenant_id", "")),
        memory_id=str(row.get("memory_id", "")),
        user_id=str(row.get("user_id", "")),
        category=str(row.get("category", "identity")),
        content=str(row.get("content", "") or ""),
        confidence=str(row.get("confidence", "high")),
        source_conversation_id=str(row.get("source_conversation_id", "") or ""),
        source_message_id=str(row.get("source_message_id", "") or ""),
        superseded_by=str(row.get("superseded_by", "") or ""),
        normalized_hash=str(row.get("normalized_hash", "") or ""),
        hit_count=int(row.get("hit_count", 1) or 1),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")),
    )


def _to_setting(row: Mapping[str, Any]) -> MemorySettingRecord:
    """把数据库行转成开关记录。

    Args:
        row: 行映射。

    Returns:
        MemorySettingRecord: 开关记录。
    """
    return MemorySettingRecord(
        tenant_id=str(row.get("tenant_id", "")),
        user_id=str(row.get("user_id", "")),
        enabled=bool(row.get("enabled", True)),
        category_switches=_json_switches(row.get("category_switches")),
        updated_at=_timestamp(row.get("updated_at")),
    )


def _timestamp(value: Any) -> str:
    """把时间值规范成 ISO 8601（UTC，`Z` 结尾）。

    Args:
        value: 时间值。

    Returns:
        str: ISO 字符串；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text.replace("+00:00", "Z") if text else ""


class _MemoryTableRepository(BaseRepository):
    """`user_memory` 表仓储。"""

    table = MEMORY_TABLE


class _EmbeddingTableRepository(BaseRepository):
    """`memory_embedding` 表仓储。"""

    table = EMBEDDING_TABLE


class _SettingTableRepository(BaseRepository):
    """`user_memory_setting` 表仓储。"""

    table = SETTING_TABLE


class PostgresMemoryStore:
    """PostgreSQL 记忆存储。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def get_setting(self, tenant_id: str, user_id: str) -> MemorySettingRecord:
        """读开关（无行时返回默认全开）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。

        Returns:
            MemorySettingRecord: 开关记录。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _SettingTableRepository(uow)
            row = await repo.fetch_one(
                f"SELECT * FROM {SETTING_TABLE} "
                f"WHERE {repo.tenant_clause()} AND user_id = :user_id",
                repo.params(user_id=user_id),
            )
        if row is None:
            return MemorySettingRecord(tenant_id=tenant_id, user_id=user_id, enabled=True)
        return _to_setting(row)

    async def upsert_setting(self, record: MemorySettingRecord) -> MemorySettingRecord:
        """写开关。

        Args:
            record: 开关记录。

        Returns:
            MemorySettingRecord: 落库后的记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _SettingTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {SETTING_TABLE} (
                    tenant_id, user_id, enabled, category_switches, updated_at
                ) VALUES (
                    :tenant_id, :user_id, :enabled, CAST(:category_switches AS jsonb), now()
                )
                ON CONFLICT (tenant_id, user_id) DO UPDATE
                   SET enabled = EXCLUDED.enabled,
                       category_switches = EXCLUDED.category_switches,
                       updated_at = now()
                RETURNING *
                """,
                repo.params(
                    user_id=record.user_id,
                    enabled=record.enabled,
                    category_switches=json.dumps(
                        record.category_switches, ensure_ascii=False, sort_keys=True
                    ),
                ),
            )
        assert row is not None
        return _to_setting(row)

    async def list_active(
        self, tenant_id: str, user_id: str, *, category: str = ""
    ) -> list[MemoryRecord]:
        """列出活跃事实。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 可选类别过滤。

        Returns:
            list[MemoryRecord]: 活跃事实（更新时间降序）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {MEMORY_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND user_id = :user_id
                   AND superseded_by IS NULL
                   AND (:category = '' OR category = :category)
                 ORDER BY updated_at DESC, memory_id DESC
                """,
                repo.params(user_id=user_id, category=category),
            )
        return [_to_memory(row) for row in rows]

    async def count_active(self, tenant_id: str, user_id: str) -> int:
        """统计活跃事实条数。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。

        Returns:
            int: 条数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT count(*) FROM {MEMORY_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND user_id = :user_id
                   AND superseded_by IS NULL
                """,
                repo.params(user_id=user_id),
            )
        return int(total or 0)

    async def get(self, tenant_id: str, memory_id: str) -> MemoryRecord | None:
        """按 ID 读取任意状态的行。

        Args:
            tenant_id: 企业 ID。
            memory_id: 记忆 ID。

        Returns:
            MemoryRecord | None: 记忆记录。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            row = await repo.fetch_one(
                f"SELECT * FROM {MEMORY_TABLE} "
                f"WHERE {repo.tenant_clause()} AND memory_id = :memory_id",
                repo.params(memory_id=memory_id),
            )
        return _to_memory(row) if row is not None else None

    async def list_page(
        self,
        tenant_id: str,
        user_id: str,
        *,
        category: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MemoryRecord], int]:
        """分页列出当前值（MM1）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 可选类别过滤。
            page: 页码（从 1 开始）。
            page_size: 每页条数。

        Returns:
            tuple[list[MemoryRecord], int]: 本页行与总数。
        """
        offset = max(0, (page - 1) * page_size)
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {MEMORY_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND user_id = :user_id
                   AND superseded_by IS NULL
                   AND (:category = '' OR category = :category)
                 ORDER BY updated_at DESC, memory_id DESC
                 LIMIT :limit OFFSET :offset
                """,
                repo.params(user_id=user_id, category=category, limit=page_size, offset=offset),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT count(*) FROM {MEMORY_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND user_id = :user_id
                   AND superseded_by IS NULL
                   AND (:category = '' OR category = :category)
                """,
                repo.params(user_id=user_id, category=category),
            )
        return [_to_memory(row) for row in rows], int(total or 0)

    async def search_similar(
        self,
        tenant_id: str,
        user_id: str,
        embedding: Sequence[float],
        *,
        limit: int,
        categories: Sequence[str] | None = None,
    ) -> list[MemoryRecord]:
        """向量检索（pgvector 余弦距离 `<=>`，走 HNSW 索引）。

        `<=>` 返回的是**距离**（1 − 余弦相似度），按它升序就是"最相近在前"。
        只 join 当前值（`superseded_by IS NULL`）——历史值不参与召回（PRD §4.4）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            embedding: 查询向量。
            limit: 条数上限。
            categories: 允许的类别（None 表示不限）。

        Returns:
            list[MemoryRecord]: 最相近的当前值。
        """
        allowed = list(categories) if categories else list(MEMORY_CATEGORIES)
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT memory.*
                  FROM {MEMORY_TABLE} AS memory
                  JOIN {EMBEDDING_TABLE} AS embedding
                    ON embedding.tenant_id = memory.tenant_id
                   AND embedding.memory_id = memory.memory_id
                 WHERE {repo.tenant_clause("memory")}
                   AND memory.user_id = :user_id
                   AND memory.superseded_by IS NULL
                   AND memory.category = ANY(:categories)
                 ORDER BY embedding.embedding <=> CAST(:embedding AS vector)
                 LIMIT :limit
                """,
                repo.params(
                    user_id=user_id,
                    categories=allowed,
                    embedding=vector_literal(embedding),
                    limit=limit,
                ),
            )
        return [_to_memory(row) for row in rows]

    async def delete_by_category(self, tenant_id: str, user_id: str, *, category: str = "") -> int:
        """按类别硬删（`category` 为空 = 全删），同一事务内先删向量再删事实。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 类别（空表示全部）。

        Returns:
            int: 删除的事实条数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            embed_repo = _EmbeddingTableRepository(uow)
            await embed_repo.execute(
                f"""
                DELETE FROM {EMBEDDING_TABLE}
                 WHERE {embed_repo.tenant_clause()}
                   AND memory_id IN (
                       SELECT memory_id FROM {MEMORY_TABLE}
                        WHERE {embed_repo.tenant_clause()}
                          AND user_id = :user_id
                          AND (:category = '' OR category = :category)
                   )
                """,
                embed_repo.params(user_id=user_id, category=category),
            )
            repo = _MemoryTableRepository(uow)
            deleted = await repo.fetch_scalar(
                f"""
                WITH removed AS (
                    DELETE FROM {MEMORY_TABLE}
                     WHERE {repo.tenant_clause()}
                       AND user_id = :user_id
                       AND (:category = '' OR category = :category)
                    RETURNING 1
                )
                SELECT count(*) FROM removed
                """,
                repo.params(user_id=user_id, category=category),
            )
        return int(deleted or 0)

    async def find_by_hash(
        self, tenant_id: str, user_id: str, category: str, normalized_hash: str
    ) -> MemoryRecord | None:
        """按去重键查活跃事实。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 类别。
            normalized_hash: 去重键。

        Returns:
            MemoryRecord | None: 命中行；没有返回 None。
        """
        if not normalized_hash:
            return None
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT * FROM {MEMORY_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND user_id = :user_id
                   AND category = :category
                   AND normalized_hash = :normalized_hash
                   AND superseded_by IS NULL
                """,
                repo.params(user_id=user_id, category=category, normalized_hash=normalized_hash),
            )
        return _to_memory(row) if row is not None else None

    async def insert(self, record: MemoryRecord) -> MemoryRecord | None:
        """插入事实（`ON CONFLICT DO NOTHING` 吸收并发下的同键插入）。

        Args:
            record: 记忆记录。

        Returns:
            MemoryRecord | None: 落库后的记录；冲突返回 None。
        """
        memory_id = record.memory_id or new_memory_id()
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {MEMORY_TABLE} (
                    tenant_id, memory_id, user_id, category, content, confidence,
                    source_conversation_id, source_message_id, superseded_by,
                    normalized_hash, hit_count
                ) VALUES (
                    :tenant_id, :memory_id, :user_id, :category, :content, :confidence,
                    :source_conversation_id, :source_message_id, NULLIF(:superseded_by, ''),
                    :normalized_hash, :hit_count
                )
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                repo.params(
                    memory_id=memory_id,
                    user_id=record.user_id,
                    category=record.category,
                    content=record.content,
                    confidence=record.confidence,
                    source_conversation_id=record.source_conversation_id,
                    source_message_id=record.source_message_id,
                    superseded_by=record.superseded_by,
                    normalized_hash=record.normalized_hash,
                    hit_count=max(1, record.hit_count),
                ),
            )
        return _to_memory(row) if row is not None else None

    async def touch(self, record: MemoryRecord, *, hit_count: int, confidence: str) -> None:
        """合并（刷新时间与计数，不新增行）。

        Args:
            record: 命中的事实。
            hit_count: 新的命中次数。
            confidence: 新的置信度。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {MEMORY_TABLE}
                   SET hit_count = :hit_count,
                       confidence = :confidence,
                       updated_at = now()
                 WHERE {repo.tenant_clause()}
                   AND memory_id = :memory_id
                   AND user_id = :user_id
                """,
                repo.params(
                    memory_id=record.memory_id,
                    user_id=record.user_id,
                    hit_count=max(1, hit_count),
                    confidence=confidence,
                ),
            )

    async def supersede(self, tenant_id: str, memory_id: str, new_memory_id: str) -> None:
        """标记旧值被新值取代。

        Args:
            tenant_id: 企业 ID。
            memory_id: 旧值 ID。
            new_memory_id: 新值 ID。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _MemoryTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {MEMORY_TABLE}
                   SET superseded_by = :new_memory_id,
                       updated_at = now()
                 WHERE {repo.tenant_clause()} AND memory_id = :memory_id
                """,
                repo.params(memory_id=memory_id, new_memory_id=new_memory_id),
            )

    async def delete(self, tenant_id: str, user_id: str, memory_ids: Sequence[str]) -> int:
        """硬删事实（含向量行），同一事务内完成。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人（防越权）。
            memory_ids: 待删 ID。

        Returns:
            int: 实际删除条数。
        """
        ids = [item for item in memory_ids if item]
        if not ids:
            return 0
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            embed_repo = _EmbeddingTableRepository(uow)
            await embed_repo.execute(
                f"DELETE FROM {EMBEDDING_TABLE} "
                f"WHERE {embed_repo.tenant_clause()} AND memory_id = ANY(:memory_ids)",
                embed_repo.params(memory_ids=ids),
            )
            repo = _MemoryTableRepository(uow)
            deleted = await repo.fetch_scalar(
                f"""
                WITH removed AS (
                    DELETE FROM {MEMORY_TABLE}
                     WHERE {repo.tenant_clause()}
                       AND user_id = :user_id
                       AND memory_id = ANY(:memory_ids)
                    RETURNING 1
                )
                SELECT count(*) FROM removed
                """,
                repo.params(user_id=user_id, memory_ids=ids),
            )
        return int(deleted or 0)

    async def upsert_embedding(
        self,
        tenant_id: str,
        memory_id: str,
        *,
        model: str,
        dim: int,
        embedding: Sequence[float],
    ) -> None:
        """写入/覆盖向量行。

        Args:
            tenant_id: 企业 ID。
            memory_id: 记忆 ID。
            model: 向量模型标识。
            dim: 维度。
            embedding: 向量分量。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _EmbeddingTableRepository(uow)
            await repo.execute(
                f"""
                INSERT INTO {EMBEDDING_TABLE} (
                    tenant_id, memory_id, model, dim, embedding, updated_at
                ) VALUES (
                    :tenant_id, :memory_id, :model, :dim,
                    CAST(:embedding AS vector), now()
                )
                ON CONFLICT (tenant_id, memory_id) DO UPDATE
                   SET model = EXCLUDED.model,
                       dim = EXCLUDED.dim,
                       embedding = EXCLUDED.embedding,
                       updated_at = now()
                """,
                repo.params(
                    memory_id=memory_id,
                    model=model,
                    dim=int(dim),
                    embedding=vector_literal(embedding),
                ),
            )

    async def list_embeddings(
        self, tenant_id: str, memory_ids: Sequence[str]
    ) -> dict[str, list[float]]:
        """批量读向量。

        Args:
            tenant_id: 企业 ID。
            memory_ids: 记忆 ID 列表。

        Returns:
            dict[str, list[float]]: `memory_id -> 向量`；向量能力不可用时返回空字典。
        """
        ids = [item for item in memory_ids if item]
        if not ids:
            return {}
        try:
            async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
                repo = _EmbeddingTableRepository(uow)
                rows = await repo.fetch_all(
                    f"""
                    SELECT memory_id, embedding::text AS embedding_text
                      FROM {EMBEDDING_TABLE}
                     WHERE {repo.tenant_clause()} AND memory_id = ANY(:memory_ids)
                    """,
                    repo.params(memory_ids=ids),
                )
        except Exception as exc:  # 向量能力缺失（D-3）→ 返回空，调用方退化为字符串去重
            logger.warning(
                "memory_embedding_read_degraded",
                tenant_id=tenant_id,
                detail=type(exc).__name__,
            )
            return {}
        return {
            str(row["memory_id"]): _parse_vector_text(str(row.get("embedding_text", "")))
            for row in rows
        }

    async def vector_ready(self) -> bool:
        """向量能力是否可用（`memory_embedding` 表可读且 `embedding` 列存在）。

        Returns:
            bool: 可用时返回 True。
        """
        try:
            async with platform_scope(settings=self._settings, read_only=True) as uow:
                repo = _EmbeddingTableRepository(uow)
                found = await repo.fetch_scalar(
                    "SELECT count(*) FROM pg_attribute "
                    "WHERE attrelid = to_regclass(:table_name) AND attname = 'embedding'",
                    {"table_name": EMBEDDING_TABLE},
                )
        except Exception as exc:
            logger.warning("memory_vector_check_degraded", detail=type(exc).__name__)
            return False
        return bool(found)


def _parse_vector_text(text: str) -> list[float]:
    """解析 pgvector 的文本表示（`[0.1,0.2]`）。

    Args:
        text: 列值（`embedding::text`）。

    Returns:
        list[float]: 向量分量；无法解析时返回空列表。
    """
    body = (text or "").strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    if not body:
        return []
    result: list[float] = []
    for chunk in body.split(","):
        try:
            result.append(float(chunk.strip()))
        except ValueError:
            return []
    return result


__all__: list[str] = [
    "EMBEDDING_TABLE",
    "MEMORY_TABLE",
    "SETTING_TABLE",
    "PostgresMemoryStore",
    "vector_literal",
]
