"""记忆事实仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `MemoryStore`，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖（拆分前它们同处一个上帝文件，看不出来；一拆就立刻变成环）。
独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`，
单向且无环，G-38 门禁据此判定「协议方法 ⊆ 两侧实现」。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.db.repositories.memory.records import MemoryRecord, MemorySettingRecord


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


__all__: list[str] = ["MemoryStore"]
