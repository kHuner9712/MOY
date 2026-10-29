#!/usr/bin/env python3
"""SBOM 生成器（CycloneDX 1.5，四层可解析）—— 阶段四 T4-05.6，架构 §9.5 / §11。

**为什么 SBOM 必须由脚本生成**
--------------------------
交付总监验收时只做一条断言：`jq '.components|length' sbom.cdx.json` > 0，
且四类 `bom-ref` 前缀**各至少 1 条**。手写一份 JSON 十分钟就能让这条断言变绿，
而它对客户没有任何用处——所以真正的判据不是"文件存在"，而是
**四层都能追溯到一台机器上的真实来源**：

四层各自的数据源：

- 容器镜像 `docker:` —— `deploy/images.lock`（含 `digest`）。
  为什么单独一层：故障的爆炸半径由镜像决定，只列"我们写的代码"等于没列。
- Python 包 `pypi:` —— `uv.lock` → 适配层 venv → `pyproject.toml`（含传递依赖）。
- Node 包 `npm:` —— 仓库根 `package-lock.json`（v3 workspaces）。
  它覆盖"前端产物里打进去的全部三方代码"。
- 离线插件包 `difypkg:` —— `deploy/offline/plugins/*.difypkg` 与 `apps/*.difypkg`。
  这类包在客户内网里跑，**不在**我们的锁文件里，只能解包才知道它带了什么。

数据源缺失时**不静默**：每一层都会在 `metadata.properties` 里留下
`sbom:source:<层>` = 实际用的来源（或 `unavailable`），谁都能看出这一层是
"没有依赖"还是"没找到源文件"。

用法::

    python deploy/sbom/generate.py                       # 写 <repo>/dist/sbom.cdx.json
    python deploy/sbom/generate.py --out dist/sbom.cdx.json
    python deploy/sbom/generate.py --quiet                # 只输出结论
    python deploy/sbom/generate.py --json                 # 把统计打到 stdout（CI 消费）

退出码：0 = 生成成功且四层判据满足；1 = 判据不满足（空清单或某层缺失）；
2 = 生成过程异常（源文件读不了/写不出去）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

#: 仓库根目录（`deploy/sbom/generate.py` 的上两级）
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: 默认产物路径：`dist/sbom.cdx.json`。
#:
#: 为什么落到 `dist/` 而不是仓库根：SBOM 是**包内产物**（随离线包交付），
#: 仓库根多一个 370 KB 的生成文件只会污染工作区——而 `dist/` 已在 `.gitignore`
#: 里，`--out` 也可以指到发布流水线自己的产物目录。
#: 验收命令里的裸文件名 `sbom.cdx.json` 指的正是这个文件（路径不限、文件名固定）。
DEFAULT_OUT: Final[Path] = REPO_ROOT / "dist" / "sbom.cdx.json"

#: 镜像锁文件（唯一事实来源，见 deploy/images.lock 头部规则）
IMAGES_LOCK: Final[Path] = REPO_ROOT / "deploy" / "images.lock"

#: Node 锁文件
PACKAGE_LOCK: Final[Path] = REPO_ROOT / "package-lock.json"

#: uv 锁文件（位于 apps/adapter/：全量传递依赖的真源，优先于 venv 探测；
#: 审计 2026-10-29 C1 修正——原先错指根级 uv.lock，该文件不存在，pypi 层静默降级）
UV_LOCK: Final[Path] = REPO_ROOT / "apps" / "adapter" / "uv.lock"

#: 适配层目录（用于 venv 探测与 pyproject 兜底）
ADAPTER_ROOT: Final[Path] = REPO_ROOT / "apps" / "adapter"

#: 插件包搜索目录（架构 §9.2：四个档位包 + 记忆抽取应用在 apps/，插件在 offline/plugins）
DIFYPKG_DIRS: Final[tuple[Path, ...]] = (
    REPO_ROOT / "deploy" / "offline" / "plugins",
    REPO_ROOT / "apps",
)

#: 四层判据的 `bom-ref` 前缀（验收要求各 ≥1）
REQUIRED_PREFIXES: Final[tuple[str, ...]] = ("docker:", "pypi:", "npm:", "difypkg:")

#: 产品标识（SBOM 的 `metadata.component`）
PRODUCT_NAME: Final[str] = "Moy（麦子）· 私有化轻量版"
PRODUCT_VERSION_FILE: Final[Path] = REPO_ROOT / "package.json"


@dataclass
class Layer:
    """一层组件的来源与产出统计。"""

    prefix: str
    source: str
    components: list[dict[str, Any]] = field(default_factory=list)


# ===========================================================================
# 第一层：容器镜像（deploy/images.lock）
# ===========================================================================


def parse_images_lock(path: Path) -> list[dict[str, str]]:
    """解析 `deploy/images.lock`（行式 YAML，字段固定：name/tag/digest/license/role）。

    为什么不引 PyYAML：这份文件由校验脚本与 CI 共同维护，结构是"`- name:` 起头 +
    若干缩进标量"，手写解析 30 行就够；而 SBOM 生成要能在只有标准库的环境里跑
    （发布机常常只有一个 python3）。

    Args:
        path: 锁文件路径。

    Returns:
        list[dict[str, str]]: 每个镜像一个字典（缺字段用空串，不猜）。
    """
    if not path.is_file():
        return []
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- name:"):
            if current:
                entries.append(current)
            current = {"name": stripped.split(":", 1)[1].strip().strip("'\""), "tag": ""}
            continue
        if current is None:
            continue
        if not raw.startswith((" ", "\t")):
            # 顶层键（version: 2 / images:）——不属于任何镜像条目
            continue
        m = re.match(r"^\s+([a-z_]+):\s*(.*)$", raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip().strip("'\"")
        if key in {"tag", "digest", "license", "role", "source"} and not current.get(key):
            current[key] = value
    if current:
        entries.append(current)
    return entries


def docker_components() -> Layer:
    """第一层：容器镜像。

    Returns:
        Layer: 组件列表；锁文件缺失时返回空列表并在 `source` 里说明。
    """
    layer = Layer(prefix="docker:", source="deploy/images.lock")
    if not IMAGES_LOCK.is_file():
        layer.source = "unavailable（缺 deploy/images.lock）"
        return layer
    for entry in parse_images_lock(IMAGES_LOCK):
        name = entry.get("name", "")
        tag = entry.get("tag", "")
        if not name:
            continue
        ref = f"docker:{name}:{tag}" if tag else f"docker:{name}"
        component: dict[str, Any] = {
            "type": "container",
            "bom-ref": ref,
            "name": name,
            "version": tag or "unknown",
            "properties": [],
        }
        digest = entry.get("digest", "")
        if digest:
            # digest 是"这个镜像到底是哪一份制品"的唯一答案（tag 会被上游重推）
            component["hashes"] = [{"alg": "SHA-256", "content": digest.split(":", 1)[-1]}]
            component["purl"] = f"pkg:docker/{name}@{digest}"
            component["properties"].append({"name": "docker:digest", "value": digest})
        license_text = entry.get("license", "")
        if license_text:
            component["licenses"] = [{"license": {"name": license_text}}]
        role = entry.get("role", "")
        if role:
            component["properties"].append({"name": "docker:role", "value": role})
        source = entry.get("source", "")
        if source:
            component["properties"].append({"name": "docker:registry", "value": source})
        if not component["properties"]:
            del component["properties"]
        layer.components.append(component)
    return layer


# ===========================================================================
# 第二层：Python 包
# ===========================================================================


def _venv_python() -> Path | None:
    """探测适配层 venv 的解释器（Windows 与 POSIX 两种布局）。

    Returns:
        Path | None: 存在的解释器路径；都不存在时返回 None。
    """
    for candidate in (
        ADAPTER_ROOT / ".venv" / "Scripts" / "python.exe",
        ADAPTER_ROOT / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    return None


def _pypi_from_uv_lock(path: Path) -> list[dict[str, Any]]:
    """从 `uv.lock` 读包（TOML 的 `[[package]]` 段，只取 name/version）。

    Args:
        path: `uv.lock` 路径。

    Returns:
        list[dict[str, Any]]: components。
    """
    components: list[dict[str, Any]] = []
    name = ""
    version = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[[package]]":
            name, version = "", ""
            continue
        m = re.match(r'^(name|version)\s*=\s*"([^"]+)"', stripped)
        if not m:
            continue
        if m.group(1) == "name":
            name = m.group(2)
        else:
            version = m.group(2)
            if name:
                components.append(_pypi_component(name, version, "uv.lock"))
    return components


def _pypi_from_venv(python: Path) -> list[dict[str, Any]]:
    """从 venv 的 `pip list --format=json` 读已安装包（含传递依赖）。

    Args:
        python: venv 解释器。

    Returns:
        list[dict[str, Any]]: components（失败时返回空列表，由调用方降级）。
    """
    try:
        out = subprocess.run(  # noqa: S603 - 命令行与参数均为脚本内常量，无外部输入
            [str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    try:
        rows = json.loads(out.stdout)
    except json.JSONDecodeError:
        return []
    return [
        _pypi_component(str(row.get("name", "")), str(row.get("version", "")), "apps/adapter/.venv")
        for row in rows
        if row.get("name")
    ]


def _pypi_from_pyproject(path: Path) -> list[dict[str, Any]]:
    """从 `pyproject.toml` 读**直接依赖**（最后一层兜底：没有传递依赖）。

    Args:
        path: `pyproject.toml` 路径。

    Returns:
        list[dict[str, Any]]: components（版本留 `declared-only`）。
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    components: list[dict[str, Any]] = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies"):
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("]"):
                in_deps = False
                continue
            m = re.match(r'^"([A-Za-z0-9][A-Za-z0-9._-]*)', stripped)
            if m:
                components.append(_pypi_component(m.group(1), "declared-only", "pyproject.toml"))
    return components


def _pypi_component(name: str, version: str, source: str) -> dict[str, Any]:
    """构造一个 `pypi:` 组件。

    Args:
        name: 包名。
        version: 版本（未知时写 `unknown`）。
        source: 数据来源（写进 properties，便于解释"为什么这条版本是这样"）。

    Returns:
        dict[str, Any]: CycloneDX 组件。
    """
    normalized = name.lower().replace("_", "-")
    return {
        "type": "library",
        "bom-ref": f"pypi:{normalized}@{version or 'unknown'}",
        "name": normalized,
        "version": version or "unknown",
        "purl": f"pkg:pypi/{normalized}@{version or 'unknown'}",
        "properties": [{"name": "sbom:source", "value": source}],
    }


def pypi_components() -> Layer:
    """第二层：Python 包（uv.lock → venv → pyproject 三级降级）。

    Returns:
        Layer: 组件列表与"实际用了哪个来源"。
    """
    if UV_LOCK.is_file():
        components = _pypi_from_uv_lock(UV_LOCK)
        if components:
            return Layer(prefix="pypi:", source="uv.lock", components=components)
    python = _venv_python()
    if python is not None:
        components = _pypi_from_venv(python)
        if components:
            return Layer(
                prefix="pypi:",
                source=f"apps/adapter/.venv（{python.parent.name}/pip list，含传递依赖）",
                components=components,
            )
    components = _pypi_from_pyproject(ADAPTER_ROOT / "pyproject.toml")
    if components:
        return Layer(
            prefix="pypi:",
            source="apps/adapter/pyproject.toml（仅直接依赖，传递依赖未覆盖）",
            components=components,
        )
    return Layer(prefix="pypi:", source="unavailable（无 uv.lock / venv / pyproject）")


# ===========================================================================
# 第三层：Node 包（仓库根 package-lock.json，v3 workspaces）
# ===========================================================================


def npm_components() -> Layer:
    """第三层：Node 包。

    Returns:
        Layer: 组件列表；锁文件缺失或结构不认时为空并在 `source` 说明。
    """
    if not PACKAGE_LOCK.is_file():
        return Layer(prefix="npm:", source="unavailable（缺根部 package-lock.json）")
    try:
        data = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Layer(prefix="npm:", source=f"unavailable（package-lock.json 解析失败：{exc}）")

    packages = data.get("packages")
    if not isinstance(packages, dict):
        return Layer(prefix="npm:", source="unavailable（锁文件不是 v2/v3 的 packages 结构）")

    seen: set[str] = set()
    components: list[dict[str, Any]] = []
    for path_key, meta in packages.items():
        if not isinstance(meta, dict):
            continue
        # 只收 node_modules 里的真实三方包：根条目（""）与 workspace（apps/web）是
        # 我们自己的产物，列进 SBOM 只会让"三方依赖"这个数字失去意义。
        if "node_modules/" not in path_key:
            continue
        name = str(meta.get("name") or path_key.rsplit("node_modules/", 1)[-1])
        version = str(meta.get("version") or "unknown")
        ref = f"npm:{name}@{version}"
        if ref in seen:
            continue
        seen.add(ref)
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": f"pkg:npm/{name.replace('/', '%2F')}@{version}",
        }
        license_text = meta.get("license")
        if isinstance(license_text, str) and license_text:
            component["licenses"] = [{"license": {"name": license_text}}]
        dev = bool(meta.get("dev"))
        component["properties"] = [
            {"name": "npm:dev", "value": "true" if dev else "false"},
            {"name": "npm:workspace", "value": path_key.split("node_modules/")[0] or "root"},
        ]
        components.append(component)
    return Layer(prefix="npm:", source="package-lock.json（v3 packages）", components=components)


# ===========================================================================
# 第四层：离线插件包（.difypkg）
# ===========================================================================


def difypkg_components() -> Layer:
    """第四层：离线插件包（复用 `deploy/validate_offline_pkgs.py`，不写第二份解析）。

    为什么必须复用：解包规则（manifest 标识、requirements 解析、pyproject 兜底）
    一旦有两份实现，"扫描说合规"与"SBOM 说有哪些包"就会各说一套。

    Returns:
        Layer: 组件列表；未找到任何包时为空并在 `source` 说明。
    """
    sys.path.insert(0, str(REPO_ROOT / "deploy"))
    try:
        from validate_offline_pkgs import collect_archives, sbom_components
    except ImportError as exc:  # pragma: no cover - 部署目录被裁剪时才会发生
        return Layer(prefix="difypkg:", source=f"unavailable（无法导入扫描器：{exc}）")

    dirs = [d for d in DIFYPKG_DIRS if d.is_dir()]
    archives = collect_archives(dirs, [])
    if not archives:
        return Layer(
            prefix="difypkg:",
            source="unavailable（未找到 .difypkg：档位包随发布产出，见架构 §9.2）",
        )
    components = sbom_components(archives)
    return Layer(
        prefix="difypkg:",
        source=f"{len(archives)} 个 .difypkg（deploy/validate_offline_pkgs.py 解包）",
        components=components,
    )


# ===========================================================================
# 组装与判据
# ===========================================================================


def product_version() -> str:
    """产品版本号（取根 `package.json` 的 version，缺省 1.0.0）。

    Returns:
        str: 版本号。
    """
    try:
        data = json.loads(PRODUCT_VERSION_FILE.read_text(encoding="utf-8"))
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    except (OSError, json.JSONDecodeError):
        pass
    return "1.0.0"


def build() -> dict[str, Any]:
    """组装完整 CycloneDX 文档。

    Returns:
        dict[str, Any]: CycloneDX 1.5 文档。
    """
    layers = [docker_components(), pypi_components(), npm_components(), difypkg_components()]
    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    for layer in layers:
        for component in layer.components:
            ref = str(component.get("bom-ref", ""))
            if not ref or ref in seen:
                continue
            seen.add(ref)
            components.append(component)

    version = product_version()
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{_serial()}",
        "version": 1,
        "metadata": {
            "timestamp": _now(),
            "component": {
                "type": "application",
                "bom-ref": f"product:moy@{version}",
                "name": PRODUCT_NAME,
                "version": version,
            },
            "tools": [{"name": "deploy/sbom/generate.py", "version": version}],
            "properties": [
                {"name": f"sbom:source:{layer.prefix.rstrip(':')}", "value": layer.source}
                for layer in layers
            ],
        },
        "components": components,
    }


def _now() -> str:
    """当前 UTC 时间（ISO 8601，`Z` 结尾——与全仓时间口径一致）。

    Returns:
        str: 时间戳。
    """
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serial() -> str:
    """生成 `serialNumber` 用的 UUID（确定性不重要，唯一即可）。

    Returns:
        str: UUID4 字符串。
    """
    import uuid

    return str(uuid.uuid4())


def counts(doc: dict[str, Any]) -> dict[str, int]:
    """按四类前缀统计组件数。

    Args:
        doc: CycloneDX 文档。

    Returns:
        dict[str, int]: 前缀 → 组件数（含 `total`）。
    """
    result = dict.fromkeys(REQUIRED_PREFIXES, 0)
    for component in doc.get("components", []):
        ref = str(component.get("bom-ref", ""))
        for prefix in REQUIRED_PREFIXES:
            if ref.startswith(prefix):
                result[prefix] += 1
                break
    result["total"] = len(doc.get("components", []))
    return result


def verify(doc: dict[str, Any]) -> list[str]:
    """验收判据：组件数 > 0 且四类前缀各 ≥1。

    Args:
        doc: CycloneDX 文档。

    Returns:
        list[str]: 不满足项（空表示通过）。
    """
    stats = counts(doc)
    problems: list[str] = []
    if stats["total"] == 0:
        problems.append("components 为空")
    for prefix in REQUIRED_PREFIXES:
        if stats[prefix] < 1:
            problems.append(f"缺少 {prefix} 类组件（该层数据源不可用，见 metadata.properties）")
    return problems


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 退出码（0 通过 / 1 判据不满足 / 2 生成异常）。
    """
    parser = argparse.ArgumentParser(description="生成 CycloneDX SBOM（四层）")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="输出路径（默认 <repo>/dist/sbom.cdx.json）",
    )
    parser.add_argument("--quiet", action="store_true", help="只打印结论")
    parser.add_argument("--json", action="store_true", help="把统计 JSON 打到 stdout（CI 消费）")
    args = parser.parse_args(argv)

    try:
        doc = build()
    except Exception as exc:  # 生成异常必须显性失败，不能产出半份 SBOM
        print(f"[FAIL] 生成 SBOM 失败：{exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[FAIL] 写出 SBOM 失败：{exc}", file=sys.stderr)
        return 2

    stats = counts(doc)
    problems = verify(doc)

    if args.json:
        print(
            json.dumps({"out": str(out), "counts": stats, "problems": problems}, ensure_ascii=False)
        )
    elif not args.quiet:
        print("=" * 70)
        print("CycloneDX SBOM（四层可解析）")
        print("=" * 70)
        print(f"[INFO] 产物：{out}")
        for layer in doc["metadata"]["properties"]:
            print(f"[INFO] {layer['name']:<28} {layer['value']}")
        print()
        for prefix in REQUIRED_PREFIXES:
            print(f"[INFO] {prefix:<10} {stats[prefix]:>5} 个")
        print(f"[INFO] {'合计':<8} {stats['total']:>5} 个组件")
        for problem in problems:
            print(f"[FAIL] {problem}")
        print()

    if problems:
        print(f"[FAIL] SBOM 判据不满足（{len(problems)} 项）；产物已写出，但**不能**用于发布")
        return 1
    print(f"[OK] SBOM 生成通过：{stats['total']} 个组件，四层各 ≥1（{out}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
