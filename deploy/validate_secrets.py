#!/usr/bin/env python3
"""密钥兜底扫描门禁（审计 OA-AUDIT-001 / P0-3，新增 G-33）。

**为什么要有这条门禁**（付过的代价）：

`deploy/docker-compose.yml` 里 `PLUGIN_DAEMON_KEY` / `PLUGIN_DIFY_INNER_API_KEY`
（共 3 处）写成了 `${OA_XXX:-<明文常量>}`——**不配 .env 也能启动**，而那个常量
已随仓库提交公开。同文件其余 20+ 枚密钥一律是 `${OA_XXX:?必须在 .env 中设置}`
（缺失即拒绝启动），唯独这两枚漏网。

后果：私有化部署默认使用公开常量密钥，plugin_daemon(5002) 与 Dify 内部 API
对任何人敞开。`.env.example` 里也没有这两个变量的登记，部署者无从知晓要配。

判据（一条，刻意保持单一）：

- 凡是变量名含 `KEY / SECRET / TOKEN / PASSWORD / CREDENTIAL` 的 `${VAR:-默认}`
  兜底，其默认值**必须**是良性的（URL、占位串、开关、短标识等）；
  一旦看起来像真实密钥（够长 + 字符类别混合），即判违规。

用法::

    python deploy/validate_secrets.py                 # 扫描（CI 默认）
    python deploy/validate_secrets.py --self-test     # 负样本自检（期望退出码 1）
    python deploy/validate_secrets.py --json          # 机器可读输出

退出码：0 = 通过；1 = 发现违规或自检命中；2 = 门禁自身异常（目录缺失）。

**不做什么**：不做密钥熵值打分（易产生噪声），只拦「明显是密钥的兜底」这一类；
也**不扫描历史 commit**——已泄露值的处置靠轮换，不靠扫描。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Final

#: 仓库根目录
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 扫描目录（编排与环境变量模板）
DEPLOY_DIR: Final[Path] = REPO_ROOT / "deploy"

#: 参与扫描的文件后缀
SCAN_SUFFIXES: Final[frozenset[str]] = frozenset({".yml", ".yaml"})

#: 变量名中命中即视为「密钥类」的词
SECRET_WORDS: Final[tuple[str, ...]] = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")

#: `${VAR:-默认值}`（只取最内层，避免与 `:-` 之外的形式混淆）
FALLBACK_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")

#: 判定为「真实密钥」的最小长度
MIN_SECRET_LEN: Final[int] = 16

#: 明确的良性兜底（出现即放行）
BENIGN_PREFIXES: Final[tuple[str, ...]] = (
    "http://",
    "https://",
    "redis://",
    "postgresql",
    "CHANGE_ME",
    "change-me",
)


def looks_like_secret(name: str, default: str) -> bool:
    """判断一个 `${VAR:-默认}` 兜底是否像真实密钥。

    Args:
        name: 变量名。
        default: 兜底默认值。

    Returns:
        bool: 像真实密钥返回 True。
    """
    upper = name.upper()
    if not any(word in upper for word in SECRET_WORDS):
        return False

    value = default.strip()
    if not value:
        return False
    if any(value.startswith(p) for p in BENIGN_PREFIXES):
        return False
    if len(value) < MIN_SECRET_LEN:
        return False

    # 字符类别混合（大小写 + 数字 + 符号中至少两类）才算高熵；
    # 纯小写单词（如服务名）不误伤。
    classes = sum(
        (
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        )
    )
    return classes >= 2


def _rel_to_repo(path: Path) -> str:
    """把路径转成相对仓库根的形式；不在仓库内时退回文件名。

    Args:
        path: 目标路径。

    Returns:
        str: 相对路径（POSIX 分隔符）；无法相对化时返回文件名。
    """
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return path.name


def scan_dir(target: Path) -> list[dict[str, str]]:
    """扫描目录下的编排文件，返回违规项。

    Args:
        target: 待扫描目录。

    Returns:
        list[dict[str, str]]: 每项含 `file` / `line` / `var` / `text`。
    """
    offenders: list[dict[str, str]] = []
    if not target.is_dir():
        return offenders

    for path in sorted(target.rglob("*")):
        if path.is_dir() or path.suffix not in SCAN_SUFFIXES:
            continue
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for name, default in FALLBACK_PATTERN.findall(line):
                if looks_like_secret(name, default):
                    offenders.append(
                        {
                            # 自检用的是临时目录，不在仓库内，故不能直接 relative_to
                            "file": _rel_to_repo(path),
                            "line": str(lineno),
                            "var": name,
                            "text": stripped[:120],
                        }
                    )
    return offenders


# ===========================================================================
# 负样本自检（证明门禁「能红」）
# ===========================================================================


def self_test() -> int:
    """负样本自检：注入 2 类样本，验证 1 类被拦截、1 类被放行。

    Returns:
        int: 行为符合预期返回 1（CI 断言该退出码），否则返回 0。
    """
    caught: list[str] = []
    missed: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)

        # 负样本 A：应被拦截 —— 密钥变量带高熵明文兜底
        bad = target / "bad.yml"
        bad.write_text(
            "services:\n"
            "  api:\n"
            # 负样本刻意用**合成值**，不复制真实泄露过的密钥
            "    PLUGIN_DAEMON_KEY: ${OA_PLUGIN_DAEMON_KEY:-Ab3xQ9zLm2Kp7RtYvN4Wc1Jd8Fs6Gh5E}\n",
            encoding="utf-8",
        )
        got = scan_dir(target)
        (
            caught.append("A 密钥明文兜底")
            if any(o["var"] == "OA_PLUGIN_DAEMON_KEY" for o in got)
            else missed.append("A 密钥明文兜底")
        )

        # 负样本 B：应被放行 —— 非密钥变量的 URL 兜底 + CHANGE_ME 占位
        good = target / "good.yml"
        good.write_text(
            "services:\n"
            "  api:\n"
            "    SSRF_PROXY_URL: ${OA_SSRF_PROXY_URL:-http://ssrf_proxy:3128}\n"
            "    SOME_SECRET: ${OA_SOME_SECRET:-CHANGE_ME_STRONG_PASSWORD}\n"
            "    MODE: ${OA_MODE:-api}\n",
            encoding="utf-8",
        )
        got = scan_dir(target)
        # 此时 bad.yml 仍在，故只断言 good.yml 的三行均未命中
        (
            caught.append("B 良性兜底放行")
            if not any(o["file"].endswith("good.yml") for o in got)
            else missed.append("B 良性兜底放行")
        )

    print("负样本自检结果：")
    for name in ("A 密钥明文兜底（须拦截）", "B 良性兜底（须放行）"):
        ok = ("A" in name and "A 密钥明文兜底" in caught) or (
            "B" in name and "B 良性兜底放行" in caught
        )
        print(f"    {'✅ 符合预期' if ok else '❌ 行为不符'}  {name}")
    if missed:
        print(f"[FAIL] 以下负样本行为不符预期：{missed}")
        return 0
    print("[PASS] 负样本行为全部符合预期（门禁确实能红）")
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
    parser = argparse.ArgumentParser(description="密钥兜底扫描门禁（G-33）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（期望退出码 1）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    print("=" * 70)
    print("编排密钥兜底扫描（G-33 / OA-AUDIT-001 P0-3）")
    print("=" * 70)

    if not DEPLOY_DIR.is_dir():
        print(f"[FAIL] 未找到部署目录：{DEPLOY_DIR}")
        return 2

    offenders = scan_dir(DEPLOY_DIR)
    print(f"[INFO] 扫描目录 {DEPLOY_DIR}（{', '.join(sorted(SCAN_SUFFIXES))}）")
    print()

    if offenders:
        print("[FAIL] 发现密钥类变量带明文兜底值（缺失须拒绝启动，改用 `:?`）：")
        for item in offenders:
            print(f"  - {item['file']}:{item['line']}  {item['var']}")
            print(f"      {item['text']}")
        if args.json:
            print(json.dumps({"ok": False, "offenders": offenders}, ensure_ascii=False))
        return 1

    print("[OK]   密钥兜底扫描通过：无密钥类变量带明文兜底")
    if args.json:
        print(json.dumps({"ok": True, "offenders": []}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
