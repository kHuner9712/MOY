"""行政人事 e2e（一）：**简历评分表每行风险非空、字段可判**（P0-P3-21 / QA §4.8.3）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
- **`test_scoring_table_fields`**（TC-P3-HR21-01 / E2E-HR-01）：3 行 × 字段齐；匹配分 ∈[0,100]；
亮点 ≥2；风险非空；结论
  ∈ 枚举；理由 ≥30 字

**判据为什么必须落在导出文件上**：评分表的用户交付面是那张表（导出 CSV / 页面表格），
不是任务状态。只断言"任务 completed"会把"表里没有风险一列"这种事故放过去——
而「风险」这一列正是 PRD 用来防止"只夸不批"的一票否决项。

**本文件未落点的计划用例（无判定面，已在交付报告列明）**：

- `test_resume_limit_twenty`（TC-P3-HR21-02）：声明里 `resumes` 是单个 `file` 输入，
  平台没有任何"一次最多 20 份"的计数规则，也没有 PDF 读取实现——判据无处可断。
- `test_encrypted_resume_partial_failure`（TC-P3-HR21-03）：同上，平台不解密/不解析 PDF，
  "加密行标『无法读取』"这条能力还没实现，写出来只能是假断言。
  这两条要等"简历解析"能力落地后再补，硬写会让 QA 拿到一条永远不会红的绿用例。
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    CANDIDATES,
    HR_PLUGIN,
    ORG_CAPABILITIES,
    SCREENING_COLUMNS,
    artifact_download_url,
    artifacts_by_key,
    build_hr_script,
    checklist_map,
    create_task,
    install_plugin,
    parse_csv_bytes,
    set_org_switches,
    task_detail,
    upgrade_to_paid_plan,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pg]

#: 结论枚举（PRD P0-P3-21）
CONCLUSIONS: frozenset[str] = frozenset({"建议面试", "待定", "不通过"})

#: 行政人事输入（带 3 份简历、并在步骤 6 生成公文，覆盖完整旅程）
HR_INPUTS: dict[str, Any] = {
    "position": "前端工程师",
    "department": "技术部",
    "duties": "负责产品前端开发与交付；参与需求评审与技术方案设计。",
    "hard_requirements": "三年以上同岗位经验",
    "resumes": ["张伟.pdf", "李娜.pdf", "王强.pdf"],
    "doc_type": "通知",
    "doc_points": "事项说明；执行要求；反馈时间",
}


@pytest.fixture
async def hr_ready(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
) -> None:
    """把行政人事装到「可建任务」状态，并切到人事语料。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
    """
    use_script(build_hr_script())
    await upgrade_to_paid_plan(e2e_settings, tenant)
    await set_org_switches(client, admin_headers, enabled=True, capabilities=ORG_CAPABILITIES)
    await install_plugin(client, admin_headers, HR_PLUGIN)


@pytest.mark.p0
@pytest.mark.asyncio
async def test_scoring_table_fields(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    hr_ready: None,
) -> None:
    """TC-P3-HR21-01 / E2E-HR-01：评分表 3 行，**每行风险非空**且六个字段可判。

    用户旅程：上传 3 份简历 → 生成招聘说明 → 看到筛选评分表 → 下载核对。
    判定点（六条，任一条不成立这张表就不能用来做招聘决定）：
    ① 行数 == 简历数 3；
    ② 列齐（姓名 / 匹配分 / 亮点 / 风险 / 结论 / 理由）；
    ③ 匹配分是 0..100 的整数；
    ④ 亮点 ≥ 2 条（用「；」分隔）；
    ⑤ **风险逐行非空**（这是本用例的唯一不可让步项）；
    ⑥ 结论 ∈ {建议面试, 待定, 不通过}，理由 ≥ 30 字（ADJ-5 字数法）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        hr_ready: 前置装配。
    """
    created = await create_task(client, admin_headers, HR_PLUGIN, HR_INPUTS)
    task_id = created["task_id"]

    detail = await task_detail(client, admin_headers, task_id)
    # 邀约是接管点：评分表与提纲已生成，停在步骤 4 的确认
    assert detail["status"] == "waiting_confirm", detail
    assert detail["confirm"]["step_seq"] == 4, "必须停在邀约确认"

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    screening = artifacts["screening"]
    response = await client.get(artifact_download_url(e2e_settings, screening))
    assert response.status_code == 200, f"评分表必须可下载，实际 {response.status_code}"

    header, rows = parse_csv_bytes(response.content)
    assert header == SCREENING_COLUMNS, f"② 列必须逐字等于 {SCREENING_COLUMNS}，实际 {header}"
    assert len(rows) == len(CANDIDATES), f"① 行数必须等于简历数 {len(CANDIDATES)}，实际 {len(rows)}"

    for row in rows:
        who = row["姓名"]
        score = int(row["匹配分"])
        assert 0 <= score <= 100, f"③ {who} 的匹配分越界：{score}"
        highlights = [item for item in row["亮点"].split("；") if item.strip()]
        assert len(highlights) >= 2, f"④ {who} 的亮点少于 2 条：{row['亮点']!r}"
        assert row["风险"].strip(), f"⑤ {who} 的「风险」为空——该行判失败并应重跑"
        assert row["结论"] in CONCLUSIONS, f"⑥ {who} 的结论不在枚举内：{row['结论']!r}"
        assert len(row["理由"].strip()) >= 30, f"⑥ {who} 的理由不足 30 字：{len(row['理由'])}"

    # 核查清单里「筛选评分表」必须指向同一份可下载产物
    items = checklist_map(detail)
    assert items["screening"]["status"] == "checked"
    assert items["screening"]["download_url"] == artifact_download_url(e2e_settings, screening)


@pytest.mark.asyncio
async def test_blank_risk_row_is_rejected_by_quality_gate(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
    hr_ready: None,
) -> None:
    """负样本自检：把某一行的「风险」清空后，**上面那套判据必须变红**。

    这是"判据可红"的自证（PM 2026-10-23 通则：禁止"永远绿"的判据）：
    用同一份断言函数跑一次被改坏的表，它必须报出「风险为空」。
    顺带证明"风险为空则该行判失败重跑"这条 PRD 要求**在平台侧还没有实现**
    （引擎护栏只管 offer 金额与排期时间）——该缺口已登记在交付报告里。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
        hr_ready: 前置装配。
    """
    use_script(build_hr_script(blank_risk=True))
    created = await create_task(client, admin_headers, HR_PLUGIN, HR_INPUTS)
    task_id = created["task_id"]
    await task_detail(client, admin_headers, task_id)

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    response = await client.get(artifact_download_url(e2e_settings, artifacts["screening"]))
    _header, rows = parse_csv_bytes(response.content)

    problems = screening_problems(rows)
    assert problems, "负样本自检失败：风险被清空后判据仍全绿，说明判据不可红"
    assert any("风险" in item for item in problems), f"必须报出风险为空：{problems}"
    # 正样本（另两行）不应被牵连
    assert len(problems) == 1, f"只应报出被改坏的那一行，实际：{problems}"


def screening_problems(rows: list[dict[str, str]]) -> list[str]:
    """把 TC-P3-HR21-01 的判据抽成函数（正样本与负样本共用同一套规则）。

    Args:
        rows: 评分表行。

    Returns:
        list[str]: 违规描述（空表示全部通过）。
    """
    problems: list[str] = []
    for row in rows:
        who = row.get("姓名", "")
        try:
            score = int(row.get("匹配分", ""))
        except ValueError:
            problems.append(f"{who} 匹配分不是整数：{row.get('匹配分')!r}")
        else:
            if not 0 <= score <= 100:
                problems.append(f"{who} 匹配分越界：{score}")
        highlights = [item for item in row.get("亮点", "").split("；") if item.strip()]
        if len(highlights) < 2:
            problems.append(f"{who} 亮点少于 2 条")
        if not row.get("风险", "").strip():
            problems.append(f"{who} 风险为空")
        if row.get("结论") not in CONCLUSIONS:
            problems.append(f"{who} 结论不在枚举内")
        if len(row.get("理由", "").strip()) < 30:
            problems.append(f"{who} 理由不足 30 字")
    return problems
