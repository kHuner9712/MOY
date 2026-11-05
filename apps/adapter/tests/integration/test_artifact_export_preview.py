"""产物导出端点的接口契约（2026-11-05 B2）。

**为什么要有接口级用例**：导出是"加性 API"，真正的约束不在渲染器，而在
「令牌 → 限流 → 产物行 → 格式判定 → 响应头」这条链路。单测直接调
`build_xlsx_export` 会绕过令牌与开关，等于没测到回退路径。

**边界**：本文件用内存后端，不验证 LibreOffice（本机无 soffice，见
`test_office_preview.py` 的 mock 口径与交付文档的部署验收步骤）。
"""

from __future__ import annotations

import io
import uuid
import zipfile
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.artifact_token import make_token
from app.core.config import Settings
from app.db.repositories.artifact_repo import (
    ArtifactRecord,
    get_artifact_store,
    reset_artifact_store,
)
from app.main import create_app
from app.plugins.renderer import DOCX_DOCUMENT_PART, XLSX_REQUIRED_PARTS

pytestmark = pytest.mark.integration

#: 本条用例的租户（内存后端进程内共享，用随机后缀隔离）
TENANT_ID = f"tnt_export_{uuid.uuid4().hex[:8]}"

#: 排期表产物（表格类：`inline_text` 即 CSV）
SCHEDULE_CSV = (
    "\ufeff平台,内容方向,发布时间,负责人\n"
    "小红书,品牌种草笔记,周一 10:00,张三\n"
    "视频号,产品功能演示,周三 19:30,李四\n"
    "公众号,深度案例拆解,周五 12:00,王五\n"
)


def _settings(**overrides: Any) -> Settings:
    """构造测试配置。

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
        "jwt_secret": "artifact-export-test-secret-32-chars",
        "ratelimit_enabled": False,
        "business_store": "memory",
    }
    values.update(overrides)
    return Settings(**values)


def _record(**overrides: Any) -> ArtifactRecord:
    """构造产物行（默认是排期表）。

    Args:
        **overrides: 覆盖项。

    Returns:
        ArtifactRecord: 产物行。
    """
    values: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "artifact_id": f"art_{uuid.uuid4().hex[:12]}",
        "task_id": f"task_{uuid.uuid4().hex[:8]}",
        "step_id": "step_schedule",
        "output_key": "schedule",
        "type": "table",
        "name": "发布排期表",
        "inline_text": SCHEDULE_CSV,
        "preview_text": "平台,内容方向",
        "size_bytes": len(SCHEDULE_CSV),
        "content_sha256": uuid.uuid4().hex,
        "content_type": "text/csv; charset=utf-8",
    }
    values.update(overrides)
    return ArtifactRecord(**values)


async def _seed(record: ArtifactRecord) -> None:
    """把产物写进内存单例存储（端点读的是同一个单例）。

    Args:
        record: 产物行。
    """
    await get_artifact_store(_settings()).add_artifacts([record])


def _token(record: ArtifactRecord, settings: Settings | None = None) -> str:
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
        settings=settings or _settings(),
    )


async def test_export_table_artifact_returns_real_xlsx() -> None:
    """表格产物导出为 xlsx：真工作簿、attachment、文件名带 .xlsx。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/export?format=xlsx")

    assert response.status_code == 200
    assert (
        response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["content-disposition"].startswith("attachment;")
    assert ".xlsx" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.content
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = set(archive.namelist())
        for part in XLSX_REQUIRED_PARTS:
            assert part in names, f"xlsx 缺少部件 {part}"
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
    assert "发布排期表" in workbook


async def test_export_does_not_create_new_version() -> None:
    """按需导出**不落新版本**（版本历史不能被下载动作污染）。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        first = await client.get(f"/files/artifacts/{token}/export?format=xlsx")
        second = await client.get(f"/files/artifacts/{token}/export?format=xlsx")

    assert first.status_code == 200 and second.status_code == 200
    stored = await get_artifact_store(settings).get(TENANT_ID, record.artifact_id)
    assert stored is not None
    assert stored.version == 1
    assert len(stored.version_history or []) <= 1


async def test_export_rejects_unknown_format() -> None:
    """只支持 xlsx；其它格式是 400（不是静默降级）。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/export?format=pdf")

    assert response.status_code == 400


async def test_export_switch_off_returns_404() -> None:
    """开关关闭时端点等同不存在（回退到"只给原格式"）。"""
    reset_artifact_store()
    record = _record()
    await _seed(record)
    settings = _settings(artifact_xlsx_enabled=False)
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/export?format=xlsx")

    assert response.status_code == 404


async def test_export_invalid_token_returns_404() -> None:
    """坏令牌统一 404（不区分伪造/过期/不存在）。"""
    reset_artifact_store()
    settings = _settings()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get("/files/artifacts/not-a-token/export?format=xlsx")

    assert response.status_code == 404


async def test_export_unsupported_artifact_type_returns_415() -> None:
    """纯文本产物没有表格数据 → 415（不产出空工作簿）。"""
    reset_artifact_store()
    record = _record(
        type="text",
        name="方案",
        inline_text="这是一段方案正文，没有表格。",
        content_type="text/markdown; charset=utf-8",
    )
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}/export?format=xlsx")

    assert response.status_code == 415


async def test_download_route_still_serves_docx_after_export_route_added() -> None:
    """新增子路由不得抢走 `/{token}` 的匹配（路由顺序回归）。"""
    reset_artifact_store()
    record = _record(
        type="file",
        name="行政公文",
        output_key="document",
        content=b"PK\x03\x04docx-bytes",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        inline_text="公文正文预览",
    )
    await _seed(record)
    settings = _settings()
    token = _token(record, settings)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/files/artifacts/{token}")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert DOCX_DOCUMENT_PART.encode() not in response.content  # 走的是文件字节而非文档解析
    assert response.content == record.content
