"""记忆（L3）接口 schema（阶段四 T4-03.5 / MM1-MM4，架构 §4.2 / §4.5）。

三条契约纪律：

1. **信封**：返回 `ApiResponse[T]`；`data` 字段一律业务语言，**不出现**
   `user_memory` / `embedding` / `vector` / `召回` 等内部术语（PRD §4.5 词表）；
2. **不接受 `user_id`**：身份只来自令牌 `sub`；schema 里**刻意不存在**该字段，
   出现即等于允许"替别人删记忆"；
3. **`superseded_by` 原样透出**：前端据此把"已被新说法取代"的历史条目标灰，
   但本项目**不新增端点**去看历史值（点击展开由 MM1 列表自带的历史行提供，
   见 V1.1 规划）——字段先到位，避免将来加字段变成破坏性变更。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import PageResult

#: 记忆列表每页默认/上限（MM1）
DEFAULT_MEMORY_PAGE_SIZE: int = 20
MAX_MEMORY_PAGE_SIZE: int = 100

#: 单条记忆正文上限（与抽取器 `MAX_FACT_CHARS` 一致）
MAX_MEMORY_CONTENT_LENGTH: int = 60


class MemoryItem(BaseModel):
    """记忆列表项（MM1）。

    Attributes:
        memory_id: 记忆 ID。
        category: 类别（`identity|org|preference|convention|constraint|project`）。
        content: 事实正文（≤60 字）。
        confidence: `high`（明确陈述）/ `low`（推测，不参与回答）。
        source_conversation_id: 来源会话 ID。
        updated_at: 更新时间。
        superseded_by: 被哪条新说法取代；非空表示这是历史值。
    """

    memory_id: str = Field(default="", description="记忆 ID")
    category: str = Field(default="identity", description="类别")
    content: str = Field(default="", description="正文")
    confidence: str = Field(default="high", description="置信度")
    source_conversation_id: str = Field(default="", description="来源会话")
    updated_at: str = Field(default="", description="更新时间（ISO 8601，UTC）")
    superseded_by: str = Field(default="", description="被哪条新说法取代（空=当前值）")


class MemoryPageData(PageResult[MemoryItem]):
    """记忆列表（MM1 的 `data`）。

    Attributes:
        enabled: 记忆功能的全局开关。与列表**同包**下发：前端才能区分
            "已关闭"与"还没记到东西"两种空态（PRD §6.1 的三态要求）。
        category_switches: 分类开关（只含被显式改过的类别，缺省视为开启）。
            与 `enabled` 同理，**必须同包下发**：否则"我的记忆"页的分类开关
            在首次渲染时只能全显示为开，用户会看到与真实状态不一致的界面。
    """

    enabled: bool = Field(default=True, description="记忆功能是否开启")
    category_switches: dict[str, bool] = Field(
        default_factory=dict, description="分类开关（缺省视为开启）"
    )


class MemoryDeleteData(BaseModel):
    """单条删除结果（MM2）。

    Attributes:
        memory_id: 被删除的记忆 ID。
        deleted: 恒为 True（删除是**硬删**，不存在"标记为已删"）。
    """

    memory_id: str = Field(default="", description="记忆 ID")
    deleted: bool = Field(default=True, description="是否已删除")


class MemoryBatchDeleteData(BaseModel):
    """按类/全删结果（MM3）。

    Attributes:
        deleted_count: 实际删除条数。
        category: 本次删除的类别；空串表示全部。
    """

    deleted_count: int = Field(default=0, ge=0, description="删除条数")
    category: str = Field(default="", description="类别（空表示全部）")


class MemorySettingsRequest(BaseModel):
    """开关更新请求（MM4）。

    两项都可选，但**至少传一项**；`category_switches` 只覆盖传入的键
    （没传的类别保持原样，否则"只关一类"会把其它类一起重置）。

    Attributes:
        enabled: 全局开关。
        category_switches: 分类开关增量。
    """

    model_config = {"extra": "forbid"}

    enabled: bool | None = Field(default=None, description="记忆功能总开关")
    category_switches: dict[str, bool] | None = Field(
        default=None, description="分类开关（只覆盖传入的类别）"
    )


class MemorySettingsData(BaseModel):
    """开关状态（MM4 的 `data`）。

    Attributes:
        enabled: 全局开关。
        category_switches: 分类开关（只包含被显式改过的类别；缺省视为开启）。
        updated_at: 更新时间。
    """

    enabled: bool = Field(default=True, description="记忆功能总开关")
    category_switches: dict[str, bool] = Field(
        default_factory=dict, description="分类开关（缺省视为开启）"
    )
    updated_at: str = Field(default="", description="更新时间（ISO 8601，UTC）")


__all__: list[str] = [
    "DEFAULT_MEMORY_PAGE_SIZE",
    "MAX_MEMORY_CONTENT_LENGTH",
    "MAX_MEMORY_PAGE_SIZE",
    "MemoryBatchDeleteData",
    "MemoryDeleteData",
    "MemoryItem",
    "MemoryPageData",
    "MemorySettingsData",
    "MemorySettingsRequest",
]
