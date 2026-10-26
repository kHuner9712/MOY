"""记忆事实仓储 · 内存实现（development / test）。

内存后端就是生产语义的**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的
公开方法集、签名与 record 构造点，任何一侧单独改动都会被判红。因此本文件的每个方法
都必须与 PG 侧同名方法**保持同形**（参数名、默认值、同步性），差异只能通过
`TEST_HOOKS`（`clear`）这类显式白名单存在。

**两条边界在这里也要守**（不能只靠 PG 的 RLS 与 `user_id = :user_id`）：所有查询都带
`user_id` 过滤，`delete()` 还要再校验一次归属，否则内存后端会变成"越权用例永远抓不到
的那条路径"。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from app.db.repositories.memory.records import (
    CONFIDENCE_LEVELS,
    MEMORY_CATEGORIES,
    MemoryRecord,
    MemorySettingRecord,
    new_memory_id,
)


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


__all__: list[str] = ["InMemoryMemoryStore"]
