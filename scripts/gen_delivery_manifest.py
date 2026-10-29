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
- **证据列**：自动填"引入该文件的短 SHA"；文件存在但尚无提交时填 `待补`（**不编造 SHA**）；
  若某行证据是**人工写的文字**（不是 7-40 位十六进制、也不是 `待补`），重跑时**原样保留**——
  这是唯一允许手改的列（交付总监/QA 可写"已核对""客户现场验证"等）；
- **备注列**由脚本按 `AUDIT_NOTES` 生成（D-28 的路径/大小核实、确属缺失项的归属），
  同样**不接受手改**：要改备注请改 `AUDIT_NOTES`；
- 表外内容（文件头说明、勾选口径）每次重写；**不要手工往表里加行**，加行请改任务表。

用法：
    python scripts/gen_delivery_manifest.py            # 生成/刷新
    python scripts/gen_delivery_manifest.py --check    # 只校验，不写盘（CI 可用）

退出码：0 正常；1 `--check` 下发现不一致：清单状态失真（文件已建但清单写"待建"、或反之）、
清单行与任务表不同步、已建项 `ls` 不到。
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
    "T4-02.1": "W1",
    "T4-02.2": "W1",
    "T4-02.3": "W2",
    "T4-02.4": "W2",
    "T4-02.5": "W2",
    "T4-02.6": "W2",
    "T4-03.1": "W2",
    "T4-03.2": "W2",
    "T4-03.3": "W3",
    "T4-03.4": "W3",
    "T4-03.5": "W3",
    "T4-03.6": "W3",
    "T4-03.7": "W3",
    "T4-04": "W3",
    "T4-05.1": "W3",
    "T4-05.2": "W3",
    "T4-05.3": "W4",
    "T4-05.4": "W4",
    "T4-05.5": "W4",
    "T4-05.6": "W4",
    "T4-05.7": "W4",
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

#: 交付审计备注（D-28 / LED-043）：键 = 仓库根相对路径，值 = (已建时的备注, 待建时的备注)。
#: 为什么写在脚本里而不是手改清单：备注描述的是"事实核对结果"，必须随状态自动切换——
#: 文件一旦补建，`确属缺失` 的备注要自动消失，否则又是新的失真。
#: 支持 `{size}` 占位符，按磁盘上当前字节数填充（大小永远现算，不会过期）。空串 = 该状态不写备注。
AUDIT_NOTES: dict[str, tuple[str, str]] = {
    # D-28：这三项曾被标"待建"，实际已建；备注附带路径/大小以留审计痕迹
    "deploy/install.sh": ("D-28 核实：已建（{size}）", ""),
    "deploy/backup.sh": ("D-28 核实：已建（{size}）", ""),
    "deploy/restore.sh": ("D-28 核实：已建（{size}）", ""),
    # D-28 同批核实的轻量版交付物（同属被误标"待建"的行）
    "deploy/.env.lite.example": ("D-28 核实：已建（{size}）", ""),
    "deploy/docker-compose.lite.yml": ("D-28 核实：已建（{size}）", ""),
    "deploy/nginx/lite.conf": ("D-28 核实：已建（{size}）", ""),
    "deploy/nginx/nginx.conf": ("D-28 核实：已建（{size}）", ""),
    "deploy/validate_lite_up.sh": ("D-28 核实：已建（{size}）", ""),
    "sql/enable-vector-extension.sql": ("D-28 核实：已建（{size}）", ""),
    # D-28：确属缺失，保留"待建"并写明归属
    "deploy/upgrade.sh": ("", "确属缺失：归属 W1（升级前置能力），任务表登记于 T4-05.5（W4）"),
    "deploy/rollback.sh": ("", "确属缺失：归属 W1（回滚前置能力），任务表登记于 T4-05.5（W4）"),
    # 待确认：目录下只有 P4-35 的轻量探针，不是任务表要求的 httpx+asyncio 压测脚本（§8.2）
    "scripts/bench/*.py": (
        "",
        "仅有 scripts/bench/lite-probe/probe.py（P4-35 轻量探针）；"
        "T4-05.7 要求的压测脚本未建（待确认）",
    ),
}

#: 「等价实现」登记：清单路径 → (仓库内实际实现路径, 为什么它是等价实现)。
#:
#: 为什么要有这个概念（这是机制，不是手工注解）：
#: 任务表是按"架构设计时的文件划分"写的，而施工时**有时刻意的实现方式更好**——
#: 例如延迟判权白名单，架构 §2.5 要求"全仓唯一位置"，于是它内联在 `core/security.py`
#: 而不是单独一个 `permission_whitelist.py`；五类攻击用例也合并成了 4 个文件覆盖。
#: 这类交付物的正确结论不是"缺失"，也不是"补一个空壳文件让它 ls 得到"（那是造假），
#: 而是"清单路径不存在，但等价实现存在且有据可查"。
#:
#: 三条硬约束（防止等价映射被滥用成掩盖缺失）：
#:   1. **等价路径必须真实存在**，否则该行判 `待建`，且 `--check` 直接红（见 `check()`）；
#:   2. 同一等价文件可被多行引用（本表 `test_attack_quota_and_leak.py` 就覆盖两条）；
#:   3. 理由必须写清"为什么等价"，不允许只写文件名。
EQUIVALENT_IMPLS: dict[str, tuple[str, str]] = {
    "apps/adapter/app/core/permission_whitelist.py": (
        "apps/adapter/app/core/security.py",
        "白名单以 `PERMISSION_WHITELIST`（:238）内联，架构 §2.5 要求「全仓唯一位置」，"
        "独立文件反而违背设计",
    ),
    "apps/adapter/tests/unit/test_permission_whitelist.py": (
        "apps/adapter/tests/security/test_permission_fail_closed.py",
        "7 用例，含 `test_lock_file_matches_code` / `test_exactly_two_entries` 即白名单锁断言",
    ),
    "apps/adapter/tests/security/test_authn_bypass.py": (
        "apps/adapter/tests/security/test_attack_token.py",
        "攻击 1&2（伪造 / 过期令牌）4 用例",
    ),
    "apps/adapter/tests/unit/test_cross_tenant.py": (
        "apps/adapter/tests/security/test_attack_cross_tenant.py",
        "攻击 3（跨租户读）",
    ),
    "apps/adapter/tests/unit/test_info_leak.py": (
        "apps/adapter/tests/security/test_attack_quota_and_leak.py",
        "攻击 5 的信息外泄部分",
    ),
    "apps/adapter/tests/unit/test_object_privilege.py": (
        "apps/adapter/tests/security/test_attack_object_scope.py",
        "攻击 4（越权对象）5 用例",
    ),
    "apps/adapter/tests/unit/test_quota_abuse.py": (
        "apps/adapter/tests/security/test_attack_quota_and_leak.py",
        "攻击 5 的额度滥用部分",
    ),
    "apps/adapter/tests/unit/test_metrics_labels.py": (
        "apps/adapter/tests/unit/test_metrics.py",
        "含 `test_rejects_unknown_labels`（标签白名单断言）+ `test_metrics_contract.py`",
    ),
}

#: 状态取值：清单路径不存在、但登记的等价实现存在
STATUS_EQUIVALENT: str = "已建（等价）"

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
    note: str = "—"


def sh(*args: str) -> str:
    """跑命令拿 stdout。"""
    # args 全为本文件内的字面量（git 子命令，见 sh() 的调用点），无外部输入
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout  # noqa: S603


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
            optional = "（若需）" in files_col[m2.end() : m2.end() + 6]
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


def human_size(path: str) -> str:
    """文件大小的可读写法（逐字节现算；目录/glob 返回"大小未知"）。"""
    try:
        n = (ROOT / path).stat().st_size
    except OSError:
        return "大小未知"
    return f"{n:,} B / {n / 1024:.1f} KiB"


def equivalent_of(path: str) -> tuple[str, str] | None:
    """清单路径未建时，登记的等价实现是否可用。

    Args:
        path: 清单里的交付物路径。

    Returns:
        tuple[str, str] | None: (等价实现路径, 理由)；没有登记、或等价路径本身
        也不存在（相当于没建）时返回 None——**不能在等价路径缺席时"认账"**，
        否则这张映射表就成了掩盖缺失的工具。
    """
    entry = EQUIVALENT_IMPLS.get(path)
    if entry is None:
        return None
    real, reason = entry
    if status_of(real) != "已建":
        return None
    return real, reason


def status_with_equivalent(path: str) -> str:
    """最终状态：已建 / 已建（等价）/ 待建。"""
    if status_of(path) == "已建":
        return "已建"
    if equivalent_of(path) is not None:
        return STATUS_EQUIVALENT
    return "待建"


def note_of(path: str, status: str) -> str:
    """备注列：按状态取值——等价实现 > `AUDIT_NOTES` > "—"。"""
    if status == STATUS_EQUIVALENT:
        entry = EQUIVALENT_IMPLS.get(path)
        if entry is not None:
            real, reason = entry
            return f"等价实现：`{real}` —— {reason}"
        return "—"
    built_note, missing_note = AUDIT_NOTES.get(path, ("", ""))
    if status == "已建":
        template = built_note
    elif status == "待建":
        template = missing_note
    else:
        template = ""
    if not template:
        return "—"
    return template.replace("{size}", human_size(path))


def existing_evidence() -> dict[tuple[str, str], str]:
    """读旧清单的"人工证据"（非 SHA、也非自动占位符 `待补` 才保留）。"""
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
        is_placeholder = evidence in {"—", "-", "待补"}
        is_sha = bool(SHA_RE.match(evidence.replace("`", "")))
        if evidence and not is_placeholder and not is_sha:
            kept[(task.strip("` "), path.strip("` "))] = evidence
    return kept


def build() -> tuple[str, list[Row]]:
    """生成清单文本。"""
    rows: list[Row] = []
    kept = existing_evidence()
    for task, paths in parse_tasks():
        for path, optional in paths:
            status = (
                "不适用"
                if (optional and not (ROOT / path).exists())
                else status_with_equivalent(path)
            )
            auto = first_commit(path) if status == "已建" else ""
            if status == "不适用":
                auto = "可选交付物（任务表标「若需」），未建不影响验收"
            elif status == STATUS_EQUIVALENT:
                # 等价实现：证据列回填等价文件自己的短 SHA，交付总监点开就能核
                equivalent = equivalent_of(path)
                auto = first_commit(equivalent[0]) if equivalent else ""
                if not auto:
                    auto = "待补"
            elif status == "已建" and not auto:
                # 文件在盘上但尚未入库：不编造 SHA，留给交付总监在合并后重跑刷新
                auto = "待补"
            evidence = kept.get((task, path)) or (auto or "—")
            rows.append(
                Row(
                    task=task,
                    path=path,
                    kind=kind_of(path),
                    status=status,
                    evidence=evidence,
                    note=note_of(path, status),
                )
            )
    rows.sort(key=lambda r: (r.task, r.path))

    n_eq = sum(1 for r in rows if r.status == STATUS_EQUIVALENT)
    n_built = sum(1 for r in rows if r.status in {"已建", STATUS_EQUIVALENT})
    n_na = sum(1 for r in rows if r.status == "不适用")
    n_todo = len(rows) - n_built - n_na
    by_task: dict[str, list[Row]] = {}
    for r in rows:
        by_task.setdefault(r.task, []).append(r)

    head = (
        f"""# 阶段四交付物清单（P4 delivery manifest）

| 项 | 内容 |
| --- | --- |
| 维护人 | **严过关（QA）** —— 只管"状态"是否与实际一致 |
| 复核人 | **齐活林（交付总监）** —— 验收时逐行核对 |
| 生成方式 | `python scripts/gen_delivery_manifest.py`（**勿手写**；`--check` 可只校验不写盘） |
| 真源 | §11 任务表（清单与归属）+ §11.1 周次映射 + `git ls-files` + `EQUIVALENT_IMPLS` |
| 生成时间 | 每次重跑自动刷新；本文件不记录时间戳（避免"看起来新、其实旧"） |

## 核对口径（验收时照此执行）

1. **`已建` 项必须 `ls` 得到**：`python scripts/gen_delivery_manifest.py --check` 退出 0 即通过；
   该命令还校验清单是否失真（已建却写"待建"、或反之）——9 个已建交付物曾被标"待建"
   （D-28 / LED-043），故为硬门禁。
2. **`待建` 项在阶段四验收时必须为 0**；若不为 0，要么补建，要么在 §13 明确"不进 V1.0"。
   `已建（等价）` 不计入待建：它表示"清单路径不存在，但登记的等价实现真实存在"（见下条）。
3. **`已建（等价）` 是脚本判定的结果，不是手工标注**：判定顺序 = 清单路径存在 → `已建`；
   否则 `EQUIVALENT_IMPLS` 登记的等价路径存在 → `已建（等价）`；两者都无 → `待建`。
   **等价路径本身不存在时该行判 `待建` 且 `--check` 红**——这条映射不能用来把缺失写成熟。
4. **`证据` 列是唯一允许手改的列**；重跑时人工文字原样保留、自动短 SHA 刷新，未入库的写 `待补`
   （可写"已核对 / 客户现场验证 / 对应 PR"）。等价实现行的自动证据 = 等价文件自己的短 SHA。
5. **`备注` 列由脚本生成**（`AUDIT_NOTES` 的路径/大小核实、`EQUIVALENT_IMPLS` 的等价实现与理由），
   不接受手改。
6. **不要手工往表里加行**——交付物归属变了，请改 `architecture-phase4.md` 的任务表再重跑。
7. `包内产物` 类（离线包内的 `manifest.json` / SBOM / 金标集）**不入仓库**，其"待建"指"随发布产出"。
8. **`状态` 只回答"这个文件 ls 得到吗"，不回答"这个任务做完了没"**。对"改既有文件"类任务（如 T4-01.1
   收敛整改、T4-03.7 扩词），`已建` 不代表改动已完成——完成度以任务表验收命令为准，
   本清单不得宣称进度。

## 汇总

- 合计 **{len(rows)}** 行：**已建 {n_built}**（其中等价实现 {n_eq}）/ **待建 {n_todo}** /
  **不适用 {n_na}**
- 按批次："""
        + " / ".join(
            f"{w} {sum(1 for r in rows if week_of(r.task) == w)}" for w in ("W1", "W2", "W3", "W4")
        )
        + """

## 清单

| ID | 交付物路径 | 所属任务 | 批次 | 类型 | 状态 | 验收证据 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""
    )
    body = []
    for task in sorted(by_task, key=lambda t: (t,)):
        for r in by_task[task]:
            body.append(
                f"| `{r.task}` | `{r.path}` | {r.task} | {week_of(r.task)} | {r.kind} "
                f"| {r.status} | {r.evidence} | {r.note} |"
            )
    return head + "\n".join(body) + "\n", rows


def disk_statuses() -> dict[tuple[str, str], str]:
    """读当前清单里写的状态 → 用于发现"清单停在旧快照"（D-28 的根因）。"""
    stored: dict[tuple[str, str], str] = {}
    if not OUT.is_file():
        return stored
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `T4-"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 6:
            continue
        stored[(cols[0].strip("` "), cols[1].strip("` "))] = cols[5]
    return stored


def check(text: str, rows: list[Row]) -> int:
    """校验"清单 ↔ 磁盘"一致；返回退出码（0 通过 / 1 不一致）。

    三类不一致都要报：
    1. 清单写 `已建` 但 `ls` 不到（假交付）；
    2. 清单写 `待建` 但文件已在（**LED-043 的失真形态**）；
    3. 清单整体与重新生成的结果不同（行增删、表头/汇总/备注过期）。
    """
    problems: list[str] = []
    computed = {(r.task, r.path): r.status for r in rows}

    for r in rows:
        actual = status_of(r.path)
        if r.status == "已建" and actual != "已建":
            problems.append(f"已建但 ls 不到：{r.task} {r.path}")
        elif r.status == "待建" and actual != "待建":
            problems.append(f"待建但文件已存在：{r.task} {r.path}")

    # 等价实现的反向校验：登记的等价路径必须真实存在。
    # 没有这一条，`EQUIVALENT_IMPLS` 就能把"两个文件都没建"写成"已建（等价）"，
    # 门禁从此失去意义——这是本机制唯一的滥用口子，必须堵死。
    for listed, (real, reason) in sorted(EQUIVALENT_IMPLS.items()):
        if status_of(listed) == "已建":
            continue
        if status_of(real) != "已建":
            problems.append(
                f"等价实现也不存在：{listed} → {real}（{reason}）；"
                "等价映射不得用于掩盖缺失，请补建或删除该条映射"
            )

    stored = disk_statuses()
    if not stored:
        problems.append(f"清单不存在或为空：{OUT.relative_to(ROOT)}，请先跑一次生成")
    else:
        for key, stale in sorted(stored.items()):
            fresh = computed.get(key)
            if fresh is None:
                problems.append(f"清单多出任务表没有的行：{key[0]} {key[1]}")
            elif stale != fresh:
                problems.append(f"清单状态失真：{key[0]} {key[1]} 写「{stale}」实为「{fresh}」")
        for key in sorted(set(computed) - set(stored)):
            problems.append(f"清单缺行（任务表有、清单没有）：{key[0]} {key[1]}")

    if OUT.is_file() and OUT.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
        problems.append("清单内容与生成结果不一致（表头/汇总/备注/证据列已过期），请重跑生成器")

    if problems:
        for p in problems:
            print(f"[ERR] {p}", file=sys.stderr)
        print(
            f"[ERR] 共 {len(problems)} 处不一致，请重跑 `python scripts/gen_delivery_manifest.py`",
            file=sys.stderr,
        )
        return 1
    n_built = sum(1 for r in rows if r.status in {"已建", STATUS_EQUIVALENT})
    n_eq = sum(1 for r in rows if r.status == STATUS_EQUIVALENT)
    n_todo = sum(1 for r in rows if r.status == "待建")
    print(f"[OK] {len(rows)} 行；已建 {n_built}（其中等价 {n_eq}）；待建 {n_todo}；清单与磁盘一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写盘")
    args = ap.parse_args(argv)
    if not ARCH.is_file():
        print(f"[ERR] 缺任务表：{ARCH}", file=sys.stderr)
        return 2
    text, rows = build()
    if args.check:
        return check(text, rows)
    OUT.write_text(text, encoding="utf-8")
    n_built = sum(1 for r in rows if r.status in {"已建", STATUS_EQUIVALENT})
    n_eq = sum(1 for r in rows if r.status == STATUS_EQUIVALENT)
    n_todo = sum(1 for r in rows if r.status == "待建")
    print(
        f"[OK] 写出 {OUT.relative_to(ROOT)}：{len(rows)} 行 / "
        f"已建 {n_built}（其中等价 {n_eq}）/ 待建 {n_todo}"
    )
    if unresolved:
        print(
            f"[WARN] {len(unresolved)} 个 token 无法归一到仓库路径"
            "（未计入清单，请补全路径或登记 PREFIX_HINTS）："
        )
        for t in sorted(unresolved):
            print(f"       - {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
