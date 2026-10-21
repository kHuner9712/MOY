"""知识库扩展端点契约（阶段二 KB1-KB8，架构文档 §4.2 / 2.2 第 2 条）。

本文件是**知识库扩展域模型的唯一归属**：KB1-KB3（B04）、KB5-KB7（B05）与 KB8（B03）
的请求/响应模型全部定义在此，`api/v1/knowledge_ext.py` 只做参数装配与响应封装
（KB4 的引用详情模型在 `schemas/segment.py`，因其同时被 M1/M2 引用回链复用）。

设计约束：

1. **加性演进**：所有新字段可选或有默认值，既有字段不删、不改语义；
2. **业务语言**：字段名与描述一律使用业务语言，禁词（切分/处理进度/内容片段数）
   见 PRD 第 7 章；用户可见文案集中在 `STAGE_DISPLAY_NAMES`；
3. **五态与三态同时返回**：`stage` 与 `index_status` 语义必须对齐；
4. **授权三要素**：对象类型（成员 / 部门 / 角色）、对象列表（≤50）、级别（可提问 / 可上传与管理）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from app.schemas.rag import (
    ChunkPolicy,
    DocumentStage,
    IndexStatus,
    Visibility,
)

#: 排序字段白名单（映射到 `document_meta` 列名，值不参与 SQL 拼接）
SORT_FIELD_MAP: Final[dict[str, str]] = {"uploaded_at": "uploaded_at", "file_name": "file_name"}

#: 排序方向白名单
SORT_DIRECTIONS: Final[frozenset[str]] = frozenset({"asc", "desc"})

#: 默认排序
DEFAULT_SORT_FIELD: Final[str] = "uploaded_at"
DEFAULT_SORT_DIRECTION: Final[str] = "desc"


class DocumentSort(BaseModel):
    """文档清单排序参数。

    Attributes:
        field: 排序字段（白名单内）。
        direction: 排序方向（`asc` / `desc`）。
    """

    field: str = Field(default=DEFAULT_SORT_FIELD, description="排序字段")
    direction: str = Field(default=DEFAULT_SORT_DIRECTION, description="排序方向")

    @classmethod
    def parse(cls, raw: str | None) -> DocumentSort:
        """解析 `field:direction` 形式的排序串。

        未知字段/方向一律回落到默认值，避免把用户输入带进排序片段
        （SQL 只接受白名单内常量）。

        Args:
            raw: 形如 `uploaded_at:desc` 的排序串；可为空。

        Returns:
            DocumentSort: 规范化后的排序参数。
        """
        text = (raw or "").strip()
        if not text:
            return cls()
        field, _, direction = text.partition(":")
        normalized_field = field.strip().lower()
        normalized_direction = direction.strip().lower()
        if normalized_field not in SORT_FIELD_MAP:
            normalized_field = DEFAULT_SORT_FIELD
        if normalized_direction not in SORT_DIRECTIONS:
            normalized_direction = DEFAULT_SORT_DIRECTION
        return cls(field=normalized_field, direction=normalized_direction)

    @property
    def column(self) -> str:
        """排序字段对应的列名常量（白名单内）。"""
        return SORT_FIELD_MAP.get(self.field, SORT_FIELD_MAP[DEFAULT_SORT_FIELD])

    @property
    def is_descending(self) -> bool:
        """是否倒序。"""
        return self.direction == "desc"


class UpdateBaseRequest(BaseModel):
    """KB1 更新知识库请求（至少一项）。

    Attributes:
        name: 知识库名称（1-128 字，去掉首尾空白后不得为空）。
        description: 知识库描述（≤1024 字）。
        chunk_policy: 内容切分精细度（仅对之后上传的文档生效）。
        visibility: 可见范围。
    """

    name: str | None = Field(default=None, max_length=128, description="知识库名称")
    description: str | None = Field(default=None, max_length=1024, description="知识库描述")
    chunk_policy: ChunkPolicy | None = Field(default=None, description="内容切分精细度")
    visibility: Visibility | None = Field(default=None, description="可见范围")

    def has_change(self) -> bool:
        """是否至少提供了一项修改。

        Returns:
            bool: 四项全为空返回 False。
        """
        fields = (self.name, self.description, self.chunk_policy, self.visibility)
        return any(value is not None for value in fields)


class UpdateBaseData(BaseModel):
    """KB1 响应数据。

    Attributes:
        base_id: 知识库 ID。
        name: 更新后的名称。
        description: 更新后的描述。
        chunk_policy: 更新后的内容切分精细度。
        visibility: 更新后的可见范围。
        updated_at: 更新时间（ISO 8601）。
    """

    base_id: str = Field(description="知识库 ID")
    name: str = Field(default="", description="知识库名称")
    description: str = Field(default="", description="知识库描述")
    chunk_policy: ChunkPolicy = Field(
        default=ChunkPolicy.DEFAULT, description="内容切分精细度：default 标准 / fine 精细"
    )
    visibility: Visibility = Field(default=Visibility.ORGANIZATION, description="可见范围")
    updated_at: str = Field(default="", description="更新时间，ISO 8601 格式")


class DeleteBaseData(BaseModel):
    """KB2 响应数据。

    Attributes:
        base_id: 知识库 ID。
        deleted: 是否删除成功（恒为 True）。
        deleted_document_count: 随库一并移除的文档数量。
    """

    base_id: str = Field(description="知识库 ID")
    deleted: bool = Field(default=True, description="是否删除成功")
    deleted_document_count: int = Field(default=0, ge=0, description="一并移除的文档数量")


class DocumentItem(BaseModel):
    """KB3 文档清单条目。

    Attributes:
        document_id: 文档 ID。
        file_name: 文档名称。
        file_size: 文档大小（字节）。
        uploader_name: 上传人。
        uploaded_at: 上传时间（ISO 8601）。
        index_status: 文档处理进度（三态，兼容字段）。
        stage: 文档处理阶段（五态）。
        stage_display_name: 阶段的用户可见文案。
        progress: 进度百分比。
        estimated_remaining_seconds: 预估剩余秒数（无法预估时为 null）。
        segment_count: 内容片段数。
        error_message: 处理未成功的原因（业务语言）。
    """

    document_id: str = Field(description="文档 ID")
    file_name: str = Field(default="", description="文档名称")
    file_size: int = Field(default=0, ge=0, description="文档大小（字节）")
    uploader_name: str = Field(default="", description="上传人")
    uploaded_at: str = Field(default="", description="上传时间，ISO 8601 格式")
    index_status: IndexStatus = Field(default=IndexStatus.PROCESSING, description="文档处理进度")
    stage: DocumentStage = Field(default=DocumentStage.UPLOADED, description="文档处理阶段")
    stage_display_name: str = Field(default="已上传", description="阶段的用户可见文案")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")
    estimated_remaining_seconds: int | None = Field(
        default=None, ge=0, description="预估剩余秒数，无法预估时为 null"
    )
    segment_count: int = Field(default=0, ge=0, description="内容片段数")
    error_message: str | None = Field(default=None, description="处理未成功的原因，业务语言")


class ListDocumentsQuery(PageQuery):
    """KB3 文档清单查询参数。

    Attributes:
        keyword: 文档名称关键字（大小写不敏感）。
        stage: 处理阶段过滤（五态之一）。
        sort: 排序串，形如 `uploaded_at:desc`。
    """

    keyword: str = Field(default="", max_length=128, description="文档名称关键字")
    stage: DocumentStage | None = Field(default=None, description="处理阶段过滤")
    sort: str = Field(default="uploaded_at:desc", max_length=64, description="排序，字段:方向")

    @property
    def parsed_sort(self) -> DocumentSort:
        """解析后的排序参数。"""
        return DocumentSort.parse(self.sort)


# ===========================================================================
# KB8 凭据绑定（B03 迁入本文件，路由层保持薄层）
# ===========================================================================
class CredentialBindingRequest(BaseModel):
    """KB8 请求体。

    Attributes:
        credential_ref: 凭据引用，形如 `cred_01K4Q8Z2X7M9B3N5P6R8T0V2W4`。
            明文密钥**永不**出现在本接口的请求/响应中。
    """

    credential_ref: str = Field(
        min_length=4,
        max_length=128,
        description="凭据引用（形如 cred_01K...），由运营侧预先入库",
    )


class CredentialBindingData(BaseModel):
    """KB8 响应数据。

    Attributes:
        base_id: 知识库 ID。
        bound: 是否已完成绑定（固定 True）。
        updated_at: 绑定更新时间（ISO 8601）。
    """

    base_id: str = Field(description="知识库 ID")
    bound: bool = Field(default=True, description="是否已绑定")
    updated_at: str = Field(default="", description="绑定更新时间，ISO 8601")


# ===========================================================================
# KB5-KB7 成员授权（B05）
# ===========================================================================
class GrantSubjectType(StrEnum):
    """授权对象类型枚举（业务语言：成员 / 部门 / 角色）。"""

    USER = "user"
    DEPARTMENT = "department"
    ROLE = "role"


class GrantPermission(StrEnum):
    """授权级别枚举。

    `viewer` = 可提问（仅问答与检索）；`editor` = 可上传与管理（含文档维护）。
    """

    VIEWER = "viewer"
    EDITOR = "editor"


#: 授权对象类型的用户可见名称
SUBJECT_TYPE_DISPLAY_NAMES: Final[dict[GrantSubjectType, str]] = {
    GrantSubjectType.USER: "成员",
    GrantSubjectType.DEPARTMENT: "部门",
    GrantSubjectType.ROLE: "角色",
}

#: 授权级别的用户可见名称
PERMISSION_DISPLAY_NAMES: Final[dict[GrantPermission, str]] = {
    GrantPermission.VIEWER: "可提问",
    GrantPermission.EDITOR: "可上传与管理",
}


class GrantItem(BaseModel):
    """KB5 授权清单条目。

    Attributes:
        grant_id: 授权记录 ID，形如 `grnt_01HF8...`。
        subject_type: 授权对象类型。
        subject_id: 授权对象 ID。
        subject_name: 授权对象展示名（部门名 / 角色名；成员暂以 ID 展示）。
        permission: 授权级别。
        granted_by_name: 授权人展示名。
        granted_at: 授权时间（ISO 8601）。
    """

    grant_id: str = Field(description="授权记录 ID")
    subject_type: GrantSubjectType = Field(
        default=GrantSubjectType.USER, description="授权对象类型：成员 / 部门 / 角色"
    )
    subject_id: str = Field(default="", description="授权对象 ID")
    subject_name: str = Field(default="", description="授权对象展示名")
    permission: GrantPermission = Field(
        default=GrantPermission.VIEWER, description="授权级别：可提问 / 可上传与管理"
    )
    granted_by_name: str = Field(default="", description="授权人展示名")
    granted_at: str = Field(default="", description="授权时间，ISO 8601")


class CreateGrantRequest(BaseModel):
    """KB6 批量授权请求。

    Attributes:
        subject_type: 授权对象类型（成员 / 部门 / 角色）。
        subject_ids: 授权对象 ID 列表（1-50 个，重复对象计入跳过）。
        permission: 授权级别（`viewer` 可提问 / `editor` 可上传与管理）。
    """

    subject_type: GrantSubjectType = Field(description="授权对象类型：成员 / 部门 / 角色")
    subject_ids: list[str] = Field(
        min_length=1, max_length=50, description="授权对象 ID 列表（最多 50 个）"
    )
    permission: GrantPermission = Field(
        default=GrantPermission.VIEWER, description="授权级别：可提问 / 可上传与管理"
    )


class CreateGrantData(BaseModel):
    """KB6 响应数据。

    Attributes:
        created_count: 新建的授权条数。
        skipped_count: 已存在（重复授权）而跳过的条数。
    """

    created_count: int = Field(default=0, ge=0, description="新建的授权条数")
    skipped_count: int = Field(default=0, ge=0, description="重复授权而跳过的条数")


class DeleteGrantData(BaseModel):
    """KB7 响应数据。

    Attributes:
        grant_id: 被撤销的授权记录 ID。
        deleted: 是否撤销成功（恒为 True）。
    """

    grant_id: str = Field(description="被撤销的授权记录 ID")
    deleted: bool = Field(default=True, description="是否撤销成功")


__all__ = [
    "DEFAULT_SORT_DIRECTION",
    "DEFAULT_SORT_FIELD",
    "PERMISSION_DISPLAY_NAMES",
    "SORT_DIRECTIONS",
    "SORT_FIELD_MAP",
    "SUBJECT_TYPE_DISPLAY_NAMES",
    "CreateGrantData",
    "CreateGrantRequest",
    "CredentialBindingData",
    "CredentialBindingRequest",
    "DeleteBaseData",
    "DeleteGrantData",
    "DocumentItem",
    "DocumentSort",
    "GrantItem",
    "GrantPermission",
    "GrantSubjectType",
    "ListDocumentsQuery",
    "UpdateBaseData",
    "UpdateBaseRequest",
]
