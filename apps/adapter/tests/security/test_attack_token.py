"""攻击 1 & 2：伪造令牌与过期令牌（阶段四 T4-04.6）。

判据不是"返回 401 就完了"——还要证明**目标资源没被改动**：
一个 401 之后库里多出一条会话，说明鉴权发生在落库之后，安全上等价于没鉴权。

三种伪造形态（覆盖 JWT 最常见的三个坑）：

1. `alg=none`：把算法声明改空，指望服务端"不验签"；
2. 错误密钥签名：攻击者自己签一张，结构完全合法；
3. 篡改 `tenant_id`：用合法令牌改载荷，冒充另一个租户。

过期：`exp` 已过 60s。
"""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.security.conftest import (
    TENANT_A,
    TENANT_B,
    USER_A,
    WRONG_SECRET,
    issue_token,
    write_headers,
)

from app.db.repositories.conversation import get_conversation_store, get_message_store

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: 受保护端点（读：不落库，用于验签断言；写：用于"行数不变"断言）
READ_PATH = "/api/v1/model/catalog"
WRITE_PATH = "/api/v1/model/generate"
WRITE_BODY: dict[str, Any] = {
    "profile": "general",
    "messages": [{"role": "user", "content": "帮我写一句周报"}],
}


def _rows() -> tuple[int, int]:
    """取"会话 / 消息"两张表的当前行数。

    Returns:
        tuple[int, int]: `(会话数, 消息数)`。
    """
    from app.core.config import get_settings

    conf = get_settings()
    conversations = get_conversation_store(conf)
    messages = get_message_store(conf)
    return (len(getattr(conversations, "_items", {})), len(getattr(messages, "_items", [])))


def _b64(raw: bytes) -> str:
    """base64url 编码（去掉 padding，JWT 规范）。

    Args:
        raw: 原始字节。

    Returns:
        str: base64url 字符串。
    """
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unsigned_token(tenant: str) -> str:
    """手搓一个 `alg=none` 的令牌（第三段为空）。

    Args:
        tenant: 想冒充的租户。

    Returns:
        str: `header.payload.` 形态的令牌。
    """
    import json
    import time

    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64(
        json.dumps(
            {
                "sub": USER_A,
                "tenant_id": tenant,
                "aud": "officeai-adapter",
                "iss": "officeai",
                "iat": now,
                "exp": now + 3600,
            },
            separators=(",", ":"),
        ).encode()
    )
    return f"{header}.{payload}."


class TestForgedToken:
    """伪造令牌必须 401，且一行都不许写。"""

    def test_alg_none_is_rejected(self, oidc_client: TestClient) -> None:
        """`alg=none` 的令牌（无签名）被拒绝。

        这个 token 是**手搓**的：`PyJWT` 拒绝"用 none 签名"，而攻击者不会用
        现成库——他们直接拼 `header.payload.`（第三段留空）。这正是要防的形态。
        """
        token = _unsigned_token(TENANT_A)

        response = oidc_client.get(
            READ_PATH, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT_A}
        )

        assert response.status_code == 401
        assert response.json()["code"] == 20002

    def test_wrong_secret_is_rejected_and_writes_nothing(self, oidc_client: TestClient) -> None:
        """错误密钥签名的令牌 → 401，且**没有产生任何会话/消息**。"""
        before = _rows()
        token = issue_token(secret=WRONG_SECRET)

        response = oidc_client.post(WRITE_PATH, json=WRITE_BODY, headers=write_headers(token))

        assert response.status_code == 401
        assert response.json()["code"] == 20002
        assert _rows() == before, "鉴权失败却写入了数据：鉴权发生在落库之后"

    def test_tampered_tenant_claim_is_rejected(self, oidc_client: TestClient) -> None:
        """篡改 `tenant_id` 后签名失配 → 401（不能靠改载荷换租户）。"""
        legit = issue_token(tenant_id=TENANT_B)
        header = legit.split(".")[0]
        forged_payload = jwt.encode(
            {"sub": USER_A, "tenant_id": TENANT_A}, WRONG_SECRET, algorithm="HS256"
        ).split(".")[1]
        forged = f"{header}.{forged_payload}.{legit.split('.')[2]}"

        response = oidc_client.get(
            READ_PATH, headers={"Authorization": f"Bearer {forged}", "X-Tenant-Id": TENANT_A}
        )

        assert response.status_code == 401
        assert response.json()["code"] == 20002


class TestExpiredToken:
    """过期令牌必须 401。"""

    def test_expired_by_sixty_seconds(self, oidc_client: TestClient) -> None:
        """`exp` 已过 60s → 401 / 20002，且不写数据。"""
        before = _rows()
        token = issue_token(expires_in=-60)

        read = oidc_client.get(
            READ_PATH, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT_A}
        )
        write = oidc_client.post(WRITE_PATH, json=WRITE_BODY, headers=write_headers(token))

        assert read.status_code == 401
        assert write.status_code == 401
        assert read.json()["code"] == 20002
        assert _rows() == before
