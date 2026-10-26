"""表格解析纯函数（P1-10，从 `engine` 迁出）。

排期表与评分表的护栏都要「把用户拿到的那份表读回来」——CSV 或模型直接
产出的 markdown 表格。解析逻辑与 `StepOutcome` / `ArtifactDraft` 无关，
放在纯函数模块里：规则模块与引擎共用同一份解析，改一处两边一致。
"""

from __future__ import annotations

import csv
import io


def parse_csv_rows(content: bytes) -> list[dict[str, str]]:
    """把 CSV 字节解析回行（护栏要读的是**已渲染出来**的那份表）。

    为什么不从 `params` 里取原始行：用户可见内容就是导出的 CSV，
    校验对象必须与实际交付物一致；只校验"打算写什么"会放过渲染期的偏差。

    Args:
        content: CSV 字节（带 BOM 与否都可）。

    Returns:
        list[dict[str, str]]: 行列表（解析不出表头时返回空列表）。
    """
    if not content:
        return []
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:  # pragma: no cover - 渲染器只产出 UTF-8
        return []
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({str(key or ""): str(value or "") for key, value in row.items()})
    return rows


def pick_column(row: dict[str, str], keys: tuple[str, ...]) -> str:
    """按候选键名取列值（命中即返回，去空白）。

    Args:
        row: 行数据。
        keys: 候选键名。

    Returns:
        str: 列值；未命中返回空串。
    """
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def column_count(rows: list[dict[str, str]]) -> int:
    """取行里的最大列数（用来判断 CSV 解析是否退化成一列）。

    Args:
        rows: 行列表。

    Returns:
        int: 最大列数（空列表返回 0）。
    """
    return max((len(row) for row in rows), default=0)


def markdown_table_rows(text: str) -> list[dict[str, str]]:
    """把 markdown 表格解析成行字典（不是表格时返回空列表）。

    只认以 `|` 开头的行；首行当表头，紧随的 `| --- | --- |` 分隔行跳过，
    其余行按表头顺序映射成 `{列名: 单元格}`。单元格数少于表头时补空串
    （缺列不能变成"这一行不存在"）。

    Args:
        text: 含 markdown 表格的文本。

    Returns:
        list[dict[str, str]]: 行列表。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return []
    header = split_markdown_row(lines[0])
    if len(header) < 2:
        return []
    body = lines[1:]
    if body and is_markdown_separator(body[0]):
        body = body[1:]
    rows: list[dict[str, str]] = []
    for line in body:
        cells = split_markdown_row(line)
        if not cells:
            continue
        rows.append(
            {
                name: (cells[index] if index < len(cells) else "")
                for index, name in enumerate(header)
            }
        )
    return rows


def split_markdown_row(line: str) -> list[str]:
    """按 `|` 切分 markdown 表格的一行（去掉首尾竖线后逐格去空白）。

    Args:
        line: 表格行原文。

    Returns:
        list[str]: 单元格列表（空行返回空列表）。
    """
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    if not text.strip():
        return []
    return [cell.strip() for cell in text.split("|")]


def is_markdown_separator(line: str) -> bool:
    """判断是否 `| --- | :---: |` 这类表头分隔行。

    Args:
        line: 表格行原文。

    Returns:
        bool: 是分隔行返回 True。
    """
    cells = split_markdown_row(line)
    if not cells:
        return False
    return all(cell and set(cell) <= set("-: ") for cell in cells)


__all__: list[str] = [
    "column_count",
    "is_markdown_separator",
    "markdown_table_rows",
    "parse_csv_rows",
    "pick_column",
    "split_markdown_row",
]
