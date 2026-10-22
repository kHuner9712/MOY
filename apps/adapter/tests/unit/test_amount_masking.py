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

from app.plugins.sanitizer import (
    MASK_AMOUNT,
    amount_spans,
    mask_amounts,
    merge_spans,
)

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
    ["2026-11-01", "EMP1234", "薪资 订单 A-100200", "薪资 第 3-5 章", "薪资 比例 20-30%", "薪 -5~-3"],
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
