#!/usr/bin/env bash
# ===========================================================================
# 轻量版起栈校验（阶段四 T4-05.4，架构 §9.1 / §9.4）
#
# 一句话：**先静态断言编排本身对不对，再把栈真起起来，用实测数字证明 9 容器 / 6 GiB
# 两条判据成立**。任何一条不成立就非 0 退出，并打印"照着做就能修"的原因。
#
# 用法：
#   bash deploy/validate_lite_up.sh                  # 全量（静态 + 真起栈）
#   bash deploy/validate_lite_up.sh --static-only    # 只跑静态断言（CI / 无镜像时）
#   bash deploy/validate_lite_up.sh --timeout 300    # 改就绪窗口（默认 180s）
#   bash deploy/validate_lite_up.sh --env-file deploy/.env.lite
#
# 前置：
#   1. `cp deploy/.env.lite.example deploy/.env.lite` 并替换**所有** CHANGE_ME_*；
#   2. 镜像已就位（离线包 install.sh 负责加载；本机可 `docker compose -f
#      deploy/docker-compose.lite.yml build adapter web`）。
#
# 为什么就绪窗口是 180 s（不是 60 s）：
#   `dify-api` 首启实测 **78.1 s**、二次 84.4 s，是镜像固有开销（与迁移无关）；
#   keycloak 首启 81.6 s；adapter 还要等它们。60 s 窗口在正常机器上必红。
#
# 为什么静态断言要单独存在：
#   拉镜像慢 / 客户环境不可达时，"编排写错了"和"网络拉不到镜像"必须能区分开。
#   静态断言的每一条都对应一次真实故障（见 docs/lite-container-baseline.md）：
#     · JVM 不限堆 → 整机内存 1.93 → 3.30 GiB（baseline §2.5）
#     · `CODE_EXECUTION_ENDPOINT` 置空串 → dify-api 进程直接退出（baseline §1.3 坑 1）
#     · PG 只判 pg_isready → plugin_daemon panic 退出（baseline §3.3）
#     · 缺 PGVECTOR_* 之一 → `ValueError: config PGVECTOR_XXX is required`
#
# 退出码：
#   0 通过 / 1 断言失败 / 2 前置检查未过（环境或配置缺失，与编排无关）
# ===========================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.lite.yml"
ENV_FILE="${SCRIPT_DIR}/.env.lite"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.lite.example"

STATIC_ONLY=0
READY_TIMEOUT=180
EXPECTED_CONTAINERS=9
# 6 GiB：架构 §9.1 判据（实测空载 1.93 GiB，留 3 倍抖动余量）
MEMORY_LIMIT_MIB=6144

PY=""

# ---------------------------------------------------------------------------
# 输出与失败处理
# ---------------------------------------------------------------------------
info() { printf '\033[36m[INFO]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[FAIL]\033[0m %s\n' "$*" >&2; }

#: 前置检查失败（环境/配置问题，退出码 2，避免与"编排断言失败"混淆）
abort_env() {
  err "$1"
  shift
  for line in "$@"; do printf '       %s\n' "${line}" >&2; done
  exit 2
}

#: 断言失败（退出码 1）：调用方已经打印了具体原因
global_failure_hint() {
  cat >&2 <<'TIP'

排查顺序（照这个顺序看，能省掉大部分来回）：
  1. docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite ps
  2. docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite logs --tail 40 <服务名>
  3. 对照 docs/lite-container-baseline.md §3.3 的两条就绪判据（PG 要断言 schema_migration、
     Keycloak 要探管理端口 9000），别只看容器"跑起来了"。
  4. 拉镜像慢/网络不通时用 --static-only 单独验证编排，别把网络问题误判成配置问题。
TIP
}

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --static-only) STATIC_ONLY=1; shift ;;
    --timeout)     READY_TIMEOUT="${2:?--timeout 需要一个秒数}"; shift 2 ;;
    --env-file)    ENV_FILE="${2:?--env-file 需要路径}"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    *)             abort_env "未知参数：$1" "用 --help 查看用法" ;;
  esac
done

# compose 一律显式带 --env-file：deploy/ 下还有标准版的 .env，
# 不显式指定的话 compose 会自动读它，于是"轻量版悄悄用了标准版的口令"。
compose() {
  docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"
}

# ===========================================================================
# 一、前置检查
# ===========================================================================
check_prereqs() {
  command -v docker >/dev/null 2>&1 || abort_env "找不到 docker 命令" "装 Docker 24+ 与 Compose v2 后重试"

  local docker_ver compose_ver
  docker_ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  [[ -n "${docker_ver}" ]] || abort_env \
    "连不上 Docker 守护进程（docker version 无 Server.Version）" \
    "Windows: 启动 Docker Desktop 并等托盘图标变绿" \
    "Linux : sudo systemctl start docker（并确认当前用户在 docker 组）"

  compose_ver="$(docker compose version --short 2>/dev/null | sed 's/^v//' || true)"
  [[ -n "${compose_ver}" ]] || abort_env "找不到 docker compose（v2）插件" "Docker Desktop 自带；Linux 装 docker-compose-plugin"

  # 版本门槛用"主版本×1000+次版本"比较，避免 sort -V 在 Git Bash 上的差异
  local d_major d_minor c_major c_minor
  IFS=. read -r d_major d_minor _ <<<"${docker_ver}"
  IFS=. read -r c_major c_minor _ <<<"${compose_ver}"
  (( 10#${d_major} * 1000 + 10#${d_minor} >= 24000 )) \
    || abort_env "Docker 版本过低（${docker_ver}）" "需要 24.0+（deploy.resources.limits 在老版本 compose 上不生效）"
  # 2.24+ 才有 env_file 的 required: false 长语法（compose 文件里用到）
  (( 10#${c_major} * 1000 + 10#${c_minor} >= 2024 )) \
    || abort_env "Compose 版本过低（${compose_ver}）" "需要 v2.24+（env_file 长语法 required:false 自该版本起支持）"

  for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then PY="${candidate}"; break; fi
  done
  [[ -n "${PY}" ]] || abort_env "找不到 python（3.8+）" "静态断言要解析 compose config 的 JSON；装 python3 后重试"

  command -v curl >/dev/null 2>&1 || abort_env "找不到 curl" "轮询 /readyz 需要它"

  [[ -f "${COMPOSE_FILE}" ]] || abort_env "缺编排文件：${COMPOSE_FILE}"
  [[ -f "${SCRIPT_DIR}/nginx/nginx.conf" ]] || abort_env "缺 nginx 主配置：${SCRIPT_DIR}/nginx/nginx.conf"
  # K4-min：proxy 挂的是 `edge.conf`（与 full/saas 共用一套路由）。
  # 检查旧文件 `lite.conf` 会变成"断言了一个不再被挂载的文件"——假绿。
  [[ -f "${SCRIPT_DIR}/nginx/edge.conf" ]] || abort_env "缺 nginx 站点配置：${SCRIPT_DIR}/nginx/edge.conf"

  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ "${STATIC_ONLY}" == "1" && -f "${ENV_EXAMPLE}" ]]; then
      # 静态模式允许拿样例跑：只为证明"编排本身能被 compose 解析"
      warn "未找到 ${ENV_FILE}，静态模式改用样例 ${ENV_EXAMPLE}（占位值）"
      ENV_FILE="${ENV_EXAMPLE}"
    else
      abort_env "缺环境文件：${ENV_FILE}" \
        "cp deploy/.env.lite.example deploy/.env.lite" \
        "然后把其中所有 CHANGE_ME_* 换成真实随机值（生成命令见该文件头部）"
    fi
  fi

  info "docker ${docker_ver} / compose v${compose_ver} / ${PY} 就绪"
}

#: CHANGE_ME 残留 + 必需键非空 + 入口端口与对外基址一致
check_env_content() {
  local residual
  # 只看"键 = 值"行：`.env.lite.example` 的注释里也写着 CHANGE_ME_*（说明文字），
  # 把注释算成残留会让每一份合法配置都被拒。
  residual="$(grep -n 'CHANGE_ME' "${ENV_FILE}" | grep -Ev '^[0-9]+:[[:space:]]*#' || true)"
  if [[ -n "${residual}" ]]; then
    err "环境文件里仍有 CHANGE_ME 占位值未替换：${ENV_FILE}"
    printf '%s\n' "${residual}" | sed 's/^/       /' >&2
    abort_env "占位口令上线等同于把数据库/缓存/身份系统敞开" \
      "生成随机值：openssl rand -hex 24（口令类，URL 安全）、openssl rand -base64 32（密钥类）" \
      "凭据加密密钥必须是 32 字节 base64：python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
  fi

  # 必需键：空值会在 `compose up` 时被 `:?` 拦下，但那时已经拉了镜像；
  # 提前报错能省掉等待，也能给出"缺哪一项"的名字。
  local -a required=(
    OA_POSTGRES_PASSWORD OA_VALKEY_PASSWORD OA_DIFY_SECRET_KEY
    OA_PLUGIN_DAEMON_KEY OA_PLUGIN_INNER_API_KEY
    OA_KEYCLOAK_ADMIN_PASSWORD OA_DATABASE_URL
    OA_JWT_SECRET OA_CREDENTIAL_ENCRYPTION_KEY
  )
  local key value missing=0
  for key in "${required[@]}"; do
    value="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 | cut -d= -f2- || true)"
    if [[ -z "${value}" ]]; then
      err "必需变量缺失或为空：${key}"
      missing=1
    fi
  done
  [[ "${missing}" == "0" ]] || abort_env "环境文件不完整：${ENV_FILE}" "每一项都在 .env.lite.example 里有逐条注释"

  # 对外基址必须带与宿主端口一致的端口号，否则 Dify 生成的下载链接会 404
  local proxy_port public_url
  proxy_port="$(grep -E '^OA_LITE_PROXY_PORT=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
  public_url="$(grep -E '^OA_LITE_PUBLIC_URL=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
  proxy_port="${proxy_port:-80}"
  public_url="${public_url:-http://localhost}"
  if [[ "${proxy_port}" != "80" && "${public_url}" != *":${proxy_port}"* ]]; then
    warn "OA_LITE_PROXY_PORT=${proxy_port} 但 OA_LITE_PUBLIC_URL=${public_url}（端口不一致）"
    warn "  后果：Dify 生成的文件/回调链接会打到 80 端口，客户点开是 404"
  fi

  info "环境文件检查通过：${ENV_FILE}"
}

# ===========================================================================
# 二、静态断言（全部读 compose config 的解析结果，**不 grep 源文件**）
#
# 为什么必须走 config：注释里也写着 `JAVA_OPTS_APPEND: "-Xms64m -Xmx384m"`、
# `CODE_EXECUTION_ENDPOINT` 这些字样，grep 全文会假通过。
# ===========================================================================
run_static_assertions() {
  # 用 `--profile '*'` 渲染**全部**服务：不带 profile 时 compose config 会把
  # dify-web / migrate 整段剔除，断言就永远看不到它们（"服务不存在"会伪装成通过）。
  # 渲染交给 python 自己做（subprocess），避免在 Git Bash 下写临时文件
  # （mktemp 返回 C:\... 路径，rm 会触发 safe-delete 抱怨）。
  "${PY}" - "${COMPOSE_FILE}" "${ENV_FILE}" "${EXPECTED_CONTAINERS}" "${MEMORY_LIMIT_MIB}" "${STATIC_ONLY}" <<'PY'
"""轻量版编排静态断言。

每一条都对应一次真实故障（注释里写明出自 baseline 哪一节），
所以**不许为了让脚本变绿而放宽判据**——放宽等于把故障放回现场。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

compose_file, env_file, expected_count, mem_limit_mib, static_only = sys.argv[1:6]
expected_count = int(expected_count)
mem_limit_mib = int(mem_limit_mib)

rendered = subprocess.run(
    [
        "docker",
        "compose",
        "-f",
        compose_file,
        "--env-file",
        env_file,
        "--profile",
        "*",
        "config",
        "--format",
        "json",
    ],
    capture_output=True,
    text=True,
    check=False,
)
if rendered.returncode != 0:
    print(f"\033[31m[FAIL]\033[0m compose config 解析失败：")
    for line in (rendered.stderr or "").splitlines()[:20]:
        print(f"       {line}")
    sys.exit(2)

cfg: dict[str, Any] = json.loads(rendered.stdout)

services: dict[str, Any] = cfg.get("services") or {}
failures: list[str] = []
checked = 0


def check(condition: bool, message: str, detail: str = "") -> None:
    """记录一条断言。失败不抛异常，一次把所有问题都报出来。"""
    global checked
    checked += 1
    if condition:
        print(f"\033[32m[ OK ]\033[0m {message}")
        return
    failures.append(message if not detail else f"{message} —— {detail}")
    print(f"\033[31m[FAIL]\033[0m {message}" + (f" —— {detail}" if detail else ""))


def env_of(name: str) -> dict[str, str]:
    """取服务的展开后环境变量（compose config 已把 ${VAR:-默认} 都替换掉了）。"""
    raw = services.get(name, {}).get("environment") or {}
    if isinstance(raw, list):  # 少数 compose 版本输出 ["K=V", ...]
        out: dict[str, str] = {}
        for item in raw:
            key, _, value = str(item).partition("=")
            out[key] = value
        return out
    return {str(k): "" if v is None else str(v) for k, v in raw.items()}


def parse_mib(value: Any) -> float:
    """把 `512M` / `1G` / `512000000` 统一成 MiB。"""
    if isinstance(value, (int, float)):
        return float(value) / (1024 * 1024)
    text = str(value).strip().lower()
    match = re.fullmatch(r"([0-9.]+)\s*([kmg]?)(?:i?b)?", text)
    if not match:
        return 0.0
    number = float(match.group(1))
    unit = match.group(2)
    factor = {"": 1.0 / (1024 * 1024), "k": 1.0 / 1024, "m": 1.0, "g": 1024.0}[unit]
    return number * factor


# --- 0. 解析成功本身是一条断言（config 能过 = 变量插值、YAML 锚点都没问题）---
check(
    bool(services),
    "compose config 可解析且含服务定义",
    f"services={len(services)}",
)

# --- 1. 常驻服务恰好 9 个（无 profiles 的服务才会被 `up -d` 默认拉起）---
EXPECTED = {
    "proxy",
    "adapter",
    "web",
    "postgres",
    "valkey",
    "keycloak",
    "dify-api",
    "dify-worker",
    "plugin_daemon",
}
resident = {name for name, body in services.items() if not (body or {}).get("profiles")}
check(
    resident == EXPECTED,
    "常驻服务集合与 baseline §1.1 的 9 项一致",
    "" if resident == EXPECTED else f"多={sorted(resident - EXPECTED)} 少={sorted(EXPECTED - resident)}",
)
check(
    len(resident) == expected_count,
    f"常驻服务数量 = {expected_count}",
    f"实际 {len(resident)}：{sorted(resident)}",
)

# --- 2. 非常驻两个容器靠 profiles 隔离（dify-web 不常驻是省内存的关键）---
check(
    (services.get("dify-web") or {}).get("profiles") == ["admin"],
    "dify-web 在 profiles:[admin] 下（不常驻）",
    "baseline §1.2：日常对话零依赖，仅首次配 provider 时起",
)
check(
    (services.get("migrate") or {}).get("profiles") == ["migrate"],
    "migrate 在 profiles:[migrate] 下（迁移不进 Web 启动）",
    "架构 §9.4：迁移由一次性 Job 执行，避免多副本并发 DDL",
)

# --- 3. 每个常驻服务都有 healthcheck 与内存上限（有状态服务 100% 覆盖）---
missing_hc = sorted(name for name in EXPECTED if not (services.get(name) or {}).get("healthcheck"))
check(not missing_hc, "9 个常驻服务全部配了 healthcheck", f"缺：{missing_hc}")

missing_limits: list[str] = []
for name in sorted(EXPECTED):
    limits = (
        ((services.get(name) or {}).get("deploy") or {}).get("resources") or {}
    ).get("limits") or {}
    if parse_mib(limits.get("memory", 0)) <= 0:
        missing_limits.append(name)
check(not missing_limits, "9 个常驻服务全部配了 deploy.resources.limits.memory", f"缺：{missing_limits}")

total_limit = sum(
    parse_mib(
        (
            (((services.get(name) or {}).get("deploy") or {}).get("resources") or {}).get("limits") or {}
        ).get("memory", 0)
    )
    for name in EXPECTED
)
check(
    total_limit <= mem_limit_mib * 1.05,
    f"内存上限之和 ≤ {mem_limit_mib} MiB（{total_limit:.0f} MiB）",
    "上限之和超过 6 GiB 意味着单容器失控时整机会被压死",
)

# --- 4. Keycloak：JVM 限堆 + 管理端口就绪判据 ---
kc_env = env_of("keycloak")
check(
    kc_env.get("JAVA_OPTS_APPEND") == "-Xms64m -Xmx384m",
    'Keycloak 带 JVM 限堆 -Xms64m -Xmx384m',
    f"实际 {kc_env.get('JAVA_OPTS_APPEND')!r}；不设时整机 1.93 → 3.30 GiB（baseline §2.5）",
)
check(
    str(kc_env.get("KC_HEALTH_ENABLED", "")).lower() == "true",
    "Keycloak KC_HEALTH_ENABLED=true",
    "自 Keycloak 25 起健康端点默认不启用，不启用则 /health/ready 404",
)
kc_cmd = " ".join(str(part) for part in (services.get("keycloak") or {}).get("command") or [])
check(
    "--health-enabled=true" in kc_cmd and "9000" in kc_cmd,
    "Keycloak 命令行启用健康端点并固定管理端口 9000",
    f"实际 command={kc_cmd!r}",
)
kc_hc = json.dumps((services.get("keycloak") or {}).get("healthcheck") or {})
check(
    "/health/ready" in kc_hc,
    "Keycloak 健康检查打 /health/ready（不是 8080 的 /health/ready 之外的路径）",
    "baseline §3.3：探 8080 实测 404，管理端口是 9000",
)

# --- 5. Dify：pgvector + 7 个 PGVECTOR_* + 三个键不许出现 ---
PGVECTOR_KEYS = (
    "PGVECTOR_HOST",
    "PGVECTOR_PORT",
    "PGVECTOR_USER",
    "PGVECTOR_PASSWORD",
    "PGVECTOR_DATABASE",
    "PGVECTOR_MIN_CONNECTION",
    "PGVECTOR_MAX_CONNECTION",
)
for name in ("dify-api", "dify-worker"):
    env = env_of(name)
    check(
        env.get("VECTOR_STORE") == "pgvector",
        f"{name} VECTOR_STORE=pgvector",
        f"实际 {env.get('VECTOR_STORE')!r}；换 milvus 会多 3 个容器 471 MiB",
    )
    missing = [key for key in PGVECTOR_KEYS if not env.get(key)]
    check(
        not missing,
        f"{name} 7 个 PGVECTOR_* 变量齐全",
        f"缺：{missing}（缺一个就 ValueError: config PGVECTOR_XXX is required）",
    )
    if not missing:
        check(
            int(env["PGVECTOR_MIN_CONNECTION"]) < int(env["PGVECTOR_MAX_CONNECTION"]),
            f"{name} PGVECTOR_MIN < MAX",
            f"{env['PGVECTOR_MIN_CONNECTION']} vs {env['PGVECTOR_MAX_CONNECTION']}",
        )
        check(
            env["PGVECTOR_DATABASE"] == env.get("DB_DATABASE"),
            f"{name} PGVECTOR_DATABASE 与 DB_DATABASE 同为 dify 库",
            f"{env['PGVECTOR_DATABASE']!r} vs {env.get('DB_DATABASE')!r}",
        )
    # 这三个键"整条不设"才是正确写法，设成空串会让进程直接退出（baseline §1.3 坑 1）
    forbidden = [
        key
        for key in ("CODE_EXECUTION_ENDPOINT", "SSRF_PROXY_HTTP_URL", "SSRF_PROXY_HTTPS_URL")
        if key in env
    ]
    check(
        not forbidden,
        f"{name} 未设置 CODE_EXECUTION_ENDPOINT / SSRF_PROXY_*（整条不设）",
        f"出现了 {forbidden}；空串会撞 HttpUrl 校验 → ValidationError → 进程退出",
    )

# --- 6. PG 就绪判据必须断言 schema_migration 表 ---
pg_hc = json.dumps((services.get("postgres") or {}).get("healthcheck") or {})
check(
    "pg_isready" in pg_hc and "to_regclass" in pg_hc and "schema_migration" in pg_hc,
    "PG 健康检查 = pg_isready + 断言 schema_migration 表存在",
    "baseline §3.3：只判 pg_isready 时 initdb 临时实例窗口会让 plugin_daemon panic 退出",
)

# --- 7. 只有 proxy 映射宿主端口（其余服务无公网鉴权模型）---
leaked: list[str] = []
for name, body in services.items():
    for port in (body or {}).get("ports") or []:
        text = json.dumps(port) if not isinstance(port, str) else port
        if name == "proxy":
            continue
        if "127.0.0.1" in text or "::1" in text:
            continue  # 管理端口只发回环，允许（dify-web 就是这种）
        leaked.append(f"{name}:{text}")
check(not leaked, "除 proxy 外的服务均未发布宿主端口", f"泄漏：{leaked}")

# --- 8. adapter：S1 生产防呆 + 迁移不进启动路径 ---
adapter_env = env_of("adapter")
check(adapter_env.get("OA_ENV") == "production", "adapter OA_ENV=production")
check(
    adapter_env.get("OA_AUTH_MODE") == "oidc",
    "adapter OA_AUTH_MODE=oidc",
    "production + stub 会被 validate_auth_startup 拒绝启动（30004，S1）",
)
check(
    adapter_env.get("OA_DEPLOY_MODE") == "private",
    "adapter OA_DEPLOY_MODE=private",
    f"实际 {adapter_env.get('OA_DEPLOY_MODE')!r}",
)
check(
    str(adapter_env.get("OA_DB_AUTO_MIGRATE", "")).strip().lower() in {"", "false", "0", "no"},
    "adapter 启动路径不跑迁移（OA_DB_AUTO_MIGRATE 未开启）",
    "迁移由 profiles:[migrate] 的一次性 Job 执行（架构 §9.4）；"
    "放在 Web 启动路径上，多副本同时上线会并发跑 DDL",
)
check(
    ":?" not in adapter_env.get("OA_DATABASE_URL", "") and "officeai_app" in adapter_env.get("OA_DATABASE_URL", ""),
    "adapter 的业务库 DSN 走应用角色 officeai_app",
    "用库属主会绕过行级安全，/readyz 的 db_privilege 自检判 not_ready",
)

# --- 9. adapter 依赖：三个有状态服务必须 healthy 之后才启动 ---
depends = (services.get("adapter") or {}).get("depends_on") or {}
for name in ("postgres", "valkey", "keycloak"):
    condition = (depends.get(name) or {}).get("condition")
    check(
        condition == "service_healthy",
        f"adapter 等 {name} healthy 后启动",
        f"实际 condition={condition!r}",
    )

# --- 10. migrate：命令与 SQL 目录（镜像内不带 deploy/，必须挂进去）---
migrate_cmd = " ".join(str(part) for part in (services.get("migrate") or {}).get("command") or [])
check(
    "app.db.migrate" in migrate_cmd and "--apply" in migrate_cmd,
    "migrate Job 命令 = python -m app.db.migrate --apply",
    f"实际 {migrate_cmd!r}",
)
migrate_env = env_of("migrate")
check(
    migrate_env.get("OA_DB_SQL_DIR", "").endswith("/deploy"),
    "migrate 显式指定 OA_DB_SQL_DIR（镜像内无 deploy/ 目录）",
    f"实际 {migrate_env.get('OA_DB_SQL_DIR')!r}",
)
migrate_vols = json.dumps((services.get("migrate") or {}).get("volumes") or [])
check(
    "/app/deploy" in migrate_vols,
    "migrate 挂载了 SQL 目录到 /app/deploy",
    f"实际 volumes={migrate_vols}",
)

# --- 11. proxy：SSE 关缓冲（会话流式的命门）---
proxy_hc = json.dumps((services.get("proxy") or {}).get("healthcheck") or {})
check("/healthz" in proxy_hc, "proxy 健康检查打 /healthz")

print()
if failures:
    print(f"\033[31m静态断言失败 {len(failures)} 条（共 {checked} 条）\033[0m")
    for item in failures:
        print(f"  · {item}")
    sys.exit(1)

print(f"\033[32m静态断言全部通过（{checked} 条）\033[0m")
if static_only == "1":
    print("静态模式：未起栈（真实起栈质量由 QA 复核）")
PY
  local rc=$?
  if [[ ${rc} -ne 0 ]]; then
    global_failure_hint
    exit 1
  fi
}

#: nginx 断言：这些是"能用"与"不能用"的分界，只能读配置本身（compose 管不到它）。
#: 每一条都对应一次真实故障：少了 proxy_buffering off，流式回答会憋到最后一次性吐出；
#: 少了 resolver，adapter 重建后 proxy 会一直打旧 IP（502 到手工 reload 为止）。
run_nginx_assertions() {
  # 断言对象 = **实际挂载**的站点配置（`edge.conf`）。此前指向 `lite.conf`，
  # 在 proxy 改挂 edge.conf 后会变成"检查一个没人用的文件"，是典型的假绿。
  local site="${SCRIPT_DIR}/nginx/edge.conf"
  local main="${SCRIPT_DIR}/nginx/nginx.conf"
  local -a missing=()

  grep -Eq 'proxy_buffering[[:space:]]+off' "${site}" \
    || missing+=("SSE 关代理缓冲：proxy_buffering off")
  grep -q 'X-Accel-Buffering' "${site}" \
    || missing+=("SSE 响应头：X-Accel-Buffering no")
  grep -Eq 'location[[:space:]]+/api/' "${site}" \
    || missing+=("/api/ 反代到 adapter")
  grep -Eq 'location[[:space:]]+=[[:space:]]+/healthz' "${site}" \
    || missing+=("/healthz 供 compose 健康检查")
  grep -Eq 'location[[:space:]]+=[[:space:]]+/readyz' "${site}" \
    || missing+=("/readyz 运维探针（起栈校验从这里轮询）")
  grep -Eq 'deny[[:space:]]+all' "${site}" \
    || missing+=("/readyz 与 /metrics 的 deny all 兜底")
  grep -Eq 'resolver[[:space:]]+127\.0\.0\.11' "${site}" \
    || missing+=("Docker 内置 DNS resolver（跟随容器重建换 IP）")
  grep -Eq 'listen[[:space:]]+80' "${site}" \
    || missing+=("proxy 监听 80")
  grep -Eq 'client_max_body_size[[:space:]]+50m' "${main}" \
    || missing+=("上传上限 50m（必须与 OA_MAX_UPLOAD_BYTES=52428800 一致）")
  grep -Eq 'proxy_read_timeout[[:space:]]+300s' "${main}" \
    || missing+=("流式读超时 300s（默认 60s 会掐断长回答）")

  # --- R4 / K4-min（2026-11-02）：登录链路的四条路由 ---
  # 每一条都对应一种"端点落错地方"的故障：落到 `location /` 会被 Next.js 吃成 404，
  # 而 404 的页面看起来像"功能没做"，会把排障引向错误方向。
  grep -Eq 'location[[:space:]]+\^~[[:space:]]+/realms/' "${site}" \
    || missing+=("/realms/ 反代到 keycloak（登录页本身，缺了点登录即 404）")
  grep -Eq 'location[[:space:]]+\^~[[:space:]]+/resources/' "${site}" \
    || missing+=("/resources/ 反代到 keycloak（登录页静态资源）")
  grep -Eq 'location[[:space:]]+\^~[[:space:]]+/sso/' "${site}" \
    || missing+=("/sso/ 反代到 adapter（服务端会话端点）")
  grep -Eq 'location[[:space:]]+\^~[[:space:]]+/files/artifacts/' "${site}" \
    || missing+=("/files/artifacts/ 反代到 adapter（产物下载，漏了生产恒定 404）")

  if [[ ${#missing[@]} -gt 0 ]]; then
    err "nginx 配置缺少关键项："
    local item
    for item in "${missing[@]}"; do printf '       · %s\n' "${item}" >&2; done
    global_failure_hint
    exit 1
  fi
  ok "nginx 配置断言通过（SSE 关缓冲 / 探针分流 / 上传上限一致）"
}

# ===========================================================================
# 三、真起栈
# ===========================================================================
#: 从 compose config 里取 proxy 的宿主端口（不写死 80：客户可能改成 8080）
host_port() {
  compose config --format json 2>/dev/null | "${PY}" -c '
import json
import sys

raw = sys.stdin.read().strip()
if not raw:
    print("80")
    raise SystemExit(0)
cfg = json.loads(raw)
ports = (cfg.get("services", {}).get("proxy", {}) or {}).get("ports") or []
for port in ports:
    if isinstance(port, dict):
        print(port.get("published") or "80")
        raise SystemExit(0)
    print(str(port).split(":")[0])
    raise SystemExit(0)
print("80")
'
}

#: 等某个服务变成 healthy（返回 0）或超时（返回 1）。用容器健康状态而不是
#: 端口探测：本栈 postgres 的 healthy 断言了 schema_migration 表，正是我们要等的。
wait_service_healthy() {
  local service="$1"
  local timeout="${2:-120}"
  local deadline=$((SECONDS + timeout))
  local cid status
  while (( SECONDS < deadline )); do
    cid="$(compose ps -q "${service}" 2>/dev/null || true)"
    if [[ -n "${cid}" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null || true)"
      [[ "${status}" == "healthy" ]] && return 0
    fi
    sleep 3
  done
  err "服务 ${service} 在 ${timeout}s 内没有变成 healthy（最后状态：${status:-未创建}）"
  compose logs --tail 30 "${service}" >&2 || true
  global_failure_hint
  return 1
}

#: 把库里的应用角色口令对齐到 .env.lite 的 DSN（**幂等**）。
#:
#: 为什么必须有这一步：`init-app-role.sql` 用占位口令建 `officeai_app`
#: （SQL 没有变量，脚本里写死 `CHANGE_ME_APP_PASSWORD`），并刻意要求运维
#: 首次部署后手工 ALTER ROLE。全新环境下若不先对齐，`migrate --apply`
#: 必然 `28P01 password authentication failed`，而报错只会出现在 migrate 的日志里，
#: 现场很容易误判成"编排写错了"。
#:
#: 为什么用 DSN 里的口令而不是另给一枚：DSN 是唯一真源——库里与配置里
#: 只要不一致，适配层的 /readyz 就永远 not_ready。
align_app_role_password() {
  local dsn password user db superuser pw_sql
  dsn="$(grep -E '^OA_DATABASE_URL=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
  [[ -n "${dsn}" ]] || abort_env "OA_DATABASE_URL 为空：${ENV_FILE}"

  # postgresql+asyncpg://user:password@host:port/db[?params]
  local rest="${dsn#*://}"
  local userinfo="${rest%%@*}"
  local hostpart="${rest#*@}"
  user="${userinfo%%:*}"
  password="${userinfo#*:}"
  password="${password%%\?*}"
  db="${hostpart#*/}"
  db="${db%%\?*}"

  if [[ -z "${user}" || -z "${password}" || "${password}" == "${userinfo}" ]]; then
    abort_env "无法从 OA_DATABASE_URL 解析出 user:password" \
      "格式必须是 postgresql+asyncpg://<用户>:<口令>@<主机>:<端口>/<库>" \
      "口令请用 URL 安全字符（推荐 openssl rand -hex 24），避免 @ : / ? 需要转义"
  fi

  superuser="$(grep -E '^OA_POSTGRES_USER=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
  superuser="${superuser:-officeai}"

  info "启动 postgres 并对齐应用角色口令（${user}）…"
  compose up -d postgres >/dev/null
  wait_service_healthy postgres 180 || exit 1

  # 单引号转义（DSN 口令里出现单引号的概率极低，但不能因此拼出坏 SQL）
  pw_sql="${password//\'/\'\'}"
  if compose exec -T postgres psql -U "${superuser}" -d postgres -v ON_ERROR_STOP=1 \
      -c "ALTER ROLE \"${user}\" PASSWORD '${pw_sql}';" >/dev/null 2>&1; then
    ok "应用角色口令已对齐（${user} @ ${db}）"
  else
    err "调整 ${user} 的口令失败（用超级用户 ${superuser} 连库）"
    compose exec -T postgres psql -U "${superuser}" -d postgres \
      -c "ALTER ROLE \"${user}\" PASSWORD '${pw_sql}';" >&2 || true
    global_failure_hint
    exit 1
  fi
}

run_migrate() {
  info "执行一次性迁移 Job（migrate --apply）…"
  if ! compose --profile migrate run --rm -T migrate; then
    err "迁移失败（数据库未变更或已回滚，按 migrate 的日志判断）"
    global_failure_hint
    exit 1
  fi
  ok "迁移完成"
}

start_stack() {
  info "起栈：docker compose up -d（9 个常驻容器）…"
  if ! compose up -d --remove-orphans; then
    err "docker compose up 失败"
    global_failure_hint
    exit 1
  fi
}

wait_readyz() {
  local port="$1" deadline=$((SECONDS + READY_TIMEOUT)) code
  info "轮询 http://127.0.0.1:${port}/readyz（窗口 ${READY_TIMEOUT}s）…"
  while (( SECONDS < deadline )); do
    code="$(curl -s -o /tmp/lite-readyz.$$ -w '%{http_code}' --max-time 5 \
      "http://127.0.0.1:${port}/readyz" 2>/dev/null || true)"
    if [[ "${code}" == "200" ]]; then
      rm -f /tmp/lite-readyz.$$
      return 0
    fi
    sleep 3
  done
  err "/readyz 在 ${READY_TIMEOUT}s 内没有返回 200（最后状态码：${code:-无}）"
  if [[ -s /tmp/lite-readyz.$$ ]]; then
    echo "       最后一次响应体：" >&2
    head -c 800 /tmp/lite-readyz.$$ | sed 's/^/       /' >&2
    echo >&2
  fi
  rm -f /tmp/lite-readyz.$$
  return 1
}

assert_containers() {
  local running
  running="$(compose ps --status running --format '{{.Service}}' 2>/dev/null | sed '/^$/d' | sort | tr '\n' ' ')"
  local count
  count="$(echo "${running}" | wc -w | tr -d ' ')"
  if [[ "${count}" != "${EXPECTED_CONTAINERS}" ]]; then
    err "运行中的容器数 = ${count}，期望 ${EXPECTED_CONTAINERS}"
    err "实际：${running:-<空>}"
    compose ps >&2 || true
    global_failure_hint
    exit 1
  fi
  ok "运行容器数 = ${EXPECTED_CONTAINERS}"
}

assert_all_healthy() {
  # /readyz 已经 200 说明链路通；这里再确认"9 个都 healthy"——
  # 因为 /readyz 只代表 adapter 自己的五项检查，proxy/web/worker 可能仍是 starting。
  local deadline=$((SECONDS + 120)) snapshot rc
  while (( SECONDS < deadline )); do
    snapshot="$(compose ps --format json 2>/dev/null || true)"
    rc="$("${PY}" -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print("EMPTY"); sys.exit(0)
try:
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
except json.JSONDecodeError:
    data = [json.loads(line) for line in raw.splitlines() if line.strip()]
rows = [r for r in data if (r.get("Service") or "") != "migrate"]
bad = {"%s=%s" % (r.get("Service"), r.get("Health") or r.get("State")) for r in rows if (r.get("Health") or "") != "healthy"}
print("RESULT %d %s" % (len(rows), ",".join(sorted(bad)) or "-"))
' <<<"${snapshot}")"
    if [[ "${rc}" == RESULT\ 9\ -* ]]; then
      ok "9 个容器全部 healthy"
      return 0
    fi
    sleep 3
  done
  err "有容器在 120s 内没有变成 healthy：${rc}"
  compose ps >&2 || true
  global_failure_hint
  exit 1
}

assert_memory() {
  local stats report
  stats="$(docker stats --no-stream --format '{{.Name}};{{.MemUsage}}' 2>/dev/null \
    | grep ';' | grep '^oal-' || true)"
  if [[ -z "${stats}" ]]; then
    err "docker stats 没有拿到任何 oal-* 容器"
    exit 1
  fi
  report="$("${PY}" - "${MEMORY_LIMIT_MIB}" <<PY
import re
import sys

limit = int(sys.argv[1])
stats = """${stats}"""
rows = []
total = 0.0
for line in stats.splitlines():
    name, _, usage = line.partition(";")
    head = (usage.split("/")[0] or "").strip().lower()
    match = re.fullmatch(r"([0-9.]+)\s*([kmg]?)(?:i?b)?", head)
    if not match:
        continue
    value = float(match.group(1))
    factor = {"": 1.0 / (1024 * 1024), "k": 1.0 / 1024, "m": 1.0, "g": 1024.0}[match.group(2)]
    mib = value * factor
    rows.append((name, mib))
    total += mib
rows.sort(key=lambda item: -item[1])
for name, mib in rows:
    print(f"  {name:<22} {mib:8.1f} MiB")
print(f"  {'合计':<20} {total:8.1f} MiB")
if total > limit:
    print(f"OVERSIZE {total:.0f} > {limit}")
else:
    print(f"WITHIN {total:.0f} <= {limit}")
PY
)"
  printf '%s\n' "${report}" | sed '/^WITHIN\|^OVERSIZE/d' | sed 's/^/       /'
  if grep -q '^OVERSIZE' <<<"${report}"; then
    err "总内存超过 6 GiB 判据：${report##*OVERSIZE }"
    err "先看 docs/lite-container-baseline.md §2.5：Keycloak 多数情况下是没限堆"
    global_failure_hint
    exit 1
  fi
  ok "总内存 ${report##*WITHIN } MiB < 6 GiB 判据"
}

# ===========================================================================
# 主流程
# ===========================================================================
main() {
  echo "=================================================================="
  echo " 轻量版起栈校验（$(basename "${COMPOSE_FILE}")）"
  echo " 工作区：${REPO_ROOT}"
  echo "=================================================================="

  check_prereqs
  echo
  echo "--- 一、静态断言 ---"
  run_static_assertions
  run_nginx_assertions

  if [[ "${STATIC_ONLY}" == "1" ]]; then
    echo
    ok "静态校验通过（未起栈）"
    exit 0
  fi

  echo
  echo "--- 二、环境文件检查 ---"
  check_env_content

  echo
  echo "--- 三、应用角色与迁移 ---"
  align_app_role_password
  run_migrate

  echo
  echo "--- 四、起栈与就绪 ---"
  local started=${SECONDS}
  start_stack

  local port
  port="$(host_port)"
  if ! wait_readyz "${port}"; then
    global_failure_hint
    exit 1
  fi
  local ready_seconds=$((SECONDS - started))
  ok "ready in ${ready_seconds}s（判据 ≤${READY_TIMEOUT}s）"

  echo
  echo "--- 五、起栈后断言 ---"
  assert_containers
  assert_all_healthy
  assert_memory

  echo
  echo "=================================================================="
  ok "轻量版起栈校验通过：9 容器 / 全部 healthy / 总内存 < 6 GiB"
  echo "   入口：http://127.0.0.1:${port}/   就绪：http://127.0.0.1:${port}/readyz"
  echo "   ready in ${ready_seconds}s"
  echo "=================================================================="
}

# 只在**直接执行**时跑主流程。
# 作为库被 source（deploy/install.sh 复用 wait_service_healthy / wait_readyz /
# align_app_role_password / assert_containers / assert_all_healthy / abort_env）时
# 必须什么都不做——否则一个 `source` 就会把整个起栈校验连带跑一遍。
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
