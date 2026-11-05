"""插件任务域接口契约（阶段三 B19，`architecture-phase3.md` §5.3）。

新增端点只有两个（**冻结 16 端点的最后两个**）：

| # | 端点 | 用途 |
| --- | --- | --- |
| WF1 | `POST /agent/tasks/{task_id}/retry` | 重试/继续/重做（4 个 `decision`） |
| WF2 | `PUT  /agent/tasks/{task_id}/artifacts/{artifact_id}` | 产物编辑 + 重新导出 |

A1-A4 的扩展全部是**加性字段**（`plugin_id` / `checklist` / `artifacts` / `failure` 等），
`plugin_id` 为空时字段缺省，行为与阶段一逐字等价（B19 一票否决项）。

**为什么 `download_url` 不是存储地址**：产物下载走平台代理（`/files/artifacts/...`），
前端拿到的永远是平台地址——把对象存储地址发给前端等于把存储桶的访问路径公开。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: WF1 的四个 `decision`（D-19：写端点只有一个，语义靠 `decision` 区分）
RetryDecision = Literal["retry_step", "retry_all", "continue", "redo"]


class ChecklistItem(BaseModel):
    """核查清单项（A3 `result.checklist[]`）。

    Attributes:
        key: 产物键。
        name: 核查项名称（用户可见）。
        type: 产物类型。
        status: `checked`（已核查）/ `pending`（待人工确认）/ `missing`（没有产出）。
        source_step: 来源步骤序号。
        label: 产物标签（与声明一致）。
        download_url: 下载地址（可下载产物）。
    """

    key: str = Field(default="", description="产物键")
    name: str = Field(default="", description="核查项名称")
    type: str = Field(default="text", description="产物类型")
    status: Literal["checked", "pending", "missing"] = Field(
        default="missing", description="核查状态"
    )
    source_step: int = Field(default=0, ge=0, description="来源步骤序号")
    label: str = Field(default="", description="产物标签")
    download_url: str | None = Field(default=None, description="下载地址")


class ArtifactVersionItem(BaseModel):
    """产物历史版本条目（WF2 响应 `version_history[]`）。

    Attributes:
        version: 版本号。
        created_at: 该版本创建时间。
        download_url: 该版本下载地址。
        content_sha256: 该版本内容摘要。
    """

    version: int = Field(default=1, ge=1, description="版本号")
    created_at: str = Field(default="", description="创建时间，ISO 8601")
    download_url: str | None = Field(default=None, description="该版本下载地址")
    content_sha256: str = Field(default="", description="内容摘要")


class StepArtifact(BaseModel):
    """步骤产物（A4 `steps[].artifacts[]`）。

    Attributes:
        artifact_id: 产物 ID。
        output_key: 产物键。
        type: 产物类型。
        name: 产物名称。
        version: 当前版本。
        content_type: 产物 MIME（下载/预览判据）。
        download_url: 下载地址（平台代理）。
        url: 外链（`link` 类型）。
        xlsx_url: 按需导出 Excel 的地址（表格/CSV 产物才有）。
        preview_url: 在线预览（PDF）地址（docx/xlsx/表格且开关开启时才有）。
        preview_text: 预览文本（表格前 5 行）。
        content_sha256: 内容摘要。
        editable: 是否可编辑（`link` / `file` 为 false）。
        version_history: 历史版本（可回看、可下载）。
    """

    artifact_id: str = Field(default="", description="产物 ID")
    output_key: str = Field(default="", description="产物键")
    type: str = Field(default="text", description="产物类型")
    name: str = Field(default="", description="产物名称")
    version: int = Field(default=1, ge=1, description="当前版本")
    content_type: str = Field(default="", description="产物 MIME（下载/预览判据）")
    download_url: str | None = Field(default=None, description="下载地址")
    url: str | None = Field(default=None, description="外部链接")
    xlsx_url: str | None = Field(default=None, description="按需导出 Excel 的地址")
    preview_url: str | None = Field(default=None, description="在线预览（PDF）地址")
    preview_text: str = Field(default="", description="预览文本")
    content_sha256: str = Field(default="", description="内容摘要")
    editable: bool = Field(default=False, description="是否可编辑")
    version_history: list[ArtifactVersionItem] = Field(default_factory=list, description="历史版本")


class StepFailure(BaseModel):
    """步骤失败信息（A4 `steps[].failure`，PRD E-9：就地展示、不弹错）。

    Attributes:
        reason_text: 失败原因（业务语言）。
        retryable: 是否可重试。
        operator_action: 建议动作（业务语言）。
    """

    reason_text: str = Field(default="", description="失败原因，业务语言")
    retryable: bool = Field(default=False, description="是否可重试")
    operator_action: str = Field(default="", description="建议动作，业务语言")


class ConfirmInfo(BaseModel):
    """人工接管信息（A3 `confirm`，PRD E-10：不返回错误码）。

    Attributes:
        step_seq: 待确认步骤序号。
        reason: 为什么需要确认（业务语言）。
        summary: 需要用户核对的要点（`key → 值`）。
    """

    step_seq: int = Field(default=0, ge=0, description="待确认步骤序号")
    reason: str = Field(default="", description="需要确认的原因")
    summary: dict[str, Any] = Field(default_factory=dict, description="需要核对的要点")


class RetryTaskRequest(BaseModel):
    """WF1 重试/继续请求。

    Attributes:
        decision: `retry_step` / `retry_all` / `continue` / `redo`。
        from_step: `retry_step` 必填，从该步开始重跑。
        reason: 可选备注（审计用，不返回前端）。
    """

    decision: RetryDecision = Field(default="continue", description="继续或重试的方式")
    from_step: int | None = Field(default=None, ge=1, le=8, description="重试起点步骤序号")
    reason: str = Field(default="", max_length=200, description="可选备注")


class RetryTaskData(BaseModel):
    """WF1 响应数据。

    Attributes:
        task_id: 任务 ID（`continue`/`redo` 时**与请求同一个**）。
        resumed: 是否同一任务续跑（`true` 表示未新建任务）。
        retry_of: 重试来源任务 ID（`resumed=true` 时必为 null）。
        status: 变更后的任务状态。
        from_step: 重试起点（`retry_step` 时有值）。
    """

    task_id: str = Field(description="任务 ID")
    resumed: bool = Field(default=False, description="是否同一任务续跑")
    retry_of: str | None = Field(default=None, description="重试来源任务 ID")
    status: str = Field(default="running", description="任务状态")
    from_step: int | None = Field(default=None, description="重试起点步骤序号")


class ArtifactEditRequest(BaseModel):
    """WF2 产物编辑请求。

    Attributes:
        content: 新正文（服务端**重跑脱敏**后落库）。
        render_format: 重新导出的格式（缺省沿用原格式）。
    """

    content: str = Field(min_length=1, max_length=32_768, description="新的产物正文")
    render_format: Literal["docx", "xlsx", "md", "csv"] | None = Field(
        default=None, description="重新导出的格式"
    )


class ArtifactEditData(BaseModel):
    """WF2 响应数据。

    Attributes:
        artifact_id: 产物 ID。
        version: 新版本号（每次保存 +1）。
        download_url: 重新导出后的下载地址（**保存即刷新**）。
        url: 外链（`link` 类型保持原值）。
        content_sha256: 新内容摘要（每次保存重算）。
        version_history: 历史版本列表。
    """

    artifact_id: str = Field(description="产物 ID")
    version: int = Field(default=1, ge=1, description="新版本号")
    download_url: str | None = Field(default=None, description="下载地址")
    url: str | None = Field(default=None, description="外部链接")
    content_sha256: str = Field(default="", description="内容摘要")
    version_history: list[ArtifactVersionItem] = Field(default_factory=list, description="历史版本")


class TaskPlanStep(BaseModel):
    """计划中的一个步骤（执行前展示给用户确认）。

    Attributes:
        seq: 步骤序号。
        title: 用户可见步骤名（声明快照）。
        action: 执行原语（内部字段，仅用于前端展示"将要做什么"）。
        tool_type: 所需工具类型（非工具步骤为 null）。
        tool_operation: 工具操作标识（非工具步骤为 null）。
        expected_artifacts: 预计产物键（来自 `produces`）。
        needs: 依赖的前序产物键。
        skippable: 是否允许用户取消勾选（必做步骤为 false）。
        selected: 当前是否纳入执行。
        note: 说明（条件不满足 / 未纳入本次计划 / 需执行前确认）。
    """

    seq: int = Field(ge=1, description="步骤序号")
    title: str = Field(default="", description="步骤名称")
    action: str = Field(default="", description="执行原语")
    tool_type: str | None = Field(default=None, description="所需工具类型")
    tool_operation: str | None = Field(default=None, description="工具操作标识")
    expected_artifacts: list[str] = Field(default_factory=list, description="预计产物键")
    needs: list[str] = Field(default_factory=list, description="依赖的前序产物键")
    skippable: bool = Field(default=True, description="是否允许取消勾选")
    selected: bool = Field(default=True, description="是否纳入执行")
    note: str = Field(default="", description="说明文案")


class TaskPlan(BaseModel):
    """任务执行计划（A3 的 `plan` 字段；PUT /plan 的响应）。

    Attributes:
        task_id: 任务 ID。
        plugin_id: 插件标识。
        version: 计划版本（创建=1，每次编辑 +1）。
        expires_at: 确认截止时间（ISO 8601）。
        confirmed_at: 确认时间（未确认为 null）。
        steps: 步骤列表。
    """

    task_id: str = Field(description="任务 ID")
    plugin_id: str = Field(default="", description="插件标识")
    version: int = Field(default=0, ge=0, description="计划版本")
    expires_at: str = Field(default="", description="确认截止时间")
    confirmed_at: str | None = Field(default=None, description="确认时间")
    steps: list[TaskPlanStep] = Field(default_factory=list, description="步骤列表")


class TaskPlanStepSelection(BaseModel):
    """计划编辑的单步选择。

    Attributes:
        seq: 步骤序号。
        selected: 是否纳入执行。
    """

    seq: int = Field(ge=1, le=8, description="步骤序号")
    selected: bool = Field(default=True, description="是否纳入执行")


class TaskPlanUpdateRequest(BaseModel):
    """计划编辑请求（只支持勾选/取消勾选，不支持改顺序或新增步骤）。

    Attributes:
        plan_version: 客户端持有的计划版本（乐观并发条件）。
        steps: 每步的选择（缺省步骤保持原状）。
    """

    plan_version: int = Field(ge=0, description="客户端持有的计划版本")
    steps: list[TaskPlanStepSelection] = Field(
        default_factory=list, max_length=8, description="每步的选择"
    )


class TaskPlanConfirmRequest(BaseModel):
    """计划确认请求。

    Attributes:
        plan_version: 客户端持有的计划版本（乐观并发条件）。
    """

    plan_version: int = Field(ge=0, description="客户端持有的计划版本")


class PluginTaskRuntimeData(BaseModel):
    """插件任务运行态摘要（供 A1/A3 复用的加性字段集合）。

    Attributes:
        plugin_id: 插件标识。
        plugin_version: 创建时的声明版本（快照）。
        queue_position: 排队位次（非排队为 null）。
        initiator_name: 发起人显示名。
        steps_total: 步骤总数。
        retry_of: 重试来源任务 ID。
        confirm: 人工接管信息。
    """

    plugin_id: str = Field(default="", description="插件标识")
    plugin_version: str = Field(default="", description="声明版本快照")
    queue_position: int | None = Field(default=None, description="排队位次")
    initiator_name: str = Field(default="", description="发起人显示名")
    steps_total: int = Field(default=0, ge=0, description="步骤总数")
    retry_of: str | None = Field(default=None, description="重试来源任务 ID")
    confirm: ConfirmInfo | None = Field(default=None, description="人工接管信息")


__all__: list[str] = [
    "ArtifactEditData",
    "ArtifactEditRequest",
    "ArtifactVersionItem",
    "ChecklistItem",
    "ConfirmInfo",
    "PluginTaskRuntimeData",
    "RetryDecision",
    "RetryTaskData",
    "RetryTaskRequest",
    "StepArtifact",
    "StepFailure",
    "TaskPlan",
    "TaskPlanConfirmRequest",
    "TaskPlanStep",
    "TaskPlanStepSelection",
    "TaskPlanUpdateRequest",
]
