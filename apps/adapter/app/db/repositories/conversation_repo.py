"""会话与消息仓储（阶段四 T4-02.2，架构 §3.1 / §3.2 / §4.4）。

**两条边界必须由仓储层承担，不能指望 RLS**：

1. **个人边界**：`conversation.user_id` / `message.user_id` 的隔离不属于 RLS 的职责
   （RLS 只保证租户边界）。本模块所有查询都带 `user_id = :user_id`，且该值只来自
   `AuthContext`——**不接受请求参数**。少写一次就是"张三能看到李四的对话"。
2. **游标语义**：分页用 `seq`（`GENERATED ALWAYS AS IDENTITY`）而不是 offset。
   offset 在"删行/并发写入"下会重复或漏行，而对话列表恰好在流式期间频繁写入。

**软删**：`deleted_at` 非空即不可见（列表/读取默认过滤），但行保留
`conversation_soft_delete_recover_days`（默认 30 天）供恢复；恢复由 T4-05.3 的
保留期任务收口为物理删除。

**双后端**：`InMemoryConversationStore`（development/test）+ `PostgresConversationStore`
（生产）。两者行为必须一致——历史上"内存全绿、生产全废"的缺陷都出在只测了前者。
"""

# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository, as_datetime
from app.db.session import tenant_scope

logger = get_logger(__name__)

CONVERSATION_TABLE: Final[str] = "conversation"
MESSAGE_TABLE: Final[str] = "message"

#: 消息状态机（与 DDL 的 CHECK 一致）
MESSAGE_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "streaming", "settled", "aborted", "failed"}
)

#: 终态（不可再流转；`settled` 也不允许回退到 `streaming`，触发器同样强制）
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"settled", "aborted", "failed"})

#: 标题上限（与 DDL 的 CHECK 一致）
MAX_TITLE_LENGTH: Final[int] = 120

#: 游标里允许出现的键（多余键一律视为非法，避免客户端塞私货）
_CURSOR_KEYS: Final[frozenset[str]] = frozenset({"seq"})


def now_iso() -> str:
    """当前 UTC 时间（ISO 8601，`Z` 结尾）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_conversation_id() -> str:
    """生成会话 ID（`conv_` + ULID）。

    Returns:
        str: 会话 ID。
    """
    return new_resource_id("conv")


def new_message_id() -> str:
    """生成消息 ID（`msg_` + ULID）。

    Returns:
        str: 消息 ID。
    """
    return new_resource_id("msg")


def encode_cursor(seq: int) -> str:
    """把 `seq` 编码成 opaque 游标（`base64url({"seq": n})`）。

    为什么编码而不是直接给 `seq`：游标一旦公开，就会有人按 `seq=1,2,3` 手工翻页
    或做算术假设；编码成不透明串是在告诉调用方「顺序语义由服务端定义」。

    Args:
        seq: 消息序号。

    Returns:
        str: base64url 字符串（无 padding，可直接进 query）。
    """
    payload = json.dumps({"seq": int(seq)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int | None:
    """解析游标；非法游标返回 None（调用方据此报 20001，而不是静默从头开始）。

    Args:
        cursor: 游标字符串。

    Returns:
        int | None: `seq`；非法返回 None。
    """
    text = (cursor or "").strip()
    if not text:
        return None
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) - _CURSOR_KEYS:
        return None
    value = payload.get("seq")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value


@dataclass(slots=True)
class ConversationRecord:
    """会话行。

    Attributes:
        tenant_id: 企业 ID。
        conversation_id: 会话 ID（`conv_` + ULID）。
        user_id: 归属个人。
        title: 标题（≤120 字，默认取首条用户消息前 20 字，决策 A-6）。
        archived: 是否归档。
        dify_conversation_id: 底座会话 ID（**恒 None**，决策 A-7）。
        message_count: 消息条数（含 user + assistant）。
        last_message_at: 最近一条消息时间（列表排序键）。
        created_at: 创建时间。
        updated_at: 更新时间。
        deleted_at: 软删时间（非空即不可见）。
    """

    tenant_id: str = ""
    conversation_id: str = ""
    user_id: str = ""
    title: str = ""
    archived: bool = False
    dify_conversation_id: str | None = None
    message_count: int = 0
    last_message_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    deleted_at: str = ""

    @property
    def deleted(self) -> bool:
        """是否已软删。

        Returns:
            bool: 已软删返回 True。
        """
        return bool(self.deleted_at)


@dataclass(slots=True)
class MessageRecord:
    """消息行（append-only）。

    Attributes:
        tenant_id: 企业 ID。
        message_id: 消息 ID（`msg_` + ULID）。
        conversation_id: 所属会话。
        user_id: 本轮发起人（assistant 行同值）。
        role: `user|assistant|system`。
        content: 正文（脱敏在读取层做）。
        status: `pending|streaming|settled|aborted|failed`。
        error_reason: 业务化失败原因（不含码/堆栈）。
        truncated: 是否因中断而截断。
        profile: 使用的能力档位。
        generation_id: 本次生成 ID（计量幂等键）。
        prompt_units: 输入额度。
        completion_units: 输出额度。
        seq: 单调序号（游标）。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    tenant_id: str = ""
    message_id: str = ""
    conversation_id: str = ""
    user_id: str = ""
    role: str = "user"
    content: str = ""
    status: str = "pending"
    error_reason: str = ""
    truncated: bool = False
    profile: str = ""
    generation_id: str = ""
    prompt_units: int = 0
    completion_units: int = 0
    seq: int = 0
    created_at: str = ""
    updated_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list, repr=False)


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


def _to_conversation(row: Mapping[str, Any]) -> ConversationRecord:
    """把数据库行转成会话。

    Args:
        row: 行映射。

    Returns:
        ConversationRecord: 会话。
    """
    return ConversationRecord(
        tenant_id=str(row.get("tenant_id", "")),
        conversation_id=str(row.get("conversation_id", "")),
        user_id=str(row.get("user_id", "")),
        title=str(row.get("title", "") or ""),
        archived=bool(row.get("archived", False)),
        dify_conversation_id=(
            str(row["dify_conversation_id"]) if row.get("dify_conversation_id") else None
        ),
        message_count=int(row.get("message_count", 0) or 0),
        last_message_at=_timestamp(row.get("last_message_at")),
        created_at=_timestamp(row.get("created_at")),
        updated_at=_timestamp(row.get("updated_at")),
        deleted_at=_timestamp(row.get("deleted_at")),
    )


def _to_message(row: Mapping[str, Any]) -> MessageRecord:
    """把数据库行转成消息。

    Args:
        row: 行映射。

    Returns:
        MessageRecord: 消息。
    """
    return MessageRecord(
        tenant_id=str(row.get("tenant_id", "")),
        message_id=str(row.get("message_id", "")),
        conversation_id=str(row.get("conversation_id", "")),
        user_id=str(row.get("user_id", "") or ""),
        role=str(row.get("role", "user")),
        content=str(row.get("content", "") or ""),
        status=str(row.get("status", "pending")),
        error_reason=str(row.get("error_reason", "") or ""),
        truncated=bool(row.get("truncated", False)),
        profile=str(row.get("profile", "") or ""),
        generation_id=str(row.get("generation_id", "") or ""),
        prompt_units=int(row.get("prompt_units", 0) or 0),
        completion_units=int(row.get("completion_units", 0) or 0),
        seq=int(row.get("seq", 0) or 0),
        created_at=_timestamp(row.get("created_at")),
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


class _ConversationTableRepository(BaseRepository):
    """`conversation` 表仓储。"""

    table = CONVERSATION_TABLE


class _MessageTableRepository(BaseRepository):
    """`message` 表仓储。"""

    table = MESSAGE_TABLE


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


class PostgresConversationStore:
    """PostgreSQL 会话存储。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def create(self, record: ConversationRecord) -> ConversationRecord:
        """新建会话。

        Args:
            record: 会话行。

        Returns:
            ConversationRecord: 落库后的会话。
        """
        conversation_id = record.conversation_id or new_conversation_id()
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _ConversationTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {CONVERSATION_TABLE} (
                    tenant_id, conversation_id, user_id, title, archived,
                    message_count, last_message_at
                ) VALUES (
                    :tenant_id, :conversation_id, :user_id, :title, :archived,
                    :message_count, :last_message_at
                )
                RETURNING *
                """,
                repo.params(
                    conversation_id=conversation_id,
                    user_id=record.user_id,
                    title=(record.title or "")[:MAX_TITLE_LENGTH],
                    archived=record.archived,
                    message_count=record.message_count,
                    last_message_at=as_datetime(record.last_message_at),
                ),
            )
        assert row is not None
        return _to_conversation(row)

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
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ConversationTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT * FROM {CONVERSATION_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND conversation_id = :conversation_id
                   AND user_id = :user_id
                   AND deleted_at IS NULL
                """,
                repo.params(conversation_id=conversation_id, user_id=user_id),
            )
        return _to_conversation(row) if row is not None else None

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
        """分页列出会话。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            page: 页码（从 1 开始）。
            page_size: 每页条数。
            archived: 是否只看归档。
            include_deleted: 是否包含已软删。

        Returns:
            tuple[list[ConversationRecord], int]: 本页行与总数。
        """
        offset = max(0, (page - 1) * page_size)
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ConversationTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {CONVERSATION_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND user_id = :user_id
                   AND archived = :archived
                   AND (:include_deleted OR deleted_at IS NULL)
                 ORDER BY COALESCE(last_message_at, created_at) DESC, conversation_id DESC
                 LIMIT :limit OFFSET :offset
                """,
                repo.params(
                    user_id=user_id,
                    archived=archived,
                    include_deleted=include_deleted,
                    limit=page_size,
                    offset=offset,
                ),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT count(*) FROM {CONVERSATION_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND user_id = :user_id
                   AND archived = :archived
                   AND (:include_deleted OR deleted_at IS NULL)
                """,
                repo.params(user_id=user_id, archived=archived, include_deleted=include_deleted),
            )
        return [_to_conversation(row) for row in rows], int(total or 0)

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
    ) -> ConversationRecord | None:
        """改标题 / 归档（幂等；同值重复调用不改 `updated_at` 之外的东西）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 会话 ID。
            title: 新标题。
            archived: 新归档状态。

        Returns:
            ConversationRecord | None: 更新后的行。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _ConversationTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {CONVERSATION_TABLE}
                   SET title = COALESCE(:title, title),
                       archived = COALESCE(:archived, archived),
                       updated_at = now()
                 WHERE {repo.tenant_clause()}
                   AND conversation_id = :conversation_id
                   AND user_id = :user_id
                   AND deleted_at IS NULL
                RETURNING *
                """,
                repo.params(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=(title[:MAX_TITLE_LENGTH] if title is not None else None),
                    archived=archived,
                ),
            )
        return _to_conversation(row) if row is not None else None

    async def soft_delete(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        """软删（已删的重复调用返回原行，保证幂等）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 会话 ID。

        Returns:
            ConversationRecord | None: 带 `deleted_at` 的行。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _ConversationTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {CONVERSATION_TABLE}
                   SET deleted_at = COALESCE(deleted_at, now()),
                       updated_at = now()
                 WHERE {repo.tenant_clause()}
                   AND conversation_id = :conversation_id
                   AND user_id = :user_id
                RETURNING *
                """,
                repo.params(conversation_id=conversation_id, user_id=user_id),
            )
        return _to_conversation(row) if row is not None else None

    async def bump(self, tenant_id: str, conversation_id: str, *, added: int = 1) -> None:
        """更新计数与最近消息时间。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。
            added: 追加条数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _ConversationTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {CONVERSATION_TABLE}
                   SET message_count = GREATEST(0, message_count + :added),
                       last_message_at = now(),
                       updated_at = now()
                 WHERE {repo.tenant_clause()} AND conversation_id = :conversation_id
                """,
                repo.params(conversation_id=conversation_id, added=added),
            )


class PostgresMessageStore:
    """PostgreSQL 消息存储。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def append(self, record: MessageRecord) -> MessageRecord:
        """追加消息（`seq` 由数据库分配）。

        Args:
            record: 消息行。

        Returns:
            MessageRecord: 落库后的消息（含 `seq`）。
        """
        message_id = record.message_id or new_message_id()
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _MessageTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {MESSAGE_TABLE} (
                    tenant_id, message_id, conversation_id, user_id, role, content,
                    status, error_reason, truncated, profile, generation_id,
                    prompt_units, completion_units
                ) VALUES (
                    :tenant_id, :message_id, :conversation_id, :user_id, :role, :content,
                    :status, :error_reason, :truncated, :profile, :generation_id,
                    :prompt_units, :completion_units
                )
                RETURNING *
                """,
                repo.params(
                    message_id=message_id,
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
                ),
            )
        assert row is not None
        return _to_message(row)

    async def get(self, tenant_id: str, message_id: str) -> MessageRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 企业 ID。
            message_id: 消息 ID。

        Returns:
            MessageRecord | None: 消息。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MessageTableRepository(uow)
            row = await repo.fetch_one(
                f"SELECT * FROM {MESSAGE_TABLE} "
                f"WHERE {repo.tenant_clause()} AND message_id = :message_id",
                repo.params(message_id=message_id),
            )
        return _to_message(row) if row is not None else None

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
            before_seq: 游标（None 从最新开始）。
            limit: 条数上限。

        Returns:
            list[MessageRecord]: 消息列表（`seq` 降序）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MessageTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {MESSAGE_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND conversation_id = :conversation_id
                   -- CAST 必须显式：before_seq 为 None 时 asyncpg 无法推断参数类型
                   -- （AmbiguousParameterError: could not determine data type of parameter）
                   AND (CAST(:before_seq AS BIGINT) IS NULL
                        OR seq < CAST(:before_seq AS BIGINT))
                 ORDER BY seq DESC
                 LIMIT :limit
                """,
                repo.params(conversation_id=conversation_id, before_seq=before_seq, limit=limit),
            )
        return [_to_message(row) for row in rows]

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
        """写入终态。

        **触发器的约束在这里必须被尊重**：如果目标行当前不是 `streaming`，写正文
        会被 `message_guard_content` 拒绝（`content is immutable after finalize`）。
        因此本方法对**非 streaming 行**只允许改状态字段、不碰正文——调用方（服务层）
        负责先把行置为 streaming（插入空壳时直接给 `status='streaming'`）。

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
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _MessageTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {MESSAGE_TABLE}
                   SET content = CASE WHEN status = 'streaming'
                                      THEN :content ELSE content END,
                       status = :status,
                       error_reason = :error_reason,
                       truncated = :truncated,
                       prompt_units = GREATEST(prompt_units, :prompt_units),
                       completion_units = GREATEST(completion_units, :completion_units)
                 WHERE {repo.tenant_clause()} AND message_id = :message_id
                RETURNING *
                """,
                repo.params(
                    message_id=message_id,
                    content=content,
                    status=status,
                    error_reason=error_reason,
                    truncated=truncated,
                    prompt_units=prompt_units,
                    completion_units=completion_units,
                ),
            )
        return _to_message(row) if row is not None else None

    async def list_orphans(self, tenant_id: str, *, older_than: datetime) -> list[MessageRecord]:
        """列出滞留的非终态消息。

        Args:
            tenant_id: 企业 ID。
            older_than: 只返回早于该时间的行。

        Returns:
            list[MessageRecord]: 滞留消息。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MessageTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {MESSAGE_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND status IN ('pending', 'streaming')
                   AND created_at <= :threshold
                 ORDER BY seq
                """,
                repo.params(threshold=older_than.astimezone(UTC)),
            )
        return [_to_message(row) for row in rows]


#: 会话存储单例（按配置选中实现）
_conversation_store: ConversationStore | None = None

#: 消息存储单例
_message_store: MessageStore | None = None


def get_conversation_store(settings: Settings | None = None) -> ConversationStore:
    """取会话存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        ConversationStore: 存储实现。
    """
    global _conversation_store
    conf = settings or get_settings()
    if _conversation_store is None:
        _conversation_store = (
            PostgresConversationStore(conf) if conf.uses_postgres else InMemoryConversationStore()
        )
    return _conversation_store


def get_message_store(settings: Settings | None = None) -> MessageStore:
    """取消息存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        MessageStore: 存储实现。
    """
    global _message_store
    conf = settings or get_settings()
    if _message_store is None:
        _message_store = (
            PostgresMessageStore(conf) if conf.uses_postgres else InMemoryMessageStore()
        )
    return _message_store


def reset_conversation_stores() -> None:
    """重置两个存储单例（测试隔离用）。"""
    global _conversation_store, _message_store
    _conversation_store = None
    _message_store = None


def recoverable_until(deleted_at: str, settings: Settings | None = None) -> str:
    """计算软删的可恢复截止时间（MC3 响应的 `recoverable_until`）。

    Args:
        deleted_at: 软删时间（ISO 8601）。
        settings: 可选配置实例。

    Returns:
        str: ISO 8601 时间戳；`deleted_at` 无法解析时返回空串。
    """
    conf = settings or get_settings()
    stamp = as_datetime(deleted_at)
    if stamp is None:
        return ""
    deadline = stamp + timedelta(days=conf.conversation_soft_delete_recover_days)
    return deadline.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__: list[str] = [
    "CONVERSATION_TABLE",
    "MAX_TITLE_LENGTH",
    "MESSAGE_STATUSES",
    "MESSAGE_TABLE",
    "TERMINAL_STATUSES",
    "ConversationRecord",
    "ConversationStore",
    "InMemoryConversationStore",
    "InMemoryMessageStore",
    "MessageRecord",
    "MessageStore",
    "PostgresConversationStore",
    "PostgresMessageStore",
    "decode_cursor",
    "encode_cursor",
    "get_conversation_store",
    "get_message_store",
    "new_conversation_id",
    "new_message_id",
    "now_iso",
    "recoverable_until",
    "reset_conversation_stores",
]
