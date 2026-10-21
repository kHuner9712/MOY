"""支付网关抽象（阶段二 B09，架构文档 §4.5 BL6 / BL10 / §5 时序图）。

三个实现共用一个 Protocol：

| 实现 | 用途 | 行为 |
| --- | --- | --- |
| `WechatPayGateway` | 生产 | 按微信支付 APIv3 口径做 HMAC-SHA256 签名与验签 |
| `FakeGateway` | 测试 / 本地联调 | 确定性签名，永不发真实请求 |
| （缺省）`get_payment_gateway` | 装配 | 按配置选择，真实环境缺密钥**不静默降级** |

**密钥缺失不阻断启动**：构造网关不读取任何文件、不校验密钥；
只有真正发起支付或验签时才抛 `30004`（文案「服务配置异常，请联系管理员」）。
理由：密钥缺失属部署事故，若在启动期抛错会让「账单页打不开」扩散成「全站不可用」，
而调用期报错只影响下单路径，且错误码与文案对用户仍然可解释。

**签名口径**（本阶段可落地的最小实现，与渠道方对接时替换内部实现即可）：

```
待签串 = "{out_trade_no}\n{transaction_id}\n{amount_total}\n{notify_id}"
sign   = hex(HMAC-SHA256(api_v3_key, 待签串))
```

验签一律使用 `hmac.compare_digest`（常量时间比较），禁止 `==`（防时序侧信道）。

**安全边界**：网关只处理「金额、订单号、流水号、签名」四个字段，
不解析、不记录渠道回传的其它字段，也不把任何密钥写进日志或响应。
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 微信支付渠道名（与 `payment_callback_log.channel` 一致）
GATEWAY_WECHAT_PAY: Final[str] = "wechat_pay"

#: 测试网关渠道名
GATEWAY_FAKE: Final[str] = "fake"

#: 模拟支付页地址前缀（仅测试网关使用）
FAKE_PAY_URL_PREFIX: Final[str] = "https://pay.example.invalid/checkout"

#: 渠道交易成功状态
TRADE_STATE_SUCCESS: Final[str] = "SUCCESS"


@dataclass(frozen=True, slots=True)
class PaymentInitiation:
    """发起支付的结果。

    Attributes:
        pay_url: 支付跳转地址（对公转账为空串）。
        bank_info: 对公转账收款信息（在线支付为 None）。
    """

    pay_url: str = ""
    bank_info: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PaymentNotification:
    """渠道回调解析结果（**只保留业务必需字段**）。

    Attributes:
        order_id: 商户订单号。
        external_txn_id: 渠道流水号（幂等键）。
        amount_cents: 实付金额（分）。
        signature_ok: 验签是否通过。
        trade_state: 渠道交易状态。
        extra: 其它字段（仅用于计算摘要，**不落库、不外泄**）。
    """

    order_id: str = ""
    external_txn_id: str = ""
    amount_cents: int = 0
    signature_ok: bool = False
    trade_state: str = TRADE_STATE_SUCCESS
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """渠道是否宣告交易成功。"""
        return self.trade_state.upper() == TRADE_STATE_SUCCESS


def signing_payload(
    *, out_trade_no: str, transaction_id: str, amount_total: int, notify_id: str = ""
) -> str:
    """构造待签串（四个字段，换行分隔，顺序固定）。

    Args:
        out_trade_no: 商户订单号。
        transaction_id: 渠道流水号。
        amount_total: 实付金额（分）。
        notify_id: 渠道通知 ID（可选）。

    Returns:
        str: 待签字符串。
    """
    return f"{out_trade_no}\n{transaction_id}\n{int(amount_total)}\n{notify_id}"


def payload_digest(payload: dict[str, Any]) -> str:
    """计算回调载荷摘要（SHA-256，**不落原文**）。

    回调载荷可能含用户标识、银行卡后四位等敏感字段，落库只存摘要，
    既能满足「永久可审计」，又不把敏感数据写进业务库。

    Args:
        payload: 回调载荷。

    Returns:
        str: 十六进制摘要。
    """
    canonical = "&".join(f"{key}={payload[key]}" for key in sorted(payload))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PaymentGateway(Protocol):
    """支付网关契约（生产实现与测试实现共用）。"""

    @property
    def channel(self) -> str:
        """渠道名（写入 `payment_callback_log.channel`）。"""
        ...

    async def create_payment(
        self, *, order_id: str, amount_cents: int, subject: str = ""
    ) -> PaymentInitiation:
        """发起支付并返回跳转地址（或对公转账信息）。"""
        ...

    def verify_and_parse(self, payload: dict[str, Any]) -> PaymentNotification:
        """验签并解析回调载荷。"""
        ...


class PaymentGatewayNotConfiguredError(ApiError):
    """网关未配置（密钥缺失）——以 `30004` 暴露，文案为业务语言。"""

    def __init__(self, detail: str) -> None:
        """初始化。

        Args:
            detail: 内部诊断信息（只进日志）。
        """
        super().__init__(ErrorCode.CONFIG_ERROR, internal_detail=detail)


class WechatPayGateway:
    """微信支付网关（APIv3 口径的最小可落地实现）。

    Attributes:
        channel: 渠道名。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化（**不校验密钥**，避免启动期失败）。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    @property
    def channel(self) -> str:
        """渠道名。"""
        return GATEWAY_WECHAT_PAY

    @property
    def credentials_ready(self) -> bool:
        """密钥是否齐备。"""
        return self._settings.payment_credentials_ready

    async def create_payment(
        self, *, order_id: str, amount_cents: int, subject: str = ""
    ) -> PaymentInitiation:
        """发起支付。

        本阶段不调用真实下单接口（无商户证书的离线环境也要能跑通账单页），
        只做「密钥齐备性校验 + 生成收银台地址」，真实下单由部署期的
        `pay_notify_url` / 证书配置接入后替换本方法内部实现。

        Args:
            order_id: 订单 ID。
            amount_cents: 金额（分）。
            subject: 商品描述。

        Returns:
            PaymentInitiation: 收银台地址。

        Raises:
            PaymentGatewayNotConfigured: 商户号或 APIv3 密钥缺失（30004）。
        """
        if not self.credentials_ready:
            logger.error("wechat_pay_credentials_missing", order_id=order_id)
            raise PaymentGatewayNotConfiguredError(
                "OA_PAY_MCH_ID / OA_PAY_API_V3_KEY missing for channel=wechat_pay"
            )
        notify_url = self._settings.pay_notify_url.strip()
        logger.info(
            "wechat_pay_payment_created",
            order_id=order_id,
            amount_cents=amount_cents,
            notify_configured=bool(notify_url),
            subject=subject,
        )
        return PaymentInitiation(
            pay_url=f"{notify_url or 'https://pay.invalid'}/cashier/{order_id}"
        )

    def sign(self, *, out_trade_no: str, transaction_id: str, amount_total: int) -> str:
        """按待签串生成签名（供联调与测试构造回调载荷）。

        Args:
            out_trade_no: 商户订单号。
            transaction_id: 渠道流水号。
            amount_total: 实付金额（分）。

        Returns:
            str: 十六进制签名。

        Raises:
            PaymentGatewayNotConfigured: APIv3 密钥缺失（30004）。
        """
        key = self._settings.pay_api_v3_key.strip()
        if not key:
            raise PaymentGatewayNotConfiguredError("OA_PAY_API_V3_KEY missing")
        message = signing_payload(
            out_trade_no=out_trade_no,
            transaction_id=transaction_id,
            amount_total=amount_total,
        )
        return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify_and_parse(self, payload: dict[str, Any]) -> PaymentNotification:
        """验签并解析回调载荷。

        Args:
            payload: 回调载荷。

        Returns:
            PaymentNotification: 解析结果（`signature_ok` 表明验签结论）。
        """
        order_id = str(payload.get("out_trade_no", "") or "")
        txn_id = str(payload.get("transaction_id", "") or "")
        amount = payload.get("amount_total", 0)
        try:
            amount_cents = int(amount)
        except (TypeError, ValueError):
            amount_cents = 0
        sign = str(payload.get("sign", "") or "")
        key = self._settings.pay_api_v3_key.strip()
        if not key:
            logger.error("wechat_pay_verify_without_key", order_id=order_id)
            raise PaymentGatewayNotConfiguredError(
                "OA_PAY_API_V3_KEY missing while verifying notify"
            )
        expected = hmac.new(
            key.encode("utf-8"),
            signing_payload(
                out_trade_no=order_id,
                transaction_id=txn_id,
                amount_total=amount_cents,
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return PaymentNotification(
            order_id=order_id,
            external_txn_id=txn_id,
            amount_cents=amount_cents,
            signature_ok=hmac.compare_digest(expected, sign),
            trade_state=str(payload.get("trade_state", TRADE_STATE_SUCCESS) or TRADE_STATE_SUCCESS),
            extra={
                key_name: value
                for key_name, value in payload.items()
                if key_name not in {"out_trade_no", "transaction_id", "amount_total", "sign"}
            },
        )


class FakeGateway:
    """测试用假网关（确定性签名，不发任何真实请求）。

    Attributes:
        channel: 渠道名。
    """

    def __init__(self, api_key: str = "test-gateway-key") -> None:
        """初始化。

        Args:
            api_key: 假网关的签名密钥（固定值，便于测试构造回调）。
        """
        self._api_key = api_key

    @property
    def channel(self) -> str:
        """渠道名。"""
        return GATEWAY_FAKE

    async def create_payment(
        self, *, order_id: str, amount_cents: int, subject: str = ""
    ) -> PaymentInitiation:
        """发起支付（返回确定性地址）。

        Args:
            order_id: 订单 ID。
            amount_cents: 金额（分）。
            subject: 商品描述。

        Returns:
            PaymentInitiation: 固定格式的收银台地址。
        """
        logger.info(
            "fake_payment_created", order_id=order_id, amount_cents=amount_cents, subject=subject
        )
        return PaymentInitiation(pay_url=f"{FAKE_PAY_URL_PREFIX}/{order_id}")

    def sign(
        self, *, out_trade_no: str, transaction_id: str, amount_total: int, notify_id: str = ""
    ) -> str:
        """生成确定性签名（测试构造回调载荷时使用）。

        Args:
            out_trade_no: 商户订单号。
            transaction_id: 渠道流水号。
            amount_total: 实付金额（分）。
            notify_id: 渠道通知 ID。

        Returns:
            str: 十六进制签名。
        """
        message = signing_payload(
            out_trade_no=out_trade_no,
            transaction_id=transaction_id,
            amount_total=amount_total,
            notify_id=notify_id,
        )
        return hmac.new(
            self._api_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def verify_and_parse(self, payload: dict[str, Any]) -> PaymentNotification:
        """验签并解析回调载荷。

        Args:
            payload: 回调载荷。

        Returns:
            PaymentNotification: 解析结果。
        """
        order_id = str(payload.get("out_trade_no", "") or "")
        txn_id = str(payload.get("transaction_id", "") or "")
        try:
            amount_cents = int(payload.get("amount_total", 0) or 0)
        except (TypeError, ValueError):
            amount_cents = 0
        expected = self.sign(
            out_trade_no=order_id, transaction_id=txn_id, amount_total=amount_cents
        )
        return PaymentNotification(
            order_id=order_id,
            external_txn_id=txn_id,
            amount_cents=amount_cents,
            signature_ok=hmac.compare_digest(expected, str(payload.get("sign", "") or "")),
            trade_state=str(payload.get("trade_state", TRADE_STATE_SUCCESS) or TRADE_STATE_SUCCESS),
            extra={
                key_name: value
                for key_name, value in payload.items()
                if key_name not in {"out_trade_no", "transaction_id", "amount_total", "sign"}
            },
        )


# ===========================================================================
# 装配
# ===========================================================================
_fake_gateway: PaymentGateway | None = None


def get_payment_gateway(settings: Settings | None = None) -> PaymentGateway:
    """获取支付网关。

    选择规则（不静默降级）：

    - `pay_channel=fake` → `FakeGateway`（测试/本地联调，进程内复用）；
    - 否则 → `WechatPayGateway`；若处于「桩模式 + 开发/测试环境」，
      回落 `FakeGateway` 并打 WARNING（与既有桩模式哲学一致）；
      真实环境即使桩模式也**不**回落，缺密钥时调用期报 `30004`。

    微信支付网关**不做进程级缓存**：它持有配置快照，缓存会让
    「测试注入不同配置」失效（每个请求重建的成本可忽略）。

    Args:
        settings: 可选配置实例。

    Returns:
        PaymentGateway: 支付网关。
    """
    global _fake_gateway
    conf = settings or get_settings()
    if conf.use_fake_payment_gateway:
        if conf.pay_channel != "fake":
            logger.warning(
                "payment_gateway_fallback_to_fake",
                env=conf.env,
                pay_channel=conf.pay_channel,
                reason="downstream_disabled_in_non_production",
            )
        if _fake_gateway is None:
            _fake_gateway = FakeGateway()
        return _fake_gateway
    return WechatPayGateway(conf)


def reset_payment_gateway() -> None:
    """重置网关缓存（测试隔离用）。"""
    global _fake_gateway
    _fake_gateway = None


__all__: list[str] = [
    "FAKE_PAY_URL_PREFIX",
    "GATEWAY_FAKE",
    "GATEWAY_WECHAT_PAY",
    "TRADE_STATE_SUCCESS",
    "FakeGateway",
    "PaymentGateway",
    "PaymentGatewayNotConfiguredError",
    "PaymentInitiation",
    "PaymentNotification",
    "WechatPayGateway",
    "get_payment_gateway",
    "payload_digest",
    "reset_payment_gateway",
    "signing_payload",
]
