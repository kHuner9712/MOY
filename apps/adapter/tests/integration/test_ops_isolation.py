"""运营后台隔离测试（阶段二 B10，架构文档 §4.6 + PRD 6.3 红线）。

这是 B10 的**红线用例**，逐条对齐验收：

1. 企业 token 调**任一** `/ops/**` 全 403（`20003`），且**不是 404**
   （不返回 404 是为了不暴露运维接口是否存在）；
2. 运营接口响应中**不可见客户内容**：无对话内容、文档原文、成员邮箱明文、凭据；
3. 平台作用域只能由运营令牌触发：企业令牌即使带 `X-Tenant-Id: __platform__`
   伪造平台视角也进不去（守卫看的是令牌租户声明 + 权限点，不看请求头）。

> 第 3 条是「请求头伪造」防线：`resolve_tenant_id` 会优先采用 `X-Tenant-Id`，
> 因此守卫必须同时校验权限点，否则任何企业成员加个请求头就能越权。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.core.errors import ErrorCode
from app.db.repositories.billing import get_billing_store
from app.db.repositories.usage import UsageEventRecord, get_usage_store
from app.main import create_app
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 企业租户（普通客户）
TENANT_ID = "tnt_ops_isolation"

#: 平台哨兵
OPS_TENANT = "__platform__"

#: 全部 `/ops/**` 端点（企业令牌必须逐个 403）
OPS_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/ops/tenants"),
    ("GET", f"/api/v1/ops/tenants/{TENANT_ID}"),
    ("PATCH", f"/api/v1/ops/tenants/{TENANT_ID}"),
    ("GET", "/api/v1/ops/orders"),
    ("POST", "/api/v1/ops/orders/ord_dummy/confirm"),
    ("GET", "/api/v1/ops/metrics"),
    ("GET", "/api/v1/ops/settings"),
    ("PATCH", "/api/v1/ops/settings"),
)

#: 请求体（避免因 body 校验先失败而掩盖判权结论）
OPS_BODIES: dict[str, dict[str, object]] = {
    "PATCH": {"status": "suspended", "reason": "越权尝试"},
    "POST": {"paid_amount_cents": 1, "remark": "越权尝试"},
}


@pytest.fixture
def saas_settings() -> Settings:
    """SaaS 模式配置。

    Returns:
        Settings: 测试配置。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="ops-isolation-secret",
        ratelimit_enabled=False,
        default_tenant_id="default",
    )


@pytest.fixture
def app(saas_settings: Settings) -> FastAPI:
    """测试应用。

    Args:
        saas_settings: 配置实例。

    Returns:
        FastAPI: 应用实例。
    """
    return create_app(saas_settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """测试客户端。

    Args:
        app: 应用实例。

    Yields:
        Iterator[TestClient]: 测试客户端。
    """
    with TestClient(app) as test_client:
        yield test_client


async def _seed(settings: Settings) -> None:
    """造一个企业 + 一条用量（既有内容型数据，也不该出现在运营响应里）。

    Args:
        settings: 配置实例。
    """
    store = get_billing_store(settings)
    await store.ensure_tenant(TENANT_ID, "被隔离测试企业")
    await get_usage_store(settings).append(
        UsageEventRecord(
            tenant_id=TENANT_ID,
            user_id="usr_isolation",
            capability="chat",
            idem_key="evt_isolation",
            units=42,
            event_id="use_isolation",
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    )


def _tenant_headers(settings: Settings, roles: list[str] | None = None) -> dict[str, str]:
    """构造企业令牌请求头。

    Args:
        settings: 配置实例。
        roles: 角色列表。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings,
        user_id="usr_tenant_admin",
        tenant_id=TENANT_ID,
        roles=roles if roles is not None else ["owner"],
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT_ID}


@pytest.mark.asyncio
@pytest.mark.p0
async def test_every_ops_endpoint_rejects_tenant_token(
    client: TestClient, saas_settings: Settings
) -> None:
    """企业 token 调任一 `/ops/**` → 403（20003），**不是 404**（验收 ①）。"""
    await _seed(saas_settings)
    headers = _tenant_headers(saas_settings)
    for method, path in OPS_ENDPOINTS:
        response = client.request(
            method,
            path,
            headers=headers,
            json=OPS_BODIES.get(method),
        )
        assert response.status_code == 403, f"{method} {path} 应 403，实际 {response.status_code}"
        body = response.json()
        assert body["code"] == int(ErrorCode.FORBIDDEN)
        assert body["data"] is None
        # 文案必须是业务语言，不得出现「运营」「平台」「无权限访问该接口」等技术/内部用语
        assert body["message"] == "当前账号没有该操作的权限"


def test_owner_role_cannot_reach_ops(client: TestClient, saas_settings: Settings) -> None:
    """即使企业 owner（拥有全部 16 个业务权限点）也不能进运营后台。"""
    headers = _tenant_headers(saas_settings, roles=["owner"])
    response = client.get("/api/v1/ops/settings", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


def test_forged_platform_header_is_rejected(client: TestClient, saas_settings: Settings) -> None:
    """企业令牌伪造 `X-Tenant-Id: __platform__` 也进不去（守卫看权限点，不看请求头）。"""
    token = make_token(
        settings=saas_settings,
        user_id="usr_tenant_admin",
        tenant_id=TENANT_ID,
        roles=["owner"],
    )
    response = client.get(
        "/api/v1/ops/tenants",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": OPS_TENANT},
    )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


def test_ops_operator_without_platform_scope_is_rejected(
    client: TestClient, saas_settings: Settings
) -> None:
    """`ops_operator` 角色但租户仍是企业 → 拒绝（租户层先于权限点层）。"""
    token = make_token(
        settings=saas_settings,
        user_id="usr_ops_but_tenant",
        tenant_id=TENANT_ID,
        roles=["ops_operator"],
    )
    response = client.get(
        "/api/v1/ops/tenants",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT_ID},
    )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


@pytest.mark.asyncio
@pytest.mark.p0
async def test_ops_never_exposes_customer_content(
    client: TestClient, saas_settings: Settings
) -> None:
    """运营响应不含对话内容、文档原文、成员邮箱明文与凭据（验收 ②）。"""
    await _seed(saas_settings)
    token = make_token(
        settings=saas_settings,
        user_id="usr_ops_tester",
        tenant_id=OPS_TENANT,
        roles=["ops_operator"],
    )
    headers = {"Authorization": f"Bearer {token}"}
    bodies = [
        client.get("/api/v1/ops/tenants", headers=headers).json(),
        client.get(f"/api/v1/ops/tenants/{TENANT_ID}", headers=headers).json(),
        client.get("/api/v1/ops/orders", headers=headers).json(),
        client.get("/api/v1/ops/metrics", headers=headers).json(),
    ]
    for body in bodies:
        text = str(body).lower()
        for forbidden in (
            "email",
            "member_email",
            "message_content",
            "conversation_content",
            "document_content",
            "segment_content",
            "password",
            "secret",
            "credential",
            "cipher",
        ):
            assert forbidden not in text, f"运营响应含客户内容字段 {forbidden}"
    # 正例：企业名称（基本信息）应可见，且用量为聚合数字
    detail = client.get(f"/api/v1/ops/tenants/{TENANT_ID}", headers=headers).json()["data"]
    assert detail["tenant"]["name"] == "被隔离测试企业"
    assert detail["usage"]["quota_units_used"] == 42


def test_private_deploy_mode_has_no_ops_console(client: TestClient) -> None:
    """私有化模式（单租户）下租户被收敛为 `default`，运营后台不可进入。

    私有化部署没有「平台侧」，运营后台只服务 SaaS 平台；此处断言的是
    「不因部署模式而意外放开」，而不是功能缺失。
    """
    settings = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="ops-private-secret",
        ratelimit_enabled=False,
    )
    private_app = create_app(settings)
    token = make_token(
        settings=settings, user_id="usr_ops", tenant_id=OPS_TENANT, roles=["ops_operator"]
    )
    with TestClient(private_app) as private_client:
        response = private_client.get(
            "/api/v1/ops/tenants", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)
