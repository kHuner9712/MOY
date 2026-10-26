"""插件任务编排 · 门面（`TaskService`，切片 2C-3b）。

**门面只做三件事**：装配（`TaskDeps` + 引擎 + 三个组件）、**属性代理**（11 个）、
**方法转发**（7 个公共 + 3 个被既有调用方/测试依赖的私有入口）。它不持有任何业务逻辑，
因此"谁能改任务状态"依然只有 `TaskRunner` 一处。

**为什么属性要做成代理而不是普通字段**：三拆前 `TaskService._usage` /
`_step_timeout_seconds` 是普通属性，测试在**构造之后**写它们
（`test_quota_notice.py:367`、`test_task_step_lock.py:96`）。代理的 setter 把写入转给共享
`TaskDeps`，Runner/Query 每次调用现取 `deps.*`，于是"构造后写回"依然生效；
`api/v1/agent.py:114` 的 `getattr(service, "_tasks")` 也照常读得到。

**引擎只建一次**：`engine=` 显式注入优先；否则由组合根注入的 `gateway_factory` 在
`__init__` 里**立即**装配（装配失败要在启动期暴露，不能等第一个任务进来才炸）。
组件不得再建引擎。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.business.audit_service import AuditService, get_audit_service
from app.business.plugin_grant_service import PluginGrantService, get_plugin_grant_service
from app.business.plugin_service import PluginService, get_plugin_service
from app.business.task.deps import TaskDeps
from app.business.task.manifest import TaskManifestResolver
from app.business.task.query import TaskQuery
from app.business.task.runner import TaskRunner
from app.business.usage_service import UsageService, get_usage_service
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.artifact_repo import ArtifactRecord, ArtifactStore, get_artifact_store
from app.db.repositories.task_repo import StepRecord, TaskRecord, TaskStore, get_task_store
from app.plugins.actions import ActionGateway
from app.plugins.engine import PluginEngine
from app.plugins.loader import PluginRegistry, get_registry
from app.plugins.sanitizer import ArtifactSanitizer
from app.schemas.agent import (
    CreateTaskData,
    CreateTaskRequest,
    ListTasksQuery,
    StepsData,
    TaskDetailData,
)
from app.schemas.task import (
    ArtifactEditData,
    ArtifactEditRequest,
    RetryTaskData,
    RetryTaskRequest,
)


class TaskService:
    """插件任务服务门面（建任务 / 推进 / 重试 / 视图 / 产物编辑）。

    Attributes:
        _settings: 配置实例（代理到 `TaskDeps.settings`）。
        _tasks: 任务仓储（代理）。
        _artifacts: 产物仓储（代理）。
        _registry: 声明注册表（代理）。
        _grants: 授权服务（代理）。
        _plugins: 插件服务（安装态 / 套餐 / 声明；代理）。
        _engine: 步骤引擎（代理）。
        _audit: 审计服务（代理）。
        _usage: 计量服务（**读写代理**：测试构造后写回，组件现取）。
        _sanitizer: 产物脱敏器（代理）。
        _step_timeout_seconds: 单步超时（**读写代理**：测试构造后写回，组件现取）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        tasks: TaskStore | None = None,
        artifacts: ArtifactStore | None = None,
        registry: PluginRegistry | None = None,
        grants: PluginGrantService | None = None,
        plugins: PluginService | None = None,
        engine: PluginEngine | None = None,
        audit: AuditService | None = None,
        usage: UsageService | None = None,
        sanitizer: ArtifactSanitizer | None = None,
        step_timeout_seconds: float | None = None,
        gateway_factory: Callable[[Settings], ActionGateway] | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            tasks: 任务仓储（缺省按配置选后端）。
            artifacts: 产物仓储（缺省按配置选后端）。
            registry: 声明注册表。
            grants: 授权服务。
            plugins: 插件服务。
            engine: 步骤引擎（缺省由 `gateway_factory` 装配）。
            audit: 审计服务。
            usage: 计量服务。
            sanitizer: 产物脱敏器。
            step_timeout_seconds: 单步超时覆盖值（测试用小值；缺省取
                `plugin_task_timeout_seconds`，生产默认 900s）。
            gateway_factory: 动作门面的**工厂**（`Settings -> ActionGateway`），
                由组合根（`api/v1/agent.py`）注入。审计 P1-6：业务层此前直接
                import `api.v1.plugin_deps.build_action_gateway`，等于让业务层
                反向依赖 HTTP 装配层——`import app.business.task_service` 会连带
                拉起整个 `app.api`，业务层再也无法独立启动。改成注入后方向恢复
                `api → business`，业务层对「谁来造门面」一无所知。

        Raises:
            ApiError: 未注入 `gateway_factory` 且未直接传 `engine`（配置错误 30004）。
        """
        conf = settings or get_settings()
        sanitizer_inst = sanitizer or ArtifactSanitizer()
        engine_inst = engine or self._build_engine(conf, sanitizer_inst, gateway_factory)
        self._deps = TaskDeps(
            settings=conf,
            tasks=tasks or get_task_store(conf),
            artifacts=artifacts or get_artifact_store(conf),
            registry=registry or get_registry(),
            grants=grants or get_plugin_grant_service(conf),
            plugins=plugins or get_plugin_service(conf),
            engine=engine_inst,
            audit=audit or get_audit_service(conf),
            usage=usage or get_usage_service(conf),
            sanitizer=sanitizer_inst,
            step_timeout_seconds=float(
                step_timeout_seconds
                if step_timeout_seconds is not None
                else conf.plugin_task_timeout_seconds
            ),
        )
        #: 组合根注入的门面工厂（只在这里用到；引擎已装配完成，无需再存进 deps）
        self._gateway_factory = gateway_factory
        self._resolver = TaskManifestResolver(self._deps)
        self._query = TaskQuery(self._deps, self._resolver)
        self._runner = TaskRunner(self._deps, self._resolver, self._query)

    def _build_engine(
        self,
        settings: Settings,
        sanitizer: ArtifactSanitizer,
        gateway_factory: Callable[[Settings], ActionGateway] | None,
    ) -> PluginEngine:
        """装配步骤引擎（**在 `TaskService.__init__` 里立即调用**，除非显式注入 `engine`）。

        注意时机：不是"用到时才造"，而是**构造 TaskService 时就造**——
        门面装配（连接器/下游客户端）失败要在启动期暴露，不能等到第一个任务
        进来才炸（那时用户已经在等结果）。测试要换门面，走
        `reset_task_service()` 后重新取单例，或直接注入 `engine=`/`gateway_factory=`。

        门面由**组合根注入的工厂**产出（审计 P1-6）：业务层不再 import
        `api.v1.plugin_deps`，只依赖 `ActionGateway` 协议（`plugins/actions.py`）。
        启动路径上「谁提供门面」变成显式契约：没注入就是配置错误，直接报
        `30004`（而不是像以前那样偷偷把 `app.api` 拉进业务进程）。

        **参数化是本切片的唯一签名变化**：三拆前它读 `self._settings` / `self._sanitizer` /
        `self._gateway_factory`，而这三个在门面上现在是 `deps` 代理——构造期 `deps`
        尚未建立（`deps.engine` 正是本方法的产物），读代理会 `AttributeError`。
        故改为显式入参（私有装配函数，不在 7 个公共入口的契约内）。

        Args:
            settings: 配置实例。
            sanitizer: 产物脱敏器。
            gateway_factory: 动作门面工厂（组合根注入）。

        Returns:
            PluginEngine: 引擎实例。

        Raises:
            ApiError: 未注入 `gateway_factory`（组合根装配遗漏）。
        """
        if gateway_factory is None:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    "action gateway not wired: 组合根须注入 gateway_factory"
                    "（见 api/v1/agent.py::_action_gateway）"
                ),
            )
        gateway = gateway_factory(settings)
        return PluginEngine(
            gateway,
            sanitizer,
            retry_backoff_ms=settings.plugin_retry_backoff_ms,
        )

    # ------------------------------------------------------------------
    # 属性代理（9 只读 + 2 读写）
    # ------------------------------------------------------------------
    @property
    def _settings(self) -> Settings:
        """配置实例（代理）。"""
        return self._deps.settings

    @property
    def _tasks(self) -> TaskStore:
        """任务仓储（代理）。"""
        return self._deps.tasks

    @property
    def _artifacts(self) -> ArtifactStore:
        """产物仓储（代理）。"""
        return self._deps.artifacts

    @property
    def _registry(self) -> PluginRegistry:
        """声明注册表（代理）。"""
        return self._deps.registry

    @property
    def _grants(self) -> PluginGrantService:
        """授权服务（代理）。"""
        return self._deps.grants

    @property
    def _plugins(self) -> PluginService:
        """插件服务（代理）。"""
        return self._deps.plugins

    @property
    def _engine(self) -> PluginEngine:
        """步骤引擎（代理）。"""
        return self._deps.engine

    @property
    def _audit(self) -> AuditService:
        """审计服务（代理）。"""
        return self._deps.audit

    @property
    def _usage(self) -> UsageService:
        """计量服务（读写代理：写入即改共享 `TaskDeps`）。"""
        return self._deps.usage

    @_usage.setter
    def _usage(self, value: UsageService) -> None:
        """写回计量服务（测试构造后注入真 `UsageService`）。"""
        self._deps.usage = value

    @property
    def _sanitizer(self) -> ArtifactSanitizer:
        """产物脱敏器（代理）。"""
        return self._deps.sanitizer

    @property
    def _step_timeout_seconds(self) -> float:
        """单步超时（读写代理：写入即改共享 `TaskDeps`）。"""
        return self._deps.step_timeout_seconds

    @_step_timeout_seconds.setter
    def _step_timeout_seconds(self, value: float) -> None:
        """写回单步超时（测试用小值）。"""
        self._deps.step_timeout_seconds = float(value)

    # ------------------------------------------------------------------
    # A1 创建 / WF1 重试 / A5 取消 / WF2 产物编辑
    # ------------------------------------------------------------------
    async def create_plugin_task(
        self,
        request: CreateTaskRequest,
        auth: AuthContext,
        *,
        idem_key: str = "",
        retry_of: str = "",
        from_step: int = 0,
        trigger_type: str = "manual",
        copy_steps_from: TaskRecord | None = None,
    ) -> CreateTaskData:
        """创建插件任务（A1 的插件分支）。见 `TaskRunner.create_plugin_task`。"""
        return await self._runner.create_plugin_task(
            request,
            auth,
            idem_key=idem_key,
            retry_of=retry_of,
            from_step=from_step,
            trigger_type=trigger_type,
            copy_steps_from=copy_steps_from,
        )

    async def retry(
        self, task_id: str, request: RetryTaskRequest, auth: AuthContext
    ) -> RetryTaskData:
        """WF1：继续 / 重做 / 重试本步 / 从头重试。见 `TaskRunner.retry`。"""
        return await self._runner.retry(task_id, request, auth)

    async def cancel(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """取消插件任务（A5）。见 `TaskRunner.cancel`。"""
        return await self._runner.cancel(task_id, auth)

    async def edit_artifact(
        self,
        task_id: str,
        artifact_id: str,
        request: ArtifactEditRequest,
        auth: AuthContext,
    ) -> ArtifactEditData:
        """WF2：编辑产物并重新导出。见 `TaskRunner.edit_artifact`。"""
        return await self._runner.edit_artifact(task_id, artifact_id, request, auth)

    # ------------------------------------------------------------------
    # A2 / A3 / A4 视图
    # ------------------------------------------------------------------
    async def list_tasks(self, query: ListTasksQuery, auth: AuthContext) -> Any:
        """A2 任务列表（含插件任务）。见 `TaskQuery.list_tasks`。"""
        return await self._query.list_tasks(query, auth)

    async def get_task(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """A3 任务详情（插件任务分支）。见 `TaskQuery.get_task`。"""
        return await self._query.get_task(task_id, auth)

    async def steps(self, task_id: str, auth: AuthContext) -> StepsData:
        """A4 步骤详情（含产物与失败信息）。见 `TaskQuery.steps`。"""
        return await self._query.steps(task_id, auth)

    # ------------------------------------------------------------------
    # 推进入口（既有调用方与测试按旧名使用，转调 Runner 的公开名）
    # ------------------------------------------------------------------
    async def _advance_record(self, record: TaskRecord) -> None:
        """推进任务（带推进锁）。见 `TaskRunner.advance_record`。"""
        await self._runner.advance_record(record)

    async def _finish(
        self,
        record: TaskRecord,
        steps: list[StepRecord],
        status: str,
        artifacts: list[ArtifactRecord],
        *,
        error_message: str = "",
    ) -> None:
        """收尾任务（终态 + 审计 + 释放额度）。见 `TaskRunner.finish`。"""
        await self._runner.finish(record, steps, status, artifacts, error_message=error_message)

    async def _release_slot(self, record: TaskRecord, *, transitioned: bool = True) -> None:
        """腾出并发额度。见 `TaskRunner.release_slot`。"""
        await self._runner.release_slot(record, transitioned=transitioned)


__all__: list[str] = ["TaskService"]
