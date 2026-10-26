"""角色与权限点仓储（阶段二 B08，架构文档 §4.4 / §4.8 / 2.6 D-13）——公开出口。

本包是 `app/db/repositories/role_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致）。五个文件的职责与依赖方向：

```
records.py    数据记录（dataclass）与种子常量/纯函数，无内部依赖
   ↑
protocol.py   RoleStore 契约（唯一，内存/PG 都必须实现）
   ↑
memory.py     InMemoryRoleStore（development/test，含测试钩子 clear）
postgres.py   PostgresRoleStore（生产，表名常量 + 四个表仓储）
   ↑
__init__.py   进程级单例（get_role_store / reset_role_store）+ 统一 re-export
```

`protocol.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环。单向依赖由 `deploy/validate_repository_parity.py`
（G-38）与 `deploy/validate_import_directions.py`（G-37）双重把关。

**`__all__` 与原 `role_repo.py` 完全一致（19 项，顺序不变）**：拆分不改变公开面，
20 处调用点无需改动；改名/增删条目会同时触发本包的 RUF022 排序检查与调用点 import
失败，属于"故意不允许的顺手改"。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.role.memory import InMemoryRoleStore
from app.db.repositories.role.postgres import (
    CATALOG_TABLE,
    MEMBER_ROLE_TABLE,
    PLATFORM_TENANT,
    ROLE_PERMISSION_TABLE,
    ROLE_TABLE,
    PostgresRoleStore,
)
from app.db.repositories.role.protocol import RoleStore
from app.db.repositories.role.records import (
    BUILTIN_ROLE_DISPLAY_NAMES,
    MEMBER_STATUS_ACTIVE,
    MEMBER_STATUSES,
    PERMISSION_CATALOG_SEED,
    ROLE_ID_PREFIX,
    PermissionCatalogEntry,
    RoleRecord,
    builtin_role_id,
    role_display_name,
)

# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryRoleStore | None = None


def get_role_store(settings: Settings | None = None) -> RoleStore:
    """获取角色与权限点存储。

    Args:
        settings: 可选配置实例。

    Returns:
        RoleStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresRoleStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryRoleStore()
    return _memory_store


def reset_role_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "BUILTIN_ROLE_DISPLAY_NAMES",
    "CATALOG_TABLE",
    "MEMBER_ROLE_TABLE",
    "MEMBER_STATUSES",
    "MEMBER_STATUS_ACTIVE",
    "PERMISSION_CATALOG_SEED",
    "PLATFORM_TENANT",
    "ROLE_ID_PREFIX",
    "ROLE_PERMISSION_TABLE",
    "ROLE_TABLE",
    "InMemoryRoleStore",
    "PermissionCatalogEntry",
    "PostgresRoleStore",
    "RoleRecord",
    "RoleStore",
    "builtin_role_id",
    "get_role_store",
    "reset_role_store",
    "role_display_name",
]
