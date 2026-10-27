#!/usr/bin/env python3
"""G-26 插件声明门禁（阶段三 B16，`architecture-phase3.md` §10）。

六条判据（任一不通过 → 退出码 1）：

1. 6 份声明通过 6 段 schema（`extra="forbid"`）与结构自洽（序号连续、产物/依赖闭合）；
2. `capabilities[].key` ⊆ 6 项能力枚举；
3. `steps[].action` ⊆ 6 项原语白名单，且 `tool_type/operation` 存在于
   `app/registry/tool_registry.yaml`（含 `tool_alternatives`）；
4. 文案字段过禁词 + 占位词表（PRD §8 第 19-30 行）；
5. 声明内不得出现禁用键名（凭据 / 代码 / 外部地址）；
6. 重复 `plugin_id` 拒绝，`manifest_sha256` 可计算并与磁盘内容一致。

**为什么需要 `--self-test`**（阶段二教训，§10「如何证明门禁不是永远绿」）：
一个只会打印 `[OK]` 的门禁等于没有门禁。`--self-test` 会注入 6 条内联负样本，
**每一条都必须被抓到**，且进程退出码为 **1**（表示"自检成功发现了问题"）。
若某条负样本漏过，本脚本退出码为 2（自检失败），提示门禁本身失灵。

**反例护栏**：找不到声明目录一律退出码 **2**，绝不"找不到文件就退出 0"。

用法::

    python deploy/validate_plugins.py                 # 正常校验（期望 0）
    python deploy/validate_plugins.py --self-test     # 负样本自检（期望 1）
    python deploy/validate_plugins.py --json          # 机器可读输出
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: 适配层根目录（脚本相对树推导：`<repo>/deploy` → `<repo>/apps/adapter`）
ADAPTER_ROOT = REPO_ROOT / "apps" / "adapter"
if str(ADAPTER_ROOT) not in sys.path:  # 允许直接以脚本方式运行
    sys.path.insert(0, str(ADAPTER_ROOT))

from app.plugins.actions import ACTION_WHITELIST, CAPABILITY_KEYS  # noqa: E402
from app.plugins.loader import (  # noqa: E402
    PluginRegistry,
    default_manifests_dir,
    manifest_digest,
)
from app.plugins.schema import BANNED_TERMS, find_banned_terms  # noqa: E402

#: 脚本相对树的声明目录（**不用 `default_manifests_dir()` 兜底**）
#:
#: 2026-10-23 QA 实测的假隔离：在 `/tmp` 的导出目录里跑本脚本，日志里赫然是
#: `.../Desktop/麦子/apps/adapter/app/plugins/manifests`——原因是缺省目录取自
#: **导入到的 `app` 包**，而导出树里若装了同名包，`import app.plugins.loader`
#: 会解析到主工作区那份，于是"验的是导出树、扫的是主仓"。
#: 因此缺省目录一律由 `Path(__file__)` 直接推导（脚本在哪个树里，就扫哪个树），
#: 并在输出里打印**绝对路径**供复核人一眼看出扫的是谁。
SCRIPT_MANIFESTS_DIR: Path = ADAPTER_ROOT / "app" / "plugins" / "manifests"

#: 期望的插件数量（V1.0 市场内置 6 个：3 标杆 + 3 轻量）
EXPECTED_PLUGIN_COUNT = 6

#: 负样本：`(名称, 变异函数)`，每条都必须被拒绝
SELF_TEST_CASES: tuple[str, ...] = (
    "多出可执行字段",
    "能力不在枚举内",
    "文案含内部用语",
    "步骤序号不连续",
    "重复 plugin_id",
    "工具操作未登记",
)


def _is_within(child: Path, parent: Path) -> bool:
    """判断目录是否位于给定归档根之内（含相等）。

    Args:
        child: 被检查的目录（已 `resolve()`）。
        parent: 归档根（已 `resolve()`）。

    Returns:
        bool: 位于其内（或相等）返回 True。
    """
    return child == parent or parent in child.parents


def assert_scan_scope(scanned: Path, archive_root: Path) -> list[str]:
    """断言「扫描目录 ∈ 归档根」，并证明**确实发生了遍历**。

    这条断言针对的是「假隔离」：门禁全绿，但扫的是另一棵树。
    只断言「路径相等」不够——路径对但 0 个文件（例如目录里只有子目录）同样会绿，
    所以还要断言命中文件数 > 0（架构 §10.1 门禁自证原则 ①）。

    Args:
        scanned: 实际扫描目录。
        archive_root: 本次运行认可的归档根（显式 `--dir` 时即其本身，否则为脚本树）。

    Returns:
        list[str]: 问题清单（空表示通过）。
    """
    resolved = scanned.resolve()
    problems: list[str] = []
    if not _is_within(resolved, archive_root.resolve()):
        problems.append(
            f"扫描目录不在归档根内：{resolved} ∉ {archive_root.resolve()}"
            "（导出树复核必须显式传 --dir，或确认脚本与声明在同一棵树里）"
        )
    hits = len(list(resolved.glob("*.json"))) if resolved.is_dir() else 0
    if hits <= 0:
        problems.append(f"扫描目录内没有任何声明文件（0 命中）：{resolved}")
    return problems


def _mutate_extra_field(payload: dict) -> dict:
    """负样本 1：声明里塞 `entry_point`（可执行载荷，PRD B-2 红线）。

    Args:
        payload: 原始声明。

    Returns:
        dict: 变异后的声明。
    """
    mutated = json.loads(json.dumps(payload, ensure_ascii=False))
    mutated["entry_point"] = "main.py"
    return mutated


def _mutate_capability(payload: dict) -> dict:
    """负样本 2：能力不在 6 项枚举内。

    Args:
        payload: 原始声明。

    Returns:
        dict: 变异后的声明。
    """
    mutated = json.loads(json.dumps(payload, ensure_ascii=False))
    mutated["capabilities"][0]["key"] = "model.invoke"
    return mutated


def _mutate_banned_term(payload: dict) -> dict:
    """负样本 3：文案含内部用语（这里用「RAG」）。

    Args:
        payload: 原始声明。

    Returns:
        dict: 变异后的声明。
    """
    mutated = json.loads(json.dumps(payload, ensure_ascii=False))
    mutated["summary"] = "RAG 助手"
    return mutated


def _mutate_seq(payload: dict) -> dict:
    """负样本 4：步骤序号不连续（1,3,4）。

    Args:
        payload: 原始声明。

    Returns:
        dict: 变异后的声明。
    """
    mutated = json.loads(json.dumps(payload, ensure_ascii=False))
    mutated["steps"][1]["seq"] = 3
    return mutated


def _mutate_tool_operation(payload: dict) -> dict:
    """负样本 6：工具操作未在 `tool_registry.yaml` 登记。

    Args:
        payload: 原始声明。

    Returns:
        dict: 变异后的声明。
    """
    mutated = json.loads(json.dumps(payload, ensure_ascii=False))
    for step in mutated["steps"]:
        if step.get("action") == "tool":
            step["operation"] = "delete_repo"
            break
    return mutated


def _load_payloads(directory: Path) -> list[tuple[str, dict]]:
    """读取目录下全部声明原文。

    Args:
        directory: 声明目录。

    Returns:
        list[tuple[str, dict]]: `(文件名, 载荷)` 列表。
    """
    payloads: list[tuple[str, dict]] = []
    for path in sorted(directory.glob("*.json")):
        payloads.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
    return payloads


def run_validation(directory: Path) -> tuple[int, list[str], dict[str, object]]:
    """执行六条判据。

    Args:
        directory: 声明目录。

    Returns:
        tuple[int, list[str], dict[str, object]]: 退出码、问题清单、统计信息。
    """
    if not directory.is_dir():
        return 2, [f"声明目录不存在：{directory}"], {}
    payloads = _load_payloads(directory)
    if not payloads:
        return 2, [f"声明目录为空：{directory}"], {}

    problems: list[str] = []
    registry = PluginRegistry()
    failures = registry.load_all(directory)
    problems.extend(item.render() for item in failures)

    if len(registry) != EXPECTED_PLUGIN_COUNT:
        problems.append(
            f"插件数量应为 {EXPECTED_PLUGIN_COUNT}，实际 {len(registry)}"
            f"（加载失败 {len(failures)}）"
        )

    # 第三段：文案禁词（`loader` 已扫过，这里再独立扫一遍原文，
    # 防"加载器被改松"时门禁一起失守——双重校验是刻意的）
    for name, payload in payloads:
        for where, text in _iter_text(payload):
            hits = find_banned_terms(text)
            if hits:
                problems.append(f"{name} [{where}] 文案含内部用语：{', '.join(hits)}")

    # 能力与原语白名单（同样独立复核）
    for name, payload in payloads:
        for claim in payload.get("capabilities", []):
            key = str(claim.get("key", ""))
            if key not in CAPABILITY_KEYS:
                problems.append(f"{name} [capabilities] 未开放的能力：{key}")
        for step in payload.get("steps", []):
            action = str(step.get("action", ""))
            if action not in ACTION_WHITELIST:
                problems.append(f"{name} [steps] 未开放的能力：{action}")

    # 摘要一致性：sha256 可计算且与声明内容一致（写库前的一次自检）
    digests = {name: manifest_digest(payload) for name, payload in payloads}
    for name, digest in digests.items():
        if len(digest) != 64:
            problems.append(f"{name} [sha256] 摘要长度异常：{len(digest)}")

    stats: dict[str, object] = {
        "directory": str(directory),
        # 绝对路径单独给一个键：复核人 `grep '"manifests_dir"'` 就能确认扫的是哪棵树，
        # 不必再从 `"directory"` 的相对/绝对形态里猜（QA 检查单要求的"归档身份"）。
        "manifests_dir": str(directory.resolve()),
        "archive_root": str(ADAPTER_ROOT),
        "declarations": len(payloads),
        "registered": len(registry),
        "plugin_ids": registry.plugin_ids(),
        "digests": digests,
    }
    return (1 if problems else 0), problems, stats


def _iter_text(payload: dict) -> list[tuple[str, str]]:
    """收集声明里全部用户可见文案（与 `schema.PluginManifest.text_fields` 同口径）。

    Args:
        payload: 声明原文。

    Returns:
        list[tuple[str, str]]: `(字段路径, 文案)` 列表。
    """
    collected: list[tuple[str, str]] = []
    for key in ("name", "summary", "description", "changelog"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            collected.append((key, value))
    ui = payload.get("ui_extension") or {}
    if isinstance(ui, dict) and isinstance(ui.get("nav_label"), str):
        collected.append(("ui_extension.nav_label", str(ui["nav_label"])))
    for index, step in enumerate(payload.get("steps", []) or []):
        if isinstance(step, dict) and isinstance(step.get("title"), str):
            collected.append((f"steps[{index}].title", str(step["title"])))
    for section in ("inputs", "outputs", "checklist", "settings_schema"):
        for index, item in enumerate(payload.get(section, []) or []):
            if isinstance(item, dict) and isinstance(item.get("label"), str):
                collected.append((f"{section}[{index}].label", str(item["label"])))
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                collected.append((f"{section}[{index}].name", str(item["name"])))
    return collected


def run_self_test(directory: Path, archive_root: Path) -> tuple[int, list[str]]:
    """注入负样本，验证门禁"能被触发"。

    **前置断言**（假隔离防线）：被扫描的目录必须位于本次认可的归档根内，且确实
    命中 > 0 个声明文件；否则退出码 **2**（门禁失灵），而不是拿着另一棵树的结果
    报「自检通过」。

    Args:
        directory: 声明目录（取第一份声明作为变异基座）。
        archive_root: 本次运行认可的归档根。

    Returns:
        tuple[int, list[str]]: 退出码（1=自检成功，2=门禁失灵）、说明清单。
    """
    scope_problems = assert_scan_scope(directory, archive_root)
    if scope_problems:
        return 2, [f"[FAIL] 自检前置断言失败：{item}" for item in scope_problems]
    if not directory.is_dir():
        return 2, [f"声明目录不存在：{directory}"]
    payloads = _load_payloads(directory)
    if not payloads:
        return 2, [f"声明目录为空：{directory}"]

    notes: list[str] = [f"[OK] 扫描目录 ⊆ 归档根：{directory.resolve()} ⊆ {archive_root.resolve()}"]
    # 「加载器认的目录」必须与「本次扫描的目录」一致——不一致说明 import 到的
    # `app` 包来自另一棵树（正是 2026-10-23 那次假隔离的成因）。
    loader_dir = default_manifests_dir().resolve()
    if directory.resolve() == SCRIPT_MANIFESTS_DIR.resolve() and loader_dir != directory.resolve():
        return 2, [
            *notes,
            f"[FAIL] 自检前置断言失败：加载器声明目录 {loader_dir} ≠ 脚本相对树 "
            f"{directory.resolve()}（import 到了别处的 app 包）",
        ]
    notes.append(f"[OK] 加载器声明目录一致：{loader_dir}")
    base_name, base_payload = payloads[0]
    undetected: list[str] = []
    mutations = {
        "多出可执行字段": _mutate_extra_field,
        "能力不在枚举内": _mutate_capability,
        "文案含内部用语": _mutate_banned_term,
        "步骤序号不连续": _mutate_seq,
        "工具操作未登记": _mutate_tool_operation,
    }
    for label, mutate in mutations.items():
        registry = PluginRegistry()
        failure = registry.load_manifest(mutate(base_payload), source=f"selftest-{label}")
        if failure is None:
            undetected.append(f"{label}（{base_name}）未被拦截")
        else:
            notes.append(f"[OK] 已拦截：{label} → {failure.stage}")

    # 第 5 条：重复 plugin_id（同 id 同版本注册两次）
    registry = PluginRegistry()
    first = registry.load_manifest(base_payload, source="selftest-first")
    second = registry.load_manifest(base_payload, source="selftest-second")
    if first is not None:
        undetected.append("首份声明自检未通过，无法验证重复注册")
    elif second is None or second.stage != "duplicate":
        undetected.append("重复 plugin_id 未被拦截")
    else:
        notes.append("[OK] 已拦截：重复 plugin_id → duplicate")
        if registry.get(base_payload["plugin_id"], base_payload["version"]) is None:
            undetected.append("重复注册后原条目丢失（应保持不变）")

    # 第 3 条反向：文案正常时不报错（防"一律报错"式假门禁）
    registry = PluginRegistry()
    if registry.load_manifest(base_payload, source="selftest-clean") is not None:
        undetected.append("干净声明被误判为违规（门禁过严，会把人逼去关掉它）")
    else:
        notes.append("[OK] 干净声明通过（门禁不是一律报错）")

    if undetected:
        notes.extend(f"[FAIL] {item}" for item in undetected)
        return 2, notes
    return 1, notes


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 `sys.argv[1:]`）。

    Returns:
        int: 退出码（0=通过 / 1=发现违规或自检命中 / 2=门禁自身异常）。
    """
    parser = argparse.ArgumentParser(description="G-26 插件声明门禁")
    parser.add_argument(
        "--dir",
        default="",
        help=(
            "声明目录（缺省取脚本相对树 <repo>/apps/adapter/app/plugins/manifests，"
            "不跟随 import 到的 app 包，避免导出树复核扫到主工作区）"
        ),
    )
    parser.add_argument("--self-test", action="store_true", help="跑负样本自检（期望退出码 1）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--list-terms", action="store_true", help="打印禁词表（供前端扫描脚本复用）"
    )
    args = parser.parse_args(argv)

    if args.list_terms:
        print(json.dumps(list(BANNED_TERMS), ensure_ascii=False))
        return 0

    # 归档根：显式 `--dir` 时以它为根（导出树复核），否则以脚本所在树为根
    archive_root = Path(args.dir).resolve() if args.dir else ADAPTER_ROOT
    directory = Path(args.dir).resolve() if args.dir else SCRIPT_MANIFESTS_DIR

    if args.self_test:
        code, notes = run_self_test(directory, archive_root)
        for note in notes:
            print(note)
        print(
            "[OK]   G-26 自检通过：6 条负样本全部被拦截（退出码 1 为预期）"
            if code == 1
            else "[FAIL] G-26 自检失败：门禁未能发现注入的问题"
        )
        return code

    code, problems, stats = run_validation(directory)
    if args.json:
        print(json.dumps({"code": code, "problems": problems, "stats": stats}, ensure_ascii=False))
        return code
    if code == 2:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 2
    if code == 1:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print(f"[FAIL] G-26 未通过：{len(problems)} 处问题")
        return 1
    print(
        f"[OK]   G-26 通过：{stats['registered']} 份声明"
        f"（{', '.join(stats['plugin_ids'])}），能力与原语均在白名单内；"
        f"扫描目录（绝对路径）= {stats['manifests_dir']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
