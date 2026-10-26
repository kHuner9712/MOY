"""插件任务编排 · 只读视图（`TaskQuery`，切片 2C-3b）。

**A2 / A3 / A4 与 WF2 的产物展示都在这里**：列表、详情、步骤、核查清单、下载地址与
版本历史。它们**只读**——不写任务状态、不推进、不释放额度，因此可以被 Runner 复用
（`cancel` 收尾后要返回详情、`finish` 要用核查清单算摘要），而不会把"写路径"的
副作用带进只读路径。

**三处跨组件调用改用了公开名**（ruff `SLF` 已开，`self._other._private()` 会直接红）：
`checklist_items` / `download_url` / `version_history`。改名只是可见性，
参数与行为逐字未变。
"""

from __future__ import annotations

from typing import Any

from app.business.task.access import assert_task_visible
from app.business.task.deps import TaskDeps
from app.business.task.manifest import TaskManifestResolver
from app.core.artifact_token import make_token
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.artifact_repo import ArtifactRecord
from app.db.repositories.task import TaskRecord
from app.plugins.engine import STEP_FAILED, TASK_WAITING_CONFIRM
from app.schemas.agent import (
    Artifact,
    ArtifactType,
    ListTasksQuery,
    StepItem,
    StepsData,
    StepStatus,
    TaskDetailData,
    TaskItem,
    TaskResult,
    TaskStatus,
)
from app.schemas.common import build_page
from app.schemas.model import Usage
from app.schemas.task import (
    ArtifactVersionItem,
    ChecklistItem,
    ConfirmInfo,
    StepArtifact,
    StepFailure,
)


class TaskQuery:
    """任务只读视图（A2 / A3 / A4 + 产物展示）。"""

    def __init__(self, deps: TaskDeps, resolver: TaskManifestResolver) -> None:
        """初始化。

        Args:
            deps: 共享依赖容器。
            resolver: 声明解析器。
        """
        self._deps = deps
        self._resolver = resolver

    async def list_tasks(self, query: ListTasksQuery, auth: AuthContext) -> Any:
        """A2 任务列表（含插件任务；无 `plugin_id` 时行为与阶段一一致）。

        Args:
            query: 查询参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[TaskItem]`。
        """
        records, total = await self._deps.tasks.list_tasks(
            auth.tenant_id,
            plugin_id=query.plugin_id,
            status=query.status.value if query.status is not None else "",
            initiator_id=query.initiator_id,
            created_from=query.created_from,
            created_to=query.created_to,
            page=query.page,
            page_size=query.page_size,
        )
        items = [self._task_item(record, auth) for record in records]
        return build_page(items=items, total=total, page=query.page, page_size=query.page_size)

    def _task_item(self, record: TaskRecord, auth: AuthContext) -> TaskItem:
        """把任务行转成列表项。

        列表是**聚合视图**：单条任务的声明缺失不该拖垮整个列表（LED-045——
        一条 `plugin_id` 指向不存在插件的脏任务曾让 `GET /api/v1/agent/tasks`
        整体 404）。此处对缺失 manifest 做容忍：`goal` 退化为插件标识这一
        **中性兜底**（事实信息，不编造名称），其余字段照常返回。
        详情/执行路径仍走 `manifest_of` 的显性 16002——那些路径没有声明
        就无法正确渲染步骤与确认语义，静默兜底会给出错误结果。

        Args:
            record: 任务行。
            auth: 鉴权上下文。

        Returns:
            TaskItem: 列表项。
        """
        manifest = self._resolver.find_manifest(record)
        return TaskItem(
            task_id=record.task_id,
            conversation_id=record.conversation_id,
            goal=manifest.name if manifest is not None else record.plugin_id,
            status=TaskStatus(record.status),
            progress=record.progress,
            created_at=record.started_at,
            updated_at=record.finished_at or record.started_at,
            plugin_id=record.plugin_id,
            warning_count=len(record.warnings),
            initiator_id=record.initiator_id,
            initiator_name=self._initiator_name(record, auth),
        )

    async def get_task(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """A3 任务详情（插件任务分支）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            TaskDetailData: 任务详情。

        Raises:
            ApiError: 任务不存在（12001）、无权查看（20003）。
        """
        record = await self._deps.tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        assert_task_visible(record, auth)
        manifest = self._resolver.manifest_of(record)
        steps = await self._deps.tasks.list_steps(record.tenant_id, task_id)
        artifacts = await self._deps.artifacts.list_by_task(record.tenant_id, task_id)
        checklist = self.checklist_items(record, artifacts)
        confirm = None
        if record.status == TASK_WAITING_CONFIRM and record.confirm_step_seq > 0:
            spec = self._resolver.spec_of(manifest, record.confirm_step_seq)
            confirm = ConfirmInfo(
                step_seq=record.confirm_step_seq,
                reason=spec.confirm.reason
                if spec is not None and spec.confirm
                else "需要你确认后继续",
                summary={
                    key: value
                    for key, value in record.inputs.items()
                    if key in (spec.confirm.summary_fields if spec and spec.confirm else [])
                },
            )
        return TaskDetailData(
            task_id=record.task_id,
            status=TaskStatus(record.status),
            progress=record.progress,
            current_step=str(record.current_step_seq or "") or None,
            result=TaskResult(
                summary=record.result_summary,
                artifacts=[
                    Artifact(
                        type=_artifact_type(item.type),
                        name=item.name,
                        download_url=self.download_url(item),
                        url=item.external_url or None,
                        output_key=item.output_key,
                        content_sha256=item.content_sha256,
                    )
                    for item in artifacts
                ],
                checklist=[item.model_dump() for item in checklist],
                warnings=list(record.warnings),
            ),
            usage=Usage(total_units=record.usage_units),
            updated_at=record.finished_at or record.started_at,
            plugin_id=record.plugin_id,
            plugin_version=record.plugin_version,
            initiator_name=self._initiator_name(record, auth),
            queue_position=record.queue_position,
            steps_total=len(steps),
            retry_of=record.retry_of or None,
            confirm=confirm.model_dump() if confirm is not None else None,
        )

    async def steps(self, task_id: str, auth: AuthContext) -> StepsData:
        """A4 步骤详情（含产物与失败信息）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            StepsData: 步骤列表。

        Raises:
            ApiError: 任务不存在（12001）、无权查看（20003）。
        """
        record = await self._deps.tasks.get_task(auth.tenant_id, task_id)
        if record is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        assert_task_visible(record, auth)
        steps = await self._deps.tasks.list_steps(record.tenant_id, task_id)
        artifacts = await self._deps.artifacts.list_by_task(record.tenant_id, task_id)
        by_step: dict[str, list[ArtifactRecord]] = {}
        for item in artifacts:
            by_step.setdefault(item.step_id, []).append(item)
        items = [
            StepItem(
                step_id=step.step_id,
                sequence=step.seq,
                title=step.title,
                status=StepStatus(step.status),
                started_at=step.started_at or None,
                finished_at=step.finished_at or None,
                output_summary=step.output_summary or None,
                artifacts=[self._step_artifact(item) for item in by_step.get(step.step_id, [])],
                failure=(
                    StepFailure(
                        reason_text=step.reason_text,
                        retryable=step.retryable,
                        operator_action=step.operator_action,
                    )
                    if step.status == STEP_FAILED
                    else None
                ),
            )
            for step in steps
        ]
        return StepsData(task_id=task_id, steps=items)

    def _initiator_name(self, record: TaskRecord, auth: AuthContext) -> str:
        """取发起人显示名（尽力而为，取不到回落 ID）。

        Args:
            record: 任务行。
            auth: 鉴权上下文。

        Returns:
            str: 显示名。
        """
        if record.initiator_id == auth.user_id:
            for key in ("name", "display_name", "email"):
                value = auth.claims.get(key) if isinstance(auth.claims, dict) else None
                if isinstance(value, str) and value:
                    return value
        return record.initiator_id

    def checklist_items(
        self, record: TaskRecord, artifacts: list[ArtifactRecord]
    ) -> list[ChecklistItem]:
        """按声明与产物生成核查清单。

        Args:
            record: 任务行。
            artifacts: 产物列表。

        Returns:
            list[ChecklistItem]: 核查清单。
        """
        manifest = self._resolver.manifest_of(record)
        by_key = {item.output_key: item for item in artifacts}
        items: list[ChecklistItem] = []
        for entry in manifest.checklist:
            artifact = by_key.get(entry.key)
            if artifact is not None:
                status = "checked"
            elif artifact is None and entry.key in {item.output_key for item in artifacts}:
                status = "pending"
            else:
                status = "missing"
            items.append(
                ChecklistItem(
                    key=entry.key,
                    name=entry.name,
                    type=artifact.type if artifact is not None else "text",
                    status=status,
                    source_step=entry.source_step,
                    label=entry.name,
                    download_url=self.download_url(artifact) if artifact is not None else None,
                )
            )
        return items

    def download_url(self, artifact: ArtifactRecord | None) -> str | None:
        """生成平台代理下载地址（**不暴露存储地址**）。

        Args:
            artifact: 产物行。

        Returns:
            str | None: 下载地址；无可下载内容返回 None。
        """
        if artifact is None:
            return None
        if artifact.type == "link":
            return artifact.external_url or None
        token = make_token(
            tenant_id=artifact.tenant_id,
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            settings=self._deps.settings,
        )
        return f"/files/artifacts/{token}"

    def version_history(self, artifact: ArtifactRecord) -> list[ArtifactVersionItem]:
        """把历史版本转成响应结构（旧版本同样可下载）。

        Args:
            artifact: 产物行。

        Returns:
            list[ArtifactVersionItem]: 历史版本列表（旧 → 新）。
        """
        items: list[ArtifactVersionItem] = []
        for entry in artifact.version_history:
            version = int(entry.get("version", 1) or 1)
            token = make_token(
                tenant_id=artifact.tenant_id,
                artifact_id=artifact.artifact_id,
                version=version,
                settings=self._deps.settings,
            )
            items.append(
                ArtifactVersionItem(
                    version=version,
                    created_at=str(entry.get("created_at", "") or ""),
                    download_url=f"/files/artifacts/{token}",
                    content_sha256=str(entry.get("content_sha256", "") or ""),
                )
            )
        return items

    def _step_artifact(self, artifact: ArtifactRecord) -> StepArtifact:
        """把产物行转成 A4 产物结构。

        Args:
            artifact: 产物行。

        Returns:
            StepArtifact: 产物结构。
        """
        return StepArtifact(
            artifact_id=artifact.artifact_id,
            output_key=artifact.output_key,
            type=artifact.type,
            name=artifact.name,
            version=artifact.version,
            download_url=self.download_url(artifact),
            url=artifact.external_url or None,
            preview_text=artifact.preview_text,
            content_sha256=artifact.content_sha256,
            editable=artifact.editable,
            version_history=self.version_history(artifact),
        )


def _artifact_type(value: str) -> ArtifactType:
    """把产物类型字符串转成枚举（未知值回落 `text`）。

    Args:
        value: 类型字符串。

    Returns:
        ArtifactType: 类型枚举。
    """
    try:
        return ArtifactType(value)
    except ValueError:  # pragma: no cover - 声明已校验，兜底防脏数据
        return ArtifactType.TEXT


__all__: list[str] = ["TaskQuery", "_artifact_type"]
