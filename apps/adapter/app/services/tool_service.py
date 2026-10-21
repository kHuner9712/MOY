"""工具服务（架构文档 3.4 / 3.6 类图 `ToolService`）。

**数据驱动模式（架构文档第 4 章 Q1）**：本服务不含任何 `if tool_type == ...` 分支，
工具类型信息全部来自 `registry/tool_registry.yaml`，连接器实现全部来自 `tools/`。
新增工具 = 追加 YAML 条目 + 新增连接器，**T1-T7 端点零改动**。

T01 阶段说明：连接器为桩实现，不发起任何真实第三方调用。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.registry.loader import ToolRegistry, ToolSpecModel, get_tool_registry
from app.schemas.common import build_page
from app.schemas.tool import (
    ConnectionData,
    ConnectionItem,
    ConnectionStatus,
    CreateConnectionRequest,
    CreateSyncJobRequest,
    ExecutionData,
    ExecutionStatus,
    ListConnectionsQuery,
    RegistryData,
    SyncJobData,
    SyncJobDetailData,
    SyncJobStatus,
    ToolCategory,
    ToolItem,
    ToolOperation,
    ToolStatus,
)
from app.tools.base import ConnectorRegistry, ToolConnector

logger = get_logger(__name__)

#: 资源 ID 前缀（架构文档 9.1）
_CONN_PREFIX: Final[str] = "conn_"
_EXEC_PREFIX: Final[str] = "exec_"
_JOB_PREFIX: Final[str] = "job_"

#: 授权有效期（天）
_DEFAULT_VALID_DAYS: Final[int] = 180

#: 连接器实现延迟导入映射（T01 仅两个示例连接器）
_CONNECTOR_MODULES: Final[tuple[str, ...]] = (
    "app.tools.email_connector",
    "app.tools.github_connector",
)


def new_connection_id() -> str:
    """生成连接 ID。

    Returns:
        str: 形如 `conn_01HF8...` 的 ID。
    """
    return f"{_CONN_PREFIX}{uuid.uuid4().hex[:24].upper()}"


def new_execution_id() -> str:
    """生成执行记录 ID。

    Returns:
        str: 形如 `exec_01HF8...` 的 ID。
    """
    return f"{_EXEC_PREFIX}{uuid.uuid4().hex[:24].upper()}"


def new_job_id() -> str:
    """生成同步任务 ID。

    Returns:
        str: 形如 `job_01HF8...` 的 ID。
    """
    return f"{_JOB_PREFIX}{uuid.uuid4().hex[:24].upper()}"


def _now_iso() -> str:
    """获取当前 UTC 时间的 ISO 8601 字符串。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _load_connectors() -> None:
    """加载并注册全部工具连接器。

    延迟导入避免 `app.tools` 与 `app.services` 之间的循环依赖；
    新增连接器只需在本模块的 `_CONNECTOR_MODULES` 中登记模块路径。
    """
    import importlib

    for module_path in _CONNECTOR_MODULES:
        if module_path.rsplit(".", 1)[-1] in ConnectorRegistry.all():
            continue
        importlib.import_module(module_path)


class ToolService:
    """工具服务。

    Attributes:
        _settings: 配置实例。
        _registry: 工具类型注册表。
        _connections: 桩连接表。
        _jobs: 桩同步任务表。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            registry: 可选工具类型注册表。
        """
        self._settings = settings or get_settings()
        self._registry = registry or get_tool_registry()
        _load_connectors()
        self._connections: dict[str, ConnectionItem] = {}
        self._jobs: dict[str, SyncJobDetailData] = {}

    # ------------------------------------------------------------------
    # 注册表查询
    # ------------------------------------------------------------------
    def _get_spec(self, tool_type: str) -> ToolSpecModel:
        """按工具类型查询注册条目。

        Args:
            tool_type: 工具类型标识。

        Returns:
            ToolSpecModel: 注册条目。

        Raises:
            ApiError: 工具类型未注册（13001）。
        """
        spec = self._registry.get(tool_type)
        if spec is None:
            raise ApiError(
                ErrorCode.TOOL_TYPE_UNSUPPORTED, internal_detail=f"tool_type={tool_type}"
            )
        return spec

    def _get_connector(self, spec: ToolSpecModel) -> ToolConnector:
        """按注册条目获取连接器实例。

        Args:
            spec: 工具注册条目。

        Returns:
            ToolConnector: 连接器实例。

        Raises:
            ApiError: 连接器实现未注册（30004 配置异常）。
        """
        connector_cls = ConnectorRegistry.get(spec.connector_impl)
        if connector_cls is None:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=f"connector not registered: {spec.connector_impl}",
            )
        return connector_cls()

    # ------------------------------------------------------------------
    # T1 工具类型注册表
    # ------------------------------------------------------------------
    def list_registry(self, auth: AuthContext) -> RegistryData:
        """查询工具类型注册表。

        前端自描述：插件市场/设置页按本返回动态渲染工具卡片与授权引导，
        不硬编码工具列表（架构文档 4.1）。

        Args:
            auth: 鉴权上下文。

        Returns:
            RegistryData: 工具类型列表。

        Raises:
            ApiError: 工具分类配置非法（30004）。
        """
        del auth  # 注册表为租户无关的全局配置
        tools: list[ToolItem] = []
        for spec in self._registry.tools:
            try:
                category = ToolCategory(spec.category)
            except ValueError as exc:
                raise ApiError(
                    ErrorCode.CONFIG_ERROR, internal_detail=f"bad category: {spec.category}"
                ) from exc
            tools.append(
                ToolItem(
                    tool_type=spec.tool_type,
                    display_name=spec.display_name,
                    category=category,
                    auth_type=spec.auth_type,
                    status=(
                        ToolStatus.AVAILABLE
                        if spec.status == "available"
                        else ToolStatus.UNAVAILABLE
                    ),
                    operations=[
                        ToolOperation(
                            operation=str(op.get("operation", "")),
                            display_name=str(op.get("display_name", "")),
                            params_schema=op.get("params_schema", {"type": "object"}),
                        )
                        for op in spec.operations
                    ],
                    timeout_ms=spec.timeout_ms,
                )
            )
        return RegistryData(tools=tools)

    # ------------------------------------------------------------------
    # T2 建立工具连接
    # ------------------------------------------------------------------
    async def create_connection(
        self, request: CreateConnectionRequest, auth: AuthContext
    ) -> ConnectionData:
        """发起工具授权连接。

        Args:
            request: 连接请求。
            auth: 鉴权上下文。

        Returns:
            ConnectionData: 连接 ID 与状态。

        Raises:
            ApiError: 工具类型未注册（13001）、授权码无效（13003）、第三方异常（13006）。
        """
        spec = self._get_spec(request.tool_type)
        connector = self._get_connector(spec)
        result = await connector.connect(
            auth_code=request.auth_code,
            redirect_uri=request.redirect_uri,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
        )
        connection_id = str(result.get("connection_id") or new_connection_id())
        expires_at = str(result.get("expires_at") or _default_expiry())
        self._connections[connection_id] = ConnectionItem(
            connection_id=connection_id,
            tool_type=spec.tool_type,
            display_name=spec.display_name,
            status=ConnectionStatus.CONNECTED,
            connected_at=_now_iso(),
            expires_at=expires_at,
        )
        logger.info(
            "tool_connection_created",
            tool_type=spec.tool_type,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
        )
        return ConnectionData(
            connection_id=connection_id,
            tool_type=spec.tool_type,
            status=ConnectionStatus.CONNECTED,
            expires_at=expires_at,
        )

    # ------------------------------------------------------------------
    # T3 已连接工具列表
    # ------------------------------------------------------------------
    def list_connections(self, query: ListConnectionsQuery, auth: AuthContext) -> Any:
        """查询已连接工具列表。

        Args:
            query: 分页与工具类型过滤参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[ConnectionItem]` 分页结果。
        """
        del auth  # T01 桩模式不持有租户维度状态
        items = [
            item
            for item in self._connections.values()
            if not query.tool_type or item.tool_type == query.tool_type
        ]
        start = query.offset
        page_items = items[start : start + query.page_size]
        return build_page(
            items=page_items, total=len(items), page=query.page, page_size=query.page_size
        )

    # ------------------------------------------------------------------
    # T4 断开工具连接
    # ------------------------------------------------------------------
    async def delete_connection(self, conn_id: str, auth: AuthContext) -> str:
        """断开工具连接。

        Args:
            conn_id: 连接 ID。
            auth: 鉴权上下文。

        Returns:
            str: 被断开的连接 ID。

        Raises:
            ApiError: 连接不存在（13002）。
        """
        item = self._connections.get(conn_id)
        spec = self._get_spec(item.tool_type) if item else None
        connector = self._get_connector(spec) if spec else None
        if connector is not None:
            await connector.disconnect(
                connection_id=conn_id, tenant_id=auth.tenant_id, user_id=auth.user_id
            )
        if conn_id not in self._connections:
            raise ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail=f"conn_id={conn_id}")
        self._connections.pop(conn_id, None)
        logger.info("tool_connection_deleted", conn_id=conn_id)
        return conn_id

    # ------------------------------------------------------------------
    # T5 执行工具操作
    # ------------------------------------------------------------------
    async def execute(self, request: Any, auth: AuthContext) -> ExecutionData:
        """执行工具操作。

        Args:
            request: 执行请求（`ExecuteRequest`）。
            auth: 鉴权上下文。

        Returns:
            ExecutionData: 执行结果（`error` 为业务语言）。

        Raises:
            ApiError: 工具未连接（13002）、操作不支持（13005）、工具无响应（13004）。
        """
        spec = self._get_spec(request.tool_type)
        if not spec.has_operation(request.operation):
            raise ApiError(
                ErrorCode.TOOL_OPERATION_UNSUPPORTED,
                internal_detail=f"tool_type={spec.tool_type} operation={request.operation}",
            )
        connection = self._resolve_connection(spec.tool_type)
        connector = self._get_connector(spec)
        result = await connector.execute(
            connection_id=connection.connection_id,
            operation=request.operation,
            params=request.params,
            timeout_ms=spec.timeout_ms,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
        )
        logger.info(
            "tool_execution_completed",
            tool_type=spec.tool_type,
            operation=request.operation,
            tenant_id=auth.tenant_id,
        )
        return ExecutionData(
            execution_id=new_execution_id(),
            tool_type=spec.tool_type,
            operation=request.operation,
            status=ExecutionStatus.SUCCESS,
            result=result,
            error=None,
        )

    def _resolve_connection(self, tool_type: str) -> ConnectionItem:
        """解析工具的有效连接。

        Args:
            tool_type: 工具类型标识。

        Returns:
            ConnectionItem: 有效连接。

        Raises:
            ApiError: 连接不存在或已过期（13002）。
        """
        for item in self._connections.values():
            if item.tool_type == tool_type and item.status == ConnectionStatus.CONNECTED:
                return item
        raise ApiError(
            ErrorCode.TOOL_NOT_CONNECTED, internal_detail=f"tool_type={tool_type} not connected"
        )

    # ------------------------------------------------------------------
    # T6 创建数据同步任务
    # ------------------------------------------------------------------
    async def create_sync_job(
        self, request: CreateSyncJobRequest, auth: AuthContext
    ) -> SyncJobData:
        """创建数据同步任务。

        Args:
            request: 创建请求。
            auth: 鉴权上下文。

        Returns:
            SyncJobData: 同步任务 ID 与初始状态。

        Raises:
            ApiError: 工具未连接（13002）、操作不支持（13005）、第三方异常（13006）。
        """
        spec = self._get_spec(request.tool_type)
        if not spec.has_operation(request.operation):
            raise ApiError(
                ErrorCode.TOOL_OPERATION_UNSUPPORTED,
                internal_detail=f"tool_type={spec.tool_type} operation={request.operation}",
            )
        connection = self._resolve_connection(spec.tool_type)
        connector = self._get_connector(spec)
        result = await connector.sync(
            connection_id=connection.connection_id,
            scope=request.scope,
            params=request.params,
            timeout_ms=spec.timeout_ms,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
        )
        job_id = new_job_id()
        job = SyncJobDetailData(
            job_id=job_id,
            tool_type=spec.tool_type,
            status=SyncJobStatus.PENDING,
            progress=0,
            created_at=_now_iso(),
            processed_count=0,
            total_count=int(result.get("total_count", 0)),
            error=None,
            updated_at=_now_iso(),
        )
        self._jobs[job_id] = job
        logger.info("tool_sync_job_created", tool_type=spec.tool_type, job_id=job_id)
        return SyncJobData(
            job_id=job_id,
            tool_type=spec.tool_type,
            status=job.status,
            progress=job.progress,
            created_at=job.created_at,
        )

    # ------------------------------------------------------------------
    # T7 查询同步任务进度
    # ------------------------------------------------------------------
    def get_sync_job(self, job_id: str, auth: AuthContext) -> SyncJobDetailData:
        """查询同步任务进度。

        Args:
            job_id: 同步任务 ID。
            auth: 鉴权上下文。

        Returns:
            SyncJobDetailData: 同步任务详情。

        Raises:
            ApiError: 任务不存在（20004）。
        """
        del auth  # T01 桩模式不持有租户维度状态
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"job_id={job_id}")
        return job


def _default_expiry() -> str:
    """计算默认授权过期时间。

    Returns:
        str: ISO 8601 格式的过期时间。
    """
    return (
        (datetime.now(UTC) + timedelta(days=_DEFAULT_VALID_DAYS)).isoformat().replace("+00:00", "Z")
    )


_service: ToolService | None = None


def get_tool_service() -> ToolService:
    """获取工具服务单例。

    Returns:
        ToolService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = ToolService()
    return _service


def reset_tool_service() -> None:
    """重置工具服务单例，供测试隔离用例。"""
    global _service
    _service = None
