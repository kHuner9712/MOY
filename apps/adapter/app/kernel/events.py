"""内核事件日志（L0，设计文档 §7.1：`kernel/events.py` = `EventStore`）。

**这是内核看到的唯一事件接口**：`append` / `read_after` / `fork` / `verify_chain`。

契约（`EventStore` Protocol）的**规范定义处**在
`app/db/repositories/event_log/protocol.py`，本模块原样导出。为什么不在这里再声明一遍：
`deploy/validate_repository_parity.py`（G-38）要求「协议 + `InMemory*` + `Postgres*`
三者在同一仓储单元内可配对」，协议一旦搬到本模块，双实现就得 import 内核、内核再
import 双实现在用的 records，立刻成环。因此规范定义留在仓储单元，内核模块做
**公开面收敛**：上层（`app.services` / `app.api`）只从这里 import，不直接碰
`app.db.repositories.event_log`，这样将来实现换掉（例如加 Redis 缓存层）时
改的是本模块的导出，而不是几十个调用点。

本模块另外提供两个**内核级**便利函数（不是协议的一部分，只是把两条纪律写成可执行
代码）：

- `append_model_deltas`：按 `plan_delta_rows` 做**行级采样**，一个 turn 只落首尾两条
  `model.delta`（R3 风险对冲：否则行数会到 turn 数的 20 倍）；
- `replay_state_block`：把一条流投影成 `TaskStateBlock` 的**确定性**形状
  （同一 `session_id` 回放三次必须完全一致——规格 §3「完成判据」原文）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.core.config import Settings
from app.db.repositories.event_log import (
    DELTA_SAMPLE_HEAD_CHARS,
    DELTA_SAMPLE_TAIL_CHARS,
    EVENT_MODEL_DELTA,
    EVENT_SESSION_CREATED,
    EVENT_SESSION_FORKED,
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_REQUESTED,
    EVENT_TURN_FINISHED,
    EVENT_TURN_STARTED,
    EVENT_TYPES,
    FORK_DEPTH_LIMIT,
    GENESIS_HASH,
    MAX_DELTA_ROWS_PER_TURN,
    MAX_SEQ_ATTEMPTS,
    Event,
    EventStore,
    build_session_id,
    canonical_json,
    compact_delta_payload,
    compute_hash,
    get_event_store,
    plan_delta_rows,
    reset_event_store,
    validate_event_type,
)

#: `TaskStateBlock` 里"产物键"字段名（设计文档 §5.2 任务状态块的必填项）
ARTIFACT_KEYS_FIELD: Final[str] = "artifact_keys"

#: `TaskStateBlock` 里"已完成步骤"字段名
COMPLETED_STEPS_FIELD: Final[str] = "completed_steps"

#: `TaskStateBlock` 里"待确认项"字段名
PENDING_APPROVALS_FIELD: Final[str] = "pending_approvals"

#: 事件载荷里承载步骤信息的键
STEP_PAYLOAD_KEY: Final[str] = "step"

#: 事件载荷里承载产物键的键（`artifact.created {key: ...}`）
ARTIFACT_KEY_PAYLOAD_FIELD: Final[str] = "key"

#: 单次回放取回的批量（回放一律按批读，不假设会话长度）
_REPLAY_BATCH: Final[int] = 1000

#: 投影用到的三个事件类型（与 `EVENT_TYPES` 同源，此处只做可读别名）
_EVENT_ARTIFACT_CREATED: Final[str] = "artifact.created"
_EVENT_APPROVAL_REQUESTED: Final[str] = "approval.requested"
_EVENT_APPROVAL_RESOLVED: Final[str] = "approval.resolved"


def event_store(settings: Settings | None = None) -> EventStore:
    """取内核事件存储（薄封装，便于测试替换）。

    Args:
        settings: 可选配置实例。

    Returns:
        EventStore: 事件存储实现。
    """
    return get_event_store(settings)


async def append_model_deltas(
    store: EventStore,
    tenant_id: str,
    session_id: str,
    deltas: Sequence[str],
) -> list[Event]:
    """按行级采样写入一个 turn 的 `model.delta`（只落首尾两条）。

    为什么不在 `EventStore.append` 里做这件事：行级采样需要"知道本 turn 一共有多少
    片段"，那是**生产者**（loop）的上下文，不是存储的上下文。存储侧只负责载荷级采样
    （`compact_delta_payload`，原始文本压成首尾 + 摘要），两层各管一半，任一层生效
    都能挡住膨胀。

    Args:
        store: 事件存储。
        tenant_id: 租户 ID。
        session_id: 会话 ID。
        deltas: 本 turn 的全部增量片段（按到达顺序）。

    Returns:
        list[Event]: 实际落库的事件（0～`MAX_DELTA_ROWS_PER_TURN` 条）。
    """
    written: list[Event] = []
    for index in plan_delta_rows(len(deltas)):
        written.append(
            await store.append(
                tenant_id,
                session_id,
                EVENT_MODEL_DELTA,
                {"delta": deltas[index], "index": index, "total": len(deltas)},
            )
        )
    return written


async def replay_state_block(
    store: EventStore,
    tenant_id: str,
    session_id: str,
) -> dict[str, Any]:
    """把事件流投影为 `TaskStateBlock`（确定性：同一条流恒得同一结果）。

    投影规则（**只依赖事件本身**，不读任何"当前状态"副本）：
    - `completed_steps`：`tool.completed` 的 `step.step_id` 累计（保序去重）；
    - `artifact_keys`：`artifact.created` 的 `key` 累计（保序去重）；
    - `pending_approvals`：`approval.requested` 未配 `approval.resolved` 的
      `approval_id`；
    - `turns`：`turn.finished` 条数。

    Args:
        store: 事件存储。
        tenant_id: 租户 ID。
        session_id: 会话 ID。

    Returns:
        dict[str, Any]: 状态块（键与顺序固定，便于逐字比对）。
    """
    completed_steps: list[str] = []
    artifact_keys: list[str] = []
    pending_approvals: list[str] = []
    turns = 0
    for event in await store.read_after(tenant_id, session_id, 0, limit=_REPLAY_BATCH):
        if event.type == EVENT_TOOL_COMPLETED:
            step = event.payload.get(STEP_PAYLOAD_KEY)
            step_id = step.get("step_id") if isinstance(step, Mapping) else None
            if isinstance(step_id, str) and step_id and step_id not in completed_steps:
                completed_steps.append(step_id)
        elif event.type == _EVENT_ARTIFACT_CREATED:
            key = event.payload.get(ARTIFACT_KEY_PAYLOAD_FIELD)
            if isinstance(key, str) and key and key not in artifact_keys:
                artifact_keys.append(key)
        elif event.type == _EVENT_APPROVAL_REQUESTED:
            approval_id = event.payload.get("approval_id")
            if (
                isinstance(approval_id, str)
                and approval_id
                and approval_id not in pending_approvals
            ):
                pending_approvals.append(approval_id)
        elif event.type == _EVENT_APPROVAL_RESOLVED:
            approval_id = event.payload.get("approval_id")
            if isinstance(approval_id, str) and approval_id in pending_approvals:
                pending_approvals.remove(approval_id)
        elif event.type == EVENT_TURN_FINISHED:
            turns += 1
    return {
        COMPLETED_STEPS_FIELD: completed_steps,
        ARTIFACT_KEYS_FIELD: artifact_keys,
        PENDING_APPROVALS_FIELD: pending_approvals,
        "turns": turns,
    }


__all__: list[str] = [
    "ARTIFACT_KEYS_FIELD",
    "ARTIFACT_KEY_PAYLOAD_FIELD",
    "COMPLETED_STEPS_FIELD",
    "DELTA_SAMPLE_HEAD_CHARS",
    "DELTA_SAMPLE_TAIL_CHARS",
    "EVENT_MODEL_DELTA",
    "EVENT_SESSION_CREATED",
    "EVENT_SESSION_FORKED",
    "EVENT_TOOL_COMPLETED",
    "EVENT_TOOL_REQUESTED",
    "EVENT_TURN_FINISHED",
    "EVENT_TURN_STARTED",
    "EVENT_TYPES",
    "FORK_DEPTH_LIMIT",
    "GENESIS_HASH",
    "MAX_DELTA_ROWS_PER_TURN",
    "MAX_SEQ_ATTEMPTS",
    "PENDING_APPROVALS_FIELD",
    "STEP_PAYLOAD_KEY",
    "Event",
    "EventStore",
    "append_model_deltas",
    "build_session_id",
    "canonical_json",
    "compact_delta_payload",
    "compute_hash",
    "event_store",
    "plan_delta_rows",
    "replay_state_block",
    "reset_event_store",
    "validate_event_type",
]
