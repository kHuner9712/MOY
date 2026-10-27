"""RAG 服务协议转换单元测试（架构文档 3.2 / 3.2.6）。"""

from __future__ import annotations

import pytest

from app.clients.dify_client import DifyClient, DifyResponse
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseRequest,
    DocumentStage,
    IndexStatus,
    ListBasesQuery,
    SearchRequest,
    Visibility,
    stage_display_name,
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


# ---------------------------------------------------------------------------
# B27：`11005 RETRIEVAL_FAILED` 接线（R7 底座故障路径）
#
# 判据（冻结契约表 R7 行）：底座连接失败 / 超时 → `HTTP 200 + 11005`，
# 而**不是** `HTTP 503 + 30001/30002`。其他下游码（429 限流、400 参数错）
# 语义已正确，必须原样上抛——把"参数错"说成"服务繁忙"会掩盖真实原因。
# ---------------------------------------------------------------------------


class _RaisingDify:
    """只实现 `post` 的假底座：记录被调用的路径后抛出预置异常。

    Attributes:
        error: 需要抛出的业务异常（模拟 `DifyClient` 的映射结果）。
        paths: 被调用过的下游路径（用于断言"只打了一次检索"）。
    """

    def __init__(self, error: ApiError) -> None:
        """初始化假底座。

        Args:
            error: 需要抛出的业务异常。
        """
        self.error = error
        self.paths: list[str] = []

    async def post(
        self, path: str, payload: dict[str, object], *, api_key: str | None = None
    ) -> object:
        """模拟一次失败的下游 POST。

        Args:
            path: 下游路径。
            payload: 请求体（本假实现不使用）。
            api_key: 下游密钥（本假实现不使用）。

        Raises:
            ApiError: 构造时注入的异常。
        """
        del payload, api_key
        self.paths.append(path)
        raise self.error


class _FailingResolver:
    """凭据解析必定失败的假解析器（模拟库级凭据失效 11007）。"""

    async def resolve(self, base_id: str, *, tenant_id: str) -> object:
        """模拟解析失败。

        Args:
            base_id: 知识库 ID。
            tenant_id: 租户 ID。

        Raises:
            ApiError: 固定抛 `11007`。
        """
        raise ApiError(
            ErrorCode.KB_CREDENTIAL_UNAVAILABLE,
            internal_detail=f"base_id={base_id} tenant_id={tenant_id} decrypt failed",
        )


def _live_service(dify: object = None, resolver: object = None) -> RagService:
    """构造**非桩模式**的 RAG 服务（让 `search()` 真正下发下游）。

    Args:
        dify: 可选假底座。
        resolver: 可选假凭据解析器。

    Returns:
        RagService: 服务实例。
    """
    return RagService(
        settings=Settings(downstream_enabled=True),
        dify=dify,  # type: ignore[arg-type]
        resolver=resolver,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "downstream_code",
    [ErrorCode.DOWNSTREAM_UNAVAILABLE, ErrorCode.DOWNSTREAM_TIMEOUT],
)
async def test_search_maps_downstream_failure_to_11005(downstream_code: ErrorCode) -> None:
    """底座连不上 / 超时 → `11005`（HTTP 200），不是一个 503 系统码。"""
    from app.core.errors import ERROR_REGISTRY

    dify = _RaisingDify(ApiError(downstream_code, internal_detail="edge reset"))
    with pytest.raises(ApiError) as exc_info:
        await _live_service(dify=dify).search(SearchRequest(base_id="kb_1", query="年假"))

    error = exc_info.value
    assert error.code == ErrorCode.RETRIEVAL_FAILED
    assert int(error.code) == 11005
    # 契约：HTTP 200（前端按业务失败处理，可原地重试），不是 503
    assert error.http_status == 200
    # 文案取注册表原文（不得临时改写为技术语言）
    assert error.message == ERROR_REGISTRY[ErrorCode.RETRIEVAL_FAILED].message
    assert error.message == "检索服务暂时不可用，请重试"
    # 下游客码只进日志，供排障；不得出现在 message 里
    assert error.internal_detail is not None
    assert f"code={int(downstream_code)}" in error.internal_detail
    assert "edge reset" in error.internal_detail
    assert f"{int(downstream_code)}" not in error.message
    # 只打了一次检索、且命中的是检索路径（不是上传/建库路径）
    assert dify.paths == ["/v1/datasets/1/retrieve"]


async def test_search_keeps_rate_limited_code() -> None:
    """429 限流原样上抛（`20006`，HTTP 429），不得被改写成 `11005`。

    把限流说成"检索服务不可用"会让用户无限重试，正是 429 想避免的行为。
    """
    dify = _RaisingDify(ApiError(ErrorCode.RATE_LIMITED, internal_detail="429"))
    with pytest.raises(ApiError) as exc_info:
        await _live_service(dify=dify).search(SearchRequest(base_id="kb_1", query="年假"))

    assert exc_info.value.code == ErrorCode.RATE_LIMITED
    assert exc_info.value.http_status == 429


async def test_search_keeps_param_invalid_code() -> None:
    """400 参数错原样上抛（`20001`），不得被改写成 `11005`。"""
    dify = _RaisingDify(ApiError(ErrorCode.PARAM_INVALID, internal_detail="bad top_k"))
    with pytest.raises(ApiError) as exc_info:
        await _live_service(dify=dify).search(SearchRequest(base_id="kb_1", query="年假"))

    assert exc_info.value.code == ErrorCode.PARAM_INVALID
    assert exc_info.value.http_status == 400


async def test_search_does_not_wrap_credential_error() -> None:
    """库级凭据失效（`11007`）在 try **之外**，必须原样上抛且绝不下发下游。

    11007 的出路是"联系管理员"，被改写成"请重试"会把用户引向死循环；
    同时必须证明凭据失败时**没有**任何下游请求发出（不回落全局凭据）。
    """
    dify = _RaisingDify(ApiError(ErrorCode.DOWNSTREAM_UNAVAILABLE))
    with pytest.raises(ApiError) as exc_info:
        await _live_service(dify=dify, resolver=_FailingResolver()).search(
            SearchRequest(base_id="kb_1", query="年假")
        )

    assert exc_info.value.code == ErrorCode.KB_CREDENTIAL_UNAVAILABLE
    assert int(exc_info.value.code) == 11007
    assert exc_info.value.http_status == 200
    assert dify.paths == []


# ---------------------------------------------------------------------------
# P1-1：真实模式上传必须把**文件字节**透传到底座
#
# 修复前的实证：`upload_document` 真实分支只向 `/v1/files/upload` POST
# `{"filename": ..., "purpose": ...}`，端点读到的字节在服务层被丢弃，
# 底座永远拿不到文件内容。本组用例把"字节 → multipart 文件字段"钉成契约。
# ---------------------------------------------------------------------------


class _RecordingDify:
    """记录 multipart 与 JSON 两种调用的假底座。

    Attributes:
        multipart_calls: 每次 multipart 调用的入参。
        post_calls: 每次 JSON POST 的入参。
    """

    def __init__(self, file_id: str = "file-abc") -> None:
        """初始化假底座。

        Args:
            file_id: 上传接口返回的文件 ID。
        """
        self.file_id = file_id
        self.multipart_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    async def post_multipart(
        self,
        path: str,
        *,
        files: dict[str, tuple[str, bytes, str]],
        form_data: dict[str, str] | None = None,
        api_key: str | None = None,
    ) -> DifyResponse:
        """记录一次 multipart 上传。

        Args:
            path: 下游路径。
            files: 文件字段。
            form_data: 文本字段。
            api_key: 下游密钥。

        Returns:
            DifyResponse: 上传成功的底座载荷。
        """
        self.multipart_calls.append(
            {
                "path": path,
                "files": files,
                "form_data": form_data,
                "api_key": api_key,
            }
        )
        return DifyResponse(200, {"id": self.file_id, "name": "员工手册.pdf"})

    async def post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        idempotent: bool = False,
        api_key: str | None = None,
    ) -> DifyResponse:
        """记录一次 JSON POST（`documents/create`）。

        Args:
            path: 下游路径。
            payload: 请求体。
            idempotent: 是否幂等（本替身不使用）。
            api_key: 下游密钥。

        Returns:
            DifyResponse: 建文档成功的底座载荷。
        """
        del idempotent
        self.post_calls.append({"path": path, "payload": payload, "api_key": api_key})
        return DifyResponse(200, {"batch": "batch-1", "indexing_status": "waiting"})


async def test_upload_live_mode_passes_file_bytes_as_multipart() -> None:
    """真实模式：字节 + 文件名进入 multipart 文件字段，`documents/create` 逻辑不变。"""
    dify = _RecordingDify()
    content = b"%PDF-1.4 \xe5\x91\x98\xe5\xb7\xa5\xe6\x89\x8b\xe5\x86\x8c"

    data = await _live_service(dify=dify).upload_document(
        base_id="kb_1",
        file_name="员工手册.pdf",
        file_size=len(content),
        chunk_policy=ChunkPolicy.FINE,
        auth=_AUTH,
        content=content,
    )

    # ① 上传走 multipart，且文件字段带真实字节与文件名
    upload_call = dify.multipart_calls[0]
    assert upload_call["path"] == "/v1/files/upload"
    files = upload_call["files"]
    assert isinstance(files, dict)
    name, payload, content_type = files["file"]
    assert name == "员工手册.pdf"
    assert payload == content
    assert content_type == "application/pdf"
    # ② 文本字段说明用途（底座据此判定"文档"而非图片）
    assert upload_call["form_data"]["purpose"] == "document"  # type: ignore[index]
    # ②b 上传者标识：Dify `/v1/files/upload` 的 `user` 是**必填**字段，
    # 漏发会直接 400（真实底座上表现为"上传失败"，而桩模式永远看不出来）。
    assert upload_call["form_data"]["user"] == _AUTH.user_id  # type: ignore[index]
    # ③ 后续建文档逻辑不变：仍走 JSON POST，携带同一文件 ID 派生的文档 ID
    create_call = dify.post_calls[0]
    assert create_call["path"] == "/v1/datasets/1/documents"
    assert data.document_id == "doc_file-abc"
    assert data.file_name == "员工手册.pdf"
    assert data.chunk_policy == ChunkPolicy.FINE


async def test_upload_live_mode_without_content_is_a_coding_defect() -> None:
    """真实模式漏传字节 → 30003（编码缺陷），且**不向底座发空文件**。"""
    dify = _RecordingDify()
    with pytest.raises(ApiError) as exc_info:
        await _live_service(dify=dify).upload_document(
            base_id="kb_1",
            file_name="员工手册.pdf",
            file_size=1024,
            auth=_AUTH,
        )
    assert exc_info.value.code == ErrorCode.INTERNAL_ERROR
    assert dify.multipart_calls == []
    assert dify.post_calls == []


@pytest.mark.asyncio
async def test_upload_is_held_without_calling_downstream_when_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P4-11 ②b：超额上传 = 零底座调用 + 记录保留 + 未处理 + 如实文案。

    判据（主理人 2026-10-27 定稿，b-1 口径）：
    - `post_multipart` 调用次数 **== 0**（"零上游"指零底座调用，不是零落库）；
    - `document_meta` 行存在且 `file_size > 0`（PRD 5.3「已上传的保留」）；
    - `progress == 0` / `segment_count == 0`（零处理）；
    - `stage == quota_held`，列表文案「额度已用尽，未处理」；
    - 提示文案带**可执行出路**（「重新上传」）——因为 V1.0 不存输入字节，
      恢复后无法重放解析（PRD 5.3 ② 已按 b-1 修订留痕）。
    """
    dify = _RecordingDify()
    service = _live_service(dify=dify)

    async def _exhausted(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(service._usage, "is_capability_exhausted", _exhausted)  # noqa: SLF001

    data = await service.upload_document(
        base_id="kb_quota_1",
        file_name="合同.docx",
        file_size=2048,
        auth=_AUTH,
        content=b"x" * 2048,
    )

    assert dify.multipart_calls == [], "超额时一次底座调用都不许发"
    assert data.stage == DocumentStage.QUOTA_HELD
    assert stage_display_name(data.stage) == "额度已用尽，未处理"
    assert data.progress == 0
    assert data.notice_code == 15006
    assert "重新上传" in data.notice_message, "红线要求文案里有可执行出路"

    record = await service._documents.store.get(_AUTH.tenant_id, data.document_id)  # noqa: SLF001
    assert record is not None, "PRD 5.3「已上传的保留」：记录必须落库"
    assert record.file_size == 2048
    assert record.hold_reason == "quota"
    assert record.progress == 0
    assert record.segment_count == 0
