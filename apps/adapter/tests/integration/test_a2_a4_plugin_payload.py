"""A2 任务列表合并与 A4 步骤载荷的**响应 JSON** 钉子（e2e 补交付撞出的真缺陷）。

本文件只钉两件"填了等于没填"的事，断言一律读**HTTP 响应体的 JSON**，
不断言模型对象（模型有字段 ≠ 前端拿得到值）：

1. **A2 合并**：`PageResult` 只有 `items` + `meta`，总数在 `meta.total` 上。
   曾经写成 `plugin_page.total + base_page.total`——租户只要有一条插件任务，
   `GET /agent/tasks` 就 `AttributeError` → 500，"我的任务"整页打不开。
2. **A4 载荷**：`StepItem` 曾经只有 7 个字段，服务层却在构造时塞了
   `artifacts=` / `failure=`——pydantic 默认忽略未声明字段（**不报错**），
   于是前端永远拿不到步骤产物与失败原因（PRD E-9 要求就地展示、不弹错）。

为什么走 HTTP 而不直接调服务：`response_model` 才是真正决定"前端看到什么"的
那一层，绕过它就等于把这条缺陷的现场删掉了。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import AuthContext
from app.schemas.agent import CreateTaskRequest
from app.schemas.task import RetryTaskRequest
from app.services.agent_service import get_agent_service
from tests.conftest import TEST_USER_ID
from tests.unit.task_helpers import EchoGateway, build_service, load_manifest

pytestmark = pytest.mark.integration

#: 带两处人工接管点（第 2 步方案确认、第 5 步提交前确认）的标杆插件
PLUGIN_ID = "plg_dev_workbench"

#: A2 列表路径
TASKS_PATH = "/api/v1/agent/tasks"


def _tenant(settings: Settings) -> str:
    """取本次请求真正生效的租户。

    **不能用令牌里的 `tenant_id`**：私有化模式（`deploy_mode=private`）下
    适配层忽略 `X-Tenant-Id` 与令牌声明，请求上下文恒为
    `settings.default_tenant_id`（`core/security.resolve_tenant_id`）。
    按令牌租户造数据、接口按默认租户查，任务就会"不存在"。

    Args:
        settings: 测试配置。

    Returns:
        str: 生效租户 ID。
    """
    return settings.default_tenant_id


def _auth(settings: Settings) -> AuthContext:
    """构造与请求上下文同企业、同用户的鉴权上下文。

    Args:
        settings: 测试配置。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id=TEST_USER_ID, tenant_id=_tenant(settings))


def _request() -> CreateTaskRequest:
    """构造 A1 请求（插件分支，输入取自该插件的声明）。

    Returns:
        CreateTaskRequest: 请求体。
    """
    return CreateTaskRequest(
        conversation_id="conv_a2_a4",
        inputs={
            "title": "登录页改版",
            "requirement": "把登录页换成新版设计稿的实现",
            "repo": "acme/web",
            "branch_policy": "新建分支",
        },
        plugin_id=PLUGIN_ID,
    )


def _run(coro: Any) -> Any:
    """同步执行协程（本文件不引 pytest-asyncio，与其他插件任务用例一致）。

    Args:
        coro: 协程对象。

    Returns:
        Any: 协程返回值。
    """
    return asyncio.run(coro)


def _inject(
    monkeypatch: pytest.MonkeyPatch, gateway: EchoGateway | None = None
) -> tuple[Any, Any, Any]:
    """把内存版 `TaskService` 装进端点（`get_task_service()` 的单例位）。

    替换的是**外部依赖装配**，被测逻辑（状态机、产物落库、载荷组装）全部真实。

    Args:
        monkeypatch: pytest 补丁器。
        gateway: 原语替身（缺省全成功）。

    Returns:
        tuple: `(服务, 任务仓储, 声明)`。
    """
    import app.business.task_service as task_service_module

    manifest = load_manifest(PLUGIN_ID)
    service, tasks, _artifacts, _usage = build_service(manifest, gateway=gateway)
    monkeypatch.setattr(task_service_module, "_service", service)
    return service, tasks, manifest


def _drive_to_end(
    service: Any, task_id: str, tasks: Any, settings: Settings, max_rounds: int = 6
) -> str:
    """逐个人工接管点确认，直到任务离开 `waiting_confirm`（或走完 `max_rounds` 轮）。

    Args:
        service: 任务服务。
        task_id: 任务 ID。
        tasks: 任务仓储。
        settings: 测试配置（取生效租户）。
        max_rounds: 最多确认轮数（防死循环）。

    Returns:
        str: 任务最终状态。
    """
    status = ""
    for _ in range(max_rounds):
        record = _run(tasks.get_task(_tenant(settings), task_id))
        assert record is not None, "任务必须真实落库"
        status = record.status
        if status != "waiting_confirm":
            break
        _run(service.retry(task_id, RetryTaskRequest(decision="continue"), _auth(settings)))
    return status


def test_a2_list_merges_plugin_and_base_tasks_without_500(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有插件任务 + 有底座任务时，`GET /agent/tasks` 200 且 `meta.total` 是两侧之和。"""
    service, _tasks, _manifest = _inject(monkeypatch)
    plugin_task = _run(service.create_plugin_task(_request(), _auth(settings)))

    # 底座通道的任务（阶段一实现）与插件任务并存：合并逻辑必须两边都数。
    # 走公开入口创建（桩模式不产生下游请求），不直接改服务内部字典。
    base_task = _run(
        get_agent_service().create_task(
            CreateTaskRequest(conversation_id="conv_base", goal="整理本周会议纪要"),
            _auth(settings),
        )
    )

    response = client.get(f"{TASKS_PATH}?page=1&page_size=20", headers=auth_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == 0
    data = payload["data"]
    task_ids = {item["task_id"] for item in data["items"]}
    assert plugin_task.task_id in task_ids, "插件任务必须出现在合并结果里"
    assert base_task.task_id in task_ids, "底座任务不得被合并逻辑吞掉"
    assert data["meta"]["total"] == 2, f"总数应为两侧之和，实际 {data['meta']['total']}"


def test_a4_steps_payload_carries_step_artifacts(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /agent/tasks/{id}/steps` 的 `steps[].artifacts[]` 带 `content_sha256` 等真实值。"""
    service, tasks, _manifest = _inject(monkeypatch)
    created = _run(service.create_plugin_task(_request(), _auth(settings)))
    final_status = _drive_to_end(service, created.task_id, tasks, settings)
    assert final_status == "completed", f"任务应走完，实际 {final_status}"

    response = client.get(f"{TASKS_PATH}/{created.task_id}/steps", headers=auth_headers)

    assert response.status_code == 200, response.text
    steps = response.json()["data"]["steps"]
    assert steps, "步骤列表不得为空"
    with_artifacts = [step for step in steps if step["artifacts"]]
    assert with_artifacts, f"步骤产物必须出现在响应 JSON 里，实际 steps={steps}"

    first_step = with_artifacts[0]
    first = first_step["artifacts"][0]
    # 证据：真的从响应 JSON 里读到了内容摘要（不是"模型里有这个字段"）
    assert first["output_key"], f"产物键为空：{first}"
    assert len(first["content_sha256"]) == 64, f"内容摘要不是 sha256：{first}"
    assert first["download_url"], f"下载地址必须走平台代理：{first}"
    # 交付证据：把 JSON 里读到的真实值贴出来（`-s` 可见）
    print(
        f"[A4 证据] step={first_step['sequence']} artifact output_key={first['output_key']} "
        f"content_sha256={first['content_sha256']} download_url={first['download_url']}"
    )


def test_a3_result_artifacts_carry_output_key_and_sha256(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /agent/tasks/{id}` 的 `result.artifacts[]` 带 `output_key` 与 `content_sha256`。

    架构文档 §5 要求 A3 的产物可被前端判断"是不是同一份文件"：没有 `content_sha256`
    就只剩"下载链接变了"，而链接里带令牌、每次刷新都变——等于没有任何判据。
    """
    service, tasks, _manifest = _inject(monkeypatch)
    created = _run(service.create_plugin_task(_request(), _auth(settings)))
    final_status = _drive_to_end(service, created.task_id, tasks, settings)
    assert final_status == "completed", f"任务应走完，实际 {final_status}"

    response = client.get(f"{TASKS_PATH}/{created.task_id}", headers=auth_headers)

    assert response.status_code == 200, response.text
    artifacts = response.json()["data"]["result"]["artifacts"]
    assert artifacts, f"结果产物不得为空，实际 {response.json()['data']['result']}"
    for artifact in artifacts:
        assert artifact["output_key"], f"产物键为空：{artifact}"
        assert len(artifact["content_sha256"]) == 64, f"内容摘要不是 sha256：{artifact}"

    print(  # 交付证据
        f"[A3 证据] artifacts[0] output_key={artifacts[0]['output_key']} "
        f"content_sha256={artifacts[0]['content_sha256']}"
    )


def test_a4_steps_payload_carries_step_failure(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /agent/tasks/{id}/steps` 的失败步带 `failure{retryable}`（就地展示、不弹错）。"""
    manifest = load_manifest(PLUGIN_ID)
    abort_seq = next(
        int(spec.seq) for spec in manifest.steps if spec.on_failure == "abort" and spec.seq > 1
    )
    service, tasks, _manifest = _inject(monkeypatch, EchoGateway(fail_at=abort_seq))
    created = _run(service.create_plugin_task(_request(), _auth(settings)))
    final_status = _drive_to_end(service, created.task_id, tasks, settings)
    assert final_status == "failed", f"第 {abort_seq} 步失败应 abort，实际 {final_status}"

    response = client.get(f"{TASKS_PATH}/{created.task_id}/steps", headers=auth_headers)

    assert response.status_code == 200, response.text
    steps = response.json()["data"]["steps"]
    failed = [step for step in steps if step["sequence"] == abort_seq]
    assert failed, f"失败步必须出现在响应里，实际 steps={steps}"
    failure = failed[0]["failure"]
    assert failure is not None, f"失败步必须带 failure 对象，实际 {failed[0]}"
    assert failure["retryable"] is True, f"该步声明可重试，实际 {failure}"
    assert failure["reason_text"], f"原因文案不能为空：{failure}"
    assert failure["operator_action"], f"建议动作不能为空：{failure}"
    # 交付证据：把 JSON 里读到的真实值贴出来（`-s` 可见）
    print(
        f"[A4 证据] step={failed[0]['sequence']} failure.retryable={failure['retryable']} "
        f"reason_text={failure['reason_text']} operator_action={failure['operator_action']}"
    )
