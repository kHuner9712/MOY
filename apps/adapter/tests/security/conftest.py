"""五类攻击用例的共享夹具（阶段四 T4-04.6）。

所有用例都标 `-m security`：它们是**验收红线**，不是普通回归——任何一项红都意味着
"能被绕过"，必须在发版前修掉，不能带着上线。

三条共用约定：

1. **用 `auth_mode=oidc`**：`stub` 模式不验签，伪造令牌在 stub 下"通过"是设计如此，
   拿 stub 测伪造令牌等于什么都没测；
2. **失败路径只看两件事**：状态码/业务码，以及"目标资源有没有被改动"；
3. **文案扫描用正则**：拒绝响应里不得出现 SQL、表名、堆栈、4-5 位业务码。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

#: 真实密钥（oidc 模式下用于签发"合法"令牌）
REAL_SECRET = "security-real-secret"

#: 伪造者用的错误密钥
WRONG_SECRET = "attacker-secret"

#: 测试租户与用户
TENANT_A = "tnt_sec_a"
TENANT_B = "tnt_sec_b"
USER_A = "usr_sec_a"
USER_B = "usr_sec_b"


@pytest.fixture
def oidc_settings() -> Settings:
    """启用真实验签的配置（`auth_mode=oidc`）。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="oidc",
        jwt_secret=REAL_SECRET,
        jwt_algorithm="HS256",
        jwt_audience="officeai-adapter",
        jwt_issuer="officeai",
        ratelimit_enabled=False,
    )


@pytest.fixture
def oidc_client(oidc_settings: Settings) -> Iterator[TestClient]:
    """oidc 模式的测试客户端。

    Args:
        oidc_settings: 配置。

    Yields:
        Iterator[TestClient]: 客户端。
    """
    app: FastAPI = create_app(oidc_settings)
    with TestClient(app) as client:
        yield client


def issue_token(
    *,
    secret: str = REAL_SECRET,
    tenant_id: str = TENANT_A,
    user_id: str = USER_A,
    roles: list[str] | None = None,
    expires_in: int = 3600,
    algorithm: str = "HS256",
) -> str:
    """签发一张令牌（可指定密钥/算法/过期）。

    Args:
        secret: 签名密钥。
        tenant_id: 租户。
        user_id: 用户。
        roles: 角色列表（None 表示**不带角色声明**）。
        expires_in: 有效期（秒，可为负）。
        algorithm: 签名算法。

    Returns:
        str: JWT。
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "aud": "officeai-adapter",
        "iss": "officeai",
        "iat": now,
        "exp": now + expires_in,
    }
    if roles is not None:
        payload["roles"] = roles
    return jwt.encode(payload, secret, algorithm=algorithm)


def write_headers(token: str, *, tenant: str = TENANT_A, key: str = "") -> dict[str, str]:
    """构造写请求头（含幂等键）。

    Args:
        token: 令牌。
        tenant: 租户头。
        key: 幂等键（空则用固定值）。

    Returns:
        dict[str, str]: 请求头。
    """
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant,
        "Idempotency-Key": key or "99999999-8888-4777-8666-555555555555",
    }


__all__: list[str] = [
    "REAL_SECRET",
    "TENANT_A",
    "TENANT_B",
    "USER_A",
    "USER_B",
    "WRONG_SECRET",
    "issue_token",
    "write_headers",
]
