"""会话与消息仓储 · 协议层（两个协议、四条实现的唯一契约）。

本包有**两个协议**：`ConversationStore`（会话行）与 `MessageStore`（消息行）。
拆包前它们与四个实现同处一个上帝文件，`PostgresMessageStore` 想复用会话侧的任何
helper 都只能"同文件随便拿"；拆开后两条三元组各自独立配对，G-38 会分别核对
「协议方法 ⊆ 两侧实现」与签名形状。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现这两个协议，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖。独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.db.repositories.conversation.records import ConversationRecord, MessageRecord


class ConversationStore(Protocol):
    """会话存储契约（内存 / PostgreSQL 双实现）。"""

    async def create(self, record: ConversationRecord) -> ConversationRecord:
        """新建会话。"""
        ...

    async def get(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        """按（租户 + 本人 + 会话）读取；**不返回已软删的行**。"""
        ...

    async def get_any(self, tenant_id: str, conversation_id: str) -> ConversationRecord | None:
        """按 ID 读取**任意状态**的行（含已软删、含他人）。

        只用于「这个 ID 到底存不存在」的判定：给了一个已存在但不属于本会话的 ID 时，
        必须报 18001 而不是悄悄覆盖（覆盖 = 把别人的会话内容清空）。
        """
        ...

    async def list_page(
        self,
        tenant_id: str,
        user_id: str,
        *,
        page: int,
        page_size: int,
        archived: bool = False,
        include_deleted: bool = False,
    ) -> tuple[list[ConversationRecord], int]:
        """分页列出会话（返回行与总数）。"""
        ...

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
    ) -> ConversationRecord | None:
        """改标题 / 归档；返回更新后的行，不存在返回 None。"""
        ...

    async def soft_delete(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        """软删；返回带 `deleted_at` 的行，不存在返回 None。"""
        ...

    async def bump(self, tenant_id: str, conversation_id: str, *, added: int = 1) -> None:
        """更新计数与最近消息时间（追加消息后调用）。"""
        ...


class MessageStore(Protocol):
    """消息存储契约（内存 / PostgreSQL 双实现）。"""

    async def append(self, record: MessageRecord) -> MessageRecord:
        """追加一条消息并回填 `seq`。"""
        ...

    async def get(self, tenant_id: str, message_id: str) -> MessageRecord | None:
        """按 ID 读取。"""
        ...

    async def count_role(self, tenant_id: str, conversation_id: str, *, role: str) -> int:
        """统计会话内某个角色的消息条数（阶段四 T4-03.3「每 10 轮」触发用）。"""
        ...

    async def list_before(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        before_seq: int | None,
        limit: int,
    ) -> list[MessageRecord]:
        """取 `seq < before_seq` 的更早消息（按 `seq` 降序返回，最多 `limit` 条）。"""
        ...

    async def finalize(
        self,
        tenant_id: str,
        message_id: str,
        *,
        content: str,
        status: str,
        error_reason: str = "",
        truncated: bool = False,
        prompt_units: int = 0,
        completion_units: int = 0,
    ) -> MessageRecord | None:
        """写入终态（幂等：同状态重复调用只更新一次）。"""
        ...

    async def list_orphans(self, tenant_id: str, *, older_than: datetime) -> list[MessageRecord]:
        """列出滞留的非终态消息（启动 reconciler 用）。"""
        ...


__all__: list[str] = ["ConversationStore", "MessageStore"]
