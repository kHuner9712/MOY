"""RAG 服务端点 R1-R7（架构文档 3.2）。

端点清单：
| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| R1 | `/api/v1/knowledge/bases` | POST | 创建知识库 |
| R2 | `/api/v1/knowledge/bases` | GET | 知识库列表（分页） |
| R3 | `/api/v1/knowledge/bases/{base_id}/documents` | POST | 上传文档（multipart，异步处理） |
| R4 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | GET | 查询文档处理进度 |
| R5 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}/reindex` | POST | 重新处理 |
| R6 | `/api/v1/knowledge/bases/{base_id}/documents/{doc_id}` | DELETE | 删除文档 |
| R7 | `/api/v1/knowledge/search` | POST | 独立检索（带引用） |

本文件只做参数装配与响应封装，协议转换全部在 `services/rag_service.py`。

B04 变更边界：**路径零改动**，仅透传新增字段——
R1 请求/响应增加库级 `chunk_policy`；R2 输出 `chunk_policy` / `my_role` / `updated_at`；
R3 的 `chunk_policy` 改为可选（不给即按库级设置处理）并输出 `stage`；
R4 输出 `stage` / `estimated_remaining_seconds` / `file_size` / `uploaded_at`。
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, UploadFile

from app.core.config import get_request_settings
from app.core.errors import ApiError, ErrorCode, error_response
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
    description=(
        "知识库列表（分页，支持名称关键字过滤），含内容切分精细度与当前用户角色。"
        "可选 `base_id` 用于精确查单个知识库（前端详情页定位）；"
        "不可见或不存在都返回空列表，不返回 404/403。"
    ),
)
async def list_bases(
    context: RequestContext = Depends(get_request_context),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    keyword: str = Query("", max_length=128, description="名称关键字"),
    base_id: str = Query(
        "",
        max_length=128,
        description="精确查询的知识库 ID（加性参数；留空表示按列表返回）",
    ),
) -> ApiResponse[PageResult[KnowledgeBaseItem]]:
    """R2 知识库列表（支持按 `base_id` 精确查询）。

    Args:
        context: 请求上下文。
        page: 页码。
        page_size: 每页条数。
        keyword: 名称关键字。
        base_id: 精确查询的知识库 ID；留空表示按列表返回。

    Returns:
        ApiResponse[PageResult[KnowledgeBaseItem]]: 分页结果（`base_id` 命中时 0 或 1 条）。
    """
    query = ListBasesQuery(page=page, page_size=page_size, keyword=keyword, base_id=base_id)
    result = await get_rag_service().list_bases(query, context.auth)
    return ApiResponse[PageResult[KnowledgeBaseItem]].ok(result)


@router.post(
    "/bases/{base_id}/documents",
    response_model=ApiResponse[UploadDocumentData],
    summary="R3 上传文档",
    description=(
        "上传文档（multipart/form-data，字段 file），异步处理。"
        "支持 pdf/docx/xlsx/md/txt，单文件上限 50MB。"
        "内容切分精细度缺省时按知识库设置处理（单次覆盖不改变库级设置）。"
        "写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def upload_document(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    file: UploadFile = File(..., description="待上传文档"),
    chunk_policy: ChunkPolicy | None = Form(
        None, description="内容切分精细度（缺省按知识库设置）：default 标准 / fine 精细"
    ),
) -> ApiResponse[UploadDocumentData]:
    """R3 上传文档。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。
        file: 上传文件。
        chunk_policy: 内容切分精细度（缺省按知识库设置）。

    Returns:
        ApiResponse[UploadDocumentData]: 文档 ID 与处理进度初值。

    Raises:
        ApiError: 文件超限（20008）、格式不支持（11002）、内容过大（11003）。
    """
    settings = get_request_settings(request)
    file_name = file.filename or "unnamed"
    # 多读 1 字节用于判断是否超过传输层上限（20008）
    payload = await file.read(settings.max_upload_bytes + 1)
    file_size = len(payload)
    if file_size > settings.max_upload_bytes:
        raise ApiError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            internal_detail=(f"file_size={file_size} limit={settings.max_upload_bytes}"),
        )

    service = get_rag_service()

    async def factory() -> UploadDocumentData:
        return await service.upload_document(
            base_id=base_id,
            file_name=file_name,
            file_size=file_size,
            auth=context.auth,
            chunk_policy=chunk_policy,
        )

    # multipart 请求体已被表单解析器消费，无法二次读取；
    # 以「文件名 + 文件内容摘要」作为幂等指纹的请求体摘要项（架构文档 3.0.5）
    file_digest = hashlib.sha256(
        f"{file_name}:{file_size}:{hashlib.sha256(payload).hexdigest()}".encode()
    ).hexdigest()

    result, replayed = await run_idempotent(
        request, context, factory, manager, body_digest=file_digest
    )
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
    summary="R4 文档处理进度查询",
    description=(
        "查询文档处理进度。同时返回三态进度与五态阶段（两者语义一致），"
        "处理中给出预估剩余秒数（无法预估时为 null）。"
    ),
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
    data = await get_rag_service().get_index_status(
        base_id=base_id, doc_id=doc_id, auth=context.auth
    )
    return ApiResponse[IndexStatusData].ok(data)


@router.post(
    "/bases/{base_id}/documents/{doc_id}/reindex",
    response_model=ApiResponse[ReindexData],
    summary="R5 重新处理文档",
    description=(
        "按当前设置重新处理文档，阶段回到「正在读取内容」。"
        "文档正在处理中时返回 11006。写接口，需携带 Idempotency-Key 请求头。"
    ),
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
        return await service.reindex(base_id=base_id, doc_id=doc_id, auth=context.auth)

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
        deleted_id = await service.delete_document(
            base_id=base_id, doc_id=doc_id, auth=context.auth
        )
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
        return await service.search(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[SearchData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[SearchData].ok(result)
