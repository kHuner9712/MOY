"""计费域端点（阶段二 BL1-BL10）。

路径前缀固定 `/api/v1/billing`（与既有资源域共用 `/api/v1`，不新增版本）。

| # | 端点 | 批 | 鉴权 |
| --- | --- | --- | --- |
| BL1 | `GET /plans` | B09 | 登录即可 |
| BL2 | `GET /subscription` | B09 | `billing:subscription:read` |
| BL3 | `POST /subscription` | B09 | `billing:subscription:manage` |
| BL4 | `PATCH /subscription/seats` | B09 | `billing:subscription:manage` |
| BL5 | `GET /usage?period=&group_by=` | B07 | `billing:subscription:read` |
| BL6 | `POST /orders` | B09 | `billing:subscription:manage` |
| BL7 | `GET /orders` | B09 | `billing:subscription:read` |
| BL8 | `GET /orders/{order_id}` | B09 | `billing:subscription:read` |
| BL9 | `GET /invoices` | B09 | `billing:subscription:read` |
| BL10 | `POST /payments/callback` | B09 | **免 Bearer，必须验签** |

设计要点：

1. **数据来源唯一**：BL5 个人视图与 M4 调用**同一个** `UsageService.snapshot()`；
   BL2 的 `quota_units_total` 与 BL5 的 `quota_units_total` 用同一算式
   （`席位 × 每席位额度`），因此三个端点的「总额度」恒等；
2. **管理视图需管理员**：`group_by` 存在时除权限点外还要求管理员角色，
   否则 `20003`（成员看不到企业总量）；
3. **写接口必须带 `Idempotency-Key`**，指纹含请求体摘要（架构文档 3.0.5）；
4. **BL10 是唯一免鉴权业务端点**：见 `middleware/error_handler.AUTH_EXEMPT_PATHS`，
   它没有 `Authorization` 依赖，改用**验签 + 回调留痕唯一键**保证安全与幂等。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, Path, Query, Request

from app.business.order_service import OrderService, get_order_service
from app.business.plan_service import PlanService, get_plan_service
from app.business.policy_service import PolicyService, get_policy_service
from app.business.subscription_service import SubscriptionService, get_subscription_service
from app.business.usage_service import UsageService, get_usage_service
from app.core.config import get_request_settings
from app.core.errors import ApiError, ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager, hash_request_body
from app.core.logging import get_logger
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.schemas.billing import (
    PERIOD_PATTERN,
    BillingUsageData,
    ChangeSubscriptionData,
    ChangeSubscriptionRequest,
    CreateOrderData,
    CreateOrderRequest,
    InvoiceListData,
    OrderDetailData,
    OrderItem,
    PaymentCallbackData,
    PaymentCallbackRequest,
    PlanListData,
    SubscriptionData,
    UpdateSeatsData,
    UpdateSeatsRequest,
    UsageBreakdownItem,
    UsageDailyTrendItem,
    UsageGroupBy,
)
from app.schemas.common import ApiResponse, PageResult

logger = get_logger(__name__)

#: BL5/BL2/BL7/BL8/BL9 所需权限点（前端 `PERM.BILL_READ`）
BILLING_READ_PERMISSION = "billing:subscription:read"

#: BL3/BL4/BL6 所需权限点（前端 `PERM.BILL_MANAGE`）
BILLING_MANAGE_PERMISSION = "billing:subscription:manage"

router = APIRouter(prefix="/billing", tags=["计费"])

#: 免鉴权子路由（BL10 支付回调专用）。
#:
#: **必须与 `router` 分开挂载**：主路由统一挂了 `enforce_ratelimit`，而该依赖内部
#: 会解析请求上下文（要求 Bearer 令牌）——把回调放进主路由会让渠道回调直接 401。
#: 回调的安全性由「验签 + 回调流水号唯一约束」保证，见 `order_service.handle_callback`。
public_router = APIRouter(prefix="/billing", tags=["计费"])

#: 幂等写操作的返回数据类型（泛型参数）
TData = TypeVar("TData")


def _usage_service(request: Request) -> UsageService:
    """按请求生效配置构造计量服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        UsageService: 计量服务（配置取自 `app.state.settings`）。
    """
    return get_usage_service(get_request_settings(request))


def _policy_service(request: Request) -> PolicyService:
    """按请求生效配置构造可见性判定服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        PolicyService: 单例判定服务。
    """
    return get_policy_service(get_request_settings(request))


def _plan_service(request: Request) -> PlanService:
    """按请求生效配置构造套餐服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        PlanService: 套餐服务。
    """
    return get_plan_service(get_request_settings(request))


def _subscription_service(request: Request) -> SubscriptionService:
    """按请求生效配置构造订阅服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        SubscriptionService: 订阅服务。
    """
    return get_subscription_service(get_request_settings(request))


def _order_service(request: Request) -> OrderService:
    """按请求生效配置构造订单服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        OrderService: 订单服务。
    """
    return get_order_service(get_request_settings(request))


async def _respond_with_idempotency(
    request: Request,
    context: RequestContext,
    manager: IdempotencyManager,
    factory: Callable[[], Awaitable[TData]],
    body_digest: str,
) -> ApiResponse[TData]:
    """执行幂等写操作并包装响应。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        factory: 无参异步可调用对象。
        body_digest: 请求体摘要。

    Returns:
        ApiResponse[TData]: 统一响应（重复请求返回 20007 + 原结果）。
    """
    result, replayed = await run_idempotent(
        request, context, factory, manager, body_digest=body_digest
    )
    if replayed:
        return ApiResponse[TData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[TData].ok(result)


# ===========================================================================
# BL1 套餐目录（登录即可）
# ===========================================================================
@router.get(
    "/plans",
    response_model=ApiResponse[PlanListData],
    summary="BL1 套餐目录",
    description=(
        "查询全部可购套餐（免费版 / 专业版 / 企业版）与功能开关。"
        "价格单位为分，展示层除以 100 即为元。登录即可查看。"
    ),
)
async def list_plans(
    request: Request,
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[PlanListData]:
    """BL1 套餐目录。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。

    Returns:
        ApiResponse[PlanListData]: 套餐清单。
    """
    del context  # 登录即可，不做权限点校验
    return ApiResponse[PlanListData].ok(await _plan_service(request).catalog())


# ===========================================================================
# BL2 订阅详情
# ===========================================================================
@router.get(
    "/subscription",
    response_model=ApiResponse[SubscriptionData],
    summary="BL2 订阅详情",
    description=(
        "查询当前套餐、席位与额度。未开通任何套餐时返回免费版视图"
        "（`plan_code=free`、`status=active`、`seats_total=3`），不会返回 404。"
        "需 billing:subscription:read 权限。"
    ),
)
async def get_subscription(
    request: Request,
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[SubscriptionData]:
    """BL2 订阅详情。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。

    Returns:
        ApiResponse[SubscriptionData]: 订阅详情。

    Raises:
        ApiError: 权限不足（20003）。
    """
    await _policy_service(request).require_point(context.auth, BILLING_READ_PERMISSION)
    data = await _subscription_service(request).current(tenant_id=context.tenant_id)
    return ApiResponse[SubscriptionData].ok(data)


# ===========================================================================
# BL3 开通 / 升级 / 降级 / 关闭续费
# ===========================================================================
@router.post(
    "/subscription",
    response_model=ApiResponse[ChangeSubscriptionData],
    summary="BL3 变更套餐",
    description=(
        "开通、升级、降级或关闭自动续费。升级立即生效并刷新功能开关；"
        "降级在当前周期结束后生效（`notice_code=15005`，属提示不属错误）。"
        "写接口，需携带 Idempotency-Key 请求头；需 billing:subscription:manage 权限。"
    ),
)
async def change_subscription(
    request: Request,
    body: ChangeSubscriptionRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[ChangeSubscriptionData]:
    """BL3 变更套餐。

    Args:
        request: FastAPI 请求对象。
        body: 变更请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[ChangeSubscriptionData]: 变更结果。

    Raises:
        ApiError: 套餐不可购（15002）、能力未开通（15001）、
            已有待支付订单（15004）、权限不足（20003）。
    """
    await _policy_service(request).require_point(context.auth, BILLING_MANAGE_PERMISSION)
    service = _subscription_service(request)
    return await _respond_with_idempotency(
        request,
        context,
        manager,
        lambda: service.change(tenant_id=context.tenant_id, actor_id=context.user_id, request=body),
        hash_request_body(body.model_dump_json()),
    )


# ===========================================================================
# BL4 调整席位
# ===========================================================================
@router.patch(
    "/subscription/seats",
    response_model=ApiResponse[UpdateSeatsData],
    summary="BL4 调整席位",
    description=(
        "增减成员席位。席位数低于已用席位时返回 15003（请先移出成员）；"
        "免费版席位固定，调整请先升级。写接口，需携带 Idempotency-Key 请求头；"
        "需 billing:subscription:manage 权限。"
    ),
)
async def update_seats(
    request: Request,
    body: UpdateSeatsRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[UpdateSeatsData]:
    """BL4 调整席位。

    Args:
        request: FastAPI 请求对象。
        body: 席位请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[UpdateSeatsData]: 调整结果。

    Raises:
        ApiError: 席位不足（15003）、免费版不可调（15001）、权限不足（20003）。
    """
    await _policy_service(request).require_point(context.auth, BILLING_MANAGE_PERMISSION)
    service = _subscription_service(request)
    return await _respond_with_idempotency(
        request,
        context,
        manager,
        lambda: service.update_seats(
            tenant_id=context.tenant_id, actor_id=context.user_id, seats=body.seats
        ),
        hash_request_body(body.model_dump_json()),
    )


# ===========================================================================
# BL5 用量明细（B07 落地的既有端点）
# ===========================================================================
@router.get(
    "/usage",
    response_model=ApiResponse[BillingUsageData],
    summary="BL5 用量明细",
    description=(
        "查询账期内的用量与分项明细。个人视图与个人中心的用量数字**必然一致**；"
        "按成员/部门/能力分组属于企业管理视图，需要管理员权限。"
        "需 billing:subscription:read 权限。"
    ),
)
async def get_billing_usage(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    period: str = Query(
        "",
        max_length=7,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="账期，形如 2026-11；留空取当前月",
    ),
    group_by: UsageGroupBy | None = Query(None, description="分组维度（管理视图）"),
) -> ApiResponse[BillingUsageData]:
    """BL5 用量明细。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        period: 账期。
        group_by: 分组维度。

    Returns:
        ApiResponse[BillingUsageData]: 用量与分项明细。

    Raises:
        ApiError: 权限不足（20003）、参数非法（20001）。
    """
    policy = _policy_service(request)
    await policy.require_point(context.auth, BILLING_READ_PERMISSION)
    # 管理视图判定必须走 PolicyService（业务库角色优先）：按令牌角色判会让
    # 「业务库已降级为 member」的账号继续看到企业总量（D-13 撤权不生效）。
    if group_by is not None and not await policy.admin_of(context.auth):
        # 管理视图：成员只能看自己的用量，不得看到企业总量（PRD 6.3 组织层）
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=f"billing group_by requires admin, roles={context.auth.roles}",
        )

    service = _usage_service(request)
    if group_by is None:
        snapshot = await service.snapshot(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            period=period,
        )
    else:
        snapshot = await service.tenant_snapshot(
            tenant_id=context.tenant_id,
            period=period,
            group_by=group_by,
        )

    logger.info(
        "billing_usage_queried",
        tenant_id=context.tenant_id,
        period=snapshot.period,
        group_by=group_by or "",
        used_units=snapshot.used_units,
    )
    return ApiResponse[BillingUsageData].ok(
        BillingUsageData(
            period=snapshot.period,
            quota_units_total=snapshot.total_units,
            quota_units_used=snapshot.quota_units_used,
            quota_units_remaining=snapshot.remaining_units,
            usage_percent=snapshot.usage_percent,
            over_quota_warning=snapshot.over_quota_warning,
            quota_unit=snapshot.quota_unit,
            breakdown=[
                UsageBreakdownItem(
                    key=item.key,
                    display_name=item.display_name,
                    units=item.units,
                    percent=item.percent,
                )
                for item in snapshot.breakdown
            ],
            daily_trend=[
                UsageDailyTrendItem(date=point.date, units=point.units)
                for point in snapshot.daily_trend
            ],
        )
    )


# ===========================================================================
# BL6 下单
# ===========================================================================
@router.post(
    "/orders",
    response_model=ApiResponse[CreateOrderData],
    summary="BL6 创建订单",
    description=(
        "创建套餐订单。同一企业存在未支付订单时返回 15004（不会产生第二个订单）。"
        "在线支付返回跳转地址，对公转账返回收款信息。订单 30 分钟未支付自动关闭。"
        "写接口，需携带 Idempotency-Key 请求头；需 billing:subscription:manage 权限。"
    ),
)
async def create_order(
    request: Request,
    body: CreateOrderRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[CreateOrderData]:
    """BL6 创建订单。

    Args:
        request: FastAPI 请求对象。
        body: 下单请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[CreateOrderData]: 订单信息。

    Raises:
        ApiError: 套餐不可购（15002）、已有待支付订单（15004）、
            渠道未配置（30004）、权限不足（20003）。
    """
    await _policy_service(request).require_point(context.auth, BILLING_MANAGE_PERMISSION)
    service = _order_service(request)
    return await _respond_with_idempotency(
        request,
        context,
        manager,
        lambda: service.create(tenant_id=context.tenant_id, actor_id=context.user_id, request=body),
        hash_request_body(body.model_dump_json()),
    )


# ===========================================================================
# BL7 订单列表
# ===========================================================================
@router.get(
    "/orders",
    response_model=ApiResponse[PageResult[OrderItem]],
    summary="BL7 订单列表",
    description="分页查询本企业订单（按创建时间倒序）。需 billing:subscription:read 权限。",
)
async def list_orders(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    status: str = Query("", max_length=32, description="订单状态过滤"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
) -> ApiResponse[PageResult[OrderItem]]:
    """BL7 订单列表。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        status: 状态过滤。
        page: 页码。
        page_size: 每页条数。

    Returns:
        ApiResponse[PageResult[OrderItem]]: 分页订单列表。

    Raises:
        ApiError: 权限不足（20003）。
    """
    await _policy_service(request).require_point(context.auth, BILLING_READ_PERMISSION)
    result = await _order_service(request).list_orders(
        tenant_id=context.tenant_id, status=status, page=page, page_size=page_size
    )
    return ApiResponse[PageResult[OrderItem]].ok(result)


# ===========================================================================
# BL8 订单详情
# ===========================================================================
@router.get(
    "/orders/{order_id}",
    response_model=ApiResponse[OrderDetailData],
    summary="BL8 订单详情",
    description=(
        "查询单个订单。非本企业订单按「不存在」处理（20004），避免探测他人订单。"
        "支付流水号已脱敏。需 billing:subscription:read 权限。"
    ),
)
async def get_order(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    order_id: str = Path(..., description="订单 ID", min_length=3, max_length=128),
) -> ApiResponse[OrderDetailData]:
    """BL8 订单详情。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        order_id: 订单 ID。

    Returns:
        ApiResponse[OrderDetailData]: 订单详情。

    Raises:
        ApiError: 订单不存在（20004）、权限不足（20003）。
    """
    await _policy_service(request).require_point(context.auth, BILLING_READ_PERMISSION)
    data = await _order_service(request).detail(tenant_id=context.tenant_id, order_id=order_id)
    return ApiResponse[OrderDetailData].ok(data)


# ===========================================================================
# BL9 账单列表
# ===========================================================================
@router.get(
    "/invoices",
    response_model=ApiResponse[InvoiceListData],
    summary="BL9 账单列表",
    description=(
        "查询本企业账单（可按账期过滤）。账单在支付成功时自动开具。"
        "需 billing:subscription:read 权限。"
    ),
)
async def list_invoices(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    period: str = Query("", max_length=7, pattern=f"^$|{PERIOD_PATTERN}", description="账期"),
) -> ApiResponse[InvoiceListData]:
    """BL9 账单列表。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        period: 账期 `YYYY-MM`（空表示全部）。

    Returns:
        ApiResponse[InvoiceListData]: 账单列表。

    Raises:
        ApiError: 权限不足（20003）。
    """
    await _policy_service(request).require_point(context.auth, BILLING_READ_PERMISSION)
    data = await _order_service(request).list_invoices(tenant_id=context.tenant_id, period=period)
    return ApiResponse[InvoiceListData].ok(data)


# ===========================================================================
# BL10 支付回调（免 Bearer，必须验签）
# ===========================================================================
@public_router.post(
    "/payments/callback",
    response_model=ApiResponse[PaymentCallbackData],
    summary="BL10 支付结果回调",
    description=(
        "支付渠道的服务端回调入口：**免 Bearer 鉴权**（唯一免鉴权的业务端点），"
        "改用验签 + 回调流水号唯一约束保证安全与幂等。"
        "同一笔回调重复发送只生效一次，不会重复开通套餐或重复开票。"
    ),
)
async def payment_callback(
    request: Request,
    body: PaymentCallbackRequest,
) -> ApiResponse[PaymentCallbackData]:
    """BL10 支付结果回调。

    Args:
        request: FastAPI 请求对象。
        body: 渠道回调载荷。

    Returns:
        ApiResponse[PaymentCallbackData]: 对渠道方的应答。

    Raises:
        ApiError: 验签失败（20001）、订单不存在（20004）、
            金额不一致（15009）、订单已处理（15008）。
    """
    payload = body.model_dump()
    # 渠道可能回传任意字段：extra 合并回载荷，验签按完整载荷计算
    for key, value in (body.model_extra or {}).items():
        payload.setdefault(key, value)
    service = _order_service(request)
    data = await service.handle_callback(payload)
    logger.info("payment_callback_processed", order_id=payload.get("out_trade_no", ""))
    return ApiResponse[PaymentCallbackData].ok(data)


__all__: list[str] = [
    "BILLING_MANAGE_PERMISSION",
    "BILLING_READ_PERMISSION",
    "change_subscription",
    "create_order",
    "get_billing_usage",
    "get_order",
    "get_subscription",
    "list_invoices",
    "list_orders",
    "list_plans",
    "payment_callback",
    "public_router",
    "router",
    "update_seats",
]
