"""模型服务端点 M1-M4（架构文档 3.1）。

端点清单：
| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| M1 | `/api/v1/model/generate` | POST | 单轮/多轮非流式生成 |
| M2 | `/api/v1/model/generate/stream` | POST | 流式生成（SSE） |
| M3 | `/api/v1/model/catalog` | GET | 查询可用能力档位目录 |
| M4 | `/api/v1/model/usage` | GET | 用量统计（业务化口径） |

本文件只做参数装配与响应封装，协议转换全部在 `services/model_service.py`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.schemas.common import SSE_HEADERS, SSE_MEDIA_TYPE, ApiResponse
from app.schemas.model import (
    CatalogData,
    GenerateData,
    GenerateRequest,
    UsageData,
)
from app.services.model_service import get_model_service

router = APIRouter(prefix="/model", tags=["模型服务"])


@router.post(
    "/generate",
    response_model=ApiResponse[GenerateData],
    summary="M1 非流式生成",
    description="单轮/多轮非流式生成。写接口，需携带 Idempotency-Key 请求头。",
)
async def generate(
    request: Request,
    body: GenerateRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[GenerateData]:
    """M1 非流式生成。

    Args:
        request: FastAPI 请求对象。
        body: 生成请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[GenerateData]: `code=0` 的生成结果。
    """
    service = get_model_service()

    async def factory() -> GenerateData:
        return await service.generate(body, context.auth.user_id)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[GenerateData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[GenerateData].ok(result)


@router.post(
    "/generate/stream",
    summary="M2 流式生成（SSE）",
    description=(
        "流式生成，响应 Content-Type 为 text/event-stream。"
        "自研事件类型：start / delta / citation / done / error。"
        "流式接口不重试、不参与幂等回放。"
    ),
    response_class=StreamingResponse,
)
async def generate_stream(
    body: GenerateRequest,
    context: RequestContext = Depends(get_request_context),
) -> StreamingResponse:
    """M2 流式生成。

    Args:
        body: 生成请求（同 M1）。
        context: 请求上下文。

    Returns:
        StreamingResponse: SSE 流式响应。

    Raises:
        ApiError: 鉴权失败（20002）、租户缺失（20001）。
    """
    user_id = context.auth.user_id
    service = get_model_service()
    # SSE 响应一旦开始写出便无法再返回统一错误体，故先做档位校验
    service.ensure_profile_available(body.profile)
    return StreamingResponse(
        service.generate_stream(body, user_id),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@router.get(
    "/catalog",
    response_model=ApiResponse[CatalogData],
    summary="M3 能力档位目录",
    description="查询可用能力档位目录。内容由适配层配置驱动，不暴露真实模型名称。",
)
async def get_catalog(
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[CatalogData]:
    """M3 能力档位目录。

    Args:
        context: 请求上下文。

    Returns:
        ApiResponse[CatalogData]: 档位列表。
    """
    del context
    return ApiResponse[CatalogData].ok(get_model_service().get_catalog())


@router.get(
    "/usage",
    response_model=ApiResponse[UsageData],
    summary="M4 用量统计",
    description="用量统计，业务化口径（次数/额度），不展示 token 精确值。",
)
async def get_usage(
    context: RequestContext = Depends(get_request_context),
    period: str = Query("month", description="统计周期：day / month"),
) -> ApiResponse[UsageData]:
    """M4 用量统计。

    Args:
        context: 请求上下文。
        period: 统计周期。

    Returns:
        ApiResponse[UsageData]: 用量数据。

    Raises:
        ApiError: 周期参数非法（20001）。
    """
    return ApiResponse[UsageData].ok(get_model_service().get_usage(context.auth, period))
