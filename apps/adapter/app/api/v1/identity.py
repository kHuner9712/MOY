"""用户权限服务端点 U1-U6（架构文档 3.5）。

端点清单：
| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| U1 | `/api/v1/identity/verify` | POST | 令牌与权限点校验（内部高频调用） |
| U2 | `/api/v1/identity/profile` | GET | 当前登录用户信息 |
| U3 | `/api/v1/identity/organizations/{org_id}/members` | GET | 组织成员列表 |
| U4 | `/api/v1/identity/organizations/{org_id}/invitations` | POST | 邀请成员 |
| U5 | `/api/v1/identity/organizations/{org_id}/roles` | POST | 创建/分配角色 |
| U6 | `/api/v1/identity/audit-logs` | POST | 审计日志查询 |

本文件只做参数装配与响应封装，协议转换全部在 `services/identity_service.py`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.schemas.common import ApiResponse, PageResult
from app.schemas.identity import (
    AuditLogItem,
    AuditLogQueryRequest,
    InvitationData,
    InviteRequest,
    ListMembersQuery,
    MemberItem,
    ProfileData,
    RoleData,
    UpsertRoleRequest,
    VerifyData,
    VerifyRequest,
)
from app.services.identity_service import get_identity_service

router = APIRouter(prefix="/identity", tags=["用户权限服务"])


@router.post(
    "/verify",
    response_model=ApiResponse[VerifyData],
    summary="U1 令牌与权限点校验",
    description=(
        "令牌与权限点校验（内部高频调用）。权限点命名规范为 resource:action；"
        "未注册的权限点返回 14004。写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def verify(
    request: Request,
    body: VerifyRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[VerifyData]:
    """U1 令牌与权限点校验。

    Args:
        request: FastAPI 请求对象。
        body: 校验请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[VerifyData]: 校验结论。
    """
    service = get_identity_service()

    async def factory() -> VerifyData:
        return service.verify(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[VerifyData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[VerifyData].ok(result)


@router.get(
    "/profile",
    response_model=ApiResponse[ProfileData],
    summary="U2 当前用户信息",
    description="查询当前登录用户信息（含租户与组织）。",
)
async def get_profile(
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[ProfileData]:
    """U2 当前用户信息。

    Args:
        context: 请求上下文。

    Returns:
        ApiResponse[ProfileData]: 用户资料。
    """
    return ApiResponse[ProfileData].ok(await get_identity_service().get_profile(context.auth))


@router.get(
    "/organizations/{org_id}/members",
    response_model=ApiResponse[PageResult[MemberItem]],
    summary="U3 组织成员列表",
    description="查询组织成员列表（分页，支持角色与关键字过滤）。",
)
async def list_members(
    context: RequestContext = Depends(get_request_context),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    role: str | None = Query(None, description="按角色过滤"),
    keyword: str = Query("", max_length=128, description="按姓名或邮箱过滤"),
) -> ApiResponse[PageResult[MemberItem]]:
    """U3 组织成员列表。

    Args:
        context: 请求上下文。
        org_id: 组织 ID。
        page: 页码。
        page_size: 每页条数。
        role: 按角色过滤。
        keyword: 按姓名或邮箱过滤。

    Returns:
        ApiResponse[PageResult[MemberItem]]: 分页结果。
    """
    from app.schemas.identity import OrgRole

    role_enum = OrgRole(role) if role else None
    query = ListMembersQuery(page=page, page_size=page_size, role=role_enum, keyword=keyword)
    return ApiResponse[PageResult[MemberItem]].ok(
        await get_identity_service().list_members(org_id, query, context.auth)
    )


@router.post(
    "/organizations/{org_id}/invitations",
    response_model=ApiResponse[InvitationData],
    summary="U4 邀请成员",
    description="邀请成员加入组织。写接口，需携带 Idempotency-Key 请求头。",
)
async def invite_member(
    request: Request,
    body: InviteRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
) -> ApiResponse[InvitationData]:
    """U4 邀请成员。

    Args:
        request: FastAPI 请求对象。
        body: 邀请请求。
        context: 请求上下文。
        manager: 幂等管理器。
        org_id: 组织 ID。

    Returns:
        ApiResponse[InvitationData]: 邀请记录。
    """
    service = get_identity_service()

    async def factory() -> InvitationData:
        return await service.invite_member(
            org_id=org_id, email=body.email, role=body.role, message=body.message, auth=context.auth
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[InvitationData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[InvitationData].ok(result)


@router.post(
    "/organizations/{org_id}/roles",
    response_model=ApiResponse[RoleData],
    summary="U5 创建/分配角色",
    description=(
        "创建或分配角色。权限点命名不合规范视为未注册，返回 14004。"
        "写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def upsert_role(
    request: Request,
    body: UpsertRoleRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
) -> ApiResponse[RoleData]:
    """U5 创建/分配角色。

    Args:
        request: FastAPI 请求对象。
        body: 角色请求。
        context: 请求上下文。
        manager: 幂等管理器。
        org_id: 组织 ID。

    Returns:
        ApiResponse[RoleData]: 角色信息。
    """
    service = get_identity_service()

    async def factory() -> RoleData:
        return await service.upsert_role(org_id=org_id, request=body, auth=context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[RoleData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[RoleData].ok(result)


@router.post(
    "/audit-logs",
    response_model=ApiResponse[PageResult[AuditLogItem]],
    summary="U6 审计日志查询",
    description=(
        "审计日志查询（适配层不持有审计逻辑，仅提供查询出口）。"
        "写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def query_audit_logs(
    request: Request,
    body: AuditLogQueryRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[PageResult[AuditLogItem]]:
    """U6 审计日志查询。

    Args:
        request: FastAPI 请求对象。
        body: 查询请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[PageResult[AuditLogItem]]: 分页结果。
    """
    service = get_identity_service()

    from typing import Any

    async def factory() -> Any:
        return service.query_audit_logs(body, context.auth)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[PageResult[AuditLogItem]].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[PageResult[AuditLogItem]].ok(result)
