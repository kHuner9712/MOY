"""运营侧 MCP server 登记端点（矩阵第 3 项，2026-11-06）。

三条判据：

1. **企业令牌一律 20003**：MCP 登记是运营能力，企业侧调用四个端点都被权限守卫拦住，
   不给"企业自己接外部代码"的口子；
2. **平台令牌可完成登记 → 列表 → 探测 → 删除**，探测打的是**真实本地 HTTP server**
   （不是 mock），沿用 `tests/fixtures/minimal_mcp_server.py`；
3. **凭据只收引用**：明文密钥登记被拒（20001），且任何响应体都不出现凭据。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.main import create_app
from tests.conftest import make_token
from tests.fixtures import minimal_mcp_server

pytestmark = pytest.mark.integration

#: 企业租户（非运营）
TENANT_A = "tnt_mcp_api_a"

#: 平台哨兵租户（运营后台的租户声明）
OPS_TENANT = "__platform__"

#: 端点前缀
BASE_PATH = "/api/v1/ops/mcp-servers"

#: 一个形态合法的登记 ID（权限用例只需要路径参数过校验）
SAMPLE_SERVER_ID = "mcp_abcdef123456"


@pytest.fixture
def mcp_settings() -> Settings:
    """开启 MCP 能力的 SaaS 测试配置。

    Returns:
        Settings: 配置。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="mcp-api-test-secret",
        ratelimit_enabled=False,
        business_store="memory",
        mcp_enabled=True,
        # H-02：本地真实 MCP server 走回环；白名单必须显式声明（默认拒绝）。
        mcp_allowed_hosts="127.0.0.1,localhost",
        mcp_allow_loopback=True,
    )


@pytest.fixture
def client(mcp_settings: Settings) -> Iterator[TestClient]:
    """测试客户端。

    Args:
        mcp_settings: 配置。

    Yields:
        Iterator[TestClient]: 客户端。
    """
    app: FastAPI = create_app(mcp_settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mcp_server() -> Iterator[str]:
    """启动一个**真实**的本地 HTTP MCP server。

    Yields:
        Iterator[str]: endpoint。
    """
    server, endpoint = minimal_mcp_server.start_http_server()
    try:
        yield endpoint
    finally:
        minimal_mcp_server.stop_http_server(server)


def _headers(settings: Settings, *, ops: bool) -> dict[str, str]:
    """构造请求头。

    Args:
        settings: 配置（签发令牌用）。
        ops: True 表示平台运营令牌，False 表示企业普通成员令牌。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings,
        user_id="usr_mcp_api",
        tenant_id=OPS_TENANT if ops else TENANT_A,
        roles=["ops_operator"] if ops else ["member"],
    )
    return {"Authorization": f"Bearer {token}"}


def _payload(endpoint: str, **overrides: Any) -> dict[str, Any]:
    """构造登记载荷。

    Args:
        endpoint: 地址。
        **overrides: 覆盖项。

    Returns:
        dict[str, Any]: 载荷。
    """
    values: dict[str, Any] = {
        "tenant_id": TENANT_A,
        "name": "demo",
        "transport": "http",
        "endpoint": endpoint,
        "allowed_tools": ["echo"],
        "enabled": True,
    }
    values.update(overrides)
    return values


def test_enterprise_token_is_rejected_on_all_endpoints(
    client: TestClient, mcp_settings: Settings, mcp_server: str
) -> None:
    """企业令牌调用四个端点全部 20003。"""
    headers = _headers(mcp_settings, ops=False)
    calls = [
        client.get(BASE_PATH, headers=headers, params={"tenant_id": TENANT_A}),
        client.post(BASE_PATH, headers=headers, json=_payload(mcp_server)),
        client.delete(
            f"{BASE_PATH}/{SAMPLE_SERVER_ID}", headers=headers, params={"tenant_id": TENANT_A}
        ),
        client.post(
            f"{BASE_PATH}/{SAMPLE_SERVER_ID}/probe",
            headers=headers,
            params={"tenant_id": TENANT_A},
        ),
    ]
    for response in calls:
        assert response.status_code == 403, response.text
        assert response.json()["code"] == 20003


def test_ops_token_registers_probes_and_deletes(
    client: TestClient, mcp_settings: Settings, mcp_server: str
) -> None:
    """平台令牌：登记 → 列表 → 真实探测 → 删除，全程不出现凭据字段。"""
    headers = _headers(mcp_settings, ops=True)

    created = client.post(BASE_PATH, headers=headers, json=_payload(mcp_server))
    assert created.status_code == 200, created.text
    data = created.json()["data"]["item"]
    assert data["credential_configured"] is False
    assert "credential_ref" not in data
    server_id = data["server_id"]

    listed = client.get(BASE_PATH, headers=headers, params={"tenant_id": TENANT_A})
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 1
    assert "credential_ref" not in listed.text

    probed = client.post(
        f"{BASE_PATH}/{server_id}/probe",
        headers=headers,
        params={"tenant_id": TENANT_A},
    )
    assert probed.status_code == 200, probed.text
    result = probed.json()["data"]
    assert result["ok"] is True, result
    assert result["tools"] == ["echo"]

    deleted = client.delete(
        f"{BASE_PATH}/{server_id}", headers=headers, params={"tenant_id": TENANT_A}
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["deleted"] is True

    after = client.get(BASE_PATH, headers=headers, params={"tenant_id": TENANT_A})
    assert after.json()["data"]["total"] == 0


def test_plaintext_credential_is_rejected(
    client: TestClient, mcp_settings: Settings, mcp_server: str
) -> None:
    """明文密钥写不进登记表（只接受 `cred_` 引用）。"""
    headers = _headers(mcp_settings, ops=True)
    response = client.post(
        BASE_PATH,
        headers=headers,
        json=_payload(mcp_server, credential_ref="sk-live-123456"),
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == 20001


def test_cross_tenant_isolation(
    client: TestClient, mcp_settings: Settings, mcp_server: str
) -> None:
    """A 企业的登记对 B 企业不可见，也删不掉、探不到（跨租户四端点全覆盖）。"""
    headers = _headers(mcp_settings, ops=True)
    other_tenant = "tnt_mcp_api_b"

    created = client.post(BASE_PATH, headers=headers, json=_payload(mcp_server))
    server_id = created.json()["data"]["item"]["server_id"]

    listed = client.get(BASE_PATH, headers=headers, params={"tenant_id": other_tenant})
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 0
    assert server_id not in listed.text

    probed = client.post(
        f"{BASE_PATH}/{server_id}/probe",
        headers=headers,
        params={"tenant_id": other_tenant},
    )
    assert probed.status_code == 404, probed.text
    assert probed.json()["code"] == 20004

    deleted = client.delete(
        f"{BASE_PATH}/{server_id}", headers=headers, params={"tenant_id": other_tenant}
    )
    assert deleted.status_code == 404, deleted.text
    assert deleted.json()["code"] == 20004

    # A 的登记仍在（跨租户删除没有误删）
    still_there = client.get(BASE_PATH, headers=headers, params={"tenant_id": TENANT_A})
    assert still_there.json()["data"]["total"] == 1


def test_platform_sentinel_tenant_is_rejected(client: TestClient, mcp_settings: Settings) -> None:
    """平台哨兵不能当企业写进表里（20001，且提示具体企业）。"""
    headers = _headers(mcp_settings, ops=True)
    response = client.get(BASE_PATH, headers=headers, params={"tenant_id": OPS_TENANT})
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == 20001
    assert body["message"] == "请指定具体企业"


def test_unknown_server_returns_20004(client: TestClient, mcp_settings: Settings) -> None:
    """未登记的 ID：探测与删除都是 20004，不是 500。"""
    headers = _headers(mcp_settings, ops=True)
    probed = client.post(
        f"{BASE_PATH}/{SAMPLE_SERVER_ID}/probe",
        headers=headers,
        params={"tenant_id": TENANT_A},
    )
    assert probed.status_code == 404, probed.text
    assert probed.json()["code"] == 20004

    deleted = client.delete(
        f"{BASE_PATH}/{SAMPLE_SERVER_ID}", headers=headers, params={"tenant_id": TENANT_A}
    )
    assert deleted.status_code == 404, deleted.text
    assert deleted.json()["code"] == 20004


@pytest.mark.parametrize("transport", ["sse", "websocket", ""])
def test_unsupported_transport_is_rejected(
    client: TestClient, mcp_settings: Settings, mcp_server: str, transport: str
) -> None:
    """旧版 SSE / 未知传输在登记阶段拒绝（20001），不落库。"""
    headers = _headers(mcp_settings, ops=True)
    response = client.post(
        BASE_PATH, headers=headers, json=_payload(mcp_server, transport=transport)
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == 20001

    listed = client.get(BASE_PATH, headers=headers, params={"tenant_id": TENANT_A})
    assert listed.json()["data"]["total"] == 0


def test_boundary_inputs_are_validation_errors(client: TestClient, mcp_settings: Settings) -> None:
    """路径/查询参数越界统一 400 + 20001（不是 500，也不放行）。"""
    headers = _headers(mcp_settings, ops=True)

    too_short_tenant = client.get(BASE_PATH, headers=headers, params={"tenant_id": "x"})
    assert too_short_tenant.status_code == 400, too_short_tenant.text
    assert too_short_tenant.json()["code"] == 20001

    too_long_tenant = client.get(BASE_PATH, headers=headers, params={"tenant_id": "t" * 65})
    assert too_long_tenant.status_code == 400, too_long_tenant.text
    assert too_long_tenant.json()["code"] == 20001

    too_short_id = client.post(
        f"{BASE_PATH}/abc/probe", headers=headers, params={"tenant_id": TENANT_A}
    )
    assert too_short_id.status_code == 400, too_short_id.text
    assert too_short_id.json()["code"] == 20001


def test_disabled_capability_rejects_write_but_allows_read() -> None:
    """`mcp_enabled=false` 时登记/删除/探测 20003，列表仍可读（不泄漏写入口）。"""
    reset_settings_cache()
    conf = Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="mcp-api-test-secret",
        ratelimit_enabled=False,
        business_store="memory",
        mcp_enabled=False,
    )
    app: FastAPI = create_app(conf)
    with TestClient(app) as disabled_client:
        headers = _headers(conf, ops=True)
        listed = disabled_client.get(BASE_PATH, headers=headers, params={"tenant_id": TENANT_A})
        assert listed.status_code == 200, listed.text

        created = disabled_client.post(
            BASE_PATH,
            headers=headers,
            json=_payload("http://127.0.0.1:9/mcp"),
        )
        assert created.status_code == 403, created.text
        assert created.json()["code"] == 20003

        deleted = disabled_client.delete(
            f"{BASE_PATH}/{SAMPLE_SERVER_ID}", headers=headers, params={"tenant_id": TENANT_A}
        )
        assert deleted.status_code == 403, deleted.text
        assert deleted.json()["code"] == 20003

        probed = disabled_client.post(
            f"{BASE_PATH}/{SAMPLE_SERVER_ID}/probe",
            headers=headers,
            params={"tenant_id": TENANT_A},
        )
        assert probed.status_code == 403, probed.text
        assert probed.json()["code"] == 20003
    reset_settings_cache()
