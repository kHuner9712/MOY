"""计费端点与支付回调集成测试（阶段二 B09，架构文档 §4.5 BL1-BL10）。

验收要点（架构文档 9.2 B09「完成后可验证」逐条对齐）：

1. 免费版开通 `status=active`、`seats_total=3`；
2. 升级生成 `order_id` 且功能开关立即变化；
3. 降级 `effective_at` = 周期末（`notice_code=15005`，**HTTP 与业务码都是成功**）；
4. 席位越界 `15003`；
5. 重复回调 3 次只生效一次、不重复开票；
6. 待支付订单存在时再下单 `15004` 且不产生第二单；
7. `/api/v1/billing/payments/callback` 是**唯一**免鉴权业务端点（其余一律 401）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.business.payment_gateway import FakeGateway
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.middleware.error_handler import AUTH_EXEMPT_PATHS
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式下的固定租户（`deploy_mode=private` 会把租户收敛为 `default`）
TENANT_ID = "default"

PLANS_PATH = "/api/v1/billing/plans"
SUBSCRIPTION_PATH = "/api/v1/billing/subscription"
SEATS_PATH = "/api/v1/billing/subscription/seats"
ORDERS_PATH = "/api/v1/billing/orders"
INVOICES_PATH = "/api/v1/billing/invoices"
CALLBACK_PATH = "/api/v1/billing/payments/callback"

#: 对外响应禁词（技术术语/下游产品名，出现即视为泄漏）
FORBIDDEN_TERMS = ("dify", "milvus", "keycloak", "postgres", "traceback", "sqlexception")


def _headers(settings: Settings, roles: list[str] | None = None) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 角色列表。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings, roles=roles if roles is not None else ["owner"], tenant_id=TENANT_ID
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT_ID}


def _assert_no_leak(payload: Any) -> None:
    """断言响应中不含技术术语。

    Args:
        payload: 响应对象（dict/list/标量）。
    """
    text = str(payload).lower()
    for term in FORBIDDEN_TERMS:
        assert term not in text, f"响应含技术术语 {term}: {text[:200]}"


def _callback_body(order_id: str, amount_cents: int, txn: str) -> dict[str, Any]:
    """构造签名正确的回调载荷（测试网关）。

    Args:
        order_id: 订单 ID。
        amount_cents: 金额（分）。
        txn: 渠道流水号。

    Returns:
        dict[str, Any]: 回调载荷。
    """
    gateway = FakeGateway()
    return {
        "out_trade_no": order_id,
        "transaction_id": txn,
        "amount_total": amount_cents,
        "sign": gateway.sign(out_trade_no=order_id, transaction_id=txn, amount_total=amount_cents),
        "trade_state": "SUCCESS",
    }


def test_plans_requires_auth(client: TestClient) -> None:
    """BL1 需要登录（无令牌 → 20002）。"""
    response = client.get(PLANS_PATH)
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


@pytest.mark.p0
def test_plans_returns_three_catalog_entries(client: TestClient, settings: Settings) -> None:
    """BL1 返回三个套餐，价格与额度为「分」与「字数额度」，无技术术语。"""
    response = client.get(PLANS_PATH, headers=_headers(settings))
    assert response.status_code == 200
    body = response.json()
    _assert_no_leak(body)
    plans = body["data"]["plans"]
    assert [item["plan_code"] for item in plans] == ["free", "professional", "enterprise"]
    free = plans[0]
    assert free["price_per_seat_month_cents"] == 0
    assert free["min_seats"] == 3
    assert free["quota_unit_label"] == "字数额度"
    professional = plans[1]
    assert professional["price_per_seat_month_cents"] == 3_900
    assert any(feature["feature_key"] == "org_structure" for feature in professional["features"])
    assert all("token" not in str(item).lower() for item in plans)


def test_subscription_defaults_to_free_plan(client: TestClient, settings: Settings) -> None:
    """BL2 未开通时返回免费版合成视图（不返回 404）。"""
    response = client.get(SUBSCRIPTION_PATH, headers=_headers(settings, ["member"]))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["plan_code"] == "free"
    assert data["status"] == "active"
    assert data["seats_total"] == 3
    assert data["subscription_id"] == ""
    assert data["pending_change"] is None


def test_subscribe_free_plan_activates(client: TestClient, settings: Settings) -> None:
    """BL3 免费版一键开通：`status=active`、`seats_total=3`（验收 ①）。"""
    response = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings), **_idem("sub-free")},
        json={"plan_code": "free", "seats": 3, "action": "subscribe"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "active"
    assert data["order_id"] == ""
    detail = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    assert detail["status"] == "active"
    assert detail["seats_total"] == 3
    assert detail["plan_code"] == "free"


def test_member_cannot_manage_subscription(client: TestClient, settings: Settings) -> None:
    """BL3 成员无 `billing:subscription:manage` → 20003。"""
    response = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, ["member"]), **_idem("sub-member")},
        json={"plan_code": "professional", "seats": 5, "action": "subscribe"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


def test_upgrade_creates_order_and_switches_plan(client: TestClient, settings: Settings) -> None:
    """BL3 升级：返回 `order_id`，BL6 里的待支付订单可查，套餐立即切换（验收 ②）。"""
    response = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings), **_idem("sub-upgrade")},
        json={"plan_code": "professional", "seats": 5, "action": "upgrade"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["order_id"].startswith("ord_")
    assert data["status"] == "active"

    detail = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    assert detail["plan_code"] == "professional"
    assert detail["quota_units_total"] == 3_000_000 * 5

    orders = client.get(ORDERS_PATH, headers=_headers(settings)).json()["data"]
    assert orders["meta"]["total"] == 1
    assert orders["items"][0]["status"] == "pending_payment"
    assert orders["items"][0]["amount_cents"] == 3_900 * 5


def test_second_order_rejected_with_pending(client: TestClient, settings: Settings) -> None:
    """BL6 已有待支付订单 → 15004，且订单总数仍为 1（验收 ⑥）。"""
    first = client.post(
        ORDERS_PATH,
        headers={**_headers(settings), **_idem("order-1")},
        json={
            "plan_code": "professional",
            "seats": 5,
            "order_type": "new",
            "payment_method": "online",
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        ORDERS_PATH,
        headers={**_headers(settings), **_idem("order-2")},
        json={
            "plan_code": "professional",
            "seats": 5,
            "order_type": "new",
            "payment_method": "online",
        },
    )
    assert second.status_code == 409
    assert second.json()["code"] == int(ErrorCode.PENDING_ORDER_EXISTS)
    orders = client.get(ORDERS_PATH, headers=_headers(settings)).json()["data"]
    assert orders["meta"]["total"] == 1


def test_downgrade_takes_effect_at_period_end(client: TestClient, settings: Settings) -> None:
    """BL3 降级：`effective_at` = 周期末，`notice_code=15005`，业务码仍为 0（验收 ③）。"""
    client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings), **_idem("dg-upgrade")},
        json={"plan_code": "enterprise", "seats": 20, "action": "upgrade"},
    )
    before = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    response = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings), **_idem("dg-down")},
        json={"plan_code": "free", "seats": 3, "action": "downgrade"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, "降级是正常业务结果，不得返回非 0 码"
    data = body["data"]
    assert data["effective_at"] == before["current_period_end"]
    assert data["notice_code"] == int(ErrorCode.PLAN_CHANGE_PENDING)
    assert data["notice_message"] == "降级将在当前周期结束后生效"

    after = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    assert after["plan_code"] == "enterprise", "周期末之前仍是原套餐"
    assert after["pending_change"]["plan_code"] == "free"
    assert after["pending_change"]["effective_at"] == before["current_period_end"]


def test_seats_update_rejects_free_plan(client: TestClient, settings: Settings) -> None:
    """BL4 免费版不可调席位 → 15001。"""
    response = client.patch(
        SEATS_PATH,
        headers={**_headers(settings), **_idem("seats-free")},
        json={"seats": 10},
    )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.PLAN_FEATURE_UNAVAILABLE)
    assert "免费版席位固定" in response.json()["message"]


def test_callback_is_idempotent_through_http(client: TestClient, settings: Settings) -> None:
    """BL10 同一 payload 连发 3 次：都返回成功语义，但只生效一次（验收 ⑤）。"""
    created = client.post(
        ORDERS_PATH,
        headers={**_headers(settings), **_idem("cb-order")},
        json={
            "plan_code": "professional",
            "seats": 5,
            "order_type": "new",
            "payment_method": "online",
        },
    )
    order = created.json()["data"]
    payload = _callback_body(order["order_id"], order["amount_cents"], "txn_http_001")
    codes = []
    for _ in range(3):
        response = client.post(CALLBACK_PATH, json=payload)  # 故意不带 Authorization
        assert response.status_code == 200, response.text
        codes.append(response.json()["data"]["code"])
    assert codes == ["SUCCESS", "SUCCESS", "SUCCESS"]

    invoices = client.get(INVOICES_PATH, headers=_headers(settings)).json()["data"]
    assert len(invoices["items"]) == 1, "重复回调不得重复开票"
    detail = client.get(f"{ORDERS_PATH}/{order['order_id']}", headers=_headers(settings)).json()[
        "data"
    ]
    assert detail["status"] == "paid"
    assert detail["invoice_status"] == "issued"
    assert detail["payment_no_masked"].startswith("****")


def test_callback_missing_trade_state_keeps_order_pending(
    client: TestClient, settings: Settings
) -> None:
    """BL10 回调**完全不带**交易状态 → 15007，订单仍待支付（W-03 fail-open 回归）。

    这条用例走完整 HTTP 路径，因此同时守住四处默认值的**第一处**
    （`PaymentCallbackRequest.trade_state`）——只改网关不改 schema 时，本用例会红。
    """
    created = client.post(
        ORDERS_PATH,
        headers={**_headers(settings), **_idem("cb-nostate-order")},
        json={"plan_code": "professional", "seats": 5, "payment_method": "online"},
    )
    order = created.json()["data"]
    payload = _callback_body(order["order_id"], order["amount_cents"], "txn_nostate_http")
    payload.pop("trade_state")

    response = client.post(CALLBACK_PATH, json=payload)
    body = response.json()
    assert body["code"] == int(ErrorCode.PAYMENT_INCOMPLETE), body
    assert "支付未完成" in body["message"]
    _assert_no_leak(body)

    detail = client.get(f"{ORDERS_PATH}/{order['order_id']}", headers=_headers(settings)).json()[
        "data"
    ]
    assert detail["status"] == "pending_payment", "缺交易状态不得把订单推成已支付"
    invoices = client.get(INVOICES_PATH, headers=_headers(settings)).json()["data"]
    assert invoices["items"] == [], "缺交易状态不得开票"


def test_callback_rejects_bad_signature(client: TestClient, settings: Settings) -> None:
    """BL10 验签失败 → 20001（文案不含技术细节）。"""
    created = client.post(
        ORDERS_PATH,
        headers={**_headers(settings), **_idem("cb-bad-order")},
        json={"plan_code": "professional", "seats": 5, "payment_method": "online"},
    )
    order = created.json()["data"]
    payload = _callback_body(order["order_id"], order["amount_cents"], "txn_bad_http")
    payload["sign"] = "deadbeef" * 8
    response = client.post(CALLBACK_PATH, json=payload)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == int(ErrorCode.PARAM_INVALID)
    assert "校验未通过" in body["message"]
    _assert_no_leak(body)


def test_only_callback_is_auth_exempt(app: FastAPI) -> None:
    """免鉴权白名单之外的所有端点都必须要求 Bearer 令牌（G-24 机械防线）。"""
    unchecked: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            path = route.path
            probe = path
            for name in list(route.param_convertors):
                probe = probe.replace(f"{{{name}}}", "probe_value")
            if probe in AUTH_EXEMPT_PATHS:
                continue
            with TestClient(app) as probe_client:
                response = probe_client.request(
                    method,
                    probe,
                    json={} if method in {"POST", "PUT", "PATCH"} else None,
                )
            assert response.status_code in {401, 405}, (
                f"{method} {probe} 未要求鉴权（status={response.status_code}）"
            )
            if response.status_code == 401:
                assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
            else:  # pragma: no cover - 405 说明该路由不接受此方法，跳过即可
                unchecked.append(f"{method} {probe}")
    assert not unchecked, f"以下路由未能验证鉴权：{unchecked}"


@pytest.mark.p0
def test_usage_endpoint_still_consistent_with_subscription(
    client: TestClient, settings: Settings
) -> None:
    """BL5 与 BL2 的「总额度」同刻一致（同一算式，不是两套口径）。"""
    client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings), **_idem("usage-upgrade")},
        json={"plan_code": "professional", "seats": 10, "action": "upgrade"},
    )
    subscription = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    usage = client.get("/api/v1/billing/usage", headers=_headers(settings)).json()["data"]
    assert usage["quota_units_total"] == subscription["quota_units_total"]


def _idem(tag: str) -> dict[str, str]:
    """生成稳定的幂等键请求头（同一 tag 复用同一个键，便于断言幂等）。

    Args:
        tag: 标签。

    Returns:
        dict[str, str]: 请求头。
    """
    import hashlib

    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    key = f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"
    return {"Idempotency-Key": key}


#: 让类型检查器知道 `_idem` 的返回被当作请求头使用
_HEADER_FACTORY: Callable[[str], dict[str, str]] = _idem
