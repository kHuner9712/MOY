#!/usr/bin/env python3
"""门禁接线体检（C-1 / C-4）：**判据存在 ≠ 判据在跑**。

**为什么要有这条门禁**（2026-10-27 第三波，同一类死角一次抓到三处）：

1. `deploy/validate_schema_retention.py`：判据完整、`--self-test` 齐全，但
   `grep -n validate_schema_retention .github/workflows/*.yml` **零命中**。
   从 workflow 侧 grep 只能证明"某些在跑"，证不出"某个没跑"。
2. CI 的 PG 初始化：缺 `init-schema-phase4.sql`，且迁移 glob 写成 `000*.sql`
   （匹配不到 `0010_`/`0011_`）。有 PG 服务 ≠ 库初始化完整；带 `--run-pg` 真跑才暴露。
3. `mypy app`：`[tool.mypy] files = ["app"]`，`deploy/` 的 25 条错误
   **从设计上就不在检查范围**。门禁绿的正当理由是"没检查"，日志里看不出区别。

三条的共同点：**判据写对了、脚本能跑、结论"绿"——但绿的理由是"没检查"**。
本脚本把"检查过"变成两条可机械验证的判据，跑一次即可发现上面三处。

判据：

- **C-1 反向遍历**：遍历 `deploy/validate_*.py`，逐个查是否出现在任一
  `.github/workflows/*.yml` 里。**从脚本侧遍历**是关键——从 workflow 侧 grep
  只能回答"某脚本有没有被引用"，回答不了"有没有脚本没被引用"。
  **不排除自身**：排除自己等于给自己开后门，正是本门禁要防的形态。
- **C-4 初始化差集**：仓库现有的基线 + 迁移 SQL（`deploy/init-schema-*.sql`、
  `deploy/migrations/0*.sql`，**不含 `rollback/*.down.sql`**）与 CI 里**实际会被执行**的
  集合做差集，必须为空。CI 一侧走**静态解析**（读 workflow 文本里的 `psql -f` 与
  `for f in …` 的 glob），glob 先展开成实际文件再比——否则 `0*.sql` 与
  `0002..0012` 这类**永远比不出来**（本轮就踩过 `000*.sql` 漏 `0010`/`0011`）。

**两个已踩过的坑（实现里刻意规避，改动前请先读）**：

1. 迁移 glob 用 `0*.sql` 而不是 `000*.sql`：后者匹配不到以 `001` 开头的 `0010_`/`0011_`；
2. 收集 SQL 时不能把 `rollback/*.down.sql` 算进来：它们是回滚脚本，不是初始化路径，
   误跑会把表删掉（实测表数从 41 掉到 34）。

用法::

    python deploy/validate_gate_wiring.py              # 体检真实仓库
    python deploy/validate_gate_wiring.py --self-test  # 负样本自检（期望退出码 1）

退出码：0 = 通过；1 = 发现未接线；2 = 门禁自身无法判定（workflow 缺失等，fail-closed）。
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

#: 仓库根目录（本脚本位于 <repo>/deploy/）
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 门禁脚本目录
DEPLOY_DIR: Final[Path] = REPO_ROOT / "deploy"

#: workflow 目录
WORKFLOWS_DIR: Final[Path] = REPO_ROOT / ".github" / "workflows"

#: 需要接线的脚本形态（C-1 的遍历面）
SCRIPT_GLOB: Final[str] = "validate_*.py"

#: 基线 schema 脚本（C-4 的仓库侧，不含 phase4 之外的其它变体）
BASELINE_GLOB: Final[str] = "init-schema-*.sql"

#: 正向迁移（**不递归**，因此天然不含 `rollback/`）
MIGRATION_GLOB: Final[str] = "0*.sql"

#: `psql … -f <path>` / `$PSQL -f <path>`（CI 用 shell 变量承载 psql 命令）
PSQL_FILE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\bpsql|\$\{?PSQL\}?)[^\n]*?\s-f\s+(\S+)"
)

#: `for f in <词…>;`——CI 用循环跑全部迁移
FOR_GLOB_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bfor\s+\w+\s+in\s+([^\n;]+)")

#: 仓库内路径锚点（workflow 里的 `../../deploy/...` 一律归一到 `deploy/...`）
PATH_ANCHOR: Final[str] = "deploy/"


def discover_scripts(deploy_dir: Path) -> list[str]:
    """列出需要接线的门禁脚本名（含本脚本自身）。

    **刻意包含自身**：若排除自己，本门禁就永远无法证明"自己真的被接线"，
    等于给自己开后门。代价是"写出来但没挂 CI"时它会报自己红——那不是 bug，是判据正确。

    Args:
        deploy_dir: `deploy/` 目录。

    Returns:
        list[str]: 脚本文件名（升序）。
    """
    return sorted(path.name for path in deploy_dir.glob(SCRIPT_GLOB) if path.is_file())


def discover_sql(deploy_dir: Path) -> list[str]:
    """列出门禁口径下的仓库 SQL（仓库相对 POSIX 路径）。

    Args:
        deploy_dir: `deploy/` 目录。

    Returns:
        list[str]: `deploy/init-schema-*.sql` 与 `deploy/migrations/0*.sql`（升序去重）。
    """
    paths: list[Path] = [p for p in deploy_dir.glob(BASELINE_GLOB) if p.is_file()]
    migrations_dir = deploy_dir / "migrations"
    if migrations_dir.is_dir():
        # 非递归 glob：`rollback/*.down.sql` 不会被收进来（那是回滚脚本，不是初始化路径）
        paths.extend(p for p in migrations_dir.glob(MIGRATION_GLOB) if p.is_file())
    return sorted({p.relative_to(REPO_ROOT).as_posix() for p in paths})


def load_workflows(workflows_dir: Path) -> dict[str, str]:
    """读取全部 workflow 文本。

    Args:
        workflows_dir: `.github/workflows` 目录。

    Returns:
        dict[str, str]: 文件名 → 文本；目录不存在时返回空字典（由调用方判 fail-closed）。
    """
    if not workflows_dir.is_dir():
        return {}
    return {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(workflows_dir.glob("*.yml"))
        if path.is_file()
    }


def _normalize_target(token: str) -> str | None:
    """把 workflow 里的路径 token 归一为仓库相对路径。

    Args:
        token: 形如 `../../deploy/migrations/0*.sql`、`"deploy/x.sql"`、`$f` 的原始 token。

    Returns:
        str | None: `deploy/...` 形式的路径；不是 SQL 路径时返回 None。
    """
    cleaned = token.strip().strip("\"'")
    if not cleaned.endswith(".sql"):
        return None
    index = cleaned.find(PATH_ANCHOR)
    if index < 0:
        return None
    return cleaned[index:]


def extract_ci_targets(workflow_texts: Mapping[str, str]) -> tuple[set[str], set[str]]:
    """静态解析 workflow，取出 CI 会执行的 SQL 目标。

    Args:
        workflow_texts: 文件名 → 文本。

    Returns:
        tuple[set[str], set[str]]: (显式路径集合, glob 模式集合)。
    """
    explicit: set[str] = set()
    patterns: set[str] = set()
    for text in workflow_texts.values():
        tokens: list[str] = [match.group(1) for match in PSQL_FILE_PATTERN.finditer(text)]
        for match in FOR_GLOB_PATTERN.finditer(text):
            tokens.extend(match.group(1).split())
        for token in tokens:
            target = _normalize_target(token)
            if target is None:
                continue
            if "*" in target or "?" in target:
                patterns.add(target)
            else:
                explicit.add(target)
    return explicit, patterns


def find_unwired_scripts(scripts: Sequence[str], workflow_texts: Mapping[str, str]) -> list[str]:
    """C-1：找出未出现在任何 workflow 里的门禁脚本。

    Args:
        scripts: 脚本名清单。
        workflow_texts: 文件名 → 文本。

    Returns:
        list[str]: 未接线的脚本名（升序）。
    """
    texts = tuple(workflow_texts.values())
    return sorted(name for name in scripts if not any(name in text for text in texts))


def find_uncovered_sql(repo_sql: Sequence[str], workflow_texts: Mapping[str, str]) -> list[str]:
    """C-4：找出仓库里有、但 CI 不会执行的 SQL。

    glob 会先展开成对仓库实际文件的匹配（`fnmatch`），而不是把模式串当字面量比——
    `0*.sql` 能覆盖 `0010_…`、`000*.sql` 覆盖不到，这一点必须靠展开才验得出来。

    Args:
        repo_sql: 仓库侧 SQL 路径清单。
        workflow_texts: 文件名 → 文本。

    Returns:
        list[str]: 未被覆盖的 SQL 路径（升序）。
    """
    explicit, patterns = extract_ci_targets(workflow_texts)
    return sorted(
        path
        for path in repo_sql
        if path not in explicit
        and not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
    )


def evaluate(
    scripts: Sequence[str],
    repo_sql: Sequence[str],
    workflow_texts: Mapping[str, str],
) -> tuple[int, list[str]]:
    """判定接线状态（纯函数，不读文件系统——self-test 靠它注入内存样本）。

    Args:
        scripts: 脚本名清单。
        repo_sql: 仓库侧 SQL 路径清单。
        workflow_texts: 文件名 → 文本。

    Returns:
        tuple[int, list[str]]: (退出码, 问题清单)。0=通过；1=有发现；2=无法判定。
    """
    if not workflow_texts:
        # fail-closed：workflow 目录缺失或被搬走时，**不能**打印"0 问题"当作通过
        return 2, [f"找不到任何 workflow 文件（{WORKFLOWS_DIR}），无法判定接线状态"]
    if not scripts:
        return 2, [f"在 {DEPLOY_DIR} 下找不到任何 {SCRIPT_GLOB}，无法判定"]

    problems: list[str] = []
    for name in find_unwired_scripts(scripts, workflow_texts):
        problems.append(f"{name} 未在任何 workflow 中被引用（判据存在但不在跑）")
    for path in find_uncovered_sql(repo_sql, workflow_texts):
        problems.append(f"{path} 存在但 CI 不会执行（库初始化会缺这一段）")
    return (1 if problems else 0), problems


def _make_tree(scripts: Sequence[str], repo_sql: Sequence[str]) -> tuple[list[str], list[str]]:
    """构造 self-test 用的内存样本（不读仓库）。

    Args:
        scripts: 脚本名清单。
        repo_sql: SQL 路径清单。

    Returns:
        tuple[list[str], list[str]]: (脚本清单, SQL 清单)。
    """
    return list(scripts), list(repo_sql)


def run_self_test() -> int:
    """负样本自检：证明两类判据都能红、且缺失输入时 fail-closed。

    Returns:
        int: 全部符合预期返回 1（本仓库约定：自检成功 = exit 1，供 CI 用 `-ne 1` 断言）。
    """
    wired_workflow = {
        "ci.yml": (
            "$PSQL -f ../../deploy/init-schema-phase2.sql\n"
            "$PSQL -f ../../deploy/init-schema-phase3.sql\n"
            "for f in ../../deploy/migrations/0*.sql; do\n"
            '  $PSQL -f "$f"\n'
            "done\n"
            "python ../../deploy/validate_a.py\n"
            "python ../../deploy/validate_b.py\n"
        )
    }
    scripts, repo_sql = _make_tree(
        ["validate_a.py", "validate_b.py"],
        [
            "deploy/init-schema-phase2.sql",
            "deploy/init-schema-phase3.sql",
            "deploy/migrations/0002_x.sql",
            "deploy/migrations/0010_y.sql",
        ],
    )

    cases: list[tuple[str, int, bool]] = []

    code, problems = evaluate(scripts, repo_sql, wired_workflow)
    cases.append(("正样本：全部接线 → exit 0 且无问题", 0, code == 0 and not problems))

    # 负样本 1：脚本未被引用
    code, problems = evaluate(
        scripts,
        repo_sql,
        {"ci.yml": wired_workflow["ci.yml"].replace("python ../../deploy/validate_b.py\n", "")},
    )
    cases.append(
        (
            "负样本 1：脚本未挂 CI → exit 1",
            1,
            code == 1 and any("validate_b.py" in item for item in problems),
        )
    )

    # 负样本 2：**自指**——新门禁自己没被引用时必须报红（证明没有自我豁免）
    code, problems = evaluate(
        ["validate_a.py", "validate_gate_wiring.py"],
        ["deploy/init-schema-phase2.sql"],
        {
            "ci.yml": "$PSQL -f ../../deploy/init-schema-phase2.sql\n"
            "python ../../deploy/validate_a.py\n"
        },
    )
    cases.append(
        (
            "负样本 2（自指）：validate_gate_wiring.py 未被引用 → 必须报它自己红",
            1,
            code == 1 and any("validate_gate_wiring.py" in item for item in problems),
        )
    )

    # 负样本 3：SQL 存在但 CI 不执行
    code, problems = evaluate(
        ["validate_a.py"],
        ["deploy/init-schema-phase2.sql", "deploy/init-schema-phase4.sql"],
        {
            "ci.yml": "$PSQL -f ../../deploy/init-schema-phase2.sql\n"
            "python ../../deploy/validate_a.py\n"
        },
    )
    cases.append(
        (
            "负样本 3：phase4 未被执行 → exit 1",
            1,
            code == 1 and any("init-schema-phase4.sql" in item for item in problems),
        )
    )

    # 负样本 4：`000*.sql` 这种写法的漏匹配必须能被抓出来（本轮真实事故的回放）
    code, problems = evaluate(
        ["validate_a.py"],
        ["deploy/migrations/0002_x.sql", "deploy/migrations/0010_y.sql"],
        {
            "ci.yml": "for f in ../../deploy/migrations/000*.sql; do\n"
            '  $PSQL -f "$f"\n'
            "done\n"
            "python ../../deploy/validate_a.py\n"
        },
    )
    cases.append(
        (
            "负样本 4：glob 写成 000*.sql → 0010_y.sql 必须被判为未覆盖",
            1,
            code == 1 and any("0010_y.sql" in item for item in problems),
        )
    )

    # 负样本 5：workflow 缺失 → fail-closed（exit 2，而不是"0 问题"）
    code, problems = evaluate(scripts, repo_sql, {})
    cases.append(
        ("负样本 5：workflow 缺失 → exit 2（fail-closed）", 2, code == 2 and bool(problems))
    )

    failed = 0
    for title, expected, ok in cases:
        if ok:
            print(f"[OK]   {title}（期望 exit {expected}，符合）")
        else:
            failed += 1
            print(f"[FAIL] {title}（期望 exit {expected}，**不符合**）")
    if failed:
        print(f"[FAIL] 门禁接线自检失败：{failed}/{len(cases)} 条样本不符（退出码 0，供 CI 判红）")
        return 0
    print(f"[OK]   门禁接线自检通过：{len(cases)} 条样本全部符合（退出码 1 为预期）")
    return 1


def main(argv: list[str] | None = None) -> int:
    """入口。

    Args:
        argv: 命令行参数（默认取 sys.argv）。

    Returns:
        int: 0=通过；1=发现未接线；2=无法判定。
    """
    parser = argparse.ArgumentParser(description="门禁接线体检（C-1 / C-4）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（成功时退出码 1）")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    scripts = discover_scripts(DEPLOY_DIR)
    repo_sql = discover_sql(DEPLOY_DIR)
    workflow_texts = load_workflows(WORKFLOWS_DIR)

    code, problems = evaluate(scripts, repo_sql, workflow_texts)
    if code == 2:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 2
    for problem in problems:
        print(f"[FAIL] {problem}")
    if code == 1:
        print(f"[FAIL] 门禁接线未通过：{len(problems)} 处（跑的是哪条路径，比跑没跑更重要）")
        return 1
    print(
        f"[OK]   门禁接线通过：{len(scripts)} 个 validate_*.py 全部被 workflow 引用；"
        f"{len(repo_sql)} 个基线/迁移 SQL 全部在 CI 执行路径内"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
