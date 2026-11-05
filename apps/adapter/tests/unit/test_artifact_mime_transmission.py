"""渲染器 MIME 透传的端到端钉子（2026-11-05 B2 的存量 bug 回归）。

**这个文件存在的原因**：`runner._save_artifacts` 曾把新建版本与新建产物行的
`content_type` 都硬编码成 `text/plain; charset=utf-8`，而 `ArtifactDraft` 里根本
没有 MIME 字段。后果是**插件渲染的 docx 落库成 text/plain**：字节是真 docx，
但下载响应的 `Content-Type` 是文本，浏览器与客户端会按文本处理。

**为什么不能只测渲染器**：渲染器一直是对的（`ArtifactBlob.content_type` 正确），
错在"从渲染器到仓储层"这一段被丢掉了。因此判据必须落在**落库后的产物行**上，
否则修不修都绿——这正是当初能漏过去的原因。

**为什么不只测内存后端**：PG 分支 `_to_artifact` 会重新归一化 MIME，只测内存
等于少测一半路径；PG 覆盖由 `test_artifact_content_pg.py` 在 `--run-pg` 下补。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.db.repositories.artifact_repo import EXTENSION_CONTENT_TYPES
from app.plugins.actions import ActionResult, ArtifactDraft
from app.plugins.renderer import FileRenderer
from app.schemas.agent import CreateTaskRequest
from app.schemas.task import RetryTaskRequest
from tests.unit.task_helpers import TENANT, auth, build_service, load_manifest

pytestmark = pytest.mark.unit

#: 声明里有 `render_file` docx 的插件（第 6 步只有 `doc_type` 是通知类才执行）
PLUGIN_ID = "plg_hr_admin"

#: 触发公文渲染所需的输入（`doc_type` 必须落在步骤 `when` 的枚举里）
INPUTS: dict[str, Any] = {
    "position": "前端工程师",
    "department": "研发部",
    "duties": "负责前端页面开发与维护",
    "doc_type": "通知",
    "doc_points": "设备采购安排",
}


class _DocxGateway:
    """把 `render_file` 交给真实渲染器、其它原语走替身的网关。

    Attributes:
        calls: 每次调用的 `(action, step_seq)` 记录。
    """

    def __init__(self) -> None:
        """初始化。"""
        self.calls: list[tuple[str, int]] = []

    def resolve(self, action: str) -> str:
        """解析原语。

        Args:
            action: 原语名。

        Returns:
            str: 固定标识。
        """
        return f"test.{action}"

    async def invoke(self, ctx: Any, action: str, params: dict[str, Any]) -> ActionResult:
        """执行原语替身（仅 `render_file` 走真实渲染器）。

        Args:
            ctx: 执行上下文。
            action: 原语名。
            params: 原语参数。

        Returns:
            ActionResult: 成功产物。
        """
        self.calls.append((action, ctx.step_seq))
        if action == "render_file":
            blob = FileRenderer().render(
                render_format=str(params.get("render_format") or "md"),
                name="行政公文",
                payload={"title": "关于设备采购的通知", "content": "现将有关事项通知如下。"},
            )
            return ActionResult(
                status="ok",
                artifacts=(
                    ArtifactDraft(
                        output_key="document",
                        type="file",
                        name="行政公文",
                        content=blob.content,
                        content_type=blob.content_type,
                        size_bytes=blob.size_bytes,
                    ),
                ),
            )
        drafts = tuple(
            ArtifactDraft(
                output_key=str(key),
                type="text",
                name=str(key),
                inline_text=f"第 {ctx.step_seq} 步产出 {key}",
                preview_text=f"{key} 预览",
            )
            for key in params.get("produces") or []
        )
        return ActionResult(status="ok", artifacts=drafts)


def _run(coro: Any) -> Any:
    """同步执行协程。

    Args:
        coro: 协程对象。

    Returns:
        Any: 返回值。
    """
    return asyncio.run(coro)


def _drive(service: Any, tasks: Any, task_id: str, max_rounds: int = 6) -> str:
    """逐个确认人工接管点，直到任务离开 `waiting_confirm`。

    Args:
        service: 任务服务。
        tasks: 任务仓储。
        task_id: 任务 ID。
        max_rounds: 最多确认轮数。

    Returns:
        str: 最终状态。
    """
    status = ""
    for _ in range(max_rounds):
        record = _run(tasks.get_task(TENANT, task_id))
        assert record is not None
        status = record.status
        if status != "waiting_confirm":
            break
        _run(service.retry(task_id, RetryTaskRequest(decision="continue"), auth()))
    return status


def test_rendered_docx_artifact_is_persisted_with_ooxml_mime() -> None:
    """插件渲染的 docx 落库 MIME 必须是 Word OOXML，**不能再是 text/plain**。"""
    gateway = _DocxGateway()
    service, tasks, artifacts, _ = build_service(load_manifest(PLUGIN_ID), gateway=gateway)
    created = _run(
        service.create_plugin_task(
            CreateTaskRequest(conversation_id="conv_mime", inputs=INPUTS, plugin_id=PLUGIN_ID),
            auth(),
        )
    )
    _drive(service, tasks, created.task_id)

    items = _run(artifacts.list_by_task(TENANT, created.task_id))
    document = next((item for item in items if item.output_key == "document"), None)
    assert document is not None, "第 6 步必须产出公文文件"
    assert document.content_type == EXTENSION_CONTENT_TYPES["docx"], (
        "**MIME 透传修复的判据**：渲染器给的是 Word OOXML，仓储层不得把它改回 text/plain"
    )
    assert document.content_type != "text/plain; charset=utf-8"
    assert FileRenderer.is_openable_docx(document.content) is True
