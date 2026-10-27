"""「常规模式」回答限长（PRD 5.3 ①，P4-11 b 批）单元测试。

三条判据（缺一不可，第三条最容易被漏）：

1. **超限收口**：正文被压到上限并追加常规模式提示；
2. **边界与中文**：恰好等于上限不截断；按码点计数，不切坏多字节字符；
3. **反向断言（关键）**：**未超限的正文必须原样返回、不加任何提示**——
   少了这条，「把限长打到全部用户」不会被任何用例发现，
   那正是本轮反复出现的那类缺陷（"看起来实现了、实际扩到了不该扩的人"）。
"""

from __future__ import annotations

import pytest

from app.services.answer_cap import STANDARD_MODE_NOTICE, apply_answer_cap

pytestmark = pytest.mark.unit

LIMIT = 300


def test_under_limit_is_untouched() -> None:
    """反向断言：未超限的正文原样返回，且不追加提示。"""
    content = "这是一段正常长度的回答。"

    capped, changed = apply_answer_cap(content, limit=LIMIT)

    assert capped == content, "未超限却改写了正文"
    assert changed is False, "未超限却标记为已收口"
    assert STANDARD_MODE_NOTICE not in capped, "未超限却给用户打了常规模式提示"


def test_exactly_at_limit_is_untouched() -> None:
    """边界：恰好等于上限**不**截断（`<=` 而不是 `<`）。"""
    content = "字" * LIMIT

    capped, changed = apply_answer_cap(content, limit=LIMIT)

    assert capped == content
    assert changed is False


def test_over_limit_is_capped_with_notice() -> None:
    """超限：正文压到上限，末尾追加提示语。"""
    content = "字" * (LIMIT + 120)

    capped, changed = apply_answer_cap(content, limit=LIMIT)

    assert changed is True
    assert capped.endswith(STANDARD_MODE_NOTICE)
    body = capped.split("\n\n")[0]
    assert len(body) == LIMIT, f"正文应被压到 {LIMIT} 字，实际 {len(body)}"
    assert len(capped) <= LIMIT + len(STANDARD_MODE_NOTICE) + 2


def test_cap_counts_code_points_for_multibyte_text() -> None:
    """中文按**码点**计数：截断后仍是完整字符，不会出现半个字。"""
    content = "多字节字符" * 200

    capped, changed = apply_answer_cap(content, limit=LIMIT)

    assert changed is True
    body = capped.split("\n\n")[0]
    # 关键断言：长度按**码点**算（Python 码点切片天然不会切出半个汉字）。
    # 若换成按字节截断，中文会在 300 字节处截出乱码，长度也不会等于 LIMIT。
    assert len(body) == LIMIT
    assert body.isprintable()


def test_cap_is_idempotent() -> None:
    """幂等：已带提示语的正文再处理一次不会被二次截断。"""
    content = "字" * (LIMIT + 120)
    once, _ = apply_answer_cap(content, limit=LIMIT)

    twice, changed = apply_answer_cap(once, limit=LIMIT)

    assert twice == once, "二次处理改写了已经收口过的正文（提示语会被自己截掉）"
    assert changed is True


def test_notice_can_be_disabled() -> None:
    """`notice=""` 时只截断、不追加提示（流式降级路径用）。"""
    content = "字" * (LIMIT + 10)

    capped, changed = apply_answer_cap(content, limit=LIMIT, notice="")

    assert changed is True
    assert capped == "字" * LIMIT
