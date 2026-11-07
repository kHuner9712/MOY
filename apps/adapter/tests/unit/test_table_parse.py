"""表格解析纯函数的直接用例（H-03，2026-11-07）。

审计结论「`plugins/guards/table_parse.py` 零测试」是按模块名检索得出的，
实际它被 `schedule_timeline` / `screening_risk` 间接覆盖。本文件补的是
**解析器本身的边界**：BOM、非法编码、缺列、非表格文本、分隔行判定 ——
这些在护栏用例里不会被单独触发，出错时表现为"校验悄悄放过"。
"""

from __future__ import annotations

from app.plugins.guards.table_parse import (
    column_count,
    is_markdown_separator,
    markdown_table_rows,
    parse_csv_rows,
    pick_column,
    split_markdown_row,
)


def test_parse_csv_rows_strips_bom_and_keeps_quoted_cells() -> None:
    """UTF-8 BOM 不污染首列名，带引号的逗号保持在同一格。"""
    content = '\ufeff姓名,岗位,备注\n"张三","内容运营","含,逗号"\n'.encode("utf-8")
    rows = parse_csv_rows(content)

    assert rows == [{"姓名": "张三", "岗位": "内容运营", "备注": "含,逗号"}]


def test_parse_csv_rows_empty_input_returns_empty_list() -> None:
    """空字节不算表，返回空列表（护栏据此判"没有可校验内容"）。"""
    assert parse_csv_rows(b"") == []
    assert parse_csv_rows(b"\n") == []


def test_parse_csv_rows_invalid_utf8_is_treated_as_no_table() -> None:
    """非 UTF-8 字节不抛异常，按"没有表"处理。"""
    assert parse_csv_rows(b"\xff\xfe\x00\x00") == []


def test_parse_csv_rows_missing_cells_become_empty_strings() -> None:
    """列数少于表头时补空串，不产生 None（下游 `pick_column` 依赖 str）。"""
    rows = parse_csv_rows(b"a,b,c\n1,2\n")
    assert rows == [{"a": "1", "b": "2", "c": ""}]


def test_markdown_table_rows_maps_header_and_skips_separator() -> None:
    """首行表头 + 分隔行跳过 + 数据行按表头映射。"""
    text = "| 日期 | 平台 |\n| --- | --- |\n| 11-01 | 公众号 |\n| 11-02 | 视频号 |"
    rows = markdown_table_rows(text)

    assert rows == [
        {"日期": "11-01", "平台": "公众号"},
        {"日期": "11-02", "平台": "视频号"},
    ]


def test_markdown_table_rows_pads_ragged_rows() -> None:
    """缺列补空串（缺列不等于"这一行不存在"）。"""
    text = "| 日期 | 平台 | 备注 |\n| --- | :---: | --- |\n| 11-01 | 公众号 |"
    rows = markdown_table_rows(text)

    assert rows == [{"日期": "11-01", "平台": "公众号", "备注": ""}]


def test_markdown_table_rows_requires_real_table_shape() -> None:
    """非表格文本、单列"表格"、只有表头，都返回空列表。"""
    assert markdown_table_rows("这是一段普通文字\n没有竖线") == []
    assert markdown_table_rows("| 只有一列 |") == []
    assert markdown_table_rows("| 日期 | 平台 |") == []


def test_markdown_table_without_separator_is_still_parsed() -> None:
    """缺分隔行时按"首行表头 + 其余数据"解析（模型直出的简化表格）。"""
    text = "| 日期 | 平台 |\n| 11-01 | 公众号 |"
    assert markdown_table_rows(text) == [{"日期": "11-01", "平台": "公众号"}]


def test_is_markdown_separator_accepts_alignment_markers() -> None:
    """`| --- | :---: | ---: |` 是分隔行；含文字的不是。"""
    assert is_markdown_separator("| --- | :---: | ---: |") is True
    assert is_markdown_separator("| --- | 日期 |") is False
    assert is_markdown_separator("") is False


def test_split_markdown_row_trims_edges_and_cells() -> None:
    """首尾竖线去掉、逐格去空白；空行返回空列表。"""
    assert split_markdown_row("|  日期 | 平台 |") == ["日期", "平台"]
    assert split_markdown_row("") == []
    # 全空单元格保留为空串（"这一格是空的"与"没有这一格"语义不同）
    assert split_markdown_row("|  |  |") == ["", ""]


def test_pick_column_returns_first_non_blank_candidate() -> None:
    """候选键按顺序命中第一个非空值；全空/缺键返回空串。"""
    row = {"日期": "  ", "时间": " 11-01 ", "start": "x"}
    assert pick_column(row, ("日期", "时间")) == "11-01"
    assert pick_column(row, ("日期", "缺失")) == ""
    assert pick_column({}, ("日期",)) == ""


def test_column_count_returns_widest_row_or_zero() -> None:
    """列数取最宽行；空列表返回 0（护栏据此判"退化成一列"）。"""
    assert column_count([{"a": "1"}, {"a": "1", "b": "2", "c": "3"}]) == 3
    assert column_count([]) == 0
