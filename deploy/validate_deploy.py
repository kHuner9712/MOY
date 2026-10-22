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

- R1：adapter 的 `OA_DATABASE_URL` 不得引用 `${OA_POSTGRES_USER`；
- R2：adapter 的 `environment` 必须显式声明 `OA_ENV` 与 `OA_AUTH_MODE`
  （依赖代码默认值 = 不配就是 stub = 不验签，S1）；
- R3：各编排的 initdb 挂载目标文件名集合必须一致（避免库结构分叉）。

用法::

    python deploy/validate_deploy.py                 # 扫描（CI 默认）
    python deploy/validate_deploy.py --self-test     # 负样本自检（期望退出码 1）
    python deploy/validate_deploy.py --json          # 机器可读输出

退出码：0 = 通过；1 = 发现违规或自检命中；2 = 门禁自身异常（目录/依赖缺失）。
"""

from __future__ import annotations

import argparse
import json
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

#: 参与比对的编排文件（顺序稳定，便于报告）
COMPOSE_FILES: Final[tuple[str, ...]] = (
    "docker-compose.yml",
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

#: 必须在 adapter environment 中显式声明的键
REQUIRED_ADAPTER_KEYS: Final[tuple[str, ...]] = ("OA_ENV", "OA_AUTH_MODE")


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

        # R1：业务库 DSN 不得回落库属主（超级用户绕过 RLS）
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
        found = {item["rule"] for item in check_dir(root)}
        expected = {"R1", "R2", "R3"}
        if found == expected:
            print("[OK] 自检通过：R1/R2/R3 三类负样本均被拦截")
            return 1
        print(f"[FAIL] 自检未命中预期负样本：期望 {sorted(expected)}，实际 {sorted(found)}")
        return 0


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
            print("[OK] 部署形态一致：应用角色 DSN / 鉴权显式声明 / initdb 挂载集合一致")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
