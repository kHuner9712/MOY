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

- 凡是变量名含 `KEY / SECRET / TOKEN / PASSWORD / CREDENTIAL` 的
  `${VAR:-默认}` 兜底、`.env*` 的 `VAR=value` 行、或 shell 脚本的 `VAR=value`
  赋值，其默认值**必须**是良性的（URL、占位串、开关、短标识等）；
  一旦看起来像真实密钥（够长 + 字符类别混合），即判违规。

覆盖范围（2026-10-31 审计 L4 扩面）：
- `deploy/**/*.yml` / `*.yaml`（原有）
- `deploy/.env*` 文件（`VAR=value` 直接赋值行）
- `deploy/**/*.sh`（shell 脚本中的 `VAR=value` 赋值）

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

#: 参与扫描的文件后缀（yml/yaml 原有 + .env* 模板 + shell 脚本）
SCAN_SUFFIXES: Final[frozenset[str]] = frozenset({".yml", ".yaml", ".sh", ".example"})

#: 文件名前缀含 `.env` 的也参与扫描（如 `.env.example` / `.env.lite.example`）
ENV_FILE_PREFIX: Final[str] = ".env"

#: `.env` 本地文件（含真实密钥）不参与扫描——`.gitignore` 排除，不入库；
#: `.env.example` / `.env.lite.example` 模板入库，必须扫描
def _is_local_env(path: Path) -> bool:
    """判断是否为本地 `.env` 文件（不入库、含真实密钥）。

    Args:
        path: 文件路径。

    Returns:
        bool: 本地 `.env` 文件返回 True（跳过扫描）。
    """
    name = path.name
    if not name.startswith(ENV_FILE_PREFIX):
        return False
    # `.env.example` / `.env.lite.example` 是入库模板，要扫
    return not name.endswith(".example")

#: 变量名中命中即视为「密钥类」的词
SECRET_WORDS: Final[tuple[str, ...]] = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")

#: `${VAR:-默认值}`（只取最内层，避免与 `:-` 之外的形式混淆）
FALLBACK_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")

#: `.env*` 文件的 `VAR=value` 直接赋值行（跳过注释行）
DIRECT_ASSIGN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.+)$"
)

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
    # 纯小写单词（如服务名）不误伤。额外要求**必须含数字或大写**，
    # 排除 `officeai-adapter` 这类纯小写标识符（含连字符但非密钥）。
    has_lower = any(c.islower() for c in value)
    has_upper = any(c.isupper() for c in value)
    has_digit = any(c.isdigit() for c in value)
    has_symbol = any(not c.isalnum() for c in value)
    classes = sum((has_lower, has_upper, has_digit, has_symbol))
    if classes < 2:
        return False
    # 纯小写 + 非字母数字（如服务名、路径片段）→ 不算密钥
    if not has_upper and not has_digit:
        return False
    return True


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
    """扫描目录下的编排文件、.env 模板和 shell 脚本，返回违规项。

    Args:
        target: 待扫描目录。

    Returns:
        list[dict[str, str]]: 每项含 `file` / `line` / `var` / `text`。
    """
    offenders: list[dict[str, str]] = []
    if not target.is_dir():
        return offenders

    for path in sorted(target.rglob("*")):
        if path.is_dir():
            continue
        # 参与扫描的文件类型：yml/yaml、.env* 模板（不含本地 .env）、shell 脚本
        is_scannable = (
            path.suffix in SCAN_SUFFIXES
            or path.name.startswith(ENV_FILE_PREFIX)
        )
        if not is_scannable:
            continue
        # 跳过本地 .env 文件（含真实密钥，.gitignore 排除，不入库）
        if _is_local_env(path):
            continue
        if ".venv" in path.parts or "node_modules" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        is_env_or_sh = path.name.startswith(ENV_FILE_PREFIX) or path.suffix == ".sh"
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            # `${VAR:-default}` 形式（yml/yaml/shell 均可出现）
            for name, default in FALLBACK_PATTERN.findall(line):
                if looks_like_secret(name, default):
                    offenders.append(
                        {
                            "file": _rel_to_repo(path),
                            "line": str(lineno),
                            "var": name,
                            "text": stripped[:120],
                        }
                    )

            # `.env*` / `.sh` 文件的 `VAR=value` 直接赋值
            if is_env_or_sh:
                match = DIRECT_ASSIGN_PATTERN.match(line)
                if match:
                    name, raw_value = match.group(1), match.group(2).strip()
                    # 跳过动态值（命令替换 / 参数展开 / 变量引用 / 空值）
                    if "$" in raw_value or "`" in raw_value:
                        continue
                    # 去掉引号
                    value = raw_value.strip("'\"")
                    if looks_like_secret(name, value):
                        offenders.append(
                            {
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
    """负样本自检：注入 3 类样本，验证 2 类被拦截、1 类被放行。

    Returns:
        int: 行为符合预期返回 1（CI 断言该退出码），否则返回 0。
    """
    caught: list[str] = []
    missed: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)

        # 负样本 A：应被拦截 —— yml 密钥变量带高熵明文兜底
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
            caught.append("A yml 密钥明文兜底")
            if any(o["var"] == "OA_PLUGIN_DAEMON_KEY" for o in got)
            else missed.append("A yml 密钥明文兜底")
        )

        # 负样本 B：应被拦截 —— .env.example 密钥变量带真实值
        bad_env = target / ".env.example"
        bad_env.write_text(
            "# 测试文件\n"
            "OA_JWT_SECRET=Xk9mQ2rT5wY8nA1bD4fG7hJ0kL3pS6uV9xC2zE5g\n",
            encoding="utf-8",
        )
        got = scan_dir(target)
        (
            caught.append("B .env 密钥明文兜底")
            if any(o["var"] == "OA_JWT_SECRET" and "env" in o["file"] for o in got)
            else missed.append("B .env 密钥明文兜底")
        )

        # 负样本 C：应被放行 —— 非密钥变量的 URL 兜底 + CHANGE_ME 占位 + shell 正常赋值
        good = target / "good.yml"
        good.write_text(
            "services:\n"
            "  api:\n"
            "    SSRF_PROXY_URL: ${OA_SSRF_PROXY_URL:-http://ssrf_proxy:3128}\n"
            "    SOME_SECRET: ${OA_SOME_SECRET:-CHANGE_ME_STRONG_PASSWORD}\n"
            "    MODE: ${OA_MODE:-api}\n",
            encoding="utf-8",
        )
        good_sh = target / "generate.sh"
        good_sh.write_text(
            "#!/bin/bash\n"
            'pg_pw="$(openssl rand -hex 24)"\n'
            "env_set OA_POSTGRES_PASSWORD \"${pg_pw}\"\n",
            encoding="utf-8",
        )
        got = scan_dir(target)
        # 此时 bad.yml 与 .env.example 仍在，故只断言 good 文件均未命中
        (
            caught.append("C 良性兜底放行")
            if not any(
                "good" in o["file"] or "generate.sh" in o["file"] for o in got
            )
            else missed.append("C 良性兜底放行")
        )

    print("负样本自检结果：")
    checks = [
        ("A yml 密钥明文兜底", "A yml 密钥明文兜底（须拦截）"),
        ("B .env 密钥明文兜底", "B .env 密钥明文兜底（须拦截）"),
        ("C 良性兜底放行", "C 良性兜底（须放行）"),
    ]
    all_ok = True
    for key, label in checks:
        ok = key in caught
        if not ok:
            all_ok = False
        print(f"    {'✅ 符合预期' if ok else '❌ 行为不符'}  {label}")
    if missed:
        print(f"[FAIL] 以下负样本行为不符预期：{missed}")
        return 0
    if not all_ok:
        print(f"[FAIL] 部分样本未命中预期：caught={caught}")
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
