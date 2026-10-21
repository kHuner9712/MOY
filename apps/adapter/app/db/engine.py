"""业务库异步引擎工厂（阶段二 D-02，架构文档 2.1 结论第 3 条）。

设计要点：

1. **`business_store=memory` 时完全不创建引擎**（返回 `None`）。这一点是硬要求：
   既有 421 个用例默认 `business_store=memory`，任何「创建引擎但不连接」的写法
   都会在无 PostgreSQL 的 CI 环境留下未关闭的连接池与告警。
2. 池参数一律来自配置（`OA_DB_POOL_SIZE` / `OA_DB_MAX_OVERFLOW` /
   `OA_DB_POOL_TIMEOUT_SECONDS`），并固定 `pool_pre_ping=True`、
   `pool_recycle=1800`（规避中间件/云负载均衡的空闲连接回收）。
3. 生产/预发环境把 `business_store` 置为 `memory` 属**配置错误**，启动即失败（30004），
   但 development/test 允许（离线单测与 T01 演示需要）。
4. 连接串只允许使用**非超级用户**（`officeai`）：PostgreSQL 超级用户带 `BYPASSRLS`，
   用超级用户会让 RLS 双保险彻底失效（架构文档 3.5「连接串」行）。
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 异步驱动前缀（asyncpg）
ASYNC_DRIVER_PREFIX: Final[str] = "postgresql+asyncpg"

#: 同步驱动前缀（若误配会被规范化为异步前缀）
SYNC_DRIVER_PREFIXES: Final[tuple[str, ...]] = ("postgresql://", "postgres://")

#: 连接回收秒数（架构文档 2.1 结论第 3 条）
POOL_RECYCLE_SECONDS: Final[int] = 1800

#: 连接串中禁止出现的超级用户名（超级用户 BYPASSRLS，双保险失效）
FORBIDDEN_DB_USERS: Final[tuple[str, ...]] = ("postgres", "supersuer", "root")

#: 探活语句
HEALTHCHECK_SQL: Final[str] = "SELECT 1"

#: RLS 前置条件自检：当前连接用户是否带 `BYPASSRLS`（超级用户恒为 true）
#: 超级用户会**绕过全部行级安全策略**，使「tenant_id 条件 + RLS」的双保险退化为单层。
#: 代码无法约束运维填什么 DSN，只能在启动/就绪自检里兜住（G-25）。
BYPASSRLS_CHECK_SQL: Final[str] = "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"

#: 进程内引擎单例与其构造签名（签名变化即重建，避免测试间串配置）
_engine: AsyncEngine | None = None
_engine_signature: tuple[str, int, int, float] | None = None


def normalize_database_url(url: str) -> str:
    """把连接串规范化为 SQLAlchemy 异步驱动形式。

    `OA_DATABASE_URL` 在编排文件里常写成 `postgresql://user:pw@host:5432/db`，
    本函数统一改写为 `postgresql+asyncpg://...`，并做一次**超级用户禁止**校验。

    Args:
        url: 原始连接串。

    Returns:
        str: 异步驱动连接串。

    Raises:
        ApiError: 连接串为空（30004）或使用超级用户（30004）。
    """
    normalized = (url or "").strip()
    if not normalized:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                "OA_DATABASE_URL is empty while OA_BUSINESS_STORE=postgres; "
                "set it to postgresql+asyncpg://officeai:<pw>@postgres:5432/officeai"
            ),
        )
    for prefix in SYNC_DRIVER_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized.replace(prefix, f"{ASYNC_DRIVER_PREFIX}://", 1)
            break
    if normalized.startswith("postgresql://"):
        normalized = normalized.replace("postgresql://", f"{ASYNC_DRIVER_PREFIX}://", 1)
    if not normalized.startswith(f"{ASYNC_DRIVER_PREFIX}://"):
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail="OA_DATABASE_URL must be a postgresql async url",
        )
    lowered = normalized.lower()
    for user in FORBIDDEN_DB_USERS:
        if f"://{user}:" in lowered:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    "OA_DATABASE_URL must not use a superuser account "
                    "(superuser bypasses row level security)"
                ),
            )
    return normalized


def validate_business_store(settings: Settings) -> None:
    """校验业务存储后端与运行环境是否匹配。

    口径（架构文档 2.1 回滚方式行）：`memory` 只允许开发/测试态。
    **生产（`env=production`）置 `memory` 直接启动失败（30004）**；
    `staging` 仅告警不阻断——预发环境允许在没有业务库的灰度链路里跑契约验证，
    但日志必须留下醒目告警，避免「以为写库了其实没写」在预发被长期忽略。

    Args:
        settings: 配置实例。

    Raises:
        ApiError: production 使用 `memory` 后端（30004）。
    """
    if settings.uses_postgres:
        return
    if settings.env == "production":
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                "OA_BUSINESS_STORE=memory is not allowed when env=production; "
                "use OA_BUSINESS_STORE=postgres"
            ),
        )
    if settings.env == "staging":
        logger.warning(
            "business_store_memory_in_staging",
            note="staging 使用内存业务库，业务数据不会持久化",
        )


def create_engine(settings: Settings | None = None) -> AsyncEngine | None:
    """按配置创建异步引擎。

    Args:
        settings: 可选配置实例，缺省读取全局单例。

    Returns:
        AsyncEngine | None: `business_store=postgres` 时返回引擎；
        `memory` 时返回 `None`（**不创建任何连接池**）。

    Raises:
        ApiError: 配置非法（30004）。
    """
    conf = settings or get_settings()
    validate_business_store(conf)
    if not conf.uses_postgres:
        logger.debug("business_store_memory_engine_skipped")
        return None

    url = normalize_database_url(conf.database_url)
    engine = create_async_engine(
        url,
        pool_size=conf.db_pool_size,
        max_overflow=conf.db_max_overflow,
        pool_timeout=conf.db_pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        future=True,
    )
    logger.info(
        "business_engine_created",
        pool_size=conf.db_pool_size,
        max_overflow=conf.db_max_overflow,
        pool_timeout_seconds=conf.db_pool_timeout_seconds,
    )
    return engine


def get_engine(settings: Settings | None = None) -> AsyncEngine | None:
    """获取进程内异步引擎单例。

    单例键为「连接串 + 池参数」四元组：配置变化（测试注入不同 Settings）时自动重建，
    避免跨用例复用指向旧库的引擎。

    Args:
        settings: 可选配置实例，缺省读取全局单例。

    Returns:
        AsyncEngine | None: 引擎实例；`memory` 模式返回 `None`。
    """
    global _engine, _engine_signature

    conf = settings or get_settings()
    validate_business_store(conf)
    if not conf.uses_postgres:
        return None

    signature = (
        conf.database_url.strip(),
        conf.db_pool_size,
        conf.db_max_overflow,
        conf.db_pool_timeout_seconds,
    )
    if _engine is not None and _engine_signature == signature:
        return _engine
    if _engine is not None:  # pragma: no cover - 配置热切换（测试注入）路径
        logger.info("business_engine_signature_changed")
    _engine = create_engine(conf)
    _engine_signature = signature
    return _engine


async def check_engine(engine: AsyncEngine | None) -> bool:
    """探活异步引擎（供 `/readyz` 使用，不抛异常）。

    Args:
        engine: 引擎实例（可为 `None`，表示内存模式）。

    Returns:
        bool: 执行 `SELECT 1` 成功返回 True；`None` 或异常返回 False。
    """
    if engine is None:
        return True
    try:
        async with engine.connect() as connection:
            await connection.execute(text(HEALTHCHECK_SQL))
    except Exception:
        logger.warning("business_engine_healthcheck_failed", exc_info=True)
        return False
    return True


async def check_engine_privilege(engine: AsyncEngine | None) -> bool:
    """自检当前数据库连接是否**不带** `BYPASSRLS`（RLS 双保险的前置条件）。

    RLS 隔离依赖运维使用非超级用户 DSN（如 `officeai`）；代码侧无法约束 DSN，
    因此这里主动查一次 `pg_roles.rolbypassrls`：

    - `false` → 正常（受策略约束）；
    - `true` → 超级用户会**绕过全部行级策略**，必须整体 `not_ready`，禁止带病上线；
    - 查询异常或查不到自身角色行 → 一律判为不通过（fail-closed，不做「查不到就当没事」）。

    Args:
        engine: 引擎实例（`None` 表示内存模式，直接通过）。

    Returns:
        bool: 通过返回 True。
    """
    if engine is None:
        return True
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(BYPASSRLS_CHECK_SQL))
            bypass_rls = result.scalar()
    except Exception:
        logger.error("db_privilege_check_failed", exc_info=True)
        return False
    if bypass_rls is None:
        logger.error("db_privilege_role_not_found")
        return False
    if bool(bypass_rls):
        logger.error(
            "db_privilege_superuser_detected",
            hint="OA_DATABASE_URL 使用了带 BYPASSRLS 的账号，行级安全策略会被绕过",
        )
        return False
    return True


async def dispose_engine() -> None:
    """释放进程内引擎与全部连接池（应用停止时调用）。"""
    global _engine, _engine_signature

    if _engine is None:
        return
    await _engine.dispose()
    logger.info("business_engine_disposed")
    _engine = None
    _engine_signature = None


async def reset_engine_for_test() -> None:
    """测试隔离用：释放并清空引擎单例。"""
    await dispose_engine()


__all__ = [
    "ASYNC_DRIVER_PREFIX",
    "BYPASSRLS_CHECK_SQL",
    "HEALTHCHECK_SQL",
    "POOL_RECYCLE_SECONDS",
    "check_engine",
    "check_engine_privilege",
    "create_engine",
    "dispose_engine",
    "get_engine",
    "normalize_database_url",
    "reset_engine_for_test",
    "validate_business_store",
]
