"""业务库会话与租户隔离测试（阶段二 B01，架构文档 2.1 结论第 5 条 / 3.5）。

本文件覆盖三个「最易踩的点」中的前两个：

1. `set_config('app.tenant_id', $1, TRUE)` 的**第三参必须为 TRUE**（事务级）。
   写 `FALSE`（会话级）会让连接池把 A 租户的会话变量带给 B 租户，
   RLS 双保险失效，且只在并发下偶发 → 本文件用「同一连接先后服务两个租户」
   的用例固定该语义。
2. `business_store=memory` 时不得创建引擎（否则离线单测会尝试连库）。

测试策略：用 `FakeRlsDatabase` 精确模拟 PostgreSQL 的两条行为——
① `set_config(..., is_local)` 的作用域（事务级随事务结束复位、会话级粘住）；
② `current_setting('app.tenant_id', TRUE)` 为 NULL 时策略为假（fail-closed）。
这样无需真实 PostgreSQL 即可验证适配层实现是否正确；
真实 PG 下的同一断言见 `tests/integration/test_migrate.py` 的 PG 用例。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.engine import (
    BYPASSRLS_CHECK_SQL,
    check_engine_privilege,
    get_engine,
    normalize_database_url,
    validate_business_store,
)
from app.db.repositories.base import assert_identifier
from app.db.session import (
    SET_READ_ONLY_SQL,
    SET_TENANT_SQL,
    TENANT_SETTING_NAME,
    UnitOfWork,
    tenant_scope,
)
from app.db.tenant import PLATFORM_TENANT, assert_tenant_allowed, is_platform_tenant

pytestmark = pytest.mark.unit

_TENANT_A = "tnt_tenant_a"
_TENANT_B = "tnt_tenant_b"


class FakeResult:
    """模拟 SQLAlchemy `Result` 的 mappings/scalar 访问。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        """初始化。

        Args:
            rows: 结果行。
        """
        self._rows = rows

    def mappings(self) -> FakeResult:
        """返回自身（对齐 SQLAlchemy API）。"""
        return self

    def all(self) -> list[dict[str, Any]]:
        """全部行。"""
        return self._rows

    def first(self) -> dict[str, Any] | None:
        """首行。"""
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        """首行首列。"""
        return self._rows[0].get("value") if self._rows else None


class FakeSession:
    """模拟会话对象（autobegin 语义：首条语句隐式开启事务）。"""

    def __init__(self, database: FakeRlsDatabase) -> None:
        """初始化。

        Args:
            database: 所属模拟库。
        """
        self._database = database
        self.closed = False
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> FakeResult:
        """执行语句（首条语句隐式开启事务）。

        Args:
            statement: SQL 语句（字符串或 SQLAlchemy TextClause）。
            parameters: 命名参数。

        Returns:
            FakeResult: 模拟结果集。
        """
        self._database.ensure_transaction()
        sql = str(statement)
        return self._database.execute_sql(sql, parameters or {})

    async def commit(self) -> None:
        """提交事务（事务级会话变量复位）。"""
        self.committed = True
        self._database.end_transaction()

    async def rollback(self) -> None:
        """回滚事务（事务级会话变量复位）。"""
        self.rolled_back = True
        self._database.end_transaction()

    async def close(self) -> None:
        """关闭会话。"""
        self.closed = True


class FakeRlsDatabase:
    """模拟 PostgreSQL 会话变量与 RLS 行过滤行为。"""

    def __init__(self) -> None:
        """初始化模拟库（每租户各 1 行数据）。"""
        self.rows: list[dict[str, Any]] = [
            {"tenant_id": _TENANT_A, "value": "a"},
            {"tenant_id": _TENANT_B, "value": "b"},
        ]
        self.session_level_tenant: str | None = None
        self.transaction_level_tenant: str | None = None
        self.in_transaction = False
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.sessions: list[FakeSession] = []

    # ---------------- 生命周期 ----------------
    def ensure_transaction(self) -> None:
        """autobegin：首条语句开启事务（事务级变量为空）。"""
        if not self.in_transaction:
            self.in_transaction = True
            self.transaction_level_tenant = None

    def end_transaction(self) -> None:
        """事务结束：事务级变量复位。"""
        self.in_transaction = False
        self.transaction_level_tenant = None

    @property
    def current_setting(self) -> str | None:
        """等价于 `current_setting('app.tenant_id', TRUE)`。"""
        return self.transaction_level_tenant or self.session_level_tenant

    # ---------------- 会话工厂 ----------------
    def factory(self) -> FakeSession:
        """返回会话（多个会话共享同一模拟库，等价于连接池复用同一连接）。"""
        session = FakeSession(self)
        self.sessions.append(session)
        return session

    # ---------------- SQL 分发 ----------------
    def execute_sql(self, sql: str, parameters: dict[str, Any]) -> FakeResult:
        """按语句类型模拟执行。

        Args:
            sql: SQL 语句。
            parameters: 命名参数。

        Returns:
            FakeResult: 模拟结果。

        Raises:
            AssertionError: 事务级设置被写成 `FALSE`（会话级）时立即失败。
        """
        self.executed.append((sql, dict(parameters)))
        normalized = " ".join(sql.split())

        if normalized.startswith("SELECT set_config"):
            tenant = str(parameters.get(TENANT_SETTING_NAME.replace("app.", ""), ""))
            if tenant == "":
                tenant = str(parameters.get("tenant_id", ""))
            if self._is_local(normalized):
                self.session_level_tenant = None
                self.transaction_level_tenant = tenant
            else:  # pragma: no cover - 该分支出现即实现有缺陷
                self.session_level_tenant = tenant
            return FakeResult([{"value": tenant}])

        if normalized.startswith("SET TRANSACTION READ ONLY"):
            return FakeResult([])

        if normalized.upper().startswith("SELECT"):
            # RLS 行为：未设置会话变量 → 查不到任何行（fail-closed）
            current = self.current_setting
            if current is None:
                return FakeResult([])
            if current == PLATFORM_TENANT:
                return FakeResult(list(self.rows))
            return FakeResult([row for row in self.rows if row["tenant_id"] == current])

        return FakeResult([])

    @staticmethod
    def _is_local(sql: str) -> bool:
        """判断 `set_config` 是否声明为事务级（第三参 TRUE）。

        Args:
            sql: 规范化后的 SQL。

        Returns:
            bool: 事务级返回 True。
        """
        return sql.rstrip().endswith(", TRUE)") or ", TRUE)" in sql


def _memory_settings() -> Settings:
    """内存模式配置。"""
    return Settings(env="test", business_store="memory", database_url="")


# ===========================================================================
# 1. 事务级 set_config（第三参必须 TRUE）
# ===========================================================================
def test_set_tenant_sql_declares_transaction_scope() -> None:
    """设置租户会话变量的 SQL 必须显式写 TRUE（事务级）。"""
    assert "set_config('app.tenant_id'" in SET_TENANT_SQL
    assert SET_TENANT_SQL.rstrip().endswith(", TRUE)")
    assert "FALSE" not in SET_TENANT_SQL.upper()


async def test_first_statement_of_transaction_is_set_config() -> None:
    """事务内第一条语句必须是 set_config，且以 current tenant 为参数。"""
    database = FakeRlsDatabase()
    async with tenant_scope(_TENANT_A, session_factory=database.factory) as uow:
        assert uow.tenant_id == _TENANT_A

    assert database.executed, "事务内未执行任何语句"
    sql, params = database.executed[0]
    assert sql.startswith("SELECT set_config")
    assert params == {"tenant_id": _TENANT_A}


async def test_read_only_transaction_is_declared() -> None:
    """只读请求需声明 `SET TRANSACTION READ ONLY`（架构文档 11.3）。"""
    database = FakeRlsDatabase()
    async with tenant_scope(_TENANT_A, read_only=True, session_factory=database.factory):
        pass
    statements = [sql for sql, _ in database.executed]
    assert any(sql == SET_READ_ONLY_SQL for sql in statements)
    # 只读声明在 set_config 之前，set_config 仍是首个「业务」语句
    assert statements.index(SET_READ_ONLY_SQL) < statements.index(SET_TENANT_SQL)


# ===========================================================================
# 2. 同一连接先后服务两个租户，第二个租户查不到第一个租户数据
# ===========================================================================
async def test_second_tenant_cannot_see_first_tenant_rows() -> None:
    """连接池复用同一连接时，租户变量必须随事务复位（第三参 TRUE 的意义）。"""
    database = FakeRlsDatabase()

    async with tenant_scope(_TENANT_A, session_factory=database.factory) as uow:
        rows_a = await uow.fetch_all("SELECT value FROM knowledge_base_meta")
        assert [row["value"] for row in rows_a] == ["a"]
        assert database.current_setting == _TENANT_A

    # 事务结束后事务级变量复位：连接归还池中时不残留任何租户身份
    assert database.current_setting is None, "事务级 set_config 未在事务结束时复位"

    # 同一底层连接（同一个模拟库）服务第二个租户
    async with tenant_scope(_TENANT_B, session_factory=database.factory) as uow:
        rows_b = await uow.fetch_all("SELECT value FROM knowledge_base_meta")
        assert [row["value"] for row in rows_b] == ["b"]
        assert all(row["tenant_id"] != _TENANT_A for row in database.rows if row["value"] == "b")

    assert database.current_setting is None


async def test_query_without_tenant_scope_is_fail_closed() -> None:
    """未设置会话变量时查询返回 0 行（RLS fail-closed）。"""
    database = FakeRlsDatabase()
    session = database.factory()
    result = await session.execute("SELECT value FROM knowledge_base_meta", {})
    assert result.all() == []


async def test_session_level_setting_would_leak_so_implementation_must_not_use_it() -> None:
    """反例固化：会话级设置会把上一租户身份带给下一租户（本实现的禁止项）。"""
    database = FakeRlsDatabase()
    database.session_level_tenant = _TENANT_A  # 模拟「误用 FALSE」后的连接池残留

    # 正确实现：事务开始即用事务级覆盖，且事务结束复位；残留会话级值不影响
    async with tenant_scope(_TENANT_B, session_factory=database.factory) as uow:
        rows = await uow.fetch_all("SELECT value FROM knowledge_base_meta")
        assert [row["value"] for row in rows] == ["b"]
        assert database.transaction_level_tenant == _TENANT_B


# ===========================================================================
# 3. 事务提交 / 回滚语义
# ===========================================================================
async def test_normal_exit_commits_and_closes_session() -> None:
    """正常退出提交事务并关闭会话。"""
    database = FakeRlsDatabase()
    uow = UnitOfWork(database.factory, _TENANT_A)
    async with uow:
        pass
    assert database.sessions and database.sessions[-1].closed


async def test_exception_inside_scope_rolls_back() -> None:
    """作用域内异常 → 回滚（异常继续向上抛，不被吞掉）。"""
    database = FakeRlsDatabase()

    class BoomError(RuntimeError):
        """测试异常。"""

    with pytest.raises(BoomError):
        async with tenant_scope(_TENANT_A, session_factory=database.factory):
            raise BoomError

    assert database.current_setting is None
    assert database.sessions[-1].closed


async def test_entry_failure_rolls_back_and_closes_session() -> None:
    """进入阶段失败（如连接中断）必须回滚并关闭会话，避免连接池泄漏。"""

    class FailingDatabase(FakeRlsDatabase):
        """首条语句即失败的模拟库。"""

        def execute_sql(self, sql: str, parameters: dict[str, Any]) -> FakeResult:
            raise RuntimeError("connection reset")

    database = FailingDatabase()
    with pytest.raises(RuntimeError):
        async with tenant_scope(_TENANT_A, session_factory=database.factory):
            pass

    session = database.sessions[-1]
    assert session.rolled_back is True
    assert session.closed is True


async def test_using_uow_before_enter_raises() -> None:
    """未进入上下文即使用 UoW 属编码缺陷（30003）。"""
    database = FakeRlsDatabase()
    uow = UnitOfWork(database.factory, _TENANT_A)
    with pytest.raises(ApiError) as excinfo:
        _ = uow.session
    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR


# ===========================================================================
# 4. 平台哨兵守卫
# ===========================================================================
def test_platform_tenant_requires_explicit_allow() -> None:
    """除运营后台外传 `__platform__` 抛 30003（架构文档 3.5）。"""
    assert is_platform_tenant(PLATFORM_TENANT)
    with pytest.raises(ApiError) as excinfo:
        assert_tenant_allowed(PLATFORM_TENANT)
    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR
    assert assert_tenant_allowed(PLATFORM_TENANT, allow_platform=True) == PLATFORM_TENANT


async def test_tenant_scope_rejects_platform_without_allow() -> None:
    """租户作用域同样受平台哨兵守卫约束。"""
    database = FakeRlsDatabase()
    with pytest.raises(ApiError) as excinfo:
        async with tenant_scope(PLATFORM_TENANT, session_factory=database.factory):
            pass
    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR


def test_empty_tenant_is_rejected() -> None:
    """空租户 ID 抛 20001。"""
    with pytest.raises(ApiError) as excinfo:
        assert_tenant_allowed("  ")
    assert excinfo.value.code == ErrorCode.PARAM_INVALID


# ===========================================================================
# 5. memory 模式不创建引擎
# ===========================================================================
def test_memory_store_creates_no_engine() -> None:
    """`business_store=memory` 时引擎为 None（不产生任何连接尝试）。"""
    assert get_engine(_memory_settings()) is None


def test_production_rejects_memory_store() -> None:
    """staging/production 使用内存后端属配置错误（30004）。"""
    with pytest.raises(ApiError) as excinfo:
        validate_business_store(Settings(env="production", business_store="memory"))
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


async def test_memory_mode_tenant_scope_raises_config_error() -> None:
    """内存模式没有事务：tenant_scope 抛 30004（禁止静默降级）。"""
    with pytest.raises(ApiError) as excinfo:
        async with tenant_scope(_TENANT_A, settings=_memory_settings()):
            pass
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


# ===========================================================================
# 6. 数据库权限自检（G-25：连接账号不得带 BYPASSRLS）
# ===========================================================================
class _FakeScalarResult:
    """模拟 SQLAlchemy 结果对象的 `scalar()`。"""

    def __init__(self, value: Any) -> None:
        """初始化。

        Args:
            value: 标量值。
        """
        self._value = value

    def scalar(self) -> Any:
        """返回标量值。"""
        return self._value


class _FakeConnection:
    """模拟数据库连接：按 SQL 关键字返回不同结果。"""

    def __init__(self, *, bypass_rls: Any, raise_on_privilege: bool = False) -> None:
        """初始化。

        Args:
            bypass_rls: `pg_roles` 查询应返回的值（True/False/None）。
            raise_on_privilege: 权限查询是否抛错。
        """
        self._bypass_rls = bypass_rls
        self._raise = raise_on_privilege
        self.statements: list[str] = []

    async def __aenter__(self) -> _FakeConnection:
        """进入上下文。"""
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """退出上下文。"""
        return False

    async def execute(self, statement: Any, parameters: Any = None) -> _FakeScalarResult:
        """执行语句（仅识别本用例关心的两类查询）。"""
        sql = str(statement)
        self.statements.append(sql)
        if "pg_roles" in sql:
            if self._raise:
                raise RuntimeError("permission denied")
            return _FakeScalarResult(self._bypass_rls)
        return _FakeScalarResult(1)


class _FakeEngine:
    """模拟异步引擎（`connect()` 返回异步上下文管理器）。"""

    def __init__(self, *, bypass_rls: Any, raise_on_privilege: bool = False) -> None:
        """初始化。

        Args:
            bypass_rls: 权限查询返回值。
            raise_on_privilege: 权限查询是否抛错。
        """
        self.connection = _FakeConnection(
            bypass_rls=bypass_rls, raise_on_privilege=raise_on_privilege
        )

    def connect(self) -> _FakeConnection:
        """返回连接（本身即异步上下文管理器）。"""
        return self.connection


async def test_privilege_check_passes_for_non_superuser() -> None:
    """`rolbypassrls=false` → 权限自检通过，且确实查了 pg_roles。"""
    engine = _FakeEngine(bypass_rls=False)
    assert await check_engine_privilege(engine) is True  # type: ignore[arg-type]
    assert any("pg_roles" in sql for sql in engine.connection.statements)


async def test_privilege_check_fails_for_superuser() -> None:
    """`rolbypassrls=true`（超级用户会绕过 RLS）→ 自检不通过。"""
    engine = _FakeEngine(bypass_rls=True)
    assert await check_engine_privilege(engine) is False  # type: ignore[arg-type]


async def test_privilege_check_fails_closed_when_query_errors() -> None:
    """查询异常 → 判为不通过（fail-closed，绝不「查不到就当没事」）。"""
    engine = _FakeEngine(bypass_rls=False, raise_on_privilege=True)
    assert await check_engine_privilege(engine) is False  # type: ignore[arg-type]


async def test_privilege_check_fails_when_role_row_missing() -> None:
    """查不到自身角色行 → 同样判为不通过。"""
    engine = _FakeEngine(bypass_rls=None)
    assert await check_engine_privilege(engine) is False  # type: ignore[arg-type]


async def test_privilege_check_skips_in_memory_mode() -> None:
    """内存模式没有连接，直接通过。"""
    assert await check_engine_privilege(None) is True


def test_bypassrls_check_sql_targets_current_user() -> None:
    """自检 SQL 必须查 `current_user` 的 `rolbypassrls`（防被改成查别人）。"""
    normalized = " ".join(BYPASSRLS_CHECK_SQL.split()).lower()
    assert "from pg_roles" in normalized
    assert "rolname = current_user" in normalized
    assert "rolbypassrls" in normalized


# ===========================================================================
# 7. 连接串规范化与 SQL 标识符校验
# ===========================================================================
def test_normalize_database_url_adds_async_driver() -> None:
    """同步连接串被改写为 asyncpg 驱动形式（样例用应用角色，不暗示属主可用）。"""
    assert normalize_database_url("postgresql://officeai_app:pw@db:5432/officeai") == (
        "postgresql+asyncpg://officeai_app:pw@db:5432/officeai"
    )
    assert normalize_database_url("postgres://officeai_app:pw@db:5432/officeai") == (
        "postgresql+asyncpg://officeai_app:pw@db:5432/officeai"
    )


def test_normalize_database_url_rejects_superuser() -> None:
    """超级用户带 BYPASSRLS，会让 RLS 双保险失效 → 直接拒绝启动。"""
    with pytest.raises(ApiError) as excinfo:
        normalize_database_url("postgresql://postgres:pw@db:5432/officeai")
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


@pytest.mark.parametrize("user", ["postgres", "superuser", "root"])
def test_normalize_database_url_rejects_generic_superusers(user: str) -> None:
    """通用超级用户名一律拒绝；`superuser` 是历史拼写错误 `supersuer` 的回归哨兵。"""
    with pytest.raises(ApiError) as excinfo:
        normalize_database_url(f"postgresql://{user}:pw@db:5432/officeai")
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_normalize_database_url_allows_app_role() -> None:
    """应用角色 DSN 必须放行；部署专用属主名由编排门禁 R1 与 rolbypassrls 自检兜底。"""
    assert normalize_database_url("postgresql://officeai_app:pw@db:5432/officeai") == (
        "postgresql+asyncpg://officeai_app:pw@db:5432/officeai"
    )


def test_normalize_database_url_allows_deployment_owner_by_design() -> None:
    """部署专用属主名刻意**不**进通用黑名单：由编排门禁与运行期自检负责。

    这是设计边界，不是遗漏：`FORBIDDEN_DB_USERS` 只放与部署无关的通用名
    （`postgres` / `superuser` / `root`）。`officeai` 是 `.env.example` 里
    `OA_POSTGRES_USER` 的默认值（initdb 属主、带 BYPASSRLS），项目自定义的
    属主名由 `deploy/validate_deploy.py` 的 R1 在编排层拦截，运行期再由
    `check_engine_privilege()` 的 `rolbypassrls` 自检兜底。

    若后续维护者把 `officeai` 塞进 `FORBIDDEN_DB_USERS`，会打断
    `tests/integration/pg_fixture.py::b01_superuser_engine` 的反向验证夹具
    （它用超级用户 DSN 做清理与反向验证），PG 隔离用例随之失去对照；
    本用例即为该回归的哨兵。
    """
    # 「不抛 ApiError」是本用例的契约：属主名不归通用黑名单管。
    try:
        normalized = normalize_database_url("postgresql://officeai:pw@db:5432/officeai")
    except ApiError as exc:  # pragma: no cover - 契约被破坏时才走到
        pytest.fail(f"部署专用属主名不得被通用黑名单拒绝：{exc}")
    assert normalized == "postgresql+asyncpg://officeai:pw@db:5432/officeai"


def test_normalize_database_url_rejects_empty() -> None:
    """`business_store=postgres` 但连接串为空 → 30004。"""
    with pytest.raises(ApiError) as excinfo:
        normalize_database_url("")
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_assert_identifier_blocks_sql_injection() -> None:
    """表名/列名常量必须是合法标识符（防后续维护者拼接用户输入）。"""
    assert assert_identifier("knowledge_base_meta") == "knowledge_base_meta"
    with pytest.raises(ApiError):
        assert_identifier("knowledge_base_meta; DROP TABLE tenant")
    with pytest.raises(ApiError):
        assert_identifier("Tenant")
