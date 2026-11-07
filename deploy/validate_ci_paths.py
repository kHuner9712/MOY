#!/usr/bin/env python3
"""CI paths 漂移校验（H-05，2026-11-07 全仓审计）。

**要钉住的事故**：`ci-web.yml` 的 `paths` 只写 `apps/web/**`，而该 job 的第一步是
`npm ci` —— 它读的是**仓库根**的 `package-lock.json` 与 `package.json`（monorepo
npm workspaces）。于是「只改根锁文件」的 PR **不会触发前端门禁**：依赖树变了、
`eslint`/`tsc`/`vitest`/`next build` 一个都不跑，合并前的绿灯是上一次的绿灯。
同类漂移还有 `.github/e2e/keycloak-realm-ci.json`（H-04 后它决定 e2e 账号与回调
地址，改了却不跑 e2e 等于没改）。

**为什么不用"人肉记住"**：`paths` 是一份**手写的触发面**，和被守的输入文件是
两处真源；只要有人搬文件、加锁文件、添夹具，两边就会静默分叉。本脚本把"每个
门禁必须被它依赖的输入触发"变成可机械验证的判据，并且**双向查**：

- **C-1 输入覆盖**：`REQUIREMENTS` 里声明的每个"门禁依赖输入"（根锁文件、
  `uv.lock`、`pyproject.toml`、Dockerfile、迁移目录、`deploy/` 配置、e2e 夹具……）
  必须在对应 workflow 的 `paths` 里被至少一条模式命中。**逐事件查**：`push` 与
  `pull_request` 都写了 `paths` 就都要覆盖；某事件不写 `paths` 等于全量触发，
  自然不会漏。
- **C-2 死模式**：`paths` 里每条正向模式必须至少命中仓库里的一个真实文件。
  `apps/web/package-lock.json` 这类"抄错路径"的过滤条件会让门禁**永远不触发**，
  而 YAML 语法完全合法——只有拿仓库文件回代才看得出来。

**glob 语义按 GitHub 走**（不是 `fnmatch`）：`*` 不跨 `/`，`**` 才跨。这一点必须
用样本证明——`apps/*` 覆盖不了 `apps/web/package.json`，若用 `str.startswith`
或 `fnmatch` 实现，这条漂移会被判绿（self-test 负样本 3 就是它）。

用法::

    python deploy/validate_ci_paths.py              # 体检真实仓库
    python deploy/validate_ci_paths.py --self-test  # 负样本自检（期望退出码 1）

退出码：0 = 通过；1 = 发现漂移；2 = 无法判定（workflow/仓库文件清单缺失，
fail-closed——绝不把"读不到输入"打印成"0 问题"）。

**本仓库约定：`--self-test` 成功时退出码为 1**（与 `validate_gate_wiring.py` /
`validate_deploy.py` 一致，CI 用 `-ne 1` 断言"负样本确实被拦"）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

#: 仓库根目录（本脚本位于 <repo>/deploy/）
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: workflow 目录
WORKFLOWS_DIR: Final[Path] = REPO_ROOT / ".github" / "workflows"

#: 仓库文件清单的排除目录（git 不可用时的回落口径）
_EXCLUDED_DIRS: Final[tuple[str, ...]] = (
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    ".next",
    "dist",
    "coverage",
)


@dataclass(frozen=True)
class Requirement:
    """一条"门禁依赖输入 → 必须被触发"的声明。

    Attributes:
        workflow: workflow 文件名（如 `ci-web.yml`）。
        target: 仓库相对路径；可含 `*` 通配（展开成实际文件后逐个校验）。
        reason: 为什么这个文件变了必须跑该门禁（写给下一个改动者看）。
    """

    workflow: str
    target: str
    reason: str


#: C-1 的声明面。**增删此表就是改判据**，请同步 self-test 的覆盖样本。
REQUIREMENTS: Final[tuple[Requirement, ...]] = (
    # ------------------------------------------------------------------
    # ci-web.yml：npm workspaces 的锁与清单都在仓库根，`npm ci` 直接读它们
    # ------------------------------------------------------------------
    Requirement("ci-web.yml", "package-lock.json", "npm ci 按根锁文件校验依赖树"),
    Requirement("ci-web.yml", "package.json", "根清单声明 workspaces 与 npm 脚本"),
    Requirement("ci-web.yml", "apps/web/package.json", "前端清单（依赖与 scan:* 脚本）"),
    Requirement("ci-web.yml", "apps/web/Dockerfile", "前端镜像依赖同一份 lock 还原"),
    Requirement("ci-web.yml", "docs/prd-phase2-increment.md", "scan:words 的 §7 词表真源"),
    Requirement(
        "ci-web.yml",
        "apps/adapter/app/plugins/actions.py",
        "scan:capability 读插件动作清单",
    ),
    Requirement(
        "ci-web.yml",
        "apps/adapter/app/plugins/manifests/*",
        "scan:capability 读插件 manifest",
    ),
    Requirement(
        "ci-web.yml",
        ".github/e2e/keycloak-realm-ci.json",
        "H-04 后它决定 e2e 账号、角色与回调地址",
    ),
    Requirement(
        "ci-web.yml",
        "deploy/validate_standards.py",
        "阶段 2 起本 workflow 执行该脚本的前端侧子集（--checks STD-3）",
    ),
    Requirement("ci-web.yml", ".github/workflows/ci-web.yml", "门禁自身"),
    # ------------------------------------------------------------------
    # ci-adapter.yml：后端锁 / 依赖清单 / 镜像 / 迁移 / 部署配置
    # ------------------------------------------------------------------
    Requirement("ci-adapter.yml", "apps/adapter/pyproject.toml", "依赖与门禁配置的真源"),
    Requirement("ci-adapter.yml", "apps/adapter/uv.lock", "`uv sync --frozen` 的锁定树"),
    Requirement("ci-adapter.yml", "apps/adapter/Dockerfile", "生产镜像按 lock 还原依赖"),
    Requirement("ci-adapter.yml", "deploy/migrations/*.sql", "迁移门禁逐条执行这些脚本"),
    Requirement("ci-adapter.yml", "deploy/init-schema-*.sql", "CI 建库基线"),
    Requirement("ci-adapter.yml", "deploy/docker-compose.yml", "validate_deploy 解析编排"),
    Requirement("ci-adapter.yml", "deploy/images.lock", "validate_images 校验镜像摘要"),
    Requirement("ci-adapter.yml", "deploy/prometheus.yml", "H-01 告警链路自证"),
    Requirement("ci-adapter.yml", "deploy/alert.rules.yml", "H-01 规则文件自证"),
    Requirement("ci-adapter.yml", "docs/api/openapi.json", "G4 快照比对"),
    Requirement("ci-adapter.yml", ".github/workflows/ci-adapter.yml", "门禁自身"),
    # ------------------------------------------------------------------
    # dependency-scan.yml：SCA 读两份锁；push 无 paths（全量触发），只查 PR
    # ------------------------------------------------------------------
    Requirement("dependency-scan.yml", "package.json", "前端依赖清单（SCA 输入）"),
    Requirement("dependency-scan.yml", "package-lock.json", "前端锁（SCA 输入）"),
    Requirement("dependency-scan.yml", "apps/adapter/pyproject.toml", "后端依赖清单"),
    Requirement("dependency-scan.yml", "apps/adapter/uv.lock", "后端锁定树"),
    Requirement("dependency-scan.yml", "deploy/images.lock", "镜像摘要扫描输入"),
    Requirement("dependency-scan.yml", ".github/workflows/dependency-scan.yml", "门禁自身"),
)


def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """把 GitHub paths 过滤模式编译成正则。

    只实现本仓库用到的子集，但**语义与 GitHub 一致**：

    - `*` 匹配一段内任意字符（不跨 `/`）；
    - `**` 跨 `/`（`**/` 可匹配零段或多段）；
    - `?` 匹配单个非 `/` 字符。

    Args:
        pattern: `paths` 里的一条模式（不含前导 `!`）。

    Returns:
        re.Pattern[str]: 整串匹配用的正则。
    """
    parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                parts.append(r"(?:[^/]+/)*")
                index += 3
            elif pattern.startswith("**", index):
                parts.append(r".*")
                index += 2
            else:
                parts.append(r"[^/]*")
                index += 1
        elif char == "?":
            parts.append(r"[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("^" + "".join(parts) + "$")


def path_matches(pattern: str, path: str) -> bool:
    """判断一条 paths 模式是否命中仓库相对路径。

    Args:
        pattern: 模式（支持前导 `!` 的排除语义）。
        path: 仓库相对 POSIX 路径。

    Returns:
        bool: 命中返回 True。
    """
    if pattern.startswith("!"):
        return not path_matches(pattern[1:], path)
    return bool(pattern_to_regex(pattern).match(path))


def is_covered(path: str, patterns: Sequence[str]) -> bool:
    """判断路径是否被 patterns 覆盖（正向命中且未被排除）。

    Args:
        path: 仓库相对 POSIX 路径。
        patterns: 该事件的 `paths` 列表。

    Returns:
        bool: 覆盖返回 True。
    """
    positive = [item for item in patterns if not item.startswith("!")]
    negative = [item[1:] for item in patterns if item.startswith("!")]
    if not any(path_matches(item, path) for item in positive):
        return False
    return not any(path_matches(item, path) for item in negative)


def expand_target(target: str, repo_files: Sequence[str]) -> list[str]:
    """把声明目标展开成实际文件清单。

    Args:
        target: 具体路径，或含 `*`/`?` 的通配。
        repo_files: 仓库文件清单（仓库相对 POSIX 路径）。

    Returns:
        list[str]: 命中的文件；具体路径直接返回自身（存在性由调用方另判）。
    """
    if not any(char in target for char in "*?["):
        return [target]
    return sorted(item for item in repo_files if path_matches(target, item))


def extract_event_paths(workflow_text: str) -> dict[str, tuple[str, ...] | None]:
    """解析 workflow 的触发事件与 `paths`。

    **PyYAML 按 YAML 1.1 解析，`on:` 会变成布尔键 `True`**，这里两态都接。

    Args:
        workflow_text: workflow YAML 文本。

    Returns:
        dict[str, tuple[str, ...] | None]: 事件名 → 模式元组；`None` 表示该事件
        没有 `paths`（全量触发）。解析失败返回 `{"_error": None}`。
    """
    try:
        data = yaml.safe_load(workflow_text)
    except yaml.YAMLError:
        return {"_error": None}
    if not isinstance(data, dict):
        return {"_error": None}
    spec = data.get("on", data.get(True))
    if not isinstance(spec, dict):
        return {"_error": None}

    result: dict[str, tuple[str, ...] | None] = {}
    for event, config in spec.items():
        if not isinstance(event, str):
            continue
        if isinstance(config, dict) and "paths" in config:
            raw = config.get("paths") or []
            result[event] = tuple(str(item) for item in raw)
        else:
            result[event] = None
    return result


def find_dead_patterns(
    event_paths: Mapping[str, tuple[str, ...] | None],
    repo_files: Sequence[str],
) -> list[str]:
    """C-2：找出不命中任何真实文件的 paths 模式。

    Args:
        event_paths: 事件 → 模式元组（`None` 跳过）。
        repo_files: 仓库文件清单。

    Returns:
        list[str]: 死模式描述（升序）。
    """
    problems: list[str] = []
    for event, patterns in sorted(event_paths.items()):
        if patterns is None:
            continue
        for pattern in patterns:
            if pattern.startswith("!"):
                continue
            if not any(path_matches(pattern, item) for item in repo_files):
                problems.append(
                    f"[{event}] paths 模式 `{pattern}` 不命中任何仓库文件（门禁永不触发）"
                )
    return problems


def evaluate(
    requirements: Sequence[Requirement],
    workflow_paths: Mapping[str, Mapping[str, tuple[str, ...] | None] | None],
    repo_files: Sequence[str],
) -> tuple[int, list[str]]:
    """判定 paths 漂移（纯函数，不读文件系统——self-test 靠它注入内存样本）。

    Args:
        requirements: 声明面。
        workflow_paths: workflow 名 → 事件 → 模式；`None` 表示 workflow 缺失/不可解析。
        repo_files: 仓库文件清单。

    Returns:
        tuple[int, list[str]]: (退出码, 问题清单)。0=通过；1=有漂移；2=无法判定。
    """
    if not workflow_paths:
        return 2, ["找不到任何 workflow，无法判定 paths 覆盖"]
    if not repo_files:
        return 2, ["仓库文件清单为空（读不到输入），无法判定 paths 覆盖"]

    problems: list[str] = []
    broken = sorted(name for name, spec in workflow_paths.items() if spec is None)
    for name in broken:
        problems.append(f"{name} 缺失或 YAML 解析失败，无法判定其 paths")

    for spec in workflow_paths.values():
        if spec is not None:
            problems.extend(find_dead_patterns(spec, repo_files))

    for requirement in requirements:
        spec = workflow_paths.get(requirement.workflow)
        if spec is None:
            # 已在上方记录缺失；此处不重复刷同类问题
            continue
        witnesses = expand_target(requirement.target, repo_files)
        if not witnesses:
            problems.append(
                f"{requirement.workflow}：声明输入 `{requirement.target}` 在仓库中不存在"
                f"（{requirement.reason}）"
            )
            continue
        for witness in witnesses:
            for event, patterns in sorted(spec.items()):
                if patterns is None:
                    continue
                if not is_covered(witness, patterns):
                    problems.append(
                        f"{requirement.workflow} [{event}] `{witness}` 未被 paths 覆盖"
                        f"（{requirement.reason}）"
                    )
    return (1 if problems else 0), problems


def load_repo_files(root: Path) -> list[str]:
    """列出仓库文件（仓库相对 POSIX 路径）。

    优先用 `git ls-files`（只含被跟踪文件，天然排除 `node_modules` 等）；
    git 不可用时回落到 `rglob` 并按排除目录过滤。两者都拿不到时返回空列表，
    由 `evaluate` 判 fail-closed（exit 2）。

    Args:
        root: 仓库根目录。

    Returns:
        list[str]: 文件路径清单。
    """
    git = shutil.which("git")
    completed: subprocess.CompletedProcess[str] | None = None
    if git is not None:
        try:
            completed = subprocess.run(  # noqa: S603 - argv 由脚本内常量组成，无外部输入
                [git, "ls-files"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
    if completed is not None:
        files = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if files:
            return sorted(files)

    if not root.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not any(part in _EXCLUDED_DIRS for part in path.parts)
    )


def _load_workflow_specs(
    root: Path,
) -> dict[str, Mapping[str, tuple[str, ...] | None] | None]:
    """读取 `<repo>/.github/workflows/*.yml` 的触发配置。

    Args:
        root: 仓库根目录。

    Returns:
        dict[str, Mapping[str, tuple[str, ...] | None] | None]: 文件名 → 事件 → 模式；
        解析失败映射为 `None`（由 evaluate 判红）。
    """
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return {}
    specs: dict[str, Mapping[str, tuple[str, ...] | None] | None] = {}
    for path in sorted(workflows_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = extract_event_paths(text)
        specs[path.name] = None if "_error" in parsed else parsed
    return specs


def run_self_test() -> int:
    """负样本自检：证明覆盖判据、死模式判据、fail-closed 都能红。

    本仓库约定：全部样本符合预期时返回 1（CI 用 `-ne 1` 断言自检真的跑过）。

    Returns:
        int: 1 = 自检通过；0 = 有样本不符（供 CI 直接判红）。
    """
    requirement = Requirement("ci-web.yml", "package-lock.json", "根锁文件")
    repo_files = [
        "package.json",
        "package-lock.json",
        "apps/web/package.json",
        "apps/web/src/page.tsx",
        "deploy/migrations/0002_x.sql",
    ]

    cases: list[tuple[str, bool]] = []

    # 正样本：显式列出根锁 + 目录通配
    code, problems = evaluate(
        [requirement],
        {"ci-web.yml": {"pull_request": ("package-lock.json", "apps/web/**")}},
        repo_files,
    )
    cases.append(("正样本：根锁被显式覆盖 → exit 0", code == 0 and not problems))

    # 负样本 1：**真实事故回放**——只写 apps/web/**，根锁变更不触发
    code, problems = evaluate(
        [requirement],
        {"ci-web.yml": {"pull_request": ("apps/web/**",)}},
        repo_files,
    )
    cases.append(
        (
            "负样本 1：paths 只含 apps/web/** → 根 package-lock.json 必须判红",
            code == 1 and any("package-lock.json" in item for item in problems),
        )
    )

    # 负样本 2：push 与 pull_request 都要查（不能只查一个事件）
    code, problems = evaluate(
        [requirement],
        {
            "ci-web.yml": {
                "push": ("package-lock.json", "apps/web/**"),
                "pull_request": ("apps/web/**",),
            }
        },
        repo_files,
    )
    cases.append(
        (
            "负样本 2：PR 事件漏根锁 → exit 1",
            code == 1 and any("pull_request" in item for item in problems),
        )
    )

    # 负样本 3：**glob 语义**——`apps/*` 不跨 `/`，覆盖不了 apps/web/package.json
    code, problems = evaluate(
        [Requirement("ci-web.yml", "apps/web/package.json", "前端清单")],
        {"ci-web.yml": {"pull_request": ("apps/*",)}},
        repo_files,
    )
    cases.append(
        (
            "负样本 3：`apps/*` 覆盖不了 apps/web/package.json（防止 startswith 式假绿）",
            code == 1 and any("apps/web/package.json" in item for item in problems),
        )
    )

    # 负样本 4：死模式（抄错路径）→ 永远不触发
    code, problems = evaluate(
        [requirement],
        {"ci-web.yml": {"pull_request": ("package-lock.json", "apps/web/package-lock.json")}},
        repo_files,
    )
    cases.append(
        (
            "负样本 4：paths 写了不存在的文件 → 判死模式红",
            code == 1 and any("apps/web/package-lock.json" in item for item in problems),
        )
    )

    # 负样本 5：声明输入在仓库里不存在 → 判红（不能静默跳过）
    code, problems = evaluate(
        [Requirement("ci-web.yml", "missing.lock", "不存在的输入")],
        {"ci-web.yml": {"pull_request": ("missing.lock",)}},
        repo_files,
    )
    cases.append(("负样本 5：声明输入不存在 → exit 1", code == 1 and bool(problems)))

    # 负样本 6：workflow 缺失 → fail-closed（exit 2，而不是"0 问题"）
    code, problems = evaluate([requirement], {}, repo_files)
    cases.append(
        (
            "负样本 6：workflow 缺失 → exit 2（fail-closed）",
            code == 2 and bool(problems),
        )
    )

    # 正样本：`**` 跨层级；迁移目录用目录通配展开
    code, problems = evaluate(
        [Requirement("ci-adapter.yml", "deploy/migrations/*.sql", "迁移脚本")],
        {"ci-adapter.yml": {"push": ("deploy/**",)}},
        repo_files,
    )
    cases.append(("正样本：deploy/** 覆盖迁移脚本 → exit 0", code == 0 and not problems))

    failed = 0
    for title, ok in cases:
        if ok:
            print(f"[OK]   {title}")
        else:
            failed += 1
            print(f"[FAIL] {title}（**不符合**）")
    if failed:
        print(f"[FAIL] CI paths 自检失败：{failed}/{len(cases)} 条样本不符（退出码 0，供 CI 判红）")
        return 0
    print(f"[OK]   CI paths 自检通过：{len(cases)} 条样本全部符合（退出码 1 为预期）")
    return 1


def main(argv: list[str] | None = None) -> int:
    """入口。

    Args:
        argv: 命令行参数（默认取 `sys.argv`）。

    Returns:
        int: 0=通过；1=发现漂移；2=无法判定。
    """
    parser = argparse.ArgumentParser(description="CI paths 漂移校验（H-05）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（成功时退出码 1）")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    workflow_specs = _load_workflow_specs(REPO_ROOT)
    repo_files = load_repo_files(REPO_ROOT)
    code, problems = evaluate(REQUIREMENTS, workflow_specs, repo_files)

    for problem in problems:
        print(f"[FAIL] {problem}")
    if code == 2:
        return 2
    if code == 1:
        print(f"[FAIL] CI paths 漂移未通过：{len(problems)} 处（改依赖输入不触发门禁 = 假绿）")
        return 1
    print(
        f"[OK]   CI paths 覆盖通过：{len(REQUIREMENTS)} 条依赖输入在对应 workflow 的"
        f" push/pull_request paths 内；模式全部命中真实文件"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
