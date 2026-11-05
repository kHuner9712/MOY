"""离线 Office → PDF 预览（LibreOffice headless，2026-11-05 B2）。

**为什么用 LibreOffice 而不是外部 SaaS**：客户内网/气隙场景没有出网，预览必须
在本机完成；LibreOffice headless 是唯一成熟的「离线 docx/xlsx → PDF」方案，
许可证 MPL-2.0（可商用、可随镜像分发）。

**为什么只转换 PDF**：PDF 是浏览器自带阅读器唯一稳定支持的格式；转 HTML/图片
都会引入额外的字体与布局失真问题。

**进程隔离的四条硬约束**（每一条都对应一种"本地能过、上线必炸"的形态）：

1. **独立 user profile**（`-env:UserInstallation=file://<临时目录>`）：默认
   profile 是单实例锁，并发转换会互相等待直到超时——本地串行测不出来，线上
   两个人同时点预览就复现。临时目录随请求销毁，也避免残留配置。
2. **超时 + kill**：soffice 卡死（损坏文件、字体扫描）不会自己退出，
   必须 `wait_for` 后主动 kill，否则线程池会被长期占住。
3. **输出校验 `%PDF-`**：进程退出码为 0 不代表产出了 PDF（LibreOffice 对不支持的
   输入会"成功"返回 0 但不写文件）。不校验就会把 0 字节/半截内容交给浏览器。
4. **并发信号量**：同时起多个 soffice 会打满 CPU，反而让所有预览都超时；
   上限默认 2，可配置。

**缓存**：同一产物在有效期内重复预览（用户刷新、多人查看）不必反复起进程。
进程内 LRU（TTL + 条数上限），多副本各算各的——为此不引入 Redis 依赖。

**验证边界**：本模块真实转换效果**未在本机/CI 验证**（无 soffice、无 Docker），
只验证了编排逻辑（注入 runner 的单测）。真机效果由部署环境按
`docs/delivery-2026-11-05-b2-xlsx-preview.md` 的验收步骤确认。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: 允许预览的输入后缀（pptx 不在内：镜像不装 impress，按矩阵默认决策降级为下载）
PREVIEW_SUFFIXES: Final[tuple[str, ...]] = (".docx", ".xlsx")

#: PDF 文件头（`%PDF-1.x`）；不以此开头的输出一律视为转换失败
PDF_MAGIC: Final[bytes] = b"%PDF-"

#: 单次转换的 soffice 参数（`--invisible` 不弹窗；`--norestore` 不写恢复文件）
_SOFFICE_ARGS: Final[tuple[str, ...]] = (
    "--headless",
    "--norestore",
    "--invisible",
    "--convert-to",
    "pdf",
)


class OfficePreviewError(Exception):
    """预览失败（携带 HTTP 状态与**业务语言**文案，不泄漏内部路径）。

    Attributes:
        status_code: 建议的 HTTP 状态码。
        message: 业务文案（可直接展示给用户）。
    """

    def __init__(self, status_code: int, message: str) -> None:
        """初始化。

        Args:
            status_code: HTTP 状态码。
            message: 业务文案。
        """
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """缓存条目。"""

    created_at: float
    payload: bytes


#: runner 签名：`(args, cwd) -> (returncode, stdout, stderr)`；单测注入替身
Runner = Callable[[list[str], str], Awaitable[tuple[int, str, str]]]


class OfficePreviewConverter:
    """Office → PDF 转换器（无状态语义，实例只持有缓存与信号量）。

    Attributes:
        _settings: 配置实例。
        _runner: 子进程执行器（单测可注入）。
        _semaphore: 并发闸门。
        _cache: 进程内 LRU。
    """

    def __init__(self, settings: Settings | None = None, *, runner: Runner | None = None) -> None:
        """初始化。

        Args:
            settings: 配置实例。
            runner: 子进程执行器（默认走 `asyncio.create_subprocess_exec`）。
        """
        self._settings = settings or get_settings()
        self._runner = runner or _subprocess_runner
        self._semaphore = asyncio.Semaphore(self._settings.office_preview_concurrency)
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()

    async def convert(self, content: bytes, *, suffix: str) -> bytes:
        """把 Office 字节转换成 PDF。

        Args:
            content: 源文件字节。
            suffix: 源文件后缀（`.docx` / `.xlsx`）。

        Returns:
            bytes: PDF 字节。

        Raises:
            OfficePreviewError: 超大（413）、服务不可用（503）、
                超时（504）、转换失败（502）。
        """
        if suffix not in PREVIEW_SUFFIXES:
            raise OfficePreviewError(415, "该格式暂不支持在线预览，请下载后查看")
        if not content:
            raise OfficePreviewError(415, "该产物没有可预览的内容，请下载后查看")
        if len(content) > self._settings.office_preview_max_bytes:
            raise OfficePreviewError(413, "文件过大，暂不支持在线预览，请下载后查看")
        key = self._cache_key(content, suffix)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        async with self._semaphore:
            # 等闸门期间可能已被另一个请求转换完成（避免重复起进程）
            cached = self._cache_get(key)
            if cached is not None:
                return cached
            pdf = await self._convert_once(content, suffix)
            self._cache_put(key, pdf)
            return pdf

    async def _convert_once(self, content: bytes, suffix: str) -> bytes:
        """起一次 soffice 进程完成转换（调用方已持有信号量）。

        Args:
            content: 源文件字节。
            suffix: 源文件后缀。

        Returns:
            bytes: PDF 字节。

        Raises:
            OfficePreviewError: 见 `convert`。
        """
        with tempfile.TemporaryDirectory(prefix="moy-preview-") as raw_dir:
            workdir = Path(raw_dir)
            profile = workdir / "profile"
            profile.mkdir()
            source = workdir / f"source{suffix}"
            source.write_bytes(content)
            target = workdir / "source.pdf"
            args = [
                self._settings.office_soffice_path,
                *_SOFFICE_ARGS,
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--outdir",
                str(workdir),
                str(source),
            ]
            try:
                returncode, stdout, stderr = await asyncio.wait_for(
                    self._runner(args, str(workdir)),
                    timeout=self._settings.office_preview_timeout_seconds,
                )
            except TimeoutError as exc:
                raise OfficePreviewError(504, "预览生成超时，请下载后查看") from exc
            except FileNotFoundError as exc:
                logger.error("office_preview_binary_missing", extra={"path": args[0]})
                raise OfficePreviewError(503, "预览服务暂不可用，请下载后查看") from exc
            except OSError as exc:
                logger.error("office_preview_spawn_failed", extra={"error": str(exc)})
                raise OfficePreviewError(503, "预览服务暂不可用，请下载后查看") from exc
            if returncode != 0:
                logger.error(
                    "office_preview_failed",
                    extra={"returncode": returncode, "stderr": (stderr or stdout)[-500:]},
                )
                raise OfficePreviewError(502, "预览生成失败，请下载后查看")
            if not target.exists():
                logger.error("office_preview_no_output", extra={"stdout": stdout[-500:]})
                raise OfficePreviewError(502, "预览生成失败，请下载后查看")
            pdf = target.read_bytes()
        if not pdf.startswith(PDF_MAGIC):
            logger.error("office_preview_not_pdf", extra={"size": len(pdf)})
            raise OfficePreviewError(502, "预览生成失败，请下载后查看")
        return pdf

    def _cache_key(self, content: bytes, suffix: str) -> str:
        """计算缓存键（内容摘要 + 后缀）。

        Args:
            content: 源文件字节。
            suffix: 源文件后缀。

        Returns:
            str: 缓存键。
        """
        return f"{hashlib.sha256(content).hexdigest()}{suffix}"

    def _cache_get(self, key: str) -> bytes | None:
        """读缓存（过期即丢）。

        Args:
            key: 缓存键。

        Returns:
            bytes | None: 命中的 PDF 字节。
        """
        entry = self._cache.get(key)
        if entry is None:
            return None
        ttl = self._settings.office_preview_cache_ttl_seconds
        if ttl <= 0 or time.monotonic() - entry.created_at > ttl:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return entry.payload

    def _cache_put(self, key: str, payload: bytes) -> None:
        """写缓存（按条数上限淘汰最旧）。

        Args:
            key: 缓存键。
            payload: PDF 字节。
        """
        limit = self._settings.office_preview_cache_entries
        if limit <= 0:
            return
        self._cache[key] = _CacheEntry(created_at=time.monotonic(), payload=payload)
        self._cache.move_to_end(key)
        while len(self._cache) > limit:
            self._cache.popitem(last=False)


async def _subprocess_runner(args: list[str], cwd: str) -> tuple[int, str, str]:
    """默认执行器：起子进程并收集输出。

    Args:
        args: 命令行参数。
        cwd: 工作目录。

    Returns:
        tuple[int, str, str]: `(returncode, stdout, stderr)`。
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        # wait_for 超时会取消本协程；**必须显式 kill**——协程取消不会终止子进程，
        # 否则卡死的 soffice 会留在后台持续占 CPU（本地串行测不出来的那种问题）。
        process.kill()
        await process.wait()
        raise
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def get_office_preview_converter(app_state: Any, settings: Settings) -> OfficePreviewConverter:
    """取（或创建）挂在 app.state 上的转换器（每应用一份缓存）。

    Args:
        app_state: `request.app.state`。
        settings: 配置实例。

    Returns:
        OfficePreviewConverter: 转换器。
    """
    converter = getattr(app_state, "office_preview_converter", None)
    if converter is None:
        converter = OfficePreviewConverter(settings)
        app_state.office_preview_converter = converter
    return converter
