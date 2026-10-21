"""运营后台端点集成测试（阶段二 B10，架构文档 §4.6 OPS1-OPS8）。

覆盖：企业列表/详情/停用、订单列表/核销、平台统计、配置读写。
平台令牌的租户声明为 `__platform__`（SaaS 模式下才会被保留；
私有化模式会把租户收敛为 `default`，此时运营后台不可用，属预期）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.business.plan_service import PlanService
from app.business.subscription_service import SubscriptionService
from app.business.usage_service import UsageService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ErrorCode
from app.db.repositories.billing_repo import (
    PLAN_PROFESSIONAL,
    get_billing_store,
)
from app.db.repositories.usage_repo import UsageEventRecord, get_usage_store
from app.main import create_app
from app.schemas.billing import ChangeSubscriptionRequest
from tests.conftest import make_token

pytestmark = pytest.mark.integration

TENANT_A = "tnt_ops_ep_a"
TENANT_B = "tnt_ops_ep_b"
OPS_TENANT = "__platform__"


@pytest.fixture
def saas_settings() -> Settings:
    """SaaS 模式配置（运营后台只在 SaaS 平台侧可用）。

    Returns:
        Settings: 测试配置。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="ops-test-secret",
        ratelimit_enabled=False,
        default_tenant_id="default",
    )


@pytest.fixture
def ops_app(saas_settings: Settings) -> FastAPI:
    """运营后台测试应用。

    Args:
        saas_settings: 配置实例。

    Returns:
        FastAPI: 应用实例。
    """
    return create_app(saas_settings)


@pytest.fixture
def ops_client(ops_app: FastAPI) -> Iterator[TestClient]:
    """运营后台测试客户端。

    Args:
        ops_app: 应用实例。

    Yields:
        Iterator[TestClient]: 测试客户端。
    """
    with TestClient(ops_app) as client:
        yield client


def _ops_headers(settings: Settings, roles: list[str] | None = None) -> dict[str, str]:
    """构造运营令牌请求头（租户声明 `__platform__`，不带 X-Tenant-Id）。

    Args:
        settings: 配置实例。
        roles: 角色列表。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings,
        user_id="usr_ops_tester",
        tenant_id=OPS_TENANT,
        roles=roles if roles is not None else ["ops_operator"],
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenant(settings: Settings, tenant_id: str, name: str) -> None:
    """登记企业并写入一条用量（用于统计口径断言）。

    Args:
        settings: 配置实例。
        tenant_id: 企业 ID。
        name: 企业名称。
    """
    store = get_billing_store(settings)
    await store.ensure_tenant(tenant_id, name)
    await get_usage_store(settings).append(
        UsageEventRecord(
            tenant_id=tenant_id,
            user_id=f"usr_{tenant_id}",
            capability="chat",
            idem_key=f"evt_{tenant_id}",
            units=10,
            event_id=f"use_{tenant_id}",
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    )


@pytest.mark.asyncio
@pytest.mark.p0
async def test_ops_tenants_list_and_detail(ops_client: TestClient, saas_settings: Settings) -> None:
    """OPS1 / OPS2：企业列表与详情字段齐备。"""
    await _seed_tenant(saas_settings, TENANT_A, "甲公司")
    headers = _ops_headers(saas_settings)

    response = ops_client.get("/api/v1/ops/tenants", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["total"] >= 1
    names = [item["tenant_id"] for item in data["items"]]
    assert TENANT_A in names
    item = next(entry for entry in data["items"] if entry["tenant_id"] == TENANT_A)
    assert item["plan_code"] == "free"
    assert item["quota_units_used"] >= 10

    detail = ops_client.get(f"/api/v1/ops/tenants/{TENANT_A}", headers=headers)
    assert detail.status_code == 200, detail.text
    tenant = detail.json()["data"]["tenant"]
    assert tenant["name"] == "甲公司"
    assert "email" not in str(detail.json()), "响应不得包含邮箱字段"

    missing = ops_client.get("/api/v1/ops/tenants/tnt_not_exists", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


@pytest.mark.asyncio
async def test_ops_suspend_tenant(ops_client: TestClient, saas_settings: Settings) -> None:
    """OPS3：停用企业后再启用，状态与原因都落到存储与响应里。"""
    await _seed_tenant(saas_settings, TENANT_B, "乙公司")
    headers = _ops_headers(saas_settings)
    suspend = ops_client.patch(
        f"/api/v1/ops/tenants/{TENANT_B}",
        headers=headers,
        json={"status": "suspended", "reason": "欠费停用"},
    )
    assert suspend.status_code == 200, suspend.text
    assert suspend.json()["data"]["status"] == "suspended"
    tenant = await get_billing_store(saas_settings).get_tenant(TENANT_B)
    assert tenant is not None
    assert tenant.status == "suspended"
    assert tenant.suspended_reason == "欠费停用"

    resume = ops_client.patch(
        f"/api/v1/ops/tenants/{TENANT_B}",
        headers=headers,
        json={"status": "active", "reason": "补缴恢复"},
    )
    assert resume.status_code == 200
    assert resume.json()["data"]["status"] == "active"


@pytest.mark.asyncio
async def test_ops_orders_list_and_confirm(ops_client: TestClient, saas_settings: Settings) -> None:
    """OPS4 / OPS5：订单列表可查，核销后 paid、订阅生效；重复核销 15008。"""
    await _seed_tenant(saas_settings, TENANT_A, "甲公司")
    billing = get_billing_store(saas_settings)
    plans = PlanService(saas_settings, billing)
    subscriptions = SubscriptionService(saas_settings, billing, plans=plans)
    await subscriptions.change(
        tenant_id=TENANT_A,
        actor_id="usr_owner_a",
        request=ChangeSubscriptionRequest(plan_code=PLAN_PROFESSIONAL, seats=5, action="upgrade"),
    )
    pending = await billing.find_pending_order(TENANT_A)
    assert pending is not None
    headers = _ops_headers(saas_settings)

    listed = ops_client.get("/api/v1/ops/orders", headers=headers, params={"tenant_id": TENANT_A})
    assert listed.status_code == 200, listed.text
    items = listed.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["order_id"] == pending.order_id
    assert items[0]["tenant_name"] == "甲公司"

    mismatch = ops_client.post(
        f"/api/v1/ops/orders/{pending.order_id}/confirm",
        headers=headers,
        json={"paid_amount_cents": 1, "remark": "金额录错"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["code"] == int(ErrorCode.ORDER_AMOUNT_MISMATCH)

    confirmed = ops_client.post(
        f"/api/v1/ops/orders/{pending.order_id}/confirm",
        headers=headers,
        json={"paid_amount_cents": pending.amount_cents, "remark": "对公到账"},
    )
    assert confirmed.status_code == 200, confirmed.text
    data = confirmed.json()["data"]
    assert data["status"] == "paid"
    assert data["subscription_status"] == "active"

    repeat = ops_client.post(
        f"/api/v1/ops/orders/{pending.order_id}/confirm",
        headers=headers,
        json={"paid_amount_cents": pending.amount_cents, "remark": "重复点击"},
    )
    assert repeat.status_code == 409
    assert repeat.json()["code"] == int(ErrorCode.ORDER_ALREADY_PROCESSED)


@pytest.mark.asyncio
@pytest.mark.p0
async def test_ops_metrics_matches_tenant_usage(
    ops_client: TestClient, saas_settings: Settings
) -> None:
    """OPS6：`range=30d` 的额度总和与 BL5 企业维度求和**误差 0**（验收 ③）。"""
    await _seed_tenant(saas_settings, TENANT_A, "甲公司")
    await _seed_tenant(saas_settings, TENANT_B, "乙公司")
    headers = _ops_headers(saas_settings)
    response = ops_client.get("/api/v1/ops/metrics", headers=headers, params={"range": "30d"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    usage = UsageService(saas_settings)
    expected = 0
    for tenant_id in (TENANT_A, TENANT_B):
        expected += await usage.tenant_total(tenant_id=tenant_id)
    assert data["quota_units_total"] == expected
    assert data["range"] == "30d"
    assert data["conversation_count"] >= 2
    assert any(item["plan_code"] == "free" for item in data["plan_distribution"])

    bad = ops_client.get("/api/v1/ops/metrics", headers=headers, params={"range": "90d"})
    assert bad.status_code == 400
    assert bad.json()["code"] == int(ErrorCode.PARAM_INVALID)


@pytest.mark.asyncio
async def test_ops_settings_read_and_patch(ops_client: TestClient, saas_settings: Settings) -> None:
    """OPS7 / OPS8：读默认配置、部分更新、空更新 20001。"""
    headers = _ops_headers(saas_settings)
    initial = ops_client.get("/api/v1/ops/settings", headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["data"]["quota_warn_threshold"] == 80
    assert set(initial.json()["data"]["plan_status"]) == {"free", "professional", "enterprise"}

    updated = ops_client.patch(
        "/api/v1/ops/settings", headers=headers, json={"trial_days": 30, "announcement": "公告"}
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()["data"]
    assert data["trial_days"] == 30
    assert data["announcement"] == "公告"
    assert data["updated_by"] == "usr_ops_tester"

    empty = ops_client.patch("/api/v1/ops/settings", headers=headers, json={})
    assert empty.status_code == 400
    assert empty.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_ops_requires_ops_permission(ops_client: TestClient, saas_settings: Settings) -> None:
    """只有 `ops:*` 权限点缺失时也拒绝（平台令牌 + 普通角色 → 20003）。"""
    headers = _ops_headers(saas_settings, roles=["member"])
    response = ops_client.get("/api/v1/ops/tenants", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


def test_ops_requires_authentication(ops_client: TestClient) -> None:
    """无令牌 → 20002（运营接口同样需要登录）。"""
    response = ops_client.get("/api/v1/ops/tenants")
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


def test_ops_response_has_no_sensitive_fields(
    ops_client: TestClient, saas_settings: Settings
) -> None:
    """运营响应不含邮箱、对话内容、文档原文与凭据字段（验收 ④）。"""
    headers = _ops_headers(saas_settings)
    bodies: list[Any] = [
        ops_client.get("/api/v1/ops/tenants", headers=headers).json(),
        ops_client.get("/api/v1/ops/orders", headers=headers).json(),
        ops_client.get("/api/v1/ops/metrics", headers=headers).json(),
        ops_client.get("/api/v1/ops/settings", headers=headers).json(),
    ]
    for body in bodies:
        text = str(body).lower()
        for forbidden in (
            "email",
            "phone",
            "conversation_content",
            "document_content",
            "secret",
            "password",
            "token",
            "credential",
        ):
            assert forbidden not in text, f"运营响应含敏感字段 {forbidden}"
