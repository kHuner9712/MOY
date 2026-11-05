"""预览响应的安全头回归锁定（2026-11-05 B2）。

**为什么单独一个文件**：预览链路和下载链路的响应头**语义相反**——

- 下载：`attachment` + `CSP: sandbox`（内容是外部生成的，浏览器不许渲染）；
- 预览：`inline` + `application/pdf` + 允许同源内嵌 + **绝不能带 `sandbox`**
  （Chromium 里 PDF 阅读器是插件，sandbox 会把它整个禁掉，用户只看到一片空白）。

两条链路都碰 `/files/artifacts/`，最危险的不是"漏加头"，而是后来有人
「顺手把预览对齐下载链路」多写一行 `sandbox`——那时 CI 全绿、单测全绿，
只有真机点预览才发现打不开。本文件就是那条防线：既逐条锁定预览该有的头，
也**显式断言不含 `sandbox`**，并同时锁定下载链路仍必须带 `sandbox`。

**验证边界**：转换器用替身 runner 返回固定 PDF，不验证 LibreOffice 真机效果
（见 `test_office_preview.py` 与交付文档的部署验收步骤）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.artifact_token import make_token
from app.core.config import Settings
from app.db.repositories.artifact_repo import (
    ArtifactRecord,
    get_artifact_store,
    reset_artifact_store,
)
from app.main import create_app
from app.plugins.renderer import RENDER_CONTENT_TYPES
from app.services.office_preview import PDF_MAGIC, OfficePreviewConverter

pytestmark = pytest.mark.integration

#: 本条用例的租户（内存后端进程内共享，用随机后缀隔离）
TENANT_ID = f"tnt_preview_{uuid.uuid4().hex[:8]}"

#: 替身转换器产出的 PDF（含魔法头）
SAMPLE_PDF = PDF_MAGIC + b"1.7\n%\xe2\xe3\xcf\xd3\n"

#: docx 的 MIME（From 渲染器常量，避免手写漂移）
DOCX_TYPE = RENDER_CONTENT_TYPES["docx"]


def _settings(**overrides: Any) -> Settings:
    """构造测试配置（预览开关默认打开）。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    values: dict[str, Any] = {
        "env": "test",
        "deploy_mode": "private",
        "downstream_enabled": False,
        "auth_mode": "stub",
        "jwt_secret": "artifact-preview-test-secret-32",
        "ratelimit_enabled": False,
        "business_store": "memory",
        "office_preview_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def _record(**overrides: Any) -> ArtifactRecord:
    """构造产物行（默认是 docx 文件产物）。

    Args:
        **overrides: 覆盖项。

    Returns:
        ArtifactRecord: 产物行。
    """
    values: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "artifact_id": f"art_{uuid.uuid4().hex[:12]}",
        "task_id": f"task_{uuid.uuid4().hex[:8]}",
        "step_id": "step_doc",
        "output_key": "document",
        "type": "file",
        "name": "行政公文",
        "content": b"PK\x03\x04fake-docx-bytes",
        "inline_text": "公文正文预览",
        "size_bytes": 20,
        "content_sha256": uuid.uuid4().hex,
        "content_type": DOCX_TYPE,
    }
    values.update(overrides)
    return ArtifactRecord(**values)


def _app_with_preview(settings: Settings) -> FastAPI:
    """构造应用并挂上一个「永远成功」的替身转换器。

    Args:
        settings: 配置实例。

    Returns:
        FastAPI: 应用实例。
    """

    async def runner(args: list[str], cwd: str) -> tuple[int, str, str]:
        from pathlib import Path

        (Path(args[args.index("--outdir") + 1]) / "source.pdf").write_bytes(SAMPLE_PDF)
        return 0, "", ""

    app = create_app(settings)
    app.state.office_preview_converter = OfficePreviewConverter(settings, runner=runner)
    return app


def _token(record: ArtifactRecord, settings: Settings) -> str:
    """签发下载令牌。

    Args:
        record: 产物行。
        settings: 配置实例。

    Returns:
        str: 令牌。
    """
    return make_token(
        tenant_id=record.tenant_id,
        artifact_id=record.artifact_id,
        version=record.version,
        settings=settings,
    )


async def _seed(record: ArtifactRecord) -> None:
    """把产物写进内存单例存储。

    Args:
        record: 产物行。
    """
    await get_artifact_store(_settings()).add_artifacts([record])


async def test_preview_response_headers_are_locked() -> None:
    """预览成功响应：inline + PDF + nosniff + SAMEORIGIN + frame-ancestors，且无 sandbox。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_preview(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/preview")

    assert response.status_code == 200
    headers = response.headers

    # 1) 浏览器就地打开（不是落地文件）
    assert headers["content-disposition"] == "inline"
    # 2) 明确声明 PDF（浏览器才走自带阅读器）
    assert headers["content-type"] == "application/pdf"
    # 3) 不许嗅探
    assert headers["x-content-type-options"] == "nosniff"
    # 4) 允许同源页面内嵌、挡住第三方站点
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["content-security-policy"] == "frame-ancestors 'self'"
    # 5) 关键负向断言：绝不能有 sandbox（PDF 阅读器是插件，sandbox 会禁掉它）
    assert "sandbox" not in headers["content-security-policy"].lower()
    assert "sandbox" not in headers.get("x-frame-options", "").lower()
    # 6) 缓存口径：进程内/浏览器私有缓存，不许中间代理缓存
    assert headers["cache-control"] == "private, max-age=300"
    # 7) 返回的确实是 PDF（不是 0 字节/半截内容）
    assert response.content.startswith(PDF_MAGIC)


async def test_download_response_keeps_attachment_and_sandbox() -> None:
    """对照组：下载链路仍必须是 `attachment` + `sandbox`（两条链路不许对齐）。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_preview(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "inline" not in response.headers["content-disposition"]


async def test_preview_switch_off_returns_503() -> None:
    """开关关闭时端点明确 503（而不是 404 让前端以为自己拼错了 URL）。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings(office_preview_enabled=False)
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/preview")

    assert response.status_code == 503
    assert "预览功能未开启" in response.text
    assert "application/pdf" not in response.headers.get("content-type", "")


async def test_preview_invalid_token_returns_404() -> None:
    """坏令牌统一 404（不区分伪造/过期/不存在）。"""
    reset_artifact_store()
    settings = _settings()

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_preview(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get("/files/artifacts/not-a-token/preview")

    assert response.status_code == 404


async def test_preview_unsupported_artifact_type_returns_415() -> None:
    """pptx/纯文本不预览（镜像不装 impress），明确 415 而不是硬转。"""
    reset_artifact_store()
    record = _record(
        type="file",
        name="演示文稿",
        output_key="slides",
        content=b"PK\x03\x04fake-pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_preview(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/preview")

    assert response.status_code == 415
    assert "暂不支持在线预览" in response.text


async def test_preview_table_artifact_converts_csv_first() -> None:
    """表格产物（CSV 内容）先转 xlsx 再转 PDF（源后缀必须是 .xlsx）。"""
    reset_artifact_store()
    record = _record(
        type="table",
        name="发布排期表",
        output_key="schedule",
        content=b"",
        inline_text="平台,内容\n小红书,种草\n",
        content_type="text/csv; charset=utf-8",
    )
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)
    suffixes: list[str] = []

    async def runner(args: list[str], cwd: str) -> tuple[int, str, str]:
        from pathlib import Path

        suffixes.append(Path(args[-1]).suffix)
        (Path(args[args.index("--outdir") + 1]) / "source.pdf").write_bytes(SAMPLE_PDF)
        return 0, "", ""

    app = create_app(settings)
    app.state.office_preview_converter = OfficePreviewConverter(settings, runner=runner)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/preview")

    assert response.status_code == 200
    assert suffixes == [".xlsx"]
    assert response.content.startswith(PDF_MAGIC)


async def test_preview_conversion_failure_maps_to_502() -> None:
    """转换失败透传业务状态码与文案（前端能展示"请下载后查看"）。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async def failing(args: list[str], cwd: str) -> tuple[int, str, str]:
        return 1, "", "boom"

    app = create_app(settings)
    app.state.office_preview_converter = OfficePreviewConverter(settings, runner=failing)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/preview")

    assert response.status_code == 502
    assert "请下载后查看" in response.text
