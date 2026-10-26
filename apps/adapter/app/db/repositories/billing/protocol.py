"""计费域仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `BillingStore`，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖（拆分前它们同处一个上帝文件，看不出来；一拆就立刻变成环）。
独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`，
单向且无环，G-38 门禁据此判定「协议方法 ⊆ 两侧实现」。

**协议被 `ops_repo` 直接引用**（`OpsStore.billing` property 返回 `BillingStore`）：
那条调用点走 `billing_repo` 兼容层，本拆分包不改它的行为。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.db.repositories.billing.records import (
    CallbackLogRecord,
    InvoiceRecord,
    OrderRecord,
    PlanRecord,
    SubscriptionRecord,
    TenantRecord,
)


class BillingStore(Protocol):
    """计费存储契约（内存 / PostgreSQL 双实现）。"""

    async def list_plans(self) -> list[PlanRecord]:
        """列出全部套餐（按 `sort_order` 升序）。"""
        ...

    async def get_plan(self, plan_code: str) -> PlanRecord | None:
        """按标识读取套餐。"""
        ...

    async def ensure_tenant(self, tenant_id: str, name: str = "") -> TenantRecord:
        """读取或登记企业行（不存在时按免费版创建）。"""
        ...

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """按 ID 读取企业行。"""
        ...

    async def update_tenant(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        plan_code: str | None = None,
        suspended_reason: str | None = None,
    ) -> TenantRecord | None:
        """更新企业行（仅状态/套餐/停用原因）。"""
        ...

    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """列出企业（关键字模糊匹配名称；平台视图使用）。"""
        ...

    async def get_active_subscription(self, tenant_id: str) -> SubscriptionRecord | None:
        """读取有效订阅。"""
        ...

    async def save_subscription(self, record: SubscriptionRecord) -> SubscriptionRecord:
        """写入（或按 `subscription_id` 更新）订阅。"""
        ...

    async def get_order(self, tenant_id: str, order_id: str) -> OrderRecord | None:
        """按 ID 读取订单（租户内）。"""
        ...

    async def find_order_by_id(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 跨企业读取（**仅支付回调与运营核销可用**）。

        支付回调只有 `out_trade_no`（=订单 ID），没有租户上下文；`order_id` 是
        ULID（不可枚举），且回调**必须验签**通过才会走到这里，因此不构成探测面。
        调用方不得把它用于任何面向企业用户的接口（那是「列表过滤了但详情没校验」）。
        """
        ...

    async def find_pending_order(self, tenant_id: str) -> OrderRecord | None:
        """读取该企业的待支付订单。"""
        ...

    async def create_order(self, record: OrderRecord) -> OrderRecord:
        """写入订单；已有待支付订单时返回既有行（**不产生第二单**）。"""
        ...

    async def update_order(
        self, tenant_id: str, order_id: str, **fields: Any
    ) -> OrderRecord | None:
        """更新订单可变字段。"""
        ...

    async def list_orders(
        self, tenant_id: str, *, status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """分页列出企业订单。"""
        ...

    async def list_all_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨企业分页列出订单（**仅运营后台可用**，调用方必须先过 `require_ops_access`）。"""
        ...

    async def expire_orders(self, deadline_iso: str, tenant_id: str = "") -> list[OrderRecord]:
        """把 `expires_at` 已过期的待支付订单置为 `closed`，返回被关闭的订单。"""
        ...

    async def ensure_invoice(self, record: InvoiceRecord) -> InvoiceRecord:
        """写入账单；一单已开票时返回既有行（**不重复开票**）。"""
        ...

    async def get_invoice_by_order(self, tenant_id: str, order_id: str) -> InvoiceRecord | None:
        """按订单读取账单。"""
        ...

    async def list_invoices(self, tenant_id: str, period: str = "") -> list[InvoiceRecord]:
        """列出企业账单。"""
        ...

    async def append_callback(self, record: CallbackLogRecord) -> bool:
        """写入回调留痕；同渠道同流水号已存在时返回 False（幂等）。"""
        ...

    async def get_callback(self, channel: str, external_txn_id: str) -> CallbackLogRecord | None:
        """按渠道流水号读取回调留痕。"""
        ...

    async def mark_callback_processed(self, channel: str, external_txn_id: str) -> None:
        """把回调留痕标记为已处理。"""
        ...


__all__: list[str] = ["BillingStore"]
