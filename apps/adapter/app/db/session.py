"""事务边界与租户会话变量（架构文档 2.1 结论第 4/5 条、3.5、11.3）。

铁律（这三条是本阶段最易踩的坑，全部由 `tests/unit/test_db_session.py` 覆盖）：

1. **一个 HTTP 请求 = 一个事务**，由 `business` 层显式开启：
   `async with tenant_scope(tenant_id) as uow:`；`db` 层**不自动提交**。
2. 事务内**第一条语句**必须是
   `SELECT set_config('app.tenant_id', :tenant_id, TRUE)`。
   第三参 `TRUE` 表示 `is_local=TRUE`（事务级），事务结束自动复位——
   写 `FALSE` 会让连接池把 A 租户的会话变量带给 B 租户，
   使 RLS 双保险失效，且只在并发下偶发。
3. 未设置会话变量时 `current_setting('app.tenant_id', TRUE)` 返回 NULL，
   RLS 策略为假 → **查不到任何行**（fail-closed）。

`business_store=memory` 模式下不存在引擎与事务：所有需要落库的能力都必须走
「内存实现 / PostgreSQL 实现」双后端（见 `db/repositories/` 与
`business/credential_service.py` 的存储抽象），`tenant_scope` 在内存模式下
直接抛 `30004`，避免出现「以为写库了其实没写」的静默失败。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, Final, Protocol, runtime_checkable

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.engine import get_engine
from app.db.tenant import assert_tenant_allowed

logger = get_logger(__name__)

#: RLS 会话变量名（与 `deploy/init-schema-phase2.sql` 第 15 节策略表达式一致）
TENANT_SETTING_NAME: Final[str] = "app.tenant_id"

#: 设置租户会话变量的**唯一**正确 SQL（第三参必须为 TRUE）
SET_TENANT_SQL: Final[str] = "SELECT set_config('app.tenant_id', :tenant_id, TRUE)"

#: 只读事务声明（只读请求不开写事务，架构文档 11.3）
SET_READ_ONLY_SQL: Final[str] = "SET TRANSACTION READ ONLY"


@runtime_checkable
class SessionLike(Protocol):
    """会话对象契约（兼容 SQLAlchemy `AsyncSession` 与测试替身）。

    事务由**语句的 autobegin** 隐式开启（`SET TRANSACTION READ ONLY` /
    `set_config` 本身就是事务的第一条语句），结束时显式 `commit()` / `rollback()`。
    不用 `session.begin()` 是因为 SQLAlchemy 2.1 的 `AsyncTransaction` 必须被
    await 或作为上下文管理，否则 `commit()` 会抛 `AsyncContextNotStarted`
    （真实数据库烟测时暴露，单测替身无法发现）。
    """

    async def execute(self, statement: Any, parameters: Mapping[str, Any] | None = None) -> Any:
        """执行语句（首条语句会隐式开启事务）。"""
        ...

    async def commit(self) -> None:
        """提交事务。"""
        ...

    async def rollback(self) -> None:
        """回滚事务。"""
        ...

    async def close(self) -> None:
        """关闭会话。"""
        ...


class UnitOfWork:
    """工作单元（一次业务写/读的事务边界）。

    用法：

    ```python
    async with tenant_scope(tenant_id) as uow:
        repo = CredentialRepository(uow)
        await repo.upsert(...)      # 与 set_config 同事务
    # 正常退出 → commit；异常退出 → rollback（异常继续向上抛）
    ```

    Attributes:
        tenant_id: 生效租户 ID。
        read_only: 是否只读事务。
    """

    __slots__ = (
        "_allow_platform",
        "_read_only",
        "_session",
        "_session_factory",
        "_tenant_id",
    )

    def __init__(
        self,
        session_factory: Callable[[], SessionLike],
        tenant_id: str,
        *,
        read_only: bool = False,
        allow_platform: bool = False,
    ) -> None:
        """初始化工作单元。

        Args:
            session_factory: 无参会话工厂（`async_sessionmaker` 或测试替身）。
            tenant_id: 租户 ID。
            read_only: 是否只读事务（只读请求使用）。
            allow_platform: 是否允许平台租户哨兵（仅运营后台路由）。

        Raises:
            ApiError: 租户 ID 非法或越权进入平台模式。
        """
        self._tenant_id = assert_tenant_allowed(tenant_id, allow_platform=allow_platform)
        self._session_factory = session_factory
        self._read_only = read_only
        self._allow_platform = allow_platform
        self._session: SessionLike | None = None

    @property
    def tenant_id(self) -> str:
        """生效租户 ID。"""
        return self._tenant_id

    @property
    def read_only(self) -> bool:
        """是否只读事务。"""
        return self._read_only

    @property
    def session(self) -> SessionLike:
        """已开启事务的会话对象。

        Raises:
            ApiError: 工作单元尚未进入（调用方忘记 `async with`）。
        """
        if self._session is None:
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                internal_detail="unit of work used before entering context",
            )
        return self._session

    async def __aenter__(self) -> UnitOfWork:
        """开启事务并设置租户会话变量。

        Returns:
            UnitOfWork: 自身（供 `async with ... as uow` 使用）。

        Raises:
            ApiError: 数据库不可用（30004）。
        """
        session = self._session_factory()
        self._session = session
        try:
            if self._read_only:
                await session.execute(coerce_statement(SET_READ_ONLY_SQL))
            # 事务内第一条业务语句：设置租户会话变量（is_local=TRUE，事务结束自动复位）
            await session.execute(coerce_statement(SET_TENANT_SQL), {"tenant_id": self._tenant_id})
        except BaseException:
            # 进入阶段失败（连接中断/权限不足等）：必须回滚并归还连接，避免连接池泄漏
            await session.rollback()
            await session.close()
            self._session = None
            raise
        logger.debug(
            "tenant_scope_opened",
            tenant_id=self._tenant_id,
            read_only=self._read_only,
            allow_platform=self._allow_platform,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """结束事务：异常回滚、正常提交，最后关闭会话。

        Args:
            exc_type: 异常类型。
            exc: 异常实例。
            tb: 异常回溯。

        Returns:
            bool: 恒为 False（不吞异常）。
        """
        try:
            if self._session is not None:
                if exc_type is None:
                    await self._session.commit()
                    logger.debug("tenant_scope_committed", tenant_id=self._tenant_id)
                else:
                    await self._session.rollback()
                    logger.warning(
                        "tenant_scope_rolled_back",
                        tenant_id=self._tenant_id,
                        reason=getattr(exc_type, "__name__", "unknown"),
                    )
        finally:
            if self._session is not None:
                await self._session.close()
            self._session = None
        return False

    async def execute(self, statement: Any, parameters: Mapping[str, Any] | None = None) -> Any:
        """在事务内执行语句。

        字符串会被自动包装为 `text()`：SQLAlchemy 2.x 不接受裸字符串，
        而仓储层的 SQL 是「表名常量 + 命名参数」拼接出的文本，统一在此收敛。

        Args:
            statement: SQLAlchemy 语句或 SQL 字符串。
            parameters: 命名参数。

        Returns:
            Any: 执行结果。
        """
        return await self.session.execute(coerce_statement(statement), parameters)

    async def fetch_all(
        self, statement: Any, parameters: Mapping[str, Any] | None = None
    ) -> list[Mapping[str, Any]]:
        """查询多行并以映射形式返回。

        Args:
            statement: 查询语句。
            parameters: 命名参数。

        Returns:
            list[Mapping[str, Any]]: 行映射列表。
        """
        result = await self.execute(statement, parameters)
        return [dict(row) for row in result.mappings().all()]

    async def fetch_one(
        self, statement: Any, parameters: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any] | None:
        """查询单行。

        Args:
            statement: 查询语句。
            parameters: 命名参数。

        Returns:
            Mapping[str, Any] | None: 行映射；无结果返回 None。
        """
        result = await self.execute(statement, parameters)
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def fetch_scalar(
        self, statement: Any, parameters: Mapping[str, Any] | None = None
    ) -> Any:
        """查询单个标量值。

        Args:
            statement: 查询语句。
            parameters: 命名参数。

        Returns:
            Any: 标量值；无结果返回 None。
        """
        result = await self.execute(statement, parameters)
        return result.scalar()


def coerce_statement(statement: Any) -> Any:
    """把 SQL 字符串包装为 SQLAlchemy `TextClause`。

    SQLAlchemy 2.x 的 `Session.execute` **不接受裸字符串**（会抛
    `ArgumentError: Textual SQL expression ... should be explicitly declared`）；
    而仓储层的 SQL 是「表名常量 + 命名参数」拼出的文本，统一在此收敛，
    避免每个仓储自己 import `text`（漏一个就只在真实数据库上炸）。

    Args:
        statement: SQL 字符串或已构造的语句对象。

    Returns:
        Any: 可交给 `AsyncSession.execute` 的语句。
    """
    if isinstance(statement, str):
        return sql_text(statement)
    return statement


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession] | None:
    """按配置构造会话工厂。

    Args:
        settings: 可选配置实例。

    Returns:
        async_sessionmaker[AsyncSession] | None: `business_store=postgres` 时返回工厂；
        `memory` 模式返回 `None`（不创建引擎）。
    """
    engine = get_engine(settings)
    if engine is None:
        return None
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def tenant_scope(
    tenant_id: str,
    *,
    allow_platform: bool = False,
    read_only: bool = False,
    settings: Settings | None = None,
    session_factory: Callable[[], SessionLike] | None = None,
) -> AsyncIterator[UnitOfWork]:
    """开启「租户作用域」事务（`business` 层唯一事务入口）。

    Args:
        tenant_id: 租户 ID。
        allow_platform: 是否允许平台租户哨兵（仅运营后台路由）。
        read_only: 是否只读事务。
        settings: 可选配置实例。
        session_factory: 可选的会话工厂（测试注入用）。

    Yields:
        UnitOfWork: 已开启事务的工作单元。

    Raises:
        ApiError: `business_store=memory` 时不存在数据库事务（30004）。
    """
    conf = settings or get_settings()
    factory: Callable[[], SessionLike] | None = session_factory
    if factory is None:
        real_factory = get_session_factory(conf)
        if real_factory is None:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    "tenant_scope requires a database; "
                    "OA_BUSINESS_STORE=memory has no transactional store"
                ),
            )
        factory = real_factory

    uow = UnitOfWork(
        factory,
        tenant_id,
        read_only=read_only,
        allow_platform=allow_platform,
    )
    async with uow:
        yield uow


__all__ = [
    "SET_READ_ONLY_SQL",
    "SET_TENANT_SQL",
    "TENANT_SETTING_NAME",
    "SessionLike",
    "UnitOfWork",
    "coerce_statement",
    "get_session_factory",
    "tenant_scope",
]
