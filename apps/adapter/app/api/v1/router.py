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

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.api.v1 import (
    agent,
    artifact_files,
    billing,
    capability,
    conversation,
    identity,
    identity_ext,
    knowledge_ext,
    memory,
    model,
    ops,
    plugins,
    rag,
    stats,
    tenant_data,
    tool,
    tools_ext,
)
from app.core.config import Settings, get_request_settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import enforce_ratelimit
from app.db.engine import check_engine, check_engine_privilege, get_engine
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

#: 阶段三插件域端点基线（14 条：PL1-PL13 + WF1），**阶段二 38 条一个字节不改**。
#: 计数由 `tests/integration/test_endpoint_baseline.py` 与
#: `deploy/validate_openapi.py` 双向断言。
PHASE3_DOMAIN_ENDPOINTS: int = 14

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
    "/metrics",
    summary="Prometheus 指标抓取（免鉴权）",
    description=(
        "标准 Prometheus 文本格式。**只读内存**：渲染时不查库、不调上游。"
        "不映射到宿主机公网端口（compose 仅内网暴露）。"
    ),
    include_in_schema=False,
)
async def metrics_endpoint() -> Response:
    """Prometheus 指标抓取端点。

    Returns:
        Response: `text/plain; version=0.0.4` 的 exposition 文本。
    """
    from prometheus_client import CONTENT_TYPE_LATEST

    from app.core.metrics import render

    return Response(content=render(), media_type=CONTENT_TYPE_LATEST)


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

    checks.append(_check_profile_availability(request))
    checks.append(await _check_database(request))
    checks.append(await _check_database_privilege(request))

    all_ready = all(check.ready for check in checks)
    data = ReadyData(status="ready" if all_ready else "not_ready", checks=checks)
    if all_ready:
        return ApiResponse[ReadyData].ok(data)
    raise ApiError(ErrorCode.CONFIG_ERROR, message=data.status, data=data)


def _check_profile_availability(request: Request) -> ReadyCheck:
    """能力档位可用性检查项（阶段四 T4-04.1，B-3）。

    口径与 M3 目录完全一致（同一个 `ProfileRouter`）：该档能否解析出密钥。
    只输出**档位展示名**，不出现模型名与应用密钥（技术黑盒化红线）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ReadyCheck: `name="profiles"` 的检查项（`checks` 是数组，新增项只能追加）。
    """
    from app.services.profile_routing import ProfileRouter

    settings = get_request_settings(request)
    router = ProfileRouter(settings)
    available = [
        route.display_name for route in router.routes.routes if router.is_available(route.profile)
    ]
    total = len(router.routes.routes)
    if available:
        return ReadyCheck(
            name="profiles",
            ready=True,
            detail=f"可用档位 {len(available)}/{total}：{'、'.join(available)}",
        )
    return ReadyCheck(name="profiles", ready=False, detail=f"可用档位 0/{total}：暂不可用")


def _effective_engine(request: Request) -> Any:
    """取当前生效的业务库引擎（注入优先）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        Any: 引擎实例；内存模式为 None。
    """
    engine = getattr(request.app.state, "db_engine", None)
    if engine is not None:
        return engine
    return get_engine(get_request_settings(request))


async def _check_database(request: Request) -> ReadyCheck:
    """数据库连通性检查项（阶段二 D-02）。

    注意 `checks` 是**数组**（`ReadyData.checks: list[ReadyCheck]`），
    新增检查项只能追加元素，不得改成对象——否则破坏既有 `/readyz` 契约。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ReadyCheck: `name="db"` 的检查项。
    """
    settings = get_request_settings(request)
    if not settings.uses_postgres:
        return ReadyCheck(name="db", ready=True, detail="内存模式（未启用业务库）")
    healthy = await check_engine(_effective_engine(request))
    return ReadyCheck(
        name="db",
        ready=healthy,
        detail="数据库连接正常" if healthy else "数据库连接异常",
    )


async def _check_database_privilege(request: Request) -> ReadyCheck:
    """数据库权限自检（G-25）：连接账号不得带 `BYPASSRLS`。

    超级用户会绕过全部行级安全策略，使「tenant_id 条件 + RLS」的双保险退化为单层；
    代码无法约束运维填什么 DSN，只能靠就绪自检兜住，故升为硬门禁。
    `detail` 一律使用业务语言，**不得**出现 `rolbypassrls` / `BYPASSRLS` 等术语。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ReadyCheck: `name="db_privilege"` 的检查项。
    """
    settings = get_request_settings(request)
    if not settings.uses_postgres:
        return ReadyCheck(name="db_privilege", ready=True, detail="内存模式（未启用业务库）")
    allowed = await check_engine_privilege(_effective_engine(request))
    return ReadyCheck(
        name="db_privilege",
        ready=allowed,
        detail="数据库连接权限正常" if allowed else "数据库连接权限配置不正确",
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
    # 阶段四 MC1-MC4：会话列表 / 历史消息 / 软删 / 更新（与 /model 同域，同一鉴权）
    api_router.include_router(
        conversation.router, prefix=prefix, dependencies=business_dependencies
    )
    # 阶段四 MM1-MM4：我的记忆（列表 / 删单条 / 按类删·全删 / 开关），与 /model 同域
    api_router.include_router(memory.router, prefix=prefix, dependencies=business_dependencies)
    api_router.include_router(rag.router, prefix=prefix, dependencies=business_dependencies)
    # 阶段二新域模块：与既有资源域共用前缀（架构文档 2.2 第 2 条），不新增版本
    api_router.include_router(
        knowledge_ext.router, prefix=prefix, dependencies=business_dependencies
    )
    api_router.include_router(agent.router, prefix=prefix, dependencies=business_dependencies)
    api_router.include_router(tool.router, prefix=prefix, dependencies=business_dependencies)
    # 阶段二工具域扩展（TL1 调用记录）：与 T1-T7 共用前缀，端点零改动
    api_router.include_router(tools_ext.router, prefix=prefix, dependencies=business_dependencies)
    api_router.include_router(identity.router, prefix=prefix, dependencies=business_dependencies)
    # 阶段二组织与权限域扩展（ID1-ID11）：与 U1-U6 共用 `/identity` 前缀
    api_router.include_router(
        identity_ext.router, prefix=prefix, dependencies=business_dependencies
    )
    # 阶段二计费域（B07 起挂载 BL5 用量明细，B09 追加 BL1-BL4/BL6-BL9）
    api_router.include_router(billing.router, prefix=prefix, dependencies=business_dependencies)
    # BL10 支付回调：**唯一免鉴权业务端点**，不挂限流依赖（限流依赖需要鉴权上下文），
    # 安全性由验签 + 回调流水号唯一约束保证（见 middleware/error_handler.AUTH_EXEMPT_PATHS）
    api_router.include_router(billing.public_router, prefix=prefix)
    # 阶段二运营后台（B10，OPS1-OPS8）：平台模式守卫在端点内做，不依赖路由级依赖
    api_router.include_router(ops.router, prefix=prefix, dependencies=business_dependencies)
    # 阶段四 TD1/TD2 数据权利（T4-05.3）：**与 OPS1-OPS8 守卫方向相反**——
    # 这里要求"企业管理员"、拒绝平台令牌，理由见 `api/v1/tenant_data.py` 文档串
    api_router.include_router(tenant_data.router, prefix=prefix, dependencies=business_dependencies)
    # 阶段三插件域（B17 起 PL1-PL5/PL13；B18 追加 PL6-PL12；B19 追加 WF1）
    api_router.include_router(plugins.router, prefix=prefix, dependencies=business_dependencies)
    # W1 切片 A：工作流任务统计（W1-A1，只读聚合；零迁移零新表，
    # 鉴权只用登录上下文 + 租户隔离，不扩权限矩阵）
    api_router.include_router(stats.router, prefix=prefix, dependencies=business_dependencies)
    # W1 切片 B：目标式能力推荐（W1-B1，只读；目录实时组装，零 DB 查询，
    # 规则打分不引模型；`POST /api/v1/agent/tasks` 契约零改）
    api_router.include_router(capability.router, prefix=prefix, dependencies=business_dependencies)
    # 健康检查不挂 API 版本前缀，路径固定为 /healthz 与 /readyz
    api_router.include_router(health_router)
    # 产物内容代理：**不挂 /api/v1**（阶段三端点表冻结 16 条，见 artifact_files 文档串）
    api_router.include_router(artifact_files.router)
    return api_router


__all__ = [
    "ADAPTER_CORE_ENDPOINTS",
    "PHASE2_DOMAIN_ENDPOINTS",
    "PHASE3_DOMAIN_ENDPOINTS",
    "TOTAL_BUSINESS_ENDPOINTS",
    "health_router",
    "healthz",
    "include_all_routers",
    "metrics_endpoint",
    "readyz",
]
