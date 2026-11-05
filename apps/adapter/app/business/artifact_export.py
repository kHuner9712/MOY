"""产物按需导出（2026-11-05 B2：表格 → xlsx）。

**为什么是"按需导出"而不是"再存一版"**：表格类产物的权威内容仍是 CSV
（`plugin_artifact.inline_text` 的既有契约，`resolve_content_type` 与下载链路都
依赖它）。导出 xlsx 只是同一份数据的另一种表示，**不改任务状态、不新增版本、
不刷新 `content_sha256`**——否则用户每点一次「下载 Excel」就多一版历史，
版本历史会从"编辑记录"退化成"下载记录"。

导出入口只有两个：内容代理端点（`/files/artifacts/{token}/export`）与 WF2 编辑
（`render_format="xlsx"`）。本模块只负责「产物行 → xlsx 字节」这一段，不碰
数据库、不碰响应头，便于单测把边界钉死。
"""

from __future__ import annotations

import csv
import io
from typing import Final

from app.db.repositories.artifact_repo import ArtifactRecord
from app.plugins.renderer import RENDER_CONTENT_TYPES, ArtifactBlob, FileRenderer

#: xlsx 的规范 MIME（与渲染器同源，避免两处写死后漂移）
XLSX_CONTENT_TYPE: Final[str] = RENDER_CONTENT_TYPES["xlsx"]

#: CSV 的规范 MIME（带 charset 参数，仓储层归一化后的写法）
CSV_CONTENT_TYPE: Final[str] = RENDER_CONTENT_TYPES["csv"]


def is_xlsx_exportable(record: ArtifactRecord) -> bool:
    """判断产物是否可导出为 xlsx（端点 415 的唯一判据）。

    Args:
        record: 产物行。

    Returns:
        bool: 可导出返回 True。
    """
    if record.content_type == XLSX_CONTENT_TYPE and bool(record.content):
        return True
    if record.type == "table":
        return bool(record.inline_text.strip())
    if record.content_type == CSV_CONTENT_TYPE:
        return bool(record.content or record.inline_text.strip())
    return False


def table_rows_from_csv(text: str) -> list[dict[str, str]]:
    """把 CSV 文本解析成表格行（容忍 BOM、空行与列数不齐）。

    `csv.reader` 而不是 `DictReader`：列数不齐时 `DictReader` 会把缺失列写成
    `None`/省略键，表格就会出现"有的行少一列"的错位；这里显式按首行列数对齐。

    单列输入（如检索产物的 `- 片段` 列表）按「内容」一列处理：它不是 CSV，
    但用户点「下载 Excel」时仍然应该拿到一个可读的表，而不是以第一行为表头的
    伪表格。

    Args:
        text: CSV 文本（可带 UTF-8 BOM）。

    Returns:
        list[dict[str, str]]: 表格行（首行为表头时不含表头）。
    """
    raw = text.lstrip("\ufeff")
    if not raw.strip():
        return []
    reader = csv.reader(io.StringIO(raw))
    try:
        all_rows = list(reader)
    except csv.Error:
        return []
    all_rows = [row for row in all_rows if any(cell.strip() for cell in row)]
    if not all_rows:
        return []
    if len(all_rows[0]) <= 1 and not any("," in cell for cell in all_rows[0]):
        return [{"内容": row[0]} for row in all_rows if row]
    headers = [header.strip() or f"列{index + 1}" for index, header in enumerate(all_rows[0])]
    rows: list[dict[str, str]] = []
    for raw_row in all_rows[1:]:
        rows.append(
            {
                header: (raw_row[index].strip() if index < len(raw_row) else "")
                for index, header in enumerate(headers)
            }
        )
    return rows


def build_xlsx_export(record: ArtifactRecord) -> ArtifactBlob | None:
    """把产物行转换成 xlsx（不可导出/无数据时返回 None）。

    Args:
        record: 产物行。

    Returns:
        ArtifactBlob | None: xlsx 渲染结果；不可导出返回 None。
    """
    if record.content_type == XLSX_CONTENT_TYPE and record.content:
        return ArtifactBlob(
            filename=_export_filename(record),
            content=record.content,
            content_type=XLSX_CONTENT_TYPE,
            render_format="xlsx",
        )
    if record.type == "table":
        source = record.inline_text
    elif record.content_type == CSV_CONTENT_TYPE and record.content:
        source = record.content.decode("utf-8-sig", errors="replace")
    elif record.content_type == CSV_CONTENT_TYPE:
        source = record.inline_text
    else:
        # 纯文本/文档类产物没有表格语义：不把正文糊成一张单列表格（端点应 415）
        return None
    rows = table_rows_from_csv(source)
    if not rows:
        return None
    return FileRenderer().render(
        render_format="xlsx",
        name=record.name or record.output_key,
        payload={"title": record.name},
        rows=rows,
    )


def _export_filename(record: ArtifactRecord) -> str:
    """生成导出文件名（去掉已有扩展名后补 `.xlsx`）。

    Args:
        record: 产物行。

    Returns:
        str: 形如 `发布排期表.xlsx`。
    """
    name = (record.name or record.output_key or "artifact").strip()
    for suffix in (".xlsx", ".csv", ".md", ".docx", ".txt"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return f"{name or 'artifact'}.xlsx"
