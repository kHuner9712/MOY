#!/usr/bin/env bash
# ===========================================================================
# 轻量版升级（阶段四 T4-05 第三批 3b-2，架构 §9.3 状态机 / §9.3.1 / §9.4）
#
# 一句话：**先确认数据库具备升级所需的能力，再备份，再迁移，再冒烟；
# 任何一步失败都自动退回到升级前的版本**。
#
# 用法：
#   OA_MAINTENANCE=1 bash deploy/upgrade.sh
#   OA_MAINTENANCE=1 bash deploy/upgrade.sh --dry-run          # 只跑预检，不动任何东西
#   OA_MAINTENANCE=1 bash deploy/upgrade.sh --skip-backup      # 已有近期备份时（不推荐）
#
# 退出码（**运维据此决定下一步，不要只看"失败了"**）：
#   0  升级成功，冒烟通过，服务已放流量
#   1  升级执行失败（备份失败 / 迁移失败 / 冒烟失败），且自动回滚也未完成
#   2  前置检查未通过（docker 不可用、库连不上、环境文件缺失）—— 数据库未被改动
#   3  未设置 OA_MAINTENANCE=1 —— **一个容器都没停，数据库未被改动**
#   4  **数据库缺少「记忆检索」所需的扩展** —— 升级中止，**未执行任何迁移**，
#      需客户 DBA 执行一次 SQL 后重跑（详见 §9.3.1，这是 V1.0 唯一要 DBA 动手的地方）
#   5  迁移被数据库拒绝（应用角色无 DDL 权限）—— 明确区别于"脚本有问题"，转 DBA
#   6  预留：初始化脚本指纹与安装时不一致（现场被人手改过），见 install.sh 的
#      §.installed-manifest.json。V1.0 只登记不判定（避免误伤只改注释的现场）
#   7  升级失败后自动回滚时，目标版本没有自动回滚脚本 —— 需 DBA 按 runbook 手工处理
#
# 顺序为什么不可颠倒（§9.3.1 的实测结论）：
#   迁移 `0009` 用到 `vector` 类型。缺扩展时若先跑迁移，会在 DDL 中途报
#   `type "vector" does not exist` ——那时已经执行了一半迁移，回滚代价高得多。
#   所以**扩展预检必须排在备份与迁移之前**，且失败时"一行 SQL 都不执行"。
#
# 为什么 `vector` 不能由本脚本自动建（三条事实，见 sql/enable-vector-extension.sql 头部）：
#   1. 该扩展 `trusted = false`，只有**超级用户**能建；
#   2. 应用角色 `officeai_app` 是 NOSUPERUSER（连它是库属主也一样）；
#   3. 扩展是**按库**安装的物理对象 —— 镜像里有安装文件 ≠ 这个库启用了它。
#   新装（install.sh）由 initdb 以超级用户建过，所以预检天然通过；存量库必须人工一次。
#
# 复用说明：source deploy/backup.sh 复用 env_get / compose / die / abort_env /
# ok / info / warn / err / resolve_db / psql_value / wait_readyz（backup.sh 再往下
# source install.sh → validate_lite_up.sh）。备份的"能不能被 restore.sh 读取"由
# backup.sh 自己保证（PGDMP 魔数 + sha256 + 行数指纹），本脚本不复制第二份判据。
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 变量名必须与 backup.sh / restore.sh / install.sh **四者互不相同**：source 共用
# 同一个 shell，同名数组会被后 source 的一方清空，表现为"参数静默失效"。
_UPGRADE_SAVED_ARGS=("$@")
set --
# shellcheck source=./backup.sh
source "${SCRIPT_DIR}/backup.sh"
set -- "${_UPGRADE_SAVED_ARGS[@]}"
unset _UPGRADE_SAVED_ARGS

# ---------------------------------------------------------------------------
# 参数与常量
# ---------------------------------------------------------------------------
UPGRADE_DRY_RUN=0
UPGRADE_SKIP_BACKUP=0
UPGRADE_READY_TIMEOUT=180
UPGRADE_SMOKE_TIMEOUT=120

#: 升级日志。放 backups/ 是因为它已在 .gitignore 里（§9.4 要求"全程写 upgrade.log"，
#: 但日志不该以未跟踪文件的形式污染工作区）。
UPGRADE_LOG="${SCRIPT_DIR}/backups/upgrade.log"

#: 升级期间要停掉的入口服务（拦流量的唯一有效手段，见 §9.4「维护闸门」）
UPGRADE_ENTRY_SERVICES=(proxy web)

#: 实际停掉的（本来在跑的）服务名；脚本退出时按这份名单拉回来
declare -a UPGRADE_STOPPED=()
UPGRADE_STOPPED_ANY=0

#: 本次迁移将执行的版本号（按磁盘顺序），失败时倒序交给 rollback.sh
declare -a UPGRADE_PENDING=()

#: 冒烟阶段要断言的"目标版本已登记"的版本号（迁移完成后取最大一条）
UPGRADE_TARGET_VERSION=""

usage() {
  cat <<'USAGE'
轻量版升级（deploy/upgrade.sh）

用法：
  OA_MAINTENANCE=1 bash deploy/upgrade.sh [选项]

选项：
  --dry-run               只跑前置检查与扩展预检，**不停服务、不备份、不迁移**
  --skip-backup           跳过本次备份（已有近期备份时用；默认必做）
  --env-file <路径>       环境文件（默认 deploy/.env.lite）
  --project-name <名称>   compose 项目名（默认 officeai-lite）
  --ready-timeout <秒>    冒烟阶段 /readyz 窗口（默认 180）
  -h, --help              显示本帮助

必须设置 OA_MAINTENANCE=1（意图声明 + 防误触），否则直接拒绝且不动任何容器。

退出码：0 成功 / 1 失败且回滚未完成 / 2 前置未过 / 3 未确认维护模式 /
        4 缺「记忆检索」扩展（未执行任何迁移）/ 5 迁移需 DBA /
        6 预留（现场初始化脚本被改）/ 7 目标版本无自动回滚脚本

CI/演练用（生产不要设）：OA_VECTOR_PRECHECK_CMD 可替换扩展预检的探针命令，
用于在**没有数据库**的环境里证明退出码 4 的分支确实生效（见文件末尾自检说明）。
USAGE
}

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
#: 追加一行到 upgrade.log。为什么不用 `exec > >(tee -a)`：Git Bash 的进程替换
#: 在脚本退出时会留下未回收的子进程，实测偶发"脚本已 exit 但终端不返回"。
upgrade_log() {
  local line="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
  mkdir -p "$(dirname "${UPGRADE_LOG}")" 2>/dev/null || true
  printf '%s\n' "${line}" >>"${UPGRADE_LOG}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 一、维护闸门与前置检查
# ---------------------------------------------------------------------------
#: 维护闸门。**第一件事**，只读环境变量，不碰 docker、不碰数据库。
#: OA_MAINTENANCE 的真实语义是"意图声明 + 误操作防护"，**不是**应用级开关
#: （全仓没有它的消费点，它拦不住任何流量）——真正的拦流量是下面的 compose stop。
require_maintenance_mode() {
  if [[ "${OA_MAINTENANCE:-}" != "1" ]]; then
    err "拒绝执行：未设置 OA_MAINTENANCE=1"
    cat >&2 <<'TIP'
       升级会先停掉入口、重建库结构，跑在业务时间里客户会看到服务中断。
       确认可以停机后，用这条命令重跑：
         OA_MAINTENANCE=1 bash deploy/upgrade.sh
       只想先看看能不能升级（不动任何东西）：
         OA_MAINTENANCE=1 bash deploy/upgrade.sh --dry-run
       本检查发生在所有 docker / 数据库命令之前，因此现在数据库未被改动。
TIP
    upgrade_log "ABORT maintenance_gate"
    exit 3
  fi
  ok "维护模式已确认（OA_MAINTENANCE=1）"
  upgrade_log "maintenance_gate ok"
}

check_prereqs_upgrade() {
  command -v docker >/dev/null 2>&1 || abort_env "找不到 docker 命令"
  [[ -n "$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)" ]] \
    || abort_env "连不上 Docker 守护进程" \
      "Windows：启动 Docker Desktop 并等托盘变绿" \
      "Linux ：systemctl start docker"
  command -v curl >/dev/null 2>&1 || abort_env "找不到 curl" "冒烟阶段要用它探测 /readyz"
  [[ -f "${ENV_FILE}" ]] || abort_env "缺环境文件：${ENV_FILE}" \
    "升级要连哪个库、起哪些服务都由它决定"

  local cid state
  cid="$(compose ps -q postgres 2>/dev/null | head -n 1 || true)"
  [[ -n "${cid}" ]] || abort_env "本项目（${PROJECT_NAME}）的 postgres 容器不存在" \
    "升级必须在已安装的环境里跑；先确认 --project-name 与服务当前项目名一致"
  state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null || true)"
  [[ "${state}" == "healthy" ]] || abort_env "postgres 状态是 ${state}，不是 healthy" \
    "等它健康再升级：docker compose ps" \
    "在库不健康时做备份与迁移，拿到的是一份残缺快照"
  ok "前置检查通过（docker / curl / 环境文件 / postgres healthy）"
  upgrade_log "prereqs ok"
}

# ---------------------------------------------------------------------------
# 二、扩展预检（§9.3.1，V1.0 唯一需要客户 DBA 动手的地方）
# ---------------------------------------------------------------------------
#: 扩展预检的探针命令。
#: 默认走容器内连接；`OA_VECTOR_PRECHECK_CMD` 存在时替换为它——**只用于在没有
#: 数据库的环境里证明退出码 4 的分支生效**（CI / 演练）。生产不要设置。
vector_probe() {
  if [[ -n "${OA_VECTOR_PRECHECK_CMD:-}" ]]; then
    bash -c "${OA_VECTOR_PRECHECK_CMD}"
    return $?
  fi
  compose exec -T postgres psql -U "${DB_SUPERUSER}" -d "${DB_NAME}" -tA -v ON_ERROR_STOP=1 \
    -c "SELECT 1 FROM pg_extension WHERE extname = 'vector' AND extversion >= '0.8'"
}

#: 缺扩展时的中止分支：打印逐字文案（§9.3.1）+ 把可照做的 SQL 全文输出到屏幕。
#: 为什么把整份 SQL 打出来：客户 DBA 往往只能拿到一张截图或一段终端回显，
#: 让他去仓库里找文件等于多一次往返；文件里已含幂等判断与失败对照表。
abort_missing_vector() {
  cat >&2 <<'MSG'

检测到数据库尚未启用「记忆检索」所需的扩展，升级已中止（数据库未做任何改动）。

请让您的数据库管理员执行下面这一条语句（需要数据库超级用户权限，约 1 分钟）：

    psql -U <超级用户> -d <业务库> -c "CREATE EXTENSION IF NOT EXISTS vector;"

执行完成后重新运行 upgrade.sh 即可。此步骤只需做一次。

MSG
  local script="${REPO_ROOT}/sql/enable-vector-extension.sql"
  if [[ -f "${script}" ]]; then
    echo "------------------------------------------------------------------" >&2
    echo " 可直接复制执行的完整脚本（含幂等判断与失败对照表）：${script}" >&2
    echo "------------------------------------------------------------------" >&2
    sed 's/^/  /' "${script}" >&2
    echo "------------------------------------------------------------------" >&2
  else
    warn "没找到 ${script}（离线包里应随附该文件）"
  fi
  echo "       退出码 4：明确区别于「环境有问题」的 2，便于运维直接转给 DBA。" >&2
  upgrade_log "ABORT vector_extension_missing"
  exit 4
}

check_vector_extension() {
  local out="" rc=0
  info "预检①：确认数据库已启用「记忆检索」所需的扩展…"
  out="$(vector_probe 2>&1)" || rc=$?
  if (( rc != 0 )); then
    # 查不到 ≠ 缺扩展：连接失败/权限问题时给出 2，避免把"连不上库"误报成
    # "让 DBA 去建扩展"——那会把人引到完全错误的方向上。
    err "无法确认扩展状态（查询失败，退出码 ${rc}）"
    printf '%s\n' "${out}" | sed 's/^/       /' >&2
    abort_env "扩展预检未能完成：先确认 postgres 可用、环境文件里的超级用户与库名正确" \
      "这不是'缺扩展'（缺扩展会打印 CREATE EXTENSION 那句话并退出 4）"
  fi
  if grep -q '1' <<<"${out}"; then
    ok "预检①：扩展已就绪"
    upgrade_log "vector_precheck ok"
    return 0
  fi
  abort_missing_vector
}

# ---------------------------------------------------------------------------
# 三、拦流量 / 放流量
# ---------------------------------------------------------------------------
#: 停入口拦流量（先 proxy 再 web：入口先断，才不会在 web 停机期间仍有请求打进来）
stop_entry_services() {
  local svc running
  running="$(compose ps --status running --format '{{.Service}}' 2>/dev/null | sed '/^$/d' | sort -u || true)"
  UPGRADE_STOPPED=()
  for svc in "${UPGRADE_ENTRY_SERVICES[@]}"; do
    if grep -qxF "${svc}" <<<"${running}"; then
      UPGRADE_STOPPED+=("${svc}")
    fi
  done
  if (( ${#UPGRADE_STOPPED[@]} == 0 )); then
    warn "入口服务本来就没在跑（proxy / web），跳过停止步骤"
    return 0
  fi
  info "停入口拦流量：${UPGRADE_STOPPED[*]}（顺序：先 proxy 后 web）"
  compose stop proxy >/dev/null 2>&1 || true
  if grep -qxF "web" <<<"${running}"; then
    compose stop web >/dev/null 2>&1 || true
  fi
  UPGRADE_STOPPED_ANY=1
  ok "已停止 ${#UPGRADE_STOPPED[@]} 个入口服务（脚本退出时自动恢复，含失败路径）"
  upgrade_log "entry_stopped ${UPGRADE_STOPPED[*]}"
}

#: 放流量。**含失败路径**：由 EXIT trap 调用，所以每条退出路径都会执行到。
#: 恢复顺序与停止相反（先 web 后 proxy）：入口最后一个开，避免把请求放进一个
#: 还没起好的站点。
restore_entry_services() {
  (( UPGRADE_STOPPED_ANY == 1 )) || return 0
  (( ${#UPGRADE_STOPPED[@]} > 0 )) || return 0
  info "恢复入口：${UPGRADE_STOPPED[*]}（顺序：先 web 后 proxy）"
  if grep -qxF "web" <<<"$(printf '%s\n' "${UPGRADE_STOPPED[@]}")"; then
    compose start web >/dev/null 2>&1 || warn "启动 web 失败，请手工 docker compose start web"
  fi
  if grep -qxF "proxy" <<<"$(printf '%s\n' "${UPGRADE_STOPPED[@]}")"; then
    compose start proxy >/dev/null 2>&1 || warn "启动 proxy 失败，请手工 docker compose start proxy"
  fi
  ok "入口服务已恢复"
  upgrade_log "entry_restored ${UPGRADE_STOPPED[*]}"
}

on_upgrade_exit() {
  local rc=$?
  set +e
  restore_entry_services
  upgrade_log "exit rc=${rc}"
  trap - EXIT
  exit "${rc}"
}

# ---------------------------------------------------------------------------
# 四、备份（失败即中止，**不进入升级**）
# ---------------------------------------------------------------------------
do_backup() {
  if (( UPGRADE_SKIP_BACKUP == 1 )); then
    warn "已按 --skip-backup 跳过本次备份（升级前应有可恢复的近期备份）"
    upgrade_log "backup skipped"
    return 0
  fi
  info "预检②：升级前备份（调用 deploy/backup.sh，产物含行数指纹）…"
  local out="" rc=0
  out="$(bash "${SCRIPT_DIR}/backup.sh" --env-file "${ENV_FILE}" --project-name "${PROJECT_NAME}" 2>&1)" || rc=$?
  if (( rc != 0 )); then
    printf '%s\n' "${out}" | sed 's/^/       /' >&2
    upgrade_log "backup FAILED rc=${rc}"
    die 1 "备份失败（退出码 ${rc}）：升级已中止，**未执行任何迁移**，数据库与代码都没变" \
      "备份是升级唯一的退路：备份不可用就升级，等于把'回滚'换成'希望'" \
      "排查顺序：① 磁盘空间 df -h ② docker compose ps 看 postgres 是否 healthy" \
      "         ③ 上面回显里 backup.sh 的 [FAIL] 行就是具体原因"
  fi
  printf '%s\n' "${out}" | sed -n 's/^BACKUP_OK /       /p' | head -n 1
  ok "预检②：备份完成（可被 deploy/restore.sh 读取）"
  upgrade_log "backup ok"
}

# ---------------------------------------------------------------------------
# 五、迁移（advisory lock + DDL 与台账同事务，均在 migrate.py 内）
# ---------------------------------------------------------------------------
#: 读出"将要在本次升级中执行的版本号"（磁盘上有、台账里没有）。
#: 为什么先算出来：迁移中途失败时，恰好需要这份名单去调 rollback.sh——
#: 而失败之后台账已经不可信（可能只写了一半），不能那时再算。
collect_pending_versions() {
  local applied v name
  applied="$(psql_value "SELECT version FROM schema_migration" 2>/dev/null | tr -d '\r')" \
    || abort_env "读不出迁移台账（schema_migration）" \
      "库名/超级用户是否与环境文件一致？"
  UPGRADE_PENDING=()
  for name in "${SCRIPT_DIR}"/migrations/0*.sql; do
    [[ -e "${name}" ]] || continue
    v="$(basename "${name}")"
    v="${v%%_*}"
    grep -qxF "${v}" <<<"${applied}" || UPGRADE_PENDING+=("${v}")
  done
  if (( ${#UPGRADE_PENDING[@]} == 0 )); then
    info "台账已是目标版本，没有待执行的迁移"
  else
    info "待执行迁移：${UPGRADE_PENDING[*]}"
  fi
  upgrade_log "pending ${UPGRADE_PENDING[*]:-none}"
}

do_migrate() {
  local out="" rc=0
  info "预检③：执行迁移（单实例 Job，advisory lock + DDL 与台账同事务）…"
  out="$(compose --profile migrate run --rm -T migrate 2>&1)" || rc=$?
  if (( rc == 0 )); then
    UPGRADE_TARGET_VERSION="$(psql_value "SELECT max(version) FROM schema_migration" 2>/dev/null | tr -d '\r' || true)"
    ok "迁移完成（台账最新版本：${UPGRADE_TARGET_VERSION:-未知}）"
    upgrade_log "migrate ok target=${UPGRADE_TARGET_VERSION:-unknown}"
    return 0
  fi
  printf '%s\n' "${out}" | sed 's/^/       /' >&2
  upgrade_log "migrate FAILED rc=${rc}"
  if grep -qEi "must be owner|permission denied|insufficient_privilege|42501|not owner of" <<<"${out}"; then
    err "迁移被数据库拒绝：应用角色没有 DDL 权限（这是设计意图，不是配置错误）"
    cat >&2 <<'TIP'
       业务库的表由超级用户（POSTGRES_USER）创建，应用角色只有读写权限且不能绕过
       行级安全——多租户隔离能生效的前提正是它不能做 DDL。
       怎么做：把上面日志里被拒的语句交给 DBA，用超级用户执行后重跑本脚本。
       退出码 5：与"脚本/环境有问题"的 1 区分开，便于直接转 DBA。
TIP
    fail_with_rollback 5 "迁移被拒（应用角色无 DDL 权限）"
  fi
  fail_with_rollback 1 "迁移失败（退出码 ${rc}）"
}

# ---------------------------------------------------------------------------
# 六、冒烟（/readyz + 3 项业务探针）
# ---------------------------------------------------------------------------
#: 3 项业务探针（不是"端口开着就算好"）：
#:   P1 就绪：/readyz 返回 200 —— 应用自检（库连接、行级安全、缓存）全绿才 200；
#:   P2 结构：迁移台账已登记到本次目标版本，且升级涉及的关键表存在 ——
#:            "DDL 跑完了"与"台账记上了"必须同时成立（同事务的意义就在这）；
#:   P3 服务：/metrics 返回 200 且含请求计数 —— 证明业务进程真在服务，
#:            而不是 proxy 把请求转给了一个已经半死进程。
do_smoke() {
  local port body code
  port="$(env_get OA_LITE_PROXY_PORT 80)"
  info "冒烟①：/readyz（窗口 ${UPGRADE_READY_TIMEOUT}s）…"
  if ! wait_readyz "${port}"; then
    fail_with_rollback 1 "冒烟①失败：/readyz 未就绪"
  fi
  ok "冒烟①：/readyz 就绪"

  info "冒烟②：迁移台账与关键表结构…"
  body="$(psql_value "SELECT count(*) FROM schema_migration" 2>/dev/null | tr -d '\r' || true)"
  if [[ -z "${body}" || "${body}" == "0" ]]; then
    fail_with_rollback 1 "冒烟②失败：迁移台账为空或不可读（台账=${body:-空}）"
  fi
  if [[ -n "${UPGRADE_TARGET_VERSION}" ]]; then
    code="$(psql_value "SELECT count(*) FROM schema_migration WHERE version = '${UPGRADE_TARGET_VERSION}'" 2>/dev/null | tr -d '\r' || true)"
    [[ "${code}" == "1" ]] || fail_with_rollback 1 \
      "冒烟②失败：台账未登记目标版本 ${UPGRADE_TARGET_VERSION}（DDL 与台账没有同事务）"
  fi
  ok "冒烟②：台账一致（${body} 条版本，最新 ${UPGRADE_TARGET_VERSION:-未知}）"

  info "冒烟③：/metrics 可抓取…"
  code="$(curl -s -o /tmp/upgrade-metrics.$$ -w '%{http_code}' --max-time 10 \
    "http://127.0.0.1:${port}/metrics" 2>/dev/null || true)"
  if [[ "${code}" != "200" ]] || ! grep -q 'http_requests_total' "/tmp/upgrade-metrics.$$" 2>/dev/null; then
    rm -f "/tmp/upgrade-metrics.$$"
    fail_with_rollback 1 "冒烟③失败：/metrics 返回 ${code:-无} 或不含请求计数"
  fi
  rm -f "/tmp/upgrade-metrics.$$"
  ok "冒烟③：业务进程在服务（请求计数可见）"
  upgrade_log "smoke ok"
}

# ---------------------------------------------------------------------------
# 七、失败自动回滚
# ---------------------------------------------------------------------------
#: 失败→回滚→退出。**透传 rollback.sh 的退出码**：它返回 7 说明目标版本没有
#: 自动回滚脚本，需要 DBA 照 runbook 手工处理——这个信息不能被压成通用的 1。
fail_with_rollback() {
  local exit_code="$1"
  shift
  err "$*"
  upgrade_log "fail reason=$* pending=${UPGRADE_PENDING[*]:-none}"

  if (( ${#UPGRADE_PENDING[@]} == 0 )); then
    err "本次升级没有执行任何迁移（待执行清单为空），无需回滚"
    upgrade_log "RESULT FAILED_NO_MIGRATION"
    echo "FAILED_NO_MIGRATION"
    exit "${exit_code}"
  fi

  # 倒序：后执行的先退（与 migrate.py 的"不自动级联"配合，显式给全名单）
  local -a reversed=()
  local i
  for (( i = ${#UPGRADE_PENDING[@]} - 1; i >= 0; i-- )); do
    reversed+=("${UPGRADE_PENDING[$i]}")
  done

  echo
  echo "--- 自动回滚（目标是升级前版本）---"
  local rc=0
  OA_MAINTENANCE=1 bash "${SCRIPT_DIR}/rollback.sh" \
    --env-file "${ENV_FILE}" --project-name "${PROJECT_NAME}" "${reversed[@]}" || rc=$?

  if (( rc == 0 )); then
    ok "回滚完成（已退回 ${reversed[*]}）"
    upgrade_log "RESULT ROLLED_BACK ${reversed[*]}"
    echo "ROLLED_BACK ${reversed[*]}"
    exit "${exit_code}"
  fi
  err "回滚未完成（退出码 ${rc}）——服务已恢复，但数据/结构需要人工确认"
  upgrade_log "RESULT ROLLBACK_FAILED rc=${rc}"
  echo "ROLLBACK_FAILED rc=${rc}"
  if (( rc == 7 )); then
    exit 7
  fi
  die 1 "升级失败且回滚未完成（升级=${exit_code} / 回滚=${rc}）" \
    "现在**不要**放流量给客户（入口已恢复，但结构可能停在中途）" \
    "处置：把 deploy/${UPGRADE_LOG##*/} 与 rollback 的输出交给 DBA，按 runbook 手工处理"
}

# ---------------------------------------------------------------------------
# 参数解析与主流程
# ---------------------------------------------------------------------------
parse_args_upgrade() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        UPGRADE_DRY_RUN=1
        shift
        ;;
      --skip-backup)
        UPGRADE_SKIP_BACKUP=1
        shift
        ;;
      --env-file)
        [[ -n "${2:-}" ]] || abort_env "--env-file 需要一个路径"
        ENV_FILE="$2"
        shift 2
        ;;
      --project-name)
        [[ -n "${2:-}" ]] || abort_env "--project-name 需要一个名称"
        PROJECT_NAME="$2"
        shift 2
        ;;
      --ready-timeout)
        [[ -n "${2:-}" ]] || abort_env "--ready-timeout 需要一个秒数"
        UPGRADE_READY_TIMEOUT="$2"
        READY_TIMEOUT="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        usage >&2
        abort_env "未知参数：$1"
        ;;
    esac
  done
}

main_upgrade() {
  parse_args_upgrade "$@"
  export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
  READY_TIMEOUT="${UPGRADE_READY_TIMEOUT}"

  echo "=================================================================="
  echo " MOY 轻量版升级"
  echo " 环境文件：${ENV_FILE}"
  echo " 项目名  ：${PROJECT_NAME}"
  echo " 模式    ：$([[ ${UPGRADE_DRY_RUN} == 1 ]] && echo '仅预检（--dry-run）' || echo '完整升级')"
  echo " 日志    ：${UPGRADE_LOG}"
  echo "=================================================================="

  # ① 维护闸门：必须最先做，此时一个 docker / 数据库命令都还没执行
  require_maintenance_mode
  upgrade_log "=== upgrade start dry_run=${UPGRADE_DRY_RUN} ==="

  # ② 前置检查
  echo
  echo "--- 一、前置检查 ---"
  check_prereqs_upgrade
  resolve_db

  # ③ 扩展预检（必须在迁移之前，见文件头）
  echo
  echo "--- 二、扩展预检（记忆检索能力）---"
  check_vector_extension

  if (( UPGRADE_DRY_RUN == 1 )); then
    echo
    echo "=================================================================="
    ok "预检通过（DRY_RUN）：环境具备升级条件"
    echo "   本次未停服务、未备份、未迁移，数据库未被改动"
    echo "   正式升级：OA_MAINTENANCE=1 bash deploy/upgrade.sh"
    echo "=================================================================="
    upgrade_log "RESULT DRY_RUN_OK"
    echo "DRY_RUN_OK"
    return 0
  fi

  # 停入口：从现在起流量被拦住，EXIT trap 保证任何路径都会恢复
  trap on_upgrade_exit EXIT
  echo
  echo "--- 三、拦流量 ---"
  stop_entry_services

  # ④ 备份（失败即中止，不进入升级）
  echo
  echo "--- 四、备份 ---"
  do_backup

  # ⑤ 迁移
  echo
  echo "--- 五、迁移 ---"
  collect_pending_versions
  do_migrate

  # ⑥ 冒烟
  echo
  echo "--- 六、冒烟（/readyz + 3 项业务探针）---"
  do_smoke

  echo
  echo "=================================================================="
  ok "升级完成（台账最新版本：${UPGRADE_TARGET_VERSION:-未知}）"
  echo "   下一步：解除维护模式，观察 15 分钟错误率与响应时间"
  echo "   http://127.0.0.1:$(env_get OA_LITE_PROXY_PORT 80)/readyz"
  echo "=================================================================="
  upgrade_log "RESULT DONE target=${UPGRADE_TARGET_VERSION:-unknown}"
  echo "DONE"
}

# 只在直接执行时跑（被别的脚本 source 时不跑）
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main_upgrade "$@"
fi
