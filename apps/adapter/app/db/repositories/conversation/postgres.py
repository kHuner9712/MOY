# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。


"""会话与消息仓储 · PostgreSQL 实现（生产）。

两张表：`conversation`（软删 + 恢复期）与 `message`（append-only，`seq` 由
`GENERATED ALWAYS AS IDENTITY` 生成，是游标唯一来源）。

**两条边界必须由仓储层承担，不能指望 RLS**：

1. **个人边界**：`conversation.user_id` / `message.user_id` 的隔离不属于 RLS 的职责
   （RLS 只保证租户边界）。本文件所有查询都带 `user_id = :user_id`，且该值只来自
   `AuthContext`——**不接受请求参数**；
2. **游标语义**：分页用 `seq` 而不是 offset。offset 在"删行/并发写入"下会重复或
   漏行，而对话列表恰好在流式期间频繁写入。

本文件还承载**行映射器**（`_to_conversation` / `_to_message`）与时间还原
（`_timestamp`）：它们只服务"从 PG 行还原 record"，内存后端直接拿对象。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.db.repositories.base import BaseRepository, as_datetime
from app.db.repositories.conversation.records import (
    MAX_TITLE_LENGTH,
    ConversationRecord,
    MessageRecord,
    new_conversation_id,
    new_message_id,
)
from app.db.session import tenant_scope

CONVERSATION_TABLE: Final[str] = "conversation"
MESSAGE_TABLE: Final[str] = "message"


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

    async def get_any(self, tenant_id: str, conversation_id: str) -> ConversationRecord | None:
        """按 ID 读取任意状态的行（忽略 `user_id` 与软删）。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。

        Returns:
            ConversationRecord | None: 会话。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ConversationTableRepository(uow)
            row = await repo.fetch_one(
                f"SELECT * FROM {CONVERSATION_TABLE} "
                f"WHERE {repo.tenant_clause()} AND conversation_id = :conversation_id",
                repo.params(conversation_id=conversation_id),
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

    async def count_role(self, tenant_id: str, conversation_id: str, *, role: str) -> int:
        """统计会话内某个角色的消息条数。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。
            role: 角色。

        Returns:
            int: 条数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MessageTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT count(*) FROM {MESSAGE_TABLE}
                 WHERE {repo.tenant_clause()}
                   AND conversation_id = :conversation_id
                   AND role = :role
                """,
                repo.params(conversation_id=conversation_id, role=role),
            )
        return int(total or 0)

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


__all__: list[str] = [
    "CONVERSATION_TABLE",
    "MESSAGE_TABLE",
    "PostgresConversationStore",
    "PostgresMessageStore",
]
