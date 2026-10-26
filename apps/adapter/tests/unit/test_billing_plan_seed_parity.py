"""套餐种子与内存种子的一致性验收（切片 2C-5a 第 1 项）。

**为什么需要这条**：`billing_plan` 的种子在 `deploy/init-schema-phase2.sql`
第 15 节，而内存后端另有一份 `default_plans()`。两处都"看起来对"时没人会发现
它们漂移——直到企业开通正式环境时看到的价格与本地演示不同。本用例把 SQL 当
**数据源**解析，与代码逐字段对账，任何一侧单方面改动都会红。

解析口径（三个坑，都在这里处理了）：

1. `VALUES` 区必须用 `ON CONFLICT` 或语句分号截断——否则 `ON CONFLICT (...)`
   会被当成第 4 行（行数变 4、末行只有 2 列）；
2. 每个元组起始必须重置缓冲区，否则列数会累积成 11/21/31；
3. `features` 是**跨行** JSONB 字面量（`'{"kb_count":3,\n ...}'::jsonb`），
   必须整段取出、剥掉 `::jsonb` 后缀再 `json.loads`，不能按行读。

对账范围：SQL 的 11 列逐列比；不在 SQL 里的两列（`currency` /
`quota_unit_label`）**显式断言仍取 dataclass 默认值**——这样将来 SQL 加列而
代码没跟上时，这条会红而不是静默漏过。
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.db.repositories.billing import PlanRecord, default_plans

pytestmark = pytest.mark.unit

#: 仓库根（`tests/unit/x.py` → `[0]unit [1]tests [2]adapter [3]apps [4]仓库根`）。
REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_FILE = REPO_ROOT / "deploy" / "init-schema-phase2.sql"

#: SQL 未覆盖、必须取 dataclass 默认值的列（列名 → 期望值）。
SQL_UNCOVERED_COLUMNS = {"currency": "CNY", "quota_unit_label": "字数额度"}

#: SQL 里需要按整数解析的列。
INT_COLUMNS = frozenset(
    {
        "price_per_seat_month_cents",
        "min_seats",
        "quota_units_per_seat_month",
        "trial_days",
        "sort_order",
    }
)


def _seed_statement(text: str) -> str:
    """切出 `billing_plan` 的 INSERT 语句（到 `ON CONFLICT` 或分号为止）。

    Args:
        text: 建库脚本全文。

    Returns:
        str: 仅含列名与 VALUES 区的语句片段。

    Raises:
        AssertionError: 脚本中找不到该 INSERT。
    """
    start = text.find("INSERT INTO billing_plan ")
    assert start >= 0, "init-schema-phase2.sql 里找不到 `INSERT INTO billing_plan`"
    tail = text[start:]
    stops = [
        match.start()
        for match in (re.search(r"\bON\s+CONFLICT\b", tail, re.IGNORECASE), re.search(r";", tail))
        if match is not None
    ]
    assert stops, "billing_plan 的 INSERT 语句没有终止符"
    return tail[: min(stops)]


def _column_names(statement: str) -> list[str]:
    """取出 INSERT 的列名序列。

    Args:
        statement: `_seed_statement` 的返回片段。

    Returns:
        list[str]: 列名（保持 SQL 中的书写顺序）。

    Raises:
        AssertionError: 列名列表无法定位。
    """
    match = re.search(
        r"INSERT\s+INTO\s+billing_plan\s*\((?P<columns>.*?)\)\s*VALUES",
        statement,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "未能定位 billing_plan 的列名列表"
    return [column.strip() for column in match.group("columns").split(",") if column.strip()]


def _split_top_level(body: str) -> list[str]:
    """按顶层逗号切分（忽略单引号内与括号内的逗号）。

    Args:
        body: 一个 VALUES 元组的内部文本。

    Returns:
        list[str]: 各列的字面量原文。
    """
    parts: list[str] = []
    buf = ""
    depth = 0
    in_quote = False
    index = 0
    while index < len(body):
        char = body[index]
        if in_quote:
            if char == "'":
                # SQL 用 '' 转义单引号，需跳过成对的那一个。
                if index + 1 < len(body) and body[index + 1] == "'":
                    buf += "''"
                    index += 2
                    continue
                in_quote = False
            buf += char
        elif char == "'":
            in_quote = True
            buf += char
        elif char in "({[":
            depth += 1
            buf += char
        elif char in ")}]":
            depth -= 1
            buf += char
        elif char == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += char
        index += 1
    if buf.strip():
        parts.append(buf.strip())
    return parts


def _value_groups(statement: str) -> list[list[str]]:
    """切出 VALUES 区的每个元组（每个元组是字面量列表）。

    Args:
        statement: `_seed_statement` 的返回片段。

    Returns:
        list[list[str]]: 逐行的字面量列表。

    Raises:
        AssertionError: 找不到 VALUES 区。
    """
    match = re.search(r"\bVALUES\b", statement, re.IGNORECASE)
    assert match is not None, "未能定位 VALUES 区"
    body = statement[match.end() :]
    groups: list[list[str]] = []
    buf = ""
    depth = 0
    in_quote = False
    index = 0
    while index < len(body):
        char = body[index]
        if in_quote:
            if char == "'":
                if index + 1 < len(body) and body[index + 1] == "'":
                    buf += "''"
                    index += 2
                    continue
                in_quote = False
            buf += char
        elif char == "'":
            in_quote = True
            buf += char
        elif char == "(":
            depth += 1
            if depth == 1:
                # 坑 2：每个元组起始重置缓冲区。
                buf = ""
                index += 1
                continue
            buf += char
        elif char == ")":
            depth -= 1
            if depth == 0:
                groups.append(_split_top_level(buf))
                buf = ""
                index += 1
                continue
            buf += char
        else:
            buf += char
        index += 1
    return groups


def _cast(column: str, literal: str) -> Any:
    """把 SQL 字面量解析成 Python 值。

    Args:
        column: 列名（决定解析方式）。
        literal: 字面量原文（可能带 `::jsonb` 之类的类型后缀）。

    Returns:
        Any: 解析后的值。

    Raises:
        AssertionError: 遇到未预期的字面量形态。
    """
    text = re.sub(r"::\s*[A-Za-z_]+(\s*\[\])?\s*$", "", literal.strip()).strip()
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        text = text[1:-1].replace("''", "'")
    elif not text:
        raise AssertionError(f"{column}: 空字面量")

    if column == "features":
        return json.loads(text)
    if column == "purchasable_quota":
        assert text.lower() in {"true", "false"}, f"purchasable_quota 非布尔：{text!r}"
        return text.lower() == "true"
    if column in INT_COLUMNS:
        return int(text)
    return text


def _parse_seed() -> tuple[list[str], list[dict[str, Any]]]:
    """解析 SQL 种子。

    Returns:
        tuple[list[str], list[dict[str, Any]]]: 列名序列与逐行字典。

    Raises:
        AssertionError: 建库脚本缺失。
    """
    assert SCHEMA_FILE.is_file(), f"建库脚本不存在：{SCHEMA_FILE}"
    statement = _seed_statement(SCHEMA_FILE.read_text(encoding="utf-8"))
    columns = _column_names(statement)
    rows: list[dict[str, Any]] = []
    for group in _value_groups(statement):
        assert len(group) == len(columns), (
            f"VALUES 行列数不符：{len(group)} 列 vs 列名 {len(columns)} 个——疑似截断或切分错误"
        )
        rows.append(
            {column: _cast(column, cell) for column, cell in zip(columns, group, strict=True)}
        )
    return columns, rows


def test_seed_matches_default_plans() -> None:
    """SQL 种子与 `default_plans()` 逐字段等价。"""
    columns, rows = _parse_seed()

    # ① 列覆盖：SQL 列必须都是 PlanRecord 字段，且未覆盖的列恰好是那两个默认列。
    field_names = {field.name for field in dataclasses.fields(PlanRecord)}
    unknown_columns = sorted(set(columns) - field_names)
    assert not unknown_columns, f"SQL 出现 PlanRecord 未定义的列：{unknown_columns}"
    uncovered = field_names - set(columns)
    assert uncovered == set(SQL_UNCOVERED_COLUMNS), (
        f"SQL 未覆盖的列发生变化：实际 {sorted(uncovered)}，"
        f"期望 {sorted(SQL_UNCOVERED_COLUMNS)}——代码或 SQL 有一侧新增了字段"
    )

    # ② 行数与套餐标识。
    plans = default_plans()
    assert len(rows) == len(plans) == 3, f"种子行数不一致：SQL {len(rows)} / 代码 {len(plans)}"
    assert {row["plan_code"] for row in rows} == {plan.plan_code for plan in plans}

    # ③ 逐行逐列对账（按 plan_code 配对，不依赖书写顺序）。
    by_code: dict[str, PlanRecord] = {plan.plan_code: plan for plan in plans}
    for row in rows:
        plan = by_code[row["plan_code"]]
        for column in columns:
            expected = getattr(plan, column)
            assert row[column] == expected, (
                f"{row['plan_code']}.{column} 漂移：SQL={row[column]!r} 代码={expected!r}"
            )

    # ④ SQL 未覆盖的列必须仍是默认值（防"SQL 加列而代码没跟上"）。
    defaults = {field.name: field for field in dataclasses.fields(PlanRecord)}
    for plan in plans:
        for column, expected_default in SQL_UNCOVERED_COLUMNS.items():
            declared = defaults[column].default
            assert declared == expected_default, (
                f"{column} 的 dataclass 默认值变了：{declared!r}（期望 {expected_default!r}）"
            )
            actual = getattr(plan, column)
            assert actual == expected_default, (
                f"{plan.plan_code}.{column} 不再是默认值：{actual!r}（期望 {expected_default!r}）"
            )
