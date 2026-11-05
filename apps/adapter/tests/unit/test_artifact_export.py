"""产物导出的纯函数边界（2026-11-05 B2）。

只测"产物行 → xlsx 字节"这一段，不碰 HTTP、不碰数据库：接口契约在
`tests/integration/test_artifact_export_preview.py`。
"""

from __future__ import annotations

import uuid

import pytest

from app.business.artifact_export import (
    CSV_CONTENT_TYPE,
    XLSX_CONTENT_TYPE,
    build_xlsx_export,
    is_xlsx_exportable,
    table_rows_from_csv,
)
from app.db.repositories.artifact_repo import ArtifactRecord
from app.plugins.renderer import FileRenderer

pytestmark = pytest.mark.unit

#: 测试租户
TENANT = "tnt_export_unit"


def _record(**overrides: object) -> ArtifactRecord:
    """构造产物行（默认为表格）。

    Args:
        **overrides: 覆盖项。

    Returns:
        ArtifactRecord: 产物行。
    """
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "artifact_id": f"art_{uuid.uuid4().hex[:10]}",
        "task_id": "task_export_unit",
        "step_id": "step_1",
        "output_key": "schedule",
        "type": "table",
        "name": "发布排期表",
        "inline_text": "平台,内容\n小红书,种草\n视频号,演示\n",
        "size_bytes": 20,
        "content_sha256": uuid.uuid4().hex,
        "content_type": CSV_CONTENT_TYPE,
    }
    values.update(overrides)
    return ArtifactRecord(**values)  # type: ignore[arg-type]


def test_rows_parse_strips_bom_and_ragged_columns() -> None:
    """BOM 要剥掉；列数不齐的行不许错位（缺列补空串）。"""
    rows = table_rows_from_csv("\ufeff平台,内容,负责人\n小红书,种草\n视频号,演示,李四\n")
    assert rows == [
        {"平台": "小红书", "内容": "种草", "负责人": ""},
        {"平台": "视频号", "内容": "演示", "负责人": "李四"},
    ]


def test_rows_parse_skips_blank_lines() -> None:
    """空行与全空白行不产出空记录（否则 Excel 里会出现空洞）。"""
    rows = table_rows_from_csv("平台,内容\n\n小红书,种草\n   \n")
    assert len(rows) == 1


def test_rows_parse_empty_text_returns_empty() -> None:
    """空文本 → 空列表（调用方据此 415，而不是生成空工作簿）。"""
    assert table_rows_from_csv("") == []
    assert table_rows_from_csv("   \n") == []


def test_rows_parse_single_column_falls_back_to_content_column() -> None:
    """检索产物的 `- 片段` 列表不是 CSV，但应导出成单列可读表。"""
    rows = table_rows_from_csv("- 第一条\n- 第二条\n")
    assert rows == [{"内容": "- 第一条"}, {"内容": "- 第二条"}]


def test_rows_parse_quoted_csv() -> None:
    """带引号的字段（内部含逗号）交给 csv 模块处理，不得手工 split。"""
    rows = table_rows_from_csv('主题,说明\n"采购,验收","含,逗号"\n')
    assert rows == [{"主题": "采购,验收", "说明": "含,逗号"}]


def test_exportable_matrix() -> None:
    """可导出判据：真 xlsx / 表格 / CSV；纯文本不能。"""
    assert is_xlsx_exportable(_record(type="table", content_type=CSV_CONTENT_TYPE)) is True
    assert is_xlsx_exportable(_record(type="table", inline_text="", content_type="")) is False
    assert (
        is_xlsx_exportable(
            _record(type="file", content_type=XLSX_CONTENT_TYPE, content=b"PK\x03\x04")
        )
        is True
    )
    assert (
        is_xlsx_exportable(
            _record(type="file", content_type=CSV_CONTENT_TYPE, content=b"a,b\n1,2\n")
        )
        is True
    )
    assert (
        is_xlsx_exportable(
            _record(type="text", content_type="text/markdown; charset=utf-8", inline_text="正文")
        )
        is False
    )


def test_build_table_xlsx_has_union_columns() -> None:
    """表格导出的列取并集：不同行缺列时仍保留该列。"""
    record = _record(
        type="table",
        inline_text="平台,内容\n小红书,种草,说明\n视频号\n",
    )
    blob = build_xlsx_export(record)
    assert blob is not None
    assert blob.render_format == "xlsx"
    assert blob.filename == "发布排期表.xlsx"
    assert FileRenderer.is_openable_xlsx(blob.content) is True


def test_build_csv_file_uses_real_content_bytes() -> None:
    """CSV 文件类产物要读 `content`（真字节），不是 `inline_text` 的摘要文本。"""
    record = _record(
        type="file",
        name="排期表.csv",
        inline_text="这是给用户看的预览摘要",
        content="平台,内容\n小红书,种草\n".encode(),
        content_type=CSV_CONTENT_TYPE,
    )
    blob = build_xlsx_export(record)
    assert blob is not None
    assert blob.filename == "排期表.xlsx"
    assert FileRenderer.is_openable_xlsx(blob.content) is True


def test_build_xlsx_passthrough_keeps_bytes() -> None:
    """已经是 xlsx 的产物原样返回（不重复渲染，保字节一致）。"""
    original = FileRenderer().render(
        render_format="xlsx", name="源表", payload={}, rows=[{"姓名": "张三"}]
    )
    record = _record(
        type="file",
        name="源表.xlsx",
        content=original.content,
        content_type=XLSX_CONTENT_TYPE,
        inline_text="",
    )
    blob = build_xlsx_export(record)
    assert blob is not None
    assert blob.content == original.content
    assert blob.filename == "源表.xlsx"


def test_build_returns_none_for_plain_text() -> None:
    """纯文本产物不硬凑表格（端点应回 415）。"""
    record = _record(
        type="text", name="方案", inline_text="正文", content_type="text/markdown; charset=utf-8"
    )
    assert build_xlsx_export(record) is None


def test_build_returns_none_for_empty_table() -> None:
    """空表格不产出空工作簿。"""
    assert build_xlsx_export(_record(type="table", inline_text=" \n")) is None
