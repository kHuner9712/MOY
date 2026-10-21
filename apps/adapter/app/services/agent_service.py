"""Agent 服务协议转换（架构文档 3.3 / 3.6 类图 `AgentService`）。

转换职责（架构文档 3.3.5）：
- `goal`/`inputs` → 底座 `inputs`；`profile`/`tool_grants` → 适配层选择工作流模板；
- 底座 `workflow_run_id` → 自研 `task_id`；
- 底座状态（running/succeeded/failed/stopped）→ 自研
  `running`/`completed`/`failed`/`cancelled`；
- 底座 `node_started`/`node_finished` 节点事件 → 自研
  `step_started`/`step_finished`（节点类型名不外泄，`title` 由模板配置注入）；
- `outputs` → `result`（按模板 schema 提取 artifacts）。

V1.0 双通道：创建 + 轮询 + SSE 推送，Webhook 回调预留（D-04）。
T01 阶段说明：返回业务化 mock 结果，不产生任何真实底座请求。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Final

from app.clients.dify_client import DifyClient, get_dify_client
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.schemas.agent import (
    Artifact,
    ArtifactType,
    CreateTaskData,
    CreateTaskRequest,
    ListTasksQuery,
    StepItem,
    StepsData,
    StepStatus,
    TaskDetailData,
    TaskItem,
    TaskResult,
    TaskStatus,
)
from app.schemas.common import build_page, sse_frame
from app.schemas.model import Usage

logger = get_logger(__name__)

#: 底座原生路径（仅本文件与 `clients/dify_client.py` 可出现）
_DIFY_WORKFLOWS_PATH: Final[str] = "/v1/workflows"

#: 资源 ID 前缀（架构文档 9.1）
_TASK_PREFIX: Final[str] = "task_"
_STEP_PREFIX: Final[str] = "step_"

#: 底座原生状态 → 自研任务状态映射
_STATUS_MAP: Final[dict[str, TaskStatus]] = {
    "running": TaskStatus.RUNNING,
    "succeeded": TaskStatus.COMPLETED,
    "failed": TaskStatus.FAILED,
    "stopped": TaskStatus.CANCELLED,
    "waiting": TaskStatus.PENDING,
}

#: 底座原生节点事件 → 自研步骤状态映射
_STEP_STATUS_MAP: Final[dict[str, StepStatus]] = {
    "running": StepStatus.RUNNING,
    "succeeded": StepStatus.COMPLETED,
    "failed": StepStatus.FAILED,
    "stopped": StepStatus.SKIPPED,
}


def new_task_id() -> str:
    """生成任务 ID。

    Returns:
        str: 形如 `task_01K4Q8Z2X7M9B3N5P6R8T0V2W4` 的 ULID（架构文档 9.1）。
    """
    return new_resource_id(_TASK_PREFIX)


def _now_iso() -> str:
    """获取当前 UTC 时间的 ISO 8601 字符串。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class AgentService:
    """Agent 服务。

    T01 为无状态桩实现：任务与步骤保存在进程内存字典中，
    仅用于验证接口契约；T02 起接入底座 + Redis，适配层仍保持无状态。

    Attributes:
        _settings: 配置实例。
        _dify: 底座客户端。
        _tasks: 桩任务表。
        _steps: 桩步骤表。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        dify: DifyClient | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            dify: 可选底座客户端。
        """
        self._settings = settings or get_settings()
        if dify is not None:
            self._dify = dify
        elif self._settings.dify_agent_app_key:
            # Agent 任务走独立工作流应用（密钥与对话应用分离，T02 实联调修复 INT-04b）
            self._dify = DifyClient(
                base_url=self._settings.dify_base_url,
                api_key=self._settings.dify_agent_app_key,
                settings=self._settings,
            )
        else:
            self._dify = get_dify_client()
        self._tasks: dict[str, TaskDetailData] = {}
        self._steps: dict[str, StepsData] = {}

    # ------------------------------------------------------------------
    # A1 创建任务
    # ------------------------------------------------------------------
    async def create_task(self, request: CreateTaskRequest, auth: AuthContext) -> CreateTaskData:
        """创建 Agent 任务（异步执行）。

        Args:
            request: 创建请求。
            auth: 鉴权上下文。

        Returns:
            CreateTaskData: 任务 ID 与初始状态。

        Raises:
            ApiError: 所需工具未连接（12005）、任务创建失败（12002）。
        """
        if request.tool_grants:
            logger.info(
                "agent_task_create_requested",
                conversation_id=request.conversation_id,
                profile=request.profile.value,
                tool_grants=request.tool_grants,
                tenant_id=auth.tenant_id,
            )
        else:
            logger.info(
                "agent_task_create_requested",
                conversation_id=request.conversation_id,
                profile=request.profile.value,
                tenant_id=auth.tenant_id,
            )

        task_id = new_task_id()
        created_at = _now_iso()
        estimated = min(request.options.max_steps * 10, request.options.timeout_seconds)

        self._tasks[task_id] = TaskDetailData(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            progress=0,
            current_step=None,
            result=None,
            usage=Usage(),
            updated_at=created_at,
        )
        self._steps[task_id] = StepsData(task_id=task_id, steps=[])

        if not self._settings.mock_mode:
            response = await self._dify.post(
                f"{_DIFY_WORKFLOWS_PATH}/run",
                {
                    "inputs": request.inputs,
                    "response_mode": "blocking",
                    # 底座 user 为必填终端用户标识（Dify >= 0.6 强制，T02 实联调修复）
                    "user": auth.user_id,
                },
            )
            native_run_id = str(response.payload.get("workflow_run_id", ""))
            if native_run_id:
                task_id = f"{_TASK_PREFIX}{native_run_id}"
                # blocking 模式下底座已同步返回终态（data.status），
                # 直接映射为自研任务状态，而非停留 running（T02 实联调修复）
                native_status = str((response.payload.get("data") or {}).get("status", "")).lower()
                self._tasks[task_id] = TaskDetailData(
                    task_id=task_id,
                    status=_STATUS_MAP.get(native_status, TaskStatus.RUNNING),
                    progress=100 if native_status == "succeeded" else 0,
                    updated_at=created_at,
                )
                self._steps[task_id] = StepsData(task_id=task_id, steps=[])

        return CreateTaskData(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            created_at=created_at,
            estimated_seconds=estimated,
        )

    # ------------------------------------------------------------------
    # A2 任务列表
    # ------------------------------------------------------------------
    def list_tasks(self, query: ListTasksQuery, auth: AuthContext) -> Any:
        """查询任务列表（按会话/状态过滤，分页）。

        Args:
            query: 分页与过滤参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[TaskItem]` 分页结果。
        """
        del auth  # T01 桩模式不持有租户维度状态
        items = [
            TaskItem(
                task_id=detail.task_id,
                conversation_id=detail.task_id,
                status=detail.status,
                progress=detail.progress,
                created_at=detail.updated_at,
                updated_at=detail.updated_at,
            )
            for detail in self._tasks.values()
            if query.status is None or detail.status == query.status
        ]
        start = query.offset
        page_items = items[start : start + query.page_size]
        return build_page(
            items=page_items, total=len(items), page=query.page, page_size=query.page_size
        )

    # ------------------------------------------------------------------
    # A3 任务状态查询
    # ------------------------------------------------------------------
    async def get_task(self, task_id: str) -> TaskDetailData:
        """查询任务状态与结果。

        Args:
            task_id: 任务 ID。

        Returns:
            TaskDetailData: 任务详情。

        Raises:
            ApiError: 任务不存在或已过期（12001）。
        """
        detail = self._tasks.get(task_id)
        if detail is None:
            if self._settings.mock_mode:
                raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
            response = await self._dify.get(
                f"{_DIFY_WORKFLOWS_PATH}/run/{task_id.removeprefix(_TASK_PREFIX)}"
            )
            return self._transform_task_detail(task_id, response.payload)

        if not self._settings.mock_mode:
            response = await self._dify.get(
                f"{_DIFY_WORKFLOWS_PATH}/run/{task_id.removeprefix(_TASK_PREFIX)}"
            )
            return self._transform_task_detail(task_id, response.payload)
        return detail

    def _transform_task_detail(self, task_id: str, payload: dict[str, Any]) -> TaskDetailData:
        """将底座原生运行结果转换为自研任务详情。

        Args:
            task_id: 自研任务 ID。
            payload: 底座原生响应体。

        Returns:
            TaskDetailData: 自研任务详情。
        """
        native_status = str(payload.get("status", "running"))
        status = _STATUS_MAP.get(native_status, TaskStatus.RUNNING)
        outputs = payload.get("outputs", {})
        result = None
        if isinstance(outputs, dict) and outputs:
            artifacts: list[Artifact] = []
            raw_artifacts = outputs.get("artifacts", [])
            if isinstance(raw_artifacts, list):
                for item in raw_artifacts:
                    if not isinstance(item, dict):
                        continue
                    artifacts.append(
                        Artifact(
                            type=ArtifactType(item.get("type", "text")),
                            name=str(item.get("name", "")),
                            download_url=item.get("download_url"),
                            url=item.get("url"),
                        )
                    )
            result = TaskResult(summary=str(outputs.get("summary", "")), artifacts=artifacts)

        progress = 100 if status == TaskStatus.COMPLETED else 0
        return TaskDetailData(
            task_id=task_id,
            status=status,
            progress=progress,
            current_step=None,
            result=result,
            usage=Usage(
                total_units=int(outputs.get("total_units", 0)) if isinstance(outputs, dict) else 0
            ),
            updated_at=_now_iso(),
        )

    # ------------------------------------------------------------------
    # A4 步骤详情
    # ------------------------------------------------------------------
    async def list_steps(self, task_id: str) -> StepsData:
        """查询任务步骤详情（工作流可视化数据源）。

        Args:
            task_id: 任务 ID。

        Returns:
            StepsData: 步骤列表。

        Raises:
            ApiError: 任务不存在（12001）。
        """
        if task_id not in self._tasks:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        return self._steps.get(task_id, StepsData(task_id=task_id, steps=[]))

    def transform_node_event(self, event: dict[str, Any]) -> StepItem | None:
        """将底座原生节点事件转换为自研步骤条目。

        底座节点 ID / 类型 → 自研 `step_id` / `title`（映射表存适配层配置），
        底座节点类型名对外一律不暴露（架构文档 3.3.4）。

        Args:
            event: 底座原生节点事件字典。

        Returns:
            StepItem | None: 自研步骤条目；非节点事件返回 `None`。
        """
        native_event = str(event.get("event", ""))
        if native_event not in {"node_started", "node_finished"}:
            return None
        data = event.get("data", {})
        if not isinstance(data, dict):
            return None
        native_status = str(data.get("status", "running"))
        status = _STEP_STATUS_MAP.get(native_status, StepStatus.RUNNING)
        sequence = int(data.get("index", 0) or 0) + 1
        title = str(data.get("title", "")) or f"步骤 {sequence}"
        return StepItem(
            step_id=f"{_STEP_PREFIX}{sequence}",
            sequence=sequence,
            title=title,
            status=status,
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            output_summary=self._summarize_outputs(data.get("outputs")),
        )

    @staticmethod
    def _summarize_outputs(outputs: Any) -> str | None:
        """将底座节点输出归纳为业务语言摘要。

        Args:
            outputs: 底座原生节点输出。

        Returns:
            str | None: 业务语言摘要；无输出时返回 `None`。
        """
        if not outputs:
            return None
        if isinstance(outputs, str):
            return outputs[:200]
        if isinstance(outputs, dict):
            summary = outputs.get("summary")
            if isinstance(summary, str) and summary:
                return summary[:200]
            return f"已生成 {len(outputs)} 项输出"
        return "已生成输出内容"

    # ------------------------------------------------------------------
    # A5 取消任务
    # ------------------------------------------------------------------
    async def cancel_task(self, task_id: str) -> TaskDetailData:
        """取消任务。

        Args:
            task_id: 任务 ID。

        Returns:
            TaskDetailData: 取消后的任务详情（状态为 `cancelled`）。

        Raises:
            ApiError: 任务不存在（12001）。
        """
        detail = self._tasks.get(task_id)
        if detail is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        if detail.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise ApiError(
                ErrorCode.TASK_CANCELLED,
                internal_detail=f"task_id={task_id} already in terminal state",
            )

        if not self._settings.mock_mode:
            await self._dify.post(
                f"{_DIFY_WORKFLOWS_PATH}/runs/{task_id.removeprefix(_TASK_PREFIX)}/stop", {}
            )

        detail.status = TaskStatus.CANCELLED
        detail.updated_at = _now_iso()
        logger.info("agent_task_cancelled", task_id=task_id)
        return detail

    # ------------------------------------------------------------------
    # A6 任务事件推送（SSE）
    # ------------------------------------------------------------------
    async def ensure_task_exists(self, task_id: str) -> None:
        """校验任务存在性（SSE 端点必须在建立响应前调用）。

        SSE 响应一旦开始写出便无法再返回统一错误响应体，
        因此资源校验必须在返回 `StreamingResponse` 之前完成（架构文档 3.0.1）。

        Args:
            task_id: 任务 ID。

        Raises:
            ApiError: 任务不存在或已过期（12001）。
        """
        if task_id not in self._tasks:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")

    async def stream_events(self, task_id: str) -> AsyncIterator[str]:
        """订阅任务进度事件（SSE）。

        自研事件集：`step_started` / `step_finished` / `task_completed`，
        绝不复用底座原生事件名（架构文档 3.3.5）。

        Args:
            task_id: 任务 ID。

        Yields:
            str: SSE 文本帧。
        """
        steps = self._steps.get(task_id, StepsData(task_id=task_id, steps=[]))
        for step in steps.steps:
            event = "step_started" if step.status == StepStatus.RUNNING else "step_finished"
            yield sse_frame(event, {"task_id": task_id, "step": step.model_dump()})

        detail = self._tasks[task_id]
        yield sse_frame("task_completed", {"task_id": task_id, "status": detail.status.value})

    def append_steps(self, task_id: str, steps: list[StepItem]) -> None:
        """追加步骤到桩任务（供联调与测试使用）。

        Args:
            task_id: 任务 ID。
            steps: 步骤列表。
        """
        existing = self._steps.setdefault(task_id, StepsData(task_id=task_id, steps=[]))
        existing.steps.extend(steps)


_service: AgentService | None = None


def get_agent_service() -> AgentService:
    """获取 Agent 服务单例。

    Returns:
        AgentService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = AgentService()
    return _service


def reset_agent_service() -> None:
    """重置 Agent 服务单例，供测试隔离用例。"""
    global _service
    _service = None
