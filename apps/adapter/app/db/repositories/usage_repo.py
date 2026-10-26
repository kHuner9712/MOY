"""兼容层（deprecated shim）：`app.db.repositories.usage_repo` → `app.db.repositories.usage`。

**真源已迁到包**：`app/db/repositories/usage/{records,protocol,memory,postgres,__init__}.py`
（审计 P1-8：仓储上帝文件 + 双后端镜像；切片 2B-2C 以 `role/` 为拆分布局样板）。

**本文件只做 re-export**，不得出现任何类/函数定义——包形态与 `*_repo.py` 同名并存时，
`deploy/validate_repository_parity.py`（G-38）要求本文件是**纯壳**：一旦这里重新长出
实现，"薄兼容层"就会慢慢变回第二份真源，拆分等于没做（G-38 判 exit 2）。

**为什么保留而不是直接删**：仍有 16 处调用点（`business/usage_service.py`、
`scripts/reconcile_usage.py`、`tests/**`）按旧路径 import。本切片只做拆分、
不动调用点，把改动面压到"移动 + re-export"，任何一跳都能单独回滚。

**弃用计划**：调用点改为 `from app.db.repositories.usage import ...` 后删本文件；
同步删除 `deploy/import_direction_baseline.txt` 里按旧路径登记的行（若有）——
路径改名后旧基线行会变 stale，G-37 会正确地判红，逼迫两处同步收口。
"""

from __future__ import annotations

from app.db.repositories.usage import (
    CAPABILITIES,
    CAPABILITY_AUTOMATION,
    CAPABILITY_CHAT,
    CAPABILITY_DOC_PROCESS,
    CAPABILITY_DOC_QA,
    DEFAULT_QUOTA_UNIT_LABEL,
    PLAN_TABLE,
    PLATFORM_TENANT,
    SCOPE_DEPARTMENT,
    SCOPE_MEMBER,
    SCOPE_TENANT,
    SOURCE_ESTIMATED,
    SOURCE_SETTLED,
    SUBSCRIPTION_TABLE,
    USAGE_EVENT_TABLE,
    USAGE_MONTHLY_TABLE,
    InMemoryUsageStore,
    MonthlyUsageRecord,
    PostgresUsageStore,
    QuotaRecord,
    RangeEventStats,
    UsageEventRecord,
    UsageStore,
    current_period,
    get_usage_store,
    period_bounds,
    reset_usage_store,
)

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
