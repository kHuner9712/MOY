"""插件域仓储（阶段三 B17，`architecture-phase3.md` §4.1 / §4.4）——公开出口。

本包是 `app/db/repositories/plugin_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致）。五个文件的职责与依赖方向：

```
records.py    数据记录 + 状态/白名单常量 + 域错误 + 注册表适配与快照构造
   ↑
protocol.py   PluginStore 契约（唯一，内存/PG 都必须实现）
   ↑
memory.py     InMemoryPluginStore（development/test，含测试钩子 clear/counts）
postgres.py   PostgresPluginStore（生产，表名常量 + 行映射器 + 五张表仓储）
   ↑
__init__.py   进程级单例（get_plugin_store / get_memory_plugin_store / reset_plugin_store）
```

`protocol.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环。单向依赖由 `deploy/validate_repository_parity.py`
（G-38）与 `deploy/validate_import_directions.py`（G-37）双重把关。

**`db → plugins` 的依赖只有一处**：`records.py` 转出 `ORG_CAPABILITIES` 与注册表门面
（`PluginRegistry` / `get_registry` / `manifest_digest`），memory/postgres 从 records 取用。
对应 G-37 基线的 3 行（`scripts` 侧同一违规），拆包后**行数与 module/mode 不变，仅 file 改**。

**`get_memory_plugin_store()` 是测试/工具出口**（不创建实例；未创建返回 None），
`reset_plugin_store()` 会先 `clear()` 再置空——两者语义原样保留。

**`__all__` 与原 `plugin_repo.py` 完全一致（27 项，顺序不变）**：拆分不改变公开面，
17 处调用点无需改动；`tests/**` 直接 import 的私有符号 `_timestamp`
（PG 行时间还原）由 shim 一并转出（不进 `__all__`）。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.plugin.memory import InMemoryPluginStore
from app.db.repositories.plugin.postgres import (
    CATALOG_TABLE,
    GRANT_TABLE,
    INSTALLATION_TABLE,
    PLATFORM_TENANT,
    POLICY_TABLE,
    VERSION_TABLE,
    PostgresPluginStore,
)
from app.db.repositories.plugin.protocol import PluginStore
from app.db.repositories.plugin.records import (
    CATALOG_AVAILABLE,
    INSTALLATION_MUTABLE_FIELDS,
    LIVE_STATUSES,
    STATUS_DISABLED,
    STATUS_ENABLED,
    STATUS_INSTALLED,
    STATUS_UNINSTALLED,
    VERSION_ACTIVE,
    VERSION_DEPRECATED,
    CatalogRecord,
    InstallationConflictError,
    InstallationRecord,
    VersionRecord,
    now_iso,
    version_records_from_registry,
)

# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryPluginStore | None = None


def get_plugin_store(settings: Settings | None = None) -> PluginStore:
    """获取插件存储。

    Args:
        settings: 可选配置实例。

    Returns:
        PluginStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresPluginStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryPluginStore()
    return _memory_store


def get_memory_plugin_store() -> InMemoryPluginStore | None:
    """取内存插件存储单例（不创建；测试与工具用）。

    Returns:
        InMemoryPluginStore | None: 已创建的实例；未创建返回 None。
    """
    return _memory_store


def reset_plugin_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "CATALOG_AVAILABLE",
    "CATALOG_TABLE",
    "GRANT_TABLE",
    "INSTALLATION_MUTABLE_FIELDS",
    "INSTALLATION_TABLE",
    "LIVE_STATUSES",
    "PLATFORM_TENANT",
    "POLICY_TABLE",
    "STATUS_DISABLED",
    "STATUS_ENABLED",
    "STATUS_INSTALLED",
    "STATUS_UNINSTALLED",
    "VERSION_ACTIVE",
    "VERSION_DEPRECATED",
    "VERSION_TABLE",
    "CatalogRecord",
    "InMemoryPluginStore",
    "InstallationConflictError",
    "InstallationRecord",
    "PluginStore",
    "PostgresPluginStore",
    "VersionRecord",
    "get_memory_plugin_store",
    "get_plugin_store",
    "now_iso",
    "reset_plugin_store",
    "version_records_from_registry",
]
