"""RAG 服务接口契约（架构文档 3.2，R1-R7）。

设计原则：仅暴露「知识库-文档」业务模型；
分块、向量化、召回参数（附录 A-3）全部封装在适配层配置与底座内，不对外暴露。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery


class Visibility(StrEnum):
    """知识库可见性枚举。"""

    ORGANIZATION = "organization"
    PRIVATE = "private"


class ChunkPolicy(StrEnum):
    """分块策略枚举（仅暴露策略，不暴露分块参数）。"""

    DEFAULT = "default"
    FINE = "fine"


class IndexStatus(StrEnum):
    """索引进度状态（三态，底座原状态名不外泄，附录 A-3）。"""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CreateBaseRequest(BaseModel):
    """R1 创建知识库请求。

    Attributes:
        name: 知识库名称。
        description: 知识库描述。
        visibility: 可见性。
    """

    name: str = Field(min_length=1, max_length=128, description="知识库名称")
    description: str = Field(default="", max_length=1024, description="知识库描述")
    visibility: Visibility = Field(default=Visibility.ORGANIZATION, description="可见性")


class KnowledgeBaseItem(BaseModel):
    """知识库条目。

    Attributes:
        base_id: 知识库 ID，形如 `kb_01HF8...`。
        name: 名称。
        description: 描述。
        visibility: 可见性。
        document_count: 文档数量。
        created_at: 创建时间（ISO 8601）。
    """

    base_id: str = Field(description="知识库 ID，形如 kb_01HF8...")
    name: str = Field(default="", description="知识库名称")
    description: str = Field(default="", description="知识库描述")
    visibility: Visibility = Field(default=Visibility.ORGANIZATION, description="可见性")
    document_count: int = Field(default=0, ge=0, description="文档数量")
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")


class CreateBaseData(BaseModel):
    """R1 响应数据。

    Attributes:
        base_id: 知识库 ID。
        name: 知识库名称。
        created_at: 创建时间。
    """

    base_id: str = Field(description="知识库 ID，形如 kb_01HF8...")
    name: str = Field(default="", description="知识库名称")
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")


class ListBasesQuery(PageQuery):
    """R2 知识库列表查询参数。

    Attributes:
        keyword: 名称关键字过滤。
    """

    keyword: str = Field(default="", max_length=128, description="名称关键字")


class UploadDocumentData(BaseModel):
    """R3 上传文档响应数据。

    Attributes:
        document_id: 文档 ID，形如 `doc_01HF8...`。
        file_name: 文件名。
        index_status: 索引进度状态。
        progress: 进度百分比（0-100）。
    """

    document_id: str = Field(description="文档 ID，形如 doc_01HF8...")
    file_name: str = Field(default="", description="文件名")
    index_status: IndexStatus = Field(default=IndexStatus.PROCESSING, description="索引进度状态")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")


class IndexStatusData(BaseModel):
    """R4 索引进度查询响应数据。

    Attributes:
        document_id: 文档 ID。
        index_status: 索引状态。
        progress: 进度百分比。
        segment_count: 已完成分块数。
        error_message: 失败原因（业务语言）。
    """

    document_id: str = Field(description="文档 ID")
    index_status: IndexStatus = Field(default=IndexStatus.PROCESSING, description="索引状态")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")
    segment_count: int = Field(default=0, ge=0, description="已完成分块数")
    error_message: str | None = Field(default=None, description="失败原因，业务语言")


class ReindexData(BaseModel):
    """R5 重建索引响应数据。

    Attributes:
        document_id: 文档 ID。
        index_status: 索引状态。
        progress: 进度百分比。
    """

    document_id: str = Field(description="文档 ID")
    index_status: IndexStatus = Field(default=IndexStatus.PROCESSING, description="索引状态")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")


class SearchRequest(BaseModel):
    """R7 独立检索请求。

    Attributes:
        base_id: 知识库 ID。
        query: 查询语句。
        top_k: 返回条数。
        score_threshold: 相关度阈值。
    """

    base_id: str = Field(min_length=1, max_length=128, description="知识库 ID")
    query: str = Field(min_length=1, max_length=2000, description="查询语句")
    top_k: int = Field(default=5, ge=1, le=50, description="返回条数")
    score_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="相关度阈值")


class SegmentPosition(BaseModel):
    """片段位置。

    Attributes:
        page: 页码。
        paragraph: 段落序号。
    """

    page: int | None = Field(default=None, ge=1, description="页码")
    paragraph: int | None = Field(default=None, ge=1, description="段落序号")


class SearchSegment(BaseModel):
    """检索命中片段。

    Attributes:
        document_id: 文档 ID。
        document_name: 文档名称。
        segment_content: 片段内容。
        score: 相关度。
        position: 片段位置。
    """

    document_id: str = Field(default="", description="文档 ID")
    document_name: str = Field(default="", description="文档名称")
    segment_content: str = Field(default="", description="片段内容")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="相关度")
    position: SegmentPosition | None = Field(default=None, description="片段位置")


class SearchData(BaseModel):
    """R7 检索响应数据。

    Attributes:
        segments: 命中片段列表。
    """

    segments: list[SearchSegment] = Field(default_factory=list, description="命中片段列表")
