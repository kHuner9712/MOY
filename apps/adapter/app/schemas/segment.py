"""引用回链契约（阶段二 B05，KB4 / 架构文档 2.4 第 5 条）。

`status=stale` 是**正常响应**（HTTP 200 + `code=0`），不是错误：
引用来自旧版本内容属于业务事实，前端据此展示「该引用来自旧版本内容，原文已更新」
并提供文档入口；**不得**向用户展示任何错误码。

字段口径（与架构文档 §4.2 KB4 逐字一致，不多不少）：

| 字段 | 说明 |
| --- | --- |
| `segment_id` | 片段稳定标识（`seg_` 前缀），重新处理后保持不变 |
| `content` | 片段正文；已过保留期或文档已删除时为 `null` |
| `context_before` / `context_after` | 前后各一段；无法定位时为 `null` |
| `position` | `{page, paragraph}`；纯文本等无页码信息时两项为 `null` |
| `status` | `valid`（可定位）/ `stale`（旧版本，原文已更新） |
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from app.schemas.rag import SegmentPosition


class SegmentStatus(StrEnum):
    """片段可定位状态（只允许两态，前端按此分支渲染）。"""

    VALID = "valid"
    STALE = "stale"


#: 状态 → 用户可见提示文案（PRD P2-KB-04 第 4 条原文，供前端对齐，不进响应体）
STATUS_DISPLAY_HINTS: Final[dict[SegmentStatus, str]] = {
    SegmentStatus.VALID: "",
    SegmentStatus.STALE: "该引用来自旧版本内容，原文已更新",
}


class SegmentDetailData(BaseModel):
    """KB4 引用详情响应数据。

    Attributes:
        segment_id: 片段稳定标识。
        document_id: 文档 ID。
        document_name: 文档名称。
        base_id: 所属知识库 ID。
        base_name: 所属知识库名称。
        content: 片段正文；不可展示时为 null。
        context_before: 前一段内容；无法定位时为 null。
        context_after: 后一段内容；无法定位时为 null。
        position: 原文位置（页码 / 段落）。
        status: 可定位状态。
    """

    segment_id: str = Field(description="片段稳定标识，形如 seg_9f2c...")
    document_id: str = Field(default="", description="文档 ID")
    document_name: str = Field(default="", description="文档名称")
    base_id: str = Field(default="", description="所属知识库 ID")
    base_name: str = Field(default="", description="所属知识库名称")
    content: str | None = Field(default=None, description="片段正文，不可展示时为 null")
    context_before: str | None = Field(default=None, description="前一段内容")
    context_after: str | None = Field(default=None, description="后一段内容")
    position: SegmentPosition = Field(
        default_factory=SegmentPosition, description="原文位置：页码与段落序号"
    )
    status: SegmentStatus = Field(default=SegmentStatus.VALID, description="可定位状态")


__all__: list[str] = [
    "STATUS_DISPLAY_HINTS",
    "SegmentDetailData",
    "SegmentStatus",
]
