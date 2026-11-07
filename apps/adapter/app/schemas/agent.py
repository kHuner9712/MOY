"""Agent 服务接口契约（架构文档 3.3，A1-A6）。

设计原则：仅暴露「任务-步骤」业务模型；
Agent 编排细节与工具调用内部协议（附录 A-6）封装在底座，节点类型名不外泄。

V1.0 双通道：创建 + 轮询（A2/A3/A4）+ SSE 推送（A6），Webhook 回调预留（D-04）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.capability_scope import CapabilityDeclaration
from app.schemas.common import PageQuery
from app.schemas.model import ModelProfile, Usage
from app.schemas.task import StepArtifact, StepFailure, TaskPlan


class TaskStatus(StrEnum):
    """任务状态枚举（底座原生状态已映射为自研状态）。

    阶段三 B19 **加性新增**两个值（不改既有五值的语义）：

    - `queued`：同租户同插件并发超限时的排队态（返回 200 + `queue_position`，
      **不是** 429/20006——4xx 会被前端渲染成报错页，用户以为没创建成功）；
    - `waiting_confirm`：人工接管点（`confirm` 步骤未确认），**永不自动推进**。

    **P4-11 ③ 明确不加 `queued_quota`**：额度挂起复用 `queued`（同样是"没开始跑"），
    原因见 `TaskItem.hold_reason` 的注释——状态是筛选维度，多造一个库里永不存在的
    状态值会让前端筛出无法解释的空列表。
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    QUEUED = "queued"
    WAITING_CONFIRM = "waiting_confirm"
    #: 计划确认态（矩阵第 2 项）：等待用户确认整单计划，**确认前不执行任何步骤**。
    WAITING_PLAN = "waiting_plan"


#: 额度挂起原因 → 用户可见文案（P4-11 ③，业务语言、零技术词）
HOLD_REASON_DISPLAY_NAMES: Final[dict[str, str]] = {
    "": "",
    "quota": "额度恢复后自动开始",
    "past_due": "结清欠费后自动开始",
}


def hold_reason_display_name(hold_reason: str) -> str:
    """取挂起原因的用户可见文案。

    Args:
        hold_reason: 挂起原因（`''` / `quota` / `past_due`）。

    Returns:
        str: 业务语言文案；未挂起或未知原因返回空串（前端按空串隐藏这一列）。
    """
    return HOLD_REASON_DISPLAY_NAMES.get(hold_reason, "")


class StepStatus(StrEnum):
    """步骤状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ArtifactType(StrEnum):
    """产物类型枚举（阶段三加性新增 `table` 与 `draft` / `doc`）。

    - `table`：表格（评分表 / 排期表），存 CSV + 前 5 行预览；
    - `draft` / `doc`：可编辑稿件与文档（WF2 三类可编辑产物之二）。
    """

    FILE = "file"
    LINK = "link"
    TEXT = "text"
    TABLE = "table"
    DRAFT = "draft"
    DOC = "doc"


class CreateTaskRequest(BaseModel):
    """A1 创建任务请求。

    Attributes:
        conversation_id: 会话 ID。
        goal: 任务目标（业务语言描述）。
        profile: 能力档位。
        tool_grants: 允许使用的工具类型列表。
        capabilities: 会话级能力声明（可选；不传=继承调用者权限）。
        inputs: 任务输入。
        options: 执行选项。
    """

    conversation_id: str = Field(
        default="", max_length=128, description="会话 ID，形如 conv_01HF8..."
    )
    goal: str = Field(default="", max_length=4000, description="任务目标")
    profile: ModelProfile = Field(default=ModelProfile.GENERAL, description="能力档位")
    tool_grants: list[str] = Field(default_factory=list, description="允许使用的工具类型列表")
    capabilities: CapabilityDeclaration | None = Field(
        default=None,
        description=(
            "会话级能力声明（B6，可选）。不传=继承调用者权限；传了则三个维度必填，"
            "只能收窄、不可放大，越界返回 14009"
        ),
    )
    inputs: dict[str, Any] = Field(default_factory=dict, description="任务输入")
    options: TaskOptions = Field(default_factory=lambda: TaskOptions(), description="执行选项")
    plugin_id: str = Field(
        default="",
        max_length=44,
        description="插件标识（有值时按插件声明执行并校验 inputs）",
    )
    plan_first: bool | None = Field(
        default=None,
        description=(
            "先产出执行计划、等用户确认后再执行（矩阵第 2 项）。"
            "null=跟随服务端开关；true=显式请求计划（开关关闭时返回 20001）"
        ),
    )

    @model_validator(mode="after")
    def _require_phase1_fields(self) -> CreateTaskRequest:
        """无 `plugin_id` 时，`conversation_id` 与 `goal` 仍为必填（阶段一等价）。

        阶段一的两个必填字段**不能因为插件流程而放松**：`plugin_id` 为空却允许
        空 `goal`，等于让阶段一的入参契约悄悄变宽（G4 加性 diff 会红）。
        插件流程则相反——工作台只提交声明驱动的 `inputs`，没有「任务目标」这一栏。

        Returns:
            CreateTaskRequest: 自身。

        Raises:
            ValueError: 缺少阶段一必填字段。
        """
        if self.plugin_id:
            return self
        if not self.conversation_id:
            raise ValueError("会话 ID 不能为空")
        if not self.goal.strip():
            raise ValueError("任务目标不能为空")
        return self


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
        notice_code: 非阻断提示码（B27 加性字段：额度用尽为 `15006`，否则 0）。
        notice_message: 非阻断提示文案（`notice_code` 为 0 时为空串）。
    """

    task_id: str = Field(description="任务 ID，形如 task_01HF8...")
    status: TaskStatus = Field(default=TaskStatus.RUNNING, description="任务状态")
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")
    estimated_seconds: int = Field(default=0, ge=0, description="预计耗时，单位秒")
    plugin_id: str = Field(default="", description="插件标识（阶段三加性字段）")
    steps_total: int = Field(default=0, ge=0, description="步骤总数（阶段三加性字段）")
    retry_of: str | None = Field(default=None, description="重试来源任务 ID（加性字段）")
    queue_position: int | None = Field(
        default=None, description="排队位次（并发超限时有值，加性字段）"
    )
    #: **非阻断提示**（PRD 5.3：超额仍建任务，绝不拦停）：只挂在成功响应的
    #: `data` 里，外层 `code` 恒为 0；放在 `code` 会让"建任务成功"变成弹错误框。
    notice_code: int = Field(default=0, ge=0, description="非阻断提示码（额度用尽为 15006）")
    notice_message: str = Field(default="", description="非阻断提示文案（无提示时为空串）")


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
    plugin_id: str = Field(default="", description="插件标识（阶段三加性字段）")
    warning_count: int = Field(default=0, ge=0, description="未完成步骤数（阶段三加性字段）")
    initiator_id: str = Field(default="", description="发起人 ID（阶段三加性字段）")
    initiator_name: str = Field(default="", description="发起人显示名（阶段三加性字段）")
    #: 额度挂起标记（P4-11 ③，加性字段）：`''` 未挂起 / `quota` 超额 / `past_due` 欠费。
    #: **不新增 `TaskStatus.queued_quota` 状态**：状态是**筛选维度**（前端会按枚举生成
    #: 筛选下拉），造一个"可筛选但库里永不存在的值"会让用户筛出空列表且无法解释。
    #: 挂起是状态之外的一个**正交事实**（排队的任务也可能被挂起），所以用一对字段表达。
    hold_reason: str = Field(
        default="",
        max_length=16,
        description="额度挂起原因：'' 未挂起 / quota 超额 / past_due 欠费（加性字段）",
    )
    hold_reason_display_name: str = Field(
        default="", description="挂起原因的展示名（未挂起为空串；照 initiator_name 先例）"
    )


class ListTasksQuery(PageQuery):
    """A2 任务列表查询参数。

    Attributes:
        conversation_id: 按会话过滤。
        status: 按状态过滤。
    """

    conversation_id: str = Field(default="", max_length=128, description="按会话过滤")
    status: TaskStatus | None = Field(default=None, description="按状态过滤")
    plugin_id: str = Field(default="", max_length=44, description="按插件过滤（加性字段）")
    initiator_id: str = Field(default="", max_length=64, description="按发起人过滤（加性字段）")
    created_from: str = Field(default="", max_length=40, description="起始时间（加性字段）")
    created_to: str = Field(default="", max_length=40, description="结束时间（加性字段）")


class Artifact(BaseModel):
    """任务产物。

    Attributes:
        type: 产物类型。
        name: 产物名称。
        download_url: 下载地址（`file` 类型）。
        url: 外部链接（`link` 类型）。
        output_key: 产物键（与插件声明 `outputs[].key` 对齐，阶段三加性字段）。
        content_sha256: 内容摘要（前端据此判断"文件到底换没换"，阶段三加性字段）。
    """

    type: ArtifactType = Field(default=ArtifactType.TEXT, description="产物类型")
    name: str = Field(default="", description="产物名称")
    download_url: str | None = Field(default=None, description="下载地址")
    url: str | None = Field(default=None, description="外部链接")
    # **加性字段**：缺省空串，阶段一响应逐字不变（B19 一票否决项）
    output_key: str = Field(default="", description="产物键（阶段三加性字段）")
    content_sha256: str = Field(default="", description="内容摘要（阶段三加性字段）")


class TaskResult(BaseModel):
    """任务结果。

    Attributes:
        summary: 结果摘要（业务语言）。
        artifacts: 产物列表。
    """

    summary: str = Field(default="", description="结果摘要")
    artifacts: list[Artifact] = Field(default_factory=list, description="产物列表")
    checklist: list[Any] = Field(
        default_factory=list, description="结果物核查清单（阶段三加性字段）"
    )
    warnings: list[str] = Field(
        default_factory=list, description="未完成但不中断的提示（阶段三加性字段）"
    )


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
    plugin_id: str = Field(default="", description="插件标识（阶段三加性字段）")
    plugin_version: str = Field(default="", description="声明版本快照（加性字段）")
    initiator_name: str = Field(default="", description="发起人显示名（加性字段）")
    queue_position: int | None = Field(default=None, description="排队位次（加性字段）")
    steps_total: int = Field(default=0, ge=0, description="步骤总数（加性字段）")
    retry_of: str | None = Field(default=None, description="重试来源任务 ID（加性字段）")
    confirm: dict[str, Any] | None = Field(default=None, description="人工接管信息（加性字段）")
    #: 计划元数据（矩阵第 2 项，加性字段）。非计划任务恒为 0 / "" / null。
    plan_version: int = Field(default=0, ge=0, description="计划版本（加性字段）")
    plan_expires_at: str = Field(default="", description="计划确认截止时间（加性字段）")
    plan_confirmed_at: str = Field(default="", description="计划确认时间（加性字段）")
    plan: TaskPlan | None = Field(default=None, description="执行计划（加性字段）")


class TaskPlanConfirmResult(BaseModel):
    """计划确认响应（矩阵第 2 项）。

    Attributes:
        task_id: 任务 ID。
        status: 确认后的任务状态（`running` / `queued`）。
        plan_version: 被确认的计划版本。
        plan_confirmed_at: 确认时间（首次确认时写入；幂等重放返回原值）。
        already_confirmed: 是否重复确认（`true` 时未再触发执行）。
    """

    task_id: str = Field(description="任务 ID")
    status: TaskStatus = Field(default=TaskStatus.RUNNING, description="任务状态")
    plan_version: int = Field(default=0, ge=0, description="被确认的计划版本")
    plan_confirmed_at: str = Field(default="", description="确认时间")
    already_confirmed: bool = Field(default=False, description="是否重复确认")


#: 步骤失败信息（A4 `steps[].failure` = `app.schemas.task.StepFailure`）。
#:
#: **只从 `app/schemas/task.py` 引入，不在本文件再定义一份同名模型**：
#: 同名类在两个模块各定义一次会让 OpenAPI 里出现两个 `StepFailure` schema
#: （互相覆盖），前端据此生成的类型会随机取到其中一份。
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
        artifacts: 该步产物（阶段三加性字段，A3/A4 工作流可视化要用）。
        failure: 该步失败信息（阶段三加性字段，PRD E-9：就地展示、不弹错）。
    """

    step_id: str = Field(description="步骤 ID")
    sequence: int = Field(default=0, ge=0, description="步骤序号")
    title: str = Field(default="", description="步骤业务名称")
    status: StepStatus = Field(default=StepStatus.PENDING, description="步骤状态")
    started_at: str | None = Field(default=None, description="开始时间，ISO 8601 格式")
    finished_at: str | None = Field(default=None, description="完成时间，ISO 8601 格式")
    output_summary: str | None = Field(default=None, description="输出摘要")
    # **加性字段**：缺省空列表，阶段一响应逐字不变。
    #
    # 这两个字段曾经漏在契约里，而服务层**已经**在构造 `StepItem` 时塞了
    # `artifacts=` / `failure=`——pydantic 默认忽略未声明字段（不报错），
    # 于是"填了等于没填"，前端永远拿不到步骤产物。加回来才算真的对外可见。
    artifacts: list[StepArtifact] = Field(
        default_factory=list, description="该步产物（阶段三加性字段）"
    )
    failure: StepFailure | None = Field(default=None, description="该步失败信息（阶段三加性字段）")


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
