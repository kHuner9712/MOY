"""会话与消息的接口 schema（阶段四 T4-02.2 / MC1-MC4，架构 §4.2 / §4.4）。

三条契约纪律（与仓库既有 30 端点一致，**不得**为方便而破例）：

1. **信封**：所有端点返回 `ApiResponse[T]`，`data` 用本文件的模型；前端只展示
   `message`，因此这里的字段一律是业务语言，**不出现**档位内部名、模型名、向量等术语。
2. **游标而非 offset**：`MessagePageData.next_cursor` 是 opaque 串（`base64url({"seq":n})`），
   客户端只能原样回传；没有 `next_cursor` 表示已到最早。
3. **不接收 `user_id`**：所有"本人"端点从令牌 `sub` 取身份，schema 里**刻意不存在**
   `user_id` 字段——一旦出现，就等于允许指定"替别人查"。

字段命名与架构 §4.2 的响应示例逐字对齐，避免前端按示例写死后再返工。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import PageResult

#: 标题上限（与 DDL / 仓储常量一致）
MAX_TITLE_LENGTH: int = 120

#: 会话列表每页上限（架构 §4.2：page_size ≤ 100）
MAX_PAGE_SIZE: int = 100

#: 消息每页默认/上限（MC2：limit=30，≤100）
DEFAULT_MESSAGE_LIMIT: int = 30
MAX_MESSAGE_LIMIT: int = 100


class ConversationItem(BaseModel):
    """会话列表项（MC1）。

    Attributes:
        conversation_id: 会话 ID。
        title: 标题（首条用户消息前 20 字，决策 A-6）。
        last_message_at: 最近消息时间；无消息时为空串。
        message_count: 消息条数。
        archived: 是否归档。
    """

    conversation_id: str = Field(default="", description="会话 ID")
    title: str = Field(default="", description="会话标题")
    last_message_at: str = Field(default="", description="最近一条消息时间（ISO 8601，UTC）")
    message_count: int = Field(default=0, ge=0, description="消息条数")
    archived: bool = Field(default=False, description="是否归档")


class ConversationPageData(PageResult[ConversationItem]):
    """会话列表（MC1 的 `data`）。"""


class MessageItem(BaseModel):
    """消息项（MC2）。

    Attributes:
        message_id: 消息 ID。
        role: `user|assistant|system`。
        content: 正文。
        status: `pending|streaming|settled|aborted|failed`。
        created_at: 创建时间。
        truncated: 是否因中断截断。
    """

    message_id: str = Field(default="", description="消息 ID")
    role: str = Field(default="user", description="消息角色")
    content: str = Field(default="", description="消息正文")
    status: str = Field(default="settled", description="消息状态")
    created_at: str = Field(default="", description="创建时间（ISO 8601，UTC）")
    truncated: bool = Field(default=False, description="是否因中断而截断")


class MessagePageData(BaseModel):
    """消息分页（MC2 的 `data`）。

    Attributes:
        items: 本页消息（**时间正序**返回，便于直接渲染）。
        next_cursor: 继续往前翻的游标；为空表示已到最早一条。
    """

    items: list[MessageItem] = Field(default_factory=list, description="本页消息（时间正序）")
    next_cursor: str = Field(default="", description="下一页游标；空表示没有更早的消息")


class ConversationDeleteData(BaseModel):
    """会话删除结果（MC3）。

    Attributes:
        conversation_id: 会话 ID。
        deleted_at: 删除时间。
        recoverable_until: 可恢复截止时间（默认 30 天后，`conversation_soft_delete_recover_days`）。
    """

    conversation_id: str = Field(default="", description="会话 ID")
    deleted_at: str = Field(default="", description="删除时间（ISO 8601，UTC）")
    recoverable_until: str = Field(
        default="", description="可恢复截止时间（ISO 8601，UTC）；超过后将被清理任务物理删除"
    )


class ConversationUpdateRequest(BaseModel):
    """会话更新请求（MC4）。

    两个字段都可选：只改标题、只改归档、或同时改，都合法。
    **不接受**其他字段（`extra="forbid"`——多传一个 `user_id` 会被 20001 拒绝，
    而不是被静默忽略）。

    Attributes:
        title: 新标题（≤120 字）。
        archived: 是否归档。
    """

    model_config = {"extra": "forbid"}

    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH, description="会话标题")
    archived: bool | None = Field(default=None, description="是否归档")


class ConversationUpdateData(BaseModel):
    """会话更新结果（MC4）。

    Attributes:
        conversation_id: 会话 ID。
        title: 当前标题。
        archived: 当前归档状态。
        updated_at: 更新时间。
    """

    conversation_id: str = Field(default="", description="会话 ID")
    title: str = Field(default="", description="会话标题")
    archived: bool = Field(default=False, description="是否归档")
    updated_at: str = Field(default="", description="更新时间（ISO 8601，UTC）")


__all__: list[str] = [
    "DEFAULT_MESSAGE_LIMIT",
    "MAX_MESSAGE_LIMIT",
    "MAX_PAGE_SIZE",
    "MAX_TITLE_LENGTH",
    "ConversationDeleteData",
    "ConversationItem",
    "ConversationPageData",
    "ConversationUpdateData",
    "ConversationUpdateRequest",
    "MessageItem",
    "MessagePageData",
]
