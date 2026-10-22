"""插件生命周期与评价端点测试（阶段三 B18，`architecture-phase3.md` §5.2 PL6-PL12）。

端到端只跑"HTTP 面"能证明的部分：状态码、幂等键、错误码、以及**卸载后列表为空**这种
跨端点一致性。安装本身在 `test_plugin_install.py` 里用服务层构造（避免为了造数据
把升级套餐的支付链路也搬进这个文件）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.business.plugin_grant_service import get_plugin_grant_service
from app.business.plugin_service import PluginService
from app.business.subscription_service import SubscriptionService
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.core.security import AuthContext
from app.db.repositories.billing_repo import get_billing_store
from app.db.repositories.plugin_repo import get_plugin_store
from app.plugins.actions import ORG_CAPABILITIES
from app.plugins.loader import get_registry
from app.schemas.billing import ChangeSubscriptionRequest
from app.schemas.plugin import InstallRequest
from tests.conftest import make_token

pytestmark = pytest.mark.integration

TENANT = "default"
PLUGIN = "plg_mail_draft"
API = "/api/v1/plugins"


def _key(tag: str) -> str:
    """由标签派生稳定 UUIDv4 幂等键。

    Args:
        tag: 标签。

    Returns:
        str: 幂等键。
    """
    import hashlib

    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"


def _headers(settings: Settings) -> dict[str, str]:
    """构造鉴权请求头（owner，可写）。

    Args:
        settings: 配置实例。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id="usr_owner", tenant_id=TENANT, roles=["owner"])
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


async def _install(settings: Settings) -> None:
    """把租户升到专业版并安装目标插件（服务层直调，复用同一批单例）。

    Args:
        settings: 配置实例。
    """
    billing = get_billing_store(settings)
    await SubscriptionService(settings, billing).change(
        tenant_id=TENANT,
        actor_id="usr_owner",
        request=ChangeSubscriptionRequest(plan_code="professional", seats=5, action="upgrade"),
    )
    store = get_plugin_store(settings)
    await store.save_policies(TENANT, dict.fromkeys(ORG_CAPABILITIES, True), "usr_owner")
    service = PluginService(
        settings, store=store, registry=get_registry(), grants=get_plugin_grant_service(settings)
    )
    manifest = get_registry().get(PLUGIN)
    assert manifest is not None
    await service.install(
        plugin_id=PLUGIN,
        request=InstallRequest(grants=dict.fromkeys(manifest.capability_keys, True)),
        auth=AuthContext(user_id="usr_owner", tenant_id=TENANT, roles=["owner"]),
    )


@pytest.mark.asyncio
@pytest.mark.p0
async def test_state_uninstall_and_review_flow(client: TestClient, settings: Settings) -> None:
    """停用 → 启用 → 提交评价 → 列表（匿名）→ 卸载 → 已添加列表为空。"""
    await _install(settings)
    headers = _headers(settings)

    disabled = client.patch(
        f"{API}/{PLUGIN}/state",
        headers={**headers, "Idempotency-Key": _key("state-1")},
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["status"] == "disabled"

    enabled = client.patch(
        f"{API}/{PLUGIN}/state",
        headers={**headers, "Idempotency-Key": _key("state-2")},
        json={"status": "enabled"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["data"]["status"] == "enabled"

    review = client.post(
        f"{API}/{PLUGIN}/reviews",
        headers={**headers, "Idempotency-Key": _key("review-1")},
        json={"rating": 5, "content": "很省事", "anonymous": True},
    )
    assert review.status_code == 200, review.text
    assert review.json()["data"]["rating_avg"] == 5.0

    listing = client.get(f"{API}/{PLUGIN}/reviews", headers=headers)
    assert listing.status_code == 200
    item = listing.json()["data"]["items"][0]
    assert item["author_name"] == "某位同事", "匿名评价不得暴露身份"
    assert item["department_name"] == ""

    removed = client.delete(
        f"{API}/{PLUGIN}", headers={**headers, "Idempotency-Key": _key("uninstall-1")}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["data"]["uninstalled"] is True

    installed = client.get(f"{API}/installed", headers=headers).json()["data"]
    assert installed["items"] == [], "卸载后已添加列表应为空"


@pytest.mark.asyncio
async def test_lifecycle_on_missing_installation_returns_16003(
    client: TestClient, settings: Settings
) -> None:
    """未添加时通过 HTTP 调 PL6/PL7/PL8/PL9 → `16003`。"""
    headers = _headers(settings)
    cases = [
        client.patch(
            f"{API}/{PLUGIN}/state",
            headers={**headers, "Idempotency-Key": _key("st-x")},
            json={"status": "disabled"},
        ),
        client.delete(f"{API}/{PLUGIN}", headers={**headers, "Idempotency-Key": _key("u-x")}),
        client.post(
            f"{API}/{PLUGIN}/upgrade",
            headers={**headers, "Idempotency-Key": _key("up-x")},
            json={"target_version": "1.0.0"},
        ),
        client.post(
            f"{API}/{PLUGIN}/rollback",
            headers={**headers, "Idempotency-Key": _key("rb-x")},
        ),
    ]
    for response in cases:
        assert response.status_code == 400, response.text
        assert response.json()["code"] == int(ErrorCode.PLUGIN_NOT_INSTALLED)


@pytest.mark.asyncio
async def test_settings_save_and_reject_unknown_key(client: TestClient, settings: Settings) -> None:
    """PL10：合法键保存成功；未声明键 → `20001`。"""
    await _install(settings)
    headers = {**_headers(settings), "Idempotency-Key": _key("settings-1")}

    ok = client.put(
        f"{API}/{PLUGIN}/settings", headers=headers, json={"settings": {"signature": "研发部"}}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["settings"] == {"signature": "研发部"}

    bad = client.put(
        f"{API}/{PLUGIN}/settings",
        headers={**_headers(settings), "Idempotency-Key": _key("settings-2")},
        json={"settings": {"unknown": "x"}},
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == int(ErrorCode.PARAM_INVALID)


@pytest.mark.asyncio
async def test_review_without_installation_returns_16011(
    client: TestClient, settings: Settings
) -> None:
    """未添加即评价 → `16011`（HTTP 面复核）。"""
    response = client.post(
        f"{API}/{PLUGIN}/reviews",
        headers={**_headers(settings), "Idempotency-Key": _key("review-2")},
        json={"rating": 4, "content": "还没用过", "anonymous": False},
    )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.PLUGIN_REVIEW_NOT_ALLOWED)
