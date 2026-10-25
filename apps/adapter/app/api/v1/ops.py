"""运营后台端点（阶段二 B10，架构文档 §4.6 OPS1-OPS8）。

路径前缀固定 `/api/v1/ops`（与既有资源域共用 `/api/v1`，不新增版本）。

| # | 端点 | 权限点 |
| --- | --- | --- |
| OPS1 | `GET /ops/tenants` | `ops:console:access` |
| OPS2 | `GET /ops/tenants/{tenant_id}` | `ops:console:access` |
| OPS3 | `PATCH /ops/tenants/{tenant_id}` | `ops:tenant:manage` |
| OPS4 | `GET /ops/orders` | `ops:console:access` |
| OPS5 | `POST /ops/orders/{order_id}/confirm` | `ops:order:confirm` |
| OPS6 | `GET /ops/metrics?range=7d\\|30d` | `ops:console:access` |
| OPS7 | `GET /ops/settings` | `ops:console:access` |
| OPS8 | `PATCH /ops/settings` | `ops:settings:manage` |

**平台模式守卫**（PRD 6.3 红线）：企业令牌调用任一 `/ops/**` → `20003`
（**不返回 404**，避免探测）。守卫实现在 `core/security.require_ops_access`，
每个端点第一行调用它，且它返回的平台租户哨兵是唯一允许进入
`db/session.platform_scope()` 的凭据。

**最小可见原则**：响应里没有对话内容、文档原文、成员邮箱与任何凭据字段
（不是「查出来再过滤」，而是模型里根本没有这些字段）。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.business.ops_service import OpsService
from app.core.config import get_request_settings
from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.logging import get_logger
from app.core.security import (
    RequestContext,
    get_request_context,
    require_ops_access,
    run_idempotent,
)
from app.schemas.common import ApiResponse
from app.schemas.ops import (
    ORDER_STATUS_FILTER,
    ConfirmOrderData,
    ConfirmOrderRequest,
    MetricsRange,
    OpsMetricsData,
    OpsOrderListData,
    OpsSettingsPayload,
    TenantDetailData,
    TenantListData,
    UpdateOpsSettingsRequest,
    UpdateTenantStatusData,
    UpdateTenantStatusRequest,
)

logger = get_logger(__name__)

#: 运营后台访问权限点（前端 `PERM.OPS_ACCESS`）
OPS_CONSOLE_PERMISSION = "ops:console:access"

#: 企业启用/停用权限点（前端 `PERM.OPS_TENANT`）
OPS_TENANT_PERMISSION = "ops:tenant:manage"

#: 订单核销权限点（前端 `PERM.OPS_ORDER`）
OPS_ORDER_PERMISSION = "ops:order:confirm"

#: 系统配置权限点（前端 `PERM.OPS_SETTINGS`）
OPS_SETTINGS_PERMISSION = "ops:settings:manage"

router = APIRouter(prefix="/ops", tags=["运营管理"])


def _ops_service(request: Request) -> OpsService:
    """按请求生效配置构造运营服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        OpsService: 运营服务。
    """
    return OpsService(get_request_settings(request))


# ===========================================================================
# OPS1 企业列表
# ===========================================================================
@router.get(
    "/tenants",
    response_model=ApiResponse[TenantListData],
    summary="OPS1 企业列表",
    description=(
        "跨企业查询企业列表（可按名称关键字与状态筛选）。"
        "响应只含聚合数字（席位、当月用量），不含对话内容、文档原文与成员邮箱。"
        "需 ops:console:access 权限（仅运营账号）。"
    ),
)
async def list_tenants(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    keyword: str = Query("", max_length=64, description="企业名称关键字"),
    status: str = Query("", max_length=16, description="企业状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
) -> ApiResponse[TenantListData]:
    """OPS1 企业列表。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        keyword: 名称关键字。
        status: 状态筛选。
        page: 页码。
        page_size: 每页条数。

    Returns:
        ApiResponse[TenantListData]: 企业列表。

    Raises:
        ApiError: 非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_CONSOLE_PERMISSION)
    data = await _ops_service(request).list_tenants(
        keyword=keyword, status=status, page=page, page_size=page_size
    )
    return ApiResponse[TenantListData].ok(data)


# ===========================================================================
# OPS2 企业详情
# ===========================================================================
@router.get(
    "/tenants/{tenant_id}",
    response_model=ApiResponse[TenantDetailData],
    summary="OPS2 企业详情",
    description=(
        "查询单个企业的基本信息、订阅、用量与近期订单。需 ops:console:access 权限（仅运营账号）。"
    ),
)
async def get_tenant(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    tenant_id: str = Path(..., description="企业 ID", min_length=2, max_length=128),
) -> ApiResponse[TenantDetailData]:
    """OPS2 企业详情。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        tenant_id: 企业 ID。

    Returns:
        ApiResponse[TenantDetailData]: 企业详情。

    Raises:
        ApiError: 企业不存在（20004）、非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_CONSOLE_PERMISSION)
    data = await _ops_service(request).tenant_detail(tenant_id)
    return ApiResponse[TenantDetailData].ok(data)


# ===========================================================================
# OPS3 启用 / 停用企业
# ===========================================================================
@router.patch(
    "/tenants/{tenant_id}",
    response_model=ApiResponse[UpdateTenantStatusData],
    summary="OPS3 启用或停用企业",
    description=(
        "停用后该企业成员无法继续使用服务（可再次启用）。"
        "操作会写入操作记录，包含运营人与停用原因。需 ops:tenant:manage 权限。"
    ),
)
async def update_tenant(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    body: UpdateTenantStatusRequest = Body(...),
    tenant_id: str = Path(..., description="企业 ID", min_length=2, max_length=128),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[UpdateTenantStatusData]:
    """OPS3 启用或停用企业。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        body: 状态变更请求。
        tenant_id: 企业 ID。
        manager: 幂等管理器（所有写接口必须携带 Idempotency-Key）。

    Returns:
        ApiResponse[UpdateTenantStatusData]: 变更结果。

    Raises:
        ApiError: 企业不存在（20004）、非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_TENANT_PERMISSION)

    async def factory() -> UpdateTenantStatusData:
        return await _ops_service(request).update_tenant_status(
            tenant_id=tenant_id, actor_id=context.user_id, request=body
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[UpdateTenantStatusData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[UpdateTenantStatusData].ok(result)


# ===========================================================================
# OPS4 订单列表
# ===========================================================================
@router.get(
    "/orders",
    response_model=ApiResponse[OpsOrderListData],
    summary="OPS4 订单列表",
    description=(
        "跨企业查询订单（可按企业、状态筛选）。金额单位为分。"
        "需 ops:console:access 权限（仅运营账号）。"
    ),
)
async def list_orders(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    tenant_id: str = Query("", max_length=128, description="企业 ID 筛选"),
    status: str = Query("", pattern=ORDER_STATUS_FILTER, description="订单状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
) -> ApiResponse[OpsOrderListData]:
    """OPS4 订单列表。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        tenant_id: 企业过滤。
        status: 状态过滤。
        page: 页码。
        page_size: 每页条数。

    Returns:
        ApiResponse[OpsOrderListData]: 订单列表。

    Raises:
        ApiError: 非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_CONSOLE_PERMISSION)
    data = await _ops_service(request).list_orders(
        tenant_id=tenant_id, status=status, page=page, page_size=page_size
    )
    return ApiResponse[OpsOrderListData].ok(data)


# ===========================================================================
# OPS5 订单核销
# ===========================================================================
@router.post(
    "/orders/{order_id}/confirm",
    response_model=ApiResponse[ConfirmOrderData],
    summary="OPS5 订单核销",
    description=(
        "确认对公转账到账并核销订单：订单置为 paid、订阅生效、开具账单，"
        "并写入操作记录（含运营人与备注）。金额与应付不一致返回 15009，"
        "重复核销返回 15008。需 ops:order:confirm 权限。"
    ),
)
async def confirm_order(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    body: ConfirmOrderRequest = Body(...),
    order_id: str = Path(..., description="订单 ID", min_length=3, max_length=128),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[ConfirmOrderData]:
    """OPS5 订单核销。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        body: 核销请求。
        order_id: 订单 ID。
        manager: 幂等管理器。**资金操作**：网络重试/双击不得重复核销。

    Returns:
        ApiResponse[ConfirmOrderData]: 核销结果。

    Raises:
        ApiError: 订单不存在（20004）、已处理（15008）、
            金额不符（15009）、非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_ORDER_PERMISSION)

    async def factory() -> ConfirmOrderData:
        return await _ops_service(request).confirm_order(
            order_id=order_id, actor_id=context.user_id, request=body
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[ConfirmOrderData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[ConfirmOrderData].ok(result)


# ===========================================================================
# OPS6 平台统计
# ===========================================================================
@router.get(
    "/metrics",
    response_model=ApiResponse[OpsMetricsData],
    summary="OPS6 平台统计",
    description=(
        "平台经营指标：企业数、活跃成员、对话与文档处理次数、额度总和、"
        "套餐分布与用量 Top 企业。额度口径与企业的用量页**完全一致**。"
        "需 ops:console:access 权限（仅运营账号）。"
    ),
)
async def get_metrics(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    range: MetricsRange = Query("7d", description="统计区间：7d / 30d"),
) -> ApiResponse[OpsMetricsData]:
    """OPS6 平台统计。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        range: 统计区间（`7d` / `30d`）。

    Returns:
        ApiResponse[OpsMetricsData]: 平台统计。

    Raises:
        ApiError: 区间非法（20001）、非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_CONSOLE_PERMISSION)
    data = await _ops_service(request).metrics(range_label=range)
    return ApiResponse[OpsMetricsData].ok(data)


# ===========================================================================
# OPS7 读取系统配置
# ===========================================================================
@router.get(
    "/settings",
    response_model=ApiResponse[OpsSettingsPayload],
    summary="OPS7 读取系统配置",
    description=(
        "读取额度告警阈值、试用天数、额度单位文案、平台公告与套餐上下架状态。"
        "需 ops:console:access 权限（仅运营账号）。"
    ),
)
async def get_settings(
    request: Request,
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[OpsSettingsPayload]:
    """OPS7 读取系统配置。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。

    Returns:
        ApiResponse[OpsSettingsPayload]: 全量配置。

    Raises:
        ApiError: 非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_CONSOLE_PERMISSION)
    data = await _ops_service(request).get_settings()
    return ApiResponse[OpsSettingsPayload].ok(data)


# ===========================================================================
# OPS8 修改系统配置
# ===========================================================================
@router.patch(
    "/settings",
    response_model=ApiResponse[OpsSettingsPayload],
    summary="OPS8 修改系统配置",
    description=(
        "按需修改配置项（只改传入字段），返回更新后的全量配置；"
        "变更写入操作记录（含运营人）。需 ops:settings:manage 权限。"
    ),
)
async def update_settings(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    body: UpdateOpsSettingsRequest = Body(...),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[OpsSettingsPayload]:
    """OPS8 修改系统配置。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        body: 部分更新请求。
        manager: 幂等管理器（所有写接口必须携带 Idempotency-Key）。

    Returns:
        ApiResponse[OpsSettingsPayload]: 更新后的全量配置。

    Raises:
        ApiError: 未传可改字段（20001）、非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_SETTINGS_PERMISSION)

    async def factory() -> OpsSettingsPayload:
        return await _ops_service(request).update_settings(actor_id=context.user_id, request=body)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[OpsSettingsPayload].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[OpsSettingsPayload].ok(result)


__all__: list[str] = [
    "OPS_CONSOLE_PERMISSION",
    "OPS_ORDER_PERMISSION",
    "OPS_SETTINGS_PERMISSION",
    "OPS_TENANT_PERMISSION",
    "confirm_order",
    "get_metrics",
    "get_settings",
    "get_tenant",
    "list_orders",
    "list_tenants",
    "router",
    "update_settings",
    "update_tenant",
]
