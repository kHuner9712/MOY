"""离线介质包（3A）纯逻辑与篡改检测的单测。

覆盖：布局计划、镜像锁/编排解析、MANIFEST 组装、SHA256SUMS 生成、逐条校验、
缺失/未登记/1 字节篡改、签名拒绝、CLI 退出码与缺工具 fail-closed。
真实镜像搬运（skopeo/syft/cosign/uv/npm + 网络）不在 CI 执行，见 docs/offline-package.md。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_module() -> ModuleType:
    """从 deploy/ 加载脚本（该目录不是可导入包）。"""
    path = REPO_ROOT / "deploy" / "validate_offline_media.py"
    spec = importlib.util.spec_from_file_location("validate_offline_media", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


media = _load_module()


def _image() -> object:
    """构造一条镜像记录（digest 固定，便于断言）。"""
    return media.ImageRef("officeai/adapter", "1.0.0", "sha256:" + "a" * 64, "内部交付物", "适配层")


def _package(tmp_path: Path, *, version: str = "1.0.0", arch: str = "x86_64") -> Path:
    """按计划条目造一个结构完整的最小包（假归档、假签名）。"""
    root = tmp_path / f"moy-offline-v{version}-{arch}"
    for name in media.PAYLOAD_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    image = _image()
    plan = media.plan_entries(
        version,
        arch,
        [image],
        sql_files=["0001_x.sql"],
        init_files=["init-schema-phase4.sql"],
        rollback_files=["0001_x.down.sql"],
        plugin_files=["plg_content_ops-1.0.0.difypkg"],
    )
    for entry in plan:
        if entry.relpath in {media.MANIFEST_NAME, media.SUMS_NAME, media.SIG_NAME}:
            continue
        path = root / entry.relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"payload:" + entry.relpath.encode())
    manifest = media.build_manifest(
        root,
        version=version,
        arch=arch,
        vertical=media.VERTICAL_DEFAULT,
        images=[image],
        tool_versions={"skopeo": "fake 1.0"},
    )
    media.write_json(root / media.MANIFEST_NAME, manifest)
    media.write_sha256sums(root, [media.SUMS_NAME, media.SIG_NAME])
    (root / media.SIG_NAME).write_bytes(b"fake-signature")
    return root


def _ok_signature(sums: Path, signature: Path) -> bool:
    """测试用签名校验器：签名存在即通过。"""
    return sums.is_file() and signature.is_file()


def test_plan_entries_covers_fixed_layout() -> None:
    entries = media.plan_entries(
        "1.0.0",
        "x86_64",
        [_image()],
        sql_files=["0001_x.sql"],
        init_files=["init-schema.sql"],
        rollback_files=["0001_x.down.sql"],
        plugin_files=["p.difypkg"],
    )
    paths = [entry.relpath for entry in entries]
    assert paths == sorted(paths)
    assert "images/officeai-adapter-1.0.0-x86_64.oci.tar" in paths
    assert "sql/init/init-schema.sql" in paths
    assert "sql/rollback/0001_x.down.sql" in paths
    assert "plugins/p.difypkg" in paths
    assert "sbom/images/officeai-adapter-1.0.0-x86_64.cdx.json" in paths
    assert media.MANIFEST_NAME in paths and media.SUMS_NAME in paths and media.SIG_NAME in paths


def test_parse_images_lock_reads_fields() -> None:
    text = """
    version: 2
    images:
      - name: officeai/adapter
        tag: 1.0.0
        digest: sha256:abc
        license: 内部交付物
        role: 适配层
      - name: postgres
        tag: 16.4
        license: PostgreSQL
    """
    images = media.parse_images_lock(text)
    assert [image.ref for image in images] == ["officeai/adapter:1.0.0", "postgres:16.4"]
    assert images[0].digest == "sha256:abc"
    assert images[1].license == "PostgreSQL"
    assert images[0].slug == "officeai-adapter-1.0.0"


def test_parse_compose_images_interpolates_and_dedupes() -> None:
    refs = media.parse_compose_images(
        {
            "a.yml": "services:\n  adapter:\n    image: officeai/adapter:${OA_IMAGE_TAG:-1.0.0}\n",
            "b.yml": "services:\n  adapter:\n    image: 'officeai/adapter:${OA_IMAGE_TAG:-1.0.0}'\n"
            "  pg:\n    image: postgres:16.4\n",
        },
        "2.0.0",
    )
    assert refs == ["officeai/adapter:2.0.0", "postgres:16.4"]


def test_resolve_images_reports_unlocked_ref() -> None:
    images, missing = media.resolve_images(["officeai/adapter:1.0.0", "ghost:latest"], [_image()])
    assert [image.ref for image in images] == ["officeai/adapter:1.0.0"]
    assert missing == ["ghost:latest"]


def test_manifest_fields_and_totals(tmp_path: Path) -> None:
    root = _package(tmp_path)
    manifest = media.json.loads((root / media.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["schema"] == media.MANIFEST_SCHEMA
    assert manifest["package"]["name"] == "moy-offline-v1.0.0-x86_64"
    assert manifest["package"]["arch"] == "x86_64"
    assert manifest["package"]["vertical"] == media.VERTICAL_DEFAULT
    assert manifest["images"][0]["archive"] == "images/officeai-adapter-1.0.0-x86_64.oci.tar"
    assert manifest["totals"]["entries"] == len(manifest["entries"])
    assert manifest["totals"]["bytes"] == sum(row["size"] for row in manifest["entries"])


def test_sha256sums_deterministic_and_excludes_self(tmp_path: Path) -> None:
    root = _package(tmp_path)
    first = (root / media.SUMS_NAME).read_text(encoding="utf-8")
    media.write_sha256sums(root, [media.SUMS_NAME, media.SIG_NAME])
    second = (root / media.SUMS_NAME).read_text(encoding="utf-8")
    assert first == second
    assert media.SUMS_NAME not in first and media.SIG_NAME not in first
    assert media.MANIFEST_NAME in first
    lines = [line for line in first.splitlines() if line]
    assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1])


def test_verify_package_clean(tmp_path: Path) -> None:
    root = _package(tmp_path)
    assert media.verify_package(root, signature_runner=_ok_signature) == []


def test_verify_detects_single_byte_tamper(tmp_path: Path) -> None:
    root = _package(tmp_path)
    archive = root / "images" / "officeai-adapter-1.0.0-x86_64.oci.tar"
    original = archive.read_bytes()
    archive.write_bytes(original[:-1] + bytes([original[-1] ^ 0x01]))
    failures = media.verify_hashes(root)
    assert any("oci.tar" in failure.path and "sha256" in failure.reason for failure in failures)


def test_verify_detects_missing_file(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "wheels" / "requirements.txt").unlink()
    failures = media.verify_hashes(root)
    assert any(
        failure.path == "wheels/requirements.txt" and "缺失" in failure.reason
        for failure in failures
    )


def test_verify_detects_unlisted_file(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / "metadata" / "injected.json").write_text("{}", encoding="utf-8")
    failures = media.verify_hashes(root)
    assert any(
        failure.path == "metadata/injected.json" and "未登记" in failure.reason
        for failure in failures
    )


def test_layout_rejects_arch_mismatch(tmp_path: Path) -> None:
    root = _package(tmp_path)
    renamed = root.with_name("moy-offline-v1.0.0-arm64")
    root.rename(renamed)
    failures = media.verify_layout(renamed, arch="x86_64")
    assert any(
        failure.path == renamed.name and "架构不符" in failure.reason for failure in failures
    )


def test_layout_rejects_missing_sql_dir(tmp_path: Path) -> None:
    root = _package(tmp_path)
    shutil.rmtree(root / "sql" / "rollback")
    failures = media.verify_layout(root)
    assert any(failure.path == "sql/rollback" for failure in failures)


def test_empty_sha256sums_is_red(tmp_path: Path) -> None:
    root = _package(tmp_path)
    (root / media.SUMS_NAME).write_text("", encoding="utf-8")
    failures = media.verify_hashes(root)
    assert any(failure.path == media.SUMS_NAME and "为空" in failure.reason for failure in failures)


def test_signature_missing_and_rejected(tmp_path: Path) -> None:
    root = _package(tmp_path)
    signature = root / media.SIG_NAME
    signature.unlink()
    assert any(failure.path == media.SIG_NAME for failure in media.verify_signature(root))
    signature.write_bytes(b"sig")
    assert any(
        failure.path == media.SIG_NAME
        for failure in media.verify_signature(root, runner=lambda _s, _g: False)
    )
    assert media.verify_signature(root, runner=_ok_signature) == []


def test_cli_verify_returns_one_and_names_bad_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _package(tmp_path)
    archive = root / "images" / "officeai-adapter-1.0.0-x86_64.oci.tar"
    archive.write_bytes(archive.read_bytes() + b"tamper")
    assert media.main(["verify", str(root)]) == 1
    out = capsys.readouterr().out
    assert "images/officeai-adapter-1.0.0-x86_64.oci.tar" in out
    assert "校验未通过" in out


def test_cli_self_test_passes() -> None:
    assert media.main(["self-test"]) == 0


def test_build_fails_closed_when_tools_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(media.shutil, "which", lambda _name: None)
    assert media.main(["build", "--out", str(tmp_path / "out")]) == 2
    out = capsys.readouterr().out
    assert "缺少构建工具" in out
    for tool in ("skopeo", "syft", "cosign", "uv", "npm"):
        assert tool in out


def test_check_repo_passes_without_tools() -> None:
    assert media.main(["check"]) == 0
