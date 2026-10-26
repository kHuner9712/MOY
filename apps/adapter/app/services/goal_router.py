"""目标式能力推荐的规则打分（W1 切片 B，`architecture-w1-metrics-entry.md` §1.5.3）。

纯函数、无 IO、可枚举可单测（PRD §2.3 边界 1：**不引入模型/语义检索**，
语义检索留 V1.1，接口签名不变）。

规则（与设计逐条对应）：

- **切分**：CJK 连续段逐 2 字滑窗 + ASCII 小词；不引 jieba（依赖门禁），
  领域词覆盖靠**声明里的 `match_hints`**（P2-13：与插件/工具声明同源，
  不再维护第二份关键词表）；**两字均为功能字的 2-gram 直接丢弃**
  （「一个」「什么」「我们」不携带领域信号——否则内容运营摘要里的
  「一个主题」足以把弱命中能力的分数顶上去，AC-B1 的易混词判据会假红）；
- **匹配提示**：每条目 0-12 个领域词（声明 `match_hints`），是**匹配辅助**——
  只进打分、不进展示文案；配防漂移测试（目录 11 条全非空 + 形态校验）；
- **打分**：词命中字段 = 词是字段文本的子串（大小写不敏感）；
  每个「词 × 字段」计一次权重分，同词命中多字段可叠加
  （如「周报」命中 name+summary = 3+2=5）；
- **入选与排序**：`score ≥ 1` 入选；按 `(score 降序, plugin 优先, id 升序)`
  取前 `limit`——确定性，可枚举可测；
- **理由回填**（AC-B2）：`matched_terms` + `matched_fields`，
  入选卡必有命中词与字段（禁空理由）；
- **兜底**（AC-B3）：无任何能力得分 ≥1 → 目录前 `limit` 条 + 引导文案
  （不得空列表、禁占位词）；**空目标**（AC-B4）→ 目录前 `limit` 条，
  不算兜底（`fallback=false`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.plugins.schema import FUNCTION_CHARS
from app.services.capability_catalog import CapabilityEntry, build_catalog

#: 字段权重（name 最重：用户说的多半就是能力名）
FIELD_WEIGHTS: Final[dict[str, int]] = {
    "name": 3,
    "summary": 2,
    "category": 1,
    "inputs.label": 1,
    "operations": 1,
}

#: 无命中兜底引导文案（给明确引导；**禁占位词**：暂不支持/开发中/即将上线/敬请期待）
FALLBACK_HINT: Final[str] = "没有找到完全匹配的能力，可以换种说法，或去插件市场浏览全部能力"

#: 中文功能字：**唯一真源在 `app.plugins.schema.FUNCTION_CHARS`**（P2-13）。
#: 本别名只保留旧名以缩小调用点 diff；行为不变。
_STOP_CHARS: Final[frozenset[str]] = FUNCTION_CHARS


@dataclass(frozen=True, slots=True)
class ScoredCapability:
    """一条打了分的能力（含命中理由）。

    Attributes:
        entry: 目录条目。
        score: 命中得分（Σ 词×字段权重；兜底/空目标为 0）。
        matched_terms: 命中词（去重，按得分降序 → goal 中出现位置升序）。
        matched_fields: 命中字段（契约字段名，按权重降序）。
    """

    entry: CapabilityEntry
    score: int
    matched_terms: tuple[str, ...]
    matched_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GoalMatchResult:
    """推荐结果（命中 / 兜底 / 空目标三种形态）。

    Attributes:
        items: 推荐卡（无命中兜底与空目标时为目录前 `limit` 条，不得为空）。
        matched: 是否有 ≥1 条得分 > 0。
        fallback: 是否走了无命中兜底（空目标不算兜底）。
        fallback_hint: 兜底引导文案（无兜底时为空串）。
    """

    items: tuple[ScoredCapability, ...]
    matched: bool
    fallback: bool
    fallback_hint: str


def _is_cjk(char: str) -> bool:
    """是否 CJK 统一表意文字（基本区 + 扩展 A + 兼容表意）。

    Args:
        char: 单个字符。

    Returns:
        bool: 是 CJK 表意文字返回 True。
    """
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF  # 基本区
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0xF900 <= code <= 0xFAFF  # 兼容表意
    )


def tokenize(goal: str) -> list[str]:
    """把目标文本切成候选词：CJK 连续段 2-gram 滑窗 + ASCII 小写词。

    - CJK 段逐 2 字滑窗（「整理本周周报」→ 整理/理本/本周/周周/周报）；
      长度 1 的段落保留单字（单字目标也有词可匹配）；
    - **两字均为功能字的 2-gram 丢弃**（「一个」「什么」；见 `_STOP_CHARS`）；
    - ASCII 段按非字母数字切分并小写（「github PR」→ github/pr）；
    - 结果按首次出现过去重（保序）；空输入返回空列表。

    Args:
        goal: 用户目标（业务语言）。

    Returns:
        list[str]: 候选词列表（保序去重）。
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        """收录候选词（去重保序；丢弃纯功能字词）。"""
        if term and term not in seen and not all(char in _STOP_CHARS for char in term):
            seen.add(term)
            terms.append(term)

    cjk_run: list[str] = []
    ascii_run: list[str] = []

    def flush_cjk() -> None:
        """结算 CJK 连续段（2-gram 滑窗）。"""
        if len(cjk_run) == 1:
            add(cjk_run[0])
        else:
            for index in range(len(cjk_run) - 1):
                add("".join(cjk_run[index : index + 2]))
        cjk_run.clear()

    def flush_ascii() -> None:
        """结算 ASCII 连续段（小写化整词）。"""
        if ascii_run:
            add("".join(ascii_run).lower())
        ascii_run.clear()

    for char in goal:
        if _is_cjk(char):
            flush_ascii()
            cjk_run.append(char)
        elif char.isalnum():
            flush_cjk()
            ascii_run.append(char)
        else:
            flush_cjk()
            flush_ascii()
    flush_cjk()
    flush_ascii()
    return terms


def _entry_terms(goal_lower: str, entry: CapabilityEntry) -> list[str]:
    """候选词 = goal 切分词 + 出现在 goal 里的该条目关键词（保序去重）。

    Args:
        goal_lower: 小写化后的 goal（关键词子串判定用）。
        entry: 目录条目。

    Returns:
        list[str]: 参与打分的候选词。
    """
    terms = tokenize(goal_lower)
    known = set(terms)
    for keyword in entry.match_hints:
        needle = keyword.lower()
        if needle in goal_lower and needle not in known:
            known.add(needle)
            terms.append(needle)
    return terms


def _score_entry(goal_lower: str, entry: CapabilityEntry) -> ScoredCapability:
    """按「词 × 字段」子串匹配给单条能力打分。

    Args:
        goal_lower: 小写化后的 goal。
        entry: 目录条目。

    Returns:
        ScoredCapability: 得分与命中理由（无命中时 score=0、理由为空）。
    """
    haystacks = {field_name: text.lower() for field_name, text in entry.match_texts.items() if text}
    score = 0
    term_scores: dict[str, int] = {}
    hit_fields: set[str] = set()
    for term in _entry_terms(goal_lower, entry):
        needle = term.lower()
        for field_name, haystack in haystacks.items():
            if needle in haystack:
                weight = FIELD_WEIGHTS[field_name]
                score += weight
                term_scores[term] = term_scores.get(term, 0) + weight
                hit_fields.add(field_name)
    matched_terms = tuple(
        sorted(
            term_scores,
            key=lambda term: (-term_scores[term], goal_lower.find(term.lower())),
        )
    )
    field_order = list(FIELD_WEIGHTS)
    matched_fields = tuple(
        sorted(hit_fields, key=lambda name: (-FIELD_WEIGHTS[name], field_order.index(name)))
    )
    return ScoredCapability(
        entry=entry,
        score=score,
        matched_terms=matched_terms,
        matched_fields=matched_fields,
    )


def _catalog_page(entries: Sequence[CapabilityEntry], limit: int) -> tuple[ScoredCapability, ...]:
    """目录前 `limit` 条（兜底与空目标共用；无命中信息）。

    Args:
        entries: 目录条目（稳定序）。
        limit: 条数上限。

    Returns:
        tuple[ScoredCapability, ...]: 零分卡列表。
    """
    return tuple(
        ScoredCapability(entry=entry, score=0, matched_terms=(), matched_fields=())
        for entry in entries[:limit]
    )


def recommend(
    goal: str,
    *,
    limit: int = 3,
    catalog: Sequence[CapabilityEntry] | None = None,
) -> GoalMatchResult:
    """规则推荐（纯函数，无 IO）。

    三种形态：

    - **命中**：≥1 条得分 ≥1 → 按 `(score 降序, plugin 优先, id 升序)` 取前
      `limit`，带命中理由；
    - **空目标**：`goal` 去空白后为空 → 返回目录前 `limit` 条，
      `matched=false, fallback=false`（AC-B4，不报错）；
    - **无命中兜底**：有目标但无人得分 → 目录前 `limit` 条 + 引导文案
      （AC-B3，不得空列表）。

    Args:
        goal: 用户目标（业务语言）。
        limit: 返回条数（端点层已校验 1..10，这里防御性夹紧）。
        catalog: 能力目录（None 时实时组装）。

    Returns:
        GoalMatchResult: 推荐结果。
    """
    entries = list(catalog) if catalog is not None else build_catalog()
    effective_limit = min(max(limit, 1), len(entries)) if entries else 0

    if not goal.strip():
        return GoalMatchResult(
            items=_catalog_page(entries, effective_limit),
            matched=False,
            fallback=False,
            fallback_hint="",
        )

    goal_lower = goal.lower()
    scored = [_score_entry(goal_lower, entry) for entry in entries]
    hits = [item for item in scored if item.score >= 1]
    if hits:
        hits.sort(
            key=lambda item: (
                -item.score,
                0 if item.entry.kind == "plugin" else 1,
                item.entry.id,
            )
        )
        return GoalMatchResult(
            items=tuple(hits[:effective_limit]),
            matched=True,
            fallback=False,
            fallback_hint="",
        )
    return GoalMatchResult(
        items=_catalog_page(entries, effective_limit),
        matched=False,
        fallback=True,
        fallback_hint=FALLBACK_HINT,
    )


__all__: list[str] = [
    "FALLBACK_HINT",
    "FIELD_WEIGHTS",
    "GoalMatchResult",
    "ScoredCapability",
    "recommend",
    "tokenize",
]
