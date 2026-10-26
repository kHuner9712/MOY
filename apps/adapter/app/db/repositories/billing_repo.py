"""兼容层（deprecated shim）：`app.db.repositories.billing_repo` → `app.db.repositories.billing`。

**真源已迁到包**：`app/db/repositories/billing/{records,protocol,memory,postgres,__init__}.py`
（审计 P1-8：仓储上帝文件 + 双后端镜像；切片 2B-2C 以 `role/` 为拆分布局样板）。

**本文件只做 re-export**，不得出现任何类/函数定义——包形态与 `*_repo.py` 同名并存时，
`deploy/validate_repository_parity.py`（G-38）要求本文件是**纯壳**：一旦这里重新长出
实现，"薄兼容层"就会慢慢变回第二份真源，拆分等于没做（G-38 判 exit 2）。

**为什么保留而不是直接删**：仍有 23 处调用点（`business/{plan,subscription,order,ops}_service.py`、
`db/repositories/ops_repo.py`、`db/repositories/usage/memory.py`、`tests/**`）按旧路径
import。其中 `ops_repo.py` 的 `OpsStore.billing` property 直接返回 `BillingStore`，
所以协议与本 shim 必须保持同名同对象——本切片只做拆分、不动调用点。

**弃用计划**：调用点改为 `from app.db.repositories.billing import ...` 后删本文件；
同步删除 `deploy/import_direction_baseline.txt` 里按旧路径登记的行（若有）——
路径改名后旧基线行会变 stale，G-37 会正确地判红，逼迫两处同步收口。
"""

from __future__ import annotations

from app.db.repositories.billing import (
    ACTIVE_SUBSCRIPTION_STATUSES,
    CALLBACK_TABLE,
    CHANNEL_WECHAT_PAY,
    FREE_PLAN_SEATS,
    INVOICE_ISSUED,
    INVOICE_PENDING,
    INVOICE_TABLE,
    ORDER_CLOSED,
    ORDER_MUTABLE_FIELDS,
    ORDER_PAID,
    ORDER_PENDING,
    ORDER_TABLE,
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
    PLAN_TABLE,
    PLATFORM_TENANT,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_TABLE,
    TENANT_ACTIVE,
    TENANT_MUTABLE_FIELDS,
    TENANT_SUSPENDED,
    TENANT_TABLE,
    BillingStore,
    CallbackLogRecord,
    InMemoryBillingStore,
    InvoiceRecord,
    OrderRecord,
    PlanRecord,
    PostgresBillingStore,
    SubscriptionRecord,
    TenantRecord,
    default_plans,
    get_billing_store,
    get_memory_billing_store,
    now_iso,
    parse_iso,
    reset_billing_store,
    to_iso,
)

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
