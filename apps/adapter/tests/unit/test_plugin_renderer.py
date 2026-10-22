"""`render_file` 渲染器单测（阶段三 B20，`architecture-phase3.md` D-20 / §7）。

钉住四件事：

1. **docx 是真 docx**：能解压出 `word/document.xml`（P0-P3-23 ③ 的唯一判据，
   扩展名不算数）；
2. **`render_format` 由声明给定**：`render_declared()` 的签名里没有格式参数，
   声明是 `docx` 就必须交 docx，调用方改不了；
3. **缺依赖时如实降级**：宁可交一份能打开的 md，也不伪造一个打不开的 .docx；
4. **不重复实现**：`ArtifactSanitizer.render_file` 与 `FileRenderer.render`
   是同一份实现（前者委托后者），两处行为必须逐字一致。
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.core.errors import ApiError, ErrorCode
from app.plugins.renderer import (
    DOCX_DOCUMENT_PART,
    RENDER_FORMATS,
    ArtifactBlob,
    FileRenderer,
    normalize_render_format,
    safe_filename,
)
from app.plugins.sanitizer import ArtifactSanitizer
from app.plugins.schema import StepSpec


def _spec(render_format: str | None, *, seq: int = 1) -> StepSpec:
    """构造一个 `render_file` 步骤声明。

    Args:
        render_format: 导出格式。
        seq: 序号。

    Returns:
        StepSpec: 步骤声明。
    """
    payload: dict[str, object] = {
        "seq": seq,
        "title": "导出文件",
        "action": "render_file",
        "produces": ["document"],
        "on_failure": "abort",
    }
    if render_format is not None:
        payload["render_format"] = render_format
    return StepSpec(**payload)  # type: ignore[arg-type]


def test_render_docx_is_real_docx_with_document_part() -> None:
    """docx 产物能解压出 `word/document.xml`，且正文非空。"""
    renderer = FileRenderer()
    blob = renderer.render(
        render_format="docx",
        name="行政公文",
        payload={
            "title": "关于设备采购的通知",
            "content": "现将有关事项通知如下。",
            "sections": [{"heading": "一、范围", "body": "适用于全体员工。"}],
        },
        rows=[{"项目": "金额", "内容": "按公司标准执行"}],
    )
    assert blob.render_format == "docx"
    assert blob.filename.endswith(".docx")
    assert blob.is_docx is True
    assert FileRenderer.is_openable_docx(blob.content) is True
    with zipfile.ZipFile(BytesIO(blob.content)) as archive:
        assert DOCX_DOCUMENT_PART in archive.namelist()
        assert len(archive.read(DOCX_DOCUMENT_PART)) > 0


def test_render_declared_takes_format_from_declaration_only() -> None:
    """格式只来自声明：声明 docx → 交 docx；声明 csv → 交 csv。"""
    renderer = FileRenderer()
    docx_blob = renderer.render_declared(
        _spec("docx"), name="公文", payload={"title": "通知", "content": "正文"}
    )
    csv_blob = renderer.render_declared(
        _spec("csv"), name="排期表", payload={"columns": ["平台"]}, rows=[{"平台": "公众号"}]
    )
    assert docx_blob.render_format == "docx"
    assert csv_blob.render_format == "csv"


def test_render_declared_refuses_missing_format() -> None:
    """声明缺格式 → 20001（**不静默回落 md**，否则"公文必须是 docx"成了空话）。"""
    renderer = FileRenderer()
    with pytest.raises(ApiError) as excinfo:
        renderer.render_declared(object(), name="x", payload={})
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)


def test_render_rejects_unknown_format() -> None:
    """白名单外的格式一律 20001。"""
    with pytest.raises(ApiError) as excinfo:
        FileRenderer().render(render_format="pdf", name="x", payload={})
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)


def test_render_docx_degrades_honestly_when_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖缺失 → 交可打开的 md，**不伪造打不开的 .docx**。"""
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "docx":
            raise ImportError("blocked in test")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _blocked)
    blob = FileRenderer().render(
        render_format="docx", name="公文", payload={"title": "通知", "content": "正文"}
    )
    assert blob.render_format == "md"
    assert blob.filename.endswith(".md")
    assert FileRenderer.is_openable_docx(blob.content) is False


def test_is_openable_docx_rejects_fake_suffix_content() -> None:
    """把扩展名改成 .docx 骗不过判据（扩展名谁都能改）。"""
    assert FileRenderer.is_openable_docx(b"not a zip at all") is False
    assert FileRenderer.is_openable_docx(b"") is False


def test_csv_has_bom_and_markdown_has_title() -> None:
    """CSV 带 BOM（Excel 不乱码）、md 带标题（下载内容可读）。"""
    renderer = FileRenderer()
    csv_blob = renderer.render(
        render_format="csv",
        name="排期",
        payload={"columns": ["平台", "时间"]},
        rows=[{"平台": "公众号", "时间": "2026-11-01 10:00"}],
    )
    assert csv_blob.content.startswith(b"\xef\xbb\xbf")
    assert csv_blob.content.decode("utf-8-sig").splitlines()[0] == "平台,时间"
    md_blob = renderer.render(
        render_format="md", name="提纲", payload={"title": "面试提纲", "content": "正文"}
    )
    assert "面试提纲" in md_blob.content.decode("utf-8")


def test_bundle_is_readable_zip() -> None:
    """代码文件产物是标准 zip，文件路径保持原样。"""
    payload = FileRenderer.render_bundle({"src/a.ts": "export const a = 1;"})
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        assert archive.namelist() == ["src_a.ts"]


def test_sanitizer_delegates_to_renderer() -> None:
    """脱敏器的 `render_file` 与渲染器是同一份实现（输出逐字一致）。"""
    sanitizer = ArtifactSanitizer()
    payload = {"title": "录用通知", "content": "正文"}
    from_sanitizer = sanitizer.render_file(render_format="md", name="录用通知", payload=payload)
    from_renderer = FileRenderer().render(render_format="md", name="录用通知", payload=payload)
    assert from_sanitizer == from_renderer


def test_normalize_and_safe_filename() -> None:
    """格式规范与文件名清洗：未知格式返回空串，路径分隔符被抹平。"""
    assert normalize_render_format(" DOCX ") == "docx"
    assert normalize_render_format("pdf") == ""
    assert normalize_render_format(None) == ""
    assert set(RENDER_FORMATS) == {"docx", "md", "csv"}
    assert safe_filename("../etc/passwd") == "etc_passwd"
    assert safe_filename("报告.docx") == "报告"
    assert safe_filename("报告.docx", keep_suffix=True) == "报告.docx"
    assert isinstance(ArtifactBlob(), ArtifactBlob)
