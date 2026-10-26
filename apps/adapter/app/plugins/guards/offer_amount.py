"""Offer 金额三规则（P0-P3-23 ② / 追溯表 C-13 / 架构 §7.3 + §11 勘误 #3）。

R1 / R2 / R3 的判据（与追溯表 §3.4 对齐）：

- **R1** 带单位的金额：`8000 元` / `1.2 万元` / `15k` / `3 亿元` / `8000/月`；
- **R2** 金额上下文（默认 `薪|工资|待遇|补贴|奖金|年终|期权|股权|月收入|总包`）
  前后 20 字符窗口内的区间或裸数字；
- **R3** 金额位置的禁用表述：`面议|待定|可谈|可议|根据经验|视情况|依能力|面谈|另议|按市场水平`
  ——这些说法等于把金额决策甩给用户，而用户会误以为结论是模型给的。

**金额形状正则住 在 `app.plugins.sanitizer`**（唯一真源）：同一套正则要同时
服务「判定」（命中即判该步失败）与「脱敏」（`mask_amounts` 整段遮掉）。
两份实现必然漂移，而**判定严 + 脱敏松** 会留下"判不出来、也没遮掉"的产物——
这是合规红线。本模块只 import、不重写正则。

**作用域**：`required_output_keys=("offer",)`——只扫这一个产物键。
`screening` 的结论枚举**合法包含**「待定」，一旦把扫描范围放到"任务级"，
每份「待定」简历都会误报成 P0 缺陷——而且方向是"看起来更严"，最容易被
当成好事放过。作用域由声明（`guards[].output_key`）钉住，不接受传参扩张。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.core.logging import get_logger
from app.plugins.guards.registry import register
from app.plugins.guards.types import GuardViolation
from app.plugins.sanitizer import (
    OFFER_AMOUNT_RANGE_PATTERN,
    OFFER_AMOUNT_UNIT_PATTERN,
    OFFER_BARE_NUMBER_PATTERN,
    strip_amount_exemptions,
)

logger = get_logger(__name__)

#: 产物键（声明侧由 `GuardSpec.output_key` 声明；这里是规则的强制覆盖范围）
OFFER_OUTPUT_KEY: Final[str] = "offer"

#: Offer 的金额字段（`bonus` 与 §7.3 的「股权」按同义处理，两边都要挡）。
OFFER_AMOUNT_FIELDS: Final[tuple[str, ...]] = (
    "salary",
    "probation_salary",
    "bonus",
    "equity",
)

#: 金额位置**唯一允许**的两个取值（空串 = 由人事手填；或明确的「按公司标准执行」）。
OFFER_AMOUNT_ALLOWED_VALUES: Final[tuple[str, ...]] = ("", "按公司标准执行")

#: 金额违规时给用户的建议动作（**固定文案**，且不得指向"重新发起任务"）。
#:
#: 合规问题不能靠重跑碰运气，也不该让用户为模型的错付一整单成本；
#: Offer 是可编辑草稿，就地改两个字段再导出即可。
OFFER_AMOUNT_OPERATOR_ACTION: Final[str] = "清空或改为「按公司标准执行」后重新导出"

#: R2：金额上下文关键词（**只在"判定"里用**——脱敏按形状遮，不需要上下文）
OFFER_AMOUNT_CONTEXT_KEYWORDS: Final[tuple[str, ...]] = (
    "薪",
    "工资",
    "待遇",
    "补贴",
    "奖金",
    "年终",
    "期权",
    "股权",
    "月收入",
    "总包",
)

#: R2 的上下文窗口半径（字符）
OFFER_AMOUNT_CONTEXT_WINDOW: Final[int] = 20

#: R3：金额位置的禁用表述（把金额决策甩给用户，且用户会误以为是模型给的结论）
OFFER_AMOUNT_FORBIDDEN_PHRASES: Final[tuple[str, ...]] = (
    "面议",
    "待定",
    "可谈",
    "可议",
    "根据经验",
    "视情况",
    "依能力",
    "面谈",
    "另议",
    "按市场水平",
)

#: 违规是否可重试（金额违规重跑还是同样的产物，白烧一次额度）
OFFER_AMOUNT_RETRYABLE: Final[bool] = False

#: 缺省上下文模式（默认词表编译一次）
_CONTEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    "|".join(re.escape(word) for word in OFFER_AMOUNT_CONTEXT_KEYWORDS)
)

#: 缺省禁用表述模式
_FORBIDDEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    "|".join(re.escape(word) for word in OFFER_AMOUNT_FORBIDDEN_PHRASES)
)


@dataclass(frozen=True, slots=True)
class AmountScan:
    """Offer 金额三规则的扫描结果（R1 / R2 / R3 + 字段取值）。

    Attributes:
        r1: R1（带单位金额）命中的原文片段。
        r2: R2（金额上下文内的裸数字）命中的原文片段。
        r3: R3（金额位置的禁用表述）命中的原文片段。
        fields: 取值不在白名单内的金额字段键。
    """

    r1: tuple[str, ...] = ()
    r2: tuple[str, ...] = ()
    r3: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """三条规则是否全部 0 命中且字段取值合规。

        Returns:
            bool: 全部合规返回 True。
        """
        return not (self.r1 or self.r2 or self.r3 or self.fields)

    def reasons(self) -> list[str]:
        """把命中翻译成业务语言（**不出现正则、字段名、错误码**）。

        Returns:
            list[str]: 逐条原因（空列表表示合规）。
        """
        reasons: list[str] = []
        if self.fields:
            reasons.append("金额相关的几处还没有确认，需要人事来填或选择「按公司标准执行」")
        if self.r1:
            reasons.append("录用通知里出现了具体金额数字")
        if self.r2:
            reasons.append("录用通知在薪资相关段落里出现了具体数字")
        if self.r3:
            reasons.append("录用通知在金额位置写了「面议」「待定」这类说法")
        return reasons


def _unique_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    """取正则命中的去重片段（保持出现顺序，便于日志与断言）。

    Args:
        pattern: 已编译的正则。
        text: 待扫文本。

    Returns:
        list[str]: 去重后的命中片段。
    """
    hits: list[str] = []
    for match in pattern.finditer(text or ""):
        value = match.group().strip()
        if value and value not in hits:
            hits.append(value)
    return hits


def _compile_words(words: tuple[str, ...], fallback: re.Pattern[str]) -> re.Pattern[str]:
    """把词表编译成匹配模式（空词表回落缺省模式）。

    Args:
        words: 词表。
        fallback: 缺省模式（词表为空时使用）。

    Returns:
        re.Pattern[str]: 编译结果。
    """
    if not words:
        return fallback
    return re.compile("|".join(re.escape(word) for word in words))


def scan_offer_amount(
    text: str = "",
    fields: dict[str, Any] | None = None,
    *,
    amount_fields: tuple[str, ...] = OFFER_AMOUNT_FIELDS,
    allowed_values: tuple[str, ...] = OFFER_AMOUNT_ALLOWED_VALUES,
    forbidden_phrases: tuple[str, ...] = OFFER_AMOUNT_FORBIDDEN_PHRASES,
    context_keywords: tuple[str, ...] = OFFER_AMOUNT_CONTEXT_KEYWORDS,
    context_window: int = OFFER_AMOUNT_CONTEXT_WINDOW,
) -> AmountScan:
    """对 Offer 文案与其金额字段跑 R1 + R2 + R3 三规则。

    **只处理传入的这一份 Offer**：调用方（引擎）已经按声明的 `output_key`
    取好了产物，本函数不再自己去扫别的产物——作用域写在声明里，不靠传参。

    R2 的三步顺序**不可颠倒**（颠倒就会出新假红或漏检）：

    1. **先剥豁免**（日期 / 业务编号 / 百分比）；
    2. **再找区间**（R2-a）：`8000-10000`、`8000~10000`、`8000至10000`；
    3. **最后找裸数字**（R2-b）：`8000`、`8,000`。

    为什么 R2-a 必须在 R2-b 之前、且独立成支：区间写法正好落在
    「R2-b 排除连字符（怕误伤日期）」与「R1 要求带单位」的空隙里，两边都抓不到；
    而如果把豁免放在区间之后，`2026-11-01` 会先被区间分支抓走变成假阳性。

    Args:
        text: Offer 正文。
        fields: 金额字段键值（键 ⊆ `amount_fields`）。
        amount_fields: 金额字段键表（默认基线；params 只允许收紧）。
        allowed_values: 金额位置白名单取值。
        forbidden_phrases: R3 禁用表述（收紧词表用）。
        context_keywords: R2 上下文关键词（收紧词表用）。
        context_window: R2 上下文窗口半径（字符）。

    Returns:
        AmountScan: 扫描结果。
    """
    body = text or ""
    forbidden_pattern = _compile_words(tuple(forbidden_phrases), _FORBIDDEN_PATTERN)
    context_pattern = _compile_words(tuple(context_keywords), _CONTEXT_PATTERN)
    r1 = _unique_matches(OFFER_AMOUNT_UNIT_PATTERN, body)
    r3 = _unique_matches(forbidden_pattern, body)
    masked = strip_amount_exemptions(body)
    window_radius = max(1, int(context_window))
    r2_hits: list[str] = []
    for context in context_pattern.finditer(masked):
        start = max(0, context.start() - window_radius)
        end = min(len(masked), context.end() + window_radius)
        window = masked[start:end]
        # 先区间（R2-a），再裸数字（R2-b）：两者独立命中，不互相短路
        for pattern in (OFFER_AMOUNT_RANGE_PATTERN, OFFER_BARE_NUMBER_PATTERN):
            for match in pattern.finditer(window):
                hit = match.group()
                if hit not in r2_hits:
                    r2_hits.append(hit)
    bad_fields: list[str] = []
    for key in amount_fields:
        if fields is None or key not in fields:
            continue
        value = fields[key]
        normalized = "" if value is None else str(value).strip()
        if normalized not in allowed_values:
            bad_fields.append(key)
    return AmountScan(
        r1=tuple(r1),
        r2=tuple(r2_hits),
        r3=tuple(r3),
        fields=tuple(bad_fields),
    )


#: 声明参数允许的键（未知键一律拒绝——多写一个键说明声明作者在试图改判据）
_ALLOWED_PARAM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "amount_fields",
        "allowed_values",
        "forbidden_phrases",
        "context_keywords",
        "context_window",
        "retryable",
        "operator_action",
    }
)


def _as_str_tuple(
    params: Mapping[str, object], key: str, *, allow_blank: bool = False
) -> tuple[str, ...]:
    """把声明参数里的字符串列表读成元组（缺失返回空元组 = 用缺省）。

    Args:
        params: 声明参数。
        key: 参数键。
        allow_blank: 是否允许空串元素（`allowed_values` 的「留空」语义）。

    Returns:
        tuple[str, ...]: 字符串元组。

    Raises:
        ValueError: 类型不合法、空列表（会关掉判据）或含空串。
    """
    raw = params.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple) or not raw:
        raise ValueError(f"参数 {key} 必须是非空列表（空列表会关掉判据）")
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"参数 {key} 必须是非空字符串列表")
        if not allow_blank and not item:
            raise ValueError(f"参数 {key} 必须是非空字符串列表")
    return tuple(raw)


class _OfferAmountRule:
    """Offer 金额三规则的声明式实现（`GuardRule` 协议）。"""

    name: Final[str] = "offer_amount"
    required_output_keys: Final[tuple[str, ...]] = (OFFER_OUTPUT_KEY,)

    def validate_params(self, params: Mapping[str, object]) -> None:
        """校验声明参数：只允许收紧词表/列名/文案，拒绝关闭判据的布尔项。

        Args:
            params: 声明里的 `guards[].params`。

        Raises:
            ValueError: 未知键、类型不合法。
        """
        unknown = sorted(set(params) - _ALLOWED_PARAM_KEYS)
        if unknown:
            raise ValueError(f"offer_amount 不认识这些参数：{unknown}")
        for key in ("amount_fields", "forbidden_phrases", "context_keywords"):
            if key in params:
                _as_str_tuple(params, key)
        if "allowed_values" in params:
            _as_str_tuple(params, "allowed_values", allow_blank=True)
        if "context_window" in params:
            window = params["context_window"]
            if not isinstance(window, int) or isinstance(window, bool) or not 1 <= window <= 200:
                raise ValueError("参数 context_window 必须是 1-200 的整数")
        if "retryable" in params and not isinstance(params["retryable"], bool):
            raise ValueError("参数 retryable 必须是布尔值")
        if "operator_action" in params:
            action = params["operator_action"]
            if not isinstance(action, str) or not action.strip():
                raise ValueError("参数 operator_action 必须是非空文案")

    def check(
        self, *, text: str, content: bytes | None, params: Mapping[str, object]
    ) -> GuardViolation | None:
        """对 `output_key=offer` **这一个产物**跑 R1 + R2 + R3（P0-P3-23 ②）。

        Args:
            text: Offer 正文。
            content: 产物字节（Offer 是文本产物，不使用）。
            params: 声明参数（缺省 = 内置基线）。

        Returns:
            GuardViolation | None: 违规返回违规记录；合规返回 None。
        """
        del content
        amount_fields = (
            _as_str_tuple(params, "amount_fields")
            if "amount_fields" in params
            else OFFER_AMOUNT_FIELDS
        )
        allowed_values = (
            _as_str_tuple(params, "allowed_values", allow_blank=True)
            if "allowed_values" in params
            else OFFER_AMOUNT_ALLOWED_VALUES
        )
        forbidden = (
            _as_str_tuple(params, "forbidden_phrases")
            if "forbidden_phrases" in params
            else OFFER_AMOUNT_FORBIDDEN_PHRASES
        )
        context = (
            _as_str_tuple(params, "context_keywords")
            if "context_keywords" in params
            else OFFER_AMOUNT_CONTEXT_KEYWORDS
        )
        raw_window = params.get("context_window")
        window = raw_window if isinstance(raw_window, int) else OFFER_AMOUNT_CONTEXT_WINDOW
        scan = scan_offer_amount(
            text,
            amount_fields=amount_fields,
            allowed_values=allowed_values,
            forbidden_phrases=forbidden,
            context_keywords=context,
            context_window=window,
        )
        if scan.clean:
            return None
        logger.warning(
            "plugin_offer_amount_blocked",
            r1=len(scan.r1),
            r2=len(scan.r2),
            r3=len(scan.r3),
            fields=list(scan.fields),
        )
        return GuardViolation(
            reason_text="；".join(scan.reasons()) or "录用通知里出现了不该有的金额信息",
            operator_action=str(params.get("operator_action") or OFFER_AMOUNT_OPERATOR_ACTION),
            retryable=bool(params.get("retryable", OFFER_AMOUNT_RETRYABLE)),
        )


register(_OfferAmountRule())


__all__: list[str] = [
    "OFFER_AMOUNT_ALLOWED_VALUES",
    "OFFER_AMOUNT_CONTEXT_KEYWORDS",
    "OFFER_AMOUNT_CONTEXT_WINDOW",
    "OFFER_AMOUNT_FIELDS",
    "OFFER_AMOUNT_FORBIDDEN_PHRASES",
    "OFFER_AMOUNT_OPERATOR_ACTION",
    "OFFER_AMOUNT_RETRYABLE",
    "OFFER_OUTPUT_KEY",
    "AmountScan",
    "scan_offer_amount",
]
