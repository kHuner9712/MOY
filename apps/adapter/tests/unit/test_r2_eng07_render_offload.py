"""R2-ENG-07：同步渲染必须移出事件循环。

## 这一组用例在防什么风险

docx / md / csv 渲染是**同步 CPU 密集**操作（docx 要走 zip + XML 打包，大文档
单次可达数百毫秒）。修复前它在 `async def` 里被**直接调用**：

- `app/business/task/runner.py:1170`（WF2 编辑产物后重渲染）；
- `app/services/action_gateway.py:331`（插件 `render_file` 原语）。

`asyncio` 是单线程事件循环，一次同步阻塞会让**整个进程**停摆：同实例上的
`/healthz` 探针、其他用户的对话流、后台轮询一起排队；k8s 甚至可能因为探针
连续超时把**健康的** Pod 重启掉——故障现象与根因完全对不上号。

这里钉住的是「渲染发生在**非事件循环线程**」，而不是"渲染变快了"：
`asyncio.to_thread` 不提升吞吐，它只是把阻塞从关键路径上挪开。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from app.business.task.manifest import TaskManifestResolver  # noqa: F401 - 保持导入面稳定
from app.core.config import Settings
from app.core.security import AuthContext
from app.plugins.actions import RunContext
from app.plugins.sanitizer import ArtifactSanitizer
from app.schemas.task import ArtifactEditRequest
from app.services.action_gateway import ServicesActionGateway
from tests.unit.task_helpers import auth, build_service, load_manifest

pytestmark = pytest.mark.unit

#: 带人工接管点的插件（用它跑出一个真正带产物的任务）
PLUGIN_ID = "plg_dev_workbench"


def _run(coro: object) -> object:
    """同步执行协程（复用 `test_artifact_edit` 的既有约定）。

    Args:
        coro: 协程对象。

    Returns:
        object: 返回值。
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def _ctx() -> RunContext:
    """构造单步执行上下文。

    Returns:
        RunContext: 执行上下文。
    """
    return RunContext(
        tenant_id="tnt_test",
        task_id="task_01HTEST",
        step_seq=1,
        plugin_id="plg_test",
        installation_id="inst_01HTEST",
        initiator_id="usr_writer",
        trace_id="trace_01",
        granted=frozenset({"personal.knowledge.read", "personal.tool.use"}),
        settings={},
        inputs={},
        outputs={},
        auth=AuthContext(user_id="usr_writer", tenant_id="tnt_test"),
    )


# ===========================================================================
# 1) 插件原语 render_file：渲染必须在线程池里跑
# ===========================================================================
async def test_action_gateway_render_runs_off_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`render_file` 原语的渲染发生在**非主线程**（事件循环线程之外）。"""
    gateway = ServicesActionGateway(Settings(env="test", mock_mode=True))
    loop_thread = threading.current_thread().name
    seen: list[str] = []
    original = ArtifactSanitizer.render_file

    def spy(self: ArtifactSanitizer, **kwargs: Any) -> Any:
        """记录执行线程后委派真实实现。"""
        seen.append(threading.current_thread().name)
        return original(self, **kwargs)

    monkeypatch.setattr(ArtifactSanitizer, "render_file", spy)

    result = await gateway.invoke(
        _ctx(), "render_file", {"render_format": "md", "title": "导出", "produces": ["file"]}
    )

    assert result.status == "ok", "渲染本身必须仍然成功（只挪线程，不改语义）"
    assert seen, "必须真的发生过一次渲染"
    assert seen[0] != loop_thread, (
        "渲染必须在线程池里执行：直接调用会把事件循环钉住数百毫秒，"
        "同进程的 /healthz 与其余请求一起排队"
    )


# ===========================================================================
# 2) WF2 编辑产物后的重渲染：同样必须在线程池里跑
# ===========================================================================
def test_artifact_edit_render_runs_off_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`TaskRunner.edit_artifact` 的重渲染发生在**非主线程**。"""
    service, _tasks, _artifacts, _usage = build_service(load_manifest(PLUGIN_ID))
    created = _run(
        service.create_plugin_task(
            _create_request(),
            auth(),
        )
    )
    task_id = created.task_id  # type: ignore[attr-defined]
    items = _run(_artifacts.list_by_task("tnt_test", task_id))
    assert items, "第 1 步应产出至少一个产物"
    artifact_id = items[0].artifact_id

    loop_thread = threading.current_thread().name
    seen: list[str] = []
    original = ArtifactSanitizer.render_file

    def spy(self: ArtifactSanitizer, **kwargs: Any) -> Any:
        """记录执行线程后委派真实实现。"""
        seen.append(threading.current_thread().name)
        return original(self, **kwargs)

    monkeypatch.setattr(ArtifactSanitizer, "render_file", spy)

    edited = _run(
        service.edit_artifact(
            task_id,
            artifact_id,
            # 显式要求 `md` 重渲染：不传时是否渲染取决于产物类型（doc/draft），
            # 而本用例要钉的是"渲染发生在哪个线程"，必须保证渲染真的被执行。
            ArtifactEditRequest(content="改后的正文，联系电话 13912345678", render_format="md"),
            auth(),
        )
    )

    assert edited.version >= 1  # type: ignore[attr-defined]
    assert seen, "编辑产物并指定 render_format 时必须触发一次重渲染"
    assert seen[0] != loop_thread, "重渲染必须在线程池里执行"


def _create_request() -> Any:
    """构造一个可推进到接管点的插件任务创建请求。

    Returns:
        CreateTaskRequest: 创建请求。
    """
    from app.schemas.agent import CreateTaskRequest

    return CreateTaskRequest(
        conversation_id="conv_1",
        inputs={
            "title": "登录页改版",
            "requirement": "换成新版设计稿",
            "repo": "acme/web",
            "branch_policy": "新建分支",
        },
        plugin_id=PLUGIN_ID,
    )
