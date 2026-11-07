"""离线介质包目标机链路（3B）单测：预检/校验/加载/幂等/崩溃重入/升级/回滚。

介质包全部用真实临时文件 + 假归档，命令执行层用 `FakeExecutor`；真实 skopeo
导入 Docker、真实 psql、真实插件导入器不在 CI 执行（见 docs/offline-package.md §10）。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
DB_URL = "postgresql://reader@db/app"


def _load_module(name: str, filename: str) -> ModuleType:
    """从 deploy/ 加载脚本（该目录不是可导入包）。"""
    path = REPO_ROOT / "deploy" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


media = _load_module("validate_offline_media", "validate_offline_media.py")
apply_mod = _load_module("validate_offline_apply", "validate_offline_apply.py")

STATE = apply_mod.STATE_FILE


def _package(
    tmp_path: Path,
    version: str = "1.0.0",
    *,
    tamper: bool = False,
    salt: str = "",
    migrations: tuple[str, ...] = ("0001_init.sql", "0002_more.sql"),
    plugin: str = "plg_content_ops-1.0.0.difypkg",
) -> Path:
    """造一个结构完整的最小介质包（真实文件、假归档/假签名）。

    `salt` 用于让同版本的两份包内容不同（验证"同版本换包"拒绝）。
    """
    package = tmp_path / f"moy-offline-v{version}-x86_64"
    for name in media.PAYLOAD_DIRS:
        (package / name).mkdir(parents=True, exist_ok=True)
    image = media.ImageRef("officeai/adapter", version, "sha256:" + "a" * 64, "内部", "适配层")
    files: dict[str, bytes] = {
        f"images/officeai-adapter-{version}-x86_64.oci.tar": b"fake-oci",
        "images/images.lock": b"version: 2\n",
        "wheels/requirements.txt": f"pydantic==2.13.5\n# {version}{salt}\n".encode(),
        "wheels/pydantic-2.13.5.whl": b"fake-wheel",
        "web/package-lock.json": b"{}",
        "web/npm-cache.tar.gz": b"fake-cache",
        "sql/init/init-schema.sql": b"CREATE SCHEMA a;\n",
        "sql/rollback/0002_more.down.sql": b"ALTER TABLE a DROP COLUMN b;\n",
        "sql/rollback/0001_init.down.sql": b"DROP TABLE a;\n",
        f"plugins/{plugin}": b"fake-difypkg",
        "sbom/sbom.cdx.json": b"{}",
        f"sbom/images/officeai-adapter-{version}-x86_64.cdx.json": b"{}",
        "metadata/version.json": json.dumps({"version": version}).encode(),
        "metadata/NOTICE.third-party.md": b"# NOTICE\n",
    }
    for name in migrations:
        stem = name.removesuffix(".sql")
        files[f"sql/migrations/{name}"] = f"SELECT '{name}';\n".encode()
        if stem in {"0001_init", "0002_more"}:
            continue
        files[f"sql/rollback/{stem}.down.sql"] = f"SELECT 'down {name}';\n".encode()
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
    """假签名器：签名文件非空即通过。"""
    return sums.is_file() and signature.is_file() and signature.stat().st_size > 0


def _load(package: Path, state_dir: Path, executor: object, **kwargs: object) -> int:
    """用固定参数调用 load（默认 ample 磁盘 + x86_64 + 假签名）。"""
    params = {
        "state_dir": state_dir,
        "executor": executor,
        "database_url": DB_URL,
        "host_arch": "x86_64",
        "free_bytes": 100_000_000_000,
        "signature_runner": _signature_ok,
    }
    params.update(kwargs)
    return apply_mod.load_package(package, **params)


def _upgrade(package: Path, state_dir: Path, executor: object) -> int:
    """用固定参数调用升级。"""
    return apply_mod.upgrade_package(
        package,
        state_dir=state_dir,
        executor=executor,
        database_url=DB_URL,
        host_arch="x86_64",
        free_bytes=100_000_000_000,
        signature_runner=_signature_ok,
    )


def _rollback(state_dir: Path, executor: object, **kwargs: object) -> int:
    """用固定参数调用回滚。"""
    params = {
        "state_dir": state_dir,
        "executor": executor,
        "database_url": DB_URL,
        "host_arch": "x86_64",
        "free_bytes": 100_000_000_000,
        "signature_runner": _signature_ok,
    }
    params.update(kwargs)
    return apply_mod.rollback_package(**params)


# ---------------------------------------------------------------------------
# 预检 / 校验（写入前的否决点）
# ---------------------------------------------------------------------------


def test_preflight_ok(tmp_path: Path) -> None:
    """架构/磁盘/工具齐全时预检无失败项。"""
    package = _package(tmp_path)
    checks, manifest = apply_mod.preflight(
        package,
        state_dir=tmp_path / "state",
        executor=apply_mod.FakeExecutor(),
        host_arch="x86_64",
        free_bytes=100_000_000_000,
    )
    assert checks == []
    assert manifest is not None


def test_preflight_rejects_low_disk(tmp_path: Path) -> None:
    """磁盘余量按 totals.bytes + 20%/512MiB 计算，不足即拒绝。"""
    package = _package(tmp_path)
    checks, _ = apply_mod.preflight(
        package,
        state_dir=tmp_path / "state",
        executor=apply_mod.FakeExecutor(),
        host_arch="x86_64",
        free_bytes=100,
    )
    assert [check.path for check in checks] == ["disk"]


@pytest.mark.parametrize("tool", ["skopeo", "cosign", "psql", "dify-plugin-import"])
def test_preflight_rejects_missing_tool(tmp_path: Path, tool: str) -> None:
    """缺任一必要工具都报缺什么，不进入写入阶段。"""
    package = _package(tmp_path)
    checks, _ = apply_mod.preflight(
        package,
        state_dir=tmp_path / "state",
        executor=apply_mod.FakeExecutor(missing=(tool,)),
        host_arch="x86_64",
        free_bytes=100_000_000_000,
    )
    assert [check.path for check in checks] == [f"tool:{tool}"]


def test_preflight_rejects_wrong_arch(tmp_path: Path) -> None:
    """介质架构与宿主不符即拒绝。"""
    package = _package(tmp_path)
    checks, _ = apply_mod.preflight(
        package,
        state_dir=tmp_path / "state",
        executor=apply_mod.FakeExecutor(),
        host_arch="aarch64",
        free_bytes=100_000_000_000,
    )
    assert any(check.path == "arch" for check in checks)


def test_verify_rejects_tampered_payload(tmp_path: Path) -> None:
    """1 字节篡改即拒绝（fail-closed）。"""
    package = _package(tmp_path, tamper=True)
    checks = apply_mod.verify_before_write(package, signature_runner=_signature_ok)
    assert any("oci.tar" in check.path for check in checks)


def test_verify_rejects_manifest_sums_mismatch(tmp_path: Path) -> None:
    """MANIFEST 记录的 sha256 与 SHA256SUMS 不一致即拒绝。"""
    package = _package(tmp_path)
    manifest = apply_mod.read_manifest(package)
    rows = manifest["entries"]
    assert isinstance(rows, list)
    rows[0]["sha256"] = "0" * 64
    media.write_json(package / media.MANIFEST_NAME, manifest)
    media.write_sha256sums(package, [media.SUMS_NAME, media.SIG_NAME])
    checks = apply_mod.verify_before_write(package, signature_runner=_signature_ok)
    assert any("MANIFEST" in check.reason for check in checks)


def test_load_refuses_when_verify_fails(tmp_path: Path) -> None:
    """校验不通过时 load 不写任何状态（fail-closed）。"""
    package = _package(tmp_path, tamper=True)
    state_dir = tmp_path / "state"
    assert _load(package, state_dir, apply_mod.FakeExecutor()) == 1
    assert not (state_dir / STATE).exists()


# ---------------------------------------------------------------------------
# 加载 / 幂等 / 崩溃重入
# ---------------------------------------------------------------------------


def test_load_success_records_state(tmp_path: Path) -> None:
    """正常加载后状态为 loaded，且不记录数据库连接串。"""
    package = _package(tmp_path)
    state_dir = tmp_path / "state"
    executor = apply_mod.FakeExecutor()
    assert _load(package, state_dir, executor) == 0
    state = apply_mod.read_state(state_dir / STATE)
    assert state["status"] == "loaded"
    assert state["current_version"] == "1.0.0"
    assert state["fingerprint"]
    assert DB_URL not in json.dumps(state, ensure_ascii=False)
    assert [call[:2] for call in executor.calls] == [
        ["skopeo", "copy"],
        [sys.executable, "-m"],
        ["psql", DB_URL],
        ["psql", DB_URL],
        ["dify-plugin-import", "install"],
    ]


def test_load_is_idempotent(tmp_path: Path) -> None:
    """重复执行同一介质包不产生任何新命令。"""
    package = _package(tmp_path)
    state_dir = tmp_path / "state"
    executor = apply_mod.FakeExecutor()
    assert _load(package, state_dir, executor) == 0
    before = len(executor.calls)
    assert _load(package, state_dir, executor) == 0
    assert len(executor.calls) == before


def test_load_resumes_after_crash(tmp_path: Path) -> None:
    """加载到一半失败后重入：只跑剩余步骤，状态从 partial 回到 loaded。"""
    package = _package(tmp_path)
    state_dir = tmp_path / "state"
    crashing = apply_mod.FakeExecutor(fail_when=lambda args: "0001_init.sql" in " ".join(args))
    assert _load(package, state_dir, crashing) == 1
    partial = apply_mod.read_state(state_dir / STATE)
    assert partial["status"] == "partial"
    assert partial["last_error"]["step"] == "sql"

    resumed = apply_mod.FakeExecutor()
    assert _load(package, state_dir, resumed) == 0
    assert not any("skopeo" in " ".join(call) for call in resumed.calls)
    assert not any("-m" in call[:2] for call in resumed.calls)
    sql_calls = [call for call in resumed.calls if call[0] == "psql"]
    assert len(sql_calls) == 2
    assert apply_mod.read_state(state_dir / STATE)["status"] == "loaded"


def test_load_refuses_different_package_same_version(tmp_path: Path) -> None:
    """同版本换包（指纹不同）拒绝，避免混批次。"""
    first = _package(tmp_path, "1.0.0")
    other = _package(tmp_path / "other", "1.0.0", salt="different")
    state_dir = tmp_path / "state"
    assert _load(first, state_dir, apply_mod.FakeExecutor()) == 0
    assert _load(other, state_dir, apply_mod.FakeExecutor()) == 1


def test_load_refuses_version_change_without_upgrade(tmp_path: Path) -> None:
    """已加载 1.0.0 时直接用 load 装 1.1.0 必须被拒绝。"""
    first = _package(tmp_path, "1.0.0")
    second = _package(tmp_path, "1.1.0")
    state_dir = tmp_path / "state"
    assert _load(first, state_dir, apply_mod.FakeExecutor()) == 0
    assert _load(second, state_dir, apply_mod.FakeExecutor()) == 1


# ---------------------------------------------------------------------------
# 升级（版本兼容）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "allowed"),
    [
        ("1.0.0", "1.0.1", True),
        ("1.0.0", "1.1.0", True),
        ("1.0.0", "1.2.0", False),
        ("1.0.0", "2.0.0", False),
        ("1.1.0", "1.0.0", False),
        ("1.1.0", "1.1.0", False),
        ("1.1.0", "1.1.1", True),
        ("bad", "1.0.0", False),
    ],
)
def test_upgrade_allowed_matrix(old: str, new: str, allowed: bool) -> None:
    """同大版本、最多跨 1 个次版本；禁止降级与同版本。"""
    assert apply_mod.upgrade_allowed(old, new)[0] is allowed


def test_upgrade_runs_only_new_migrations(tmp_path: Path) -> None:
    """升级到 1.1.0 时只执行新增迁移，旧迁移不重跑。"""
    old = _package(tmp_path / "v1", "1.0.0")
    new = _package(
        tmp_path / "v2",
        "1.1.0",
        migrations=("0001_init.sql", "0002_more.sql", "0003_extra.sql"),
        plugin="plg_content_ops-1.1.0.difypkg",
    )
    state_dir = tmp_path / "state"
    assert _load(old, state_dir, apply_mod.FakeExecutor()) == 0
    executor = apply_mod.FakeExecutor()
    assert _upgrade(new, state_dir, executor) == 0
    sql_paths = [call[-1] for call in executor.calls if call[0] == "psql"]
    assert sql_paths == [str(new / "sql/migrations/0003_extra.sql")]
    state = apply_mod.read_state(state_dir / STATE)
    assert state["current_version"] == "1.1.0"
    assert state["previous"]["version"] == "1.0.0"
    assert state["previous"]["package_dir"] == str(old.resolve())


@pytest.mark.parametrize("target", ["1.2.0", "2.0.0"])
def test_upgrade_rejects_illegal_jump(tmp_path: Path, target: str) -> None:
    """跨多个次版本或跨大版本拒绝，且不改状态。"""
    old = _package(tmp_path / "v1", "1.0.0")
    new = _package(tmp_path / f"v{target}", target)
    state_dir = tmp_path / "state"
    assert _load(old, state_dir, apply_mod.FakeExecutor()) == 0
    assert _upgrade(new, state_dir, apply_mod.FakeExecutor()) == 1
    state = apply_mod.read_state(state_dir / STATE)
    assert state["current_version"] == "1.0.0"
    assert state.get("previous") is None


def test_upgrade_failure_keeps_previous_for_rollback(tmp_path: Path) -> None:
    """升级中途失败也保留 previous 快照，可立即回滚。"""
    old = _package(tmp_path / "v1", "1.0.0")
    new = _package(tmp_path / "v2", "1.1.0", migrations=("0001_init.sql", "0003_extra.sql"))
    state_dir = tmp_path / "state"
    assert _load(old, state_dir, apply_mod.FakeExecutor()) == 0
    crashing = apply_mod.FakeExecutor(fail_when=lambda args: "0003_extra.sql" in " ".join(args))
    assert _upgrade(new, state_dir, crashing) == 1
    state = apply_mod.read_state(state_dir / STATE)
    assert state["status"] == "partial"
    assert state["previous"]["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------


def test_rollback_success(tmp_path: Path) -> None:
    """回滚：down SQL 逆序 → 旧镜像 → 插件，最终版本回退。"""
    old = _package(tmp_path / "v1", "1.0.0")
    new = _package(tmp_path / "v2", "1.1.0")
    state_dir = tmp_path / "state"
    assert _load(old, state_dir, apply_mod.FakeExecutor()) == 0
    assert _upgrade(new, state_dir, apply_mod.FakeExecutor()) == 0
    executor = apply_mod.FakeExecutor()
    assert _rollback(state_dir, executor) == 0
    sql_paths = [call[-1] for call in executor.calls if call[0] == "psql"]
    assert sql_paths == [
        str(new / "sql/rollback/0002_more.down.sql"),
        str(new / "sql/rollback/0001_init.down.sql"),
    ]
    state = apply_mod.read_state(state_dir / STATE)
    assert state["status"] == "rolled_back"
    assert state["current_version"] == "1.0.0"
    assert state["previous"] is None


def test_rollback_mid_failure_exits_three(tmp_path: Path) -> None:
    """回滚中途失败：退出码 3、状态 rollback_failed、待办可续跑。"""
    old = _package(tmp_path / "v1", "1.0.0")
    new = _package(tmp_path / "v2", "1.1.0")
    state_dir = tmp_path / "state"
    assert _load(old, state_dir, apply_mod.FakeExecutor()) == 0
    assert _upgrade(new, state_dir, apply_mod.FakeExecutor()) == 0
    failing = apply_mod.FakeExecutor(fail_when=lambda args: "0001_init.down.sql" in " ".join(args))
    assert _rollback(state_dir, failing) == 3
    state = apply_mod.read_state(state_dir / STATE)
    assert state["status"] == "rollback_failed"
    assert state["rollback_pending"] == ["sql", "images", "plugins"]
    assert state["current_version"] == "1.1.0"

    resumed = apply_mod.FakeExecutor()
    assert _rollback(state_dir, resumed) == 0
    state = apply_mod.read_state(state_dir / STATE)
    assert state["status"] == "rolled_back"
    assert state["current_version"] == "1.0.0"


def test_rollback_without_previous_fails(tmp_path: Path) -> None:
    """没有上一版本快照时拒绝回滚（退出码 1）。"""
    package = _package(tmp_path)
    state_dir = tmp_path / "state"
    assert _load(package, state_dir, apply_mod.FakeExecutor()) == 0
    assert _rollback(state_dir, apply_mod.FakeExecutor()) == 1


def test_rollback_missing_current_package_is_manual(tmp_path: Path) -> None:
    """当前版本介质包丢失时停在 rollback_failed 并给出人工介入（退出码 3）。"""
    old = _package(tmp_path / "v1", "1.0.0")
    new = _package(tmp_path / "v2", "1.1.0")
    state_dir = tmp_path / "state"
    assert _load(old, state_dir, apply_mod.FakeExecutor()) == 0
    assert _upgrade(new, state_dir, apply_mod.FakeExecutor()) == 0
    shutil.rmtree(new)
    assert _rollback(state_dir, apply_mod.FakeExecutor()) == 3
    state = apply_mod.read_state(state_dir / STATE)
    assert state["status"] == "rollback_failed"
    assert state["current_version"] == "1.1.0"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_self_test_exit_zero() -> None:
    """CI 真跑入口：`python deploy/validate_offline_apply.py self-test`。"""
    result = subprocess.run(  # noqa: S603 - 固定参数调用仓库内脚本
        [sys.executable, str(REPO_ROOT / "deploy" / "validate_offline_apply.py"), "self-test"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "自检通过" in result.stdout
