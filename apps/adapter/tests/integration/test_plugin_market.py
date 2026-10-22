"""插件市场端点测试（阶段三 B17，`architecture-phase3.md` §5.2 PL1-PL4 / §5.5）。

**本文件盯的是 PRD v1.1 的两条勘误**（QA 会用反硬编码断言复核）：

1. **勘误 #1**：免费版市场**照常返回 6 个插件**，只是每个都标 `installable=false`
   并给出原因；`15001` 只在安装时抛。整页降级成"升级提示"是不可接受的。
2. **勘误 #2**：企业能力开关**逐能力**由端点返回（`org_switch_enabled`），
   前端不得硬编码 `true` 或按套餐推导。

另覆盖：分类计数固定顺序（不随筛选抖动）、分页两页无交集、详情字段齐备、
未登录 401、不存在的插件 16002（在服务层覆盖，避免直接动内存私有结构）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.business.plugin_service import PluginService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式的固定租户
TENANT = "default"

MARKET_PATH = "/api/v1/plugins/market"

#: 内置 6 个插件
EXPECTED_IDS = {
    "plg_dev_workbench",
    "plg_content_ops",
    "plg_hr_admin",
    "plg_weekly_report",
    "plg_meeting_minutes",
    "plg_mail_draft",
}


def _headers(
    settings: Settings, *, roles: list[str] | None = None, user_id: str = "usr_owner"
) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 角色列表。
        user_id: 用户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings, user_id=user_id, tenant_id=TENANT, roles=roles or ["member"]
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


def test_market_requires_login(client: TestClient) -> None:
    """未登录访问市场 → 401（市场虽公开可看，但仍需身份确定企业）。"""
    response = client.get(MARKET_PATH)
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


@pytest.mark.p0
def test_free_plan_still_sees_all_plugins(client: TestClient, settings: Settings) -> None:
    """**勘误 #1**：免费版市场照常返回 6 个插件，但每个都不可安装且有原因。"""
    response = client.get(MARKET_PATH, headers=_headers(settings))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    items = data["items"]
    assert {item["plugin_id"] for item in items} == EXPECTED_IDS, "免费版必须看到全部 6 个插件"
    assert data["total"] == 6
    for item in items:
        assert item["installable"] is False, f"{item['plugin_id']} 免费版不应可安装"
        assert item["install_block_reason"], "不可安装必须给出原因（业务语言）"
        assert "token" not in item["install_block_reason"].lower()


def test_categories_are_stable_and_complete(client: TestClient, settings: Settings) -> None:
    """分类计数固定 6 项且顺序稳定；切分类后计数不随筛选抖动。"""
    base = client.get(MARKET_PATH, headers=_headers(settings)).json()["data"]
    keys = [item["key"] for item in base["categories"]]
    assert keys == ["dev", "content", "hr", "collab", "data", "other"]
    counts = {item["key"]: item["count"] for item in base["categories"]}
    assert counts["dev"] == 1
    assert counts["content"] == 1
    assert counts["hr"] == 1
    assert counts["collab"] == 3

    filtered = client.get(
        MARKET_PATH, params={"category": "collab"}, headers=_headers(settings)
    ).json()["data"]
    assert filtered["total"] == 3
    filtered_counts = {item["key"]: item["count"] for item in filtered["categories"]}
    assert filtered_counts == counts, "分类计数基于全量目录，不随筛选变化"


def test_pagination_pages_do_not_overlap(client: TestClient, settings: Settings) -> None:
    """分页第 1、2 页的 `plugin_id` 无交集（B17 验收项）。"""
    first = client.get(
        MARKET_PATH, params={"page": 1, "page_size": 4}, headers=_headers(settings)
    ).json()["data"]
    second = client.get(
        MARKET_PATH, params={"page": 2, "page_size": 4}, headers=_headers(settings)
    ).json()["data"]
    first_ids = {item["plugin_id"] for item in first["items"]}
    second_ids = {item["plugin_id"] for item in second["items"]}
    assert len(first_ids) == 4
    assert len(second_ids) == 2
    assert first_ids & second_ids == set()


def test_keyword_filter_hits_name_and_summary(client: TestClient, settings: Settings) -> None:
    """关键字同时匹配名称与说明。"""
    by_name = client.get(
        MARKET_PATH, params={"keyword": "周报"}, headers=_headers(settings)
    ).json()["data"]
    assert [item["plugin_id"] for item in by_name["items"]] == ["plg_weekly_report"]

    miss = client.get(
        MARKET_PATH, params={"keyword": "不存在的插件"}, headers=_headers(settings)
    ).json()["data"]
    assert miss["items"] == []
    assert miss["total"] == 0


def test_sort_by_rating_desc(client: TestClient, settings: Settings) -> None:
    """按评分排序：`rating_avg` 单调不增。"""
    data = client.get(MARKET_PATH, params={"sort": "rating"}, headers=_headers(settings)).json()[
        "data"
    ]
    ratings = [item["rating_avg"] for item in data["items"]]
    assert ratings == sorted(ratings, reverse=True)


@pytest.mark.p0
def test_detail_exposes_declaration(client: TestClient, settings: Settings) -> None:
    """详情返回声明的三要素、6 个步骤名称与版本历史（前端据此渲染，不硬编码）。"""
    response = client.get(f"{MARKET_PATH}/plg_dev_workbench", headers=_headers(settings))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["name"] == "前端研发工作台"
    assert len(data["description"]) >= 120
    assert len(data["step_titles"]) == 6
    assert len(data["capabilities"]) == 3
    for capability in data["capabilities"]:
        assert capability["display_name"] and capability["reason"] and capability["denial_effect"]
        assert "token" not in str(capability).lower(), "能力文案不得出现技术词"
    assert data["versions"][0]["version"] == "1.0.0"
    assert len(data["outputs"]) >= 4


@pytest.mark.p0
def test_capabilities_endpoint_returns_org_switch_per_capability(
    client: TestClient, settings: Settings
) -> None:
    """**勘误 #2**：`org_switch_enabled` 逐能力返回；个人能力恒 True、企业能力默认 False。"""
    response = client.get(
        "/api/v1/plugins/plg_content_ops/capabilities", headers=_headers(settings)
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    switches = {item["capability_key"]: item["org_switch_enabled"] for item in data["items"]}
    assert switches["personal.knowledge.read"] is True
    assert switches["personal.tool.use"] is True
    assert switches["org.external.publish"] is False, "企业能力默认关闭（Q3-2）"
    assert data["granted_capabilities"] == []
    assert data["installable"] is False, "免费版不可安装（但能看到清单）"


@pytest.mark.asyncio
async def test_unknown_plugin_returns_16002(settings: Settings) -> None:
    """不存在的插件 → `16002`（服务层断言，避免为造数据去动内存私有结构）。"""
    service = PluginService(settings)
    auth = AuthContext(user_id="usr_owner", tenant_id=TENANT, roles=["owner"])
    with pytest.raises(ApiError) as excinfo:
        await service.detail(plugin_id="plg_not_exists", auth=auth)
    assert excinfo.value.code is ErrorCode.PLUGIN_NOT_FOUND
    assert excinfo.value.message == "该插件已下架，去市场看看其他插件"


def test_installed_list_starts_empty(client: TestClient, settings: Settings) -> None:
    """未添加任何插件时已安装列表为空（不是报错）。"""
    response = client.get("/api/v1/plugins/installed", headers=_headers(settings))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0
