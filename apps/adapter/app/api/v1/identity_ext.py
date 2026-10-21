"""组织与权限扩展端点（阶段二 B08，架构文档 §4.4 ID1-ID11）。

路径前缀固定 `/api/v1/identity`（与既有 U1-U6 同前缀，不新增版本）：

| # | 端点 | 权限点 |
| --- | --- | --- |
| ID1 | `GET /organizations/{org_id}/departments` | 登录即可（只读） |
| ID2 | `POST /organizations/{org_id}/departments` | `team:structure:manage` |
| ID3 | `PATCH /departments/{department_id}` | `team:structure:manage` |
| ID4 | `DELETE /departments/{department_id}` | `team:structure:manage` |
| ID5 | `GET /permissions` | 登录即可 |
| ID6 | `GET /organizations/{org_id}/roles` | `team:role:manage` |
| ID7 | `DELETE /organizations/{org_id}/roles/{role_id}` | `team:role:manage` |
| ID8 | `PATCH /organizations/{org_id}/members/{member_id}` | `team:member:update` |
| ID9 | `DELETE /organizations/{org_id}/members/{member_id}` | `team:member:update` |
| ID10 | `POST /organizations/{org_id}/members/batch` | `team:member:update` |
| ID11 | `GET /audit-logs/actions` | 登录即可 |

写接口（ID2/ID3/ID4/ID7/ID8/ID9/ID10）必须携带 `Idempotency-Key`，
指纹**必须含请求体摘要**（架构文档 12.3 第 3 条）；ID10 是批量成员接口，
漏传 `body_digest` 会造成「同键换 members 被静默回放」。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, Path, Query, Request

from app.business.department_service import DepartmentService
from app.business.kc_projection import KcProjectionService
from app.business.member_service import MemberService
from app.business.role_service import RoleService
from app.core.config import get_request_settings
from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager, hash_request_body
from app.core.logging import get_logger
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.db.repositories.audit_repo import action_groups
from app.schemas.common import ApiResponse
from app.schemas.identity_ext import (
    ActionCatalogData,
    ActionGroup,
    ActionItem,
    BatchMemberData,
    BatchMemberRequest,
    CreateDepartmentData,
    CreateDepartmentRequest,
    DeleteDepartmentData,
    DeleteRoleData,
    DepartmentTreeData,
    PermissionCatalogData,
    RemoveMemberData,
    RoleListData,
    UpdateDepartmentData,
    UpdateDepartmentRequest,
    UpdateMemberData,
    UpdateMemberRequest,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/identity", tags=["用户权限服务"])

#: 空请求体的固定摘要（无体写接口使用，架构文档 3.0.5「摘要项不得省略」）
EMPTY_BODY_DIGEST = hash_request_body("")


def _department_service(request: Request) -> DepartmentService:
    """构造部门业务服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        DepartmentService: 部门服务实例。
    """
    return DepartmentService(get_request_settings(request))


def _role_service(request: Request) -> RoleService:
    """构造角色业务服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        RoleService: 角色服务实例。
    """
    return RoleService(get_request_settings(request))


def _member_service(request: Request) -> MemberService:
    """构造成员业务服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        MemberService: 成员服务实例。
    """
    return MemberService(get_request_settings(request))


def _projection_service(request: Request) -> KcProjectionService:
    """构造投影服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        KcProjectionService: 投影服务实例。
    """
    return KcProjectionService(get_request_settings(request))


#: 幂等写操作的返回数据类型（泛型参数）
TData = TypeVar("TData")


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
# ID1 部门树
# ===========================================================================
@router.get(
    "/organizations/{org_id}/departments",
    response_model=ApiResponse[DepartmentTreeData],
    summary="ID1 部门层级",
    description=(
        "查询组织部门层级。`flat=true` 返回平铺数组，否则返回树；"
        "每项包含从根到本部门的名称链与成员数。登录即可查看。"
    ),
)
async def list_departments(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
    flat: bool = Query(False, description="是否返回平铺数组"),
) -> ApiResponse[DepartmentTreeData]:
    """ID1 部门层级。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        org_id: 组织 ID。
        flat: 是否平铺返回。

    Returns:
        ApiResponse[DepartmentTreeData]: 部门层级。
    """
    service = _department_service(request)
    return ApiResponse[DepartmentTreeData].ok(
        await service.tree(tenant_id=context.tenant_id, org_id=org_id, flat=flat)
    )


# ===========================================================================
# ID2 新建部门
# ===========================================================================
@router.post(
    "/organizations/{org_id}/departments",
    response_model=ApiResponse[CreateDepartmentData],
    summary="ID2 新建部门",
    description=(
        "在指定上级部门下新建部门；层级最多 5 层，超出返回 14006。"
        "写接口，需携带 Idempotency-Key 请求头；需 team:structure:manage 权限。"
    ),
)
async def create_department(
    request: Request,
    body: CreateDepartmentRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
) -> ApiResponse[CreateDepartmentData]:
    """ID2 新建部门。

    Args:
        request: FastAPI 请求对象。
        body: 新建请求。
        context: 请求上下文。
        manager: 幂等管理器。
        org_id: 组织 ID。

    Returns:
        ApiResponse[CreateDepartmentData]: 新部门信息。

    Raises:
        ApiError: 上级部门不存在（14005）、层级超限（14006）、权限不足（20003）。
    """
    service = _department_service(request)
    projection = _projection_service(request)

    async def factory() -> CreateDepartmentData:
        created = await service.create(
            tenant_id=context.tenant_id, org_id=org_id, request=body, auth=context.auth
        )
        # D-13：业务库为权威源，向 KC 的单向投影异步补登（失败不回滚业务）
        await projection.enqueue_group_upsert(
            tenant_id=context.tenant_id,
            group_id=created.department_id,
            name=created.name,
            parent_id=created.parent_id,
        )
        return created

    return await _respond_with_idempotency(
        request,
        context,
        manager,
        factory,
        hash_request_body(body.model_dump_json()),
    )


# ===========================================================================
# ID3 修改 / 移动部门
# ===========================================================================
@router.patch(
    "/departments/{department_id}",
    response_model=ApiResponse[UpdateDepartmentData],
    summary="ID3 修改或移动部门",
    description=(
        "修改部门名称、负责人，或把部门（含子树）移动到新的上级部门。"
        "移动到自己的下级部门返回 14006。写接口，需携带 Idempotency-Key 请求头；"
        "需 team:structure:manage 权限。"
    ),
)
async def update_department(
    request: Request,
    body: UpdateDepartmentRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    department_id: str = Path(..., description="部门 ID", min_length=3, max_length=128),
) -> ApiResponse[UpdateDepartmentData]:
    """ID3 修改或移动部门。

    Args:
        request: FastAPI 请求对象。
        body: 更新请求。
        context: 请求上下文。
        manager: 幂等管理器。
        department_id: 部门 ID。

    Returns:
        ApiResponse[UpdateDepartmentData]: 更新后的部门信息。

    Raises:
        ApiError: 部门不存在（14005）、层级/移动冲突（14006）、权限不足（20003）。
    """
    service = _department_service(request)
    projection = _projection_service(request)

    async def factory() -> UpdateDepartmentData:
        updated = await service.update(
            tenant_id=context.tenant_id,
            department_id=department_id,
            request=body,
            auth=context.auth,
        )
        await projection.enqueue_group_upsert(
            tenant_id=context.tenant_id,
            group_id=updated.department_id,
            name=updated.name,
            parent_id=updated.parent_id,
        )
        return updated

    return await _respond_with_idempotency(
        request,
        context,
        manager,
        factory,
        hash_request_body(body.model_dump_json()),
    )


# ===========================================================================
# ID4 删除部门
# ===========================================================================
@router.delete(
    "/departments/{department_id}",
    response_model=ApiResponse[DeleteDepartmentData],
    summary="ID4 删除部门",
    description=(
        "删除部门；该部门下仍有成员或子部门时返回 14006（请先迁移）。"
        "写接口，需携带 Idempotency-Key 请求头；需 team:structure:manage 权限。"
    ),
)
async def delete_department(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    department_id: str = Path(..., description="部门 ID", min_length=3, max_length=128),
) -> ApiResponse[DeleteDepartmentData]:
    """ID4 删除部门。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        department_id: 部门 ID。

    Returns:
        ApiResponse[DeleteDepartmentData]: 删除结果。

    Raises:
        ApiError: 部门不存在（14005）、仍有成员或子部门（14006）。
    """
    service = _department_service(request)
    projection = _projection_service(request)

    async def factory() -> DeleteDepartmentData:
        deleted = await service.delete(
            tenant_id=context.tenant_id, department_id=department_id, auth=context.auth
        )
        await projection.enqueue_group_delete(
            tenant_id=context.tenant_id, group_id=deleted.department_id
        )
        return deleted

    return await _respond_with_idempotency(request, context, manager, factory, EMPTY_BODY_DIGEST)


# ===========================================================================
# ID5 权限点目录
# ===========================================================================
@router.get(
    "/permissions",
    response_model=ApiResponse[PermissionCatalogData],
    summary="ID5 权限点目录",
    description="查询全部权限点（按分组），用于角色权限配置界面。登录即可查看。",
)
async def list_permissions(
    request: Request,
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[PermissionCatalogData]:
    """ID5 权限点目录。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。

    Returns:
        ApiResponse[PermissionCatalogData]: 分组后的权限点。
    """
    return ApiResponse[PermissionCatalogData].ok(
        await _role_service(request).permission_catalog(tenant_id=context.tenant_id)
    )


# ===========================================================================
# ID6 角色列表
# ===========================================================================
@router.get(
    "/organizations/{org_id}/roles",
    response_model=ApiResponse[RoleListData],
    summary="ID6 角色列表",
    description="查询组织内角色（含权限点与引用成员数）。需 team:role:manage 权限。",
)
async def list_roles(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
) -> ApiResponse[RoleListData]:
    """ID6 角色列表。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        org_id: 组织 ID。

    Returns:
        ApiResponse[RoleListData]: 角色列表。
    """
    return ApiResponse[RoleListData].ok(
        await _role_service(request).list_roles(
            tenant_id=context.tenant_id, org_id=org_id, auth=context.auth
        )
    )


# ===========================================================================
# ID7 删除角色
# ===========================================================================
@router.delete(
    "/organizations/{org_id}/roles/{role_id}",
    response_model=ApiResponse[DeleteRoleData],
    summary="ID7 删除角色",
    description=(
        "删除自定义角色；内置角色或仍有成员引用的角色不可删除（20005，文案含成员数）。"
        "写接口，需携带 Idempotency-Key 请求头；需 team:role:manage 权限。"
    ),
)
async def delete_role(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
    role_id: str = Path(..., description="角色 ID", min_length=3, max_length=128),
) -> ApiResponse[DeleteRoleData]:
    """ID7 删除角色。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        org_id: 组织 ID。
        role_id: 角色 ID。

    Returns:
        ApiResponse[DeleteRoleData]: 删除结果。

    Raises:
        ApiError: 角色不存在（14003）、仍被引用（20005）。
    """
    service = _role_service(request)

    async def factory() -> DeleteRoleData:
        return await service.delete_role(
            tenant_id=context.tenant_id, org_id=org_id, role_id=role_id, auth=context.auth
        )

    return await _respond_with_idempotency(request, context, manager, factory, EMPTY_BODY_DIGEST)


# ===========================================================================
# ID8 修改成员
# ===========================================================================
@router.patch(
    "/organizations/{org_id}/members/{member_id}",
    response_model=ApiResponse[UpdateMemberData],
    summary="ID8 修改成员",
    description=(
        "变更成员角色、所属部门、主部门或在册状态。"
        "改企业所有者返回 14007；改自己或同级/更高级成员返回 14008。"
        "写接口，需携带 Idempotency-Key 请求头；需 team:member:update 权限。"
    ),
)
async def update_member(
    request: Request,
    body: UpdateMemberRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
    member_id: str = Path(..., description="成员用户 ID", min_length=3, max_length=128),
) -> ApiResponse[UpdateMemberData]:
    """ID8 修改成员。

    Args:
        request: FastAPI 请求对象。
        body: 变更请求。
        context: 请求上下文。
        manager: 幂等管理器。
        org_id: 组织 ID。
        member_id: 成员用户 ID。

    Returns:
        ApiResponse[UpdateMemberData]: 变更后的成员状态。

    Raises:
        ApiError: 用户不存在（14001）、所有者保护（14007）、同级保护（14008）。
    """
    service = _member_service(request)
    projection = _projection_service(request)

    async def factory() -> UpdateMemberData:
        updated = await service.update_member(
            tenant_id=context.tenant_id,
            org_id=org_id,
            member_id=member_id,
            request=body,
            auth=context.auth,
        )
        await projection.enqueue_user_groups(
            tenant_id=context.tenant_id,
            user_id=member_id,
            group_ids=updated.department_ids,
        )
        return updated

    return await _respond_with_idempotency(
        request,
        context,
        manager,
        factory,
        hash_request_body(body.model_dump_json()),
    )


# ===========================================================================
# ID9 移出成员
# ===========================================================================
@router.delete(
    "/organizations/{org_id}/members/{member_id}",
    response_model=ApiResponse[RemoveMemberData],
    summary="ID9 移出成员",
    description=(
        "把成员移出企业；其创建的知识库自动转交组织管理员。"
        "移出企业所有者返回 14007；移出自己或同级成员返回 14008。"
        "写接口，需携带 Idempotency-Key 请求头；需 team:member:update 权限。"
    ),
)
async def remove_member(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
    member_id: str = Path(..., description="成员用户 ID", min_length=3, max_length=128),
) -> ApiResponse[RemoveMemberData]:
    """ID9 移出成员。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        org_id: 组织 ID。
        member_id: 成员用户 ID。

    Returns:
        ApiResponse[RemoveMemberData]: 移出结果与转交库数量。

    Raises:
        ApiError: 用户不存在（14001）、所有者保护（14007）、同级保护（14008）。
    """
    service = _member_service(request)
    projection = _projection_service(request)

    async def factory() -> RemoveMemberData:
        removed = await service.remove_member(
            tenant_id=context.tenant_id, org_id=org_id, member_id=member_id, auth=context.auth
        )
        await projection.enqueue_user_groups(
            tenant_id=context.tenant_id, user_id=member_id, group_ids=[]
        )
        return removed

    return await _respond_with_idempotency(request, context, manager, factory, EMPTY_BODY_DIGEST)


# ===========================================================================
# ID10 批量成员操作
# ===========================================================================
@router.post(
    "/organizations/{org_id}/members/batch",
    response_model=ApiResponse[BatchMemberData],
    summary="ID10 批量变更成员",
    description=(
        "批量变更角色或批量移出成员，最多 100 位；逐条失败不中断，返回失败原因。"
        "写接口，需携带 Idempotency-Key 请求头；需 team:member:update 权限。"
    ),
)
async def batch_members(
    request: Request,
    body: BatchMemberRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    org_id: str = Path(..., description="组织 ID", min_length=3, max_length=128),
) -> ApiResponse[BatchMemberData]:
    """ID10 批量变更成员。

    Args:
        request: FastAPI 请求对象。
        body: 批量请求。
        context: 请求上下文。
        manager: 幂等管理器。
        org_id: 组织 ID。

    Returns:
        ApiResponse[BatchMemberData]: 成功条数与失败清单。

    Raises:
        ApiError: 权限不足（20003）、批量条数超限（20001）。
    """
    service = _member_service(request)

    async def factory() -> BatchMemberData:
        return await service.batch(
            tenant_id=context.tenant_id, org_id=org_id, request=body, auth=context.auth
        )

    # 批量接口必须含请求体摘要：同键换 member_ids 不得被静默回放（架构文档 12.3）
    return await _respond_with_idempotency(
        request,
        context,
        manager,
        factory,
        hash_request_body(body.model_dump_json()),
    )


# ===========================================================================
# ID11 操作动作目录
# ===========================================================================
@router.get(
    "/audit-logs/actions",
    response_model=ApiResponse[ActionCatalogData],
    summary="ID11 操作动作目录",
    description="查询操作记录页可筛选的动作清单（按分组）。登录即可查看。",
)
async def list_audit_actions(
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[ActionCatalogData]:
    """ID11 操作动作目录。

    Args:
        context: 请求上下文。

    Returns:
        ApiResponse[ActionCatalogData]: 分组后的动作清单。
    """
    del context
    return ApiResponse[ActionCatalogData].ok(
        ActionCatalogData(
            groups=[
                ActionGroup(
                    group=entry.group,
                    group_name=entry.group_name,
                    actions=[
                        ActionItem(action=action, display_name=display_name)
                        for action, display_name in entry.actions
                    ],
                )
                for entry in action_groups()
            ]
        )
    )


__all__: list[str] = [
    "EMPTY_BODY_DIGEST",
    "batch_members",
    "create_department",
    "delete_department",
    "delete_role",
    "list_audit_actions",
    "list_departments",
    "list_permissions",
    "list_roles",
    "remove_member",
    "router",
    "update_department",
    "update_member",
]
