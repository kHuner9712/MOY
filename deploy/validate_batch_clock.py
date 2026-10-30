#!/usr/bin/env python3
"""批次时钟门禁（R2-QA-16，审计 2026-10-29）。

**为什么需要它**

`ci-adapter.yml` 里 `OA_BATCH: B21` 是**硬编码**的批次号，而缺陷台账
（`apps/adapter/tests/expected_xfail.py::DEFECT_LEDGER`）用 `due_batch` 表达
「这条缺陷最迟必须在哪个批次前处理完」。

两者的联动**完全靠人记得改**。而 `expected_xfail.current_batch()` 的
「缺失即红」只防住了「忘记设」，**没防住「设成旧值」**——把 `B21` 一直留着，
它永远合法、永远绿。

后果是**静默放行**型失效：批次推进到 B30 而 CI 仍写 B21 时，那些
`due_batch=B30` 的到期缺陷**永远不会被判定为过期**，门禁照常绿、没人会注意。
台账文件自己就在警告这件事：「字符串比较会让它永久逃检」。

**判据（三条）**

- C1：`OA_BATCH` 必须符合 `B<正整数>` 格式（防「阶段四」这类非批次号混入）；
- C2：`OA_BATCH` 不得**落后**于台账中任何 `status != "fixed"` 条目的 `due_batch`
      （领先无妨；落后即说明时钟停在过去，到期检查已失真）；
- C3（`--check-docs`，P1-5）：文档/本地脚本里硬编码的批次号必须与当前批次一致
      （`DOC_SOURCES` 列出的同步点）。C2 管「CI 落后」，C3 管「CI 改了而文档没改」
      ——后者让两套口径都自洽，评审时无法判断哪个才是准的。

用法::

    OA_BATCH=B21 python deploy/validate_batch_clock.py
    OA_BATCH=B21 python deploy/validate_batch_clock.py --check-docs   # CI 口径（含文档同步）
    python deploy/validate_batch_clock.py --self-test   # 负样本自检（期望退出码 1）

退出码：0 = 通过；1 = 违规或自检命中；2 = 门禁自身异常（文件缺失/解析失败）。
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

#: 仓库根
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 缺陷台账文件
LEDGER_PATH: Final[Path] = REPO_ROOT / "apps" / "adapter" / "tests" / "expected_xfail.py"

#: 台账变量名
LEDGER_NAME: Final[str] = "DEFECT_LEDGER"

#: 批次号格式：B + 正整数
BATCH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^B(\d+)$")

#: 批次号的**同步点**（文件相对路径 + 提取正则），升批时逐处同步（P1-5，审计 2026-10-30）。
#:
#: 为什么需要它：`OA_BATCH` 在 CI env 与文档/本地脚本里各写一遍，全靠人记得同步。
#: 上一道 C2 只防「CI 里的批次落后于台账」，防不住「CI 改成 B30 但 README 仍是 B21」
#: ——后者是**文档口径分叉**：门禁按 B30 判、文档按 B21 讲，评审时两套数字都对。
#: 本检查把「漏改一处」变成红，并在输出里直接列出所有未同步位置。
#:
#: 唯一真源 = `ci-adapter.yml` 的 `OA_BATCH`；其余位置必须与它一致。
DOC_SOURCES: Final[tuple[tuple[str, str], ...]] = (
    ("README.md", r"OA_BATCH\s*[=:]\s*(B\d+)"),
    ("apps/adapter/README.md", r"OA_BATCH\s*[=:]\s*(B\d+)"),
    (".github/workflows/ci-adapter.yml", r"OA_BATCH\s*[=:]\s*(B\d+)"),
    ("qa/phase4-verify.sh", r"OA_BATCH\s*[=:]\s*(B\d+)"),
)


def parse_batch(value: str) -> int:
    """把 `B21` 解析为 21。

    Args:
        value: 批次号字符串。

    Returns:
        int: 批次序号。

    Raises:
        ValueError: 格式不符。
    """
    match = BATCH_PATTERN.match(value.strip())
    if match is None:
        raise ValueError(f"批次号格式非法：{value!r}（期望形如 B21）")
    return int(match.group(1))


def load_ledger(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    """用 AST 解析台账（**不 import 被测模块**，避免把 pytest 变成前置依赖）。

    Args:
        path: 台账文件路径。

    Returns:
        list[dict[str, Any]]: 台账条目列表。

    Raises:
        RuntimeError: 文件缺失或找不到 `DEFECT_LEDGER` 字面量。
    """
    if not path.is_file():
        raise RuntimeError(f"台账文件缺失：{path}")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    value: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == LEDGER_NAME:
                value = node.value
                break
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == LEDGER_NAME for t in node.targets):
                value = node.value
                break
    if value is None:
        raise RuntimeError(f"未在 {path.name} 中找到 {LEDGER_NAME} 定义")

    if not isinstance(value, ast.Tuple):
        raise RuntimeError(f"{LEDGER_NAME} 不是字面量元组，无法静态解析")
    return [ast.literal_eval(element) for element in value.elts]


def find_violations(current_batch: str, ledger: list[dict[str, Any]]) -> list[str]:
    """按 C1/C2 判定，返回违规描述列表（空列表 = 通过）。

    Args:
        current_batch: 当前批次号（如 `B21`）。
        ledger: 台账条目。

    Returns:
        list[str]: 违规描述。
    """
    violations: list[str] = []
    try:
        current = parse_batch(current_batch)
    except ValueError as exc:
        violations.append(f"C1 {exc}")
        return violations

    for index, entry in enumerate(ledger):
        status = str(entry.get("status", "")).strip().lower()
        if status == "fixed":
            # 已修复条目不再构成"到期必须处理"的压力，跳过
            continue
        due_raw = entry.get("due_batch")
        if not isinstance(due_raw, str) or not due_raw.strip():
            violations.append(
                f"C2 台账第 {index + 1} 条（status={status}）缺少 due_batch——"
                "无法判断是否过期；未写批次号等于永久逃检"
            )
            continue
        try:
            due = parse_batch(due_raw)
        except ValueError as exc:
            violations.append(f"C2 台账第 {index + 1} 条：{exc}")
            continue
        if current < due:
            violations.append(
                f"C2 OA_BATCH={current_batch} 落后于台账第 {index + 1} 条"
                f"（status={status}）的 due_batch={due_raw}："
                "批次时钟停在过去，该条到期检查已失真（门禁仍绿，无人会注意）"
            )
    return violations


def check_docs(batch: str, root: Path = REPO_ROOT) -> list[str]:
    """校验文档/脚本中硬编码的批次号与当前批次一致（C3，P1-5）。

    Args:
        batch: 当前批次（如 `B21`）。
        root: 仓库根（自检时传临时目录）。

    Returns:
        list[str]: 漂移描述（空列表 = 全部一致）。

    Raises:
        RuntimeError: 同步点文件缺失（改名/删除时必须同步更新 `DOC_SOURCES`）。
    """
    violations: list[str] = []
    for relative, pattern in DOC_SOURCES:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(
                f"批次同步点文件缺失：{relative}（文件改名/删除时必须同步更新 DOC_SOURCES）"
            )
        compiled = re.compile(pattern)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = compiled.search(line)
            if match is None:
                continue
            found = match.group(1)
            if found != batch:
                violations.append(
                    f"C3 {relative}:{lineno} 写的是 {found}，与当前批次 {batch} 不一致——"
                    "升批漏改会让文档口径与门禁分叉（两套数字都自洽，评审无法判断）"
                )
    return violations


def run_self_test() -> int:
    """负样本自检：构造一个"时钟落后"的台账，必须被判红。

    Returns:
        int: 命中预期返回 1（供 CI 断言），否则 0。
    """
    with tempfile.TemporaryDirectory() as tmp:
        ledger_file = Path(tmp) / "expected_xfail.py"
        ledger_file.write_text(
            "\n".join(
                [
                    "DEFECT_LEDGER = (",
                    '    {"id": "X-1", "status": "open", "due_batch": "B30"},',
                    '    {"id": "X-2", "status": "fixed", "due_batch": "B21"},',
                    ")",
                ]
            ),
            encoding="utf-8",
        )
        ledger = load_ledger(ledger_file)

        # 正样本：当前批次落后（B21 < B30）→ 必须命中
        stale = find_violations("B21", ledger)
        # 负样本：当前批次达标（B31 >= B30）→ 必须为空
        fresh = find_violations("B31", ledger)
        # 负样本：格式非法 → 必须命中 C1
        malformed = find_violations("阶段四", ledger)
        # 负样本：fixed 条目即便 due 更晚也不该报
        only_fixed = find_violations("B21", [{"status": "fixed", "due_batch": "B99"}])

        # C3 文档口径：四个同步点全部写 B21 → 放行；改其中一处 → 必须命中
        docs_root = Path(tmp)
        (docs_root / "README.md").write_text("OA_BATCH=B21\n", encoding="utf-8")
        (docs_root / "apps" / "adapter").mkdir(parents=True, exist_ok=True)
        (docs_root / "apps" / "adapter" / "README.md").write_text(
            "OA_BATCH=B21\n", encoding="utf-8"
        )
        (docs_root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (docs_root / ".github" / "workflows" / "ci-adapter.yml").write_text(
            "OA_BATCH: B21\n", encoding="utf-8"
        )
        (docs_root / "qa").mkdir(exist_ok=True)
        (docs_root / "qa" / "phase4-verify.sh").write_text("OA_BATCH=B21\n", encoding="utf-8")
        docs_ok = check_docs("B21", docs_root)
        (docs_root / "README.md").write_text("OA_BATCH=B30\n", encoding="utf-8")
        docs_drift = check_docs("B21", docs_root)

        ok = (
            bool(stale)
            and not fresh
            and bool(malformed)
            and not only_fixed
            and not docs_ok
            and bool(docs_drift)
        )
        if ok:
            print(
                "[OK] 自检通过：落后判红 / 达标放行 / 格式校验 / fixed 豁免 / 文档漂移判红 "
                "五类样本均符合预期"
            )
            return 1
        print(
            f"[FAIL] 自检未达预期：stale={stale} fresh={fresh} "
            f"malformed={malformed} only_fixed={only_fixed} "
            f"docs_ok={docs_ok} docs_drift={docs_drift}"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数。

    Returns:
        int: 退出码。
    """
    parser = argparse.ArgumentParser(description="批次时钟门禁（R2-QA-16）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检")
    parser.add_argument("--batch", default=None, help="显式指定当前批次（默认读 OA_BATCH）")
    parser.add_argument(
        "--check-docs",
        action="store_true",
        help="额外校验文档/脚本中的批次号与当前批次一致（CI 用；本地临跑不必带）",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    current = args.batch or os.environ.get("OA_BATCH", "")
    if not current.strip():
        print(
            "[FATAL] 未设置 OA_BATCH：批次时钟无法判定。"
            "与 expected_xfail.current_batch() 口径一致——缺失即红。",
            file=sys.stderr,
        )
        return 2

    try:
        ledger = load_ledger()
    except RuntimeError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    violations = find_violations(current, ledger)
    if args.check_docs:
        try:
            violations += check_docs(current)
        except RuntimeError as exc:
            print(f"[FATAL] {exc}", file=sys.stderr)
            return 2
    if violations:
        print(f"[FAIL] 批次时钟检查发现 {len(violations)} 项违规：")
        for item in violations:
            print(f"  - {item}")
        return 1

    open_count = sum(1 for e in ledger if str(e.get("status", "")).lower() != "fixed")
    print(
        f"[OK] 批次时钟正常：OA_BATCH={current}，台账 {len(ledger)} 条"
        f"（未修复 {open_count} 条），无到期未处理项"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
