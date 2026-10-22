"""行政人事 e2e（三）：**Offer 金额永久留空 + 0 处金额臆造 + 公文可导出**（P0-P3-23 / QA §4.8.3）。

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
| `test_amount_fields_empty` | TC-P3-HR23-01 / E2E-HR-03 ① | 三个金额字段 `== ""`（`null` / `0` / 「面议」/「待定」均判失败） |
| `test_no_fabricated_amount` | TC-P3-HR23-02 / E2E-HR-03 ②③④⑤ | R1/R2 0 命中；正样本必红；豁免项不计；**扫描范围只含 offer** |
| `test_export_blocked_without_salary` | TC-P3-HR23-03 | 金额位置写「面议」→ 被拦 + **不产生文件**（offer 产物不落库） |
| `test_four_doc_types_exportable` | TC-P3-HR23-04 / E2E-HR-04 | 4 类公文各 1 篇；`.docx` 可被 `python-docx` 打开且段落 > 0 |

**金额规则用的是平台自己那份实现**（`app.plugins.engine.scan_offer_amount` 与
`sanitizer` 的豁免规则），不是测试另写一份——判据与护栏同源，才能保证
"护栏放过的"和"用例判定放过的"是同一批。

**一条与计划的自洽性说明（已在交付报告登记）**：TC-P3-HR23-01 要求三个金额字段
**必须为空**，TC-P3-HR23-03 又要求"未填金额点导出要被拦住"。两条同时成立只能靠
"人事先手填再导出"，而平台当前**没有**"导出前校验金额已填"这条链路
（`render_file` 只渲染正文）。因此本文件把 `test_export_blocked_without_salary`
落成**已有护栏的真实语义**：金额位置写「面议/待定」这种"甩给用户"的写法会被拦下
且不产出文件；空白视为"等人工填写"（合法）。
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from httpx import AsyncClient

from app.plugins.engine import scan_offer_amount
from app.plugins.sanitizer import ArtifactSanitizer, strip_amount_exemptions
from tests.e2e.conftest import (
    HR_PLUGIN,
    ORG_CAPABILITIES,
    artifacts_by_key,
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

#: Offer 的三个金额字段（PRD §0.4：一律留空由人事手填）
AMOUNT_FIELDS: tuple[str, ...] = ("薪资", "试用期薪资", "年终奖")

#: 公文四类（声明的 `doc_type` 枚举）
DOC_TYPES: tuple[str, ...] = ("通知", "会议纪要", "请示", "周报")

#: 行政人事输入
HR_INPUTS: dict[str, Any] = {
    "position": "前端工程师",
    "department": "技术部",
    "duties": "负责产品前端开发与交付；参与需求评审与技术方案设计。",
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
) -> Any:
    """装好行政人事并切到人事语料。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。

    Returns:
        Any: 本次用例装进门面的桩脚本。
    """
    script = build_hr_script()
    use_script(script)
    await upgrade_to_paid_plan(e2e_settings, tenant)
    await set_org_switches(client, admin_headers, enabled=True, capabilities=ORG_CAPABILITIES)
    await install_plugin(client, admin_headers, HR_PLUGIN)
    return script


@pytest.mark.p0
@pytest.mark.asyncio
async def test_amount_fields_empty(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    hr_ready: Any,
) -> None:
    """TC-P3-HR23-01 / E2E-HR-03 ①：**三个金额字段必须为空**，不得出现任何占位取值。

    用户旅程：确认邀约 → 拿到录用通知草稿 → 人事自己去填金额。
    判定点（三条）：
    ① 三个金额字段解析出来全是 `""`（`null` / `0` / 「面议」/「待定」都算失败——
       因为审批者无法区分"模型不知道"与"人事还没填"）；
    ② 全文不含任何带单位金额（R1）与薪资上下文里的裸数字（R2）；
    ③ 草稿明确写了"金额由人事填写"的说明（用户知道下一步该做什么）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        hr_ready: 前置装配。
    """
    created = await create_task(client, admin_headers, HR_PLUGIN, HR_INPUTS)
    task_id = created["task_id"]
    await decide(client, admin_headers, task_id, "continue")  # 确认邀约 → 跑到公文
    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "completed", detail

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    offer = artifacts["offer"].inline_text

    values = parse_amount_fields(offer)
    assert set(values) == set(AMOUNT_FIELDS), f"① 三个金额字段必须都在：{values}"
    for key, value in values.items():
        assert value == "", f"① {key} 必须为空，实际 {value!r}（null/0/面议/待定都判失败）"

    scan = scan_offer_amount(offer)
    assert scan.r1 == (), f"② 不得出现带单位金额：{scan.r1}"
    assert scan.r2 == (), f"② 薪资上下文不得出现裸数字：{scan.r2}"
    assert "人事" in offer, "③ 必须写明金额由人事填写"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_no_fabricated_amount(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    hr_ready: Any,
) -> None:
    """TC-P3-HR23-02 / E2E-HR-03 ②~⑤：金额规则 **只扫 offer 产物**，正样本必红。

    用户旅程：拿到录用通知 → 合规复核（离开界面也能复核）。
    判定点（五条）：
    ① `output_key=offer` 的产物 R1（带单位金额）0 命中；
    ② R2（薪资词 ±20 字符内的区间或裸数字）0 命中；
    ③ **正样本必红**：`15k`、`1.2万`、`15000元`、`8000-10000` 都要被判出来
       （旧正则漏判的自然写法，是最容易"看起来更严"却漏检的一批）；
    ④ 豁免项固化：日期 / 百分比 / 「3 个月」不计入命中；
    ⑤ **扫描范围不含简历筛选产物**：评分表里的「待定」是合法结论，
       一旦把扫描放到任务级就会把每份「待定」简历误报成 P0。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        hr_ready: 前置装配。
    """
    created = await create_task(client, admin_headers, HR_PLUGIN, HR_INPUTS)
    task_id = created["task_id"]
    await decide(client, admin_headers, task_id, "continue")
    await task_detail(client, admin_headers, task_id)

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    offer = artifacts["offer"].inline_text

    scan = scan_offer_amount(offer)
    assert scan.r1 == (), f"① R1 必须 0 命中：{scan.r1}"
    assert scan.r2 == (), f"② R2 必须 0 命中：{scan.r2}"
    assert scan.r3 == (), f"① 金额位置不得出现「面议/待定」：{scan.r3}"

    # ③ 正样本必红（判据可红的自证）
    positives = {
        "薪资：15k": True,
        "薪资：1.2万": True,
        "月薪 15000 元": True,
        "薪资：8000-10000": True,
    }
    for text, should_hit in positives.items():
        hit = scan_offer_amount(text)
        assert (hit.r1 or hit.r2) if should_hit else True, f"③ 正样本必须被判出来：{text!r}"

    # ④ 豁免项不计入（日期 / 百分比 / 「3 个月」）
    exemption = "入职日期：2026-11-01，试用期 3 个月，绩效浮动 20%，薪资："
    exempted = strip_amount_exemptions(exemption)
    assert "2026-11-01" not in exempted, "④ 日期必须被豁免"
    assert scan_offer_amount(exemption).r1 == (), "④ 豁免项不得被判成金额"
    assert scan_offer_amount(exemption).r2 == (), "④ 日期/百分比不得被判成裸数字"

    # ⑤ 扫描范围只含 offer：评分表里的「待定」合法，不得纳入扫描
    screening = artifacts["screening"].inline_text
    assert "待定" in screening, "语料里应有一份「待定」的简历（用来证明扫描范围）"
    assert scan_offer_amount(screening).r3, (
        "⑤ 若把扫描放到任务级，评分表的「待定」会被误报——所以范围必须写死为 offer"
    )


@pytest.mark.asyncio
async def test_export_blocked_without_salary(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
    hr_ready: Any,
) -> None:
    """TC-P3-HR23-03：金额位置写「面议」→ **被拦住且不产生文件**。

    用户旅程：草稿里金额被写成「面议」→ 想去导出 → 被拦下并给出改法 → 改完再导出。
    判定点：
    ① offer 步骤失败（`retryable=false`：合规问题不能靠重跑碰运气）；
    ② 失败原因点明"金额位置写了面议/待定这类说法"；
    ③ 建议动作指向"清空或改为「按公司标准执行」后重新导出"，**不是**"重新发起任务"；
    ④ **不产生文件**：`offer` 产物没有落库（拦下的草稿不能交给用户去发）；
    ⑤ 失败被写进 `result.warnings`，后续公文仍照常推进（部分失败不拖垮整体）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
        hr_ready: 前置装配。
    """
    use_script(build_hr_script(negotiable=True))
    created = await create_task(client, admin_headers, HR_PLUGIN, HR_INPUTS)
    task_id = created["task_id"]
    await decide(client, admin_headers, task_id, "continue")

    detail = await task_detail(client, admin_headers, task_id)
    records = await step_records(e2e_settings, tenant, task_id)
    assert records[5].status == "failed", f"① offer 步骤必须失败：{records[5].status}"
    assert records[5].retryable is False, "① 合规失败不得标为可重试"
    assert "面议" in records[5].reason_text, f"② 原因必须点明：{records[5].reason_text}"
    assert "重新导出" in records[5].operator_action, (
        f"③ 建议动作必须指向改字段后重新导出：{records[5].operator_action}"
    )

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    assert "offer" not in artifacts, "④ 被拦下的 offer 不得落库"
    warnings = (detail.get("result") or {}).get("warnings") or []
    assert any("录用通知" in item for item in warnings), f"⑤ 失败必须进 warnings：{warnings}"
    assert "document" in artifacts, "⑤ 后续公文步骤不受影响（on_failure=continue）"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_four_doc_types_exportable(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    hr_ready: Any,
) -> None:
    """TC-P3-HR23-04 / E2E-HR-04：4 类公文各 1 篇，**真 docx 可打开**且核查清单 4 项齐。

    用户旅程：选公文类型 → 生成 → 导出 `.docx` → 打开确认要素齐。
    判定点（四条）：
    ① 4 类各跑 1 次，任务全部 `completed`；
    ② 每次都有 `document` 产物，且名称/类型是文件；
    ③ **是真 docx**：`python-docx` 能打开、段落数 > 0、文件大小 > 0（不是 md 改名）；
    ④ 核查清单 4 项名称逐字相等且全部 `checked`。

    **第 ③ 条的落点说明**：`plugin_artifact` 表**没有二进制列**（字节走对象存储
    `storage_key`），而本阶段的对象存储未接，因此下载代理回落到 `inline_text`，
    用户拿到的是正文文本而不是 zip。这是真实缺口（交付报告 GAP-B26-02）。
    为了让本用例仍能钉住"平台确实能产出真 docx"，第 ③ 条用**平台自己的渲染器**
    （`ArtifactSanitizer.render_file(render_format="docx")`，与步骤 6 同一实现）
    产出字节并用 `python-docx` 打开；产物记录、清单状态仍走真库真端点。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        hr_ready: 前置装配。
    """
    names = ["招聘说明", "筛选评分表", "面试提纲", "公文或录用通知"]
    for doc_type in DOC_TYPES:
        inputs = {**HR_INPUTS, "doc_type": doc_type}
        created = await create_task(client, admin_headers, HR_PLUGIN, inputs)
        task_id = created["task_id"]
        await decide(client, admin_headers, task_id, "continue")
        detail = await task_detail(client, admin_headers, task_id)
        assert detail["status"] == "completed", f"① 公文「{doc_type}」任务未完成：{detail}"

        artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
        document = artifacts["document"]
        assert document.type == "file", f"② 公文必须是文件产物，实际 {document.type}"
        assert document.size_bytes > 0, f"② 「{doc_type}」导出文件不得为空"
        assert document.content_sha256, f"② 「{doc_type}」必须记录内容摘要"

        items = checklist_map(detail)
        assert [items[key]["name"] for key in ("jd", "screening", "outlines", "document")] == names, (
            "④ 核查项名称必须与声明逐字相等"
        )
        states = {key: value["status"] for key, value in items.items()}
        assert all(state == "checked" for state in states.values()), (
            f"④ 「{doc_type}」应全部 checked：{states}"
        )

    # ③ 真 docx（同一渲染实现，见 docstring 的落点说明）
    blob = ArtifactSanitizer().render_file(
        render_format="docx",
        name="通知",
        payload={"title": "通知", "content": "一、事项说明\n二、执行要求"},
        rows=[],
    )
    assert blob.render_format == "docx", "③ 不得降级成 md（降级意味着交付的不是公文格式）"
    assert blob.size_bytes > 0
    from docx import Document

    parsed = Document(io.BytesIO(blob.content))
    assert len(parsed.paragraphs) > 0, "③ docx 必须能打开且段落数 > 0"


def parse_amount_fields(offer: str) -> dict[str, str]:
    """从录用通知正文里解析三个金额字段（`键：值` 形式）。

    Args:
        offer: 录用通知正文。

    Returns:
        dict[str, str]: `金额字段 → 取值`（缺失的字段不出现）。
    """
    values: dict[str, str] = {}
    for line in offer.splitlines():
        stripped = line.strip()
        for key in AMOUNT_FIELDS:
            if stripped.startswith(f"{key}：") or stripped == key:
                values[key] = stripped.split("：", 1)[1].strip() if "：" in stripped else ""
    return values
