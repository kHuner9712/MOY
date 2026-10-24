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

from app.business.usage_service import UsageService, get_usage_service
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
from app.schemas.task import ArtifactVersionItem, StepArtifact, StepFailure

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


def _total_units_of(native_data: dict[str, Any]) -> int:
    """取工作流运行结果中的结算额度。

    Args:
        native_data: 底座 `data` 对象（含 `total_tokens` 等字段）。

    Returns:
        int: 结算额度；字段缺失返回 0（此时不写事件）。
    """
    try:
        return int(native_data.get("total_tokens", 0) or 0)
    except (TypeError, ValueError):  # pragma: no cover - 下游字段类型异常
        return 0


def _as_int(value: Any, default: int) -> int:
    """把任意上游值收敛成整数（**不抛**，坏值退回缺省）。

    Args:
        value: 上游原始值（可能是 `str` / `None` / 非法串）。
        default: 无法转换时的缺省值。

    Returns:
        int: 整数结果。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AgentService:
    """Agent 服务。

    T01 为无状态桩实现：任务与步骤保存在进程内存字典中，
    仅用于验证接口契约；T02 起接入底座 + Redis，适配层仍保持无状态。

    **租户化（SEC-01）**：`_tasks` / `_steps` 的键是 `(tenant_id, task_id)` 复合键，
    不是裸 `task_id`。进程内存字典是全进程共享的，若按裸 ID 索引，
    持 B 租户合法令牌的人只要知道（或枚举到）A 的任务 ID 就能读到 A 的目标、
    产物与步骤——这正是实测到的越权形态。复合键让「按 ID 直取」在数据结构层
    就不可能命中别人的记录，而不是依赖每个调用点自觉记得判断租户。

    Attributes:
        _settings: 配置实例。
        _dify: 底座客户端。
        _tasks: 桩任务表（键 `(tenant_id, task_id)`）。
        _steps: 桩步骤表（键 `(tenant_id, task_id)`）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        dify: DifyClient | None = None,
        usage: UsageService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            dify: 可选底座客户端。
            usage: 可选计量服务（测试注入；D-12 唯一写入口）。
        """
        self._settings = settings or get_settings()
        self._usage: UsageService = usage or get_usage_service(self._settings)
        if dify is not None:
            self._dify = dify
        elif self._settings.dify_agent_app_key:
            # Agent 任务走独立工作流应用（密钥与对话应用分离，T02 实联调修复 INT-04b）
            # 底座地址由 `DifyClient.for_app_key` 内部读取（审计 P1-1：
            # 上层不再直接触碰底座地址配置项，保持唯一出口约束）。
            self._dify = DifyClient.for_app_key(
                api_key=self._settings.dify_agent_app_key,
                settings=self._settings,
            )
        else:
            self._dify = get_dify_client()
        self._tasks: dict[tuple[str, str], TaskDetailData] = {}
        self._steps: dict[tuple[str, str], StepsData] = {}

    # ------------------------------------------------------------------
    # 租户化索引（SEC-01 的唯一键构造出口）
    # ------------------------------------------------------------------
    @staticmethod
    def _key(auth: AuthContext, task_id: str) -> tuple[str, str]:
        """构造任务索引键（租户 + 任务）。

        **所有**读写 `_tasks` / `_steps` 的地方都必须经过本方法：裸 `task_id`
        索引一旦出现，就是一条跨租户越权路径（进程内存字典里混着全部租户的记录）。

        Args:
            auth: 鉴权上下文（取生效租户）。
            task_id: 任务 ID。

        Returns:
            tuple[str, str]: `(tenant_id, task_id)` 复合键。
        """
        return (auth.tenant_id, task_id)

    def _require(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """取本租户的任务记录，未命中一律 12001。

        为什么「不属于本租户」与「不存在」返回**同一个码、同一句文案**：
        区分两者等于给攻击者一个探测接口（能试出别的租户有哪些任务 ID）。
        这也是 A3 契约里 12001 的既定语义（`TASK_NOT_FOUND`）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            TaskDetailData: 任务详情。

        Raises:
            ApiError: 任务不存在、已过期或不属于当前租户（12001）。
        """
        detail = self._tasks.get(self._key(auth, task_id))
        if detail is None:
            raise ApiError(ErrorCode.TASK_NOT_FOUND, internal_detail=f"task_id={task_id}")
        return detail

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
            ApiError: 下游工作流调用失败（30001 连接失败 / 30002 超时，由
                `DifyClient` 统一映射）。**任务执行失败与超时不抛码**：失败落地为
                状态机终态（`status=failed` + `error_message` + `_summary_text`），
                前端就地展示、不弹错。域内码 `12001` 由 A3 查询路径抛、
                `12003` 由 A4/A5 取消与重试路径抛，本方法均不产生。
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

        self._tasks[self._key(auth, task_id)] = TaskDetailData(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            progress=0,
            current_step=None,
            result=None,
            usage=Usage(),
            updated_at=created_at,
        )
        self._steps[self._key(auth, task_id)] = StepsData(task_id=task_id, steps=[])

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
                native_data = response.payload.get("data") or {}
                native_status = str(native_data.get("status", "")).lower()
                self._tasks[self._key(auth, task_id)] = TaskDetailData(
                    task_id=task_id,
                    status=_STATUS_MAP.get(native_status, TaskStatus.RUNNING),
                    progress=100 if native_status == "succeeded" else 0,
                    updated_at=created_at,
                )
                self._steps[self._key(auth, task_id)] = StepsData(task_id=task_id, steps=[])
                if native_status == "succeeded":
                    await self._record_automation(
                        task_id=task_id, units=_total_units_of(native_data), auth=auth
                    )

        return CreateTaskData(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            created_at=created_at,
            estimated_seconds=estimated,
        )

    async def _record_automation(self, *, task_id: str, units: int, auth: AuthContext) -> None:
        """任务成功完成时写一条 `automation` 用量事件（D-12 第 2 条幂等键 = task_id）。

        计量失败不得影响任务创建结果，只记 WARN（由对账脚本发现偏差）。

        Args:
            task_id: 任务 ID。
            units: 结算额度。
            auth: 鉴权上下文。
        """
        if units <= 0:
            return
        try:
            await self._usage.record_automation(
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
                task_id=task_id,
                units=units,
            )
        except Exception as exc:  # 计量失败不得影响任务创建（对账脚本负责发现偏差）
            logger.warning("usage_automation_failed", task_id=task_id, detail=type(exc).__name__)

    # ------------------------------------------------------------------
    # A2 任务列表
    # ------------------------------------------------------------------
    def list_tasks(self, query: ListTasksQuery, auth: AuthContext) -> Any:
        """查询任务列表（按会话/状态过滤，分页）。

        **只遍历当前租户的记录**（SEC-01 的列表枚举面）：进程内存字典里存着全部
        租户的任务，若不做租户过滤，任何租户一打开「我的任务」就能看到别人的任务 ID
        与状态——这比按 ID 直取更容易被发现，也更容易被批量利用。

        Args:
            query: 分页与过滤参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[TaskItem]` 分页结果。
        """
        items = [
            TaskItem(
                task_id=detail.task_id,
                conversation_id=detail.task_id,
                status=detail.status,
                progress=detail.progress,
                created_at=detail.updated_at,
                updated_at=detail.updated_at,
            )
            for (tenant, _), detail in self._tasks.items()
            if tenant == auth.tenant_id and (query.status is None or detail.status == query.status)
        ]
        start = query.offset
        page_items = items[start : start + query.page_size]
        return build_page(
            items=page_items, total=len(items), page=query.page, page_size=query.page_size
        )

    # ------------------------------------------------------------------
    # A3 任务状态查询
    # ------------------------------------------------------------------
    async def get_task(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """查询任务状态与结果。

        **未命中一律 12001，不再回落到底座**：早前实现里「本地查不到就问底座」
        是第二条越权路径——底座按原生 `workflow_run_id` 取运行结果，不知道租户，
        于是任何租户拿着别人的任务 ID 都能从这里读出别人的产物。归属判定只能
        来自本地记录（`_require`），取不到就是不存在，fail-closed。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            TaskDetailData: 任务详情。

        Raises:
            ApiError: 任务不存在、已过期或不属于当前租户（12001）。
        """
        detail = self._require(task_id, auth)
        if not self._settings.mock_mode:
            # 归属已确认后才回查底座刷新终态（只刷新「自己的」任务）
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
            artifacts: list[Artifact] = [
                self._map_native_artifact(item)
                for item in (outputs.get("artifacts") or [])
                if isinstance(item, dict)
            ]
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
    async def list_steps(self, task_id: str, auth: AuthContext) -> StepsData:
        """查询任务步骤详情（工作流可视化数据源）。

        先确认归属再取步骤：步骤里有每一环的产出摘要与产物下载地址，
        越权读取的信息量比任务详情更大。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            StepsData: 步骤列表。

        Raises:
            ApiError: 任务不存在、已过期或不属于当前租户（12001）。
        """
        self._require(task_id, auth)  # 归属校验：未命中即 12001
        return self._steps.get(self._key(auth, task_id), StepsData(task_id=task_id, steps=[]))

    @staticmethod
    def _map_native_artifact(item: dict[str, Any]) -> Artifact:
        """把底座产物项映射为自研产物（A3 `result.artifacts[]` / A4 `steps[].artifacts[]`）。

        只做字段搬迁与类型收敛，**不补造数据**：底座没给的字段一律留空串，
        前端据此判断"没有摘要"而不是"摘要是 0"。

        Args:
            item: 底座产物项（`type` / `name` / `download_url` / `url` /
                `output_key` / `content_sha256`）。

        Returns:
            Artifact: 自研产物。
        """
        try:
            artifact_type = ArtifactType(str(item.get("type", "text")))
        except ValueError:  # 底座出现未知类型时退回 text，不让一个字段把整页打红
            artifact_type = ArtifactType.TEXT
        return Artifact(
            type=artifact_type,
            name=str(item.get("name", "")),
            download_url=item.get("download_url"),
            url=item.get("url"),
            output_key=str(item.get("output_key", "") or item.get("key", "") or ""),
            content_sha256=str(item.get("content_sha256", "") or item.get("sha256", "") or ""),
        )

    @staticmethod
    def _map_native_step_artifact(item: dict[str, Any]) -> StepArtifact:
        """把底座产物项映射为步骤产物（A4 `steps[].artifacts[]`）。

        Args:
            item: 底座产物项。

        Returns:
            StepArtifact: 步骤产物。
        """
        history = item.get("version_history")
        versions: list[ArtifactVersionItem] = []
        if isinstance(history, list):
            for entry in history:
                if not isinstance(entry, dict):
                    continue
                versions.append(
                    ArtifactVersionItem(
                        version=max(_as_int(entry.get("version"), 1), 1),
                        created_at=str(entry.get("created_at", "") or ""),
                        download_url=entry.get("download_url"),
                        content_sha256=str(entry.get("content_sha256", "") or ""),
                    )
                )
        return StepArtifact(
            artifact_id=str(item.get("artifact_id", "") or item.get("id", "") or ""),
            output_key=str(item.get("output_key", "") or item.get("key", "") or ""),
            type=str(item.get("type", "text") or "text"),
            name=str(item.get("name", "")),
            version=max(_as_int(item.get("version"), 1), 1),
            download_url=item.get("download_url"),
            url=item.get("url"),
            preview_text=str(item.get("preview_text", "") or ""),
            content_sha256=str(item.get("content_sha256", "") or item.get("sha256", "") or ""),
            editable=bool(item.get("editable", False)),
            version_history=versions,
        )

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
        # 产物与失败信息**从同一条节点事件里取**（`node_finished` 才带 `outputs`），
        # 不另外补造：前端要的是"这一步产出了什么 / 为什么没成"，不是占位符。
        outputs = data.get("outputs")
        return StepItem(
            step_id=f"{_STEP_PREFIX}{sequence}",
            sequence=sequence,
            title=title,
            status=status,
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            output_summary=self._summarize_outputs(outputs),
            artifacts=[
                self._map_native_step_artifact(item)
                for item in (outputs.get("artifacts") if isinstance(outputs, dict) else None) or []
                if isinstance(item, dict)
            ],
            failure=self._node_failure(data, status),
        )

    @staticmethod
    def _node_failure(data: dict[str, Any], status: StepStatus) -> StepFailure | None:
        """从节点事件里提取步骤失败信息（A4 `steps[].failure`）。

        **文案必须是业务语言**：底座原始报错（含节点类型、下游 URL 等）只进日志，
        不拼进 `reason_text`——用户看不懂，也可能把内部实现细节带到前端。

        Args:
            data: 节点事件 `data` 对象。
            status: 已映射的步骤状态。

        Returns:
            StepFailure | None: 失败信息；未失败返回 `None`。
        """
        if status != StepStatus.FAILED:
            return None
        # 底座原始报错只进日志：`reason_text` 是给用户看的一句话，不含节点类型、
        # 下游地址等技术细节；`operator_action` 是"照做就能继续"的动作。
        native_error = data.get("error") or data.get("error_message") or ""
        if native_error:
            logger.warning(
                "agent_step_failed", step_hint=str(data.get("title", "")), detail=str(native_error)
            )
        return StepFailure(
            reason_text="这一步没有处理成功，后面的步骤需要它才能继续",
            retryable=True,
            operator_action="可以点「重试这一步」，已完成的步骤不会重跑",
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
    async def cancel_task(self, task_id: str, auth: AuthContext) -> TaskDetailData:
        """取消任务。

        **未命中不得下达底座 `/stop`**：那是一句「作用于别人的运行」的写指令，
        越权取消会让别的租户的任务无故中止（比越权读更严重）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            TaskDetailData: 取消后的任务详情（状态为 `cancelled`）。

        Raises:
            ApiError: 任务不存在、已过期或不属于当前租户（12001）；
                任务已处于终态（12003）。
        """
        detail = self._require(task_id, auth)
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
    async def ensure_task_exists(self, task_id: str, auth: AuthContext) -> None:
        """校验任务存在性（SSE 端点必须在建立响应前调用）。

        SSE 响应一旦开始写出便无法再返回统一错误响应体，
        因此资源校验必须在返回 `StreamingResponse` 之前完成（架构文档 3.0.1）。
        归属校验同理：不能等流开始推送了才发现这条任务不属于当前租户。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Raises:
            ApiError: 任务不存在、已过期或不属于当前租户（12001）。
        """
        self._require(task_id, auth)

    async def stream_events(self, task_id: str, auth: AuthContext) -> AsyncIterator[str]:
        """订阅任务进度事件（SSE）。

        自研事件集：`step_started` / `step_finished` / `task_completed`，
        绝不复用底座原生事件名（架构文档 3.3.5）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Yields:
            str: SSE 文本帧。
        """
        steps = self._steps.get(self._key(auth, task_id), StepsData(task_id=task_id, steps=[]))
        for step in steps.steps:
            event = "step_started" if step.status == StepStatus.RUNNING else "step_finished"
            yield sse_frame(event, {"task_id": task_id, "step": step.model_dump()})

        detail = self._require(task_id, auth)
        yield sse_frame("task_completed", {"task_id": task_id, "status": detail.status.value})

    def append_steps(self, task_id: str, steps: list[StepItem], auth: AuthContext) -> None:
        """追加步骤到桩任务（供联调与测试使用）。

        Args:
            task_id: 任务 ID。
            steps: 步骤列表。
            auth: 鉴权上下文（写入落在当前租户的键上）。
        """
        key = self._key(auth, task_id)
        existing = self._steps.setdefault(key, StepsData(task_id=task_id, steps=[]))
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
