"""记忆事实仓储（阶段四 T4-03.3，架构 §3.3）——公开出口。

本包是 `app/db/repositories/memory_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致）。五个文件的职责与依赖方向：

```
records.py    数据记录（dataclass）+ 6 类事实/置信度/分类顺序 + new_memory_id()
   ↑
protocol.py   MemoryStore 契约（唯一，内存/PG 都必须实现）
   ↑
memory.py     InMemoryMemoryStore（development/test，含测试钩子 clear）
postgres.py   PostgresMemoryStore（生产，表名常量 + 行映射器 + 三张表仓储）
   ↑
__init__.py   进程级单例（get_memory_store / reset_memory_store）+ 统一 re-export
```

`protocol.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环。单向依赖由 `deploy/validate_repository_parity.py`
（G-38）与 `deploy/validate_import_directions.py`（G-37）双重把关。

**单例口径与 `role` / `usage` 不同（原样保留，不做"顺手统一"）**：本仓储的
`get_memory_store()` 把 PG 实现也缓存进 `_memory_store`
（`PostgresMemoryStore` 无状态，缓存成立），`reset_memory_store()` 只清引用、
不调用 `clear()`。

**`__all__` 与原 `memory_repo.py` 完全一致（15 项，顺序不变）**：拆分不改变公开面，
15 处调用点无需改动。`tests/unit/test_memory_internals.py` 还直接 import 了
`_timestamp` / `_json_switches` / `_parse_vector_text` 三个私有辅助，因此
`memory_repo` shim 一并转出它们（不进 `__all__`，公开面不变）。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.memory.memory import InMemoryMemoryStore
from app.db.repositories.memory.postgres import (
    EMBEDDING_TABLE,
    MEMORY_TABLE,
    SETTING_TABLE,
    PostgresMemoryStore,
    vector_literal,
)
from app.db.repositories.memory.protocol import MemoryStore
from app.db.repositories.memory.records import (
    CATEGORY_ORDER,
    CONFIDENCE_LEVELS,
    MEMORY_CATEGORIES,
    MemoryRecord,
    MemorySettingRecord,
    new_memory_id,
)

#: 记忆存储单例（按配置选中实现）
_memory_store: MemoryStore | None = None


def get_memory_store(settings: Settings | None = None) -> MemoryStore:
    """取记忆存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        MemoryStore: 存储实现。
    """
    global _memory_store
    conf = settings or get_settings()
    if _memory_store is None:
        _memory_store = PostgresMemoryStore(conf) if conf.uses_postgres else InMemoryMemoryStore()
    return _memory_store


def reset_memory_store() -> None:
    """重置记忆存储单例（测试隔离用）。"""
    global _memory_store
    _memory_store = None


__all__: list[str] = [
    "CATEGORY_ORDER",
    "CONFIDENCE_LEVELS",
    "EMBEDDING_TABLE",
    "MEMORY_CATEGORIES",
    "MEMORY_TABLE",
    "SETTING_TABLE",
    "InMemoryMemoryStore",
    "MemoryRecord",
    "MemorySettingRecord",
    "MemoryStore",
    "PostgresMemoryStore",
    "get_memory_store",
    "new_memory_id",
    "reset_memory_store",
    "vector_literal",
]
