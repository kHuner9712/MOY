#!/usr/bin/env python3
"""离线介质包目标机加载器（3B：加载 / 升级 / 回滚）。

**定位**
--------
3A（`deploy/validate_offline_media.py`）解决"造包 + 校验"；3B 解决"到目标机上
无网落地"。本脚本只依赖 3A 的固定契约（`MANIFEST` schema
`moy-offline-manifest/v1`、`SHA256SUMS`、`SHA256SUMS.sig`、固定子目录），
不改契约、不解析目录里"有什么就跑什么"。

**四个子命令**
--------------
- `preflight`：架构 / 磁盘余量（`totals.bytes` + 20% + 512 MiB）/ 必要工具
  （`skopeo`、`cosign`、`psql`、插件导入器）；任一不满足**不进入写入阶段**。
- `verify`：布局 + 逐条 SHA256SUMS + 未登记文件 + 签名 + MANIFEST↔清单一致性；
  不过一律不写任何东西（fail-closed）。
- `load`：镜像导入（docker-daemon）、wheels 安装（`pip --no-index`）、SQL 按
  `sql/migrations/` 文件名升序、插件导入；每步成功后原子写状态，**重复执行跳过
  已完成步骤**（幂等），崩溃后可从断点继续。
- `upgrade`：按语义化版本校验（同大版本、最多跨 1 个次版本、禁止降级），通过后
  执行 load，并保存上一版本快照供 rollback。
- `rollback`：回滚 SQL（升序文件的**逆序**）→ 旧镜像重新导入 → 插件移除/恢复；
  任一步失败即停止、状态记为 `rollback_failed`、打印人工介入指令、退出码 3。

**安全**
--------
- 不执行介质包内的任何脚本；只执行清单中登记的 `.sql` / `.difypkg` / `.whl`。
- 状态文件只记录版本、路径、哈希、插件文件名与步骤；**不记录数据库口令等凭据**。

**可测性**
--------
命令执行层通过 `Executor` 注入；`self-test` 用假介质包 + 真实临时文件系统 +
假命令执行器跑完整流程（加载、幂等重入、崩溃重入、升级、回滚、回滚中途失败……）。
真实镜像导入 Docker、真实 psql、真实插件导入器仍需在目标机验证（见
`docs/offline-package.md` §11）。

用法::

    python deploy/validate_offline_apply.py preflight dist/offline/moy-offline-v1.0.0-x86_64
    python deploy/validate_offline_apply.py verify dist/offline/moy-offline-v1.0.0-x86_64
    python deploy/validate_offline_apply.py load dist/offline/moy-offline-v1.0.0-x86_64 \
        --state-dir /var/lib/moy-offline
    python deploy/validate_offline_apply.py upgrade dist/offline/moy-offline-v1.1.0-x86_64 \
        --state-dir /var/lib/moy-offline
    python deploy/validate_offline_apply.py rollback --state-dir /var/lib/moy-offline
    python deploy/validate_offline_apply.py self-test

退出码：0 = 成功；1 = 预检/校验/加载失败（未完成，可修复后重入）；3 = 回滚中途失败
（已停在安全点，需人工按输出指令介入）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

# 同目录导入：CI/目标机都以 `python deploy/validate_offline_apply.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_offline_media as media

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
STATE_SCHEMA: Final[str] = "moy-offline-state/v1"
STATE_FILE: Final[str] = "state.json"

#: 默认状态目录（目标机可覆盖；CI/自检必须传临时目录）
DEFAULT_STATE_DIR: Final[Path] = Path("/var/lib/moy-offline")

#: 磁盘余量：介质总量之外再留 20% 且不少于 512 MiB
DISK_MARGIN_RATIO: Final[float] = 0.20
DISK_MARGIN_MIN_BYTES: Final[int] = 512 * 1024 * 1024

#: 升级规则：同大版本、最多跨 1 个次版本；补丁随意
MAX_MINOR_SKIP: Final[int] = 1

#: 插件导入器（部署方提供的包装命令；`install <file>` / `remove <file>`）
DEFAULT_PLUGIN_LOADER: Final[str] = "dify-plugin-import"

MIGRATIONS_PREFIX: Final[str] = "sql/migrations/"
ROLLBACK_PREFIX: Final[str] = "sql/rollback/"
PLUGINS_PREFIX: Final[str] = "plugins/"
WHEELS_PREFIX: Final[str] = "wheels/"


class ApplyError(RuntimeError):
    """加载/升级/回滚失败（带步骤与条目，便于定位）。"""

    def __init__(self, step: str, target: str, detail: str) -> None:
        super().__init__(f"{step} `{target}` 失败：{detail}")
        self.step = step
        self.target = target
        self.detail = detail


@dataclass(frozen=True)
class ExecResult:
    """外部命令结果。"""

    returncode: int
    stdout: str
    stderr: str


class Executor(Protocol):
    """命令执行层（真实实现 / 测试替身共用）。"""

    def which(self, name: str) -> str | None:
        """返回可执行文件路径，找不到返回 None。"""
        ...

    def run(self, args: Sequence[str], cwd: Path | None = None) -> ExecResult:
        """执行命令；失败由调用方判定。"""
        ...


class RealExecutor:
    """真实执行器（subprocess + shutil.which）。"""

    def which(self, name: str) -> str | None:
        """查找可执行文件。"""
        return shutil.which(name)

    def run(self, args: Sequence[str], cwd: Path | None = None) -> ExecResult:
        """执行命令，不抛异常（返回码交给调用方判定）。"""
        try:
            out = subprocess.run(  # noqa: S603 - 命令由本脚本构造，不接受包内脚本
                list(args),
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=7200,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ExecResult(126, "", str(exc))
        return ExecResult(out.returncode, out.stdout.strip(), out.stderr.strip())


@dataclass
class Check:
    """一条预检/校验失败或状态。"""

    path: str
    reason: str


@dataclass
class ApplyContext:
    """一次操作的上下文。"""

    package: Path
    state_dir: Path
    executor: Executor
    manifest: Mapping[str, object]
    entries: Mapping[str, Mapping[str, object]]
    state: dict[str, object]
    database_url: str = ""
    plugin_loader: str = DEFAULT_PLUGIN_LOADER
    host_arch: str = field(default_factory=lambda: platform.machine())
    free_bytes: int | None = None

    @property
    def state_path(self) -> Path:
        """状态文件路径。"""
        return self.state_dir / STATE_FILE


# ===========================================================================
# 基础：清单 / 状态 / 版本
# ===========================================================================


def utc_now() -> str:
    """当前 UTC 时间（秒级，便于审计）。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    """文件 SHA-256（复用 3A 实现）。"""
    return media.sha256_file(path)


def read_manifest(package: Path) -> dict[str, object]:
    """读 MANIFEST（失败抛 ApplyError）。

    Args:
        package: 介质包目录。

    Returns:
        dict[str, object]: MANIFEST 内容。
    """
    path = package / media.MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError("manifest", media.MANIFEST_NAME, f"读取失败：{exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != media.MANIFEST_SCHEMA:
        raise ApplyError("manifest", media.MANIFEST_NAME, "schema 不是 moy-offline-manifest/v1")
    return data


def manifest_entries(manifest: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """MANIFEST 条目 → {relpath: row}（并校验路径不越界）。"""
    rows = manifest.get("entries")
    if not isinstance(rows, list) or not rows:
        raise ApplyError("manifest", "entries", "为空")
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ApplyError("manifest", "entries", "条目缺 path")
        rel = row["path"]
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise ApplyError("manifest", rel, "路径越界")
        result[rel] = row
    return result


def read_state(state_path: Path) -> dict[str, object]:
    """读状态文件（不存在返回空骨架）。"""
    if not state_path.is_file():
        return {
            "schema": STATE_SCHEMA,
            "current_version": "",
            "status": "never_loaded",
            "applied": {},
            "previous": None,
            "history": [],
        }
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError("state", str(state_path), f"解析失败：{exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        raise ApplyError("state", str(state_path), "schema 不是 moy-offline-state/v1")
    data.setdefault("applied", {})
    data.setdefault("history", [])
    return data


def write_state(state_path: Path, state: Mapping[str, object]) -> None:
    """原子写状态文件（同目录临时文件 + `os.replace`）。

    Args:
        state_path: 状态文件路径。
        state: 内容。
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=".state-", dir=str(state_path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp_name, state_path)
    finally:
        if Path(temp_name).exists():
            Path(temp_name).unlink(missing_ok=True)


def parse_version(value: str) -> tuple[int, int, int]:
    """解析 `X.Y.Z`（非语义化版本抛 ApplyError）。

    Args:
        value: 版本字符串。

    Returns:
        tuple[int, int, int]: (major, minor, patch)。
    """
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", value.strip())
    if not match:
        raise ApplyError("version", value, "不是 X.Y.Z 形式")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def upgrade_allowed(old: str, new: str) -> tuple[bool, str]:
    """判断旧版本 → 新版本是否允许（可跳版本上限见 `MAX_MINOR_SKIP`）。

    Args:
        old: 当前版本。
        new: 目标版本。

    Returns:
        tuple[bool, str]: (是否允许, 拒绝理由或空串)。
    """
    try:
        old_v = parse_version(old)
        new_v = parse_version(new)
    except ApplyError as exc:
        return False, str(exc)
    if new_v <= old_v:
        return False, f"目标版本 {new} 不高于当前 {old}（降级请用 rollback）"
    if new_v[0] != old_v[0]:
        return False, f"禁止跨大版本升级：{old} → {new}"
    if new_v[1] - old_v[1] > MAX_MINOR_SKIP:
        return False, f"最多跨 {MAX_MINOR_SKIP} 个次版本：{old} → {new}，请逐级升级"
    return True, ""


def package_fingerprint(package: Path) -> str:
    """介质指纹：`SHA256SUMS` 的哈希（加签后即代表整包）。"""
    sums = package / media.SUMS_NAME
    if not sums.is_file():
        raise ApplyError("verify", media.SUMS_NAME, "文件缺失")
    return sha256_file(sums)


def path_is_within(root: Path, rel: str) -> bool:
    """确认清单路径落在包内（防 `..` 与绝对路径）。"""
    target = (root / rel).resolve()
    return root.resolve() in target.parents and target.is_file()


def entries_under(
    package: Path, entries: Mapping[str, Mapping[str, object]], prefix: str
) -> list[tuple[str, Path]]:
    """取清单中某前缀下的文件，按相对路径升序（不扫描目录）。

    Args:
        package: 包目录。
        entries: MANIFEST 条目。
        prefix: 前缀（如 `sql/migrations/`）。

    Returns:
        list[tuple[str, Path]]: (相对路径, 绝对路径)。
    """
    selected = []
    for rel in sorted(entries):
        if not rel.startswith(prefix) or not rel.endswith((".sql", ".difypkg", ".whl", ".txt")):
            continue
        if not path_is_within(package, rel):
            raise ApplyError("manifest", rel, "文件不在包内或缺失")
        selected.append((rel, package / rel))
    return selected


# ===========================================================================
# 预检 / 校验（写入前的全部否决点）
# ===========================================================================


def preflight(
    package: Path,
    *,
    state_dir: Path,
    executor: Executor,
    plugin_loader: str = DEFAULT_PLUGIN_LOADER,
    host_arch: str | None = None,
    free_bytes: int | None = None,
) -> tuple[list[Check], dict[str, object] | None]:
    """写入前预检：架构、磁盘余量、必要工具。

    Args:
        package: 介质包目录。
        state_dir: 状态目录（磁盘余量按它的最近存在父目录计算）。
        executor: 命令执行层。
        plugin_loader: 插件导入器命令名。
        host_arch: 覆盖宿主架构（测试用）。
        free_bytes: 覆盖可用磁盘字节数（测试用）。

    Returns:
        tuple[list[Check], dict[str, object] | None]: (失败项, MANIFEST)。
    """
    checks: list[Check] = []
    manifest: dict[str, object] | None = None
    if not package.is_dir():
        return [Check(str(package), "介质包目录不存在")], None
    try:
        manifest = read_manifest(package)
    except ApplyError as exc:
        return [Check(exc.target, exc.detail)], None
    arch = host_arch or platform.machine()
    normalized = "x86_64" if arch.lower() in {"amd64", "x86_64"} else arch.lower()
    package_info = manifest.get("package")
    want_arch = package_info.get("arch") if isinstance(package_info, dict) else None
    if want_arch != normalized:
        checks.append(Check("arch", f"介质架构 {want_arch} 与宿主 {normalized} 不符"))
    if want_arch not in media.SUPPORTED_ARCHES:
        checks.append(Check("arch", f"架构 {want_arch} 本轮不支持"))
    totals = manifest.get("totals")
    need = int(totals.get("bytes", 0)) if isinstance(totals, dict) else 0
    margin = max(int(need * DISK_MARGIN_RATIO), DISK_MARGIN_MIN_BYTES)
    anchor = state_dir
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    free = free_bytes if free_bytes is not None else shutil.disk_usage(anchor).free
    if free < need + margin:
        checks.append(
            Check(
                "disk",
                f"磁盘不足：需要 {need + margin} 字节（介质 {need} + 余量 {margin}），可用 {free}",
            )
        )
    for tool in ("skopeo", "cosign", "psql", plugin_loader):
        if executor.which(tool) is None:
            checks.append(Check(f"tool:{tool}", f"缺少工具 `{tool}`（请先安装后重跑预检）"))
    return checks, manifest


def verify_before_write(
    package: Path, signature_runner: Callable[[Path, Path], bool] | None = None
) -> list[Check]:
    """写入前校验：3A 全量校验 + MANIFEST↔SHA256SUMS 一致性。

    Args:
        package: 包目录。
        signature_runner: 注入式签名校验器（测试用）。

    Returns:
        list[Check]: 失败项（空 = 可写入）。
    """
    failures = media.verify_package(package, signature_runner=signature_runner)
    checks = [Check(failure.path, failure.reason) for failure in failures]
    try:
        manifest = read_manifest(package)
        entries = manifest_entries(manifest)
        expected, parse_failures = media.read_sha256sums(package)
        checks.extend(Check(failure.path, failure.reason) for failure in parse_failures)
        for rel, row in entries.items():
            recorded = row.get("sha256") if isinstance(row, dict) else None
            if expected.get(rel) != recorded:
                checks.append(Check(rel, "MANIFEST 记录与 SHA256SUMS 不一致"))
        for rel in expected:
            # MANIFEST 自身由 3A 的哈希校验覆盖，不属于 payload entries
            if rel == media.MANIFEST_NAME:
                continue
            if rel not in entries:
                checks.append(Check(rel, "SHA256SUMS 条目未登记进 MANIFEST"))
    except ApplyError as exc:
        checks.append(Check(exc.target, exc.detail))
    return checks


# ===========================================================================
# 步骤执行（幂等 + 断点续跑）
# ===========================================================================


def _run_step(
    context: ApplyContext,
    step: str,
    target: str,
    args: Sequence[str],
    *,
    key: str,
) -> None:
    """执行一步并落状态；已完成则跳过（幂等）。

    Args:
        context: 上下文。
        step: 步骤名（image / wheels / sql / plugin）。
        target: 清单相对路径或标识。
        args: 命令与参数。
        key: 幂等键。
    """
    applied = context.state.setdefault("applied", {})
    assert isinstance(applied, dict)
    if applied.get(key):
        print(f"[SKIP] {step} {target}（已完成）")
        return
    print(f"[RUN ] {step} {target}")
    result = context.executor.run(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).splitlines()
        raise ApplyError(step, target, detail[-1] if detail else f"退出码 {result.returncode}")
    applied[key] = {"at": utc_now(), "target": target}
    write_state(context.state_path, context.state)


def _import_images(context: ApplyContext) -> None:
    """按 MANIFEST 的 images[] 导入镜像（幂等键 = 归档相对路径）。"""
    rows = context.manifest.get("images")
    if not isinstance(rows, list) or not rows:
        raise ApplyError("manifest", "images", "为空")
    for row in rows:
        if not isinstance(row, dict):
            raise ApplyError("manifest", "images", "条目格式不合法")
        archive = str(row.get("archive", ""))
        ref = str(row.get("ref", ""))
        if not path_is_within(context.package, archive) or not ref:
            raise ApplyError("image", archive, "归档缺失或 ref 为空")
        tag = ref.rsplit(":", 1)[-1]
        args = [
            "skopeo",
            "copy",
            "--all",
            f"oci-archive:{context.package / archive}:{tag}",
            f"docker-daemon:{ref}",
        ]
        _run_step(context, "image", archive, args, key=f"image:{archive}")


def _install_wheels(context: ApplyContext) -> None:
    """离线安装 wheels（幂等键 = requirements.txt 的 sha256）。"""
    requirements = context.package / "wheels" / "requirements.txt"
    if not requirements.is_file():
        raise ApplyError("wheels", "wheels/requirements.txt", "文件缺失")
    wheels_dir = context.package / "wheels"
    args = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(wheels_dir),
        "-r",
        str(requirements),
    ]
    key = f"pip:{sha256_file(requirements)}"
    _run_step(context, "wheels", "wheels/requirements.txt", args, key=key)


def _run_sql(context: ApplyContext, prefix: str, *, reverse: bool) -> None:
    """按清单文件名顺序执行 SQL（migrations 升序 / rollback 逆序）。"""
    pairs = entries_under(context.package, context.entries, prefix)
    if not pairs:
        raise ApplyError("sql", prefix, "清单中没有 SQL 文件")
    if not context.database_url:
        raise ApplyError("sql", prefix, "未设置 OA_DATABASE_URL")
    ordered = list(reversed(pairs)) if reverse else pairs
    for rel, path in ordered:
        args = [
            "psql",
            context.database_url,
            "-v",
            "ON_ERROR_STOP=1",
            "--single-transaction",
            "-f",
            str(path),
        ]
        _run_step(context, "sql", rel, args, key=f"sql:{rel}")


def _import_plugins(context: ApplyContext, *, remove: bool = False) -> None:
    """导入/移除插件（逐文件、按清单顺序；幂等键区分 install/remove）。"""
    pairs = entries_under(context.package, context.entries, PLUGINS_PREFIX)
    if not pairs:
        raise ApplyError("plugin", PLUGINS_PREFIX, "清单中没有插件包")
    for rel, path in pairs:
        action = "remove" if remove else "install"
        args = [context.plugin_loader, action, str(path)]
        _run_step(context, "plugin", rel, args, key=f"plugin:{action}:{rel}")


def _build_context(
    package: Path,
    state_dir: Path,
    executor: Executor,
    *,
    plugin_loader: str,
    database_url: str,
    host_arch: str | None,
    free_bytes: int | None,
) -> tuple[ApplyContext | None, list[Check]]:
    """预检 + 校验 + 读状态，构成上下文。"""
    checks, manifest = preflight(
        package,
        state_dir=state_dir,
        executor=executor,
        plugin_loader=plugin_loader,
        host_arch=host_arch,
        free_bytes=free_bytes,
    )
    if checks or manifest is None:
        return None, checks
    checks = verify_before_write(package)
    if checks:
        return None, checks
    try:
        entries = manifest_entries(manifest)
        state = read_state(state_dir / STATE_FILE)
    except ApplyError as exc:
        return None, [Check(exc.target, exc.detail)]
    context = ApplyContext(
        package=package,
        state_dir=state_dir,
        executor=executor,
        manifest=manifest,
        entries=entries,
        state=state,
        database_url=database_url,
        plugin_loader=plugin_loader,
        host_arch=host_arch or platform.machine(),
        free_bytes=free_bytes,
    )
    return context, []


def _record_success(context: ApplyContext, action: str, version: str, vertical: str) -> None:
    """写成功状态（含介质指纹与已执行步骤）。"""
    context.state.pop("pending_package_dir", None)
    context.state.update(
        {
            "schema": STATE_SCHEMA,
            "current_version": version,
            "vertical": vertical,
            "arch": context.manifest.get("package", {}).get("arch", ""),
            "package_dir": str(context.package.resolve()),
            "fingerprint": package_fingerprint(context.package),
            "status": "loaded",
            "updated_at": utc_now(),
            "last_action": action,
        }
    )
    history = context.state.setdefault("history", [])
    assert isinstance(history, list)
    history.append({"action": action, "version": version, "at": utc_now()})
    write_state(context.state_path, context.state)


def load_package(
    package: Path,
    *,
    state_dir: Path,
    executor: Executor | None = None,
    database_url: str = "",
    plugin_loader: str = DEFAULT_PLUGIN_LOADER,
    host_arch: str | None = None,
    free_bytes: int | None = None,
    signature_runner: Callable[[Path, Path], bool] | None = None,
    allow_version_change: bool = False,
) -> int:
    """加载介质包（幂等、可断点续跑）。

    Args:
        package: 介质包目录。
        state_dir: 状态目录。
        executor: 命令执行层。
        database_url: PostgreSQL 连接串（不入状态文件）。
        plugin_loader: 插件导入器命令。
        host_arch: 覆盖宿主架构（测试）。
        free_bytes: 覆盖磁盘可用字节（测试）。
        signature_runner: 注入式签名校验器（测试）。
        allow_version_change: 允许版本变化（仅 `upgrade` 内部使用）。

    Returns:
        int: 0 = 成功；1 = 失败（状态已记录断点）。
    """
    executor = executor or RealExecutor()
    checks, _ = preflight(
        package,
        state_dir=state_dir,
        executor=executor,
        plugin_loader=plugin_loader,
        host_arch=host_arch,
        free_bytes=free_bytes,
    )
    if checks:
        for check in checks:
            print(f"[FAIL] {check.path}：{check.reason}")
        print("[FAIL] 预检未通过：未写入任何内容")
        return 1
    checks = verify_before_write(package, signature_runner=signature_runner)
    if checks:
        for check in checks:
            print(f"[FAIL] {check.path}：{check.reason}")
        print("[FAIL] 介质校验未通过：未写入任何内容")
        return 1
    manifest = read_manifest(package)
    entries = manifest_entries(manifest)
    state = read_state(state_dir / STATE_FILE)
    existing_fp = state.get("fingerprint")
    existing_version = str(state.get("current_version", ""))
    fingerprint = package_fingerprint(package)
    version = str(manifest.get("package", {}).get("version", ""))
    if (
        existing_fp
        and existing_fp != fingerprint
        and (not existing_version or existing_version == version)
    ):
        print("[FAIL] 介质指纹与已记录不一致：同一版本换包必须人工确认或走升级流程")
        return 1
    if existing_version and existing_version != version and not allow_version_change:
        print(f"[FAIL] 当前已加载 {existing_version}：跨版本变更必须用 upgrade，不能直接用 load")
        return 1
    if (
        existing_fp
        and existing_fp == fingerprint
        and existing_version == version
        and state.get("status") == "loaded"
    ):
        print(f"[OK] {version} 已加载且指纹一致，无需重复执行")
        return 0
    context = ApplyContext(
        package=package,
        state_dir=state_dir,
        executor=executor,
        manifest=manifest,
        entries=entries,
        state=state,
        database_url=database_url,
        plugin_loader=plugin_loader,
        host_arch=host_arch or platform.machine(),
        free_bytes=free_bytes,
    )
    context.state.update(
        {
            "schema": STATE_SCHEMA,
            "status": "loading",
            "fingerprint": fingerprint,
            "pending_package_dir": str(package.resolve()),
            "started_at": utc_now(),
        }
    )
    write_state(context.state_path, context.state)
    try:
        _import_images(context)
        _install_wheels(context)
        _run_sql(context, MIGRATIONS_PREFIX, reverse=False)
        _import_plugins(context)
    except ApplyError as exc:
        context.state["status"] = "partial"
        context.state["last_error"] = {"step": exc.step, "target": exc.target, "detail": exc.detail}
        context.state["updated_at"] = utc_now()
        write_state(context.state_path, context.state)
        print(f"[FAIL] {exc}")
        print("[FAIL] 已停止；修复后重跑同一命令会从断点继续（已完成步骤跳过）")
        return 1
    _record_success(context, "load", version, str(manifest.get("package", {}).get("vertical", "")))
    print(f"[OK] 已加载 {version}：{package}")
    return 0


def upgrade_package(
    package: Path,
    *,
    state_dir: Path,
    executor: Executor | None = None,
    database_url: str = "",
    plugin_loader: str = DEFAULT_PLUGIN_LOADER,
    host_arch: str | None = None,
    free_bytes: int | None = None,
    signature_runner: Callable[[Path, Path], bool] | None = None,
) -> int:
    """升级到新版本介质（先做版本兼容校验，再走 load）。

    Args:
        package: 新版本介质包。
        state_dir: 状态目录。
        其余参数同 `load_package`。

    Returns:
        int: 0 = 成功；1 = 拒绝或失败。
    """
    state = read_state(state_dir / STATE_FILE)
    current = str(state.get("current_version", ""))
    if not current:
        print("[FAIL] 状态中没有已加载版本：首次安装请用 load")
        return 1
    try:
        new_version = str(read_manifest(package).get("package", {}).get("version", ""))
    except ApplyError as exc:
        print(f"[FAIL] {exc}")
        return 1
    allowed, reason = upgrade_allowed(current, new_version)
    if not allowed:
        print(f"[FAIL] 升级被拒绝：{reason}")
        return 1
    previous = {
        "version": current,
        "package_dir": state.get("package_dir", ""),
        "images": state.get("images", []),
        "plugins": state.get("plugins", []),
        "at": utc_now(),
    }
    # 先落 previous 快照再动数据库：升级中途失败也能用 rollback 回到旧版本
    state["previous"] = previous
    state["last_action"] = "upgrade_started"
    state["updated_at"] = utc_now()
    write_state(state_dir / STATE_FILE, state)
    status = load_package(
        package,
        state_dir=state_dir,
        executor=executor,
        database_url=database_url,
        plugin_loader=plugin_loader,
        host_arch=host_arch,
        free_bytes=free_bytes,
        signature_runner=signature_runner,
        allow_version_change=True,
    )
    if status != 0:
        print(f"[FAIL] 升级未完成；已保留 previous 快照，可执行 rollback 回到 {current}")
        return status
    state = read_state(state_dir / STATE_FILE)
    manifest = read_manifest(package)
    images = manifest.get("images", [])
    state["previous"] = previous
    state["images"] = images if isinstance(images, list) else []
    state["plugins"] = [
        rel for rel, _path in entries_under(package, manifest_entries(manifest), PLUGINS_PREFIX)
    ]
    state["last_action"] = "upgrade"
    state["updated_at"] = utc_now()
    history = state.setdefault("history", [])
    assert isinstance(history, list)
    history.append({"action": "upgrade", "version": new_version, "at": utc_now()})
    write_state(state_dir / STATE_FILE, state)
    print(f"[OK] 已从 {current} 升级到 {new_version}")
    return 0


def rollback_package(
    *,
    state_dir: Path,
    executor: Executor | None = None,
    database_url: str = "",
    plugin_loader: str = DEFAULT_PLUGIN_LOADER,
    host_arch: str | None = None,
    free_bytes: int | None = None,
    signature_runner: Callable[[Path, Path], bool] | None = None,
) -> int:
    """回滚到上一版本；失败停在安全状态并给出人工指令（退出码 3）。

    Args:
        state_dir: 状态目录。
        其余参数同 `load_package`。

    Returns:
        int: 0 = 成功；1 = 前置条件不满足；3 = 回滚中途失败（需人工）。
    """
    state = read_state(state_dir / STATE_FILE)
    previous = state.get("previous")
    current_version = str(state.get("current_version", ""))
    package_dir = Path(str(state.get("package_dir", ""))) if state.get("package_dir") else None
    old_dir = Path(str(previous.get("package_dir", ""))) if isinstance(previous, dict) else None
    if not current_version or not isinstance(previous, dict) or not old_dir or not old_dir.is_dir():
        print("[FAIL] 没有可回滚的上一版本记录（或旧介质包已不存在）")
        return 1
    old_version = str(previous.get("version", ""))
    checks = verify_before_write(old_dir, signature_runner=signature_runner)
    if checks:
        for check in checks:
            print(f"[FAIL] 旧介质 {check.path}：{check.reason}")
        print("[FAIL] 旧介质校验未通过：回滚未开始")
        return 1
    manifest = read_manifest(old_dir)
    entries = manifest_entries(manifest)
    if not package_dir or not package_dir.is_dir():
        state["status"] = "rollback_failed"
        state["rollback_completed"] = []
        state["rollback_pending"] = ["sql", "images", "plugins"]
        state["last_error"] = {
            "step": "precondition",
            "target": str(package_dir or ""),
            "detail": "当前版本介质包不存在",
        }
        state["updated_at"] = utc_now()
        write_state(state_dir / STATE_FILE, state)
        print("[FAIL] 当前版本介质包不存在：无法自动执行 down SQL，回滚未开始。")
        print("人工介入：")
        print("  1. 找回当前版本介质包并放回原路径，或按该版本 sql/rollback/ 逆序手工执行；")
        print("  2. 完成后把状态文件 status 改为 rolled_back 并核对版本；")
        print("  3. 回滚完成前不要启动业务容器，避免新旧 schema 混用。")
        return 3
    current_checks = verify_before_write(package_dir, signature_runner=signature_runner)
    if current_checks:
        for check in current_checks:
            print(f"[FAIL] 当前介质 {check.path}：{check.reason}")
        print("[FAIL] 当前介质校验未通过：回滚未开始（未执行任何包内 SQL）")
        return 1
    old_context = ApplyContext(
        package=old_dir,
        state_dir=state_dir,
        executor=executor or RealExecutor(),
        manifest=manifest,
        entries=entries,
        state=state,
        database_url=database_url,
        plugin_loader=plugin_loader,
        host_arch=host_arch or platform.machine(),
        free_bytes=free_bytes,
    )
    current_manifest = read_manifest(package_dir)
    current_context = ApplyContext(
        package=package_dir,
        state_dir=state_dir,
        executor=old_context.executor,
        manifest=current_manifest,
        entries=manifest_entries(current_manifest),
        state=state,
        database_url=database_url,
        plugin_loader=plugin_loader,
        host_arch=old_context.host_arch,
        free_bytes=free_bytes,
    )
    state["status"] = "rolling_back"
    state["rollback_from"] = current_version
    state["rollback_to"] = old_version
    write_state(state_dir / STATE_FILE, state)
    completed: list[str] = []
    pending: list[str] = []
    try:
        # 1) 当前版本的 down SQL，按文件名逆序
        _run_sql(current_context, ROLLBACK_PREFIX, reverse=True)
        completed.append("sql")
        # 2) 旧版本镜像重新导入（tag 回退）
        _import_images(old_context)
        completed.append("images")
        # 3) 插件：移除当前版本，恢复旧版本
        _import_plugins(current_context, remove=True)
        _import_plugins(old_context)
        completed.append("plugins")
    except ApplyError as exc:
        pending = ["sql", "images", "plugins"][len(completed) :]
        state["status"] = "rollback_failed"
        state["last_error"] = {"step": exc.step, "target": exc.target, "detail": exc.detail}
        state["rollback_completed"] = completed
        state["rollback_pending"] = pending
        state["updated_at"] = utc_now()
        write_state(state_dir / STATE_FILE, state)
        print(f"[FAIL] 回滚在 {exc.step} `{exc.target}` 处失败：{exc.detail}")
        print("[FAIL] 已停止，未标记成功。当前可能处于混合状态（部分新版本/部分旧版本）。")
        print("人工介入：")
        print("  1. 修复上述失败（数据库/镜像/插件）后，重跑同一 rollback 命令；")
        print(f"     已完成步骤会跳过，从 {pending[0] if pending else exc.step} 继续。")
        print(f"  2. 若无法继续，按状态文件 {state_dir / STATE_FILE} 的 rollback_completed")
        print("     与 rollback_pending 手工执行；回滚完成后把 status 改为 rolled_back。")
        print("  3. 回滚完成前不要启动业务容器，避免新旧 schema 混用。")
        return 3
    state.update(
        {
            "status": "rolled_back",
            "current_version": old_version,
            "package_dir": str(old_dir.resolve()),
            "fingerprint": package_fingerprint(old_dir),
            "previous": None,
            "images": manifest.get("images", []),
            "plugins": [rel for rel, _path in entries_under(old_dir, entries, PLUGINS_PREFIX)],
            "updated_at": utc_now(),
            "last_action": "rollback",
        }
    )
    history = state.setdefault("history", [])
    assert isinstance(history, list)
    history.append({"action": "rollback", "version": old_version, "at": utc_now()})
    write_state(state_dir / STATE_FILE, state)
    print(f"[OK] 已回滚到 {old_version}")
    return 0


# ===========================================================================
# 自检（CI 可跑：假介质 + 真实临时文件系统 + 假命令执行器）
# ===========================================================================


class FakeExecutor:
    """测试替身：记录命令，可按条件失败，可模拟缺工具。"""

    def __init__(
        self, missing: Sequence[str] = (), fail_when: Callable[[Sequence[str]], bool] | None = None
    ) -> None:
        self.calls: list[list[str]] = []
        self.missing = set(missing)
        self.fail_when = fail_when

    def which(self, name: str) -> str | None:
        """模拟工具发现。"""
        return None if name in self.missing else f"/usr/bin/{name}"

    def run(self, args: Sequence[str], cwd: Path | None = None) -> ExecResult:
        """记录并可选失败。"""
        self.calls.append(list(args))
        if self.fail_when and self.fail_when(args):
            return ExecResult(1, "", "fake failure")
        return ExecResult(0, "", "")


def _fake_package(root: Path, version: str, *, tamper: bool = False) -> Path:
    """造一个最小可用介质包（真实文件、假归档/假签名）。"""
    package = root / f"moy-offline-v{version}-x86_64"
    for name in media.PAYLOAD_DIRS:
        (package / name).mkdir(parents=True, exist_ok=True)
    image = media.ImageRef("officeai/adapter", version, "sha256:" + "a" * 64, "内部", "适配层")
    files = {
        f"images/officeai-adapter-{version}-x86_64.oci.tar": b"fake-oci",
        "images/images.lock": b"version: 2\n",
        "wheels/requirements.txt": b"pydantic==2.13.5\n",
        "wheels/pydantic-2.13.5.whl": b"fake-wheel",
        "web/package-lock.json": b"{}",
        "web/npm-cache.tar.gz": b"fake-cache",
        "sql/migrations/0001_init.sql": b"CREATE TABLE a (id int);\n",
        "sql/migrations/0002_more.sql": b"ALTER TABLE a ADD COLUMN b int;\n",
        "sql/init/init-schema.sql": b"CREATE SCHEMA a;\n",
        "sql/rollback/0002_more.down.sql": b"ALTER TABLE a DROP COLUMN b;\n",
        "sql/rollback/0001_init.down.sql": b"DROP TABLE a;\n",
        "plugins/plg_content_ops-1.0.0.difypkg": b"fake-difypkg",
        "sbom/sbom.cdx.json": b"{}",
        f"sbom/images/officeai-adapter-{version}-x86_64.cdx.json": b"{}",
        "metadata/version.json": json.dumps({"version": version}).encode(),
        "metadata/NOTICE.third-party.md": b"# NOTICE\n",
    }
    for rel, data in files.items():
        path = package / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = media.build_manifest(
        package,
        version=version,
        arch="x86_64",
        vertical=media.VERTICAL_DEFAULT,
        images=[image],
        tool_versions={"skopeo": "fake"},
    )
    media.write_json(package / media.MANIFEST_NAME, manifest)
    media.write_sha256sums(package, [media.SUMS_NAME, media.SIG_NAME])
    (package / media.SIG_NAME).write_bytes(b"fake-sig")
    if tamper:
        (package / f"images/officeai-adapter-{version}-x86_64.oci.tar").write_bytes(b"fake-oci!")
    return package


def _signature_ok(sums: Path, signature: Path) -> bool:
    """测试签名器：签名文件非空即通过。"""
    return sums.is_file() and signature.is_file() and signature.stat().st_size > 0


def run_self_test() -> int:
    """自检整条 3B 链路（CI 真跑，无 Docker/网络）。

    Returns:
        int: 0 = 全部符合预期；1 = 有不符合项。
    """
    print("[SELFTEST] 离线介质目标机链路（3B）")
    failures: list[str] = []
    ample = 100_000_000_000

    def expect(label: str, actual: bool) -> None:
        if actual:
            print(f"[SELFTEST] [OK] {label}")
        else:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state"
        p1 = _fake_package(root, "1.0.0")
        p11 = _fake_package(root, "1.1.0")
        p15 = _fake_package(root, "1.5.0")
        db = "postgresql://reader@db/app"

        checks, _manifest = preflight(
            p1, state_dir=state, executor=FakeExecutor(), host_arch="x86_64", free_bytes=ample
        )
        expect("预检通过（架构/磁盘/工具齐全）", checks == [])

        checks, _ = preflight(
            p1,
            state_dir=state,
            executor=FakeExecutor(),
            host_arch="x86_64",
            free_bytes=100,
        )
        expect("磁盘不足预检拒绝", any(check.path == "disk" for check in checks))

        checks, _ = preflight(
            p1,
            state_dir=state,
            executor=FakeExecutor(missing=("skopeo",)),
            host_arch="x86_64",
            free_bytes=ample,
        )
        expect("缺工具预检拒绝", any(check.path == "tool:skopeo" for check in checks))

        tampered = _fake_package(root, "1.0.1", tamper=True)
        expect(
            "签名/哈希失败拒绝", verify_before_write(tampered, signature_runner=_signature_ok) != []
        )

        mismatched = _fake_package(root, "1.0.2")
        manifest = read_manifest(mismatched)
        rows = manifest["entries"]
        assert isinstance(rows, list)
        rows[0]["sha256"] = "0" * 64
        media.write_json(mismatched / media.MANIFEST_NAME, manifest)
        media.write_sha256sums(mismatched, [media.SUMS_NAME, media.SIG_NAME])
        expect(
            "MANIFEST 与清单不一致拒绝",
            any(
                "MANIFEST" in check.reason
                for check in verify_before_write(mismatched, signature_runner=_signature_ok)
            ),
        )

        executor = FakeExecutor()
        status = load_package(
            p1,
            state_dir=state,
            executor=executor,
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("正常加载成功", status == 0)
        loaded = read_state(state / STATE_FILE)
        expect(
            "加载后状态为 loaded 且版本正确",
            loaded.get("status") == "loaded" and loaded.get("current_version") == "1.0.0",
        )
        expect("未记录数据库连接串", db not in json.dumps(loaded, ensure_ascii=False))

        before = len(executor.calls)
        status = load_package(
            p1,
            state_dir=state,
            executor=executor,
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("幂等重入不重复执行", status == 0 and len(executor.calls) == before)

        crash_state = root / "crash-state"
        crash_executor = FakeExecutor(fail_when=lambda args: "0001_init.sql" in " ".join(args))
        status = load_package(
            p1,
            state_dir=crash_state,
            executor=crash_executor,
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("加载中途失败非零退出", status == 1)
        partial = read_state(crash_state / STATE_FILE)
        expect("失败后状态为 partial（可续跑）", partial.get("status") == "partial")
        resume_executor = FakeExecutor()
        status = load_package(
            p1,
            state_dir=crash_state,
            executor=resume_executor,
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        resumed = [call for call in resume_executor.calls if "skopeo" in " ".join(call)]
        first_sql = [call for call in resume_executor.calls if "0001_init.sql" in " ".join(call)]
        expect("崩溃后重入只跑剩余步骤", status == 0 and restored(resumed) and len(first_sql) == 1)

        upgrade_state = root / "upgrade-state"
        status = load_package(
            p1,
            state_dir=upgrade_state,
            executor=FakeExecutor(),
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("升级前先完成 1.0.0 安装", status == 0)
        status = upgrade_package(
            p15,
            state_dir=upgrade_state,
            executor=FakeExecutor(),
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("跨多个次版本的升级被拒绝", status == 1)
        status = upgrade_package(
            p1,
            state_dir=upgrade_state,
            executor=FakeExecutor(),
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("同版本/降级升级被拒绝", status == 1)
        status = upgrade_package(
            p11,
            state_dir=upgrade_state,
            executor=FakeExecutor(),
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("合法升级 1.0.0→1.1.0 成功", status == 0)
        upgraded = read_state(upgrade_state / STATE_FILE)
        expect("升级后保存上一版本快照", isinstance(upgraded.get("previous"), dict))

        status = rollback_package(
            state_dir=upgrade_state,
            executor=FakeExecutor(),
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        expect("回滚成功", status == 0)
        rolled = read_state(upgrade_state / STATE_FILE)
        expect(
            "回滚后版本回到 1.0.0",
            rolled.get("current_version") == "1.0.0" and rolled.get("status") == "rolled_back",
        )

        fail_state = root / "rollback-fail-state"
        load_package(
            p1,
            state_dir=fail_state,
            executor=FakeExecutor(),
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        upgrade_package(
            p11,
            state_dir=fail_state,
            executor=FakeExecutor(),
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        failing = FakeExecutor(fail_when=lambda args: "0001_init.down.sql" in " ".join(args))
        status = rollback_package(
            state_dir=fail_state,
            executor=failing,
            database_url=db,
            host_arch="x86_64",
            free_bytes=ample,
            signature_runner=_signature_ok,
        )
        failed_state = read_state(fail_state / STATE_FILE)
        expect("回滚中途失败退出码 3", status == 3)
        expect(
            "回滚失败停在 rollback_failed 并记录待办",
            failed_state.get("status") == "rollback_failed"
            and failed_state.get("rollback_pending") == ["sql", "images", "plugins"],
        )

    for failure in failures:
        print(f"[SELFTEST] [FAIL] {failure}")
    if failures:
        print(f"\n[FAIL] 自检未通过（{len(failures)} 项）")
        return 1
    print("\n[OK] 自检通过：预检/校验/加载/幂等/崩溃重入/升级/回滚/回滚失败全部符合预期")
    return 0


def restored(resumed_calls: Sequence[Sequence[str]]) -> bool:
    """辅助断言：崩溃重入后不应再搬镜像。"""
    return not any("skopeo" in " ".join(call) for call in resumed_calls)


# ===========================================================================
# 入口
# ===========================================================================


def _print_checks(checks: Sequence[Check], label: str) -> int:
    """打印检查项并返回退出码。"""
    for check in checks:
        print(f"[FAIL] {check.path}：{check.reason}")
    print(f"[FAIL] {label}（{len(checks)} 项）")
    return 1


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="离线介质包目标机加载/升级/回滚（3B）")
    sub = parser.add_subparsers(dest="command")

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="状态目录")
        target.add_argument(
            "--database-url",
            default=os.environ.get("OA_DATABASE_URL", ""),
            help="PG 连接串（不落状态文件）",
        )
        target.add_argument("--plugin-loader", default=DEFAULT_PLUGIN_LOADER, help="插件导入器命令")

    pre = sub.add_parser("preflight", help="架构/磁盘/工具预检（不写入）")
    pre.add_argument("package")
    common(pre)

    ver = sub.add_parser("verify", help="签名与 SHA256SUMS 校验（不写入）")
    ver.add_argument("package")

    load = sub.add_parser("load", help="加载介质包（幂等、断点续跑）")
    load.add_argument("package")
    common(load)

    upg = sub.add_parser("upgrade", help="升级到新版本介质")
    upg.add_argument("package")
    common(upg)

    roll = sub.add_parser("rollback", help="回滚到上一版本")
    common(roll)

    sub.add_parser("self-test", help="假介质 + 假命令执行器跑整条链路")

    args = parser.parse_args(argv)
    command = args.command or "self-test"
    if command == "self-test":
        return run_self_test()

    def resolve(path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else (REPO_ROOT / candidate)

    if command == "verify":
        package = resolve(args.package)
        if not package.is_dir():
            print(f"[FAIL] 目录不存在：{package}")
            return 1
        checks = verify_before_write(package)
        if checks:
            return _print_checks(checks, "介质校验未通过：未写入任何内容")
        print(f"[OK] 介质校验通过：{package.name}")
        return 0

    state_dir = resolve(args.state_dir)
    package = resolve(args.package) if getattr(args, "package", None) else None
    if command == "preflight":
        assert package is not None
        checks, _ = preflight(
            package,
            state_dir=state_dir,
            executor=RealExecutor(),
            plugin_loader=args.plugin_loader,
            host_arch=platform.machine(),
        )
        if checks:
            return _print_checks(checks, "预检未通过：未进入写入阶段")
        print(f"[OK] 预检通过：{package.name}")
        return 0
    if command == "load":
        assert package is not None
        return load_package(
            package,
            state_dir=state_dir,
            database_url=args.database_url,
            plugin_loader=args.plugin_loader,
        )
    if command == "upgrade":
        assert package is not None
        return upgrade_package(
            package,
            state_dir=state_dir,
            database_url=args.database_url,
            plugin_loader=args.plugin_loader,
        )
    return rollback_package(
        state_dir=state_dir,
        database_url=args.database_url,
        plugin_loader=args.plugin_loader,
    )


if __name__ == "__main__":
    sys.exit(main())
