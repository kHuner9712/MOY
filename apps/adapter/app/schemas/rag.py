"""RAG 服务接口契约（架构文档 3.2，R1-R7）。

设计原则：仅暴露「知识库-文档」业务模型；
内容切分精细度只暴露两档业务选项（标准 / 精细），检索参数（附录 A-3）
全部封装在适配层配置与底座内，不对外暴露。

阶段二 B04 扩展（PRD P2-KB-01 / P2-KB-02）：

- `Visibility` 加宽 `restricted`（指定成员可见）；
- `CreateBaseRequest` / `CreateBaseData` 增加库级 `chunk_policy`；
- `KnowledgeBaseItem` 增加 `chunk_policy` / `my_role` / `updated_at`；
- `UploadDocumentData` 增加 `stage`；`IndexStatusData` 增加
  `stage` / `estimated_remaining_seconds` / `file_size` / `uploaded_at`；
- 新增 `DocumentStage` 五态枚举与 `STAGE_DISPLAY_NAMES` 用户可见文案。

五态与三态必须同时返回且语义一致：`index_status=completed` ⟺ `stage=completed`，
`index_status=failed` ⟺ `stage=failed`（由数据库 CHECK 约束兜底）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery


class Visibility(StrEnum):
    """知识库可见范围枚举（阶段二新增 `restricted`，只允许加宽）。"""

    ORGANIZATION = "organization"
    PRIVATE = "private"
    #: 指定成员可见（授权清单由 P2-KB-03 维护）
    RESTRICTED = "restricted"


class ChunkPolicy(StrEnum):
    """内容切分精细度枚举（仅暴露两档策略，不暴露任何处理参数）。

    业务语言：`default` = 标准（处理更快，适合手册与说明文档）；
    `fine` = 精细（条款定位更准，适合制度与合同）。
    """

    DEFAULT = "default"
    FINE = "fine"


class KnowledgeBaseRole(StrEnum):
    """当前用户在该知识库上的角色（P2-KB-01 R2 新增 `my_role`）。"""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class IndexStatus(StrEnum):
    """文档处理进度（三态，底座原状态名不外泄，附录 A-3）。"""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentStage(StrEnum):
    """文档处理阶段（六态，P2-KB-02 / P4-11 ②；与三态 `index_status` 语义对齐）。"""

    UPLOADED = "uploaded"
    PARSING = "parsing"
    PREPARING = "preparing"
    COMPLETED = "completed"
    FAILED = "failed"
    #: 额度挂起（P4-11 ②）：文档**已保存**、字节已落库，但因超额暂不送底座。
    #: **这是展示层派生值**，DB 的 `stage` 列仍然是 `'uploaded'`——`document_meta`
    #: 的 CHECK 约束只允许五态，加值要全表重写且前后端版本必须同步发布。
    #: 派生规则见 `business/document_service.py` 的 mapper：`hold_reason != ''` 时
    #: 覆盖成该值。这样"库里的物理阶段"与"用户看到的阶段"解耦，
    #: 前端 `document-table.tsx` 无需改动即可显示新文案。
    QUOTA_HELD = "quota_held"


#: 阶段 → 用户可见文案（PRD P2-KB-02 第 2 条，禁词已全部替换为业务语言）
STAGE_DISPLAY_NAMES: Final[dict[DocumentStage, str]] = {
    DocumentStage.UPLOADED: "已上传",
    DocumentStage.PARSING: "正在读取内容",
    DocumentStage.PREPARING: "正在准备内容",
    DocumentStage.COMPLETED: "可使用",
    DocumentStage.FAILED: "处理未成功",
    #: **如实折价（PRD 5.3 ②，2026-10-27 主理人裁决 b-1）**：V1.0 不存输入字节
    #: （`document_meta` 无字节列，上传路径是 `content → post_multipart` 一次网络调用），
    #: 因此额度恢复后**无法**自动重放解析。列表这里只如实反映状态，
    #: **可执行出路放在上传响应**（`upload_document` 的 `notice_message`）——
    #: 红线要求"界面上有明确出路"，而动作必须出现在用户当场能看到的位置。
    #: 不要把它改回「额度恢复后自动处理」：那句在 b-2（存输入字节）落地前是假承诺。
    DocumentStage.QUOTA_HELD: "额度已用尽，未处理",
}


def stage_display_name(stage: DocumentStage | str) -> str:
    """取阶段的用户可见文案。

    Args:
        stage: 阶段枚举或其字符串值。

    Returns:
        str: 业务语言文案；未知阶段按「已上传」处理。
    """
    try:
        normalized = DocumentStage(str(stage))
    except ValueError:
        normalized = DocumentStage.UPLOADED
    return STAGE_DISPLAY_NAMES[normalized]


class CreateBaseRequest(BaseModel):
    """R1 创建知识库请求。

    Attributes:
        name: 知识库名称。
        description: 知识库描述。
        visibility: 可见范围。
        chunk_policy: 内容切分精细度（库级默认，缺省 `default`）。
    """

    name: str = Field(min_length=1, max_length=128, description="知识库名称")
    description: str = Field(default="", max_length=1024, description="知识库描述")
    visibility: Visibility = Field(default=Visibility.ORGANIZATION, description="可见范围")
    chunk_policy: ChunkPolicy = Field(
        default=ChunkPolicy.DEFAULT, description="内容切分精细度：default 标准 / fine 精细"
    )


class KnowledgeBaseItem(BaseModel):
    """知识库条目。

    Attributes:
        base_id: 知识库 ID，形如 `kb_01HF8...`。
        name: 名称。
        description: 描述。
        visibility: 可见范围。
        chunk_policy: 内容切分精细度。
        my_role: 当前用户在该库上的角色。
        document_count: 文档数量。
        created_at: 创建时间（ISO 8601）。
        updated_at: 最近更新时间（ISO 8601）。
    """

    base_id: str = Field(description="知识库 ID，形如 kb_01HF8...")
    name: str = Field(default="", description="知识库名称")
    description: str = Field(default="", description="知识库描述")
    visibility: Visibility = Field(default=Visibility.ORGANIZATION, description="可见范围")
    chunk_policy: ChunkPolicy = Field(
        default=ChunkPolicy.DEFAULT, description="内容切分精细度：default 标准 / fine 精细"
    )
    my_role: KnowledgeBaseRole = Field(
        default=KnowledgeBaseRole.VIEWER, description="当前用户在该知识库上的角色"
    )
    document_count: int = Field(default=0, ge=0, description="文档数量")
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")
    updated_at: str = Field(default="", description="最近更新时间，ISO 8601 格式")


class CreateBaseData(BaseModel):
    """R1 响应数据。

    Attributes:
        base_id: 知识库 ID。
        name: 知识库名称。
        chunk_policy: 内容切分精细度。
        created_at: 创建时间。
    """

    base_id: str = Field(description="知识库 ID，形如 kb_01HF8...")
    name: str = Field(default="", description="知识库名称")
    chunk_policy: ChunkPolicy = Field(
        default=ChunkPolicy.DEFAULT, description="内容切分精细度：default 标准 / fine 精细"
    )
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")


class ListBasesQuery(PageQuery):
    """R2 知识库列表查询参数。

    Attributes:
        keyword: 名称关键字过滤。
        base_id: 精确查询某个知识库（**加性**，缺省为空表示按列表返回）。

            存在理由：前端知识库详情页需要「按 ID 定位单个库」。此前的唯一途径是
            拉 R2 列表再在前端按 `base_id` 扫描，库数量超过一页（>200）就失效。
            语义与列表一致：不可见或不存在都返回**空列表**（不 404、不 403），
            避免通过该参数探测他人库是否存在；传了就忽略 `keyword` 与分页。
    """

    keyword: str = Field(default="", max_length=128, description="名称关键字")
    base_id: str = Field(
        default="",
        max_length=128,
        description="精确查询的知识库 ID；留空表示按列表返回（加性参数）",
    )


class UploadDocumentData(BaseModel):
    """R3 上传文档响应数据。

    Attributes:
        document_id: 文档 ID，形如 `doc_01HF8...`。
        file_name: 文件名。
        index_status: 文档处理进度（三态）。
        stage: 文档处理阶段（五态，与三态语义一致）。
        chunk_policy: 本次实际使用的内容切分精细度（未指定时取库级设置，便于核对）。
        progress: 进度百分比（0-100）。
        notice_code: 非阻断提示码（B27 加性字段：额度用尽为 `15006`，否则 0）。
        notice_message: 非阻断提示文案（`notice_code` 为 0 时为空串）。
    """

    document_id: str = Field(description="文档 ID，形如 doc_01HF8...")
    file_name: str = Field(default="", description="文件名")
    index_status: IndexStatus = Field(default=IndexStatus.PROCESSING, description="文档处理进度")
    stage: DocumentStage = Field(default=DocumentStage.UPLOADED, description="文档处理阶段")
    chunk_policy: ChunkPolicy = Field(
        default=ChunkPolicy.DEFAULT, description="内容切分精细度：default 标准 / fine 精细"
    )
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")
    #: **非阻断提示**（PRD 5.3：超额仍接收上传，绝不拦停）：只挂在成功响应的
    #: `data` 里，外层 `code` 恒为 0；放在 `code` 会让"上传成功"变成弹错误框。
    notice_code: int = Field(default=0, ge=0, description="非阻断提示码（额度用尽为 15006）")
    notice_message: str = Field(default="", description="非阻断提示文案（无提示时为空串）")


class IndexStatusData(BaseModel):
    """R4 文档处理进度查询响应数据。

    Attributes:
        document_id: 文档 ID。
        index_status: 文档处理进度（三态）。
        stage: 文档处理阶段（五态）。
        progress: 进度百分比。
        estimated_remaining_seconds: 预估剩余秒数（无法预估时为 null）。
        segment_count: 内容片段数。
        file_size: 文档大小（字节）。
        uploaded_at: 上传时间（ISO 8601）。
        error_message: 失败原因（业务语言）。
    """

    document_id: str = Field(description="文档 ID")
    index_status: IndexStatus = Field(default=IndexStatus.PROCESSING, description="文档处理进度")
    stage: DocumentStage = Field(default=DocumentStage.UPLOADED, description="文档处理阶段")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")
    estimated_remaining_seconds: int | None = Field(
        default=None, ge=0, description="预估剩余秒数，无法预估时为 null"
    )
    segment_count: int = Field(default=0, ge=0, description="内容片段数")
    file_size: int = Field(default=0, ge=0, description="文档大小（字节）")
    uploaded_at: str = Field(default="", description="上传时间，ISO 8601 格式")
    error_message: str | None = Field(default=None, description="失败原因，业务语言")


class ReindexData(BaseModel):
    """R5 重新处理响应数据。

    Attributes:
        document_id: 文档 ID。
        index_status: 文档处理进度（三态）。
        stage: 文档处理阶段（五态）。
        progress: 进度百分比。
    """

    document_id: str = Field(description="文档 ID")
    index_status: IndexStatus = Field(default=IndexStatus.PROCESSING, description="文档处理进度")
    stage: DocumentStage = Field(default=DocumentStage.PARSING, description="文档处理阶段")
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

    B05 加性扩展（PRD P2-KB-04 / 架构文档 §4.7）：新增 `base_id` / `base_name` /
    `segment_id` 三个**可选**字段，用于引用回链；既有字段语义与取值域不变。

    Attributes:
        document_id: 文档 ID。
        document_name: 文档名称。
        segment_content: 片段内容。
        score: 相关度。
        position: 片段位置。
        base_id: 所属知识库 ID（回链用）。
        base_name: 所属知识库名称（回链展示用）。
        segment_id: 片段稳定标识（重新处理后保持不变）。
    """

    document_id: str = Field(default="", description="文档 ID")
    document_name: str = Field(default="", description="文档名称")
    segment_content: str = Field(default="", description="片段内容")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="相关度")
    position: SegmentPosition | None = Field(default=None, description="片段位置")
    base_id: str = Field(default="", description="所属知识库 ID")
    base_name: str = Field(default="", description="所属知识库名称")
    segment_id: str = Field(default="", description="片段稳定标识，形如 seg_9f2c...")


class SearchData(BaseModel):
    """R7 检索响应数据。

    Attributes:
        segments: 命中片段列表。
    """

    segments: list[SearchSegment] = Field(default_factory=list, description="命中片段列表")
