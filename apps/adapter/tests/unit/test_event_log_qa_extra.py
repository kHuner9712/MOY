"""QA 补充用例（T02 复核，非开发者提交内容）。

本文件是 QA 独立复核时**新增**的用例，覆盖开发者自述里没有证明的三件事：

1. **并发取号**：同一 `session_id` 同时 append 5 条（`asyncio.gather`），
   `seq` 必须连续无空洞、无重复，hash 链必须仍可校验（内存实现；
   PG 实现的并发取号由 `--run-pg` 用例覆盖，本机无 PG 时无法判定）；
2. **服务层并发写**：`AgentService.append_steps` 并发调用同一任务，
   事件流仍必须自洽（E1：事件是唯一真源，并发下也不能出现断链）；
3. **delta 采样后的回放保真度**：采样后还能不能"还原首尾"，还是只剩摘要——
   这是判断"回放确定性"是否名副其实的依据；
4. **任务 ID 切换后的会话归属**：`create_task` 在底座返回
   `workflow_run_id` 后会把 `task_id` 换成 `task_{run_id}`，此时
   `session.created`（唯一承载 `goal` 的事件）落在哪个会话上。

第 4 条是**缺陷固化用例**：它当前红，见 `test_goal_event_is_readable_on_returned_task_session`
的 `xfail(strict=True)` 理由。修复后 strict xfail 会变成 XPASS 失败，
提醒修复者把标记摘掉（而不是让缺陷悄悄留在绿线后面）。
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest

from app.clients.dify_client import DifyClient
from app.core.config import Settings
from app.core.security import AuthContext
from app.db.repositories.event_log.memory import InMemoryEventStore
from app.db.repositories.event_log.records import (
    DELTA_SAMPLE_HEAD_CHARS,
    DELTA_SAMPLE_TAIL_CHARS,
    EVENT_TURN_STARTED,
    build_session_id,
    compute_hash,
)
from app.kernel.events import append_model_deltas
from app.schemas.agent import CreateTaskRequest, StepItem, StepStatus
from app.services.agent_service import AgentService

pytestmark = pytest.mark.unit

TENANT = "tnt_qa_extra"

_BASE_PARAMS: dict[str, Any] = {
    "env": "test",
    "deploy_mode": "saas",
    "business_store": "memory",
    "auth_mode": "stub",
    "jwt_secret": "qa-extra-secret",
    "ratelimit_enabled": False,
    "background_jobs_enabled": False,
}


def _auth(tenant_id: str = TENANT) -> AuthContext:
    """构造鉴权上下文。

    Args:
        tenant_id: 租户 ID。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id="usr_qa", tenant_id=tenant_id)


class _StubResponse:
    """底座响应替身（业务层只读 `payload`）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化。

        Args:
            payload: 响应体。
        """
        self.payload = payload


class _StubDify:
    """底座客户端替身：`post` 恒定返回给定 payload。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化。

        Args:
            payload: `post()` 的响应体。
        """
        self.payload = payload

    async def post(self, path: str, body: dict[str, Any]) -> _StubResponse:
        """返回预设响应。

        Args:
            path: 请求路径。
            body: 请求体。

        Returns:
            _StubResponse: 响应。
        """
        return _StubResponse(self.payload)

    async def get(self, path: str) -> _StubResponse:
        """返回空响应。

        Args:
            path: 请求路径。

        Returns:
            _StubResponse: 响应。
        """
        return _StubResponse({})


def _service(
    *,
    store: InMemoryEventStore | None = None,
    dify: Any = None,
    downstream_enabled: bool = False,
) -> AgentService:
    """构造注入了内存事件存储的服务。

    Args:
        store: 事件存储（缺省新建）。
        dify: 底座客户端替身。
        downstream_enabled: 是否启用真实下游路径。

    Returns:
        AgentService: 服务实例。
    """
    settings = Settings(**_BASE_PARAMS, downstream_enabled=downstream_enabled)
    return AgentService(
        settings=settings,
        dify=dify or DifyClient("http://example.invalid", "app-qa"),
        events=store or InMemoryEventStore(),
    )


# ===========================================================================
# 1 / 2. 并发追加：取号与 hash 链在并发下是否自洽
# ===========================================================================
class TestConcurrentAppend:
    """并发 append 是 `MAX(seq)+1` 取号唯一的失效面，必须有用例钉住。"""

    async def test_five_concurrent_appends_keep_seq_contiguous(self) -> None:
        """同一会话并发 5 条：`seq` 连续无空洞、无重复，hash 链可校验。"""
        store = InMemoryEventStore()
        session = build_session_id(TENANT, "task_concurrent")

        events = await asyncio.gather(
            *(store.append(TENANT, session, EVENT_TURN_STARTED, {"n": index}) for index in range(5))
        )

        seqs = sorted(event.seq for event in events)
        assert seqs == [1, 2, 3, 4, 5], f"seq 出现空洞或重复：{seqs}"
        stored = await store.read_after(TENANT, session)
        assert [event.seq for event in stored] == [1, 2, 3, 4, 5]
        # 链必须仍然首尾相接（并发取号若重号，prev_hash 会分叉成两条链）
        for previous, current in itertools.pairwise(stored):
            assert current.prev_hash == previous.hash
        for event in stored:
            assert (
                compute_hash(event.prev_hash, event.event_id, event.type, event.payload)
                == event.hash
            )
        assert await store.verify_chain(TENANT, session) is True

    async def test_concurrent_append_steps_keep_stream_consistent(self) -> None:
        """服务层并发写同一任务：事件流仍自洽，步骤投影不丢不重。"""
        service = _service()
        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_q", goal="并发写"), _auth()
        )
        steps = [
            StepItem(
                step_id=f"step_{index}",
                sequence=index,
                title=f"步骤{index}",
                status=StepStatus.COMPLETED,
            )
            for index in range(1, 6)
        ]

        await asyncio.gather(
            *(service.append_steps(created.task_id, [step], _auth()) for step in steps)
        )

        session = build_session_id(TENANT, created.task_id)
        stored = await service._events.read_after(TENANT, session)  # noqa: SLF001
        assert [event.seq for event in stored] == [1, 2, 3, 4, 5, 6]
        projected = await service.list_steps(created.task_id, _auth())
        assert sorted(item.step_id for item in projected.steps) == [
            f"step_{index}" for index in range(1, 6)
        ]
        assert await service._events.verify_chain(TENANT, session) is True  # noqa: SLF001


# ===========================================================================
# 3. delta 采样后的回放保真度
# ===========================================================================
class TestDeltaReplayFidelity:
    """采样后回放能还原什么、还原不了什么，必须写清楚而不是"看起来能回放"。"""

    async def test_replay_recovers_only_head_and_tail_of_the_answer(self) -> None:
        """一个 turn 的 40 个片段只落 2 行，回放只能拿到全文的首尾各 ≤64 字。"""
        store = InMemoryEventStore()
        session = "s_delta_fidelity"
        # 40 个片段拼成 4000 字答案：首片段 200 字、末片段 100 字，中间 38 段
        deltas = [f"<{index:03d}>" + "中" * 96 for index in range(40)]
        original = "".join(deltas)
        middle_marker = deltas[20]

        written = await append_model_deltas(store, TENANT, session, deltas)
        assert len(written) == 2  # 行级采样：首 + 尾

        replay = await store.read_after(TENANT, session)
        heads = [event.payload["sample"]["head"] for event in replay]
        tails = [event.payload["sample"]["tail"] for event in replay]

        # 首片段的首部 = 全文前 64 字（可还原）
        assert heads[0] == original[:DELTA_SAMPLE_HEAD_CHARS]
        # 末片段（100 字 > 64）的尾部 = 全文最后 64 字（可还原）
        assert tails[-1] == original[-DELTA_SAMPLE_TAIL_CHARS:]

        # 中间 38 个片段**一个字节都没进库**：回放拿不到中间内容
        dumped = repr([event.payload for event in replay])
        assert middle_marker not in dumped
        reconstructed = heads[0] + tails[-1]
        assert len(original) - len(reconstructed) > 3000, "中间内容不可还原（只剩摘要）"
        # 采样摘要的 sha256 是**整个片段**的摘要，而片段只剩首尾：
        # 客户拿到事件流也无法用它校验片段完整性（摘要不可验）
        assert replay[0].payload["sample"]["chars"] == len(deltas[0])


# ===========================================================================
# 4. 任务 ID 切换后的会话归属（缺陷固化）
# ===========================================================================
class TestSessionOwnershipAfterRunIdSwitch:
    """`create_task` 换 ID 后，新任务的会话里还有没有 `session.created`。

    **缺陷 D2 已修（2026-11-04）**：`session.created` 现在会在换 ID 后于**新会话**
    再写一条（payload 带 `origin_task_id` / `origin_session_id` / `run_id` /
    `reissued=true`），占位会话那条保留（append-only）。原先的
    `xfail(strict=True)` 按约定摘除——strict xfail 修好后会变 XPASS 判红，
    这条用例现在是正常绿线用例。
    """

    async def test_goal_event_is_readable_on_returned_task_session(self) -> None:
        """返回给调用方的 `task_id` 所属会话里必须能读到 `session.created`（含 goal）。"""
        store = InMemoryEventStore()
        dify = _StubDify({"workflow_run_id": "run_qa_1", "data": {"status": "running"}})
        service = _service(store=store, dify=dify, downstream_enabled=True)

        created = await service.create_task(
            CreateTaskRequest(conversation_id="conv_qa", goal="重建目标要用这条事件"), _auth()
        )
        assert created.task_id == "task_run_qa_1"

        session = build_session_id(TENANT, created.task_id)
        events = await store.read_after(TENANT, session)

        assert [event.type for event in events] == ["session.created"]
        assert events[0].payload["goal"] == "重建目标要用这条事件"
        # 搬迁可追溯：新会话第一条事件指回占位任务与占位会话
        assert events[0].payload["origin_task_id"].startswith("task_")
        assert events[0].payload["origin_session_id"].startswith(TENANT)
        assert events[0].payload["run_id"] == "run_qa_1"
        assert events[0].payload["reissued"] is True
