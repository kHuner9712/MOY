"""知识库扩展端点（阶段二 KB1-KB8，架构文档 §4 / 2.2 第 2 条）。

路径前缀固定 `/api/v1/knowledge`（与既有 RAG 域同前缀，不新增版本）：

| # | 端点 | 批 |
| --- | --- | --- |
| KB1 | `PATCH /bases/{base_id}` | B04 |
| KB2 | `DELETE /bases/{base_id}` | B04 |
| KB3 | `GET /bases/{base_id}/documents` | B04 |
| KB4 | `GET /bases/{base_id}/documents/{doc_id}/segments/{segment_id}` | B05 |
| KB5 | `GET /bases/{base_id}/grants` | B05 |
| KB6 | `POST /bases/{base_id}/grants` | B05 |
| KB7 | `DELETE /bases/{base_id}/grants/{grant_id}` | B05 |
| KB8 | `PUT /bases/{base_id}/credential-binding` | B03（本批模型迁入 `schemas/knowledge_ext.py`）|

设计要点：

1. **权限**：统一走 `require_permission`（与 U1 同一套规则）+ **对象级判权**
   （业务层 `KnowledgeBaseService` / `GrantService` / `SegmentService`）；
2. **幂等**：KB1/KB2/KB6/KB7 是写接口，必须携带 `Idempotency-Key`；指纹含请求体摘要
   （有请求体时显式传入 `body_digest`，无请求体走空体固定摘要）；
3. **软删除**：KB2 只做软删除，列表立即不可见、操作记录保留；
4. **分页**：KB3/KB5 一律 `page/page_size + COUNT(*)`，5000 条下第 250 页也稳定；
5. **stale 不是错误**：KB4 的引用失效一律 `200 + code=0 + data.status=stale`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from app.business.credential_service import get_credential_resolver, ref_suffix
from app.business.document_service import DocumentService
from app.business.grant_service import GrantService
from app.business.knowledge_base_service import KnowledgeBaseService
from app.business.segment_service import SegmentService
from app.core.config import get_request_settings
from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager, hash_request_body
from app.core.logging import get_logger
from app.core.security import (
    RequestContext,
    get_request_context,
    require_permission,
    run_idempotent,
)
from app.schemas.common import ApiResponse, PageResult
from app.schemas.knowledge_ext import (
    CreateGrantData,
    CreateGrantRequest,
    CredentialBindingData,
    CredentialBindingRequest,
    DeleteBaseData,
    DeleteGrantData,
    DocumentItem,
    GrantItem,
    ListDocumentsQuery,
    UpdateBaseData,
    UpdateBaseRequest,
)
from app.schemas.rag import DocumentStage
from app.schemas.segment import SegmentDetailData

logger = get_logger(__name__)

#: KB1/KB8 所需权限点（前端 `PERM.KB_UPDATE`）
KB_UPDATE_PERMISSION = "knowledge:base:update"

#: KB2 所需权限点（前端 `PERM.KB_DELETE`）
KB_DELETE_PERMISSION = "knowledge:base:delete"

#: KB3/KB4 所需权限点（前端 `PERM.KB_READ`）
KB_READ_PERMISSION = "knowledge:base:read"

#: KB5-KB7 所需权限点（前端 `PERM.KB_GRANT`）
KB_GRANT_PERMISSION = "knowledge:grant:manage"

router = APIRouter(prefix="/knowledge", tags=["RAG 服务"])


def _knowledge_base_service(request: Request) -> KnowledgeBaseService:
    """按请求生效配置构造知识库业务服务。

    配置取自 `app.state.settings`（注入优先），与鉴权链路同一口径，
    避免 `create_app(settings=...)` 注入的配置被全局单例覆盖。

    Args:
        request: FastAPI 请求对象。

    Returns:
        KnowledgeBaseService: 业务服务实例。
    """
    return KnowledgeBaseService(get_request_settings(request))


def _document_service(request: Request) -> DocumentService:
    """按请求生效配置构造文档业务服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        DocumentService: 业务服务实例（复用同一份知识库服务做可见性判权）。
    """
    settings = get_request_settings(request)
    return DocumentService(settings, bases=KnowledgeBaseService(settings))


def _grant_service(request: Request) -> GrantService:
    """按请求生效配置构造授权业务服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        GrantService: 授权业务服务实例。
    """
    return GrantService(get_request_settings(request))


def _segment_service(request: Request) -> SegmentService:
    """按请求生效配置构造片段映射业务服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        SegmentService: 片段映射业务服务实例。
    """
    return SegmentService(get_request_settings(request))


@router.patch(
    "/bases/{base_id}",
    response_model=ApiResponse[UpdateBaseData],
    summary="KB1 修改知识库设置",
    description=(
        "修改知识库的名称、描述、内容切分精细度或可见范围（至少提供一项）。"
        "修改内容切分精细度仅对之后上传的文档生效，已处理文档需在文档行按新设置重新处理。"
        "写接口，需携带 Idempotency-Key 请求头；需 knowledge:base:update 权限。"
    ),
)
async def update_base(
    request: Request,
    body: UpdateBaseRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
) -> ApiResponse[UpdateBaseData]:
    """KB1 修改知识库设置。

    Args:
        request: FastAPI 请求对象。
        body: 更新请求。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。

    Returns:
        ApiResponse[UpdateBaseData]: 更新后的库级设置。

    Raises:
        ApiError: 权限不足（20003）、知识库不存在（11001）、参数非法（20001）。
    """
    require_permission(context.auth, KB_UPDATE_PERMISSION)
    service = _knowledge_base_service(request)

    async def factory() -> UpdateBaseData:
        return await service.update(base_id, body, context.auth)

    # 指纹必须含请求体摘要：同键改不同字段时不得静默回放首次结果
    digest = hash_request_body(body.model_dump_json())
    result, replayed = await run_idempotent(request, context, factory, manager, body_digest=digest)
    if replayed:
        return ApiResponse[UpdateBaseData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[UpdateBaseData].ok(result)


@router.delete(
    "/bases/{base_id}",
    response_model=ApiResponse[DeleteBaseData],
    summary="KB2 删除知识库",
    description=(
        "删除知识库（软删除）：列表内立即不可见，其下文档一并移除，操作记录保留。"
        "仅知识库创建者或组织管理员可删除。写接口，需携带 Idempotency-Key 请求头；"
        "需 knowledge:base:delete 权限。"
    ),
)
async def delete_base(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
) -> ApiResponse[DeleteBaseData]:
    """KB2 删除知识库。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。

    Returns:
        ApiResponse[DeleteBaseData]: 删除结果（含一并移除的文档数）。

    Raises:
        ApiError: 权限不足（20003）、知识库不存在（11001）、重复删除（20005）。
    """
    require_permission(context.auth, KB_DELETE_PERMISSION)
    service = _knowledge_base_service(request)

    async def factory() -> DeleteBaseData:
        return await service.soft_delete(base_id, context.auth)

    # DELETE 无请求体：走空体固定摘要（架构文档 3.0.5 要求摘要项不得省略）
    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[DeleteBaseData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[DeleteBaseData].ok(result)


@router.get(
    "/bases/{base_id}/documents",
    response_model=ApiResponse[PageResult[DocumentItem]],
    summary="KB3 文档清单",
    description=(
        "知识库下的文档清单（分页）。支持文档名称关键字与处理阶段筛选，"
        "支持按上传时间排序（uploaded_at:desc / uploaded_at:asc）。"
        "需 knowledge:base:read 权限。"
    ),
)
async def list_documents(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    keyword: str = Query("", max_length=128, description="文档名称关键字"),
    stage: DocumentStage | None = Query(None, description="处理阶段筛选"),
    sort: str = Query("uploaded_at:desc", max_length=64, description="排序，字段:方向"),
) -> ApiResponse[PageResult[DocumentItem]]:
    """KB3 文档清单。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        base_id: 知识库 ID。
        page: 页码。
        page_size: 每页条数。
        keyword: 文档名称关键字。
        stage: 处理阶段筛选。
        sort: 排序串。

    Returns:
        ApiResponse[PageResult[DocumentItem]]: 分页结果。

    Raises:
        ApiError: 权限不足（20003）、知识库不存在（11001）。
    """
    require_permission(context.auth, KB_READ_PERMISSION)
    query = ListDocumentsQuery(
        page=page, page_size=page_size, keyword=keyword, stage=stage, sort=sort
    )
    service = _document_service(request)
    result = await service.list_documents(base_id, query, context.auth)
    return ApiResponse[PageResult[DocumentItem]].ok(result)


@router.put(
    "/bases/{base_id}/credential-binding",
    response_model=ApiResponse[CredentialBindingData],
    summary="KB8 更新知识库凭据绑定",
    description=(
        "为知识库绑定（或换绑）下游访问凭据。换绑后下一个请求即生效，无需重启服务。"
        "写接口，需携带 Idempotency-Key 请求头；需 knowledge:base:update 权限。"
    ),
)
async def update_credential_binding(
    request: Request,
    body: CredentialBindingRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
) -> ApiResponse[CredentialBindingData]:
    """KB8 更新知识库凭据绑定。

    Args:
        request: FastAPI 请求对象。
        body: 绑定请求。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。

    Returns:
        ApiResponse[CredentialBindingData]: 绑定结果（不含任何密钥信息）。

    Raises:
        ApiError: 权限不足（20003）、凭据不可用（11007）、知识库不存在（11001）。
    """
    require_permission(context.auth, KB_UPDATE_PERMISSION)
    settings = get_request_settings(request)
    resolver = get_credential_resolver(settings)
    credential_ref = body.credential_ref.strip()

    async def factory() -> CredentialBindingData:
        updated_at = await resolver.bind(base_id, credential_ref, tenant_id=context.tenant_id)
        logger.info(
            "credential_binding_requested",
            base_id=base_id,
            credential_ref_suffix=ref_suffix(credential_ref),
            tenant_id=context.tenant_id,
        )
        return CredentialBindingData(base_id=base_id, bound=True, updated_at=updated_at)

    # 幂等指纹必须含请求体摘要（架构文档 3.0.5）：KB8 是「同 path 不同 body」的
    # 典型接口，指纹漏掉 body_digest 就会出现「用户看到成功但凭据没换」。
    digest = hash_request_body(body.model_dump_json())
    result, replayed = await run_idempotent(request, context, factory, manager, body_digest=digest)
    if replayed:
        return ApiResponse[CredentialBindingData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[CredentialBindingData].ok(result)


@router.get(
    "/bases/{base_id}/documents/{doc_id}/segments/{segment_id}",
    response_model=ApiResponse[SegmentDetailData],
    summary="KB4 引用片段详情（回链）",
    description=(
        "按引用条携带的片段标识取原文片段与位置（含前后各一段上下文）。"
        "引用来自旧版本内容时返回 `code=0` 且 `status=stale`（**不是错误**），"
        "前端据此提示「该引用来自旧版本内容，原文已更新」。需 knowledge:base:read 权限。"
    ),
)
async def get_segment_detail(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    doc_id: str = Path(..., description="文档 ID", min_length=3, max_length=128),
    segment_id: str = Path(..., description="片段标识", min_length=4, max_length=64),
    with_context: bool = Query(True, description="是否返回前后各一段上下文"),
) -> ApiResponse[SegmentDetailData]:
    """KB4 引用片段详情。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        base_id: 知识库 ID。
        doc_id: 文档 ID。
        segment_id: 片段稳定标识。
        with_context: 是否返回上下文。

    Returns:
        ApiResponse[SegmentDetailData]: 引用详情（stale 也是成功响应）。

    Raises:
        ApiError: 权限不足（20003）、库不存在（11001）、文档不可定位（11004）。
    """
    require_permission(context.auth, KB_READ_PERMISSION)
    data = await _segment_service(request).get_detail(
        base_id=base_id,
        document_id=doc_id,
        segment_key=segment_id,
        with_context=with_context,
        auth=context.auth,
    )
    return ApiResponse[SegmentDetailData].ok(data)


@router.get(
    "/bases/{base_id}/grants",
    response_model=ApiResponse[PageResult[GrantItem]],
    summary="KB5 知识库授权清单",
    description=(
        "查询知识库的成员授权清单（分页）。条目含授权对象、对象名称、级别、授权人与时间。"
        "需 knowledge:grant:manage 权限，且仅知识库所有者或组织管理员可查看。"
    ),
)
async def list_grants(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
) -> ApiResponse[PageResult[GrantItem]]:
    """KB5 授权清单。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        base_id: 知识库 ID。
        page: 页码。
        page_size: 每页条数。

    Returns:
        ApiResponse[PageResult[GrantItem]]: 分页结果。

    Raises:
        ApiError: 权限不足（20003）、库不存在（11001）。
    """
    require_permission(context.auth, KB_GRANT_PERMISSION)
    result = await _grant_service(request).list_grants(base_id, page, page_size, context.auth)
    return ApiResponse[PageResult[GrantItem]].ok(result)


@router.post(
    "/bases/{base_id}/grants",
    response_model=ApiResponse[CreateGrantData],
    summary="KB6 批量添加成员授权",
    description=(
        "为知识库批量添加授权（成员 / 部门 / 角色，单次最多 50 个）。"
        "重复授权计入 `skipped_count` 且不报错；添加后该库可见范围收敛为「指定成员可见」。"
        "写接口，需携带 Idempotency-Key 请求头；需 knowledge:grant:manage 权限。"
    ),
)
async def create_grants(
    request: Request,
    body: CreateGrantRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
) -> ApiResponse[CreateGrantData]:
    """KB6 批量添加授权。

    Args:
        request: FastAPI 请求对象。
        body: 批量授权请求。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。

    Returns:
        ApiResponse[CreateGrantData]: 新建与跳过条数。

    Raises:
        ApiError: 权限不足（20003）、库不存在（11001）、参数非法（20001）。
    """
    require_permission(context.auth, KB_GRANT_PERMISSION)
    service = _grant_service(request)

    async def factory() -> CreateGrantData:
        return await service.create_grants(base_id, body, context.auth)

    # 指纹必须含请求体摘要：同键改授权对象时不得静默回放首次结果
    digest = hash_request_body(body.model_dump_json())
    result, replayed = await run_idempotent(request, context, factory, manager, body_digest=digest)
    if replayed:
        return ApiResponse[CreateGrantData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[CreateGrantData].ok(result)


@router.delete(
    "/bases/{base_id}/grants/{grant_id}",
    response_model=ApiResponse[DeleteGrantData],
    summary="KB7 撤销成员授权",
    description=(
        "撤销一条成员授权（撤销立即生效，被撤销成员下一次请求返回无权限）。"
        "写接口，需携带 Idempotency-Key 请求头；需 knowledge:grant:manage 权限。"
    ),
)
async def revoke_grant(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
    grant_id: str = Path(..., description="授权记录 ID", min_length=4, max_length=128),
) -> ApiResponse[DeleteGrantData]:
    """KB7 撤销授权。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。
        grant_id: 授权记录 ID。

    Returns:
        ApiResponse[DeleteGrantData]: 撤销结果。

    Raises:
        ApiError: 权限不足（20003）、库不存在（11001）、授权不存在（20004）。
    """
    require_permission(context.auth, KB_GRANT_PERMISSION)
    service = _grant_service(request)

    async def factory() -> DeleteGrantData:
        return await service.revoke_grant(base_id, grant_id, context.auth)

    # DELETE 无请求体：走空体固定摘要（架构文档 3.0.5 要求摘要项不得省略）
    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[DeleteGrantData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[DeleteGrantData].ok(result)


__all__ = [
    "KB_DELETE_PERMISSION",
    "KB_GRANT_PERMISSION",
    "KB_READ_PERMISSION",
    "KB_UPDATE_PERMISSION",
    "CredentialBindingData",
    "CredentialBindingRequest",
    "create_grants",
    "get_segment_detail",
    "list_grants",
    "revoke_grant",
    "router",
    "update_credential_binding",
]
