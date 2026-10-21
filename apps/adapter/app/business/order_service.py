"""订单与支付服务（阶段二 B09，架构文档 §4.5 BL6-BL10 / §5 计费时序图）。

覆盖六个端点 + 两条兜底机制：

| # | 端点 / 机制 | 关键约束 |
| --- | --- | --- |
| BL6 | `POST /billing/orders` | 已有待支付订单 → `15004` 且**不产生第二单** |
| BL7 | `GET /billing/orders` | 分页；读前先做**懒过期** |
| BL8 | `GET /billing/orders/{id}` | 对象级归属：非本企业订单一律 `20004` |
| BL9 | `GET /billing/invoices` | 只读，按账期过滤 |
| BL10 | `POST /billing/payments/callback` | 验签 + **幂等**（同 payload 连发 3 次只生效一次） |
| 懒过期 | 每次读写前 `expire_orders()` | 30 分钟未支付 → `closed` |
| 补偿 | `close_expired_orders()` | 定时任务调用的跨租户补偿入口 |

**幂等的三层**（缺一不可）：

1. `payment_callback_log` 的 `uq_callback_external` 唯一键：同渠道同流水号只处理一次；
2. 订单状态机：`pending_payment → paid` 单向，已支付订单不再重复生效；
3. `billing_invoice` 的 `uq_invoice_order`：一单一票，重复核销不会重复开票。

**金额一律以分为单位**，展示层除以 100；`15009` 是核销金额校验的唯一出口。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.business.audit_service import (
    ACTION_ORDER_CREATED,
    ACTION_ORDER_PAID,
    RESOURCE_ORDER,
    AuditService,
)
from app.business.payment_gateway import (
    GATEWAY_WECHAT_PAY,
    PaymentGateway,
    get_payment_gateway,
    payload_digest,
)
from app.business.plan_service import PlanService
from app.business.subscription_service import SubscriptionService
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.db.repositories.billing_repo import (
    INVOICE_ISSUED,
    ORDER_CLOSED,
    ORDER_PAID,
    ORDER_PENDING,
    PAYMENT_OFFLINE,
    PLAN_FREE,
    BillingStore,
    CallbackLogRecord,
    InvoiceRecord,
    OrderRecord,
    get_billing_store,
    now_iso,
)
from app.schemas.billing import (
    CreateOrderData,
    CreateOrderRequest,
    InvoiceItem,
    InvoiceListData,
    OrderDetailData,
    OrderItem,
    OrderType,
    PaymentCallbackData,
    PaymentMethod,
)
from app.schemas.common import PageResult, build_page

logger = get_logger(__name__)

#: 订单 ID 前缀
ORDER_ID_PREFIX: Final[str] = "ord_"

#: 账单 ID 前缀
INVOICE_ID_PREFIX: Final[str] = "inv_"

#: 回调留痕 ID 前缀
CALLBACK_ID_PREFIX: Final[str] = "cbk_"

#: 一笔订单的支付渠道（本阶段仅微信支付；测试走 FakeGateway）
DEFAULT_CHANNEL: Final[str] = GATEWAY_WECHAT_PAY

#: 系统操作人（渠道回调无用户身份）
SYSTEM_ACTOR: Final[str] = "system"

#: 支付通知验签未通过的文案（`20001` 同码多文案之一）
MESSAGE_SIGNATURE_INVALID: Final[str] = "支付通知校验未通过，请稍后重试"

#: 订单不存在（渠道侧）的文案（`20004` 同码多文案之一）
MESSAGE_ORDER_UNKNOWN: Final[str] = "支付通知对应的订单不存在，请联系客服核对"

#: 回调应答码（渠道方语义）
CALLBACK_SUCCESS: Final[str] = "SUCCESS"

#: 回调应答说明
CALLBACK_MESSAGE: Final[str] = "支付结果已处理"

#: 脱敏流水号保留的尾部字符数
MASK_KEEP_TAIL: Final[int] = 4


def mask_payment_no(payment_no: str) -> str:
    """脱敏支付流水号（只保留尾部 4 位）。

    Args:
        payment_no: 渠道流水号。

    Returns:
        str: 形如 `****a1b2` 的脱敏串；空值返回空串。
    """
    value = (payment_no or "").strip()
    if not value:
        return ""
    tail = value[-MASK_KEEP_TAIL:] if len(value) > MASK_KEEP_TAIL else value
    return f"****{tail}"


def _parse_bank_info(raw: str) -> dict[str, Any] | None:
    """解析对公转账收款信息（配置项为 JSON 字符串）。

    Args:
        raw: JSON 字符串。

    Returns:
        dict[str, Any] | None: 收款信息；空串或非法 JSON 返回 None。
    """
    import json

    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        logger.warning("pay_bank_info_invalid_json")
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): value for key, value in parsed.items()}


class OrderService:
    """订单与支付服务（BL6-BL10 的唯一业务入口）。

    Attributes:
        _settings: 配置实例。
        _store: 计费存储。
        _plans: 套餐服务。
        _audit: 操作记录服务。
        _gateway: 支付网关。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: BillingStore | None = None,
        plans: PlanService | None = None,
        audit: AuditService | None = None,
        gateway: PaymentGateway | None = None,
        subscriptions: SubscriptionService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选计费存储（测试注入）。
            plans: 可选套餐服务（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            gateway: 可选支付网关（测试注入）。
            subscriptions: 可选订阅服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: BillingStore = store or get_billing_store(self._settings)
        # 套餐与订阅服务必须与**同一个**存储绑定，否则注入自定义存储时
        # 会读到全局单例的数据（判定与写入分家）。
        self._plans: PlanService = plans or PlanService(self._settings, self._store)
        self._audit = audit or AuditService(self._settings)
        self._gateway: PaymentGateway = gateway or get_payment_gateway(self._settings)
        self._subscriptions: SubscriptionService = subscriptions or SubscriptionService(
            settings=self._settings, store=self._store, plans=self._plans
        )

    @property
    def store(self) -> BillingStore:
        """当前计费存储（测试可据此断言）。"""
        return self._store

    @property
    def gateway(self) -> PaymentGateway:
        """当前支付网关。"""
        return self._gateway

    # ------------------------------------------------------------------
    # BL6 下单
    # ------------------------------------------------------------------
    async def create(
        self, *, tenant_id: str, actor_id: str, request: CreateOrderRequest
    ) -> CreateOrderData:
        """创建订单（BL6）。

        Args:
            tenant_id: 租户 ID。
            actor_id: 下单人用户 ID。
            request: 下单请求。

        Returns:
            CreateOrderData: 订单信息（含支付地址或对公转账信息）。

        Raises:
            ApiError: 套餐不可购（15002）、已有待支付订单（15004）、
                能力未开通（15001）、渠道未配置（30004）。
        """
        order = await self.create_internal(
            tenant_id=tenant_id,
            actor_id=actor_id,
            plan_code=request.plan_code,
            seats=request.seats,
            order_type=request.order_type,
            payment_method=request.payment_method,
        )
        logger.info(
            "order_created",
            tenant_id=tenant_id,
            order_id=order.order_id,
            amount_cents=order.amount_cents,
            order_type=order.order_type,
        )
        return CreateOrderData(
            order_id=order.order_id,
            amount_cents=order.amount_cents,
            currency=order.currency,
            status=order.status,
            payment_method=order.payment_method,
            pay_url=order.pay_url,
            bank_info=order.bank_info,
            expires_at=order.expires_at,
        )

    async def create_internal(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        plan_code: str,
        seats: int,
        order_type: OrderType | str,
        payment_method: PaymentMethod | str,
    ) -> OrderRecord:
        """创建订单（业务内部入口，BL3/BL4 复用）。

        Args:
            tenant_id: 租户 ID。
            actor_id: 下单人用户 ID。
            plan_code: 套餐标识。
            seats: 席位数。
            order_type: 订单类型。
            payment_method: 支付方式。

        Returns:
            OrderRecord: 订单记录。

        Raises:
            ApiError: 套餐不可购（15002）、已有待支付订单（15004）、
                渠道未配置（30004）。
        """
        await self._expire_pending(tenant_id)
        plan = await self._plans.require_purchasable(plan_code or PLAN_FREE)
        pending = await self._store.find_pending_order(tenant_id)
        if pending is not None:
            logger.warning(
                "order_pending_conflict",
                tenant_id=tenant_id,
                order_id=pending.order_id,
            )
            raise ApiError(
                ErrorCode.PENDING_ORDER_EXISTS,
                internal_detail=f"pending_order={pending.order_id}",
            )
        seats_total = max(int(seats), 1)
        amount_cents = plan.amount_cents(seats_total)
        order_id = new_resource_id(ORDER_ID_PREFIX)
        pay_url = ""
        bank_info: dict[str, Any] | None = None
        if payment_method == PAYMENT_OFFLINE:
            bank_info = _parse_bank_info(self._settings.pay_bank_info) or {
                "account_name": "请填写收款户名",
                "bank_name": "请填写开户行",
                "account_no": "请填写收款账号",
            }
        else:
            # 在线支付：订单号要带进收银台地址（回调按 out_trade_no 找回订单）
            initiation = await self._gateway.create_payment(
                order_id=order_id, amount_cents=amount_cents, subject=plan.display_name
            )
            pay_url = initiation.pay_url
        moment = datetime.now(UTC)
        record = OrderRecord(
            tenant_id=tenant_id,
            order_id=order_id,
            order_type=str(order_type),
            plan_code=plan.plan_code,
            seats=seats_total,
            amount_cents=amount_cents,
            currency=plan.currency,
            status=ORDER_PENDING,
            payment_method=str(payment_method),
            pay_url=pay_url,
            bank_info=bank_info,
            expires_at=(moment + timedelta(minutes=int(self._settings.order_expire_minutes)))
            .isoformat()
            .replace("+00:00", "Z"),
            created_by=actor_id,
        )
        stored = await self._store.create_order(record)
        if stored.order_id != record.order_id:
            # 并发下单：数据库部分唯一索引已拦下，这里统一转成 15004
            logger.warning(
                "order_created_conflict",
                tenant_id=tenant_id,
                attempted=record.order_id,
                existing=stored.order_id,
            )
            raise ApiError(
                ErrorCode.PENDING_ORDER_EXISTS,
                internal_detail=f"pending_order={stored.order_id}",
            )
        await self._store.ensure_tenant(tenant_id)
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=ACTION_ORDER_CREATED,
            resource_type=RESOURCE_ORDER,
            resource_id=stored.order_id,
            change_summary={
                "plan_code": stored.plan_code,
                "seats": stored.seats,
                "amount_cents": stored.amount_cents,
                "order_type": stored.order_type,
                "payment_method": stored.payment_method,
            },
        )
        return stored

    # ------------------------------------------------------------------
    # BL7 订单列表
    # ------------------------------------------------------------------
    async def list_orders(
        self, *, tenant_id: str, status: str = "", page: int = 1, page_size: int = 20
    ) -> PageResult[OrderItem]:
        """分页查询订单（BL7）。

        Args:
            tenant_id: 租户 ID。
            status: 状态过滤。
            page: 页码（从 1 开始）。
            page_size: 每页条数。

        Returns:
            PageResult[OrderItem]: 分页订单列表。
        """
        await self._expire_pending(tenant_id)
        offset = max(page - 1, 0) * page_size
        records, total = await self._store.list_orders(
            tenant_id, status=status, offset=offset, limit=page_size
        )
        items = [
            OrderItem(
                order_id=record.order_id,
                order_type=record.order_type,
                plan_code=record.plan_code,
                seats=record.seats,
                amount_cents=record.amount_cents,
                status=record.status,
                payment_method=record.payment_method,
                created_at=record.created_at,
            )
            for record in records
        ]
        return build_page(items, total=total, page=page, page_size=page_size)

    # ------------------------------------------------------------------
    # BL8 订单详情
    # ------------------------------------------------------------------
    async def detail(self, *, tenant_id: str, order_id: str) -> OrderDetailData:
        """查询订单详情（BL8）。

        Args:
            tenant_id: 租户 ID。
            order_id: 订单 ID。

        Returns:
            OrderDetailData: 订单详情。

        Raises:
            ApiError: 订单不存在或不属于本企业（20004）。
        """
        await self._expire_pending(tenant_id)
        record = await self._store.get_order(tenant_id, order_id)
        if record is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message="订单不存在或已被删除",
                internal_detail=f"order_id={order_id} tenant_id={tenant_id}",
            )
        invoice = await self._store.get_invoice_by_order(tenant_id, order_id)
        return OrderDetailData(
            order_id=record.order_id,
            status=record.status,
            amount_cents=record.amount_cents,
            payment_no_masked=record.payment_no_masked,
            paid_at=record.paid_at,
            invoice_status=invoice.status if invoice is not None else "",
            plan_code=record.plan_code,
            seats=record.seats,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

    # ------------------------------------------------------------------
    # BL9 账单列表
    # ------------------------------------------------------------------
    async def list_invoices(self, *, tenant_id: str, period: str = "") -> InvoiceListData:
        """查询账单列表（BL9）。

        Args:
            tenant_id: 租户 ID。
            period: 账期 `YYYY-MM`（空表示全部）。

        Returns:
            InvoiceListData: 账单列表。
        """
        records = await self._store.list_invoices(tenant_id, period)
        return InvoiceListData(
            items=[
                InvoiceItem(
                    invoice_id=record.invoice_id,
                    period=record.period,
                    amount_cents=record.amount_cents,
                    status=record.status,
                    download_url=record.download_path,
                )
                for record in records
            ]
        )

    # ------------------------------------------------------------------
    # BL10 支付回调（免鉴权 + 验签 + 幂等）
    # ------------------------------------------------------------------
    async def handle_callback(self, payload: dict[str, Any]) -> PaymentCallbackData:
        """处理渠道支付回调（BL10）。

        幂等语义：同一 `(channel, transaction_id)` 第二次及以后直接返回成功语义
        （渠道要求 200），**但不重复开通、不重复开票**。

        Args:
            payload: 渠道回调载荷。

        Returns:
            PaymentCallbackData: 对渠道方的应答。

        Raises:
            ApiError: 验签失败（20001）、订单不存在（20004）、
                订单已关闭（15008）、金额不一致（15009）、渠道未配置（30004）。
        """
        notification = self._gateway.verify_and_parse(payload)
        digest = payload_digest(payload)
        if not notification.signature_ok or not notification.external_txn_id:
            await self._store.append_callback(
                CallbackLogRecord(
                    tenant_id="__platform__",
                    callback_id=new_resource_id(CALLBACK_ID_PREFIX),
                    channel=self._gateway.channel or DEFAULT_CHANNEL,
                    external_txn_id=notification.external_txn_id or digest[:32],
                    order_id=notification.order_id,
                    amount_cents=notification.amount_cents,
                    signature_ok=False,
                    payload_sha256=digest,
                    processed=False,
                )
            )
            logger.warning(
                "payment_callback_signature_invalid",
                order_id=notification.order_id,
                amount_cents=notification.amount_cents,
            )
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message=MESSAGE_SIGNATURE_INVALID,
                internal_detail=(
                    f"signature verification failed order={notification.order_id} "
                    f"txn={notification.external_txn_id}"
                ),
            )

        order = await self._store.find_order_by_id(notification.order_id)
        if order is None:
            logger.warning("payment_callback_order_missing", order_id=notification.order_id)
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message=MESSAGE_ORDER_UNKNOWN,
                internal_detail=f"order_id={notification.order_id}",
            )

        appended = await self._store.append_callback(
            CallbackLogRecord(
                tenant_id=order.tenant_id,
                callback_id=new_resource_id(CALLBACK_ID_PREFIX),
                channel=self._gateway.channel or DEFAULT_CHANNEL,
                external_txn_id=notification.external_txn_id,
                order_id=order.order_id,
                amount_cents=notification.amount_cents,
                signature_ok=True,
                payload_sha256=digest,
                processed=False,
            )
        )
        if not appended:
            # 重复回调：渠道要求 200，但**不重复开通**（幂等核心）
            logger.info(
                "payment_callback_duplicated",
                order_id=order.order_id,
                txn=notification.external_txn_id,
            )
            return PaymentCallbackData(code=CALLBACK_SUCCESS, message=CALLBACK_MESSAGE)

        if order.is_paid:
            await self._store.mark_callback_processed(
                self._gateway.channel or DEFAULT_CHANNEL, notification.external_txn_id
            )
            return PaymentCallbackData(code=CALLBACK_SUCCESS, message=CALLBACK_MESSAGE)
        if order.status != ORDER_PENDING:
            logger.warning(
                "payment_callback_order_not_pending",
                order_id=order.order_id,
                status=order.status,
            )
            raise ApiError(
                ErrorCode.ORDER_ALREADY_PROCESSED,
                internal_detail=f"order_id={order.order_id} status={order.status}",
            )
        if not notification.is_success:
            raise ApiError(
                ErrorCode.PAYMENT_INCOMPLETE,
                internal_detail=f"trade_state={notification.trade_state}",
            )
        if notification.amount_cents != order.amount_cents:
            logger.warning(
                "payment_callback_amount_mismatch",
                order_id=order.order_id,
                expected=order.amount_cents,
                actual=notification.amount_cents,
            )
            raise ApiError(
                ErrorCode.ORDER_AMOUNT_MISMATCH,
                internal_detail=(
                    f"order_id={order.order_id} expected={order.amount_cents} "
                    f"actual={notification.amount_cents}"
                ),
            )

        await self._apply_payment(
            order=order,
            paid_amount_cents=notification.amount_cents,
            actor_id=SYSTEM_ACTOR,
            remark="在线支付回调",
            payment_no=notification.external_txn_id,
        )
        await self._store.mark_callback_processed(
            self._gateway.channel or DEFAULT_CHANNEL, notification.external_txn_id
        )
        return PaymentCallbackData(code=CALLBACK_SUCCESS, message=CALLBACK_MESSAGE)

    # ------------------------------------------------------------------
    # 支付生效（回调 / 运营核销共用）
    # ------------------------------------------------------------------
    async def confirm_paid(
        self,
        *,
        tenant_id: str,
        order_id: str,
        paid_amount_cents: int,
        actor_id: str,
        remark: str = "",
    ) -> OrderRecord:
        """运营核销订单（OPS5 的业务实现）。

        Args:
            tenant_id: 企业 ID。
            order_id: 订单 ID。
            paid_amount_cents: 到账金额（分）。
            actor_id: 运营人用户 ID。
            remark: 核销备注。

        Returns:
            OrderRecord: 已支付订单。

        Raises:
            ApiError: 订单不存在（20004）、已处理（15008）、金额不符（15009）。
        """
        order = await self._store.get_order(tenant_id, order_id)
        if order is None:
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message="订单不存在或已被删除",
                internal_detail=f"order_id={order_id} tenant_id={tenant_id}",
            )
        if order.status == ORDER_PAID:
            raise ApiError(
                ErrorCode.ORDER_ALREADY_PROCESSED,
                internal_detail=f"order_id={order_id} already paid",
            )
        if order.status != ORDER_PENDING:
            raise ApiError(
                ErrorCode.ORDER_ALREADY_PROCESSED,
                internal_detail=f"order_id={order_id} status={order.status}",
            )
        if int(paid_amount_cents) != order.amount_cents:
            raise ApiError(
                ErrorCode.ORDER_AMOUNT_MISMATCH,
                internal_detail=(
                    f"order_id={order_id} expected={order.amount_cents} actual={paid_amount_cents}"
                ),
            )
        return await self._apply_payment(
            order=order,
            paid_amount_cents=int(paid_amount_cents),
            actor_id=actor_id,
            remark=remark or "运营核销",
            payment_no="",
        )

    async def _apply_payment(
        self,
        *,
        order: OrderRecord,
        paid_amount_cents: int,
        actor_id: str,
        remark: str,
        payment_no: str,
    ) -> OrderRecord:
        """把订单置为已支付并联动订阅与开票（幂等由调用方保证）。

        **四步不在同一事务内**（阶段验收裁定接受）：订单置 `paid`、订阅生效、
        开具账单、写审计各自是短事务；不一致风险由三道幂等收敛——
        `uq_callback_external`（不重复处理回调）、`uq_invoice_order`（不重复开票）、
        订单状态机单向（`paid` 不再二次生效）。**禁止**在此基础上改成「先开通再校验」，
        那会把偶发不一致变成确定性重复开通。

        Args:
            order: 待支付订单。
            paid_amount_cents: 实付金额（分）。
            actor_id: 触发人。
            remark: 备注。
            payment_no: 渠道流水号（用于脱敏展示）。

        Returns:
            OrderRecord: 已支付订单。
        """
        updated = await self._store.update_order(
            order.tenant_id,
            order.order_id,
            status=ORDER_PAID,
            paid_amount_cents=paid_amount_cents,
            paid_at=now_iso(),
            payment_no_masked=mask_payment_no(payment_no),
            confirmed_by=actor_id,
            confirm_remark=remark,
        )
        if updated is None:  # pragma: no cover - 刚读到的订单不可能消失
            raise ApiError(
                ErrorCode.RESOURCE_NOT_FOUND, internal_detail=f"order_id={order.order_id}"
            )
        subscription = await self._subscriptions.apply_paid_order(
            order=updated, actor_id=actor_id, remark=remark
        )
        invoice = await self._store.ensure_invoice(
            InvoiceRecord(
                tenant_id=order.tenant_id,
                invoice_id=new_resource_id(INVOICE_ID_PREFIX),
                order_id=order.order_id,
                period=now_iso()[:7],
                amount_cents=paid_amount_cents,
                currency=order.currency,
                status=INVOICE_ISSUED,
                issued_at=now_iso(),
            )
        )
        await self._audit.record(
            tenant_id=order.tenant_id,
            actor_id=actor_id,
            action=ACTION_ORDER_PAID,
            resource_type=RESOURCE_ORDER,
            resource_id=order.order_id,
            change_summary={
                "before": {"status": order.status},
                "after": {
                    "status": updated.status,
                    "paid_amount_cents": paid_amount_cents,
                    "invoice_id": invoice.invoice_id,
                    "subscription_id": subscription.subscription_id,
                    "remark": remark,
                },
            },
        )
        logger.info(
            "order_paid",
            tenant_id=order.tenant_id,
            order_id=order.order_id,
            paid_amount_cents=paid_amount_cents,
            invoice_id=invoice.invoice_id,
        )
        return updated

    # ------------------------------------------------------------------
    # 过期关闭（懒过期 + 定时补偿）
    # ------------------------------------------------------------------
    async def _expire_pending(self, tenant_id: str, moment: datetime | None = None) -> int:
        """把该企业已超时的待支付订单置为 `closed`（懒过期）。

        Args:
            tenant_id: 企业 ID。
            moment: 判定时刻（测试可注入）。

        Returns:
            int: 被关闭的订单数。
        """
        deadline = (moment or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
        try:
            closed = await self._store.expire_orders(deadline, tenant_id)
        except ApiError:  # pragma: no cover - 内存模式无事务，不会走到这里
            logger.warning("order_expire_failed", tenant_id=tenant_id)
            return 0
        if closed:
            logger.info("orders_expired", tenant_id=tenant_id, count=len(closed))
        return len(closed)

    async def close_expired_orders(self) -> int:
        """跨企业关闭超时订单（定时补偿任务的唯一入口）。

        Returns:
            int: 被关闭的订单数。
        """
        deadline = now_iso()
        closed = await self._store.expire_orders(deadline, "")
        if closed:
            logger.info("orders_expired_all", count=len(closed))
        return len(closed)


# ===========================================================================
# 进程级单例
# ===========================================================================
_service: OrderService | None = None


def get_order_service(settings: Settings | None = None) -> OrderService:
    """获取订单服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        OrderService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = OrderService(settings)
    return _service


def reset_order_service() -> None:
    """重置订单服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "CALLBACK_ID_PREFIX",
    "CALLBACK_MESSAGE",
    "CALLBACK_SUCCESS",
    "DEFAULT_CHANNEL",
    "INVOICE_ID_PREFIX",
    "MESSAGE_ORDER_UNKNOWN",
    "MESSAGE_SIGNATURE_INVALID",
    "ORDER_CLOSED",
    "ORDER_ID_PREFIX",
    "SYSTEM_ACTOR",
    "OrderService",
    "get_order_service",
    "mask_payment_no",
    "reset_order_service",
]
