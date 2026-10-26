"""匹配提示形态防漂移测试（P2-13：`match_hints` 数据落盘后的单源校验）。

钉住三件事：

1. **能力目录 11 条**（6 插件 + 5 工具）的 `match_hints` 全非空——
   空条目说明声明里漏写了，推荐层会静默失去一路召回；
2. **形态**：条数 ≤ 12、每条 2–8 字、条内无重复——与
   `schema.MATCH_HINT_*` 及 `registry.loader._parse_match_hints` 同口径；
3. **无纯功能字**：「一个」「什么」不携带领域信号，进了打分只会给
   弱命中能力白送分（AC-B1 易混词判据会假红）。

为什么单独一个文件：`test_goal_router` 测的是**推荐行为**，本文件测的是
**声明数据形态**——数据漂移（某条目被清空、某提示词超长）在这里先红。
"""

from __future__ import annotations

import pytest

from app.plugins.schema import (
    FUNCTION_CHARS,
    MATCH_HINT_MAX_COUNT,
    MATCH_HINT_MAX_LEN,
    MATCH_HINT_MIN_LEN,
)
from app.services.capability_catalog import build_catalog

pytestmark = pytest.mark.unit


def test_catalog_has_eleven_entries_with_non_empty_match_hints() -> None:
    """能力目录 11 条，每条 `match_hints` 非空（数据落盘防漂移）。"""
    catalog = build_catalog()
    assert len(catalog) == 11, f"能力目录条数应为 11，实际 {len(catalog)}"
    for entry in catalog:
        assert entry.match_hints, f"{entry.id} 的 match_hints 为空"


def test_match_hints_respect_count_and_length_bounds() -> None:
    """条数 ≤ 12、每条 2–8 字（与 schema / registry 解析器同口径）。"""
    for entry in build_catalog():
        assert len(entry.match_hints) <= MATCH_HINT_MAX_COUNT, (
            f"{entry.id} match_hints 超过 {MATCH_HINT_MAX_COUNT} 条"
        )
        for hint in entry.match_hints:
            assert MATCH_HINT_MIN_LEN <= len(hint) <= MATCH_HINT_MAX_LEN, (
                f"{entry.id} 提示词「{hint}」长度 {len(hint)} "
                f"应在 {MATCH_HINT_MIN_LEN}-{MATCH_HINT_MAX_LEN} 字之间"
            )


def test_match_hints_have_no_duplicates_within_entry() -> None:
    """条内无重复（重复词说明声明作者复制粘贴时没去重）。"""
    for entry in build_catalog():
        assert len(set(entry.match_hints)) == len(entry.match_hints), (
            f"{entry.id} match_hints 有重复：{entry.match_hints}"
        )


def test_match_hints_are_never_pure_function_words() -> None:
    """无纯功能字提示（「一个」「什么」不携带领域信号）。"""
    for entry in build_catalog():
        for hint in entry.match_hints:
            assert any(char not in FUNCTION_CHARS for char in hint), (
                f"{entry.id} 提示词「{hint}」全是功能字，不携带领域信号"
            )
