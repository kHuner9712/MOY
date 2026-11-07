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

from app.business.authz_service import TOOL_EXECUTE_POINT, get_authorization_service
from app.business.usage_service import UsageService, get_usage_service
from app.clients.dify_client import DifyClient, get_dify_client
from app.core.capability import AuthzRequest
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.kernel.events import (
    EVENT_SESSION_CREATED,
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_REQUESTED,
    EVENT_TURN_FINISHED,
    Event,
    EventStore,
    build_session_id,
    event_store,
)
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
from app.schemas.common import build_page, sse_frame_with_id
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

#: 事件类型 → 自研 SSE 事件名（A6 契约的**向后兼容映射**）
#:
#: T02 迁移后事件流用的是底座 14 类封闭枚举（`model.delta` / `tool.requested` …），
#: 而 A6 的对外事件名是自研的 `step_started` / `step_finished` / `task_completed`
#: （架构文档 3.3.5：绝不复用底座原生事件名）。两者不是同一套词汇，映射表因此是
#: 契约的一部分：
#:
#: - 工作流节点在底座词汇里就是一次**动作**（`tool.requested` 派发 / `tool.completed` 收结果），
#:   映射到 `step_started` / `step_finished`；
#: - 未登记的类型**透传自身类型名**（加性变更：新增事件不会让老客户端解析失败，
#:   也不会被静默吞掉）；
#: - `_SSE_SILENT_TYPES` 里的类型不产帧——它们进事件流，但不是"任务进度"。
_SSE_EVENT_BY_TYPE: Final[dict[str, str]] = {
    EVENT_TOOL_REQUESTED: "step_started",
    EVENT_TOOL_COMPLETED: "step_finished",
}

#: 不产出 SSE 帧的事件类型
_SSE_SILENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        # 会话建立：任务页此刻还没有任何可渲染的进度
        EVENT_SESSION_CREATED,
        # 上下文装配明细（含完整 TaskStateBlock）：体积大、属内部投影，
        # 走 `A4 步骤详情` 与回放接口读取，不推给浏览器
        "context.assembled",
    }
)

#: SSE 单次从事件流取回的批量（增量续传按批读，不假设会话长度）
_STREAM_BATCH: Final[int] = 500

#: 步骤投影使用的事件类型（步骤详情 = 事件流的投影，不是第二份状态）
_STEP_EVENT_TYPES: Final[frozenset[str]] = frozenset({EVENT_TOOL_REQUESTED, EVENT_TOOL_COMPLETED})


def _sse_legacy_fields(event: Event) -> dict[str, Any]:
    """旧帧字段兼容：把老客户端读的字段提到帧数据顶层。

    迁移前的帧数据是 `{"task_id": ..., "step": {...}}`；迁移后帧数据多了
    `seq` / `type` / `payload`（加性），但 `step` 仍必须留在顶层——否则前端
    "步骤列表就地更新"的逻辑要跟着改，那就不是向后兼容的增量了。

    Args:
        event: 事件记录。

    Returns:
        dict[str, Any]: 需要并入帧数据顶层的字段（无则空字典）。
    """
    if event.type in _STEP_EVENT_TYPES:
        raw_step = event.payload.get("step")
        if isinstance(raw_step, dict):
            return {"step": raw_step}
    return {}


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

    T02 起**步骤状态不再存进程内存**：任务步骤、SSE 增量全部来自会话事件日志
    （`EventStore`，规格 §3.1 原则 E1「事件是状态真源」）。迁移前的 `self._steps`
    已被**整体删除**，不允许保留双写——"既写事件、又写一份可独立更新的状态表"
    正是 E1 的违反判据，评审直接拒。

    仍留在进程内的是 `_tasks`（任务详情桩）：它是 T01/T02 期间的**降级来源**，
    由 T03/T04 的 loop 用 `replay_state_block` 投影替换。**注意**：`_tasks` 里
    不保存任何步骤，`A4 步骤详情`（`list_steps`）已完全改为读事件流，
    因此"步骤"这条数据只有一个可写副本。

    **租户化（SEC-01）**：`_tasks` 的键是 `(tenant_id, task_id)` 复合键，
    不是裸 `task_id`。进程内存字典是全进程共享的，若按裸 ID 索引，
    持 B 租户合法令牌的人只要知道（或枚举到）A 的任务 ID 就能读到 A 的目标、
    产物与步骤——这正是实测到的越权形态。事件流的租户维度由
    `EventStore` 的 `tenant_id` 入参与库内 RLS 双重保证。

    Attributes:
        _settings: 配置实例。
        _dify: 底座客户端。
        _tasks: 桩任务表（键 `(tenant_id, task_id)`）。
        _events: 会话事件存储（唯一状态真源）。
    """

    #: 桩任务表上限（防长跑进程无界增长；优先淘汰最旧的终态记录）
    _STUB_CAPACITY: Final[int] = 1000

    def __init__(
        self,
        settings: Settings | None = None,
        dify: DifyClient | None = None,
        usage: UsageService | None = None,
        events: EventStore | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            dify: 可选底座客户端。
            usage: 可选计量服务（测试注入；D-12 唯一写入口）。
            events: 可选事件存储（测试注入；缺省按 `business_store` 选后端）。
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
        self._events: EventStore = events or event_store(self._settings)

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

    @staticmethod
    def _session_id(auth: AuthContext, task_id: str) -> str:
        """构造任务的会话 ID（事件流的取数维度）。

        与 `_key` 同源：都把租户编进去，保证"同一个 task_id 在两个租户下"
        是两条互不干扰的流（详见 `event_log/records.py::build_session_id`）。

        Args:
            auth: 鉴权上下文（取生效租户）。
            task_id: 任务 ID。

        Returns:
            str: 形如 `tnt_x:task_y` 的会话 ID。
        """
        return build_session_id(auth.tenant_id, task_id)

    async def _append_event(
        self,
        auth: AuthContext,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> Event | None:
        """向任务会话追加一条事件（灰度开关关闭时不动存储）。

        开关语义（规格 §3.1 E1 的灰度口径）：`event_log_enabled=false` 时**不写**，
        但**绝不**回落到"内存 dict 第二副本"——那等于把刚拆掉的设计缺陷装回去。
        关闭后 SSE 只剩终态帧（见 `stream_events`），这是**可见的降级**，
        不是静默的双写。

        Args:
            auth: 鉴权上下文。
            task_id: 任务 ID。
            event_type: 事件类型（14 类封闭枚举之一）。
            payload: 事件载荷。

        Returns:
            Event | None: 落库事件；开关关闭时返回 None。
        """
        if not self._settings.event_log_enabled:
            return None
        return await self._events.append(
            auth.tenant_id,
            self._session_id(auth, task_id),
            event_type,
            payload,
        )

    def _prune_stub_tasks(self) -> None:
        """桩任务表达到上限时淘汰最旧的终态任务（运行中的任务不淘汰）。

        终态集合与 `TaskStatus` 的语义一致：pending/running/queued/waiting_confirm
        都视为活跃，不能淘汰（否则用户会看到任务凭空消失）。

        **不再联动步骤表**：步骤已迁到事件流，事件流按 `(session_id, seq)` 追加、
        不随任务淘汰——审计要求"已发生的历史不可因进程内缓存淘汰而消失"。
        """
        if len(self._tasks) < self._STUB_CAPACITY:
            return
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        for key, task in list(self._tasks.items()):
            if len(self._tasks) < self._STUB_CAPACITY:
                return
            if task.status in terminal:
                self._tasks.pop(key, None)

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
    async def _authorize_capabilities(self, request: CreateTaskRequest, auth: AuthContext) -> None:
        """在普通任务建单期执行四层授权（B6）。

        未提交 `capabilities` 时保持旧行为（继承调用者权限），只登记一次调试日志；
        提交后则按「租户 → 权限点 → 会话能力 → 资源」校验，越界直接 `14009`。

        Args:
            request: 创建请求。
            auth: 鉴权上下文。

        Raises:
            ApiError: 任一层拒绝（权限/租户/资源 `20003`，能力越界 `14009`）。
        """
        if request.capabilities is None:
            logger.debug(
                "authz_capability_inherited",
                operation="agent.task.create",
                tenant_id=auth.tenant_id,
            )
            return
        declared = request.capabilities.to_capability_set()
        required_tools = frozenset(request.tool_grants or ())
        await get_authorization_service().authorize(
            auth,
            AuthzRequest(
                tenant_id=auth.tenant_id,
                permission=TOOL_EXECUTE_POINT if required_tools else "",
                declared=declared,
                required_tools=required_tools,
                operation="agent.task.create",
            ),
        )

    async def create_task(self, request: CreateTaskRequest, auth: AuthContext) -> CreateTaskData:
        """创建 Agent 任务（异步执行）。

        底座返回原生 `workflow_run_id` 后会改用 `task_{run_id}` 作为任务 ID，
        此时必须把按本地 ULID 先落下的占位记录一并删除：旧键不清会在进程内存里
        留下一条永远 `running`、进度 0 的"幽灵任务"，`list_tasks` 会把它返回给
        前端（"我的任务"里出现永不结束的项），用户因看不到真实进度还可能重复提交。

        **两条与事件流相关的顺序约定（缺陷 D2 / D18）**：

        1. 事件先写、任务后登记：append 失败时不能留下"有任务、无事件"的幽灵任务
           （E1 之下，没有事件支撑的状态就是不存在）；
        2. 换 ID 后要在**新会话**补写 `session.created`：占位会话由本地 ULID 派生，
           真实会话由 `task_{run_id}` 派生，两者不是同一个会话。不补写则真实任务的
           会话里一条事件都没有——回放重建不出 `goal`、A4 恒空、A6 只有终态帧。

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
        await self._authorize_capabilities(request, auth)
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
        self._prune_stub_tasks()

        # **顺序：先写事件、再登记任务**（缺陷 D18）。反过来一旦 append 失败，
        # 就会留下"有任务、无事件"的幽灵任务：前端看到一条永远 running 的任务，
        # 而事件流里没有任何事实支撑它——E1 之下，**没有事件的状态就是不存在**。
        # 会话第一条事件（设计文档 §5.1 状态机：`[*] --> open: session.created`）。
        # `goal` 进 payload 是必要的：回放要能重建任务目标，而目标只在这条事件里出现。
        await self._append_event(
            auth,
            task_id,
            EVENT_SESSION_CREATED,
            self._session_created_payload(task_id, request),
        )
        self._tasks[self._key(auth, task_id)] = TaskDetailData(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            progress=0,
            current_step=None,
            result=None,
            usage=Usage(),
            updated_at=created_at,
        )

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
                run_task_id = f"{_TASK_PREFIX}{native_run_id}"
                # **换 ID 后必须在新会话里补写 `session.created`**（缺陷 D2）。
                # 切换前的占位会话是本地 ULID 派生的，真实任务的会话由
                # `_session_id(auth, new_task_id)` 决定——两句话不是同一个会话。
                # 不补写的后果：`session.created` 是唯一承载 `goal` 的事件，
                # 真实任务的会话里空无一物 → 回放重建不出目标、A4 步骤详情恒空、
                # A6 只有终态帧。保留 `origin_*` 三个字段是为了让"占位会话 →
                # 真实会话"的搬迁可追溯（否则两条 session.created 看起来像重复写入）。
                await self._append_event(
                    auth,
                    run_task_id,
                    EVENT_SESSION_CREATED,
                    self._session_created_payload(
                        run_task_id,
                        request,
                        origin_task_id=task_id,
                        origin_session_id=self._session_id(auth, task_id),
                        run_id=native_run_id,
                        reissued=True,
                    ),
                )
                # 换 ID 前先清掉按本地 ULID 落下的占位任务：否则内存里会留下一条永远
                # `running`、进度 0 的"幽灵任务"，`list_tasks` 会把它返回给前端。
                # 占位会话的事件**不删**——事件流 append-only，且它记录的是
                # "任务确实被发起过"这一事实（`origin_session_id` 指回来）。
                self._tasks.pop(self._key(auth, task_id), None)
                task_id = run_task_id
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

    @staticmethod
    def _session_created_payload(
        task_id: str,
        request: CreateTaskRequest,
        **extra: Any,
    ) -> dict[str, Any]:
        """构造 `session.created` 的载荷（两处写入共用，避免字段漂移）。

        为什么共用：`session.created` 会在"占位会话"与"换 ID 后的真实会话"各写一次
        （缺陷 D2），两处若各写一份字面量，`goal`/`profile` 迟早只在一处被改到，
        而回放只认其中一条。

        Args:
            task_id: 该事件所属会话对应的任务 ID。
            request: 创建请求（提供 goal / conversation_id / profile / tool_grants）。
            **extra: 追加字段（换 ID 时带 `origin_task_id` / `origin_session_id` /
                `run_id` / `reissued`）。

        Returns:
            dict[str, Any]: 事件载荷。
        """
        payload: dict[str, Any] = {
            "task_id": task_id,
            "conversation_id": request.conversation_id,
            "profile": request.profile.value,
            "goal": request.goal,
            "tool_grants": list(request.tool_grants or []),
        }
        payload.update(extra)
        return payload

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

        **T02 起步骤是事件流的投影**（规格 §3.1 E1）：步骤不再有独立存储，
        每次查询都按序重放本会话的 `tool.requested` / `tool.completed` 事件，
        同一 `step_id` 以**最后一条**事件为准（后到的事件覆盖状态）。
        因此"当前步骤"不存在第二个可写副本。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            StepsData: 步骤列表。

        Raises:
            ApiError: 任务不存在、已过期或不属于当前租户（12001）。
        """
        self._require(task_id, auth)  # 归属校验：未命中即 12001
        return await self._project_steps(task_id, auth)

    async def _project_steps(self, task_id: str, auth: AuthContext) -> StepsData:
        """把事件流投影成步骤列表（A4 的唯一数据来源）。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。

        Returns:
            StepsData: 步骤列表（按首次出现顺序，同 `step_id` 取最后一条事件）。
        """
        session_id = self._session_id(auth, task_id)
        ordered: dict[str, StepItem] = {}
        cursor = 0
        while True:
            batch = await self._events.read_after(
                auth.tenant_id, session_id, cursor, limit=_STREAM_BATCH
            )
            if not batch:
                break
            for event in batch:
                cursor = event.cursor
                if event.type not in _STEP_EVENT_TYPES:
                    continue
                raw_step = event.payload.get("step")
                if not isinstance(raw_step, dict):
                    continue
                try:
                    step = StepItem.model_validate(raw_step)
                except ValueError:  # pragma: no cover - 历史脏数据不应让整页 500
                    logger.warning(
                        "agent_step_payload_invalid",
                        task_id=task_id,
                        session_id=session_id,
                        seq=event.seq,
                    )
                    continue
                ordered[step.step_id] = step
            if len(batch) < _STREAM_BATCH:
                break
        return StepsData(task_id=task_id, steps=list(ordered.values()))

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

        # 取消是**状态迁移**，必须进事件流（缺陷 D8）：只改 `_tasks` 的话，
        # 取消这件事回放不出来、审计查不到、重启即丢——而"任务为什么停在这里"
        # 恰恰是审计最常问的问题。
        #
        # **但写入点在下游 `/stop` 之后，所以这里必须降级而不是抛错**（缺陷 N2）：
        # 上面那句 `POST /stop` 已经把运行停掉了，若此后 append 失败还返回错误，
        # 用户看到的是"取消失败"→ 重试 → 再打一次 `/stop`（对已停的 run 可能 404）
        # → 于是"取消一直失败，但任务其实早就停了"，而事件流里一条记录都没有
        # ——这是最坏的一种不一致：状态、界面、事实三者互相矛盾。
        # 取舍：**用户感知"已取消"优先于"事件一定写得下"**，写失败降级为 WARN
        # （由对账脚本按 WARN 发现偏差）。用已有类型 `turn.finished` 承载，
        # 不新增枚举（V1.0 封闭 14 类）。
        #
        # ⚠ V1.0 的临时妥协：`_tasks` 与事件流仍是两份状态，才需要这种"容忍一半
        # 成功"的写法。T03/T04 把 `_tasks` 改为事件投影之后，取消只需写事件
        # （无第二份状态要同步），这个 try/except 即可删除。
        try:
            await self._append_event(
                auth,
                task_id,
                EVENT_TURN_FINISHED,
                {
                    "task_id": task_id,
                    "outcome": "cancelled",
                    "reason": str(ErrorCode.TASK_CANCELLED.value),
                    "status": TaskStatus.CANCELLED.value,
                },
            )
        except Exception as exc:
            logger.warning(
                "agent_task_cancel_event_write_failed",
                task_id=task_id,
                tenant_id=auth.tenant_id,
                error=type(exc).__name__,
                detail=str(exc)[:200],
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

    async def stream_events(
        self,
        task_id: str,
        auth: AuthContext,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[str]:
        """订阅任务进度事件（SSE）。

        自研事件集：`step_started` / `step_finished` / `task_completed`，
        绝不复用底座原生事件名（架构文档 3.3.5）。

        **T02 迁移的核心**：迁移前这里读的是进程内 `self._steps`（刷新即丢、
        多副本不可用、断线不续）；现在读的是 `EventStore.read_after` 的增量
        （`cursor > after_id`），于是：

        - 每一帧带 `id: <cursor>`，客户端断线后用 `Last-Event-ID` 重连，
          服务端只补增量 → **零丢失、零重复**；
        - 多副本部署与刷新页面都能读到同一份历史（历史在库里，不在进程里）。

        帧格式说明：`id:` 行放在 `event:` 行**之后**。SSE 规范对同一帧内的字段顺序
        没有要求，这样做是为了保持既有解析方式（客户端与用例都按"第一行是
        `event:`"读取）不变——这是一次**向后兼容的增量**，不是新协议。

        Args:
            task_id: 任务 ID。
            auth: 鉴权上下文。
            after_id: 续传游标（来自 `Last-Event-ID` 请求头；0 表示从头）。

        Yields:
            str: SSE 文本帧。

        Raises:
            ApiError: 任务不存在、已过期或不属于当前租户（12001）。
        """
        self._require(task_id, auth)  # 归属校验必须在产出第一帧之前
        session_id = self._session_id(auth, task_id)
        # 游标必须**钳在 [0, tail]**（缺陷 D3）。不钳的后果不是"多推几条"，而是
        # 客户端永久静默：越界值（时间戳 / 别的会话的 id / 人为构造）被原样回显成
        # 终态帧的 `id:`，客户端下次仍带同一个越界值 → 永远拿不到进度帧，而服务端
        # 一切正常、日志无异常。越界一律按"从头回放"处理：回放幂等，重复下发不会
        # 让状态错（这正是 E1 把"续传"定义成 `cursor > after_seq` 的红利）。
        if self._settings.event_log_enabled:
            tail = await self._events.tail_cursor(auth.tenant_id, session_id)
            cursor = after_id if 0 < after_id <= tail else 0
        else:
            # 灰度关闭时没有事件可读，也不该为一次"注定空的读"去打数据库
            cursor = 0

        if self._settings.event_log_enabled:
            while True:
                batch = await self._events.read_after(
                    auth.tenant_id, session_id, cursor, limit=_STREAM_BATCH
                )
                if not batch:
                    break
                for event in batch:
                    cursor = max(cursor, event.cursor)
                    if event.type in _SSE_SILENT_TYPES:
                        continue
                    yield sse_frame_with_id(
                        _SSE_EVENT_BY_TYPE.get(event.type, event.type),
                        {
                            "task_id": task_id,
                            "seq": event.cursor,
                            "type": event.type,
                            "payload": event.payload,
                            **_sse_legacy_fields(event),
                        },
                        cursor,
                    )
                if len(batch) < _STREAM_BATCH:
                    break

        detail = self._require(task_id, auth)
        yield sse_frame_with_id(
            "task_completed",
            {"task_id": task_id, "status": detail.status.value},
            cursor,
        )

    async def append_steps(self, task_id: str, steps: list[StepItem], auth: AuthContext) -> None:
        """把步骤写入任务会话的事件流（联调与测试入口）。

        **为什么是 `async`**：步骤现在落在 PostgreSQL / 内存事件存储上，写是一次
        真实 I/O；保留同步签名只能靠"起个后台任务然后不等"，那正是规格 §3.1 E1
        警告的静默丢失形态（调用方以为写完了，回放却读不到）。

        事件类型映射：`step.status == running` → `tool.requested`，
        其余 → `tool.completed`（与 `_SSE_EVENT_BY_TYPE` 互为逆映射，保证
        "写进去什么、推出来什么"一致）。

        Args:
            task_id: 任务 ID。
            steps: 步骤列表。
            auth: 鉴权上下文（写入落在当前租户的会话上）。
        """
        for step in steps:
            event_type = (
                EVENT_TOOL_REQUESTED if step.status == StepStatus.RUNNING else EVENT_TOOL_COMPLETED
            )
            await self._append_event(
                auth,
                task_id,
                event_type,
                {"step": step.model_dump(mode="json")},
            )


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
