"""计费域仓储 · 内存实现（development / test）。

内存后端就是生产语义的**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的
公开方法集、签名与 record 构造点，差异只能通过 `TEST_HOOKS`（`clear` / `counts`）
这类显式白名单存在。因此本文件的每个方法都必须与 PG 侧同名方法**保持同形**。

**套餐目录来自 `default_plans()`**（内存模式没有 SQL 种子），`ensure_tenant` 按免费版
建档——这两条与 PG 的"种子行 + `ON CONFLICT DO NOTHING`"是同一口径的两种表达。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from app.core.errors import ApiError, ErrorCode
from app.db.repositories.billing.records import (
    ORDER_CLOSED,
    ORDER_MUTABLE_FIELDS,
    ORDER_PENDING,
    PLAN_FREE,
    TENANT_ACTIVE,
    CallbackLogRecord,
    InvoiceRecord,
    OrderRecord,
    PlanRecord,
    SubscriptionRecord,
    TenantRecord,
    default_plans,
    now_iso,
)


class InMemoryBillingStore:
    """进程内计费存储（development/test）。"""

    def __init__(self) -> None:
        """初始化（写入与 DDL 一致的三条套餐种子）。"""
        self._plans: dict[str, PlanRecord] = {plan.plan_code: plan for plan in default_plans()}
        self._tenants: dict[str, TenantRecord] = {}
        self._subscriptions: dict[str, SubscriptionRecord] = {}
        self._orders: dict[tuple[str, str], OrderRecord] = {}
        self._invoices: dict[tuple[str, str], InvoiceRecord] = {}
        self._invoice_by_order: dict[tuple[str, str], str] = {}
        self._callbacks: dict[tuple[str, str], CallbackLogRecord] = {}
        self._callback_txn: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 套餐
    # ------------------------------------------------------------------
    async def list_plans(self) -> list[PlanRecord]:
        """列出全部套餐。

        Returns:
            list[PlanRecord]: 按 `sort_order` 升序的套餐列表。
        """
        async with self._lock:
            plans = list(self._plans.values())
        plans.sort(key=lambda item: (item.sort_order, item.plan_code))
        return plans

    async def get_plan(self, plan_code: str) -> PlanRecord | None:
        """按标识读取套餐。

        Args:
            plan_code: 套餐标识。

        Returns:
            PlanRecord | None: 套餐；不存在返回 None。
        """
        async with self._lock:
            return self._plans.get(plan_code)

    # ------------------------------------------------------------------
    # 企业
    # ------------------------------------------------------------------
    async def ensure_tenant(self, tenant_id: str, name: str = "") -> TenantRecord:
        """读取或登记企业行。

        Args:
            tenant_id: 企业 ID。
            name: 企业名称（仅首次登记时使用）。

        Returns:
            TenantRecord: 企业行。
        """
        async with self._lock:
            current = self._tenants.get(tenant_id)
            if current is not None:
                if name and not current.name:
                    updated = replace(current, name=name, updated_at=now_iso())
                    self._tenants[tenant_id] = updated
                    return updated
                return current
            stamp = now_iso()
            # 8 个字段**全部显式写出**：与 `_to_tenant`（PG 侧行映射，同样写齐 8 个）保持
            # 同一"构造点形状"（G-38 R4 按形状集合逐一对齐，缺字段即判红）。
            # 语义与 PG 等价：DDL 的 `deploy_mode` 默认 'saas'、`suspended_reason` 可空
            # （`_to_tenant` 对 NULL 归一为 ""），此处写出的就是这两个默认值。
            created = TenantRecord(
                tenant_id=tenant_id,
                name=name or tenant_id,
                status=TENANT_ACTIVE,
                deploy_mode="saas",
                plan_code=PLAN_FREE,
                suspended_reason="",
                created_at=stamp,
                updated_at=stamp,
            )
            self._tenants[tenant_id] = created
            return created

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """按 ID 读取企业行。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantRecord | None: 企业行；不存在返回 None。
        """
        async with self._lock:
            return self._tenants.get(tenant_id)

    async def update_tenant(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        plan_code: str | None = None,
        suspended_reason: str | None = None,
    ) -> TenantRecord | None:
        """更新企业行。

        Args:
            tenant_id: 企业 ID。
            status: 新状态。
            plan_code: 新套餐。
            suspended_reason: 停用原因。

        Returns:
            TenantRecord | None: 更新后的企业行；企业不存在返回 None。
        """
        async with self._lock:
            current = self._tenants.get(tenant_id)
            if current is None:
                return None
            updated = replace(
                current,
                status=status if status is not None else current.status,
                plan_code=plan_code if plan_code is not None else current.plan_code,
                suspended_reason=(
                    suspended_reason if suspended_reason is not None else current.suspended_reason
                ),
                updated_at=now_iso(),
            )
            self._tenants[tenant_id] = updated
            return updated

    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """列出企业。

        Args:
            keyword: 名称关键字（大小写不敏感）。
            status: 状态过滤。

        Returns:
            list[TenantRecord]: 企业列表（创建时间倒序）。
        """
        async with self._lock:
            items = list(self._tenants.values())
        needle = keyword.strip().lower()
        if needle:
            items = [item for item in items if needle in item.name.lower()]
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.created_at, item.tenant_id), reverse=True)
        return items

    async def page_tenants(
        self, *, keyword: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[TenantRecord], int]:
        """分页列出企业（内存后端：已在本地，切片即可）。

        Args:
            keyword: 名称关键字（大小写不敏感）。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[TenantRecord], int]: `(当前页, 筛选后总数)`。
        """
        items = await self.list_tenants(keyword=keyword, status=status)
        total = len(items)
        start = max(int(offset), 0)
        return items[start : start + max(int(limit), 0)], total

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------
    async def get_active_subscription(self, tenant_id: str) -> SubscriptionRecord | None:
        """读取有效订阅。

        Args:
            tenant_id: 企业 ID。

        Returns:
            SubscriptionRecord | None: 有效订阅；无则返回 None。
        """
        async with self._lock:
            current = self._subscriptions.get(tenant_id)
        if current is None or not current.is_active:
            return None
        return current

    async def save_subscription(self, record: SubscriptionRecord) -> SubscriptionRecord:
        """写入（或按 `subscription_id` 更新）订阅。

        Args:
            record: 订阅行。

        Returns:
            SubscriptionRecord: 落库后的订阅行。
        """
        stamp = now_iso()
        async with self._lock:
            current = self._subscriptions.get(record.tenant_id)
            payload = replace(
                record,
                created_at=record.created_at or (current.created_at if current else stamp),
                updated_at=stamp,
            )
            self._subscriptions[record.tenant_id] = payload
        return payload

    # ------------------------------------------------------------------
    # 订单
    # ------------------------------------------------------------------
    async def get_order(self, tenant_id: str, order_id: str) -> OrderRecord | None:
        """按 ID 读取订单。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。

        Returns:
            OrderRecord | None: 订单；不存在返回 None。
        """
        async with self._lock:
            return self._orders.get((tenant_id, order_id))

    async def find_order_by_id(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 跨企业读取订单。

        Args:
            order_id: 订单 ID。

        Returns:
            OrderRecord | None: 订单；不存在返回 None。
        """
        async with self._lock:
            for (_, current), item in self._orders.items():
                if current == order_id:
                    return item
        return None

    async def find_pending_order(self, tenant_id: str) -> OrderRecord | None:
        """读取该企业的待支付订单。

        Args:
            tenant_id: 企业 ID。

        Returns:
            OrderRecord | None: 待支付订单；无则返回 None。
        """
        async with self._lock:
            pending = [
                item
                for (owner, _), item in self._orders.items()
                if owner == tenant_id and item.is_pending
            ]
        pending.sort(key=lambda item: item.created_at)
        return pending[0] if pending else None

    async def create_order(self, record: OrderRecord) -> OrderRecord:
        """写入订单（同企业仅允许一个待支付订单）。

        Args:
            record: 订单行。

        Returns:
            OrderRecord: 落库后的订单；**已有待支付订单时返回既有行**。
        """
        stamp = now_iso()
        async with self._lock:
            existing = [
                item
                for (owner, _), item in self._orders.items()
                if owner == record.tenant_id and item.is_pending
            ]
            if existing:
                existing.sort(key=lambda item: item.created_at)
                return existing[0]
            payload = replace(
                record,
                created_at=record.created_at or stamp,
                updated_at=stamp,
            )
            self._orders[(record.tenant_id, record.order_id)] = payload
        return payload

    async def update_order(
        self, tenant_id: str, order_id: str, **fields: Any
    ) -> OrderRecord | None:
        """更新订单可变字段。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。
            **fields: 可变字段（白名单校验）。

        Returns:
            OrderRecord | None: 更新后的订单；不存在返回 None。

        Raises:
            ApiError: 出现白名单外的字段（20001）。
        """
        unknown = set(fields) - ORDER_MUTABLE_FIELDS
        if unknown:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"order fields not mutable: {sorted(unknown)}",
            )
        async with self._lock:
            current = self._orders.get((tenant_id, order_id))
            if current is None:
                return None
            updated = replace(current, **fields, updated_at=now_iso())
            self._orders[(tenant_id, order_id)] = updated
            return updated

    async def list_orders(
        self, tenant_id: str, *, status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """分页列出企业订单。

        Args:
            tenant_id: 企业 ID。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[OrderRecord], int]: 当前页订单与总条数。
        """
        async with self._lock:
            items = [item for (owner, _), item in self._orders.items() if owner == tenant_id]
        return _slice_orders(items, status=status, offset=offset, limit=limit)

    async def list_all_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨企业分页列出订单。

        Args:
            tenant_id: 企业过滤（空表示全部）。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[OrderRecord], int]: 当前页订单与总条数。
        """
        async with self._lock:
            items = [
                item
                for (owner, _), item in self._orders.items()
                if not tenant_id or owner == tenant_id
            ]
        return _slice_orders(items, status=status, offset=offset, limit=limit)

    async def expire_orders(self, deadline_iso: str, tenant_id: str = "") -> list[OrderRecord]:
        """把已过期的待支付订单置为 `closed`。

        Args:
            deadline_iso: 判定阈值（`expires_at < deadline_iso` 即过期）。
            tenant_id: 企业过滤（空表示全部，仅运营侧补偿任务使用）。

        Returns:
            list[OrderRecord]: 被关闭的订单。
        """
        closed: list[OrderRecord] = []
        async with self._lock:
            for key, item in list(self._orders.items()):
                if item.status != ORDER_PENDING:
                    continue
                if tenant_id and key[0] != tenant_id:
                    continue
                if not item.expires_at or item.expires_at >= deadline_iso:
                    continue
                updated = replace(item, status=ORDER_CLOSED, updated_at=now_iso())
                self._orders[key] = updated
                closed.append(updated)
        return closed

    # ------------------------------------------------------------------
    # 账单
    # ------------------------------------------------------------------
    async def ensure_invoice(self, record: InvoiceRecord) -> InvoiceRecord:
        """写入账单（一单一票）。

        Args:
            record: 账单行。

        Returns:
            InvoiceRecord: 落库后的账单；该订单已开票时返回既有行。
        """
        stamp = now_iso()
        async with self._lock:
            existed_id = self._invoice_by_order.get((record.tenant_id, record.order_id))
            if existed_id is not None:
                return self._invoices[(record.tenant_id, existed_id)]
            payload = replace(record, created_at=record.created_at or stamp)
            self._invoices[(record.tenant_id, record.invoice_id)] = payload
            self._invoice_by_order[(record.tenant_id, record.order_id)] = record.invoice_id
            return payload

    async def get_invoice_by_order(self, tenant_id: str, order_id: str) -> InvoiceRecord | None:
        """按订单读取账单。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。

        Returns:
            InvoiceRecord | None: 账单；未开票返回 None。
        """
        async with self._lock:
            invoice_id = self._invoice_by_order.get((tenant_id, order_id))
            if invoice_id is None:
                return None
            return self._invoices.get((tenant_id, invoice_id))

    async def list_invoices(self, tenant_id: str, period: str = "") -> list[InvoiceRecord]:
        """列出企业账单。

        Args:
            tenant_id: 企业 ID。
            period: 账期过滤（空表示全部）。

        Returns:
            list[InvoiceRecord]: 账单列表（创建时间倒序）。
        """
        async with self._lock:
            items = [item for (owner, _), item in self._invoices.items() if owner == tenant_id]
        if period:
            items = [item for item in items if item.period == period]
        items.sort(key=lambda item: (item.created_at, item.invoice_id), reverse=True)
        return items

    # ------------------------------------------------------------------
    # 回调留痕
    # ------------------------------------------------------------------
    async def append_callback(self, record: CallbackLogRecord) -> bool:
        """写入回调留痕（同渠道同流水号只允许一条）。

        Args:
            record: 回调留痕。

        Returns:
            bool: 首次写入返回 True；重复流水号返回 False。
        """
        key = (record.channel, record.external_txn_id)
        async with self._lock:
            if key in self._callback_txn:
                return False
            payload = replace(record, received_at=record.received_at or now_iso())
            self._callbacks[(record.tenant_id, record.callback_id)] = payload
            self._callback_txn[key] = record.callback_id
            return True

    async def get_callback(self, channel: str, external_txn_id: str) -> CallbackLogRecord | None:
        """按渠道流水号读取回调留痕。

        Args:
            channel: 渠道。
            external_txn_id: 渠道流水号。

        Returns:
            CallbackLogRecord | None: 留痕；不存在返回 None。
        """
        key = (channel, external_txn_id)
        async with self._lock:
            callback_id = self._callback_txn.get(key)
            if callback_id is None:
                return None
            for (_, current), item in self._callbacks.items():
                if current == callback_id:
                    return item
        return None

    async def mark_callback_processed(self, channel: str, external_txn_id: str) -> None:
        """把回调留痕标记为已处理（内存后端内部使用）。

        Args:
            channel: 渠道。
            external_txn_id: 渠道流水号。
        """
        key = (channel, external_txn_id)
        async with self._lock:
            callback_id = self._callback_txn.get(key)
            if callback_id is None:
                return
            for tenant_key, item in list(self._callbacks.items()):
                if item.callback_id != callback_id:
                    continue
                self._callbacks[tenant_key] = replace(item, processed=True)

    def counts(self) -> dict[str, int]:
        """返回各表行数（测试断言用）。

        Returns:
            dict[str, int]: `orders` / `invoices` / `callbacks` / `subscriptions` 行数。
        """
        return {
            "orders": len(self._orders),
            "invoices": len(self._invoices),
            "callbacks": len(self._callbacks),
            "subscriptions": len(self._subscriptions),
        }

    def clear(self) -> None:
        """清空业务数据（测试隔离用；保留套餐种子）。"""
        self._tenants.clear()
        self._subscriptions.clear()
        self._orders.clear()
        self._invoices.clear()
        self._invoice_by_order.clear()
        self._callbacks.clear()
        self._callback_txn.clear()


def _slice_orders(
    items: list[OrderRecord], *, status: str, offset: int, limit: int
) -> tuple[list[OrderRecord], int]:
    """按状态过滤并分页（内存后端共用）。

    Args:
        items: 订单全集。
        status: 状态过滤。
        offset: 偏移量。
        limit: 每页条数。

    Returns:
        tuple[list[OrderRecord], int]: 当前页订单与总条数。
    """
    filtered = [item for item in items if not status or item.status == status]
    filtered.sort(key=lambda item: (item.created_at, item.order_id), reverse=True)
    start = max(offset, 0)
    return filtered[start : start + limit], len(filtered)


__all__: list[str] = ["InMemoryBillingStore"]
