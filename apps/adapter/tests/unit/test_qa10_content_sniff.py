"""QA-10：R3 上传内容类型嗅探的端点级用例。

覆盖口径：
- 正向：pdf / docx / xlsx / md / txt 各一份**结构真实**的最小样本；
- 反向：改扩展名伪装（png→pdf、docx→txt、txt→pdf、普通 zip→docx、exe→txt）、
  空文件、截断 PDF（缺 `%%EOF`）、截断 DOCX（ZIP 中央目录不完整）、超限；
- 边界：正好等于上传上限、全零字节头。
- 顺序：超限仍先于类型嗅探（20008），不会因内容伪装改变错误码。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ErrorCode
from app.main import create_app
from app.schemas.rag import ChunkPolicy, DocumentStage, IndexStatus, UploadDocumentData
from app.services.upload_content import detect_content_kind
from tests.conftest import TEST_JWT_SECRET, TEST_TENANT_ID, make_token

pytestmark = pytest.mark.unit

_UPLOAD_PATH = "/api/v1/knowledge/bases/kb_1/documents"
_IDEMPOTENCY_HEADERS = {"Idempotency-Key": "idem-qa10-0001"}


def _settings(max_upload_bytes: int = 52_428_800) -> Settings:
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


def _headers(settings: Settings) -> dict[str, str]:
    """桩模式鉴权头。"""
    return {
        "Authorization": f"Bearer {make_token(settings=settings)}",
        "X-Tenant-Id": TEST_TENANT_ID,
        **_IDEMPOTENCY_HEADERS,
    }


@contextmanager
def _client(settings: Settings) -> Iterator[TestClient]:
    """按给定配置建测试应用。"""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """按给定目录构造 ZIP 字节。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _docx_bytes() -> bytes:
    """最小可识别的 DOCX（OOXML 容器含 `word/document.xml`）。"""
    return _zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<w:document/>",
        }
    )


def _xlsx_bytes() -> bytes:
    """最小可识别的 XLSX（OOXML 容器含 `xl/workbook.xml`）。"""
    return _zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
        }
    )


_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
_MD_BYTES = "# 员工手册\n\n年假需提前三个工作日申请。\n".encode()
_TXT_BYTES = "年假需提前三个工作日申请。".encode()

_SAMPLES: tuple[tuple[str, bytes], ...] = (
    ("员工手册.pdf", _PDF_BYTES),
    ("员工手册.docx", _docx_bytes()),
    ("员工手册.xlsx", _xlsx_bytes()),
    ("员工手册.md", _MD_BYTES),
    ("员工手册.txt", _TXT_BYTES),
)


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


def _post(client: TestClient, settings: Settings, file_name: str, payload: bytes) -> Any:
    """上传一份文件并返回响应 JSON。"""
    return client.post(
        _UPLOAD_PATH,
        files={"file": (file_name, io.BytesIO(payload), "application/octet-stream")},
        headers=_headers(settings),
    ).json()


@pytest.mark.parametrize(("file_name", "payload"), _SAMPLES)
def test_each_allowed_type_is_accepted(
    file_name: str,
    payload: bytes,
    capturing_service: dict[str, Any],
) -> None:
    """各允许类型真实样本通过校验，字节完整到达服务层。"""
    settings = _settings()
    with _client(settings) as client:
        body = _post(client, settings, file_name, payload)
    assert body["code"] == 0, body
    assert capturing_service["file_name"] == file_name
    assert capturing_service["content"] == payload


@pytest.mark.parametrize(
    ("file_name", "payload"),
    (
        ("伪装.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32),
        ("伪装.txt", _docx_bytes()),
        ("伪装.pdf", _TXT_BYTES),
        ("伪装.docx", _zip_bytes({"readme.txt": b"not ooxml"})),
        ("伪装.txt", b"MZ" + b"\x00" * 32),
    ),
)
def test_extension_content_mismatch_is_rejected(
    file_name: str,
    payload: bytes,
    capturing_service: dict[str, Any],
) -> None:
    """扩展名与真实内容不符（或无扩展名）一律 11002，且不触达服务层。"""
    settings = _settings()
    with _client(settings) as client:
        body = _post(client, settings, file_name, payload)
    assert body["code"] == int(ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED)
    assert "不符" in body["message"]
    assert not capturing_service, "伪装内容不应到达服务层"


@pytest.mark.parametrize("file_name", ("空文件.pdf", "空文件.txt"))
def test_empty_file_is_rejected(file_name: str, capturing_service: dict[str, Any]) -> None:
    """零字节文件按格式不支持拒收。"""
    settings = _settings()
    with _client(settings) as client:
        body = _post(client, settings, file_name, b"")
    assert body["code"] == int(ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED)
    assert body["message"] == "暂不支持该格式的文档"
    assert not capturing_service


@pytest.mark.parametrize(
    ("file_name", "payload"),
    (
        # PDF 头合法但缺结尾标记 → 截断
        ("截断.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"),
        # ZIP 被截断，中央目录不可读 → 截断
        ("截断.docx", _docx_bytes()[: len(_docx_bytes()) // 2]),
    ),
)
def test_truncated_file_is_rejected(
    file_name: str, payload: bytes, capturing_service: dict[str, Any]
) -> None:
    """截断文件按内容不符拒收。"""
    settings = _settings()
    with _client(settings) as client:
        body = _post(client, settings, file_name, payload)
    assert body["code"] == int(ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED)
    assert "不符" in body["message"]
    assert not capturing_service


def test_zero_byte_header_is_rejected(capturing_service: dict[str, Any]) -> None:
    """全零字节头不能冒充文本。"""
    settings = _settings()
    with _client(settings) as client:
        body = _post(client, settings, "零字节.txt", b"\x00" * 64)
    assert body["code"] == int(ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED)
    assert not capturing_service


def test_exact_limit_is_accepted(capturing_service: dict[str, Any]) -> None:
    """正好等于上限（1024 字节）通过；超 1 字节仍先报 20008。"""
    limit = 1024
    exact = b"a" * limit
    settings = _settings(max_upload_bytes=limit)
    with _client(settings) as client:
        ok = _post(client, settings, "边界.txt", exact)
        assert ok["code"] == 0, ok
        assert capturing_service["content"] == exact
        assert capturing_service["file_size"] == limit

        over = _post(client, settings, "超限.txt", b"a" * (limit + 1))
        assert over["code"] == int(ErrorCode.PAYLOAD_TOO_LARGE)


def test_oversize_wins_over_content_sniff(capturing_service: dict[str, Any]) -> None:
    """超限的伪装文件仍先被大小校验拦下（顺序不破）。"""
    settings = _settings(max_upload_bytes=1024)
    with _client(settings) as client:
        body = _post(client, settings, "伪装.pdf", b"x" * 4096)
    assert body["code"] == int(ErrorCode.PAYLOAD_TOO_LARGE)
    assert not capturing_service


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (_PDF_BYTES, "pdf"),
        (_docx_bytes(), "docx"),
        (_xlsx_bytes(), "xlsx"),
        (_zip_bytes({"a.txt": b"x"}), "zip"),
        (b"PK\x03\x04", "zip-truncated"),
        (_MD_BYTES, "text"),
        (b"\xff\xd8\xff", "binary"),
    ),
)
def test_detect_content_kind_returns_expected(payload: bytes, expected: str) -> None:
    """嗅探函数对每种结构给出口径内的结果。"""
    assert detect_content_kind(payload) == expected
