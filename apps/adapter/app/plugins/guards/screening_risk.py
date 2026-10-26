"""简历评分表的行级质量门（PRD P0-P3-21 / QA TC-P3-HR21-01 / P1-10 声明化）。

判据：

1. **整张表必须有「风险」列**（容忍 `Risk` / `风险点` 这类别名）——
   **语义硬约束，不进 params**：缺列时逐行检查会一行都不报，于是这张表会以
   "全部通过"的姿态交到用户手里——漏判比误判更危险，所以缺列直接整表判失败；
2. 每一行的「风险」去空白后必须非空（**一次报全**所有违规行）；
3. **零行不判失败**（`allow_zero_rows`，默认 true）：没有简历就没有可判的行
   （`resumes` 是 `required=false`，"本次不做筛选"是合法输入），把"没行"
   当成"风险为空"会让没传简历的用户收到一条无从修改的报错。

「风险」列是"只夸不批"的唯一刹车——没有它，一张全绿的评分表会把不合格
候选人送进面试。PRD 原文「风险为空判该行失败重跑」，因此违规可重试
（唯一一道 `retryable=True` 的产物护栏）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.core.logging import get_logger
from app.plugins.guards.registry import register
from app.plugins.guards.table_parse import (
    column_count,
    markdown_table_rows,
    parse_csv_rows,
    pick_column,
)
from app.plugins.guards.types import GuardViolation

logger = get_logger(__name__)

#: 产物键（声明侧由 `GuardSpec.output_key` 声明；这里是规则的强制覆盖范围）
SCREENING_OUTPUT_KEY: Final[str] = "screening"

#: 「风险」列的表头候选。**必须容忍别名**：评分表的表头由模型产出、
#: 不是声明里写死的列名，只认字面「风险」会在模型写 `Risk` / `风险点` 时漏判
#: ——**漏判比误判更危险**，因为它让这张表"看起来验过了"。
#:
#: 匹配语义写死在本模块（**去空白 + 小写 + 子串包含**）：`风险` / `风险点` /
#: `Risk` / `Risks` 都能认出来。子串而非全等，是因为模型常写「风险点」
#: 「主要风险」这类更自然的表头。params 只允许收紧/扩充候选词表。
SCREENING_RISK_HEADERS: Final[tuple[str, ...]] = ("风险", "risk", "risks")

#: 违规时用来点名「是哪一份简历」的列（取不到就退回"第 N 行"）
SCREENING_ROW_ID_KEYS: Final[tuple[str, ...]] = ("姓名", "候选人", "name")

#: 零行是否放行（默认放行；置 false 是**收紧**，允许）
SCREENING_ALLOW_ZERO_ROWS: Final[bool] = True

#: 违规是否可重试（PRD：判该行失败重跑，模型换一次采样就可能补上风险）
SCREENING_RETRYABLE: Final[bool] = True

#: 违规时给用户的建议动作
SCREENING_OPERATOR_ACTION: Final[str] = "可以重试这一步"

#: 缺风险列时的整表判失败文案（硬编码语义的固定出口）
_MISSING_COLUMN_REASON: Final[str] = "评分表缺少「风险」列，没法逐份判断候选人的风险"


def _screening_text(content: bytes | None, inline_text: str) -> str:
    """取评分表原文（优先 `content` 字节，缺了用 `inline_text`）。

    Args:
        content: 产物字节。
        inline_text: 产物内联文本。

    Returns:
        str: 表原文（空串表示没有内容）。
    """
    if content:
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:  # pragma: no cover - 渲染器只产出 UTF-8
            return ""
    return inline_text or ""


def _screening_rows(content: bytes | None, inline_text: str) -> list[dict[str, str]]:
    """取评分表的行（CSV 或**模型直接产出的 markdown 表格**）。

    为什么先看 `content`：`content` 就是用户下载得到的那份表，校验对象必须与
    交付物一致（与排期表同一口径）。没有字节时回落到 `inline_text`，
    但**不因为回落就跳过校验**。

    为什么还要认 markdown：真实链路里 `understand` 的产物是**模型生成的文本**
    （`ServicesActionGateway._run_generate` 把 `data.content` 原样落成
    `inline_text`），而模型几乎总是用 markdown 表格交付评分表。只认 CSV 会把
    "表头形态不同"误判成"缺少「风险」列"，让每一次真实筛选都失败——
    误判和漏判一样会让用户不再信任这道门，只是方向相反。

    判定顺序：先按 CSV 解析，**解析出的列数 > 1** 说明确实是 CSV；否则再试
    markdown 表格；都不成立时把 CSV 的结果原样返回（交给缺列判据处理）。

    Args:
        content: 产物字节（优先）。
        inline_text: 产物内联文本（`content` 为空时用）。

    Returns:
        list[dict[str, str]]: 行列表（无可判行时返回空列表）。
    """
    text = _screening_text(content, inline_text)
    if not text.strip():
        return []
    csv_rows = parse_csv_rows(text.encode("utf-8"))
    if column_count(csv_rows) > 1:
        return csv_rows
    markdown_rows = markdown_table_rows(text)
    if markdown_rows:
        return markdown_rows
    return csv_rows


def _risk_column(rows: list[dict[str, str]], risk_headers: tuple[str, ...]) -> str | None:
    """找出「风险」列的**表头原文**（找不到返回 None）。

    表头来自模型产出的行字典，不是声明里的固定列名，因此按
    `risk_headers` 做「去空白 + 小写 + 子串包含」匹配（语义见模块头）。

    Args:
        rows: 评分表行。
        risk_headers: 风险列表头候选（子串匹配）。

    Returns:
        str | None: 命中的表头原文；整张表没有风险列时返回 None。
    """
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    for header in headers:
        normalized = str(header or "").strip().lower()
        if not normalized:
            continue
        if any(candidate in normalized for candidate in risk_headers):
            return header
    return None


def scan_screening(
    rows: list[dict[str, str]],
    *,
    risk_headers: tuple[str, ...] = SCREENING_RISK_HEADERS,
    row_id_keys: tuple[str, ...] = SCREENING_ROW_ID_KEYS,
) -> list[str]:
    """按「风险」列逐行判定，返回**业务语言**问题清单（空表示通过）。

    判据两条（PRD P0-P3-21）：

    1. 整张表必须有「风险」列（容忍别名，匹配语义写死在本模块）；
    2. 每一行的「风险」去空白后必须非空。

    **一次报全**所有违规行，不在第一条就 `return`：用户拿到的应该是一份
    "要补哪几行"的清单，而不是挤牙膏式的逐次重跑。

    Args:
        rows: 评分表行。
        risk_headers: 风险列表头候选（收紧/扩充词表用）。
        row_id_keys: 行标识列候选（点名到人用）。

    Returns:
        list[str]: 问题清单（每条都带可识别的行标识，用户能照着改）。
    """
    if not rows:
        return []
    risk_key = _risk_column(rows, risk_headers)
    if risk_key is None:
        return [_MISSING_COLUMN_REASON]
    problems: list[str] = []
    for index, row in enumerate(rows, start=1):
        if str(row.get(risk_key) or "").strip():
            continue
        who = pick_column(row, row_id_keys)
        where = f"第 {index} 行（{who}）" if who else f"第 {index} 行"
        problems.append(f"{where}的「风险」是空的，请补上这份简历的具体风险后再导出评分表")
    return problems


#: 声明参数允许的键
_ALLOWED_PARAM_KEYS: Final[frozenset[str]] = frozenset(
    {"risk_headers", "row_id_keys", "allow_zero_rows", "retryable", "operator_action"}
)


def _as_str_tuple(params: Mapping[str, object], key: str) -> tuple[str, ...]:
    """把声明参数里的非空字符串列表读成元组（缺失返回空元组 = 用缺省）。

    Args:
        params: 声明参数。
        key: 参数键。

    Returns:
        tuple[str, ...]: 字符串元组。

    Raises:
        ValueError: 类型不合法或空列表（空列表会关掉判据）。
    """
    raw = params.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple) or not raw:
        raise ValueError(f"参数 {key} 必须是非空列表（空列表会关掉判据）")
    if not all(isinstance(item, str) and item for item in raw):
        raise ValueError(f"参数 {key} 必须是非空字符串列表")
    return tuple(raw)


class _ScreeningRiskRule:
    """评分表「风险」行级质量门的声明式实现（`GuardRule` 协议）。"""

    name: Final[str] = "screening_risk"
    required_output_keys: Final[tuple[str, ...]] = (SCREENING_OUTPUT_KEY,)

    def validate_params(self, params: Mapping[str, object]) -> None:
        """校验声明参数：只允许收紧词表/列名/文案。

        「缺风险列整表判失败」写死在模块里，没有任何参数能关掉它；
        `allow_zero_rows=false` 是**收紧**（把零行也判失败），允许。

        Args:
            params: 声明里的 `guards[].params`。

        Raises:
            ValueError: 未知键或类型不合法。
        """
        unknown = sorted(set(params) - _ALLOWED_PARAM_KEYS)
        if unknown:
            raise ValueError(f"screening_risk 不认识这些参数：{unknown}")
        for key in ("risk_headers", "row_id_keys"):
            if key in params:
                _as_str_tuple(params, key)
        if "allow_zero_rows" in params and not isinstance(params["allow_zero_rows"], bool):
            raise ValueError("参数 allow_zero_rows 必须是布尔值")
        if "retryable" in params and not isinstance(params["retryable"], bool):
            raise ValueError("参数 retryable 必须是布尔值")
        if "operator_action" in params:
            action = params["operator_action"]
            if not isinstance(action, str) or not action.strip():
                raise ValueError("参数 operator_action 必须是非空文案")

    def check(
        self, *, text: str, content: bytes | None, params: Mapping[str, object]
    ) -> GuardViolation | None:
        """校验简历评分表：**每行「风险」非空**（PRD P0-P3-21 的一票否决项）。

        Args:
            text: 产物内联文本（`content` 缺失时按 CSV/markdown 解析）。
            content: 评分表 CSV 字节。
            params: 声明参数（缺省 = 内置基线）。

        Returns:
            GuardViolation | None: 违规返回违规记录；合规返回 None。
        """
        rows = _screening_rows(content, text or "")
        allow_zero_rows = bool(params.get("allow_zero_rows", SCREENING_ALLOW_ZERO_ROWS))
        if not rows:
            if allow_zero_rows:
                logger.info("plugin_screening_skipped", reason="no_rows")
                return None
            problems = ["评分表里一行数据都没有，没法判断候选人风险"]
        else:
            risk_headers = _as_str_tuple(params, "risk_headers") or SCREENING_RISK_HEADERS
            row_id_keys = _as_str_tuple(params, "row_id_keys") or SCREENING_ROW_ID_KEYS
            problems = scan_screening(rows, risk_headers=risk_headers, row_id_keys=row_id_keys)
        if not problems:
            return None
        logger.warning("plugin_screening_blocked", problems=list(problems))
        return GuardViolation(
            reason_text="；".join(problems),
            operator_action=str(params.get("operator_action") or SCREENING_OPERATOR_ACTION),
            retryable=bool(params.get("retryable", SCREENING_RETRYABLE)),
        )


register(_ScreeningRiskRule())


__all__: list[str] = [
    "SCREENING_ALLOW_ZERO_ROWS",
    "SCREENING_OPERATOR_ACTION",
    "SCREENING_OUTPUT_KEY",
    "SCREENING_RETRYABLE",
    "SCREENING_RISK_HEADERS",
    "SCREENING_ROW_ID_KEYS",
    "scan_screening",
]
