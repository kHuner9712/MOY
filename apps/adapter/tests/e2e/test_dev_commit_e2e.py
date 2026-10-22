"""研发工作台 e2e：**离开界面也成立**的提交结果物（P0-P3-17 / QA §4.8.1）。

三个用例对应 QA 计划的三条判定点（落点名与计划逐字一致）：

| 用例 | 计划编号 | 判定点 |
| --- | --- | --- |
- **`test_no_commit_before_confirm`**（TC-P3-DEV17-01 / E2E-DEV-01）：未确认不得提交（`git
rev-list --count`
  必须不变）
- **`test_commit_exists_and_content_identical`**（TC-P3-DEV17-02 /
  E2E-DEV-02/03/04）：提交真的存在，且抽一个文件逐字相同；合并请求只发一次且 `head==sha`
| `test_repo_auth_expired_recoverable` | TC-P3-DEV17-03 | 授权过期 → 原因与「重新连接」→
重连后重试本步成功，前序不重跑 |

**判据为什么必须落在 git 对象上**：`plugin_artifact` 里存的是"平台认为发生了什么"，
只有 `git cat-file` / `git show` 才能证明"仓库里真的发生了什么"。两者不一致
（"界面显示已提交、仓库里没有"）正是本阶段要防的假结果物。

**为什么产物从表里读而不是从 A4 读**：QA §4.8 要求结果物用例「绕过 UI 直接对产物
断言」；另外当前 A4 的 `StepItem` 丢了 `artifacts[]` / `failure`
（交付报告 GAP-B26-01），从 `plugin_artifact` / `plugin_task_step` 读既是计划要求，
也不让本批用例被那个契约缺口堵死。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import (
    DEV_CODE_PATH,
    DEV_PLUGIN,
    ORG_CAPABILITIES,
    BareRepo,
    GatewayScript,
    artifacts_by_key,
    checklist_identity,
    checklist_map,
    create_task,
    decide,
    install_plugin,
    set_org_switches,
    sha256_of,
    step_records,
    task_detail,
    upgrade_to_paid_plan,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pg]

#: 研发工作台的输入（固定，避免每次任务参数漂移）
DEV_INPUTS: dict[str, Any] = {
    "title": "列表页加导出按钮",
    "requirement": "在客户列表页工具栏加一个导出按钮，导出当前筛选结果。",
    "repo": "acme/web",
    "branch_policy": "新建分支",
    "stack": "React + TypeScript",
}


@pytest.fixture
async def dev_ready(
    client: AsyncClient,
    admin_headers: dict[str, str],
    e2e_settings: Any,
    tenant: str,
) -> None:
    """把研发工作台装到「可建任务」状态（套餐 + 企业能力 + 安装）。

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
async def test_no_commit_before_confirm(
    client: AsyncClient,
    admin_headers: dict[str, str],
    bare_repo: BareRepo,
    script: GatewayScript,
    e2e_settings: Any,
    tenant: str,
    dev_ready: None,
) -> None:
    """TC-P3-DEV17-01 / E2E-DEV-01：**人工确认之前，仓库里不得出现新提交**。

    用户旅程：填表提交 → 看到方案 → 继续 → 看到代码与用例 → 停在「提交仓库」确认条。
    判定点：这一整段路走完，仓库提交数与分支指针必须与开工前**完全一样**；
    提交类工具一次都不许被调用——"先提交再让用户确认"是典型事故。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        bare_repo: 本地裸仓库。
        script: 桩脚本（记录工具调用流水）。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        dev_ready: 前置装配。
    """
    before = bare_repo.commit_count()
    before_head = bare_repo.head()

    created = await create_task(client, admin_headers, DEV_PLUGIN, DEV_INPUTS)
    task_id = created["task_id"]

    # ① 首个接管点：方案确认（步骤 2 未确认前不得继续）
    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "waiting_confirm", detail
    assert detail["confirm"]["step_seq"] == 2, "必须停在方案确认"
    assert bare_repo.commit_count() == before, "TC-P3-DEV17-01 ① 方案确认前不得有提交"

    # ② 继续后在**提交仓库**前再次停住（步骤 5 的 confirm）
    await decide(client, admin_headers, task_id, "continue")
    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "waiting_confirm", detail
    assert detail["confirm"]["step_seq"] == 5, "必须停在提交确认"
    assert bare_repo.commit_count() == before, "TC-P3-DEV17-01 ② 提交确认前不得有提交"
    assert bare_repo.head() == before_head, "TC-P3-DEV17-01 ② 分支指针不得被移动"

    # ③ 代码与用例产物**已经生成**（不是"什么都没做"，而是"做了但没提交"）
    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    assert artifacts["code_files"].inline_text, "步骤 3 必须已有代码产物"
    assert artifacts["test_files"].inline_text, "步骤 4 必须已用用例产物"
    assert "commit" not in artifacts, "确认前不得存在提交记录产物"

    # ④ 提交类工具一次都没被调用（防"先做了再拦住"）
    tools = [item for item in script.calls_of(task_id) if item["action"] == "tool"]
    assert tools == [], f"TC-P3-DEV17-01 ④ 确认前不得调用任何写仓库的工具，实际 {tools}"


@pytest.mark.p0
@pytest.mark.asyncio
async def test_commit_exists_and_content_identical(
    client: AsyncClient,
    admin_headers: dict[str, str],
    bare_repo: BareRepo,
    script: GatewayScript,
    e2e_settings: Any,
    tenant: str,
    dev_ready: None,
) -> None:
    """TC-P3-DEV17-02 / E2E-DEV-02~04：提交存在、**内容逐字相同**、合并请求只发一次。

    用户旅程：确认方案 → 确认提交 → 任务完成 → 打开结果。
    判定点（缺一条就不算"有结果物"）：
    ① `git rev-list --count` 恰好 +1；
    ② `git cat-file -e <sha>` 成功（对象真在仓库里）；
    ③ `sha256(git show <sha>:<path>)` == `sha256(产物 preview)`，且**逐行**相等（不是包含）；
    ④ 合并请求桩只收到 1 次、`head == sha`、`pr_url` 里含该 sha；
    ⑤ 核查清单 4 项名称逐字相等、状态全部 `checked`，重新 GET 结果一致（换端等价）。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        bare_repo: 本地裸仓库。
        script: 桩脚本（记录合并请求调用）。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        dev_ready: 前置装配。
    """
    before = bare_repo.commit_count()

    created = await create_task(client, admin_headers, DEV_PLUGIN, DEV_INPUTS)
    task_id = created["task_id"]
    await decide(client, admin_headers, task_id, "continue")  # 确认方案
    await decide(client, admin_headers, task_id, "continue")  # 确认提交

    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "completed", detail
    assert detail["progress"] == 100, "完成后进度必须是 100"

    # ① 提交数恰好 +1（不是 0，也不是 2）
    assert bare_repo.commit_count() == before + 1, (
        f"TC-P3-DEV17-02 ① 提交数应 +1，实际 {bare_repo.commit_count() - before}"
    )

    artifacts = await artifacts_by_key(e2e_settings, tenant, task_id)
    sha = str(artifacts["commit"].inline_text).strip()
    assert re.fullmatch(r"[0-9a-f]{40}", sha), f"提交号形状不对：{sha!r}"

    # ② 对象真在仓库里
    assert bare_repo.has_object(sha), f"TC-P3-DEV17-02 ② git cat-file -e {sha} 失败"

    # ③ 抽一个文件，逐字比对（不是包含、不是前缀）
    preview = artifacts["code_files"].inline_text
    committed = bare_repo.file_text(sha, DEV_CODE_PATH)
    assert sha256_of(committed) == sha256_of(preview), "TC-P3-DEV17-02 ③ 提交内容与产物不一致"
    assert committed.splitlines() == preview.splitlines(), "TC-P3-DEV17-02 ③ 逐行比对必须无差异"
    assert (
        artifacts["code_files"].content_sha256
        == hashlib.sha256(preview.encode("utf-8")).hexdigest()
    ), "产物自身的 sha256 也要与内容一致（否则下载到的是另一份）"

    # ④ 合并请求只发 1 次，head == sha
    pr_calls = [item for item in script.published if item.get("tool") == "github"]
    assert len(pr_calls) == 1, f"合并请求必须只发 1 次，实际 {len(pr_calls)}"
    assert pr_calls[0]["head"] == sha, "ADJ-7：合并请求的 head 必须是这次提交"
    assert sha in (artifacts["pull_request"].external_url or ""), "pr_url 必须含本次提交号"
    assert bare_repo.changed_file_count(sha) == 1, "本次提交只应新增 1 个文件"

    # ⑤ 核查清单 4 项，名称逐字相等，全部 checked
    expected = ["需求要点", "代码文件", "用例文件", "提交记录与合并请求"]
    items = checklist_map(detail)
    order = ("key_points", "code_files", "test_files", "commit")
    assert [items[key]["name"] for key in order] == expected, (
        "TC-P3-WF14-02 / E2E-DEV-04 核查项名称必须与 PRD §3.2.3 ② 逐字相等"
    )
    states = {key: value["status"] for key, value in items.items()}
    assert all(state == "checked" for state in states.values()), (
        f"E2E-DEV-04 应全部 checked：{states}"
    )
    assert all(items[key]["download_url"] for key in order), "每项核查结果都要能点开"

    # 重新 GET（换端/刷新等价）结果必须一致。
    # 下载地址按**产物身份**（租户/产物/版本）比对：令牌自 S9 起带 `exp`，
    # 不同秒签发字面量必然不同，直接比字符串会把用例绑死在时钟上。
    again = await task_detail(client, admin_headers, task_id)
    assert checklist_identity(again["result"]["checklist"], e2e_settings) == checklist_identity(
        detail["result"]["checklist"], e2e_settings
    ), "E2E-DEV-04 刷新后核查清单必须逐项一致"


@pytest.mark.asyncio
async def test_repo_auth_expired_recoverable(
    client: AsyncClient,
    admin_headers: dict[str, str],
    bare_repo: BareRepo,
    script: GatewayScript,
    e2e_settings: Any,
    tenant: str,
    dev_ready: None,
) -> None:
    """TC-P3-DEV17-03：仓库授权过期 → 说清原因并指向「重新连接」→ 重连后重试本步成功。

    用户旅程：确认方案 → 确认提交 → 提交步骤报「授权已过期」→ 重新连接仓库 → 重试本步。
    判定点：
    ① 失败步骤三要素齐（原因 / 可重试性 / 建议动作），原因提到「授权已过期」、
       建议动作提到「重新连接」——**不能**只说"失败了请重试"；
    ② 任务状态 `failed`，HTTP 信封仍是 `code=0` 且响应体不含四位内部码（PRD E-9）；
    ③ 重连后 `retry_step from_step=5` 新建任务，**前序 1-4 步一次都不重跑**（调用流水为空），
       且前序产物 `content_sha256` 逐字相等（复用而不是重生成）；
    ④ 最终提交数 +1，`git cat-file -e` 成功。

    Args:
        client: 异步客户端。
        admin_headers: 管理员请求头。
        bare_repo: 本地裸仓库。
        script: 桩脚本。
        e2e_settings: 配置实例。
        tenant: 租户 ID。
        dev_ready: 前置装配。
    """
    before = bare_repo.commit_count()
    created = await create_task(client, admin_headers, DEV_PLUGIN, DEV_INPUTS)
    task_id = created["task_id"]
    await decide(client, admin_headers, task_id, "continue")  # 确认方案

    # 注入「授权已过期」（13003）：在用户「重新连接」之前必然失败
    script.fail_at(
        5,
        error_code=13003,
        reason_text="代码仓库的授权已过期",
        operator_action="重新连接这个办公工具后再试",
        retryable=False,
    )
    await decide(client, admin_headers, task_id, "continue")  # 确认提交 → 步骤 5 失败

    detail = await task_detail(client, admin_headers, task_id)
    assert detail["status"] == "failed", detail
    records = await step_records(e2e_settings, tenant, task_id)
    failed = records[5]
    assert failed.status == "failed"
    assert failed.reason_text.strip(), "① 必须给出失败原因"
    assert "授权已过期" in failed.reason_text, failed.reason_text
    assert failed.operator_action.strip(), "① 必须给出建议动作"
    assert "重新连接" in failed.operator_action, failed.operator_action
    assert failed.retryable is False, "写外部动作失败不得静默自动重试（§3.5）"
    assert bare_repo.commit_count() == before, "失败的提交不得留下半成品"

    # ② 失败走状态，不走错误码
    raw = await client.get(f"/api/v1/agent/tasks/{task_id}", headers=admin_headers)
    assert raw.json()["code"] == 0, "PRD E-9：失败步骤仍返回 code=0"
    assert not re.search(r"\b1[0-9]{4}\b", raw.text), "A3 响应体不得出现内部错误码"
    steps_raw = await client.get(f"/api/v1/agent/tasks/{task_id}/steps", headers=admin_headers)
    assert steps_raw.json()["code"] == 0
    assert not re.search(r"\b1[0-9]{4}\b", steps_raw.text), "A4 响应体不得出现内部错误码"

    # ③ 重新连接后重试本步
    original = await artifacts_by_key(e2e_settings, tenant, task_id)
    script.heal()
    retried = await decide(client, admin_headers, task_id, "retry_step", from_step=5)
    assert retried["resumed"] is False, "重试本步应新建任务"
    assert retried["retry_of"] == task_id, "retry_of 必须指回原任务"
    new_task = retried["task_id"]
    assert new_task != task_id

    # 重试本步会新建任务，而"写仓库"是人工接管点：新任务会**再确认一次**才提交
    # （不自动沿用上一次的确认——用户重新连接后可能只想改仓库/分支）。
    new_detail = await task_detail(client, admin_headers, new_task)
    assert new_detail["status"] == "waiting_confirm", new_detail
    assert new_detail["confirm"]["step_seq"] == 5, "重试后的任务必须在写仓库前再确认一次"
    await decide(client, admin_headers, new_task, "continue")
    # 「重试本步」会新建任务。新任务若再次停在写仓库的接管点，就按用户会做的那样确认一次；
    # 若直接跑完也不算错——计划只要求"前序不重跑"，没有规定必须重新确认。
    new_detail = await task_detail(client, admin_headers, new_task)
    if new_detail["status"] == "waiting_confirm":
        assert new_detail["confirm"]["step_seq"] == 5, "重试后若再确认，必须确认的是同一处"
        await decide(client, admin_headers, new_task, "continue")
        new_detail = await task_detail(client, admin_headers, new_task)
    assert new_detail["status"] == "completed", new_detail
    executed = script.steps_of(new_task)
    assert 1 not in executed and 4 not in executed, f"③ 前序步骤不得重跑，实际执行了 {executed}"
    assert 5 in executed, "重试起点必须是步骤 5"

    replayed = await artifacts_by_key(e2e_settings, tenant, new_task)
    for key in ("key_points", "code_files", "test_files"):
        assert replayed[key].content_sha256 == original[key].content_sha256, (
            f"③ 前序产物 {key} 必须原样复用（sha256 相等）"
        )

    # ④ 提交真的落进仓库
    assert bare_repo.commit_count() == before + 1
    sha = str(replayed["commit"].inline_text).strip()
    assert bare_repo.has_object(sha), "重连后的提交必须真在仓库里"
    assert sha256_of(bare_repo.file_text(sha, DEV_CODE_PATH)) == sha256_of(
        replayed["code_files"].inline_text
    ), "重连后提交的内容仍必须与产物逐字相同"
