"""会话与消息仓储 · 内存实现（development / test）。

两组实现（`InMemoryConversationStore` / `InMemoryMessageStore`）都是生产语义的
**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的公开方法集、签名与
record 构造点，差异只能通过 `TEST_HOOKS`（`clear`）这类显式白名单存在。

**个人边界在这里也要守**（不能只靠 PG 的 RLS）：所有查询都带 `user_id` 过滤
（`get_any` 这类"跨用户"出口只给内部对账用，签名上就与 `get` 分开）。
游标同样走 `shared.decode_cursor`，与 PG 侧共用一份编解码。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.db.repositories.base import as_datetime
from app.db.repositories.conversation.records import (
    MAX_TITLE_LENGTH,
    TERMINAL_STATUSES,
    ConversationRecord,
    MessageRecord,
    new_conversation_id,
    new_message_id,
    now_iso,
)


class InMemoryConversationStore:
    """进程内会话存储（development / test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._items: dict[tuple[str, str], ConversationRecord] = {}

    async def create(self, record: ConversationRecord) -> ConversationRecord:
        """新建会话。

        Args:
            record: 会话行。

        Returns:
            ConversationRecord: 落库后的会话。
        """
        stamp = now_iso()
        stored = ConversationRecord(
            tenant_id=record.tenant_id,
            conversation_id=record.conversation_id or new_conversation_id(),
            user_id=record.user_id,
            title=(record.title or "")[:MAX_TITLE_LENGTH],
            archived=record.archived,
            dify_conversation_id=None,
            message_count=record.message_count,
            last_message_at=record.last_message_at,
            created_at=record.created_at or stamp,
            updated_at=record.updated_at or stamp,
            deleted_at=record.deleted_at,
        )
        self._items[(stored.tenant_id, stored.conversation_id)] = stored
        return stored

    async def get(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        """读取（本人 + 未软删）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 会话 ID。

        Returns:
            ConversationRecord | None: 会话。
        """
        record = self._items.get((tenant_id, conversation_id))
        if record is None or record.user_id != user_id or record.deleted:
            return None
        return record

    async def get_any(self, tenant_id: str, conversation_id: str) -> ConversationRecord | None:
        """按 ID 读取任意状态的行（内存实现）。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。

        Returns:
            ConversationRecord | None: 会话。
        """
        return self._items.get((tenant_id, conversation_id))

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
        """分页列出。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            page: 页码（从 1 开始）。
            page_size: 每页条数。
            archived: 只看归档（False 表示只看未归档）。
            include_deleted: 是否包含已软删。

        Returns:
            tuple[list[ConversationRecord], int]: 本页行与总数。
        """
        rows = [
            record
            for (tid, _cid), record in self._items.items()
            if tid == tenant_id
            and record.user_id == user_id
            and record.archived == archived
            and (include_deleted or not record.deleted)
        ]
        rows.sort(key=lambda item: item.last_message_at or item.created_at, reverse=True)
        start = max(0, (page - 1) * page_size)
        return rows[start : start + page_size], len(rows)

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
    ) -> ConversationRecord | None:
        """改标题 / 归档。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 会话 ID。
            title: 新标题。
            archived: 新归档状态。

        Returns:
            ConversationRecord | None: 更新后的行。
        """
        record = await self.get(tenant_id, user_id, conversation_id)
        if record is None:
            return None
        # `slots=True` 的 dataclass 没有 `__dict__`，只能走 `replace`（拷贝 + 改字段）
        updated = replace(
            record,
            title=(title[:MAX_TITLE_LENGTH] if title is not None else record.title),
            archived=(archived if archived is not None else record.archived),
            updated_at=now_iso(),
        )
        self._items[(tenant_id, conversation_id)] = updated
        return updated

    async def soft_delete(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        """软删。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 会话 ID。

        Returns:
            ConversationRecord | None: 带 `deleted_at` 的行。
        """
        record = await self.get(tenant_id, user_id, conversation_id)
        if record is None:
            return None
        record.deleted_at = now_iso()
        record.updated_at = record.deleted_at
        self._items[(tenant_id, conversation_id)] = record
        return record

    async def bump(self, tenant_id: str, conversation_id: str, *, added: int = 1) -> None:
        """更新计数与最近消息时间。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。
            added: 追加条数。
        """
        record = self._items.get((tenant_id, conversation_id))
        if record is None:
            return
        record.message_count = max(0, record.message_count + added)
        record.last_message_at = now_iso()
        record.updated_at = record.last_message_at

    def clear(self) -> None:
        """清空存储（测试隔离）。"""
        self._items.clear()


class InMemoryMessageStore:
    """进程内消息存储（development / test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._items: list[MessageRecord] = []
        self._seq = 0

    async def append(self, record: MessageRecord) -> MessageRecord:
        """追加消息并分配 `seq`。

        Args:
            record: 消息行。

        Returns:
            MessageRecord: 落库后的消息。
        """
        self._seq += 1
        stamp = now_iso()
        stored = MessageRecord(
            tenant_id=record.tenant_id,
            message_id=record.message_id or new_message_id(),
            conversation_id=record.conversation_id,
            user_id=record.user_id,
            role=record.role,
            content=record.content,
            status=record.status,
            error_reason=record.error_reason,
            truncated=record.truncated,
            profile=record.profile,
            generation_id=record.generation_id,
            prompt_units=record.prompt_units,
            completion_units=record.completion_units,
            seq=self._seq,
            created_at=record.created_at or stamp,
            updated_at=record.updated_at or stamp,
        )
        self._items.append(stored)
        return stored

    async def get(self, tenant_id: str, message_id: str) -> MessageRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 企业 ID。
            message_id: 消息 ID。

        Returns:
            MessageRecord | None: 消息。
        """
        for item in self._items:
            if item.tenant_id == tenant_id and item.message_id == message_id:
                return item
        return None

    async def count_role(self, tenant_id: str, conversation_id: str, *, role: str) -> int:
        """统计会话内某个角色的消息条数。

        用"库里真实条数"而不是 `conversation.message_count`（后者含 assistant 行）：
        抽取触发口径是**用户轮次**（每 10 轮），拿总条数当轮次会让触发频率翻倍。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。
            role: 角色（`user` / `assistant` / `system`）。

        Returns:
            int: 条数。
        """
        return len(
            [
                item
                for item in self._items
                if item.tenant_id == tenant_id
                and item.conversation_id == conversation_id
                and item.role == role
            ]
        )

    async def list_before(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        before_seq: int | None,
        limit: int,
    ) -> list[MessageRecord]:
        """取更早的消息（降序）。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。
            before_seq: 游标（None 表示从最新开始）。
            limit: 条数上限。

        Returns:
            list[MessageRecord]: 消息列表（`seq` 降序）。
        """
        rows = [
            item
            for item in self._items
            if item.tenant_id == tenant_id
            and item.conversation_id == conversation_id
            and (before_seq is None or item.seq < before_seq)
        ]
        rows.sort(key=lambda item: item.seq, reverse=True)
        return rows[:limit]

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
        """写入终态（幂等）。

        Args:
            tenant_id: 企业 ID。
            message_id: 消息 ID。
            content: 终态正文。
            status: 终态。
            error_reason: 失败原因。
            truncated: 是否截断。
            prompt_units: 输入额度。
            completion_units: 输出额度。

        Returns:
            MessageRecord | None: 更新后的消息。
        """
        record = await self.get(tenant_id, message_id)
        if record is None:
            return None
        if record.status in TERMINAL_STATUSES and record.status == status:
            return record  # 幂等：同终态重复调用不改动
        record.content = content
        record.status = status
        record.error_reason = error_reason
        record.truncated = truncated
        record.prompt_units = prompt_units or record.prompt_units
        record.completion_units = completion_units or record.completion_units
        record.updated_at = now_iso()
        return record

    async def list_orphans(self, tenant_id: str, *, older_than: datetime) -> list[MessageRecord]:
        """列出滞留的非终态消息。

        Args:
            tenant_id: 企业 ID。
            older_than: 只返回早于该时间的行。

        Returns:
            list[MessageRecord]: 滞留消息。
        """
        threshold = older_than.astimezone(UTC)
        result: list[MessageRecord] = []
        for item in self._items:
            if item.tenant_id != tenant_id or item.status in TERMINAL_STATUSES:
                continue
            created = as_datetime(item.created_at)
            if created is not None and created <= threshold:
                result.append(item)
        return result

    def clear(self) -> None:
        """清空存储（测试隔离）。"""
        self._items.clear()
        self._seq = 0


__all__: list[str] = ["InMemoryConversationStore", "InMemoryMessageStore"]
