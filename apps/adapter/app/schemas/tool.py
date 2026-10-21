"""工具服务接口契约（架构文档 3.4，T1-T7）。

设计原则：**工具类型注册表模式**（架构文档第 4 章 Q1）。
新增工具只向 `registry/tool_registry.yaml` 登记 + 在 `tools/` 新增连接器，
T1-T7 端点、请求响应结构、错误码**零改动**。

对外展示形态（`display_name` / `operations`）完全自研，
不暴露任何第三方工具的原始接口结构与错误形态。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery


class ToolAuthType(StrEnum):
    """工具授权方式枚举。"""

    OAUTH2 = "oauth2"
    TOKEN = "token"  # noqa: S105 - 授权方式枚举值，非凭据
    NONE = "none"


class ToolCategory(StrEnum):
    """工具分类枚举（业务化分类，不使用第三方原始分类）。"""

    COMMUNICATION = "communication"
    DEVELOPMENT = "development"
    DOCUMENT = "document"
    SCHEDULE = "schedule"
    DATA = "data"


class ToolStatus(StrEnum):
    """工具可用状态枚举。"""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ConnectionStatus(StrEnum):
    """工具连接状态枚举。"""

    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ExecutionStatus(StrEnum):
    """工具执行结果状态枚举。"""

    SUCCESS = "success"
    FAILED = "failed"


class SyncJobStatus(StrEnum):
    """数据同步任务状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolOperation(BaseModel):
    """工具操作项。

    Attributes:
        operation: 操作标识，形如 `send_message`。
        display_name: 业务化操作名称。
        params_schema: 参数 JSON Schema（自研描述）。
    """

    operation: str = Field(description="操作标识")
    display_name: str = Field(default="", description="业务化操作名称")
    params_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object"}, description="参数 JSON Schema"
    )


class ToolItem(BaseModel):
    """工具类型条目（T1 注册表元素）。

    Attributes:
        tool_type: 工具类型标识。
        display_name: 业务化名称。
        category: 业务化分类。
        auth_type: 授权方式。
        status: 可用状态。
        operations: 能力清单。
        timeout_ms: 超时声明（架构文档 3.0.4）。
    """

    tool_type: str = Field(description="工具类型标识")
    display_name: str = Field(default="", description="业务化名称")
    category: ToolCategory = Field(default=ToolCategory.DATA, description="业务化分类")
    auth_type: ToolAuthType = Field(default=ToolAuthType.OAUTH2, description="授权方式")
    status: ToolStatus = Field(default=ToolStatus.AVAILABLE, description="可用状态")
    operations: list[ToolOperation] = Field(default_factory=list, description="能力清单")
    timeout_ms: int = Field(default=10_000, ge=100, description="超时声明，单位毫秒")


class RegistryData(BaseModel):
    """T1 注册表响应数据。

    Attributes:
        tools: 工具类型列表（配置驱动，前端按此动态渲染）。
    """

    tools: list[ToolItem] = Field(default_factory=list, description="工具类型列表")


class CreateConnectionRequest(BaseModel):
    """T2 建立工具连接请求。

    Attributes:
        tool_type: 工具类型标识。
        auth_code: 授权码或令牌。
        redirect_uri: 授权回调地址。
    """

    tool_type: str = Field(min_length=1, max_length=64, description="工具类型标识")
    auth_code: str = Field(min_length=1, max_length=2048, description="授权码或令牌")
    redirect_uri: str = Field(default="", max_length=512, description="授权回调地址")


class ConnectionData(BaseModel):
    """T2 响应数据。

    Attributes:
        connection_id: 连接 ID，形如 `conn_01HF8...`。
        tool_type: 工具类型标识。
        status: 连接状态。
        expires_at: 过期时间。
    """

    connection_id: str = Field(description="连接 ID，形如 conn_01HF8...")
    tool_type: str = Field(default="", description="工具类型标识")
    status: ConnectionStatus = Field(default=ConnectionStatus.CONNECTED, description="连接状态")
    expires_at: str | None = Field(default=None, description="过期时间，ISO 8601 格式")


class ConnectionItem(BaseModel):
    """已连接工具条目（T3）。

    Attributes:
        connection_id: 连接 ID。
        tool_type: 工具类型标识。
        display_name: 业务化名称。
        status: 连接状态。
        connected_at: 连接时间。
        expires_at: 过期时间。
    """

    connection_id: str = Field(description="连接 ID")
    tool_type: str = Field(default="", description="工具类型标识")
    display_name: str = Field(default="", description="业务化名称")
    status: ConnectionStatus = Field(default=ConnectionStatus.CONNECTED, description="连接状态")
    connected_at: str = Field(default="", description="连接时间，ISO 8601 格式")
    expires_at: str | None = Field(default=None, description="过期时间，ISO 8601 格式")


class ListConnectionsQuery(PageQuery):
    """T3 已连接工具列表查询参数。

    Attributes:
        tool_type: 按工具类型过滤。
    """

    tool_type: str = Field(default="", max_length=64, description="按工具类型过滤")


class ExecuteRequest(BaseModel):
    """T5 执行工具操作请求。

    Attributes:
        tool_type: 工具类型标识。
        operation: 操作标识。
        params: 操作参数。
    """

    tool_type: str = Field(min_length=1, max_length=64, description="工具类型标识")
    operation: str = Field(min_length=1, max_length=128, description="操作标识")
    params: dict[str, Any] = Field(default_factory=dict, description="操作参数")


class ExecutionData(BaseModel):
    """T5 响应数据。

    Attributes:
        execution_id: 执行记录 ID，形如 `exec_01HF8...`。
        tool_type: 工具类型标识。
        operation: 操作标识。
        status: 执行状态。
        result: 执行结果（业务化）。
        error: 失败原因（业务语言），成功时为 `None`。
    """

    execution_id: str = Field(description="执行记录 ID，形如 exec_01HF8...")
    tool_type: str = Field(default="", description="工具类型标识")
    operation: str = Field(default="", description="操作标识")
    status: ExecutionStatus = Field(default=ExecutionStatus.SUCCESS, description="执行状态")
    result: dict[str, Any] | None = Field(default=None, description="执行结果")
    error: str | None = Field(default=None, description="失败原因，业务语言")


class CreateSyncJobRequest(BaseModel):
    """T6 创建数据同步任务请求。

    Attributes:
        tool_type: 工具类型标识。
        operation: 同步操作标识。
        scope: 同步范围（如 `inbox`）。
        params: 同步参数。
    """

    tool_type: str = Field(min_length=1, max_length=64, description="工具类型标识")
    operation: str = Field(default="sync", min_length=1, max_length=128, description="同步操作标识")
    scope: str = Field(default="", max_length=128, description="同步范围")
    params: dict[str, Any] = Field(default_factory=dict, description="同步参数")


class SyncJobData(BaseModel):
    """T6 响应数据。

    Attributes:
        job_id: 同步任务 ID，形如 `job_01HF8...`。
        tool_type: 工具类型标识。
        status: 任务状态。
        progress: 进度百分比。
        created_at: 创建时间。
    """

    job_id: str = Field(description="同步任务 ID，形如 job_01HF8...")
    tool_type: str = Field(default="", description="工具类型标识")
    status: SyncJobStatus = Field(default=SyncJobStatus.PENDING, description="任务状态")
    progress: int = Field(default=0, ge=0, le=100, description="进度百分比")
    created_at: str = Field(default="", description="创建时间，ISO 8601 格式")


class SyncJobDetailData(SyncJobData):
    """T7 同步任务进度响应数据。

    Attributes:
        processed_count: 已处理条目数。
        total_count: 总条目数。
        error: 失败原因（业务语言）。
        updated_at: 更新时间。
    """

    processed_count: int = Field(default=0, ge=0, description="已处理条目数")
    total_count: int = Field(default=0, ge=0, description="总条目数")
    error: str | None = Field(default=None, description="失败原因，业务语言")
    updated_at: str = Field(default="", description="更新时间，ISO 8601 格式")
