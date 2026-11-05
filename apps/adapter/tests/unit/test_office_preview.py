"""离线预览转换器的编排逻辑（2026-11-05 B2）。

**验证边界（必须如实说明）**：本文件注入替身 runner，只验证
「参数构造 / 超时 / kill 语义 / 输出校验 / 缓存 / 并发闸门」这些**编排逻辑**。
真实 LibreOffice 转换效果**未在本机或 CI 验证**（本机没有 soffice），必须在
部署环境按 `docs/delivery-2026-11-05-b2-xlsx-preview.md` 的手工步骤验收。

**为什么值得把编排逻辑测这么细**：四条硬约束（独立 profile、超时 kill、
`%PDF-` 校验、并发闸门）每条都对应一种"本地串行能过、线上一炸就炸"的形态；
它们全都是纯编排问题，不需要真 soffice 也能锁定。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.services.office_preview import (
    PDF_MAGIC,
    PREVIEW_SUFFIXES,
    OfficePreviewConverter,
    OfficePreviewError,
    get_office_preview_converter,
)

pytestmark = pytest.mark.unit

#: 一条能通过 `%PDF-` 校验的最小输出
SAMPLE_PDF = PDF_MAGIC + b"1.7\n%\xe2\xe3\xcf\xd3\n"


def _settings(**overrides: Any) -> Settings:
    """构造测试配置（预览相关项可覆盖）。

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
        "jwt_secret": "office-preview-test-secret-32-chars",
        "ratelimit_enabled": False,
        "business_store": "memory",
    }
    values.update(overrides)
    return Settings(**values)


def _outdir(args: list[str]) -> Path:
    """从命令行参数里取 `--outdir`（替身 runner 需要往那里写输出）。

    Args:
        args: 命令行参数。

    Returns:
        Path: 输出目录。
    """
    return Path(args[args.index("--outdir") + 1])


def _write_pdf_runner(payload: bytes = SAMPLE_PDF) -> Any:
    """构造「转换成功」的替身 runner。

    Args:
        payload: 写入 `source.pdf` 的内容。

    Returns:
        Any: 异步 runner。
    """

    async def runner(args: list[str], cwd: str) -> tuple[int, str, str]:
        (_outdir(args) / "source.pdf").write_bytes(payload)
        return 0, "", ""

    return runner


async def _no_output_runner(args: list[str], cwd: str) -> tuple[int, str, str]:
    """退出码 0 但不产出文件（LibreOffice 对不支持输入的典型表现）。

    Args:
        args: 命令行参数。
        cwd: 工作目录。

    Returns:
        tuple[int, str, str]: 全零退出。
    """
    return 0, "", ""


async def test_convert_success_passes_isolation_args_and_returns_pdf() -> None:
    """成功路径：独立 profile、临时工作目录、无残留、输出为 PDF。"""
    settings = _settings()
    converter = OfficePreviewConverter(settings, runner=_write_pdf_runner())
    seen: dict[str, Any] = {}

    async def spy(args: list[str], cwd: str) -> tuple[int, str, str]:
        seen["args"] = args
        seen["cwd"] = cwd
        (_outdir(args) / "source.pdf").write_bytes(SAMPLE_PDF)
        return 0, "convert ok", ""

    converter._runner = spy  # noqa: SLF001 - 单测需要观察实际参数
    pdf = await converter.convert(b"PK\x03\x04docx", suffix=".docx")

    assert pdf == SAMPLE_PDF
    assert pdf.startswith(PDF_MAGIC)
    args: list[str] = seen["args"]
    assert args[0] == settings.office_soffice_path
    assert "--headless" in args
    assert "--convert-to" in args and args[args.index("--convert-to") + 1] == "pdf"
    # 硬约束 1：每次转换用独立 user profile（默认 profile 是单实例锁）
    assert any(arg.startswith("-env:UserInstallation=file://") for arg in args)
    # 用的是临时工作目录，不在仓库/系统目录里留文件
    assert "moy-preview-" in seen["cwd"]


async def test_convert_rejects_unsupported_suffix_and_empty_content() -> None:
    """只允许 docx/xlsx；空内容不进入转换进程。"""
    converter = OfficePreviewConverter(_settings(), runner=_write_pdf_runner())
    with pytest.raises(OfficePreviewError) as pptx_error:
        await converter.convert(b"PK", suffix=".pptx")
    assert pptx_error.value.status_code == 415
    assert ".pptx" not in PREVIEW_SUFFIXES

    with pytest.raises(OfficePreviewError) as empty_error:
        await converter.convert(b"", suffix=".docx")
    assert empty_error.value.status_code == 415


async def test_convert_rejects_oversized_input_before_spawning() -> None:
    """超大输入直接 413，不把大文件塞进转换进程。"""
    calls: list[int] = []

    async def runner(args: list[str], cwd: str) -> tuple[int, str, str]:
        calls.append(1)
        return 0, "", ""

    converter = OfficePreviewConverter(_settings(office_preview_max_bytes=64), runner=runner)
    with pytest.raises(OfficePreviewError) as error:
        await converter.convert(b"x" * 65, suffix=".docx")
    assert error.value.status_code == 413
    assert calls == []


async def test_convert_timeout_maps_to_504() -> None:
    """转换超时映射 504（wait_for 会取消并终止等待的协程）。"""
    converter = OfficePreviewConverter(_settings(), runner=_write_pdf_runner())

    async def slow_runner(args: list[str], cwd: str) -> tuple[int, str, str]:
        # 直接抛 TimeoutError 与 wait_for 超时在调用方看来是同一语义，且不必真等 5s
        raise TimeoutError

    converter._runner = slow_runner  # noqa: SLF001
    with pytest.raises(OfficePreviewError) as error:
        await converter.convert(b"PK", suffix=".docx")
    assert error.value.status_code == 504
    assert "超时" in error.value.message


async def test_convert_nonzero_exit_maps_to_502() -> None:
    """非零退出码 → 502（并把 stderr 截断进日志）。"""
    converter = OfficePreviewConverter(_settings(), runner=_write_pdf_runner())

    async def failing(args: list[str], cwd: str) -> tuple[int, str, str]:
        return 137, "", "killed"

    converter._runner = failing  # noqa: SLF001
    with pytest.raises(OfficePreviewError) as error:
        await converter.convert(b"PK", suffix=".docx")
    assert error.value.status_code == 502


async def test_convert_zero_exit_without_output_maps_to_502() -> None:
    """退出码 0 但没产出文件 → 502（不能把 0 字节交给浏览器）。"""
    converter = OfficePreviewConverter(_settings(), runner=_no_output_runner)
    with pytest.raises(OfficePreviewError) as error:
        await converter.convert(b"PK", suffix=".xlsx")
    assert error.value.status_code == 502


async def test_convert_output_without_pdf_magic_maps_to_502() -> None:
    """产出了文件但不是 PDF（`%PDF-` 魔法校验）→ 502。"""
    converter = OfficePreviewConverter(
        _settings(), runner=_write_pdf_runner(b"<html>not a pdf</html>")
    )
    with pytest.raises(OfficePreviewError) as error:
        await converter.convert(b"PK", suffix=".docx")
    assert error.value.status_code == 502


async def test_convert_binary_missing_maps_to_503() -> None:
    """镜像没装 LibreOffice（FileNotFoundError）→ 503，而不是 500。"""
    converter = OfficePreviewConverter(_settings(), runner=_write_pdf_runner())

    async def missing(args: list[str], cwd: str) -> tuple[int, str, str]:
        raise FileNotFoundError(args[0])

    converter._runner = missing  # noqa: SLF001
    with pytest.raises(OfficePreviewError) as error:
        await converter.convert(b"PK", suffix=".docx")
    assert error.value.status_code == 503


async def test_convert_spawn_oserror_maps_to_503() -> None:
    """起进程失败（权限/资源）→ 503。"""
    converter = OfficePreviewConverter(_settings(), runner=_write_pdf_runner())

    async def broken(args: list[str], cwd: str) -> tuple[int, str, str]:
        raise OSError("spawn denied")

    converter._runner = broken  # noqa: SLF001
    with pytest.raises(OfficePreviewError) as error:
        await converter.convert(b"PK", suffix=".docx")
    assert error.value.status_code == 503


async def test_convert_caches_repeated_preview() -> None:
    """同一产物的第二次预览不再起进程（用户刷新/多人查看不重复付代价）。"""
    calls: list[str] = []

    async def counting(args: list[str], cwd: str) -> tuple[int, str, str]:
        calls.append(cwd)
        (_outdir(args) / "source.pdf").write_bytes(SAMPLE_PDF)
        return 0, "", ""

    converter = OfficePreviewConverter(_settings(), runner=counting)
    first = await converter.convert(b"PK\x03\x04same", suffix=".docx")
    second = await converter.convert(b"PK\x03\x04same", suffix=".docx")

    assert first == second == SAMPLE_PDF
    assert len(calls) == 1


async def test_convert_cache_disabled_when_ttl_or_entries_zero() -> None:
    """TTL=0 或条数上限=0 时等价于关闭缓存（回退开关必须有实际效果）。"""
    calls: list[int] = []

    async def counting(args: list[str], cwd: str) -> tuple[int, str, str]:
        calls.append(1)
        (_outdir(args) / "source.pdf").write_bytes(SAMPLE_PDF)
        return 0, "", ""

    for overrides in (
        {"office_preview_cache_ttl_seconds": 0},
        {"office_preview_cache_entries": 0},
    ):
        calls.clear()
        converter = OfficePreviewConverter(_settings(**overrides), runner=counting)
        await converter.convert(b"PK same", suffix=".docx")
        await converter.convert(b"PK same", suffix=".docx")
        assert len(calls) == 2, overrides


async def test_convert_cache_distinguishes_suffix() -> None:
    """同字节不同后缀不可命中同一条缓存（转换结果不同）。"""
    converter = OfficePreviewConverter(_settings(), runner=_write_pdf_runner())
    assert converter._cache_key(b"PK", ".docx") != converter._cache_key(b"PK", ".xlsx")  # noqa: SLF001


async def test_convert_limits_concurrency_with_semaphore() -> None:
    """并发闸门：同时运行的转换进程不超过配置上限。"""
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def tracked(args: list[str], cwd: str) -> tuple[int, str, str]:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1
        (_outdir(args) / "source.pdf").write_bytes(SAMPLE_PDF)
        return 0, "", ""

    converter = OfficePreviewConverter(
        _settings(office_preview_concurrency=1, office_preview_cache_entries=0),
        runner=tracked,
    )
    await asyncio.gather(
        *(converter.convert(f"PK-{index}".encode(), suffix=".docx") for index in range(3))
    )
    assert peak == 1


async def test_subprocess_runner_kills_child_on_cancel() -> None:
    """超时取消时必须显式 kill 子进程（协程取消不会终止进程）。"""
    from unittest.mock import patch

    from app.services.office_preview import _subprocess_runner

    class _Process:
        """卡死的子进程替身。"""

        returncode: int | None = None
        killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            """模拟永不退出。"""
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

        def kill(self) -> None:
            """记录 kill 调用。"""
            self.killed = True

        async def wait(self) -> int:
            """模拟进程被回收。"""
            self.returncode = -9
            return -9

    process = _Process()

    async def fake_exec(*args: Any, **kwargs: Any) -> _Process:
        return process

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        task = asyncio.create_task(_subprocess_runner(["soffice"], "cwd"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert process.killed
    assert process.returncode == -9


def test_get_office_preview_converter_reuses_app_state_instance() -> None:
    """每应用一份缓存：挂在 app.state 上的实例应被复用。"""

    class _State:
        """最小 app.state 替身。"""

    state = _State()
    settings = _settings()
    first = get_office_preview_converter(state, settings)
    second = get_office_preview_converter(state, settings)
    assert first is second
