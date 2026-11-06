"""MCP 运营接口 DTO（矩阵第 3 项，2026-11-06）。

**最小可见原则**：响应模型里没有 `credential_ref`、没有密文、没有凭据明文，
只给 `credential_configured: bool`；请求模型接受引用但拒绝密钥形态
（校验在 `business/mcp_service`，模型层只做形态约束）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    # 仅类型标注用：依赖矩阵只允许 `schemas → core`，运行期不导入仓储。
    from app.db.repositories.mcp_repo import McpServerRecord

#: 连接方式（legacy SSE 不在枚举里：登记层直接拒绝）
McpTransport = Literal["http", "stdio"]


class McpServerItem(BaseModel):
    """一条 MCP server 登记（不含任何凭据字段）。

    Attributes:
        server_id: 登记 ID。
        tenant_id: 所属企业。
        name: 服务器名（插件动作标识的前半段）。
        transport: 连接方式。
        endpoint: 地址或本地程序路径。
        args: 本地程序的启动内容。
        allowed_tools: 允许的工具白名单。
        enabled: 是否启用。
        timeout_ms: 单次超时。
        max_output_bytes: 返回内容上限。
        credential_configured: 是否配置了凭据（**不返回引用本身**）。
        created_at: 创建时间。
        updated_at: 最近更新时间。
    """

    server_id: str
    tenant_id: str
    name: str
    transport: McpTransport
    endpoint: str
    args: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool
    timeout_ms: int
    max_output_bytes: int
    credential_configured: bool
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_record(cls, record: McpServerRecord) -> McpServerItem:
        """从仓储记录构造。

        Args:
            record: 登记记录。

        Returns:
            McpServerItem: 响应项。
        """
        return cls(
            server_id=record.server_id,
            tenant_id=record.tenant_id,
            name=record.name,
            transport=record.transport,
            endpoint=record.endpoint,
            args=list(record.args),
            allowed_tools=list(record.allowed_tools),
            enabled=record.enabled,
            timeout_ms=record.timeout_ms,
            max_output_bytes=record.max_output_bytes,
            credential_configured=bool(record.credential_ref),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class McpServerListData(BaseModel):
    """登记列表。

    Attributes:
        items: 登记项。
        total: 总数。
    """

    items: list[McpServerItem] = Field(default_factory=list)
    total: int = 0


class McpServerWriteData(BaseModel):
    """登记写入结果。

    Attributes:
        item: 落库后的登记。
    """

    item: McpServerItem


class UpsertMcpServerRequest(BaseModel):
    """登记/更新一台 MCP server（声明式）。

    Attributes:
        tenant_id: 目标企业。
        server_id: 更新时传既有 ID；不传则新建。
        name: 服务器名。
        transport: 连接方式。
        endpoint: 地址或本地程序路径。
        args: 本地程序启动内容。
        credential_ref: 凭据编号（**只接受引用形态**）。
        allowed_tools: 工具白名单。
        enabled: 是否启用（默认关）。
        timeout_ms: 单次超时。
        max_output_bytes: 返回内容上限。
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=2, max_length=64)
    server_id: str = Field(default="", max_length=64)
    name: str = Field(min_length=2, max_length=32)
    transport: McpTransport
    endpoint: str = Field(min_length=1, max_length=2048)
    args: list[str] = Field(default_factory=list, max_length=16)
    credential_ref: str = Field(default="", max_length=128)
    allowed_tools: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = False
    timeout_ms: int | None = Field(default=None, ge=1_000, le=120_000)
    max_output_bytes: int | None = Field(default=None, ge=1_024, le=1_048_576)


class DeleteMcpServerData(BaseModel):
    """删除结果。

    Attributes:
        deleted: 是否已删除。
    """

    deleted: bool


class ProbeMcpServerData(BaseModel):
    """连通性探测结果（不含地址与凭据）。

    Attributes:
        ok: 是否连通。
        reason: 失败分类（`connect_failed` / `timeout` / `auth_failed` / `protocol_error`）。
        duration_ms: 耗时。
        tool_count: 白名单内可用工具数。
        tools: 白名单内可用工具名。
    """

    ok: bool
    reason: str = ""
    duration_ms: int = 0
    tool_count: int = 0
    tools: list[str] = Field(default_factory=list)


__all__: list[str] = [
    "DeleteMcpServerData",
    "McpServerItem",
    "McpServerListData",
    "McpServerWriteData",
    "McpTransport",
    "ProbeMcpServerData",
    "UpsertMcpServerRequest",
]
