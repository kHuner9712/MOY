"""鉴权上下文与权限校验单元测试（架构文档 3.0.2 / 3.5.1）。"""

from __future__ import annotations

import pytest
from fastapi import Request

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import (
    TENANT_HEADER,
    AuthContext,
    extract_bearer_token,
    require_permission,
    resolve_tenant_id,
)
from tests.conftest import TEST_TENANT_ID, TEST_USER_ID, make_expired_token, make_token

pytestmark = pytest.mark.unit


def _request(headers: dict[str, str] | None = None) -> Request:
    """构造最小 Request 对象。

    Args:
        headers: 请求头。

    Returns:
        Request: FastAPI 请求对象。
    """
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request({"type": "http", "headers": raw_headers, "method": "GET", "path": "/"})


def test_resolve_extracts_claims(settings: Settings) -> None:
    """从令牌声明中解析用户、租户、角色与邮箱。"""
    token = make_token(roles=["org_admin", "member"], settings=settings)
    auth = AuthContext.resolve(token, settings)
    assert auth.user_id == TEST_USER_ID
    assert auth.tenant_id == TEST_TENANT_ID
    assert auth.roles == ["org_admin", "member"]
    assert auth.email == "tester@example.com"


def test_resolve_extracts_realm_access_roles(settings: Settings) -> None:
    """兼容 realm_access.roles 形态的角色声明。"""
    import time

    from jose import jwt

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": TEST_USER_ID,
            "realm_access": {"roles": ["owner"]},
            "exp": now + 600,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert AuthContext.resolve(token, settings).roles == ["owner"]


@pytest.mark.parametrize("token", ["", "   "])
def test_resolve_rejects_empty_token(token: str, settings: Settings) -> None:
    """空令牌必须返回 20002。"""
    with pytest.raises(ApiError) as exc_info:
        AuthContext.resolve(token, settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_resolve_rejects_expired_token(settings: Settings) -> None:
    """过期令牌必须返回 20002。"""
    with pytest.raises(ApiError) as exc_info:
        AuthContext.resolve(make_expired_token(settings=settings), settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_resolve_rejects_token_without_subject(settings: Settings) -> None:
    """缺少 subject 声明的令牌必须返回 20002。"""
    import time

    from jose import jwt

    now = int(time.time())
    token = jwt.encode({"exp": now + 600}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(ApiError) as exc_info:
        AuthContext.resolve(token, settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_resolve_falls_back_to_default_tenant(settings: Settings) -> None:
    """无 tenant_id 声明时回落到配置默认租户。"""
    import time

    from jose import jwt

    now = int(time.time())
    token = jwt.encode(
        {"sub": TEST_USER_ID, "exp": now + 600},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert AuthContext.resolve(token, settings).tenant_id == settings.default_tenant_id


def test_extract_bearer_token_requires_scheme(settings: Settings) -> None:
    """Authorization 头必须使用 Bearer 前缀。"""
    assert extract_bearer_token(_request({"Authorization": "Bearer abc"})) == "abc"
    with pytest.raises(ApiError) as exc_info:
        extract_bearer_token(_request({"Authorization": "Basic abc"}))
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_extract_bearer_token_requires_header() -> None:
    """缺少 Authorization 头必须返回 20002。"""
    with pytest.raises(ApiError) as exc_info:
        extract_bearer_token(_request())
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_resolve_tenant_prefers_header(settings: Settings) -> None:
    """SaaS 模式下优先使用 X-Tenant-Id 请求头。"""
    saas = Settings(deploy_mode="saas")
    auth = AuthContext(user_id=TEST_USER_ID, tenant_id="tnt_from_token")
    resolved = resolve_tenant_id(_request({TENANT_HEADER: "tnt_from_header"}), auth, saas)
    assert resolved == "tnt_from_header"


def test_resolve_tenant_uses_token_claim_in_saas(settings: Settings) -> None:
    """SaaS 模式下无请求头时回落到令牌声明。"""
    saas = Settings(deploy_mode="saas")
    auth = AuthContext(user_id=TEST_USER_ID, tenant_id="tnt_from_token")
    assert resolve_tenant_id(_request(), auth, saas) == "tnt_from_token"


def test_resolve_tenant_ignores_header_in_private_mode(settings: Settings) -> None:
    """私有化模式恒返回 default 租户（单租户天然隔离）。"""
    private = Settings(deploy_mode="private")
    auth = AuthContext(user_id=TEST_USER_ID, tenant_id="tnt_from_token")
    assert resolve_tenant_id(_request({TENANT_HEADER: "tnt_x"}), auth, private) == "default"


def test_resolve_tenant_requires_identifier_in_saas(settings: Settings) -> None:
    """SaaS 模式下既无请求头也无令牌声明时返回 20001。"""
    saas = Settings(deploy_mode="saas")
    auth = AuthContext(user_id=TEST_USER_ID, tenant_id="default")
    with pytest.raises(ApiError) as exc_info:
        resolve_tenant_id(_request(), auth, saas)
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


def test_require_permission_defers_when_no_roles() -> None:
    """无角色声明时放行，由业务能力层最终判权。"""
    require_permission(AuthContext(user_id="u", tenant_id="t"), "knowledge:document:upload")


def test_require_permission_allows_wildcard_role() -> None:
    """通配角色允许全部权限。"""
    require_permission(AuthContext(user_id="u", tenant_id="t", roles=["*"]), "any:action")


def test_require_permission_rejects_missing_permission() -> None:
    """权限不足时返回 20003。"""
    auth = AuthContext(user_id="u", tenant_id="t", roles=["member"])
    with pytest.raises(ApiError) as exc_info:
        require_permission(auth, "knowledge:document:upload")
    assert exc_info.value.code == ErrorCode.FORBIDDEN


def test_require_permission_ignores_empty_permission() -> None:
    """空权限点直接放行。"""
    require_permission(AuthContext(user_id="u", tenant_id="t", roles=["member"]), "")
