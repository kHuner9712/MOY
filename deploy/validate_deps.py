#!/usr/bin/env python3
"""适配层依赖门禁（架构文档 §8.1.1 / §9.6）。

三道校验：

1. **清单一致性**：``pyproject.toml`` 的运行时依赖必须与架构文档第 8.1 节
   清单一致（包名 + 版本约束双校验），防止文档与实现漂移；
2. **最低安全版本**：对已知存在高危漏洞且**命中本项目攻击面**的包，
   校验其版本下限（starlette 大文件 DoS 命中 R3 上传接口）；
3. **加固约束检查**：对无上游修复版本的漏洞，校验代码侧是否已落实
   架构文档 §8.1.1 规定的加固约束（静态检查）。

用法::

    python deploy/validate_deps.py                  # 1 + 2 + 3
    python deploy/validate_deps.py --vuln-check     # 追加 pip-audit 漏洞扫描

退出码：0 = 通过；1 = 不通过。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

#: 仓库根目录
REPO_ROOT = Path(__file__).resolve().parent.parent

#: 适配层 pyproject.toml
PYPROJECT = REPO_ROOT / "apps" / "adapter" / "pyproject.toml"

#: 架构文档
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-v1.0.md"

#: 适配层源码根目录
ADAPTER_SRC = REPO_ROOT / "apps" / "adapter" / "app"

#: 架构文档 §8.1 依赖清单中允许出现的包名（用于校验文档漂移）
#: 注意：这是「文档清单」的白名单，不是「允许引入任何包」的许可 ——
#: 新增依赖必须先登记进架构文档 §8.1，再进本白名单。
DOCUMENTED_PACKAGES = {
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "httpx",
    "redis",
    "sqlalchemy",
    "asyncpg",
    "python-jose",
    "tenacity",
    "structlog",
    "slowapi",
    "python-multipart",
    "pyyaml",
    "python-docx",
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "ruff",
    "mypy",
}

#: 命中本项目攻击面的高危漏洞包 → 最低安全版本
#:
#: starlette：PYSEC-2026-1941 等 14 项。PYSEC-2026-1941（大文件 multipart
#: 解析阻塞事件循环导致 DoS）直接命中 R3 文档上传接口的 50MB 上限，
#: 与 AC-09（并发 ≥1000）/ AC-10（可用率 ≥99.5%）直接相关。
MIN_SAFE_VERSIONS = {
    "starlette": "1.3.1",
}

#: 加固约束（无上游修复版本的漏洞 → 代码侧补偿措施）
#:
#: python-jose PYSEC-2026-4298：未显式限定 algorithms 时可伪造 HS256 令牌。
#: 约束：调用 jwt.decode() 必须显式传 algorithms=[...]。
#: ecdsa PYSEC-2026-1325：P-256 Minerva 时序攻击，影响签名/密钥生成/ECDH，
#: 验签不受影响。约束：禁止引入这三个 API。
HARDENING_RULES = [    {
        "id": "H-1",
        "desc": "jwt.decode() 必须显式传 algorithms=[...]（python-jose PYSEC-2026-4298 加固）",
        "pattern": re.compile(r"jwt\.decode\("),
        "forbid_pattern": re.compile(r"jwt\.decode\((?:[^()]|\([^()]*\))*\)", re.S),
        "require_in_block": re.compile(r"algorithms\s*="),
        "forbidden_apis": [],
    },
    {
        "id": "H-2",
        "desc": "禁止使用 ecdsa 的签名/密钥生成/ECDH API（ecdsa PYSEC-2026-1325 加固）",
        "pattern": re.compile(r"\becdsa\b"),
        "forbid_pattern": None,
        "require_in_block": None,
        "forbidden_apis": [
            "SigningKey",
            "sign_digest",
            "generate_private_key",
            "ECDH",
        ],
    },
]


#: 已完成调用面适用性评估并被接受的漏洞（架构文档 §8.1.1）
#:
#: 键为 (包名, 漏洞 ID)，值为「不受影响」的判定依据。
#: 这些包**上游无修复版本**，已通过代码加固约束补偿（见 HARDENING_RULES），
#: 故不阻断合并，但每次升级相关依赖时须重新评估。
ACCEPTED_RISKS: dict[tuple[str, str], str] = {
    (
        "python-jose",
        "PYSEC-2026-4298",
    ): "已显式限定 algorithms=[...] 且默认 HS256 对称，漏洞前提不成立（§8.1.1 强制项 1）",
    (
        "ecdsa",
        "PYSEC-2026-1325",
    ): "仅使用验签，未引入签名/密钥生成/ECDH API（§8.1.1 强制项 2）",
}


def _version_tuple(version: str) -> tuple[int, ...]:
    """将版本号转为可比较的元组。

    Args:
        version: 版本号字符串，如 ``1.3.1``。

    Returns:
        tuple[int, ...]: 各段数字组成的元组，非法段按 0 处理。
    """
    parts: list[int] = []
    for chunk in version.split("."):
        digits = re.match(r"^\d+", chunk.strip())
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


def _parse_min_version(spec: str) -> str:
    """从版本约束中提取最低版本。

    Args:
        spec: 版本约束，如 ``^1.3``、``>=1.3.1,<2.0``。

    Returns:
        str: 最低版本号；无法解析时返回空串。
    """
    match = re.search(r">=\s*([0-9][0-9.]*)", spec)
    if match:
        return match.group(1)
    match = re.search(r"\\^\\s*([0-9][0-9.]*)", spec)
    if match:
        return match.group(1)
    return ""


def load_dependencies() -> list[str]:
    """读取适配层运行时依赖声明。

    Returns:
        list[str]: 依赖规格列表（含 extras 与版本约束）。
    """
    with PYPROJECT.open("rb") as fp:
        data = tomllib.load(fp)
    return list(data["project"]["dependencies"])


def split_package(spec: str) -> tuple[str, str]:
    """拆分依赖规格为包名与版本约束。

    Args:
        spec: 依赖规格，如 ``uvicorn[standard]>=0.30``。

    Returns:
        tuple[str, str]: (包名, 版本约束)。
    """
    name = re.split(r"[<>=!~\[]", spec, maxsplit=1)[0].strip()
    constraint = spec[len(name):].strip()
    return name.lower().replace("_", "-"), constraint


def check_documented(deps: list[str]) -> list[str]:
    """校验依赖已在架构文档 §8.1 清单登记。

    Args:
        deps: 依赖规格列表。

    Returns:
        list[str]: 失败项描述。
    """
    failures: list[str] = []
    if not ARCHITECTURE_DOC.is_file():
        return [f"架构文档不存在：{ARCHITECTURE_DOC}"]
    doc_text = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    # 仅校验 §8.1 依赖清单小节
    start = doc_text.find("## 8.1 ")
    end = doc_text.find("### 8.1.1")
    if start == -1:
        return ["架构文档中未找到 §8.1 依赖清单小节"]
    section = doc_text[start:end] if end > start else doc_text[start:]
    for spec in deps:
        name, _ = split_package(spec)
        if name not in DOCUMENTED_PACKAGES:
            failures.append(
                f"{name}：未登记进架构文档 §8.1 清单（须先补文档再引入依赖）"
            )
            continue
        if f"| {name}" not in section and f"{name} /" not in section:
            failures.append(f"{name}：架构文档 §8.1 清单中未找到该包条目")
    return failures


def check_min_safe_versions(deps: list[str]) -> list[str]:
    """校验高危漏洞包的版本下限。

    校验两个层面：
    1. **声明层**：若包在 ``pyproject.toml`` 中显式声明，其版本约束下限不得低于要求；
    2. **解析层**（关键）：若包是传递依赖（如 starlette 由 fastapi 引入），
       **必须校验实际解析安装的版本** —— 声明层看不到传递依赖，
       而传递依赖恰恰是漏洞的高发区（本次 starlette 即属此类）。

    Args:
        deps: 依赖规格列表。

    Returns:
        list[str]: 失败项描述。
    """
    failures: list[str] = []
    declared = {split_package(spec)[0]: split_package(spec)[1] for spec in deps}

    for name, required in MIN_SAFE_VERSIONS.items():
        # 1) 声明层
        if name in declared:
            declared_min = _parse_min_version(declared[name])
            if not declared_min:
                failures.append(
                    f"{name}：未声明版本下限，无法保证 ≥ {required}（命中攻击面的高危漏洞）"
                )
            elif _version_tuple(declared_min) < _version_tuple(required):
                failures.append(
                    f"{name}：声明下限 {declared_min} < 最低安全版本 {required}"
                    f"（命中 R3 上传接口攻击面）"
                )
        else:
            # 传递依赖：要求显式声明，避免被上游静默降级
            failures.append(
                f"{name}：命中攻击面的高危漏洞包，须在 pyproject.toml 中**显式声明**"
                f"并锁定 ≥ {required}（当前为传递依赖，版本随上游漂移）"
            )

        # 2) 解析层：校验实际安装版本
        try:
            import importlib.metadata as metadata

            installed = metadata.version(name)
        except Exception:
            continue  # 未安装（CI 中会在后续步骤安装），跳过
        if _version_tuple(installed) < _version_tuple(required):
            failures.append(
                f"{name}：实际安装版本 {installed} < 最低安全版本 {required}"
                f"（命中 R3 上传接口攻击面）"
            )
    return failures


def check_hardening_rules() -> list[str]:
    """校验加固约束是否在代码中落实。

    Returns:
        list[str]: 失败项描述。
    """
    failures: list[str] = []
    py_files = sorted(ADAPTER_SRC.rglob("*.py")) if ADAPTER_SRC.is_dir() else []
    if not py_files:
        return [f"未找到适配层源码目录：{ADAPTER_SRC}"]

    for rule in HARDENING_RULES:
        if rule["id"] == "H-1":
            for path in py_files:
                text = path.read_text(encoding="utf-8")
                if not rule["pattern"].search(text):
                    continue
                for call in rule["forbid_pattern"].finditer(text):
                    if not rule["require_in_block"].search(call.group()):
                        rel = path.relative_to(REPO_ROOT)
                        failures.append(
                            f"{rule['id']} {rule['desc']} —— {rel} 中的 jwt.decode() 未显式限定 algorithms"
                        )
        elif rule["id"] == "H-2":
            for path in py_files:
                text = path.read_text(encoding="utf-8")
                if not rule["pattern"].search(text):
                    continue
                for api in rule["forbidden_apis"]:
                    if re.search(rf"\b{re.escape(api)}\b", text):
                        rel = path.relative_to(REPO_ROOT)
                        failures.append(
                            f"{rule['id']} {rule['desc']} —— {rel} 中出现 {api}"
                        )
    return failures


def run_pip_audit(deps: list[str]) -> list[str]:
    """执行 pip-audit 漏洞扫描（需已安装 pip-audit）。

    Args:
        deps: 依赖规格列表。

    Returns:
        list[str]: 失败项描述（空列表表示无命中攻击面的漏洞）。
    """
    try:
        import pip_audit  # noqa: F401
    except ImportError:
        return [
            "pip-audit 未安装，跳过漏洞扫描（CI 中应使用独立 venv 安装后执行）"
        ]

    with tempfile.TemporaryDirectory() as tmp:
        req_file = Path(tmp) / "requirements.txt"
        req_file.write_text("\n".join(deps) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "-r", str(req_file), "--progress-spinner", "off"],
            capture_output=True,
            text=True,
            check=False,
        )
    output = result.stdout + result.stderr
    if result.returncode == 0:
        return []

    # 逐条判定：命中攻击面的（starlette）零容忍；已评估接受的不阻断；其余需人工评估
    blocking: list[str] = []
    advisory: list[str] = []
    accepted: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in output.splitlines():
        if not re.match(r"^[a-zA-Z0-9_.-]+\s+\S+\s+PYSEC-", line):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        pkg, vuln_id = parts[0], parts[2]
        key = (pkg.lower().replace("_", "-"), vuln_id)
        if key in seen:
            continue
        seen.add(key)
        entry = f"{pkg} {vuln_id}"
        if pkg.lower().replace("_", "-") in MIN_SAFE_VERSIONS:
            blocking.append(f"{entry} —— 命中业务攻击面，须修复后合并")
        elif key in ACCEPTED_RISKS:
            accepted.append(f"{entry} —— 已评估接受风险：{ACCEPTED_RISKS[key]}")
        else:
            advisory.append(
                f"{entry} —— 需做调用面适用性评估并记录结论"
                f"（架构文档 §8.1.1；不接受二值结论）"
            )
    for note in accepted:
        print(f"[ACCEPTED] {note}")
    if blocking:
        if advisory:
            return blocking + [f"另有 {len(advisory)} 项待评估：{'; '.join(advisory)}"]
        return blocking
    return advisory


def main() -> int:
    """执行校验。

    Returns:
        int: 退出码（0 通过 / 1 不通过）。
    """
    parser = argparse.ArgumentParser(description="适配层依赖门禁")
    parser.add_argument(
        "--vuln-check",
        action="store_true",
        help="追加 pip-audit 漏洞扫描（需已安装 pip-audit）",
    )
    args = parser.parse_args()

    deps = load_dependencies()

    print("=" * 70)
    print("适配层依赖门禁（架构文档 §8.1.1 / §9.6）")
    print("=" * 70)
    print(f"[INFO] 运行时依赖 {len(deps)} 条")

    failures: list[str] = []
    failures += check_documented(deps)
    failures += check_min_safe_versions(deps)
    failures += check_hardening_rules()
    if args.vuln_check:
        failures += run_pip_audit(deps)

    for failure in failures:
        print(f"[FAIL] {failure}")

    if failures:
        print(f"\\n[FAIL] 依赖门禁不通过（{len(failures)} 项）")
        return 1

    print("\\n[OK] 依赖门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
