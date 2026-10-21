"""工具服务扩展契约（阶段二 B06，TL1 / 架构文档 §4.3）。

TL1 `GET /tools/executions` 的模型归属本文件；**T1-T7 的模型一律不动**
（`schemas/tool.py` 保持阶段一形态，CI G4 机械校验）。

失败项口径（PRD P2-TL-04 验收 3）：只返回业务语言 `error_message`，
**禁止**返回错误码或堆栈——用户看到的是「未找到该成员，请确认后在通讯录中核对」，
而不是 `13006` 或异常回溯。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from app.schemas.tool import ExecutionStatus


class ListExecutionsQuery(PageQuery):
    """TL1 调用记录查询参数。

    Attributes:
        tool_type: 按工具类型过滤（空表示全部）。
    """

    tool_type: str = Field(default="", max_length=64, description="按工具类型过滤")


class ToolExecutionItem(BaseModel):
    """一条工具调用记录（TL1 元素）。

    Attributes:
        execution_id: 调用记录 ID，形如 `exec_01HF8...`。
        tool_type: 工具类型标识。
        tool_display_name: 工具的业务化名称。
        operation: 操作标识。
        operation_display_name: 操作的业务化名称。
        status: 调用结果（`success` / `failed`）。
        error_message: 失败原因（业务语言）；成功时为 null。
        executed_at: 调用时间（ISO 8601）。
        operator_name: 操作人展示名。
    """

    execution_id: str = Field(description="调用记录 ID")
    tool_type: str = Field(default="", description="工具类型标识")
    tool_display_name: str = Field(default="", description="工具的业务化名称")
    operation: str = Field(default="", description="操作标识")
    operation_display_name: str = Field(default="", description="操作的业务化名称")
    status: ExecutionStatus = Field(
        default=ExecutionStatus.SUCCESS, description="调用结果：success / failed"
    )
    error_message: str | None = Field(default=None, description="失败原因，业务语言")
    executed_at: str = Field(default="", description="调用时间，ISO 8601")
    operator_name: str = Field(default="", description="操作人展示名")


__all__: list[str] = [
    "ListExecutionsQuery",
    "ToolExecutionItem",
]
