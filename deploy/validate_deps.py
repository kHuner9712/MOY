#!/usr/bin/env python3
"""适配层依赖门禁（架构文档 §8.1.1 / §9.6）。

三道校验：

1. **清单一致性**：``pyproject.toml`` 的运行时依赖必须与架构文档第 8.1 节
   清单一致（包名 + 版本约束双校验），防止文档与实现漂移；
2. **最低安全版本**：对已知存在高危漏洞且**命中本项目攻击面**的包，
   校验其版本下限（starlette 大文件 DoS 命中 R3 上传接口）；
3. **加固约束检查**：对无上游修复版本的漏洞，校验代码侧是否已落实
   架构文档 §8.1.1 规定的加固约束（静态检查）；
4. **前端依赖一致性**：`apps/web/package.json` 的 dependencies +
   devDependencies 必须全部登记在架构文档 §8.2 表格里（真源即表格本身）。

用法::

    python deploy/validate_deps.py                  # 1 + 2 + 3 + 4
    python deploy/validate_deps.py --vuln-check     # 追加 pip-audit 漏洞扫描
    python deploy/validate_deps.py --self-test      # 自检（A–G，全内存注入）

退出码：0 = 通过；1 = 不通过；2 = 无法判定（解析塌陷，fail-closed）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

#: 仓库根目录
REPO_ROOT = Path(__file__).resolve().parent.parent

#: 适配层 pyproject.toml
PYPROJECT = REPO_ROOT / "apps" / "adapter" / "pyproject.toml"

#: 架构文档
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture-v1.0.md"

#: 适配层源码根目录
ADAPTER_SRC = REPO_ROOT / "apps" / "adapter" / "app"

#: 前端依赖清单（真源：本脚本只读 dependencies + devDependencies）
WEB_PACKAGE_JSON = REPO_ROOT / "apps" / "web" / "package.json"

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
    "prometheus-client",
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "ruff",
    "mypy",
}

#: 前端依赖条数下限（`dependencies` + `devDependencies`）。
#:
#: **低于此数视为「解析塌陷」→ exit 2**，不能当成「全部合规」：
#: 读错键名、包名切分失败、package.json 被改成别的结构，都会让条目数骤降，
#: 而"0 条依赖 = 0 条违规"恰好是门禁最容易自欺的一种绿。
#: 实测 19 runtime + 17 dev = 36 条（2026-10-27），留 2 条余量。
FRONTEND_DEPS_MIN = 34

#: 非 registry 协议 → 判红（来源不可审计，也无法核验协议白名单）。
FORBIDDEN_SPEC_PREFIXES: tuple[str, ...] = (
    "file:",
    "link:",
    "portal:",
    "git+",
    "git:",
    "github:",
    "http:",
    "https:",
)

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
class HardeningRule(TypedDict):
    """单条代码加固约束。

    此前这张表是裸 `list[dict]`，值被推断成 `object`：`rule["pattern"].search(...)`
    这类调用过不了类型检查（`object` 没有 `search`），只能靠 `cast`/`ignore` 掩盖。
    TypedDict 让「哪条规则带正则、哪条只有 API 名单」变成类型可表达的差异——
    `forbid_pattern` / `require_in_block` 在 H-2 里就是 `None`。
    """

    id: str
    desc: str
    pattern: re.Pattern[str]
    forbid_pattern: re.Pattern[str] | None
    require_in_block: re.Pattern[str] | None
    forbidden_apis: list[str]


HARDENING_RULES: list[HardeningRule] = [
    {
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
    constraint = spec[len(name) :].strip()
    return name.lower().replace("_", "-"), constraint


def check_documented(deps: list[str], doc_text: str | None = None) -> list[str]:
    """校验依赖已在架构文档 §8.1 清单登记。

    Args:
        deps: 依赖规格列表。
        doc_text: 架构文档全文（自检注入用；None 时读文件）。

    Returns:
        list[str]: 失败项描述。
    """
    failures: list[str] = []
    if doc_text is None:
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
            # 提示要把两道校验都说出来：只写"补文档"会误导（文档补完了仍然红，
            # 因为还有本脚本的这个集合），上一批排查就卡在这里。
            failures.append(
                f"{name}：未登记进 §8.1 清单或未加入 DOCUMENTED_PACKAGES"
                "（须先补架构文档，再加进本脚本白名单）"
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
        installed: str | None = None
        try:
            import importlib.metadata as metadata

            installed = metadata.version(name)
        except Exception:  # 未安装（CI 中会在后续步骤安装）→ 本项跳过
            installed = None
        if installed is None:
            continue
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
            # H-1 的两条正则是该规则的组成部分（表里恒有值）；断言同时完成类型收窄，
            # 等价于把「表被改坏」变成启动即失败，而不是静默跳过这条防线。
            forbid_pattern = rule["forbid_pattern"]
            require_in_block = rule["require_in_block"]
            assert forbid_pattern is not None and require_in_block is not None
            for path in py_files:
                text = path.read_text(encoding="utf-8")
                if not rule["pattern"].search(text):
                    continue
                for call in forbid_pattern.finditer(text):
                    if not require_in_block.search(call.group()):
                        rel = path.relative_to(REPO_ROOT)
                        failures.append(
                            f"{rule['id']} {rule['desc']} —— {rel} 中的 jwt.decode() "
                            f"未显式限定 algorithms"
                        )
        elif rule["id"] == "H-2":
            for path in py_files:
                text = path.read_text(encoding="utf-8")
                if not rule["pattern"].search(text):
                    continue
                for api in rule["forbidden_apis"]:
                    if re.search(rf"\b{re.escape(api)}\b", text):
                        rel = path.relative_to(REPO_ROOT)
                        failures.append(f"{rule['id']} {rule['desc']} —— {rel} 中出现 {api}")
    return failures


class _FatalError(Exception):
    """门禁无法判定时必须 exit 2（fail-closed），不得退化成「0 违规」。"""


#: 文档表格里的分组分隔符：`react / react-dom`、`a + b`。
#: **两侧必须有空白**——零空格会把 `@tanstack/react-query` 劈成两半。
_GROUP_SPLIT = re.compile(r"\s+(?:/|\+)\s+")


def _architecture_section(doc_text: str, heading: str) -> str:
    """截取架构文档的某个二级小节（到下一个一/二级标题为止）。

    Args:
        doc_text: 架构文档全文。
        heading: 小节标题前缀，如 `## 8.2 `。

    Returns:
        str: 小节正文；标题不存在时返回空串。
    """
    start = doc_text.find(heading)
    if start == -1:
        return ""
    rest = doc_text[start + len(heading) :]
    # 只认一/二级标题：`### 8.2.1` 这类子标题不能截断本节
    stop = re.search(r"\n#{1,2} ", rest)
    return rest[: stop.start()] if stop else rest


def normalize_frontend_spec(raw: str) -> tuple[str, str]:
    """把一条前端依赖规格归一化为 (真包名, 版本约束)。

    `npm:` 别名的**真身份是被指向的包**（`"foo": "npm:bar@1"` 装的是 `bar`）。
    按字面名 `foo` 校验，等于给「用别名绕过架构文档」留一条合法通道。

    Args:
        raw: 依赖规格原文，如 `npm:bar@^1`。

    Returns:
        tuple[str, str]: (真包名, 版本约束)。
    """
    spec = raw.strip()
    if spec.startswith("npm:"):
        spec = spec[4:].strip()
    spec = re.sub(r"\[[^\]]*\]", "", spec)
    cut = len(spec)
    # 作用域包以 `@` 开头，分隔符只能取第一个非首位的 `@`
    for index in range(1, len(spec)):
        if spec[index] == "@":
            cut = index
            break
    return spec[:cut].strip().lower(), spec[cut:].lstrip("@").strip()


def parse_doc_package_table(section: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """解析 §8.2 表格首列，得到已登记包名与通配前缀。

    分组行的分隔符**必须带空格**：`react / react-dom` 是一格写两个包，
    而 `@tanstack/react-query` 里的 `/` 两侧没有空格。用 `split("/")` 或
    零空格正则，后者会被劈成 `@tanstack` + `react-query`（实测漏判 6 个包）。

    `/*` 通配（`@radix-ui/*`）**先判后缀再取前缀**，顺序反了前缀就丢。

    Args:
        section: §8.2 小节正文。

    Returns:
        tuple[frozenset[str], tuple[str, ...]]: (显式包名, 通配前缀)。
    """
    names: set[str] = set()
    prefixes: set[str] = set()
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0].strip().strip("`").strip()
        # 表头行与 `| --- |` 分隔行
        if not first or first in {"包", "依赖"} or set(first) <= {"-", ":", " "}:
            continue
        for token in _GROUP_SPLIT.split(first):
            token = token.strip().strip("`").strip()
            if not token:
                continue
            if token.endswith("/*"):
                prefixes.add(token[:-1].rstrip("/") + "/")
                continue
            names.add(normalize_frontend_spec(token)[0])
    return frozenset(names), tuple(sorted(prefixes))


def _frontend_entries(payload: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
    """从已解析的 package.json 内容提取可校验条目。

    只读 `dependencies` + `devDependencies`：`peerDependencies` 与
    `optionalDependencies` 不是本项目安装的依赖，`scripts` 里的工具链也不在此列。

    Args:
        payload: `package.json` 解析结果。

    Returns:
        tuple[list[tuple[str, str]], list[str]]: (可校验条目, 失败项)。

    Raises:
        _FatalError: 依赖段存在但不是对象（解析塌陷）。
    """
    entries: list[tuple[str, str]] = []
    failures: list[str] = []
    for key in ("dependencies", "devDependencies"):
        block = payload.get(key)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise _FatalError(f"前端 package.json 的 {key} 不是对象（解析塌陷）")
        for raw_name, raw_spec in block.items():
            name = str(raw_name).strip().lower()
            spec = str(raw_spec).strip()
            if spec.startswith("workspace:"):
                continue  # monorepo 内部引用，不是外部依赖
            bad = next((item for item in FORBIDDEN_SPEC_PREFIXES if spec.startswith(item)), "")
            if bad:
                failures.append(f"{name}：依赖来源协议 {bad} 不可审计（只接受 registry 版本约束）")
                continue
            if spec.startswith("npm:"):
                name = normalize_frontend_spec(spec)[0]
            entries.append((name, spec))
    return entries, failures


def load_frontend_dependencies(
    package_json: Path,
) -> tuple[list[tuple[str, str]], list[str]]:
    """读取前端 `package.json` 的依赖清单。

    Args:
        package_json: `apps/web/package.json` 路径。

    Returns:
        tuple[list[tuple[str, str]], list[str]]: (可校验条目, 失败项)。

    Raises:
        _FatalError: 文件缺失、不是合法 JSON、或顶层不是对象。
    """
    if not package_json.is_file():
        raise _FatalError(f"前端 package.json 不存在：{package_json}")
    try:
        payload: dict[str, Any] = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _FatalError(f"前端 package.json 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise _FatalError("前端 package.json 顶层不是对象（解析塌陷）")
    return _frontend_entries(payload)


def check_frontend_documented(
    doc_text: str | None = None,
    payload: dict[str, Any] | None = None,
) -> list[str]:
    """校验前端依赖已在架构文档 §8.2 登记。

    真源是 §8.2 表格**本身**，脚本内**不**建前端包名常量：新增前端依赖必须先
    登记进架构文档，脚本自动跟随 —— 两处白名单必然漂移，一处不会。

    Args:
        doc_text: 架构文档全文（自检注入用；None 时读文件）。
        payload: 已解析的 package.json 内容（自检注入用；None 时读文件）。

    Returns:
        list[str]: 失败项描述。

    Raises:
        _FatalError: 文档缺 §8.2、或依赖条数低于下限（解析塌陷）。
    """
    text = doc_text
    if text is None:
        if not ARCHITECTURE_DOC.is_file():
            raise _FatalError(f"架构文档不存在：{ARCHITECTURE_DOC}")
        text = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    section = _architecture_section(text, "## 8.2 ")
    if not section:
        raise _FatalError("架构文档中未找到 §8.2 前端依赖清单小节")
    documented, prefixes = parse_doc_package_table(section)
    if payload is None:
        entries, failures = load_frontend_dependencies(WEB_PACKAGE_JSON)
        print(f"[INFO] 前端依赖 {len(entries)} 条（dependencies + devDependencies）")
    else:
        entries, failures = _frontend_entries(payload)
    if len(entries) < FRONTEND_DEPS_MIN:
        raise _FatalError(
            f"前端依赖仅解析出 {len(entries)} 条（下限 {FRONTEND_DEPS_MIN}）："
            "疑似解析塌陷，不能当成「全部合规」"
        )
    for name, spec in entries:
        if name in documented:
            continue
        if any(name.startswith(prefix) for prefix in prefixes):
            continue
        failures.append(f"{name}（{spec}）：未登记进架构文档 §8.2 前端依赖清单")
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
        return ["pip-audit 未安装，跳过漏洞扫描（CI 中应使用独立 venv 安装后执行）"]

    with tempfile.TemporaryDirectory() as tmp:
        req_file = Path(tmp) / "requirements.txt"
        req_file.write_text("\n".join(deps) + "\n", encoding="utf-8")
        # argv 全为常量（解释器路径 + 本函数自建的临时文件路径），不含任何外部输入，
        # 因此 S603 判据里的「不可信输入」前提不成立，按白名单豁免并注明理由。
        result = subprocess.run(  # noqa: S603 - argv 由常量构造，无外部输入
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
                f"{entry} —— 需做调用面适用性评估并记录结论（架构文档 §8.1.1；不接受二值结论）"
            )
    for note in accepted:
        print(f"[ACCEPTED] {note}")
    if blocking:
        if advisory:
            return [*blocking, f"另有 {len(advisory)} 项待评估：{'; '.join(advisory)}"]
        return blocking
    return advisory


def _self_test_fixture(count: int) -> tuple[str, dict[str, Any]]:
    """构造「条数达下限、且全部已登记」的合成样本。

    Args:
        count: 合成依赖条数。

    Returns:
        tuple[str, dict[str, Any]]: (文档文本, package.json 内容)。
    """
    names = [f"pkg-{index:02d}" for index in range(count)]
    rows = "\n".join(f"| {name} | ^1.0 | MIT | 合成样本 |" for name in names)
    doc = (
        "## 8.2 Node（合成样本）\n\n"
        "| 包 | 版本约束 | 协议 | 用途 |\n"
        "| --- | --- | --- | --- |\n"
        f"{rows}\n\n## 9. 其它\n"
    )
    return doc, {"dependencies": dict.fromkeys(names, "^1.0")}


def self_test() -> int:
    """自检：A–G 七条样本，全部内存注入、不读仓库文件。

    只证明「能过」的门禁等于没有门禁，所以每条都配一条**必须变红**的样本。

    Returns:
        int: 0 全部通过；1 有用例不通过。
    """
    problems: list[str] = []

    # A：全部已登记 → 0 失败；顺带证明 check_documented 的 doc_text 注入通道可用
    doc_a, payload_a = _self_test_fixture(FRONTEND_DEPS_MIN)
    failures = check_frontend_documented(doc_text=doc_a, payload=payload_a)
    if failures:
        problems.append(f"A 正样本误报 {len(failures)} 条：{failures[0]}")
    injected = "## 8.1 Python\n\n| 包 | 版本 |\n| --- | --- |\n| fastapi | ^0.1 |\n"
    if check_documented(["fastapi>=0.1"], doc_text=injected):
        problems.append("A2 已登记的 fastapi 在注入文档下仍报未登记")
    if not check_documented(["left-pad>=1.0"], doc_text=injected):
        problems.append("A2 未登记的 left-pad 未被 §8.1 判红")

    # B：多一个未登记的包 → 恰好命中该包
    doc_b, payload_b = _self_test_fixture(FRONTEND_DEPS_MIN)
    payload_b["dependencies"]["left-pad"] = "^1.0"
    if not any(
        "left-pad" in item for item in check_frontend_documented(doc_text=doc_b, payload=payload_b)
    ):
        problems.append("B 未登记包 left-pad 未被判红")

    # C：分组行（/ 与 +）与 `/*` 通配
    names_c = [f"pkg-{index:02d}" for index in range(FRONTEND_DEPS_MIN)]
    rows_c = "\n".join(f"| {name} | ^1.0 | MIT | 合成 |" for name in names_c)
    doc_c = (
        "## 8.2 Node\n\n| 包 | 版本约束 | 协议 | 用途 |\n| --- | --- | --- | --- |\n"
        "| react / react-dom | ^19.2 | MIT | UI |\n"
        "| @radix-ui/* | ^1.x | MIT | 基元 |\n"
        "| react-hook-form + @hookform/resolvers | ^7.5 | MIT | 表单 |\n"
        "| @tanstack/react-query | ^5.5 | MIT | 缓存 |\n"
        f"{rows_c}\n\n## 9. 其它\n"
    )
    payload_c: dict[str, Any] = {
        "dependencies": {
            "react": "^19.2.0",
            "react-dom": "^19.2.0",
            "@radix-ui/tooltip": "^1.1.0",
            "react-hook-form": "^7.5.0",
            "@hookform/resolvers": "^3.9.0",
            "@tanstack/react-query": "^5.5.0",
        },
        "devDependencies": dict.fromkeys(names_c, "^1.0"),
    }
    failures = check_frontend_documented(doc_text=doc_c, payload=payload_c)
    if failures:
        problems.append(f"C 分组/通配误判 {len(failures)} 条：{failures[0]}")
    documented_c, prefixes_c = parse_doc_package_table(_architecture_section(doc_c, "## 8.2 "))
    if "@tanstack/react-query" not in documented_c:
        problems.append("C 分组正则把 @tanstack/react-query 劈开了（零空格切分会复现）")
    if "@radix-ui/" not in prefixes_c:
        problems.append("C `@radix-ui/*` 未解析成通配前缀（先切分后判后缀会复现）")

    # D：非 registry 协议判红、workspace: 跳过、Python 侧 extras 归一
    doc_d, payload_d = _self_test_fixture(FRONTEND_DEPS_MIN)
    payload_d["devDependencies"] = {
        "file-dep": "file:../local",
        "link-dep": "link:../x",
        "portal-dep": "portal:../y",
        "git-dep": "git+https://example.com/a.git",
        "gh-dep": "github:owner/repo",
        "http-dep": "https://example.com/a.tgz",
        "ws-dep": "workspace:*",
    }
    hit = " ".join(check_frontend_documented(doc_text=doc_d, payload=payload_d))
    for label in ("file-dep", "link-dep", "portal-dep", "git-dep", "gh-dep", "http-dep"):
        if label not in hit:
            problems.append(f"D 协议违规 {label} 未被判红")
    if "ws-dep" in hit:
        problems.append("D workspace:* 被误判红（monorepo 内部引用应跳过）")
    if split_package("uvicorn[standard]>=0.30")[0] != "uvicorn":
        problems.append("D extras 未剥离（uvicorn[standard] 应归一为 uvicorn）")

    # E：`npm:` 别名按**真包名**校验，两个相反方向都要成立
    names_e = [f"pkg-{index:02d}" for index in range(FRONTEND_DEPS_MIN - 1)]
    rows_e = "\n".join(f"| {name} | ^1.0 | MIT | 合成 |" for name in names_e)
    head_e = "## 8.2 Node\n\n| 包 | 版本约束 | 协议 | 用途 |\n| --- | --- | --- | --- |\n"
    doc_e_ok = f"{head_e}| real-b | ^1.0 | MIT | 合成 |\n{rows_e}\n\n## 9. 其它\n"
    doc_e_bad = f"{head_e}| alias-x | ^1.0 | MIT | 合成 |\n{rows_e}\n\n## 9. 其它\n"
    payload_e: dict[str, Any] = {
        "dependencies": {**dict.fromkeys(names_e, "^1.0"), "alias-x": "npm:real-b@^1.0"}
    }
    if check_frontend_documented(doc_text=doc_e_ok, payload=payload_e):
        problems.append("E 登记真包名 real-b 时仍判红（别名未被解引用）")
    bad_e = check_frontend_documented(doc_text=doc_e_bad, payload=payload_e)
    if not any("real-b" in item for item in bad_e):
        problems.append("E 只登记别名 alias-x 时未判红（等于给别名留后门）")

    # F：条数低于下限 → fail-closed（解析塌陷不能当成「全部合规」）
    doc_f, payload_f = _self_test_fixture(3)
    try:
        check_frontend_documented(doc_text=doc_f, payload=payload_f)
        problems.append("F 条数低于下限时未 fail-closed（应抛 _FatalError）")
    except _FatalError:
        pass

    # G：文档缺 §8.2 → fail-closed
    try:
        check_frontend_documented(doc_text="## 8.1 Python\n", payload=payload_a)
        problems.append("G 文档缺 §8.2 时未 fail-closed")
    except _FatalError:
        pass

    if problems:
        for item in problems:
            print(f"[FAIL] {item}")
        print(f"\n[FAIL] 自检不通过（{len(problems)} 项）")
        return 1
    print(
        "\n[OK] 自检通过：A 正样本+注入 / B 未登记判红 / C 分组与通配 / "
        "D 协议与 extras / E 别名双向 / F 条数下限 / G 缺小节"
    )
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 参数列表（None 表示 sys.argv）。

    Returns:
        argparse.Namespace: 解析结果。
    """
    parser = argparse.ArgumentParser(description="适配层依赖门禁")
    parser.add_argument(
        "--vuln-check",
        action="store_true",
        help="追加 pip-audit 漏洞扫描（需已安装 pip-audit）",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="自检：A–G 七条样本证明门禁会红/会白/会 fail-closed",
    )
    return parser.parse_args(argv)


def main() -> int:
    """执行校验。

    Returns:
        int: 退出码（0 通过 / 1 不通过 / 2 无法判定）。
    """
    args = _parse_args()
    if args.self_test:
        return self_test()

    deps = load_dependencies()

    print("=" * 70)
    print("适配层依赖门禁（架构文档 §8.1.1 / §9.6）")
    print("=" * 70)
    print(f"[INFO] 运行时依赖 {len(deps)} 条")

    failures: list[str] = []
    failures += check_documented(deps)
    failures += check_min_safe_versions(deps)
    failures += check_hardening_rules()
    try:
        failures += check_frontend_documented()
    except _FatalError as exc:
        print(f"[FAIL] {exc}")
        print("\n[FAIL] 依赖门禁无法判定（前端依赖解析塌陷）→ exit 2")
        return 2
    if args.vuln_check:
        failures += run_pip_audit(deps)

    for failure in failures:
        print(f"[FAIL] {failure}")

    if failures:
        print(f"\n[FAIL] 依赖门禁不通过（{len(failures)} 项）")
        return 1

    print("\n[OK] 依赖门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
