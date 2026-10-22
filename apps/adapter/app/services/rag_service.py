"""RAG 服务协议转换（架构文档 3.2 / 3.6 类图 `RagService`）。

转换职责（架构文档 3.2.6）：
- 知识库：底座 `id` → 自研 `base_id`；`visibility` 由适配层落业务库（底座无此概念）；
- 文档：底座 `file_id`/`batch` → 自研 `document_id`；
- 处理进度：底座七态（waiting/parsing/cleaning/splitting/indexing/completed/error）
  **映射**为自研五态 `stage` 与三态 `index_status`（原状态名不外泄，附录 A-3）；
- 检索：底座 `records[].segment.*` → 自研 `segments[]`。

T01 阶段说明：`Settings.downstream_enabled=false` 时返回业务化 mock 结果，
不产生任何真实底座请求；**但库级/文档级元数据仍会落业务库**（B04 起），
否则「改名 / 删除 / 文档清单」在桩模式下会退化为空实现。

B04 落库边界（架构文档 9.2 第 8 条）：

- R1 创建 → `KnowledgeBaseService.create()`（库级 `chunk_policy` 持久化）；
- R2 列表 → `KnowledgeBaseService.list()`（含 `my_role` / `updated_at`）；
- R3 上传 → `DocumentService.register_document()`（不带 `chunk_policy` 时按库级处理）；
- R4 进度 → 已落库文档直接读本地记录，未落库时回落到桩结果（保证既有回归不变）。

B05 追加（引用回链 D-11）：

- R7 命中结果带 `base_id` / `base_name` / `segment_id`，并旁路回填 `segment_map`；
- R7 先做对象级判权（`20003`），未登记元数据的历史库保持阶段一行为；
- R5 成功完成后由 `DocumentService` 把旧批次映射统一转 stale（见其实现）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from app.business.credential_service import CredentialResolver, get_credential_resolver
from app.business.document_service import DocumentService, map_stage
from app.business.knowledge_base_service import KnowledgeBaseService
from app.business.policy_service import PolicyService, get_policy_service, normalize_base_id
from app.business.segment_service import SegmentHit, SegmentService, build_key
from app.business.usage_service import UsageService, get_usage_service
from app.clients.dify_client import DifyClient, get_dify_client
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.tenant import resolve_runtime_tenant
from app.registry.loader import ProfileRouteTable, get_profile_routes
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseData,
    CreateBaseRequest,
    DocumentStage,
    IndexStatus,
    IndexStatusData,
    ListBasesQuery,
    ReindexData,
    SearchData,
    SearchRequest,
    SearchSegment,
    SegmentPosition,
    UploadDocumentData,
    Visibility,
)

logger = get_logger(__name__)

#: 底座原生路径（仅本文件与 `clients/dify_client.py` 可出现）
_DIFY_DATASETS_PATH: Final[str] = "/v1/datasets"
_DIFY_UPLOAD_PATH: Final[str] = "/v1/files/upload"
_DIFY_RETRIEVE_PATH: Final[str] = "/v1/datasets/{dataset_id}/retrieve"

#: 资源 ID 前缀（架构文档 9.1）
_BASE_PREFIX: Final[str] = "kb_"
_DOC_PREFIX: Final[str] = "doc_"

#: 需要在 R7 改写成 `11005 检索失败` 的下游码（B27 接线）。
#:
#: 只含「底座连不上 / 超时」：两者对用户是同一件事（稍后重试即可恢复），
#: 且系统级码（HTTP 503）会让前端弹「服务不可用」并中断知识库问答。
#: **不得**扩大本集合——429（限流）/ 400（参数错）等的既有语义已经正确，
#: 改写会掩盖真实原因（尤其是把"请求参数错"说成"服务繁忙"）。
_RETRIEVAL_DOWNSTREAM_CODES: Final[frozenset[ErrorCode]] = frozenset(
    {ErrorCode.DOWNSTREAM_UNAVAILABLE, ErrorCode.DOWNSTREAM_TIMEOUT}
)

#: 底座索引状态 → 自研三态映射（附录 A-3）
_INDEX_STATUS_MAP: Final[dict[str, IndexStatus]] = {
    "waiting": IndexStatus.PROCESSING,
    "parsing": IndexStatus.PROCESSING,
    "cleaning": IndexStatus.PROCESSING,
    "splitting": IndexStatus.PROCESSING,
    "indexing": IndexStatus.PROCESSING,
    "completed": IndexStatus.COMPLETED,
    "error": IndexStatus.FAILED,
}


def new_base_id() -> str:
    """生成知识库 ID。

    Returns:
        str: 形如 `kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID（架构文档 9.1）。
    """
    return new_resource_id(_BASE_PREFIX)


def new_document_id() -> str:
    """生成文档 ID。

    Returns:
        str: 形如 `doc_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID（架构文档 9.1）。
    """
    return new_resource_id(_DOC_PREFIX)


def _now_iso() -> str:
    """获取当前 UTC 时间的 ISO 8601 字符串。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _elapsed_since(uploaded_at: str) -> float:
    """计算自上传时间以来的秒数（用于预估剩余时间）。

    Args:
        uploaded_at: 上传时间（ISO 8601）。

    Returns:
        float: 秒数；时间不可解析时返回 0。
    """
    if not uploaded_at:
        return 0.0
    try:
        moment = datetime.fromisoformat(uploaded_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max((datetime.now(UTC) - moment).total_seconds(), 0.0)


def map_index_status(native_status: str) -> IndexStatus:
    """将底座原生索引状态映射为自研三态。

    Args:
        native_status: 底座原生索引状态。

    Returns:
        IndexStatus: 自研索引状态；未知状态按处理中处理。
    """
    return _INDEX_STATUS_MAP.get(native_status, IndexStatus.PROCESSING)


class RagService:
    """RAG 服务。

    Attributes:
        _settings: 配置实例。
        _routes: 档位路由表（提供检索与内容切分默认策略）。
        _dify: 底座客户端。
        _credentials: per-kb 凭据解析器（D-10）。
        _bases: 知识库元数据业务服务（B04）。
        _documents: 文档元数据业务服务（B04）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        routes: ProfileRouteTable | None = None,
        dify: DifyClient | None = None,
        resolver: CredentialResolver | None = None,
        bases: KnowledgeBaseService | None = None,
        documents: DocumentService | None = None,
        policy: PolicyService | None = None,
        segments: SegmentService | None = None,
        usage: UsageService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            routes: 可选档位路由表。
            dify: 可选底座客户端。
            resolver: 可选凭据解析器（测试注入）。
            bases: 可选知识库业务服务（测试注入）。
            documents: 可选文档业务服务（测试注入）。
            policy: 可选可见性判定服务（测试注入）。
            segments: 可选片段映射服务（测试注入）。
            usage: 可选计量服务（测试注入；D-12 唯一写入口）。
        """
        self._settings = settings or get_settings()
        self._routes = routes or get_profile_routes()
        self._dify = dify or get_dify_client()
        self._credentials = resolver or get_credential_resolver(self._settings)
        self._bases: KnowledgeBaseService = bases or KnowledgeBaseService(self._settings)
        self._documents: DocumentService = documents or DocumentService(
            self._settings, bases=self._bases
        )
        self._policy: PolicyService = policy or get_policy_service(self._settings)
        self._segments: SegmentService = segments or SegmentService(
            self._settings, documents=self._documents.store, bases=self._bases
        )
        self._usage: UsageService = usage or get_usage_service(self._settings)

    # ------------------------------------------------------------------
    # per-kb 凭据（D-10）
    # ------------------------------------------------------------------
    def _tenant_of(self, auth: AuthContext | None) -> str:
        """解析本次下游调用所属租户。

        Args:
            auth: 鉴权上下文（路由层传入；服务层单测可为 None）。

        Returns:
            str: 租户 ID。
        """
        if auth is not None and auth.tenant_id:
            return auth.tenant_id
        return resolve_runtime_tenant(self._settings)

    async def _api_key_for(self, base_id: str, auth: AuthContext | None) -> str:
        """解析该知识库应使用的下游密钥。

        未绑定库返回全局凭据（现状行为不变）；已绑定库解析失败抛 `11007`
        且**绝不回落**（架构文档 2.3 结论第 4 条）。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            str: 下游密钥。

        Raises:
            ApiError: 库级凭据不可用（11007）。
        """
        resolved = await self._credentials.resolve(base_id, tenant_id=self._tenant_of(auth))
        return resolved.api_key

    # ------------------------------------------------------------------
    # R1 创建知识库
    # ------------------------------------------------------------------
    async def create_base(self, request: CreateBaseRequest, auth: AuthContext) -> CreateBaseData:
        """创建知识库。

        Args:
            request: 创建请求。
            auth: 鉴权上下文。

        Returns:
            CreateBaseData: 自研知识库信息。

        Raises:
            ApiError: 底座不可用（30001）或名称冲突（20005）。
        """
        logger.info(
            "knowledge_base_create_requested",
            name=request.name,
            visibility=request.visibility.value,
            chunk_policy=request.chunk_policy.value,
            tenant_id=auth.tenant_id,
        )
        if self._settings.mock_mode:
            return await self._bases.create(request, auth, base_id=new_base_id())

        response = await self._dify.post(
            _DIFY_DATASETS_PATH,
            {
                "name": request.name,
                "description": request.description,
                "indexing_technique": "high_quality",
                "permission": "only_me"
                if request.visibility == Visibility.PRIVATE
                else "all_team_members",
            },
        )
        native_id = str(response.payload.get("id", ""))
        if not native_id:
            raise ApiError(ErrorCode.DOWNSTREAM_UNAVAILABLE, internal_detail="dataset id missing")
        return await self._bases.create(request, auth, base_id=f"{_BASE_PREFIX}{native_id}")

    # ------------------------------------------------------------------
    # R2 知识库列表
    # ------------------------------------------------------------------
    async def list_bases(self, query: ListBasesQuery, auth: AuthContext) -> Any:
        """查询知识库列表（含 `chunk_policy` / `my_role` / `updated_at`）。

        Args:
            query: 分页与关键字过滤参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[KnowledgeBaseItem]` 分页结果。
        """
        return await self._bases.list(query, auth)

    # ------------------------------------------------------------------
    # R3 上传文档
    # ------------------------------------------------------------------
    async def upload_document(
        self,
        *,
        base_id: str,
        file_name: str,
        file_size: int,
        auth: AuthContext,
        chunk_policy: ChunkPolicy | None = None,
    ) -> UploadDocumentData:
        """上传文档并登记文档元数据（不带 `chunk_policy` 时按库级设置处理）。

        Args:
            base_id: 知识库 ID。
            file_name: 文件名。
            file_size: 文件字节数。
            auth: 鉴权上下文。
            chunk_policy: 单次覆盖的内容切分精细度；缺省取库级设置。

        Returns:
            UploadDocumentData: 文档 ID 与处理进度初值（含五态阶段）。

        Raises:
            ApiError: 知识库已删除（11001）、格式不支持（11002）、超出大小限制（11003）。
        """
        # ID 归一化（QA B05-P0-01）：下游 `datasets/<原生 id>/...` 与本地记录必须同一规范键
        base_id = normalize_base_id(base_id)
        self._validate_document(file_name, file_size)
        logger.info(
            "document_upload_requested",
            base_id=base_id,
            file_name=file_name,
            file_size=file_size,
            chunk_policy=chunk_policy.value if chunk_policy is not None else "inherit",
            tenant_id=auth.tenant_id,
        )

        if self._settings.mock_mode:
            record = await self._documents.register_document(
                base_id=base_id,
                file_name=file_name,
                file_size=file_size,
                auth=auth,
                chunk_policy=chunk_policy,
                document_id=new_document_id(),
            )
            return await self._with_quota_notice(
                UploadDocumentData(
                    document_id=record.document_id,
                    file_name=record.file_name,
                    index_status=IndexStatus(record.index_status),
                    stage=DocumentStage(record.stage),
                    chunk_policy=ChunkPolicy(record.chunk_policy),
                    progress=record.progress,
                ),
                auth,
            )

        api_key = await self._api_key_for(base_id, auth)
        effective_policy = chunk_policy or await self._resolve_base_policy(base_id, auth)
        upload = await self._dify.post(
            _DIFY_UPLOAD_PATH, {"filename": file_name, "purpose": "document"}, api_key=api_key
        )
        native_file_id = str(upload.payload.get("id", ""))
        if not native_file_id:
            raise ApiError(
                ErrorCode.DOWNSTREAM_UNAVAILABLE, internal_detail="uploaded file id missing"
            )
        created = await self._dify.post(
            f"{_DIFY_DATASETS_PATH}/{base_id.removeprefix(_BASE_PREFIX)}/documents",
            {
                "data": {
                    "type": "document",
                    "name": file_name,
                    "doc_form": file_name.rsplit(".", 1)[-1].lower(),
                    "chunk_policy": effective_policy.value,
                },
                "indexing_technique": "high_quality",
            },
            api_key=api_key,
        )
        batch = created.payload.get("batch", "")
        logger.debug("document_upload_dispatched", base_id=base_id, has_batch=bool(batch))
        record = await self._documents.register_document(
            base_id=base_id,
            file_name=file_name,
            file_size=file_size,
            auth=auth,
            chunk_policy=effective_policy,
            document_id=f"{_DOC_PREFIX}{native_file_id}",
            native_status=str(created.payload.get("indexing_status", "waiting")),
        )
        return await self._with_quota_notice(
            UploadDocumentData(
                document_id=record.document_id,
                file_name=record.file_name,
                index_status=IndexStatus(record.index_status),
                stage=DocumentStage(record.stage),
                chunk_policy=ChunkPolicy(record.chunk_policy),
                progress=record.progress,
            ),
            auth,
        )

    async def _with_quota_notice(
        self, data: UploadDocumentData, auth: AuthContext
    ) -> UploadDocumentData:
        """给 R3 成功响应补上非阻断额度提示（B27 `15006`）。

        为什么是 `data.notice_*` 而不是错误码：超额用户**仍然可以上传**
        （PRD 5.3 只要求"暂停后续处理/提醒"，不要求拒收），而前端 `unwrap()`
        把非 0 `code` 当异常抛——提示走 `code` 会让"上传成功"变成弹错误框。

        单一真源：委托 `UsageService.quota_notice()`（判据与 M4/BL5 同源的
        `over_quota_warning`），本类不重算额度公式。

        Args:
            data: R3 响应数据（已构造完成）。
            auth: 鉴权上下文。

        Returns:
            UploadDocumentData: 带提示字段的响应数据（无提示时原样返回）。
        """
        notice_code, notice_message = await self._usage.quota_notice(
            tenant_id=self._tenant_of(auth), user_id=auth.user_id
        )
        if not notice_code:
            return data
        return data.model_copy(
            update={"notice_code": notice_code, "notice_message": notice_message}
        )

    async def _resolve_base_policy(self, base_id: str, auth: AuthContext) -> ChunkPolicy:
        """取知识库的内容切分精细度（上传未指定时使用）。

        Args:
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            ChunkPolicy: 库级设置；库不在本地元数据中时返回 `default`。
        """
        base = await self._bases.base_exists_for_delete_check(base_id, auth)
        if base is None:
            return ChunkPolicy.DEFAULT
        try:
            return ChunkPolicy(base.chunk_policy)
        except ValueError:
            return ChunkPolicy.DEFAULT

    def _validate_document(self, file_name: str, file_size: int) -> None:
        """校验文档格式与大小。

        错误码语义（架构文档 3.0.3）：
        - `20008`（413，请求体超限）：文件字节数超过传输层上限；
        - `11003`（400，文档内容过多）：解析后的字符数超过处理上限。

        Args:
            file_name: 文件名。
            file_size: 文件字节数。

        Raises:
            ApiError: 格式不支持（11002）、请求体超限（20008）。
        """
        if file_size <= 0:
            raise ApiError(ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED, internal_detail="empty file")
        if file_size > self._settings.max_upload_bytes:
            raise ApiError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                internal_detail=f"file_size={file_size} limit={self._settings.max_upload_bytes}",
            )
        extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        allowed = self._routes.allowed_document_extensions
        if extension not in allowed:
            raise ApiError(
                ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED,
                internal_detail=f"extension not allowed: {extension}",
            )

    def _validate_document_chars(self, char_count: int) -> None:
        """校验文档解析后的字符数上限。

        Args:
            char_count: 解析后的字符数。

        Raises:
            ApiError: 文档内容过多（11003）。
        """
        if char_count > self._settings.max_document_chars:
            raise ApiError(
                ErrorCode.DOCUMENT_TOO_LARGE,
                internal_detail=(
                    f"char_count={char_count} limit={self._settings.max_document_chars}"
                ),
            )

    # ------------------------------------------------------------------
    # R4 索引进度查询
    # ------------------------------------------------------------------
    async def get_index_status(
        self,
        *,
        base_id: str,
        doc_id: str,
        auth: AuthContext | None = None,
    ) -> IndexStatusData:
        """查询文档处理进度（五态 + 三态同时返回）。

        已落库的文档直接读本地记录（桩模式下也如此），未落库的历史文档
        回落到原有桩结果——保证既有回归（如 `kb_1/doc_1`）行为不变。

        Args:
            base_id: 知识库 ID。
            doc_id: 文档 ID。
            auth: 鉴权上下文（用于 per-kb 凭据的租户解析）。

        Returns:
            IndexStatusData: 处理进度（阶段、百分比、预估剩余、大小、上传时间）。

        Raises:
            ApiError: 文档不存在（20004）或库级凭据不可用（11007）。
        """
        base_id = normalize_base_id(base_id)
        tenant_id = self._tenant_of(auth)
        stored = await self._documents.store.get(tenant_id, doc_id)

        if self._settings.mock_mode:
            if stored is not None:
                return self._documents.to_status_data(stored)
            return IndexStatusData(
                document_id=doc_id,
                index_status=IndexStatus.PROCESSING,
                progress=0,
                segment_count=0,
                error_message=None,
            )

        api_key = await self._api_key_for(base_id, auth)
        native_doc_id = doc_id.removeprefix(_DOC_PREFIX)
        response = await self._dify.get(
            f"{_DIFY_DATASETS_PATH}/{base_id.removeprefix(_BASE_PREFIX)}/documents/{native_doc_id}",
            api_key=api_key,
        )
        data = self._transform_index_status(doc_id, response.payload)
        if stored is not None:
            await self._documents.refresh_progress(
                tenant_id=tenant_id,
                document_id=doc_id,
                native_status=str(response.payload.get("indexing_status", "waiting")),
                progress=data.progress,
                segment_count=data.segment_count,
                error_message=data.error_message,
                elapsed_seconds=_elapsed_since(stored.uploaded_at),
            )
            refreshed = await self._documents.store.get(tenant_id, doc_id)
            if refreshed is not None:
                await self._record_doc_process_if_completed(
                    tenant_id=tenant_id, record=refreshed, previous_stage=stored.stage, auth=auth
                )
                return self._documents.to_status_data(refreshed)
        return data

    async def _record_doc_process_if_completed(
        self,
        *,
        tenant_id: str,
        record: Any,
        previous_stage: str,
        auth: AuthContext | None,
    ) -> None:
        """文档处理「刚刚完成」时写一条 `doc_process` 用量事件（D-12）。

        只在**状态跃迁**到 `completed` 时写，且幂等键为 `document_id:run_id`，
        因此重复轮询 R4 不会产生第二条事件；R5 重新处理换新批次才产生新事件。

        Args:
            tenant_id: 租户 ID。
            record: 刷新后的文档记录。
            previous_stage: 刷新前的阶段（用于识别跃迁）。
            auth: 鉴权上下文（上传人缺失时的兜底用户）。
        """
        if record.stage != DocumentStage.COMPLETED.value or previous_stage == record.stage:
            return
        user_id = record.uploader_user_id or (auth.user_id if auth is not None else "")
        if not user_id:
            return
        units = self._usage.estimate_document_units(
            file_size=int(record.file_size or 0),
            segment_count=int(record.segment_count or 0),
        )
        if units <= 0:
            return
        try:
            await self._usage.record_document_processed(
                tenant_id=tenant_id,
                user_id=user_id,
                document_id=record.document_id,
                run_id=record.run_id,
                units=units,
            )
        except Exception as exc:  # 计量失败不得影响进度查询（对账脚本负责发现偏差）
            logger.warning(
                "usage_doc_process_failed",
                document_id=record.document_id,
                detail=type(exc).__name__,
            )

    def _transform_index_status(self, doc_id: str, payload: dict[str, Any]) -> IndexStatusData:
        """将底座原生处理进度转换为自研五态 + 三态。

        Args:
            doc_id: 自研文档 ID。
            payload: 底座原生响应体。

        Returns:
            IndexStatusData: 自研处理进度。

        Raises:
            ApiError: 底座返回 error 状态时按业务语言给出失败原因。
        """
        native_status = str(payload.get("indexing_status", "waiting"))
        status = map_index_status(native_status)
        completed = int(payload.get("completed_segments", 0) or 0)
        total = int(payload.get("total_segments", 0) or 0)
        progress = (
            100 if status == IndexStatus.COMPLETED else int(completed / total * 100) if total else 0
        )
        error_message = None
        if status == IndexStatus.FAILED:
            error_message = "文档解析失败，请确认文件内容后重新上传"
        view = map_stage(native_status, progress)
        return IndexStatusData(
            document_id=doc_id,
            index_status=status,
            stage=view.stage,
            progress=max(min(progress, 100), 0),
            estimated_remaining_seconds=view.estimated_remaining_seconds,
            segment_count=completed,
            error_message=error_message,
        )

    async def list_documents(
        self,
        base_id: str,
        query: Any,
        auth: AuthContext,
    ) -> Any:
        """KB3 文档清单（协议层入口，业务规则在 `DocumentService`）。

        Args:
            base_id: 知识库 ID。
            query: `ListDocumentsQuery` 查询参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[DocumentItem]` 分页结果。
        """
        return await self._documents.list_documents(base_id, query, auth)

    # ------------------------------------------------------------------
    # R5 重建索引
    # ------------------------------------------------------------------
    async def reindex(
        self, *, base_id: str, doc_id: str, auth: AuthContext | None = None
    ) -> ReindexData:
        """触发文档重新处理（阶段回到「正在读取内容」）。

        底座无原生重建 API，T02 由适配层编排「查原文档 → 重新触发处理」；
        文档处于处理中时返回 11006。

        Args:
            base_id: 知识库 ID。
            doc_id: 文档 ID。
            auth: 鉴权上下文（用于 per-kb 凭据的租户解析）。

        Returns:
            ReindexData: 重新处理后的阶段与进度。

        Raises:
            ApiError: 文档正在处理（11006）、文档不存在（20004）或凭据不可用（11007）。
        """
        base_id = normalize_base_id(base_id)
        current = await self.get_index_status(base_id=base_id, doc_id=doc_id, auth=auth)
        if current.index_status == IndexStatus.PROCESSING:
            raise ApiError(
                ErrorCode.DOCUMENT_STATE_CONFLICT,
                internal_detail=f"doc_id={doc_id} still processing",
            )
        logger.info("document_reindex_requested", base_id=base_id, doc_id=doc_id)
        tenant_id = self._tenant_of(auth)
        await self._documents.mark_reprocessing(tenant_id=tenant_id, document_id=doc_id)
        return ReindexData(
            document_id=doc_id,
            index_status=IndexStatus.PROCESSING,
            stage=DocumentStage.PARSING,
            progress=0,
        )

    # ------------------------------------------------------------------
    # R6 删除文档
    # ------------------------------------------------------------------
    async def delete_document(
        self, *, base_id: str, doc_id: str, auth: AuthContext | None = None
    ) -> str:
        """删除文档。

        Args:
            base_id: 知识库 ID。
            doc_id: 文档 ID。
            auth: 鉴权上下文（用于 per-kb 凭据的租户解析）。

        Returns:
            str: 被删除的文档 ID。

        Raises:
            ApiError: 文档正在处理（11006）、文档不存在（20004）或凭据不可用（11007）。
        """
        base_id = normalize_base_id(base_id)
        logger.info("document_delete_requested", base_id=base_id, doc_id=doc_id)
        tenant_id = self._tenant_of(auth)
        actor_id = auth.user_id if auth is not None else ""
        await self._documents.require_deletable(tenant_id=tenant_id, document_id=doc_id)
        if not self._settings.mock_mode:
            api_key = await self._api_key_for(base_id, auth)
            await self._dify.delete(
                f"{_DIFY_DATASETS_PATH}/{base_id.removeprefix(_BASE_PREFIX)}"
                f"/documents/{doc_id.removeprefix(_DOC_PREFIX)}",
                api_key=api_key,
            )
        await self._documents.soft_delete(
            tenant_id=tenant_id, actor_id=actor_id, document_id=doc_id
        )
        return doc_id

    # ------------------------------------------------------------------
    # R7 独立检索
    # ------------------------------------------------------------------
    async def search(self, request: SearchRequest, auth: AuthContext | None = None) -> SearchData:
        """执行知识库检索。

        召回参数经适配层默认策略校准后下发，底座参数不对外暴露（附录 A-3）。
        命中片段附带 `base_id` / `base_name` / `segment_id` 并回填片段映射（D-11）。

        **B27**：底座「连不上 / 超时」（`30001` / `30002`）在此处统一改写为
        `11005 检索失败`（HTTP 200）——R7 的用户语义是"检索服务暂时不可用，
        请重试"，HTTP 503 会让前端弹「服务不可用」并中断知识库问答。
        判权（`20003`）与凭据（`11007`）在改写范围**之外**，原样上抛。

        Args:
            request: 检索请求。
            auth: 鉴权上下文（用于 per-kb 凭据的租户解析与对象级判权）。

        Returns:
            SearchData: 命中片段列表。

        Raises:
            ApiError: 知识库不存在（11001）、无权限（20003）、检索失败（11005）
                或凭据不可用（11007）。
        """
        logger.info(
            "knowledge_search_requested",
            base_id=request.base_id,
            top_k=request.top_k,
            score_threshold=request.score_threshold,
        )
        base_id = normalize_base_id(request.base_id)
        if not base_id:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail="empty base_id after normalization"
            )
        await self._assert_readable(base_id, auth)
        if self._settings.mock_mode:
            return SearchData(segments=[])

        api_key = await self._api_key_for(base_id, auth)
        defaults = self._routes.retrieval_defaults
        try:
            response = await self._dify.post(
                _DIFY_RETRIEVE_PATH.format(dataset_id=base_id.removeprefix(_BASE_PREFIX)),
                {
                    "query": request.query,
                    "retrieval_model": {
                        "search_method": defaults.search_method,
                        "reranking_enable": False,
                        "top_k": min(request.top_k, defaults.top_k * 2),
                        "score_threshold_enabled": True,
                        "score_threshold": max(request.score_threshold, defaults.score_threshold),
                    },
                },
                api_key=api_key,
            )
        except ApiError as exc:
            # 检索失败（11005，HTTP 200）的唯一接线点：
            # 底座「连不上 / 超时」（30001/30002）在 R7 的用户语义是"检索服务暂时
            # 不可用，请重试"，前端可原地重试即可恢复；而 30001/30002 是系统级码，
            # 对外表现为 HTTP 503，会让前端弹「服务不可用」并中断整个知识库问答流程。
            # 因此**只**把这两个码改写为 11005，对齐冻结契约表的 R7 行。
            #
            # 为什么只包这一处：
            # - `_assert_readable()`（20003/11001）与 `_api_key_for()`（11007）在
            #   try **之外**——它们是权限与凭据问题，重试无意义，改写会误导用户；
            # - 其他下游码（429→20006 限流、400→20001 参数错）原样上抛，语义已正确。
            if exc.code in _RETRIEVAL_DOWNSTREAM_CODES:
                raise ApiError(
                    ErrorCode.RETRIEVAL_FAILED,
                    internal_detail=(
                        f"retrieve downstream code={int(exc.code)} {exc.internal_detail or ''}"
                    ).strip(),
                ) from exc
            raise
        data = self._transform_search_result(response.payload)
        return await self._attach_backlinks(data, base_id, auth)

    async def _assert_readable(self, base_id: str, auth: AuthContext | None) -> None:
        """R7 的对象级判权（fail-closed，与列表 / 问答同一套可见性判定）。

        **QA B05-P0-01 修复点**：本地查不到元数据、或元数据已软删除时一律拒绝
        （`20003`），不再「无法判定就放行」。理由：

        - R2 列表本来就只返回元数据内的知识库，历史库早已从列表消失，
          「R7 放行」并没有达成阶段一行为不变，反而形成「列表看不到、检索拿得到」；
        - 同一底座对象存在 `kb_<原生>` 与 `<原生>` 两种写法，放行等于给判权开后门；
        - 跨租户访问时 `store.get(tenant_id, ...)` 必然为 None，放行即跨租户读取。

        调用方已把 ID 归一化为 `kb_` 形态，因此「换一种写法绕过」不成立。

        Args:
            base_id: 已归一化的知识库 ID。
            auth: 鉴权上下文（None 表示服务层无鉴权调用，跳过判权）。

        Raises:
            ApiError: 库不存在 / 已删除 / 无权读取（20003）。
        """
        if auth is None:
            return
        record = await self._bases.store.get(auth.tenant_id, base_id)
        if record is None or record.is_deleted:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                internal_detail=(
                    f"base not found in tenant base_id={base_id} tenant_id={auth.tenant_id}"
                ),
            )
        if not await self._policy.can_read(auth, record):
            raise ApiError(
                ErrorCode.FORBIDDEN,
                internal_detail=f"user_id={auth.user_id} cannot read base_id={base_id}",
            )

    async def _attach_backlinks(
        self, data: SearchData, base_id: str, auth: AuthContext | None
    ) -> SearchData:
        """为命中片段补齐回链字段并回填片段映射。

        Args:
            data: 检索结果。
            base_id: 知识库 ID。
            auth: 鉴权上下文。

        Returns:
            SearchData: 补齐回链字段后的检索结果。
        """
        tenant_id = self._tenant_of(auth)
        base_name = await self._base_name_of(tenant_id, base_id)
        tenant_hits: list[SegmentHit] = []
        enriched: list[SearchSegment] = []
        for index, segment in enumerate(data.segments):
            segment_key = build_key(segment.document_id, index, segment.segment_content)
            enriched.append(
                segment.model_copy(
                    update={
                        "base_id": base_id,
                        "base_name": base_name,
                        "segment_id": segment_key,
                    }
                )
            )
            if segment.document_id:
                tenant_hits.append(
                    SegmentHit(
                        base_id=base_id,
                        document_id=segment.document_id,
                        segment_index=index,
                        content=segment.segment_content,
                        position_page=segment.position.page if segment.position else None,
                        position_paragraph=(
                            segment.position.paragraph if segment.position else None
                        ),
                    )
                )
        # R7 是单次请求：直接写入（≤ top_k 行），失败只记 WARN，不影响检索结果
        await self._segments.record_hits(tenant_id, tenant_hits, schedule=False)
        return data.model_copy(update={"segments": enriched})

    async def _base_name_of(self, tenant_id: str, base_id: str) -> str:
        """取知识库展示名（未登记时返回空串）。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。

        Returns:
            str: 知识库名称。
        """
        record = await self._bases.store.get(tenant_id, base_id)
        return record.name if record is not None else ""

    def _transform_search_result(self, payload: dict[str, Any]) -> SearchData:
        """将底座原生检索结果转换为自研片段列表。

        Args:
            payload: 底座原生响应体。

        Returns:
            SearchData: 自研检索结果。
        """
        records = payload.get("records", [])
        segments: list[SearchSegment] = []
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                segment = record.get("segment", {})
                if not isinstance(segment, dict):
                    segment = {}
                position_raw = segment.get("position", {})
                position = None
                if isinstance(position_raw, dict) and position_raw:
                    position = SegmentPosition(
                        page=int(position_raw["page"]) if position_raw.get("page") else None,
                        paragraph=(
                            int(position_raw["paragraph"])
                            if position_raw.get("paragraph")
                            else None
                        ),
                    )
                segments.append(
                    SearchSegment(
                        document_id=str(record.get("document_id", "")),
                        document_name=str(segment.get("document_name", "")),
                        segment_content=str(segment.get("content", "")),
                        score=float(record.get("score", 0.0)),
                        position=position,
                    )
                )
        return SearchData(segments=segments)


_service: RagService | None = None


def get_rag_service() -> RagService:
    """获取 RAG 服务单例。

    Returns:
        RagService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = RagService()
    return _service


def reset_rag_service() -> None:
    """重置 RAG 服务单例，供测试隔离用例。"""
    global _service
    _service = None
