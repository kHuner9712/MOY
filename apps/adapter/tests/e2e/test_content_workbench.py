"""内容运营 e2e：**平台选择校验来自声明**（P0-P3-18 / QA TC-P3-CNT18-01）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
| `test_platform_required` | TC-P3-CNT18-01 | 未选平台 → 被拒 + 「请填写「目标平台」」；
选了声明外的平台 → 取值不在可选范围 |

**为什么值得一条 e2e**：`channels` 是 `multi_select`，其候选值写在声明里。
只在前端拦"至少选一个"是不够的——绕过界面提交时后端必须按同一条声明拒绝，
并且**不接受声明外的平台**（否则"选了抖音"却跑出一个声明里没有的平台，
执行路径就超出契约了）。
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    CONTENT_PLUGIN,
    ORG_CAPABILITIES,
    build_content_script,
    install_plugin,
    set_org_switches,
    upgrade_to_paid_plan,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pg]

#: 完整输入
FULL_INPUTS: dict[str, Any] = {
    "topic": "季度新品上线",
    "channels": ["微信公众号", "小红书"],
    "window": "本周内",
    "source": "知识库",
}


@pytest.fixture
async def content_ready(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
) -> None:
    """装好内容运营并切到内容语料。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
    """
    use_script(build_content_script())
    await upgrade_to_paid_plan(e2e_settings, tenant)
    await set_org_switches(client, admin_headers, enabled=True, capabilities=ORG_CAPABILITIES)
    await install_plugin(client, admin_headers, CONTENT_PLUGIN)


@pytest.mark.p0
@pytest.mark.asyncio
async def test_platform_required(
    client: AsyncClient, admin_headers: dict[str, str], content_ready: None
) -> None:
    """TC-P3-CNT18-01：未选平台被拒并点名；声明外的平台不被接受；选了对的能跑。

    判定点（三条）：
    ① `channels=[]` → 400/403 + `20001` + 文案含「目标平台」；
    ② `channels` 里混入声明外的平台 → `20001`（"取值不在可选范围内"），
       不得"悄悄忽略多出来的那个"；
    ③ 合规取值能建任务（证明拒绝是"因取值"而不是"这条路本身不通"）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        content_ready: 前置装配。
    """
    empty = await client.post(
        "/api/v1/agent/tasks",
        headers={**admin_headers, "Idempotency-Key": "e2e-no-platform"},
        json={
            "plugin_id": CONTENT_PLUGIN,
            "conversation_id": "conv_e2e_content",
            "goal": "",
            "inputs": {**FULL_INPUTS, "channels": []},
        },
    )
    assert empty.status_code in {400, 403}, f"① 未选平台时必须被拒：{empty.text}"
    body = empty.json()
    assert body["code"] == 20001, f"① 应为 20001，实际 {body}"
    assert "目标平台" in body["message"], f"① 文案必须点名「目标平台」：{body['message']}"

    unknown = await client.post(
        "/api/v1/agent/tasks",
        headers={**admin_headers, "Idempotency-Key": "e2e-unknown-platform"},
        json={
            "plugin_id": CONTENT_PLUGIN,
            "conversation_id": "conv_e2e_content",
            "goal": "",
            "inputs": {**FULL_INPUTS, "channels": ["微信公众号", "某不存在的平台"]},
        },
    )
    assert unknown.status_code in {400, 403}, f"② 声明外的平台必须被拒：{unknown.text}"
    assert unknown.json()["code"] == 20001, f"② 应为 20001，实际 {unknown.json()}"
    assert "可选范围" in unknown.json()["message"], (
        f"② 文案必须说明取值不在可选范围：{unknown.json()['message']}"
    )

    ok = await client.post(
        "/api/v1/agent/tasks",
        headers={**admin_headers, "Idempotency-Key": "e2e-content-ok"},
        json={
            "plugin_id": CONTENT_PLUGIN,
            "conversation_id": "conv_e2e_content",
            "goal": "",
            "inputs": FULL_INPUTS,
        },
    )
    assert ok.status_code == 200, f"③ 合规取值不应被拒：{ok.text}"
    assert ok.json()["code"] == 0
    assert ok.json()["data"]["task_id"]
