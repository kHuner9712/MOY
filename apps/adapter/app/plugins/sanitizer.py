"""产物脱敏与文件渲染（阶段三 B19，`architecture-phase3.md` §3.5 / D-20）。

**三条不可妥协的口径**：

1. **落库前必过脱敏**：`mask_pii()` 掩码邮箱/手机/身份证；`assert_no_secret()`
   对凭据类键名直接拒绝（抛 `30003` + 告警），宁可任务失败也不把凭据写进产物。
2. **WF2 编辑后必须重跑**（B19 一票否决项）：用户改过的正文**不是**跳过脱敏的理由。
   否则任何人只要打开编辑器把手机号/密钥贴进产物，就能绕过整套 `ArtifactSanitizer`
   ——把"用户手工输入"当成可信输入，等于给合规开口子。
3. **`render_file` 是纯本地动作**：不读用户文件、不调下游、不计计量；`render_format`
   由**声明**给定，不接受调用方覆盖（`docx` 渲染器在 B20 引入，见 D-20）。

本模块不 import `app.services.*` / `httpx` / 数据库会话（§3.1 分层铁律）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 手机号（中国大陆，允许 `+86`/空格/短横线分隔）
_PHONE_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")

#: 邮箱
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

#: 身份证（18 位，末位可为 X）
_ID_CARD_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

#: 带 `Bearer` 前缀的令牌（漏网的直接抹掉整段）
_BEARER_PATTERN: Final[re.Pattern[str]] = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)

#: **键名**命中即视为凭据（值一律不进产物，§3.6 第 4 条）
SECRET_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|apikey|bearer|credential|private[_-]?key)",
    re.IGNORECASE,
)

#: 允许的渲染格式（`render_format` 只有这三个值，封闭集合）
RENDER_FORMATS: Final[tuple[str, ...]] = ("docx", "md", "csv")

#: 掩码占位（用户可见，业务语言，不含技术词汇）
MASK_PHONE: Final[str] = "138****0000"
MASK_EMAIL: Final[str] = "user***@example.com"
MASK_ID_CARD: Final[str] = "110101********1234"


@dataclass(frozen=True, slots=True)
class ArtifactBlob:
    """渲染后的文件内容（尚未落库）。

    Attributes:
        filename: 建议文件名（含扩展名）。
        content: 文件字节。
        content_type: MIME 类型（下载响应用）。
        render_format: 渲染格式（`docx` / `md` / `csv`）。
    """

    filename: str = ""
    content: bytes = b""
    content_type: str = "application/octet-stream"
    render_format: str = "md"

    @property
    def size_bytes(self) -> int:
        """文件大小（字节）。

        Returns:
            int: 字节数。
        """
        return len(self.content)


@dataclass(frozen=True, slots=True)
class SanitizeReport:
    """脱敏结果（供任务汇总与审计使用）。

    Attributes:
        text: 脱敏后的文本。
        masked: 命中并掩码的项数（手机 + 邮箱 + 身份证）。
        redacted_bearer: 被抹掉的 Bearer 令牌数。
    """

    text: str = ""
    masked: int = 0
    redacted_bearer: int = 0

    @property
    def changed(self) -> bool:
        """是否发生了任何替换。

        Returns:
            bool: 有替换返回 True。
        """
        return self.masked > 0 or self.redacted_bearer > 0


@dataclass(slots=True)
class ArtifactSanitizer:
    """产物脱敏器与文件渲染器（无状态，可安全复用）。

    Attributes:
        extra_mask_patterns: 追加的掩码正则（测试与私有化可注入，**不放开"不脱敏"**）。
    """

    extra_mask_patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # 脱敏
    # ------------------------------------------------------------------
    def sanitize(self, text: str) -> SanitizeReport:
        """对文本执行掩码（邮箱 / 手机 / 身份证 / Bearer 令牌）。

        替换顺序按"最长最具体优先"：身份证 → 手机 → 邮箱 → Bearer。
        先掩码身份证再掩码手机是必要的：18 位身份证里的 11 位连续数字
        会被手机号正则误伤（掩码后反而看不出是证件号）。

        Args:
            text: 原始文本。

        Returns:
            SanitizeReport: 脱敏结果与命中计数。
        """
        masked = 0
        result = text or ""

        result, hits = _ID_CARD_PATTERN.subn(MASK_ID_CARD, result)
        masked += hits
        result, hits = _PHONE_PATTERN.subn(MASK_PHONE, result)
        masked += hits
        result, hits = _EMAIL_PATTERN.subn(MASK_EMAIL, result)
        masked += hits
        result, bearer_hits = _BEARER_PATTERN.subn("Bearer ***", result)
        for pattern in self.extra_mask_patterns:
            result, hits = pattern.subn("***", result)
            masked += hits
        return SanitizeReport(text=result, masked=masked, redacted_bearer=bearer_hits)

    def mask_pii(self, text: str) -> str:
        """只取脱敏后的文本（`sanitize` 的简写）。

        Args:
            text: 原始文本。

        Returns:
            str: 脱敏后的文本。
        """
        return self.sanitize(text).text

    def assert_no_secret(self, payload: Any, *, where: str = "") -> None:
        """校验载荷里没有凭据键（**键名命中即拒绝**，不看值像不像凭据）。

        为什么不看值：值可以是任意随机串，"像不像密钥"无法判定；而键名是
        声明契约的一部分，`token` / `api_key` 这类键出现在产物里本身就是越界。

        Args:
            payload: 任意可嵌套结构（dict / list / str）。
            where: 诊断位置（只进日志与内部详情）。

        Raises:
            ApiError: 命中凭据键名（30003），并写 WARNING 日志供告警规则抓取。
        """
        hit = _find_secret_key(payload)
        if hit is None:
            return
        logger.warning("plugin_artifact_secret_rejected", where=where, key=hit)
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            internal_detail=f"artifact secret key rejected: {hit} at {where}",
        )

    # ------------------------------------------------------------------
    # 文件渲染
    # ------------------------------------------------------------------
    def render_file(
        self,
        *,
        render_format: str,
        name: str,
        payload: dict[str, Any],
        rows: list[dict[str, Any]] | None = None,
    ) -> ArtifactBlob:
        """按声明给定的格式渲染文件（纯本地）。

        Args:
            render_format: `docx` / `md` / `csv`（调用方只能传声明值）。
            name: 产物名称（用户可见，用作文件名主体）。
            payload: 渲染数据（`title` / `sections[{heading, body}]` / `content`）。
            rows: 表格数据（`csv` 用；也作为 `md` 的表格段落）。

        Returns:
            ArtifactBlob: 渲染结果。

        Raises:
            ApiError: 格式不在白名单（20001）。
        """
        normalized = (render_format or "md").strip().lower()
        if normalized not in RENDER_FORMATS:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"render_format={render_format} not in {RENDER_FORMATS}",
            )
        safe_name = _safe_filename(name or "artifact")
        if normalized == "csv":
            content = _render_csv(rows or [], payload)
            return ArtifactBlob(
                filename=f"{safe_name}.csv",
                content=content,
                content_type="text/csv; charset=utf-8",
                render_format="csv",
            )
        if normalized == "docx":
            blob = _render_docx(safe_name, payload, rows or [])
            if blob is not None:
                return blob
            # 渲染器未装配（B20 引入 `python-docx`）时如实降级为可下载文本，
            # **不伪造**一个打不开的 .docx（假回执比降级更糟）
            logger.warning("plugin_render_docx_unavailable", name=safe_name)
            content = _render_markdown(payload, rows or [])
            return ArtifactBlob(
                filename=f"{safe_name}.md",
                content=content,
                content_type="text/markdown; charset=utf-8",
                render_format="md",
            )
        content = _render_markdown(payload, rows or [])
        return ArtifactBlob(
            filename=f"{safe_name}.md",
            content=content,
            content_type="text/markdown; charset=utf-8",
            render_format="md",
        )

    @staticmethod
    def digest(text: str | bytes) -> str:
        """计算内容摘要（`content_sha256`，WF2 每次保存都重算）。

        Args:
            text: 文本或字节。

        Returns:
            str: 64 位十六进制 sha256。
        """
        payload = text.encode("utf-8") if isinstance(text, str) else text
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def zip_bundle(files: dict[str, str]) -> bytes:
        """把多个文本文件打包成 zip（代码文件产物的落点，纯本地）。

        Args:
            files: `文件名 → 文本`。

        Returns:
            bytes: zip 字节。
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in files.items():
                archive.writestr(_safe_filename(name, keep_suffix=True), text)
        return buffer.getvalue()


def _find_secret_key(payload: Any) -> str | None:
    """递归查找凭据键名。

    Args:
        payload: 任意结构。

    Returns:
        str | None: 命中的键名；未命中返回 None。
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and SECRET_KEY_PATTERN.search(key):
                return key
            found = _find_secret_key(value)
            if found is not None:
                return found
        return None
    if isinstance(payload, list | tuple | set):
        for item in payload:
            found = _find_secret_key(item)
            if found is not None:
                return found
    return None


def _safe_filename(name: str, *, keep_suffix: bool = False) -> str:
    """把产物名转成安全文件名（去掉路径分隔符与空白）。

    Args:
        name: 原始名称。
        keep_suffix: 是否保留原扩展名。

    Returns:
        str: 安全文件名。
    """
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("._") or "artifact"
    if keep_suffix:
        return cleaned[:120]
    return re.sub(r"\.[A-Za-z0-9]{1,8}$", "", cleaned)[:120] or "artifact"


def _render_markdown(payload: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    """渲染 Markdown 正文。

    Args:
        payload: 渲染数据。
        rows: 表格数据。

    Returns:
        bytes: UTF-8 字节。
    """
    lines: list[str] = []
    title = str(payload.get("title") or payload.get("name") or "")
    if title:
        lines.append(f"# {title}")
        lines.append("")
    content = str(payload.get("content") or "")
    if content:
        lines.append(content)
        lines.append("")
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "")
        body = str(section.get("body") or "")
        if heading:
            lines.append(f"## {heading}")
        if body:
            lines.append(body)
        lines.append("")
    if rows:
        headers = list(rows[0].keys())
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            lines.append("| " + " | ".join(str(row.get(key, "")) for key in headers) + " |")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _render_csv(rows: list[dict[str, Any]], payload: dict[str, Any]) -> bytes:
    """渲染 CSV（带 BOM，Excel 打开不乱码）。

    Args:
        rows: 表格数据。
        payload: 渲染数据（`columns` 可指定列顺序）。

    Returns:
        bytes: UTF-8-BOM 字节。
    """
    columns = [str(item) for item in (payload.get("columns") or [])]
    if not columns and rows:
        columns = list(rows[0].keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    return buffer.getvalue().encode("utf-8-sig")


def _render_docx(
    name: str, payload: dict[str, Any], rows: list[dict[str, Any]]
) -> ArtifactBlob | None:
    """尝试用 `python-docx` 渲染（B20 才引入依赖，缺依赖时返回 None）。

    Args:
        name: 安全文件名。
        payload: 渲染数据。
        rows: 表格数据。

    Returns:
        ArtifactBlob | None: 渲染结果；依赖未安装返回 None（由调用方降级）。
    """
    try:  # pragma: no cover - B20 引入依赖后由集成用例覆盖
        from docx import Document
    except ImportError:
        return None
    document = Document()
    title = str(payload.get("title") or "")
    if title:
        document.add_heading(title, level=1)
    content = str(payload.get("content") or "")
    if content:
        document.add_paragraph(content)
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        if section.get("heading"):
            document.add_heading(str(section["heading"]), level=2)
        if section.get("body"):
            document.add_paragraph(str(section["body"]))
    if rows:
        headers = list(rows[0].keys())
        table = document.add_table(rows=1, cols=len(headers))
        for index, header in enumerate(headers):
            table.rows[0].cells[index].text = header
        for row in rows:
            cells = table.add_row().cells
            for index, header in enumerate(headers):
                cells[index].text = str(row.get(header, ""))
    buffer = io.BytesIO()
    document.save(buffer)
    return ArtifactBlob(
        filename=f"{name}.docx",
        content=buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        render_format="docx",
    )


__all__: list[str] = [
    "MASK_EMAIL",
    "MASK_ID_CARD",
    "MASK_PHONE",
    "RENDER_FORMATS",
    "SECRET_KEY_PATTERN",
    "ArtifactBlob",
    "ArtifactSanitizer",
    "SanitizeReport",
]
