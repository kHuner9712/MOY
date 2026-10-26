"""用量事件与月度聚合仓储（阶段二 B07，架构文档 2.5 / D-12）——公开出口。

本包是 `app/db/repositories/usage_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致）。五个文件的职责与依赖方向：

```
records.py    数据记录（dataclass）+ 能力/维度/来源常量 + 账期纯函数，无内部依赖
   ↑
protocol.py   UsageStore 契约（唯一，内存/PG 都必须实现）
   ↑
memory.py     InMemoryUsageStore（development/test，含测试钩子 clear）
postgres.py   PostgresUsageStore（生产，表名常量 + 四个表仓储 + 幂等/聚合事务）
   ↑
__init__.py   进程级单例（get_usage_store / reset_usage_store）+ 统一 re-export
```

`protocol.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环。单向依赖由 `deploy/validate_repository_parity.py`
（G-38）与 `deploy/validate_import_directions.py`（G-37）双重把关。

**`__all__` 与原 `usage_repo.py` 完全一致（27 项，顺序不变）**：拆分不改变公开面，
16 处调用点无需改动。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.usage.memory import InMemoryUsageStore
from app.db.repositories.usage.postgres import (
    PLAN_TABLE,
    PLATFORM_TENANT,
    SUBSCRIPTION_TABLE,
    USAGE_EVENT_TABLE,
    USAGE_MONTHLY_TABLE,
    PostgresUsageStore,
)
from app.db.repositories.usage.protocol import UsageStore
from app.db.repositories.usage.records import (
    CAPABILITIES,
    CAPABILITY_AUTOMATION,
    CAPABILITY_CHAT,
    CAPABILITY_DOC_PROCESS,
    CAPABILITY_DOC_QA,
    DEFAULT_QUOTA_UNIT_LABEL,
    SCOPE_DEPARTMENT,
    SCOPE_MEMBER,
    SCOPE_TENANT,
    SOURCE_ESTIMATED,
    SOURCE_SETTLED,
    MonthlyUsageRecord,
    QuotaRecord,
    RangeEventStats,
    UsageEventRecord,
    current_period,
    period_bounds,
)

# ===========================================================================
# 进程级单例（内存后端跨模块共享；postgres 后端无状态）
# ===========================================================================
_memory_store: InMemoryUsageStore | None = None


def get_usage_store(settings: Settings | None = None) -> UsageStore:
    """获取计量存储。

    Args:
        settings: 可选配置实例。

    Returns:
        UsageStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresUsageStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryUsageStore()
    return _memory_store


def reset_usage_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "CAPABILITIES",
    "CAPABILITY_AUTOMATION",
    "CAPABILITY_CHAT",
    "CAPABILITY_DOC_PROCESS",
    "CAPABILITY_DOC_QA",
    "DEFAULT_QUOTA_UNIT_LABEL",
    "PLAN_TABLE",
    "PLATFORM_TENANT",
    "SCOPE_DEPARTMENT",
    "SCOPE_MEMBER",
    "SCOPE_TENANT",
    "SOURCE_ESTIMATED",
    "SOURCE_SETTLED",
    "SUBSCRIPTION_TABLE",
    "USAGE_EVENT_TABLE",
    "USAGE_MONTHLY_TABLE",
    "InMemoryUsageStore",
    "MonthlyUsageRecord",
    "PostgresUsageStore",
    "QuotaRecord",
    "RangeEventStats",
    "UsageEventRecord",
    "UsageStore",
    "current_period",
    "get_usage_store",
    "period_bounds",
    "reset_usage_store",
]
