"""行政人事 e2e（一）：**简历评分表每行风险非空、字段可判**（P0-P3-21 / QA §4.8.3）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
- **`test_scoring_table_fields`**（TC-P3-HR21-01 / E2E-HR-01）：3 行 × 字段齐；匹配分 ∈[0,100]；
亮点 ≥2；风险非空；结论
  ∈ 枚举；理由 ≥30 字
- **`test_blank_risk_row_fails_the_screening_step`**（TC-P3-HR21-01 反面）：某行风险为空 →
  平台**在引擎层就判该步失败并置可重试**，且不把这张表交给用户
- **`test_resume_limit_twenty`**（TC-P3-HR21-02）：第 21 份 → `20001`
  +「一次最多 20 份，请分批上传」

**判据为什么必须落在导出文件上**：评分表的用户交付面是那张表（导出 CSV / 页面表格），
不是任务状态。只断言"任务 completed"会把"表里没有风险一列"这种事故放过去——
而「风险」这一列正是 PRD 用来防止"只夸不批"的一票否决项。

**判据为什么还必须落在引擎上**：交付面判据只能证明"这一次的表好看"，
证明不了"下一次模型偷懒时会被拦住"。第 2 条用例把风险清空后要求**任务里能看到
失败步骤**，这样"风险为空"就不再是一个靠 QA 事后发现的缺口，而是产品行为。

**本文件未落点的计划用例**：`test_encrypted_resume_partial_failure`
（TC-P3-HR21-03）——平台不解密/不解析 PDF，"加密行标『无法读取』"这条能力还没实现，
写出来只能是假断言。硬写会让 QA 拿到一条永远不会红的绿用例。
"""

from __future__ import annotations

import uuid
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
    step_records,
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
async def test_blank_risk_row_fails_the_screening_step(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
    hr_ready: None,
) -> None:
    """TC-P3-HR21-01 反面：某行「风险」为空 → **平台判该步失败并允许重跑**。

    PRD 原文：「『风险』为空判该行失败」。这条要求此前只落在 QA 的断言函数里
    （`screening_problems`），平台侧没有任何实现——那一版用例只能证明"QA 看得见"，
    证明不了"产品会拦"。现在判据落在引擎的评分表门上，用例改成断言**产品行为**：

    ① 第 2 步 `failed`（不是 completed）；
    ② `retryable is True`（PRD 说"判该行失败重跑"，所以用户能原地重试）；
    ③ 原因点名到那一行的候选人，且是业务语言（不出现 `risk` / 错误码）；
    ④ 这张不合格的表**不交付**（`screening` 不在产物列表里）；
    ⑤ 任务没被拖垮：`on_failure=continue`，后续步骤与接管点照常。

    失败文案的具体措辞由引擎单测逐字锁定（`tests/unit/test_guard_screening.py`），
    这里只钉"用户能不能看到、能不能重试"这一层。

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
    detail = await task_detail(client, admin_headers, task_id)

    records = await step_records(e2e_settings, tenant, task_id)
    failed = records[2]
    assert failed.status == "failed", "① 风险为空必须是该步失败，而不是照常完成"
    assert failed.retryable is True, "② PRD 要求该行重跑，必须标为可重试"
    assert "李娜" in failed.reason_text, f"③ 原因要点名到那一行：{failed.reason_text}"
    assert "风险" in failed.reason_text, f"③ 原因要说明是「风险」为空：{failed.reason_text}"
    for banned in ("risk", "Risk", "30000", "17002", "Traceback"):
        assert banned not in failed.reason_text, (
            f"③ 原因必须是业务语言，不得出现 {banned}：{failed.reason_text}"
        )
    assert failed.operator_action.strip(), "② 必须给出可照做的建议动作"

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    assert "screening" not in artifacts, "④ 风险为空的评分表不得交给用户"

    assert detail["status"] == "waiting_confirm", "⑤ 任务应继续跑到接管点，而不是整体失败"
    assert detail["confirm"]["step_seq"] == 4


def test_screening_problems_can_go_red() -> None:
    """判据函数自身的"可红"自证：喂一张风险为空的行，必须报出问题。

    平台侧的门拦住之后，坏表已经拿不到了；但 QA 那条**判据本身**仍需证明
    "不是永远绿"（PM 2026-10-23 通则）。这里直接用手工构造的行自证，
    不依赖平台产物，因此不会随平台改动变成一条永远绿的装饰用例。
    """
    broken = [{**CANDIDATES[0], "风险": ""}]
    problems = screening_problems(broken)
    assert problems, "负样本自检失败：风险被清空后判据仍全绿，说明判据不可红"
    assert all("风险" in item for item in problems), f"必须只报出风险为空：{problems}"


@pytest.mark.asyncio
async def test_resume_limit_twenty(
    client: AsyncClient, admin_headers: dict[str, str], hr_ready: None
) -> None:
    """TC-P3-HR21-02：第 21 份被拒，20 份放行（PRD P0-P3-21 验收②）。

    判据（三条）：
    ① 21 份 → 400/403 + `20001`，文案**逐字**等于「一次最多 20 份，请分批上传」；
    ② 20 份 → 能建任务（证明拒绝是"因份数"而不是"这条路本身不通"）；
    ③ 拒绝发生在建任务前（A1 返回错误，不产生半成品任务）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        hr_ready: 前置装配。
    """
    too_many = [f"候选人{index:02d}.pdf" for index in range(1, 22)]
    rejected = await client.post(
        "/api/v1/agent/tasks",
        headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={
            "plugin_id": HR_PLUGIN,
            "conversation_id": "conv_e2e_hr_limit",
            "goal": "",
            "inputs": {**HR_INPUTS, "resumes": too_many},
        },
    )
    assert rejected.status_code in {400, 403}, f"① 21 份必须被拒：{rejected.text}"
    body = rejected.json()
    assert body["code"] == 20001, f"① 应为 20001，实际 {body}"
    assert body["message"] == "一次最多 20 份，请分批上传", f"① 文案必须逐字一致：{body['message']}"
    assert "task_id" not in str(body.get("data") or {}), "③ 拒绝不得留下半成品任务"

    exactly_twenty = [f"候选人{index:02d}.pdf" for index in range(1, 21)]
    created = await create_task(
        client, admin_headers, HR_PLUGIN, {**HR_INPUTS, "resumes": exactly_twenty}
    )
    assert created["task_id"], "② 20 份必须能建任务（上限是闭区间）"


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
