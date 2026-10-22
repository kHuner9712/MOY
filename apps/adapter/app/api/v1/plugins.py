"""插件域端点（阶段三 B17：PL1-PL5 + PL13）。

路径前缀 `/api/v1/plugins`（与既有资源域共用 `/api/v1`，不新增版本）。

| # | 端点 | 批 | 判权 |
| --- | --- | --- | --- |
| PL1 | `GET /plugins/market` | B17 | 登录即可 |
| PL2 | `GET /plugins/market/{plugin_id}` | B17 | 登录即可 |
| PL3 | `GET /plugins/installed` | B17 | 登录即可（非管理员只见 enabled） |
| PL4 | `GET /plugins/{plugin_id}/capabilities` | B17 | 登录即可 |
| PL5 | `POST /plugins/{plugin_id}/install` | B17 | 登录即可（成员可自行添加，Q3-2） |
| PL13 | `PUT /plugins/capability-policy` | B17 | `team:role:manage` |
| PL6-PL12 | 生命周期/设置/评价 | B18 | — |

**两条与套餐有关的红线**（PRD v1.1 勘误，QA 会盯）：

1. 市场/详情/能力清单**不受套餐影响**——免费版照样返回 6 个插件，
   只是 `installable=false`；`15001` 只在安装时抛；
2. 企业能力开关状态**逐能力由端点返回**（`org_switch_enabled`），
   前端不得硬编码或按套餐推导。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.api.v1.plugin_deps import get_plugins_service
from app.business.plugin_service import PluginService
from app.business.policy_service import PolicyService, get_policy_service
from app.core.config import get_request_settings
from app.core.errors import ErrorCode
from app.core.idempotency import IdempotencyManager, get_idempotency_manager, hash_request_body
from app.core.logging import get_logger
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.schemas.common import ApiResponse
from app.schemas.plugin import (
    CapabilityListData,
    CapabilityPolicyData,
    CapabilityPolicyRequest,
    InstallData,
    InstalledData,
    InstallRequest,
    MarketData,
    MarketDetailData,
)

logger = get_logger(__name__)

#: 企业能力开关所需权限点（复用阶段二 20 权限点之一，零新增）
PLUGIN_POLICY_PERMISSION = "team:role:manage"

router = APIRouter(prefix="/plugins", tags=["插件"])


def _policy_service(request: Request) -> PolicyService:
    """按请求生效配置构造判权服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        PolicyService: 判权服务。
    """
    return get_policy_service(get_request_settings(request))


def _service(request: Request) -> PluginService:
    """按请求生效配置构造插件服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        PluginService: 插件服务。
    """
    return get_plugins_service(request)


# ===========================================================================
# PL1 市场列表
# ===========================================================================
@router.get(
    "/market",
    response_model=ApiResponse[MarketData],
    summary="PL1 插件市场",
    description=(
        "浏览可添加的插件（按分类与关键字筛选，支持推荐/最新/评分排序）。"
        "**不区分套餐**：免费版同样返回全部插件，只在每个插件上标注是否可添加，"
        "以及不可添加的原因。登录即可查看。"
    ),
)
async def list_market(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    category: str = Query("", max_length=16, description="分类筛选"),
    keyword: str = Query("", max_length=64, description="名称或说明关键字"),
    sort: Literal["recommended", "newest", "rating"] = Query("recommended", description="排序方式"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(12, ge=1, le=60, description="每页条数"),
) -> ApiResponse[MarketData]:
    """PL1 插件市场。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        category: 分类筛选。
        keyword: 关键字。
        sort: 排序方式。
        page: 页码。
        page_size: 每页条数。

    Returns:
        ApiResponse[MarketData]: 市场列表。
    """
    data = await _service(request).market(
        auth=context.auth,
        category=category,
        keyword=keyword,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return ApiResponse[MarketData].ok(data)


# ===========================================================================
# PL2 市场详情
# ===========================================================================
@router.get(
    "/market/{plugin_id}",
    response_model=ApiResponse[MarketDetailData],
    summary="PL2 插件详情",
    description=(
        "查看插件详情：能力清单（含每项能力的作用与不同意的后果）、步骤名称、"
        "产物说明、配置项与版本历史。已下架插件返回「该插件已下架」。登录即可查看。"
    ),
)
async def get_market_detail(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    plugin_id: str = Path(..., description="插件标识", min_length=6, max_length=44),
) -> ApiResponse[MarketDetailData]:
    """PL2 插件详情。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        plugin_id: 插件标识。

    Returns:
        ApiResponse[MarketDetailData]: 插件详情。

    Raises:
        ApiError: 插件不存在或已下架（16002）。
    """
    data = await _service(request).detail(plugin_id=plugin_id, auth=context.auth)
    return ApiResponse[MarketDetailData].ok(data)


# ===========================================================================
# PL3 已安装列表
# ===========================================================================
@router.get(
    "/installed",
    response_model=ApiResponse[InstalledData],
    summary="PL3 已添加的插件",
    description=(
        "查看本企业已添加的插件、当前版本与是否有可用更新。"
        "受限状态的插件只对管理员可见，普通成员看到的是「不存在的插件」。"
        "登录即可查看。"
    ),
)
async def list_installed(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    status: str = Query("", max_length=16, description="状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
) -> ApiResponse[InstalledData]:
    """PL3 已添加的插件。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        status: 状态筛选。
        page: 页码。
        page_size: 每页条数。

    Returns:
        ApiResponse[InstalledData]: 已添加列表。
    """
    data = await _service(request).installed(
        auth=context.auth, status=status, page=page, page_size=page_size
    )
    return ApiResponse[InstalledData].ok(data)


# ===========================================================================
# PL4 能力清单
# ===========================================================================
@router.get(
    "/{plugin_id}/capabilities",
    response_model=ApiResponse[CapabilityListData],
    summary="PL4 插件能力清单",
    description=(
        "查看某插件需要哪些能力、每项能力为什么需要、不同意会有什么影响，"
        "以及**逐能力**的企业开放状态（企业能力需管理员在团队设置中开启）。"
        "登录即可查看。"
    ),
)
async def list_capabilities(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    plugin_id: str = Path(..., description="插件标识", min_length=6, max_length=44),
) -> ApiResponse[CapabilityListData]:
    """PL4 能力清单。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        plugin_id: 插件标识。

    Returns:
        ApiResponse[CapabilityListData]: 能力清单。

    Raises:
        ApiError: 插件不存在（16002）。
    """
    data = await _service(request).capabilities(plugin_id=plugin_id, auth=context.auth)
    return ApiResponse[CapabilityListData].ok(data)


# ===========================================================================
# PL5 安装
# ===========================================================================
@router.post(
    "/{plugin_id}/install",
    response_model=ApiResponse[InstallData],
    summary="PL5 添加插件",
    description=(
        "添加插件并立即启用。必须逐项同意该插件声明的全部必需能力；"
        "涉及企业能力（读取通讯录、写入仓库、对外发布）时还需管理员先在团队设置中开启。"
        "写接口，需携带 Idempotency-Key 请求头；登录即可操作。"
    ),
)
async def install_plugin(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    body: InstallRequest = Body(...),
    plugin_id: str = Path(..., description="插件标识", min_length=6, max_length=44),
) -> ApiResponse[InstallData]:
    """PL5 添加插件。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        body: 安装请求。
        plugin_id: 插件标识。

    Returns:
        ApiResponse[InstallData]: 安装结果。

    Raises:
        ApiError: 套餐不支持（15001）、插件不可用（16002）、缺少能力（16005）、
            企业未开放（16006）、前置插件缺失（16009）、参数非法（20001）。
    """
    service = _service(request)

    async def factory() -> InstallData:
        """执行安装。

        Returns:
            InstallData: 安装结果。
        """
        return await service.install(plugin_id=plugin_id, request=body, auth=context.auth)

    result, replayed = await run_idempotent(
        request, context, factory, manager, body_digest=hash_request_body(body.model_dump_json())
    )
    if replayed:
        return ApiResponse[InstallData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message="本次操作已处理，无需重复提交",
            data=result,
        )
    return ApiResponse[InstallData].ok(result)


# ===========================================================================
# PL13 企业能力开关
# ===========================================================================
@router.put(
    "/capability-policy",
    response_model=ApiResponse[CapabilityPolicyData],
    summary="PL13 企业能力开关",
    description=(
        "管理员开启或关闭企业级能力（读取通讯录、写入代码仓库、对外发布内容）。"
        "个人能力始终开启，不受此处影响。写后返回全量开关。"
        "需团队角色配置权限；写接口，需携带 Idempotency-Key 请求头。"
    ),
)
async def set_capability_policy(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    body: CapabilityPolicyRequest = Body(...),
) -> ApiResponse[CapabilityPolicyData]:
    """PL13 企业能力开关。

    Args:
        request: FastAPI 请求对象。
        context: 请求上下文。
        manager: 幂等管理器。
        body: 开关列表。

    Returns:
        ApiResponse[CapabilityPolicyData]: 全量开关。

    Raises:
        ApiError: 权限不足（20003）、含个人能力（20001）。
    """
    await _policy_service(request).require_point(context.auth, PLUGIN_POLICY_PERMISSION)
    service = _service(request)

    async def factory() -> CapabilityPolicyData:
        """写入开关。

        Returns:
            CapabilityPolicyData: 全量开关。
        """
        return await service.set_capability_policy(request=body, auth=context.auth)

    result, replayed = await run_idempotent(
        request, context, factory, manager, body_digest=hash_request_body(body.model_dump_json())
    )
    if replayed:
        return ApiResponse[CapabilityPolicyData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message="本次操作已处理，无需重复提交",
            data=result,
        )
    return ApiResponse[CapabilityPolicyData].ok(result)


__all__: list[str] = [
    "PLUGIN_POLICY_PERMISSION",
    "get_market_detail",
    "install_plugin",
    "list_capabilities",
    "list_installed",
    "list_market",
    "router",
    "set_capability_policy",
]
