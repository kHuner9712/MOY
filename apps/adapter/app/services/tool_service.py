"""工具服务（架构文档 3.4 / 3.6 类图 `ToolService`）。

**数据驱动模式（架构文档第 4 章 Q1）**：本服务不含任何 `if tool_type == ...` 分支，
工具类型信息全部来自 `registry/tool_registry.yaml`，连接器实现全部来自 `tools/`。
新增工具 = 追加 YAML 条目 + 新增连接器，**T1-T7 端点零改动**。

阶段二 B06 落库边界（架构文档 9.2 B06 第 6 条）：

| 动作 | 落库 |
| --- | --- |
| T2/T4 连接与断开 | `tool_connection`（`uq_conn_active` 保证同一工具同一使用者只有一条有效连接） |
| T5/T6 调用 | `tool_execution`（成功与失败都记；**失败只记业务语言原因，禁止堆栈**） |
| TL1 调用记录 | 读 `tool_execution`（最近 20 条，按时间倒序） |

错误码映射（架构文档 3.0.3 工具域）：

- `13002` 未连接（含工具专属文案「企业微信尚未连接，请先在设置中完成授权」）；
- `13003` 授权已过期；`13004` 工具无响应（含连接器未预期异常）；
- `13005` 操作不在能力清单；`13006` 第三方业务错误。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.tool_repo import (
    EXECUTION_FAILED,
    EXECUTION_SUCCESS,
    ToolConnectionRecord,
    ToolExecutionRecord,
    ToolStore,
    get_tool_store,
    summarize_result,
)
from app.registry.loader import ToolRegistry, ToolSpecModel, get_tool_registry
from app.schemas.common import PageResult, build_page
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
from app.schemas.tool_ext import ListExecutionsQuery, ToolExecutionItem
from app.tools.base import ConnectorRegistry, ToolConnector

logger = get_logger(__name__)

#: 资源 ID 前缀（架构文档 9.1）
_CONN_PREFIX: Final[str] = "conn_"
_EXEC_PREFIX: Final[str] = "exec_"
_JOB_PREFIX: Final[str] = "job_"

#: 授权有效期（天）
_DEFAULT_VALID_DAYS: Final[int] = 180

#: 连接器实现延迟导入映射（数据驱动：新增连接器在此登记模块路径）
_CONNECTOR_MODULES: Final[tuple[str, ...]] = (
    "app.tools.email_connector",
    "app.tools.github_connector",
    "app.tools.wecom_connector",
    "app.tools.feishu_connector",
    "app.tools.wechat_mp_connector",
)

#: 失败调用记录的兜底业务文案（绝不写堆栈或错误码）
_FAILURE_FALLBACK_MESSAGE: Final[str] = "该操作未能完成，请稍后重试"


def new_connection_id() -> str:
    """生成连接 ID。

    Returns:
        str: 形如 `conn_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID（架构文档 9.1）。
    """
    return new_resource_id(_CONN_PREFIX)


def new_execution_id() -> str:
    """生成执行记录 ID。

    Returns:
        str: 形如 `exec_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID（架构文档 9.1）。
    """
    return new_resource_id(_EXEC_PREFIX)


def new_job_id() -> str:
    """生成同步任务 ID。

    Returns:
        str: 形如 `job_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID（架构文档 9.1）。
    """
    return new_resource_id(_JOB_PREFIX)


def _now_iso() -> str:
    """获取当前 UTC 时间的 ISO 8601 字符串。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_expiry() -> str:
    """计算默认授权过期时间。

    Returns:
        str: ISO 8601 格式的过期时间。
    """
    return (
        (datetime.now(UTC) + timedelta(days=_DEFAULT_VALID_DAYS)).isoformat().replace("+00:00", "Z")
    )


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
        _store: 连接与调用记录存储（内存 / PostgreSQL）。
        _jobs: 同步任务表（短生命周期状态，不落业务库）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        registry: ToolRegistry | None = None,
        store: ToolStore | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            registry: 可选工具类型注册表。
            store: 可选连接与调用记录存储（测试注入）。
        """
        self._settings = settings or get_settings()
        self._registry = registry or get_tool_registry()
        self._store: ToolStore = store or get_tool_store(self._settings)
        _load_connectors()
        self._jobs: dict[str, SyncJobDetailData] = {}
        self._assert_connector_consistency()

    # ------------------------------------------------------------------
    # 注册表与连接器
    # ------------------------------------------------------------------
    def _assert_connector_consistency(self) -> None:
        """校验「注册表能力清单」与「连接器能力声明」一致（启动期暴露缺口）。

        Raises:
            ApiError: 任一工具的声明不一致（30004）。
        """
        for spec in self._registry.tools:
            connector_cls = ConnectorRegistry.get(spec.connector_impl)
            if connector_cls is None:
                # 未实现的连接器由 T1/T5 处理为 30004，此处不阻断（便于局部灰度）
                continue
            connector_cls.assert_registry_consistency(spec.operation_names())

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

    @staticmethod
    def _operation_display_name(spec: ToolSpecModel, operation: str) -> str:
        """取操作的用户可见名称（未登记时回落为操作标识）。

        Args:
            spec: 工具注册条目。
            operation: 操作标识。

        Returns:
            str: 业务化操作名称。
        """
        for item in spec.operations:
            if str(item.get("operation", "")) == operation:
                return str(item.get("display_name", "")) or operation
        return operation

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
        """发起工具授权连接并落库。

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
        await self._store.upsert_connection(
            ToolConnectionRecord(
                tenant_id=auth.tenant_id,
                conn_id=connection_id,
                tool_type=spec.tool_type,
                scope="personal",
                owner_user_id=auth.user_id,
                expires_at=expires_at,
            )
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
    async def list_connections(
        self, query: ListConnectionsQuery, auth: AuthContext
    ) -> PageResult[ConnectionItem]:
        """查询已连接工具列表（分页）。

        Args:
            query: 分页与工具类型过滤参数。
            auth: 鉴权上下文。

        Returns:
            PageResult[ConnectionItem]: 分页结果。
        """
        records, total = await self._store.list_connections(
            auth.tenant_id,
            tool_type=query.tool_type,
            limit=query.page_size,
            offset=query.offset,
        )
        items = [self._to_connection_item(record) for record in records]
        return build_page(items=items, total=total, page=query.page, page_size=query.page_size)

    def _to_connection_item(self, record: ToolConnectionRecord) -> ConnectionItem:
        """把连接记录转换为对外条目。

        Args:
            record: 连接记录。

        Returns:
            ConnectionItem: 对外条目。
        """
        spec = self._registry.get(record.tool_type)
        return ConnectionItem(
            connection_id=record.conn_id,
            tool_type=record.tool_type,
            display_name=spec.display_name if spec is not None else record.tool_type,
            status=_connection_status_of(record.status),
            connected_at=record.created_at,
            expires_at=record.expires_at,
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
        record = await self._store.get_connection(auth.tenant_id, conn_id)
        if record is None:
            raise ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail=f"conn_id={conn_id}")
        spec = self._registry.get(record.tool_type)
        if spec is not None:
            connector = self._get_connector(spec)
            await connector.disconnect(
                connection_id=conn_id, tenant_id=auth.tenant_id, user_id=auth.user_id
            )
        await self._store.revoke_connection(auth.tenant_id, conn_id)
        logger.info("tool_connection_deleted", conn_id=conn_id, tenant_id=auth.tenant_id)
        return conn_id

    # ------------------------------------------------------------------
    # T5 执行工具操作
    # ------------------------------------------------------------------
    async def execute(self, request: Any, auth: AuthContext) -> ExecutionData:
        """执行工具操作并落调用记录（成功与失败都记）。

        Args:
            request: 执行请求（`ExecuteRequest`）。
            auth: 鉴权上下文。

        Returns:
            ExecutionData: 执行结果（`error` 为业务语言）。

        Raises:
            ApiError: 工具未连接（13002）、授权过期（13003）、操作不支持（13005）、
                工具无响应（13004）、第三方业务错误（13006）。
        """
        spec = self._get_spec(request.tool_type)
        if not spec.has_operation(request.operation):
            raise ApiError(
                ErrorCode.TOOL_OPERATION_UNSUPPORTED,
                internal_detail=f"tool_type={spec.tool_type} operation={request.operation}",
            )
        connection = await self._resolve_connection(spec, auth)
        connector = self._get_connector(spec)
        execution_id = new_execution_id()
        started = time.monotonic()
        try:
            result = await connector.execute(
                connection_id=connection.conn_id,
                operation=request.operation,
                params=request.params,
                timeout_ms=spec.timeout_ms,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
            )
        except ApiError as exc:
            await self._record_failure(
                execution_id=execution_id,
                spec=spec,
                connection=connection,
                operation=request.operation,
                auth=auth,
                exc=exc,
                started=started,
            )
            raise
        except Exception as exc:
            mapped = ApiError(
                ErrorCode.TOOL_UNRESPONSIVE, internal_detail=f"connector error {type(exc).__name__}"
            )
            await self._record_failure(
                execution_id=execution_id,
                spec=spec,
                connection=connection,
                operation=request.operation,
                auth=auth,
                exc=mapped,
                started=started,
            )
            raise mapped from exc

        await self._record_success(
            execution_id=execution_id,
            spec=spec,
            connection=connection,
            operation=request.operation,
            auth=auth,
            result=result,
            started=started,
        )
        logger.info(
            "tool_execution_completed",
            tool_type=spec.tool_type,
            operation=request.operation,
            tenant_id=auth.tenant_id,
        )
        return ExecutionData(
            execution_id=execution_id,
            tool_type=spec.tool_type,
            operation=request.operation,
            status=ExecutionStatus.SUCCESS,
            result=result,
            error=None,
        )

    async def _record_success(
        self,
        *,
        execution_id: str,
        spec: ToolSpecModel,
        connection: ToolConnectionRecord,
        operation: str,
        auth: AuthContext,
        result: dict[str, Any],
        started: float,
    ) -> None:
        """写入成功调用记录（写失败只记 WARN，不影响已完成的业务动作）。

        Args:
            execution_id: 调用记录 ID。
            spec: 工具注册条目。
            connection: 使用的连接。
            operation: 操作标识。
            auth: 鉴权上下文。
            result: 连接器返回的业务结果。
            started: 起始时刻（`time.monotonic()`）。
        """
        await self._safe_record(
            ToolExecutionRecord(
                tenant_id=auth.tenant_id,
                execution_id=execution_id,
                tool_type=spec.tool_type,
                operation=operation,
                status=EXECUTION_SUCCESS,
                conn_id=connection.conn_id,
                result_summary=summarize_result(result),
                operator_user_id=auth.user_id,
                duration_ms=_elapsed_ms(started),
            )
        )

    async def _record_failure(
        self,
        *,
        execution_id: str,
        spec: ToolSpecModel,
        connection: ToolConnectionRecord,
        operation: str,
        auth: AuthContext,
        exc: ApiError,
        started: float,
    ) -> None:
        """写入失败调用记录（只存业务语言原因，禁止堆栈与错误码外泄）。

        Args:
            execution_id: 调用记录 ID。
            spec: 工具注册条目。
            connection: 使用的连接。
            operation: 操作标识。
            auth: 鉴权上下文。
            exc: 业务异常。
            started: 起始时刻（`time.monotonic()`）。
        """
        logger.warning(
            "tool_execution_failed",
            tool_type=spec.tool_type,
            operation=operation,
            detail=exc.log_message,
        )
        await self._safe_record(
            ToolExecutionRecord(
                tenant_id=auth.tenant_id,
                execution_id=execution_id,
                tool_type=spec.tool_type,
                operation=operation,
                status=EXECUTION_FAILED,
                conn_id=connection.conn_id,
                error_code=int(exc.code),
                error_message=exc.message or _FAILURE_FALLBACK_MESSAGE,
                operator_user_id=auth.user_id,
                duration_ms=_elapsed_ms(started),
            )
        )

    async def _safe_record(self, record: ToolExecutionRecord) -> None:
        """写调用记录：失败只记 WARN（调用记录是旁路事实，不能反噬业务）。

        Args:
            record: 调用记录。
        """
        try:
            await self._store.record_execution(record)
        except Exception as exc:
            logger.warning(
                "tool_execution_record_failed",
                execution_id=record.execution_id,
                reason=type(exc).__name__,
            )

    async def _resolve_connection(
        self, spec: ToolSpecModel, auth: AuthContext
    ) -> ToolConnectionRecord:
        """解析工具的有效连接（个人连接优先）。

        Args:
            spec: 工具注册条目。
            auth: 鉴权上下文。

        Returns:
            ToolConnectionRecord: 有效连接。

        Raises:
            ApiError: 连接不存在（13002，文案含工具名）。
        """
        record = await self._store.find_active_connection(
            auth.tenant_id, spec.tool_type, user_id=auth.user_id
        )
        if record is None:
            raise ApiError(
                ErrorCode.TOOL_NOT_CONNECTED,
                message=f"{spec.display_name}尚未连接，请先在设置中完成授权",
                internal_detail=f"tool_type={spec.tool_type} not connected",
            )
        return record

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
        connection = await self._resolve_connection(spec, auth)
        connector = self._get_connector(spec)
        result = await connector.sync(
            connection_id=connection.conn_id,
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
        del auth  # 同步任务为短生命周期状态，按 job_id 直取
        job = self._jobs.get(job_id)
        if job is None:
            raise ApiError(ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"job_id={job_id}")
        return job

    # ------------------------------------------------------------------
    # TL1 调用记录
    # ------------------------------------------------------------------
    async def list_executions(
        self, query: ListExecutionsQuery, auth: AuthContext
    ) -> PageResult[ToolExecutionItem]:
        """查询最近的工具调用记录（业务语言，失败项无堆栈、无错误码）。

        Args:
            query: 分页与工具类型过滤参数。
            auth: 鉴权上下文。

        Returns:
            PageResult[ToolExecutionItem]: 分页结果。
        """
        records, total = await self._store.list_executions(
            auth.tenant_id,
            tool_type=query.tool_type,
            limit=query.page_size,
            offset=query.offset,
        )
        items = [self._to_execution_item(record) for record in records]
        logger.info(
            "tool_executions_loaded",
            total=total,
            tool_type=query.tool_type,
            tenant_id=auth.tenant_id,
        )
        return build_page(items=items, total=total, page=query.page, page_size=query.page_size)

    def _to_execution_item(self, record: ToolExecutionRecord) -> ToolExecutionItem:
        """把调用记录转换为对外条目。

        Args:
            record: 调用记录。

        Returns:
            ToolExecutionItem: 对外条目。
        """
        spec = self._registry.get(record.tool_type)
        display_name = spec.display_name if spec is not None else record.tool_type
        operation_name = (
            self._operation_display_name(spec, record.operation)
            if spec is not None
            else record.operation
        )
        return ToolExecutionItem(
            execution_id=record.execution_id,
            tool_type=record.tool_type,
            tool_display_name=display_name,
            operation=record.operation,
            operation_display_name=operation_name,
            status=ExecutionStatus(record.status),
            error_message=record.error_message,
            executed_at=record.executed_at,
            operator_name=record.operator_user_id,
        )


def _elapsed_ms(started: float) -> int:
    """计算耗时（毫秒）。

    Args:
        started: 起始时刻（`time.monotonic()`）。

    Returns:
        int: 耗时毫秒数（非负）。
    """
    return max(int((time.monotonic() - started) * 1000), 0)


def _connection_status_of(value: str) -> ConnectionStatus:
    """把存储值转换为连接状态枚举。

    Args:
        value: 存储值。

    Returns:
        ConnectionStatus: 枚举值；未知值回落为 `expired`（提示重连，不误报已连接）。
    """
    try:
        return ConnectionStatus(str(value))
    except ValueError:
        return ConnectionStatus.EXPIRED


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
