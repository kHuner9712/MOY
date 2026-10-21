"""pytest 共享夹具。

约定：
- 全部测试运行在**桩模式**（`OA_DOWNSTREAM_ENABLED=false`），不产生任何真实下游请求；
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

from app.business.credential_service import reset_credential_resolver
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
from app.db.repositories.credential_repo import reset_credential_stores
from app.main import create_app
from app.registry.loader import reset_registry_cache
from app.services.agent_service import reset_agent_service
from app.services.identity_service import reset_identity_service
from app.services.model_service import reset_model_service
from app.services.rag_service import reset_rag_service
from app.services.tool_service import reset_tool_service

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


@pytest.fixture(autouse=True)
def reset_all_singletons() -> Iterator[None]:
    """每个用例前后重置全部进程级单例与缓存。"""
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
    reset_logging_for_test()
    yield
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
