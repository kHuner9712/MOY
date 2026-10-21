"""FastAPI 应用入口（架构文档 6.1 `app/main.py`）。

职责（架构文档 1.2）：中间件、异常处理器、路由挂载。
**禁止**在此处或本包任何位置出现业务编排逻辑。

启动顺序：
1. 初始化结构化日志；
2. 注册异常处理器（统一 `{code, message, data}`）；
3. 注册中间件（trace_id 注入、访问日志、CORS）；
4. 挂载 v1 路由（30 个核心业务端点 + 2 个免鉴权探针）。

阶段二新增（D-02）：`lifespan` 负责业务库引擎初始化与启动期迁移；
`/readyz` 纳入数据库检查项。`business_store=memory` 时**完全不创建引擎**，
既不连接数据库，也不执行迁移（保证离线单测与 T01 演示可用）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import include_all_routers
from app.core.config import Settings, get_settings
from app.core.crypto import validate_startup as validate_credential_startup
from app.core.errors import ApiError
from app.core.logging import configure_logging, get_logger
from app.core.ratelimit import RateLimiter
from app.db.engine import dispose_engine, get_engine
from app.db.migrate import run_startup_migrations
from app.middleware.error_handler import register_exception_handlers
from app.middleware.trace import AccessLogMiddleware, TraceMiddleware

logger = get_logger(__name__)

#: OpenAPI 契约描述（对外文档中不得出现任何底座术语，架构文档 1.4 第 2 条）
API_DESCRIPTION = """
企业级跨生态办公 AI 平台 · 自研统一适配层标准接口 v1.0。

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

    # D-10：真实部署形态下凭据加密密钥缺失即启动失败（30004，含可操作文案）。
    # development/test 允许缺失，首次真正需要解密时才报错（绝不静默降级）。
    try:
        validate_credential_startup(settings)
    except ApiError:
        await dispose_engine()
        raise

    yield

    await dispose_engine()
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
        title="企业级跨生态办公 AI 平台 · 统一适配层",
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if conf.env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )

    register_exception_handlers(app)
    app.include_router(include_all_routers(conf))
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
