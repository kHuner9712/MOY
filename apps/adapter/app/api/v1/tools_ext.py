"""工具服务扩展端点（阶段二 B06，TL1 / 架构文档 §4.3）。

| # | 端点 | 用途 |
| --- | --- | --- |
| TL1 | `GET /api/v1/tools/executions` | 办公工具调用记录（最近 20 条起，支持工具类型过滤）|

设计要点：

1. **只读**：TL1 不写任何状态，也不写幂等键（GET 无需 `Idempotency-Key`）；
2. **业务语言**：条目含工具名与操作名的中文文案，失败项只给业务原因；
3. **权限**：`tool:connection:manage`（无权限时 T1-T5 只读，本端点直接拒绝）；
4. **T1-T7 零改动**：本模块独立挂载，既有 `/tools/*` 契约不变。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.security import RequestContext, get_request_context, require_permission
from app.schemas.common import ApiResponse, PageResult
from app.schemas.tool_ext import ListExecutionsQuery, ToolExecutionItem
from app.services.tool_service import get_tool_service

router = APIRouter(prefix="/tools", tags=["工具服务"])

#: TL1 所需权限点（前端 `PERM.TOOL_MANAGE`）
TOOL_MANAGE_PERMISSION = "tool:connection:manage"


@router.get(
    "/executions",
    response_model=ApiResponse[PageResult[ToolExecutionItem]],
    summary="TL1 工具调用记录",
    description=(
        "查询办公工具的调用记录（分页，默认按调用时间倒序）。"
        "失败项只展示业务语言原因，不含任何技术细节。"
        "需 tool:connection:manage 权限。"
    ),
)
async def list_executions(
    context: RequestContext = Depends(get_request_context),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    tool_type: str = Query("", max_length=64, description="按工具类型过滤"),
) -> ApiResponse[PageResult[ToolExecutionItem]]:
    """TL1 工具调用记录。

    Args:
        context: 请求上下文。
        page: 页码。
        page_size: 每页条数。
        tool_type: 按工具类型过滤。

    Returns:
        ApiResponse[PageResult[ToolExecutionItem]]: 分页结果。

    Raises:
        ApiError: 权限不足（20003）。
    """
    require_permission(context.auth, TOOL_MANAGE_PERMISSION)
    query = ListExecutionsQuery(page=page, page_size=page_size, tool_type=tool_type)
    result = await get_tool_service().list_executions(query, context.auth)
    return ApiResponse[PageResult[ToolExecutionItem]].ok(result)


__all__ = [
    "TOOL_MANAGE_PERMISSION",
    "list_executions",
    "router",
]
