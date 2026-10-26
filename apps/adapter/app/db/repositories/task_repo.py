"""兼容层（deprecated shim）：`app.db.repositories.task_repo` → `app.db.repositories.task`。

**真源已迁到包**：`app/db/repositories/task/{records,protocol,memory,postgres,__init__}.py`
（审计 P1-8：仓储上帝文件 + 双后端镜像；切片 2B-2C 以 `role/` 为拆分布局样板）。

**本文件只做 re-export**，不得出现任何类/函数定义——包形态与 `*_repo.py` 同名并存时，
`deploy/validate_repository_parity.py`（G-38）要求本文件是**纯壳**：一旦这里重新长出
实现，"薄兼容层"就会慢慢变回第二份真源，拆分等于没做（G-38 判 exit 2）。

**为什么保留而不是直接删**：仍有 16 处调用点（`api/v1/stats.py`、
`business/{plugin,task}_service.py`、`tests/**`）按旧路径 import。本切片只做拆分、
不动调用点，把改动面压到"移动 + re-export"，任何一跳都能单独回滚。

**弃用计划**：调用点改为 `from app.db.repositories.task import ...` 后删本文件；
同步删除 `deploy/import_direction_baseline.txt` 里按旧路径登记的行（若有）——
路径改名后旧基线行会变 stale，G-37 会正确地判红，逼迫两处同步收口。
"""

from __future__ import annotations

from app.db.repositories.task import (
    OCCUPYING_STATUSES,
    STEP_MUTABLE_FIELDS,
    STEP_TABLE,
    TASK_MUTABLE_FIELDS,
    TASK_TABLE,
    TERMINAL_STATUSES,
    InMemoryTaskStore,
    PostgresTaskStore,
    StepRecord,
    TaskRecord,
    TaskStats,
    TaskStore,
    get_memory_task_store,
    get_task_store,
    now_iso,
    reset_task_store,
)

# `STATUS_QUEUED` 不在原 `__all__` 里（公开面不变），但原模块属性可读：
# `tests/integration/test_task_repo_pg.py` 按旧路径 import 它，故一并转出。
from app.db.repositories.task.records import STATUS_QUEUED  # noqa: F401

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
