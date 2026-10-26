"""组织架构仓储（阶段二 B08，架构文档 2.6 / D-13）——公开出口。

本包是 `app/db/repositories/org_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致）。六个文件的职责与依赖方向：

```
records.py    数据记录 + 层级/前缀常量 + normalize_department_id
   ↑
protocol.py   OrgStore 契约（唯一，内存/PG 都必须实现）
   ↑
directory.py  OrgUsageDirectory（只依赖协议，供 UsageService 注入）
memory.py     InMemoryOrgStore（development/test，含测试钩子 clear）
postgres.py   PostgresOrgStore（生产，表名常量 + 行映射器 + 两张表仓储）
   ↑
__init__.py   进程级单例（get_org_store / reset_org_store）+ get_org_directory
```

`protocol.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环。单向依赖由 `deploy/validate_repository_parity.py`
（G-38）与 `deploy/validate_import_directions.py`（G-37）双重把关。

**`get_org_directory()` 与单例同处本文件**：它只是 `OrgUsageDirectory(get_org_store(settings))`
一行，放 `directory.py` 会让 `__init__ ↔ directory` 成环（要么延迟导入，要么让适配器
反依赖单例）。工厂与单例放在一起，是这个包里唯一不成环的摆法。

**`__all__` 与原 `org_repo.py` 完全一致（15 项，顺序不变）**：拆分不改变公开面，
26 处调用点无需改动。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.org.directory import OrgUsageDirectory
from app.db.repositories.org.memory import InMemoryOrgStore
from app.db.repositories.org.postgres import (
    DEPARTMENT_TABLE,
    MEMBER_TABLE,
    PostgresOrgStore,
)
from app.db.repositories.org.protocol import OrgStore
from app.db.repositories.org.records import (
    DEPARTMENT_ID_PREFIX,
    MAX_DEPARTMENT_LEVEL,
    ROOT_PARENT_ID,
    DepartmentRecord,
    MemberLink,
    normalize_department_id,
)

# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryOrgStore | None = None


def get_org_store(settings: Settings | None = None) -> OrgStore:
    """获取组织架构存储。

    Args:
        settings: 可选配置实例。

    Returns:
        OrgStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresOrgStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryOrgStore()
    return _memory_store


def reset_org_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


def get_org_directory(settings: Settings | None = None) -> OrgUsageDirectory:
    """获取计量服务所需的组织信息解析器。

    Args:
        settings: 可选配置实例。

    Returns:
        OrgUsageDirectory: 组织信息解析器。
    """
    return OrgUsageDirectory(get_org_store(settings))


__all__: list[str] = [
    "DEPARTMENT_ID_PREFIX",
    "DEPARTMENT_TABLE",
    "MAX_DEPARTMENT_LEVEL",
    "MEMBER_TABLE",
    "ROOT_PARENT_ID",
    "DepartmentRecord",
    "InMemoryOrgStore",
    "MemberLink",
    "OrgStore",
    "OrgUsageDirectory",
    "PostgresOrgStore",
    "get_org_directory",
    "get_org_store",
    "normalize_department_id",
    "reset_org_store",
]
