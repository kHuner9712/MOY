"""研发工作台 e2e：**表单校验全部来自声明**（P0-P3-15 / QA TC-P3-DEV15-01）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
| `test_required_fields` | TC-P3-DEV15-01 | 缺标题 / 缺描述 / 缺仓库 → 被拒且**精确指出缺哪一项** |

**为什么值得一条 e2e**：工作台的表单不许写死字段（G-27：一个宿主渲染六个插件）。
字段从声明来，校验也必须从声明来——只在前端置灰不够，绕过界面直接提交时
后端仍要按**同一条声明**拒绝并说出缺的是哪一项，否则"置灰"只是装饰。
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    DEV_PLUGIN,
    ORG_CAPABILITIES,
    install_plugin,
    set_org_switches,
    upgrade_to_paid_plan,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pg]

#: 完整输入（逐条删掉一项来做负样本）
FULL_INPUTS: dict[str, Any] = {
    "title": "列表页加导出按钮",
    "requirement": "在客户列表页工具栏加一个导出按钮。",
    "repo": "acme/web",
    "branch_policy": "新建分支",
}

#: 声明里的必填输入 → 用户可见标签
REQUIRED: dict[str, str] = {
    "title": "需求标题",
    "requirement": "需求描述",
    "repo": "目标仓库",
    "branch_policy": "分支策略",
}


@pytest.fixture
async def dev_ready(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
) -> None:
    """装好研发工作台。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
    """
    await upgrade_to_paid_plan(e2e_settings, tenant)
    await set_org_switches(client, admin_headers, enabled=True, capabilities=ORG_CAPABILITIES)
    await install_plugin(client, admin_headers, DEV_PLUGIN)


@pytest.mark.p0
@pytest.mark.asyncio
async def test_required_fields(
    client: AsyncClient, admin_headers: dict[str, str], dev_ready: None
) -> None:
    """TC-P3-DEV15-01：缺任一项必填 → 400/403 + `20001` + **文案点名缺的那一项**。

    判定点（三条）：
    ① 四项必填逐个删掉都会被拒（不是只挡第一项）；
    ② 拒绝文案里出现**该字段的标签**（"请填写「需求标题」"），而不是笼统的"参数错误"；
    ③ 四项齐全时能正常建任务（证明拒绝是"因缺项"而不是"这条路本身不通"）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        dev_ready: 前置装配。
    """
    for key, label in REQUIRED.items():
        payload = {name: value for name, value in FULL_INPUTS.items() if name != key}
        response = await client.post(
            "/api/v1/agent/tasks",
            headers={**admin_headers, "Idempotency-Key": f"e2e-missing-{key}"},
            json={
                "plugin_id": DEV_PLUGIN,
                "conversation_id": "conv_e2e_form",
                "goal": "",
                "inputs": payload,
            },
        )
        assert response.status_code in {400, 403}, f"① 缺 {key} 时应被拒：{response.text}"
        body = response.json()
        assert body["code"] == 20001, f"① 缺 {key} 时应为 20001，实际 {body}"
        assert label in body["message"], (
            f"② 文案必须点名缺的那一项（{label}），实际：{body['message']}"
        )

    ok = await client.post(
        "/api/v1/agent/tasks",
        headers={**admin_headers, "Idempotency-Key": "e2e-form-complete"},
        json={
            "plugin_id": DEV_PLUGIN,
            "conversation_id": "conv_e2e_form",
            "goal": "",
            "inputs": FULL_INPUTS,
        },
    )
    assert ok.status_code == 200, f"③ 齐全时不应被拒：{ok.text}"
    assert ok.json()["code"] == 0
    assert ok.json()["data"]["task_id"]
