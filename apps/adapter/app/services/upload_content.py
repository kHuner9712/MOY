"""QA-10：上传内容类型嗅探。

**为什么需要**：此前 R3 只按扩展名判类型（`file_name.rsplit(".", 1)[-1]`），
把 `.exe` 改名成 `.pdf` 就能通过校验，字节再被透传到底座解析。
本模块按**文件头魔数**判定真实类型，扩展名与内容不符一律拒收（`11002`）。

**白名单来源**：`app/registry/profile_routes.yaml` 的
`allowed_document_extensions`（pdf/docx/xlsx/md/txt），不在此处另立一套，
避免"代码白名单"与"注册表白名单"漂移。

**判定口径**：
- `pdf`：头部 `%PDF-`，且尾部存在 `%%EOF`（截断的 PDF 视为内容不完整）；
- `docx` / `xlsx`：ZIP 容器（`PK\\x03\\x04`）且分别含 `word/document.xml` /
  `xl/workbook.xml`（仅读目录，不解压，避免 ZIP 炸弹）；
- `md` / `txt`：纯文本（头部无二进制魔数、无控制字符），二者内容无法区分，
  统一按 `text` 判定；
- 其它（含损坏的 ZIP、截断流）：`binary` / `zip-truncated`，一律视为不符。

**错误语义**：复用既有 `11002 DOCUMENT_FORMAT_UNSUPPORTED`——扩展名不在白名单
沿用默认文案「暂不支持该格式的文档」；扩展名合法但内容不符时覆盖为业务文案，
`internal_detail` 只记录扩展名与探测结果，**不含文件路径**。
"""

from __future__ import annotations

import io
import zipfile
from typing import Final

from app.core.errors import ApiError, ErrorCode

#: 嗅探窗口（头部字节数）。仅用于文本/二进制启发式判定，不参与全量解析。
SNIFF_BYTES: Final[int] = 8192

#: 内容与扩展名不符时的用户可见文案（业务语言，无技术词）。
MESSAGE_CONTENT_MISMATCH: Final[str] = "文件内容与扩展名不符，请重新上传正确格式的文件"

#: PDF 尾部完整性标记的搜索窗口。
PDF_TAIL_BYTES: Final[int] = 4096

#: 已知二进制文件头（命中即判 binary，防止 `.txt` 伪装可执行文件/图片）。
_BINARY_MAGICS: Final[tuple[tuple[bytes, str], ...]] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"\x7fELF", "elf"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),
    (b"Rar!\x1a\x07", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"\x1f\x8b", "gzip"),
    (b"MZ", "exe"),
    (b"\xca\xfe\xba\xbe", "java-class"),
)

#: 扩展名 → 允许的探测结果集合（与注册表白名单一一对应）。
_EXPECTED_KINDS: Final[dict[str, frozenset[str]]] = {
    "pdf": frozenset({"pdf"}),
    "docx": frozenset({"docx"}),
    "xlsx": frozenset({"xlsx"}),
    "md": frozenset({"text"}),
    "txt": frozenset({"text"}),
}

#: 文本判定允许出现的控制字符（制表/换行/垂直制表/换页/回车）。
_TEXT_CONTROLS: Final[frozenset[int]] = frozenset({0x09, 0x0A, 0x0B, 0x0C, 0x0D})


def _extension_of(file_name: str) -> str:
    """取小写扩展名（无扩展名返回空串）。

    Args:
        file_name: 文件名。

    Returns:
        str: 小写扩展名（不含点）。
    """
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def _zip_kind(payload: bytes) -> str:
    """按 OOXML 目录判定 ZIP 容器的真实类型。

    Args:
        payload: 完整文件字节（校验过大小上限，可安全在内存中打开）。

    Returns:
        str: `docx` / `xlsx` / `zip`（普通 ZIP 或缺少 OOXML 目录）/ `zip-truncated`。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = frozenset(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return "zip-truncated"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return "zip"


def _looks_like_text(head: bytes) -> bool:
    """头部字节是否为纯文本（无二进制魔数、无控制字符）。

    Args:
        head: 文件头字节（不超过 `SNIFF_BYTES`）。

    Returns:
        bool: 是否按纯文本处理。
    """
    return all(byte in _TEXT_CONTROLS or 0x20 <= byte <= 0x7E or byte >= 0x80 for byte in head)


def detect_content_kind(payload: bytes) -> str:
    """按文件头/容器结构判定真实内容类型。

    Args:
        payload: 文件字节内容。

    Returns:
        str: `pdf` / `docx` / `xlsx` / `text` / `binary` / `zip` / `zip-truncated`。
    """
    head = payload[:SNIFF_BYTES]
    if head.startswith(b"%PDF-"):
        if b"%%EOF" in payload[-PDF_TAIL_BYTES:]:
            return "pdf"
        return "pdf-truncated"
    if head.startswith(b"PK\x03\x04"):
        return _zip_kind(payload)
    for magic, _name in _BINARY_MAGICS:
        if head.startswith(magic):
            return "binary"
    if _looks_like_text(head):
        return "text"
    return "binary"


def validate_upload_content(
    *,
    file_name: str,
    payload: bytes,
    allowed_extensions: tuple[str, ...],
) -> None:
    """校验扩展名白名单与内容真实类型是否一致。

    校验顺序固定为「扩展名 → 空文件 → 内容嗅探」：扩展名不合法时沿用默认文案，
    内容不符时才给出「内容与扩展名不符」的细分文案。

    Args:
        file_name: 文件名（取扩展名）。
        payload: 文件字节内容。
        allowed_extensions: 注册表声明的允许扩展名（小写，不含点）。

    Raises:
        ApiError: 格式不支持（11002）——扩展名不在白名单、空文件或内容不符。
    """
    extension = _extension_of(file_name)
    if extension not in allowed_extensions:
        raise ApiError(
            ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED,
            internal_detail=f"extension not allowed: {extension}",
        )
    if not payload:
        raise ApiError(ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED, internal_detail="empty file")

    detected = detect_content_kind(payload)
    expected = _EXPECTED_KINDS.get(extension, frozenset())
    if detected not in expected:
        raise ApiError(
            ErrorCode.DOCUMENT_FORMAT_UNSUPPORTED,
            message=MESSAGE_CONTENT_MISMATCH,
            internal_detail=f"content mismatch: extension={extension} detected={detected}",
        )
