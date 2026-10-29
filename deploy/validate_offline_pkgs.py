#!/usr/bin/env python3
"""离线包（`.difypkg`）合规扫描门禁（阶段四 T4-05.6，架构 §9.5 / §11）。

**为什么要有这条门禁**
--------------------
离线包里塞的是**第三方分发的插件**（`.difypkg` 是 zip）。客户拿到的是一份
"能直接装进内网"的东西，所以它的合规性不能靠"当初下载时看过一眼"：

1. **许可证传染**：插件把依赖打进了包（`requirements.txt` 冻结了 38 个包），
   其中一个 GPL/AGPL 包就足以让"随产品分发"这件事变成违约。这类问题在
   代码评审里看不见——它不在我们的源码里，而在别人的 zip 里。
2. **已知漏洞**：客户环境断网，装不了安全更新。发布时点上的已知高危漏洞
   必须在我们这边拦掉，否则客户连"知道"的机会都没有。
3. **结构完整性**：`.difypkg` 缺 `manifest.yaml` 或依赖清单解不出来时，
   "扫描通过"其实等于"什么都没扫到"——这是最危险的假绿。

三条判据都做**离线**判定（不联网查数据库）：发布的机器可能本身就是离网的。

用法::

    python deploy/validate_offline_pkgs.py                  # 扫默认目录
    python deploy/validate_offline_pkgs.py --dir deploy/offline/plugins
    python deploy/validate_offline_pkgs.py --file x.difypkg
    python deploy/validate_offline_pkgs.py --sbom           # 输出 components（给 SBOM 生成器）
    python deploy/validate_offline_pkgs.py --self-test      # 正/负样本自检
    python deploy/validate_offline_pkgs.py --json           # 机器可读结果

退出码：0 = 通过；1 = 发现违规（或自检未通过）；2 = 门禁自身异常。

**自检约定**（本仓库两族之一，本脚本属"直接调用型"）：`--self-test` 直接调用
扫描函数、不起子进程，因此**成功时退出 0**；正/负样本各自的退出码由脚本打印出来
供断言（正样本 0 / 负样本非 0）。另一族是"子进程型"（如打包门禁），成功时退出 1
——两族不混用，避免 CI 里出现"看到 1 到底算过还是没过"的歧义。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: 仓库根目录
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 默认扫描目录（离线包里插件的位置；架构 §9.2 还要求 apps/*.difypkg，存在则一起扫）
DEFAULT_DIRS: Final[tuple[Path, ...]] = (
    REPO_ROOT / "deploy" / "offline" / "plugins",
    REPO_ROOT / "apps",
)

#: 会"传染"到分发方的强著佐权协议标记。
#: 只列强著佐权：LGPL / MPL / EPL 属弱著佐权，动态链接或独立进程使用不触发
#: 传染义务，随包分发是被允许的，因此**不拦**（拦了会把人逼去关掉门禁）。
COPYLEFT_MARKERS: Final[tuple[str, ...]] = ("GPL", "AGPL", "SSPL")

#: 弱著佐权（允许，但留痕）：出现时只提示，供法务在发布清单里说明
WEAK_COPYLEFT_MARKERS: Final[tuple[str, ...]] = ("LGPL", "MPL", "EPL", "CDDL")

#: 许可证快照（离线判定的数据源之一）。
#:
#: 为什么内置一张表而不是联网查：① 发布机可能离网；② 上游 PyPI 的 license 字段
#: 大量是"UNKNOWN"或自由文本，联网也拿不到确定答案。表里只登记**已核实**的包，
#: 未登记的包走"未知 → 提示"，不会因此判失败（否则新装一个 MIT 包就会红）。
#: 维护方式：新增离线依赖时，核实其许可证后登记一行；核实不了的留空串并加 TODO。
LICENSE_SNAPSHOT: Final[dict[str, str]] = {
    # --- 插件运行时的常见依赖（核实来源：上游仓库 LICENSE 文件） ---
    "dify-plugin": "Apache-2.0",
    "flask": "BSD-3-Clause",
    "werkzeug": "BSD-3-Clause",
    "jinja2": "BSD-3-Clause",
    "markupsafe": "BSD-3-Clause",
    "itsdangerous": "BSD-3-Clause",
    "click": "BSD-3-Clause",
    "blinker": "MIT",
    "requests": "Apache-2.0",
    "urllib3": "MIT",
    "certifi": "MPL-2.0",
    "charset-normalizer": "MIT",
    "idna": "BSD-3-Clause",
    "httpx": "BSD-3-Clause",
    "httpcore": "BSD-3-Clause",
    "h11": "MIT",
    "anyio": "MIT",
    "socksio": "MIT",
    "multidict": "Apache-2.0",
    "yarl": "Apache-2.0",
    "propcache": "Apache-2.0",
    "pydantic": "MIT",
    "pydantic-core": "MIT",
    "pydantic-settings": "MIT",
    "annotated-types": "MIT",
    "typing-extensions": "PSF-2.0",
    "typing-inspection": "MIT",
    "python-dotenv": "BSD-3-Clause",
    "pyyaml": "MIT",
    "regex": "Apache-2.0",
    "tiktoken": "MIT",
    "gevent": "MIT",
    "greenlet": "MIT",
    "zope-event": "ZPL-2.1",
    "zope-interface": "ZPL-2.1",
    "packaging": "Apache-2.0 / BSD-2-Clause",
    "cffi": "MIT",
    "pycparser": "BSD-3-Clause",
    # --- 已知强著佐权（用于自检负样本，也可用于真实命中判定） ---
    "gnureadline": "GPL-2.0",
    "mysql-connector-python": "GPL-2.0-or-later（含 FOSS 例外）",
    "pyreadline": "GPL-2.0",
    "readline": "GPL-3.0-or-later",
    "ghostscript": "AGPL-3.0",
}

#: 已知漏洞快照（离线判定）：包名 → [(受影响版本前缀, 编号, 一句话影响)]。
#:
#: 口径（必须写清，否则这张表会被当成"永远最新"）：
#:   - 数据截至 **2026-10-29**，来源为上游安全公告与 GitHub Advisory；
#:   - 只登记"随包分发且客户无法自行升级"的高危项；
#:   - 命中判据是**版本前缀匹配**（够用且不会因补丁版本漏判）；
#:   - 发布前必须复核本表（`docs/deployment-guide.md` 的「发布前检查」列了这一步）。
CVE_SNAPSHOT: Final[dict[str, list[tuple[str, str, str]]]] = {
    "jinja2": [
        ("3.1.4", "CVE-2024-34064", "沙箱属性访问可绕过（模板注入面）"),
    ],
    "werkzeug": [
        ("3.0.2", "CVE-2024-34069", "调试器 PIN 可被绕过（仅 debug 模式）"),
    ],
    "requests": [
        ("2.31.0", "CVE-2024-35195", "首次请求后仍复用连接证书校验结果"),
    ],
    "urllib3": [
        ("2.2.1", "CVE-2024-37891", "跨主机重定向时泄漏 Proxy-Authorization"),
    ],
    "zipp": [
        ("3.19.0", "CVE-2024-5569", "无限循环导致拒绝服务"),
    ],
}

#: `requirements.txt` 一行：name[extras] == version ; marker
REQ_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[^\]]*\])?"
    r"\s*(?P<op>===|==|>=|<=|~=|!=|>|<)?\s*"
    r"(?P<version>[A-Za-z0-9._!+-]*)"
)

#: `manifest.yaml` 里取插件标识（只取顶层键，够用且不引 yaml 依赖）
MANIFEST_FIELD_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<key>[a-z_]+):\s*(?P<value>.+)$", re.MULTILINE
)


@dataclass
class Dep:
    """一个依赖声明。"""

    name: str
    version: str
    license: str = ""
    note: str = ""


@dataclass
class Package:
    """一个 `.difypkg` 的扫描结果。"""

    path: Path
    name: str = ""
    version: str = ""
    deps: list[Dep] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ===========================================================================
# 解析
# ===========================================================================


def parse_requirements(text: str) -> list[Dep]:
    """解析 `requirements.txt`（冻结格式，带 marker 的行也认）。

    Args:
        text: 文件内容。

    Returns:
        list[Dep]: 解析出的依赖（保持原顺序，便于报告与原始文件对照）。
    """
    deps: list[Dep] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http://", "https://")):
            # `-e` / 直接 URL 依赖无法用名字+版本判定，交给"结构问题"分支处理
            if line.startswith(("-e", "git+", "http://", "https://")):
                deps.append(Dep(name="<direct-reference>", version="", note=line[:80]))
            continue
        m = REQ_RE.match(line)
        if not m:
            continue
        deps.append(
            Dep(
                name=m.group("name").strip().lower(),
                version=(m.group("version") or "").strip(),
            )
        )
    return deps


def parse_manifest_identity(text: str) -> tuple[str, str]:
    """从 `manifest.yaml` 取插件名与版本（不引 yaml 依赖：只要两个顶层标量）。

    Args:
        text: `manifest.yaml` 内容。

    Returns:
        tuple[str, str]: (名称, 版本)；取不到的留空串。
    """
    name = ""
    version = ""
    for m in MANIFEST_FIELD_RE.finditer(text):
        key = m.group("key")
        value = m.group("value").strip().strip("'\"")
        if key in {"label", "description", "author", "icon"}:
            continue
        if key == "name" and not name:
            name = value
        elif key == "version" and not version:
            version = value
    return name, version


def scan_archive(path: Path) -> Package:
    """扫描单个 `.difypkg`。

    Args:
        path: 离线包路径。

    Returns:
        Package: 扫描结果（结构问题写在 `problems`，不抛异常——一个坏包不该让
        其余包也扫不成）。
    """
    pkg = Package(path=path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "manifest.yaml" not in names:
                pkg.problems.append("缺 manifest.yaml（不是一个合法的插件包）")
            else:
                pkg.name, pkg.version = parse_manifest_identity(
                    zf.read("manifest.yaml").decode("utf-8", errors="replace")
                )
            if "requirements.txt" in names:
                pkg.deps = parse_requirements(
                    zf.read("requirements.txt").decode("utf-8", errors="replace")
                )
            elif "pyproject.toml" in names:
                # 没有冻结清单时退到 pyproject：只能拿到"声明"，拿不到传递依赖。
                # 这不是失败，但必须留痕——"只扫了直接依赖"和"扫了全部"结论不同。
                text = zf.read("pyproject.toml").decode("utf-8", errors="replace")
                pkg.warnings.append(
                    "包内只有 pyproject.toml（无 requirements.txt）：只能覆盖直接依赖，"
                    "传递依赖未纳入扫描"
                )
                for m in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*)\s*([><=~!]=?[^"]*)?', text):
                    pkg.deps.append(Dep(name=m.group(1).lower(), version="", note="pyproject"))
            else:
                pkg.problems.append("包内既无 requirements.txt 也无 pyproject.toml（无法判定依赖）")
    except zipfile.BadZipFile:
        pkg.problems.append("不是有效的 zip 容器（文件损坏或下载被截断）")
    except OSError as exc:  # pragma: no cover - 权限/磁盘类问题
        pkg.problems.append(f"读取失败：{exc}")
    return pkg


def evaluate(pkg: Package) -> Package:
    """按许可证快照与漏洞快照判定一个包，把结论补进 `problems` / `warnings`。

    Args:
        pkg: 已解析的包。

    Returns:
        Package: 同一个对象（就地补结论），便于链式调用。
    """
    for dep in pkg.deps:
        dep.license = LICENSE_SNAPSHOT.get(dep.name, "")
        if dep.name == "<direct-reference>":
            pkg.warnings.append(f"存在直接引用依赖（无法按名字判定许可证）：{dep.note}")
            continue

        # ① 强著佐权：直接判失败（这是本门禁存在的主要理由）
        if dep.license and any(m in dep.license for m in COPYLEFT_MARKERS):
            pkg.problems.append(
                f"依赖 {dep.name} 的许可证为 {dep.license}（强著佐权）：随产品分发会触发传染义务"
            )
        elif dep.license and any(m in dep.license for m in WEAK_COPYLEFT_MARKERS):
            pkg.warnings.append(
                f"依赖 {dep.name} 的许可证为 {dep.license}（弱著佐权）：允许分发，"
                "但需在发布清单里如实列明"
            )

        # ② 已知漏洞：版本前缀命中即失败（客户环境断网，无法自行升级）
        for prefix, cve, impact in CVE_SNAPSHOT.get(dep.name, []):
            if dep.version.startswith(prefix):
                pkg.problems.append(f"依赖 {dep.name}=={dep.version} 命中 {cve}：{impact}")

        # ③ 未登记许可证：只提示（避免"新装一个 MIT 包就红"）
        if not dep.license:
            pkg.warnings.append(
                f"依赖 {dep.name} 未登记许可证（请在 LICENSE_SNAPSHOT 补一行后复核）"
            )
    return pkg


def collect_archives(dirs: list[Path], files: list[Path]) -> list[Path]:
    """收集待扫描的 `.difypkg`（显式文件优先，其余按目录递归找）。

    Args:
        dirs: 扫描目录。
        files: 显式指定的包。

    Returns:
        list[Path]: 去重后的包列表（排序保证输出稳定）。
    """
    found: dict[Path, None] = {}
    for f in files:
        found[f.resolve()] = None
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.difypkg")):
            found[p.resolve()] = None
    return sorted(found)


def scan(
    dirs: list[Path] | None = None, files: list[Path] | None = None
) -> tuple[list[Package], list[Path]]:
    """扫描并判定。

    Args:
        dirs: 扫描目录（缺省用 `DEFAULT_DIRS` 里存在的那些）。
        files: 显式指定的包。

    Returns:
        tuple[list[Package], list[Path]]: (扫描结果, 实际扫描的包路径)。
    """
    if dirs is None:
        dirs = [d for d in DEFAULT_DIRS if d.is_dir()]
    archives = collect_archives(dirs, files or [])
    return [evaluate(scan_archive(p)) for p in archives], archives


# ===========================================================================
# 输出
# ===========================================================================


def report(packages: list[Package], archives: list[Path]) -> tuple[int, int, int]:
    """打印人读报告。

    Args:
        packages: 扫描结果。
        archives: 实际扫描的包。

    Returns:
        tuple[int, int, int]: (失败项数, 提示项数, 依赖总数)。
    """
    n_fail = 0
    n_warn = 0
    n_deps = 0
    print("=" * 70)
    print("离线包（.difypkg）合规扫描")
    print("=" * 70)
    if not archives:
        print("[WARN] 没有找到任何 .difypkg：本次没有可判定的对象")
        print("       （V1.0 的四个档位包与记忆抽取应用随发布产出，见架构 §9.2；")
        print("        只有插件包存在时扫到的就是它，结论只覆盖已存在的包）")
    for pkg in packages:
        rel = pkg.path.relative_to(REPO_ROOT) if pkg.path.is_relative_to(REPO_ROOT) else pkg.path
        print(f"\n[PKG] {rel}  标识={pkg.name or '<未取到>'} 版本={pkg.version or '<未取到>'}")
        print(
            f"      依赖 {len(pkg.deps)} 项；失败 {len(pkg.problems)} 项；"
            f"提示 {len(pkg.warnings)} 项"
        )
        n_deps += len(pkg.deps)
        for problem in pkg.problems:
            print(f"  [FAIL] {problem}")
        n_fail += len(pkg.problems)
        for warning in pkg.warnings:
            print(f"  [WARN] {warning}")
        n_warn += len(pkg.warnings)
    print()
    print(
        f"[INFO] 扫描 {len(archives)} 个包 / 依赖 {n_deps} 项 / 失败 {n_fail} 项 / 提示 {n_warn} 项"
    )
    return n_fail, n_warn, n_deps


def sbom_components(files: list[Path]) -> list[dict[str, object]]:
    """产出 CycloneDX `components` 片段（供 `deploy/sbom/generate.py` 的第四层用）。

    Args:
        files: 要纳入的包（缺省取默认目录里找到的全部）。

    Returns:
        list[dict[str, object]]: components 列表，`bom-ref` 前缀统一为 `difypkg:`。
    """
    packages, _ = scan(files=files or None)
    components: list[dict[str, object]] = []
    for pkg in packages:
        base = pkg.path.stem  # `deepseek-0.0.25`
        components.append(
            {
                "type": "application",
                "bom-ref": f"difypkg:{base}",
                "name": pkg.name or base,
                "version": pkg.version or "unknown",
                "properties": [
                    {"name": "difypkg:archive", "value": pkg.path.name},
                    {"name": "difypkg:dependency_count", "value": str(len(pkg.deps))},
                    {"name": "difypkg:compliance_problems", "value": str(len(pkg.problems))},
                ],
            }
        )
        for dep in pkg.deps:
            if dep.name == "<direct-reference>":
                continue
            ref = f"difypkg:{dep.name}"
            if dep.version:
                ref = f"{ref}@{dep.version}"
            component: dict[str, object] = {
                "type": "library",
                "bom-ref": ref,
                "name": dep.name,
                "version": dep.version or "unknown",
                "properties": [{"name": "difypkg:bundled_by", "value": base}],
            }
            if dep.license:
                component["licenses"] = [{"license": {"name": dep.license}}]
            components.append(component)
    return components


# ===========================================================================
# 自检（正样本 0 退出、负样本非 0 退出）
# ===========================================================================


def _make_pkg(target: Path, requirements: str, *, with_manifest: bool = True) -> Path:
    """在临时目录里造一个最小 `.difypkg`（zip）。

    Args:
        target: 目标文件路径。
        requirements: `requirements.txt` 内容。
        with_manifest: 是否写 `manifest.yaml`（负样本要测"缺清单"）。

    Returns:
        Path: 生成的文件路径。
    """
    with zipfile.ZipFile(target, "w") as zf:
        if with_manifest:
            zf.writestr(
                "manifest.yaml",
                "author: selftest\nname: selftest-plugin\nversion: 0.0.1\n",
            )
        zf.writestr("requirements.txt", requirements)
        zf.writestr("main.py", "print('selftest')\n")
    return target


def run_self_test() -> int:
    """正/负样本自检：**直接调用扫描函数**（不起子进程）。

    为什么必须自检：没有负样本的门禁无法区分"有效"与"恒过"。这里证明三件事：
      ① 干净包 → 0 个失败项（防"一律报错"式假门禁）；
      ② 塞 1 个 GPL 包 → 失败项非空（门禁真的能抓到传染协议）；
      ③ 缺 manifest → 失败项非空（门禁不会对坏包静默放行）。

    Returns:
        int: 0 = 自检通过（正样本 0 / 负样本非 0 均符合预期）；1 = 自检未通过。
    """
    import tempfile

    print("[SELFTEST] 离线包合规门禁自检（直接调用型：成功时本函数返回 0）")
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        positive = _make_pkg(
            root / "positive.difypkg",
            "requests==2.34.2\nurllib3==2.7.0\npydantic==2.13.5\n",
        )
        # 负样本 1：一个 GPL 包（强著佐权）
        negative_gpl = _make_pkg(
            root / "negative-gpl.difypkg",
            "requests==2.34.2\ngnureadline==1.2.8\n",
        )
        # 负样本 2：缺 manifest.yaml（结构不合法 → 扫描不到东西也不能算通过）
        negative_broken = _make_pkg(
            root / "negative-broken.difypkg",
            "requests==2.34.2\n",
            with_manifest=False,
        )

        cases = [
            ("正样本（MIT/Apache 干净包）", positive, 0),
            ("负样本 1（塞 1 个 GPL 包）", negative_gpl, 1),
            ("负样本 2（缺 manifest.yaml）", negative_broken, 1),
        ]
        for label, path, expected in cases:
            pkg = evaluate(scan_archive(path))
            # 判定口径与真实运行一致：有失败项 → 1，无 → 0
            code = 1 if pkg.problems else 0
            if code != expected:
                failures.append(
                    f"[SELFTEST] {label} 预期退出码 {expected}，实际 {code}"
                    f"（失败项 {pkg.problems}）"
                )
            else:
                print(f"[SELFTEST] ✅ {label} → 退出码 {code}（符合预期）")

        # 负样本 3：已知漏洞（版本前缀命中）
        negative_cve = _make_pkg(root / "negative-cve.difypkg", "jinja2==3.1.4\n")
        pkg = evaluate(scan_archive(negative_cve))
        if not pkg.problems:
            failures.append("[SELFTEST] 负样本 3（jinja2==3.1.4 命中已知漏洞）应为非 0，实际 0")
        else:
            print("[SELFTEST] ✅ 负样本 3（已知漏洞）→ 退出码 1（符合预期）")

    for failure in failures:
        print(failure)
    if failures:
        print(f"\n[FAIL] 自检未通过（{len(failures)} 项）")
        return 1
    print("\n[OK] 自检通过：正样本 0 / 负样本非 0（3 条负样本全部命中）")
    return 0


# ===========================================================================
# 入口
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 退出码（0 通过 / 1 发现违规或自检未过 / 2 门禁自身异常）。
    """
    parser = argparse.ArgumentParser(description="离线包（.difypkg）合规扫描")
    parser.add_argument("--dir", action="append", default=[], help="扫描目录（可重复）")
    parser.add_argument("--file", action="append", default=[], help="指定 .difypkg（可重复）")
    parser.add_argument(
        "--sbom", action="store_true", help="只输出 components JSON（供 SBOM 生成）"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="正/负样本自检（直接调用型：成功退出 0，正样本 0 / 负样本非 0）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出扫描结果")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    dirs = [Path(d).resolve() for d in args.dir] or None
    files = [Path(f).resolve() for f in args.file]

    if args.sbom:
        try:
            print(json.dumps(sbom_components(files), ensure_ascii=False))
        except Exception as exc:  # 门禁自身异常必须显性失败（不能只打堆栈）
            print(f"[FAIL] 生成 SBOM 片段失败：{exc}", file=sys.stderr)
            return 2
        return 0

    try:
        packages, archives = scan(dirs, files)
    except Exception as exc:  # 同上：任何异常都要转成退出码 2
        print(f"[FAIL] 扫描异常：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "archives": [str(p) for p in archives],
                    "packages": [
                        {
                            "path": str(p.path),
                            "name": p.name,
                            "version": p.version,
                            "dependencies": len(p.deps),
                            "problems": p.problems,
                            "warnings": p.warnings,
                        }
                        for p in packages
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 1 if any(p.problems for p in packages) else 0

    n_fail, _n_warn, _n_deps = report(packages, archives)
    if n_fail:
        print(f"\n[FAIL] 离线包合规扫描不通过（{n_fail} 项）")
        return 1
    print("\n[OK] 离线包合规扫描通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
