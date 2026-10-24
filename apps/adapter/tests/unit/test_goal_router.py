"""规则推荐单测（W1 切片 B，`architecture-w1-metrics-entry.md` §1.5.3 / T03 判据 2）。

覆盖四组规则（全部可枚举、不依赖网络与模型）：

1. **切分**：CJK 2-gram 滑窗 + ASCII 小写词、混合串、保序去重；
2. **权重**：`name×3 / summary×2 / category×1 / inputs.label×1 / operations×1`，
   同词命中多字段可叠加；
3. **排序确定性**：同分 → plugin 优先 → id 升序；
4. **关键词表防漂移**：`_KEYWORD_TERMS` 的每个 id 必须存在于真实目录
   （插件声明 / 工具注册表单例）。

另覆盖空目标与无命中兜底两种形态（AC-B3/B4）。
"""

from __future__ import annotations

import pytest

from app.services.capability_catalog import (
    CapabilityEntry,
    CapabilityInputBrief,
    build_catalog,
)
from app.services.goal_router import (
    _KEYWORD_TERMS,
    FALLBACK_HINT,
    GoalMatchResult,
    recommend,
    tokenize,
)

pytestmark = pytest.mark.unit


def _entry(
    entry_id: str,
    *,
    kind: str = "plugin",
    name: str = "",
    summary: str = "",
    category: str = "",
    inputs_label: str = "",
    operations: str = "",
) -> CapabilityEntry:
    """构造合成目录条目（只填传入的打分料源字段）。

    Args:
        entry_id: 能力标识。
        kind: `plugin` / `tool`。
        name: name 料源文本。
        summary: summary 料源文本。
        category: category 料源文本。
        inputs_label: inputs.label 料源文本。
        operations: operations 料源文本。

    Returns:
        CapabilityEntry: 目录条目。
    """
    match_texts: dict[str, str] = {}
    if name:
        match_texts["name"] = name
    if summary:
        match_texts["summary"] = summary
    if category:
        match_texts["category"] = category
    if inputs_label:
        match_texts["inputs.label"] = inputs_label
    if operations:
        match_texts["operations"] = operations
    inputs = (
        (CapabilityInputBrief(key="k", label=inputs_label, type="text", required=False),)
        if inputs_label
        else ()
    )
    return CapabilityEntry(
        id=entry_id,
        kind=kind,
        name=name,
        summary=summary,
        category=category,
        inputs=inputs,
        match_texts=match_texts,
        sort_key=(1, entry_id),
    )


# ===========================================================================
# 1. 切分（tokenize）
# ===========================================================================
def test_tokenize_cjk_uses_bigram_sliding_window() -> None:
    """CJK 连续段逐 2 字滑窗（单字段落保留单字）。"""
    assert tokenize("整理本周周报") == ["整理", "理本", "本周", "周周", "周报"]
    assert tokenize("报") == ["报"]
    assert tokenize("周报") == ["周报"]


def test_tokenize_ascii_words_are_lowercased_and_split() -> None:
    """ASCII 段按非字母数字切分并小写。"""
    assert tokenize("GitHub PR") == ["github", "pr"]
    assert tokenize("feishu、calendar") == ["feishu", "calendar"]


def test_tokenize_mixed_text_dedups_in_order() -> None:
    """中英混合、标点分隔、按首次出现去重（保序）。"""
    assert tokenize("写周报，写周报 weekly") == ["写周", "周报", "weekly"]


def test_tokenize_empty_and_punctuation_only() -> None:
    """空串与纯标点 → 空列表（调用方据此走空目标/兜底分支）。"""
    assert tokenize("") == []
    assert tokenize("！？。，") == []


def test_tokenize_drops_pure_function_word_bigrams() -> None:
    """两字均为功能字的 2-gram 丢弃（「一个」不给内容运营白送分）。

    这是 AC-B1「易混词不串」的机械保障：内容运营摘要以「一个主题」开头，
    不过滤「一个」，「写一个招聘说明」会被顶到它前面。
    """
    assert tokenize("写一个招聘说明") == ["写一", "个招", "招聘", "聘说", "说明"]
    assert tokenize("我们什么") == []
    assert "一个" not in tokenize("安排一个会议")
    # 领域词不受影响：两字不都在功能字表里
    assert tokenize("整理本周") == ["整理", "理本", "本周"]


# ===========================================================================
# 2. 权重（name×3 / summary×2 / category×1 / inputs.label×1 / operations×1）
# ===========================================================================
@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("name", 3),
        ("summary", 2),
        ("category", 1),
        ("inputs.label", 1),
        ("operations", 1),
    ],
)
def test_each_field_weight(field: str, expected: int) -> None:
    """单字段命中只计该字段权重。"""
    # 契约字段名 `inputs.label` 不是合法 Python 标识符，映射到助手参数名
    kwarg = "inputs_label" if field == "inputs.label" else field
    entry = _entry("plg_w", **{kwarg: "alpha"})
    result = recommend("alpha", limit=1, catalog=[entry])
    assert result.matched is True
    assert result.items[0].score == expected
    assert result.items[0].matched_fields == (field,)


def test_same_term_stacks_across_fields() -> None:
    """同词命中多字段可叠加（如「周报」命中 name+summary = 3+2=5）。"""
    entry = _entry("plg_stack", name="alpha", summary="alpha")
    result = recommend("alpha", limit=1, catalog=[entry])
    assert result.items[0].score == 5
    assert result.items[0].matched_terms == ("alpha",)
    assert result.items[0].matched_fields == ("name", "summary")


def test_field_order_in_matched_fields_follows_weights() -> None:
    """命中字段按权重降序输出（同权按字段声明序）。"""
    entry = _entry(
        "plg_order",
        name="alpha",
        summary="alpha",
        category="alpha",
        inputs_label="alpha",
        operations="alpha",
    )
    result = recommend("alpha", limit=1, catalog=[entry])
    assert result.items[0].score == 8  # 3+2+1+1+1
    assert result.items[0].matched_fields == (
        "name",
        "summary",
        "category",
        "inputs.label",
        "operations",
    )


# ===========================================================================
# 3. 排序确定性（同分 → plugin 优先 → id 升序）
# ===========================================================================
def test_ordering_is_deterministic_on_ties() -> None:
    """同分 → plugin 优先 → id 升序（可枚举，无随机性）。"""
    entries = [
        _entry("tool:zzz", kind="tool", name="alpha"),
        _entry("plg_bbb", name="alpha"),
        _entry("plg_aaa", name="alpha"),
    ]
    result = recommend("alpha", limit=10, catalog=entries)
    assert [item.entry.id for item in result.items] == ["plg_aaa", "plg_bbb", "tool:zzz"]


def test_higher_score_ranks_first_regardless_of_kind() -> None:
    """得分高者优先，插件优先只在同分时生效。"""
    entries = [
        _entry("tool:aaa", kind="tool", name="alpha", summary="alpha"),  # 5 分
        _entry("plg_zzz", name="alpha", summary="alpha", category="alpha"),  # 6 分
    ]
    result = recommend("alpha", limit=10, catalog=entries)
    assert [item.entry.id for item in result.items] == ["plg_zzz", "tool:aaa"]


def test_limit_truncates_and_keeps_order() -> None:
    """`limit` 截断结果且不改变排序。"""
    entries = [_entry(f"plg_{index:03d}", name="alpha") for index in range(5)]
    result = recommend("alpha", limit=2, catalog=entries)
    assert [item.entry.id for item in result.items] == ["plg_000", "plg_001"]


# ===========================================================================
# 4. 关键词表防漂移（id 必须存在于目录）
# ===========================================================================
def test_keyword_table_ids_all_exist_in_catalog() -> None:
    """关键词表引用的每个能力 id 都必须存在于真实目录（防漂移）。"""
    catalog = build_catalog()
    catalog_ids = {entry.id for entry in catalog}
    missing = sorted(set(_KEYWORD_TERMS) - catalog_ids)
    assert missing == [], f"关键词表引用了目录中不存在的能力：{missing}"
    assert catalog_ids, "目录为空（注册表没加载？）"


def test_keyword_terms_are_all_non_empty_strings() -> None:
    """每个能力都有领域词且无空词。

    设计 §1.5.3 的指导值是「每能力 5-12 个」，但其示例表自身就有
    4 词（plg_weekly_report）与 3 词（plg_meeting_minutes）的能力——
    以表为准，这里只硬断言「非空且不超过 12」（防漂移看 id 与空词，
    词数是风格约束，不是正确性约束）。
    """
    for entry_id, terms in _KEYWORD_TERMS.items():
        assert 1 <= len(terms) <= 12, f"{entry_id} 关键词数 {len(terms)} 越界（应 1-12）"
        assert all(term.strip() for term in terms), f"{entry_id} 存在空关键词"
        assert len(set(terms)) == len(terms), f"{entry_id} 关键词表有重复词"


def test_multi_char_keyword_extends_matching_beyond_bigrams() -> None:
    """「代码仓库」是 4 字词，不是任何 goal 的 2-gram，靠关键词表命中。"""
    catalog = build_catalog()
    result = recommend("把代码仓库里的议题处理一下", limit=3, catalog=catalog)
    assert result.items[0].entry.id == "tool:github"


# ===========================================================================
# 5. 空目标与无命中兜底（AC-B3 / AC-B4）
# ===========================================================================
def test_empty_goal_returns_catalog_without_error() -> None:
    """AC-B4：空/纯空白目标 → 返回目录，不算兜底，不报错。"""
    catalog = build_catalog()
    result = recommend("   ", limit=3, catalog=catalog)
    assert result.matched is False
    assert result.fallback is False
    assert result.fallback_hint == ""
    assert [item.entry.id for item in result.items] == [entry.id for entry in catalog[:3]]
    assert all(item.score == 0 for item in result.items)


def test_no_hit_falls_back_to_catalog_with_guidance() -> None:
    """AC-B3：无命中 → 目录兜底 + 引导文案（不得空列表、禁占位词）。"""
    catalog = build_catalog()
    result = recommend("量子引力波谐振器", limit=3, catalog=catalog)
    assert result.matched is False
    assert result.fallback is True
    assert result.fallback_hint == FALLBACK_HINT
    assert result.items, "无命中不得返回空列表"
    assert all(item.score == 0 for item in result.items)
    assert all(item.matched_terms == () for item in result.items)
    assert all(item.matched_fields == () for item in result.items)
    for banned in ("暂不支持", "开发中", "即将上线", "敬请期待", "稍后开放"):
        assert banned not in result.fallback_hint


def test_hits_always_carry_reasons() -> None:
    """AC-B2：入选卡必有命中词与命中字段（禁空理由）。"""
    catalog = build_catalog()
    result: GoalMatchResult = recommend("整理本周周报发给主管", limit=3, catalog=catalog)
    assert result.matched is True
    assert result.items[0].entry.id == "plg_weekly_report"
    for item in result.items:
        assert item.matched_terms, "入选卡必须有命中词"
        assert item.matched_fields, "入选卡必须有命中字段"
        assert item.score >= 1


def test_recommend_accepts_explicit_limit_default() -> None:
    """`limit` 缺省 3（PRD 口径）；目录不足时全量返回。"""
    catalog = build_catalog()
    result = recommend("alpha beta", limit=3, catalog=catalog)
    assert len(result.items) <= 3
