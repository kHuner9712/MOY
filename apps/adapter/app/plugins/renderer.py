"""`render_file` 渲染器（阶段三 B20，`architecture-phase3.md` §3.2 / §7 / D-20）。

**为什么要有独立渲染器**：`render_file` 是"写产物"的本地原语，与"读用户文件"
（`personal.file.read`）**无耦合**（§11 冲突 12）。把渲染逻辑收在一个模块里，
既让"格式只有三种"成为封闭集合，也让 **docx 只由平台生成**这一条（D-20）
有唯一的落点——插件目录里没有、也不允许有第二处能产出 docx 的代码。

**三条硬约束**：

1. **`render_format` 由声明给定**：`render_declared()` 只读 `StepSpec.render_format`，
   **签名里没有格式参数**，因此调用方（宿主 / 网关 / 插件）在类型层面就无法覆盖它。
   `render()` 保留显式格式入参，仅供 WF2 产物编辑这类"用户明确要求改格式"的路径复用。
2. **docx 由平台生成**（D-20）：`python-docx` 是**已审核的平台依赖**，在本模块内
   **延迟导入**（与 `sanitizer` 同一体例）——插件声明既不感知格式实现，也拿不到这个库。
   依赖缺失时**如实降级为 `.md`**，绝不伪造一个打不开的 `.docx`（假回执比降级更糟）。
3. **纯本地、不计量、不读用户文件**：本模块不 import `httpx`、不碰数据库、不碰
   `app.services.*`（§3.1 分层铁律）。

格式与产物类型的对应（声明侧封闭，运行期不再猜）：

| `render_format` | 扩展名 | MIME | 典型产物 |
| --- | --- | --- | --- |
| `docx` | `.docx` | OOXML | 公文、Offer 导出（P0-P3-23 ③） |
| `csv` | `.csv` | `text/csv`（带 BOM） | 发布排期表、简历筛选评分表 |
| `md` | `.md` | `text/markdown` | 方案、提纲、降级文本 |
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 允许的渲染格式（**封闭三种**，与 `StepSpec.render_format` 的字面量一致）
RENDER_FORMATS: Final[tuple[str, ...]] = ("docx", "md", "csv")

#: 格式 → 扩展名
RENDER_EXTENSIONS: Final[dict[str, str]] = {
    "docx": ".docx",
    "md": ".md",
    "csv": ".csv",
}

#: 格式 → MIME
RENDER_CONTENT_TYPES: Final[dict[str, str]] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}

#: docx 内部必须存在的部件（P0-P3-23 ③ 可解压判定）
DOCX_DOCUMENT_PART: Final[str] = "word/document.xml"

#: 文件名最大长度（含扩展名）
MAX_FILENAME_LENGTH: Final[int] = 120

#: 文件名兜底
DEFAULT_FILENAME: Final[str] = "artifact"


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

    @property
    def is_docx(self) -> bool:
        """是否为真正的 docx（而非降级后的文本）。

        Returns:
            bool: 渲染格式为 `docx` 返回 True。
        """
        return self.render_format == "docx"


class FileRenderer:
    """文件渲染器（无状态，可安全复用）。

    实例不带任何可配置项：格式只来自声明或用户明确指定的编辑请求，
    没有"默认格式"可被环境变量或开关悄悄改掉。
    """

    # ------------------------------------------------------------------
    # 声明驱动入口（**唯一允许插件路径使用的入口**）
    # ------------------------------------------------------------------
    def render_declared(
        self,
        spec: Any,
        *,
        name: str,
        payload: dict[str, Any],
        rows: list[dict[str, Any]] | None = None,
    ) -> ArtifactBlob:
        """按**声明**给定的格式渲染（不接受调用方覆盖）。

        签名里刻意没有 `render_format` 参数：格式的唯一来源是
        `StepSpec.render_format`。任何"让调用方顺手改个格式"的需求都必须
        改声明（即改插件版本），不能靠请求参数开口子。

        Args:
            spec: 步骤声明（只要求具备 `render_format` 属性）。
            name: 产物名称（用户可见，用作文件名主体）。
            payload: 渲染数据（`title` / `sections[{heading, body}]` / `content`）。
            rows: 表格数据。

        Returns:
            ArtifactBlob: 渲染结果。

        Raises:
            ApiError: 声明未给出格式或格式不在白名单（20001）。
        """
        declared = normalize_render_format(getattr(spec, "render_format", ""))
        if not declared:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail="render_file step 未声明 render_format",
            )
        return self.render(
            render_format=declared,
            name=name,
            payload=payload,
            rows=rows,
        )

    # ------------------------------------------------------------------
    # 显式入口（WF2 编辑等"用户明确要求改格式"的路径）
    # ------------------------------------------------------------------
    def render(
        self,
        *,
        render_format: str,
        name: str,
        payload: dict[str, Any],
        rows: list[dict[str, Any]] | None = None,
    ) -> ArtifactBlob:
        """按给定格式渲染文件（纯本地）。

        Args:
            render_format: `docx` / `md` / `csv`。
            name: 产物名称（用户可见，用作文件名主体）。
            payload: 渲染数据（`title` / `sections[{heading, body}]` / `content`）。
            rows: 表格数据（`csv` 用；也作为 `md` / `docx` 的表格段落）。

        Returns:
            ArtifactBlob: 渲染结果。

        Raises:
            ApiError: 格式不在白名单（20001）。
        """
        normalized = normalize_render_format(render_format)
        if not normalized:
            if isinstance(render_format, str) and render_format.strip():
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    internal_detail=f"render_format={render_format} not in {RENDER_FORMATS}",
                )
            # 完全没给格式（WF2 编辑的历史路径）才回落 md；给了个不认识的值一律报错，
            # 否则"用户要 pdf、系统悄悄给了 md"就是另一种假回执。
            normalized = "md"
        safe_name = safe_filename(name or DEFAULT_FILENAME)
        table = rows or []
        if normalized == "csv":
            return ArtifactBlob(
                filename=f"{safe_name}{RENDER_EXTENSIONS['csv']}",
                content=render_csv(table, payload),
                content_type=RENDER_CONTENT_TYPES["csv"],
                render_format="csv",
            )
        if normalized == "docx":
            blob = render_docx(safe_name, payload, table)
            if blob is not None:
                return blob
            # 依赖未装配时**如实降级**为可下载文本：不伪造打不开的 .docx
            logger.warning("plugin_render_docx_unavailable", name=safe_name)
            return ArtifactBlob(
                filename=f"{safe_name}{RENDER_EXTENSIONS['md']}",
                content=render_markdown(payload, table),
                content_type=RENDER_CONTENT_TYPES["md"],
                render_format="md",
            )
        return ArtifactBlob(
            filename=f"{safe_name}{RENDER_EXTENSIONS['md']}",
            content=render_markdown(payload, table),
            content_type=RENDER_CONTENT_TYPES["md"],
            render_format="md",
        )

    # ------------------------------------------------------------------
    # 代码包（zip）
    # ------------------------------------------------------------------
    @staticmethod
    def render_bundle(files: dict[str, str]) -> bytes:
        """把多个文本文件打包成 zip（代码文件产物的落点，纯本地）。

        Args:
            files: `文件名 → 文本`。

        Returns:
            bytes: zip 字节。
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in files.items():
                archive.writestr(safe_filename(name, keep_suffix=True), text)
        return buffer.getvalue()

    @staticmethod
    def is_openable_docx(content: bytes) -> bool:
        """校验字节确实是可解压出 `word/document.xml` 的 docx。

        验收口径（P0-P3-23 ③）不看扩展名：扩展名谁都能改，**能不能解压出正文部件**
        才是"真 docx"的判据。

        Args:
            content: 文件字节。

        Returns:
            bool: 是结构完整且含正文部件的 docx 返回 True。
        """
        if not content:
            return False
        buffer = io.BytesIO(content)
        if not zipfile.is_zipfile(buffer):
            return False
        try:
            with zipfile.ZipFile(buffer) as archive:
                if DOCX_DOCUMENT_PART not in archive.namelist():
                    return False
                body = archive.read(DOCX_DOCUMENT_PART)
        except (zipfile.BadZipFile, KeyError, OSError):
            return False
        return bool(body)


def normalize_render_format(value: Any) -> str:
    """把任意输入规范成白名单内的格式（未知一律返回空串）。

    Args:
        value: 原始取值（可能是 `None` / 非字符串）。

    Returns:
        str: `docx` / `md` / `csv` 之一；不合法返回空串。
    """
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    return normalized if normalized in RENDER_FORMATS else ""


def safe_filename(name: str, *, keep_suffix: bool = False) -> str:
    """把产物名转成安全文件名（去掉路径分隔符与空白）。

    Args:
        name: 原始名称。
        keep_suffix: 是否保留原扩展名。

    Returns:
        str: 安全文件名。
    """
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("._") or DEFAULT_FILENAME
    if keep_suffix:
        return cleaned[:MAX_FILENAME_LENGTH]
    stripped = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", cleaned)
    return stripped[:MAX_FILENAME_LENGTH] or DEFAULT_FILENAME


def render_markdown(payload: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
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


def render_csv(rows: list[dict[str, Any]], payload: dict[str, Any]) -> bytes:
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


def render_docx(
    name: str, payload: dict[str, Any], rows: list[dict[str, Any]]
) -> ArtifactBlob | None:
    """用 `python-docx` 渲染（依赖未安装时返回 None，由调用方降级）。

    **延迟导入**是刻意的（D-20 + G-28）：`python-docx` 是**平台**依赖，
    插件声明既不感知格式实现，也无法在自己的目录里引用它。

    Args:
        name: 安全文件名。
        payload: 渲染数据。
        rows: 表格数据。

    Returns:
        ArtifactBlob | None: 渲染结果；依赖未安装返回 None。
    """
    try:
        from docx import Document
    except ImportError:  # pragma: no cover - 缺依赖时走降级分支
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
    payload_bytes = buffer.getvalue()
    if not is_zip_with_document_part(payload_bytes):  # pragma: no cover - 库异常兜底
        logger.error("plugin_render_docx_malformed", name=name)
        return None
    return ArtifactBlob(
        filename=f"{name}{RENDER_EXTENSIONS['docx']}",
        content=payload_bytes,
        content_type=RENDER_CONTENT_TYPES["docx"],
        render_format="docx",
    )


def is_zip_with_document_part(content: bytes) -> bool:
    """判断字节是否为含 `word/document.xml` 的 zip（docx 结构下限）。

    Args:
        content: 文件字节。

    Returns:
        bool: 结构成立返回 True。
    """
    return FileRenderer.is_openable_docx(content)


__all__: list[str] = [
    "DEFAULT_FILENAME",
    "DOCX_DOCUMENT_PART",
    "MAX_FILENAME_LENGTH",
    "RENDER_CONTENT_TYPES",
    "RENDER_EXTENSIONS",
    "RENDER_FORMATS",
    "ArtifactBlob",
    "FileRenderer",
    "is_zip_with_document_part",
    "normalize_render_format",
    "render_csv",
    "render_docx",
    "render_markdown",
    "safe_filename",
]
