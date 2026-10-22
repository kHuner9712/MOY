"""记忆事实仓储（阶段四 T4-03.3，架构 §3.3）。

三张表：`user_memory`（事实）/ `memory_embedding`（向量）/ `user_memory_setting`（开关）。

**两条边界**：

1. **个人边界在这里**：RLS 只保证租户边界，`user_id` 边界必须由本模块强制——
   所有查询都带 `user_id = :user_id`，该值只来自 `AuthContext`，**不接受请求参数**。
   少写一次就是"张三的记忆出现在李四的回答里"，这是记忆功能唯一不可接受的事故。
2. **"活跃"的定义**：`superseded_by IS NULL`。被新值取代的历史行**保留可查**，
   但绝不参与召回、不占 200 条上限、也不参与去重比较。

**淘汰是硬删**（PRD §4.4「不允许删了还在数据库里」）：删事实行同时删向量行，
不留残影。软删只存在于会话（`conversation.deleted_at`），记忆域没有软删概念。

向量维度的坑（架构 §3.3.1）：`vector(1024)` 是索引前提，换模型必须重建列；
因此 `memory_embedding` 的写入一律带 `model` 与 `dim`，便于运维判断哪些行需要重算。
"""

# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import platform_scope, tenant_scope

logger = get_logger(__name__)

MEMORY_TABLE: Final[str] = "user_memory"
EMBEDDING_TABLE: Final[str] = "memory_embedding"
SETTING_TABLE: Final[str] = "user_memory_setting"

#: 6 类可抽取事实（与 DDL 的 CHECK 一致，PRD §4.2）
MEMORY_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"identity", "org", "preference", "convention", "constraint", "project"}
)

#: 置信度（与 DDL 的 CHECK 一致）
CONFIDENCE_LEVELS: Final[frozenset[str]] = frozenset({"high", "low"})

#: 分类开关的键（用户可见名在 UI 层，本层只用内部键）
CATEGORY_ORDER: Final[tuple[str, ...]] = (
    "identity",
    "org",
    "preference",
    "convention",
    "constraint",
    "project",
)


def new_memory_id() -> str:
    """生成记忆 ID（`mem_` + ULID）。

    Returns:
        str: 记忆 ID。
    """
    return new_resource_id("mem")


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


@dataclass(slots=True)
class MemoryRecord:
    """记忆事实行。

    Attributes:
        tenant_id: 企业 ID。
        memory_id: 记忆 ID（`mem_` + ULID）。
        user_id: 归属个人。
        category: 6 类之一。
        content: 事实正文（≤60 字，用户可读）。
        confidence: `high`（明确陈述）/ `low`（推测，不注入上下文）。
        source_conversation_id: 来源会话。
        source_message_id: 来源消息（**只可能是 `role=user` 的行**）。
        superseded_by: 被哪条新值取代；非空即不参与召回。
        normalized_hash: 同义去重键（`sha256(category + 归一化内容)`）。
        hit_count: 命中次数（同一事实重复出现时 +1）。
        created_at: 创建时间。
        updated_at: 更新时间（合并时刷新）。
    """

    tenant_id: str = ""
    memory_id: str = ""
    user_id: str = ""
    category: str = "identity"
    content: str = ""
    confidence: str = "high"
    source_conversation_id: str = ""
    source_message_id: str = ""
    superseded_by: str = ""
    normalized_hash: str = ""
    hit_count: int = 1
    created_at: str = ""
    updated_at: str = ""

    @property
    def active(self) -> bool:
        """是否仍为当前值（未被他条取代）。

        Returns:
            bool: `superseded_by` 为空时返回 True。
        """
        return not self.superseded_by


@dataclass(slots=True)
class MemorySettingRecord:
    """记忆开关行。

    Attributes:
        tenant_id: 企业 ID。
        user_id: 归属个人。
        enabled: 全局开关（关 = 不抽取 + 不召回）。
        category_switches: 分类开关；**缺省视为开启**（表里只存被显式改过的类）。
        updated_at: 更新时间。
    """

    tenant_id: str = ""
    user_id: str = ""
    enabled: bool = True
    category_switches: dict[str, bool] = field(default_factory=dict)
    updated_at: str = ""

    def category_enabled(self, category: str) -> bool:
        """某一类是否允许抽取/召回（缺省开启）。

        Args:
            category: 类别。

        Returns:
            bool: 允许时返回 True；全局关闭时恒为 False。
        """
        if not self.enabled:
            return False
        return bool(self.category_switches.get(category, True))


class MemoryStore(Protocol):
    """记忆存储契约（内存 / PostgreSQL 双实现）。"""

    async def get_setting(self, tenant_id: str, user_id: str) -> MemorySettingRecord:
        """读开关（无行时返回默认全开）。"""
        ...

    async def upsert_setting(self, record: MemorySettingRecord) -> MemorySettingRecord:
        """写开关（整行覆盖）。"""
        ...

    async def list_active(
        self, tenant_id: str, user_id: str, *, category: str = ""
    ) -> list[MemoryRecord]:
        """列出活跃事实（`superseded_by IS NULL`，按更新时间降序）。"""
        ...

    async def get(self, tenant_id: str, memory_id: str) -> MemoryRecord | None:
        """按 ID 读取**任意状态**的行（含已被取代的历史值，`superseded_by` 链用）。"""
        ...

    async def list_page(
        self,
        tenant_id: str,
        user_id: str,
        *,
        category: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MemoryRecord], int]:
        """分页列出当前值（MM1 用；只返回 `superseded_by IS NULL` 的行）。"""
        ...

    async def search_similar(
        self,
        tenant_id: str,
        user_id: str,
        embedding: Sequence[float],
        *,
        limit: int,
        categories: Sequence[str] | None = None,
    ) -> list[MemoryRecord]:
        """向量检索（按余弦距离升序返回最相近的当前值）。"""
        ...

    async def delete_by_category(self, tenant_id: str, user_id: str, *, category: str = "") -> int:
        """按类别硬删（`category` 为空 = 全删）；返回删除的事实条数。"""
        ...

    async def count_active(self, tenant_id: str, user_id: str) -> int:
        """统计活跃事实条数。"""
        ...

    async def find_by_hash(
        self, tenant_id: str, user_id: str, category: str, normalized_hash: str
    ) -> MemoryRecord | None:
        """按去重键查活跃事实（同 hash 即同一条事实）。"""
        ...

    async def insert(self, record: MemoryRecord) -> MemoryRecord | None:
        """插入事实；同去重键冲突返回 None（并发下的兜底）。"""
        ...

    async def touch(self, record: MemoryRecord, *, hit_count: int, confidence: str) -> None:
        """合并：刷新 `updated_at` / `hit_count` / `confidence`，**不新增行**。"""
        ...

    async def supersede(self, tenant_id: str, memory_id: str, new_memory_id: str) -> None:
        """把旧值标记为被新值取代（`superseded_by` 链）。"""
        ...

    async def delete(self, tenant_id: str, user_id: str, memory_ids: Sequence[str]) -> int:
        """硬删事实（含向量行）；返回删除的事实条数。"""
        ...

    async def upsert_embedding(
        self,
        tenant_id: str,
        memory_id: str,
        *,
        model: str,
        dim: int,
        embedding: Sequence[float],
    ) -> None:
        """写入/覆盖向量行。"""
        ...

    async def list_embeddings(
        self, tenant_id: str, memory_ids: Sequence[str]
    ) -> dict[str, list[float]]:
        """批量读向量（缺失的事实不出现在结果里）。"""
        ...

    async def vector_ready(self) -> bool:
        """向量能力是否可用（扩展/表/配置三者齐备）。"""
        ...


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


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601，`Z` 结尾）。

    Returns:
        str: 时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class InMemoryMemoryStore:
    """进程内记忆存储（development / test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._facts: dict[tuple[str, str], MemoryRecord] = {}
        self._settings: dict[tuple[str, str], MemorySettingRecord] = {}
        self._embeddings: dict[tuple[str, str], tuple[str, int, list[float]]] = {}

    async def get_setting(self, tenant_id: str, user_id: str) -> MemorySettingRecord:
        """读开关（无行时返回默认全开）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。

        Returns:
            MemorySettingRecord: 开关记录。
        """
        found = self._settings.get((tenant_id, user_id))
        if found is not None:
            return found
        return MemorySettingRecord(tenant_id=tenant_id, user_id=user_id, enabled=True)

    async def upsert_setting(self, record: MemorySettingRecord) -> MemorySettingRecord:
        """写开关。

        Args:
            record: 开关记录。

        Returns:
            MemorySettingRecord: 落库后的记录。
        """
        stored = MemorySettingRecord(
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            enabled=record.enabled,
            category_switches=dict(record.category_switches),
            updated_at=_now_iso(),
        )
        self._settings[(stored.tenant_id, stored.user_id)] = stored
        return stored

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
        rows = [
            item
            for item in self._facts.values()
            if item.tenant_id == tenant_id
            and item.user_id == user_id
            and item.active
            and (not category or item.category == category)
        ]
        rows.sort(key=lambda item: (item.updated_at, item.memory_id), reverse=True)
        return rows

    async def count_active(self, tenant_id: str, user_id: str) -> int:
        """统计活跃事实条数。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。

        Returns:
            int: 条数。
        """
        return len(
            [
                item
                for item in self._facts.values()
                if item.tenant_id == tenant_id and item.user_id == user_id and item.active
            ]
        )

    async def get(self, tenant_id: str, memory_id: str) -> MemoryRecord | None:
        """按 ID 读取任意状态的行。

        Args:
            tenant_id: 企业 ID。
            memory_id: 记忆 ID。

        Returns:
            MemoryRecord | None: 记忆记录。
        """
        return self._facts.get((tenant_id, memory_id))

    async def list_page(
        self,
        tenant_id: str,
        user_id: str,
        *,
        category: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[MemoryRecord], int]:
        """分页列出当前值（更新时间降序）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 可选类别过滤。
            page: 页码（从 1 开始）。
            page_size: 每页条数。

        Returns:
            tuple[list[MemoryRecord], int]: 本页行与总数。
        """
        rows = await self.list_active(tenant_id, user_id, category=category)
        offset = max(0, (page - 1) * page_size)
        return rows[offset : offset + page_size], len(rows)

    async def search_similar(
        self,
        tenant_id: str,
        user_id: str,
        embedding: Sequence[float],
        *,
        limit: int,
        categories: Sequence[str] | None = None,
    ) -> list[MemoryRecord]:
        """向量检索（内存实现：本地算余弦并排序）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            embedding: 查询向量。
            limit: 条数上限。
            categories: 允许的类别（None 表示不限）。

        Returns:
            list[MemoryRecord]: 最相近的当前值（距离升序）。
        """
        from app.services.memory_embedding import cosine_similarity

        allowed = set(categories) if categories else set(MEMORY_CATEGORIES)
        active = [
            item for item in await self.list_active(tenant_id, user_id) if item.category in allowed
        ]
        vectors = await self.list_embeddings(tenant_id, [item.memory_id for item in active])
        scored: list[tuple[float, MemoryRecord]] = []
        for item in active:
            vector = vectors.get(item.memory_id)
            if not vector:
                continue
            scored.append((cosine_similarity(embedding, vector), item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
        return [item for _score, item in scored[: max(0, limit)]]

    async def delete_by_category(self, tenant_id: str, user_id: str, *, category: str = "") -> int:
        """按类别硬删（`category` 为空 = 全删），含历史值与向量行。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人。
            category: 类别（空表示全部）。

        Returns:
            int: 删除的事实条数。
        """
        victims = [
            item.memory_id
            for item in list(self._facts.values())
            if item.tenant_id == tenant_id
            and item.user_id == user_id
            and (not category or item.category == category)
        ]
        return await self.delete(tenant_id, user_id, victims)

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
        for item in self._facts.values():
            if (
                item.tenant_id == tenant_id
                and item.user_id == user_id
                and item.category == category
                and item.normalized_hash == normalized_hash
                and item.active
            ):
                return item
        return None

    async def insert(self, record: MemoryRecord) -> MemoryRecord | None:
        """插入事实（同去重键冲突返回 None）。

        Args:
            record: 记忆记录。

        Returns:
            MemoryRecord | None: 落库后的记录；冲突返回 None。
        """
        conflict = await self.find_by_hash(
            record.tenant_id, record.user_id, record.category, record.normalized_hash
        )
        if conflict is not None:
            return None
        stamp = _now_iso()
        stored = MemoryRecord(
            tenant_id=record.tenant_id,
            memory_id=record.memory_id or new_memory_id(),
            user_id=record.user_id,
            category=record.category,
            content=record.content,
            confidence=record.confidence if record.confidence in CONFIDENCE_LEVELS else "high",
            source_conversation_id=record.source_conversation_id,
            source_message_id=record.source_message_id,
            superseded_by=record.superseded_by,
            normalized_hash=record.normalized_hash,
            hit_count=max(1, record.hit_count),
            created_at=record.created_at or stamp,
            updated_at=record.updated_at or stamp,
        )
        self._facts[(stored.tenant_id, stored.memory_id)] = stored
        return stored

    async def touch(self, record: MemoryRecord, *, hit_count: int, confidence: str) -> None:
        """合并（刷新时间与计数，不新增行）。

        Args:
            record: 命中的事实。
            hit_count: 新的命中次数。
            confidence: 新的置信度（只升不降：明确陈述可抬高推测）。
        """
        stored = self._facts.get((record.tenant_id, record.memory_id))
        if stored is None:
            return
        stored.hit_count = max(1, hit_count)
        stored.confidence = confidence if confidence in CONFIDENCE_LEVELS else stored.confidence
        stored.updated_at = _now_iso()

    async def supersede(self, tenant_id: str, memory_id: str, new_memory_id: str) -> None:
        """标记旧值被新值取代。

        Args:
            tenant_id: 企业 ID。
            memory_id: 旧值 ID。
            new_memory_id: 新值 ID。
        """
        stored = self._facts.get((tenant_id, memory_id))
        if stored is None:
            return
        stored.superseded_by = new_memory_id
        stored.updated_at = _now_iso()

    async def delete(self, tenant_id: str, user_id: str, memory_ids: Sequence[str]) -> int:
        """硬删事实（含向量）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属个人（防越权：只能删自己的）。
            memory_ids: 待删 ID。

        Returns:
            int: 实际删除条数。
        """
        removed = 0
        for memory_id in list(memory_ids):
            key = (tenant_id, memory_id)
            stored = self._facts.get(key)
            if stored is None or stored.user_id != user_id:
                continue
            del self._facts[key]
            self._embeddings.pop(key, None)
            removed += 1
        return removed

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
            model: 向量模型标识（换模型需重算，故必须落库）。
            dim: 维度。
            embedding: 向量分量。
        """
        self._embeddings[(tenant_id, memory_id)] = (
            model,
            int(dim),
            [float(item) for item in embedding],
        )

    async def list_embeddings(
        self, tenant_id: str, memory_ids: Sequence[str]
    ) -> dict[str, list[float]]:
        """批量读向量。

        Args:
            tenant_id: 企业 ID。
            memory_ids: 记忆 ID 列表。

        Returns:
            dict[str, list[float]]: `memory_id -> 向量`。
        """
        result: dict[str, list[float]] = {}
        for memory_id in memory_ids:
            found = self._embeddings.get((tenant_id, memory_id))
            if found is not None:
                result[memory_id] = list(found[2])
        return result

    async def vector_ready(self) -> bool:
        """向量能力是否可用（内存实现恒可用，便于离线用例覆盖向量分支）。

        Returns:
            bool: 恒为 True。
        """
        return True

    def clear(self) -> None:
        """清空存储（测试隔离）。"""
        self._facts.clear()
        self._settings.clear()
        self._embeddings.clear()


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


#: 记忆存储单例（按配置选中实现）
_memory_store: MemoryStore | None = None


def get_memory_store(settings: Settings | None = None) -> MemoryStore:
    """取记忆存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        MemoryStore: 存储实现。
    """
    global _memory_store
    conf = settings or get_settings()
    if _memory_store is None:
        _memory_store = PostgresMemoryStore(conf) if conf.uses_postgres else InMemoryMemoryStore()
    return _memory_store


def reset_memory_store() -> None:
    """重置记忆存储单例（测试隔离用）。"""
    global _memory_store
    _memory_store = None


__all__: list[str] = [
    "CATEGORY_ORDER",
    "CONFIDENCE_LEVELS",
    "EMBEDDING_TABLE",
    "MEMORY_CATEGORIES",
    "MEMORY_TABLE",
    "SETTING_TABLE",
    "InMemoryMemoryStore",
    "MemoryRecord",
    "MemorySettingRecord",
    "MemoryStore",
    "PostgresMemoryStore",
    "get_memory_store",
    "new_memory_id",
    "reset_memory_store",
    "vector_literal",
]
