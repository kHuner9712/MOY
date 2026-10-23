"""租户数据权利端点 TD1/TD2（阶段四 T4-05.3，架构 §4.2 / PRD P4-05 ③④）。

| # | 端点 | 权限 | 幂等 |
| --- | --- | --- | --- |
| TD1 | `POST /api/v1/ops/tenant-data/export` | `team:role:manage`（owner / org_admin） | 是 |
| TD2 | `DELETE /api/v1/ops/tenant-data` | **owner 专用** + 逐字输入企业名称 | 是 |

**为什么挂在 `/ops/tenant-data` 而不是平台运营域**（容易看错的一处）：

路径里的 `/ops` 是**历史命名**，与 OPS1-OPS8 的平台运营端点不是一回事：

- OPS1-OPS8 的守卫是 `require_ops_access()`：**企业令牌一律 20003**，因为它们是
  平台运营方跨租户看数据；
- TD1/TD2 的守卫是 `guard_permission(auth, "team:role:manage")`：**平台令牌一律
  20003**，因为"代表企业行使数据权利"只能由该企业的管理员来做——运营账号不是
  任何企业的成员。

两者方向正好相反，所以本模块**不复用** `ops.py` 的 router，而是独立的
`/ops/tenant-data` 前缀。路径本身已冻结在 `docs/api/baseline-phase4-10.json`
与架构 §4.2，改路径会让端点门禁 G1/G3 立刻判红。

**为什么不新增"导出进度"端点**：端点预算 10 条已满（架构 §4.1 第 3 条），
因此导出做成同步（理由见 `app/business/tenant_data_service.py` 模块说明）。

**幂等**：两个端点都是写操作，必须带 `Idempotency-Key`（架构 §4.1 第 2 条）。
重放返回 `20007` + 首次的 `data`——对导出而言这很关键：同一个请求键重放不会
产生第二份几十兆的包，也不会让客户拿到两个不同的 `download_url`。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.business.tenant_data_service import TenantDataService
from app.core.config import Settings, get_request_settings
from app.core.errors import ApiError, ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.logging import get_logger
from app.core.security import (
    RequestContext,
    get_request_context,
    guard_permission,
    is_platform_scope,
    run_idempotent,
)
from app.schemas.common import ApiResponse
from app.schemas.tenant_data import (
    TenantDataDeleteData,
    TenantDataDeleteRequest,
    TenantDataExportData,
    TenantDataExportRequest,
)

logger = get_logger(__name__)

#: 导出/删除所需权限点（架构 §4.2：`team:role:manage`，即 owner / org_admin）
TENANT_DATA_PERMISSION = "team:role:manage"

#: 删除专用的角色（架构 §4.2："owner 专用 + 二次确认"）。
#:
#: 单独判角色而不是只判权限点：`org_admin` 也有 `team:role:manage`，
#: 但"把企业全部数据删掉"的后果不可撤销，只留给企业所有者。
OWNER_ROLE = "owner"

router = APIRouter(prefix="/ops/tenant-data", tags=["数据权利"])


def _service(request: Request) -> TenantDataService:
    """按请求生效配置构造数据权利服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        TenantDataService: 服务实例。
    """
    settings: Settings = get_request_settings(request)
    return TenantDataService(settings)


async def require_tenant_admin(
    context: RequestContext,
    permission: str = TENANT_DATA_PERMISSION,
    *,
    method: str = "",
    owner_only: bool = False,
) -> None:
    """校验「企业管理员」身份（数据权利端点统一入口）。

    判定顺序：

    1. **平台令牌直接拒绝**（20003）：运营账号不是任何企业的成员，
       不能代表企业导出或删除数据；`*` 通配角色同样落在这里；
    2. 权限点校验（`team:role:manage`，拒绝时由 `guard_permission` 写操作记录）；
    3. `owner_only` 时再要求 `owner` 角色。

    Args:
        context: 请求上下文。
        permission: 权限点。
        method: HTTP 方法（大写，进失败审计）。
        owner_only: 是否要求 owner 角色。

    Raises:
        ApiError: 平台令牌或权限不足（20003）。
    """
    if is_platform_scope(context.auth, context.tenant_id):
        logger.warning(
            "tenant_data_platform_denied",
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            permission=permission,
        )
        raise ApiError(
            ErrorCode.FORBIDDEN,
            internal_detail=(
                "tenant-data endpoint requires tenant scope; "
                f"tenant_id={context.tenant_id} roles={context.auth.roles}"
            ),
        )
    await guard_permission(context.auth, permission, method=method)
    if owner_only:
        roles = {str(role).strip().lower() for role in context.auth.roles if str(role).strip()}
        if OWNER_ROLE not in roles:
            logger.warning(
                "tenant_data_owner_required",
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                roles=list(context.auth.roles),
            )
            raise ApiError(
                ErrorCode.FORBIDDEN,
                internal_detail=(
                    f"tenant data deletion requires the owner role; roles={context.auth.roles}"
                ),
            )


@router.post(
    "/export",
    response_model=ApiResponse[TenantDataExportData],
    summary="TD1 导出企业数据",
    description=(
        "生成企业数据导出包（操作记录 / 用量 / 会话与消息 / 记忆 / 产物清单，JSON Lines）。"
        "包内附 manifest.json（每个文件的行数与摘要），生成完成后直接返回短期下载地址。"
        "数据量超过上限时提示分批导出，不会静默截断。需 team:role:manage 权限。"
    ),
)
async def export_tenant_data(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    body: TenantDataExportRequest | None = Body(default=None),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[TenantDataExportData]:
    """TD1 导出企业数据。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        body: 导出范围（缺省全部 5 类）。
        manager: 幂等管理器。

    Returns:
        ApiResponse[TenantDataExportData]: 导出结果与下载地址。

    Raises:
        ApiError: 平台令牌或无权限（20003）、范围非法或数据量超限（20001）、
            私有化部署（20001）。
    """
    await require_tenant_admin(context, method="POST")
    service = _service(request)
    scopes = body.scope if body is not None else None
    tenant_id = context.tenant_id
    user_id = context.user_id

    async def factory() -> TenantDataExportData:
        result = await service.export(tenant_id, scopes, actor_id=user_id)
        return TenantDataExportData(
            export_id=result.export_id,
            status=result.status,
            download_url=result.download_url,
            expires_at=result.expires_at,
            rows=dict(result.rows),
            size_bytes=result.size_bytes,
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[TenantDataExportData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[TenantDataExportData].ok(result)


@router.delete(
    "",
    response_model=ApiResponse[TenantDataDeleteData],
    summary="TD2 删除企业数据",
    description=(
        "二次确认后硬删除企业数据（操作记录 / 用量 / 会话与消息 / 记忆 / 产物）。"
        "需逐字输入企业名称；删除会先停用企业以阻断后续写入，并生成删除证明。"
        "操作记录受「只增不改」保护，需由运维按证明里的说明单独清理。仅企业所有者可用。"
    ),
)
async def delete_tenant_data(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    body: TenantDataDeleteRequest = Body(...),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[TenantDataDeleteData]:
    """TD2 删除企业数据。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        body: 二次确认文本与删除范围。
        manager: 幂等管理器。

    Returns:
        ApiResponse[TenantDataDeleteData]: 两段式删除结果与证明下载地址。

    Raises:
        ApiError: 平台令牌 / 非 owner（20003）、确认文本不匹配（18003）、
            企业不存在（20004）、范围非法或私有化部署（20001）。
    """
    await require_tenant_admin(context, method="DELETE", owner_only=True)
    service = _service(request)
    tenant_id = context.tenant_id
    user_id = context.user_id
    confirm_text = body.confirm_text
    scopes = body.scope

    async def factory() -> TenantDataDeleteData:
        result = await service.delete(tenant_id, confirm_text, scopes, actor_id=user_id)
        return TenantDataDeleteData(
            deletion_id=result.deletion_id,
            deleted_at=result.deleted_at,
            deleted_tables=list(result.deleted_tables),
            deleted_rows=dict(result.deleted_rows),
            skipped_tables=list(result.skipped_tables),
            retained_tables=list(result.retained_tables),
            audit_pending_dba=result.audit_pending_dba,
            audit_sql_hint=result.audit_sql_hint,
            proof_download_url=result.proof_download_url,
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[TenantDataDeleteData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[TenantDataDeleteData].ok(result)


__all__: list[str] = [
    "OWNER_ROLE",
    "TENANT_DATA_PERMISSION",
    "delete_tenant_data",
    "export_tenant_data",
    "require_tenant_admin",
    "router",
]
