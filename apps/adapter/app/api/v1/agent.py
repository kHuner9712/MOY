"""Agent 服务端点 A1-A6（架构文档 3.3）。

端点清单：
| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| A1 | `/api/v1/agent/tasks` | POST | 创建任务（异步执行） |
| A2 | `/api/v1/agent/tasks` | GET | 任务列表（按会话/租户过滤） |
| A3 | `/api/v1/agent/tasks/{task_id}` | GET | 查询任务状态与结果 |
| A4 | `/api/v1/agent/tasks/{task_id}/steps` | GET | 查询步骤详情 |
| A5 | `/api/v1/agent/tasks/{task_id}/cancel` | POST | 取消任务 |
| A6 | `/api/v1/agent/tasks/{task_id}/events` | GET | SSE 订阅 |
| WF1 | `/api/v1/agent/tasks/{task_id}/retry` | POST | 继续/重做/重试 |
| WF2 | `/api/v1/agent/tasks/{id}/artifacts/{artifact_id}` | PUT | 产物编辑 + 重新导出 |

**双模式**（D-22）：`plugin_id` 非空的 A1 走**本地插件任务**（`TaskService`）；
`plugin_id` 为空时与阶段一逐字等价（`AgentService` 底座代理）。A3/A4/A5 同样按
"任务在本地是否存在"分流——同一组端点上并存两种实现，是阶段三的既定设计，
不是临时兼容层（阶段四会逐步把底座任务也搬到本地）。

本文件只做参数装配与响应封装。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Request
from fastapi.responses import StreamingResponse

from app.business.task_service import TaskService, get_task_service
from app.core.config import Settings, get_request_settings
from app.core.errors import ErrorCode, error_response
from app.core.idempotency import (
    IdempotencyManager,
    get_idempotency_manager,
    hash_request_body,
)
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.plugins.actions import ActionGateway
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
from app.schemas.common import SSE_HEADERS, SSE_MEDIA_TYPE, ApiResponse, PageResult, build_page
from app.schemas.task import (
    ArtifactEditData,
    ArtifactEditRequest,
    RetryTaskData,
    RetryTaskRequest,
)
from app.services.agent_service import get_agent_service

router = APIRouter(prefix="/agent", tags=["Agent 服务"])

#: 幂等键请求头（写接口必须携带，阶段二通用规则）
IDEMPOTENCY_HEADER = "Idempotency-Key"


def _parse_last_event_id(raw: str | None) -> int:
    """解析 `Last-Event-ID` 请求头为回放游标。

    **宽容解析，不报 4xx**：这个头是浏览器/代理按 SSE 规范自动重发的，取值可能
    是空串、带空格，或（代理改写后）非数字。此时唯一正确的动作是**从头发一遍**
    （游标 0）——回放幂等，重复下发不会让状态错；而返回 400 会让"网络抖动后
    页面永久黑屏"，这是更坏的失败方式。

    Args:
        raw: 请求头原始值（可能为 None）。

    Returns:
        int: 回放游标；无法解析或为负时返回 0。
    """
    if not raw:
        return 0
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _action_gateway(settings: Settings) -> ActionGateway:
    """动作门面工厂（组合根职责，审计 P1-6）。

    **为什么工厂在这里而不是在业务层**：门面要装配办公工具连接器与实际下游
    客户端，属于「进程怎么拼起来」的装配知识。放在业务层会让
    `business/task_service.py` 直接 import `api.v1.plugin_deps`——业务层反向
    依赖 HTTP 层，`import app.business.task_service` 会连带拉起整个 `app.api`。
    现在业务层只认 `ActionGateway` 协议，由本函数在**组合根**提供实现。

    **为什么 import 写在函数体内**（而不是模块顶部）：
    ① e2e 的桩注入点是 `app.api.v1.plugin_deps.build_action_gateway`
    （`tests/e2e/conftest.py::use_script` 用 monkeypatch 打在这个**属性**上），
    函数体内导入可保证补丁生效；
    ② 避免 `api.v1.agent` 与 `api.v1.plugin_deps` 在模块加载期互相牵连。

    Args:
        settings: 请求生效配置。

    Returns:
        ActionGateway: 已装配的动作门面实现。
    """
    from app.api.v1.plugin_deps import build_action_gateway

    return build_action_gateway(settings)


def _task_service(request: Request) -> TaskService:
    """按请求生效配置构造任务服务（插件任务分支）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        TaskService: 任务服务。
    """
    settings: Settings = get_request_settings(request)
    return get_task_service(settings, gateway_factory=_action_gateway)


async def _plugin_task_exists(service: TaskService, task_id: str, auth: object) -> bool:
    """判断任务是否为本地的插件任务（双模式分流依据）。

    Args:
        service: 任务服务。
        task_id: 任务 ID。
        auth: 鉴权上下文。

    Returns:
        bool: 是插件任务返回 True。
    """
    tenant_id = getattr(auth, "tenant_id", "")
    store = getattr(service, "_tasks", None)
    if store is None:  # pragma: no cover - 服务总是带仓储
        return False
    return await store.get_task(tenant_id, task_id) is not None


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
    if body.plugin_id:
        tasks = _task_service(request)
        idem_key = request.headers.get(IDEMPOTENCY_HEADER, "")

        async def plugin_factory() -> CreateTaskData:
            """创建插件任务（本地持久化 + 声明式推进）。

            Returns:
                CreateTaskData: 任务 ID、状态与排队位次。
            """
            return await tasks.create_plugin_task(body, context.auth, idem_key=idem_key)

        plugin_result, plugin_replayed = await run_idempotent(
            request,
            context,
            plugin_factory,
            manager,
            body_digest=hash_request_body(body.model_dump_json()),
        )
        if plugin_replayed:
            return ApiResponse[CreateTaskData].fail(
                code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
                message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
                data=plugin_result,
            )
        return ApiResponse[CreateTaskData].ok(plugin_result)

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
    request: Request,
    context: RequestContext = Depends(get_request_context),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    conversation_id: str = Query("", max_length=128, description="按会话过滤"),
    status: TaskStatus | None = Query(None, description="按状态过滤"),
    plugin_id: str = Query("", max_length=44, description="按插件过滤（阶段三加性）"),
    initiator_id: str = Query("", max_length=64, description="按发起人过滤（阶段三加性）"),
    created_from: str = Query("", max_length=40, description="起始时间（阶段三加性）"),
    created_to: str = Query("", max_length=40, description="结束时间（阶段三加性）"),
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
        page=page,
        page_size=page_size,
        conversation_id=conversation_id,
        status=status,
        plugin_id=plugin_id,
        initiator_id=initiator_id,
        created_from=created_from,
        created_to=created_to,
    )
    plugin_page = await _task_service(request).list_tasks(query, context.auth)
    base_page = get_agent_service().list_tasks(query, context.auth)
    if not plugin_page.items:
        # 没有插件任务时**逐字返回阶段一结果**（30 端点回归的等价性靠这条）
        return ApiResponse[PageResult[TaskItem]].ok(base_page)
    seen = {item.task_id for item in plugin_page.items}
    merged = list(plugin_page.items) + [
        item for item in base_page.items if item.task_id not in seen
    ]
    # `PageResult` 只有 `items` + `meta`（`meta.total` 才是总数），
    # 直接读 `.total` 会抛 `AttributeError` → 有插件任务的租户一打开"我的任务"就 500。
    total = plugin_page.meta.total + base_page.meta.total
    start = max(0, (page - 1) * page_size)
    page_items = merged[start : start + page_size]
    return ApiResponse[PageResult[TaskItem]].ok(
        build_page(items=page_items, total=total, page=page, page_size=page_size)
    )


@router.get(
    "/tasks/{task_id}",
    response_model=ApiResponse[TaskDetailData],
    summary="A3 任务状态查询",
    description="查询任务状态与结果。status 为自研五态，底座原状态名不暴露。",
)
async def get_task(
    request: Request,
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
    tasks = _task_service(request)
    if await _plugin_task_exists(tasks, task_id, context.auth):
        return ApiResponse[TaskDetailData].ok(await tasks.get_task(task_id, context.auth))
    return ApiResponse[TaskDetailData].ok(await get_agent_service().get_task(task_id, context.auth))


@router.get(
    "/tasks/{task_id}/steps",
    response_model=ApiResponse[StepsData],
    summary="A4 步骤详情",
    description="查询任务步骤详情（工作流可视化数据源）。步骤标题为业务语言，底座节点类型名不暴露。",
)
async def list_steps(
    request: Request,
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
    tasks = _task_service(request)
    if await _plugin_task_exists(tasks, task_id, context.auth):
        return ApiResponse[StepsData].ok(await tasks.steps(task_id, context.auth))
    return ApiResponse[StepsData].ok(await get_agent_service().list_steps(task_id, context.auth))


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
    tasks = _task_service(request)

    async def factory() -> CancelTaskData:
        if await _plugin_task_exists(tasks, task_id, context.auth):
            detail = await tasks.cancel(task_id, context.auth)
            return CancelTaskData(
                task_id=detail.task_id, status=detail.status, updated_at=detail.updated_at
            )
        detail = await service.cancel_task(task_id, context.auth)
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
        "每帧携带 `id:`（回放游标）；断线后带 `Last-Event-ID` 请求头重连，"
        "服务端只补该游标之后的增量事件（零丢失、零重复）。"
    ),
    response_class=StreamingResponse,
)
async def stream_task_events(
    context: RequestContext = Depends(get_request_context),
    task_id: str = Path(..., description="任务 ID", min_length=6, max_length=128),
    last_event_id: str | None = Header(
        default=None,
        alias="Last-Event-ID",
        description="上次收到的最后一个事件 id（回放游标）；缺省表示从头推送。",
    ),
) -> StreamingResponse:
    """A6 任务事件推送。

    Args:
        context: 请求上下文。
        task_id: 任务 ID。
        last_event_id: `Last-Event-ID` 请求头（断线续传游标）。

    Returns:
        StreamingResponse: SSE 流式响应。

    Raises:
        ApiError: 任务不存在（12001）。在建立 SSE 响应**之前**校验，
            否则响应一旦开始写出便无法再返回统一错误体。
    """
    service = get_agent_service()
    await service.ensure_task_exists(task_id, context.auth)
    return StreamingResponse(
        service.stream_events(
            task_id,
            context.auth,
            after_id=_parse_last_event_id(last_event_id),
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


# ===========================================================================
# WF1 继续 / 重做 / 重试（阶段三 B19，D-19：任务级写端点只有一个）
# ===========================================================================
@router.post(
    "/tasks/{task_id}/retry",
    response_model=ApiResponse[RetryTaskData],
    summary="WF1 重试或继续任务",
    description=(
        "继续被人工接管打断的任务、重做某一步，或从失败处重试。"
        "**继续与重做都在同一个任务上接着跑**（返回 resumed=true，不会产生第二个任务）；"
        "**重试本步/从头重试会新建一个任务**（返回 retry_of 指向原任务，原任务状态不变）。"
        "写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def retry_task(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    body: RetryTaskRequest = Body(...),
    task_id: str = Path(..., description="任务 ID", min_length=6, max_length=128),
) -> ApiResponse[RetryTaskData]:
    """WF1 重试或继续任务。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        body: 重试请求（`decision` 四值 + `from_step`）。
        task_id: 任务 ID。

    Returns:
        ApiResponse[RetryTaskData]: `resumed` 区分续跑与新建。

    Raises:
        ApiError: 任务不存在（12001）、插件停用（16004）、状态不允许（17001）、
            `retry_step` 缺 `from_step`（20001）、无权操作（20003）。
    """
    tasks = _task_service(request)

    async def factory() -> RetryTaskData:
        """执行重试。

        Returns:
            RetryTaskData: 重试结果。
        """
        return await tasks.retry(task_id, body, context.auth)

    result, replayed = await run_idempotent(
        request, context, factory, manager, body_digest=hash_request_body(body.model_dump_json())
    )
    if replayed:
        return ApiResponse[RetryTaskData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message="本次操作已处理，无需重复提交",
            data=result,
        )
    return ApiResponse[RetryTaskData].ok(result)


# ===========================================================================
# WF2 产物编辑 + 重新导出（阶段三 B19，D-25）
# ===========================================================================
@router.put(
    "/tasks/{task_id}/artifacts/{artifact_id}",
    response_model=ApiResponse[ArtifactEditData],
    summary="WF2 编辑产物并重新导出",
    description=(
        "修改稿件、文档、表格类产物的正文并重新导出（版本 +1，旧版本可回看可下载）。"
        "**提交记录与代码文件类产物只读**；保存后会重新脱敏并刷新下载地址。"
        "编辑不改变任务状态、不会重新执行流程。写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def edit_artifact(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    body: ArtifactEditRequest = Body(...),
    task_id: str = Path(..., description="任务 ID", min_length=6, max_length=128),
    artifact_id: str = Path(..., description="产物 ID", min_length=6, max_length=128),
) -> ApiResponse[ArtifactEditData]:
    """WF2 编辑产物。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        body: 编辑请求（正文 + 可选导出格式）。
        task_id: 任务 ID。
        artifact_id: 产物 ID。

    Returns:
        ApiResponse[ArtifactEditData]: 新版本、下载地址与历史版本。

    Raises:
        ApiError: 任务不存在（12001）、产物不存在（20004）、类型只读（20001）、
            非发起人（20003）。
    """
    tasks = _task_service(request)

    async def factory() -> ArtifactEditData:
        """执行编辑。

        Returns:
            ArtifactEditData: 编辑结果。
        """
        return await tasks.edit_artifact(task_id, artifact_id, body, context.auth)

    result, replayed = await run_idempotent(
        request, context, factory, manager, body_digest=hash_request_body(body.model_dump_json())
    )
    if replayed:
        return ApiResponse[ArtifactEditData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message="本次操作已处理，无需重复提交",
            data=result,
        )
    return ApiResponse[ArtifactEditData].ok(result)
