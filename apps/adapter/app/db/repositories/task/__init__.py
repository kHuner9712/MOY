"""插件任务与步骤仓储（阶段三 B19，`architecture-phase3.md` §4.1 / §4.4 / D-22）——公开出口。

本包是 `app/db/repositories/task_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致）。五个文件的职责与依赖方向：

```
records.py    数据记录（dataclass）+ 状态常量/字段白名单 + 三个共用纯函数
   ↑
protocol.py   TaskStore 契约（唯一，内存/PG 都必须实现）
   ↑
memory.py     InMemoryTaskStore（development/test，含测试钩子 clear）
postgres.py   PostgresTaskStore（生产，表名/列白名单 + 行映射器 + 两张表仓储）
   ↑
__init__.py   进程级单例（get_task_store / get_memory_task_store / reset_task_store）
```

`protocol.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环。单向依赖由 `deploy/validate_repository_parity.py`
（G-38）与 `deploy/validate_import_directions.py`（G-37）双重把关。

**`get_memory_task_store()` 是测试专用出口**（清库用），语义原样保留：单例尚未创建时
它**会**创建内存实现并写回单例；当前是 PG 实现时返回 `None`。这条"顺手创建"的行为
看着可疑，但 `tests/**` 依赖它（先清库再造数），故不做"顺手统一"。

**`__all__` 与原 `task_repo.py` 完全一致（16 项，顺序不变）**：拆分不改变公开面，
16 处调用点无需改动。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.task.memory import InMemoryTaskStore
from app.db.repositories.task.postgres import (
    STEP_TABLE,
    TASK_TABLE,
    PostgresTaskStore,
)
from app.db.repositories.task.protocol import TaskStore
from app.db.repositories.task.records import (
    OCCUPYING_STATUSES,
    STEP_MUTABLE_FIELDS,
    TASK_MUTABLE_FIELDS,
    TERMINAL_STATUSES,
    StepRecord,
    TaskRecord,
    TaskStats,
    now_iso,
)

_store: TaskStore | None = None


def get_task_store(settings: Settings | None = None) -> TaskStore:
    """获取任务存储单例（按 `business_store` 选择后端）。

    Args:
        settings: 可选配置实例。

    Returns:
        TaskStore: 存储实现。
    """
    global _store
    effective = settings or get_settings()
    if _store is None:
        _store = (
            PostgresTaskStore(effective)
            if effective.business_store == "postgres"
            else InMemoryTaskStore()
        )
    return _store


def get_memory_task_store() -> InMemoryTaskStore | None:
    """获取内存存储（测试清库用）。

    Returns:
        InMemoryTaskStore | None: 内存实现；当前为 PG 时返回 None。
    """
    global _store
    if _store is None:
        _store = InMemoryTaskStore()
    return _store if isinstance(_store, InMemoryTaskStore) else None


def reset_task_store() -> None:
    """重置单例（测试隔离）。"""
    global _store
    _store = None


__all__: list[str] = [
    "OCCUPYING_STATUSES",
    "STEP_MUTABLE_FIELDS",
    "STEP_TABLE",
    "TASK_MUTABLE_FIELDS",
    "TASK_TABLE",
    "TERMINAL_STATUSES",
    "InMemoryTaskStore",
    "PostgresTaskStore",
    "StepRecord",
    "TaskRecord",
    "TaskStats",
    "TaskStore",
    "get_memory_task_store",
    "get_task_store",
    "now_iso",
    "reset_task_store",
]
