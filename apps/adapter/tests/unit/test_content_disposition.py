"""产物下载 `Content-Disposition` 的非 ASCII 文件名处理（回归钉子）。

**为什么单独立一条**：HTTP 头按 latin-1 编码，把 `filename="发布排期表.csv"`
直接写进响应头会在序列化时抛 `UnicodeEncodeError` → 整条下载链路 500。
阶段三的产物名**几乎全是中文**（发布排期表 / 筛选评分表 / 录用通知），
所以这不是"边角场景"，是"所有产物都下不了"。

这条用例的价值在于：它断言的是**响应头可被 latin-1 编码**，
而不是"代码里出现了 `filename*` 字样"——后者改个写法就会假绿。
"""

from __future__ import annotations

import pytest

from app.api.v1.artifact_files import _content_disposition


@pytest.mark.parametrize(
    "filename",
    [
        "发布排期表.csv",
        "筛选评分表.csv",
        "录用通知（研发工程师）.docx",
        "关于做好二〇二六年设备采购工作的通知.docx",
        "a-b_c(1).csv",
        "mixed中文andASCII.txt",
    ],
)
def test_content_disposition_is_latin1_encodable(filename: str) -> None:
    """任何产物名都必须能安全写进 HTTP 头（不得抛 UnicodeEncodeError）。"""
    header = _content_disposition(filename)

    # 判据：能按 latin-1 编码（HTTP 头的编码约束），不抛异常
    header.encode("latin-1")


def test_content_disposition_keeps_ascii_fallback() -> None:
    """非 ASCII 名要给老客户端一个 ASCII 兜底名，不能是空串。"""
    header = _content_disposition("发布排期表.csv")

    assert 'filename="' in header
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert fallback, "ASCII 兜底名为空，老客户端无法落地文件"
    assert fallback.isascii()
    assert fallback.endswith(".csv"), f"兜底名丢了扩展名：{fallback!r}"


def test_content_disposition_uses_rfc5987_for_utf8_name() -> None:
    """现代客户端要能拿到原始中文名（RFC 5987 的 `filename*`）。"""
    header = _content_disposition("发布排期表.csv")

    assert "filename*=UTF-8''" in header
    assert "%E5%8F%91%E5%B8%83" in header, "中文名未按 UTF-8 百分号编码"


def test_content_disposition_strips_quotes_in_fallback() -> None:
    """兜底名里的引号必须被替换，否则会把响应头的引号提前闭合。"""
    header = _content_disposition('a"b.csv')

    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in fallback
