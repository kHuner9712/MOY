"""会话事件日志仓储（T02）——公开出口。

本包是 `event_log` 域的仓储单元，体例与 `app/db/repositories/task/` 一致：

```
records.py    记录 + 14 类事件封闭枚举 + hash/规范化/采样纯函数
   ↑
protocol.py   EventStore 契约（唯一）
   ↑
memory.py     InMemoryEventStore（development / test）
postgres.py   PostgresEventStore（生产）
   ↑
__init__.py   进程级单例（get_event_store / get_memory_event_store / reset_event_store）
```

**单例为什么按 `business_store` 二次判定**：`business_store=memory` 时连引擎都不创建
（`db/session.py::get_session_factory` 返回 None），此时选 PG 实现会让每次 append
都抛 30004。这与 `task/__init__.py` 的口径完全一致——仓储选择由**同一个配置项**
决定，不新增第二套开关（规格 §3.1 E1：不许为"能不能用事件日志"再开一条存储路径）。

**`get_memory_event_store()` 是测试专用出口**：单例尚未创建时它会创建内存实现并写回；
当前是 PG 实现时返回 None。与 `get_memory_task_store()` 同语义（`tests/**` 依赖它先清库）。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.event_log.memory import DEFAULT_READ_LIMIT, InMemoryEventStore
from app.db.repositories.event_log.postgres import EVENT_LOG_TABLE, PostgresEventStore
from app.db.repositories.event_log.protocol import EventStore
from app.db.repositories.event_log.records import (
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
    build_session_id,
    canonical_json,
    compact_delta_payload,
    compute_hash,
    plan_delta_rows,
    validate_event_type,
)

_store: EventStore | None = None


def get_event_store(settings: Settings | None = None) -> EventStore:
    """获取事件存储单例（按 `business_store` 选择后端）。

    Args:
        settings: 可选配置实例。

    Returns:
        EventStore: 存储实现。
    """
    global _store
    effective = settings or get_settings()
    if _store is None:
        _store = (
            PostgresEventStore(effective)
            if effective.business_store == "postgres"
            else InMemoryEventStore()
        )
    return _store


def get_memory_event_store() -> InMemoryEventStore | None:
    """获取内存实现（测试清库用）。

    Returns:
        InMemoryEventStore | None: 内存实现；当前为 PG 时返回 None。
    """
    global _store
    if _store is None:
        _store = InMemoryEventStore()
    return _store if isinstance(_store, InMemoryEventStore) else None


def reset_event_store() -> None:
    """重置单例（测试隔离）。"""
    global _store
    _store = None


__all__: list[str] = [
    "DEFAULT_READ_LIMIT",
    "DELTA_SAMPLE_HEAD_CHARS",
    "DELTA_SAMPLE_TAIL_CHARS",
    "EVENT_LOG_TABLE",
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
    "Event",
    "EventStore",
    "InMemoryEventStore",
    "PostgresEventStore",
    "build_session_id",
    "canonical_json",
    "compact_delta_payload",
    "compute_hash",
    "get_event_store",
    "get_memory_event_store",
    "plan_delta_rows",
    "reset_event_store",
    "validate_event_type",
]
