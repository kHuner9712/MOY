"""行政人事 e2e（二）：**面试提纲每人 ≥5 问，每题带考察点与参考追问**（P0-P3-22 / QA §4.8.3）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
| `test_outline_five_questions` | TC-P3-HR22-01 / E2E-HR-02 | 提纲份数 == 建议面试人数；每份 ≥5 问；每题含考察点；含参考追问 ≥1 |
| `test_channel_not_connected` | TC-P3-HR22-02 | 渠道未连接时该步失败并给「先连接」的出路，不静默成功 |

**本文件未落点的计划用例**：`test_schedule_conflict_gives_options`（TC-P3-HR22-03）——
"面试官时间冲突给 2 个可选时间"需要日程查询/冲突消解能力，平台侧还没有这条链路
（`tool` 原语只回传下游结果），写出来只能断言桩自己的返回，属于自证循环，故不落点。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    HR_PLUGIN,
    ORG_CAPABILITIES,
    artifact_download_url,
    artifacts_by_key,
    build_hr_script,
    checklist_map,
    create_task,
    decide,
    install_plugin,
    parse_csv_bytes,
    set_org_switches,
    step_records,
    task_detail,
    upgrade_to_paid_plan,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pg]

#: 输入（不带 doc_type：本文件只走到面试提纲与邀约）
HR_INTERVIEW_INPUTS: dict[str, Any] = {
    "position": "前端工程师",
    "department": "技术部",
    "duties": "负责产品前端开发与交付；参与需求评审与技术方案设计。",
    "resumes": ["张伟.pdf", "李娜.pdf", "王强.pdf"],
}


@pytest.fixture
async def hr_ready(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
) -> None:
    """装好行政人事并切到人事语料。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。

    Returns:
        GatewayScript: 本次用例装进门面的桩脚本（用例可直接注入失败）。
    """
    script = build_hr_script()
    use_script(script)
    await upgrade_to_paid_plan(e2e_settings, tenant)
    await set_org_switches(client, admin_headers, enabled=True, capabilities=ORG_CAPABILITIES)
    await install_plugin(client, admin_headers, HR_PLUGIN)
    return script


@pytest.mark.p0
@pytest.mark.asyncio
async def test_outline_five_questions(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    hr_ready: None,
) -> None:
    """TC-P3-HR22-01 / E2E-HR-02：提纲**份数 == 建议面试人数**，每人 ≥5 问且每题可判。

    用户旅程：看到评分表 → 只有「建议面试」的人进入下一轮 → 拿到他们各自的提纲。
    判定点（四条）：
    ① 提纲份数 == 评分表里「建议面试」的人数（不能给"待定"的人也发提纲，
       也不能少发——少了面试官就没法问）；
    ② 每份 ≥ 5 问；
    ③ 每题都带「考察点」且非空（否则这道题没法评分）；
    ④ 每题至少 1 条「参考追问」（面试官临时卡壳时的兜底）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        hr_ready: 前置装配。
    """
    created = await create_task(client, admin_headers, HR_PLUGIN, HR_INTERVIEW_INPUTS)
    task_id = created["task_id"]
    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "waiting_confirm", detail

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)

    # ① 「建议面试」人数取自同一份评分表（两个产物必须自洽）
    screening_response = await client.get(
        artifact_download_url(e2e_settings, artifacts["screening"])
    )
    _header, rows = parse_csv_bytes(screening_response.content)
    invited = [row["姓名"] for row in rows if row["结论"] == "建议面试"]
    assert invited, "语料里必须有「建议面试」的候选人"

    outlines = artifacts["outlines"].inline_text
    sections = parse_outlines(outlines)
    assert len(sections) == len(invited), (
        f"① 提纲份数必须等于建议面试人数 {len(invited)}，实际 {len(sections)}：{list(sections)}"
    )
    assert set(sections) == set(invited), f"① 提纲对象必须与建议面试名单相等：{sections} vs {invited}"

    for who, questions in sections.items():
        assert len(questions) >= 5, f"② {who} 的提纲只有 {len(questions)} 问"
        for index, question in enumerate(questions, start=1):
            assert question["考察点"].strip(), f"③ {who} 第 {index} 问缺考察点"
            assert question["参考追问"], f"④ {who} 第 {index} 问缺参考追问"
            assert all(item.strip() for item in question["参考追问"]), (
                f"④ {who} 第 {index} 问的参考追问不得为空串"
            )


@pytest.mark.asyncio
async def test_channel_not_connected(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    hr_ready: None,
) -> None:
    """TC-P3-HR22-02（等价形态）：渠道未连接时**如实失败并指向「先连接」**，不静默成功。

    用户旅程：确认邀约 → 邮件渠道没连过 → 这一步失败，但提纲与评分表都还在。
    判定点：
    ① 步骤 4 失败，原因是"未连接"且建议动作指向连接（不是"请重试"）；
    ② 任务**不中断**（`on_failure=continue`）：步骤 5、6 照常推进，任务终态 completed，
       并把"哪一步没做成"写进 `result.warnings`（用户能看到缺了什么）；
    ③ 邀约产物不存在，但评分表与提纲仍在（部分失败不拖垮整体）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        hr_ready: 前置装配。
    """
    created = await create_task(client, admin_headers, HR_PLUGIN, HR_INTERVIEW_INPUTS)
    task_id = created["task_id"]

    # 注入「工具未连接」（13002）：邀约这一步必失败
    hr_ready.fail_at(
        4,
        error_code=13002,
        reason_text="邮件还没有连接",
        operator_action="先在设置里连接这个办公工具，再重试这一步",
        retryable=False,
    )

    await decide(client, admin_headers, task_id, "continue")  # 确认邀约 → 步骤 4 失败
    detail = await task_detail(client, admin_headers, task_id)

    records = await step_records(e2e_settings, tenant, task_id)
    assert records[4].status == "failed", "① 渠道未连接时邀约步骤必须失败"
    assert "没有连接" in records[4].reason_text, records[4].reason_text
    assert "连接" in records[4].operator_action, f"① 建议动作必须指向连接：{records[4].operator_action}"
    assert "重试" not in records[4].operator_action or "连接" in records[4].operator_action

    assert detail["status"] == "completed", f"② 部分失败不得拖垮整体：{detail['status']}"
    warnings = (detail.get("result") or {}).get("warnings") or []
    assert warnings, "② 未完成的步骤必须写进 warnings"
    assert any("安排面试邀约" in item for item in warnings), warnings

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    assert "invitation" not in artifacts, "③ 失败的邀约不得留下产物"
    assert "screening" in artifacts and "outlines" in artifacts, "③ 前序产物必须保住"

    items = checklist_map(detail)
    assert items["outlines"]["status"] == "checked"
    assert all(items[key]["status"] == "checked" for key in ("jd", "screening", "outlines")), (
        f"③ 已产出的三项都应是 checked：{ {k: v['status'] for k, v in items.items()} }"
    )


def parse_outlines(text: str) -> dict[str, list[dict[str, Any]]]:
    """把提纲产物解析成 `候选人 → 问题列表`。

    提纲是文本产物（`type=text`），结构由插件声明约定：
    `### 姓名` 起一段，每题三行（题干 / 考察点 / 参考追问）。

    Args:
        text: 提纲正文。

    Returns:
        dict[str, list[dict[str, Any]]]: 每人的问题列表（含考察点与参考追问）。
    """
    sections: dict[str, list[dict[str, Any]]] = {}
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("### "):
            current = line[4:].strip()
            sections.setdefault(current, [])
            continue
        if re.match(r"^\d+\.\s", line) and current:
            sections[current].append({"题干": line, "考察点": "", "参考追问": []})
            continue
        if current and sections[current]:
            if line.startswith("考察点"):
                sections[current][-1]["考察点"] = line.split("：", 1)[-1].strip()
            elif line.startswith("参考追问"):
                sections[current][-1]["参考追问"].append(line.split("：", 1)[-1].strip())
    return sections

