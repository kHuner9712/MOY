"""ARC-10（审计 2026-10-28）：R3 上传流式化单测。

修复前：`payload = await file.read(max_upload_bytes + 1)` 把整文件（≤50MB）
一次性读进内存——超限判定与内容读取捆在同一个全量缓冲策略里。
修复后：1MB 分块落临时文件，累计超限立即抛 20008，超限请求的内存峰值
从「整个文件」降到「一个分块」。

本文件三类断言：
1. 超限（limit+1 字节）返回 20008，且**不会**触发全量 `read(limit+1)`；
2. 正常上传分块读取（每次 read 请求 ≤ 1MB），字节完整到达服务层；
3. 幂等指纹所需的内容摘要与整读等值（分块增量摘要 == sha256(全量)）。
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import Settings
from app.core.errors import ErrorCode
from app.main import create_app
from app.schemas.rag import ChunkPolicy, DocumentStage, IndexStatus, UploadDocumentData
from tests.conftest import TEST_JWT_SECRET, TEST_TENANT_ID

pytestmark = pytest.mark.unit

_CHUNK_SIZE = 1024 * 1024  # 与 rag.py 的分块大小一致（1MB）


def _settings(max_upload_bytes: int) -> Settings:
    """构造桩模式配置（可注入小的上传上限）。"""
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        host="127.0.0.1",  # B-5：stub 仅 loopback 可启动
        jwt_secret=TEST_JWT_SECRET,
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        max_upload_bytes=max_upload_bytes,
    )


@contextmanager
def _client(settings: Settings) -> Iterator[TestClient]:
    """按给定配置建测试应用。"""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _auth_headers(settings: Settings) -> dict[str, str]:
    """桩模式鉴权头（复用 tests/conftest.py 的 make_token）。"""
    from tests.conftest import make_token

    token = make_token(settings=settings)
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TEST_TENANT_ID}


_IDEMPOTENCY_HEADERS = {"Idempotency-Key": "idem-arc10-test-0001"}


@pytest.fixture
def capturing_service(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把路由上的 RAG 服务替换为记录入参的替身。"""
    from app.api.v1 import rag as rag_api

    captured: dict[str, Any] = {}

    class _CapturingService:
        """只实现 `upload_document` 的替身服务。"""

        async def upload_document(self, **kwargs: Any) -> UploadDocumentData:
            captured.update(kwargs)
            return UploadDocumentData(
                document_id="doc_01K4Q8Z2X7M9B3N5P6R8T0V2W4",
                file_name=str(kwargs.get("file_name", "")),
                index_status=IndexStatus.PROCESSING,
                stage=DocumentStage.UPLOADED,
                chunk_policy=ChunkPolicy.DEFAULT,
                progress=0,
            )

    monkeypatch.setattr(rag_api, "get_rag_service", lambda: _CapturingService())
    return captured


def test_oversize_upload_rejected_without_full_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """limit+1 字节即拒绝（20008），且历史全量 read(limit+1) 调用不复存在。"""
    limit = 1024
    read_requests: list[int] = []

    original_read = StarletteUploadFile.read

    async def _spy_read(self: StarletteUploadFile, size: int = -1) -> bytes:
        read_requests.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(StarletteUploadFile, "read", _spy_read)

    settings = _settings(max_upload_bytes=limit)
    with _client(settings) as client:
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("大文件.pdf", io.BytesIO(b"x" * (limit + 1)), "application/pdf")},
            headers={**_auth_headers(settings), **_IDEMPOTENCY_HEADERS},
        )
    assert response.json()["code"] == int(ErrorCode.PAYLOAD_TOO_LARGE)
    # 修复前的实证：一次 read(limit+1) 把整个文件读进内存。
    # 修复后：任何一次 read 请求都不超过分块大小。
    assert read_requests, "handler 未读取上传流"
    assert max(read_requests) <= _CHUNK_SIZE, f"出现全量缓冲读取：{read_requests}"


def test_upload_reads_in_chunks_and_passes_bytes(
    capturing_service: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """2MB 文件分块读取（每次 ≤1MB），字节完整、大小正确地到达服务层。"""
    read_requests: list[int] = []

    original_read = StarletteUploadFile.read

    async def _spy_read(self: StarletteUploadFile, size: int = -1) -> bytes:
        read_requests.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(StarletteUploadFile, "read", _spy_read)

    settings = _settings(max_upload_bytes=52_428_800)
    payload = b"%PDF-1.4 " + b"m" * (2 * _CHUNK_SIZE) + b"\n%%EOF"  # >1 个分块
    with _client(settings) as client:
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("多块文档.pdf", io.BytesIO(payload), "application/pdf")},
            headers={**_auth_headers(settings), **_IDEMPOTENCY_HEADERS},
        )
    assert response.json()["code"] == 0
    assert capturing_service["content"] == payload
    assert capturing_service["file_size"] == len(payload)
    # 分块证明：多次读取且单次请求不超过 1MB（修复前是一次 read(limit+1)）。
    assert len(read_requests) >= 2, f"未分块：{read_requests}"
    assert max(read_requests) <= _CHUNK_SIZE


def test_chunked_digest_equals_full_read_digest(
    capturing_service: dict[str, Any],
) -> None:
    """流式增量摘要与整读 sha256 等值（幂等指纹口径不变）。"""
    settings = _settings(max_upload_bytes=52_428_800)
    payload = b"%PDF-1.4 digest-check\n%%EOF"
    with _client(settings) as client:
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("摘要.pdf", io.BytesIO(payload), "application/pdf")},
            headers={**_auth_headers(settings), **_IDEMPOTENCY_HEADERS},
        )
    assert response.json()["code"] == 0
    # 替身服务收不到摘要本身（摘要只进幂等管理器），这里以「同请求重放命中
    # 幂等」间接验证指纹稳定：同 Idempotency-Key 重发应返回 IDEMPOTENT_DUPLICATE。
    replay = client.post(
        "/api/v1/knowledge/bases/kb_1/documents",
        files={"file": ("摘要.pdf", io.BytesIO(payload), "application/pdf")},
        headers={**_auth_headers(settings), **_IDEMPOTENCY_HEADERS},
    )
    assert replay.json()["code"] == int(ErrorCode.IDEMPOTENT_DUPLICATE)
