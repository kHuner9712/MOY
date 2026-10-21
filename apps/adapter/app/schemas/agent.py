"""Agent 服务接口契约（架构文档 3.3，A1-A6）。

设计原则：仅暴露「任务-步骤」业务模型；
Agent 编排细节与工具调用内部协议（附录 A-6）封装在底座，节点类型名不外泄。

V1.0 双通道：创建 + 轮询（A2/A3/A4）+ SSE 推送（A6），Webhook 回调预留（D-04）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from app.schemas.model import ModelProfile, Usage


class TaskStatus(StrEnum):
    """任务状态枚举（底座原生状态已映射为自研三态）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """步骤状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ArtifactType(StrEnum):
    """产物类型枚举。"""

    FILE = "file"
    LINK = "link"
    TEXT = "text"


class CreateTaskRequest(BaseModel):
    """A1 创建任务请求。

    Attributes:
        conversation_id: 会话 ID。
        goal: 任务目标（业务语言描述）。
        profile: 能力档位。
        tool_grants: 允许使用的工具类型列表。
        inputs: 任务输入。
        options: 执行选项。
    """

    conversation_id: str = Field(
        min_length=1, max_length=128, description="会话 ID，形如 conv_01HF8..."
    )
    goal: str = Field(min_length=1, max_length=4000, description="任务目标")
    profile: ModelProfile = Field(default=ModelProfile.GENERAL, description="能力档位")
    tool_grants: list[str] = Field(default_factory=list, description="允许使用的工具类型列表")
    inputs: dict[str, Any] = Field(default_factory=dict, description="任务输入")
    options: TaskOptions = Field(default_factory=lambda: TaskOptions(), description="执行选项")


class TaskOptions(BaseModel):
    """任务执行选项。

    Attributes:
        max_steps: 最大步骤数。
        timeout_seconds: 超时秒数。
    """

    max_steps: int = Field(default=12, ge=1, le=100, description="最大步骤数")
    timeout_seconds: int = Field(default=600, ge=10, le=7200, description="超时秒数")


CreateTaskRequest.model_rebuild()


class CreateTaskData(BaseModel):
    """A1 创建任务响应数据。

    Attributes:
        task_id: 任务 ID，形如 `task_01HF8...`。
        status: 任务状态。
        created_at: 创建时间。
        estimated_seconds: 预计耗时（秒）。
    """

    task_id: str = Field(description="任务 ID，形如 task_01HF8...")
    status: TaskStatus = Field(default=TaskStatus.RUNNING, description="任务状态")
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")
    estimated_seconds: int = Field(default=0, ge=0, description="预计耗时，单位秒")


class TaskItem(BaseModel):
    """任务列表条目。

    Attributes:
        task_id: 任务 ID。
        conversation_id: 所属会话 ID。
        goal: 任务目标。
        status: 任务状态。
        progress: 进度百分比。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    task_id: str = Field(description="任务 ID")
    conversation_id: str = Field(default="", description="所属会话 ID")
    goal: str = Field(default="", description="任务目标")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")
    updated_at: str = Field(default="", description="更新时间，ISO 8601 格式")


class ListTasksQuery(PageQuery):
    """A2 任务列表查询参数。

    Attributes:
        conversation_id: 按会话过滤。
        status: 按状态过滤。
    """

    conversation_id: str = Field(default="", max_length=128, description="按会话过滤")
    status: TaskStatus | None = Field(default=None, description="按状态过滤")


class Artifact(BaseModel):
    """任务产物。

    Attributes:
        type: 产物类型。
        name: 产物名称。
        download_url: 下载地址（`file` 类型）。
        url: 外部链接（`link` 类型）。
    """

    type: ArtifactType = Field(default=ArtifactType.TEXT, description="产物类型")
    name: str = Field(default="", description="产物名称")
    download_url: str | None = Field(default=None, description="下载地址")
    url: str | None = Field(default=None, description="外部链接")


class TaskResult(BaseModel):
    """任务结果。

    Attributes:
        summary: 结果摘要（业务语言）。
        artifacts: 产物列表。
    """

    summary: str = Field(default="", description="结果摘要")
    artifacts: list[Artifact] = Field(default_factory=list, description="产物列表")


class TaskDetailData(BaseModel):
    """A3 任务状态查询响应数据。

    Attributes:
        task_id: 任务 ID。
        status: 任务状态。
        progress: 进度百分比。
        current_step: 当前步骤序号。
        result: 任务结果。
        usage: 业务化用量。
        updated_at: 更新时间。
    """

    task_id: str = Field(description="任务 ID")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")
    current_step: str | None = Field(default=None, description="当前步骤序号")
    result: TaskResult | None = Field(default=None, description="任务结果")
    usage: Usage = Field(default_factory=Usage, description="业务化用量")
    updated_at: str = Field(default="", description="更新时间，ISO 8601 格式")


class StepItem(BaseModel):
    """任务步骤条目（`title` / `output_summary` 为业务语言，底座节点类型名不暴露）。

    Attributes:
        step_id: 步骤 ID。
        sequence: 步骤序号。
        title: 步骤业务名称。
        status: 步骤状态。
        started_at: 开始时间。
        finished_at: 完成时间。
        output_summary: 输出摘要。
    """

    step_id: str = Field(description="步骤 ID")
    sequence: int = Field(default=0, ge=0, description="步骤序号")
    title: str = Field(default="", description="步骤业务名称")
    status: StepStatus = Field(default=StepStatus.PENDING, description="步骤状态")
    started_at: str | None = Field(default=None, description="开始时间，ISO 8601 格式")
    finished_at: str | None = Field(default=None, description="完成时间，ISO 8601 格式")
    output_summary: str | None = Field(default=None, description="输出摘要")


class StepsData(BaseModel):
    """A4 步骤详情响应数据。

    Attributes:
        task_id: 任务 ID。
        steps: 步骤列表。
    """

    task_id: str = Field(description="任务 ID")
    steps: list[StepItem] = Field(default_factory=list, description="步骤列表")


class CancelTaskData(BaseModel):
    """A5 取消任务响应数据。

    Attributes:
        task_id: 任务 ID。
        status: 取消后的任务状态。
        updated_at: 更新时间。
    """

    task_id: str = Field(description="任务 ID")
    status: TaskStatus = Field(default=TaskStatus.CANCELLED, description="任务状态")
    updated_at: str = Field(default="", description="更新时间，ISO 8601 格式")


#: 自研 SSE 事件类型（A6 任务进度推送，绝不复用底座事件名）
TaskEventName = Literal["step_started", "step_finished", "task_completed", "error"]


class TaskEventData(BaseModel):
    """A6 任务事件载荷。

    Attributes:
        task_id: 任务 ID。
        step: 步骤信息（`step_started` / `step_finished` 时有值）。
        status: 任务状态（`task_completed` 时有值）。
    """

    task_id: str = Field(default="", description="任务 ID")
    step: StepItem | None = Field(default=None, description="步骤信息")
    status: TaskStatus | None = Field(default=None, description="任务状态")
