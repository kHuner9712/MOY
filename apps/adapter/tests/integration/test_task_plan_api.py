"""通用任务计划生成 → 确认（矩阵第 2 项）· HTTP 契约钉子。

为什么走 HTTP：`response_model` 才是决定"前端拿得到什么"的那一层。
本文件断言**响应 JSON**里的 `plan` 结构、编辑版本流转、确认幂等，以及
"确认前零执行 / 取消勾选的步骤确实不跑"。

计划默认关闭的行为不在这里验（服务层单测已钉），这里只验开关打开后的真实契约。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.plugins.schema import PluginManifest
from tests.unit.task_helpers import (
    EchoGateway,
    InMemoryTaskStore,
    TaskService,
    build_service,
    fixture_manifest,
)

pytestmark = pytest.mark.integration

PLUGIN_ID = "plg_fixture_case"
TASKS_PATH = "/api/v1/agent/tasks"


def _run(coro: object) -> object:
    """同步执行协程。

    Args:
        coro: 协程对象。

    Returns:
        object: 返回值。
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def _manifest() -> PluginManifest:
    """计划专用声明：第 2 步换成非对外发送的工具操作（避免额外人工确认）。

    Returns:
        PluginManifest: 声明对象。
    """
    return fixture_manifest(
        steps=[
            {
                "seq": 1,
                "title": "生成稿件",
                "action": "compose",
                "produces": ["draft"],
                "on_failure": "continue",
            },
            {
                "seq": 2,
                "title": "创建日程",
                "action": "tool",
                "tool_type": "feishu",
                "operation": "create_calendar_event",
                "produces": [],
                "needs": ["draft"],
                "on_failure": "abort",
            },
        ]
    )


def _inject(
    monkeypatch: pytest.MonkeyPatch, gateway: EchoGateway
) -> tuple[TaskService, InMemoryTaskStore]:
    """把开启计划的内存服务装进端点。

    Args:
        monkeypatch: 补丁器。
        gateway: 原语替身。

    Returns:
        tuple: `(service, store)`。
    """
    import app.business.task_service as task_service_module

    service, tasks, _artifacts, _usage = build_service(
        _manifest(), gateway=gateway, task_plan_enabled=True, task_plan_timeout_seconds=900
    )
    monkeypatch.setattr(task_service_module, "_service", service)
    return service, tasks


def _headers(auth_headers: dict[str, str], key: str) -> dict[str, str]:
    """带幂等键的请求头。

    Args:
        auth_headers: 鉴权头。
        key: 幂等键。

    Returns:
        dict[str, str]: 合并后的请求头。
    """
    return {**auth_headers, "Idempotency-Key": key}


def test_plan_http_contract_edit_then_confirm(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建 → 读计划 → 编辑（版本冲突）→ 确认（幂等）全部走 HTTP。"""
    gateway = EchoGateway()
    _inject(monkeypatch, gateway)

    created = client.post(
        TASKS_PATH,
        headers=_headers(auth_headers, "idem-plan-create"),
        json={
            "conversation_id": "conv_plan_api",
            "inputs": {"topic": "登录页改版"},
            "plugin_id": PLUGIN_ID,
            "plan_first": True,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()["data"]
    assert body["status"] == "waiting_plan"
    task_id = body["task_id"]
    assert gateway.calls == [], "确认前不得执行任何步骤"

    detail = client.get(f"{TASKS_PATH}/{task_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["plan_version"] == 1
    assert data["plan_expires_at"].endswith("Z")
    assert data["plan"]["steps"][1]["tool_type"] == "feishu"
    assert data["plan"]["steps"][1]["tool_operation"] == "create_calendar_event"
    assert data["plan"]["steps"][0]["skippable"] is False
    assert data["plan"]["steps"][1]["skippable"] is True

    stale = client.put(
        f"{TASKS_PATH}/{task_id}/plan",
        headers=_headers(auth_headers, "idem-plan-stale"),
        json={"plan_version": 0, "steps": [{"seq": 2, "selected": False}]},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == 20005

    edited = client.put(
        f"{TASKS_PATH}/{task_id}/plan",
        headers=_headers(auth_headers, "idem-plan-edit"),
        json={"plan_version": 1, "steps": [{"seq": 2, "selected": False}]},
    )
    assert edited.status_code == 200, edited.text
    plan = edited.json()["data"]
    assert plan["version"] == 2
    assert plan["steps"][1]["selected"] is False

    confirmed = client.post(
        f"{TASKS_PATH}/{task_id}/plan/confirm",
        headers=_headers(auth_headers, "idem-plan-confirm"),
        json={"plan_version": 2},
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["data"]
    assert result["status"] == "completed"
    assert result["already_confirmed"] is False
    assert gateway.calls == [("compose", 1)], "被取消勾选的步骤不得执行"

    again = client.post(
        f"{TASKS_PATH}/{task_id}/plan/confirm",
        headers=_headers(auth_headers, "idem-plan-confirm-2"),
        json={"plan_version": 2},
    )
    assert again.status_code == 200, again.text
    replay = again.json()["data"]
    assert replay["already_confirmed"] is True
    assert gateway.calls == [("compose", 1)], "重复确认不得二次执行"


def test_plan_endpoint_rejects_when_feature_off(
    client: TestClient,
    auth_headers: dict[str, str],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关关闭时：显式 `plan_first=true` 被 20001 拒绝，存量路径不变。"""
    import app.business.task_service as task_service_module

    service, _tasks, _artifacts, _usage = build_service(
        _manifest(), gateway=EchoGateway(), task_plan_enabled=False
    )
    monkeypatch.setattr(task_service_module, "_service", service)

    response = client.post(
        TASKS_PATH,
        headers=_headers(auth_headers, "idem-plan-off"),
        json={
            "conversation_id": "conv_plan_off",
            "inputs": {"topic": "登录页改版"},
            "plugin_id": PLUGIN_ID,
            "plan_first": True,
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == 20001
