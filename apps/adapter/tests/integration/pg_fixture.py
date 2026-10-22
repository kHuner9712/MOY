"""阶段二 B01：真实 PostgreSQL 多租户隔离验证夹具。

为什么放在**独立文件**而不是 `tests/conftest.py`：

1. `conftest.py` 正被 B04 批次并发修改，往里加夹具必然产生冲突；
2. 本文件由各用例文件**显式导入**，而 pytest 中「模块级夹具」优先级高于
   conftest 同名夹具，因此即使 conftest 后续被改动，本批次用例仍固定使用本文件夹具；
3. 夹具本身也要过 ruff 门禁，独立文件便于定位问题。

前置条件（缺一即 skip，**绝不允许假绿**）：

- `OA_TEST_DATABASE_URL` 必须指向 **NOSUPERUSER NOBYPASSRLS** 的角色（如
  `officeai_app`）。未设置时全部依赖它的用例 skip 并给出明确原因。
- 建连后立刻自检 `pg_roles.rolbypassrls`，为 true 直接 `pytest.fail`：
  超级用户会绕过 `FORCE ROW LEVEL SECURITY`，此时所有隔离断言都是假阳性，
  比不测更危险（initdb 建出来的 `officeai` 正是这种超级用户）。

超级用户连接串（`OA_TEST_SUPERUSER_DATABASE_URL`）用于两类**反向验证**：

- SEC-07：`/readyz` 的 `db_privilege` 在超级用户 DSN 下必须 `not_ready`；
- G-11：空库的建库与属主移交（应用角色 `NOCREATEDB`，无法自建空库）。

未提供该变量时，若应用连接串形如 `…://officeai_app:PASSWORD@…`，则按
「QA 容器与超级用户共用同一口令」的约定把用户名替换为 `officeai` 派生。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse, urlunparse

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.engine import normalize_database_url

#: 应用角色连接串环境变量（必须指向 NOSUPERUSER NOBYPASSRLS 的角色）
APP_DSN_ENV: str = "OA_TEST_DATABASE_URL"

#: 超级用户连接串环境变量（反向验证用，可缺省并自动派生）
SUPERUSER_DSN_ENV: str = "OA_TEST_SUPERUSER_DATABASE_URL"

#: 应用角色名（deploy/init-app-role.sql 创建）
APP_ROLE: str = "officeai_app"

#: initdb 创建的超级用户角色名（POSTGRES_USER）
SUPERUSER_ROLE: str = "officeai"

#: 业务表策略名（deploy/init-schema-phase2.sql 第 15 节）
BUSINESS_POLICY: str = "p_tenant_isolation"

#: 平台共享目录表策略名（同一节）
CATALOG_POLICY: str = "p_catalog_shared"

#: 哨兵数据前缀（便于断言与清理，绝不落进真实数据）
SENTINEL_PREFIX: str = "QA_B01_SENTINEL"

#: 当前连接角色名与其 RLS 绕过标记
_BYPASSRLS_SQL: str = "SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user"

#: 业务表清单（按策略名动态识别，避免把共享目录表误判为业务表）
_BUSINESS_TABLES_SQL: str = (
    "SELECT c.relname FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "JOIN pg_policy p ON p.polrelid = c.oid "
    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND p.polname = :policy "
    "ORDER BY c.relname"
)


def to_raw_dsn(dsn: str) -> str:
    """把 SQLAlchemy 异步连接串还原为 asyncpg 原生形式。

    Args:
        dsn: `postgresql+asyncpg://...` 或 `postgresql://...`。

    Returns:
        str: `postgresql://...` 形式。
    """
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def to_async_dsn(dsn: str) -> str:
    """把连接串规范化为 SQLAlchemy 异步驱动形式。

    Args:
        dsn: `postgresql://...` 或 `postgresql+asyncpg://...`。

    Returns:
        str: `postgresql+asyncpg://...` 形式。
    """
    if "+asyncpg" in dsn:
        return dsn
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


def _swap_database(dsn: str, database: str) -> str:
    """替换连接串中的库名。

    Args:
        dsn: 原连接串。
        database: 目标库名。

    Returns:
        str: 指向新库的连接串。
    """
    parts = urlparse(dsn)
    return urlunparse(
        (parts.scheme, parts.netloc, f"/{database}", parts.params, parts.query, parts.fragment)
    )


def _derive_superuser_dsn(app_dsn: str) -> str | None:
    """由应用连接串派生超级用户连接串。

    约定：QA 容器以同一口令同时创建 `officeai` 与 `officeai_app`
    （`POSTGRES_PASSWORD` 与 `init-app-role.sql` 的占位密码一致），
    因此仅替换用户名即可得到可连通的超级用户 DSN，
    从而让「只给一个环境变量」也能跑满全套用例（无 skip）。

    Args:
        app_dsn: 应用角色连接串。

    Returns:
        str | None: 超级用户连接串；无法派生时返回 None。
    """
    marker = f"://{APP_ROLE}:"
    if marker in app_dsn:
        return app_dsn.replace(marker, f"://{SUPERUSER_ROLE}:", 1)
    return None


async def assert_rls_not_bypassed(engine: AsyncEngine) -> None:
    """前置断言：当前连接角色**不得**带 `BYPASSRLS`。

    超级用户（含 initdb 默认创建的 `officeai`）会绕过 `FORCE ROW LEVEL SECURITY`，
    用它跑隔离用例时所有断言都会"通过"，但通过的原因是根本没有策略约束。

    Args:
        engine: 异步引擎。

    Raises:
        pytest.fail.Exception: 角色带 BYPASSRLS 或查不到角色行时直接失败。
    """
    async with engine.connect() as connection:
        row = (await connection.execute(sql_text(_BYPASSRLS_SQL))).first()

    if row is None:
        pytest.fail("无法确认数据库角色权限（pg_roles 查不到 current_user），隔离用例无效")
    role, bypass = str(row[0]), row[1]
    if bool(bypass):
        pytest.fail(
            "测试连接使用了超级用户，行级安全未生效，隔离用例无效。"
            f"current_user={role} rolbypassrls={bypass}。"
            f"请改用 NOSUPERUSER NOBYPASSRLS 的应用角色（如 {APP_ROLE}）重跑。"
        )


def _require_app_dsn() -> str:
    """读取应用角色连接串（未提供则 skip）。

    Returns:
        str: 连接串。

    Raises:
        pytest.skip.Exception: 未设置 `OA_TEST_DATABASE_URL`。
    """
    dsn = os.environ.get(APP_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(
            f"未提供 {APP_DSN_ENV}（应指向 NOSUPERUSER NOBYPASSRLS 的应用角色），跳过真实 PG 用例"
        )
    return dsn


def _require_superuser_dsn() -> str:
    """读取超级用户连接串（显式优先，其次派生）。

    Returns:
        str: 超级用户连接串。

    Raises:
        pytest.skip.Exception: 显式变量与派生均不可得。
    """
    explicit = os.environ.get(SUPERUSER_DSN_ENV, "").strip()
    if explicit:
        return explicit
    derived = _derive_superuser_dsn(_require_app_dsn())
    if derived is None:
        pytest.skip(
            f"未提供 {SUPERUSER_DSN_ENV} 且无法从 {APP_DSN_ENV} 派生"
            f"（连接串中未出现 ://{APP_ROLE}:）"
        )
    return derived


@pytest.fixture(scope="session")
def b01_app_dsn() -> str:
    """应用角色连接串（未提供则 skip）。

    Returns:
        str: 连接串。
    """
    return _require_app_dsn()


@pytest.fixture(scope="session")
def b01_superuser_dsn() -> str:
    """超级用户连接串（用于反向验证与建空库）。

    Returns:
        str: 超级用户连接串。
    """
    return _require_superuser_dsn()


@pytest.fixture
async def b01_engine() -> AsyncIterator[AsyncEngine]:
    """单连接池引擎（`pool_size=1`），用于证明"同一连接先后服务两个租户"。

    刻意**不带参数**：夹具自取环境变量。各用例文件通过
    `pytest_plugins = ["tests.integration.pg_fixture"]` 载入本模块，
    因此夹具名一律带 `b01_` 前缀，与 conftest 同名夹具互不干扰。
    （不用 `from ... import b01_engine` 是因为 ruff 的 F811 会把
    用例参数里的同名标识符判为重定义，只能逐行 noqa，噪声过大。）

    Yields:
        AsyncIterator[AsyncEngine]: 异步引擎；用例结束后释放。
    """
    engine = create_async_engine(
        normalize_database_url(_require_app_dsn()),
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        await assert_rls_not_bypassed(engine)
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def b01_concurrent_engine() -> AsyncIterator[AsyncEngine]:
    """多连接池引擎（`pool_size=8`），用于并发隔离断言。

    Yields:
        AsyncIterator[AsyncEngine]: 异步引擎；用例结束后释放。
    """
    engine = create_async_engine(
        normalize_database_url(_require_app_dsn()),
        pool_size=8,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        await assert_rls_not_bypassed(engine)
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def b01_superuser_engine() -> AsyncIterator[AsyncEngine]:
    """超级用户引擎（仅供清理与反向验证，**绝不可**用于隔离断言）。

    Yields:
        AsyncIterator[AsyncEngine]: 异步引擎；用例结束后释放。
    """
    engine = create_async_engine(
        normalize_database_url(_require_superuser_dsn()),
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def b01_tenants(b01_engine: AsyncEngine) -> AsyncIterator[dict[str, dict[str, str]]]:
    """造两个租户及各自的哨兵知识库（含清理）。

    造数在 `set_config('app.tenant_id', …, TRUE)` 事务内完成，
    因此对非超级用户也合法（满足 RLS 的 `WITH CHECK`）。

    Args:
        b01_engine: 单连接池引擎。

    Yields:
        AsyncIterator[dict[str, dict[str, str]]]: `{"a": {...}, "b": {...}}`，
        每项含 `tenant_id` / `base_id` / `name` / `user_id`。
    """
    suffix = uuid.uuid4().hex[:8]
    tenants: dict[str, dict[str, str]] = {}
    for key in ("a", "b"):
        tenants[key] = {
            "tenant_id": f"tnt_b01_{suffix}_{key}",
            "base_id": f"kb_b01_{suffix}_{key}",
            "name": f"{SENTINEL_PREFIX}_{suffix.upper()}_{key.upper()}",
            "user_id": f"usr_b01_{suffix}_{key}",
        }

    for item in tenants.values():
        async with b01_engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    sql_text("SELECT set_config('app.tenant_id', :tid, TRUE)"),
                    {"tid": item["tenant_id"]},
                )
                await connection.execute(
                    sql_text(
                        "INSERT INTO tenant (tenant_id, name, deploy_mode) "
                        "VALUES (:tid, :name, 'saas') ON CONFLICT (tenant_id) DO NOTHING"
                    ),
                    {"tid": item["tenant_id"], "name": item["name"]},
                )
                await connection.execute(
                    sql_text(
                        "INSERT INTO knowledge_base_meta "
                        "(tenant_id, base_id, name, owner_user_id, created_by) "
                        "VALUES (:tid, :bid, :name, :uid, :uid)"
                    ),
                    {
                        "tid": item["tenant_id"],
                        "bid": item["base_id"],
                        "name": item["name"],
                        "uid": item["user_id"],
                    },
                )

    try:
        yield tenants
    finally:
        for item in tenants.values():
            async with b01_engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(
                        sql_text("SELECT set_config('app.tenant_id', :tid, TRUE)"),
                        {"tid": item["tenant_id"]},
                    )
                    await connection.execute(
                        sql_text(
                            "DELETE FROM knowledge_base_meta WHERE tenant_id = :tid "
                            "AND base_id = :bid"
                        ),
                        {"tid": item["tenant_id"], "bid": item["base_id"]},
                    )
                    await connection.execute(
                        sql_text("DELETE FROM tenant WHERE tenant_id = :tid"),
                        {"tid": item["tenant_id"]},
                    )


def scratch_name(prefix: str, label: str = "") -> str:
    """返回**进程 + 用例唯一**的临时库名（防并发 `DROP`/`CREATE` 互踢）。

    为什么必须有这个函数（阶段三实测的假抖动）：
    `create_empty_database()` 开头就是 `DROP DATABASE IF EXISTS {name}` +
    `CREATE DATABASE {name}`，`finally` 里再 `drop_database`。若库名是**固定**的，
    两个并发进程（两个人同时跑 `--run-pg`，或 CI 上并行分片）会互相把对方正在
    `--apply` 的库端掉 → 现象是
    `InvalidCatalogNameError: database "oa_qa_g11_cli" does not exist`，
    而同一命令再跑一遍又绿了。**这种"跑两遍才绿"的用例在 CI 上表现为随机红**，
    最容易被误判成代码缺陷（本轮就误判过一次，结论"缺库、建议预建"是错的：
    预建既会被 `finally` 删掉，也挡不住 `DROP`/`CREATE` 的竞态）。

    名字里同时带 pid 与短 uuid：
    - **pid** 区分并发进程（跨人、跨分片）；
    - **短 uuid** 区分同一进程里的不同用例（同一用例连跑两遍也不会撞）；
    - 总长控制在 PostgreSQL 标识符上限 63 以内。

    Args:
        prefix: 业务前缀（如 `oa_qa_g11`）。
        label: 用例标签（如 `cli` / `apply`），便于在 `pg_database` 里认人。

    Returns:
        str: 形如 `oa_qa_g11_cli_2f3a_9c1d4e` 的库名。
    """
    suffix = f"{os.getpid():x}_{uuid.uuid4().hex[:6]}"
    body = f"{prefix}_{label}" if label else prefix
    return f"{body}_{suffix}"[:63]


async def list_policy_tables(engine: AsyncEngine, policy: str) -> list[str]:
    """列出挂在指定 RLS 策略下的表名。

    Args:
        engine: 异步引擎。
        policy: 策略名（`p_tenant_isolation` / `p_catalog_shared`）。

    Returns:
        list[str]: 表名清单（升序）。
    """
    async with engine.connect() as connection:
        rows = (
            await connection.execute(sql_text(_BUSINESS_TABLES_SQL), {"policy": policy})
        ).scalars()
    return [str(name) for name in rows]


async def create_empty_database(superuser_dsn_value: str, name: str) -> str:
    """用超级用户建一个**空库**并把属主移交应用角色（G-11 前置）。

    应用角色 `NOCREATEDB`，无法自建空库；而把属主交给它之后，
    它就拥有该库 `public` 模式的全部 DDL 权限，可以像真实部署一样跑迁移。

    Args:
        superuser_dsn_value: 超级用户连接串。
        name: 目标库名。

    Returns:
        str: 指向新建空库的应用角色连接串。
    """
    import asyncpg

    connection = await asyncpg.connect(_swap_database(to_raw_dsn(superuser_dsn_value), "postgres"))
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await connection.execute(f"DROP DATABASE IF EXISTS {name}")
        await connection.execute(f"CREATE DATABASE {name}")
        await connection.execute(f"ALTER DATABASE {name} OWNER TO {APP_ROLE}")
    finally:
        await connection.close()

    # **扩展必须由超级用户预建**：`vector` 的 trusted=false，应用角色**即使是
    # 库属主**也建不出 `CREATE EXTENSION vector`（实测 pgvector 0.8.7）。
    # 真实部署里这一步由 `init-schema-phase4.sql`（initdb 超级用户）或 DBA 完成；
    # 临时空库必须等价模拟一次，否则 0009 在每个 `--run-pg` 用例里第一步就死。
    installer = await asyncpg.connect(_swap_database(superuser_dsn_value, name))
    try:
        await installer.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await installer.close()

    return _swap_database(superuser_dsn_value, name).replace(
        f"://{SUPERUSER_ROLE}:", f"://{APP_ROLE}:", 1
    )


async def drop_database(superuser_dsn_value: str, name: str) -> None:
    """删除临时库（G-11 收尾）。

    Args:
        superuser_dsn_value: 超级用户连接串。
        name: 目标库名。
    """
    import asyncpg

    connection = await asyncpg.connect(_swap_database(to_raw_dsn(superuser_dsn_value), "postgres"))
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await connection.execute(f"DROP DATABASE IF EXISTS {name}")
    finally:
        await connection.close()


async def fetch_scalar(engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None) -> Any:
    """执行查询并返回标量。

    Args:
        engine: 异步引擎。
        sql: SQL 文本。
        params: 命名参数。

    Returns:
        Any: 标量值。
    """
    async with engine.connect() as connection:
        return (await connection.execute(sql_text(sql), params or {})).scalar()


__all__ = [
    "APP_DSN_ENV",
    "APP_ROLE",
    "BUSINESS_POLICY",
    "CATALOG_POLICY",
    "SENTINEL_PREFIX",
    "SUPERUSER_DSN_ENV",
    "SUPERUSER_ROLE",
    "assert_rls_not_bypassed",
    "b01_app_dsn",
    "b01_concurrent_engine",
    "b01_engine",
    "b01_superuser_dsn",
    "b01_superuser_engine",
    "b01_tenants",
    "create_empty_database",
    "drop_database",
    "fetch_scalar",
    "list_policy_tables",
    "scratch_name",
    "to_async_dsn",
    "to_raw_dsn",
]
