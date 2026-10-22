"""产物 MIME 归一化与留存期判定的分支覆盖（阶段四 T4-01.5 / S9）。

这两组都是**纯函数**，但都是"出事才走到"的分支：

- `resolve_content_type`：扩展名命中 / 表格兜底 / 调用方声明 / `octet-stream` 兜底；
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
