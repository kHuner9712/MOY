"""Agent 服务端点 A1-A6（架构文档 3.3）。

端点清单：
| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| A1 | `/api/v1/agent/tasks` | POST | 创建任务（异步执行） |
| A2 | `/api/v1/agent/tasks` | GET | 任务列表（按会话/租户过滤） |
| A3 | `/api/v1/agent/tasks/{task_id}` | GET | 查询任务状态与结果 |
| A4 | `/api/v1/agent/tasks/{task_id}/steps` | GET | 查询步骤详情 |
| A5 | `/api/v1/agent/tasks/{task_id}/cancel` | POST | 取消任务 |
| A6 | `/api/v1/agent/tasks/{task_id}/events` | GET | SSE 订阅任务进度 |

本文件只做参数装配与响应封装，协议转换全部在 `services/agent_service.py`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.schemas.agent import (
    CancelTaskData,
    CreateTaskData,
    CreateTaskRequest,
    ListTasksQuery,
    StepsData,
    TaskDetailData,
    TaskItem,
    TaskStatus,
)
from app.schemas.common import SSE_HEADERS, SSE_MEDIA_TYPE, ApiResponse, PageResult
from app.services.agent_service import get_agent_service

router = APIRouter(prefix="/agent", tags=["Agent 服务"])


@router.post(
    "/tasks",
    response_model=ApiResponse[CreateTaskData],
    summary="A1 创建任务",
    description="创建 Agent 任务（异步执行）。写接口，需携带 Idempotency-Key 请求头。",
)
async def create_task(
    request: Request,
    body: CreateTaskRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[CreateTaskData]:
    """A1 创建任务。

    Args:
        request: FastAPI 请求对象。
        body: 创建请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[CreateTaskData]: 任务 ID 与初始状态。
    """
    service = get_agent_service()

    async def factory() -> CreateTaskData:
        return await service.create_task(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[CreateTaskData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[CreateTaskData].ok(result)


@router.get(
    "/tasks",
    response_model=ApiResponse[PageResult[TaskItem]],
    summary="A2 任务列表",
    description="任务列表（按会话/状态过滤，分页）。",
)
async def list_tasks(
    context: RequestContext = Depends(get_request_context),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    conversation_id: str = Query("", max_length=128, description="按会话过滤"),
    status: TaskStatus | None = Query(None, description="按状态过滤"),
) -> ApiResponse[PageResult[TaskItem]]:
    """A2 任务列表。

    Args:
        context: 请求上下文。
        page: 页码。
        page_size: 每页条数。
        conversation_id: 按会话过滤。
        status: 按状态过滤。

    Returns:
        ApiResponse[PageResult[TaskItem]]: 分页结果。
    """
    query = ListTasksQuery(
        page=page, page_size=page_size, conversation_id=conversation_id, status=status
    )
    return ApiResponse[PageResult[TaskItem]].ok(get_agent_service().list_tasks(query, context.auth))


@router.get(
    "/tasks/{task_id}",
    response_model=ApiResponse[TaskDetailData],
    summary="A3 任务状态查询",
    description="查询任务状态与结果。status 为自研五态，底座原状态名不暴露。",
)
async def get_task(
    context: RequestContext = Depends(get_request_context),
    task_id: str = Path(..., description="任务 ID", min_length=6, max_length=128),
) -> ApiResponse[TaskDetailData]:
    """A3 任务状态查询。

    Args:
        context: 请求上下文。
        task_id: 任务 ID。

    Returns:
        ApiResponse[TaskDetailData]: 任务详情。
    """
    del context
    return ApiResponse[TaskDetailData].ok(await get_agent_service().get_task(task_id))


@router.get(
    "/tasks/{task_id}/steps",
    response_model=ApiResponse[StepsData],
    summary="A4 步骤详情",
    description="查询任务步骤详情（工作流可视化数据源）。步骤标题为业务语言，底座节点类型名不暴露。",
)
async def list_steps(
    context: RequestContext = Depends(get_request_context),
    task_id: str = Path(..., description="任务 ID", min_length=6, max_length=128),
) -> ApiResponse[StepsData]:
    """A4 步骤详情。

    Args:
        context: 请求上下文。
        task_id: 任务 ID。

    Returns:
        ApiResponse[StepsData]: 步骤列表。
    """
    del context
    return ApiResponse[StepsData].ok(await get_agent_service().list_steps(task_id))


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=ApiResponse[CancelTaskData],
    summary="A5 取消任务",
    description="取消任务。任务已处于终态时返回 12003。写接口，需携带 Idempotency-Key 请求头。",
)
async def cancel_task(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    task_id: str = Path(..., description="任务 ID", min_length=6, max_length=128),
) -> ApiResponse[CancelTaskData]:
    """A5 取消任务。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        task_id: 任务 ID。

    Returns:
        ApiResponse[CancelTaskData]: 取消后的任务状态。
    """
    service = get_agent_service()

    async def factory() -> CancelTaskData:
        detail = await service.cancel_task(task_id)
        return CancelTaskData(
            task_id=detail.task_id, status=detail.status, updated_at=detail.updated_at
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[CancelTaskData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[CancelTaskData].ok(result)


@router.get(
    "/tasks/{task_id}/events",
    summary="A6 任务事件推送（SSE）",
    description=(
        "订阅任务进度推送，响应 Content-Type 为 text/event-stream。"
        "自研事件类型：step_started / step_finished / task_completed。"
    ),
    response_class=StreamingResponse,
)
async def stream_task_events(
    context: RequestContext = Depends(get_request_context),
    task_id: str = Path(..., description="任务 ID", min_length=6, max_length=128),
) -> StreamingResponse:
    """A6 任务事件推送。

    Args:
        context: 请求上下文。
        task_id: 任务 ID。

    Returns:
        StreamingResponse: SSE 流式响应。

    Raises:
        ApiError: 任务不存在（12001）。在建立 SSE 响应**之前**校验，
            否则响应一旦开始写出便无法再返回统一错误体。
    """
    del context
    service = get_agent_service()
    await service.ensure_task_exists(task_id)
    return StreamingResponse(
        service.stream_events(task_id),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
