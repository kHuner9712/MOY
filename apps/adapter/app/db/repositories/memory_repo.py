"""兼容层（deprecated shim）：`app.db.repositories.memory_repo` → `app.db.repositories.memory`。

**真源已迁到包**：`app/db/repositories/memory/{records,protocol,memory,postgres,__init__}.py`
（审计 P1-8：仓储上帝文件 + 双后端镜像；切片 2B-2C 以 `role/` 为拆分布局样板）。

**本文件只做 re-export**，不得出现任何类/函数定义——包形态与 `*_repo.py` 同名并存时，
`deploy/validate_repository_parity.py`（G-38）要求本文件是**纯壳**：一旦这里重新长出
实现，"薄兼容层"就会慢慢变回第二份真源，拆分等于没做（G-38 判 exit 2）。

**为什么保留而不是直接删**：仍有 15 处调用点（`api/v1/memory.py`、
`business/memory_service.py`、`services/memory_extractor.py`、`tests/**`）按旧路径
import。本切片只做拆分、不动调用点，把改动面压到"移动 + re-export"。

**私有符号也要转出**：`tests/unit/test_memory_internals.py` 直接
`from app.db.repositories.memory_repo import _timestamp / _json_switches /
_parse_vector_text`。它们不在 `__all__` 里（公开面不变），但必须可从旧路径取到，
否则"零改动调用点"这条约束当场失效。

**弃用计划**：调用点改为 `from app.db.repositories.memory import ...` 后删本文件；
同步修改 `deploy/import_direction_baseline.txt` 里 `R-DB-SVC
app/db/repositories/memory_repo.py` 那一行的文件路径（同一处违规换了文件位置，
登记条数不变）——路径改名后旧基线行会变 stale，G-37 会正确地判红。
"""

from __future__ import annotations

from app.db.repositories.memory import (
    CATEGORY_ORDER,
    CONFIDENCE_LEVELS,
    EMBEDDING_TABLE,
    MEMORY_CATEGORIES,
    MEMORY_TABLE,
    SETTING_TABLE,
    InMemoryMemoryStore,
    MemoryRecord,
    MemorySettingRecord,
    MemoryStore,
    PostgresMemoryStore,
    get_memory_store,
    new_memory_id,
    reset_memory_store,
    vector_literal,
)

# 私有符号：`tests/unit/test_memory_internals.py` 直接 import 这三个 PG 侧辅助，
# 故旧路径必须继续取得到（不进 `__all__`，公开面不变）。
from app.db.repositories.memory.postgres import (  # noqa: F401
    _json_switches,
    _parse_vector_text,
    _timestamp,
)

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
