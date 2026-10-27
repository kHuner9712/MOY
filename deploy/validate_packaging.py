#!/usr/bin/env python3
"""打包完整性门禁（审计 OA-AUDIT-001 / P0-1，新增 G-32）。

**为什么要有这条门禁**（付过的代价）：

适配层 `pyproject.toml` 曾手工枚举 `[tool.setuptools] packages`，漏登了
`app.business`（22 文件）、`app.db` + `app.db.repositories`（23 文件）、
`app.plugins`（7 文件）。实测 `pip wheel` 产物中这三个包 **0 个文件**。

问题在于：`apps/adapter/Dockerfile` 用 `pip install .`（**非 editable**），
所以容器一启动 `import app.business` 直接 ImportError，84 个端点绝大多数起不来；
而本地是 editable 安装（`.pth` finder 指向源码树），全量测试照常全绿——
**缺陷被安装方式完全掩盖**，只有真构建产物才暴露。

三条机械判据（本脚本把它们固化）：

1. **包齐全**：源码里含 `__init__.py` 的每个目录，都必须在打包清单里；
2. **资源随包**：包目录内的非 `.py` 文件（插件声明 / 档位路由表等）必须被
   `package-data` 声明，否则容器内 `Path(__file__).parent / "manifests"` 取不到；
3. **声明非空**：`package-data` 的每个 glob 必须至少命中一个真实文件
   （拼错路径 = 静默不分发，比不声明更难发现）。

用法::

    python deploy/validate_packaging.py                 # 静态校验（CI 默认，秒级）
    python deploy/validate_packaging.py --self-test     # 负样本自检（期望退出码 1）
    python deploy/validate_packaging.py --json          # 机器可读输出

退出码：0 = 通过；1 = 发现违规或自检命中；2 = 门禁自身异常（文件缺失 / 配置不可解析）。

**不做什么**：本门禁不做真实 `pip wheel`（CI 上耗时长且需构建后端）。
它校验的是"清单 ↔ 源码"的一致性——这正是本次事故的直接根因。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Final

#: 仓库根目录
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 适配层根目录
ADAPTER_ROOT: Final[Path] = REPO_ROOT / "apps" / "adapter"

#: 打包配置来源
PYPROJECT: Final[Path] = ADAPTER_ROOT / "pyproject.toml"

#: 顶层包目录
APP_DIR: Final[Path] = ADAPTER_ROOT / "app"

#: 视为「必须随包分发」的资源后缀（非 Python 源码）
RESOURCE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".json", ".yaml", ".yml", ".txt", ".sql", ".jinja", ".jinja2", ".html", ".css"}
)


# ===========================================================================
# 判据计算
# ===========================================================================


def source_packages(app_dir: Path) -> set[str]:
    """源码中真实的包集合（含 `__init__.py` 的目录，转成点分模块名）。

    Args:
        app_dir: 顶层包目录（`app/`）。

    Returns:
        set[str]: 形如 `{"app", "app.db", "app.db.repositories"}` 的集合。
    """
    found: set[str] = set()
    if not app_dir.is_dir():
        return found
    for init in app_dir.rglob("__init__.py"):
        rel = init.parent.relative_to(app_dir.parent)
        found.add(".".join(rel.parts))
    return found


def configured_packages(config: dict[str, Any], root: Path) -> set[str]:
    """从 `pyproject.toml` 解析打包清单。

    支持两种写法：
    - `[tool.setuptools.packages.find] include = [...]`：按 include 模式自动发现；
    - `[tool.setuptools] packages = [...]`：手工枚举（**不推荐**，会漂移）。

    Args:
        config: 已解析的 `pyproject.toml`。
        root: 发现起点（`apps/adapter`）。

    Returns:
        set[str]: 配置声明的包集合。
    """
    setuptools_cfg: dict[str, Any] = config.get("tool", {}).get("setuptools", {})

    # 写法一：自动发现（当前采用）
    find_cfg = setuptools_cfg.get("packages", {})
    if isinstance(find_cfg, dict) and "find" in find_cfg:
        includes: list[str] = find_cfg["find"].get("include") or ["*"]
        excludes: list[str] = find_cfg["find"].get("exclude") or []
        return _discover(root, includes, excludes)

    # 写法二：手工枚举
    if isinstance(find_cfg, list):
        return set(find_cfg)

    # 只写了 package-data 等、未声明 packages：交给调用方判为「无声明」
    return set()


def _discover(root: Path, includes: list[str], excludes: list[str]) -> set[str]:
    """按 setuptools `find:` 语义扫描包（目录含 `__init__.py`）。

    include/exclude 的模式既匹配完整点分名，也匹配其中任一层级前缀，
    与 setuptools `PackageFinder` 的行为保持一致。

    Args:
        root: 扫描起点。
        includes: include 模式列表。
        excludes: exclude 模式列表。

    Returns:
        set[str]: 命中的包集合。
    """
    result: set[str] = set()
    for candidate in sorted(root.rglob("__init__.py")):
        rel = candidate.parent.relative_to(root)
        dotted = ".".join(rel.parts)
        if not any(_match_any_level(dotted, pat) for pat in includes):
            continue
        if any(_match_any_level(dotted, pat) for pat in excludes):
            continue
        result.add(dotted)
    return result


def _match_any_level(dotted: str, pattern: str) -> bool:
    """判断 include/exclude 模式是否命中（逐层前缀 + 全名）。

    Args:
        dotted: 点分包名，如 `app.db.repositories`。
        pattern: 模式，如 `app*`。

    Returns:
        bool: 命中返回 True。
    """
    parts = dotted.split(".")
    levels = [".".join(parts[: i + 1]) for i in range(len(parts))]
    return any(fnmatch.fnmatch(level, pattern) for level in levels)


def package_data_globs(config: dict[str, Any]) -> dict[str, list[str]]:
    """读取 `[tool.setuptools.package-data]` 声明。

    Args:
        config: 已解析的 `pyproject.toml`。

    Returns:
        dict[str, list[str]]: 包名 → glob 列表。
    """
    raw = config.get("tool", {}).get("setuptools", {}).get("package-data", {})
    return {str(k): [str(g) for g in v] for k, v in raw.items()} if raw else {}


def undeclared_resources(app_dir: Path, config: dict[str, Any]) -> list[str]:
    """找出「在包目录内、但未被 package-data 声明」的资源文件。

    这条判据直接对应事故：`app/plugins/manifests/*.json` 未声明 →
    容器内 `default_manifests_dir()` 取到一个不存在的目录，6 份插件全丢。

    Args:
        app_dir: 顶层包目录。
        config: 已解析的 `pyproject.toml`。

    Returns:
        list[str]: 未声明资源的相对路径列表。
    """
    globs = package_data_globs(config)
    offenders: list[str] = []

    for path in sorted(app_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix == ".py":
            continue  # 源码由 packages 清单分发
        if path.suffix == ".pyc" or path.parent.name == "__pycache__":
            continue
        if path.suffix not in RESOURCE_SUFFIXES:
            continue

        # 所属包（最近的有 __init__.py 的祖先目录）
        pkg_dir = _owning_package_dir(app_dir, path)
        rel_in_pkg = path.relative_to(pkg_dir)
        pkg_dotted = ".".join(pkg_dir.relative_to(app_dir.parent).parts)

        declared = globs.get(pkg_dotted, [])
        if not any(fnmatch.fnmatch(str(rel_in_pkg).replace("\\", "/"), g) for g in declared):
            offenders.append(str(path.relative_to(app_dir.parent)).replace("\\", "/"))

    return offenders


def _owning_package_dir(app_dir: Path, path: Path) -> Path:
    """返回文件所属包的目录（最近含 `__init__.py` 的祖先）。

    Args:
        app_dir: 顶层包目录。
        path: 目标文件。

    Returns:
        Path: 所属包目录。
    """
    current = path.parent
    while current != app_dir.parent:
        if (current / "__init__.py").is_file():
            return current
        current = current.parent
    return app_dir


def empty_globs(app_dir: Path, config: dict[str, Any]) -> list[str]:
    """找出「声明了但一个文件都没命中」的 package-data glob。

    Args:
        app_dir: 顶层包目录。
        config: 已解析的 `pyproject.toml`。

    Returns:
        list[str]: 形如 `包名::glob` 的空声明列表。
    """
    offenders: list[str] = []
    for pkg, globs in package_data_globs(config).items():
        pkg_dir = app_dir.parent / Path(*pkg.split("."))
        if not pkg_dir.is_dir():
            offenders.append(f"{pkg}::{'(包目录不存在)'}")
            continue
        for g in globs:
            matched = [p for p in pkg_dir.rglob(g) if p.is_file()]
            if not matched:
                offenders.append(f"{pkg}::{g}")
    return offenders


# ===========================================================================
# 门禁主体
# ===========================================================================


def run_check(app_dir: Path, pyproject: Path) -> tuple[list[str], dict[str, Any]]:
    """执行三条判据。

    Args:
        app_dir: 顶层包目录。
        pyproject: `pyproject.toml` 路径。

    Returns:
        tuple[list[str], dict[str, Any]]: (违规项列表, 统计信息)。
    """
    violations: list[str] = []

    if not pyproject.is_file():
        return [f"未找到打包配置：{pyproject}"], {}
    if not app_dir.is_dir():
        return [f"未找到顶层包目录：{app_dir}"], {}

    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    root = pyproject.parent

    src = source_packages(app_dir)
    cfg = configured_packages(config, root)

    stats: dict[str, Any] = {
        "source_packages": len(src),
        "configured_packages": len(cfg),
        "package_data_packages": len(package_data_globs(config)),
    }

    # 判据 1：包齐全
    missing = sorted(src - cfg)
    if missing:
        stats["missing_packages"] = missing
        violations.append(
            "以下源码包未被打包清单收录（容器 `pip install .` 后 ImportError）：\n"
            + "\n".join(f"    - {m}" for m in missing)
        )

    # 判据 2：资源随包
    undeclared = undeclared_resources(app_dir, config)
    if undeclared:
        stats["undeclared_resources"] = undeclared
        violations.append(
            "以下包内资源未被 `package-data` 声明（容器内取不到该文件）：\n"
            + "\n".join(f"    - {u}" for u in undeclared)
        )

    # 判据 3：声明非空
    empty = empty_globs(app_dir, config)
    if empty:
        stats["empty_globs"] = empty
        violations.append(
            "以下 `package-data` 声明未命中任何文件（路径拼错 = 静默不分发）：\n"
            + "\n".join(f"    - {e}" for e in empty)
        )

    return violations, stats


# ===========================================================================
# 负样本自检（证明门禁「能红」）
# ===========================================================================


def _write(path: Path, content: str) -> None:
    """写入文件（自动建父目录）。

    Args:
        path: 目标路径。
        content: 文件内容。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def self_test() -> int:
    """负样本自检：注入 3 类违规，验证全部被拦截。

    Returns:
        int: 3 类全部命中返回 1（CI 断言该退出码），否则返回 0。
    """
    caught: list[str] = []
    missed: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app = root / "app"
        _write(app / "__init__.py", "")
        _write(app / "core" / "__init__.py", "")
        _write(app / "plugins" / "__init__.py", "")
        _write(app / "plugins" / "manifests" / "plg_demo.json", "{}")
        _write(app / "registry" / "routes.yaml", "x: 1")

        # 负样本 A：手工清单漏登 app.plugins（复刻本次事故的清单形态）
        pp = root / "pyproject.toml"
        _write(
            pp,
            "[tool.setuptools]\n"
            'packages = ["app", "app.core", "app.registry"]\n'
            "[tool.setuptools.package-data]\n"
            '"app.plugins" = ["manifests/*.json"]\n'
            '"app.registry" = ["*.yaml"]\n',
        )
        v, _ = run_check(app, pp)
        (
            caught.append("A 漏登包")
            if any("app.plugins" in x for x in v)
            else missed.append("A 漏登包")
        )

        # 负样本 B：资源未声明（去掉 app.plugins 的 package-data）
        _write(
            pp,
            "[tool.setuptools]\n"
            'packages = ["app", "app.core", "app.plugins", "app.registry"]\n'
            "[tool.setuptools.package-data]\n"
            '"app.registry" = ["*.yaml"]\n',
        )
        v, _ = run_check(app, pp)
        (
            caught.append("B 资源未声明")
            if any("manifests/plg_demo.json" in x for x in v)
            else missed.append("B 资源未声明")
        )

        # 负样本 C：glob 拼错（空声明）
        _write(
            pp,
            "[tool.setuptools]\n"
            'packages = ["app", "app.core", "app.plugins", "app.registry"]\n'
            "[tool.setuptools.package-data]\n"
            '"app.plugins" = ["manifests/*.json"]\n'
            '"app.registry" = ["*.yml"]\n',
        )
        v, _ = run_check(app, pp)
        (
            caught.append("C 空声明")
            if any("app.registry::*.yml" in x for x in v)
            else missed.append("C 空声明")
        )

    print("负样本自检结果：")
    for name in ("A 漏登包", "B 资源未声明", "C 空声明"):
        print(f"    {'✅ 已拦截' if name in caught else '❌ 漏放行'}  {name}")
    if missed:
        print(f"[FAIL] 以下负样本未命中：{missed}")
        return 0
    print("[PASS] 3 类负样本全部被拦截（门禁确实能红）")
    return 1


# ===========================================================================
# CLI
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数。

    Returns:
        int: 退出码（0 通过 / 1 违规或自检命中 / 2 门禁异常）。
    """
    parser = argparse.ArgumentParser(description="打包完整性门禁（G-32）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（期望退出码 1）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    print("=" * 70)
    print("适配层打包完整性校验（G-32 / OA-AUDIT-001 P0-1）")
    print("=" * 70)

    violations, stats = run_check(APP_DIR, PYPROJECT)
    print(
        f"[INFO] 源码包 {stats.get('source_packages', 0)} 个 / "
        f"清单收录 {stats.get('configured_packages', 0)} 个 / "
        f"声明资源的包 {stats.get('package_data_packages', 0)} 个"
    )
    print()

    if violations:
        print("[FAIL] 打包完整性门禁未通过：")
        for item in violations:
            print(f"  - {item}")
        if args.json:
            print(json.dumps({"ok": False, "stats": stats}, ensure_ascii=False))
        return 1

    print("[OK]   打包完整性门禁通过：包清单齐全、包内资源已声明、无空声明")
    if args.json:
        print(json.dumps({"ok": True, "stats": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
