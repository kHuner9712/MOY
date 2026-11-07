#!/usr/bin/env python3
"""工程规范强制门禁（阶段 2，2026-11-07 全仓审计）。

**定位**：`docs/engineering-standards.md` 的 S1–S7 把既有约束收敛成条款，附录 A.1
把"条款 → 门禁"映射成表。但条款本身仍可能**漂移**：基线豁免可以偷偷加行、新增
二级包可以绕过层矩阵、配置项可以只写进 `Settings` 却永远没人读、文档里的门禁
计数可以和磁盘上的文件对不上。本脚本把六类规范中**可用代码判定**的部分变成机械
约束，不重复已生效的 18 道门禁：

- **STD-1 分层与依赖方向**：两份 `*_baseline.txt` 的每条豁免必须带 `-- 审计编号`、
  `due_batch=Bn` 与非空消除计划；条数不得超过审计基线（方向 23 / 环 3），只许减不许增。
- **STD-2 命名与模块边界**：`apps/adapter/app/` 下实际二级包必须全部在
  `validate_import_directions.LAYERS` 矩阵内；新增包必须同步矩阵（S1.1-b）。
- **STD-3 测试强制**：① 测试/e2e 禁止 `.only(`（聚焦用例会让其余用例静默不跑）；
  ② 禁止无条件 `pytest.mark.skip(`；③ workflow 禁止任何 `ALLOW_SKIP` 开关；
  ④ **新增业务模块必须同 diff 带测试改动**（基线 `HEAD~1`，PR 合并提交下即整条 PR）。
- **STD-4 配置一致**：① `.env.example` / `.env.lite.example` 的每个 `OA_*` 必须解析到
  `Settings` 字段或基础设施白名单（带理由）；② 每个 `Settings` 字段必须在 `app/` 内
  有消费方，或登记为"代码默认值"白名单（带理由）；③ 安全敏感键必须在模板中显式出现。
- **STD-5 文档一致**：附录 A.1 的「20 个：19 `.py` + 1 `.sh`」声明必须与磁盘实际一致；
  附录 A.5 的**门禁清单**必须逐条覆盖全部 `deploy/validate_*`
  （名字 / 检查什么 / 红灯条件 / 维护者 四列齐全）。
- **STD-6 安全默认**：`mcp_allowed_hosts` 默认空串（出站默认拒绝）、
  `mcp_allow_loopback=false`、`mcp_enabled=false`、`ssrf_proxy_url` 默认空串；
  模板对应键保持同一安全默认。

用法::

    python deploy/validate_standards.py              # 体检真实仓库
    python deploy/validate_standards.py --self-test  # 负样本自检（期望退出码 1）

退出码：0 = 通过；1 = 发现违规；2 = 无法判定（输入缺失，fail-closed）。

**本仓库约定：`--self-test` 成功时退出码为 1**（同 `validate_gate_wiring.py`）。
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# 同目录导入：CI 用 `python ../../deploy/validate_standards.py` 运行，sys.path[0] 即 deploy/
from validate_import_directions import LAYERS

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
APP_ROOT: Final[Path] = REPO_ROOT / "apps" / "adapter" / "app"
CONFIG_PATH: Final[Path] = APP_ROOT / "core" / "config.py"
TESTS_ROOT: Final[Path] = REPO_ROOT / "apps" / "adapter" / "tests"
E2E_ROOT: Final[Path] = REPO_ROOT / "apps" / "web" / "e2e"
WORKFLOWS_DIR: Final[Path] = REPO_ROOT / ".github" / "workflows"
STANDARDS_DOC: Final[Path] = REPO_ROOT / "docs" / "engineering-standards.md"
DIRECTION_BASELINE: Final[Path] = REPO_ROOT / "deploy" / "import_direction_baseline.txt"
CYCLE_BASELINE: Final[Path] = REPO_ROOT / "deploy" / "same_layer_cycle_baseline.txt"
ENV_TEMPLATES: Final[tuple[Path, ...]] = (
    REPO_ROOT / "deploy" / ".env.example",
    REPO_ROOT / "deploy" / ".env.lite.example",
)

#: STD-1 的审计基线上限（2026-11-07 实测）；**只许减不许增**
DIRECTION_BASELINE_CAP: Final[int] = 23
CYCLE_BASELINE_CAP: Final[int] = 3
DUE_PATTERN: Final[re.Pattern[str]] = re.compile(r"due_batch=B([1-9][0-9]*)")
AUDIT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(P[0-9]-|R2-|H-)")

#: STD-3：新增这些目录下的模块必须同 diff 带测试改动
BUSINESS_MODULE_DIRS: Final[tuple[str, ...]] = (
    "apps/adapter/app/business/",
    "apps/adapter/app/services/",
    "apps/adapter/app/db/repositories/",
)

#: STD-4：基础设施键（由 compose 内各镜像 / 边缘脚本消费，不进 Settings）。
#: 值 = 理由；**新增键必须填理由**，否则 STD-4①判红。
INFRA_ENV_KEYS: Final[dict[str, str]] = {
    "OA_POSTGRES_USER": "postgres 镜像初始化参数（容器自身消费）",
    "OA_POSTGRES_PASSWORD": "postgres 镜像初始化参数；应用侧用 OA_DB_APP_PASSWORD",
    "OA_POSTGRES_DB": "postgres 镜像初始化参数",
    "OA_DB_APP_PASSWORD": "init SQL 为应用角色设口令，postgres 容器初始化时消费",
    "OA_VALKEY_PASSWORD": "valkey 容器启动参数（requirepass）",
    "OA_VALKEY_MAXMEMORY": "valkey 容器启动参数（maxmemory）",
    "OA_MILVUS_PORT": "milvus 容器端口映射右侧",
    "OA_MILVUS_METRICS_PORT": "milvus 容器指标端口映射右侧",
    "OA_OBJECT_STORE_ADMIN_PORT": "对象存储管理端口映射右侧",
    "OA_OBJECT_STORE_API_PORT": "对象存储 API 端口映射右侧",
    "OA_OBJECT_STORE_REGION": "对象存储镜像启动参数（region）",
    "OA_KEYCLOAK_ADMIN": "Keycloak 引导管理员（容器自身消费）",
    "OA_KEYCLOAK_ADMIN_PASSWORD": "Keycloak 引导管理员口令（容器自身消费）",
    "OA_KEYCLOAK_DB": "Keycloak 数据库名（容器自身消费）",
    "OA_DIFY_DB": "Dify 数据库名（底座容器自身消费）",
    "OA_DIFY_MODE": "底座部署模式（Dify 容器自身消费）",
    "OA_DIFY_SECRET_KEY": "底座 SECRET_KEY（Dify 容器自身消费）",
    "OA_DIFY_SANDBOX_KEY": "底座沙箱密钥（Dify 容器自身消费）",
    "OA_DIFY_S3_BUCKET": "底座对象桶名（Dify 容器自身消费）",
    "OA_DEEPSEEK_API_KEY": "模型供应商密钥，由底座容器注入，不经适配层",
    "OA_PLUGIN_DAEMON_KEY": "插件守护进程密钥（底座容器自身消费）",
    "OA_PLUGIN_INNER_API_KEY": "插件内部 API 密钥（底座容器自身消费）",
    "OA_LICENSE_KEY": "许可证注入到底座，不经适配层",
    "OA_LICENSE_SEAT_COUNT": "许可证席位，注入到底座",
    "OA_IMAGE_TAG": "三套编排共用的镜像标签变量（compose 插值）",
    "OA_ADAPTER_PORT": "适配层端口映射右侧（应用读 Settings.port）",
    "OA_WEB_PORT": "前端端口映射右侧（Next.js 自身消费）",
    "OA_PROMETHEUS_PORT": "Prometheus 端口映射右侧",
    "OA_ALERTMANAGER_PORT": "Alertmanager 端口映射右侧（H-01）",
    "OA_ALERT_WEBHOOK_URL": "Alertmanager 入口脚本渲染（H-01）",
    "OA_WEB_OIDC_ISSUER": "前端 OIDC issuer 构建参数（Dockerfile ARG）",
    "OA_WEB_OIDC_CLIENT_ID": "前端 OIDC client_id 构建参数（Dockerfile ARG）",
    "OA_WEB_ADAPTER_BASE_URL": "前端适配层地址构建参数（Dockerfile ARG）",
    "OA_LITE_PUBLIC_URL": "轻量版站点唯一入口；install.sh 由它渲染 OA_PUBLIC_BASE_URL / issuer",
    "OA_LITE_PROXY_PORT": "轻量版边缘代理端口映射右侧",
    "OA_LITE_PROXY_TLS_PORT": "轻量版边缘 TLS 端口映射右侧",
    "OA_LITE_DIFY_WEB_PORT": "轻量版底座 Web 端口映射右侧",
}

#: STD-4②：`Settings` 字段无 app 内消费方，但属于「代码默认值」的登记。
#: 值 = (理由, 到期批次)；新增条目必须两列都填。
CODE_ONLY_FIELDS: Final[dict[str, tuple[str, str]]] = {
    "milvus_uri": ("向量库地址未接入（检索走底座），留待 B 阶段接线", "B30"),
    "object_store_endpoint": ("对象存储未接自建实现，留待 B 阶段接线", "B30"),
    "object_store_access_key": ("同上：对象存储未接自建实现", "B30"),
    "object_store_secret_key": ("同上：对象存储未接自建实现", "B30"),
    "object_store_bucket": ("同上：对象存储未接自建实现", "B30"),
    "knowledge_base_cache_ttl_seconds": ("知识库缓存未启用，随 B05 接线", "B30"),
    "pay_cert_serial": ("支付网关证书序列号，支付渠道未启用", "B30"),
    "pay_private_key_path": ("支付网关私钥路径，支付渠道未启用", "B30"),
    "memory_embedding_dim": ("记忆向量维度未接线（记忆走底座）", "B30"),
    "memory_embedding_timeout_seconds": ("同上：记忆向量化未接线", "B30"),
    "memory_embedding_batch_size": ("同上：记忆向量化未接线", "B30"),
}

#: STD-4③：安全敏感键必须按模板出现（显式可配置）。
#: `.env.lite.example` 用 `OA_LITE_PUBLIC_URL` 由 `install.sh` 渲染站点键，故不重复
#: `OA_PUBLIC_BASE_URL` / JWT / AUTH；但 MCP 出站与 SSRF 代理必须显式关闭。
SECURITY_TEMPLATE_KEYS: Final[dict[str, tuple[str, ...]]] = {
    ".env.example": (
        "OA_AUTH_MODE",
        "OA_JWT_ALGORITHM",
        "OA_JWT_ISSUER",
        "OA_PUBLIC_BASE_URL",
        "OA_MCP_ENABLED",
        "OA_MCP_ALLOWED_HOSTS",
        "OA_MCP_ALLOW_LOOPBACK",
        "OA_SSRF_PROXY_URL",
    ),
    ".env.lite.example": (
        "OA_MCP_ENABLED",
        "OA_MCP_ALLOWED_HOSTS",
        "OA_MCP_ALLOW_LOOPBACK",
        "OA_SSRF_PROXY_URL",
    ),
}

#: STD-3：允许出现 `pytest.mark.skip(` 的文件（带理由）。PG 标记门控本身是
#: S6.1/S6.3 的实现：未加 `--run-pg` 时跳过、CI 带 `--run-pg` 且断言 skipped=0。
SKIP_ALLOWED_FILES: Final[dict[str, str]] = {
    "apps/adapter/tests/conftest.py": "PG 标记门控（--run-pg 时实际执行，S6.3 断言 skipped=0）",
}

#: STD-6：字段 → 期望默认值（源码字面量）；空串即 fail-closed
SECURITY_DEFAULTS: Final[dict[str, tuple[str, ...]]] = {
    "mcp_allowed_hosts": ('""', "''"),
    "mcp_allow_loopback": ("False",),
    "mcp_enabled": ("False",),
    "ssrf_proxy_url": ('""', "''"),
}

#: STD-6：模板里这些键的默认值必须与代码默认一致（空 = 拒绝 / false = 关闭）
TEMPLATE_SECURITY_DEFAULTS: Final[dict[str, set[str]]] = {
    "OA_MCP_ALLOWED_HOSTS": {"", '""', "''"},
    "OA_MCP_ALLOW_LOOPBACK": {"false", "False", "0"},
    "OA_MCP_ENABLED": {"false", "False", "0"},
}


@dataclass(frozen=True)
class Problem:
    """一条违规。

    Attributes:
        code: STD-1 … STD-6 编号。
        message: 人读描述（含文件与定位）。
    """

    code: str
    message: str


def _read(path: Path) -> str:
    """读文本（缺失返回空串，由调用方判定 fail-closed）。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# STD-1：基线豁免必须带审计编号、到期批次与消除计划
# ---------------------------------------------------------------------------


def check_baseline(
    text: str,
    code: str,
    cap: int,
    label: str,
) -> list[Problem]:
    """校验一份豁免基线的"理由 + 到期"完备性。

    Args:
        text: 基线文件全文。
        code: 违规编号（STD-1）。
        cap: 审计基线允许的最大条数（只许减不许增）。
        label: 文件名（用于报错定位）。

    Returns:
        list[Problem]: 违规清单。
    """
    problems: list[Problem] = []
    entries = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not entries:
        return [Problem(code, f"{label} 没有可判定的条目（文件缺失或全为注释）")]
    if len(entries) > cap:
        problems.append(
            Problem(code, f"{label} 登记 {len(entries)} 条，超过审计基线上限 {cap}（只许减不许增）")
        )
    for index, entry in enumerate(entries, start=1):
        if " -- " not in entry:
            problems.append(
                Problem(
                    code, f"{label} 第 {index} 条缺少 `-- <审计编号> due_batch=<Bn> <消除计划>`"
                )
            )
            continue
        head, tail = entry.split(" -- ", 1)
        audit_id = tail.split(" ", 1)[0]
        if not AUDIT_ID_PATTERN.match(audit_id):
            problems.append(Problem(code, f"{label} 第 {index} 条审计编号不合法：`{audit_id}`"))
        if not DUE_PATTERN.search(tail):
            problems.append(
                Problem(code, f"{label} 第 {index} 条缺少 due_batch=<Bn>（不允许无期限豁免）")
            )
        plan = DUE_PATTERN.sub("", tail).split(" ", 1)
        if len(plan) < 2 or not plan[1].strip():
            problems.append(Problem(code, f"{label} 第 {index} 条缺少消除计划（到期条件）"))
        if not head.strip():
            problems.append(Problem(code, f"{label} 第 {index} 条缺少豁免主体（规则/文件/模块）"))
    return problems


# ---------------------------------------------------------------------------
# STD-2：二级包必须在层矩阵内
# ---------------------------------------------------------------------------


def check_layer_packages(actual: Sequence[str], declared: Sequence[str]) -> list[Problem]:
    """校验 `app/` 二级包全部在层矩阵声明内。

    Args:
        actual: 实际二级包名。
        declared: 层矩阵声明的包名。

    Returns:
        list[Problem]: 违规清单。
    """
    allowed = set(declared)
    unknown = sorted(set(actual) - allowed)
    return [
        Problem(
            "STD-2",
            f"`app/{name}/` 不在层矩阵 LAYERS 内：新增二级包必须同步 "
            "`deploy/validate_import_directions.py` 的 LAYERS 与依赖方向矩阵（S1.1-b）",
        )
        for name in unknown
    ]


# ---------------------------------------------------------------------------
# STD-3：测试强制（禁止 skip 掩盖 / 新增业务模块带用例）
# ---------------------------------------------------------------------------


def check_test_discipline(
    test_sources: Mapping[str, str],
    workflow_sources: Mapping[str, str],
    added_paths: Sequence[str],
    changed_paths: Sequence[str],
) -> list[Problem]:
    """校验测试纪律。

    Args:
        test_sources: 测试/e2e 文件 → 文本。
        workflow_sources: workflow 文件 → 文本。
        added_paths: 本次 diff 新增的文件（仓库相对路径）。
        changed_paths: 本次 diff 修改/新增的文件。

    Returns:
        list[Problem]: 违规清单。
    """
    problems: list[Problem] = []
    for name, text in sorted(test_sources.items()):
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ".only(" in line:
                problems.append(
                    Problem("STD-3", f"{name}:{line_no} 使用 `.only(`，其余用例会静默不跑")
                )
            if (
                "pytest.mark.skip(" in line
                and "skipif" not in line
                and name not in SKIP_ALLOWED_FILES
            ):
                problems.append(
                    Problem(
                        "STD-3",
                        f"{name}:{line_no} 无条件 `pytest.mark.skip(`（禁止用 skip 掩盖失败）",
                    )
                )

    for name, text in sorted(workflow_sources.items()):
        code_only = [line for line in text.splitlines() if not line.strip().startswith("#")]
        if any("ALLOW_SKIP" in line for line in code_only):
            problems.append(
                Problem("STD-3", f"{name} 出现 `ALLOW_SKIP` 开关：主链路不允许永久跳过（H-04）")
            )

    touched_tests = [path for path in changed_paths if path.startswith("apps/adapter/tests/")]
    for path in sorted(added_paths):
        if not any(path.startswith(prefix) for prefix in BUSINESS_MODULE_DIRS):
            continue
        if path.rsplit("/", 1)[-1] == "__init__.py":
            continue
        if not touched_tests:
            problems.append(
                Problem(
                    "STD-3",
                    f"新增业务模块 `{path}` 未带任何测试改动：新增业务模块零测试 = 红灯（S6.1）",
                )
            )
    return problems


# ---------------------------------------------------------------------------
# STD-4：配置一致（模板键 ↔ Settings ↔ 消费方）
# ---------------------------------------------------------------------------


def _env_keys(text: str) -> list[str]:
    """提取模板中的 OA_* 键（保持出现顺序）。"""
    keys: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("OA_") and "=" in stripped:
            keys.append(stripped.split("=", 1)[0])
    return keys


def _template_values(text: str) -> dict[str, str]:
    """提取模板 key=value（去引号与行内注释）。"""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("OA_") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        value = raw.split(" #", 1)[0].strip()
        values[key] = value
    return values


def parse_settings_fields(text: str) -> dict[str, str]:
    """解析 `Settings` 的字段名 → 默认值字面量。

    Args:
        text: `app/core/config.py` 源码。

    Returns:
        dict[str, str]: 字段名 → 默认值源码文本（`<required>` 表示必填）。
    """
    tree = ast.parse(text)
    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    fields: dict[str, str] = {}
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        value = node.value
        if value is None:
            fields[node.target.id] = "<required>"
        elif isinstance(value, ast.Call) and getattr(value.func, "id", "") == "Field":
            default_kw = next((kw for kw in value.keywords if kw.arg == "default"), None)
            fields[node.target.id] = ast.unparse(default_kw.value) if default_kw else "<required>"
        else:
            fields[node.target.id] = ast.unparse(value)
    return fields


def check_config_consistency(
    settings_defaults: Mapping[str, str],
    template_texts: Mapping[str, str],
    app_sources: Mapping[str, str],
    config_text: str,
    infra_keys: Mapping[str, str],
    code_only: Mapping[str, tuple[str, str]],
    security_keys: Mapping[str, Sequence[str]],
) -> list[Problem]:
    """校验配置三方一致（模板键 / Settings 字段 / 应用消费方）。

    Args:
        settings_defaults: Settings 字段 → 默认值。
        template_texts: 模板文件名 → 全文。
        app_sources: `app/` 下（除 config.py）文件 → 全文。
        config_text: `config.py` 全文（内部 `self.<field>` 消费也算消费方）。
        infra_keys: 基础设施键白名单（键 → 理由）。
        code_only: 代码默认值字段白名单（字段 → (理由, 到期批次)）。
        security_keys: 模板文件 → 必须出现的安全敏感键。

    Returns:
        list[Problem]: 违规清单。
    """
    problems: list[Problem] = []
    known = set(settings_defaults)

    for template, text in sorted(template_texts.items()):
        if not text.strip():
            problems.append(Problem("STD-4", f"{template} 缺失或为空（配置模板是键集合真源）"))
            continue
        for key in _env_keys(text):
            field = key.removeprefix("OA_").lower()
            if field in known:
                continue
            if infra_keys.get(key):
                continue
            problems.append(
                Problem(
                    "STD-4",
                    f"{template} 的 `{key}` 既不是 Settings 字段，也未在 INFRA_ENV_KEYS 登记理由"
                    "（每环境变量必须有消费方与文档归属）",
                )
            )

    combined = "\n".join(app_sources.values()) + "\n" + config_text
    for field in sorted(settings_defaults):
        if field in code_only:
            reason, due = code_only[field]
            if not reason.strip() or not due.strip():
                problems.append(Problem("STD-4", f"CODE_ONLY_FIELDS[{field}] 缺理由或到期批次"))
            continue
        if not re.search(rf"\.{re.escape(field)}\b", combined):
            problems.append(
                Problem(
                    "STD-4",
                    f"Settings.{field} 在 app/ 内无消费方（死配置），且未登记到 CODE_ONLY_FIELDS",
                )
            )

    for template, text in sorted(template_texts.items()):
        keys = set(_env_keys(text))
        for key in security_keys.get(template, ()):
            if key not in keys:
                problems.append(
                    Problem("STD-4", f"{template} 缺安全敏感键 `{key}`（安全默认必须显式可配置）")
                )
    return problems


# ---------------------------------------------------------------------------
# STD-5：文档一致（门禁计数与清单）
# ---------------------------------------------------------------------------

COUNT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"### A\.1[^\n]*（(\d+) 个：(\d+) `\.py` \+ (\d+) `\.sh`）"
)
INVENTORY_ROW: Final[re.Pattern[str]] = re.compile(
    r"^\| \d+ \| `(deploy/validate_[^`]+)` \|([^|]*)\|([^|]*)\|([^|]*)\|"
)


def check_doc_consistency(
    standards_text: str, actual_gates: Sequence[str], doc_name: str
) -> list[Problem]:
    """校验文档声明的门禁计数与清单是否与磁盘一致。

    Args:
        standards_text: 规范文档全文。
        actual_gates: 实际门禁文件（`deploy/validate_*`）。
        doc_name: 文档名（报错定位）。

    Returns:
        list[Problem]: 违规清单。
    """
    problems: list[Problem] = []
    match = COUNT_PATTERN.search(standards_text)
    if match is None:
        return [Problem("STD-5", f"{doc_name} 缺附录 A.1 的计数声明（`（N 个：M .py + K .sh）`）")]
    total, py_count, sh_count = (int(item) for item in match.groups())
    actual_py = sum(1 for gate in actual_gates if gate.endswith(".py"))
    actual_sh = sum(1 for gate in actual_gates if gate.endswith(".sh"))
    if (total, py_count, sh_count) != (len(actual_gates), actual_py, actual_sh):
        problems.append(
            Problem(
                "STD-5",
                f"{doc_name} 声明 {total} 个（{py_count} .py + {sh_count} .sh），"
                f"磁盘实际 {len(actual_gates)} 个（{actual_py} .py + {actual_sh} .sh）",
            )
        )

    rows = []
    for line in standards_text.splitlines():
        match = INVENTORY_ROW.match(line)
        if match:
            rows.append((match.group(1), match.group(2), match.group(3), match.group(4)))
    listed = [row[0].removeprefix("deploy/") for row in rows]
    missing = sorted(set(actual_gates) - set(listed))
    extra = sorted(set(listed) - set(actual_gates))
    for gate in missing:
        problems.append(Problem("STD-5", f"附录 A.5 门禁清单缺 `deploy/{gate}` 一行"))
    for gate in extra:
        problems.append(Problem("STD-5", f"附录 A.5 门禁清单多余 `deploy/{gate}`（磁盘不存在）"))
    for gate, what, red, maintainer in rows:
        for value, label in ((what, "检查什么"), (red, "红灯条件"), (maintainer, "维护者")):
            if not value.strip():
                problems.append(Problem("STD-5", f"附录 A.5 `{gate}` 的「{label}」列为空"))
    return problems


# ---------------------------------------------------------------------------
# STD-6：安全默认
# ---------------------------------------------------------------------------


def check_security_defaults(
    settings_defaults: Mapping[str, str], template_texts: Mapping[str, str]
) -> list[Problem]:
    """校验安全相关默认值（出站默认拒绝 / 功能默认关闭）。

    Args:
        settings_defaults: Settings 字段 → 默认值源码文本。
        template_texts: 模板文件名 → 全文。

    Returns:
        list[Problem]: 违规清单。
    """
    problems: list[Problem] = []
    for field, allowed in SECURITY_DEFAULTS.items():
        actual = settings_defaults.get(field, "<missing>")
        if actual not in allowed:
            problems.append(
                Problem(
                    "STD-6",
                    f"Settings.{field} 默认值为 `{actual}`，要求 {allowed}（默认关闭）",
                )
            )
    for template, text in sorted(template_texts.items()):
        values = _template_values(text)
        for key, allowed in TEMPLATE_SECURITY_DEFAULTS.items():
            actual = values.get(key)
            if actual is None:
                problems.append(Problem("STD-6", f"{template} 缺 `{key}`（安全默认必须可复核）"))
            elif actual not in allowed:
                problems.append(
                    Problem(
                        "STD-6",
                        f"{template} 的 `{key}` 默认值为 `{actual}`，要求 {sorted(allowed)}",
                    )
                )
    return problems


def _list_app_packages(app_root: Path) -> list[str]:
    """列出 `app/` 下的二级包名。"""
    if not app_root.is_dir():
        return []
    return sorted(
        path.name
        for path in app_root.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )


def _list_gates() -> list[str]:
    """列出 `deploy/validate_*` 门禁文件名。"""
    deploy = REPO_ROOT / "deploy"
    return sorted(
        path.name for path in deploy.glob("validate_*") if path.name.endswith((".py", ".sh"))
    )


def _diff_paths() -> tuple[list[str], list[str]]:
    """取 `HEAD~1..HEAD` 的新增与变更文件清单。

    Returns:
        tuple[list[str], list[str]]: (新增, 变更+新增)；git 不可用时均为空。
    """
    git = shutil.which("git")
    if git is None:
        return [], []

    def run(args: Sequence[str]) -> list[str]:
        try:
            completed = subprocess.run(  # noqa: S603 - 固定 argv，无外部输入
                [git, *args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

    changed = run(["diff", "--name-only", "--diff-filter=ACMR", "HEAD~1", "HEAD"])
    added = run(["diff", "--name-only", "--diff-filter=A", "HEAD~1", "HEAD"])
    return added, changed


def _test_sources() -> dict[str, str]:
    """收集测试与 e2e 源文件。

    Returns:
        dict[str, str]: 路径 → 全文。
    """
    sources: dict[str, str] = {}
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        sources[path.relative_to(REPO_ROOT).as_posix()] = _read(path)
    for path in sorted(E2E_ROOT.rglob("*.ts")):
        sources[path.relative_to(REPO_ROOT).as_posix()] = _read(path)
    return sources


def evaluate(
    direction_text: str,
    cycle_text: str,
    actual_packages: Sequence[str],
    test_sources: Mapping[str, str],
    workflow_sources: Mapping[str, str],
    added_paths: Sequence[str],
    changed_paths: Sequence[str],
    config_text: str,
    template_texts: Mapping[str, str],
    app_sources: Mapping[str, str],
    standards_text: str,
    actual_gates: Sequence[str],
    checks: Sequence[str] | None = None,
) -> tuple[int, list[Problem]]:
    """汇总六类判据（纯函数，便于自检注入样本）。

    Args:
        checks: 只跑指定编号（如 `("STD-3",)`）；`None` = 全部六类。前端 CI 用
            `--checks STD-3` 跑同一份判据，避免"前端约束只接在 adapter 侧"漏一半。

    Returns:
        tuple[int, list[Problem]]: (退出码, 违规清单)。
    """
    selected = set(checks or ("STD-1", "STD-2", "STD-3", "STD-4", "STD-5", "STD-6"))
    if ("STD-4" in selected or "STD-6" in selected) and not config_text:
        return 2, [Problem("STD-0", "config.py 缺失，无法判定配置与安全默认（fail-closed）")]
    if "STD-5" in selected and not standards_text:
        return 2, [Problem("STD-0", "规范文档缺失，无法判定文档一致（fail-closed）")]

    problems: list[Problem] = []
    if "STD-1" in selected:
        problems.extend(
            check_baseline(
                direction_text, "STD-1", DIRECTION_BASELINE_CAP, "import_direction_baseline.txt"
            )
        )
        problems.extend(
            check_baseline(cycle_text, "STD-1", CYCLE_BASELINE_CAP, "same_layer_cycle_baseline.txt")
        )
    if "STD-2" in selected:
        problems.extend(check_layer_packages(actual_packages, LAYERS))
    if "STD-3" in selected:
        problems.extend(
            check_test_discipline(test_sources, workflow_sources, added_paths, changed_paths)
        )
    if "STD-4" in selected:
        problems.extend(
            check_config_consistency(
                parse_settings_fields(config_text),
                template_texts,
                app_sources,
                config_text,
                INFRA_ENV_KEYS,
                CODE_ONLY_FIELDS,
                SECURITY_TEMPLATE_KEYS,
            )
        )
    if "STD-5" in selected:
        problems.extend(check_doc_consistency(standards_text, actual_gates, STANDARDS_DOC.name))
    if "STD-6" in selected:
        problems.extend(check_security_defaults(parse_settings_fields(config_text), template_texts))
    return (1 if problems else 0), problems


def run_self_test() -> int:
    """负样本自检：六类判据都必须能被样本打红。

    Returns:
        int: 1 = 自检通过；0 = 有样本不符。
    """
    good_direction = (
        "# 注释\nR-API-DB apps/x.py app.db.repositories.x module -- P1-3 due_batch=B27 "
        "api 直接使用仓储（绕过 services），改为 services 暴露查询入口后消除\n"
    )
    bad_direction = "R-API-DB apps/x.py app.db.repositories.x module -- P1-3 消除计划写在注释里\n"
    good_cycle = "CYCLE apps/a.py apps/b.py -- R2-ARC-07 due_batch=B25 查询对方状态，改事件解耦\n"
    bad_cycle = "CYCLE apps/a.py apps/b.py -- 无编号\n"

    config_text = (
        "class Settings:\n"
        '    mcp_allowed_hosts: str = ""\n'
        "    mcp_allow_loopback: bool = False\n"
        "    mcp_enabled: bool = False\n"
        '    ssrf_proxy_url: str = ""\n'
        '    jwt_algorithm: str = "HS256"\n'
        '    jwt_issuer: str = ""\n'
        '    public_base_url: str = ""\n'
        '    auth_mode: str = "stub"\n'
    )
    templates = {
        ".env.example": (
            "OA_AUTH_MODE=stub\nOA_JWT_ALGORITHM=RS256\nOA_JWT_ISSUER=http://x\n"
            "OA_PUBLIC_BASE_URL=http://x\nOA_MCP_ENABLED=false\nOA_MCP_ALLOWED_HOSTS=\n"
            "OA_MCP_ALLOW_LOOPBACK=false\nOA_SSRF_PROXY_URL=\nOA_POSTGRES_USER=u\n"
        )
    }
    app_sources = {
        "app/main.py": (
            "settings.auth_mode settings.jwt_algorithm settings.mcp_allowed_hosts "
            "settings.mcp_allow_loopback settings.mcp_enabled settings.ssrf_proxy_url "
            "settings.jwt_issuer settings.public_base_url\n"
        )
    }

    standards_text = (
        "### A.1 `deploy/validate_*`（2 个：1 `.py` + 1 `.sh`）\n"
        "| 1 | `deploy/validate_x.py` | 检查什么 | 红灯条件 | 维护者 |\n"
        "| 2 | `deploy/validate_y.sh` | 检查 | 红 | 维护者 |\n"
    )

    cases: list[tuple[str, bool, list[Problem]]] = []

    def one_case(title: str, result: tuple[int, list[Problem]], expect: int, needle: str) -> None:
        code, problems = result
        if expect == 0:
            # 正样本：必须 exit 0 且**零违规**（空关键词查不到任何消息，不能用关键词判）
            ok = code == 0 and not problems
        else:
            ok = code == expect and any(needle in item.message for item in problems)
        cases.append((title, ok, problems))

    one_case(
        "STD-1 正样本：豁免带编号/到期/计划 → 通过",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        0,
        "",
    )
    one_case(
        "STD-1 负样本：缺 due_batch → 红",
        evaluate(
            bad_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "due_batch",
    )
    one_case(
        "STD-1 负样本：环基线缺审计编号 → 红",
        evaluate(
            good_direction,
            bad_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "审计编号",
    )
    one_case(
        "STD-2 负样本：新增二级包未进层矩阵 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api", "shadow"],
            {},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "shadow",
    )
    one_case(
        "STD-3 负样本：`.only(` 静默略过其余用例 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {"t.py": "it.only('x', () => {})"},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        ".only(",
    )
    one_case(
        "STD-3 负样本：无条件 pytest.mark.skip → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {"t.py": "@pytest.mark.skip(reason='x')"},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "pytest.mark.skip",
    )
    one_case(
        "STD-3 负样本：ALLOW_SKIP 开关 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {"w.yml": "OA_E2E_ALLOW_SKIP: 1"},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "ALLOW_SKIP",
    )
    one_case(
        "STD-3 负样本：新增业务模块零测试 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            ["apps/adapter/app/business/new_x.py"],
            [],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "未带任何测试改动",
    )
    one_case(
        "STD-3 正样本：新增业务模块 + 同 diff 测试改动 → 通过",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            ["apps/adapter/app/business/new_x.py"],
            ["apps/adapter/tests/unit/test_new_x.py"],
            config_text,
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        0,
        "",
    )
    one_case(
        "STD-4 负样本：模板键既非 Settings 字段也无理由 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text,
            {".env.example": templates[".env.example"] + "OA_MYSTERY_KEY=x\n"},
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "OA_MYSTERY_KEY",
    )
    one_case(
        "STD-4 负样本：Settings 字段无消费方 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text + '    dead_config: str = "x"\n',
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "dead_config",
    )
    one_case(
        "STD-5 负样本：文档计数与磁盘不一致 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text.replace("2 个：1", "3 个：1"),
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "磁盘实际",
    )
    one_case(
        "STD-5 负样本：清单缺行 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text,
            templates,
            app_sources,
            standards_text.replace("| 2 | `deploy/validate_y.sh` | 检查 | 红 | 维护者 |\n", ""),
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "缺 `deploy/validate_y.sh`",
    )
    one_case(
        "STD-6 负样本：出站白名单默认通配 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text.replace('mcp_allowed_hosts: str = ""', 'mcp_allowed_hosts: str = "*"'),
            templates,
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "mcp_allowed_hosts",
    )
    one_case(
        "STD-6 负样本：模板把回环开关默认打开 → 红",
        evaluate(
            good_direction,
            good_cycle,
            ["api"],
            {},
            {},
            [],
            [],
            config_text,
            {
                ".env.example": templates[".env.example"].replace(
                    "OA_MCP_ALLOW_LOOPBACK=false", "OA_MCP_ALLOW_LOOPBACK=true"
                )
            },
            app_sources,
            standards_text,
            ["validate_x.py", "validate_y.sh"],
        ),
        1,
        "OA_MCP_ALLOW_LOOPBACK",
    )

    failed = [title for title, ok, _ in cases if not ok]
    for title, ok, problems in cases:
        print(f"[{'OK' if ok else 'FAIL'}]   {title}")
        if not ok:
            # 失败时打印样本内部的问题清单：让"样本写错了"与"判据写错了"可区分
            for problem in problems:
                print(f"         ↳ {problem.code}: {problem.message}")
    if failed:
        print(f"[FAIL] 规范门禁自检失败：{len(failed)}/{len(cases)} 条样本不符（退出码 0）")
        return 0
    print(f"[OK]   规范门禁自检通过：{len(cases)} 条样本全部符合（退出码 1 为预期）")
    return 1


def main(argv: list[str] | None = None) -> int:
    """入口。

    Args:
        argv: 命令行参数（默认取 `sys.argv`）。

    Returns:
        int: 0=通过；1=违规；2=无法判定。
    """
    parser = argparse.ArgumentParser(description="工程规范强制门禁（阶段 2）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检（成功时退出码 1）")
    parser.add_argument(
        "--checks",
        default="",
        help="只跑指定编号（逗号分隔，如 STD-3）；默认全部六类",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    selected = tuple(item.strip() for item in args.checks.split(",") if item.strip()) or None

    app_sources = {
        path.relative_to(REPO_ROOT).as_posix(): _read(path)
        for path in sorted(APP_ROOT.rglob("*.py"))
        if path != CONFIG_PATH
    }
    added, changed = _diff_paths()
    code, problems = evaluate(
        _read(DIRECTION_BASELINE),
        _read(CYCLE_BASELINE),
        _list_app_packages(APP_ROOT),
        _test_sources(),
        {path.name: _read(path) for path in sorted(WORKFLOWS_DIR.glob("*.yml"))},
        added,
        changed,
        _read(CONFIG_PATH),
        {path.name: _read(path) for path in ENV_TEMPLATES},
        app_sources,
        _read(STANDARDS_DOC),
        _list_gates(),
        selected,
    )

    for problem in problems:
        print(f"[FAIL] {problem.code}: {problem.message}")
    if code == 2:
        return 2
    if code == 1:
        print(f"[FAIL] 规范门禁未通过：{len(problems)} 处违规（docs/engineering-standards.md S8）")
        return 1
    scope = "+".join(selected) if selected else "STD-1..STD-6"
    print(f"[OK]   规范门禁通过：{scope}（docs/engineering-standards.md S8）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
