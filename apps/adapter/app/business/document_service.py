"""文档元数据业务能力（阶段二 B04/B05，架构文档 5 类图 / PRD P2-KB-02）。

两条硬规则：

1. **五态与三态必须语义一致**：`stage`（给用户看）与 `index_status`（兼容字段）
   成对写入、成对返回；`completed` ⟺ `stage=completed`，`failed` ⟺ `stage=failed`
   （数据库 CHECK 约束兜底，本层不再二次校验）。
2. **清单必须分页**：单库 5000 条文档时第 250 页也必须稳定返回，
   `list_documents()` 一律走存储层 `LIMIT/OFFSET + COUNT(*)`，禁止全量捞取再切片。

B05 追加的两条引用回链联动（架构文档 2.4 第 3 条）：

- **R5 重新处理 → 换批次号**：`mark_reprocessing()` 生成新 `run_id`，旧片段映射行不动
  （处理期间旧引用仍可查看）；
- **新批次成功完成 → 旧引用统一转 stale**：`refresh_progress()` 在 `stage=completed`
  时调用 `SegmentService.mark_stale(keep_run_id=当前批次)`；**失败不置 stale**
  （未成功处理不应让用户丢引用）；
- **R6 删除文档 → 映射转 deleted**：抽屉展示「该引用已不可查看」。

用户可见文案集中在 `STAGE_DISPLAY_NAMES`（已上传 / 正在读取内容 / 正在准备内容 /
可使用 / 处理未成功），**不出现**任何技术术语。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.business.audit_service import (
    ACTION_DOC_DELETE,
    ACTION_DOC_UPLOAD,
    RESOURCE_DOCUMENT,
    AuditService,
    get_audit_recorder,
)
from app.business.knowledge_base_service import KnowledgeBaseService
from app.business.policy_service import normalize_base_id
from app.business.segment_service import SegmentService
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.document_repo import (
    DEFAULT_CHUNK_POLICY,
    DocumentProgressPatch,
    DocumentRecord,
    DocumentStoreProtocol,
    get_document_store,
)
from app.db.repositories.knowledge_base_repo import KnowledgeBaseRecord
from app.schemas.common import PageResult, build_page
from app.schemas.knowledge_ext import DocumentItem, ListDocumentsQuery
from app.schemas.rag import (
    ChunkPolicy,
    DocumentStage,
    IndexStatus,
    IndexStatusData,
    stage_display_name,
)

logger = get_logger(__name__)

#: 文档 ID 前缀（与 `services/rag_service.py` 的 `new_document_id()` 保持一致）
DOCUMENT_ID_PREFIX: Final[str] = "doc_"

#: 处理批次号前缀（R5 重新处理换新批次）
RUN_ID_PREFIX: Final[str] = "run_"

#: 底座七态 → 自研五态（附录 A-3 的细化版，原状态名不外泄）
NATIVE_STAGE_MAP: Final[dict[str, DocumentStage]] = {
    "waiting": DocumentStage.UPLOADED,
    "parsing": DocumentStage.PARSING,
    "cleaning": DocumentStage.PARSING,
    "splitting": DocumentStage.PREPARING,
    "indexing": DocumentStage.PREPARING,
    "completed": DocumentStage.COMPLETED,
    "error": DocumentStage.FAILED,
}

#: 正在处理中的阶段（删除前置校验用）
PROCESSING_STAGES: Final[frozenset[DocumentStage]] = frozenset(
    {DocumentStage.UPLOADED, DocumentStage.PARSING, DocumentStage.PREPARING}
)

#: 预估剩余时间的上限（秒），防止异常进度算出天文数字
MAX_ESTIMATE_SECONDS: Final[int] = 86_400


@dataclass(frozen=True, slots=True)
class StageView:
    """阶段视图（五态 + 三态 + 预估剩余时间）。

    Attributes:
        stage: 五态阶段。
        index_status: 三态进度（与 `stage` 语义一致）。
        progress: 进度百分比（0-100）。
        estimated_remaining_seconds: 预估剩余秒数；无法预估为 None。
    """

    stage: DocumentStage
    index_status: IndexStatus
    progress: int = 0
    estimated_remaining_seconds: int | None = None

    @property
    def display_name(self) -> str:
        """阶段的用户可见文案。"""
        return stage_display_name(self.stage)


def map_stage(
    native_status: str,
    progress: int = 0,
    *,
    elapsed_seconds: float | None = None,
) -> StageView:
    """把底座原生状态（或已落库的五态值）映射为自研阶段视图。

    五态与三态在此**统一推导**，杜绝两处各自算导致语义漂移。

    Args:
        native_status: 底座原生状态（waiting/parsing/cleaning/splitting/indexing/
            completed/error）或已落库的五态值（uploaded/parsing/preparing/...）。
        progress: 已完成百分比（0-100，越界会被裁剪）。
        elapsed_seconds: 已耗时（秒）；缺省时无法预估剩余时间。

    Returns:
        StageView: 阶段视图（五态 + 三态 + 预估剩余秒数）。
    """
    normalized = str(native_status or "").strip().lower()
    stage = NATIVE_STAGE_MAP.get(normalized)
    if stage is None:
        stage = _stage_from_value(normalized)

    clamped = max(min(int(progress), 100), 0)
    if stage is DocumentStage.COMPLETED:
        clamped = 100

    index_status = _index_status_of(stage)
    estimate = _estimate_remaining(stage, clamped, elapsed_seconds)
    return StageView(
        stage=stage,
        index_status=index_status,
        progress=clamped,
        estimated_remaining_seconds=estimate,
    )


def _stage_from_value(value: str) -> DocumentStage:
    """把字符串安全转换为五态枚举。

    Args:
        value: 阶段字符串。

    Returns:
        DocumentStage: 枚举值；未知值按 `uploaded` 处理。
    """
    try:
        return DocumentStage(value)
    except ValueError:
        return DocumentStage.UPLOADED


def _index_status_of(stage: DocumentStage) -> IndexStatus:
    """五态 → 三态（唯一映射，保证两者语义一致）。

    Args:
        stage: 五态阶段。

    Returns:
        IndexStatus: 三态进度。
    """
    if stage is DocumentStage.COMPLETED:
        return IndexStatus.COMPLETED
    if stage is DocumentStage.FAILED:
        return IndexStatus.FAILED
    return IndexStatus.PROCESSING


def _estimate_remaining(
    stage: DocumentStage, progress: int, elapsed_seconds: float | None
) -> int | None:
    """按当前进度与已耗时估算剩余秒数。

    终态、零进度或缺少耗时基线时返回 None（前端只显示「处理中」，不显示数值）。

    Args:
        stage: 五态阶段。
        progress: 进度百分比。
        elapsed_seconds: 已耗时（秒）。

    Returns:
        int | None: 预估剩余秒数；无法预估返回 None。
    """
    if stage in {DocumentStage.COMPLETED, DocumentStage.FAILED}:
        return None
    if progress <= 0 or elapsed_seconds is None or elapsed_seconds <= 0:
        return None
    remaining = (100 - progress) * elapsed_seconds / progress
    return int(min(max(remaining, 0), MAX_ESTIMATE_SECONDS))


def _parse_iso(value: str) -> datetime | None:
    """解析 ISO 8601 时间串。

    Args:
        value: 时间字符串。

    Returns:
        datetime | None: 解析成功返回时间；失败返回 None。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_seconds(record: DocumentRecord) -> float:
    """计算文档已耗时（秒）。

    Args:
        record: 文档记录。

    Returns:
        float: 已耗时；时间不可解析时为 0。
    """
    uploaded = _parse_iso(record.uploaded_at)
    if uploaded is None:
        return 0.0
    return max((datetime.now(UTC) - uploaded).total_seconds(), 0.0)


class DocumentService:
    """文档元数据业务服务。

    Attributes:
        _settings: 配置实例。
        _store: 文档元数据存储（内存 / PostgreSQL）。
        _bases: 知识库服务（校验库存在与可见性）。
        _audit: 操作记录服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: DocumentStoreProtocol | None = None,
        bases: KnowledgeBaseService | None = None,
        audit: AuditService | None = None,
        segments: SegmentService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选文档存储（测试注入）。
            bases: 可选知识库服务（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            segments: 可选片段映射服务（测试注入；用于引用回链状态迁移）。
        """
        self._settings = settings or get_settings()
        self._store: DocumentStoreProtocol = store or get_document_store(self._settings)
        self._bases: KnowledgeBaseService = bases or KnowledgeBaseService(self._settings)
        self._audit: AuditService = audit or AuditService(
            self._settings, recorder=get_audit_recorder(self._settings)
        )
        self._segments: SegmentService = segments or SegmentService(
            self._settings, documents=self._store
        )

    @property
    def store(self) -> DocumentStoreProtocol:
        """文档元数据存储（测试可据此准备数据）。"""
        return self._store

    # ------------------------------------------------------------------
    # R3 登记文档
    # ------------------------------------------------------------------
    async def register_document(
        self,
        *,
        base_id: str,
        file_name: str,
        file_size: int,
        auth: AuthContext,
        chunk_policy: ChunkPolicy | None = None,
        document_id: str | None = None,
        native_status: str = "waiting",
        progress: int = 0,
    ) -> DocumentRecord:
        """登记新上传的文档（库级内容切分精细度缺省时按库级设置处理）。

        Args:
            base_id: 知识库 ID。
            file_name: 文档名称。
            file_size: 文档大小（字节）。
            auth: 鉴权上下文。
            chunk_policy: 单次覆盖的内容切分精细度；缺省取库级设置。
            document_id: 已由协议转换层生成的文档 ID；缺省时本层生成。
            native_status: 底座原生状态（缺省为刚上传）。
            progress: 初始进度。

        Returns:
            DocumentRecord: 已落库的文档记录。

        Raises:
            ApiError: 所属知识库已删除（11001）。
        """
        # ID 归一化后再写：否则「用原生形态上传、用规范形态看清单」会查不到文档
        base_id = normalize_base_id(base_id)
        existing = await self._bases.base_exists_for_delete_check(base_id, auth)
        if existing is not None and existing.is_deleted:
            raise ApiError(
                ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                internal_detail=f"base deleted base_id={base_id}",
            )

        policy = chunk_policy.value if chunk_policy is not None else self._base_policy(existing)
        view = map_stage(native_status, progress)
        identifier = document_id or new_resource_id(DOCUMENT_ID_PREFIX)
        record = DocumentRecord(
            tenant_id=auth.tenant_id,
            document_id=identifier,
            base_id=base_id,
            file_name=file_name,
            file_size=max(int(file_size), 0),
            chunk_policy=policy,
            stage=view.stage.value,
            index_status=view.index_status.value,
            progress=view.progress,
            estimated_remaining_seconds=view.estimated_remaining_seconds,
            uploader_user_id=auth.user_id,
            uploader_name=auth.user_id,
            uploaded_at="",
            run_id=new_resource_id(RUN_ID_PREFIX),
        )
        stored = await self._store.create(record)
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_DOC_UPLOAD,
            resource_type=RESOURCE_DOCUMENT,
            resource_id=identifier,
            change_summary={"base_id": base_id, "file_name": file_name},
        )
        logger.info(
            "document_registered",
            base_id=base_id,
            document_id=identifier,
            chunk_policy=policy,
            stage=stored.stage,
            tenant_id=auth.tenant_id,
        )
        return stored

    def _base_policy(self, base: KnowledgeBaseRecord | None) -> str:
        """取知识库的内容切分精细度缺省值。

        Args:
            base: 知识库记录（None 表示该库不在本地元数据中，按标准档处理）。

        Returns:
            str: `default` 或 `fine`。
        """
        if base is None:
            return DEFAULT_CHUNK_POLICY
        policy = str(base.chunk_policy or DEFAULT_CHUNK_POLICY).strip().lower()
        if policy not in {ChunkPolicy.DEFAULT.value, ChunkPolicy.FINE.value}:
            return DEFAULT_CHUNK_POLICY
        return policy

    # ------------------------------------------------------------------
    # KB3 文档清单
    # ------------------------------------------------------------------
    async def list_documents(
        self, base_id: str, query: ListDocumentsQuery, auth: AuthContext
    ) -> PageResult[DocumentItem]:
        """分页查询知识库下的文档清单。

        Args:
            base_id: 知识库 ID。
            query: 分页 / 关键字 / 阶段 / 排序参数。
            auth: 鉴权上下文。

        Returns:
            PageResult[DocumentItem]: 分页结果。

        Raises:
            ApiError: 库不存在（11001）或不可见（20003）。
        """
        # 与写入侧同一套规范化，保证「一个对象一个 ID」（QA B05-P0-01）
        base_id = normalize_base_id(base_id)
        await self._bases.require_readable(base_id, auth)
        sort = query.parsed_sort
        records, total = await self._store.list(
            auth.tenant_id,
            base_id,
            keyword=query.keyword,
            stage=query.stage.value if query.stage is not None else None,
            sort_field=sort.field,
            sort_direction=sort.direction,
            limit=query.page_size,
            offset=query.offset,
        )
        items = [self.to_item(record) for record in records]
        logger.info(
            "document_list_loaded",
            base_id=base_id,
            page=query.page,
            page_size=query.page_size,
            total=total,
            stage=query.stage.value if query.stage is not None else "",
        )
        return build_page(items=items, total=total, page=query.page, page_size=query.page_size)

    def to_item(self, record: DocumentRecord) -> DocumentItem:
        """把文档记录转换为清单条目。

        Args:
            record: 文档记录。

        Returns:
            DocumentItem: 清单条目。
        """
        stage = _stage_from_value(record.stage)
        estimate = record.estimated_remaining_seconds
        if estimate is None:
            estimate = map_stage(
                record.stage, record.progress, elapsed_seconds=_elapsed_seconds(record)
            ).estimated_remaining_seconds
        return DocumentItem(
            document_id=record.document_id,
            file_name=record.file_name,
            file_size=record.file_size,
            uploader_name=record.uploader_name or record.uploader_user_id,
            uploaded_at=record.uploaded_at,
            index_status=_index_status_of(stage),
            stage=stage,
            stage_display_name=stage_display_name(stage),
            progress=max(min(record.progress, 100), 0),
            estimated_remaining_seconds=estimate,
            segment_count=record.segment_count,
            error_message=record.error_message,
        )

    def to_status_data(self, record: DocumentRecord) -> IndexStatusData:
        """把文档记录转换为 R4 处理进度响应。

        Args:
            record: 文档记录。

        Returns:
            IndexStatusData: 处理进度（含阶段、大小、上传时间）。
        """
        stage = _stage_from_value(record.stage)
        estimate = record.estimated_remaining_seconds
        if estimate is None:
            estimate = map_stage(
                record.stage, record.progress, elapsed_seconds=_elapsed_seconds(record)
            ).estimated_remaining_seconds
        return IndexStatusData(
            document_id=record.document_id,
            index_status=_index_status_of(stage),
            stage=stage,
            progress=max(min(record.progress, 100), 0),
            estimated_remaining_seconds=estimate,
            segment_count=record.segment_count,
            file_size=record.file_size,
            uploaded_at=record.uploaded_at,
            error_message=record.error_message,
        )

    # ------------------------------------------------------------------
    # 进度流转（R4 刷新 / R5 重新处理 / R6 删除前置校验）
    # ------------------------------------------------------------------
    async def refresh_progress(
        self,
        *,
        tenant_id: str,
        document_id: str,
        native_status: str,
        progress: int,
        segment_count: int = 0,
        error_message: str | None = None,
        elapsed_seconds: float | None = None,
    ) -> DocumentRecord | None:
        """按底座最新状态刷新文档阶段（五态与三态成对写入）。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            native_status: 底座原生状态。
            progress: 进度百分比。
            segment_count: 内容片段数。
            error_message: 失败原因（业务语言）。
            elapsed_seconds: 已耗时（用于预估剩余时间）。

        Returns:
            DocumentRecord | None: 更新后的记录；文档不存在返回 None。
        """
        view = map_stage(native_status, progress, elapsed_seconds=elapsed_seconds)
        patch = DocumentProgressPatch(
            stage=view.stage.value,
            index_status=view.index_status.value,
            progress=view.progress,
            estimated_remaining_seconds=view.estimated_remaining_seconds,
            segment_count=segment_count,
            error_message=error_message,
        )
        updated = await self._store.update_progress(tenant_id, document_id, patch)
        if view.stage is DocumentStage.COMPLETED:
            await self._close_previous_runs(tenant_id, document_id, updated)
        return updated

    async def _close_previous_runs(
        self, tenant_id: str, document_id: str, record: DocumentRecord | None
    ) -> None:
        """新批次成功完成：把旧批次的片段映射统一转 stale（架构文档 2.4 第 3 条）。

        失败路径**不**调用本方法：未成功处理不应让用户丢引用。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。
            record: 更新后的文档记录（取当前批次号）。
        """
        current = record or await self._store.get(tenant_id, document_id)
        keep_run_id = current.run_id if current is not None else ""
        try:
            await self._segments.mark_stale(tenant_id, document_id, keep_run_id=keep_run_id)
        except Exception as exc:
            logger.warning(
                "segment_stale_marking_failed",
                document_id=document_id,
                reason=type(exc).__name__,
            )

    async def mark_reprocessing(self, *, tenant_id: str, document_id: str) -> DocumentRecord | None:
        """把文档置回「正在读取内容」，并换新处理批次（R5 重新处理）。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            DocumentRecord | None: 更新后的记录；文档不存在返回 None。
        """
        patch = DocumentProgressPatch(
            stage=DocumentStage.PARSING.value,
            index_status=IndexStatus.PROCESSING.value,
            progress=0,
            estimated_remaining_seconds=None,
            error_message=None,
            run_id=new_resource_id(RUN_ID_PREFIX),
        )
        return await self._store.update_progress(tenant_id, document_id, patch)

    async def require_deletable(self, *, tenant_id: str, document_id: str) -> DocumentRecord | None:
        """删除前置校验：处理中的文档不允许删除。

        Args:
            tenant_id: 租户 ID。
            document_id: 文档 ID。

        Returns:
            DocumentRecord | None: 文档记录；未落库（历史数据）返回 None。

        Raises:
            ApiError: 文档正在处理（11006，文案「文档正在处理，请稍后再删除」）。
        """
        record = await self._store.get(tenant_id, document_id)
        if record is None:
            return None
        if _stage_from_value(record.stage) in PROCESSING_STAGES:
            raise ApiError(
                ErrorCode.DOCUMENT_STATE_CONFLICT,
                message="文档正在处理，请稍后再删除",
                internal_detail=f"document={document_id} stage={record.stage}",
            )
        return record

    async def soft_delete(self, *, tenant_id: str, actor_id: str, document_id: str) -> bool:
        """软删除文档。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID。
            document_id: 文档 ID。

        Returns:
            bool: 删除成功返回 True；未落库或已删除返回 False。
        """
        removed = await self._store.soft_delete(tenant_id, document_id)
        if not removed:
            return False
        try:
            await self._segments.mark_deleted(tenant_id, document_id)
        except Exception as exc:
            logger.warning(
                "segment_delete_marking_failed",
                document_id=document_id,
                reason=type(exc).__name__,
            )
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=ACTION_DOC_DELETE,
            resource_type=RESOURCE_DOCUMENT,
            resource_id=document_id,
        )
        logger.info("document_deleted", document_id=document_id, tenant_id=tenant_id)
        return True


__all__: list[str] = [
    "DOCUMENT_ID_PREFIX",
    "MAX_ESTIMATE_SECONDS",
    "NATIVE_STAGE_MAP",
    "PROCESSING_STAGES",
    "RUN_ID_PREFIX",
    "DocumentService",
    "StageView",
    "map_stage",
]
