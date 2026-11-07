"""FastAPI 应用入口（架构文档 6.1 `app/main.py`）。

职责（架构文档 1.2）：中间件、异常处理器、路由挂载。
**禁止**在此处或本包任何位置出现业务编排逻辑。

启动顺序：
1. 初始化结构化日志；
2. 注册异常处理器（统一 `{code, message, data}`）；
3. 注册中间件（trace_id 注入、访问日志、CORS）；
4. 挂载 v1 路由（96 个业务端点；OpenAPI 快照 104 个操作，含 5 个 `/sso` 与 2 个免鉴权探针）。

阶段二新增（D-02）：`lifespan` 负责业务库引擎初始化与启动期迁移；
`/readyz` 纳入数据库检查项。`business_store=memory` 时**完全不创建引擎**，
既不连接数据库，也不执行迁移（保证离线单测与 T01 演示可用）。

阶段四新增（T4-03.2）：`lifespan` 按 `OA_BACKGROUND_JOBS_ENABLED` 启停后台任务轮询
（记忆抽取的载体）。关停顺序是**先停轮询、再释放连接池**——反序会让正在执行的任务
拿到已 dispose 的引擎，把一次干净的停机变成一堆 `InterfaceError` 日志。

质量收口新增（W2）：`build_openapi` 给对外文档补齐公共层——安全方案 `BearerAuth`、
逐操作的 `security`、公共头 `X-Tenant-Id` 与 `servers`。

**为什么公共层要在这里补而不是逐个端点声明**：104 个 OpenAPI 操作里手工声明 `security`
必然漏，而漏掉的后果是"生成的客户端不带令牌"——联调期表现为 401，排查方向会被
引向令牌本身。同样的理由适用于 `X-Tenant-Id`：它由 `resolve_tenant_id` 在后台
从请求头读取，函数签名里看不到，只能靠 OpenAPI 显式标注。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.sso_routes import router as sso_router
from app.api.v1.artifact_files import router as artifact_files_router
from app.api.v1.router import include_all_routers
from app.business.task.plan_cleanup import plan_cleanup_loop
from app.core.cache_backend import aclose_shared_redis, validate_cache_backend_startup
from app.core.config import Settings, get_settings
from app.core.crypto import validate_startup as validate_credential_startup
from app.core.errors import ApiError
from app.core.idempotency import configure_idempotency_manager
from app.core.logging import configure_logging, get_logger
from app.core.metrics import configure_deploy_mode_metrics
from app.core.ratelimit import RateLimiter
from app.core.security import validate_auth_startup
from app.db.engine import dispose_engine, get_engine
from app.db.migrate import run_startup_migrations
from app.db.repositories.job_repo import get_job_store
from app.middleware.error_handler import AUTH_EXEMPT_PATHS, register_exception_handlers
from app.middleware.metrics import MetricsMiddleware
from app.middleware.trace import AccessLogMiddleware, TraceMiddleware
from app.services.memory_extractor import warn_missing_memory_app_key_once
from app.services.model_service import get_model_service
from app.workers.job_runner import JobRunner, get_job_runner, should_start_runner
from app.workers.queue_depth import refresh_loop
from app.workers.tasks.retention_cleanup import schedule_all_tenants

logger = get_logger(__name__)

#: OpenAPI 契约描述（对外文档中不得出现任何底座术语，架构文档 1.4 第 2 条）
API_DESCRIPTION = """
Moy（麦子）· 自研统一适配层标准接口 v1.0。

## 统一约定

- 所有接口返回 `{code, message, data}`；`code=0` 表示成功。
- `message` 为面向用户的业务语言提示，前端直接展示；
  技术细节（错误码、堆栈、下游原始错误）不会出现在响应中。
- 除 `/healthz`、`/readyz` 外，均需携带 `Authorization: Bearer <access_token>`。
- 所有写接口需携带 `Idempotency-Key` 请求头（UUID v4，24 小时内有效）。

## 能力档位

模型服务仅暴露能力档位（通用 / 编程 / 长文档 / 深度推理），
真实模型名称与路由策略由平台内部维护，不对终端用户暴露。
""".strip()

#: OpenAPI 标签
API_TAGS_METADATA: list[dict[str, Any]] = [
    {"name": "模型服务", "description": "对话生成、流式输出、能力档位目录与用量统计"},
    {"name": "RAG 服务", "description": "知识库、文档索引与带引用的检索"},
    {"name": "Agent 服务", "description": "任务创建、状态查询、步骤详情与进度推送"},
    {"name": "工具服务", "description": "工具类型注册表、授权连接、操作执行与数据同步"},
    {"name": "用户权限服务", "description": "令牌校验、用户资料、组织成员、角色与审计"},
    {"name": "健康检查", "description": "存活探针与就绪探针（免鉴权）"},
]

#: OpenAPI 安全方案名（`components.securitySchemes` 的键）
BEARER_SCHEME_NAME: Final[str] = "BearerAuth"

#: 公共请求头：生效租户
TENANT_HEADER_NAME: Final[str] = "X-Tenant-Id"

#: 公共请求头的描述。**必须说清"可省略"**：私有化单租户部署没有这个头，
#: 写成 `required: true` 会让生成的客户端在私有化现场强制塞一个无意义的值。
TENANT_HEADER_DESCRIPTION: Final[str] = (
    "私有化模式可省略；SaaS 多租户场景用于指定生效租户，需与令牌声明一致"
)

#: OpenAPI `servers`。同源部署：对外只有一个入口，由编排层反向代理转发
OPENAPI_SERVERS: Final[list[dict[str, str]]] = [
    {"url": "/", "description": "同源部署（由编排层反向代理）"},
]

#: OpenAPI 路径对象里代表"一个真实操作"的键（其余键如 `parameters` / `summary` 不是操作）
OPERATION_METHODS: Final[frozenset[str]] = frozenset(
    {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
)


def artifact_token_paths() -> frozenset[str]:
    """列出「靠链接令牌自身鉴权」的端点路径（不是免鉴权，是换了一种鉴权）。

    **为什么从路由声明里推导**：这些路径一旦改地址，手写的字符串就会悄悄失配，
    表现为 OpenAPI 给下载端点标上 Bearer 鉴权——而它实际不认令牌。
    直接读路由对象，路径只有一个真源。

    Returns:
        frozenset[str]: 形如 `/files/artifacts/{token}` 的路径模板集合。
    """
    return frozenset(
        str(getattr(route, "path", "") or "")
        for route in artifact_files_router.routes
        if getattr(route, "path", "")
    )


def auth_exempt_paths() -> frozenset[str]:
    """OpenAPI 中**不加** `security` 的路径集合。

    合流点只有这一处：中间件层的免鉴权清单（`AUTH_EXEMPT_PATHS`，探针与支付回调）
    加上靠链接令牌鉴权的产物下载端点。**不再维护第二份手写清单**——两份清单一旦
    分叉，文档会要求一个实际不需要的令牌，或者反过来把免鉴权端点写成需要鉴权。

    Returns:
        frozenset[str]: 免鉴权路径（与 OpenAPI 的 path 模板同形）。
    """
    return AUTH_EXEMPT_PATHS | artifact_token_paths()


def _has_tenant_header(parameters: list[Any]) -> bool:
    """判断参数列表里是否已声明 `X-Tenant-Id` 头。

    Args:
        parameters: 操作级或路径级的参数列表。

    Returns:
        bool: 已声明返回 True。
    """
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        if parameter.get("in") == "header" and parameter.get("name") == TENANT_HEADER_NAME:
            return True
    return False


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """生成对外 OpenAPI 文档（在 FastAPI 默认结果上补齐公共层）。

    补齐三件事，缺任何一件都无法据此生成可用客户端：

    1. `components.securitySchemes.BearerAuth` + 逐操作 `security`：默认只有 4 个免鉴权
       端点，其余全部要求 Bearer；
    2. 公共头 `X-Tenant-Id`：它由 `resolve_tenant_id` 在后台读请求头，函数签名里看不到；
    3. `servers: /`：同源部署，客户端不需要（也不应该）拼域名。

    结果写回 `app.openapi_schema` 缓存，重复调用（`/openapi.json`、快照门禁）不会再算一遍。

    Args:
        app: FastAPI 应用实例。

    Returns:
        dict[str, Any]: 补齐后的 OpenAPI 文档。
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema: dict[str, Any] = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    schema["servers"] = [dict(item) for item in OPENAPI_SERVERS]

    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes[BEARER_SCHEME_NAME] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "登录后获得的访问令牌，放在 `Authorization: Bearer <token>`",
    }

    exempt = auth_exempt_paths()
    tenant_parameter: dict[str, Any] = {
        "name": TENANT_HEADER_NAME,
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": TENANT_HEADER_DESCRIPTION,
    }

    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        return schema

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        # 路径级参数对全部方法生效；先看一眼，避免与方法级重复声明同一个头
        shared_parameters = path_item.get("parameters")
        has_shared_tenant_header = isinstance(shared_parameters, list) and _has_tenant_header(
            shared_parameters
        )
        for method, operation in path_item.items():
            if method.lower() not in OPERATION_METHODS or not isinstance(operation, dict):
                continue
            if path not in exempt:
                # 已声明 security 的操作（理论上不存在）保持原样，不覆盖
                operation.setdefault("security", [{BEARER_SCHEME_NAME: []}])

            if has_shared_tenant_header:
                continue
            parameters = operation.setdefault("parameters", [])
            if not isinstance(parameters, list):
                continue
            if not _has_tenant_header(parameters):
                parameters.append(dict(tenant_parameter))

    app.openapi_schema = schema
    return schema


async def _release_on_shutdown(step: str, closer: Callable[[], Awaitable[None]]) -> None:
    """执行一步关停释放；**异常只记日志**，绝不中断后续步骤（L-14）。

    修复前 `finally` 里是三条裸 `await` 顺序执行：`aclose()` 抛一次，
    后面 `dispose_engine()` / `aclose_shared_redis()` 全部被跳过——进程带着
    未释放的引擎与 Redis 连接退出，对端表现为连接缓慢堆积，现场只能靠重启兜。
    关停路径的每一步都应当"尽最大努力做完"，而不是"遇到第一个错就放弃"。

    为什么参数是"无参可等待对象工厂"而不是现成的 coroutine：若直接传
    coroutine 对象，异常发生前它已被创建；一旦前一步抛错，后续 coroutine
    就再也没人 await（`RuntimeWarning: coroutine was never awaited`）。
    传工厂则在真正轮到这一步时才调用，跳过就是纯粹地不执行。

    Args:
        step: 步骤名（写进日志，便于定位是哪一步失败）。
        closer: 无参异步可调用对象，执行该步骤的释放动作。
    """
    try:
        await closer()
    except Exception as exc:  # 关停路径不得因为单步失败而中断
        logger.warning("shutdown_step_failed", step=step, detail=type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期钩子。

    阶段二（D-02）新增职责：

    1. 按 `OA_BUSINESS_STORE` 初始化业务库异步引擎（`memory` 模式返回 `None`，
       不创建任何连接池）；
    2. `OA_DB_AUTO_MIGRATE=true` 且 `business_store=postgres` 时执行启动期迁移
       （checksum 漂移直接抛出 → 启动失败，防手工改库导致的静默不一致）；
    3. 退出时释放连接池。

    配置一律取 `app.state.settings`（注入优先），与 QA BUG-01/02 的修复口径一致。

    Args:
        app: FastAPI 应用实例。

    Yields:
        None: 应用运行期间。

    Raises:
        ApiError: 业务库/迁移配置非法（30004），启动即失败。
    """
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    logger.info(
        "adapter_starting",
        app=settings.app_name,
        version=settings.app_version,
        env=settings.env,
        deploy_mode=settings.deploy_mode,
        downstream_enabled=settings.downstream_enabled,
        business_store=settings.business_store,
        db_auto_migrate=settings.db_auto_migrate,
    )

    engine = get_engine(settings)
    app.state.db_engine = engine
    if engine is None:
        logger.info("business_store_memory_mode", note="no database engine created")
    else:
        try:
            await run_startup_migrations(settings)
        except ApiError:
            await dispose_engine()
            raise

    # S1（OA-REVIEW-002）：生产/预发环境禁止桩鉴权。`auth_mode` 默认值就是 stub，
    # 不拦等于「不配就关掉鉴权」；此处让配置漏填在启动时暴露，而不是等被伪造令牌打穿。
    # 同时要求 jwt_secret 非空：OIDC 校验与产物下载签名都依赖它。
    try:
        validate_auth_startup(settings)
    except ApiError:
        await dispose_engine()
        raise

    # D-10：真实部署形态下凭据加密密钥缺失即启动失败（30004，含可操作文案）。
    # development/test 允许缺失，首次真正需要解密时才报错（绝不静默降级）。
    try:
        validate_credential_startup(settings)
    except ApiError:
        await dispose_engine()
        raise

    # R2-ENG-01（审计 2026-10-29）：把「幂等/限流后端」从**配置幻觉**变成真实装配。
    #
    # 修复前两处失效叠加：
    # 1. `IdempotencyManager()` / `RateLimiter()` 都是无参构造，恒用进程内实现，
    #    `RedisIdempotencyStore` / `RedisRateLimitStore` 全仓**只在 tests 里**实例化；
    # 2. 告警条件恰好是「配成 memory」→ **配成 redis 反而让告警永久沉默**，
    #    运维看到的是"配置是 redis、日志也干净"的假象。
    #
    # 现在：先 fail-fast 校验（声明 redis 就必须有 url），再主动装配，最后
    # **断言装配结果与配置一致**——比类名，不比配置字符串。
    try:
        validate_cache_backend_startup(settings)
    except ApiError:
        await dispose_engine()
        raise

    # 主动装配一次：幂等管理器是懒加载单例，不主动取的话，装配偏差要等首个写请求
    # 才暴露（那时已经在服务真实流量了）。
    #
    # 用 `configure_idempotency_manager(settings)` 而不是 `get_idempotency_manager()`：
    # 后者是 FastAPI 依赖（`Depends(get_idempotency_manager)` 有 20+ 处）**签名必须无参**，
    # 且它读的是进程级 `get_settings()`，在测试注入场景下未必等于 `app.state.settings`。
    try:
        idempotency_store = configure_idempotency_manager(settings).store_name
    except ApiError:
        await dispose_engine()
        raise
    ratelimit_store = app.state.rate_limiter.store_name
    logger.info(
        "cache_backend_assembled",
        idempotency_declared=settings.idempotency_backend,
        idempotency_actual=idempotency_store,
        ratelimit_declared=settings.ratelimit_backend,
        ratelimit_actual=ratelimit_store,
    )

    def _matches(declared: str, actual: str) -> bool:
        """配置声明与装配结果是否一致（redis 家族 vs 进程内）。"""
        return ("Redis" in actual) if declared == "redis" else ("Redis" not in actual)

    if not _matches(settings.idempotency_backend, idempotency_store) or not _matches(
        settings.ratelimit_backend, ratelimit_store
    ):
        # 装配与声明不符属**代码缺陷**（不是配置问题），必须响亮
        logger.error(
            "cache_backend_assertion_failed",
            detail="幂等/限流后端的实际装配结果与配置声明不一致——配置在说谎",
            idempotency_declared=settings.idempotency_backend,
            idempotency_actual=idempotency_store,
            ratelimit_declared=settings.ratelimit_backend,
            ratelimit_actual=ratelimit_store,
        )

    if settings.env == "production" and (
        "Redis" not in idempotency_store or "Redis" not in ratelimit_store
    ):
        # 单副本交付下这是**合法形态**（V1.0 三套编排均无 replicas），故用 warn 而
        # 非 error；但扩容前必须切——否则幂等静默失效、限流按副本数放大 N 倍。
        logger.warning(
            "cache_backend_memory_in_production",
            detail=(
                "生产环境使用进程内幂等/限流后端：单副本下行为正确，"
                "但**扩容（compose --scale / k8s 多 Pod）前必须**改配 "
                "OA_IDEMPOTENCY_BACKEND=redis 与 OA_RATELIMIT_BACKEND=redis"
            ),
        )

    # 阶段四 T4-03.2：后台任务轮询（抽取记忆、导出、保留期清理的载体）。
    # **只在 `OA_BACKGROUND_JOBS_ENABLED=true` 且 `env != test` 且确有数据库时启动**：
    # 测试环境起一个永不结束的协程会污染 pytest 事件循环（"Task was destroyed"），
    # 而 `business_store=memory` 下没有队列可消费，起来只是空转。
    # 阶段四 T4-05.1：私有化模式才注册 `tenant_budget_used_ratio`（SaaS 下语义不明）
    configure_deploy_mode_metrics(settings)

    runner: JobRunner | None = None
    queue_depth_task: asyncio.Task[None] | None = None
    plan_cleanup_task: asyncio.Task[None] | None = None
    if should_start_runner(settings, has_engine=engine is not None):
        runner = get_job_runner(settings)
        runner.start()
        # 抽取专用应用没配 → 整条抽取链路不启动（刻意不回落默认对话应用）。
        # 只在启动时告警一次，避免每 10 轮刷一条同名 WARNING。
        warn_missing_memory_app_key_once(settings)
        # 队列深度每 10s 刷新一次：`/metrics` 渲染时不查库（见 app/workers/queue_depth.py）
        queue_depth_task = asyncio.create_task(
            refresh_loop(get_job_store(settings)), name="oa-task-queue-depth"
        )
        # 矩阵第 2 项：计划超时清理（只在开关打开时启动）。
        # 写路径逐租户走 `tenant_scope`，枚举租户是只读 `platform_scope`。
        if settings.task_plan_enabled:
            plan_cleanup_task = asyncio.create_task(
                plan_cleanup_loop(settings), name="oa-task-plan-cleanup"
            )
        # 阶段四 T4-05.3：保留期清理**每日一次**。入队放在启动流程里而不是依赖
        # cron：私有化现场没有 cron 是常态，而"清理任务靠人记得配"等于没有。
        # 去重键固定（`retention-daily`），重启多少次都只会有一次在排。
        # 失败只告警不阻断启动——清理排不上不该让整个服务起不来。
        try:
            await schedule_all_tenants(settings)
        except Exception as exc:
            logger.warning("retention_schedule_failed", detail=type(exc).__name__)
    app.state.job_runner = runner

    try:
        yield
    finally:
        # 先停轮询再释放连接池：反序会让 in-flight 任务拿到已 dispose 的引擎
        if runner is not None:
            await _release_on_shutdown("job_runner", runner.stop)
        if queue_depth_task is not None:
            queue_depth_task.cancel()
            try:
                await queue_depth_task
            except asyncio.CancelledError:
                pass
        if plan_cleanup_task is not None:
            plan_cleanup_task.cancel()
            try:
                await plan_cleanup_task
            except asyncio.CancelledError:
                pass
        # H-1（审计 2026-10-28）：先停底座连接、再释放业务库——
        # 与"先停轮询再释放引擎"的关停顺序纪律一致。
        #
        # L-14（本轮）：三步各自独立 `try/except`（见 `_release_on_shutdown`）。
        # **顺序不许调换**（底座 → 业务库 → Redis）：反序同样会把一次干净停机
        # 变成一串 `InterfaceError` 日志。异常只降级为 WARN，后续步骤照常执行。
        # 用 lambda 而非直接传方法：`get_model_service()` 本身也可能抛异常，
        # 提前求值会把"取单例失败"变成又一次中断后续释放的机会。
        await _release_on_shutdown("model_service", lambda: get_model_service().aclose())
        await _release_on_shutdown("db_engine", dispose_engine)
        # R2-ENG-01（审计 2026-10-29）：共享 Redis 客户端纳入统一关停，
        # 否则进程退出时连接池留在对端（Valkey 侧表现为连接缓慢堆积）。
        await _release_on_shutdown("shared_redis", aclose_shared_redis)
        logger.info("adapter_stopping")


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用。

    Args:
        settings: 可选配置实例。

    Returns:
        FastAPI: 配置完成的应用实例。
    """
    conf = settings or get_settings()
    configure_logging(conf)

    app = FastAPI(
        title="Moy（麦子）· 统一适配层",
        description=API_DESCRIPTION,
        version=conf.app_version,
        openapi_tags=API_TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 配置与限流器挂到 app.state：请求链路一律从这里读取，
    # 避免回落到全局单例导致注入式配置失效（QA BUG-01 / BUG-02 / BUG-03）。
    app.state.settings = conf
    app.state.rate_limiter = RateLimiter(settings=conf)

    # 中间件：trace_id 注入在最外层，保证全链路（含错误响应）均带 trace_id
    app.add_middleware(TraceMiddleware)
    app.add_middleware(AccessLogMiddleware)
    # 指标在最内层：路由已匹配（能拿到模板）且耗时包含鉴权与业务处理
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # QA-06（审计 2026-10-28）：`allow_origins=["*"]` + `allow_credentials=True`
        # 是浏览器规范明令禁止/告警的组合（带凭据时通配符被拒），还误导排障。
        # dev 白名单只开本机前端联调端口；非 development 一律为空（同源部署无跨域需求）。
        allow_origins=(
            [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                # 3100 是 Playwright 的 webServer 端口（H-04 主旅程要在浏览器里
                # 真跑 SSO→兑换→工作台→插件安装）；只开 development，生产同源部署
                # 不需要跨域。
                "http://localhost:3100",
                "http://127.0.0.1:3100",
            ]
            if conf.env == "development"
            else []
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    register_exception_handlers(app)
    app.include_router(include_all_routers(conf))
    # 会话端点挂在 `/sso` 下（**不进 `/api/v1`**）：96 条端点基线一条不动，
    # 理由与产物下载同款（`app/api/v1/artifact_files.py:3-5`）。注册位置在
    # `include_all_routers` 之后，照抄产物下载的注册方式。
    app.include_router(sso_router)
    # 对外文档补齐公共层（安全方案 / 逐操作 security / X-Tenant-Id / servers）。
    # 用 functools.partial 绑定 app 而不是 lambda：partial 可被 mypy 推导出
    # 「零参返回 dict」的签名，与 FastAPI 的 `openapi()` 一致，且不会引入闭包延迟绑定。
    app.openapi = partial(build_openapi, app)  # type: ignore[method-assign]
    return app


app = create_app()


def main() -> None:  # pragma: no cover - 进程入口
    """以 uvicorn 启动适配层服务。

    用法：`python -m app.main` 或 `uvicorn app.main:app`。
    """
    import uvicorn

    conf = get_settings()
    uvicorn.run(
        "app.main:app",
        host=conf.host,
        port=conf.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover - 进程入口
    main()
