"""计费域仓储（阶段二 B09 / B10，架构 2.5 / D-12）——公开出口。

本包是 `app/db/repositories/billing_repo.py` 的三拆产物（审计 P1-8：仓储上帝文件，
双后端镜像导致"三处同步"漏改即静默不一致；本仓储是全仓行数最多、record 最多的一处）。
五个文件的职责与依赖方向：

```
records.py    6 个数据记录 + 状态/枚举/白名单常量 + 时间口径 + default_plans() 种子
   ↑
protocol.py   BillingStore 契约（唯一，内存/PG 都必须实现；ops_repo 直接引用它）
   ↑
memory.py     InMemoryBillingStore（development/test，含测试钩子 clear/counts）
postgres.py   PostgresBillingStore（生产，6 张表名 + 行映射器 + 6 个表仓储）
   ↑
__init__.py   进程级单例（get_billing_store / get_memory_billing_store / reset_billing_store）
```

`protocol.py` **必须**是独立文件：协议若定义在任一侧，另一侧就得 import 对侧，
`memory ↔ postgres` 立刻成环。单向依赖由 `deploy/validate_repository_parity.py`
（G-38）与 `deploy/validate_import_directions.py`（G-37）双重把关。

**`get_memory_billing_store()` 是跨域只读出口**（**不创建**实例）：计量域
（`usage/memory.py` 的 `InMemoryUsageStore.quota()`）用它补齐"内存模式下的额度总量"，
否则开发/测试环境的 BL5 额度恒为 0、与 BL2 的 `quota_units_total` 不一致。

**`__all__` 与原 `billing_repo.py` 完全一致（47 项，顺序不变）**：拆分不改变公开面，
23 处调用点无需改动（含 `ops_repo.py` 的 6 个符号：`PLATFORM_TENANT` / `BillingStore` /
`OrderRecord` / `TenantRecord` / `get_billing_store` / `now_iso`）。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.db.repositories.billing.memory import InMemoryBillingStore
from app.db.repositories.billing.postgres import (
    CALLBACK_TABLE,
    INVOICE_TABLE,
    ORDER_TABLE,
    PLAN_TABLE,
    SUBSCRIPTION_TABLE,
    TENANT_TABLE,
    PostgresBillingStore,
)
from app.db.repositories.billing.protocol import BillingStore
from app.db.repositories.billing.records import (
    ACTIVE_SUBSCRIPTION_STATUSES,
    CHANNEL_WECHAT_PAY,
    FREE_PLAN_SEATS,
    INVOICE_ISSUED,
    INVOICE_PENDING,
    ORDER_CLOSED,
    ORDER_MUTABLE_FIELDS,
    ORDER_PAID,
    ORDER_PENDING,
    ORDER_TYPE_NEW,
    ORDER_TYPE_RENEW,
    ORDER_TYPE_SEATS,
    ORDER_TYPE_UPGRADE,
    PAYMENT_OFFLINE,
    PAYMENT_ONLINE,
    PLAN_AVAILABLE,
    PLAN_ENTERPRISE,
    PLAN_FREE,
    PLAN_OFFLINE,
    PLAN_PROFESSIONAL,
    PLATFORM_TENANT,
    SUBSCRIPTION_ACTIVE,
    TENANT_ACTIVE,
    TENANT_MUTABLE_FIELDS,
    TENANT_SUSPENDED,
    CallbackLogRecord,
    InvoiceRecord,
    OrderRecord,
    PlanRecord,
    SubscriptionRecord,
    TenantRecord,
    default_plans,
    now_iso,
    parse_iso,
    to_iso,
)

# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryBillingStore | None = None


def get_billing_store(settings: Settings | None = None) -> BillingStore:
    """获取计费存储。

    Args:
        settings: 可选配置实例。

    Returns:
        BillingStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresBillingStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryBillingStore()
    return _memory_store


def get_memory_billing_store() -> InMemoryBillingStore | None:
    """取内存计费存储单例（**不创建**；供计量域读取额度口径）。

    计量仓储（`usage_repo.InMemoryUsageStore`）用它补齐「内存模式下的额度总量」：
    否则开发/测试环境的 BL5 额度恒为 0，与 BL2 的 `quota_units_total` 不一致。

    Returns:
        InMemoryBillingStore | None: 已创建的内存存储；未创建返回 None。
    """
    return _memory_store


def reset_billing_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "ACTIVE_SUBSCRIPTION_STATUSES",
    "CALLBACK_TABLE",
    "CHANNEL_WECHAT_PAY",
    "FREE_PLAN_SEATS",
    "INVOICE_ISSUED",
    "INVOICE_PENDING",
    "INVOICE_TABLE",
    "ORDER_CLOSED",
    "ORDER_MUTABLE_FIELDS",
    "ORDER_PAID",
    "ORDER_PENDING",
    "ORDER_TABLE",
    "ORDER_TYPE_NEW",
    "ORDER_TYPE_RENEW",
    "ORDER_TYPE_SEATS",
    "ORDER_TYPE_UPGRADE",
    "PAYMENT_OFFLINE",
    "PAYMENT_ONLINE",
    "PLAN_AVAILABLE",
    "PLAN_ENTERPRISE",
    "PLAN_FREE",
    "PLAN_OFFLINE",
    "PLAN_PROFESSIONAL",
    "PLAN_TABLE",
    "PLATFORM_TENANT",
    "SUBSCRIPTION_ACTIVE",
    "SUBSCRIPTION_TABLE",
    "TENANT_ACTIVE",
    "TENANT_MUTABLE_FIELDS",
    "TENANT_SUSPENDED",
    "TENANT_TABLE",
    "BillingStore",
    "CallbackLogRecord",
    "InMemoryBillingStore",
    "InvoiceRecord",
    "OrderRecord",
    "PlanRecord",
    "PostgresBillingStore",
    "SubscriptionRecord",
    "TenantRecord",
    "default_plans",
    "get_billing_store",
    "get_memory_billing_store",
    "now_iso",
    "parse_iso",
    "reset_billing_store",
    "to_iso",
]
