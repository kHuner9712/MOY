"""会话与消息仓储 · records 层（`conversation_repo` 三拆后的数据记录与域常量）。

本文件是 `app/db/repositories/conversation/` 包拆分后的**最底层**：数据记录
（dataclass）、两侧共用的状态/长度常量、ID 生成器、时间归一，以及软删恢复期计算
（`recoverable_until`），**不依赖** protocol / memory / postgres / shared，
因此可被它们任意引用而不成环（依赖方向
`records ← {protocol, shared} ← {memory, postgres} ← __init__`）。

**表名不在这里**：`CONVERSATION_TABLE` / `MESSAGE_TABLE` 只服务 PG 的 SQL，
放 `postgres.py`；**游标编解码也不在这里**（`shared.py`）——它跟记录无关，
是两个实现共用的分页协议。

`recoverable_until()` 留在本层：它只读 `ConversationRecord.deleted_at` 与配置项
`conversation_soft_delete_recover_days`，不碰任何存储，放实现层会让内存/PG 两侧
各写一份"可恢复截止时间"的算式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.ids import new_resource_id
from app.db.repositories.base import as_datetime

#: 消息状态机（与 DDL 的 CHECK 一致）
MESSAGE_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "streaming", "settled", "aborted", "failed"}
)


#: 终态（不可再流转；`settled` 也不允许回退到 `streaming`，触发器同样强制）
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"settled", "aborted", "failed"})


#: 标题上限（与 DDL 的 CHECK 一致）
MAX_TITLE_LENGTH: Final[int] = 120


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
    "MAX_TITLE_LENGTH",
    "MESSAGE_STATUSES",
    "TERMINAL_STATUSES",
    "ConversationRecord",
    "MessageRecord",
    "new_conversation_id",
    "new_message_id",
    "now_iso",
    "recoverable_until",
]
