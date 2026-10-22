"""模型服务接口契约（架构文档 3.1，M1-M4）。

设计原则：对上层只暴露「能力档位」`model_profile`，不暴露真实模型名（附录 A-1）；
用量字段业务化（`units` 而非 token，附录 A-7）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.rag import SegmentPosition


class ModelProfile(StrEnum):
    """能力档位枚举（契约枚举，新增模型不新增枚举值）。"""

    GENERAL = "general"
    CODE = "code"
    LONG_CONTEXT = "long_context"
    REASONING = "reasoning"


class MessageRole(StrEnum):
    """消息角色枚举。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class FinishReason(StrEnum):
    """生成结束原因枚举。"""

    STOP = "stop"
    LENGTH = "length"
    ERROR = "error"
    CANCELLED = "cancelled"


class ProfileStatus(StrEnum):
    """能力档位状态枚举。"""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ChatMessage(BaseModel):
    """对话消息。

    Attributes:
        role: 消息角色。
        content: 消息正文（纯文本或附件引用描述）。
        attachments: 附件引用 ID 列表。
    """

    role: MessageRole = Field(description="消息角色")
    content: str = Field(default="", description="消息正文")
    attachments: list[str] = Field(default_factory=list, description="附件引用 ID 列表")


class GenerationContext(BaseModel):
    """生成上下文配置。

    Attributes:
        history_window: 携带的历史轮数，默认 20。
        memory_hints: 上层记忆注入（P0-02，边界见 Q6）。
    """

    history_window: int = Field(default=20, ge=0, le=200, description="携带的历史轮数")
    memory_hints: list[str] = Field(default_factory=list, description="上层记忆注入")


class GenerationOptions(BaseModel):
    """生成参数配置。

    Attributes:
        temperature: 采样温度，缺省按档位默认策略。
        max_tokens: 最大生成长度，缺省按档位默认策略。
    """

    temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="采样温度")
    max_tokens: int | None = Field(default=None, ge=1, le=32_768, description="最大生成长度")


class GenerateRequest(BaseModel):
    """M1/M2 生成请求。

    Attributes:
        conversation_id: 会话 ID（上层维护，用于上下文关联与日志追踪）。
        profile: 能力档位。
        messages: 当前轮消息。
        context: 上下文配置。
        knowledge_base_ids: 关联知识库，触发 RAG 增强。
        options: 生成参数。
    """

    conversation_id: str = Field(
        min_length=1, max_length=128, description="会话 ID，形如 conv_01HF8..."
    )
    profile: ModelProfile = Field(description="能力档位")
    messages: list[ChatMessage] = Field(min_length=1, description="当前轮消息")
    context: GenerationContext = Field(default_factory=GenerationContext, description="上下文配置")
    knowledge_base_ids: list[str] = Field(default_factory=list, description="关联知识库 ID 列表")
    options: GenerationOptions = Field(default_factory=GenerationOptions, description="生成参数")


class Usage(BaseModel):
    """业务化用量（字段名 units，附录 A-7）。

    Attributes:
        prompt_units: 输入用量。
        completion_units: 输出用量。
        total_units: 总用量。
    """

    prompt_units: int = Field(default=0, ge=0, description="输入用量")
    completion_units: int = Field(default=0, ge=0, description="输出用量")
    total_units: int = Field(default=0, ge=0, description="总用量")


class Citation(BaseModel):
    """引用片段（用于前端引用展示，FLOW-02）。

    B05 加性扩展（PRD P2-KB-04 / 架构文档 §4.7）：新增 `base_id` / `base_name` /
    `segment_id` / `position` 四个**可选**字段，使引用条可点击回链到原文那一段；
    既有字段 `document_name` / `segment_content` / `score` 保留不变。

    Attributes:
        document_id: 文档 ID。
        document_name: 文档名称。
        segment_content: 命中的片段内容。
        score: 命中相关度。
        base_id: 所属知识库 ID。
        base_name: 所属知识库名称。
        segment_id: 片段稳定标识（重新处理后保持不变）。
        position: 原文位置（页码 / 段落）。
    """

    document_id: str = Field(default="", description="文档 ID")
    document_name: str = Field(default="", description="文档名称")
    segment_content: str = Field(default="", description="命中的片段内容")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="命中相关度")
    base_id: str = Field(default="", description="所属知识库 ID")
    base_name: str = Field(default="", description="所属知识库名称")
    segment_id: str = Field(default="", description="片段稳定标识，形如 seg_9f2c...")
    position: SegmentPosition | None = Field(default=None, description="原文位置：页码与段落序号")


class GenerateData(BaseModel):
    """M1 非流式生成响应数据。

    Attributes:
        generation_id: 生成记录 ID。
        content: 生成内容。
        finish_reason: 结束原因。
        usage: 业务化用量。
        citations: 引用片段列表。
        notice_code: 非阻断提示码（B27 加性字段：额度用尽为 `15006`，否则 0）。
        notice_message: 非阻断提示文案（`notice_code` 为 0 时为空串）。
    """

    generation_id: str = Field(description="生成记录 ID，形如 gen_01HF8...")
    content: str = Field(default="", description="生成内容")
    finish_reason: FinishReason = Field(default=FinishReason.STOP, description="结束原因")
    usage: Usage = Field(default_factory=Usage, description="业务化用量")
    citations: list[Citation] = Field(default_factory=list, description="引用片段列表")
    #: **非阻断提示**（PRD 5.3 软限制）：只挂在成功响应里，外层 `code` 恒为 0。
    #: 放在 `data` 而非 `code` 是硬约束——前端 `unwrap()` 把非 0 `code` 当异常抛，
    #: 提示走 `code` 就会变成弹错误框，等于把"提醒"做成"停服"。
    notice_code: int = Field(default=0, ge=0, description="非阻断提示码（额度用尽为 15006）")
    notice_message: str = Field(default="", description="非阻断提示文案（无提示时为空串）")


class ProfileItem(BaseModel):
    """能力档位条目。

    Attributes:
        profile: 档位枚举值。
        display_name: 业务化展示名。
        description: 业务化描述。
        max_context_turns: 最大上下文轮数。
        status: 档位状态。
    """

    profile: ModelProfile = Field(description="档位枚举值")
    display_name: str = Field(default="", description="业务化展示名")
    description: str = Field(default="", description="业务化描述")
    max_context_turns: int = Field(default=20, ge=0, description="最大上下文轮数")
    status: ProfileStatus = Field(default=ProfileStatus.AVAILABLE, description="档位状态")


class CatalogData(BaseModel):
    """M3 能力档位目录响应数据。

    Attributes:
        profiles: 档位列表（由适配层配置驱动，接口契约不变）。
    """

    profiles: list[ProfileItem] = Field(default_factory=list, description="能力档位列表")


class Quota(BaseModel):
    """套餐额度（业务化口径）。

    Attributes:
        plan_name: 套餐名称。
        total_units: 总额度。
        used_units: 已用额度。
        remaining_units: 剩余额度。
    """

    plan_name: str = Field(default="", description="套餐名称")
    total_units: int = Field(default=0, ge=0, description="总额度")
    used_units: int = Field(default=0, ge=0, description="已用额度")
    remaining_units: int = Field(default=0, ge=0, description="剩余额度")


class UsageData(BaseModel):
    """M4 用量统计响应数据。

    B07 加性扩展：新增 `quota_unit`（额度单位文案，如「字数额度」），
    使前端不必硬编码单位；既有字段与 `quota` 子结构保持不变。

    Attributes:
        period: 统计周期，形如 `2026-11`。
        conversation_count: 对话次数。
        document_qa_count: 文档问答次数。
        quota: 套餐额度。
        quota_unit: 额度单位文案。
    """

    period: str = Field(default="", description="统计周期，形如 2026-11")
    conversation_count: int = Field(default=0, ge=0, description="对话次数")
    document_qa_count: int = Field(default=0, ge=0, description="文档问答次数")
    quota: Quota = Field(default_factory=Quota, description="套餐额度")
    quota_unit: str = Field(default="", description="额度单位文案")


#: 自研 SSE 事件类型（不复用底座原生 event 名称，附录 A-4/A-6）
StreamEventName = Literal["start", "delta", "citation", "done", "error"]


class StreamStartData(BaseModel):
    """`start` 事件载荷。

    Attributes:
        generation_id: 生成记录 ID。
    """

    generation_id: str = Field(description="生成记录 ID")


class StreamDeltaData(BaseModel):
    """`delta` 事件载荷。

    Attributes:
        text: 增量文本。
    """

    text: str = Field(default="", description="增量文本")


class StreamCitationData(BaseModel):
    """`citation` 事件载荷（随答案位置穿插下发）。

    B05 加性扩展：新增 `base_id` / `base_name` / `segment_id` / `document_id` /
    `position`，使流式引用条**到达即可点击**（PRD P2-KB-04 验收 5）。

    Attributes:
        document_name: 文档名称。
        segment_content: 片段内容。
        document_id: 文档 ID。
        base_id: 所属知识库 ID。
        base_name: 所属知识库名称。
        segment_id: 片段稳定标识。
        position: 原文位置（页码 / 段落）。
    """

    document_name: str = Field(default="", description="文档名称")
    segment_content: str = Field(default="", description="片段内容")
    document_id: str = Field(default="", description="文档 ID")
    base_id: str = Field(default="", description="所属知识库 ID")
    base_name: str = Field(default="", description="所属知识库名称")
    segment_id: str = Field(default="", description="片段稳定标识，形如 seg_9f2c...")
    position: SegmentPosition | None = Field(default=None, description="原文位置：页码与段落序号")


class StreamDoneData(BaseModel):
    """`done` 事件载荷。

    Attributes:
        finish_reason: 结束原因。
        usage: 业务化用量。
        notice_code: 非阻断提示码（额度用尽为 `15006`，否则 0）。
        notice_message: 非阻断提示文案（`notice_code` 为 0 时为空串）。
    """

    finish_reason: FinishReason = Field(default=FinishReason.STOP, description="结束原因")
    usage: Usage = Field(default_factory=Usage, description="业务化用量")
    #: **非阻断提示挂在终帧**：流式期间不得插帧（会打断前端渲染与滚动），
    #: 提示只在 `done` 帧一次性下发；`code` 语义在 SSE 里由帧类型承担，仍恒为 0。
    notice_code: int = Field(default=0, ge=0, description="非阻断提示码（额度用尽为 15006）")
    notice_message: str = Field(default="", description="非阻断提示文案（无提示时为空串）")
