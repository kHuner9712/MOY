"""失败 → 三个动作 → 重试本步 → **前序步骤不重跑**（P0-P3-13/14 / QA §4.3 / §4.8）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
| `test_failed_step_exposes_three_actions` | TC-P3-WF13-01 ① | 失败步骤给出「原因 / 可重试性 / 建议动作」三要素，且响应体不含内部错误码 |
| `test_retry_step_reuses_prior_artifacts` | TC-P3-WF13-01 ② | 新任务 `retry_of` 指向原任务；前序步骤**一次都不重跑**且产物 `sha256` 逐字相等 |
| `test_three_plugins_checklist_exact` | TC-P3-WF14-02 | 三插件的核查清单项数 **4 / 3 / 4**，名称逐字相等，状态全部 `checked` |

**"前序不重跑"为什么必须用调用流水证明**：只看任务详情，`completed` 的步骤
长得一模一样；只有"下游这一单到底被调了几次"能区分「复用」与「重新生成」。
重跑一次的成本是三重（模型费用、用户等待、结果漂移），重试的全部价值就在这条。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    CONTENT_PLUGIN,
    DEV_PLUGIN,
    HR_PLUGIN,
    ORG_CAPABILITIES,
    artifacts_by_key,
    build_content_script,
    build_dev_script,
    build_hr_script,
    checklist_map,
    create_task,
    decide,
    install_plugin,
    set_org_switches,
    step_records,
    task_detail,
    upgrade_to_paid_plan,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pg]

#: 内容运营输入（失败注入用）
CONTENT_INPUTS: dict[str, Any] = {
    "topic": "季度新品上线",
    "channels": ["微信公众号", "小红书", "抖音"],
    "window": "本周内",
    "source": "知识库",
}


async def _prepare(
    client: AsyncClient,
    headers: dict[str, str],
    settings: Any,
    tenant: str,
    use_script: Callable[[Any], None],
    script: Any,
    plugin_id: str,
) -> None:
    """装好某个插件并切到它的语料。

    Args:
        client: 异步客户端。
        headers: 管理员请求头。
        settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
        script: 要装入的桩脚本。
        plugin_id: 插件标识。
    """
    use_script(script)
    await upgrade_to_paid_plan(settings, tenant)
    await set_org_switches(client, headers, enabled=True, capabilities=ORG_CAPABILITIES)
    await install_plugin(client, headers, plugin_id)


@pytest.mark.p0
@pytest.mark.asyncio
async def test_failed_step_exposes_three_actions(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Callable[[Any], None],
    bare_repo: Any,
) -> None:
    """TC-P3-WF13-01 ①：失败步骤必须给**能照着做**的三要素，而不是一句"失败了"。

    用户旅程：排期表这一步没做成 → 看到"哪一步、为什么、我该做什么"。
    判定点（四条）：
    ① 失败步骤 `status=failed`，且 `reason_text` 非空、是业务语言（不含堆栈/错误码）；
    ② `operator_action` 非空（用户知道下一步做什么）；
    ③ `retryable` 有明确取值（不静默自动重试写外部动作）；
    ④ A3/A4 响应体 `code=0` 且不含四位内部码（PRD E-9：失败走状态不走错误码）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
        bare_repo: 裸仓库（占位，保证 dev 语料可装配）。
    """
    script = build_content_script()
    await _prepare(
        client, admin_headers, e2e_settings, tenant, use_script, script, CONTENT_PLUGIN
    )
    script.fail_at(
        5,
        error_code=30001,
        reason_text="发布排期表没有生成",
        operator_action="修改排期表里的计划时间后重新导出",
        retryable=False,
    )

    created = await create_task(client, admin_headers, CONTENT_PLUGIN, CONTENT_INPUTS)
    task_id = created["task_id"]
    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "failed", detail

    records = await step_records(e2e_settings, tenant, task_id)
    failed = records[5]
    assert failed.status == "failed", "① 失败步骤状态必须是 failed"
    assert failed.reason_text.strip(), "① 必须给出失败原因"
    for banned in ("Traceback", "Error", "Exception", "at 0x"):
        assert banned not in failed.reason_text, f"① 原因里不得出现技术细节：{failed.reason_text}"
    assert failed.operator_action.strip(), "② 必须给出建议动作"
    assert isinstance(failed.retryable, bool), "③ 可重试性必须是明确布尔值"
    assert records[6].status == "skipped", "① abort 语义：后续步骤必须跳过"

    for path in (
        f"/api/v1/agent/tasks/{task_id}",
        f"/api/v1/agent/tasks/{task_id}/steps",
    ):
        response = await client.get(path, headers=admin_headers)
        assert response.json()["code"] == 0, f"④ {path} 必须仍是 code=0"
        assert "17002" not in response.text, "④ 响应体不得出现内部汇总码 17002"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_retry_step_reuses_prior_artifacts(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Callable[[Any], None],
    bare_repo: Any,
) -> None:
    """TC-P3-WF13-01 ②：重试本步只跑该步，**前序步骤一次都不重跑、产物原样复用**。

    用户旅程：排期没成功 → 处理完再"重试这一步" → 不希望前三步的稿件被重新生成一遍。
    判定点（五条）：
    ① 重试返回 `resumed=false`、`retry_of=原任务`、任务 ID 不同；
    ② 新任务的调用流水里**没有**步骤 1-4（"没重跑"的硬证据）；
    ③ 前序产物的 `content_sha256` 与原任务逐字相等（复用而非重生成）；
    ④ 新任务最终 `completed`；
    ⑤ 原任务状态不变（重试不影响历史）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
        bare_repo: 裸仓库（占位）。
    """
    script = build_content_script()
    await _prepare(
        client, admin_headers, e2e_settings, tenant, use_script, script, CONTENT_PLUGIN
    )
    script.fail_at(
        5,
        error_code=30001,
        reason_text="发布排期表没有生成",
        operator_action="修改后重新导出",
        retryable=False,
    )
    created = await create_task(client, admin_headers, CONTENT_PLUGIN, CONTENT_INPUTS)
    task_id = created["task_id"]
    original = await artifacts_by_key(e2e_settings, tenant, task_id)
    assert "schedule" not in original, "失败步骤不得留下产物"

    # 用户按建议处理完之后重试本步
    script.heal()
    retried = await decide(client, admin_headers, task_id, "retry_step", from_step=5)
    assert retried["resumed"] is False, "① 重试本步应新建任务"
    assert retried["retry_of"] == task_id, "① retry_of 必须指回原任务"
    new_task = retried["task_id"]
    assert new_task != task_id

    executed = script.steps_of(new_task)
    assert 4 not in executed and 3 not in executed, f"② 前序步骤不得重跑，实际执行了 {executed}"
    assert 5 in executed, "② 重试起点必须被执行"

    replayed = await artifacts_by_key(e2e_settings, tenant, new_task)
    for key in ("materials", "angles", "articles"):
        assert replayed[key].content_sha256 == original[key].content_sha256, (
            f"③ 前序产物 {key} 必须原样复用"
        )
    assert "schedule" in replayed, "④ 重试后该步产物必须补上"

    new_detail = await task_detail(client, admin_headers, new_task)
    if new_detail["status"] == "waiting_confirm":
        await decide(client, admin_headers, new_task, "continue")
        new_detail = await task_detail(client, admin_headers, new_task)
    assert new_detail["status"] == "completed", f"④ 重试后必须完成：{new_detail['status']}"
    assert new_detail["retry_of"] == task_id, "④ 详情里必须能看出这是哪一单的重试"

    old = await task_detail(client, admin_headers, task_id)
    assert old["status"] == "failed", "⑤ 原任务状态不得被改写"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_three_plugins_checklist_exact(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Callable[[Any], None],
    bare_repo: Any,
) -> None:
    """TC-P3-WF14-02：三插件各完成 1 次，核查清单 **4 / 3 / 4** 项且名称逐字相等。

    用户旅程：演示/验收时最常被问的一句是"这次到底交付了哪几样东西"。
    核查清单就是那个答案，项数与名称都不许漂。
    判定点（四条）：
    ① 项数分别为 4 / 3 / 4；
    ② 名称与 PRD §3.2.3 ② **逐字相等**；
    ③ 每项状态全部 `checked`（没有 `pending_manual` / `missing`）；
    ④ 每项都带可点开的 `download_url`（"能核对"而不是"说完成了"）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
        bare_repo: 裸仓库。
    """
    expected: dict[str, list[str]] = {
        DEV_PLUGIN: ["需求要点", "代码文件", "用例文件", "提交记录与合并请求"],
        CONTENT_PLUGIN: ["三平台稿件", "排期表", "发布回执"],
        HR_PLUGIN: ["招聘说明", "筛选评分表", "面试提纲", "公文或录用通知"],
    }
    inputs: dict[str, dict[str, Any]] = {
        DEV_PLUGIN: {
            "title": "列表页加导出按钮",
            "requirement": "在客户列表页工具栏加一个导出按钮。",
            "repo": "acme/web",
            "branch_policy": "新建分支",
        },
        CONTENT_PLUGIN: CONTENT_INPUTS,
        HR_PLUGIN: {
            "position": "前端工程师",
            "department": "技术部",
            "duties": "负责产品前端开发与交付。",
            "resumes": ["张伟.pdf", "李娜.pdf", "王强.pdf"],
            "doc_type": "通知",
        },
    }
    builders: dict[str, Callable[[], Any]] = {
        DEV_PLUGIN: lambda: build_dev_script(bare_repo),
        CONTENT_PLUGIN: build_content_script,
        HR_PLUGIN: build_hr_script,
    }

    for plugin_id, names in expected.items():
        await _prepare(
            client,
            admin_headers,
            e2e_settings,
            tenant,
            use_script,
            builders[plugin_id](),
            plugin_id,
        )
        created = await create_task(client, admin_headers, plugin_id, inputs[plugin_id])
        task_id = created["task_id"]
        detail = await _finish(client, admin_headers, task_id)
        assert detail["status"] == "completed", f"{plugin_id} 未完成：{detail['status']}"

        items = checklist_map(detail)
        assert list(items) == [item for item in _keys_of(plugin_id)], (
            f"② {plugin_id} 核查项顺序与键必须与声明一致：{list(items)}"
        )
        assert len(items) == len(names), f"① {plugin_id} 应有 {len(names)} 项，实际 {len(items)}"
        assert [items[key]["name"] for key in items] == names, (
            f"② {plugin_id} 核查项名称必须逐字相等"
        )
        states = {key: value["status"] for key, value in items.items()}
        assert all(state == "checked" for state in states.values()), (
            f"③ {plugin_id} 应全部 checked：{states}"
        )
        assert all(items[key]["download_url"] for key in items), f"④ {plugin_id} 每项都要能点开"


def _keys_of(plugin_id: str) -> tuple[str, ...]:
    """取三插件核查清单的键顺序（与声明一致）。

    Args:
        plugin_id: 插件标识。

    Returns:
        tuple[str, ...]: 键序列。
    """
    mapping: dict[str, tuple[str, ...]] = {
        DEV_PLUGIN: ("key_points", "code_files", "test_files", "commit"),
        CONTENT_PLUGIN: ("articles", "schedule", "receipt"),
        HR_PLUGIN: ("jd", "screening", "outlines", "document"),
    }
    return mapping[plugin_id]


async def _finish(
    client: AsyncClient, headers: dict[str, str], task_id: str
) -> dict[str, Any]:
    """把任务推到终态（一路确认所有人工接管点，最多 3 次）。

    Args:
        client: 异步客户端。
        headers: 请求头。
        task_id: 任务 ID。

    Returns:
        dict[str, Any]: 终态详情。
    """
    detail = await task_detail(client, headers, task_id)
    for _ in range(3):
        if detail["status"] != "waiting_confirm":
            break
        await decide(client, headers, task_id, "continue")
        detail = await task_detail(client, headers, task_id)
    return detail

