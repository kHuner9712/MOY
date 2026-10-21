"""计费域端点（阶段二 BL1-BL10；本批只落 BL5）。

路径前缀固定 `/api/v1/billing`（与既有资源域共用 `/api/v1`，不新增版本）。

| # | 端点 | 批 |
| --- | --- | --- |
| BL5 | `GET /usage?period=YYYY-MM&group_by=member\\|department\\|capability` | B07 |

设计要点（D-12 第 5 条）：

1. **数据来源唯一**：个人视图与 M4 调用**同一个** `UsageService.snapshot()`，
   同一张聚合表、同一段计算 → 同刻 `quota_units_used == used_units`；
2. **管理视图需管理员**：`group_by` 存在时除权限点外还要求管理员角色，
   否则 `20003`（成员看不到企业总量）；
3. **不做额度计算**：所有数字来自 `usage_monthly`，端点只做协议转换；
4. 只读端点，不需要 `Idempotency-Key`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.business.policy_service import PolicyService, get_policy_service, has_admin_role
from app.business.usage_service import UsageService, get_usage_service
from app.core.config import get_request_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import RequestContext, get_request_context
from app.schemas.billing import (
    BillingUsageData,
    UsageBreakdownItem,
    UsageDailyTrendItem,
    UsageGroupBy,
)
from app.schemas.common import ApiResponse

logger = get_logger(__name__)

#: BL5 所需权限点（前端 `PERM.BILL_READ`）
BILLING_READ_PERMISSION = "billing:subscription:read"

router = APIRouter(prefix="/billing", tags=["计费"])


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
    await _policy_service(request).require_point(context.auth, BILLING_READ_PERMISSION)
    if group_by is not None and not has_admin_role(context.auth):
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


__all__: list[str] = ["BILLING_READ_PERMISSION", "get_billing_usage", "router"]
