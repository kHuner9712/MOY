"""v1 路由汇总（架构文档 6.1 `api/v1/router.py`）。

职责：
- 把五大服务域的 30 个核心端点挂载到 `/api/v1` 前缀下；
- 挂载 2 个免鉴权健康探针（`/healthz`、`/readyz`，架构文档 10.2 D-07）；
- 阶段二起追加 5 个新域模块（`knowledge_ext` / `tools_ext` / `identity_ext` /
  `billing` / `ops`），**共用同一 `/api/v1` 前缀**，不新增版本（架构文档 2.2 第 2 条）。

端点总览（阶段一核心基线，任何人不得修改，只能通过 `/api/v2` 演进）：
| 域 | 前缀 | 端点数 | 编号 |
| --- | --- | --- | --- |
| 模型服务 | `/api/v1/model` | 4 | M1-M4 |
| RAG 服务 | `/api/v1/knowledge` | 7 | R1-R7 |
| Agent 服务 | `/api/v1/agent` | 6 | A1-A6 |
| 工具服务 | `/api/v1/tools` | 7 | T1-T7 |
| 用户权限 | `/api/v1/identity` | 6 | U1-U6 |
| 健康检查 | `/healthz` `/readyz` | 2 | 免鉴权 |

阶段二新域基线（`PHASE2_DOMAIN_ENDPOINTS = 38`）：
KB1-KB8 / TL1 / ID1-ID11 / BL1-BL10 / OPS1-OPS8，按 B04-B10 逐批落地。

不兼容变更必须新增 `/api/v2` 汇总，禁止在 v1 内破坏契约（架构文档第 3 章前言）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.v1 import agent, identity, knowledge_ext, model, rag, tool
from app.core.config import Settings, get_request_settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import enforce_ratelimit
from app.db.engine import check_engine, get_engine
from app.registry.loader import get_profile_routes, get_tool_registry
from app.schemas.common import ApiResponse, HealthData, ReadyCheck, ReadyData

logger = get_logger(__name__)

#: 阶段一核心端点基线（30 条，冻结；语义与 `ADAPTER_CORE_ENDPOINTS` 完全相同，
#: 保留旧名是为了既有测试与 CI 引用的**向后兼容**，架构文档 2.2 第 1 条）
TOTAL_BUSINESS_ENDPOINTS: int = 30

#: 适配层核心端点基线（30 条，冻结；M1-M4 / R1-R7 / A1-A6 / T1-T7 / U1-U6）
ADAPTER_CORE_ENDPOINTS: int = 30

#: 阶段二新域端点基线（38 条；KB1-8 / TL1 / ID1-11 / BL1-10 / OPS1-8）
#: 现阶段（B02）为 0 条已实现，基线文件 `docs/api/baseline-phase2-38.json` 由 B03 起逐步补齐
PHASE2_DOMAIN_ENDPOINTS: int = 38

#: 健康检查路由（免鉴权，不挂 API 版本前缀）
health_router = APIRouter(tags=["健康检查"])


@health_router.get(
    "/healthz",
    response_model=ApiResponse[HealthData],
    summary="存活探针（免鉴权）",
    description="进程存活检查，不校验任何依赖。",
)
async def healthz(request: Request) -> ApiResponse[HealthData]:
    """存活探针。

    配置取自 `app.state.settings`（注入优先），保证反映应用真实生效的配置。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ApiResponse[HealthData]: `code=0` 的健康数据。
    """
    settings = get_request_settings(request)
    return ApiResponse[HealthData].ok(
        HealthData(
            status="ok",
            app=settings.app_name,
            version=settings.app_version,
            env=settings.env,
            deploy_mode=settings.deploy_mode,
            downstream_enabled=settings.downstream_enabled,
        )
    )


@health_router.get(
    "/readyz",
    response_model=ApiResponse[ReadyData],
    summary="就绪探针（免鉴权）",
    description="就绪检查：配置加载与注册表解析是否可用。",
)
async def readyz(request: Request) -> ApiResponse[ReadyData]:
    """就绪探针。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ApiResponse[ReadyData]: 就绪状态与检查项明细。

    Raises:
        ApiError: 配置或注册表不可用（30004），HTTP 状态码 500。
    """
    checks: list[ReadyCheck] = [
        ReadyCheck(
            name="config",
            ready=True,
            detail="配置已加载",
        )
    ]

    try:
        profiles = get_profile_routes().profiles
        checks.append(
            ReadyCheck(
                name="profile_routes",
                ready=bool(profiles),
                detail=f"已登记 {len(profiles)} 个能力档位",
            )
        )
    except ApiError:
        checks.append(ReadyCheck(name="profile_routes", ready=False, detail="档位路由表不可用"))

    try:
        tools = get_tool_registry().tool_types
        checks.append(
            ReadyCheck(
                name="tool_registry",
                ready=bool(tools),
                detail=f"已登记 {len(tools)} 个工具类型",
            )
        )
    except ApiError:
        checks.append(ReadyCheck(name="tool_registry", ready=False, detail="工具注册表不可用"))

    checks.append(await _check_database(request))

    all_ready = all(check.ready for check in checks)
    data = ReadyData(status="ready" if all_ready else "not_ready", checks=checks)
    if all_ready:
        return ApiResponse[ReadyData].ok(data)
    raise ApiError(ErrorCode.CONFIG_ERROR, message=data.status, data=data)


async def _check_database(request: Request) -> ReadyCheck:
    """数据库就绪检查项（阶段二 D-02）。

    `business_store=memory` 时直接 ready 并注明「内存模式」——
    内存模式不依赖 PostgreSQL，不能让 T01 离线演示与离线单测被判为未就绪。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ReadyCheck: 数据库检查项。
    """
    settings = get_request_settings(request)
    if not settings.uses_postgres:
        return ReadyCheck(name="database", ready=True, detail="内存模式（未启用业务库）")

    engine = getattr(request.app.state, "db_engine", None)
    if engine is None:
        engine = get_engine(settings)
    healthy = await check_engine(engine)
    return ReadyCheck(
        name="database",
        ready=healthy,
        detail="数据库连接正常" if healthy else "数据库连接异常",
    )


def include_all_routers(settings: Settings | None = None) -> APIRouter:
    """构建并返回全部 v1 路由汇总。

    每次调用返回**全新**的路由实例，避免重复挂载导致同一端点注册多次
    （测试中多次 `create_app()` 的常见陷阱）。

    五大业务路由统一挂载限流依赖 `enforce_ratelimit`（架构文档 3.0.3 错误码 20006），
    限流在鉴权之后生效；健康探针不参与限流。

    Args:
        settings: 可选配置实例，缺省读取全局单例（取 `api_prefix`）。

    Returns:
        APIRouter: 挂载完成的汇总路由。
    """
    conf = settings or get_settings()
    prefix = conf.api_prefix

    business_dependencies = [Depends(enforce_ratelimit)]

    api_router = APIRouter()
    api_router.include_router(model.router, prefix=prefix, dependencies=business_dependencies)
    api_router.include_router(rag.router, prefix=prefix, dependencies=business_dependencies)
    # 阶段二新域模块：与既有资源域共用前缀（架构文档 2.2 第 2 条），不新增版本
    api_router.include_router(
        knowledge_ext.router, prefix=prefix, dependencies=business_dependencies
    )
    api_router.include_router(agent.router, prefix=prefix, dependencies=business_dependencies)
    api_router.include_router(tool.router, prefix=prefix, dependencies=business_dependencies)
    api_router.include_router(identity.router, prefix=prefix, dependencies=business_dependencies)
    # 健康检查不挂 API 版本前缀，路径固定为 /healthz 与 /readyz
    api_router.include_router(health_router)
    return api_router


__all__ = [
    "ADAPTER_CORE_ENDPOINTS",
    "PHASE2_DOMAIN_ENDPOINTS",
    "TOTAL_BUSINESS_ENDPOINTS",
    "health_router",
    "healthz",
    "include_all_routers",
    "readyz",
]
