"""目标式能力推荐端点集成测试（W1 切片 B，`architecture-w1-metrics-entry.md` §3.2）。

12 条用例覆盖 AC-B1~B5 / B7：

| # | 用例 | 验收 |
| --- | --- | --- |
| 1 | 六类目标各命中正确插件（参数化） | AC-B1 推荐正确性 |
| 2 | 易混词不串（「写周报」不串到邮件起草） | AC-B1 |
| 3 | 多字领域词靠关键词表命中（「代码仓库」→ 工具卡） | AC-B1 |
| 4 | 空目标返回目录不报错 | AC-B4 |
| 5 | `goal` 超 4000 字 → 20001 | AC-B5 |
| 6 | `limit=0` / `limit=11` → 20001 | AC-B5 |
| 7 | 无命中 → 兜底且 items 非空且 hint 无占位词 | AC-B3 |
| 8 | 每卡 matched_terms / matched_fields 非空 | AC-B2 |
| 9 | 周报卡 inputs 含「汇报周期 / 收件人」 | AC-B7 |
| 10 | `total_catalog=11` 且 `limit` 缺省 3 | AC-B1 |
| 11 | 工具卡 `kind=tool`、`inputs=[]`、落点诚实 | AC-B1/B7 |
| 12 | 未登录 401 | 既有机制 |
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ErrorCode
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式的生效租户（`resolve_tenant_id` 在 private 模式恒返回配置默认值）
TENANT = "default"

#: 被测端点
RECOMMEND_PATH = "/api/v1/capabilities/recommend"

#: 目录规模（6 插件 + 5 工具，实时组装）
TOTAL_CATALOG = 11

#: 六类目标 → 期望命中的插件（AC-B1：各类目标命中正确插件）
GOAL_TO_PLUGIN = {
    "整理本周周报发给主管": "plg_weekly_report",
    "起草一封邮件给客户": "plg_mail_draft",
    "整理会议纪要": "plg_meeting_minutes",
    "帮我提交代码到仓库": "plg_dev_workbench",
    "安排下周的内容发布排期": "plg_content_ops",
    "写一个招聘说明": "plg_hr_admin",
}

#: 兜底文案禁词（占位词表，设计 §8 第 3 条）
BANNED_PLACEHOLDERS = (
    "暂不支持",
    "暂未支持",
    "尚未支持",
    "敬请期待",
    "稍后开放",
    "即将上线",
    "即将开放",
    "正在开发",
    "开发中",
    "后续版本提供",
)


def _headers(settings, *, user_id: str = "usr_recommend") -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例（取签发密钥）。
        user_id: 用户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, tenant_id=TENANT, roles=["member"])
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


# ===========================================================================
# 1-2. AC-B1：推荐正确性与易混词
# ===========================================================================
@pytest.mark.parametrize(("goal", "expected"), sorted(GOAL_TO_PLUGIN.items()))
def test_each_goal_category_hits_the_right_plugin(
    client: TestClient, settings, goal: str, expected: str
) -> None:
    """AC-B1：六类目标各命中正确插件（且是 Top1）。"""
    response = client.get(
        RECOMMEND_PATH, headers=_headers(settings), params={"goal": goal, "limit": 3}
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0
    data = response.json()["data"]
    assert data["matched"] is True
    assert data["items"], "命中时不得空列表"
    assert data["items"][0]["id"] == expected, (
        f"目标「{goal}」应命中 {expected}，实际 {data['items'][0]['id']}"
    )


def test_confusable_words_do_not_cross_plugins(client: TestClient, settings) -> None:
    """AC-B1 易混词不串：「写周报」必须命中周报生成，不串到邮件起草。"""
    response = client.get(
        RECOMMEND_PATH, headers=_headers(settings), params={"goal": "写周报", "limit": 3}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    top = data["items"][0]
    assert top["id"] == "plg_weekly_report"
    assert top["id"] != "plg_mail_draft"
    assert "plg_mail_draft" not in [item["id"] for item in data["items"]]


def test_multi_char_domain_term_matches_tool_card(client: TestClient, settings) -> None:
    """「代码仓库」是 4 字词（不是任何 2-gram），靠关键词表命中工具卡。"""
    response = client.get(
        RECOMMEND_PATH,
        headers=_headers(settings),
        params={"goal": "把代码仓库里的议题处理一下", "limit": 3},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matched"] is True
    assert data["items"][0]["id"] == "tool:github"
    assert data["items"][0]["kind"] == "tool"


# ===========================================================================
# 3. AC-B4：空目标
# ===========================================================================
def test_empty_goal_returns_catalog_without_error(client: TestClient, settings) -> None:
    """AC-B4：空目标 → 返回目录前 limit 条，不算兜底，不报错。"""
    response = client.get(RECOMMEND_PATH, headers=_headers(settings), params={"goal": ""})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matched"] is False
    assert data["fallback"] is False
    assert data["fallback_hint"] == ""
    assert data["goal"] == ""
    assert len(data["items"]) == 3  # limit 缺省 3
    assert data["total_catalog"] == TOTAL_CATALOG


def test_whitespace_only_goal_returns_catalog(client: TestClient, settings) -> None:
    """纯空白目标同样返回目录（去空白后为空即空目标）。"""
    response = client.get(RECOMMEND_PATH, headers=_headers(settings), params={"goal": "   "})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matched"] is False
    assert data["fallback"] is False
    assert len(data["items"]) == 3


# ===========================================================================
# 4. AC-B5：参数边界
# ===========================================================================
def test_goal_over_4000_chars_returns_20001(client: TestClient, settings) -> None:
    """AC-B5：`goal` 超 4000 字 → 20001（HTTP 400，与 A1 上限一致）。"""
    response = client.get(RECOMMEND_PATH, headers=_headers(settings), params={"goal": "整" * 4001})
    assert response.status_code == 400
    assert response.json()["code"] == int(ErrorCode.PARAM_INVALID)
    assert response.json()["data"] is None


@pytest.mark.parametrize("limit", [0, 11, -1])
def test_limit_out_of_range_returns_20001(client: TestClient, settings, limit: int) -> None:
    """AC-B5：`limit` 越界（<1 或 >10）→ 20001。"""
    response = client.get(
        RECOMMEND_PATH,
        headers=_headers(settings),
        params={"goal": "整理本周周报", "limit": limit},
    )
    assert response.status_code == 400
    assert response.json()["code"] == int(ErrorCode.PARAM_INVALID)


def test_goal_at_max_length_is_accepted(client: TestClient, settings) -> None:
    """边界值放行：`goal` 恰好 4000 字不报错（走无命中兜底）。"""
    response = client.get(RECOMMEND_PATH, headers=_headers(settings), params={"goal": "整" * 4000})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["fallback"] is True
    assert data["items"], "兜底不得空列表"


# ===========================================================================
# 5. AC-B3：无命中兜底
# ===========================================================================
def test_no_hit_falls_back_with_guidance(client: TestClient, settings) -> None:
    """AC-B3：无命中 → fallback=true，items 非空，hint 给引导且无占位词。"""
    response = client.get(
        RECOMMEND_PATH,
        headers=_headers(settings),
        params={"goal": "量子引力波谐振器", "limit": 3},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matched"] is False
    assert data["fallback"] is True
    assert data["items"], "无命中不得返回空列表"
    assert len(data["items"]) == 3
    for item in data["items"]:
        assert item["score"] == 0
        assert item["matched_terms"] == []
        assert item["matched_fields"] == []
    hint = data["fallback_hint"]
    assert hint, "兜底必须给引导文案"
    for banned in BANNED_PLACEHOLDERS:
        assert banned not in hint, f"兜底文案出现占位词「{banned}」"


# ===========================================================================
# 6-7. AC-B2 / AC-B7：理由回填与所需输入
# ===========================================================================
def test_every_hit_card_carries_reasons(client: TestClient, settings) -> None:
    """AC-B2：每张入选卡都有命中词与命中字段（禁空理由）。"""
    response = client.get(
        RECOMMEND_PATH,
        headers=_headers(settings),
        params={"goal": "整理本周周报发给主管", "limit": 3},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matched"] is True
    for item in data["items"]:
        assert item["matched_terms"], f"{item['id']} 缺命中词（禁空理由）"
        assert item["matched_fields"], f"{item['id']} 缺命中字段（禁空理由）"
        assert item["score"] >= 1
        # 命中字段只允许契约字段名
        assert set(item["matched_fields"]) <= {
            "name",
            "summary",
            "category",
            "inputs.label",
            "operations",
        }


def test_weekly_report_card_lists_required_inputs(client: TestClient, settings) -> None:
    """AC-B7：周报卡的所需输入含「汇报周期 / 收件人」（D-17 所需输入提示）。"""
    response = client.get(
        RECOMMEND_PATH,
        headers=_headers(settings),
        params={"goal": "整理本周周报发给主管", "limit": 3},
    )
    assert response.status_code == 200, response.text
    top = response.json()["data"]["items"][0]
    assert top["id"] == "plg_weekly_report"
    labels = {item["label"] for item in top["inputs"]}
    assert "汇报周期" in labels
    assert "收件人" in labels
    required = {item["label"] for item in top["inputs"] if item["required"]}
    assert "汇报周期" in required and "收件人" in required
    period = next(item for item in top["inputs"] if item["label"] == "汇报周期")
    assert period["key"] == "period" and period["type"] == "text"


# ===========================================================================
# 8. 目录规模与工具卡形态
# ===========================================================================
def test_total_catalog_is_eleven(client: TestClient, settings) -> None:
    """目录 = 6 插件 + 5 工具 = 11 条（实时组装，不落库）。"""
    response = client.get(
        RECOMMEND_PATH, headers=_headers(settings), params={"goal": "整理本周周报"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["total_catalog"] == TOTAL_CATALOG


def test_tool_card_has_no_inputs_and_is_honest(client: TestClient, settings) -> None:
    """工具卡 `kind=tool`、`id=tool:{type}`、`inputs=[]`（无所需输入概念）。"""
    response = client.get(
        RECOMMEND_PATH,
        headers=_headers(settings),
        params={"goal": "用企业微信发消息", "limit": 5},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    tool_cards = [item for item in data["items"] if item["kind"] == "tool"]
    assert tool_cards, "企业微信目标应出现工具卡"
    wecom = next(item for item in tool_cards if item["id"] == "tool:wecom")
    assert wecom["inputs"] == []
    assert wecom["summary"], "工具摘要为操作展示名拼接，不得为空"


# ===========================================================================
# 9. 鉴权
# ===========================================================================
def test_recommend_requires_login(client: TestClient) -> None:
    """未登录 → 401（既有机制）。"""
    response = client.get(RECOMMEND_PATH, params={"goal": "整理本周周报"})
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)
