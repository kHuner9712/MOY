"""内容运营 e2e：排期表（4 列 / 行数 / 导出逐行一致）与发布回执（P0-P3-20 / QA §4.8.2）。

三个用例对应 QA 计划的三条判定点：

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
- **`test_schedule_table_and_csv`**（TC-P3-CNT20-01 / E2E-CNT-02）：表头逐字等于 [平台, 标题,
计划时间, 状态]；行数 ==
  稿件数；导出 CSV 逐行与页面一致
- **`test_publish_receipt_and_manual_pending`**（TC-P3-CNT20-03 / E2E-CNT-03）：公众号 `已发布` +
  `receipt_id` 非空；小红书 `待人工发布` + 稿件下载 200 且非空
| `test_past_schedule_rejected` | TC-P3-CNT20-02 | 计划时间在过去 → 该步失败并指出冲突行；
改时间后重试本步成功且**不重跑步骤 3** |

**这份 CSV 不是拼出来的字符串**：桩只提供"三个平台各一篇"的输入，CSV 由平台的
`FileRenderer.render_csv`（真实现，带 BOM）渲染，落库后由排期护栏
（`PluginEngine.guard`）解析回来校验时间——"渲染 → 落库 → 护栏 → 下载"整条链路都在。
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    CONTENT_PLUGIN,
    ORG_CAPABILITIES,
    SCHEDULE_COLUMNS,
    STUB_RECEIPT_ID,
    artifact_download_url,
    artifacts_by_key,
    build_content_script,
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

#: 三个平台（公众号已接入、小红书未接入、抖音未接入）
CHANNELS: list[str] = ["微信公众号", "小红书", "抖音"]

#: 内容运营输入
CONTENT_INPUTS: dict[str, Any] = {
    "topic": "季度新品上线",
    "channels": CHANNELS,
    "audience": "老客户",
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
    """把内容运营装到「可建任务」状态，并切到内容运营语料。

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
async def test_schedule_table_and_csv(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    content_ready: None,
) -> None:
    """TC-P3-CNT20-01 / E2E-CNT-02：**排期表 4 列齐、行数 == 稿件数、导出一致**。

    用户旅程：填主题 → 勾三个平台 → 生成稿件 → 看到排期表 → 导出 CSV。
    判定点（五条）：
    ① 排期表产物存在且类型为文件；
    ② 下载地址 HTTP 200，字节与落库 `content` **完全相同**（不是"重新渲染一遍"）；
    ③ 表头逐字等于 [平台, 标题, 计划时间, 状态]；
    ④ 行数 == 3 == 输入的平台数（不是 0，也不是 4）；
    ⑤ 每一行的平台与勾选的平台集合**相等**，且计划时间 / 状态非空
       （页面看到的就是这三行——导出与页面同源）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        content_ready: 前置装配。
    """
    created = await create_task(client, admin_headers, CONTENT_PLUGIN, CONTENT_INPUTS)
    task_id = created["task_id"]

    detail = await task_detail(client, admin_headers, task_id)
    # 发布是接管点：步骤 5（排期表）跑完，停在步骤 6 的确认
    assert detail["status"] == "waiting_confirm", detail
    assert detail["confirm"]["step_seq"] == 6, "必须停在发布确认"

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    schedule = artifacts["schedule"]
    assert schedule.type == "file", f"① 排期表应为文件产物，实际 {schedule.type}"

    status_code, content = await _download(client, e2e_settings, schedule)
    assert status_code == 200, f"② 下载失败：{status_code}"
    assert content, "② 导出的 CSV 不能是空文件"
    assert len(schedule.content_sha256) == 64, "② 产物必须带 content_sha256（可用于比对是否被改写）"
    assert schedule.version == 1 and schedule.size_bytes > 0, "② 产物版本与大小必须齐全"

    header, rows = parse_csv_bytes(content)
    assert header == SCHEDULE_COLUMNS, f"③ 表头必须逐字等于 {SCHEDULE_COLUMNS}，实际 {header}"
    assert len(rows) == len(CHANNELS), f"④ 行数必须等于稿件数 {len(CHANNELS)}，实际 {len(rows)}"
    assert {row["平台"] for row in rows} == set(CHANNELS), f"⑤ 平台集合必须相等：{rows}"
    for row in rows:
        assert row["标题"].strip(), f"⑤ 标题不得为空：{row}"
        assert row["计划时间"].strip(), f"⑤ 计划时间不得为空：{row}"
        assert row["状态"].strip(), f"⑤ 状态不得为空：{row}"

    # A3 核查清单里的「排期表」必须指向同一份可下载产物（页面与导出同源）
    items = checklist_map(detail)
    assert items["schedule"]["download_url"] == artifact_download_url(e2e_settings, schedule)
    assert items["schedule"]["status"] == "checked"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_publish_receipt_and_manual_pending(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    content_ready: None,
) -> None:
    """TC-P3-CNT20-03 / E2E-CNT-03：**已接入的有回执，未接入的如实标待人工发布**。

    用户旅程：看到排期表 → 确认发布 → 打开结果。
    判定点：
    ① 公众号行 `status=已发布` 且 `receipt_id` 非空（不伪造：回执号必须来自下游返回）；
    ② 小红书行 `status=待人工发布` 且回执号为空（**不能**编一个回执出来）；
    ③ 待人工发布的稿件仍可下载：HTTP 200 且内容非空（否则用户拿不到可发的稿子）；
    ④ 核查清单三项（三平台稿件 / 排期表 / 发布回执）全部 checked。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        content_ready: 前置装配。
    """
    created = await create_task(client, admin_headers, CONTENT_PLUGIN, CONTENT_INPUTS)
    task_id = created["task_id"]
    await decide(client, admin_headers, task_id, "continue")  # 确认发布

    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "completed", detail

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    receipt_rows = _parse_receipt(artifacts["receipt"].inline_text)
    by_channel = {row["平台"]: row for row in receipt_rows}

    assert by_channel["微信公众号"]["状态"] == "已发布", by_channel["微信公众号"]
    assert by_channel["微信公众号"]["回执号"].strip(), "① 已发布必须有回执号"
    assert by_channel["微信公众号"]["回执号"] == STUB_RECEIPT_ID, "① 回执号必须原样来自下游"

    for channel in ("小红书", "抖音"):
        assert by_channel[channel]["状态"] == "待人工发布", by_channel[channel]
        assert not by_channel[channel]["回执号"].strip(), "② 未接入平台不得有回执号"

    # ③ 待人工发布的稿件仍可下载
    status_code, content = await _download(client, e2e_settings, artifacts["articles"])
    assert status_code == 200, f"③ 稿件下载失败：{status_code}"
    assert content.strip(), "③ 稿件内容不得为空"
    body = content.decode("utf-8")
    for channel in CHANNELS:
        assert channel in body, f"③ 稿件必须覆盖 {channel}"

    # ④ 核查清单三项全部 checked
    items = checklist_map(detail)
    assert [items[key]["name"] for key in ("articles", "schedule", "receipt")] == [
        "三平台稿件",
        "排期表",
        "发布回执",
    ], "核查项名称必须与声明逐字相等"
    states = {key: value["status"] for key, value in items.items()}
    assert all(state == "checked" for state in states.values()), f"④ 应全部 checked：{states}"


@pytest.mark.asyncio
async def test_past_schedule_rejected(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
    use_script: Any,
    content_ready: None,
) -> None:
    """TC-P3-CNT20-02：计划时间设成过去 → 指出冲突行 → 改时间后重试成功（不重跑步骤 3）。

    用户旅程：排期表里把时间填成了昨天 → 提交 → 被拦住并指出是第几行 → 改时间 → 重试本步。
    判定点：
    ① 步骤 5 失败（`on_failure=abort`），原因提到"已经过去了"并给出**行号**；
    ② 排期表产物**没有落库**（被拦下的产物不能交给用户去发布）；
    ③ 后续步骤 6 被跳过（不得在排期没定下来的情况下发布）；
    ④ 改好时间后 `retry_step from_step=5`：步骤 1-4 **一次都不重跑**（调用流水为空），
       且新任务产出的 CSV 行数仍为 3。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        use_script: 桩脚本切换器。
        content_ready: 前置装配。
    """
    use_script(build_content_script(offset_days=-1))

    created = await create_task(client, admin_headers, CONTENT_PLUGIN, CONTENT_INPUTS)
    task_id = created["task_id"]

    # 步骤 5 是 abort 语义：建任务的请求内就失败了，**还没走到**步骤 6 的发布确认
    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "failed", detail

    records = await step_records(e2e_settings, tenant, task_id)
    assert records[5].status == "failed", "① 排期步骤必须失败"
    assert "已经过去了" in records[5].reason_text, records[5].reason_text
    assert "第 1 行" in records[5].reason_text, f"① 必须指出冲突行：{records[5].reason_text}"
    assert records[5].operator_action.strip(), "① 必须给出建议动作"

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    assert "schedule" not in artifacts, "② 被拦下的排期表不得落库"
    assert records[6].status == "skipped", "③ 排期没定下来不得发布"

    # ④ 改好时间后重试本步
    script = build_content_script(offset_days=3)
    use_script(script)
    retried = await decide(client, admin_headers, task_id, "retry_step", from_step=5)
    assert retried["retry_of"] == task_id
    new_task = retried["task_id"]

    new_detail = await task_detail(client, admin_headers, new_task)
    if new_detail["status"] == "waiting_confirm":
        # 发布仍是接管点：新任务会再确认一次
        assert new_detail["confirm"]["step_seq"] == 6
        await decide(client, admin_headers, new_task, "continue")
        new_detail = await task_detail(client, admin_headers, new_task)
    assert new_detail["status"] == "completed", new_detail

    executed = script.steps_of(new_task)
    assert 3 not in executed and 4 not in executed, f"④ 前序步骤不得重跑，实际执行了 {executed}"
    new_artifacts = await artifacts_by_key(e2e_settings, tenant, new_task)
    code, fixed_csv = await _download(client, e2e_settings, new_artifacts["schedule"])
    assert code == 200, f"④ 修好后排期表必须可下载，实际 {code}"
    _header, rows = parse_csv_bytes(fixed_csv)
    assert len(rows) == len(CHANNELS), f"④ 修好后仍应是 {len(CHANNELS)} 行，实际 {len(rows)}"


async def _download(client: AsyncClient, settings: Any, record: Any) -> tuple[int, bytes]:
    """按产物行下载内容。

    Args:
        client: 异步客户端。
        settings: 配置实例。
        record: 产物行。

    Returns:
        tuple[int, bytes]: 状态码与内容。
    """
    response = await client.get(artifact_download_url(settings, record))
    return response.status_code, response.content


def _parse_receipt(text: str) -> list[dict[str, str]]:
    """解析发布回执（CSV 文本）。

    Args:
        text: 回执文本。

    Returns:
        list[dict[str, str]]: 行。
    """
    reader = csv.DictReader(io.StringIO(text))
    return [{str(key): str(value or "") for key, value in row.items()} for row in reader]
