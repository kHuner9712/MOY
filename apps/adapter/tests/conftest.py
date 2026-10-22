"""pytest 共享夹具。

约定：
- 全部测试运行在**桩模式**（`OA_DOWNSTREAM_ENABLED=false`），不产生任何真实下游请求；
- 默认 `OA_BUSINESS_STORE=memory`（不依赖 PostgreSQL）；需要真实库的用例
  用 `--run-pg` 显式启用（QA 门禁口径：带 `--run-pg` 时 skipped 必须为 0）；
- 每个用例拿到独立的配置、服务单例与幂等/限流存储，避免相互污染；
- 令牌通过 `python-jose` 现场签发，遵循 `OA_AUTH_MODE=stub`（不验签）。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.business.audit_service import reset_audit_recorder
from app.business.credential_service import reset_credential_resolver
from app.business.order_service import reset_order_service
from app.business.payment_gateway import reset_payment_gateway
from app.business.plan_service import reset_plan_service
from app.business.plugin_grant_service import reset_plugin_grant_service
from app.business.plugin_review_service import reset_plugin_review_service
from app.business.plugin_service import reset_plugin_service
from app.business.policy_service import reset_policy_service
from app.business.segment_service import reset_segment_service
from app.business.subscription_service import reset_subscription_service
from app.business.usage_service import reset_usage_service
from app.clients.dify_client import reset_dify_client
from app.clients.keycloak_client import reset_keycloak_client
from app.core.config import Settings, get_settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.core.idempotency import (
    IdempotencyManager,
    InMemoryIdempotencyStore,
    build_fingerprint,
    reset_idempotency_manager,
)
from app.core.logging import reset_logging_for_test
from app.core.ratelimit import RateLimiter, reset_rate_limiter
from app.db.repositories.billing_repo import reset_billing_store
from app.db.repositories.credential_repo import reset_credential_stores
from app.db.repositories.document_repo import reset_document_store
from app.db.repositories.grant_repo import reset_grant_store
from app.db.repositories.knowledge_base_repo import reset_knowledge_base_store
from app.db.repositories.ops_repo import reset_ops_store
from app.db.repositories.org_repo import reset_org_store
from app.db.repositories.plugin_repo import reset_plugin_store
from app.db.repositories.plugin_review_repo import reset_review_store
from app.db.repositories.segment_repo import reset_segment_store
from app.db.repositories.tool_repo import reset_tool_store
from app.db.repositories.usage_repo import reset_usage_store
from app.main import create_app
from app.plugins.loader import reset_registry
from app.registry.loader import reset_registry_cache
from app.services.agent_service import reset_agent_service
from app.services.identity_service import reset_identity_service
from app.services.model_service import reset_model_service
from app.services.rag_service import reset_rag_service
from app.services.tool_service import reset_tool_service
from app.tools.wechat_mp_connector import reset_publish_registry

#: 测试用租户 ID
TEST_TENANT_ID = "tnt_test_tenant"

#: 测试用用户 ID
TEST_USER_ID = "usr_test_user"

#: 测试用 IDP 密钥
TEST_JWT_SECRET = "unit-test-secret"


def make_token(
    *,
    user_id: str = TEST_USER_ID,
    tenant_id: str = TEST_TENANT_ID,
    roles: list[str] | None = None,
    email: str = "tester@example.com",
    settings: Settings | None = None,
    expires_in: int = 3600,
) -> str:
    """签发测试用 JWT 令牌。

    Args:
        user_id: 用户 ID。
        tenant_id: 租户 ID。
        roles: 角色列表。
        email: 邮箱。
        settings: 配置实例，缺省读取全局单例。
        expires_in: 有效期（秒）。

    Returns:
        str: JWT 令牌字符串。
    """
    conf = settings or get_settings()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles if roles is not None else ["member"],
        "email": email,
        "aud": conf.jwt_audience,
        "iss": conf.jwt_issuer,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, conf.jwt_secret, algorithm=conf.jwt_algorithm)


def make_expired_token(*, settings: Settings | None = None) -> str:
    """签发已过期的测试令牌。

    Args:
        settings: 配置实例。

    Returns:
        str: 已过期的 JWT 令牌。
    """
    conf = settings or get_settings()
    now = int(time.time())
    return jwt.encode(
        {
            "sub": TEST_USER_ID,
            "tenant_id": TEST_TENANT_ID,
            "aud": conf.jwt_audience,
            "iss": conf.jwt_issuer,
            "exp": now - 60,
        },
        conf.jwt_secret,
        algorithm=conf.jwt_algorithm,
    )


# 说明：`pytest_addoption` / `pg_dsn` 在本文件末尾（阶段二 B01/B02 追加段）
# 各有一份定义，语义完全相同；此处**不再重复定义**，否则 ruff F811 与
# pytest 钩子重复注册都会红。真实 PostgreSQL 用例的开关见文件末尾。


def pytest_configure(config: pytest.Config) -> None:
    """把 `--run-pg` 记录到全局标记，供非 fixture 场景读取。

    Args:
        config: pytest 配置对象。
    """
    config.addinivalue_line("markers", "pg: 需要真实 PostgreSQL 的用例（用 --run-pg 启用）")


@pytest.fixture(autouse=True)
def reset_all_singletons() -> Iterator[None]:
    """每个用例前后重置全部进程级单例与缓存。"""
    _reset_all()
    yield
    _reset_all()


def _reset_all() -> None:
    """重置全部进程级单例与缓存（前后各调用一次）。

    B04 起知识库/文档/操作记录都有**进程级内存单例**：不清空会让
    「上一个用例建的知识库」出现在下一个用例的列表里（分页总数断言直接漂移）。
    """
    reset_settings_cache()
    reset_registry_cache()
    reset_idempotency_manager()
    reset_rate_limiter()
    reset_dify_client()
    reset_keycloak_client()
    reset_model_service()
    reset_rag_service()
    reset_agent_service()
    reset_tool_service()
    reset_identity_service()
    reset_credential_resolver()
    reset_credential_stores()
    reset_knowledge_base_store()
    reset_document_store()
    reset_grant_store()
    reset_segment_store()
    reset_segment_service()
    reset_policy_service()
    reset_usage_store()
    reset_usage_service()
    reset_tool_store()
    reset_publish_registry()
    reset_audit_recorder()
    reset_logging_for_test()
    # 阶段二 B08：组织/角色/投影存储。席位判据读「在册成员数」，
    # 不重置会让上一个用例的成员把本用例的席位顶高（QA-P2-W3-03）。
    reset_org_store()
    # 阶段二 B09/B10：计费与运营后台的进程级单例
    # 注意顺序：运营存储持有计费存储的引用，必须先重置它，否则会继续指向旧实例
    reset_ops_store()
    reset_billing_store()
    reset_plan_service()
    reset_subscription_service()
    reset_order_service()
    reset_payment_gateway()
    # 阶段三插件域：注册表 + 三个仓储 + 三个服务（含授权快照缓存）
    reset_registry()
    reset_plugin_store()
    reset_review_store()
    reset_plugin_grant_service()
    reset_plugin_review_service()
    reset_plugin_service()


@pytest.fixture
def settings() -> Settings:
    """桩模式配置实例（关闭真实下游调用与限流）。"""
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret=TEST_JWT_SECRET,
        ratelimit_enabled=False,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """FastAPI 测试应用实例。"""
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """同步测试客户端。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def token(settings: Settings) -> str:
    """有效测试令牌。"""
    return make_token(settings=settings)


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    """鉴权请求头。"""
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TEST_TENANT_ID}


@pytest.fixture
def idempotency_headers() -> dict[str, str]:
    """幂等键请求头。"""
    return {"Idempotency-Key": "11111111-2222-4333-8444-555555555555"}


@pytest.fixture
def make_idempotency_headers() -> Callable[[str], dict[str, str]]:
    """构造互不相同的幂等键请求头（同一用例内多次写操作时使用）。

    同一幂等键被复用于不同请求会返回 20005，因此一个用例内做多次写操作时
    必须为每次操作生成独立键。

    Returns:
        Callable[[str], dict[str, str]]: 以标签生成请求头的工厂。
    """

    def factory(tag: str) -> dict[str, str]:
        digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
        key = f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"
        return {"Idempotency-Key": key}

    return factory


@pytest.fixture
def manager() -> IdempotencyManager:
    """独立的幂等管理器实例。"""
    return IdempotencyManager(store=InMemoryIdempotencyStore())


@pytest.fixture
def limiter() -> RateLimiter:
    """启用限流的限流器实例。"""
    return RateLimiter(settings=Settings(ratelimit_enabled=True))


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncIterator[Any]:
    """异步测试客户端（供需要 await 的用例使用）。"""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_c:
        yield async_c


@pytest.fixture
def api_error() -> type[ApiError]:
    """业务异常类型（供类型标注使用）。"""
    return ApiError


@pytest.fixture
def error_code() -> type[ErrorCode]:
    """错误码枚举类型。"""
    return ErrorCode


@pytest.fixture
def fingerprint() -> str:
    """请求指纹样例。"""
    return build_fingerprint("POST", "/api/v1/model/generate", TEST_TENANT_ID, TEST_USER_ID)


# ===========================================================================
# 阶段二（B01/B02）：PostgreSQL 门控、RLS 隔离与连接权限夹具
#
# 设计要点（对应 docs/qa-phase2-test-plan.md §3.1 / §5.3 / §5.5）：
# 1. 默认 `pytest` 不跑 PG 用例（保持阶段一"30 秒内出结果"的习惯）；
#    `pytest --run-pg` 时必须 0 skip / 0 fail（CI 口径，skipped 即红）。
# 2. 所有 PG 夹具建连后**先做角色自检**：`rolbypassrls` 必须为 false。
#    超级用户（含 initdb 默认创建的 `officeai`）会绕过 FORCE ROW LEVEL SECURITY，
#    用它跑隔离用例会"全绿但无效"——比不测更危险，故直接 pytest.fail 而非 skip。
# 3. 连接池压到 1（pg_engine）用于"同一连接先后服务两个租户"的证明；
#    并发用例另用 pg_pool_engine（8 连接）证明真的并发。
# ===========================================================================

#: 测试库连接串环境变量（应指向 NOSUPERUSER NOBYPASSRLS 的应用角色，如 officeai_app）
PG_DSN_ENV = "OA_TEST_DATABASE_URL"

#: 隔离断言用的哨兵前缀（便于断言与清理，避免污染真实数据）
SENTINEL_PREFIX = "QA_P2_SENTINEL"


def pytest_addoption(parser: pytest.Parser) -> None:
    """注册 `--run-pg` 开关（默认关闭）。

    Args:
        parser: pytest 参数解析器。
    """
    parser.addoption(
        "--run-pg",
        action="store_true",
        default=False,
        help="运行需要真实 PostgreSQL 的用例（需同时设置 OA_TEST_DATABASE_URL）",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """未加 `--run-pg` 时跳过带 `pg` 标记的用例。

    Args:
        config: pytest 配置对象。
        items: 收集到的用例列表。
    """
    if config.getoption("--run-pg"):
        return
    skip_pg = pytest.mark.skip(
        reason="需要真实 PostgreSQL：加 --run-pg 并设置 OA_TEST_DATABASE_URL",
    )
    for item in items:
        if "pg" in item.keywords:
            item.add_marker(skip_pg)


async def assert_rls_not_bypassed(engine: Any) -> None:
    """前置断言：当前连接角色不得带 `BYPASSRLS`，否则隔离用例无效。

    用超级用户（`rolbypassrls=true`）跑 RLS 用例时，所有隔离断言都会"通过"，
    但通过的原因是**根本没有策略约束**，属于比不测更危险的假阳性 → 这里直接 fail。

    Args:
        engine: 异步引擎。

    Raises:
        pytest.fail.Exception: 角色缺失或带 BYPASSRLS 时失败。
    """
    from sqlalchemy import text as sql_text

    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    sql_text(
                        "SELECT current_user AS role_name, rolbypassrls AS bypass_rls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            )
            .mappings()
            .first()
        )

    if row is None:
        pytest.fail("无法确认数据库角色权限（pg_roles 查不到 current_user），RLS 隔离用例无效")

    if row["bypass_rls"] is not False:
        pytest.fail(
            "测试连接使用了超级用户，隔离用例无效："
            f"current_user={row['role_name']} rolbypassrls={row['bypass_rls']}。"
            "请改用 NOSUPERUSER NOBYPASSRLS 的应用角色（如 officeai_app）重跑。"
        )


@pytest.fixture
def pg_dsn() -> str:
    """测试库连接串（未提供则 skip）。

    Returns:
        str: `postgresql://` 连接串。

    Raises:
        pytest.skip.Exception: 未设置 `OA_TEST_DATABASE_URL`。
    """
    import os

    dsn = os.environ.get(PG_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"未提供 {PG_DSN_ENV}，跳过真实 PostgreSQL 用例")
    return dsn


@pytest.fixture
async def pg_engine(pg_dsn: str) -> AsyncIterator[Any]:
    """单连接池引擎（`pool_size=1`），用于证明"同一连接先后服务两个租户"。

    Args:
        pg_dsn: 测试库连接串。

    Yields:
        AsyncIterator[Any]: 异步引擎；用例结束后释放。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.engine import normalize_database_url

    engine = create_async_engine(
        normalize_database_url(pg_dsn),
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
async def pg_pool_engine(pg_dsn: str) -> AsyncIterator[Any]:
    """多连接池引擎（8 连接），用于并发隔离断言。

    Args:
        pg_dsn: 测试库连接串。

    Yields:
        AsyncIterator[Any]: 异步引擎；用例结束后释放。
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.engine import normalize_database_url

    engine = create_async_engine(
        normalize_database_url(pg_dsn),
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
async def seeded_tenants(pg_engine: Any) -> AsyncIterator[dict[str, dict[str, str]]]:
    """造两个租户及各自的哨兵知识库（含清理）。

    造数在 `set_config('app.tenant_id', ..., TRUE)` 事务内完成，
    因此对**非超级用户**也合法（满足 RLS 的 WITH CHECK）。

    Args:
        pg_engine: 单连接池引擎。

    Yields:
        AsyncIterator[dict[str, dict[str, str]]]: `{"a": {...}, "b": {...}}`，
        每项含 `tenant_id` / `base_id` / `name` / `user_id`。
    """
    import uuid

    from sqlalchemy import text as sql_text

    suffix = uuid.uuid4().hex[:8]
    tenants: dict[str, dict[str, str]] = {}
    for key in ("a", "b"):
        tenants[key] = {
            "tenant_id": f"tnt_qa_{suffix}_{key}",
            "base_id": f"kb_qa_{suffix}_{key}",
            "name": f"{SENTINEL_PREFIX}_{suffix.upper()}_{key.upper()}",
            "user_id": f"usr_qa_{suffix}_{key}",
        }

    async def seed_one(item: dict[str, str]) -> None:
        async with pg_engine.connect() as connection:
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
                    {"tid": item["tenant_id"], "name": f"QA {item['tenant_id']}"},
                )
                await connection.execute(
                    sql_text(
                        "INSERT INTO knowledge_base_meta "
                        "(tenant_id, base_id, name, owner_user_id, created_by) "
                        "VALUES (:tid, :bid, :name, :uid, :uid) "
                        "ON CONFLICT (tenant_id, base_id) DO NOTHING"
                    ),
                    {
                        "tid": item["tenant_id"],
                        "bid": item["base_id"],
                        "name": item["name"],
                        "uid": item["user_id"],
                    },
                )

    async def purge_one(item: dict[str, str]) -> None:
        async with pg_engine.connect() as connection:
            async with connection.begin():
                await connection.execute(
                    sql_text("SELECT set_config('app.tenant_id', :tid, TRUE)"),
                    {"tid": item["tenant_id"]},
                )
                await connection.execute(
                    sql_text("DELETE FROM knowledge_base_meta WHERE tenant_id = :tid"),
                    {"tid": item["tenant_id"]},
                )
                await connection.execute(
                    sql_text("DELETE FROM tenant WHERE tenant_id = :tid"),
                    {"tid": item["tenant_id"]},
                )

    for key in ("a", "b"):
        await seed_one(tenants[key])
    try:
        yield tenants
    finally:
        for key in ("b", "a"):
            await purge_one(tenants[key])
