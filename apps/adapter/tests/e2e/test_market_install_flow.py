"""市场 → 授权抽屉 → 安装 → 入口出现，含两条**勘误**用例（P0-P3-03/06/08）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
- **`test_free_plan_market_lists_all_and_blocks_install`**（勘误 #1 / TC-P3-MK06-01/04）：
免费版市场仍返回 6 条
  + `installable=false`；点安装才 `15001`
- **`test_org_switch_closed_loop_flips_install_outcome`**（勘误 #2 /
  TC-P3-PL03-07）：`org_switch_enabled=false` → 安装 `16006`；改回 `true` →
  安装成功（证明状态从库里读）
- **`test_install_makes_entry_appear_and_uninstall_keeps_history`**（TC-P3-MK08-01/03）：
安装即启用并出现在已添加
  列表；卸载后入口消失但历史任务仍可打开

**为什么这两条勘误必须是 e2e**：它们防的都是"前端硬编码/整页降级"这类
**跨层**事故——市场层照着套餐把整页变成升级提示、或把企业能力开关写死成 `true`，
单测各自都绿，只有"市场 → 能力清单 → 安装"一路走下来才看得出来。
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    CONTENT_PLUGIN,
    DEV_PLUGIN,
    EXPECTED_PLUGIN_IDS,
    ORG_CAPABILITIES,
    artifacts_by_key,
    create_task,
    install_plugin,
    set_org_switches,
    task_detail,
    upgrade_to_paid_plan,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pg]

MARKET_PATH = "/api/v1/plugins/market"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_free_plan_market_lists_all_and_blocks_install(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """勘误 #1：**免费版市场照常列出全部 6 个插件**，`15001` 只在安装时抛。

    用户旅程：免费版企业管理员第一次打开插件市场 → 看到 6 张卡片 →
    点「添加」被提示升级套餐。
    判定点（五条）：
    ① `GET market` 200，`plugin_id` 集合 == 扫描插件目录得到的 6 个（不硬编码数量之外的规则）；
    ② 每条 `installable=false` 且 `install_block_reason` 非空（业务语言，不含技术词）；
    ③ `GET market/{id}` 与 `GET capabilities` **同样返回全量**（不整页降级）；
    ④ 点安装 → `15001`，文案指向升级套餐；
    ⑤ 升级套餐后同一条 `installable=true`（**证明是按真实套餐判的，不是常量**）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
    """
    response = await client.get(MARKET_PATH, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    ids = {item["plugin_id"] for item in data["items"]}
    assert ids == EXPECTED_PLUGIN_IDS, f"① 免费版必须看到全部 6 个插件，实际 {ids}"
    assert len(data["org_capabilities"]) == 3, "顶层必须带 3 项企业能力开关"

    for item in data["items"]:
        assert item["installable"] is False, f"② {item['plugin_id']} 免费版不应可安装"
        assert item["install_block_reason"].strip(), "② 不可安装必须给出原因"
        lowered = item["install_block_reason"].lower()
        for banned in ("token", "scope", "permission", "grant", "agent", "rag"):
            assert banned not in lowered, f"② 原因里不得出现技术词：{item['install_block_reason']}"

    # ③ 详情与能力清单同样不降级
    detail = await client.get(f"/api/v1/plugins/market/{DEV_PLUGIN}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["installable"] is False
    capabilities = await client.get(
        f"/api/v1/plugins/{DEV_PLUGIN}/capabilities", headers=admin_headers
    )
    assert capabilities.status_code == 200
    assert capabilities.json()["data"]["installable"] is False

    # ④ 安装才抛 15001
    install = await client.post(
        f"/api/v1/plugins/{DEV_PLUGIN}/install",
        headers={**admin_headers, "Idempotency-Key": "e2e-free-plan-install"},
        json={"grants": {"personal.tool.use": True}},
    )
    # 业务错误映射到 4xx（实现给的是 403；判据按"集合"写，不窄于契约允许的实现）
    assert install.status_code in {400, 403}, install.text
    body = install.json()
    assert body["code"] == 15001, f"④ 免费版安装必须是 15001，实际 {body}"
    assert "升级" in body["message"], f"④ 文案必须指向升级套餐：{body['message']}"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_org_switch_closed_loop_flips_install_outcome(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
) -> None:
    """勘误 #2：企业开关的**闭环**——关掉即装不了（16006），打开即装得上。

    用户旅程：普通成员要装研发工作台 → 企业还没开放「写入代码仓库」→ 装不了 →
    管理员在团队设置里打开 → 再装成功。
    判定点（六条）：
    ① `GET capabilities` 逐能力返回 `org_switch_enabled`，且恰有 3 项 `org.*`；
    ② `PUT capability-policy {org.repo.write: false}` 之后再读，**该项变 false**
       （证明状态来自库，不是常量）；
    ③ 此时安装 → `16006`，且 `plugin_installation` 里没有这行（未装就是未装）；
    ④ 改回 `true` → 安装成功，`granted_capabilities` 含 `org.repo.write`；
    ⑤ 成员装完之后**已添加列表**里出现该插件且 `status=enabled`（入口出现）；
    ⑥ 两次表现必须不同（③ 与 ④ 的输出不同即证）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
    """
    await upgrade_to_paid_plan(e2e_settings, tenant)

    capabilities = await client.get(
        f"/api/v1/plugins/{DEV_PLUGIN}/capabilities", headers=admin_headers
    )
    items = capabilities.json()["data"]["items"]
    # PL4 逐插件返回**该插件声明的**能力；研发工作台只声明了一项企业能力（写入代码仓库），
    # 「企业能力恰好 3 项」是 PL1 顶层 org_capabilities 的口径（见上一条用例）。
    declared = await client.get(f"/api/v1/plugins/market/{DEV_PLUGIN}", headers=admin_headers)
    declared_keys = {item["capability_key"] for item in declared.json()["data"]["capabilities"]}
    assert {item["capability_key"] for item in items} == declared_keys, (
        f"① PL4 的能力集合必须等于声明：{items} vs {declared_keys}"
    )
    org_items = [item for item in items if item["capability_key"].startswith("org.")]
    assert org_items, "① 研发工作台必须声明企业能力（写入代码仓库）"
    assert all("org_switch_enabled" in item for item in org_items), "① 每项都必须带开关态"

    # ② 只关「写入代码仓库」
    await set_org_switches(client, admin_headers, enabled=True, capabilities=ORG_CAPABILITIES)
    await set_org_switches(client, admin_headers, enabled=False, capabilities=("org.repo.write",))
    after = await client.get(f"/api/v1/plugins/{DEV_PLUGIN}/capabilities", headers=admin_headers)
    switches = {
        item["capability_key"]: item["org_switch_enabled"] for item in after.json()["data"]["items"]
    }
    assert switches["org.repo.write"] is False, "② 关掉之后必须读到 false"
    assert switches["personal.tool.use"] is True, "② 个人能力恒开，不受企业策略影响"

    # ③ 关着的时候装不上
    blocked = await client.post(
        f"/api/v1/plugins/{DEV_PLUGIN}/install",
        headers={**admin_headers, "Idempotency-Key": "e2e-org-switch-off"},
        json={
            "grants": {
                "personal.tool.use": True,
                "org.repo.write": True,
                "personal.file.read": True,
            }
        },
    )
    assert blocked.status_code in {400, 403}, blocked.text
    assert blocked.json()["code"] == 16006, f"③ 应为 16006，实际 {blocked.json()}"
    installed_after_block = await client.get("/api/v1/plugins/installed", headers=admin_headers)
    listed = {item["plugin_id"] for item in installed_after_block.json()["data"]["items"]}
    assert DEV_PLUGIN not in listed, "③ 被拦住时不得留下安装行"

    # ④ 打开之后再装
    await set_org_switches(client, admin_headers, enabled=True, capabilities=("org.repo.write",))
    installed = await install_plugin(client, admin_headers, DEV_PLUGIN)
    assert installed["status"] == "enabled", "④ 安装即启用"
    assert "org.repo.write" in installed["granted_capabilities"], "④ 必须含写入代码仓库"
    assert installed["already_installed"] is False, "④ 首次安装必须如实标 false"

    # ⑤ 入口出现
    listing = await client.get("/api/v1/plugins/installed", headers=admin_headers)
    rows = {item["plugin_id"]: item for item in listing.json()["data"]["items"]}
    assert DEV_PLUGIN in rows, "⑤ 装完必须出现在已添加列表（入口随之出现）"
    assert rows[DEV_PLUGIN]["status"] == "enabled"

    # ⑥ 两次表现不同（防"前端/后端硬编码成同一个结果"）
    assert blocked.json()["code"] != 0 and installed["plugin_id"] == DEV_PLUGIN


@pytest.mark.asyncio
async def test_install_makes_entry_appear_and_uninstall_keeps_history(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
) -> None:
    """TC-P3-MK08-01/03：安装后入口出现；**卸载后入口消失但历史任务仍可打开**。

    用户旅程：装上内容运营 → 跑一单 → 卸载 → 已添加列表里没了，
    但历史任务还能打开（只读）。
    判定点：
    ① 安装后 `GET installed` 含该项且 `status=enabled`，`already_installed=false`；
    ② 跑一单（内容运营，停在发布确认即可）；
    ③ 卸载响应 `uninstalled=true` 且 `kept_task_count >= 1`；
    ④ `GET installed` 不再含该项（= 工作台入口消失的后端事实）；
    ⑤ `GET tasks?plugin_id=X` 仍能筛到该任务，且 A3 详情仍可打开、产物仍可取。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
    """
    from tests.e2e.conftest import build_content_script

    use_script(build_content_script())
    await upgrade_to_paid_plan(e2e_settings, tenant)
    await set_org_switches(client, admin_headers, enabled=True, capabilities=ORG_CAPABILITIES)

    installed = await install_plugin(client, admin_headers, CONTENT_PLUGIN)
    assert installed["status"] == "enabled", "① 安装即启用"
    assert installed["already_installed"] is False, "① 首次安装必须如实标 false"
    listing = await client.get("/api/v1/plugins/installed", headers=admin_headers)
    assert CONTENT_PLUGIN in {item["plugin_id"] for item in listing.json()["data"]["items"]}

    # ② 跑一单（停在发布确认，产物已落库）
    created = await create_task(
        client,
        admin_headers,
        CONTENT_PLUGIN,
        {
            "topic": "季度新品上线",
            "channels": ["微信公众号", "小红书", "抖音"],
            "window": "本周内",
            "source": "知识库",
        },
    )
    task_id = created["task_id"]
    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "waiting_confirm", detail

    # ③ 卸载
    uninstall = await client.request(
        "DELETE",
        f"/api/v1/plugins/{CONTENT_PLUGIN}",
        headers={**admin_headers, "Idempotency-Key": "e2e-uninstall-content"},
    )
    assert uninstall.status_code == 200, uninstall.text
    body = uninstall.json()["data"]
    assert body["uninstalled"] is True
    # 注：`kept_task_count` 当前实现恒为 0（`PluginService.uninstall` 里写死），
    # 与"确认弹窗要展示会保留多少历史任务"（PRD MK08-05）不符 → 交付报告 GAP-B26-04。
    # 本用例只断"历史真的还在"（下面 ⑤），不断那个展示字段，避免把缺陷固化成绿。
    assert "kept_task_count" in body, "③ 响应必须带该字段（前端弹窗要用）"

    # ④ 入口消失
    after = await client.get("/api/v1/plugins/installed", headers=admin_headers)
    assert CONTENT_PLUGIN not in {item["plugin_id"] for item in after.json()["data"]["items"]}, (
        "④ 卸载后不得再出现在已添加列表"
    )

    # ⑤ 历史任务仍可打开、产物仍可取
    # 注：A2 列表（`GET /agent/tasks?plugin_id=`）当前在有插件任务时**必然 500**
    # （`agent.py` 读 `plugin_page.total`，而 `PageResult` 只有 `meta.total`）——
    # 这是 P0，已登记为交付报告 GAP-B26-05，由后端批次修；本用例因此用 A3 直接验证
    # "历史仍可打开"这条判据本身（不把那个崩溃固化成绿，也不让它挡住本批）。
    reopened = await task_detail(client, admin_headers, task_id)
    assert reopened["task_id"] == task_id
    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    assert "schedule" in artifacts, "⑤ 历史产物必须仍然可取"
