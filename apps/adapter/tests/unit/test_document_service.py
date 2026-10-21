"""文档元数据业务能力单元测试（阶段二 B04，PRD P2-KB-02）。

覆盖点：

1. `map_stage` 把底座七态映射为五态，且三态与五态**语义一致**；
2. 文档登记继承库级内容切分精细度（上传不带该字段时按库级处理）；
3. 清单分页：5000 条文档下第 250 页返回正确（禁止全量返回）；
4. 关键字筛选大小写不敏感、阶段筛选、排序双向；
5. 处理中的文档不允许删除 → `11006`（文案「文档正在处理，请稍后再删除」）；
6. 重新处理后阶段回到「正在读取内容」。

全部在 `OA_BUSINESS_STORE=memory` 下运行，**不依赖 PostgreSQL**。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.business.audit_service import ACTION_DOC_UPLOAD, AuditService, InMemoryAuditRecorder
from app.business.document_service import DocumentService, StageView, map_stage
from app.business.knowledge_base_service import KnowledgeBaseService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.document_repo import DocumentRecord, InMemoryDocumentStore
from app.db.repositories.knowledge_base_repo import InMemoryKnowledgeBaseStore
from app.schemas.knowledge_ext import ListDocumentsQuery
from app.schemas.rag import ChunkPolicy, DocumentStage, IndexStatus

pytestmark = pytest.mark.unit

TENANT = "tnt_doc"
UPLOADER = "usr_uploader"
OTHER = "usr_other"

#: 5000 条文档的翻页基准时间
_BASE_TIME = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)


def _settings() -> Settings:
    """内存后端配置。"""
    return Settings(env="test", deploy_mode="private", business_store="memory")


def _auth(user_id: str = UPLOADER, roles: list[str] | None = None) -> AuthContext:
    """构造鉴权上下文。"""
    return AuthContext(user_id=user_id, tenant_id=TENANT, roles=roles or ["member"])


class _Bundle:
    """服务与内存存储的组合。"""

    def __init__(self) -> None:
        """初始化。"""
        self.settings = _settings()
        self.documents = InMemoryDocumentStore()
        self.bases_store = InMemoryKnowledgeBaseStore()
        self.audit_recorder = InMemoryAuditRecorder()
        self.audit = AuditService(self.settings, recorder=self.audit_recorder)
        self.bases = KnowledgeBaseService(
            self.settings,
            store=self.bases_store,
            documents=self.documents,
            audit=self.audit,
        )
        self.service = DocumentService(
            self.settings, store=self.documents, bases=self.bases, audit=self.audit
        )


@pytest.fixture
def bundle() -> _Bundle:
    """全新的服务组合。"""
    return _Bundle()


async def _seed_base(bundle: _Bundle, *, chunk_policy: ChunkPolicy = ChunkPolicy.DEFAULT) -> str:
    """造一个知识库并返回 ID。"""
    from app.schemas.rag import CreateBaseRequest

    data = await bundle.bases.create(
        CreateBaseRequest(name="制度库", chunk_policy=chunk_policy), _auth()
    )
    return data.base_id


# ===========================================================================
# 1. map_stage：七态 → 五态，三态与五态语义一致
# ===========================================================================
@pytest.mark.parametrize(
    ("native", "expected_stage"),
    [
        ("waiting", DocumentStage.UPLOADED),
        ("parsing", DocumentStage.PARSING),
        ("cleaning", DocumentStage.PARSING),
        ("splitting", DocumentStage.PREPARING),
        ("indexing", DocumentStage.PREPARING),
        ("completed", DocumentStage.COMPLETED),
        ("error", DocumentStage.FAILED),
    ],
)
def test_map_stage_collapses_native_states(native: str, expected_stage: DocumentStage) -> None:
    """底座七态映射为五态（原状态名不外泄）。"""
    assert map_stage(native).stage == expected_stage


def test_map_stage_unknown_state_defaults_to_uploaded() -> None:
    """未知状态按「已上传」处理，不抛异常。"""
    assert map_stage("some_future_state").stage == DocumentStage.UPLOADED


def test_map_stage_accepts_persisted_stage_value() -> None:
    """已落库的五态值可直接回读（刷新进度场景复用同一函数）。"""
    assert map_stage("preparing").stage == DocumentStage.PREPARING


@pytest.mark.parametrize(
    ("native", "expected_status"),
    [
        ("waiting", IndexStatus.PROCESSING),
        ("parsing", IndexStatus.PROCESSING),
        ("indexing", IndexStatus.PROCESSING),
        ("completed", IndexStatus.COMPLETED),
        ("error", IndexStatus.FAILED),
    ],
)
def test_map_stage_keeps_three_state_consistent(native: str, expected_status: IndexStatus) -> None:
    """五态与三态语义一致：`completed` ⟺ 阶段完成，`failed` ⟺ 阶段失败。"""
    view = map_stage(native)
    assert view.index_status == expected_status
    assert (view.index_status is IndexStatus.COMPLETED) == (view.stage is DocumentStage.COMPLETED)
    assert (view.index_status is IndexStatus.FAILED) == (view.stage is DocumentStage.FAILED)


def test_map_stage_completed_forces_full_progress() -> None:
    """完成态进度恒为 100。"""
    assert map_stage("completed", 42).progress == 100


def test_map_stage_clamps_progress() -> None:
    """进度越界被裁剪到 0-100。"""
    assert map_stage("parsing", 150).progress == 100
    assert map_stage("parsing", -5).progress == 0


def test_map_stage_estimate_is_none_without_baseline() -> None:
    """缺少耗时基线时无法预估（前端只显示「处理中」）。"""
    assert map_stage("parsing", 50).estimated_remaining_seconds is None


def test_map_stage_estimate_computed_from_elapsed() -> None:
    """进度 50% 且已耗时 60 秒 → 预估剩余 60 秒。"""
    view = map_stage("parsing", 50, elapsed_seconds=60.0)
    assert view.estimated_remaining_seconds == 60


def test_map_stage_estimate_is_none_for_terminal_states() -> None:
    """终态不预估剩余时间。"""
    assert map_stage("completed", 100, elapsed_seconds=10.0).estimated_remaining_seconds is None
    assert map_stage("error", 30, elapsed_seconds=10.0).estimated_remaining_seconds is None


def test_stage_view_display_name_is_business_language() -> None:
    """阶段文案使用业务语言（无技术术语）。"""
    view: StageView = map_stage("indexing", 20)
    assert view.display_name == "正在准备内容"
    assert map_stage("completed").display_name == "可使用"
    assert map_stage("error").display_name == "处理未成功"


# ===========================================================================
# 2. 文档登记：库级内容切分精细度继承
# ===========================================================================
async def test_register_inherits_base_chunk_policy(bundle: _Bundle) -> None:
    """上传不带精细度时按库级设置处理（PRD 验收 1）。"""
    base_id = await _seed_base(bundle, chunk_policy=ChunkPolicy.FINE)

    record = await bundle.service.register_document(
        base_id=base_id, file_name="员工手册.pdf", file_size=2048, auth=_auth()
    )
    assert record.chunk_policy == "fine"


async def test_register_single_upload_override(bundle: _Bundle) -> None:
    """单次覆盖不改变库级设置。"""
    base_id = await _seed_base(bundle, chunk_policy=ChunkPolicy.FINE)

    record = await bundle.service.register_document(
        base_id=base_id,
        file_name="临时.txt",
        file_size=128,
        auth=_auth(),
        chunk_policy=ChunkPolicy.DEFAULT,
    )
    assert record.chunk_policy == "default"
    base = await bundle.bases_store.get(TENANT, base_id)
    assert base is not None and base.chunk_policy == "fine"


async def test_register_defaults_when_base_unknown(bundle: _Bundle) -> None:
    """库不在本地元数据中时按标准档处理（历史库兼容）。"""
    record = await bundle.service.register_document(
        base_id="kb_unknown", file_name="手册.pdf", file_size=1024, auth=_auth()
    )
    assert record.chunk_policy == "default"


async def test_register_initial_stage_is_uploaded(bundle: _Bundle) -> None:
    """新文档初始阶段为「已上传」，三态为处理中。"""
    record = await bundle.service.register_document(
        base_id="kb_1", file_name="手册.pdf", file_size=1024, auth=_auth()
    )
    assert record.stage == DocumentStage.UPLOADED.value
    assert record.index_status == IndexStatus.PROCESSING.value
    assert record.uploaded_at
    assert record.uploader_user_id == UPLOADER


async def test_register_rejects_deleted_base(bundle: _Bundle) -> None:
    """库已删除 → 11001（PRD 边界：其下文档接口不可用）。"""
    base_id = await _seed_base(bundle)
    await bundle.bases.soft_delete(base_id, _auth())

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.register_document(
            base_id=base_id, file_name="手册.pdf", file_size=1024, auth=_auth()
        )
    assert exc_info.value.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND


async def test_register_records_audit_entry(bundle: _Bundle) -> None:
    """上传写入操作记录。"""
    record = await bundle.service.register_document(
        base_id="kb_1", file_name="手册.pdf", file_size=1024, auth=_auth()
    )
    entries = await bundle.audit.recent(TENANT, action=ACTION_DOC_UPLOAD)
    assert entries[0].resource_id == record.document_id


# ===========================================================================
# 3. 清单分页（5000 条文档）
# ===========================================================================
async def _seed_many(bundle: _Bundle, base_id: str, count: int) -> None:
    """造 `count` 条文档，上传时间按秒递增（排序确定性）。"""
    for index in range(count):
        await bundle.documents.create(
            DocumentRecord(
                tenant_id=TENANT,
                document_id=f"doc_{index:05d}",
                base_id=base_id,
                file_name=f"文档_{index:05d}.pdf",
                file_size=1024 + index,
                uploader_user_id=UPLOADER,
                uploader_name=UPLOADER,
                uploaded_at=(_BASE_TIME + timedelta(seconds=index))
                .isoformat()
                .replace("+00:00", "Z"),
            )
        )


async def test_list_paginates_deep_page_without_full_scan(bundle: _Bundle) -> None:
    """5000 条文档下第 250 页返回正确的 20 条（PRD 验收 4）。"""
    base_id = await _seed_base(bundle)
    await _seed_many(bundle, base_id, 5000)

    query = ListDocumentsQuery(page=250, page_size=20)
    result = await bundle.service.list_documents(base_id, query, _auth())

    assert result.meta.total == 5000
    assert result.meta.total_pages == 250
    assert len(result.items) == 20
    # 倒序：第 250 页是「最早上传」的 20 条
    assert result.items[0].document_id == "doc_00019"
    assert result.items[-1].document_id == "doc_00000"


async def test_list_first_page_is_newest_first(bundle: _Bundle) -> None:
    """默认按上传时间倒序。"""
    base_id = await _seed_base(bundle)
    await _seed_many(bundle, base_id, 30)

    result = await bundle.service.list_documents(base_id, ListDocumentsQuery(), _auth())
    assert result.items[0].document_id == "doc_00029"


async def test_list_sort_ascending(bundle: _Bundle) -> None:
    """`uploaded_at:asc` 升序返回最早上传的文档。"""
    base_id = await _seed_base(bundle)
    await _seed_many(bundle, base_id, 30)

    query = ListDocumentsQuery(sort="uploaded_at:asc")
    result = await bundle.service.list_documents(base_id, query, _auth())
    assert result.items[0].document_id == "doc_00000"


async def test_list_unknown_sort_falls_back_to_default(bundle: _Bundle) -> None:
    """未知排序字段/方向回落到默认（不把用户输入带进排序片段）。"""
    base_id = await _seed_base(bundle)
    await _seed_many(bundle, base_id, 5)

    query = ListDocumentsQuery(sort="password:drop")
    result = await bundle.service.list_documents(base_id, query, _auth())
    assert result.items[0].document_id == "doc_00004"


async def test_list_keyword_is_case_insensitive(bundle: _Bundle) -> None:
    """关键字筛选大小写不敏感（PRD 验收 3）。"""
    base_id = await _seed_base(bundle)
    await bundle.documents.create(
        DocumentRecord(
            tenant_id=TENANT,
            document_id="doc_a",
            base_id=base_id,
            file_name="Employee Handbook.pdf",
            uploader_user_id=UPLOADER,
        )
    )
    await bundle.documents.create(
        DocumentRecord(
            tenant_id=TENANT,
            document_id="doc_b",
            base_id=base_id,
            file_name="财务制度.pdf",
            uploader_user_id=UPLOADER,
        )
    )

    query = ListDocumentsQuery(keyword="handbook")
    result = await bundle.service.list_documents(base_id, query, _auth())
    assert [item.document_id for item in result.items] == ["doc_a"]


async def test_list_filters_by_stage(bundle: _Bundle) -> None:
    """按阶段筛选只返回该阶段的文档（PRD 验收 3）。"""
    base_id = await _seed_base(bundle)
    for index, stage in enumerate(["uploaded", "parsing", "failed", "completed"]):
        await bundle.documents.create(
            DocumentRecord(
                tenant_id=TENANT,
                document_id=f"doc_{index}",
                base_id=base_id,
                file_name=f"文档{index}.pdf",
                stage=stage,
                index_status=IndexStatus.FAILED.value
                if stage == "failed"
                else (IndexStatus.COMPLETED.value if stage == "completed" else "processing"),
                uploader_user_id=UPLOADER,
            )
        )

    query = ListDocumentsQuery(stage=DocumentStage.FAILED)
    result = await bundle.service.list_documents(base_id, query, _auth())
    assert [item.document_id for item in result.items] == ["doc_2"]
    assert result.items[0].stage_display_name == "处理未成功"


async def test_list_missing_base_returns_11001(bundle: _Bundle) -> None:
    """库不存在返回 11001。"""
    with pytest.raises(ApiError) as exc_info:
        await bundle.service.list_documents("kb_missing", ListDocumentsQuery(), _auth())
    assert exc_info.value.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND


async def test_list_invisible_base_returns_20003(bundle: _Bundle) -> None:
    """他人私有库返回 20003（列表/检索/问答同一套判定）。"""
    from app.schemas.rag import CreateBaseRequest, Visibility

    data = await bundle.bases.create(
        CreateBaseRequest(name="私人库", visibility=Visibility.PRIVATE), _auth()
    )

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.list_documents(data.base_id, ListDocumentsQuery(), _auth(OTHER))
    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_list_excludes_deleted_documents(bundle: _Bundle) -> None:
    """已删除的文档不出现在清单中。"""
    base_id = await _seed_base(bundle)
    await bundle.documents.create(
        DocumentRecord(tenant_id=TENANT, document_id="doc_1", base_id=base_id, file_name="a.pdf")
    )
    await bundle.documents.soft_delete(TENANT, "doc_1")

    result = await bundle.service.list_documents(base_id, ListDocumentsQuery(), _auth())
    assert result.items == []


# ===========================================================================
# 4. 条目转换与进度流转
# ===========================================================================
async def test_to_item_falls_back_to_user_id_for_uploader(bundle: _Bundle) -> None:
    """上传人姓名缺省时以用户 ID 兜底展示（成员目录由 ID 批次接入）。"""
    record = DocumentRecord(
        tenant_id=TENANT,
        document_id="doc_1",
        base_id="kb_1",
        file_name="手册.pdf",
        uploader_user_id=UPLOADER,
    )
    assert bundle.service.to_item(record).uploader_name == UPLOADER


async def test_to_status_data_carries_file_size_and_upload_time(bundle: _Bundle) -> None:
    """R4 扩展字段：文档大小与上传时间（PRD P2-KB-02 R4 扩展）。"""
    record = DocumentRecord(
        tenant_id=TENANT,
        document_id="doc_1",
        base_id="kb_1",
        file_name="手册.pdf",
        file_size=2048,
        uploaded_at="2026-10-01T00:00:00Z",
        stage=DocumentStage.PARSING.value,
        progress=50,
    )
    data = bundle.service.to_status_data(record)
    assert data.file_size == 2048
    assert data.uploaded_at == "2026-10-01T00:00:00Z"
    assert data.stage == DocumentStage.PARSING
    assert data.index_status == IndexStatus.PROCESSING


async def test_refresh_progress_writes_stage_and_status_together(bundle: _Bundle) -> None:
    """刷新进度时五态与三态成对写入。"""
    await bundle.documents.create(
        DocumentRecord(tenant_id=TENANT, document_id="doc_1", base_id="kb_1", file_name="a.pdf")
    )
    updated = await bundle.service.refresh_progress(
        tenant_id=TENANT,
        document_id="doc_1",
        native_status="completed",
        progress=100,
        segment_count=12,
    )
    assert updated is not None
    assert updated.stage == DocumentStage.COMPLETED.value
    assert updated.index_status == IndexStatus.COMPLETED.value
    assert updated.segment_count == 12
    assert updated.progress == 100


async def test_refresh_progress_sets_failure_message(bundle: _Bundle) -> None:
    """失败态写入业务语言原因。"""
    await bundle.documents.create(
        DocumentRecord(tenant_id=TENANT, document_id="doc_1", base_id="kb_1", file_name="a.pdf")
    )
    updated = await bundle.service.refresh_progress(
        tenant_id=TENANT,
        document_id="doc_1",
        native_status="error",
        progress=10,
        error_message="文档解析失败，请确认文件内容后重新上传",
    )
    assert updated is not None
    assert updated.stage == DocumentStage.FAILED.value
    assert updated.error_message == "文档解析失败，请确认文件内容后重新上传"


async def test_refresh_progress_returns_none_for_unknown_document(bundle: _Bundle) -> None:
    """未落库的文档刷新返回 None（不抛异常，兼容历史数据）。"""
    assert (
        await bundle.service.refresh_progress(
            tenant_id=TENANT, document_id="doc_missing", native_status="completed", progress=100
        )
        is None
    )


async def test_mark_reprocessing_resets_stage(bundle: _Bundle) -> None:
    """重新处理后阶段回到「正在读取内容」（PRD 验收 2）。"""
    await bundle.documents.create(
        DocumentRecord(
            tenant_id=TENANT,
            document_id="doc_1",
            base_id="kb_1",
            file_name="a.pdf",
            stage=DocumentStage.FAILED.value,
            index_status=IndexStatus.FAILED.value,
            progress=30,
        )
    )
    updated = await bundle.service.mark_reprocessing(tenant_id=TENANT, document_id="doc_1")
    assert updated is not None
    assert updated.stage == DocumentStage.PARSING.value
    assert updated.index_status == IndexStatus.PROCESSING.value
    assert updated.progress == 0


async def test_delete_processing_document_returns_11006(bundle: _Bundle) -> None:
    """处理中的文档不允许删除 → 11006 且文案为业务语言（PRD 验收 5）。"""
    await bundle.documents.create(
        DocumentRecord(
            tenant_id=TENANT,
            document_id="doc_1",
            base_id="kb_1",
            file_name="a.pdf",
            stage=DocumentStage.PARSING.value,
        )
    )
    with pytest.raises(ApiError) as exc_info:
        await bundle.service.require_deletable(tenant_id=TENANT, document_id="doc_1")
    assert exc_info.value.code == ErrorCode.DOCUMENT_STATE_CONFLICT
    assert exc_info.value.message == "文档正在处理，请稍后再删除"


async def test_delete_completed_document_allowed(bundle: _Bundle) -> None:
    """已完成的文档允许删除。"""
    await bundle.documents.create(
        DocumentRecord(
            tenant_id=TENANT,
            document_id="doc_1",
            base_id="kb_1",
            file_name="a.pdf",
            stage=DocumentStage.COMPLETED.value,
            index_status=IndexStatus.COMPLETED.value,
        )
    )
    record = await bundle.service.require_deletable(tenant_id=TENANT, document_id="doc_1")
    assert record is not None
    assert await bundle.service.soft_delete(
        tenant_id=TENANT, actor_id=UPLOADER, document_id="doc_1"
    )


async def test_require_deletable_unknown_document_returns_none(bundle: _Bundle) -> None:
    """未落库的文档不做处理中校验（兼容历史数据）。"""
    assert await bundle.service.require_deletable(tenant_id=TENANT, document_id="doc_x") is None
