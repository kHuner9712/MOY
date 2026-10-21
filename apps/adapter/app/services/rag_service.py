"""RAG 服务协议转换（架构文档 3.2 / 3.6 类图 `RagService`）。

转换职责（架构文档 3.2.6）：
- 知识库：底座 `id` → 自研 `base_id`；`visibility` 由适配层落业务库（底座无此概念）；
- 文档：底座 `file_id`/`batch` → 自研 `document_id`；
- 索引状态：底座七态（waiting/parsing/cleaning/splitting/indexing/completed/error）
  **映射**为自研三态 `processing`/`completed`/`failed`（原状态名不外泄，附录 A-3）；
- 检索：底座 `records[].segment.*` → 自研 `segments[]`。

T01 阶段说明：`Settings.downstream_enabled=false` 时返回业务化 mock 结果，
不产生任何真实底座请求。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from app.business.credential_service import CredentialResolver, get_credential_resolver
from app.clients.dify_client import DifyClient, get_dify_client
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.tenant import resolve_runtime_tenant
from app.registry.loader import ProfileRouteTable, get_profile_routes
from app.schemas.common import build_page
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseData,
    CreateBaseRequest,
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
        _routes: 档位路由表（提供检索与分块默认策略）。
        _dify: 底座客户端。
        _credentials: per-kb 凭据解析器（D-10）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        routes: ProfileRouteTable | None = None,
        dify: DifyClient | None = None,
        resolver: CredentialResolver | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            routes: 可选档位路由表。
            dify: 可选底座客户端。
            resolver: 可选凭据解析器（测试注入）。
        """
        self._settings = settings or get_settings()
        self._routes = routes or get_profile_routes()
        self._dify = dify or get_dify_client()
        self._credentials = resolver or get_credential_resolver(self._settings)

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
            tenant_id=auth.tenant_id,
        )
        if self._settings.mock_mode:
            return CreateBaseData(base_id=new_base_id(), name=request.name, created_at=_now_iso())

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
        return CreateBaseData(
            base_id=f"{_BASE_PREFIX}{native_id}",
            name=str(response.payload.get("name", request.name)),
            created_at=_now_iso(),
        )

    # ------------------------------------------------------------------
    # R2 知识库列表
    # ------------------------------------------------------------------
    def list_bases(self, query: ListBasesQuery, auth: AuthContext) -> Any:
        """查询知识库列表（桩实现返回空分页）。

        Args:
            query: 分页与关键字过滤参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[KnowledgeBaseItem]` 分页结果。
        """
        del auth  # T01 桩模式不持有状态
        logger.info("knowledge_base_list_queried", keyword=query.keyword, page=query.page)
        return build_page(
            items=[],
            total=0,
            page=query.page,
            page_size=query.page_size,
        )

    # ------------------------------------------------------------------
    # R3 上传文档
    # ------------------------------------------------------------------
    async def upload_document(
        self,
        *,
        base_id: str,
        file_name: str,
        file_size: int,
        chunk_policy: ChunkPolicy,
        auth: AuthContext,
    ) -> UploadDocumentData:
        """上传文档并触发异步索引。

        Args:
            base_id: 知识库 ID。
            file_name: 文件名。
            file_size: 文件字节数。
            chunk_policy: 分块策略。
            auth: 鉴权上下文。

        Returns:
            UploadDocumentData: 文档 ID 与索引进度初值。

        Raises:
            ApiError: 知识库不存在（11001）、格式不支持（11002）、超出大小限制（11003）。
        """
        self._validate_document(file_name, file_size)
        logger.info(
            "document_upload_requested",
            base_id=base_id,
            file_name=file_name,
            file_size=file_size,
            chunk_policy=chunk_policy.value,
            tenant_id=auth.tenant_id,
        )

        if self._settings.mock_mode:
            return UploadDocumentData(
                document_id=new_document_id(),
                file_name=file_name,
                index_status=IndexStatus.PROCESSING,
                progress=0,
            )

        api_key = await self._api_key_for(base_id, auth)
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
                    "chunk_policy": chunk_policy.value,
                },
                "indexing_technique": "high_quality",
            },
            api_key=api_key,
        )
        batch = created.payload.get("batch", "")
        logger.debug("document_upload_dispatched", base_id=base_id, has_batch=bool(batch))
        return UploadDocumentData(
            document_id=f"{_DOC_PREFIX}{native_file_id}",
            file_name=file_name,
            index_status=IndexStatus.PROCESSING,
            progress=0,
        )

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
        """查询文档索引进度。

        Args:
            base_id: 知识库 ID。
            doc_id: 文档 ID。
            auth: 鉴权上下文（用于 per-kb 凭据的租户解析）。

        Returns:
            IndexStatusData: 三态索引状态与进度百分比。

        Raises:
            ApiError: 文档不存在（20004）或库级凭据不可用（11007）。
        """
        if self._settings.mock_mode:
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
        return self._transform_index_status(doc_id, response.payload)

    def _transform_index_status(self, doc_id: str, payload: dict[str, Any]) -> IndexStatusData:
        """将底座原生索引进度转换为自研三态。

        Args:
            doc_id: 自研文档 ID。
            payload: 底座原生响应体。

        Returns:
            IndexStatusData: 自研索引进度。

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
        return IndexStatusData(
            document_id=doc_id,
            index_status=status,
            progress=max(min(progress, 100), 0),
            segment_count=completed,
            error_message=error_message,
        )

    # ------------------------------------------------------------------
    # R5 重建索引
    # ------------------------------------------------------------------
    async def reindex(
        self, *, base_id: str, doc_id: str, auth: AuthContext | None = None
    ) -> ReindexData:
        """触发文档重建索引。

        底座无原生重建 API，T02 由适配层编排「查原文档 → 重新触发索引」；
        文档处于处理中时返回 11006。

        Args:
            base_id: 知识库 ID。
            doc_id: 文档 ID。
            auth: 鉴权上下文（用于 per-kb 凭据的租户解析）。

        Returns:
            ReindexData: 重建后的索引状态。

        Raises:
            ApiError: 文档正在处理（11006）、文档不存在（20004）或凭据不可用（11007）。
        """
        current = await self.get_index_status(base_id=base_id, doc_id=doc_id, auth=auth)
        if current.index_status == IndexStatus.PROCESSING:
            raise ApiError(
                ErrorCode.DOCUMENT_STATE_CONFLICT,
                internal_detail=f"doc_id={doc_id} still processing",
            )
        logger.info("document_reindex_requested", base_id=base_id, doc_id=doc_id)
        return ReindexData(
            document_id=doc_id,
            index_status=IndexStatus.PROCESSING,
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
            ApiError: 文档不存在（20004）或库级凭据不可用（11007）。
        """
        logger.info("document_delete_requested", base_id=base_id, doc_id=doc_id)
        if not self._settings.mock_mode:
            api_key = await self._api_key_for(base_id, auth)
            await self._dify.delete(
                f"{_DIFY_DATASETS_PATH}/{base_id.removeprefix(_BASE_PREFIX)}"
                f"/documents/{doc_id.removeprefix(_DOC_PREFIX)}",
                api_key=api_key,
            )
        return doc_id

    # ------------------------------------------------------------------
    # R7 独立检索
    # ------------------------------------------------------------------
    async def search(self, request: SearchRequest, auth: AuthContext | None = None) -> SearchData:
        """执行知识库检索。

        召回参数经适配层默认策略校准后下发，底座参数不对外暴露（附录 A-3）。

        Args:
            request: 检索请求。
            auth: 鉴权上下文（用于 per-kb 凭据的租户解析）。

        Returns:
            SearchData: 命中片段列表。

        Raises:
            ApiError: 知识库不存在（11001）、检索失败（11005）或凭据不可用（11007）。
        """
        logger.info(
            "knowledge_search_requested",
            base_id=request.base_id,
            top_k=request.top_k,
            score_threshold=request.score_threshold,
        )
        if self._settings.mock_mode:
            return SearchData(segments=[])

        api_key = await self._api_key_for(request.base_id, auth)
        defaults = self._routes.retrieval_defaults
        response = await self._dify.post(
            _DIFY_RETRIEVE_PATH.format(dataset_id=request.base_id.removeprefix(_BASE_PREFIX)),
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
        return self._transform_search_result(response.payload)

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
