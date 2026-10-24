"""Agent 服务「真实下游模式」路径单元测试（架构 §3.3 / §3.3.5）。

`tests/unit/test_agent_service.py` 覆盖的是**桩模式**（`downstream_enabled=false`），
本文件补的是它够不到的一半：

1. **底座阻塞式返回后的状态映射**：`blocking` 模式下底座已返回终态，若仍停留
   `running`，前端会一直转圈、任务永远不会被结算（T02 实联调修复的那条）。
2. **结算幂等键**：任务成功必须写一条 `automation` 用量事件，幂等键是 `task_id`
   （D-12 第 2 条）。写漏了客户账单就是少一笔；写重了就是多收钱。
3. **计量失败不得影响任务结果**：计量是旁路，失败只记 WARN（由对账脚本发现偏差）。
4. **步骤详情的信息量比任务详情更大**：步骤里有每环产出摘要与产物下载地址，
   越权读后果更重，因此 `_require` 必须先校验归属。
5. **底座原始报错不外泄**：`reason_text` 必须是业务语言（前端给用户看），
   节点类型、下游地址等技术细节只进日志。

底座 HTTP 客户端与本机无任何真实请求（`StubDify` 记录调用、返回固定 payload）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.usage_service import UsageService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.agent import (
    ArtifactType,
    CreateTaskRequest,
    ListTasksQuery,
    StepItem,
    StepStatus,
    TaskStatus,
)
from app.services import agent_service
from app.services.agent_service import AgentService, get_agent_service, reset_agent_service

pytestmark = pytest.mark.unit

TENANT = "tnt_agent_unit"
OTHER_TENANT = "tnt_agent_other"
USER = "usr_agent"

_BASE_PARAMS: dict[str, Any] = {
    "env": "test",
    "deploy_mode": "saas",
    "business_store": "memory",
    "auth_mode": "stub",
    "jwt_secret": "agent-unit-secret",
    "ratelimit_enabled": False,
    "background_jobs_enabled": False,
}


class _Response:
    """底座响应替身（业务层只读 `payload`）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化。

        Args:
            payload: 响应体。
        """
        self.payload = payload


class StubDify:
    """底座客户端替身：记录调用路径与请求体，按脚本返回 payload。

    `post_payload` 既可以是单个 dict（每次返回它），也可以是 dict 列表
    （按调用顺序依次返回，用完后重复最后一个）——列表形态用于"一次创建多个
    任务、每个任务要有不同 run id"的场景。
    """

    def __init__(
        self,
        *,
        post_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        get_payload: dict[str, Any] | None = None,
    ) -> None:
        """初始化。

        Args:
            post_payload: `post()` 返回的响应体（或响应体序列）。
            get_payload: `get()` 返回的响应体。
        """
        if isinstance(post_payload, list):
            self._post_queue = list(post_payload)
        else:
            self._post_queue = [post_payload or {}]
        self.get_payload = get_payload or {}
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []

    async def post(self, path: str, body: dict[str, Any]) -> _Response:
        """记录并返回预设响应。

        Args:
            path: 请求路径。
            body: 请求体。

        Returns:
            _Response: 响应。
        """
        self.posts.append((path, body))
        payload = self._post_queue[0]
        if len(self._post_queue) > 1:
            self._post_queue.pop(0)
        return _Response(payload)

    async def get(self, path: str) -> _Response:
        """记录并返回预设响应。

        Args:
            path: 请求路径。

        Returns:
            _Response: 响应。
        """
        self.gets.append(path)
        return _Response(self.get_payload)


class StubUsage:
    """计量服务替身：记录 `record_automation` 调用，可配置为失败。"""

    def __init__(self, *, boom: bool = False) -> None:
        """初始化。

        Args:
            boom: 为真时抛异常（模拟计量链路故障）。
        """
        self.boom = boom
        self.calls: list[dict[str, Any]] = []

    async def record_automation(
        self, *, tenant_id: str, user_id: str, task_id: str, units: int
    ) -> bool:
        """记录一次结算。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            task_id: 任务 ID（幂等键）。
            units: 结算额度。

        Returns:
            bool: 首次写入返回 True。

        Raises:
            RuntimeError: `boom=True` 时。
        """
        self.calls.append(
            {"tenant_id": tenant_id, "user_id": user_id, "task_id": task_id, "units": units}
        )
        if self.boom:
            raise RuntimeError("usage store unavailable")
        return True


class LoggerSpy:
    """记录 structlog 调用的替身。"""

    def __init__(self) -> None:
        """初始化空记录。"""
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def debug(self, event: str, **fields: Any) -> None:
        """DEBUG 级。"""
        self.events.append(("debug", str(event), dict(fields)))

    def info(self, event: str, **fields: Any) -> None:
        """INFO 级。"""
        self.events.append(("info", str(event), dict(fields)))

    def warning(self, event: str, **fields: Any) -> None:
        """WARNING 级。"""
        self.events.append(("warning", str(event), dict(fields)))

    def error(self, event: str, **fields: Any) -> None:
        """ERROR 级。"""
        self.events.append(("error", str(event), dict(fields)))

    def named(self, event: str) -> list[dict[str, Any]]:
        """取某事件的全部字段。

        Args:
            event: 事件名。

        Returns:
            list[dict[str, Any]]: 字段列表。
        """
        return [fields for _level, name, fields in self.events if name == event]


def _settings(**overrides: Any) -> Settings:
    """构造"真实下游模式"配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置。
    """
    params = dict(_BASE_PARAMS)
    # `mock_mode = not downstream_enabled`：置 True 才走底座分支
    params["downstream_enabled"] = True
    params["dify_base_url"] = "http://dify.invalid"
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


def _auth(user_id: str = USER, tenant_id: str = TENANT) -> AuthContext:
    """构造鉴权上下文。

    Args:
        user_id: 用户 ID。
        tenant_id: 租户 ID。

    Returns:
        AuthContext: 上下文。
    """
    return AuthContext(user_id=user_id, tenant_id=tenant_id, roles=["member"])


def _service(
    *,
    dify: StubDify | None = None,
    usage: StubUsage | None = None,
    settings: Settings | None = None,
) -> AgentService:
    """构造真实下游模式的服务（底座与计量都是替身）。

    Args:
        dify: 底座替身。
        usage: 计量替身。
        settings: 配置。

    Returns:
        AgentService: 服务实例。
    """
    return AgentService(
        settings=settings or _settings(),
        dify=dify or StubDify(),  # type: ignore[arg-type]
        usage=usage or StubUsage(),  # type: ignore[arg-type]
    )


def _run_payload(
    *,
    run_id: str = "run_abc",
    status: str = "succeeded",
    tokens: Any = 120,
    outputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造底座工作流运行响应。

    Args:
        run_id: 底座运行 ID。
        status: 底座原生状态。
        tokens: `total_tokens`。
        outputs: `data.outputs`。

    Returns:
        dict[str, Any]: 响应体。
    """
    data: dict[str, Any] = {"status": status, "total_tokens": tokens}
    if outputs is not None:
        data["outputs"] = outputs
    return {"workflow_run_id": run_id, "data": data}


def _stored_status(service: AgentService, task_id: str, auth: AuthContext) -> TaskStatus:
    """读任务的**本地状态**（不经底座刷新）。

    `get_task()` 在真实下游模式下会用底座结果覆盖本地状态，因此断言"创建时把
    状态映射成了什么"必须走列表（列表读的是本地记录）。

    Args:
        service: 服务实例。
        task_id: 任务 ID。
        auth: 鉴权上下文。

    Returns:
        TaskStatus: 本地状态。

    Raises:
        AssertionError: 列表里找不到该任务。
    """
    result = service.list_tasks(ListTasksQuery(page=1, page_size=50), auth)
    for item in result.items:
        if item.task_id == task_id:
            return item.status
    raise AssertionError(f"任务 {task_id} 不在本地列表里：{[i.task_id for i in result.items]}")


# ===========================================================================
# 客户端装配
# ===========================================================================
class TestClientWiring:
    """底座客户端的装配顺序（专用应用密钥优先，其次全局单例）。"""

    async def test_agent_app_key_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """配置了 Agent 专用应用密钥时用它，而不是对话应用的全局单例。"""
        stub = StubDify(post_payload=_run_payload(status="running"))
        seen: dict[str, Any] = {}

        def fake_for_app_key(*, api_key: str, settings: Settings | None = None) -> StubDify:
            seen["api_key"] = api_key
            return stub

        monkeypatch.setattr(agent_service.DifyClient, "for_app_key", staticmethod(fake_for_app_key))
        service = AgentService(
            settings=_settings(dify_agent_app_key="app-agent-only"), usage=StubUsage()
        )
        await service.create_task(CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth())

        assert seen["api_key"] == "app-agent-only"
        assert stub.posts, "应使用按 app_key 构造的客户端"

    async def test_falls_back_to_global_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """没有专用密钥时回落全局对话客户端单例。"""
        stub = StubDify(post_payload=_run_payload(status="running"))
        monkeypatch.setattr(agent_service, "get_dify_client", lambda: stub)
        service = AgentService(settings=_settings(), usage=StubUsage())

        await service.create_task(CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth())

        assert stub.posts, "应使用全局客户端单例"

    def test_singleton_and_reset(self) -> None:
        """`get_agent_service` 是进程单例，`reset_agent_service` 可重置（测试隔离）。"""
        first = get_agent_service()
        assert get_agent_service() is first
        reset_agent_service()
        assert get_agent_service() is not first


# ===========================================================================
# A1 创建任务（底座阻塞式返回）
# ===========================================================================
class TestCreateTaskWithDownstream:
    """创建任务在真实下游模式下的状态机与结算。"""

    async def test_succeeded_run_maps_to_completed_and_settles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """底座 `blocking` 已返回终态 → 直接落地 `completed` + 结算一次。"""
        spy = LoggerSpy()
        monkeypatch.setattr(agent_service, "logger", spy)
        dify = StubDify(post_payload=_run_payload(run_id="run_001", tokens=120))
        usage = StubUsage()
        service = _service(dify=dify, usage=usage)

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="生成技术方案", inputs={"a": 1}),
            _auth(),
        )

        # 请求形状：底座 user 必填、必须是阻塞模式
        path, body = dify.posts[0]
        assert path == "/v1/workflows/run"
        assert body["response_mode"] == "blocking"
        assert body["user"] == USER
        assert body["inputs"] == {"a": 1}
        # 任务 ID 采用底座运行 ID（便于反查）
        assert created.task_id == "task_run_001"

        assert _stored_status(service, created.task_id, _auth()) == TaskStatus.COMPLETED

        # 结算：幂等键是 task_id，额度取 total_tokens
        assert usage.calls == [
            {"tenant_id": TENANT, "user_id": USER, "task_id": "task_run_001", "units": 120}
        ]

    async def test_running_run_stays_running_without_settlement(self) -> None:
        """底座仍在跑 → 停留 `running`，且**不写**用量事件（跑完才结算）。"""
        dify = StubDify(post_payload=_run_payload(status="running"))
        usage = StubUsage()
        service = _service(dify=dify, usage=usage)

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        assert _stored_status(service, created.task_id, _auth()) == TaskStatus.RUNNING
        assert usage.calls == []

    async def test_failed_run_maps_to_failed_without_settlement(self) -> None:
        """底座失败 → `failed`（不是 `running`，否则前端永久转圈）。"""
        dify = StubDify(post_payload=_run_payload(status="failed"))
        usage = StubUsage()
        service = _service(dify=dify, usage=usage)

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        assert _stored_status(service, created.task_id, _auth()) == TaskStatus.FAILED
        assert usage.calls == []

    @pytest.mark.parametrize("tokens", ["abc", None, "", {"x": 1}])
    async def test_unusable_token_count_settles_nothing(self, tokens: Any) -> None:
        """`total_tokens` 坏值不许把任务创建打红，也不许按 0 之外的值结算。"""
        dify = StubDify(post_payload=_run_payload(status="succeeded", tokens=tokens))
        usage = StubUsage()
        service = _service(dify=dify, usage=usage)

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        assert created.task_id.startswith("task_")
        assert usage.calls == []

    async def test_missing_run_id_keeps_generated_task_id(self) -> None:
        """底座没给 `workflow_run_id` 时保留本地 ID，不生成 `task_` 空壳。"""
        dify = StubDify(post_payload={"data": {"status": "succeeded", "total_tokens": 10}})
        usage = StubUsage()
        service = _service(dify=dify, usage=usage)

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        assert created.task_id.startswith("task_")
        assert created.task_id != "task_"
        assert usage.calls == [], "没有 run_id 时不应结算（幂等键不可靠）"

    async def test_local_placeholder_record_is_removed_when_run_id_arrives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """底座回传 `workflow_run_id` 后，按本地 ULID 落下的占位记录必须被删掉。

        早前实现只"追加"新 ID 的两条记录、不删旧键，于是每次真实调用都在内存里
        留下一条永远 `running`、进度 0 的孤儿任务：`list_tasks` 把它返回给前端，
        "我的任务"里就出现永不结束的项，用户看不到进度还会重复提交同一次任务。
        """
        local_id = "task_01K4Q8Z2X7M9B3N5P6R8T0V2W4"
        monkeypatch.setattr(agent_service, "new_task_id", lambda: local_id)
        dify = StubDify(post_payload=_run_payload(run_id="run_777", status="succeeded"))
        service = _service(dify=dify, usage=StubUsage())

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        assert created.task_id == "task_run_777"
        # ① 内存里只剩按底座运行 ID 建的那一条（旧占位记录已被清掉）
        assert len(service._tasks) == 1  # noqa: SLF001
        assert list(service._tasks) == [(TENANT, "task_run_777")]  # noqa: SLF001
        # ② 旧 ULID 键在任务表与步骤表里都不存在；新键的步骤表已建
        assert (TENANT, local_id) not in service._tasks  # noqa: SLF001
        assert (TENANT, local_id) not in service._steps  # noqa: SLF001
        assert (TENANT, "task_run_777") in service._steps  # noqa: SLF001
        # ③ 列表只有 1 条，且状态不是永不结束的 running
        result = service.list_tasks(ListTasksQuery(page=1, page_size=50), _auth())
        assert result.meta.total == 1
        assert [item.task_id for item in result.items] == ["task_run_777"]
        assert result.items[0].status == TaskStatus.COMPLETED

    async def test_settlement_failure_does_not_break_task_creation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """计量链路故障只记 WARN：任务照常返回（对账脚本负责发现偏差）。"""
        spy = LoggerSpy()
        monkeypatch.setattr(agent_service, "logger", spy)
        dify = StubDify(post_payload=_run_payload(status="succeeded", tokens=50))
        service = _service(dify=dify, usage=StubUsage(boom=True))

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        assert created.task_id.startswith("task_")
        warnings = spy.named("usage_automation_failed")
        assert warnings and warnings[0]["detail"] == "RuntimeError"

    async def test_tool_grants_are_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """授权了工具时日志里带上 `tool_grants`（排障要看"这次能调什么"）。"""
        spy = LoggerSpy()
        monkeypatch.setattr(agent_service, "logger", spy)
        service = _service(dify=StubDify(post_payload=_run_payload(status="running")))

        await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标", tool_grants=["http"]), _auth()
        )

        requested = spy.named("agent_task_create_requested")
        assert requested and requested[0]["tool_grants"] == ["http"]


# ===========================================================================
# A3 查询 / A5 取消（真实下游模式）
# ===========================================================================
class TestTaskDetailWithDownstream:
    """查询与取消在真实下游模式下的行为。"""

    async def test_get_task_refreshes_from_downstream(self) -> None:
        """归属确认后才回查底座，并把底座运行结果映射为业务结果。"""
        dify = StubDify(
            post_payload=_run_payload(run_id="run_002", status="running"),
            get_payload={
                "status": "succeeded",
                "outputs": {
                    "summary": "已生成技术方案",
                    "total_units": 7,
                    "artifacts": [
                        {"type": "file", "name": "方案.md", "download_url": "https://x/1"}
                    ],
                },
            },
        )
        service = _service(dify=dify, usage=StubUsage())
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        detail = await service.get_task(created.task_id, _auth())

        # 底座路径里是原生 run id（不带自研前缀）
        assert dify.gets == ["/v1/workflows/run/run_002"]
        assert detail.status == TaskStatus.COMPLETED
        assert detail.progress == 100
        assert detail.result is not None
        assert detail.result.summary == "已生成技术方案"
        assert detail.result.artifacts[0].name == "方案.md"
        assert detail.usage.total_units == 7

    async def test_get_task_never_asks_downstream_for_foreign_task(self) -> None:
        """别人的任务 ID 一律 12001，且**不向底座发任何请求**。

        早前实现"本地查不到就问底座"是第二条越权路径：底座按原生 run id 取结果，
        不知道租户，于是任何租户都能读到别人的产物。
        """
        dify = StubDify(post_payload=_run_payload(status="running"))
        service = _service(dify=dify, usage=StubUsage())
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        dify.gets.clear()

        with pytest.raises(ApiError) as exc_info:
            await service.get_task(created.task_id, _auth(tenant_id=OTHER_TENANT))

        assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND
        assert dify.gets == []

    async def test_cancel_posts_stop_to_downstream(self) -> None:
        """取消要把 `/stop` 下达到**正确的原生 run id**（否则取消别人的运行）。"""
        dify = StubDify(post_payload=_run_payload(run_id="run_003", status="running"))
        service = _service(dify=dify, usage=StubUsage())
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        detail = await service.cancel_task(created.task_id, _auth())

        assert dify.posts[-1] == ("/v1/workflows/runs/run_003/stop", {})
        assert detail.status == TaskStatus.CANCELLED

    async def test_cancel_completed_task_is_conflict(self) -> None:
        """已完成的任务不能再取消 → 12003（终态冲突），且不下达 `/stop`。"""
        dify = StubDify(post_payload=_run_payload(run_id="run_004", status="succeeded"))
        service = _service(dify=dify, usage=StubUsage())
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        dify.posts.clear()

        with pytest.raises(ApiError) as exc_info:
            await service.cancel_task(created.task_id, _auth())

        assert exc_info.value.code == ErrorCode.TASK_CANCELLED
        assert dify.posts == []

    async def test_foreign_task_cannot_be_cancelled(self) -> None:
        """越权取消比越权读更严重：未命中即 12001，绝不下达 `/stop`。"""
        dify = StubDify(post_payload=_run_payload(status="running"))
        service = _service(dify=dify, usage=StubUsage())
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        dify.posts.clear()

        with pytest.raises(ApiError) as exc_info:
            await service.cancel_task(created.task_id, _auth(tenant_id=OTHER_TENANT))

        assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND
        assert dify.posts == []


# ===========================================================================
# A2 列表（租户隔离与分页）
# ===========================================================================
class TestListTasks:
    """任务列表只遍历本租户记录。"""

    async def test_list_is_tenant_scoped_and_paginated(self) -> None:
        """三个自己 + 一个别人的任务：只看到自己的 3 条，分页切片正确。

        这里让底座**不返回** `workflow_run_id`（任务保留本地 ID），
        以便单独验证列表的租户隔离与分页，不受"按 run id 重新索引"干扰。
        """
        dify = StubDify(post_payload={"data": {"status": "running"}})
        service = _service(dify=dify)
        mine = [
            await service.create_task(
                CreateTaskRequest(conversation_id=f"conv_{index}", goal="目标"), _auth()
            )
            for index in range(3)
        ]
        await service.create_task(
            CreateTaskRequest(conversation_id="conv_other", goal="目标"),
            _auth(tenant_id=OTHER_TENANT),
        )

        first = service.list_tasks(ListTasksQuery(page=1, page_size=2), _auth())
        second = service.list_tasks(ListTasksQuery(page=2, page_size=2), _auth())

        assert first.meta.total == 3
        assert [item.task_id for item in first.items] == [item.task_id for item in mine[:2]]
        assert [item.task_id for item in second.items] == [mine[2].task_id]

    async def test_status_filter_excludes_others(self) -> None:
        """按状态过滤时不混入其他状态的记录。"""
        service = _service(dify=StubDify(post_payload={"data": {"status": "running"}}))
        cancelled = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        still_running = await service.create_task(
            CreateTaskRequest(conversation_id="conv_2", goal="目标"), _auth()
        )
        await service.cancel_task(cancelled.task_id, _auth())

        result = service.list_tasks(
            ListTasksQuery(page=1, page_size=20, status=TaskStatus.RUNNING), _auth()
        )

        assert [item.task_id for item in result.items] == [still_running.task_id]


# ===========================================================================
# A4 步骤映射
# ===========================================================================
class TestStepMapping:
    """`transform_node_event` 的产物与失败信息。"""

    def test_outputs_are_summarized_and_artifacts_mapped(self) -> None:
        """节点输出 → 业务摘要 + 步骤产物（含版本历史）。"""
        step = _service().transform_node_event(
            {
                "event": "node_finished",
                "data": {
                    "index": 1,
                    "status": "succeeded",
                    "title": "生成方案",
                    "outputs": {
                        "summary": "已生成 3 份材料",
                        "artifacts": [
                            {
                                "artifact_id": "art_1",
                                "output_key": "report",
                                "type": "file",
                                "name": "方案.md",
                                "version": "3",
                                "version_history": [
                                    {"version": "2", "created_at": "2026-01-01T00:00:00Z"},
                                    {"version": "bad", "created_at": ""},
                                    "不是对象",
                                ],
                            },
                            {
                                "artifact_id": "art_2",
                                "name": "无版本历史.md",
                                # 版本历史形态异常（不是列表）→ 空历史，而不是抛异常
                                "version_history": "oops",
                            },
                        ],
                    },
                },
            }
        )

        assert step is not None
        assert step.sequence == 2
        assert step.status == StepStatus.COMPLETED
        assert step.output_summary == "已生成 3 份材料"
        artifact = step.artifacts[0]
        assert artifact.artifact_id == "art_1"
        assert artifact.version == 3
        # 坏版本号退回 1（不让一个字段把整页打红）
        assert [version.version for version in artifact.version_history] == [2, 1]
        assert step.artifacts[1].version_history == []
        assert step.failure is None

    def test_failure_text_is_business_language(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """失败步骤给出业务语言与可执行动作；底座原始报错只进日志。"""
        spy = LoggerSpy()
        monkeypatch.setattr(agent_service, "logger", spy)

        step = _service().transform_node_event(
            {
                "event": "node_finished",
                "data": {
                    "index": 2,
                    "status": "failed",
                    "title": "调用下游",
                    "error": "httpx.ConnectError: http://internal-dify:5001 refused",
                },
            }
        )

        assert step is not None
        assert step.status == StepStatus.FAILED
        assert step.failure is not None
        assert step.failure.retryable is True
        assert step.failure.operator_action
        serialized = step.model_dump_json()
        for leaked in ("httpx", "internal-dify", "ConnectError", "node_finished"):
            assert leaked not in serialized
        logged = spy.named("agent_step_failed")
        assert logged and "internal-dify" in logged[0]["detail"]

    def test_non_dict_outputs_are_summarized_generically(self) -> None:
        """输出是标量时给通用文案（不抛异常、不空摘要）。"""
        step = _service().transform_node_event(
            {"event": "node_finished", "data": {"index": 0, "status": "succeeded", "outputs": 42}}
        )
        assert step is not None
        assert step.output_summary == "已生成输出内容"

    def test_unknown_artifact_type_falls_back_to_text(self) -> None:
        """底座出现未知产物类型时退回 `text`（不让一个字段把整页打红）。"""
        detail = _service()._transform_task_detail(  # noqa: SLF001
            "task_1",
            {
                "status": "succeeded",
                "outputs": {
                    "artifacts": [{"type": "hologram", "name": "x", "key": "k1", "sha256": "abc"}]
                },
            },
        )
        assert detail.result is not None
        artifact = detail.result.artifacts[0]
        assert artifact.type == ArtifactType.TEXT
        # `key` / `sha256` 是底座旧字段名，必须也能取到
        assert artifact.output_key == "k1"
        assert artifact.content_sha256 == "abc"


# ===========================================================================
# A6 事件推送
# ===========================================================================
class TestStreamEvents:
    """SSE 帧：自研事件名 + 步骤逐条推送。"""

    async def test_stream_pushes_each_step_then_completion(self) -> None:
        """有步骤时逐条推 `step_started` / `step_finished`，最后是 `task_completed`。"""
        service = _service(dify=StubDify(post_payload=_run_payload(status="running")))
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        service.append_steps(
            created.task_id,
            [
                StepItem(step_id="step_1", sequence=1, title="理解需求", status=StepStatus.RUNNING),
                StepItem(
                    step_id="step_2", sequence=2, title="生成方案", status=StepStatus.COMPLETED
                ),
            ],
            _auth(),
        )

        frames = [frame async for frame in service.stream_events(created.task_id, _auth())]

        events = [frame.splitlines()[0] for frame in frames]
        assert events == [
            "event: step_started",
            "event: step_finished",
            "event: task_completed",
        ]
        assert "理解需求" in frames[0]
        assert "生成方案" in frames[1]
        assert '"running"' in frames[-1]
        # 底座事件名一律不外泄
        assert not any("node_" in frame for frame in frames)

    async def test_ensure_task_exists_rejects_foreign_task(self) -> None:
        """SSE 建流前的守卫必须做归属校验（流开始后无法再返回错误体）。"""
        service = _service(dify=StubDify(post_payload=_run_payload(status="running")))
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )

        await service.ensure_task_exists(created.task_id, _auth())
        with pytest.raises(ApiError) as exc_info:
            await service.ensure_task_exists(created.task_id, _auth(tenant_id=OTHER_TENANT))
        assert exc_info.value.code == ErrorCode.TASK_NOT_FOUND

    async def test_append_steps_keeps_existing_steps(self) -> None:
        """重复追加是"追加"而不是"覆盖"（联调时步骤会分批到达）。"""
        service = _service(dify=StubDify(post_payload=_run_payload(status="running")))
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_1", goal="目标"), _auth()
        )
        first = StepItem(step_id="step_1", sequence=1, title="理解需求")
        second = StepItem(step_id="step_2", sequence=2, title="生成方案")

        service.append_steps(created.task_id, [first], _auth())
        service.append_steps(created.task_id, [second], _auth())

        steps = await service.list_steps(created.task_id, _auth())
        assert [item.step_id for item in steps.steps] == ["step_1", "step_2"]


class TestUsageServiceContract:
    """`AgentService` 注入的用量服务必须是 `UsageService` 契约实现。"""

    def test_default_usage_service_is_real_one(self) -> None:
        """不注入计量时用真实服务（`get_usage_service`），不是 None。"""
        service = AgentService(settings=_settings(), dify=StubDify())  # type: ignore[arg-type]
        assert isinstance(service._usage, UsageService)  # noqa: SLF001
