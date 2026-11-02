#!/usr/bin/env bash
# ===========================================================================
# 轻量版一键安装（阶段四 T4-05 第三批 3b-1）
#
# 一句话：**在一台干净机器上，从零把 9 容器栈装起来并证明它可用**。
# 只做 install；upgrade / rollback / 备份由 3b-2 的 upgrade.sh 负责。
#
# 用法：
#   bash deploy/install.sh --online                    # 从镜像仓库拉取（tag 不重要，按 digest 拉）
#   bash deploy/install.sh --offline                   # 从离线包加载（deploy/offline/images/*.tar）
#   bash deploy/install.sh --offline --images-dir /mnt/usb/officeai-images
#   bash deploy/install.sh --online --proxy-port 8080  # 80 被占用时换端口（只影响新生成的环境文件）
#   bash deploy/install.sh --online --project-name oalinst --ready-timeout 300
#
# 前置（脚本会自己检查，缺了会明说怎么补）：
#   - Docker 24+ / Compose v2.24+（compose 文件用到 env_file 的 required:false 长语法）
#   - Docker 引擎可用内存 ≥ 8 GiB（架构 §9.1 判据；实测空载 1.93 GiB，留 3 倍抖动余量）
#   - openssl（生成随机口令）、sha256sum（离线包校验、安装台账指纹）
#   - 宿主端口未被占用（默认 80；占用就 --proxy-port 换一个）
#
# 退出码（**每个码都有唯一含义，运维据此决定下一步**）：
#   0  安装成功（也包括"已装过、本次幂等无实质变更"）
#   1  通用失败（某一步执行失败；上面必有一行 [FAIL] 说明是哪一步）
#   2  前置检查未通过（缺命令 / 版本过低 / 内存不足 / 端口被占 / 配置不合规）
#   3  镜像不可用（离线包校验失败、docker load 失败、digest 与 images.lock 不符、拉取失败）
#   4  缺 pgvector 扩展（**本批 install 不会返回此码**；预留给 3b-2 的 upgrade：
#      存量库升级时 vector 扩展缺失/版本 < 0.8，需要 DBA 执行 sql/enable-vector-extension.sql）
#   5  迁移需要 DBA 介入（应用角色 NOSUPERUSER，DDL 被拒；迁移原文已完整打印）
#   6  基线变更（**本批 install 不会返回此码**；预留给 3b-2 的 upgrade：
#      init 脚本或镜像 digest 与 .installed-manifest.json 记录的基线不一致）
#
# 为什么全程强制 `--env-file deploy/.env.lite`：
#   deploy/ 下还有标准栈的 `deploy/.env`，compose 会**自动**读它。不显式指定时，
#   轻量版会悄悄用上标准版的口令（端口、库名也会串），排障时极难看出来。
#
# 为什么口令生成用 `openssl rand -hex 24`：
#   口令要嵌进 DSN 与 URL，base64 的 `+ / =` 需要转义，现场极易配出坏连接串；
#   hex 字符集天然 URL 安全。密钥类（Dify SECRET_KEY / 插件两枚 / 凭据加密密钥）
#   不嵌 URL，按各自格式要求用 base64。
# ===========================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. 定位与"复用而不是重写"
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 复用 deploy/validate_lite_up.sh 里的公共函数（**不写第四份**）：
#   wait_service_healthy / wait_readyz / align_app_role_password / start_stack /
#   check_env_content / abort_env / info / ok / warn / err
# 为什么可以安全 source：
#   1. 该文件末尾的主流程有 `BASH_SOURCE[0] == $0` 守卫，被 source 时不会执行；
#   2. 这里先把位置参数清空，避免它的顶层参数解析循环把 install.sh 的参数吃掉。
_INSTALL_SAVED_ARGS=("$@")
set --
# shellcheck source=./validate_lite_up.sh
source "${SCRIPT_DIR}/validate_lite_up.sh"
set -- "${_INSTALL_SAVED_ARGS[@]}"
unset _INSTALL_SAVED_ARGS

# ---------------------------------------------------------------------------
# 1. 本脚本自己的参数与常量（覆盖上面被 source 带进来的同名默认值）
# ---------------------------------------------------------------------------
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.lite.yml"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.lite.example"
ENV_FILE="${SCRIPT_DIR}/.env.lite"
MANIFEST_FILE="${SCRIPT_DIR}/.installed-manifest.json"
IMAGES_LOCK="${SCRIPT_DIR}/images.lock"
IMAGES_DIR="${SCRIPT_DIR}/offline/images"

MODE=""                      # online | offline
READY_TIMEOUT=180            # dify-api 首启实测 78.1 s、keycloak 81.6 s，180 s 是下限而非余量
PROXY_PORT_OVERRIDE=""       # 空 = 不覆盖，端口以环境文件为准
PROJECT_NAME="officeai-lite" # 与 compose 文件里的 name: 一致（测试可用 --project-name 隔离）
INSTALLER_VERSION="3b-1"
EXPECTED_CONTAINERS=9
MIN_MEMORY_MIB=8192

#: 与 .installed-manifest.json 里登记、且被 compose 挂进 initdb.d 的 5 份脚本一一对应。
#: 3b-2 的 upgrade 用这份指纹判断"现场被人手改过没有"，所以这里**不能漏**。
INIT_SCRIPTS=(
  init-databases.sql
  init-schema-phase2.sql
  init-schema-phase3.sql
  init-schema-phase4.sql
  init-app-role.sql
)

#: images.lock 的解析结果（只解析一次）
declare -A LOCK_DIGEST=()
declare -A LOCK_SOURCE=()
REQUIRED_IMAGES=""
READY_PORT=""

# ---------------------------------------------------------------------------
# 2. 输出与失败处理
# ---------------------------------------------------------------------------
#: 以指定退出码终止（退出码语义见文件头，调用点必须写清"为什么是这个码"）。
#: 第一个参数是消息，后面每个参数各占一行缩进打印——同 abort_env 的排版，
#: 因为安装期的失败信息基本都是"一句结论 + 两三条怎么办"。
die() {
  local code="$1"
  shift
  err "$1"
  if (( $# > 1 )); then
    shift
    local line
    for line in "$@"; do
      printf '       %s\n' "${line}" >&2
    done
  fi
  exit "${code}"
}

usage() {
  cat <<'USAGE'
轻量版一键安装（deploy/install.sh）

用法：
  bash deploy/install.sh (--online | --offline) [选项]

必选其一：
  --online                 从镜像仓库拉取（按 images.lock 的 name@digest 拉，不用 tag）
  --offline                从离线包加载（docker load + 按 digest 核对 RepoDigests）

选项：
  --images-dir <目录>      离线镜像目录（默认 deploy/offline/images）
  --proxy-port <端口>      宿主入口端口；只在新生成 deploy/.env.lite 时写入
  --project-name <名称>    compose 项目名（默认 officeai-lite；卷/网络随之加前缀）
  --env-file <路径>        环境文件（默认 deploy/.env.lite）
  --ready-timeout <秒>     /readyz 就绪窗口（默认 180）
  -h, --help               显示本帮助

退出码：
  0 成功 / 1 通用失败 / 2 前置检查未过 / 3 镜像不可用 / 5 迁移需 DBA
  （4 缺 vector、6 基线变更 预留给 3b-2 的 upgrade.sh）
USAGE
}

banner() {
  echo "=================================================================="
  echo " MOY 轻量版安装（officeai-lite / installer ${INSTALLER_VERSION}）"
  echo " 工作区：${REPO_ROOT}"
  echo " 模式  ：${MODE:-<未指定>}"
  echo " 环境  ：${ENV_FILE}"
  echo " 项目名：${PROJECT_NAME}"
  echo "=================================================================="
}

# ---------------------------------------------------------------------------
# 3. 环境文件读取/写入
# ---------------------------------------------------------------------------
#: 读环境文件里的某个键（不存在或空值返回默认值）。
#: 为什么不 source 环境文件：口令里出现 `$` / 空格 / `#` 时 shell 解析会走样，
#: 而这里要的是"原样字符串"。
env_get() {
  local key="$1"
  local default="${2-}"
  local value=""
  if [[ -f "${ENV_FILE}" ]]; then
    value="$(grep -E "^${key}=" "${ENV_FILE}" 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  fi
  # 去掉 Windows 编辑器留下的 CR，否则口令尾部会多一个 \r（表现为"认证失败但密码看着一样"）
  value="${value%$'\r'}"
  if [[ -z "${value}" ]]; then
    printf '%s' "${default}"
  else
    printf '%s' "${value}"
  fi
}

#: 幂等写键：存在则替换整行，不存在则追加。用临时文件 + mv，避免写一半留坏文件。
env_set() {
  local key="$1"
  local value="$2"
  local tmp="${ENV_FILE}.tmp.$$"
  if grep -qE "^${key}=" "${ENV_FILE}" 2>/dev/null; then
    awk -v k="${key}" -v v="${value}" '
      BEGIN { done = 0 }
      $0 ~ "^" k "=" { print k "=" v; done = 1; next }
      { print }
    ' "${ENV_FILE}" >"${tmp}"
  else
    cat "${ENV_FILE}" >"${tmp}"
    printf '%s=%s\n' "${key}" "${value}" >>"${tmp}"
  fi
  mv "${tmp}" "${ENV_FILE}"
}

#: 从对外基址里取 scheme 与 host（保留运维在样例里改过的域名，只换端口）
url_scheme() { printf '%s' "${1%%://*}"; }
url_host() {
  local rest="$1"
  rest="${rest#*://}"
  printf '%s' "${rest%%[:/]*}"
}

# ---------------------------------------------------------------------------
# 4. 前置检查（退出码 2）
# ---------------------------------------------------------------------------
check_prereqs_install() {
  command -v docker >/dev/null 2>&1 \
    || abort_env "找不到 docker 命令" "安装 Docker 24+ 与 Compose v2 插件后重试（Linux: docker-ce + docker-compose-plugin）"

  local docker_ver
  docker_ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  [[ -n "${docker_ver}" ]] || abort_env \
    "连不上 Docker 守护进程（docker version 无 Server.Version）" \
    "Windows：启动 Docker Desktop 并等托盘图标变绿" \
    "Linux ：systemctl start docker，并确认当前用户在 docker 组（重新登录后生效）"

  local compose_ver
  compose_ver="$(docker compose version --short 2>/dev/null | sed 's/^v//' || true)"
  [[ -n "${compose_ver}" ]] || abort_env "找不到 docker compose（v2）插件" \
    "Docker Desktop 自带；Linux 装 docker-compose-plugin（不是老的 docker-compose v1）"

  # 版本比较用"主版本×1000+次版本"，避免 sort -V 在 Git Bash 与非 GNU 环境上的差异
  local d_major d_minor c_major c_minor
  IFS=. read -r d_major d_minor _ <<<"${docker_ver}"
  IFS=. read -r c_major c_minor _ <<<"${compose_ver}"
  (( 10#${d_major} * 1000 + 10#${d_minor} >= 24000 )) \
    || abort_env "Docker 版本过低（${docker_ver}）" "需要 24.0+（deploy.resources.limits 在更老版本上不生效，会导致内存判据失控）"
  # 2.24 起才有 env_file 的 required:false 长语法；低版本会直接拒绝解析编排文件
  (( 10#${c_major} * 1000 + 10#${c_minor} >= 2024 )) \
    || abort_env "Compose 版本过低（${compose_ver}）" "需要 v2.24+（编排里用了 env_file 的 required:false 长语法）"

  command -v openssl >/dev/null 2>&1 \
    || abort_env "找不到 openssl" "生成随机口令/密钥需要它：Linux 装 openssl；Git Bash 自带"
  command -v sha256sum >/dev/null 2>&1 \
    || abort_env "找不到 sha256sum" "离线包校验与安装台账指纹需要它（coreutils）"
  command -v curl >/dev/null 2>&1 \
    || abort_env "找不到 curl" "轮询 /readyz 需要它"

  [[ -f "${COMPOSE_FILE}" ]] || abort_env "缺编排文件：${COMPOSE_FILE}" "从交付包完整拷贝 deploy/ 目录"
  [[ -f "${IMAGES_LOCK}" ]] || abort_env "缺镜像锁文件：${IMAGES_LOCK}" "images.lock 是 digest 的唯一来源，缺了无法核对现场镜像"
  [[ -f "${SCRIPT_DIR}/nginx/nginx.conf" ]] || abort_env "缺 nginx 主配置：${SCRIPT_DIR}/nginx/nginx.conf"
  # K4-min：proxy 挂的是 `edge.conf`（与 full/saas 共用一套路由）。
  # 检查旧文件 `lite.conf` 会变成"断言了一个不再被挂载的文件"——假绿。
  [[ -f "${SCRIPT_DIR}/nginx/edge.conf" ]] || abort_env "缺 nginx 站点配置：${SCRIPT_DIR}/nginx/edge.conf"

  check_memory
  check_project_name
  check_container_name_collisions
  info "docker ${docker_ver} / compose v${compose_ver} 就绪"
}

#: 内存判据 8 GiB。读的是 **Docker 引擎**可用内存（.MemTotal），不是宿主物理内存——
#: 容器跑在引擎的 VM/cgroup 里，宿主 16 G 而引擎只给 4 G 的机器照样跑不起来。
check_memory() {
  local mem_bytes need_bytes
  mem_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
  mem_bytes="${mem_bytes//[^0-9]/}"
  mem_bytes="${mem_bytes:-0}"
  need_bytes=$(( MIN_MEMORY_MIB * 1024 * 1024 ))
  if (( mem_bytes == 0 )); then
    warn "读不到 Docker 引擎可用内存（docker info .MemTotal 为空），跳过 ≥${MIN_MEMORY_MIB} MiB 检查"
    return 0
  fi
  if (( mem_bytes < need_bytes )); then
    abort_env "Docker 引擎可用内存 $(( mem_bytes / 1024 / 1024 )) MiB < 判据 ${MIN_MEMORY_MIB} MiB" \
      "本栈实测空载 1.93 GiB、峰值接近 5 GiB；再小会被 OOM killer 打断（表现为容器反复重启、日志无异常）" \
      "Docker Desktop：Settings → Resources → Memory 调到 8 GiB 以上" \
      "Linux：扩容宿主内存或加 swap（swap 只救急，不能替代内存）"
  fi
  ok "Docker 引擎可用内存 $(( mem_bytes / 1024 / 1024 )) MiB ≥ ${MIN_MEMORY_MIB} MiB"
}

#: 项目名只能是 compose 允许的字符；大写会被 compose 直接拒绝，提前给出人话解释
check_project_name() {
  if [[ ! "${PROJECT_NAME}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    abort_env "项目名不合法：${PROJECT_NAME}" \
      "只允许小写字母、数字、下划线与连字符，且必须以字母或数字开头（compose 的硬约束）"
  fi
}

#: 容器名冲突检查（实测坑）：`docker-compose.lite.yml` 给 11 个服务都写死了
#: `container_name: oal-*`，所以**换项目名并不能让两套轻量版共存**——
#: 卷和网络会随项目名加前缀，容器名不会。不同项目名第二次 install 撞上的报错是
#: `Conflict. The container name "/oal-valkey" is already in use`，看起来像环境坏了，
#: 其实只是"这台机器上已经有另一套轻量版"。
#: 提前说清楚，比让运维去读 compose 的英文报错强。
check_container_name_collisions() {
  local names name owner conflict=0

  # 容器名从渲染后的编排里取（含 profile），不写死名单——名单会随编排改动漂移
  names="$(compose --profile '*' config 2>/dev/null \
    | awk '/^[[:space:]]*container_name:/{print $2}' | sort -u || true)"
  if [[ -z "${names}" ]]; then
    warn "从编排里没解析到 container_name，跳过容器名冲突检查"
    return 0
  fi

  while IFS= read -r name; do
    [[ -n "${name}" ]] || continue
    if ! docker inspect "${name}" >/dev/null 2>&1; then
      continue # 容器不存在，没有冲突
    fi
    owner="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${name}" 2>/dev/null | tr -d '\r\n' || true)"
    if [[ "${owner}" == "${PROJECT_NAME}" ]]; then
      continue # 是本项目自己的容器（重复执行 install）
    fi
    if [[ -z "${owner}" ]]; then
      err "容器名冲突：${name} 已存在，且不是 compose 创建的（手工 docker run？）"
    else
      err "容器名冲突：${name} 属于 compose 项目 ${owner}（本次项目名 ${PROJECT_NAME}）"
    fi
    conflict=$((conflict + 1))
  done <<<"${names}"

  (( conflict == 0 )) || abort_env \
    "有 ${conflict} 个容器名被占用：这台机器上已经有另一套轻量版在跑" \
    "轻量版容器名是**编排写死的**（oal-*），换项目名只隔离卷和网络，不隔离容器名" \
    "处理：先停掉另一套（cd <另一套的 deploy 目录> 后执行 docker compose down），再重跑本脚本" \
    "或者：直接用**同一个** --project-name 接管已有那套（重复执行 install 是幂等的）"
  ok "容器名无冲突（${PROJECT_NAME}）"
}

# ---------------------------------------------------------------------------
# 5. 环境文件：不存在则生成（含全部口令），存在则**原样保留**
# ---------------------------------------------------------------------------
#: 端口探测：能连上就说明有人在听。用 bash 的 /dev/tcp 而不是 ss/lsof——
#: 客户机器上这两个工具经常没装，而 bash 一定有。
port_listening() {
  ( exec 3<>"/dev/tcp/127.0.0.1/$1" ) >/dev/null 2>&1
}

generate_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    # 重复执行 install 的路径：口令已经在用，重新生成等于把线上口令换掉
    info "环境文件已存在，保持不动（不覆盖既有口令）：${ENV_FILE}"
    # 但**派生键**必须补：K1 起 `OA_PUBLIC_BASE_URL` 是 compose 的必填项
    # （`${VAR:?}`），老现场的环境文件里没有这个键 → 升级后 `compose up`
    # 直接拒绝启动。只补缺失/仍是旧占位值的键，已有人工值的键一律不动。
    backfill_derived_env
    return 0
  fi
  [[ -f "${ENV_EXAMPLE}" ]] || abort_env "缺环境样例：${ENV_EXAMPLE}" "从交付包完整拷贝 deploy/ 目录"

  info "生成环境文件：${ENV_FILE}（从 $(basename "${ENV_EXAMPLE}") 复制后填随机值）"
  # 先收紧 umask 再落地，避免"文件写到一半、口令已被同机其他用户读到"
  local saved_umask
  saved_umask="$(umask)"
  umask 077
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  umask "${saved_umask}"

  local pg_pw vk_pw kc_pw kc_secret jwt_secret dify_secret
  local plugin_key inner_key cred_key app_pw dsn
  # 口令类：hex 24（48 字符，URL 安全，128 bit 熵）
  pg_pw="$(openssl rand -hex 24)"
  vk_pw="$(openssl rand -hex 24)"
  kc_pw="$(openssl rand -hex 24)"
  kc_secret="$(openssl rand -hex 24)"
  jwt_secret="$(openssl rand -hex 24)"
  app_pw="$(openssl rand -hex 24)"
  # 密钥类：不嵌 URL，按各自格式要求用 base64
  dify_secret="$(openssl rand -base64 32 | tr -d '\n')"
  plugin_key="$(openssl rand -base64 32 | tr -d '\n')"
  # 两枚插件密钥必须不同（同值会让插件守护的内部鉴权形同虚设），所以再抽一次
  inner_key="$(openssl rand -base64 32 | tr -d '\n')"
  cred_key="$(openssl rand -base64 32 | tr -d '\n')"

  env_set OA_POSTGRES_PASSWORD "${pg_pw}"
  env_set OA_VALKEY_PASSWORD "${vk_pw}"
  env_set OA_KEYCLOAK_ADMIN_PASSWORD "${kc_pw}"
  env_set OA_KEYCLOAK_CLIENT_SECRET "${kc_secret}"
  env_set OA_JWT_SECRET "${jwt_secret}"
  env_set OA_DIFY_SECRET_KEY "${dify_secret}"
  env_set OA_PLUGIN_DAEMON_KEY "${plugin_key}"
  env_set OA_PLUGIN_INNER_API_KEY "${inner_key}"
  env_set OA_CREDENTIAL_ENCRYPTION_KEY "${cred_key}"

  # DSN：只换口令，保留样例里的主机/端口/库名（运维可能改过）
  dsn="$(env_get OA_DATABASE_URL "")"
  if [[ -n "${dsn}" ]]; then
    env_set OA_DATABASE_URL "${dsn/CHANGE_ME_APP_PASSWORD/${app_pw}}"
  else
    env_set OA_DATABASE_URL "postgresql+asyncpg://officeai_app:${app_pw}@postgres:5432/officeai"
  fi

  # 安装模式与运行期"是否允许出站"是同一件事，写进环境文件免得两处不一致
  case "${MODE}" in
    offline) env_set OA_OFFLINE_MODE true ;;
    online) env_set OA_OFFLINE_MODE false ;;
  esac

  apply_proxy_port_on_new_file

  chmod 600 "${ENV_FILE}" 2>/dev/null || warn "无法把 ${ENV_FILE} 权限设为 600（Windows 上属正常），请确认目录权限"
  ok "环境文件已生成（9 项口令/密钥全部随机，权限 600）"
}

#: 只在**新生成**环境文件时写端口与对外基址。
#: 为什么对外基址必须跟着端口改：Dify 用它生成文件/回调链接，端口不一致时
#: 客户点开下载链接会打到 80 端口 → 404。
apply_proxy_port_on_new_file() {
  local port url scheme host base_url realm
  port="${PROXY_PORT_OVERRIDE:-$(env_get OA_LITE_PROXY_PORT 80)}"
  port="${port:-80}"
  url="$(env_get OA_LITE_PUBLIC_URL http://localhost)"
  scheme="$(url_scheme "${url}")"
  host="$(url_host "${url}")"
  [[ -n "${scheme}" ]] || scheme="http"
  [[ -n "${host}" ]] || host="localhost"
  env_set OA_LITE_PROXY_PORT "${port}"
  if [[ "${port}" == "80" ]]; then
    base_url="${scheme}://${host}"
  else
    base_url="${scheme}://${host}:${port}"
  fi
  env_set OA_LITE_PUBLIC_URL "${base_url}"

  # ---------------------------------------------------------------------
  # K1 / R4（2026-11-02）：对外基址派生出三个键。
  #
  # 为什么必须在这里由脚本写，而不是让部署者手填四处：
  #   OA_PUBLIC_BASE_URL / OA_JWT_ISSUER / OA_WEB_OIDC_ISSUER 三者必须**逐字符**
  #   自洽（后者 = <基址>/realms/<realm>）。手填等于把"三处一致"变成一个靠人记的
  #   纪律，而这套系统里最贵的故障正是"两个配置各写一份、其中一份没改"。
  #   与 OA_LITE_PUBLIC_URL 同源，也保证轻量版只有一个对外地址事实来源。
  # ---------------------------------------------------------------------
  realm="$(env_get OA_KEYCLOAK_REALM officeai)"
  realm="${realm:-officeai}"
  # **不写 OA_PUBLIC_BASE_URL**：轻量版编排里它是 `OA_PUBLIC_BASE_URL: ${OA_LITE_PUBLIC_URL}`
  # 的插值。再往 .env.lite 写一个字面量，会让"谁生效"取决于变量替换顺序——
  # 这正是"配了、看起来生效了、其实没生效"的最坏形态。
  env_set OA_JWT_ISSUER "${base_url}/realms/${realm}"
  env_set OA_WEB_OIDC_ISSUER "${base_url}/realms/${realm}"
  env_set OA_WEB_OIDC_CLIENT_ID "$(env_get OA_KEYCLOAK_CLIENT_ID officeai-adapter)"

  # ---------------------------------------------------------------------
  # realm 模板渲染：把上面算出的基址写进 `keycloak/rendered/realm-officeai.json`。
  # 渲染产物不进库（含现场域名），但必须"有"，否则 `keycloak-import` 服务挂载
  # 不到文件（bind mount 一个不存在的路径会变成空目录，报错更难懂）。
  # ---------------------------------------------------------------------
  render_realm_template "${base_url}"
}

#: 渲染 realm 模板（幂等；失败只告警不中断）。
#:
#: Args:
#:   $1: 对外基址（不含尾斜杠）。
render_realm_template() {
  local base_url="$1"
  [[ -f "${SCRIPT_DIR}/keycloak/render-realm.sh" ]] || return 0
  if OA_PUBLIC_BASE_URL="${base_url}" \
     KC_SSL_REQUIRED="${KC_SSL_REQUIRED:-}" \
     sh "${SCRIPT_DIR}/keycloak/render-realm.sh" >/dev/null; then
    ok "realm 模板已渲染（keycloak/rendered/realm-officeai.json）"
  else
    # 不中断安装：realm 导入是**后置**动作，缺它不影响栈起来；
    # 但必须让部署者看到，否则等到"登录跳不出去"时已无从追溯。
    warn "realm 模板渲染失败，登录链路需人工处理：sh deploy/keycloak/render-realm.sh"
  fi
}

#: 升级路径：给**既有**环境文件补派生键（只补缺失 / 旧占位值，绝不覆盖人工值）。
#:
#: 为什么需要它：`OA_PUBLIC_BASE_URL` 在编排里是 `${VAR:?}`（缺失即拒绝启动），
#: 而 K1 之前生成的环境文件里没有这个键。只"保持不动"会让每一个老现场
#: 升级后都起不来，且报错只说"变量缺失"，不会提示"跑一次 install.sh 就好"。
backfill_derived_env() {
  local base_url realm added=0 current legacy_issuer

  # 基址唯一来源是 OA_LITE_PUBLIC_URL（编排把它插值给 OA_PUBLIC_BASE_URL）。
  # 这里**只读不写** OA_PUBLIC_BASE_URL，理由见 apply_proxy_port_on_new_file。
  base_url="$(env_get OA_LITE_PUBLIC_URL http://localhost)"
  base_url="${base_url%/}"

  realm="$(env_get OA_KEYCLOAK_REALM officeai)"
  realm="${realm:-officeai}"

  # `iss` 校验值：空值或**仍是历史占位值**（`officeai`）时才改。
  # 历史占位值下 OIDC 登录本来就不可用（R4：没有 realm、issuer 不可达），
  # 所以覆盖它不会破坏任何"正在工作"的配置；但若运维填了别的值，一律尊重。
  legacy_issuer="$(env_get OA_JWT_ISSUER "")"
  if [[ -z "${legacy_issuer}" || "${legacy_issuer}" == "officeai" ]]; then
    env_set OA_JWT_ISSUER "${base_url}/realms/${realm}"
    added=1
  elif [[ "${legacy_issuer}" != "${base_url}/realms/${realm}" ]]; then
    warn "OA_JWT_ISSUER 与对外基址不自洽：${legacy_issuer} ≠ ${base_url}/realms/${realm}"
    warn "  登录会失败（令牌 iss 校验不通过）。确认哪个是对的，然后改到一致。"
  fi

  if [[ -z "$(env_get OA_WEB_OIDC_ISSUER "")" ]]; then
    env_set OA_WEB_OIDC_ISSUER "${base_url}/realms/${realm}"
    added=1
  fi
  if [[ -z "$(env_get OA_WEB_OIDC_CLIENT_ID "")" ]]; then
    env_set OA_WEB_OIDC_CLIENT_ID "$(env_get OA_KEYCLOAK_CLIENT_ID officeai-adapter)"
    added=1
  fi

  if [[ ${added} -eq 1 ]]; then
    ok "既有环境文件已补齐派生键（对外基址 / 两个 issuer / client id），其余值未改动"
    warn "派生键变了 → 前端必须重建才生效：docker compose build web（restart 不够）"
  fi
  render_realm_template "${base_url}"
}

#: 环境文件内容检查：CHANGE_ME 归零、必需键非空、端口与基址一致（复用校验脚本的实现）
check_env_secrets() {
  check_env_content

  # 凭据加密密钥：必须是 **32 字节的 base64**。长度不对不会在启动时报错，
  # 而是等第一个用户点知识库才抛异常——那时现场已经算"上线完成"了。
  local key bytes
  key="$(env_get OA_CREDENTIAL_ENCRYPTION_KEY "")"
  [[ -n "${key}" ]] || abort_env "OA_CREDENTIAL_ENCRYPTION_KEY 为空" \
    "生成：openssl rand -base64 32"
  bytes="$(printf '%s' "${key}" | openssl base64 -d -A 2>/dev/null | wc -c | tr -d ' ')"
  [[ "${bytes}" == "32" ]] || abort_env \
    "OA_CREDENTIAL_ENCRYPTION_KEY 不是 32 字节（解出 ${bytes:-0} 字节）" \
    "生成：openssl rand -base64 32（必须是 32 字节的 base64，不是 hex，也不是任意长字符串）"

  # 两枚插件密钥同值 = 内部鉴权退化成一个公开常量（审计 OA-AUDIT-001 的教训）
  local plugin_key inner_key
  plugin_key="$(env_get OA_PLUGIN_DAEMON_KEY "")"
  inner_key="$(env_get OA_PLUGIN_INNER_API_KEY "")"
  if [[ -n "${plugin_key}" && "${plugin_key}" == "${inner_key}" ]]; then
    abort_env "OA_PLUGIN_DAEMON_KEY 与 OA_PLUGIN_INNER_API_KEY 相同" \
      "两枚必须是**不同**的随机值（同值等于把内部鉴权降级成公开常量）" \
      "生成：openssl rand -base64 32"
  fi

  ok "环境文件内容检查通过（无 CHANGE_ME 残留、必需键齐全、密钥格式正确）"
}

#: 端口占用检查。先解析出"本次实际会用的端口"，再判断是否被**别人**占着。
check_port_free() {
  local port="$1"
  [[ "${port}" =~ ^[0-9]+$ ]] || abort_env "端口不是数字：${port}" "检查环境文件里的 OA_LITE_PROXY_PORT"
  if ! port_listening "${port}"; then
    ok "宿主端口 ${port} 空闲"
    return 0
  fi
  # 已被占用：如果占用者就是**本项目的 proxy 容器**（重复执行 install），放行。
  # 否则第二次 install 会因为"自己的端口被自己占着"而失败，幂等性无从谈起。
  local cid
  cid="$(compose ps -q proxy 2>/dev/null | head -n 1 || true)"
  if [[ -n "${cid}" ]] && [[ -n "$(docker ps -q --filter "id=${cid}" 2>/dev/null || true)" ]]; then
    warn "端口 ${port} 由本项目 proxy 容器占用（重复执行 install，放行）"
    return 0
  fi
  abort_env "宿主端口 ${port} 已被占用" \
    "换端口：bash deploy/install.sh --online --proxy-port 8080（只影响新生成的环境文件）" \
    "查占用者：Linux 用 ss -ltnp | grep :${port}；Windows 用 netstat -ano | findstr :${port}" \
    "本栈只暴露 proxy 一个端口，换掉它不影响其他组件"
}

#: 本次实际使用的入口端口（既有安装以环境文件为准）
effective_proxy_port() {
  local port
  port="$(env_get OA_LITE_PROXY_PORT 80)"
  port="${port:-80}"
  if [[ -n "${PROXY_PORT_OVERRIDE}" && "${PROXY_PORT_OVERRIDE}" != "${port}" ]]; then
    warn "--proxy-port ${PROXY_PORT_OVERRIDE} 被忽略：${ENV_FILE} 已存在，端口以文件里的 ${port} 为准"
    warn "  要改端口：先改 ${ENV_FILE} 的 OA_LITE_PROXY_PORT 与 OA_LITE_PUBLIC_URL，再重跑"
  fi
  printf '%s' "${port}"
}

# ---------------------------------------------------------------------------
# 6. 镜像：锁文件解析 → 所需清单 → 拉取或加载 → 按 digest 全量核对
# ---------------------------------------------------------------------------
#: 解析 images.lock 的 name/tag/digest/source。
#: 字段顺序在不同条目里并不固定（pgvector 那条 source 在 digest 前面），
#: 所以必须"攒够一个条目再输出"，不能逐行 grep。
load_images_lock() {
  local table
  table="$(awk '
    function flush() {
      if (name != "") { printf "%s:%s\t%s\t%s\n", name, tag, digest, source }
      name = ""; tag = ""; digest = ""; source = ""
    }
    /^[[:space:]]*-[[:space:]]*name:/ {
      flush()
      name = $0; sub(/^[[:space:]]*-[[:space:]]*name:[[:space:]]*/, "", name); gsub(/"/, "", name); next
    }
    /^[[:space:]]*tag:/ {
      tag = $0; sub(/^[[:space:]]*tag:[[:space:]]*/, "", tag); gsub(/"/, "", tag); next
    }
    /^[[:space:]]*digest:/ {
      digest = $0; sub(/^[[:space:]]*digest:[[:space:]]*/, "", digest); gsub(/"/, "", digest); next
    }
    /^[[:space:]]*source:/ {
      source = $0; sub(/^[[:space:]]*source:[[:space:]]*/, "", source); gsub(/"/, "", source); next
    }
    END { flush() }
  ' "${IMAGES_LOCK}")"

  local ref dig src
  while IFS=$'\t' read -r ref dig src; do
    [[ -n "${ref}" ]] || continue
    LOCK_DIGEST["${ref}"]="${dig}"
    LOCK_SOURCE["${ref}"]="${src:-docker-hub}"
  done <<<"${table}"

  (( ${#LOCK_DIGEST[@]} > 0 )) || die 2 "images.lock 解析不出任何镜像（文件损坏？）：${IMAGES_LOCK}"
  info "镜像锁文件已解析：${#LOCK_DIGEST[@]} 个条目"
}

#: 编排**实际需要**哪些镜像：交给 compose 自己算（含 profiles 里的 dify-web / migrate），
#: 比手工维护一份名单可靠——名单会随编排改动漂移，而漂移的表现是"少拉一个镜像，
#: 起栈时才 404"。
collect_required_images() {
  local out count
  out="$(compose --profile '*' config --images 2>/dev/null | sed '/^[[:space:]]*$/d' | sort -u || true)"
  [[ -n "${out}" ]] || die 2 "无法从编排解析镜像清单（docker compose config --images 返回空）"
  REQUIRED_IMAGES="${out}"
  count="$(printf '%s\n' "${out}" | grep -c . || true)"
  info "编排需要 ${count} 个镜像（含 profile 内的 dify-web / migrate 所用镜像）"
}

#: 本地某个引用是否已经是锁文件登记的那份 digest。
#: 比的是 RepoDigests 里的 manifest digest：tag 可以被上游重推覆盖，digest 不能。
image_has_digest() {
  local ref="$1"
  local want="$2"
  local got
  got="$(docker inspect --format '{{json .RepoDigests}}' "${ref}" 2>/dev/null || true)"
  [[ "${got}" == *"${want}"* ]]
}

image_present() {
  docker image inspect "$1" >/dev/null 2>&1
}

#: online：按 **name@digest** 拉取。
#: 为什么不用 tag：上游重推同名 tag 会静默换掉镜像内容，装出来的东西就不是验收过的那份了。
acquire_images_online() {
  local ref repo dig
  while IFS= read -r ref; do
    [[ -n "${ref}" ]] || continue
    dig="${LOCK_DIGEST[${ref}]:-}"
    repo="${ref%:*}"
    if [[ -z "${dig}" ]]; then
      # 没有 digest 的条目交给下面统一的 gate 报错/告警，这里不重复判断
      continue
    fi
    if image_has_digest "${ref}" "${dig}"; then
      ok "已就位（digest 匹配）：${ref}"
      continue
    fi
    info "拉取 ${repo}@${dig:0:19}…"
    docker pull "${repo}@${dig}" || die 3 "拉取失败：${repo}@${dig}" \
      "内网无外网时改用 --offline（离线包）" \
      "或先在有网机器 docker pull 后 docker save 成 tar"
    # 按 digest 拉下来不会自动给 name:tag 打标，而 compose 是按 tag 找镜像的
    docker tag "${repo}@${dig}" "${ref}" || die 3 "给 ${ref} 打标失败（digest 已拉到本地但没有 tag）"
    ok "拉取并打标完成：${ref}"
  done <<<"${REQUIRED_IMAGES}"
}

#: offline：校验清单 → 逐个 docker load → digest 核对在 verify_images_by_digest 里统一做
acquire_images_offline() {
  local cksum="" candidate tar loaded=0
  local -a tars=()

  [[ -d "${IMAGES_DIR}" ]] || abort_env "缺离线镜像目录：${IMAGES_DIR}" \
    "离线包应包含 images/*.tar 与 checksums.sha256" \
    "也可用 --images-dir 指定介质挂载点（如 /mnt/usb/officeai-images）"

  # 校验清单允许放在几处常见位置（不同批次的打包脚本命名不完全一致）
  for candidate in \
    "${IMAGES_DIR}/checksums.sha256" \
    "${IMAGES_DIR}/checksums.txt" \
    "${SCRIPT_DIR}/offline/checksums.txt" \
    "${SCRIPT_DIR}/offline/manifests/checksums.sha256"; do
    if [[ -f "${candidate}" ]]; then
      cksum="${candidate}"
      break
    fi
  done
  [[ -n "${cksum}" ]] || abort_env "离线模式必须提供校验清单，但一个都没找到" \
    "期望位置：${IMAGES_DIR}/checksums.sha256 或 ${IMAGES_DIR}/checksums.txt" \
    "生成：cd ${IMAGES_DIR} && sha256sum *.tar > checksums.sha256" \
    "没有清单就无法证明 tar 在拷盘过程中没损坏（镜像 tar 损坏只在 docker load 一半时才暴露）"
  verify_checksums "${cksum}"

  shopt -s nullglob
  tars=("${IMAGES_DIR}"/*.tar)
  shopt -u nullglob
  [[ ${#tars[@]} -gt 0 ]] || abort_env "离线镜像目录里没有任何 .tar：${IMAGES_DIR}" \
    "检查挂载点是否真的挂上了（U 盘/镜像目录挂空目录是最常见的一次失败）"

  for tar in "${tars[@]}"; do
    info "加载镜像包：$(basename "${tar}") …"
    docker load -i "${tar}" >/dev/null || die 3 "docker load 失败：${tar}" \
      "先确认磁盘空间（docker system df）与文件完整性（sha256sum -c）"
    loaded=$((loaded + 1))
  done
  ok "已加载 ${loaded} 个镜像包"
}

#: sha256sum -c 在清单所在目录里执行（清单里写的是相对文件名）
verify_checksums() {
  local file="$1"
  local dir
  dir="$(cd "$(dirname "${file}")" && pwd)"
  info "校验离线包完整性：$(basename "${file}") …"
  if ( cd "${dir}" && sha256sum -c --quiet "$(basename "${file}")" ); then
    ok "校验清单全部通过"
  else
    die 3 "离线包校验失败（上面列出了不匹配的文件）" \
      "不要继续安装：先重新拷贝离线包" \
      "镜像 tar 损坏的表现是 docker load 中途报 unexpected EOF，或装出来 digest 与 images.lock 不符"
  fi
}

#: 统一出口：每个所需镜像都必须能对上 images.lock 的 digest。
#: 上游镜像缺 digest 或缺镜像 → 退出码 3（不能带着"不知道装的是哪份"的镜像上线）；
#: 自研镜像（source: local）只告警，因为它的 digest 每次重建都变。
verify_images_by_digest() {
  local ref dig src bad=0 warned=0
  while IFS= read -r ref; do
    [[ -n "${ref}" ]] || continue
    dig="${LOCK_DIGEST[${ref}]:-}"
    src="${LOCK_SOURCE[${ref}]:-docker-hub}"
    if [[ -z "${dig}" ]]; then
      if [[ "${src}" == "local" ]]; then
        warn "${ref} 未在 images.lock 登记 digest（自研镜像，允许缺失）"
        warned=$((warned + 1))
        if ! image_present "${ref}"; then
          err "本地也没有该镜像：${ref}"
          bad=$((bad + 1))
        fi
      else
        err "${ref} 未在 images.lock 登记 digest —— 无法与验收单对账"
        bad=$((bad + 1))
      fi
      continue
    fi
    if image_has_digest "${ref}" "${dig}"; then
      ok "digest 核对通过：${ref}"
    else
      err "digest 不符或镜像缺失：${ref}"
      err "  期望：${dig}"
      err "  实际：$(docker inspect --format '{{json .RepoDigests}}' "${ref}" 2>/dev/null || echo '<镜像不存在>')"
      bad=$((bad + 1))
    fi
  done <<<"${REQUIRED_IMAGES}"

  (( bad == 0 )) || die 3 "有 ${bad} 个镜像不满足 digest 要求（见上面每条 [FAIL]）" \
    "online：重跑会按 digest 重新拉取" \
    "offline：说明离线包与 images.lock 不是同一批次，两者必须成对交付"

  if (( warned > 0 )); then
    warn "有 ${warned} 个自研镜像没有 digest 可核对（已在上面逐条标注）"
  fi
  ok "全部所需镜像均可用"
}

# ---------------------------------------------------------------------------
# 7. 安装步骤
# ---------------------------------------------------------------------------
#: 先只起 PG 与 Valkey，等它们真的 healthy，再做角色口令与迁移。
#: 为什么不等整栈：adapter/keycloak 都依赖 PG，PG 没好就 up 全栈只会让一堆容器
#: 反复重启，日志互相淹没。分两步能把"库没起来"和"应用没起来"分开定位。
step_start_infra() {
  info "起基础设施（postgres / valkey）…"
  compose up -d postgres valkey || die 1 "docker compose up postgres valkey 失败（看上面 compose 的输出）"
  # postgres 的 healthy 断言了 schema_migration 表存在（不是只 pg_isready），
  # 所以这里等到 healthy 就等于"initdb 已经把 4 份脚本跑完"。
  wait_service_healthy postgres 240 || die 1 "postgres 未在 240s 内变成 healthy（initdb 失败见上面日志）"
  wait_service_healthy valkey 120 || die 1 "valkey 未在 120s 内变成 healthy（口令或内存上限问题）"
  ok "postgres / valkey 均 healthy"
}

#: 迁移：一次性 Job，失败要按 RAISE 原文区分"权限/归属"与"其他错误"。
#: 为什么要区分：应用角色是 NOSUPERUSER + NOBYPASSRLS（这是隔离生效的前提），
#: 它本来就**不该**能做 DDL。所以"must be owner"不是脚本写错了，是流程要求 DBA。
#:
#: 为什么把输出收到变量里而不是写临时文件：Git Bash 的 TMPDIR 是 `C:\...` 形态，
#: `mktemp` 会返回带盘符的路径，随后 `rm` 会被安全删除钩子拒绝（实测
#: `[safe-delete][SAFE_DELETE_INVALID_PATH] embedded drive prefix is not allowed`），
#: 在 `set -e` 下直接把脚本打死——而那时迁移其实**已经成功**了。
#: 迁移输出只有几十行，收进变量没有任何代价，还顺手消掉了"临时文件没清干净"这个隐患。
step_migrate() {
  local out="" rc=0

  info "执行一次性迁移 Job（--profile migrate run --rm migrate）…"
  out="$(compose --profile migrate run --rm -T migrate 2>&1)" || rc=$?

  # 迁移输出不长，全程给出：出问题时它是唯一证据，事后补捞往往已经被新日志冲掉
  if [[ -n "${out}" ]]; then
    printf '%s\n' "${out}" | sed 's/^/       /'
  fi

  if (( rc == 0 )); then
    ok "迁移完成（库结构已到目标版本）"
    return 0
  fi

  if grep -qEi "must be owner|permission denied|insufficient_privilege|42501|not owner of" <<<"${out}"; then
    err "迁移被数据库拒绝：应用角色没有 DDL 权限（这正是设计意图，不是配置错误）"
    cat >&2 <<'TIP'
       含义：业务库的表由超级用户（POSTGRES_USER）创建，应用角色 officeai_app 只有
             DML 权限，且带 NOBYPASSRLS —— 多租户隔离能生效的前提就是它不能绕过策略。
       怎么做（二选一）：
         A. 把上面日志里被拒的语句交给 DBA，用超级用户执行后重跑本脚本；
         B. 仅限"全新库、无数据"：删卷重来（docker compose down -v），
            让 initdb 用 init-schema-*.sql 建出完整结构。
       退出码 5：明确区别于"脚本/环境有问题"的 1，便于运维直接转给 DBA。
TIP
    exit 5
  fi

  err "迁移失败（退出码 ${rc}），不是 DBA 权限问题"
  echo "       排查顺序：" >&2
  echo "         1) compose ps（看 postgres 是否 healthy）" >&2
  echo "         2) compose logs --tail 40 postgres" >&2
  echo "         3) 确认 OA_DATABASE_URL 与库里 officeai_app 的口令一致（本脚本已自动对齐）" >&2
  exit 1
}

#: 全栈起起来。复用校验脚本的 start_stack（up -d --remove-orphans，失败即 exit 1）。
step_up_full() {
  ensure_edge_cert
  start_stack
}

#: G1（审计 2026-11-02 P0-4）：边缘已升级为 TLS 终止（80 只做探活与 301 跳转，
#: 443 承载全部路由），nginx 启动时**必须**能读到证书，否则 proxy/edge 容器起不来。
#: 现场已有证书（真实证书或早前生成的自签）则原样保留；没有才生成自签。
ensure_edge_cert() {
  local dir="${SCRIPT_DIR}/nginx/tls"
  if [[ -s "${dir}/fullchain.pem" && -s "${dir}/privkey.pem" ]]; then
    ok "边缘 TLS 证书已就位（${dir}，不覆盖既有证书）"
    return 0
  fi
  warn "未发现边缘 TLS 证书，生成自签一对（**仅限验证**）"
  sh "${SCRIPT_DIR}/gen-selfsigned-cert.sh" || die 1 "生成自签证书失败（需要 openssl）"
  warn "当前是自签证书：浏览器告警属预期；上线前请以真实证书覆盖 ${dir} 下同名两件（chmod 600 私钥）"
}

#: 等全部常驻容器 healthy。
#: 为什么不复用 validate_lite_up.sh 的 assert_all_healthy：它靠 python 解析 JSON，
#: 而安装脚本要在只有 docker 的客户机器上跑。这里用 compose 的模板输出，零外部依赖。
wait_all_healthy() {
  local deadline=$(( SECONDS + 120 ))
  local snapshot count bad
  while (( SECONDS < deadline )); do
    snapshot="$(compose ps --format '{{.Service}}={{.Health}}' 2>/dev/null | sed '/^[[:space:]]*$/d' | grep -v '^migrate=' || true)"
    count="$(printf '%s\n' "${snapshot}" | grep -c . || true)"
    bad="$(printf '%s\n' "${snapshot}" | grep -v '=healthy' || true)"
    if [[ "${count}" == "${EXPECTED_CONTAINERS}" && -z "${bad}" ]]; then
      ok "${EXPECTED_CONTAINERS} 个常驻容器全部 healthy"
      return 0
    fi
    sleep 3
  done
  err "有容器在 120s 内没有变成 healthy（运行中 ${count:-0} 个，判据 ${EXPECTED_CONTAINERS} 个）"
  [[ -z "${bad}" ]] || printf '       %s\n' "${bad}" >&2
  compose ps >&2 || true
  global_failure_hint
  return 1
}

#: 一次性管理员口令：只在**首次安装**打印。
#: 为什么只打印一次：它会留在终端回滚缓冲、CI 日志、截图里。重复执行 install
#: 不应该无限次把生产口令刷到屏幕上——需要时让运维自己去 600 权限的文件里读。
step_print_admin_secret() {
  local first_install="$1"
  local user pass

  if [[ "${first_install}" != "1" ]]; then
    info "已安装过（存在 $(basename "${MANIFEST_FILE}")），本次不再打印管理员口令"
    info "需要时自行查看：grep -E '^OA_KEYCLOAK_ADMIN' ${ENV_FILE}（该文件权限 600）"
    return 0
  fi

  user="$(env_get OA_KEYCLOAK_ADMIN admin)"
  pass="$(env_get OA_KEYCLOAK_ADMIN_PASSWORD "")"
  [[ -n "${pass}" ]] || { warn "OA_KEYCLOAK_ADMIN_PASSWORD 为空，跳过口令打印"; return 0; }

  printf '\n'
  echo "================================================================"
  echo " Keycloak 管理员凭据 —— 只打印这一次，请立即保存到密码管理器"
  echo "================================================================"
  printf '   用户名：%s\n' "${user}"
  printf '   口  令：%s\n' "${pass}"
  echo "----------------------------------------------------------------"
  printf ' 保存后请清理本终端回滚缓冲。重装不会覆盖 %s，\n' "$(basename "${ENV_FILE}")"
  echo " 也就不会再打印；丢失后只能进容器用 kcadm 重置。"
  echo "================================================================"
}

#: 安装台账：版本 + 9 个镜像 digest + 5 份 init 脚本 sha256 + 安装时间。
#: 3b-2 的 upgrade 靠它做基线指纹比对（"现场还是不是当初装的那份"），
#: 所以 installed_at 在重复执行时**保留首次值**，只更新 last_run_at。
write_manifest() {
  local first_install="$1"
  local last_run_at installed_at version ref dig sha script first

  last_run_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  installed_at=""
  if [[ -f "${MANIFEST_FILE}" ]]; then
    installed_at="$(sed -n 's/.*"installed_at":[[:space:]]*"\([^"]*\)".*/\1/p' "${MANIFEST_FILE}" | head -n 1)"
  fi
  [[ -n "${installed_at}" ]] || installed_at="${last_run_at}"
  version="$(env_get OA_IMAGE_TAG 1.0.0)"

  local tmp="${MANIFEST_FILE}.tmp.$$"
  {
    printf '{\n'
    printf '  "manifest_version": 1,\n'
    printf '  "product": "officeai-lite",\n'
    printf '  "version": "%s",\n' "${version}"
    printf '  "installer": "deploy/install.sh",\n'
    printf '  "installer_version": "%s",\n' "${INSTALLER_VERSION}"
    printf '  "installed_at": "%s",\n' "${installed_at}"
    printf '  "last_run_at": "%s",\n' "${last_run_at}"
    printf '  "project_name": "%s",\n' "${PROJECT_NAME}"
    printf '  "proxy_port": %s,\n' "${READY_PORT}"
    printf '  "public_url": "%s",\n' "$(env_get OA_LITE_PUBLIC_URL http://localhost)"
    printf '  "compose_file": "deploy/docker-compose.lite.yml",\n'
    printf '  "compose_file_sha256": "sha256:%s",\n' "$(sha256sum "${COMPOSE_FILE}" | cut -d' ' -f1)"

    printf '  "images": {\n'
    first=1
    while IFS= read -r ref; do
      [[ -n "${ref}" ]] || continue
      dig="${LOCK_DIGEST[${ref}]:-}"
      if (( first == 1 )); then
        first=0
      else
        printf ',\n'
      fi
      printf '    "%s": "%s"' "${ref}" "${dig:-unknown}"
    done <<<"${REQUIRED_IMAGES}"
    printf '\n  },\n'

    printf '  "init_scripts": {\n'
    first=1
    for script in "${INIT_SCRIPTS[@]}"; do
      if [[ -f "${SCRIPT_DIR}/${script}" ]]; then
        sha="$(sha256sum "${SCRIPT_DIR}/${script}" | cut -d' ' -f1)"
      else
        warn "缺 init 脚本 ${SCRIPT_DIR}/${script}（台账里记为空）"
        sha=""
      fi
      if (( first == 1 )); then
        first=0
      else
        printf ',\n'
      fi
      printf '    "%s": "sha256:%s"' "${script}" "${sha}"
    done
    printf '\n  }\n'
    printf '}\n'
  } >"${tmp}"

  # 先写临时文件再 mv：写到一半被打断时不至于留下半截 JSON，
  # 而 upgrade 读到半截 JSON 会把它当"基线损坏"。
  mv "${tmp}" "${MANIFEST_FILE}"
  chmod 600 "${MANIFEST_FILE}" 2>/dev/null || true

  if [[ "${first_install}" == "1" ]]; then
    ok "安装台账已写入：$(basename "${MANIFEST_FILE}")"
  else
    ok "安装台账已更新（installed_at 保留首次值 ${installed_at}）：$(basename "${MANIFEST_FILE}")"
  fi
}

summary() {
  local first_install="$1"
  echo
  echo "=================================================================="
  if [[ "${first_install}" == "1" ]]; then
    ok "轻量版安装完成"
  else
    ok "轻量版安装完成（重复执行，幂等：未重建库、未覆盖环境文件）"
  fi
  echo "   入口      ：$(env_get OA_LITE_PUBLIC_URL http://localhost)/"
  echo "   就绪探针  ：http://127.0.0.1:${READY_PORT}/readyz"
  echo "   健康探针  ：http://127.0.0.1:${READY_PORT}/healthz"
  echo "   项目名    ：${PROJECT_NAME}（卷/网络前缀 ${PROJECT_NAME}_）"
  echo "   环境文件  ：${ENV_FILE}（600，含全部口令，勿入库、勿外传）"
  echo "   安装台账  ：${MANIFEST_FILE}"
  echo "   常驻容器  ：${EXPECTED_CONTAINERS} 个，全部 healthy"
  echo
  echo "   下一步（可选）："
  echo "     · Dify 控制台（首次配模型 provider 时才需要）"
  echo "       docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} --profile admin up -d dify-web"
  echo "     · 重复执行本脚本是安全的（幂等）；升级请用 3b-2 的 upgrade.sh"
  echo "=================================================================="
}

# ---------------------------------------------------------------------------
# 8. 参数解析（放在 source 之后：这样可以用上面带 [FAIL] 前缀的 abort_env）
# ---------------------------------------------------------------------------
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --online)
        [[ -z "${MODE}" ]] || abort_env "只能选一种镜像来源（已经指定了 --${MODE}）"
        MODE="online"
        shift
        ;;
      --offline)
        [[ -z "${MODE}" ]] || abort_env "只能选一种镜像来源（已经指定了 --${MODE}）"
        MODE="offline"
        shift
        ;;
      --images-dir)
        [[ -n "${2:-}" ]] || abort_env "--images-dir 需要一个目录" "例：--images-dir /mnt/usb/officeai-images"
        IMAGES_DIR="$2"
        shift 2
        ;;
      --proxy-port)
        [[ -n "${2:-}" ]] || abort_env "--proxy-port 需要一个端口号" "例：--proxy-port 8080"
        PROXY_PORT_OVERRIDE="$2"
        shift 2
        ;;
      --project-name)
        [[ -n "${2:-}" ]] || abort_env "--project-name 需要一个名称" "例：--project-name officeai-lite"
        PROJECT_NAME="$2"
        shift 2
        ;;
      --env-file)
        [[ -n "${2:-}" ]] || abort_env "--env-file 需要一个路径" "例：--env-file deploy/.env.lite"
        ENV_FILE="$2"
        shift 2
        ;;
      --ready-timeout)
        [[ -n "${2:-}" ]] || abort_env "--ready-timeout 需要一个秒数" "例：--ready-timeout 300"
        READY_TIMEOUT="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        usage >&2
        abort_env "未知参数：$1" "用 --help 查看用法"
        ;;
    esac
  done

  [[ -n "${MODE}" ]] || abort_env "必须指定镜像来源：--online 或 --offline" \
    "有外网/内网镜像仓库：--online（按 digest 拉取）" \
    "纯离线交付：--offline（从 deploy/offline/images/*.tar 加载）"
}

# ---------------------------------------------------------------------------
# 9. 主流程
# ---------------------------------------------------------------------------
main() {
  parse_args "$@"

  # 项目名通过环境变量注入：这样被 source 进来的 compose()/wait_service_healthy()
  # 以及本脚本的每一次 docker compose 调用都自动带上同一个项目名，不用各写一遍 -p。
  export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"

  banner

  local first_install=0
  # "首次安装"的判据是安装台账在不在：台账是安装成功的最后一步产物，
  # 它存在就说明这台机器至少装成功过一次（口令早就打印过了）。
  [[ -f "${MANIFEST_FILE}" ]] || first_install=1

  echo
  echo "--- 一、前置检查 ---"
  check_prereqs_install

  echo
  echo "--- 二、环境文件 ---"
  generate_env_file
  check_env_secrets
  READY_PORT="$(effective_proxy_port)"
  check_port_free "${READY_PORT}"

  echo
  echo "--- 三、镜像（${MODE}）---"
  load_images_lock
  collect_required_images
  case "${MODE}" in
    offline) acquire_images_offline ;;
    online)  acquire_images_online ;;
  esac
  verify_images_by_digest

  echo
  echo "--- 四、基础设施与数据库 ---"
  step_start_infra
  # 库里 officeai_app 的口令与 DSN 对齐（幂等）。不做这一步 migrate 必然 28P01，
  # 而且报错只出现在 migrate 日志里，现场极易误判成编排问题。
  align_app_role_password

  echo
  echo "--- 五、数据库迁移 ---"
  step_migrate

  echo
  echo "--- 六、起全栈并就绪 ---"
  step_up_full
  wait_readyz "${READY_PORT}" || die 1 "/readyz 在 ${READY_TIMEOUT}s 内没有返回 200（见上面最后一次响应体）"
  ok "/readyz 已返回 200"
  wait_all_healthy || die 1 "有容器未达到 healthy"

  echo
  echo "--- 七、凭据与安装台账 ---"
  step_print_admin_secret "${first_install}"
  write_manifest "${first_install}"

  summary "${first_install}"
}

# 只在**直接执行**时跑主流程。
# 作为库被 source（deploy/backup.sh、deploy/restore.sh 复用 env_get / compose /
# die / abort_env 等）时必须什么都不做——否则一次 source 就会真的开始装一套栈。
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
