"""W3 独立验证：B09 计费订阅与订单支付 + B10 运营后台接口（冻结提交 `837f19b`）。

本文件由 **QA 独立编写**（不复用工程师用例的断言），逐条对齐架构文档 §9.2 的
「完成后可验证」，每条用例只证明**一个**验收点，失败即定位到具体条目：

| 用例 | 验收点（架构 §9.2） |
| --- | --- |
| `test_b09_1_*` | B09-① 免费版开通 `status=active`、`seats_total=3` |
| `test_b09_2_*` | B09-② 升级生成 `order_id` 且功能开关**立即**变化（`tenant.plan_code` 权威落点） |
| `test_b09_3_*` | B09-③ 降级 `effective_at` = 当前周期末，周期末前仍是原套餐 |
| `test_b09_4_*` | B09-④ 席位用满 `15003`（BL4 减席越界 + BL3 目标席位低于已用） |
| `test_b09_5_*` | B09-⑤ 同一回调连发 3 次：订阅只生效一次、**不重复开票**、留痕仅 1 条 |
| `test_b09_6_*` | B09-⑥ 有待支付订单再下单 → `15004` 且**不产生第二单** |
| `test_b09_7_*` | B09-⑦ 30 分钟未支付自动 `closed`（懒过期 + 补偿任务两条路径） |
| `test_b10_1_*` | B10-① 企业 token 调**任一** `/ops/**` 全 403（`20003`）且不泄露客户数据 |
| `test_b10_2_*` | B10-② 核销 → `paid` + 订阅生效 + `audit_log` 含运营人与备注 |
| `test_b10_3_*` | B10-③ `metrics?range=30d` 与 BL5 企业维度求和**误差 0** |
| `test_b10_4_*` | B10-④ 响应无对话内容/文档原文/成员邮箱明文/凭据（真实样本造数后全文扫描） |
| `test_extra_1_*` | 附加：支付回调是**唯一**免鉴权业务路径（其余 BL/OPS 无 token 必 401/403） |
| `test_extra_2_*` | 附加：支付密钥缺失时服务能启动、调用返业务错误（不得 500 堆栈外泄） |
| `test_extra_3_*` | 附加：BL 回归不破坏 M4/BL5 同源（B07 的 `used_units == quota_units_used`） |

写用例的口径约定（与 W2 报告 §5.6 一致）：

- 断言只依赖**对外契约**（HTTP 响应 + 公开服务方法），不 patch 业务函数；
  席位占用等前置数据一律**真实造数**（`org_store.link_member`），不 mock 判定；
- 幂等键、订单号、流水号全部按用例隔离，避免跨用例污染；
- 时间推进用「回填 `expires_at`」而不是改系统时钟，保证可复现且不做等待。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.business.audit_service import (
    ACTION_ORDER_CONFIRMED,
    AuditService,
)
from app.business.order_service import OrderService
from app.business.payment_gateway import FakeGateway
from app.business.subscription_service import SubscriptionService
from app.business.usage_service import UsageService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ERROR_REGISTRY, ErrorCode
from app.db.repositories.billing_repo import (
    PLAN_FREE,
    PLAN_PROFESSIONAL,
    get_billing_store,
)
from app.db.repositories.credential_repo import CredentialRecord, get_credential_store
from app.db.repositories.document_repo import DocumentRecord, get_document_store
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseRecord,
    get_knowledge_base_store,
)
from app.db.repositories.org_repo import (
    DepartmentRecord,
    MemberLink,
    get_org_store,
    reset_org_store,
)
from app.db.repositories.usage_repo import UsageEventRecord, get_usage_store
from app.main import create_app
from tests.conftest import make_token

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
#: 私有化模式下令牌/请求头里的租户会被收敛为该值（与 `tests/conftest.py` 一致）
TENANT: Final[str] = "default"

#: SaaS 模式下的两个客户企业（B10 用例）
TENANT_A: Final[str] = "tnt_w3_alpha"
TENANT_B: Final[str] = "tnt_w3_beta"

#: 运营平台哨兵（唯一被允许进入 `platform_scope()` 的租户声明）
OPS_TENANT: Final[str] = "__platform__"

#: 免费版固定席位（架构 §4.5 要点：无订阅时 `seats_total=3`）
FREE_SEATS: Final[int] = 3

#: 专业版每席位月价（分）与最小席位
PRO_PRICE: Final[int] = 3_900
PRO_MIN_SEATS: Final[int] = 5

#: 端点
PLANS_PATH: Final[str] = "/api/v1/billing/plans"
SUBSCRIPTION_PATH: Final[str] = "/api/v1/billing/subscription"
SEATS_PATH: Final[str] = "/api/v1/billing/subscription/seats"
ORDERS_PATH: Final[str] = "/api/v1/billing/orders"
INVOICES_PATH: Final[str] = "/api/v1/billing/invoices"
CALLBACK_PATH: Final[str] = "/api/v1/billing/payments/callback"
BL5_PATH: Final[str] = "/api/v1/billing/usage"
M4_PATH: Final[str] = "/api/v1/model/usage"
OPS_TENANTS: Final[str] = "/api/v1/ops/tenants"
OPS_ORDERS: Final[str] = "/api/v1/ops/orders"
OPS_METRICS: Final[str] = "/api/v1/ops/metrics"
OPS_SETTINGS: Final[str] = "/api/v1/ops/settings"

#: 全部 `/ops/**` 端点（B10-① 必须逐个 403）
OPS_ENDPOINTS: Final[tuple[tuple[str, str], ...]] = (
    ("GET", OPS_TENANTS),
    ("GET", f"{OPS_TENANTS}/{TENANT_A}"),
    ("PATCH", f"{OPS_TENANTS}/{TENANT_A}"),
    ("GET", OPS_ORDERS),
    ("POST", f"{OPS_ORDERS}/ord_probe/confirm"),
    ("GET", OPS_METRICS),
    ("GET", OPS_SETTINGS),
    ("PATCH", OPS_SETTINGS),
)

#: 上述端点对应的请求体（避免「因 body 校验失败」掩盖判权结论）
OPS_BODIES: Final[dict[str, dict[str, Any]]] = {
    "PATCH": {"status": "suspended", "reason": "越权尝试"},
    "POST": {"paid_amount_cents": 1, "remark": "越权尝试"},
}

#: 对外响应禁词（技术术语/下游产品名，出现即视为泄漏）
FORBIDDEN_TERMS: Final[tuple[str, ...]] = (
    "traceback",
    "sqlexception",
    "asyncpg",
    "paymentgatewaynotconfigured",
    "apiv3_key",
    "dify",
    "milvus",
    "keycloak",
)

#: 邮箱明文正则（B10-④ 全文扫描口径）
EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture
def saas_settings() -> Settings:
    """SaaS 模式配置（运营后台只在平台侧可用）。

    Returns:
        Settings: 测试配置（内存业务库 + 桩模式 + 假支付网关）。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="qa-w3-secret",
        ratelimit_enabled=False,
        default_tenant_id="default",
    )


@pytest.fixture
def saas_app(saas_settings: Settings) -> FastAPI:
    """SaaS 模式应用实例。

    Args:
        saas_settings: 配置实例。

    Returns:
        FastAPI: 应用实例。
    """
    return create_app(saas_settings)


@pytest.fixture
def saas_client(saas_app: FastAPI) -> Iterator[TestClient]:
    """SaaS 模式测试客户端。

    Args:
        saas_app: 应用实例。

    Yields:
        Iterator[TestClient]: 测试客户端。
    """
    with TestClient(saas_app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------
def _key(tag: str) -> dict[str, str]:
    """由标签派生稳定幂等键（UUID v4 形态，避免同键复用触发 20005）。

    Args:
        tag: 标签。

    Returns:
        dict[str, str]: 幂等键请求头。
    """
    import hashlib

    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return {
        "Idempotency-Key": (
            f"{digest[0:8]}-{digest[8:12]}-4{digest[12:15]}-8{digest[15:18]}-{digest[18:30]}"
        )
    }


def _headers(
    settings: Settings,
    *,
    roles: tuple[str, ...] = ("owner",),
    tenant_id: str = TENANT,
    user_id: str = "usr_w3_owner",
    idem: str = "",
) -> dict[str, str]:
    """构造企业侧鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 令牌角色。
        tenant_id: 租户 ID。
        user_id: 用户 ID。
        idem: 幂等键标签（空表示不带）。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, roles=list(roles), tenant_id=tenant_id)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}
    if idem:
        headers.update(_key(idem))
    return headers


def _ops_headers(settings: Settings, *, roles: tuple[str, ...] = ("ops_operator",)) -> dict:
    """构造运营侧鉴权请求头（租户声明为平台哨兵，不带 `X-Tenant-Id`）。

    Args:
        settings: 配置实例。
        roles: 令牌角色。

    Returns:
        dict: 请求头。
    """
    token = make_token(
        settings=settings, user_id="usr_w3_ops", roles=list(roles), tenant_id=OPS_TENANT
    )
    return {"Authorization": f"Bearer {token}"}


def _callback_payload(order_id: str, amount_cents: int, txn: str) -> dict[str, Any]:
    """构造签名正确的渠道回调载荷（测试网关口径）。

    Args:
        order_id: 商户订单号。
        amount_cents: 实付金额（分）。
        txn: 渠道流水号。

    Returns:
        dict[str, Any]: 回调载荷。
    """
    gateway = FakeGateway()
    return {
        "out_trade_no": order_id,
        "transaction_id": txn,
        "amount_total": amount_cents,
        "trade_status": "SUCCESS",
        "trade_state": "SUCCESS",
        "sign": gateway.sign(out_trade_no=order_id, transaction_id=txn, amount_total=amount_cents),
    }


def _subscriptions(settings: Settings) -> SubscriptionService:
    """构造与进程内单例共享同一存储的订阅服务。

    Args:
        settings: 配置实例。

    Returns:
        SubscriptionService: 订阅服务。
    """
    return SubscriptionService(settings, get_billing_store(settings))


async def _link_members(settings: Settings, tenant_id: str, count: int, tag: str = "") -> None:
    """往组织里塞 `count` 个在册成员（席位占用的真实数据来源）。

    席位占用口径是「组织在册成员数」，因此必须先建部门再建成员关联
    （孤儿 `department_member` 行不计入在册成员，与生产 SQL 口径一致）。

    **隔离说明**：`tests/conftest.py::_reset_all()` 目前**没有**重置组织存储
    （`reset_org_store()` 存在但未登记），因此本文件在造席位前显式调用它，
    避免「上一个用例的在册成员」把本用例的席位数字顶高（已登记 QA-P2-W3-03）。

    Args:
        settings: 配置实例。
        tenant_id: 企业 ID。
        count: 成员数。
        tag: 用例标签（保证成员/部门 ID 不跨用例复用）。
    """
    reset_org_store()
    store = get_org_store(settings)
    department_id = f"dept_w3_seats_{tag or count}"
    await store.create_department(
        DepartmentRecord(
            tenant_id=tenant_id,
            department_id=department_id,
            org_id=f"org_w3_{tag or count}",
            parent_id="",
            name="席位测试部",
            path=f"/{department_id}/",
            level=1,
        )
    )
    for index in range(count):
        await store.link_member(
            MemberLink(
                tenant_id=tenant_id,
                department_id=department_id,
                user_id=f"usr_w3_{tag or count}_seat_{index}",
                is_primary=index == 0,
            )
        )


def _assert_no_technical_leak(text: str, *, where: str) -> None:
    """断言文本不含技术术语/堆栈痕迹。

    Args:
        text: 待检查文本（响应体原文）。
        where: 断言失败时的定位信息。
    """
    lowered = text.lower()
    for term in FORBIDDEN_TERMS:
        assert term not in lowered, f"{where} 泄漏技术信息 {term}: {text[:300]}"


# ===========================================================================
# B09-① 免费版开通：status=active、seats_total=3
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_1_free_plan_subscribe_is_active_with_three_seats(
    client: TestClient, settings: Settings
) -> None:
    """未订阅时返回合成视图，一键开通后落库为 `active` + 3 席位（B09-①）。"""
    headers = _headers(settings)

    synthetic = client.get(SUBSCRIPTION_PATH, headers=headers)
    assert synthetic.status_code == 200, synthetic.text
    synthetic_data = synthetic.json()["data"]
    assert synthetic_data["plan_code"] == PLAN_FREE
    assert synthetic_data["status"] == "active"
    assert synthetic_data["seats_total"] == FREE_SEATS

    created = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-free-sub"), **_key("w3-free-sub")},
        json={"plan_code": PLAN_FREE, "seats": 1, "action": "subscribe"},
    )
    assert created.status_code == 200, created.text
    created_data = created.json()["data"]
    assert created_data["status"] == "active"
    assert created_data["order_id"] == "", "免费版开通不得产生订单"

    detail = client.get(SUBSCRIPTION_PATH, headers=headers).json()["data"]
    assert detail["status"] == "active"
    assert detail["seats_total"] == FREE_SEATS, "免费版席位固定为 3（前端传 1 也按 3 开通）"
    assert detail["plan_code"] == PLAN_FREE

    record = await get_billing_store(settings).get_active_subscription(TENANT)
    assert record is not None, "开通后必须落库一条有效订阅（不能只靠合成视图）"
    assert record.status == "active"
    assert record.seats_total == FREE_SEATS


# ===========================================================================
# B09-② 升级：生成 order_id 且功能开关立即变化
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_2_upgrade_returns_order_and_flips_tenant_plan_immediately(
    client: TestClient, settings: Settings
) -> None:
    """升级返回 `ord_` 订单；`tenant.plan_code`（功能开关权威落点）同刻翻转。"""
    store = get_billing_store(settings)
    await store.ensure_tenant(TENANT, "W3 升级测试企业")
    before_tenant = await store.get_tenant(TENANT)
    assert before_tenant is not None
    assert before_tenant.plan_code == PLAN_FREE, "前置：企业初始为免费版"

    response = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-upgrade"), **_key("w3-upgrade")},
        json={"plan_code": PLAN_PROFESSIONAL, "seats": 10, "action": "upgrade"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["order_id"].startswith("ord_"), "升级必须生成订单（在线支付路径）"
    assert data["status"] == "active"

    # 功能开关立即变化：`tenant.plan_code` 是前端读取能力的权威落点
    after_tenant = await store.get_tenant(TENANT)
    assert after_tenant is not None
    assert after_tenant.plan_code == PLAN_PROFESSIONAL, "升级后必须立即刷新企业套餐标记"

    subscription = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    assert subscription["plan_code"] == PLAN_PROFESSIONAL
    assert subscription["seats_total"] == 10
    assert subscription["quota_units_total"] == 3_000_000 * 10

    # 功能开关的对外表现：套餐目录里 professional 含组织架构能力，free 不含
    plans = client.get(PLANS_PATH, headers=_headers(settings)).json()["data"]["plans"]
    features = {
        item["plan_code"]: {
            feature["feature_key"]: feature["included"] for feature in item["features"]
        }
        for item in plans
    }
    assert features[PLAN_PROFESSIONAL]["org_structure"] is True
    assert features[PLAN_FREE]["org_structure"] is False

    # 订单侧可查：金额 = 单价 × 席位，状态待支付
    orders = client.get(ORDERS_PATH, headers=_headers(settings)).json()["data"]
    assert orders["meta"]["total"] == 1
    assert orders["items"][0]["order_id"] == data["order_id"]
    assert orders["items"][0]["amount_cents"] == PRO_PRICE * 10
    assert orders["items"][0]["status"] == "pending_payment"


# ===========================================================================
# B09-③ 降级：effective_at = 周期末，周期末前仍是原套餐
# ===========================================================================
@pytest.mark.asyncio
async def test_b09_3_downgrade_effective_at_is_period_end_plan_unchanged(
    client: TestClient, settings: Settings
) -> None:
    """降级是「预约生效」：`effective_at` == `current_period_end`，套餐与席位不变。"""
    upgraded = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-dg-up"), **_key("w3-dg-up")},
        json={"plan_code": PLAN_PROFESSIONAL, "seats": 10, "action": "upgrade"},
    )
    assert upgraded.status_code == 200, upgraded.text
    before = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    assert before["plan_code"] == PLAN_PROFESSIONAL
    assert before["current_period_end"], "周期末必须可读（降级生效时点的唯一依据）"

    response = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-dg-down"), **_key("w3-dg-down")},
        json={"plan_code": PLAN_FREE, "seats": FREE_SEATS, "action": "downgrade"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, "降级是正常业务结果，不得返回非 0 业务码"
    data = body["data"]
    assert data["effective_at"] == before["current_period_end"]
    assert data["order_id"] == "", "降级不产生订单"
    assert data["notice_code"] == int(ErrorCode.PLAN_CHANGE_PENDING)

    after = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    assert after["plan_code"] == PLAN_PROFESSIONAL, "周期末之前仍是原套餐"
    assert after["seats_total"] == before["seats_total"], "降级生效前席位不变"
    assert after["pending_change"] is not None
    assert after["pending_change"]["plan_code"] == PLAN_FREE
    assert after["pending_change"]["effective_at"] == before["current_period_end"]

    tenant = await get_billing_store(settings).get_tenant(TENANT)
    assert tenant is not None
    assert tenant.plan_code == PLAN_PROFESSIONAL, "功能开关不得提前降级"


# ===========================================================================
# B09-④ 席位用满 → 15003
# ===========================================================================
@pytest.mark.asyncio
async def test_b09_4_seats_exhausted_returns_15003_on_bl4_path(
    client: TestClient, settings: Settings
) -> None:
    """席位被 8 名在册成员占满时，BL4 减席越界必须 `15003` 且不改动席位（验收 ④）。"""
    reset_org_store()
    upgraded = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-seats-up"), **_key("w3-seats-up")},
        json={"plan_code": PLAN_PROFESSIONAL, "seats": 8, "action": "upgrade"},
    )
    assert upgraded.status_code == 200, upgraded.text
    await _link_members(settings, TENANT, 8, tag="bl4")
    assert await _subscriptions(settings).seats_used(TENANT) == 8, "前置：席位已占满 8"

    reduce = client.patch(
        SEATS_PATH,
        headers={**_headers(settings, idem="w3-seats-down"), **_key("w3-seats-down")},
        json={"seats": 5},
    )
    assert reduce.status_code == 400, reduce.text
    reduce_body = reduce.json()
    assert reduce_body["code"] == int(ErrorCode.SEATS_EXHAUSTED)
    assert reduce_body["message"] == "成员席位已用满，请先增加席位"
    _assert_no_technical_leak(str(reduce_body), where="BL4 席位越界响应")

    after_reduce = client.get(SUBSCRIPTION_PATH, headers=_headers(settings)).json()["data"]
    assert after_reduce["seats_total"] == 8, "越界失败不得改动席位"
    assert after_reduce["seats_used"] == 8


@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_4b_bl3_seat_guard_must_use_live_seats_used(
    client: TestClient, settings: Settings
) -> None:
    """BL3 变更席位的 15003 判据必须与 BL4 同源（实时在册成员数），不得用落库快照。

    **QA-P2-W3-01（P1）** 曾以 `xfail(strict=True)` 钉住：BL3 `_resolve_seats()` 读的是
    落库快照 `record.seats_used`（开通时为 0），于是「目标席位 < 已用席位」不报 15003，
    反而把订阅写成 `seats_total=5 / seats_used=8`。该缺陷已在 `20b8c45` 修复
    （判据改为实时用量，且 `已用 == 目标` 放行、只有 `已用 > 目标` 才拒），本用例已转常绿。
    """
    reset_org_store()
    store = get_billing_store(settings)
    upgraded = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-seats3-up"), **_key("w3-seats3-up")},
        json={"plan_code": PLAN_PROFESSIONAL, "seats": 8, "action": "upgrade"},
    )
    assert upgraded.status_code == 200, upgraded.text
    await _link_members(settings, TENANT, 8, tag="bl3")
    assert await _subscriptions(settings).seats_used(TENANT) == 8

    # 清掉待支付订单，排除 15004 干扰（本用例只验证席位判据）
    pending = await store.find_pending_order(TENANT)
    assert pending is not None
    await store.update_order(TENANT, pending.order_id, status="paid")

    change = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-seats3-chg"), **_key("w3-seats3-chg")},
        json={"plan_code": PLAN_PROFESSIONAL, "seats": PRO_MIN_SEATS, "action": "upgrade"},
    )
    assert change.status_code == 400, change.text
    assert change.json()["code"] == int(ErrorCode.SEATS_EXHAUSTED)

    # 反向断言：订阅数据不得落入「已用 > 总数」的自相矛盾状态
    record = await store.get_active_subscription(TENANT)
    assert record is not None
    assert record.seats_total >= record.seats_used, (
        f"订阅落入超卖状态 seats_total={record.seats_total} seats_used={record.seats_used}"
    )


@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_4c_invite_member_blocked_when_seats_full(
    client: TestClient, settings: Settings
) -> None:
    """免费版 3 席位被 3 名成员占满时，邀请新成员必须 `15003`（PRD P2-BL-02 验收 4）。

    **QA-P2-W3-02（P1）** 曾以 `xfail(strict=True)` 钉住：`invite_member` 没有任何席位校验，
    满席仍返 200 + `invitation_id`。该缺陷已在 `20b8c45` 修复（邀请入口接入席位判据，
    待接受邀请按 `extra_reserved=1` 预留），本用例已转常绿。
    """
    reset_org_store()
    subscribed = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-inv-sub"), **_key("w3-inv-sub")},
        json={"plan_code": PLAN_FREE, "seats": FREE_SEATS, "action": "subscribe"},
    )
    assert subscribed.status_code == 200, subscribed.text

    # 反面：已用 2 / 总 3，邀请再占 1 → 正好用满（2+1 == 3）必须放行
    await _link_members(settings, TENANT, 2, tag="invite_ok")
    assert await _subscriptions(settings).seats_used(TENANT) == 2
    allowed = client.post(
        "/api/v1/identity/organizations/org_w3_invite/invitations",
        headers={**_headers(settings, idem="w3-inv-ok"), **_key("w3-inv-ok")},
        json={"email": "still.room@corp-example.com", "role": "member", "message": ""},
    )
    assert allowed.status_code == 200, f"2+1==3 应放行（判据须用 >，不能用 >=）：{allowed.text}"

    # 正面：已用 3 / 总 3，再邀请 → 15003
    await _link_members(settings, TENANT, 3, tag="invite_full")
    assert await _subscriptions(settings).seats_used(TENANT) == FREE_SEATS

    invited = client.post(
        "/api/v1/identity/organizations/org_w3_invite/invitations",
        headers={**_headers(settings, idem="w3-inv"), **_key("w3-inv")},
        json={"email": "new.member@corp-example.com", "role": "member", "message": ""},
    )
    assert invited.status_code == 400, invited.text
    assert invited.json()["code"] == int(ErrorCode.SEATS_EXHAUSTED)
    assert invited.json()["message"] == "成员席位已用满，请先增加席位"


@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_4d_reduce_seats_to_exactly_used_is_allowed(
    client: TestClient, settings: Settings
) -> None:
    """`已用 == 目标席位` 是合法状态：10 席减到 8 席、正好 8 人在册必须放行。

    判据必须是 `used + reserved > target`；若误写成 `>=`，会把「正好用满」误拒，
    属典型的「修 A 引入 B」。本用例固定该边界。
    """
    reset_org_store()
    store = get_billing_store(settings)
    upgraded = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-eq-up"), **_key("w3-eq-up")},
        json={"plan_code": PLAN_PROFESSIONAL, "seats": 10, "action": "upgrade"},
    )
    assert upgraded.status_code == 200, upgraded.text
    pending = await store.find_pending_order(TENANT)
    assert pending is not None
    await store.update_order(TENANT, pending.order_id, status="paid")

    await _link_members(settings, TENANT, 8, tag="exactly_used")
    assert await _subscriptions(settings).seats_used(TENANT) == 8

    reduced = client.patch(
        SEATS_PATH,
        headers={**_headers(settings, idem="w3-eq-down"), **_key("w3-eq-down")},
        json={"seats": 8},
    )
    assert reduced.status_code == 200, f"8 已用减到 8 席必须放行：{reduced.text}"
    assert reduced.json()["data"]["seats_total"] == 8
    assert reduced.json()["data"]["seats_used"] == 8
    assert reduced.json()["data"]["order_id"] == "", "减席不产生订单"

    # 再减 1 席就真的不足了
    too_low = client.patch(
        SEATS_PATH,
        headers={**_headers(settings, idem="w3-eq-low"), **_key("w3-eq-low")},
        json={"seats": 7},
    )
    assert too_low.status_code == 400
    assert too_low.json()["code"] == int(ErrorCode.SEATS_EXHAUSTED)


# ===========================================================================
# B09-⑤ 回调幂等：连发 3 次只生效一次、不重复开票
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_5_callback_three_times_activates_once_and_invoices_once(
    client: TestClient, settings: Settings
) -> None:
    """同一 payload 连发 3 次：3 次都 200/SUCCESS，但订阅、发票、留痕都只有 1 份。"""
    created = client.post(
        ORDERS_PATH,
        headers={**_headers(settings, idem="w3-cb-order"), **_key("w3-cb-order")},
        json={
            "plan_code": PLAN_PROFESSIONAL,
            "seats": PRO_MIN_SEATS,
            "order_type": "new",
            "payment_method": "online",
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    payload = _callback_payload(order["order_id"], order["amount_cents"], "w3txn0001")

    store = get_billing_store(settings)
    first = client.post(CALLBACK_PATH, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["code"] == "SUCCESS"
    after_first = await store.get_active_subscription(TENANT)
    assert after_first is not None, "首次回调必须激活订阅"
    first_snapshot = (
        after_first.subscription_id,
        after_first.started_at,
        after_first.current_period_end,
    )

    for _ in range(2):
        repeat = client.post(CALLBACK_PATH, json=payload)
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["data"]["code"] == "SUCCESS", "渠道要求重复回调也必须返回成功语义"

    after_third = await store.get_active_subscription(TENANT)
    assert after_third is not None
    assert (
        after_third.subscription_id,
        after_third.started_at,
        after_third.current_period_end,
    ) == first_snapshot, "订阅只允许生效一次（订阅号与周期起点均不得变化）"

    invoices = client.get(INVOICES_PATH, headers=_headers(settings)).json()["data"]["items"]
    assert len(invoices) == 1, "一单一票：重复回调不得重复开票"
    assert invoices[0]["status"] == "issued"

    counts = store.counts()
    assert counts["invoices"] == 1
    assert counts["callbacks"] == 1, "同渠道同流水号只允许留痕一条"

    detail = client.get(f"{ORDERS_PATH}/{order['order_id']}", headers=_headers(settings)).json()
    assert detail["data"]["status"] == "paid"
    assert detail["data"]["invoice_status"] == "issued"
    assert detail["data"]["payment_no_masked"].startswith("****"), "流水号必须脱敏"


# ===========================================================================
# B09-⑥ 待支付订单存在时再下单 → 15004 且不产生第二单
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_6_pending_order_blocks_second_order_without_creating_one(
    client: TestClient, settings: Settings
) -> None:
    """连续两次下单：第二次 `15004`，订单总数仍为 1（数据库唯一索引兜底）。"""
    body = {
        "plan_code": PLAN_PROFESSIONAL,
        "seats": PRO_MIN_SEATS,
        "order_type": "new",
        "payment_method": "online",
    }
    first = client.post(
        ORDERS_PATH, headers={**_headers(settings, idem="w3-o1"), **_key("w3-o1")}, json=body
    )
    assert first.status_code == 200, first.text
    first_order = first.json()["data"]
    assert first_order["status"] == "pending_payment"
    assert first_order["pay_url"], "在线支付必须返回收银台地址"
    expires = datetime.fromisoformat(first_order["expires_at"].replace("Z", "+00:00"))
    assert abs(expires - datetime.now(UTC) - timedelta(minutes=30)) < timedelta(seconds=10)

    second = client.post(
        ORDERS_PATH, headers={**_headers(settings, idem="w3-o2"), **_key("w3-o2")}, json=body
    )
    assert second.status_code == 409, second.text
    second_body = second.json()
    assert second_body["code"] == int(ErrorCode.PENDING_ORDER_EXISTS)
    assert "请先完成支付" in second_body["message"]
    _assert_no_technical_leak(str(second_body), where="BL6 重复下单响应")

    store = get_billing_store(settings)
    counts = store.counts()
    assert counts["orders"] == 1, "不得产生第二个订单"
    listed = client.get(ORDERS_PATH, headers=_headers(settings)).json()["data"]
    assert listed["meta"]["total"] == 1
    assert listed["items"][0]["order_id"] == first_order["order_id"]


# ===========================================================================
# B09-⑦ 30 分钟未支付自动 closed
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b09_7_unpaid_order_closed_after_thirty_minutes(
    client: TestClient, settings: Settings
) -> None:
    """回填过期时间后：懒过期（读接口触发）与补偿任务（`close_expired_orders`）都关闭。"""
    body = {
        "plan_code": PLAN_PROFESSIONAL,
        "seats": PRO_MIN_SEATS,
        "order_type": "new",
        "payment_method": "online",
    }

    # 路径一：懒过期（读 BL8 详情时触发 `expire_orders`）
    lazy = client.post(
        ORDERS_PATH, headers={**_headers(settings, idem="w3-exp1"), **_key("w3-exp1")}, json=body
    )
    assert lazy.status_code == 200, lazy.text
    lazy_order = lazy.json()["data"]
    await get_billing_store(settings).update_order(
        TENANT,
        lazy_order["order_id"],
        expires_at=(datetime.now(UTC) - timedelta(minutes=31)).isoformat().replace("+00:00", "Z"),
    )
    lazy_detail = client.get(f"{ORDERS_PATH}/{lazy_order['order_id']}", headers=_headers(settings))
    assert lazy_detail.status_code == 200, lazy_detail.text
    assert lazy_detail.json()["data"]["status"] == "closed", "超过 30 分钟未支付必须关闭"

    # 关闭后可以重新下单（证明「待支付」闸门已释放，不是靠旗标绕开）
    again = client.post(
        ORDERS_PATH, headers={**_headers(settings, idem="w3-exp2"), **_key("w3-exp2")}, json=body
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["order_id"] != lazy_order["order_id"]

    # 路径二：补偿任务（跨企业关闭），供定时任务调用
    second_order = again.json()["data"]
    await get_billing_store(settings).update_order(
        TENANT,
        second_order["order_id"],
        expires_at=(datetime.now(UTC) - timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
    )
    closed_count = await OrderService(settings).close_expired_orders()
    assert closed_count >= 1, "补偿任务必须返回被关闭的订单数（可观测）"
    compensated = client.get(
        f"{ORDERS_PATH}/{second_order['order_id']}", headers=_headers(settings)
    )
    assert compensated.json()["data"]["status"] == "closed"

    # 已关闭订单再收到回调 → 15008（不得把关闭订单重新置为 paid）
    stale_callback = client.post(
        CALLBACK_PATH,
        json=_callback_payload(second_order["order_id"], second_order["amount_cents"], "w3txn0002"),
    )
    assert stale_callback.status_code == 409, stale_callback.text
    assert stale_callback.json()["code"] == int(ErrorCode.ORDER_ALREADY_PROCESSED)


# ===========================================================================
# 附加 1：支付回调是唯一免鉴权业务路径
# ===========================================================================
def test_extra_1_callback_is_the_only_auth_exempt_business_endpoint(
    client: TestClient, settings: Settings
) -> None:
    """BL1-BL9 与全部 `/ops/**` 无 token 一律 401（不得 200/500/403 混用）。"""
    probes: tuple[tuple[str, str], ...] = (
        ("GET", PLANS_PATH),
        ("GET", SUBSCRIPTION_PATH),
        ("POST", SUBSCRIPTION_PATH),
        ("PATCH", SEATS_PATH),
        ("GET", BL5_PATH),
        ("POST", ORDERS_PATH),
        ("GET", ORDERS_PATH),
        ("GET", f"{ORDERS_PATH}/ord_probe"),
        ("GET", INVOICES_PATH),
        ("GET", M4_PATH),
        *OPS_ENDPOINTS,
    )
    for method, path in probes:
        response = client.request(
            method, path, json={} if method in {"POST", "PATCH", "PUT"} else None
        )
        assert response.status_code == 401, f"{method} {path} 应 401，实际 {response.status_code}"
        assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)

    # 唯一例外：支付回调免 Bearer（无令牌可达业务逻辑层，返回验签结论而非 401）
    callback = client.post(CALLBACK_PATH, json={"out_trade_no": "ord_x"})
    assert callback.status_code != 401
    assert callback.json()["code"] == int(ErrorCode.PARAM_INVALID)
    _assert_no_technical_leak(str(callback.json()), where="BL10 无签名回调响应")

    # 断言设置侧没有被误改成「全局免鉴权」
    assert settings.auth_mode == "stub"


# ===========================================================================
# 附加 2：支付密钥缺失 → 服务可启动 + 业务错误（无堆栈外泄）
# ===========================================================================
def test_extra_2_pay_secret_missing_returns_business_error_not_stacktrace() -> None:
    """未配置商户号/APIv3 密钥：应用正常启动，下单返 `30004` 且响应无堆栈。"""
    reset_settings_cache()
    secretless = Settings(
        env="test",
        deploy_mode="private",
        # 关掉桩模式 → 不回落假网关，走真实微信支付实现（密钥为空）
        downstream_enabled=True,
        auth_mode="stub",
        jwt_secret="qa-w3-no-secret",
        ratelimit_enabled=False,
        pay_channel="wechat_pay",
        pay_mch_id="",
        pay_api_v3_key="",
    )
    assert secretless.use_fake_payment_gateway is False, "前置：必须走真实网关分支"

    secretless_app = create_app(secretless)
    with TestClient(secretless_app) as secretless_client:
        # 服务能启动并正常服务只读接口（密钥缺失不得阻断启动）
        alive = secretless_client.get(PLANS_PATH, headers=_headers(secretless))
        assert alive.status_code == 200, alive.text

        failed = secretless_client.post(
            ORDERS_PATH,
            headers={**_headers(secretless, idem="w3-nosecret"), **_key("w3-nosecret")},
            json={
                "plan_code": PLAN_PROFESSIONAL,
                "seats": PRO_MIN_SEATS,
                "order_type": "new",
                "payment_method": "online",
            },
        )
        assert failed.status_code == 500, failed.text
        body = failed.json()
        assert body["code"] == int(ErrorCode.CONFIG_ERROR)
        assert body["message"] == ERROR_REGISTRY[ErrorCode.CONFIG_ERROR].message
        assert body["data"] is None
        raw = failed.text
        _assert_no_technical_leak(raw, where="支付密钥缺失响应")
        assert 'File "' not in raw, "响应不得包含堆栈帧"
        assert "OA_PAY" not in raw, "响应不得回显配置键名"


# ===========================================================================
# 附加 3：M4 / BL5 同源（B07 回归不被 B09 破坏）
# ===========================================================================
@pytest.mark.asyncio
async def test_extra_3_m4_and_bl5_quota_stay_same_source(
    client: TestClient, settings: Settings
) -> None:
    """升级后 M4 `used_units` 仍严格等于 BL5 `quota_units_used`，且总额度同式。"""
    headers = _headers(settings)
    before_m4 = client.get(M4_PATH, params={"period": "month"}, headers=headers).json()["data"]
    before_bl5 = client.get(BL5_PATH, headers=headers).json()["data"]
    assert before_m4["quota"]["used_units"] == before_bl5["quota_units_used"]

    upgraded = client.post(
        SUBSCRIPTION_PATH,
        headers={**_headers(settings, idem="w3-same"), **_key("w3-same")},
        json={"plan_code": PLAN_PROFESSIONAL, "seats": 10, "action": "upgrade"},
    )
    assert upgraded.status_code == 200, upgraded.text

    usage = UsageService(settings)
    await usage.record_generation(
        tenant_id=TENANT,
        user_id="usr_w3_owner",
        generation_id="gen_w3_same_source",
        units=321,
    )

    after_m4 = client.get(M4_PATH, params={"period": "month"}, headers=headers).json()["data"]
    after_bl5 = client.get(BL5_PATH, headers=headers).json()["data"]
    assert after_m4["quota"]["used_units"] - after_bl5["quota_units_used"] == 0, (
        f"M4={after_m4['quota']['used_units']} BL5={after_bl5['quota_units_used']} 不同源"
    )
    assert after_m4["quota"]["used_units"] - before_m4["quota"]["used_units"] == 321
    assert after_bl5["quota_units_used"] - before_bl5["quota_units_used"] == 321
    assert after_bl5["quota_units_total"] - after_m4["quota"]["total_units"] == 0
    assert after_bl5["quota_units_total"] == 3_000_000 * 10


# ===========================================================================
# B10-① 企业 token 调任一 /ops/** 全 403 且不泄露客户数据
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b10_1_tenant_token_forbidden_on_all_ops_endpoints_without_leak(
    saas_client: TestClient, saas_settings: Settings
) -> None:
    """8 个 `/ops/**` 端点对 3 类企业角色全部 403（20003），响应体零客户数据。"""
    store = get_billing_store(saas_settings)
    secret_name = "星海绝密科技-W3-客户名"
    await store.ensure_tenant(TENANT_A, secret_name)
    order = await OrderService(saas_settings).create_internal(
        tenant_id=TENANT_A,
        actor_id="usr_alpha_owner",
        plan_code=PLAN_PROFESSIONAL,
        seats=PRO_MIN_SEATS,
        order_type="new",
        payment_method="online",
    )

    for roles in (("owner",), ("org_admin",), ("member",)):
        headers = _headers(saas_settings, roles=roles, tenant_id=TENANT_A)
        for method, path in OPS_ENDPOINTS:
            response = saas_client.request(
                method, path, headers=headers, json=OPS_BODIES.get(method)
            )
            where = f"{roles[0]} {method} {path}"
            assert response.status_code == 403, f"{where} 应 403，实际 {response.status_code}"
            body = response.json()
            assert body["code"] == int(ErrorCode.FORBIDDEN), where
            assert body["data"] is None, where
            assert body["message"] == "当前账号没有该操作的权限", where
            raw = response.text
            assert secret_name not in raw, f"{where} 泄露企业名"
            assert order.order_id not in raw, f"{where} 泄露订单号"
            assert TENANT_A not in raw, f"{where} 泄露企业 ID"
            assert "ord_" not in raw, f"{where} 泄露订单号前缀"
            _assert_no_technical_leak(raw, where=f"{where} 403 响应")


@pytest.mark.asyncio
async def test_b10_1b_forged_platform_header_cannot_open_ops(
    saas_client: TestClient, saas_settings: Settings
) -> None:
    """企业令牌伪造 `X-Tenant-Id: __platform__` 仍进不去（守卫看令牌声明 + 权限点）。"""
    token = make_token(
        settings=saas_settings, user_id="usr_forge", roles=["owner"], tenant_id=TENANT_A
    )
    response = saas_client.get(
        OPS_TENANTS, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": OPS_TENANT}
    )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)

    # 反过来：运营令牌但租户仍是企业 → 同样拒绝（租户层先于权限点层）
    ops_token = make_token(
        settings=saas_settings, user_id="usr_ops", roles=["ops_operator"], tenant_id=TENANT_A
    )
    reversed_probe = saas_client.get(
        OPS_TENANTS, headers={"Authorization": f"Bearer {ops_token}", "X-Tenant-Id": TENANT_A}
    )
    assert reversed_probe.status_code == 403


# ===========================================================================
# B10-② 核销：paid + 订阅生效 + 审计含运营人与备注
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b10_2_confirm_order_pays_activates_and_audits_operator_and_remark(
    saas_client: TestClient, saas_settings: Settings
) -> None:
    """对公转账核销：订单 paid、订阅 active、`audit_log` 含运营人与备注原文。"""
    await get_billing_store(saas_settings).ensure_tenant(TENANT_A, "W3 核销企业")
    created = saas_client.post(
        ORDERS_PATH,
        headers={**_headers(saas_settings, tenant_id=TENANT_A, idem="w3-ops-order")}
        | _key("w3-ops-order"),
        json={
            "plan_code": PLAN_PROFESSIONAL,
            "seats": PRO_MIN_SEATS,
            "order_type": "new",
            "payment_method": "offline_transfer",
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    assert order["status"] == "pending_payment"
    assert order["bank_info"], "对公转账必须返回收款信息"

    ops_headers = _ops_headers(saas_settings)
    remark = "W3 核销：对公到账已核对（流水 20261021）"
    confirmed = saas_client.post(
        f"{OPS_ORDERS}/{order['order_id']}/confirm",
        headers=ops_headers | _key("w3-confirm-ok"),
        json={"paid_amount_cents": order["amount_cents"], "remark": remark},
    )
    assert confirmed.status_code == 200, confirmed.text
    data = confirmed.json()["data"]
    assert data["status"] == "paid"
    assert data["subscription_status"] == "active"

    # 订阅真的生效了（不只是返回字段好看）
    subscription = await get_billing_store(saas_settings).get_active_subscription(TENANT_A)
    assert subscription is not None
    assert subscription.plan_code == PLAN_PROFESSIONAL
    assert subscription.status == "active"
    tenant = await get_billing_store(saas_settings).get_tenant(TENANT_A)
    assert tenant is not None
    assert tenant.plan_code == PLAN_PROFESSIONAL

    # 订单落库为 paid，且核销人与备注可追溯
    stored = await get_billing_store(saas_settings).get_order(TENANT_A, order["order_id"])
    assert stored is not None
    assert stored.status == "paid"
    assert stored.confirmed_by == "usr_w3_ops"
    assert stored.confirm_remark == remark

    # 审计：动作/运营人/被操作企业/备注
    entries = await AuditService(saas_settings).recent(TENANT_A, action=ACTION_ORDER_CONFIRMED)
    assert entries, "核销必须写 audit_log"
    summary = entries[0].change_summary
    assert entries[0].actor_id == "usr_w3_ops", "审计必须记录运营人"
    assert entries[0].tenant_id == TENANT_A, "审计的 tenant_id 应为被操作企业"
    assert summary.get("operator") == "usr_w3_ops"
    assert summary.get("remark") == remark
    assert summary.get("status") == "paid"
    assert str(summary.get("paid_amount_cents")) == str(order["amount_cents"])

    # 重复核销 → 15008（不得二次生效）
    repeated = saas_client.post(
        f"{OPS_ORDERS}/{order['order_id']}/confirm",
        headers=ops_headers | _key("w3-confirm-repeat"),
        json={"paid_amount_cents": order["amount_cents"], "remark": "重复点击"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == int(ErrorCode.ORDER_ALREADY_PROCESSED)

    # 金额不符 → 15009（另一个待支付订单验证）
    second = saas_client.post(
        ORDERS_PATH,
        headers={**_headers(saas_settings, tenant_id=TENANT_B, idem="w3-ops-order2")}
        | _key("w3-ops-order2"),
        json={
            "plan_code": PLAN_PROFESSIONAL,
            "seats": PRO_MIN_SEATS,
            "order_type": "new",
            "payment_method": "offline_transfer",
        },
    )
    assert second.status_code == 200, second.text
    second_order = second.json()["data"]
    mismatch = saas_client.post(
        f"{OPS_ORDERS}/{second_order['order_id']}/confirm",
        headers=ops_headers | _key("w3-confirm-mismatch"),
        json={"paid_amount_cents": second_order["amount_cents"] - 1, "remark": "少一分"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["code"] == int(ErrorCode.ORDER_AMOUNT_MISMATCH)
    assert mismatch.json()["message"] == "订单金额与应付金额不一致，请核对后重试"


# ===========================================================================
# B10-③ metrics?range=30d == BL5 企业维度求和（误差 0）
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b10_3_metrics_30d_equals_bl5_tenant_sum_exactly(
    saas_client: TestClient, saas_settings: Settings
) -> None:
    """三家企业各自造真实用量，`metrics.quota_units_total` 与 BL5 求和的差严格为 0。"""
    seed: dict[str, int] = {TENANT_A: 10, TENANT_B: 25, "tnt_w3_gamma": 7}
    usage_store = get_usage_store(saas_settings)
    billing = get_billing_store(saas_settings)
    for tenant_id, units in seed.items():
        await billing.ensure_tenant(tenant_id, f"{tenant_id} 企业")
        await usage_store.append(
            UsageEventRecord(
                tenant_id=tenant_id,
                user_id=f"usr_{tenant_id}",
                capability="chat",
                idem_key=f"evt_w3_{tenant_id}",
                units=units,
                event_id=f"use_w3_{tenant_id}",
                occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        )

    from_bl5 = 0
    per_tenant: dict[str, int] = {}
    for tenant_id in seed:
        response = saas_client.get(
            BL5_PATH,
            headers=_headers(saas_settings, tenant_id=tenant_id),
            params={"group_by": "capability"},
        )
        assert response.status_code == 200, response.text
        used = response.json()["data"]["quota_units_used"]
        per_tenant[tenant_id] = used
        from_bl5 += used

    assert per_tenant == seed, f"BL5 企业维度应逐家等于真实用量，实际 {per_tenant}"
    assert from_bl5 > 0, "前置：样本必须非零，否则 0 == 0 无法证明口径一致"

    metrics = saas_client.get(
        OPS_METRICS, headers=_ops_headers(saas_settings), params={"range": "30d"}
    )
    assert metrics.status_code == 200, metrics.text
    data = metrics.json()["data"]
    assert data["quota_units_total"] - from_bl5 == 0, (
        f"metrics={data['quota_units_total']} BL5={from_bl5} 误差不为 0"
    )
    assert data["range"] == "30d"
    assert data["tenant_count"] == len(seed)
    assert data["conversation_count"] >= len(seed)
    top = {item["tenant_id"]: item["quota_units_used"] for item in data["top_tenants"]}
    for tenant_id, units in seed.items():
        assert top[tenant_id] == units, "Top 榜的用量也必须与 BL5 同源"

    distribution = {item["plan_code"]: item["tenant_count"] for item in data["plan_distribution"]}
    assert distribution.get(PLAN_FREE) == len(seed)

    bad_range = saas_client.get(
        OPS_METRICS, headers=_ops_headers(saas_settings), params={"range": "90d"}
    )
    assert bad_range.status_code == 400
    assert bad_range.json()["code"] == int(ErrorCode.PARAM_INVALID)


# ===========================================================================
# B10-④ 运营响应无对话内容 / 文档原文 / 邮箱明文 / 凭据
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_b10_4_ops_responses_contain_no_content_email_or_credentials(
    saas_client: TestClient, saas_settings: Settings
) -> None:
    """先造**真实**内容型数据（对话键、文档名、库名、凭据密文），再全文扫描响应。"""
    conv_marker = "W3-CONV-MARKER-这是对话内容原文"
    doc_marker = "W3-DOC-MARKER-这是文档原文片段"
    kb_marker = "W3-KB-MARKER-绝密知识库"
    email_marker = "secret.person.w3@corp-example.com"
    credential_marker = "sk-w3-SECRET-CREDENTIAL-VALUE"

    billing = get_billing_store(saas_settings)
    await billing.ensure_tenant(TENANT_A, "W3 内容样本企业")

    # 1) 对话侧：用量事件的幂等键就是对话标识（真实落库字段）
    await get_usage_store(saas_settings).append(
        UsageEventRecord(
            tenant_id=TENANT_A,
            user_id="usr_content_owner",
            capability="chat",
            idem_key=conv_marker,
            units=13,
            event_id="use_w3_content",
            occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    )
    # 2) 文档侧：知识库名 + 文档名（真实业务表）
    await get_knowledge_base_store(saas_settings).create(
        KnowledgeBaseRecord(
            tenant_id=TENANT_A,
            base_id="kb_w3_marker",
            name=kb_marker,
            description=f"{doc_marker}（描述里也可能有原文）",
            owner_user_id="usr_content_owner",
        )
    )
    await get_document_store(saas_settings).create(
        DocumentRecord(
            tenant_id=TENANT_A,
            document_id="doc_w3_marker",
            base_id="kb_w3_marker",
            file_name=f"{doc_marker}.txt",
            file_size=2048,
            uploader_user_id="usr_content_owner",
            uploader_name=email_marker,
        )
    )
    # 3) 凭据侧：密文（运营接口永不读 credential_store）
    await get_credential_store(saas_settings).put(
        CredentialRecord(
            tenant_id=TENANT_A,
            credential_ref="cred_w3_marker",
            provider="knowledge_base",
            cipher_text=credential_marker.encode("utf-8"),
            nonce=b"0123456789ab",
            key_version=1,
        )
    )
    # 4) 成员侧：在册成员（成员邮箱只在身份底座，运营视图结构上无邮箱字段）
    await _link_members(saas_settings, TENANT_A, 2, tag="content")

    headers = _ops_headers(saas_settings)
    bodies: dict[str, str] = {}
    for label, method, path, params in (
        ("OPS1 企业列表", "GET", OPS_TENANTS, None),
        ("OPS2 企业详情", "GET", f"{OPS_TENANTS}/{TENANT_A}", None),
        ("OPS4 订单列表", "GET", OPS_ORDERS, None),
        ("OPS6 统计", "GET", OPS_METRICS, {"range": "7d"}),
        ("OPS7 配置", "GET", OPS_SETTINGS, None),
    ):
        response = saas_client.request(method, path, headers=headers, params=params)
        assert response.status_code == 200, f"{label}: {response.text}"
        bodies[label] = response.text

    markers = {
        "对话内容": conv_marker,
        "文档原文": doc_marker,
        "知识库名": kb_marker,
        "成员邮箱明文": email_marker,
        "凭据明文": credential_marker,
    }
    for label, raw in bodies.items():
        for kind, marker in markers.items():
            assert marker not in raw, f"{label} 泄露{kind}"
        assert not EMAIL_PATTERN.search(raw), f"{label} 含邮箱明文：{EMAIL_PATTERN.search(raw)}"
        for field in (
            "email",
            "member_email",
            "conversation_content",
            "message_content",
            "document_content",
            "segment_content",
            "cipher",
            "secret",
            "credential_ref",
            "password",
        ):
            assert field not in raw.lower(), f"{label} 含敏感字段名 {field}"
        _assert_no_technical_leak(raw, where=label)

    # 正例：聚合数字与基本信息仍可用（不然就是「全遮」而非「最小可见」）
    detail = saas_client.get(f"{OPS_TENANTS}/{TENANT_A}", headers=headers).json()["data"]
    assert detail["tenant"]["name"] == "W3 内容样本企业"
    assert detail["usage"]["quota_units_used"] == 13
    assert detail["usage"]["member_count"] == 2
    listed = saas_client.get(OPS_METRICS, headers=headers).json()["data"]
    assert listed["tenant_count"] >= 1
    assert isinstance(listed["quota_units_total"], int)


# ===========================================================================
# 附加 4：验签失败的回调「契约」——20001 + 订阅/发票/幂等键零变化 + 拒绝审计
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_extra_4_rejected_callback_must_not_consume_idempotency_key(
    client: TestClient, settings: Settings
) -> None:
    """脏回调（同流水号+错签名）不得污染幂等键，也不得让真通知静默丢失。

    该用例曾以 `xfail(strict=True)` 钉住缺陷 **QA-P2-W3-05（P0）**：修复前验签失败会先
    领取 `(channel, external_txn_id)` 幂等键，导致随后同流水号的真实通知被当作重复静默
    吞掉（应答 SUCCESS 但订单永远 `pending_payment`、无订阅无发票、渠道不再重试）。
    缺陷已在 `686b387` 修复（领取幂等键后移到全部校验通过之后，拒绝留痕改用
    `rejected:<digest>` 键），本用例随之转正常绿回归。原 `P0-07` / 裁定 #6 的**编号与
    表述已由主理人撤回**（验签失败本就返回 `20001`，不存在「改判」），但该修复对应的
    **行为**保留，故本用例保留为常绿 p0 回归，防止后续改动 reintroduce。
    """
    created = client.post(
        ORDERS_PATH,
        headers={**_headers(settings, idem="w3-reject-order"), **_key("w3-reject-order")},
        json={
            "plan_code": PLAN_PROFESSIONAL,
            "seats": PRO_MIN_SEATS,
            "order_type": "new",
            "payment_method": "online",
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    txn = "w3txn_reject_0001"
    good = _callback_payload(order["order_id"], order["amount_cents"], txn)

    # ① 伪造/脏数据：流水号与金额都对，签名错
    rejected = client.post(CALLBACK_PATH, json={**good, "sign": "deadbeef" * 8})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == int(ErrorCode.PARAM_INVALID), "验签失败必须是 20001"

    store = get_billing_store(settings)
    after_reject = await store.get_active_subscription(TENANT)
    assert after_reject is None, "拒绝路径不得开通订阅"
    assert store.counts()["invoices"] == 0, "拒绝路径不得开票"

    # ② 渠道方随后发来「同一流水号 + 正确签名」的真通知：必须正常开通
    accepted = client.post(CALLBACK_PATH, json=good)
    assert accepted.status_code == 200, accepted.text
    after_accept = await store.get_active_subscription(TENANT)
    assert after_accept is not None, (
        "脏回调占用了幂等键：真通知被当作重复吞掉（应答 SUCCESS 但未开通）"
    )
    assert store.counts()["invoices"] == 1

    detail = client.get(f"{ORDERS_PATH}/{order['order_id']}", headers=_headers(settings)).json()
    assert detail["data"]["status"] == "paid"

    # ③ 幂等不能被「修好」削弱：真通知重发 2 次仍只生效一次、只开一张票
    for _ in range(2):
        replay = client.post(CALLBACK_PATH, json=good)
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["code"] == "SUCCESS"
    assert store.counts()["invoices"] == 1, "重复通知不得重复开票"
    assert store.counts()["callbacks"] == 2, "只应有 1 条拒绝留痕 + 1 条成功留痕"
    still = await store.get_active_subscription(TENANT)
    assert still is not None
    assert (still.subscription_id, still.started_at) == (
        after_accept.subscription_id,
        after_accept.started_at,
    ), "重复通知不得重复开通"

    # ④ 拒绝必须留痕（TC-P2-BL04-04）：payment_callback_log 有 1 条 signature_ok=False
    rejected_rows = [
        row for row in getattr(store, "_callbacks", {}).values() if not row.signature_ok
    ]
    assert len(rejected_rows) == 1, "验签失败必须留 1 条拒绝痕迹（signature_ok=False）"
    assert rejected_rows[0].processed is False, "拒绝痕迹不得标记为已处理"

    # ⑤ 拒绝事件进审计（加固后行为，保留）
    entries = await AuditService(settings).recent(TENANT, limit=50)
    platform_entries = await AuditService(settings).recent("__platform__", limit=50)
    actions = {item.action for item in [*entries, *platform_entries]}
    assert actions & {"payment_callback_rejected", "billing.payment_callback_rejected"}, (
        f"验签失败必须有拒绝审计，实际审计动作={sorted(actions)}"
    )
