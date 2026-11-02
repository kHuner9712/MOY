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

第二条判据（P1-09 残留，批次 3 新增）：**模板占位值的生成路径一致性**。

背景：`.env.example` / `.env.lite.example` 里刻意保留 `CHANGE_ME_*` 占位值（模板本该如此），
但「留了占位值」与「告诉部署者填什么」是两件事。批次 1 修 `OA_JWT_SECRET` 时发现：
`OA_OBJECT_STORE_SECRET_KEY` / `OA_DIFY_SANDBOX_KEY` / `OA_DB_APP_PASSWORD` 三处占位值
既没被 `install.sh` 自动生成、模板里也没写生成命令——部署者照抄模板只会看到
「请填强口令」加一行 `CHANGE_ME_STRONG_PASSWORD`，无从知道该填多长、怎么生成。
`validate_lite_up.sh` 只拦「CHANGE_ME 残留」，拦不到「填了但形态不对」。

故第二条判据：**模板里带 `CHANGE_ME` 的密钥/口令类变量，必须存在可复制的生成路径**——
- `install.sh` 里有 `env_set <VAR>`（安装脚本管理的栈，这是唯一权威来源，模板注释不算数）；
- 或 `.env.example`（标准栈：无安装脚本、手工 `cp` 路径）在变量上方 6 行内给出
  `# 生成：<命令>` 注释。

注意这**不是**要求模板里没有占位值，而是要求「占位值 + 生成路径」成对出现。

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
        is_scannable = path.suffix in SCAN_SUFFIXES or path.name.startswith(ENV_FILE_PREFIX)
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
# 第二条判据：模板占位值的生成路径一致性（P1-09 残留）
# ===========================================================================

#: 安装脚本（自动生成路径的唯一权威来源）
INSTALL_SCRIPT: Final[Path] = DEPLOY_DIR / "install.sh"

#: 模板文件后缀（`.env.example` / `.env.lite.example`）
TEMPLATE_SUFFIX: Final[str] = ".example"

#: 安装脚本里的 `env_set <VAR> ...`（生成逻辑）
ENV_SET_PATTERN: Final[re.Pattern[str]] = re.compile(r"\benv_set\s+([A-Za-z_][A-Za-z0-9_]*)")

#: 模板里的手工生成命令注释：`# 生成：openssl rand -hex 24`
GEN_HINT_PATTERN: Final[re.Pattern[str]] = re.compile(r"#\s*生成[:：]\s*\S")

#: 手工生成命令允许出现在变量行上方多少行内
GEN_HINT_LOOKBACK: Final[int] = 6

#: 安装脚本管理的模板（installer 会覆写其中的占位值，因此注释不能替代 `env_set`）
INSTALLER_MANAGED_TEMPLATES: Final[frozenset[str]] = frozenset({".env.lite.example"})


def collect_generated_vars(install_sh: Path) -> set[str]:
    """收集 `install.sh` 会随机生成并写入的变量名。

    **必须跳过注释行**：`env_set OA_XXX` 被注释掉就表示「不再生成」，
    而注释里仍留着这行文本。正则直接扫全文会把注释也算成生成路径，
    于是「把 env_set 注释掉」这种最常见的破坏方式恰好躲过门禁
    （自证时实测踩到：注释掉 `env_set OA_VALKEY_PASSWORD` 后门禁仍是绿的）。

    Args:
        install_sh: 安装脚本路径（不存在时返回空集）。

    Returns:
        set[str]: 非注释行里 `env_set <VAR>` 出现过的变量名集合。
    """
    if not install_sh.is_file():
        return set()
    try:
        text = install_sh.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        names.update(ENV_SET_PATTERN.findall(line))
    return names


def _is_secret_name(name: str) -> bool:
    """判断变量名是否属于密钥/口令类。

    Args:
        name: 变量名。

    Returns:
        bool: 命中 `SECRET_WORDS` 返回 True。
    """
    upper = name.upper()
    return any(word in upper for word in SECRET_WORDS)


def collect_template_placeholders(deploy_dir: Path) -> list[dict[str, object]]:
    """收集 `*.example` 模板里仍带 `CHANGE_ME` 的赋值行。

    Args:
        deploy_dir: 部署目录。

    Returns:
        list[dict[str, object]]: 每项含 `file` / `line` / `var` / `value` /
        `secret`（是否密钥类）/ `hint`（该行上方是否有 `# 生成：` 注释）。
    """
    items: list[dict[str, object]] = []
    if not deploy_dir.is_dir():
        return items

    for path in sorted(deploy_dir.rglob(f"*{TEMPLATE_SUFFIX}")):
        if path.is_dir() or ".venv" in path.parts or "node_modules" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for index, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            match = DIRECT_ASSIGN_PATTERN.match(line)
            if not match:
                continue
            name, raw_value = match.group(1), match.group(2).strip()
            if "CHANGE_ME" not in raw_value:
                continue
            window_start = max(0, index - GEN_HINT_LOOKBACK)
            has_hint = any(
                GEN_HINT_PATTERN.search(candidate) for candidate in lines[window_start:index]
            )
            items.append(
                {
                    "file": _rel_to_repo(path),
                    "line": str(index + 1),
                    "var": name,
                    "value": raw_value.strip("'\""),
                    "secret": _is_secret_name(name),
                    "hint": has_hint,
                }
            )
    return items


def check_template_generation_paths(
    deploy_dir: Path, install_sh: Path | None = None
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """校验模板占位值是否都有生成路径。

    判据见模块文档「第二条判据」。刻意做成「一致性校验」而非「占位值清零校验」：
    模板本来就该有占位值，缺的是「填什么、怎么生成」。

    Args:
        deploy_dir: 部署目录。
        install_sh: 安装脚本路径；缺省用 `deploy_dir/install.sh`。

    Returns:
        tuple: `(placeholders, offenders)`。前者是完整清单（含是否已覆盖），
        后者只含「密钥类 + 无生成路径」的条目（`var` / `file` / `line` / `reason`）。
    """
    script = install_sh if install_sh is not None else deploy_dir / "install.sh"
    generated = collect_generated_vars(script)

    placeholders: list[dict[str, str]] = []
    offenders: list[dict[str, str]] = []

    for item in collect_template_placeholders(deploy_dir):
        name = str(item["var"])
        file_name = str(item["file"])
        is_secret = bool(item["secret"])
        has_hint = bool(item["hint"])
        managed = Path(file_name).name in INSTALLER_MANAGED_TEMPLATES
        # 安装脚本管理的栈：只认 env_set；手工栈（.env.example）：env_set 或生成命令
        covered = name in generated or (has_hint and not managed)

        if not is_secret:
            cover_kind = "非密钥类（无需生成）"
        elif name in generated:
            cover_kind = "install.sh 自动生成"
        elif covered:
            cover_kind = "模板给出生成命令"
        else:
            cover_kind = "无自动生成路径"

        placeholders.append(
            {
                "file": file_name,
                "line": str(item["line"]),
                "var": name,
                "cover": cover_kind,
            }
        )

        if is_secret and not covered:
            reason = (
                f"{Path(file_name).name} 受 install.sh 管理，但 install.sh 里没有 `env_set {name}`"
                if managed
                else "install.sh 无 `env_set` 且模板未给出 `# 生成：` 命令"
            )
            offenders.append(
                {
                    "file": file_name,
                    "line": str(item["line"]),
                    "var": name,
                    "reason": reason,
                }
            )

    return placeholders, offenders


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
            "# 测试文件\nOA_JWT_SECRET=Xk9mQ2rT5wY8nA1bD4fG7hJ0kL3pS6uV9xC2zE5g\n",
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
            'env_set OA_POSTGRES_PASSWORD "${pg_pw}"\n',
            encoding="utf-8",
        )
        got = scan_dir(target)
        # 此时 bad.yml 与 .env.example 仍在，故只断言 good 文件均未命中
        (
            caught.append("C 良性兜底放行")
            if not any("good" in o["file"] or "generate.sh" in o["file"] for o in got)
            else missed.append("C 良性兜底放行")
        )

        # 负样本 D：应被拦截 —— 模板里的密钥类占位值没有生成路径
        # （install.sh 里没有 env_set，模板上方也没有 `# 生成：` 命令）
        orphan_dir = target / "orphan"
        orphan_dir.mkdir()
        (orphan_dir / ".env.lite.example").write_text(
            "# 安装脚本管理的栈\nOA_TEST_ORPHAN_SECRET=CHANGE_ME_STRONG_PASSWORD\n",
            encoding="utf-8",
        )
        (orphan_dir / "install.sh").write_text(
            '#!/bin/bash\nenv_set OA_OTHER_PASSWORD "$(openssl rand -hex 24)"\n',
            encoding="utf-8",
        )
        _placeholders, orphan_offenders = check_template_generation_paths(orphan_dir)
        (
            caught.append("D 占位值无生成路径")
            if any(o["var"] == "OA_TEST_ORPHAN_SECRET" for o in orphan_offenders)
            else missed.append("D 占位值无生成路径")
        )

        # 负样本 E：应被放行 —— 覆盖的两条合法路径都命中
        covered_dir = target / "covered"
        covered_dir.mkdir()
        (covered_dir / "install.sh").write_text(
            '#!/bin/bash\nenv_set OA_TEST_MANAGED_PASSWORD "$(openssl rand -hex 24)"\n',
            encoding="utf-8",
        )
        (covered_dir / ".env.lite.example").write_text(
            "OA_TEST_MANAGED_PASSWORD=CHANGE_ME_STRONG_PASSWORD\n",
            encoding="utf-8",
        )
        (covered_dir / ".env.example").write_text(
            "# 口令类：openssl rand -hex 24\n"
            "# 生成：openssl rand -hex 24\n"
            "OA_TEST_HANDWRITTEN_PASSWORD=CHANGE_ME_STRONG_PASSWORD\n"
            "# 非密钥配置，不要求生成路径\n"
            "OA_TEST_PLAIN_NAME=CHANGE_ME_NOT_A_SECRET\n",
            encoding="utf-8",
        )
        _placeholders, covered_offenders = check_template_generation_paths(covered_dir)
        (
            caught.append("E 生成路径齐全放行")
            if not covered_offenders
            else missed.append("E 生成路径齐全放行")
        )

    print("负样本自检结果：")
    checks = [
        ("A yml 密钥明文兜底", "A yml 密钥明文兜底（须拦截）"),
        ("B .env 密钥明文兜底", "B .env 密钥明文兜底（须拦截）"),
        ("C 良性兜底放行", "C 良性兜底（须放行）"),
        ("D 占位值无生成路径", "D 占位值无生成路径（须拦截）"),
        ("E 生成路径齐全放行", "E 生成路径齐全（须放行）"),
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

    placeholders, path_offenders = check_template_generation_paths(DEPLOY_DIR, INSTALL_SCRIPT)

    print("[INFO] 模板占位值清单（密钥类 + 非密钥类，逐项标注生成路径）：")
    if not placeholders:
        print("       （模板里没有 CHANGE_ME 占位值）")
    for item in placeholders:
        print(f"  - {item['file']}:{item['line']}  {item['var']}  → {item['cover']}")
    print()

    if offenders:
        print("[FAIL] 发现密钥类变量带明文兜底值（缺失须拒绝启动，改用 `:?`）：")
        for item in offenders:
            print(f"  - {item['file']}:{item['line']}  {item['var']}")
            print(f"      {item['text']}")
        if args.json:
            print(json.dumps({"ok": False, "offenders": offenders}, ensure_ascii=False))
        return 1

    if path_offenders:
        print("[FAIL] 以下模板占位值没有生成路径（部署者不知道填什么）：")
        for item in path_offenders:
            print(f"  - {item['file']}:{item['line']}  {item['var']}")
            print(f"      {item['reason']}")
        print()
        print("  修法：在 install.sh 的 generate_env_file() 里加 `env_set <VAR>`，")
        print("        或在模板该变量上方补一行 `# 生成：openssl rand -hex 24`。")
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "offenders": [], "uncovered": path_offenders},
                    ensure_ascii=False,
                )
            )
        return 1

    print("[OK]   密钥兜底扫描通过：无密钥类变量带明文兜底")
    print(f"[OK]   模板占位值一致性通过：{len(placeholders)} 项占位值均有生成路径")
    if args.json:
        print(
            json.dumps(
                {"ok": True, "offenders": [], "uncovered": [], "placeholders": placeholders},
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
