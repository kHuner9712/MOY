"""运营侧 MCP server 登记端点（矩阵第 3 项，2026-11-06）。

只增不改：全部挂在 `/api/v1/ops/mcp-servers`，不动 OPS1-OPS8 的路径与响应。

| 方法 | 路径 | 权限点 |
| --- | --- | --- |
| GET | `/ops/mcp-servers?tenant_id=` | `ops:console:access` |
| POST | `/ops/mcp-servers` | `ops:settings:manage` |
| DELETE | `/ops/mcp-servers/{server_id}?tenant_id=` | `ops:settings:manage` |
| POST | `/ops/mcp-servers/{server_id}/probe?tenant_id=` | `ops:settings:manage` |

企业令牌调用任一端点 → `20003`（由 `core.security.require_ops_access` 统一拦截）。
目标企业必须显式给出：运营可以跨企业登记，但**不允许**把平台哨兵当企业写进表里。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.business.mcp_service import McpServerService
from app.core.config import get_request_settings
from app.core.security import (
    RequestContext,
    get_request_context,
    require_ops_access,
)
from app.schemas.common import ApiResponse
from app.schemas.mcp import (
    DeleteMcpServerData,
    McpServerItem,
    McpServerListData,
    McpServerWriteData,
    ProbeMcpServerData,
    UpsertMcpServerRequest,
)
from app.services.mcp_runner import McpRunner

#: 运营后台访问权限点
OPS_CONSOLE_PERMISSION = "ops:console:access"

#: 系统配置权限点（登记/删除/探测属于配置写操作）
OPS_SETTINGS_PERMISSION = "ops:settings:manage"

router = APIRouter(prefix="/ops/mcp-servers", tags=["运营管理"])


def _service(request: Request) -> McpServerService:
    """按请求生效配置构造服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        McpServerService: MCP 服务。
    """
    return McpServerService(get_request_settings(request))


def _runner(request: Request) -> McpRunner:
    """按请求生效配置构造受控调用器。

    Args:
        request: FastAPI 请求对象。

    Returns:
        McpRunner: MCP 调用器。
    """
    return McpRunner(get_request_settings(request))


# ===========================================================================
# GET 登记列表
# ===========================================================================
@router.get(
    "",
    response_model=ApiResponse[McpServerListData],
    summary="MCP1 外部服务登记列表",
    description=(
        "列出指定企业已登记的外部工具服务。响应不含凭据明文与凭据编号，"
        "只给出是否已配置凭据。需 ops:console:access 权限。"
    ),
)
async def list_mcp_servers(
    request: Request,
    tenant_id: str = Query(min_length=2, max_length=64, description="目标企业"),
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[McpServerListData]:
    """列出登记。

    Args:
        request: FastAPI 请求对象。
        tenant_id: 目标企业。
        context: 鉴权上下文。

    Returns:
        ApiResponse[McpServerListData]: 登记列表。

    Raises:
        ApiError: 非运营账号（20003）。
    """
    await require_ops_access(request, context, OPS_CONSOLE_PERMISSION)
    records = await _service(request).list_servers(tenant_id)
    return ApiResponse[McpServerListData].ok(
        McpServerListData(
            items=[McpServerItem.from_record(record) for record in records],
            total=len(records),
        )
    )


# ===========================================================================
# POST 登记 / 更新
# ===========================================================================
@router.post(
    "",
    response_model=ApiResponse[McpServerWriteData],
    summary="MCP2 登记外部服务",
    description=(
        "声明式登记或更新一台外部工具服务。凭据只接受平台凭据编号，"
        "不接受明文密钥；默认关闭，需显式 enabled=true 才会被任务使用。"
    ),
)
async def upsert_mcp_server(
    request: Request,
    payload: UpsertMcpServerRequest = Body(...),
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[McpServerWriteData]:
    """登记或更新。

    Args:
        request: FastAPI 请求对象。
        payload: 登记内容。
        context: 鉴权上下文。

    Returns:
        ApiResponse[McpServerWriteData]: 落库后的登记。

    Raises:
        ApiError: 非运营账号（20003）、登记不合法（20001）。
    """
    await require_ops_access(request, context, OPS_SETTINGS_PERMISSION)
    record = await _service(request).register_server(payload.tenant_id, payload.model_dump())
    return ApiResponse[McpServerWriteData].ok(
        McpServerWriteData(item=McpServerItem.from_record(record))
    )


# ===========================================================================
# DELETE 删除登记
# ===========================================================================
@router.delete(
    "/{server_id}",
    response_model=ApiResponse[DeleteMcpServerData],
    summary="MCP3 删除外部服务",
    description="删除指定企业的外部服务登记。删除后插件步骤会立即失败，不会继续调用外部代码。",
)
async def delete_mcp_server(
    request: Request,
    server_id: str = Path(min_length=4, max_length=64),
    tenant_id: str = Query(min_length=2, max_length=64, description="目标企业"),
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[DeleteMcpServerData]:
    """删除登记。

    Args:
        request: FastAPI 请求对象。
        server_id: 登记 ID。
        tenant_id: 目标企业。
        context: 鉴权上下文。

    Returns:
        ApiResponse[DeleteMcpServerData]: 删除结果。

    Raises:
        ApiError: 非运营账号（20003）、资源不存在（20004）。
    """
    await require_ops_access(request, context, OPS_SETTINGS_PERMISSION)
    await _service(request).delete_server(tenant_id, server_id)
    return ApiResponse[DeleteMcpServerData].ok(DeleteMcpServerData(deleted=True))


# ===========================================================================
# POST 连通性探测
# ===========================================================================
@router.post(
    "/{server_id}/probe",
    response_model=ApiResponse[ProbeMcpServerData],
    summary="MCP4 探测外部服务",
    description=(
        "连接一次并读取工具列表，用于登记后确认可用性。"
        "失败只返回分类（超时/连不上/鉴权/协议），不返回地址、凭据或外部错误原文。"
    ),
)
async def probe_mcp_server(
    request: Request,
    server_id: str = Path(min_length=4, max_length=64),
    tenant_id: str = Query(min_length=2, max_length=64, description="目标企业"),
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[ProbeMcpServerData]:
    """探测外部服务。

    Args:
        request: FastAPI 请求对象。
        server_id: 登记 ID。
        tenant_id: 目标企业。
        context: 鉴权上下文。

    Returns:
        ApiResponse[ProbeMcpServerData]: 探测结果。

    Raises:
        ApiError: 非运营账号（20003）、资源不存在（20004）。
    """
    await require_ops_access(request, context, OPS_SETTINGS_PERMISSION)
    result = await _runner(request).probe(tenant_id, server_id)
    return ApiResponse[ProbeMcpServerData].ok(ProbeMcpServerData(**result))


__all__: list[str] = ["router"]
