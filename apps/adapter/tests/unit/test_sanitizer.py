"""产物脱敏与渲染单测（阶段三 B19，`architecture-phase3.md` §3.5 / §3.6）。

一票否决项的单元级钉子：**WF2 编辑后的内容仍必须过脱敏**（见
`test_artifact_edit.py` 的集成断言），这里先钉住脱敏器本身的判据：
手机/邮箱/身份证/Bearer 掩码、凭据**键名**拒绝、渲染格式与摘要可复现。
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.core.errors import ApiError, ErrorCode
from app.plugins.sanitizer import ArtifactSanitizer


def test_mask_phone_email_id_card() -> None:
    """手机 / 邮箱 / 身份证都被掩码，且原文不再出现。"""
    sanitizer = ArtifactSanitizer()
    text = "联系电话 13912345678，邮箱 zhang.san@corp.com，证件 110101199001011234"
    report = sanitizer.sanitize(text)
    for raw in ("13912345678", "zhang.san@corp.com", "110101199001011234"):
        assert raw not in report.text, f"{raw} 未被掩码"
    assert report.masked >= 3
    assert report.changed is True


def test_id_card_is_masked_before_phone() -> None:
    """身份证先于手机掩码（否则 18 位号码里的 11 位数字会被手机规则切断）。"""
    sanitizer = ArtifactSanitizer()
    text = sanitizer.mask_pii("证件号 110101199001011234")
    assert "110101********1234" in text, f"身份证应整段掩码，实际 {text}"


def test_bearer_token_is_redacted() -> None:
    """`Bearer xxx` 即使不是声明字段也要抹掉（漏网的令牌同样会泄露）。"""
    sanitizer = ArtifactSanitizer()
    report = sanitizer.sanitize("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc")
    assert "eyJhbGciOiJIUzI1NiJ9" not in report.text
    assert report.redacted_bearer == 1


def test_sanitize_is_idempotent_on_clean_text() -> None:
    """干净文本不被改动（防"一律打码"式假脱敏把正文毁掉）。"""
    sanitizer = ArtifactSanitizer()
    clean = "这是一段没有敏感信息的正文。"
    assert sanitizer.mask_pii(clean) == clean


@pytest.mark.parametrize("key", ["token", "api_key", "secret", "password", "credential"])
def test_secret_key_names_are_rejected(key: str) -> None:
    """凭据类**键名**命中即拒绝（抛 30003），不看值像不像密钥。"""
    sanitizer = ArtifactSanitizer()
    with pytest.raises(ApiError) as excinfo:
        sanitizer.assert_no_secret({"result": {key: "whatever"}}, where="tool:github.pr")
    assert int(excinfo.value.code) == int(ErrorCode.INTERNAL_ERROR)


def test_nested_secret_key_is_rejected() -> None:
    """嵌套结构里的凭据键名同样命中（递归检查，不只看顶层）。"""
    sanitizer = ArtifactSanitizer()
    with pytest.raises(ApiError):
        sanitizer.assert_no_secret({"items": [{"meta": {"access_token": "x"}}]})


def test_render_csv_has_bom_and_header() -> None:
    """CSV 带 BOM（Excel 打开不乱码）且首行是表头。"""
    sanitizer = ArtifactSanitizer()
    blob = sanitizer.render_file(
        render_format="csv",
        name="排期表",
        payload={"columns": ["平台", "时间"]},
        rows=[{"平台": "公众号", "时间": "周一"}],
    )
    assert blob.content.startswith(b"\xef\xbb\xbf")
    text = blob.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "平台,时间"


def test_render_markdown_contains_title() -> None:
    """Markdown 渲染带标题与正文（下载内容可读，不是空文件）。"""
    sanitizer = ArtifactSanitizer()
    blob = sanitizer.render_file(
        render_format="md", name="技术方案", payload={"title": "技术方案", "content": "正文"}
    )
    assert blob.render_format == "md"
    assert "技术方案" in blob.content.decode("utf-8")


def test_render_rejects_unknown_format() -> None:
    """未知渲染格式 → 20001（声明已封闭三种，运行期也不接受别的）。"""
    sanitizer = ArtifactSanitizer()
    with pytest.raises(ApiError) as excinfo:
        sanitizer.render_file(render_format="pdf", name="x", payload={})
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)


def test_zip_bundle_is_readable() -> None:
    """zip 包可被标准库解开（代码文件产物走这条）。"""
    payload = ArtifactSanitizer.zip_bundle({"a.ts": "export const a = 1;", "b.ts": "export {};"})
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        assert sorted(archive.namelist()) == ["a.ts", "b.ts"]


def test_digest_changes_with_content() -> None:
    """摘要随内容变化（WF2 每次保存都重算，靠这条能发现"保存了但没变"）。"""
    # 摘要是静态方法：走类调用即可（不需要实例，避免"实例未使用"的告警）
    assert ArtifactSanitizer.digest("v1") != ArtifactSanitizer.digest("v1 ")
    assert len(ArtifactSanitizer.digest("v1")) == 64
