"""产物下载 `Content-Disposition` 的非 ASCII 文件名处理（回归钉子）。

**为什么单独立一条**：HTTP 头按 latin-1 编码，把 `filename="发布排期表.csv"`
直接写进响应头会在序列化时抛 `UnicodeEncodeError` → 整条下载链路 500。
阶段三的产物名**几乎全是中文**（发布排期表 / 筛选评分表 / 录用通知），
所以这不是"边角场景"，是"所有产物都下不了"。

这条用例的价值在于：它断言的是**响应头可被 latin-1 编码**，
而不是"代码里出现了 `filename*` 字样"——后者改个写法就会假绿。
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from app.api.v1 import artifact_files
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


def test_content_disposition_is_attachment_not_inline() -> None:
    """必须是 `attachment`（2026-11-02 审计 S-02）。

    `inline` 会让浏览器按声明的 MIME 就地渲染：一旦某条产物的 MIME 是
    `text/html`，脚本就在平台同源下执行，可以读走 localStorage 里的访问令牌。
    `attachment` 让浏览器只落地文件、不渲染——这是这条链路上最靠前的一道闸。
    """
    header = _content_disposition("发布排期表.csv")

    assert header.startswith("attachment;"), f"下载响应不应允许内联渲染：{header!r}"
    assert "inline" not in header


def test_content_disposition_strips_crlf() -> None:
    """文件名里的 CR/LF 必须被清掉（header injection）。

    `filename` 来自插件声明，不是完全可信输入；`\\r\\n` 会截断响应头，
    攻击者可借此注入任意响应头。
    """
    header = _content_disposition("evil\r\nX-Injected: 1.csv")

    assert "\r" not in header
    assert "\n" not in header
    # 清理后仍要能被 latin-1 编码，且保留可用的兜底名
    header.encode("latin-1")
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert fallback


# ---------------------------------------------------------------------------
# 端点级：上面几条测的是纯函数，这里证明**响应真的下发了那三个头**。
# 纯函数正确但没拼进 Response，是"改了等于没改"的典型形态（审计 S-02/S-03）。
# ---------------------------------------------------------------------------
def _download_request() -> Request:
    """构造一个最小可用的 GET 请求对象（不启服务、不建 app）。"""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/files/artifacts/tok",
            "raw_path": b"/files/artifacts/tok",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


async def test_download_response_carries_safety_headers() -> None:
    """下载响应必须自带 `attachment` + `nosniff` + `CSP sandbox`。

    为什么必须由适配层自己下发（审计 S-02）：nginx 的 `add_header` 一旦在
    location 层出现就不再继承上层。`/files/` 是新增 location，运维往里加
    安全头反而会把 server 级的 `nosniff` 顶掉——所以不赌外面配对了。
    """
    record = types.SimpleNamespace(
        content=b"PK\x03\x04fake-docx",
        inline_text="",
        name="发布排期表.docx",
        output_key="deliverables/schedule.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        expires_at="",
    )
    claims = types.SimpleNamespace(tenant_id="tenant_1", artifact_id="art_1", version=3)

    with (
        patch.object(artifact_files, "get_request_settings", return_value=object()),
        patch.object(artifact_files, "parse_token", return_value=claims),
        patch.object(
            artifact_files,
            "get_request_rate_limiter",
            return_value=types.SimpleNamespace(enabled=False),
        ),
        patch.object(artifact_files, "fetch_artifact", new=AsyncMock(return_value=record)),
    ):
        response = await artifact_files.download_artifact(  # type: ignore[call-arg]
            _download_request(), token="tok-12345678"
        )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;"), disposition
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"
    # 二进制产物不能被改写成文本类型
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
