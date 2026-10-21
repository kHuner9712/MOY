"""工具服务端点 T1-T7（架构文档 3.4）。

端点清单：
| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| T1 | `/api/v1/tools/registry` | GET | 查询工具类型注册表 |
| T2 | `/api/v1/tools/connections` | POST | 发起工具授权连接 |
| T3 | `/api/v1/tools/connections` | GET | 已连接工具列表 |
| T4 | `/api/v1/tools/connections/{conn_id}` | DELETE | 断开工具连接 |
| T5 | `/api/v1/tools/execute` | POST | 执行工具操作 |
| T6 | `/api/v1/tools/sync-jobs` | POST | 创建数据同步任务 |
| T7 | `/api/v1/tools/sync-jobs/{job_id}` | GET | 查询同步任务进度 |

**本文件不含任何 `if tool_type == ...` 分支**——新增工具只改注册表与 `tools/`，
T1-T7 端点零改动（架构文档第 4 章 Q1）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.schemas.common import ApiResponse, DeletedData, PageResult
from app.schemas.tool import (
    ConnectionData,
    ConnectionItem,
    CreateConnectionRequest,
    CreateSyncJobRequest,
    ExecuteRequest,
    ExecutionData,
    ListConnectionsQuery,
    RegistryData,
    SyncJobData,
    SyncJobDetailData,
)
from app.services.tool_service import get_tool_service

router = APIRouter(prefix="/tools", tags=["工具服务"])


@router.get(
    "/registry",
    response_model=ApiResponse[RegistryData],
    summary="T1 工具类型注册表",
    description=(
        "查询工具类型注册表（可用工具与能力清单）。前端按本返回动态渲染工具卡片，"
        "不硬编码工具列表；新增工具不改本接口契约。"
    ),
)
async def get_registry(
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[RegistryData]:
    """T1 工具类型注册表。

    Args:
        context: 请求上下文。

    Returns:
        ApiResponse[RegistryData]: 工具类型列表。
    """
    return ApiResponse[RegistryData].ok(get_tool_service().list_registry(context.auth))


@router.post(
    "/connections",
    response_model=ApiResponse[ConnectionData],
    summary="T2 发起工具授权连接",
    description="发起工具授权连接（OAuth/令牌）。写接口，需携带 Idempotency-Key 请求头。",
)
async def create_connection(
    request: Request,
    body: CreateConnectionRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[ConnectionData]:
    """T2 发起工具授权连接。

    Args:
        request: FastAPI 请求对象。
        body: 连接请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[ConnectionData]: 连接 ID 与状态。

    Raises:
        ApiError: 工具类型未注册（13001）、授权码无效（13003）。
    """
    service = get_tool_service()

    async def factory() -> ConnectionData:
        return await service.create_connection(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[ConnectionData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[ConnectionData].ok(result)


@router.get(
    "/connections",
    response_model=ApiResponse[PageResult[ConnectionItem]],
    summary="T3 已连接工具列表",
    description="已连接工具列表（分页，支持工具类型过滤）。",
)
async def list_connections(
    context: RequestContext = Depends(get_request_context),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    tool_type: str = Query("", max_length=64, description="按工具类型过滤"),
) -> ApiResponse[PageResult[ConnectionItem]]:
    """T3 已连接工具列表。

    Args:
        context: 请求上下文。
        page: 页码。
        page_size: 每页条数。
        tool_type: 按工具类型过滤。

    Returns:
        ApiResponse[PageResult[ConnectionItem]]: 分页结果。
    """
    query = ListConnectionsQuery(page=page, page_size=page_size, tool_type=tool_type)
    return ApiResponse[PageResult[ConnectionItem]].ok(
        get_tool_service().list_connections(query, context.auth)
    )


@router.delete(
    "/connections/{conn_id}",
    response_model=ApiResponse[DeletedData],
    summary="T4 断开工具连接",
    description="断开工具连接。写接口，需携带 Idempotency-Key 请求头。",
)
async def delete_connection(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    conn_id: str = Path(..., description="连接 ID", min_length=6, max_length=128),
) -> ApiResponse[DeletedData]:
    """T4 断开工具连接。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        conn_id: 连接 ID。

    Returns:
        ApiResponse[DeletedData]: 删除结果。
    """
    service = get_tool_service()

    async def factory() -> DeletedData:
        deleted_id = await service.delete_connection(conn_id, context.auth)
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
    "/execute",
    response_model=ApiResponse[ExecutionData],
    summary="T5 执行工具操作",
    description=(
        "执行工具操作。操作必须在注册表能力清单内，否则返回 13005；"
        "工具未连接返回 13002。写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def execute_tool(
    request: Request,
    body: ExecuteRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[ExecutionData]:
    """T5 执行工具操作。

    Args:
        request: FastAPI 请求对象。
        body: 执行请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[ExecutionData]: 执行结果。
    """
    service = get_tool_service()

    async def factory() -> ExecutionData:
        return await service.execute(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[ExecutionData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[ExecutionData].ok(result)


@router.post(
    "/sync-jobs",
    response_model=ApiResponse[SyncJobData],
    summary="T6 创建数据同步任务",
    description="创建数据同步任务（如邮件收件箱同步）。写接口，需携带 Idempotency-Key 请求头。",
)
async def create_sync_job(
    request: Request,
    body: CreateSyncJobRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[SyncJobData]:
    """T6 创建数据同步任务。

    Args:
        request: FastAPI 请求对象。
        body: 创建请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[SyncJobData]: 同步任务 ID 与初始状态。
    """
    service = get_tool_service()

    async def factory() -> SyncJobData:
        return await service.create_sync_job(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[SyncJobData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[SyncJobData].ok(result)


@router.get(
    "/sync-jobs/{job_id}",
    response_model=ApiResponse[SyncJobDetailData],
    summary="T7 查询同步任务进度",
    description="查询数据同步任务进度。",
)
async def get_sync_job(
    context: RequestContext = Depends(get_request_context),
    job_id: str = Path(..., description="同步任务 ID", min_length=6, max_length=128),
) -> ApiResponse[SyncJobDetailData]:
    """T7 查询同步任务进度。

    Args:
        context: 请求上下文。
        job_id: 同步任务 ID。

    Returns:
        ApiResponse[SyncJobDetailData]: 同步任务详情。
    """
    return ApiResponse[SyncJobDetailData].ok(get_tool_service().get_sync_job(job_id, context.auth))
