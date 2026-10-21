"""RAG 服务协议转换单元测试（架构文档 3.2 / 3.2.6）。"""

from __future__ import annotations

import pytest

from app.clients.dify_client import DifyClient
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseRequest,
    IndexStatus,
    ListBasesQuery,
    SearchRequest,
    Visibility,
)
from app.services.rag_service import (
    RagService,
    map_index_status,
    new_base_id,
    new_document_id,
)

pytestmark = pytest.mark.unit

_AUTH = AuthContext(user_id="usr_1", tenant_id="tnt_1")


def _service(**overrides: object) -> RagService:
    """构造 RAG 服务。

    Args:
        **overrides: 覆盖的配置项。

    Returns:
        RagService: 服务实例。
    """
    settings = Settings(downstream_enabled=False, **overrides)  # type: ignore[arg-type]
    return RagService(settings=settings, dify=DifyClient("http://example.invalid", "app-test"))


def test_id_prefixes_follow_spec() -> None:
    """资源 ID 前缀符合架构文档 9.1。"""
    assert new_base_id().startswith("kb_")
    assert new_document_id().startswith("doc_")


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        ("waiting", IndexStatus.PROCESSING),
        ("parsing", IndexStatus.PROCESSING),
        ("cleaning", IndexStatus.PROCESSING),
        ("splitting", IndexStatus.PROCESSING),
        ("indexing", IndexStatus.PROCESSING),
        ("completed", IndexStatus.COMPLETED),
        ("error", IndexStatus.FAILED),
        ("unknown_native_state", IndexStatus.PROCESSING),
    ],
)
def test_map_index_status_collapses_to_three_states(native: str, expected: IndexStatus) -> None:
    """底座七态映射为自研三态，原状态名不外泄（附录 A-3）。"""
    assert map_index_status(native) == expected
    assert len({IndexStatus.PROCESSING, IndexStatus.COMPLETED, IndexStatus.FAILED}) == 3


async def test_create_base_stub() -> None:
    """桩模式创建知识库返回自研 base_id。"""
    data = await _service().create_base(
        CreateBaseRequest(
            name="人事制度库", description="制度文档", visibility=Visibility.ORGANIZATION
        ),
        _AUTH,
    )
    assert data.base_id.startswith("kb_")
    assert data.name == "人事制度库"
    assert data.created_at.endswith("Z")


async def test_list_bases_returns_empty_page() -> None:
    """空存储下列表返回空分页且元信息正确。"""
    result = await _service().list_bases(ListBasesQuery(page=1, page_size=20), _AUTH)
    assert result.items == []
    assert result.meta.total == 0
    assert result.meta.total_pages == 0


async def test_upload_document_stub() -> None:
    """桩模式上传返回 processing 初态。"""
    data = await _service().upload_document(
        base_id="kb_1",
        file_name="员工手册.pdf",
        file_size=1024,
        chunk_policy=ChunkPolicy.DEFAULT,
        auth=_AUTH,
    )
    assert data.document_id.startswith("doc_")
    assert data.index_status == IndexStatus.PROCESSING
    assert data.progress == 0


@pytest.mark.parametrize("extension", ["pdf", "docx", "xlsx", "md", "txt"])
async def test_upload_accepts_whitelisted_formats(extension: str) -> None:
    """白名单格式全部允许。"""
    data = await _service().upload_document(
        base_id="kb_1",
        file_name=f"文档.{extension}",
        file_size=2048,
        chunk_policy=ChunkPolicy.DEFAULT,
        auth=_AUTH,
    )
    assert data.index_status == IndexStatus.PROCESSING


async def test_upload_rejects_unsupported_format() -> None:
    """非白名单格式返回 11002。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().upload_document(
            base_id="kb_1",
            file_name="病毒.exe",
            file_size=2048,
            chunk_policy=ChunkPolicy.DEFAULT,
            auth=_AUTH,
        )
    assert exc_info.value.code == ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED


async def test_upload_rejects_oversized_file() -> None:
    """超出传输层上限返回 20008（请求体超限，QA BUG-04）。"""
    with pytest.raises(ApiError) as exc_info:
        await _service(max_upload_bytes=1024).upload_document(
            base_id="kb_1",
            file_name="大文件.pdf",
            file_size=4096,
            chunk_policy=ChunkPolicy.DEFAULT,
            auth=_AUTH,
        )
    assert exc_info.value.code == ErrorCode.PAYLOAD_TOO_LARGE


async def test_document_char_limit_returns_11003() -> None:
    """解析后字符数超限返回 11003（文档内容过多，QA BUG-04）。"""
    service = _service(max_document_chars=100)
    with pytest.raises(ApiError) as exc_info:
        service._validate_document_chars(500)  # noqa: SLF001 - 验证校验函数
    assert exc_info.value.code == ErrorCode.DOCUMENT_TOO_LARGE


async def test_upload_rejects_empty_file() -> None:
    """空文件返回 11002。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().upload_document(
            base_id="kb_1",
            file_name="空.pdf",
            file_size=0,
            chunk_policy=ChunkPolicy.DEFAULT,
            auth=_AUTH,
        )
    assert exc_info.value.code == ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED


def test_transform_index_status_computes_progress() -> None:
    """按已完成/总分块数计算进度百分比。"""
    service = _service()
    data = service._transform_index_status(  # noqa: SLF001
        "doc_1", {"indexing_status": "indexing", "completed_segments": 30, "total_segments": 60}
    )
    assert data.index_status == IndexStatus.PROCESSING
    assert data.progress == 50
    assert data.segment_count == 30


def test_transform_index_status_completed_is_100() -> None:
    """完成态进度恒为 100。"""
    service = _service()
    data = service._transform_index_status("doc_1", {"indexing_status": "completed"})  # noqa: SLF001
    assert data.progress == 100
    assert data.error_message is None


def test_transform_index_status_failed_has_business_message() -> None:
    """失败态给出业务语言原因，不透出底座报文。"""
    service = _service()
    data = service._transform_index_status(  # noqa: SLF001
        "doc_1", {"indexing_status": "error", "error": "parser crashed at line 42"}
    )
    assert data.index_status == IndexStatus.FAILED
    assert data.error_message == "文档解析失败，请确认文件内容后重新上传"
    assert "parser crashed" not in (data.error_message or "")


def test_transform_search_result_maps_native_records() -> None:
    """底座 records 转换为自研 segments。"""
    service = _service()
    data = service._transform_search_result(  # noqa: SLF001
        {
            "records": [
                {
                    "document_id": "doc_1",
                    "score": 0.92,
                    "segment": {
                        "document_name": "员工手册.pdf",
                        "content": "年假申请需提前三个工作日提交",
                        "position": {"page": 12, "paragraph": 3},
                    },
                }
            ]
        }
    )
    assert len(data.segments) == 1
    segment = data.segments[0]
    assert segment.document_name == "员工手册.pdf"
    assert segment.score == 0.92
    assert segment.position is not None
    assert segment.position.page == 12


def test_transform_search_result_handles_empty() -> None:
    """空结果安全返回。"""
    assert _service()._transform_search_result({}).segments == []  # noqa: SLF001


async def test_search_stub_returns_empty_segments() -> None:
    """桩模式检索返回空片段列表。"""
    data = await _service().search(SearchRequest(base_id="kb_1", query="年假怎么申请"))
    assert data.segments == []


async def test_search_with_auth_denies_unregistered_base() -> None:
    """判权 fail-closed：无元数据的知识库即便换 ID 写法也一律 20003（QA B05-P0-01）。

    修复前 `_assert_readable()` 在「本地查不到元数据」时直接 return，
    而 R7 下发下游会执行 `removeprefix("kb_")`，于是未授权成员只要去掉前缀
    就能让判权整体短路。现在入口先归一化、再 fail-closed。
    """
    service = _service()
    for base_id in ("kb_unregistered", "unregistered"):
        with pytest.raises(ApiError) as excinfo:
            await service.search(SearchRequest(base_id=base_id, query="年假"), _AUTH)
        assert excinfo.value.code == ErrorCode.FORBIDDEN
        assert excinfo.value.http_status == 403


async def test_search_skips_permission_check_without_auth() -> None:
    """服务层无鉴权调用（auth=None）不做对象级判权，保持既有内部调用路径可用。"""
    data = await _service().search(SearchRequest(base_id="kb_unregistered", query="年假"))
    assert data.segments == []


def test_normalize_base_id_has_single_canonical_form() -> None:
    """同一底座对象的两种写法归一为同一判定键（`kb_` 形态）。"""
    from app.business.policy_service import normalize_base_id

    assert normalize_base_id("kb_01ABC") == "kb_01ABC"
    assert normalize_base_id("01ABC") == "kb_01ABC"
    assert normalize_base_id("  01ABC  ") == "kb_01ABC"
    assert normalize_base_id("") == ""


async def test_r3_and_kb3_share_one_canonical_base_id() -> None:
    """R3 写入与 KB3 查询对「原生写法 / 规范写法」得到同一对象（QA B05-P0-01）。

    修复前：用原生 ID 上传会写进 `base_id=<原生>` 的文档行，用 `kb_<原生>` 查清单
    永远查不到（反之亦然），于是「一个对象两种 ID」在文档链路上也成立。
    """
    from app.business.document_service import DocumentService
    from app.db.repositories.knowledge_base_repo import (
        KnowledgeBaseRecord,
        get_knowledge_base_store,
    )
    from app.schemas.knowledge_ext import ListDocumentsQuery

    settings = Settings(downstream_enabled=False)
    await get_knowledge_base_store(settings).create(
        KnowledgeBaseRecord(
            tenant_id=_AUTH.tenant_id,
            base_id="kb_NATIVE_BASE",
            name="R3 归一化库",
            visibility="organization",
            owner_user_id=_AUTH.user_id,
        )
    )

    service = _service()
    uploaded = await service.upload_document(
        base_id="NATIVE_BASE",
        file_name="handbook.pdf",
        file_size=1024,
        auth=_AUTH,
    )

    document_service = DocumentService(settings=settings)
    record = await document_service.store.get(_AUTH.tenant_id, uploaded.document_id)
    assert record is not None
    assert record.base_id == "kb_NATIVE_BASE"

    listed = await document_service.list_documents("kb_NATIVE_BASE", ListDocumentsQuery(), _AUTH)
    assert listed.meta.total == 1
    listed_by_native = await document_service.list_documents(
        "NATIVE_BASE", ListDocumentsQuery(), _AUTH
    )
    assert [item.document_id for item in listed_by_native.items] == [uploaded.document_id]


async def test_reindex_conflicts_while_processing() -> None:
    """文档处理中重建索引返回 11006。"""
    service = _service()
    # 桩模式 get_index_status 恒返回 processing，因此直接验证冲突分支
    with pytest.raises(ApiError) as exc_info:
        await service.reindex(base_id="kb_1", doc_id="doc_1")
    assert exc_info.value.code == ErrorCode.DOCUMENT_STATE_CONFLICT


async def test_delete_document_returns_id() -> None:
    """删除文档返回被删 ID。"""
    assert await _service().delete_document(base_id="kb_1", doc_id="doc_1") == "doc_1"


def test_search_request_validates_bounds() -> None:
    """检索入参边界校验由 Pydantic 承担。"""
    with pytest.raises(ValueError):
        SearchRequest(base_id="kb_1", query="x", top_k=0)
    with pytest.raises(ValueError):
        SearchRequest(base_id="kb_1", query="x", score_threshold=1.5)
