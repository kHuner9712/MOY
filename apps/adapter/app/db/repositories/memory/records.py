"""记忆事实仓储 · records 层（`memory_repo` 三拆后的数据记录与域常量）。

本文件是 `app/db/repositories/memory/` 包拆分后的**最底层**：只有数据记录（dataclass）、
两侧共用的域常量（6 类事实 / 置信度 / 分类顺序）与 ID 生成器，**不依赖**
protocol / memory / postgres，因此可被它们任意引用而不成环
（依赖方向 `records ← protocol ← {memory, postgres} ← __init__`）。

**表名常量不在这里**：`MEMORY_TABLE` / `EMBEDDING_TABLE` / `SETTING_TABLE` 只服务
PG 的 SQL（内存后端不需要表名），放 `postgres.py`；`vector_literal()` /
`_json_switches()` / `_parse_vector_text()` 同理——它们是 pgvector 与 JSONB 的
**持久化表示**，只有 PG 侧用得到。

`new_memory_id()` 留在本层：内存与 PG 两侧的 `insert()` 都要在"调用方没给 ID 时"
补一个 `mem_` + ULID，两侧各写一遍迟早出现前缀/长度不一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from app.core.ids import new_resource_id

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


__all__: list[str] = [
    "CATEGORY_ORDER",
    "CONFIDENCE_LEVELS",
    "MEMORY_CATEGORIES",
    "MemoryRecord",
    "MemorySettingRecord",
    "new_memory_id",
]
