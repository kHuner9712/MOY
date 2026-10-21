"""KB8 凭据绑定端点测试（阶段二 B03，架构文档 §4 KB8 / 2.3）。

覆盖点：

1. 契约形态：`PUT /api/v1/knowledge/bases/{base_id}/credential-binding` →
   `{code:0, data:{base_id, bound, updated_at}}`；
2. 权限：需 `knowledge:base:update`（无权限返回 20003）；
3. 幂等：缺 `Idempotency-Key` 返回 20001；同键同体重放 20007；
   **同键不同体必须 20005**（KB8 最易漏 `body_digest` 的坑）；
4. 不可用凭据 → 11007；
5. 换绑后「下一请求即生效」（无需重启）；
6. 响应与日志中**不得出现明文密钥**。
"""

from __future__ import annotations

import base64
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.credential_service import CredentialResolver
from app.core.config import Settings
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式下的固定租户（`deploy_mode=private` → 租户恒为 default）
TENANT = "default"

#: 知识库 ID
BASE_ID = "kb_01K4Q8Z2X7M9B3N5P6R8T0V2W4"

#: 两个凭据引用与对应明文
CRED_A = "cred_01K4Q8Z2X7M9B3N5P6R8T0V2W5"
CRED_B = "cred_01K4Q8Z2X7M9B3N5P6R8T0V2W6"
KEY_A = "app-bound-key-a-0001"
KEY_B = "app-bound-key-b-0002"

#: 端点路径
KB8_PATH = f"/api/v1/knowledge/bases/{BASE_ID}/credential-binding"


def cred_settings(settings: Settings) -> Settings:
    """在测试配置上补齐凭据加密密钥（固定密钥保证同用例内可解密）。

    Args:
        settings: 基础测试配置。

    Returns:
        Settings: 带密钥的配置。
    """
    return settings.model_copy(
        update={"credential_encryption_key": base64.b64encode(os.urandom(32)).decode("ascii")}
    )


def admin_headers(settings: Settings) -> dict[str, str]:
    """具备 KB8 权限的请求头。

    Args:
        settings: 配置实例。

    Returns:
        dict[str, str]: 鉴权 + 租户请求头。
    """
    token = make_token(settings=settings, roles=["*"])
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


async def seed_credential(settings: Settings, credential_ref: str, api_key: str) -> None:
    """把凭据加密入库（模拟运营侧预置）。

    Args:
        settings: 带密钥的配置。
        credential_ref: 凭据引用。
        api_key: 明文密钥。
    """
    resolver = CredentialResolver(settings=settings)
    await resolver.store_credential(credential_ref, api_key, tenant_id=TENANT)


@pytest.fixture
def bound_settings(settings: Settings) -> Settings:
    """带凭据密钥的配置。"""
    return cred_settings(settings)


async def test_kb8_binds_credential(
    client: TestClient,
    settings: Settings,
    bound_settings: Settings,
    make_idempotency_headers: Any,
) -> None:
    """KB8 成功绑定，响应只含 base_id / bound / updated_at。"""
    await seed_credential(bound_settings, CRED_A, KEY_A)

    response = client.put(
        KB8_PATH,
        json={"credential_ref": CRED_A},
        headers={
            **admin_headers(settings),
            **make_idempotency_headers("kb8-bind-ok"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert set(body["data"]) == {"base_id", "bound", "updated_at"}
    assert body["data"]["base_id"] == BASE_ID
    assert body["data"]["bound"] is True
    assert body["data"]["updated_at"]
    # 响应中不得出现任何密钥材料
    assert KEY_A not in response.text
    assert CRED_A not in response.text


async def test_kb8_requires_idempotency_key(
    client: TestClient, settings: Settings, bound_settings: Settings
) -> None:
    """缺少 Idempotency-Key → 20001。"""
    await seed_credential(bound_settings, CRED_A, KEY_A)
    response = client.put(
        KB8_PATH, json={"credential_ref": CRED_A}, headers=admin_headers(settings)
    )
    assert response.status_code == 400
    assert response.json()["code"] == 20001


async def test_kb8_requires_permission(
    client: TestClient, settings: Settings, bound_settings: Settings, make_idempotency_headers: Any
) -> None:
    """无 `knowledge:base:update` 权限 → 20003。"""
    await seed_credential(bound_settings, CRED_A, KEY_A)
    token = make_token(settings=settings, roles=["guest"])
    response = client.put(
        KB8_PATH,
        json={"credential_ref": CRED_A},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": TENANT,
            **make_idempotency_headers("kb8-forbidden"),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


async def test_kb8_same_key_different_body_conflicts(
    client: TestClient, settings: Settings, bound_settings: Settings
) -> None:
    """同 Idempotency-Key 但请求体不同 → 20005（指纹含 body_digest）。

    若实现漏掉请求体摘要，第二次会被当成重复请求并**静默回放首次结果**，
    用户看到「成功」但凭据并未换绑。
    """
    await seed_credential(bound_settings, CRED_A, KEY_A)
    await seed_credential(bound_settings, CRED_B, KEY_B)
    idem = {"Idempotency-Key": "aaaaaaaa-1111-4222-8333-444444444444"}

    first = client.put(
        KB8_PATH, json={"credential_ref": CRED_A}, headers={**admin_headers(settings), **idem}
    )
    assert first.json()["code"] == 0

    second = client.put(
        KB8_PATH, json={"credential_ref": CRED_B}, headers={**admin_headers(settings), **idem}
    )
    assert second.json()["code"] == 20005


async def test_kb8_replay_same_body_returns_20007(
    client: TestClient, settings: Settings, bound_settings: Settings
) -> None:
    """同键同体重放 → 20007，且回放数据与首次一致。"""
    await seed_credential(bound_settings, CRED_A, KEY_A)
    idem = {"Idempotency-Key": "bbbbbbbb-1111-4222-8333-444444444444"}

    first = client.put(
        KB8_PATH, json={"credential_ref": CRED_A}, headers={**admin_headers(settings), **idem}
    )
    replay = client.put(
        KB8_PATH, json={"credential_ref": CRED_A}, headers={**admin_headers(settings), **idem}
    )

    assert first.json()["code"] == 0
    assert replay.json()["code"] == 20007
    assert replay.json()["data"] == first.json()["data"]


async def test_kb8_unknown_credential_returns_11007(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """绑定不存在的凭据 → 11007（不允许把悬空引用写进库）。"""
    response = client.put(
        KB8_PATH,
        json={"credential_ref": "cred_01K4Q8Z2X7M9B3N5P6R8T0V2ZZ"},
        headers={**admin_headers(settings), **make_idempotency_headers("kb8-unknown")},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["code"] == 11007
    assert body["message"] == "该知识库暂时无法访问，请联系管理员"


async def test_binding_takes_effect_on_next_request(
    settings: Settings, bound_settings: Settings, make_idempotency_headers: Any
) -> None:
    """换绑后下一次解析即返回新密钥（缓存被精确删除，无需重启）。"""
    await seed_credential(bound_settings, CRED_A, KEY_A)
    await seed_credential(bound_settings, CRED_B, KEY_B)
    resolver = CredentialResolver(settings=bound_settings)

    await resolver.bind(BASE_ID, CRED_A, tenant_id=TENANT)
    assert (await resolver.resolve(BASE_ID, tenant_id=TENANT)).api_key == KEY_A

    await resolver.bind(BASE_ID, CRED_B, tenant_id=TENANT)
    assert (await resolver.resolve(BASE_ID, tenant_id=TENANT)).api_key == KEY_B


async def test_response_and_logs_never_leak_plaintext(
    client: TestClient,
    settings: Settings,
    bound_settings: Settings,
    make_idempotency_headers: Any,
    capsys: Any,
) -> None:
    """响应体与 stdout 日志中均不得出现明文密钥（架构文档 11.4）。"""
    await seed_credential(bound_settings, CRED_A, KEY_A)
    response = client.put(
        KB8_PATH,
        json={"credential_ref": CRED_A},
        headers={**admin_headers(settings), **make_idempotency_headers("kb8-no-leak")},
    )
    captured = capsys.readouterr()
    combined = response.text + captured.out + captured.err
    assert KEY_A not in combined
    assert CRED_A not in combined
    assert CRED_A[-6:] in combined, "日志应只记录 ref 后 6 位，便于定位"
