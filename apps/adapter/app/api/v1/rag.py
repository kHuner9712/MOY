"""RAG 服务端点 R1-R7（架构文档 3.2）。

端点清单：
| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| R1 | `/api/v1/knowledge/bases` | POST | 创建知识库 |
| R2 | `/api/v1/knowledge/bases` | GET | 知识库列表（分页） |
| R3 | `/api/v1/knowledge/bases/{base_id}/documents` | POST | 上传文档（multipart，异步索引） |
| R4 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | GET | 查询文档索引进度 |
| R5 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}/reindex` | POST | 重建索引 |
| R6 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | DELETE | 删除文档 |
| R7 | `/api/v1/knowledge/search` | POST | 独立检索（带引用） |

本文件只做参数装配与响应封装，协议转换全部在 `services/rag_service.py`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, UploadFile

from app.core.config import get_settings
from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.schemas.common import ApiResponse, DeletedData, PageResult
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseData,
    CreateBaseRequest,
    IndexStatusData,
    KnowledgeBaseItem,
    ListBasesQuery,
    ReindexData,
    SearchData,
    SearchRequest,
    UploadDocumentData,
)
from app.services.rag_service import get_rag_service

router = APIRouter(prefix="/knowledge", tags=["RAG 服务"])


@router.post(
    "/bases",
    response_model=ApiResponse[CreateBaseData],
    summary="R1 创建知识库",
    description="创建知识库。写接口，需携带 Idempotency-Key 请求头。",
)
async def create_base(
    request: Request,
    body: CreateBaseRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[CreateBaseData]:
    """R1 创建知识库。

    Args:
        request: FastAPI 请求对象。
        body: 创建请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[CreateBaseData]: 知识库信息。
    """
    service = get_rag_service()

    async def factory() -> CreateBaseData:
        return await service.create_base(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[CreateBaseData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[CreateBaseData].ok(result)


@router.get(
    "/bases",
    response_model=ApiResponse[PageResult[KnowledgeBaseItem]],
    summary="R2 知识库列表",
    description="知识库列表（分页，支持名称关键字过滤）。",
)
async def list_bases(
    context: RequestContext = Depends(get_request_context),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    keyword: str = Query("", max_length=128, description="名称关键字"),
) -> ApiResponse[PageResult[KnowledgeBaseItem]]:
    """R2 知识库列表。

    Args:
        context: 请求上下文。
        page: 页码。
        page_size: 每页条数。
        keyword: 名称关键字。

    Returns:
        ApiResponse[PageResult[KnowledgeBaseItem]]: 分页结果。
    """
    query = ListBasesQuery(page=page, page_size=page_size, keyword=keyword)
    return ApiResponse[PageResult[KnowledgeBaseItem]].ok(
        get_rag_service().list_bases(query, context.auth)
    )


@router.post(
    "/bases/{base_id}/documents",
    response_model=ApiResponse[UploadDocumentData],
    summary="R3 上传文档",
    description=(
        "上传文档（multipart/form-data，字段 file），异步构建索引。"
        "支持 pdf/docx/xlsx/md/txt，单文件上限 50MB。写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def upload_document(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    file: UploadFile = File(..., description="待上传文档"),
    chunk_policy: ChunkPolicy = Form(ChunkPolicy.DEFAULT, description="分块策略"),
) -> ApiResponse[UploadDocumentData]:
    """R3 上传文档。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。
        file: 上传文件。
        chunk_policy: 分块策略。

    Returns:
        ApiResponse[UploadDocumentData]: 文档 ID 与索引进度初值。

    Raises:
        ApiError: 文件超限（20008）、格式不支持（11002）、内容过大（11003）。
    """
    settings = get_settings()
    file_name = file.filename or "unnamed"
    payload = await file.read(settings.max_upload_bytes + 1)
    file_size = len(payload)

    service = get_rag_service()

    async def factory() -> UploadDocumentData:
        return await service.upload_document(
            base_id=base_id,
            file_name=file_name,
            file_size=file_size,
            chunk_policy=chunk_policy,
            auth=context.auth,
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[UploadDocumentData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[UploadDocumentData].ok(result)


@router.get(
    "/bases/{base_id}/documents/{doc_id}",
    response_model=ApiResponse[IndexStatusData],
    summary="R4 索引进度查询",
    description="查询文档索引进度。index_status 为自研三态，底座原状态名不暴露。",
)
async def get_document_status(
    context: RequestContext = Depends(get_request_context),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    doc_id: str = Path(..., description="文档 ID", min_length=4, max_length=128),
) -> ApiResponse[IndexStatusData]:
    """R4 索引进度查询。

    Args:
        context: 请求上下文。
        base_id: 知识库 ID。
        doc_id: 文档 ID。

    Returns:
        ApiResponse[IndexStatusData]: 索引进度。
    """
    del context
    data = await get_rag_service().get_index_status(base_id=base_id, doc_id=doc_id)
    return ApiResponse[IndexStatusData].ok(data)


@router.post(
    "/bases/{base_id}/documents/{doc_id}/reindex",
    response_model=ApiResponse[ReindexData],
    summary="R5 重建索引",
    description="重建文档索引。文档正在处理中时返回 11006。写接口，需携带 Idempotency-Key 请求头。",
)
async def reindex_document(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    doc_id: str = Path(..., description="文档 ID", min_length=4, max_length=128),
) -> ApiResponse[ReindexData]:
    """R5 重建索引。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。
        doc_id: 文档 ID。

    Returns:
        ApiResponse[ReindexData]: 重建后的索引状态。
    """
    service = get_rag_service()

    async def factory() -> ReindexData:
        return await service.reindex(base_id=base_id, doc_id=doc_id)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[ReindexData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[ReindexData].ok(result)


@router.delete(
    "/bases/{base_id}/documents/{doc_id}",
    response_model=ApiResponse[DeletedData],
    summary="R6 删除文档",
    description="删除文档。写接口，需携带 Idempotency-Key 请求头。",
)
async def delete_document(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    doc_id: str = Path(..., description="文档 ID", min_length=4, max_length=128),
) -> ApiResponse[DeletedData]:
    """R6 删除文档。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。
        doc_id: 文档 ID。

    Returns:
        ApiResponse[DeletedData]: 删除结果。
    """
    service = get_rag_service()

    async def factory() -> DeletedData:
        deleted_id = await service.delete_document(base_id=base_id, doc_id=doc_id)
        return DeletedData(id=deleted_id, deleted=True)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[DeletedData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[DeletedData].ok(result)


@router.post(
    "/search",
    response_model=ApiResponse[SearchData],
    summary="R7 独立检索",
    description=(
        "独立知识库检索（带引用）。对话内增强由 M1 触发。写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def search(
    request: Request,
    body: SearchRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[SearchData]:
    """R7 独立检索。

    Args:
        request: FastAPI 请求对象。
        body: 检索请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[SearchData]: 命中片段列表。
    """
    service = get_rag_service()

    async def factory() -> SearchData:
        return await service.search(body)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[SearchData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[SearchData].ok(result)
