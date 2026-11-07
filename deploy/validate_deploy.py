#!/usr/bin/env python3
"""部署形态一致性门禁（阶段四 B28，新增 G-34）。

**为什么需要它**（两起真实事故的共同形态）：

1. `docker-compose.saas.yml` 的 `OA_DATABASE_URL` 一直写死 `${OA_POSTGRES_USER}`
   ——库属主/超级用户，`FORCE ROW LEVEL SECURITY` 也约束不住。于是「SaaS 多租户」
   这个最需要隔离的形态，RLS 反而全程被绕过（OA-REVIEW-002 / S6）。
   私有化编排已改成应用角色，但**改一处不等于改完**：两份编排是各自维护的。
2. 两份编排的 initdb 挂载集合不等价：SaaS 多挂了历史文件 `10-init-rls.sql`，
   它的旧策略与阶段二的 `p_tenant_isolation` 同时存在。库结构因此按部署形态分叉，
   后续排障与合规核查都要先问「你用的是哪套 compose」（M16）。

判据（三条，全部是「配置事实」而非「代码意图」）：

- R1：adapter 的 `OA_DATABASE_URL` 不得引用 `${OA_POSTGRES_USER`，也不得内联
  字面超级用户 DSN；判据覆盖 full / lite / saas **三套**编排；
- R2：adapter 的 `environment` 必须显式声明 `OA_ENV` 与 `OA_AUTH_MODE`
  （依赖代码默认值 = 不配就是 stub = 不验签，S1）；
- R3：各编排的 initdb 挂载目标文件名集合必须一致（避免库结构分叉）。

R1/R2/R3 的扫描集合见 `COMPOSE_FILES`。**新增编排形态必须同步该集合**，
否则门禁会像 A-01 那样对新形态恒绿；自检会显式验证 lite 的 R1 负样本被拦截。

用法::

    python deploy/validate_deploy.py                 # 扫描（CI 默认）
    python deploy/validate_deploy.py --self-test     # 负样本自检（期望退出码 1）
    python deploy/validate_deploy.py --json          # 机器可读输出

退出码：0 = 通过；1 = 发现违规或自检命中；2 = 门禁自身异常（目录/依赖缺失）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Final

try:  # pragma: no cover - 缺依赖属于环境问题，给出可操作提示而不是 traceback
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    print("[FATAL] 缺少 pyyaml：请先执行 `pip install pyyaml`", file=sys.stderr)
    raise SystemExit(2) from None

#: 仓库根目录
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

#: 部署目录
DEPLOY_DIR: Final[Path] = REPO_ROOT / "deploy"

#: 参与 R1/R2/R3 比对的编排文件（顺序稳定，便于报告）。
#:
#: **三套交付形态必须全覆盖**：2026-11-06 全仓审计 A-01 的根因不是"没有门禁"，
#: 而是本集合只列了 full / saas，把真正的轻量版主交付形态 lite 排除在外——
#: 门禁对 lite 恒绿，而 lite 的 adapter 内联了 initdb 超级用户 DSN，
#: 导致 /readyz 恒 not_ready、proxy 永不启动。新增编排形态时必须同步本集合。
COMPOSE_FILES: Final[tuple[str, ...]] = (
    "docker-compose.yml",
    "docker-compose.lite.yml",
    "docker-compose.saas.yml",
)

#: adapter 服务名
ADAPTER_SERVICE: Final[str] = "adapter"

#: 数据库服务名
DATABASE_SERVICE: Final[str] = "postgres"

#: initdb 挂载目标目录
INITDB_DIR: Final[str] = "/docker-entrypoint-initdb.d/"

#: 超级用户变量（出现即违规）
SUPERUSER_VAR: Final[str] = "${OA_POSTGRES_USER"

#: 内联的**字面**超级用户 DSN（`://<name>:` 后跟口令），同样违规。
#:
#: 只查变量名不够：把 `${OA_POSTGRES_USER}` 写成字面 `officeai` 一样能绕过门禁。
#: `officeai` 是 `.env.example` / `.env.lite.example` 里 `OA_POSTGRES_USER` 的默认值
#: （initdb 属主、BYPASSRLS）；`officeai_app` 因要求冒号紧跟用户名而不会被误伤。
#: 部署自定义了别的属主名时，仍由 R1 的变量判据与 /readyz 的 rolbypassrls 自检兜住。
LITERAL_SUPERUSER_DSN: Final[re.Pattern[str]] = re.compile(
    r"://(?:postgres|superuser|root|officeai):", re.IGNORECASE
)

#: 必须在 adapter environment 中显式声明的键
REQUIRED_ADAPTER_KEYS: Final[tuple[str, ...]] = ("OA_ENV", "OA_AUTH_MODE")

#: 参与 R4（Dockerfile 命名上下文 ↔ 编排声明）比对的编排文件。
#:
#: R1/R2/R3 沿用 `COMPOSE_FILES`（不改变既有判据行为）；R4 必须覆盖**三套交付形态**
#: ——R2-ARC-01 的根因正是「改一套漏两套」：H-3 给全量栈加了
#: `additional_contexts.lockroot`，lite / saas 漏配，而 `apps/web/Dockerfile` 硬依赖
#: `COPY --from=lockroot`，于是两套真正交付给客户的编排连镜像都产不出来。
NAMED_CONTEXT_COMPOSE_FILES: Final[tuple[str, ...]] = (
    "docker-compose.yml",
    "docker-compose.lite.yml",
    "docker-compose.saas.yml",
)

#: R5：唯一允许对外（可不绑回环）的服务——边缘/反代（对外入口本身）
EDGE_SERVICES: Final[frozenset[str]] = frozenset({"edge", "proxy"})

#: R5：边缘允许对外的容器端口（80 = 探活 + 301 跳转，443 = TLS 终止）
PUBLIC_TARGETS: Final[frozenset[str]] = frozenset({"80", "443"})

#: R6：k8s 清单目录（相对部署目录；不存在时跳过——清单是 V1.1 准备物）
K8S_BASE_DIR: Final[str] = "k8s/base"

#: R6：env 键唯一真源（相对部署目录）
ENV_EXAMPLE_NAME: Final[str] = ".env.example"


def _dockerfile_named_context_refs(dockerfile: Path) -> set[str]:
    """返回 Dockerfile 中被 `COPY --from=<name>` 引用的**外部命名上下文**。

    两类值不算命名上下文，需排除：
    - 构建阶段名（`FROM ... AS deps` 里的 `deps`，属内部阶段）；
    - 数字索引（`COPY --from=0`）。

    Args:
        dockerfile: Dockerfile 路径。

    Returns:
        set[str]: 小写化的命名上下文集合。
    """
    stages: set[str] = set()
    refs: set[str] = set()
    for raw in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        if upper.startswith("FROM "):
            match = re.search(r"\bAS\s+([A-Za-z0-9_.-]+)", line, re.IGNORECASE)
            if match:
                stages.add(match.group(1).lower())
        if upper.startswith("COPY "):
            for match in re.finditer(r"--from=([A-Za-z0-9_.-]+)", line, re.IGNORECASE):
                refs.add(match.group(1).lower())
    return {ref for ref in refs if ref not in stages and not ref.isdigit()}


def _check_named_contexts(target: Path) -> list[dict[str, str]]:
    """R4：Dockerfile 引用的命名上下文必须在该编排的 `build.additional_contexts` 中声明。

    为什么需要：CI 不构建镜像，静态门禁若也不校验这条跨文件约束，那么
    「Dockerfile 引用了 named context、但某套编排没声明」只能等到真实
    `docker build` 时暴露——而真实构建根本不在自动化里，于是三套编排全坏
    也能一路绿灯（审计 2026-10-29 的 H-1 就是这么来的）。

    Args:
        target: 部署目录。

    Returns:
        list[dict[str, str]]: 违规项（rule=R4）。
    """
    violations: list[dict[str, str]] = []
    for name in NAMED_CONTEXT_COMPOSE_FILES:
        path = target / name
        if not path.is_file():
            continue
        data = _load(path)
        for service_name, service in _services(data).items():
            if not isinstance(service, dict):
                continue
            build = service.get("build")
            if not isinstance(build, dict):
                continue
            context_dir = (target / str(build.get("context", "."))).resolve()
            dockerfile = context_dir / str(build.get("dockerfile", "Dockerfile"))
            if not dockerfile.is_file():
                continue
            refs = _dockerfile_named_context_refs(dockerfile)
            if not refs:
                continue
            declared = build.get("additional_contexts") or {}
            declared_keys = (
                {str(key).lower() for key in declared} if isinstance(declared, dict) else set()
            )
            missing = sorted(refs - declared_keys)
            if missing:
                violations.append(
                    {
                        "rule": "R4",
                        "file": name,
                        "detail": (
                            f"服务 {service_name} 的 {dockerfile.name} 引用了命名上下文 "
                            f"{missing}，但本编排 build.additional_contexts 未声明"
                            f"（已声明：{sorted(declared_keys) or '无'}）——"
                            "镜像会在解析 Dockerfile 阶段直接失败"
                        ),
                    }
                )
    return violations


def _load(path: Path) -> dict[str, Any]:
    """读取单个编排文件。

    Args:
        path: 编排文件路径。

    Returns:
        dict[str, Any]: 解析后的映射（空文件返回空字典）。
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _services(data: dict[str, Any]) -> dict[str, Any]:
    """取 services 映射。

    Args:
        data: 编排文件内容。

    Returns:
        dict[str, Any]: 服务名 → 服务定义。
    """
    services = data.get("services")
    return services if isinstance(services, dict) else {}


def _env_map(service: dict[str, Any]) -> dict[str, str]:
    """把 environment 规范化为「键 → 字符串值」。

    Compose 支持 dict 与 `KEY=VALUE` 列表两种写法，门禁必须两种都认，
    否则换个写法就能绕过检查。

    Args:
        service: 单个服务定义。

    Returns:
        dict[str, str]: 环境变量映射。
    """
    raw = service.get("environment")
    result: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            result[str(key)] = "" if value is None else str(value)
    elif isinstance(raw, list):
        for item in raw:
            text = str(item)
            key, _, value = text.partition("=")
            result[key.strip()] = value
    return result


def _initdb_targets(service: dict[str, Any]) -> set[str]:
    """取 postgres 服务的 initdb 挂载目标文件名集合。

    Args:
        service: postgres 服务定义。

    Returns:
        set[str]: 形如 `20-init-schema-phase2.sql` 的文件名集合。
    """
    volumes = service.get("volumes")
    if not isinstance(volumes, list):
        return set()
    targets: set[str] = set()
    for item in volumes:
        text = str(item)
        parts = text.split(":")
        if len(parts) < 2:
            continue
        if parts[1].rstrip("/").startswith(INITDB_DIR.rstrip("/")):
            targets.add(parts[1].rsplit("/", 1)[-1])
    return targets


def check_dir(target: Path) -> list[dict[str, str]]:
    """扫描部署目录，返回违规项。

    Args:
        target: 部署目录。

    Returns:
        list[dict[str, str]]: 每项含 `rule` / `file` / `detail`。
    """
    violations: list[dict[str, str]] = []
    compose_data: dict[str, dict[str, Any]] = {}

    for name in COMPOSE_FILES:
        path = target / name
        if not path.is_file():
            violations.append(
                {"rule": "R0", "file": name, "detail": "编排文件缺失（部署形态不完整）"}
            )
            continue
        data = _load(path)
        compose_data[name] = data
        adapter = _services(data).get(ADAPTER_SERVICE)
        if not isinstance(adapter, dict):
            violations.append(
                {"rule": "R0", "file": name, "detail": f"缺少 {ADAPTER_SERVICE} 服务"}
            )
            continue
        env = _env_map(adapter)

        # R1：业务库 DSN 不得回落库属主（超级用户绕过 RLS）。
        # 两种写法都要拦：变量插值 `${OA_POSTGRES_USER}` 与内联字面量 `officeai:`。
        dsn = env.get("OA_DATABASE_URL", "")
        if SUPERUSER_VAR in dsn:
            violations.append(
                {
                    "rule": "R1",
                    "file": name,
                    "detail": (
                        "OA_DATABASE_URL 引用了 ${OA_POSTGRES_USER}（库属主/超级用户），"
                        "RLS 会对其失效；必须使用 officeai_app 应用角色"
                    ),
                }
            )
        elif LITERAL_SUPERUSER_DSN.search(dsn):
            violations.append(
                {
                    "rule": "R1",
                    "file": name,
                    "detail": (
                        "OA_DATABASE_URL 内联了字面超级用户 DSN（库属主/超级用户），"
                        "RLS 会对其失效；请改为引用 ${OA_DATABASE_URL} 应用角色 DSN"
                    ),
                }
            )

        # R2：鉴权与环境名必须显式声明（代码默认 stub = 不验签）
        for key in REQUIRED_ADAPTER_KEYS:
            if key not in env:
                violations.append(
                    {
                        "rule": "R2",
                        "file": name,
                        "detail": f"adapter environment 未显式声明 {key}（依赖代码默认值）",
                    }
                )

    # R3：initdb 挂载集合必须跨编排一致
    initdb_sets: dict[str, set[str]] = {}
    for name, data in compose_data.items():
        postgres = _services(data).get(DATABASE_SERVICE)
        if isinstance(postgres, dict):
            initdb_sets[name] = _initdb_targets(postgres)
    if len(initdb_sets) >= 2:
        names = sorted(initdb_sets)
        baseline_name = names[0]
        baseline = initdb_sets[baseline_name]
        for name in names[1:]:
            if initdb_sets[name] != baseline:
                missing = sorted(baseline - initdb_sets[name])
                extra = sorted(initdb_sets[name] - baseline)
                violations.append(
                    {
                        "rule": "R3",
                        "file": name,
                        "detail": (
                            f"initdb 挂载集合与 {baseline_name} 不一致；"
                            f"缺少 {missing or '无'}，多出 {extra or '无'}"
                        ),
                    }
                )

    # R4：Dockerfile 的 `COPY --from=<name>` ↔ 各编排 `build.additional_contexts` 键集合
    # （R2-ARC-01：三套编排的 web 镜像全靠这条才能被静态拦住）
    violations.extend(_check_named_contexts(target))

    # R5：端口收口（G1，审计 2026-11-02 P0-4）——除边缘 80/443 外，一切宿主端口
    # 必须绑 `127.0.0.1`（明文业务端口裸绑全网卡 = 绕过边缘限流/安全头/证书）
    violations.extend(_check_port_bindings(target))

    # R6：k8s 清单的 env 键 ⊆ `.env.example`（设计 §3.3.6：防 k8s 与 compose 键漂移）
    violations.extend(_check_k8s_env_keys(target))
    return violations


def _check_port_bindings(target: Path) -> list[dict[str, str]]:
    """R5：宿主端口绑定收口检查。

    判据：`ports` 里带宿主口的条目，要么绑 `127.0.0.1`（仅本机调试），
    要么属于边缘服务（edge/proxy）的 80/443（对外入口本身）。

    Args:
        target: 部署目录。

    Returns:
        list[dict[str, str]]: 违规项。
    """
    violations: list[dict[str, str]] = []
    for name in NAMED_CONTEXT_COMPOSE_FILES:
        path = target / name
        if not path.is_file():
            continue
        for service_name, service in _services(_load(path)).items():
            if not isinstance(service, dict):
                continue
            ports = service.get("ports")
            if not isinstance(ports, list):
                continue
            for entry in ports:
                text = str(entry).strip().strip('"').strip("'")
                # `${VAR:-80}` 里带冒号，先把占位符归一化再按冒号切分，
                # 否则 `"${OA_EDGE_HTTP_PORT:-80}:80"` 会被切成 3 段误判
                normalized = re.sub(r"\$\{[^}]*\}", "VAR", text)
                parts = normalized.split(":")
                if len(parts) == 3:
                    host_ip, target_port = parts[0], parts[2]
                elif len(parts) == 2:
                    host_ip, target_port = None, parts[1]
                else:
                    continue  # 仅容器端口（不映射宿主），不对外
                target_port = target_port.split("/")[0]
                if host_ip == "127.0.0.1":
                    continue
                if (
                    service_name in EDGE_SERVICES
                    and host_ip is None
                    and target_port in PUBLIC_TARGETS
                ):
                    continue
                violations.append(
                    {
                        "rule": "R5",
                        "file": f"{name}#{service_name}",
                        "detail": (
                            f"端口 `{text}` 裸绑定宿主网卡（G1：除边缘 80/443 外必须绑 "
                            f"`127.0.0.1:`，否则明文绕过边缘限流/安全头/TLS）"
                        ),
                    }
                )
    return violations


def _collect_k8s_env_keys(node: Any) -> list[str]:
    """递归收集 k8s 清单里的 env 键名（`env[].name` 与 ConfigMap `data` 键）。

    Args:
        node: YAML 解析结果（任意层级）。

    Returns:
        list[str]: 键名列表。
    """
    found: list[str] = []
    if isinstance(node, dict):
        env_list = node.get("env")
        if isinstance(env_list, list):
            for item in env_list:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    found.append(item["name"])
        if node.get("kind") == "ConfigMap" and isinstance(node.get("data"), dict):
            found.extend(str(key) for key in node["data"])
        for value in node.values():
            found.extend(_collect_k8s_env_keys(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_k8s_env_keys(item))
    return found


def _check_k8s_env_keys(target: Path) -> list[dict[str, str]]:
    """R6：k8s 清单用到的 env 键必须是 `.env.example` 的子集（设计 §3.3.6）。

    防的是「k8s 手写了 compose 里没有的键」：键集合唯一真源是
    `deploy/.env.example`（compose `env_file` 与 k8s Secret 都从它生成），
    清单里冒出新键 = 两处配置悄然分叉。

    Args:
        target: 部署目录。

    Returns:
        list[dict[str, str]]: 违规项。
    """
    k8s_base = target / K8S_BASE_DIR
    env_example = target / ENV_EXAMPLE_NAME
    if not k8s_base.is_dir() or not env_example.is_file():
        return []  # 无 k8s 清单或模板时跳过（清单是 V1.1 准备物，不强绑）
    allowed = {
        line.split("=", 1)[0].strip()
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    violations: list[dict[str, str]] = []

    keys_file = k8s_base / "app.env.keys"
    if keys_file.is_file():
        declared = {
            line.strip()
            for line in keys_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        for key in sorted(declared - allowed):
            violations.append(
                {
                    "rule": "R6",
                    "file": f"{K8S_BASE_DIR}/app.env.keys",
                    "detail": f"键 `{key}` 不在 {ENV_EXAMPLE_NAME}（键集合唯一真源）里",
                }
            )

    for yaml_path in sorted(k8s_base.rglob("*.yaml")):
        for doc in yaml.safe_load_all(yaml_path.read_text(encoding="utf-8")):
            for key in _collect_k8s_env_keys(doc):
                if key not in allowed:
                    violations.append(
                        {
                            "rule": "R6",
                            "file": f"{K8S_BASE_DIR}/{yaml_path.relative_to(k8s_base).as_posix()}",
                            "detail": f"env 键 `{key}` 不在 {ENV_EXAMPLE_NAME}（键集合唯一真源）里",
                        }
                    )
    return violations


def run_self_test() -> int:
    """用临时夹具验证门禁能命中三类负样本。

    Returns:
        int: 命中全部负样本返回 1（供 CI 断言），否则返回 0。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "docker-compose.yml").write_text(
            "\n".join(
                [
                    "services:",
                    "  adapter:",
                    "    environment:",
                    "      OA_ENV: production",
                    "      OA_AUTH_MODE: oidc",
                    "      OA_DATABASE_URL: ${OA_DATABASE_URL:?must-set}",
                    # R5 负样本：业务端口裸绑全网卡（G1 红线）
                    "    ports:",
                    '      - "8000:8000"',
                    "  postgres:",
                    "    volumes:",
                    "      - ./init-databases.sql:"
                    "/docker-entrypoint-initdb.d/00-init-databases.sql:ro",
                ]
            ),
            encoding="utf-8",
        )
        (root / "docker-compose.saas.yml").write_text(
            "\n".join(
                [
                    "services:",
                    "  adapter:",
                    "    environment:",
                    "      OA_DATABASE_URL: postgresql://${OA_POSTGRES_USER}:x@db/y",
                    "  postgres:",
                    "    volumes:",
                    "      - ./init-rls.sql:/docker-entrypoint-initdb.d/10-init-rls.sql:ro",
                ]
            ),
            encoding="utf-8",
        )
        # lite 是**合规**夹具：证明新增到 COMPOSE_FILES 后不会误报。
        # 它同时是第二段的载体——第二段会把它改成内联超级用户 DSN，
        # 验证 R1 确实扫到了轻量版（A-01 的回归哨兵，不允许再退化成恒绿）。
        (root / "docker-compose.lite.yml").write_text(
            "\n".join(
                [
                    "services:",
                    "  adapter:",
                    "    environment:",
                    "      OA_ENV: production",
                    "      OA_AUTH_MODE: oidc",
                    "      OA_DATABASE_URL: ${OA_DATABASE_URL:?must-set}",
                    "  postgres:",
                    "    volumes:",
                    "      - ./init-databases.sql:"
                    "/docker-entrypoint-initdb.d/00-init-databases.sql:ro",
                ]
            ),
            encoding="utf-8",
        )
        # R4 负样本：Dockerfile 引用命名上下文 `lockroot`，但编排**未**声明
        # `build.additional_contexts` —— 这正是 R2-ARC-01 的形态（lite/saas 漏配），
        # 真实后果是镜像在解析 Dockerfile 阶段直接构建失败。
        (root / "web").mkdir(parents=True, exist_ok=True)
        (root / "web" / "Dockerfile").write_text(
            "FROM node:22-alpine AS deps\nCOPY --from=lockroot package.json ./\nCOPY . .\n",
            encoding="utf-8",
        )
        with (root / "docker-compose.yml").open("a", encoding="utf-8") as fh:
            fh.write("  web:\n    build:\n      context: ./web\n      dockerfile: Dockerfile\n")

        # R6 负样本：k8s 清单手写了 `.env.example` 里没有的键（键漂移）
        (root / ".env.example").write_text("OA_ENV=test\n", encoding="utf-8")
        (root / "k8s" / "base").mkdir(parents=True, exist_ok=True)
        (root / "k8s" / "base" / "app.env.keys").write_text(
            "OA_ENV\nOA_STRAY_KEY\n", encoding="utf-8"
        )

        found = {item["rule"] for item in check_dir(root)}
        expected = {"R1", "R2", "R3", "R4", "R5", "R6"}
        if found != expected:
            print(f"[FAIL] 自检未命中预期负样本：期望 {sorted(expected)}，实际 {sorted(found)}")
            return 0

        # 第二段：A-01 回归哨兵。把 lite 的合规 DSN 换成内联属主 DSN，
        # 断言 R1 在 **lite 这份文件**上被命中；否则说明新交付形态又漏扫了。
        lite_path = root / "docker-compose.lite.yml"
        lite_path.write_text(
            lite_path.read_text(encoding="utf-8").replace(
                "OA_DATABASE_URL: ${OA_DATABASE_URL:?must-set}",
                "OA_DATABASE_URL: postgresql://officeai:secret@postgres:5432/officeai",
            ),
            encoding="utf-8",
        )
        lite_rules = {
            item["rule"] for item in check_dir(root) if item["file"] == "docker-compose.lite.yml"
        }
        if "R1" not in lite_rules:
            print("[FAIL] 自检未拦截 lite 的内联超级用户 DSN：门禁对新交付形态仍有盲区")
            return 0

        print("[OK] 自检通过：R1-R6 六类负样本均被拦截，且 lite 形态的 R1 负样本被拦截")
        return 1


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数（默认取 `sys.argv[1:]`）。

    Returns:
        int: 退出码。
    """
    parser = argparse.ArgumentParser(description="部署形态一致性门禁（G-34）")
    parser.add_argument("--self-test", action="store_true", help="负样本自检")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not DEPLOY_DIR.is_dir():
        print(f"[FATAL] 部署目录不存在：{DEPLOY_DIR}", file=sys.stderr)
        return 2

    violations = check_dir(DEPLOY_DIR)
    if args.json:
        print(json.dumps({"violations": violations}, ensure_ascii=False, indent=2))
    else:
        if violations:
            print(f"[FAIL] 部署形态检查发现 {len(violations)} 项违规：")
            for item in violations:
                print(f"  - [{item['rule']}] {item['file']}: {item['detail']}")
        else:
            print(
                "[OK] 部署形态一致：应用角色 DSN / 鉴权显式声明 / initdb 挂载集合一致 / "
                "Dockerfile 命名上下文已声明 / 端口收口（G1）/ k8s 键不漂移（R6）"
            )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
