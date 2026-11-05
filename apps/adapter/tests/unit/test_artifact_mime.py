"""产物 MIME 归一化与留存期判定的分支覆盖（阶段四 T4-01.5 / S9）。

这两组都是**纯函数**，但都是"出事才走到"的分支：

- `resolve_content_type`：扩展名命中 / 调用方声明 / 表格兜底 / `octet-stream` 兜底；
  它是 P3-B22-04 的修复本体——下载响应的 `Content-Type` 从这里出。
- `_is_expired`：空值 / 无时区 / 无法解析 / 已过期 / 未过期；
  它是"产物 180 天后必须下不到"的唯一判定点，任何一个分支写错都会让过期产物
  永久可下载（或让正常产物全部 404）。

用参数化把每个分支都钉死，比只跑一条 happy path 有用得多。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.v1.artifact_files import _is_expired
from app.db.repositories.artifact_repo import (
    DEFAULT_CONTENT_TYPE,
    EXTENSION_CONTENT_TYPES,
    is_safe_declared_content_type,
    resolve_content_type,
)

pytestmark = pytest.mark.unit


# ===========================================================================
# 1. resolve_content_type
# ===========================================================================
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "行政公文.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("周报.md", "text/markdown; charset=utf-8"),
        ("排期表.csv", "text/csv; charset=utf-8"),
        ("成果物.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("README.TXT", "text/plain; charset=utf-8"),  # 大写扩展名同样命中
        ("export.json", "application/json"),
        ("bundle.zip", "application/zip"),
    ],
)
def test_extension_wins_over_declared_type(name: str, expected: str) -> None:
    """扩展名优先：换格式重渲染时 MIME 必须跟着变，不能被上/个版本的声明带偏。"""
    assert resolve_content_type(name, "file", "text/plain; charset=utf-8") == expected


def test_table_type_maps_to_csv() -> None:
    """`table` 类产物的 `inline_text` 就是 CSV 文本（无扩展名时按类型兜底）。"""
    assert resolve_content_type("排期表", "table", "") == EXTENSION_CONTENT_TYPES["csv"]


def test_declared_xlsx_wins_over_table_type() -> None:
    """表格重新导出为 xlsx 时，声明值必须优先于 `table` 的 CSV 兜底。

    否则库里会出现「MIME 是 CSV、字节是 xlsx」的错配（2026-11-05 B2 的判据）。
    """
    xlsx = EXTENSION_CONTENT_TYPES["xlsx"]
    assert resolve_content_type("排期表", "table", xlsx) == xlsx


def test_declared_type_is_kept_when_extension_unknown() -> None:
    """扩展名不认识且声明可用 → 沿用声明（如 `image/png` 这类不在表里的类型）。"""
    assert resolve_content_type("截图", "file", "image/png") == "image/png"


def test_octet_stream_declared_type_falls_back_to_default() -> None:
    """`application/octet-stream` 等于"我不知道" → 用兜底类型，别把未知传给浏览器。"""
    assert resolve_content_type("未知文件", "file", "application/octet-stream") == (
        DEFAULT_CONTENT_TYPE
    )


def test_empty_declared_type_falls_back_to_default() -> None:
    """什么都没声明（历史行）→ 兜底 `text/plain; charset=utf-8`。"""
    assert resolve_content_type("", "text", "") == DEFAULT_CONTENT_TYPE


def test_name_without_dot_does_not_crash() -> None:
    """无扩展名的名称不能触发索引错误（`rsplit` 边界）。"""
    assert resolve_content_type("无扩展名", "file", "") == DEFAULT_CONTENT_TYPE


# ---------------------------------------------------------------------------
# 1.1 声明值白名单（2026-11-02 审计 S-02）
#
# 原实现是「declared 非 octet-stream 就原样采用」，等于把 MIME 决定权交给插件。
# 插件由租户管理员安装、内容不必可信：声明 text/html 且产物字节是 HTML/JS 时，
# 用户点下载就会在**平台同源**下执行脚本 —— 配合令牌存 localStorage 构成窃取链。
# 所以服务端必须收口：只采信"浏览器不会当脚本执行"的类型。
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "declared",
    [
        "text/html",
        "text/html; charset=utf-8",
        "application/xhtml+xml",
        "image/svg+xml",  # SVG 可内嵌 <script>，必须一并排除
        "text/javascript",
        "application/javascript",
        "TEXT/HTML",  # 大小写不敏感：不能靠改大小写绕过
    ],
)
def test_executable_declared_types_are_downgraded(declared: str) -> None:
    """可执行/可渲染类型一律降级为兜底，不给浏览器渲染上下文。"""
    assert resolve_content_type("无扩展名产物", "file", declared) == DEFAULT_CONTENT_TYPE


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("application/pdf", "application/pdf"),
        ("image/png", "image/png"),
        ("text/csv; charset=utf-8", "text/csv; charset=utf-8"),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ],
)
def test_safe_declared_types_are_kept(declared: str, expected: str) -> None:
    """白名单内的类型仍沿用声明（含带 charset 参数的写法）。"""
    assert resolve_content_type("无扩展名产物", "file", declared) == expected


def test_safe_declared_content_type_helper() -> None:
    """白名单判定按基础类型比对：带参数、大小写混写都要能过，空值必须为 False。"""
    assert is_safe_declared_content_type("text/csv; charset=utf-8") is True
    assert is_safe_declared_content_type("IMAGE/PNG") is True
    assert is_safe_declared_content_type("text/html") is False
    assert is_safe_declared_content_type("") is False
    assert is_safe_declared_content_type("   ") is False


# ===========================================================================
# 2. _is_expired
# ===========================================================================
def test_empty_expires_at_is_not_expired() -> None:
    """空值 = 未设置（内存后端的历史记录）→ 不拦截，否则老产物全部 404。"""
    assert _is_expired("") is False
    assert _is_expired("   ") is False


def test_past_timestamp_is_expired() -> None:
    """已过期：180 天留存期的真正判定点。"""
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert _is_expired(past) is True


def test_future_timestamp_is_not_expired() -> None:
    """未过期。"""
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert _is_expired(future) is False


def test_naive_timestamp_is_treated_as_utc() -> None:
    """无时区的时间按 UTC 解释（仓储层写出的都是 UTC，缺 `Z` 也不该判错）。"""
    past = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert _is_expired(past) is True


def test_unparsable_timestamp_is_not_expired() -> None:
    """解析失败不拦截：留存期是业务约束，不该因格式异常把整条下载链路 500 掉。"""
    assert _is_expired("not-a-timestamp") is False
