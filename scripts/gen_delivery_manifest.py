#!/usr/bin/env python
"""阶段四交付清单生成器（`docs/phase4-delivery-manifest.md`）。

为什么要有这个脚本
------------------
"交付物清单"天生会过期：任务表在 `docs/architecture-phase4.md`，而文件在不断被建出来。
手写一份静态清单，第一天是对的，第二天就开始骗人——阶段三为这类问题付过学费。
所以清单**由脚本从单一真源生成**，允许随时重跑刷新"状态"列：

    真源 1：`docs/architecture-phase4.md` §11 的 T4 任务表（交付物清单与归属任务）
    真源 2：`git ls-files --cached --others --exclude-standard`（文件是否已建 + 引入它的提交）
    真源 3：`docs/architecture-phase4.md` §11.1 的周次映射（脚本内 `WEEK_MAP`，改动需同步）

重跑规则（重要）
----------------
- **状态列永远由脚本算**（文件存在=已建，否则=待建），不接受手工填写；
- **证据列**：自动填"引入该文件的短 SHA"；若某行证据是**人工写的文字**（不是 7-40 位十六进制），
  重跑时**原样保留**——这是唯一允许手改的列（交付总监/QA 可写"已核对""客户现场验证"等）；
- 表外内容（文件头说明、勾选口径）每次重写；**不要手工往表里加行**，加行请改任务表。

用法：
    python scripts/gen_delivery_manifest.py            # 生成/刷新
    python scripts/gen_delivery_manifest.py --check    # 只校验，不写盘（CI 可用）

退出码：0 正常；1 `--check` 下发现"已建项 ls 不到"或"待建项已存在"的不一致。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "architecture-phase4.md"
OUT = ROOT / "docs" / "phase4-delivery-manifest.md"

#: 子任务 → 周次（唯一真源 = `architecture-phase4.md` §11.1 的映射表；改 §11.1 必须同步这里）
WEEK_MAP: dict[str, str] = {
    "T4-01": "W1",
    "T4-02.1": "W1", "T4-02.2": "W1",
    "T4-02.3": "W2", "T4-02.4": "W2", "T4-02.5": "W2", "T4-02.6": "W2",
    "T4-03.1": "W2", "T4-03.2": "W2",
    "T4-03.3": "W3", "T4-03.4": "W3", "T4-03.5": "W3", "T4-03.6": "W3", "T4-03.7": "W3",
    "T4-04": "W3",
    "T4-05.1": "W3", "T4-05.2": "W3",
    "T4-05.3": "W4", "T4-05.4": "W4", "T4-05.5": "W4", "T4-05.6": "W4", "T4-05.7": "W4",
}

#: 这些路径是"构建/发布时才产出"的包内产物，不进仓库
PACKAGE_ARTIFACTS = {"manifest.json", "sbom.cdx.json", "T01..T20.yaml", "scoring.md"}

#: 类型判定顺序：先看目录/文件名特征，再看扩展名
MIGRATION_RE = re.compile(r"^deploy/migrations/")
TEST_RE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]+\.py$|(^|/)conftest\.py$")
DOC_RE = re.compile(r"\.(md)$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

#: 任务表里以裸名/简写出现、且当前**尚未创建**的交付物 → 归一成仓库根相对路径。
#: 为什么必须显式列出：未创建的文件无法靠 basename 反查，靠猜会把文件写到错目录。
#: 新增交付物时，要么在任务表里写全路径（推荐），要么在这里登记一条。
PREFIX_HINTS: dict[str, str] = {
    "install.sh": "deploy/install.sh",
    "upgrade.sh": "deploy/upgrade.sh",
    "rollback.sh": "deploy/rollback.sh",
    "backup.sh": "deploy/backup.sh",
    "restore.sh": "deploy/restore.sh",
    "validate_lite_up.sh": "deploy/validate_lite_up.sh",
    "alert.rules.yml": "deploy/alert.rules.yml",
    "docker-compose.lite.yml": "deploy/docker-compose.lite.yml",
    ".env.lite.example": "deploy/.env.lite.example",
    "nginx/nginx.conf": "deploy/nginx/nginx.conf",
    "nginx/lite.conf": "deploy/nginx/lite.conf",
    "sbom/generate.py": "deploy/sbom/generate.py",
    "baseline-phase4-10.json": "docs/api/baseline-phase4-10.json",
    "openapi.snapshot.json": "docs/api/openapi.snapshot.json",
    "permission-whitelist.lock": "docs/security/permission-whitelist.lock",
    "perf-disclosure.md": "docs/perf-disclosure.md",
    "deployment-guide.md": "docs/deployment-guide.md",
    "user-guide.md": "docs/user-guide.md",
    "key-rotation-checklist.md": "docs/key-rotation-checklist.md",
    "privacy-policy.md": "docs/compliance/privacy-policy.md",
    "dpa-template.md": "docs/compliance/dpa-template.md",
    "security-whitepaper.md": "docs/compliance/security-whitepaper.md",
}

ROW_RE = re.compile(r"^\|\s*\*\*(T4-\d\d(?:\.\d+)?)\*\*\s*\|")

#: 归一失败的 token（脚本会打印出来，避免交付物被静默丢掉）
unresolved: set[str] = set()


@dataclass
class Row:
    """清单一行。"""

    task: str
    path: str
    kind: str
    status: str
    evidence: str


def sh(*args: str) -> str:
    """跑命令拿 stdout。"""
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout


def tracked_files() -> list[str]:
    """仓库内全部文件（含未跟踪但未被 ignore 的）。"""
    out = sh("git", "ls-files", "--cached", "--others", "--exclude-standard")
    return [line for line in out.splitlines() if line.strip()]


def week_of(task: str) -> str:
    """子任务所属周次（批次级任务退化为前缀匹配）。"""
    if task in WEEK_MAP:
        return WEEK_MAP[task]
    prefix = task.rsplit(".", 1)[0]
    return WEEK_MAP.get(prefix, WEEK_MAP.get(task[:5], "W?"))


def kind_of(path: str) -> str:
    """交付物类型。"""
    name = path.rsplit("/", 1)[-1]
    if name in PACKAGE_ARTIFACTS:
        return "包内产物"
    if MIGRATION_RE.match(path):
        return "迁移"
    if TEST_RE.search(path):
        return "测试"
    if DOC_RE.search(path):
        return "文档"
    return "代码"


def resolve(token: str, all_files: list[str]) -> str | None:
    """把任务表里的写法归一成仓库根相对路径。

    任务表里同一文件有全路径、简写（`errors.py`）、带 glob（`rollback/0006_*.down.sql`）
    三种写法，这里统一：能按原样命中就用原样，否则按 basename 唯一命中回填真实路径。
    """
    t = token.strip().strip("`").strip()
    if not t or " " in t:
        return None
    if t.startswith(("http", "/api", "/v1", "npm ", "pytest ", "python ", "docker ")):
        return None
    if "/" not in t and "." not in t:
        return None
    if (ROOT / t).exists() or (ROOT / t).is_dir():
        return t
    # 已写成仓库根相对路径（只是尚未创建）：直接采信，不做 basename 反查
    if t.startswith(("apps/", "deploy/", "docs/", "scripts/", "qa/", "sql/", ".github/", "tests/")):
        return t
    # 离线包内产物：不入仓库，保留裸名以便交付总监对照包结构
    if t in PACKAGE_ARTIFACTS:
        return t
    base = t.rsplit("/", 1)[-1].lstrip("*")
    if not base:
        return None
    hits = [f for f in all_files if f.rsplit("/", 1)[-1] == base]
    if len(hits) == 1:
        return hits[0]
    # --- 以下处理"尚未创建"的交付物：只能按约定归一，不能靠 basename 反查 ---
    if t in PREFIX_HINTS:
        return PREFIX_HINTS[t]
    if t.startswith("app/"):
        return "apps/adapter/" + t
    if re.match(r"^0\d{3}_[^/]+\.sql$", t):
        return "deploy/migrations/" + t
    if t.startswith("rollback/"):
        return "deploy/migrations/" + t
    if re.match(r"^test_[^/]+\.py$", t) or base == "conftest.py":
        for sub in ("unit", "integration", "e2e", "security"):
            if (ROOT / "apps/adapter/tests" / sub).is_dir():
                return f"apps/adapter/tests/{sub}/{t}"
    unresolved.add(t)
    return None


def parse_tasks() -> list[tuple[str, list[tuple[str, bool]]]]:
    """解析 §11 任务表 → [(子任务 ID, 交付物路径列表)]。"""
    text = ARCH.read_text(encoding="utf-8")
    all_files = tracked_files()
    result: list[tuple[str, list[tuple[str, bool]]]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        task = m.group(1)
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 3:
            continue
        files_col = cols[2]
        paths: list[str] = []
        for m2 in re.finditer(r"`([^`]+)`", files_col):
            token = m2.group(1)
            optional = "（若需）" in files_col[m2.end():m2.end() + 6]
            p = resolve(token, all_files)
            if p and p not in seen:
                seen.add(p)
                paths.append((p, optional))
        if paths:
            result.append((task, paths))
    return result


def first_commit(path: str) -> str:
    """引入该文件的短 SHA（未跟踪文件返回空）。"""
    out = sh("git", "log", "--diff-filter=A", "--format=%h", "-1", "--", path).strip()
    return out.splitlines()[0] if out else ""


def status_of(path: str) -> str:
    """已建 / 待建。glob 与目录按"存在且有内容"判定。"""
    p = ROOT / path
    if "*" in path:
        return "已建" if any(ROOT.glob(path)) else "待建"
    if path.endswith("/"):
        return "已建" if p.is_dir() and any(p.iterdir()) else "待建"
    if p.is_dir():
        return "已建" if any(p.iterdir()) else "待建"
    return "已建" if p.exists() else "待建"


def existing_evidence() -> dict[tuple[str, str], str]:
    """读旧清单的"人工证据"（非 SHA 才保留）。"""
    kept: dict[tuple[str, str], str] = {}
    if not OUT.is_file():
        return kept
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `T4-") and not line.startswith("| T4-"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 7:
            continue
        task, path, evidence = cols[0], cols[1], cols[6]
        if evidence and evidence not in {"—", "-"} and not SHA_RE.match(evidence.replace("`", "")):
            kept[(task.strip("` "), path.strip("` "))] = evidence
    return kept


def build() -> tuple[str, list[Row]]:
    """生成清单文本。"""
    rows: list[Row] = []
    kept = existing_evidence()
    for task, paths in parse_tasks():
        for path, optional in paths:
            status = "不适用" if (optional and not (ROOT / path).exists()) else status_of(path)
            auto = first_commit(path) if status == "已建" else ""
            if status == "不适用":
                auto = "可选交付物（任务表标「若需」），未建不影响验收"
            evidence = kept.get((task, path)) or (auto or "—")
            rows.append(Row(task=task, path=path, kind=kind_of(path), status=status, evidence=evidence))
    rows.sort(key=lambda r: (r.task, r.path))

    n_built = sum(1 for r in rows if r.status == "已建")
    n_na = sum(1 for r in rows if r.status == "不适用")
    n_todo = len(rows) - n_built - n_na
    by_task: dict[str, list[Row]] = {}
    for r in rows:
        by_task.setdefault(r.task, []).append(r)

    head = f"""# 阶段四交付物清单（P4 delivery manifest）

| 项 | 内容 |
| --- | --- |
| 维护人 | **严过关（QA）** —— 只管"状态"是否与实际一致 |
| 复核人 | **齐活林（交付总监）** —— 验收时逐行核对 |
| 生成方式 | `python scripts/gen_delivery_manifest.py`（**勿手写**；`--check` 可只校验不写盘） |
| 真源 | `docs/architecture-phase4.md` §11 任务表（清单与归属）+ §11.1 周次映射 + `git ls-files`（是否已建） |
| 生成时间 | 每次重跑自动刷新；本文件不记录时间戳（避免"看起来新、其实旧"） |

## 核对口径（验收时照此执行）

1. **`已建` 项必须 `ls` 得到**：`python scripts/gen_delivery_manifest.py --check` 退出 0 即通过。
2. **`待建` 项在阶段四验收时必须为 0**；若不为 0，要么补建，要么在 §13 明确"不进 V1.0"。
3. **`证据` 列是唯一允许手改的列**（可写"已核对 / 客户现场验证 / 对应 PR"）。
   脚本重跑时：人工写的文字**原样保留**，自动生成的短 SHA 会刷新。
4. **不要手工往表里加行**——交付物归属变了，请改 `architecture-phase4.md` 的任务表再重跑。
5. `包内产物` 类（离线包内的 `manifest.json` / SBOM / 金标集）**不入仓库**，其"待建"指"随发布产出"。
6. **`状态` 只回答"这个文件 ls 得到吗"，不回答"这个任务做完了没"**。对"改既有文件"类任务
   （如 T4-01.1 收敛整改、T4-03.7 扩词），文件本来就在，`已建` 不代表改动已完成——
   任务完成度**以任务表的验收命令为准**，本清单不得用于宣称阶段进度。

## 汇总

- 合计 **{len(rows)}** 行：**已建 {n_built}** / **待建 {n_todo}** / **不适用 {n_na}**
- 按批次：""" + " / ".join(
        f"{w} {sum(1 for r in rows if week_of(r.task) == w)}"
        for w in ("W1", "W2", "W3", "W4")
    ) + """

## 清单

| ID | 交付物路径 | 所属任务 | 批次 | 类型 | 状态 | 验收证据 |
| --- | --- | --- | --- | --- | --- | --- |
"""
    body = []
    for task in sorted(by_task, key=lambda t: (t, )):
        for r in by_task[task]:
            body.append(
                f"| `{r.task}` | `{r.path}` | {r.task} | {week_of(r.task)} | {r.kind} | {r.status} | {r.evidence} |"
            )
    return head + "\n".join(body) + "\n", rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写盘")
    args = ap.parse_args(argv)
    if not ARCH.is_file():
        print(f"[ERR] 缺任务表：{ARCH}", file=sys.stderr)
        return 2
    text, rows = build()
    if args.check:
        bad = [r for r in rows if r.status == "已建" and status_of(r.path) != "已建"]
        if bad:
            for r in bad:
                print(f"[ERR] 已建但 ls 不到：{r.task} {r.path}", file=sys.stderr)
            return 1
        print(f"[OK] {len(rows)} 行；已建 {sum(1 for r in rows if r.status == '已建')}")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"[OK] 写出 {OUT.relative_to(ROOT)}：{len(rows)} 行 / 已建 {sum(1 for r in rows if r.status == '已建')}")
    if unresolved:
        print(f"[WARN] {len(unresolved)} 个 token 无法归一到仓库路径（未计入清单，请补全路径或登记 PREFIX_HINTS）：")
        for t in sorted(unresolved):
            print(f"       - {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
