"""金额脱敏：整段遮盖 + 合并区间（阶段三 B21c，追溯表 §3.4）。

**为什么单独一个文件**：金额有**两个用途**共用同一套正则——判定（`engine` 命中即判
该步失败）与脱敏（`mask_amounts` 整段遮掉）。这份用例只钉**脱敏**这一侧，
判定侧在 `test_plugin_guardrails.py`。两者共用规则集，但**判据完全不同**：
判定问"有没有命中"，脱敏问"遮完还看不看得出金额"。

**核心判据（唯一）**：遮后该位置 **0 个数字字符**（含小数点与千分位逗号）。
为什么不用"命中数 == 1"：`8,000-10,000` 在**规则层**确实是 2 次命中
（R2-a 整段 + R2-b 尾段），合并发生在**替换层**。用命中数当判据会把正确实现判成失败。

**为什么必须合并区间**（两处实测证据）：
- `8000 至 10000`：R2-a 整段 + R2-b 命中 `8000` → 不合并只遮左半；
- `8,000-10,000`：R2-a 整段 + R2-b 尾段碎片 → 不合并会产出 `8,***-**0,***`，
  **每个碎片里都留着数字，整体却"看起来遮了"**——比完全不遮更危险。
"""

from __future__ import annotations

import re

import pytest

from app.plugins.actions import ArtifactDraft
from app.plugins.engine import PluginEngine, StepOutcome, StepSpec
from app.plugins.guards import scan_offer_amount
from app.plugins.sanitizer import (
    MASK_AMOUNT,
    amount_spans,
    mask_amounts,
    merge_spans,
)
from app.plugins.schema import GuardSpec
from app.services.action_gateway import InertActionGateway

#: 遮后就不该再有数字痕迹（小数点与千分位逗号也算）
DIGIT_TRACES = re.compile(r"[\d.,]")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8,000", "*****"),
        ("1,500", "*****"),
        ("12,345,678", "**********"),
        ("8000 至 10000", "************"),
        ("8,000-10,000", "************"),
        ("¥8,000", "¥*****"),
        ("RMB8,000", "RMB*****"),
        ("8,000元/月", "******/月"),
        ("月薪 8000 元", "月薪 ******"),
        ("15k", "***"),
        ("1.2万元", "*****"),
    ],
)
def test_amount_is_masked_verbatim(text: str, expected: str) -> None:
    """10 条样本整段遮盖：**位置一个字符不差**。

    币种与单位（`¥` / `RMB` / `/月`）保留——那是呈现选择；判据是下面那条
    「遮后 0 个数字字符」。
    """
    masked = mask_amounts(text)
    assert masked == expected, f"{text!r} 遮盖结果不符：{masked!r}"


@pytest.mark.parametrize(
    "text",
    ["8,000", "1,500", "12,345,678", "8000 至 10000", "8,000-10,000", "8,000元/月", "15k"],
)
def test_masked_region_has_no_digit_traces(text: str) -> None:
    """**唯一硬判据**：遮后该位置 0 个数字字符（含 `.` 与 `,`）。

    `8,***` 之所以危险，正因为它「看起来遮了」：首位还在、逗号还在，
    数量级仍然猜得出来。这条用例专门挡这种"半遮"。
    """
    masked = mask_amounts(text)
    spans = amount_spans(text)
    assert spans, f"{text!r} 应至少命中一处金额"
    for start, end in spans:
        covered = masked[start:end]
        assert not DIGIT_TRACES.search(covered), f"{text!r} 遮盖区仍有数字痕迹：{covered!r}"
        assert set(covered) == {MASK_AMOUNT}, f"{text!r} 遮盖区不是纯占位：{covered!r}"


def test_range_becomes_exactly_one_span() -> None:
    """区间在**替换层**必须是 1 个区间（规则层可以是 2 次命中）。

    `8,000-10,000`：R2-a 整段命中，R2-b 还会命中尾段碎片——
    合并后必须只剩一段，否则替换会切出多个碎片。
    """
    assert len(amount_spans("8,000-10,000")) == 1
    assert len(amount_spans("8000 至 10000")) == 1


def test_merge_spans_joins_touching_ranges() -> None:
    """首尾相接也必须合并（用 `<=` 而不是 `<`），否则替换后中间会夹着未处理字符。"""
    assert merge_spans([(0, 5), (5, 9)]) == [(0, 9)]
    assert merge_spans([(5, 9), (0, 5)]) == [(0, 9)]
    assert merge_spans([(0, 3), (10, 12)]) == [(0, 3), (10, 12)]
    assert merge_spans([(0, 6), (2, 4)]) == [(0, 6)]


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-01",
        "EMP1234",
        "薪资 订单 A-100200",
        "薪资 第 3-5 章",
        "薪资 比例 20-30%",
        "薪 -5~-3",
    ],
)
def test_non_amount_numbers_are_not_masked(text: str) -> None:
    """豁免与排除项一律不动：日期 / 编号 / 章节号 / 百分比 / 负数区间。

    `A-100200` 这条尤其重要：它钉的是"分隔符排除**真的生效**"——
    lookaround 里的字符类若把 `[-–—~～至]` 整块插进 `[\\d…]`，内层 `[` 会提前闭合，
    排除那半个条件会**静默失效**，于是 `100200` 被当成金额遮掉。这种错不报错。
    """
    assert mask_amounts(text) == text, f"{text!r} 不该被遮"
    assert amount_spans(text) == []


def test_mask_is_idempotent() -> None:
    """二次脱敏不再变化（遮后没有数字可命中）。"""
    once = mask_amounts("月薪 8,000-10,000，另有 15k 补贴")
    assert mask_amounts(once) == once


def test_plain_text_is_untouched() -> None:
    """没有金额的正文一个字符都不改（防"一律打码"把内容毁掉）。"""
    clean = "这是一段没有任何金额的正文，只有职责与要求。"
    assert mask_amounts(clean) == clean
    assert amount_spans(clean) == []


# ===========================================================================
# 8. 分隔符字符类与全角输入（追溯表 §3.4 补齐 6 条：①②③）
# ===========================================================================
#: en dash（U+2013）与全角波浪（U+FF5E）——`OFFER_AMOUNT_RANGE_SEPARATORS` 里都有，
#: 但此前**一次都没被真实样本走过**。全角波浪尤其重要：Word / 公众号粘贴默认转全角。
EN_DASH_RANGE = "8000\u201310000"
FULLWIDTH_TILDE_RANGE = "8000\uff5e10000"
#: 全角￥（U+FFE5）——与半角 `¥`（U+00A5）**不是一个字符**
FULLWIDTH_YEN = "\uffe5"


@pytest.mark.parametrize("text", [EN_DASH_RANGE, FULLWIDTH_TILDE_RANGE])
def test_fullwidth_range_separators_are_masked_as_one_span(text: str) -> None:
    """①② en dash / 全角波浪写的区间：**整段一个区间**、遮后无数字。

    这两条钉的是"分隔符字符类真的生效"。字符类里漏一个码位不会报错，
    只会让该写法**整段不被识别**——而它恰恰是粘贴场景里最高频的两种。
    """
    assert len(amount_spans(text)) == 1, f"{text!r} 应整段命中一个区间"
    masked = mask_amounts(text)
    assert masked == MASK_AMOUNT * len(text), f"{text!r} 应整段遮盖，实际 {masked!r}"
    assert not DIGIT_TRACES.search(masked)


def test_fullwidth_yen_is_guard_sample() -> None:
    """③ `￥8000`（全角￥）**必须仍然判失败**。

    这是一条**守卫样本**：豁免②（业务编号）按前缀白名单匹配（`EMP|PO|SKU|ORD|SO|NO`），
    如果哪天有人为了"支持币种"把 `￥`/`RMB` 加进前缀白名单，`￥8000` 会被整段挖掉，
    于是一份写着金额的 Offer **静默通过**。本条就是防这次"好心放宽"。
    """
    in_context = f"月薪 {FULLWIDTH_YEN}8000"
    assert scan_offer_amount(in_context).r2, "全角￥后的数字必须被 R2-b 抓到（不得被豁免挖掉）"
    masked = mask_amounts(f"{FULLWIDTH_YEN}8000")
    assert not DIGIT_TRACES.search(masked.replace(FULLWIDTH_YEN, "")), (
        f"全角￥只保留币种符号，数字必须遮掉，实际 {masked!r}"
    )


# ===========================================================================
# 9. R2-b 的上下文依赖（补齐 ④）
# ===========================================================================
def test_bare_number_requires_money_context() -> None:
    """④ 裸 `8000`：**没有**金额上下文不判失败，有上下文才判失败。

    两个方向都断，才证明 R2-b 是"窗口内匹配"而不是"全文扫数字"——
    只断"有上下文时命中"，会把"全文扫"的实现也判成通过（那会让工期、人数全变红）。
    """
    assert scan_offer_amount("8000").r2 == (), "无上下文窗口时不该命中（否则工期/人数全是金额）"
    assert scan_offer_amount("月薪 8000").r2, "有「月薪」上下文时必须命中"
    assert scan_offer_amount("薪资 8000").r2, "「薪资」同样在上下文词表内"


# ===========================================================================
# 10. 豁免与区间分支的顺序（补齐 ⑤：⑧ 组唯一能抓顺序错误的样本）
# ===========================================================================
def test_exemption_runs_before_range_branch() -> None:
    """⑤ `PO2024001-2025001`：**顺序正确时不命中，顺序颠倒时命中**。

    真实实现是「先剥豁免（编号挖成空格）→ 再跑 R2-a」，于是：
    - 编号整体是 `PO…`，前半被挖掉，剩下的 `-2025001` 前面是分隔符 → **不命中**；
    - 若把两步颠倒（先 R2-a 再豁免），`2024001-2025001` 会先被当成区间抓走 → **命中**。
    所以这条用例用"同形状但不带编号前缀"的对照样本模拟"顺序颠倒"的结果：
    对照样本命中、带前缀样本不命中，才说明豁免**先**跑了。
    """
    assert scan_offer_amount("薪资编号 PO2024001-2025001").r2 == (), (
        "带编号前缀时不得命中（豁免必须先于区间分支）"
    )
    assert scan_offer_amount("薪资区间 2024001-2025001").r2, (
        "去掉编号前缀后必须命中——否则说明豁免把整段都挖掉了"
    )


# ===========================================================================
# 11. 作用域守卫：金额规则**只扫 `output_key=offer`**（补齐 ⑥：⑦ 组整组缺失）
# ===========================================================================
#: 业务规模话术：形如金额（`2000 万`、`500 万`、`1200 万`），但**不是薪资**
SCALE_SENTENCE = "服务 2000 万用户，覆盖 500 万家企业，累计 1200 万次调用。"


def _step_spec() -> StepSpec:
    """构造一个产出 `offer` 的步骤声明。

    Returns:
        StepSpec: 步骤声明。
    """
    return StepSpec(
        seq=5,
        title="生成录用通知文案",
        action="compose",
        produces=["offer"],
        on_failure="continue",
    )


def _outcome(output_key: str, text: str) -> StepOutcome:
    """构造一步成功结果，里面只有一个产物。

    Args:
        output_key: 产物键。
        text: 产物正文。

    Returns:
        StepOutcome: 步骤结果。
    """
    return StepOutcome(
        status="completed",
        artifacts=(
            ArtifactDraft(output_key=output_key, type="text", name="文案", inline_text=text),
        ),
    )


def test_amount_scope_is_offer_only() -> None:
    """⑥ 同一段"规模话术"：放进 `draft` **不判失败**，放进 `offer` **必须判失败**。

    这是 ⑦ 组的作用域防线，此前**零证据**。两个方向都必须断：
    - 只断"offer 失败"→ 无法排除"所有产物都被扫"（那会把正常的方案文档判红）；
    - 只断"draft 通过"→ 无法证明 offer 真的会被拦。
    作用域写死在引擎里（`if draft.output_key == OFFER_OUTPUT_KEY`），不靠调用方传参——
    这条用例就是那句话的执行证据。
    """
    engine = PluginEngine(InertActionGateway())
    spec = _step_spec()
    offer_guards = (GuardSpec(output_key="offer", rule="offer_amount"),)
    blocked = engine.guard(spec, _outcome("draft", SCALE_SENTENCE), offer_guards)
    assert blocked.status == "completed", (
        f"非 offer 产物不该被金额规则判失败，实际 {blocked.status}：{blocked.reason_text}"
    )
    assert blocked.reason_text == ""

    guarded = engine.guard(spec, _outcome("offer", SCALE_SENTENCE), offer_guards)
    assert guarded.status == "failed", "offer 产物里的「2000 万」必须判失败（不得漏检）"
    assert guarded.operator_action, "失败必须给可照做的建议动作"


def test_offer_scope_does_not_leak_into_other_steps() -> None:
    """⑥-b 同一段文本配不同产物键，结果必须不同（证明判定只看 `output_key`）。

    这条是上一条的"反向对照"：如果哪天有人把扫描条件改成"扫所有产物"，
    上一条的 draft 分支会红，这一条会同时红——两处一起红才能定位到"作用域被改了"。
    """
    engine = PluginEngine(InertActionGateway())
    spec = _step_spec()
    offer_guards = (GuardSpec(output_key="offer", rule="offer_amount"),)
    draft_result = engine.guard(spec, _outcome("code_files", SCALE_SENTENCE), offer_guards)
    offer_result = engine.guard(spec, _outcome("offer", SCALE_SENTENCE), offer_guards)
    assert draft_result.status != offer_result.status, (
        "同一段文本因产物键不同应得到不同判定（作用域生效）"
    )
