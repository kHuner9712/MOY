#!/usr/bin/env python3
"""离线介质包门禁：构建（3A）与校验（3B 的输入）。

**为什么要有这个脚本**
--------------------
私有化交付要求"目标机无网也能起栈"，但此前 `deploy/offline/README.md` 只是一份
占位清单——没有任何命令能产出介质包，也没有任何命令能证明介质包**没被动过**。
本脚本把介质包的**结构、清单与校验**钉成代码，构建与校验共用同一份布局定义：

1. **可复现**：版本、架构、垂直方向、镜像 digest 全部写进 `MANIFEST.json`；
   目录名固定为 `moy-offline-v<version>-<arch>`，同一版本重建产物路径一致。
2. **可校验**：`SHA256SUMS` 覆盖除自身与签名外的全部文件；`verify` 逐条比对，
   失败时指出**具体哪个文件**（缺失 / 哈希不符 / 未登记），不笼统报错。
3. **不静默降级**：真实搬运依赖 `skopeo` / `syft` / `cosign` / `uv` / `npm`，
   缺任何一个都直接退出 2 并列出"需要安装什么"，绝不生成一个"看起来成功"的空包。
4. **可测**：`plan` / 清单组装 / 校验 / 篡改检测全部是纯函数，CI 用假归档即可跑；
   真实镜像搬运标注为"需工具与网络，本机/CI 未验证"。

用法::

    python deploy/validate_offline_media.py                 # 仓库就绪检查（纯逻辑）
    python deploy/validate_offline_media.py build --out dist/offline
    python deploy/validate_offline_media.py verify dist/offline/moy-offline-v1.0.0-x86_64
    python deploy/validate_offline_media.py self-test       # 正样本 + 篡改检测

退出码：0 = 通过；1 = 校验失败 / 自检未通过；2 = 缺工具或构建失败（fail-closed）。

**自检约定**：本脚本属"直接调用型"，`self-test` 成功时退出 **0**（同
`validate_offline_pkgs.py`）。

本轮（3A）只做**构建侧**：介质生成 + 校验 + 文档。目标机加载/升级/回滚属 3B。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
IMAGES_LOCK: Final[Path] = REPO_ROOT / "deploy" / "images.lock"
COMPOSE_FILES: Final[tuple[Path, ...]] = (
    REPO_ROOT / "deploy" / "docker-compose.yml",
    REPO_ROOT / "deploy" / "docker-compose.lite.yml",
    REPO_ROOT / "deploy" / "docker-compose.saas.yml",
)
MIGRATIONS_DIR: Final[Path] = REPO_ROOT / "deploy" / "migrations"
ROLLBACK_DIR: Final[Path] = MIGRATIONS_DIR / "rollback"
PLUGINS_DIR: Final[Path] = REPO_ROOT / "deploy" / "offline" / "plugins"
SBOM_GENERATOR: Final[Path] = REPO_ROOT / "deploy" / "sbom" / "generate.py"
PACKAGE_JSON: Final[Path] = REPO_ROOT / "package.json"
DEFAULT_OUT: Final[Path] = REPO_ROOT / "dist" / "offline"

#: 本轮只做 x86_64；扩展点：加架构时同步 docs/offline-package.md 与测试矩阵
SUPPORTED_ARCHES: Final[tuple[str, ...]] = ("x86_64",)
DEFAULT_ARCH: Final[str] = "x86_64"

#: 垂直方向默认值（可被 --vertical 覆盖）；内容运营以 plg_content_ops 为证
VERTICAL_DEFAULT: Final[str] = "plg_content_ops"

#: 介质包根目录命名（版本与架构必须出现在名字里）
PACKAGE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^moy-offline-v(?P<version>[0-9A-Za-z._-]+)-(?P<arch>[0-9A-Za-z_]+)$"
)

#: 固定目录布局（3B 的加载器按这份契约读取，不得随意改名）
PAYLOAD_DIRS: Final[tuple[str, ...]] = (
    "images",
    "wheels",
    "web",
    "sql/migrations",
    "sql/init",
    "sql/rollback",
    "plugins",
    "sbom/images",
    "metadata",
)

#: 根文件（构建必须产出；SHA256SUMS 与签名不进入自身清单）
ROOT_FILES: Final[tuple[str, ...]] = ("MANIFEST.json", "SHA256SUMS", "SHA256SUMS.sig")
SUMS_NAME: Final[str] = "SHA256SUMS"
SIG_NAME: Final[str] = "SHA256SUMS.sig"
MANIFEST_NAME: Final[str] = "MANIFEST.json"

#: 真实构建所需工具：缺一即退出 2（工具名 → 用途 + 安装提示）
REQUIRED_TOOLS: Final[dict[str, str]] = {
    "skopeo": "镜像搬运（docker:// → OCI 归档）；安装：apt install skopeo / dnf install skopeo",
    "syft": "镜像 SBOM；安装：https://github.com/anchore/syft#installation",
    "cosign": "SHA256SUMS 签名；安装：https://docs.sigstore.dev/cosign/installation/",
    "uv": "导出 Python 依赖锁（uv export）；安装：pipx install uv",
    "npm": "前端离线缓存（npm ci --cache）；安装：随 Node.js 22",
}

#: MANIFEST schema 版本（3B 读取方按此判断兼容性）
MANIFEST_SCHEMA: Final[str] = "moy-offline-manifest/v1"


class BuildError(RuntimeError):
    """构建步骤失败（带步骤名，便于定位）。"""


@dataclass(frozen=True)
class ImageRef:
    """镜像锁文件中的一条记录。"""

    name: str
    tag: str
    digest: str = ""
    license: str = ""
    role: str = ""

    @property
    def ref(self) -> str:
        """完整镜像引用（name:tag）。"""
        return f"{self.name}:{self.tag}"

    @property
    def slug(self) -> str:
        """文件名友好的标识（斜杠与冒号替换为连字符）。"""
        return re.sub(r"[^0-9A-Za-z._-]+", "-", f"{self.name}-{self.tag}").strip("-")


@dataclass(frozen=True)
class PlannedEntry:
    """介质包内一个计划条目（源 → 目的）。"""

    relpath: str
    kind: str
    source: str


@dataclass(frozen=True)
class VerifyFailure:
    """一条校验失败：必须能定位到具体条目。"""

    path: str
    reason: str


# ===========================================================================
# 解析：镜像锁 / 编排引用
# ===========================================================================


def parse_images_lock(text: str) -> list[ImageRef]:
    """解析 `deploy/images.lock`（行式 YAML，只取固定字段）。

    Args:
        text: 锁文件全文。

    Returns:
        list[ImageRef]: 镜像记录（缺 digest 的记录仍保留，由构建阶段判定）。
    """
    images: list[ImageRef] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- name:"):
            if current:
                images.append(
                    ImageRef(
                        name=current.get("name", ""),
                        tag=current.get("tag", ""),
                        digest=current.get("digest", ""),
                        license=current.get("license", ""),
                        role=current.get("role", ""),
                    )
                )
            current = {"name": stripped.split(":", 1)[1].strip().strip("'\""), "tag": ""}
            continue
        if current is None or not raw.startswith((" ", "\t")):
            continue
        match = re.match(r"^\s+([a-z_]+):\s*(.*)$", raw)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip().strip("'\"")
        if key in {"tag", "digest", "license", "role"} and not current.get(key):
            current[key] = value
    if current:
        images.append(
            ImageRef(
                name=current.get("name", ""),
                tag=current.get("tag", ""),
                digest=current.get("digest", ""),
                license=current.get("license", ""),
                role=current.get("role", ""),
            )
        )
    return [image for image in images if image.name and image.tag]


def _interpolate(value: str, version: str) -> str:
    """展开 compose 里的 `${VAR:-default}`（只处理镜像标签这一个变量）。

    Args:
        value: 原始 image 值。
        version: 默认标签（产品版本）。

    Returns:
        str: 展开后的引用。
    """
    return re.sub(r"\$\{OA_IMAGE_TAG:-[^}]+\}", version, value).strip().strip("'\"")


def parse_compose_images(texts: Mapping[str, str], version: str) -> list[str]:
    """从编排文件提取 `image:` 引用（去重、保序）。

    Args:
        texts: 文件名 → 全文。
        version: `${OA_IMAGE_TAG:-…}` 的默认值。

    Returns:
        list[str]: 镜像引用列表。
    """
    refs: dict[str, None] = {}
    for text in texts.values():
        for raw in text.splitlines():
            match = re.match(r"^\s+image:\s*(?P<value>.+)$", raw)
            if not match:
                continue
            value = match.group("value").split(" #", 1)[0].strip()
            refs[_interpolate(value, version)] = None
    return list(refs)


def resolve_images(
    compose_refs: Sequence[str], locked: Sequence[ImageRef]
) -> tuple[list[ImageRef], list[str]]:
    """把编排引用解析为锁文件记录。

    Args:
        compose_refs: compose 中出现的 `name:tag`。
        locked: 锁文件记录。

    Returns:
        tuple[list[ImageRef], list[str]]: (解析成功的镜像, 锁文件缺失的引用)。
    """
    by_ref = {image.ref: image for image in locked}
    images: list[ImageRef] = []
    missing: list[str] = []
    for ref in compose_refs:
        image = by_ref.get(ref)
        if image is None:
            missing.append(ref)
        else:
            images.append(image)
    return images, missing


# ===========================================================================
# 纯逻辑：计划 / 清单 / 校验和
# ===========================================================================


def plan_entries(
    version: str,
    arch: str,
    images: Sequence[ImageRef],
    *,
    sql_files: Sequence[str],
    init_files: Sequence[str],
    rollback_files: Sequence[str],
    plugin_files: Sequence[str],
) -> list[PlannedEntry]:
    """组装介质包的计划条目（纯函数，供 build / check / 测试共用）。

    Args:
        version: 产品版本。
        arch: 目标架构（目录名的一部分）。
        images: 要搬运的镜像。
        sql_files: `deploy/migrations/*.sql` 的文件名。
        init_files: `deploy/init-*.sql` 的文件名。
        rollback_files: `deploy/migrations/rollback/*.sql` 的文件名。
        plugin_files: `deploy/offline/plugins/` 下的文件名。

    Returns:
        list[PlannedEntry]: 计划条目（排序稳定）。
    """
    entries: list[PlannedEntry] = []
    for image in images:
        entries.append(
            PlannedEntry(
                f"images/{image.slug}-{arch}.oci.tar",
                "image",
                f"docker://{image.name}@{image.digest}"
                if image.digest
                else f"docker://{image.ref}",
            )
        )
    entries.append(PlannedEntry("images/images.lock", "metadata", "deploy/images.lock"))
    entries.append(
        PlannedEntry(
            "wheels/requirements.txt",
            "python",
            "uv export --frozen --no-dev --no-emit-project",
        )
    )
    entries.append(PlannedEntry("web/package-lock.json", "web", "package-lock.json"))
    entries.append(PlannedEntry("web/npm-cache.tar.gz", "web", "npm ci --cache"))
    for name in sql_files:
        entries.append(PlannedEntry(f"sql/migrations/{name}", "sql", f"deploy/migrations/{name}"))
    for name in init_files:
        entries.append(PlannedEntry(f"sql/init/{name}", "sql", f"deploy/{name}"))
    for name in rollback_files:
        entries.append(
            PlannedEntry(f"sql/rollback/{name}", "sql", f"deploy/migrations/rollback/{name}")
        )
    for name in plugin_files:
        entries.append(PlannedEntry(f"plugins/{name}", "plugin", f"deploy/offline/plugins/{name}"))
    entries.append(PlannedEntry("sbom/sbom.cdx.json", "sbom", "deploy/sbom/generate.py"))
    for image in images:
        entries.append(PlannedEntry(f"sbom/images/{image.slug}-{arch}.cdx.json", "sbom", "syft"))
    entries.append(
        PlannedEntry("metadata/NOTICE.third-party.md", "metadata", "images.lock 许可证字段")
    )
    entries.append(
        PlannedEntry("metadata/version.json", "metadata", "product version / git commit")
    )
    entries.append(PlannedEntry(MANIFEST_NAME, "manifest", "本脚本生成"))
    entries.append(PlannedEntry(SUMS_NAME, "manifest", "本脚本生成"))
    entries.append(PlannedEntry(SIG_NAME, "manifest", "cosign sign-blob"))
    return sorted(entries, key=lambda entry: entry.relpath)


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256（分块读，避免大镜像吃内存）。

    Args:
        path: 文件路径。

    Returns:
        str: 十六进制摘要。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_files(root: Path, exclude: Sequence[str]) -> list[str]:
    """列出目录下全部文件（POSIX 相对路径，排序稳定）。

    Args:
        root: 包根目录。
        exclude: 需要排除的相对路径。

    Returns:
        list[str]: 相对路径列表。
    """
    excluded = set(exclude)
    candidates = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )
    return [rel for rel in candidates if rel not in excluded]


def build_manifest(
    root: Path,
    *,
    version: str,
    arch: str,
    vertical: str,
    images: Sequence[ImageRef],
    tool_versions: Mapping[str, str],
    notes: Sequence[str] = (),
) -> dict[str, object]:
    """扫描已写好的包，产出 MANIFEST 文档。

    Args:
        root: 包根目录。
        version: 产品版本。
        arch: 目标架构。
        vertical: 垂直方向。
        images: 已搬运的镜像。
        tool_versions: 工具版本（构建时实测）。
        notes: 已知提示（如插件为空）。

    Returns:
        dict[str, object]: MANIFEST 内容。
    """
    entries = []
    total = 0
    for rel in walk_files(root, [MANIFEST_NAME, SUMS_NAME, SIG_NAME]):
        path = root / rel
        size = path.stat().st_size
        total += size
        entries.append({"path": rel, "sha256": sha256_file(path), "size": size})
    image_rows = [
        {
            "ref": image.ref,
            "digest": image.digest,
            "archive": f"images/{image.slug}-{arch}.oci.tar",
            "license": image.license,
            "role": image.role,
        }
        for image in images
    ]
    return {
        "schema": MANIFEST_SCHEMA,
        "package": {
            "name": f"moy-offline-v{version}-{arch}",
            "version": version,
            "arch": arch,
            "vertical": vertical,
            "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_commit": _git_commit(),
        },
        "tools": dict(sorted(tool_versions.items())),
        "images": image_rows,
        "entries": entries,
        "totals": {"entries": len(entries), "bytes": total},
        "notes": list(notes),
    }


def _git_commit() -> str:
    """当前提交 SHA（取不到时留空，不猜）。

    Returns:
        str: 40 位 SHA 或空串。
    """
    try:
        out = subprocess.run(  # noqa: S603 - 参数为常量
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],  # noqa: S607 - git 在 PATH
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def write_json(path: Path, data: Mapping[str, object]) -> None:
    """写 JSON（UTF-8、缩进 2、末尾换行，保证可复现）。

    Args:
        path: 输出路径。
        data: 内容。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_sha256sums(root: Path, exclude: Sequence[str]) -> list[str]:
    """生成 `SHA256SUMS`（覆盖除自身与签名外的全部文件）。

    Args:
        root: 包根目录。
        exclude: 排除项（通常为 `SHA256SUMS` 与 `.sig`）。

    Returns:
        list[str]: 写入的行（`<sha256>  <relpath>`）。
    """
    lines = [f"{sha256_file(root / rel)}  {rel}" for rel in walk_files(root, exclude)]
    (root / SUMS_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def read_sha256sums(root: Path) -> tuple[dict[str, str], list[VerifyFailure]]:
    """解析 `SHA256SUMS`。

    Args:
        root: 包根目录。

    Returns:
        tuple[dict[str, str], list[VerifyFailure]]: (路径 → 摘要, 格式错误)。
    """
    path = root / SUMS_NAME
    if not path.is_file():
        return {}, [VerifyFailure(SUMS_NAME, "文件缺失")]
    expected: dict[str, str] = {}
    failures: list[VerifyFailure] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^(?P<digest>[0-9a-f]{64})  (?P<path>.+)$", line)
        if not match:
            failures.append(VerifyFailure(SUMS_NAME, f"第 {number} 行格式不合法：`{line[:60]}`"))
            continue
        expected[match.group("path")] = match.group("digest")
    if not expected and not failures:
        failures.append(VerifyFailure(SUMS_NAME, "清单为空（没有任何可校验条目）"))
    return expected, failures


def verify_layout(
    root: Path, *, version: str | None = None, arch: str | None = None
) -> list[VerifyFailure]:
    """校验目录命名、固定结构与 MANIFEST 元数据。

    Args:
        root: 包根目录。
        version: 期望版本（None = 从目录名推断）。
        arch: 期望架构（None = 从目录名推断）。

    Returns:
        list[VerifyFailure]: 失败项。
    """
    failures: list[VerifyFailure] = []
    match = PACKAGE_NAME_PATTERN.match(root.name)
    if not match:
        failures.append(VerifyFailure(root.name, "目录名不符合 `moy-offline-v<version>-<arch>`"))
        return failures
    dir_version = match.group("version")
    dir_arch = match.group("arch")
    if version and version != dir_version:
        failures.append(VerifyFailure(root.name, f"版本不符：目录 {dir_version} / 期望 {version}"))
    if arch and arch != dir_arch:
        failures.append(VerifyFailure(root.name, f"架构不符：目录 {dir_arch} / 期望 {arch}"))
    if dir_arch not in SUPPORTED_ARCHES:
        failures.append(VerifyFailure(root.name, f"架构 `{dir_arch}` 本轮不支持"))
    for name in PAYLOAD_DIRS:
        if not (root / name).is_dir():
            failures.append(VerifyFailure(name, "目录缺失"))
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        failures.append(VerifyFailure(MANIFEST_NAME, "文件缺失"))
        return failures
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(VerifyFailure(MANIFEST_NAME, f"解析失败：{exc}"))
        return failures
    package = manifest.get("package") if isinstance(manifest, dict) else None
    if not isinstance(package, dict):
        failures.append(VerifyFailure(MANIFEST_NAME, "缺 `package` 段"))
        return failures
    if manifest.get("schema") != MANIFEST_SCHEMA:
        failures.append(VerifyFailure(MANIFEST_NAME, f"schema 不是 {MANIFEST_SCHEMA}"))
    if package.get("version") != dir_version:
        failures.append(VerifyFailure(MANIFEST_NAME, "package.version 与目录版本不一致"))
    if package.get("arch") != dir_arch:
        failures.append(VerifyFailure(MANIFEST_NAME, "package.arch 与目录架构不一致"))
    if not manifest.get("images"):
        failures.append(VerifyFailure(MANIFEST_NAME, "images 为空（没有运行时镜像）"))
    if not manifest.get("entries"):
        failures.append(VerifyFailure(MANIFEST_NAME, "entries 为空（没有可校验文件）"))
    return failures


def verify_hashes(root: Path) -> list[VerifyFailure]:
    """逐条比对 `SHA256SUMS`，并检测未登记文件。

    Args:
        root: 包根目录。

    Returns:
        list[VerifyFailure]: 每条失败都带具体路径（缺失 / 哈希不符 / 未登记）。
    """
    expected, failures = read_sha256sums(root)
    for rel, digest in sorted(expected.items()):
        path = root / rel
        if not path.is_file():
            failures.append(VerifyFailure(rel, "文件缺失"))
            continue
        actual = sha256_file(path)
        if actual != digest:
            failures.append(
                VerifyFailure(rel, f"sha256 不匹配（清单 {digest[:12]}… / 实际 {actual[:12]}…）")
            )
    listed = set(expected) | {SUMS_NAME, SIG_NAME}
    for rel in walk_files(root, []):
        if rel not in listed:
            failures.append(VerifyFailure(rel, "不在 SHA256SUMS 内（未登记或已被篡改）"))
    return failures


def verify_signature(
    root: Path, runner: Callable[[Path, Path], bool] | None = None
) -> list[VerifyFailure]:
    """校验 `SHA256SUMS` 的 detached 签名。

    Args:
        root: 包根目录。
        runner: 注入式校验器（测试用）；None 时调用 `cosign verify-blob`。

    Returns:
        list[VerifyFailure]: 失败项。
    """
    sums = root / SUMS_NAME
    signature = root / SIG_NAME
    if not signature.is_file():
        return [VerifyFailure(SIG_NAME, "签名文件缺失")]
    if runner is not None:
        if not runner(sums, signature):
            return [VerifyFailure(SIG_NAME, "签名校验失败")]
        return []
    cosign = shutil.which("cosign")
    if cosign is None:
        return [VerifyFailure(SIG_NAME, "缺少 cosign，无法校验签名")]
    key = ""
    import os

    key = os.environ.get("COSIGN_PUBLIC_KEY", "")
    if not key:
        return [VerifyFailure(SIG_NAME, "未设置 COSIGN_PUBLIC_KEY，无法校验签名")]
    out = subprocess.run(  # noqa: S603 - 参数来自受控环境变量与本包路径
        [cosign, "verify-blob", "--key", key, "--signature", str(signature), str(sums)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if out.returncode != 0:
        detail = (out.stderr or out.stdout).strip().splitlines()
        return [VerifyFailure(SIG_NAME, f"cosign 校验失败：{detail[-1] if detail else '无输出'}")]
    return []


def verify_package(
    root: Path,
    *,
    version: str | None = None,
    arch: str | None = None,
    signature_runner: Callable[[Path, Path], bool] | None = None,
) -> list[VerifyFailure]:
    """完整校验：布局 + 逐条哈希 + 未登记文件 + 签名。

    Args:
        root: 包根目录。
        version: 期望版本。
        arch: 期望架构。
        signature_runner: 注入式签名校验器。

    Returns:
        list[VerifyFailure]: 全部失败项（空 = 通过）。
    """
    failures = verify_layout(root, version=version, arch=arch)
    failures.extend(verify_hashes(root))
    failures.extend(verify_signature(root, runner=signature_runner))
    return failures


# ===========================================================================
# 仓库就绪检查（无工具、无网络）
# ===========================================================================


def _product_version() -> str:
    """产品版本（根 `package.json` 的 version，缺省 1.0.0）。

    Returns:
        str: 版本号。
    """
    try:
        data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
        value = data.get("version")
        if isinstance(value, str) and value:
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return "1.0.0"


def _sql_names(directory: Path, pattern: str = "*.sql") -> list[str]:
    """列出目录内的 SQL 文件名（排序稳定）。

    Args:
        directory: 目录。
        pattern: glob 模式。

    Returns:
        list[str]: 文件名。
    """
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.glob(pattern) if path.is_file())


def _plugin_names() -> list[str]:
    """列出离线插件目录内的文件。"""
    if not PLUGINS_DIR.is_dir():
        return []
    return sorted(path.name for path in PLUGINS_DIR.iterdir() if path.is_file())


def check_repo() -> int:
    """检查构建输入是否齐备（不要求任何外部工具）。

    Returns:
        int: 0 = 输入齐备；1 = 有缺失（打印每条）。
    """
    version = _product_version()
    failures: list[str] = []
    if not IMAGES_LOCK.is_file():
        failures.append("缺 deploy/images.lock（镜像真源）")
    compose_texts = {
        path.name: path.read_text(encoding="utf-8") for path in COMPOSE_FILES if path.is_file()
    }
    for path in COMPOSE_FILES:
        if not path.is_file():
            failures.append(f"缺编排文件 {path.relative_to(REPO_ROOT).as_posix()}")
    locked = (
        parse_images_lock(IMAGES_LOCK.read_text(encoding="utf-8")) if IMAGES_LOCK.is_file() else []
    )
    compose_refs = parse_compose_images(compose_texts, version)
    images, missing = resolve_images(compose_refs, locked)
    for ref in missing:
        failures.append(f"编排镜像 `{ref}` 未在 images.lock 登记")
    if not (MIGRATIONS_DIR / "rollback").is_dir():
        failures.append("缺 deploy/migrations/rollback（回滚 SQL）")
    if not SBOM_GENERATOR.is_file():
        failures.append("缺 deploy/sbom/generate.py（SBOM 生成器）")
    plugins = _plugin_names()
    init_files = _sql_names(REPO_ROOT / "deploy", "init-*.sql")
    plan = plan_entries(
        version,
        DEFAULT_ARCH,
        images,
        sql_files=_sql_names(MIGRATIONS_DIR),
        init_files=init_files,
        rollback_files=_sql_names(ROLLBACK_DIR),
        plugin_files=plugins,
    )
    print("=" * 70)
    print("离线介质包（3A 构建侧）仓库就绪检查")
    print("=" * 70)
    print(f"[INFO] 版本 {version} / 架构 {DEFAULT_ARCH} / 垂直 {VERTICAL_DEFAULT}")
    print(f"[INFO] 编排运行时镜像 {len(images)} 个；计划条目 {len(plan)} 条")
    migration_count = len(_sql_names(MIGRATIONS_DIR))
    rollback_count = len(_sql_names(ROLLBACK_DIR))
    print(f"[INFO] SQL：迁移 {migration_count} / 初始化 {len(init_files)} / 回滚 {rollback_count}")
    if not plugins:
        print("[WARN] deploy/offline/plugins 为空：真实构建会因“无插件包”失败（需放入 .difypkg）")
    for failure in failures:
        print(f"[FAIL] {failure}")
    if failures:
        print(f"\n[FAIL] 仓库就绪检查未通过（{len(failures)} 项）")
        return 1
    print("\n[OK] 仓库就绪检查通过（真实搬运需 skopeo/syft/cosign/uv/npm 与网络，未在本机验证）")
    return 0


# ===========================================================================
# 构建（需工具与网络；CI 不执行）
# ===========================================================================


def _missing_tools() -> dict[str, str]:
    """返回缺少的工具 → 安装提示。"""
    return {name: hint for name, hint in REQUIRED_TOOLS.items() if shutil.which(name) is None}


def _run(args: Sequence[str], *, cwd: Path | None = None, timeout: int = 3600) -> str:
    """执行外部命令，失败抛 `BuildError`（带命令与 stderr 末行）。

    Args:
        args: 命令与参数。
        cwd: 工作目录。
        timeout: 超时秒数。

    Returns:
        str: stdout。
    """
    try:
        out = subprocess.run(  # noqa: S603 - 命令为常量或受控路径
            list(args),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError(f"{' '.join(args[:2])} 执行失败：{exc}") from exc
    if out.returncode != 0:
        detail = (out.stderr or out.stdout).strip().splitlines()
        tail = detail[-1] if detail else "无输出"
        raise BuildError(f"{' '.join(args[:3])} 返回 {out.returncode}：{tail}")
    return out.stdout.strip()


def _tool_version(tool: str) -> str:
    """取工具版本（失败返回 unknown，不影响构建）。"""
    try:
        return _run([tool, "--version"], timeout=30).splitlines()[0]
    except BuildError:
        return "unknown"


def _copy(source: Path, target: Path) -> None:
    """复制文件并建目录。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_dir_files(source: Path, target: Path, pattern: str = "*") -> int:
    """复制目录内文件，返回数量。"""
    count = 0
    if not source.is_dir():
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob(pattern)):
        if path.is_file():
            shutil.copy2(path, target / path.name)
            count += 1
    return count


def render_notice(images: Sequence[ImageRef], plugins: Sequence[str]) -> str:
    """生成第三方组件与许可证清单（NOTICE）。

    Args:
        images: 随包镜像。
        plugins: 随包插件文件名。

    Returns:
        str: Markdown 文本。
    """
    lines = [
        "# 第三方组件与许可证清单（NOTICE）",
        "",
        "> 由 `deploy/validate_offline_media.py` 从 `deploy/images.lock` 的 `license` 字段",
        "> 与 `deploy/offline/plugins/` 文件名生成；许可证原文随各自制品分发。",
        "",
        "## 容器镜像",
        "",
        "| 镜像 | 许可证 | 用途 |",
        "| --- | --- | --- |",
    ]
    for image in images:
        lines.append(f"| `{image.ref}` | {image.license or '<未登记>'} | {image.role} |")
    lines.extend(["", "## 离线插件包", "", "| 文件 | 说明 |", "| --- | --- |"])
    if plugins:
        for name in plugins:
            lines.append(f"| `{name}` | 随包分发，依赖与许可证见插件内清单 |")
    else:
        lines.append("| — | 无（构建时插件目录为空） |")
    return "\n".join(lines) + "\n"


def _stage_images(stage: Path, images: Sequence[ImageRef], arch: str) -> None:
    """用 skopeo 把镜像搬成 OCI 归档（自研镜像从本地 daemon 取）。"""
    for image in images:
        target = stage / "images" / f"{image.slug}-{arch}.oci.tar"
        target.parent.mkdir(parents=True, exist_ok=True)
        if image.name.startswith("officeai/"):
            source = f"docker-daemon:{image.ref}"
        elif image.digest:
            source = f"docker://{image.name}@{image.digest}"
        else:
            source = f"docker://{image.ref}"
        _run(["skopeo", "copy", "--all", source, f"oci-archive:{target}:{image.tag}"])


def _stage_sboms(stage: Path, images: Sequence[ImageRef], arch: str) -> None:
    """syft 逐镜像生成 CycloneDX，随后生成产品级 SBOM。"""
    for image in images:
        archive = stage / "images" / f"{image.slug}-{arch}.oci.tar"
        out = stage / "sbom" / "images" / f"{image.slug}-{arch}.cdx.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        _run(["syft", f"oci-archive:{archive}", f"cyclonedx-json:{out}"])
    product = stage / "sbom" / "sbom.cdx.json"
    _run([sys.executable, str(SBOM_GENERATOR), "--out", str(product)], cwd=REPO_ROOT)


def _stage_wheels(stage: Path, work: Path) -> None:
    """导出 Python 依赖锁并下载全部 wheels（需 uv + 网络）。"""
    wheels = stage / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    requirements = wheels / "requirements.txt"
    _run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "-o",
            str(requirements),
        ],
        cwd=REPO_ROOT / "apps" / "adapter",
    )
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "-r",
            str(requirements),
            "-d",
            str(wheels),
            "--only-binary=:all:",
        ],
        cwd=work,
    )


def _stage_web(stage: Path, work: Path) -> None:
    """在临时目录跑 `npm ci` 填充离线缓存，再打成 tar.gz（需 npm + 网络）。"""
    web_work = work / "web"
    (web_work / "apps" / "web").mkdir(parents=True, exist_ok=True)
    for rel in ("package.json", "package-lock.json"):
        source = REPO_ROOT / rel
        if source.is_file():
            shutil.copy2(source, web_work / rel)
    web_package = REPO_ROOT / "apps" / "web" / "package.json"
    if web_package.is_file():
        shutil.copy2(web_package, web_work / "apps" / "web" / "package.json")
    cache = web_work / "npm-cache"
    _run(
        ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--cache", str(cache)],
        cwd=web_work,
    )
    dest = stage / "web" / "npm-cache.tar.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as archive:
        archive.add(cache, arcname="npm-cache")
    for rel in ("package.json", "package-lock.json"):
        source = REPO_ROOT / rel
        if source.is_file():
            _copy(source, stage / "web" / rel)


def build_package(out_base: Path, *, version: str, arch: str, vertical: str) -> int:
    """真实构建介质包（缺工具立即退出 2，不降级）。

    Args:
        out_base: 输出根目录（如 `dist/offline`）。
        version: 产品版本。
        arch: 目标架构。
        vertical: 垂直方向。

    Returns:
        int: 0 = 成功；2 = 缺工具/构建失败。
    """
    missing = _missing_tools()
    if missing:
        print("[FAIL] 缺少构建工具，未生成任何产物：")
        for name, hint in sorted(missing.items()):
            print(f"  - {name}：{hint}")
        return 2
    if arch not in SUPPORTED_ARCHES:
        print(f"[FAIL] 架构 `{arch}` 本轮不支持（可选：{', '.join(SUPPORTED_ARCHES)}）")
        return 2

    package = out_base / f"moy-offline-v{version}-{arch}"
    if package.exists():
        print(f"[FAIL] 目标已存在，请先移除：{package}")
        return 2

    locked = parse_images_lock(IMAGES_LOCK.read_text(encoding="utf-8"))
    compose_texts = {
        path.name: path.read_text(encoding="utf-8") for path in COMPOSE_FILES if path.is_file()
    }
    images, unresolved = resolve_images(parse_compose_images(compose_texts, version), locked)
    if unresolved:
        print(f"[FAIL] 编排镜像未在 images.lock 登记：{', '.join(unresolved)}")
        return 2
    plugins = _plugin_names()
    if not plugins:
        print(f"[FAIL] {PLUGINS_DIR.relative_to(REPO_ROOT).as_posix()} 为空：介质包缺插件包")
        return 2

    tool_versions = {name: _tool_version(name) for name in REQUIRED_TOOLS}
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with tempfile.TemporaryDirectory(prefix="moy-offline-build-") as tmp:
        work = Path(tmp)
        stage = work / package.name
        stage.mkdir(parents=True)
        try:
            print(f"[INFO] 搬运 {len(images)} 个镜像（skopeo）……")
            _stage_images(stage, images, arch)
            for name in PAYLOAD_DIRS:
                (stage / name).mkdir(parents=True, exist_ok=True)
            _copy(IMAGES_LOCK, stage / "images" / "images.lock")
            print("[INFO] 生成镜像 SBOM（syft）与产品 SBOM……")
            _stage_sboms(stage, images, arch)
            print("[INFO] 下载 Python wheels（uv export + pip download）……")
            _stage_wheels(stage, work)
            print("[INFO] 生成前端离线缓存（npm ci --cache）……")
            _stage_web(stage, work)
            _copy_dir_files(MIGRATIONS_DIR, stage / "sql" / "migrations", "*.sql")
            for path in sorted((REPO_ROOT / "deploy").glob("init-*.sql")):
                _copy(path, stage / "sql" / "init" / path.name)
            _copy_dir_files(ROLLBACK_DIR, stage / "sql" / "rollback", "*.sql")
            _copy_dir_files(PLUGINS_DIR, stage / "plugins")
            (stage / "metadata" / "NOTICE.third-party.md").write_text(
                render_notice(images, plugins), encoding="utf-8"
            )
            write_json(
                stage / "metadata" / "version.json",
                {
                    "version": version,
                    "arch": arch,
                    "vertical": vertical,
                    "built_at": started,
                    "git_commit": _git_commit(),
                },
            )
            manifest = build_manifest(
                stage,
                version=version,
                arch=arch,
                vertical=vertical,
                images=images,
                tool_versions=tool_versions,
            )
            write_json(stage / MANIFEST_NAME, manifest)
            write_sha256sums(stage, [SUMS_NAME, SIG_NAME])
            print("[INFO] 签名 SHA256SUMS（cosign sign-blob）……")
            _run(
                [
                    "cosign",
                    "sign-blob",
                    "--yes",
                    "--output-signature",
                    str(stage / SIG_NAME),
                    str(stage / SUMS_NAME),
                ]
            )
            out_base.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage), str(package))
        except (BuildError, OSError, tarfile.TarError) as exc:
            print(f"[FAIL] 构建失败：{exc}")
            return 2

    totals = manifest["totals"]
    print(f"[OK] 介质包已生成：{package}")
    print(f"     条目 {totals['entries']} 个 / {totals['bytes'] / 1024 / 1024:.1f} MiB")
    return 0


# ===========================================================================
# 自检：正样本 + 篡改检测（确定性、不依赖工具）
# ===========================================================================


def _fake_package(root: Path, *, version: str = "1.0.0", arch: str = "x86_64") -> tuple[Path, dict]:
    """在临时目录里造一个结构完整的最小介质包（假归档）。

    Args:
        root: 临时根目录。
        version: 版本。
        arch: 架构。

    Returns:
        tuple[Path, dict]: (包目录, MANIFEST)。
    """
    package = root / f"moy-offline-v{version}-{arch}"
    for name in PAYLOAD_DIRS:
        (package / name).mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {
        "images/adapter-1.0.0-x86_64.oci.tar": b"fake-oci-archive",
        "images/images.lock": b"version: 2\n",
        "wheels/requirements.txt": b"pydantic==2.13.5\n",
        "wheels/pydantic-2.13.5.whl": b"fake-wheel",
        "web/package-lock.json": b'{"lockfileVersion": 3}\n',
        "web/npm-cache.tar.gz": b"fake-npm-cache",
        "sql/migrations/0001_x.sql": b"CREATE TABLE x (id int);\n",
        "sql/init/init-schema-phase4.sql": b"CREATE SCHEMA a;\n",
        "sql/rollback/0001_x.down.sql": b"DROP TABLE x;\n",
        "plugins/plg_content_ops-1.0.0.difypkg": b"fake-difypkg",
        "sbom/sbom.cdx.json": b'{"bomFormat": "CycloneDX"}\n',
        "sbom/images/adapter-1.0.0-x86_64.cdx.json": b'{"bomFormat": "CycloneDX"}\n',
        "metadata/version.json": json.dumps({"version": version, "arch": arch}).encode(),
        "metadata/NOTICE.third-party.md": b"# NOTICE\n",
    }
    for rel, data in files.items():
        path = package / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    image = ImageRef("officeai/adapter", "1.0.0", "sha256:" + "a" * 64, "内部交付物", "适配层")
    manifest = build_manifest(
        package,
        version=version,
        arch=arch,
        vertical=VERTICAL_DEFAULT,
        images=[image],
        tool_versions={"skopeo": "fake 1.0"},
    )
    write_json(package / MANIFEST_NAME, manifest)
    write_sha256sums(package, [SUMS_NAME, SIG_NAME])
    (package / SIG_NAME).write_bytes(b"fake-signature")
    return package, manifest


def _fake_signature(sums: Path, signature: Path) -> bool:
    """测试用签名校验器：签名文件非空即视为通过。"""
    return signature.is_file() and signature.stat().st_size > 0


def run_self_test() -> int:
    """自检：正样本通过 + 8 条篡改/缺失用例必须红。

    Returns:
        int: 0 = 全部符合预期；1 = 自检未通过。
    """
    print("[SELFTEST] 离线介质包门禁自检（直接调用型：成功时退出 0）")
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        package, _ = _fake_package(root)

        def expect(label: str, problems: list[VerifyFailure], target: str) -> None:
            hit = any(target in failure.path for failure in problems)
            if not problems:
                failures.append(f"[SELFTEST] {label}：预期红灯，实际通过（未命中 {target}）")
            elif not hit:
                failures.append(
                    f"[SELFTEST] {label}：红灯了但没指向 `{target}`（实际："
                    + "; ".join(failure.path for failure in problems[:3])
                    + "）"
                )
            else:
                print(f"[SELFTEST] [OK] {label} → 红灯并指向 `{target}`")

        clean = verify_package(package, signature_runner=_fake_signature)
        if clean:
            failures.append(f"[SELFTEST] 正样本应通过，实际 {len(clean)} 项失败：{clean[:3]}")
        else:
            print("[SELFTEST] [OK] 正样本（结构+哈希+签名齐全）→ 通过")

        target = package / "images" / "adapter-1.0.0-x86_64.oci.tar"
        original = target.read_bytes()
        target.write_bytes(original[:-1] + b"X")
        expect("改 1 个字节（镜像归档）", verify_hashes(package), target.name)
        target.write_bytes(original)

        wheel = package / "wheels" / "pydantic-2.13.5.whl"
        saved = wheel.read_bytes()
        wheel.unlink()
        expect("删除 1 个已登记文件（wheel）", verify_hashes(package), wheel.name)
        wheel.write_bytes(saved)

        extra = package / "payload-extra.bin"
        extra.write_bytes(b"unlisted")
        expect("新增未登记文件", verify_hashes(package), extra.name)
        extra.unlink()

        renamed = root / "moy-offline-v1.0.0-arm64"
        package.rename(renamed)
        expect("架构与目录名不符", verify_layout(renamed, arch="x86_64"), renamed.name)
        renamed.rename(package)

        sums = package / SUMS_NAME
        sums_text = sums.read_text(encoding="utf-8")
        sums.write_text("", encoding="utf-8")
        expect("SHA256SUMS 为空", verify_hashes(package), SUMS_NAME)
        sums.write_text(sums_text, encoding="utf-8")

        expect(
            "签名校验器拒绝",
            verify_signature(package, runner=lambda _s, _g: False),
            SIG_NAME,
        )

        signature = package / SIG_NAME
        signature_bytes = signature.read_bytes()
        signature.unlink()
        expect("签名文件缺失", verify_signature(package, runner=_fake_signature), SIG_NAME)
        signature.write_bytes(signature_bytes)

    for failure in failures:
        print(failure)
    if failures:
        print(f"\n[FAIL] 自检未通过（{len(failures)} 项）")
        return 1
    print("\n[OK] 自检通过：1 条正样本 + 8 条红灯用例（含 1 字节篡改）全部符合预期")
    return 0


# ===========================================================================
# 入口
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 退出码（0 通过 / 1 校验失败 / 2 缺工具或构建失败）。
    """
    parser = argparse.ArgumentParser(description="离线介质包（3A 构建侧）")
    sub = parser.add_subparsers(dest="command")

    build = sub.add_parser("build", help="真实构建介质包（需 skopeo/syft/cosign/uv/npm 与网络）")
    build.add_argument("--out", default=str(DEFAULT_OUT), help="输出根目录（默认 dist/offline）")
    build.add_argument(
        "--version", default=_product_version(), help="产品版本（默认取 package.json）"
    )
    build.add_argument("--arch", default=DEFAULT_ARCH, choices=SUPPORTED_ARCHES, help="目标架构")
    build.add_argument("--vertical", default=VERTICAL_DEFAULT, help="垂直方向（可覆盖）")

    verify = sub.add_parser("verify", help="校验介质包（布局 + 逐条哈希 + 未登记 + 签名）")
    verify.add_argument("package", help="介质包目录（moy-offline-v<version>-<arch>）")

    sub.add_parser("self-test", help="正样本 + 篡改检测自检（无需工具）")
    sub.add_parser("check", help="仓库就绪检查（纯逻辑，默认动作）")

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "self-test":
        return run_self_test()
    if command == "check":
        return check_repo()
    if command == "build":
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO_ROOT / out
        return build_package(out, version=args.version, arch=args.arch, vertical=args.vertical)

    package = Path(args.package)
    if not package.is_absolute():
        package = REPO_ROOT / package
    if not package.is_dir():
        print(f"[FAIL] 目录不存在：{package}")
        return 1
    failures = verify_package(package)
    print("=" * 70)
    print(f"离线介质包校验：{package.name}")
    print("=" * 70)
    for failure in failures:
        print(f"[FAIL] {failure.path}：{failure.reason}")
    if failures:
        print(f"\n[FAIL] 校验未通过（{len(failures)} 项）")
        return 1
    manifest = json.loads((package / MANIFEST_NAME).read_text(encoding="utf-8"))
    totals = manifest.get("totals", {})
    size_mib = totals.get("bytes", 0) / 1024 / 1024
    print(f"\n[OK] 校验通过：{totals.get('entries', 0)} 个条目 / {size_mib:.1f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
