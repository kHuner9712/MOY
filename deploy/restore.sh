#!/usr/bin/env bash
# ===========================================================================
# 轻量版恢复（阶段四 T4-05 第三批 3b-2）
#
# 一句话：**停掉入口 → 整库恢复到某份备份 → 用备份时留下的指纹证明"一行没丢"**。
# 恢复是唯一会覆盖生产数据的动作，所以它处处往"宁可停着、不可半恢复"的方向选。
#
# 用法：
#   OA_MAINTENANCE=1 bash deploy/restore.sh --from deploy/backups/20261023T035500Z
#   OA_MAINTENANCE=1 bash deploy/restore.sh        # 不给 --from 时用最新一份备份
#   OA_MAINTENANCE=1 bash deploy/restore.sh --from <dir> --stop-apps   # 锁冲突时
#
# 退出码（**注意 2 与 3 的区别，运维据此决定下一步**）：
#   0  恢复成功，服务已重新拉起
#   1  恢复执行失败（pg_restore 报错）——**服务保持停止**，按提示处置
#   2  前置检查未通过（最常见：没有 OA_MAINTENANCE=1）——**一个容器都没动**
#   3  恢复执行了但对不上（行数丢失 / 抽查内容不一致）——**服务保持停止**，禁止放流量
#
# 为什么必须 OA_MAINTENANCE=1：
#   恢复会 `pg_restore --clean`，把现有的表全部 DROP 再重建。跑在业务时间里
#   = 直接把线上表删掉。所以这道闸门放在**第一行**，且在任何 docker 命令之前。
#
# 为什么除了停 proxy/web 还要 pg_terminate_backend：
#   proxy/web 只是入口，adapter / dify-api / dify-worker / plugin_daemon / keycloak
#   也都连着业务库。它们持有连接时，pg_restore 的 DROP TABLE 拿不到 ACCESS EXCLUSIVE
#   锁，会一直等（表现为脚本卡住不动）。所以停完入口还要把"别的连接"踢掉，
#   应用侧的连接池会自动重连。锁还是抢不到时用 --stop-apps 把这些服务也停掉。
#
# 为什么核对失败不自动重启服务：
#   半恢复的库比停机的库危险得多——业务能连上、能读到缺失的数据、能继续写，
#   而运维以为恢复成功了。宁可让客户看到"服务不可用"，也不要让客户看到"服务正常但数据少了"。
#
# 复用说明：source deploy/backup.sh 复用 env_get / compose / die / abort_env /
# resolve_db / db_counts / db_verify_rows / sql_literal（backup.sh 再往下 source
# install.sh）。这几段 SQL 涉及时区与 md5 口径，复制第二份必然漂移——备份与
# 恢复的指纹必须由同一份代码产生，否则"对不上"到底是数据问题还是口径问题就说不清了。
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 复用 backup.sh 的函数（其 main 有守卫，不会被执行）。
# 变量名与 backup.sh / install.sh **三者互不相同**：source 共用同一 shell，
# 同名数组会被后 source 的一方覆盖成空，表现为"参数静默失效"。
_RESTORE_SAVED_ARGS=("$@")
set --
# shellcheck source=./backup.sh
source "${SCRIPT_DIR}/backup.sh"
set -- "${_RESTORE_SAVED_ARGS[@]}"
unset _RESTORE_SAVED_ARGS

# ---------------------------------------------------------------------------
# 参数与常量
# ---------------------------------------------------------------------------
BACKUP_FROM=""
STOP_APPS=0
PROJECT_NAME="officeai-lite"

#: 恢复期间要停掉的入口服务（真正把流量拦在门外的东西）
ENTRY_SERVICES=(proxy web)

#: --stop-apps 时额外停掉的、连着业务库的服务（pg_restore 抢不到锁时用）
APP_SERVICES=(adapter dify-api dify-worker plugin_daemon keycloak)

#: 记录"恢复前本来在跑的服务"，恢复成功后只把这些拉回来
declare -a RESTART_SERVICES=()

usage() {
  cat <<'USAGE'
轻量版恢复（deploy/restore.sh）

用法：
  OA_MAINTENANCE=1 bash deploy/restore.sh [选项]

选项：
  --from <目录>           备份目录（默认取 deploy/backups 下最新一份）
  --stop-apps             额外停掉 adapter/dify-*/keycloak（pg_restore 抢不到锁时用）
  --env-file <路径>       环境文件（默认 deploy/.env.lite）
  --project-name <名称>   compose 项目名（默认 officeai-lite）
  -h, --help              显示本帮助

必须设置 OA_MAINTENANCE=1，否则直接拒绝且不动任何容器。

退出码：0 成功 / 1 pg_restore 失败 / 2 前置检查未过 / 3 行数或抽查核对不通过
USAGE
}

# ---------------------------------------------------------------------------
# 前置检查（顺序很重要：维护模式闸门必须在任何 docker 命令之前）
# ---------------------------------------------------------------------------
#: 维护模式闸门。**第一件事**，且只读环境变量，不碰 docker。
require_maintenance_mode() {
  if [[ "${OA_MAINTENANCE:-}" != "1" ]]; then
    err "拒绝执行：未设置 OA_MAINTENANCE=1"
    cat >&2 <<'TIP'
       恢复会 pg_restore --clean：把现有表 DROP 掉再重建，跑在业务时间等于删库。
       确认可以停机后，用这条命令重跑：
         OA_MAINTENANCE=1 bash deploy/restore.sh --from <备份目录>
       此时不会有任何容器被停/启（本检查发生在所有 docker 命令之前）。
TIP
    exit 2
  fi
  ok "维护模式已确认（OA_MAINTENANCE=1）"
}

#: 备份目录校验：目录、dump 魔数、sha256 与 meta.json 一致、指纹文件在。
validate_backup_dir() {
  local dir="$1"
  [[ -d "${dir}" ]] || abort_env "备份目录不存在：${dir}" \
    "用 bash deploy/backup.sh 先做一份" \
    "或者用 --from 指向正确的目录"

  [[ -f "${dir}/db.dump" ]] || abort_env "备份里缺 db.dump：${dir}"
  [[ -f "${dir}/meta.counts.tsv" ]] || abort_env "备份里缺 meta.counts.tsv（没有指纹就无法证明恢复完整）"
  [[ -f "${dir}/meta.samples.tsv" ]] || abort_env "备份里缺 meta.samples.tsv（没有抽查指纹就无法证明内容一致）"

  local magic
  magic="$(head -c 5 "${dir}/db.dump" || true)"
  [[ "${magic}" == "PGDMP" ]] || abort_env "db.dump 不是 pg_dump 自定义格式（前 5 字节 = '${magic}'）" \
    "文件可能在拷贝过程中损坏；重新做一份备份" \
    "不要用 gzip 后的文件顶替（pg_restore 可以直接吃 -Fc，不需要再压）"

  # sha256 与 meta.json 记录值比对：挡住"同名文件被换掉/截断"
  if [[ -f "${dir}/meta.json" ]]; then
    local want got
    want="$(sed -n 's/.*"dump_sha256":[[:space:]]*"sha256:\([0-9a-f]\{64\}\)".*/\1/p' "${dir}/meta.json" | head -n 1)"
    got="$(sha256sum "${dir}/db.dump" | cut -d' ' -f1)"
    if [[ -n "${want}" && "${want}" != "${got}" ]]; then
      abort_env "db.dump 的 sha256 与 meta.json 记载不一致" \
        "记载：sha256:${want}" \
        "实际：sha256:${got}" \
        "文件被替换或损坏了，换一份备份"
    fi
    ok "db.dump 完整性校验通过（sha256 与 meta.json 一致）"
  else
    warn "备份里没有 meta.json，跳过 sha256 比对（仅靠 PGDMP 魔数判断）"
  fi
}

#: 在备份根目录里找最新一份（按目录名排序：名字是 UTC 时间戳，排序即时间序）
pick_latest_backup() {
  local latest=""
  [[ -d "${BACKUP_ROOT}" ]] || abort_env "备份根目录不存在：${BACKUP_ROOT}" \
    "先做一份备份：bash deploy/backup.sh"
  # 排除 .partial（未完成的备份绝不能被当成最新一份）
  latest="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d ! -name '.*' ! -name '*.partial' -print 2>/dev/null | sort | tail -n 1)"
  [[ -n "${latest}" ]] || abort_env "备份根目录里没有任何备份：${BACKUP_ROOT}"
  printf '%s' "${latest}"
}

check_prereqs_restore() {
  command -v docker >/dev/null 2>&1 || abort_env "找不到 docker 命令"
  [[ -n "$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)" ]] \
    || abort_env "连不上 Docker 守护进程"
  command -v sha256sum >/dev/null 2>&1 || abort_env "找不到 sha256sum"
  [[ -f "${ENV_FILE}" ]] || abort_env "缺环境文件：${ENV_FILE}"

  local cid state
  cid="$(compose ps -q postgres 2>/dev/null | head -n 1 || true)"
  [[ -n "${cid}" ]] || abort_env "本项目（${PROJECT_NAME}）的 postgres 容器不存在" \
    "恢复必须有库在跑；先确认 --project-name 对不对"
  state="$(docker inspect --format '{{.Status}}' "${cid}" 2>/dev/null || true)"
  [[ "${state}" == Up* ]] || abort_env "postgres 没有在运行（${state}）" \
    "先起来：docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite --profile '*' up -d postgres"
  ok "postgres 正在运行"
}

# ---------------------------------------------------------------------------
# 拦流量 / 放流量
# ---------------------------------------------------------------------------
#: 停掉入口（以及可选的连带服务），并记下"本来就该恢复的"那些。
stop_services_for_restore() {
  local running svc
  local -a targets=("${ENTRY_SERVICES[@]}")
  if (( STOP_APPS == 1 )); then
    targets+=("${APP_SERVICES[@]}")
    info "按 --stop-apps 要求，连带停掉所有连着业务库的服务"
  fi

  running="$(compose ps --status running --format '{{.Service}}' 2>/dev/null | sed '/^$/d' | sort -u || true)"
  RESTART_SERVICES=()
  for svc in "${targets[@]}"; do
    if grep -qxF "${svc}" <<<"${running}"; then
      RESTART_SERVICES+=("${svc}")
    fi
  done

  info "停服务拦流量：${targets[*]}"
  compose stop "${targets[@]}" >/dev/null || die 1 "停服务失败（compose stop ${targets[*]}）" \
    "恢复前必须先把流量拦住，否则恢复期间会有写请求打进来"
  ok "已停止 ${#RESTART_SERVICES[@]} 个服务（恢复完按这份名单拉回来）"
}

#: 恢复成功后放流量。只拉起"恢复前本来在跑"的服务，不多启。
start_services_after_restore() {
  if (( ${#RESTART_SERVICES[@]} == 0 )); then
    warn "恢复前入口服务本来就没在跑，不自动启动（避免改变现场状态）"
    return 0
  fi
  info "恢复流量：启动 ${RESTART_SERVICES[*]}"
  compose start "${RESTART_SERVICES[@]}" >/dev/null || die 1 "启动 ${RESTART_SERVICES[*]} 失败" \
    "数据已恢复，但服务没起来；手工执行 docker compose --profile '*' start ${RESTART_SERVICES[*]}"
  ok "服务已重新启动（${RESTART_SERVICES[*]}）"
}

#: 踢掉除了自己以外的业务库连接。不踢的话 pg_restore 的 DROP TABLE 会等锁、
#: 表现为"脚本卡在这里不动"，而人往往误以为是数据库慢。
terminate_other_connections() {
  local killed
  killed="$(psql_value "SELECT count(*) FROM (
      SELECT pg_terminate_backend(pid) FROM pg_stat_activity
       WHERE datname = $(sql_literal "${DB_NAME}") AND pid <> pg_backend_pid()
    ) AS k" 2>/dev/null || echo "?")"
  info "已断开 ${killed} 个其它连接（应用侧连接池会自动重连）"
}

# ---------------------------------------------------------------------------
# 恢复
# ---------------------------------------------------------------------------
do_pg_restore() {
  local dir="$1"
  info "整库恢复（pg_restore --clean --if-exists，库 ${DB_NAME}）…"
  # 用 stdin 喂 dump：容器里没有这个文件，也不需要把宿主机目录挂进去（少一个挂载点）。
  # --clean --if-exists：先 DROP 再建，保证"恢复到备份那一刻"，而不是叠加。
  # 不加 --no-privileges：officeai_app 的 GRANT/REVOKE 必须一起回来，
  #   否则恢复完 /readyz 的 db_privilege 会 not_ready。
  compose exec -T postgres pg_restore \
    --clean --if-exists --no-owner \
    -d "${DB_NAME}" <"${dir}/db.dump" \
    || die 1 "pg_restore 失败（上面是 pg_restore 的错误输出）" \
      "数据现在处于**部分恢复**状态，服务保持停止" \
      "排查：上面若出现 lock timeout / could not acquire lock，加 --stop-apps 重跑" \
      "修好后必须重新跑本脚本（不要手工 start 服务）"

  # pg_restore 对"无伤大雅"的告警（如已存在的扩展）不报错，但对真正的失败会非 0，
  # 所以走到这里就说明结构+数据都回来了。
  ok "pg_restore 执行完成"
}

# ---------------------------------------------------------------------------
# 核对（恢复后必须证明"一行没丢"）
# ---------------------------------------------------------------------------
#: 逐表比对行数。规则（来自 3b-2 施工单）：
#:   少一行 → 失败（exit 3）。这是恢复的唯一硬判据。
#:   多出来 → 允许，但分两类提示：会话/消息/审计允许自然增长；其它表多出来要人看一眼。
verify_row_counts() {
  local dir="$1"
  local now_tsv tbl want got bad=0 grown=0
  local tables
  tables="$(awk -F'|' '{printf "%s%s", (n++?" ":""), $1}' "${dir}/meta.counts.tsv")"
  [[ -n "${tables}" ]] || die 3 "备份里的行数指纹是空的（meta.counts.tsv）"

  now_tsv="$(db_counts "${tables}")" || die 1 "恢复后统计行数失败（库不可查？）"

  printf ' %-22s %10s %10s\n' "表" "备份" "恢复后"
  while IFS='|' read -r tbl want; do
    [[ -n "${tbl}" ]] || continue
    got="$(awk -F'|' -v t="${tbl}" '$1==t {print $2}' <<<"${now_tsv}")"
    if [[ -z "${got}" ]]; then
      err "${tbl}：恢复后查不到这张表（备份里有 ${want} 行）"
      bad=$((bad + 1))
      continue
    fi
    printf ' %-22s %10s %10s\n' "${tbl}" "${want}" "${got}"
    if (( got < want )); then
      err "${tbl}：丢数据（备份 ${want} 行，恢复后 ${got} 行）"
      bad=$((bad + 1))
    elif (( got > want )); then
      case "${tbl}" in
        conversation | message | audit_log)
          info "${tbl}：比备份多 $((got - want)) 行（会话/消息/审计允许自然增长）"
          ;;
        *)
          warn "${tbl}：比备份多 $((got - want)) 行（这张表本不该变化，请人工看一眼）"
          grown=$((grown + 1))
          ;;
      esac
    fi
  done <"${dir}/meta.counts.tsv"

  (( bad == 0 )) || die 3 "有 ${bad} 张表比备份少（数据丢失）——服务保持停止，禁止放流量" \
    "先别重启业务：现在放流量会让客户读到缺失的数据并继续写入" \
    "处置：确认备份文件完好后重跑本脚本；仍失败则走 DBA runbook"

  (( grown == 0 )) || warn "有 ${grown} 张表出现意外增长（见上），放流量前建议人工确认"
  ok "行数核对通过（无丢失）"
}

#: 抽查行内容：按备份时记下的主键回查整行 md5。
#: 为什么按主键而不是按序 limit：恢复后若新增行，按序 limit 会取到另一批行，
#: 那就变成"拿不同的行比不同的事"，测不出任何东西。
verify_sample_rows() {
  local dir="$1"
  local bad=0
  local spec table id_col expected values actual tenant key md5v

  for spec in "message:message_id" "user_memory:memory_id"; do
    table="${spec%%:*}"
    id_col="${spec##*:}"
    expected="$(awk -F'|' -v t="${table}" '$1==t' "${dir}/meta.samples.tsv" | sort)"
    [[ -n "${expected}" ]] || continue

    values=""
    local i=0
    while IFS='|' read -r tenant key md5v; do
      [[ -n "${key}" ]] || continue
      if (( i > 0 )); then values+=","; fi
      values+="($(sql_literal "${tenant}"),$(sql_literal "${key}"))"
      i=$((i + 1))
    done < <(awk -F'|' -v t="${table}" '$1==t {print $2"|"$3"|"$4}' "${dir}/meta.samples.tsv")

    [[ -n "${values}" ]] || continue
    actual="$(db_verify_rows "${table}" "${id_col}" "${values}" | sort)" \
      || die 1 "回查 ${table} 的抽查行失败"

    # 逐行核对：expected 里每一行都必须在 actual 里找到完全相同的 md5 与主键。
    # （actual 不可能比 expected 多：键是固定的，多出来的只可能是重复）
    local line found=0
    while IFS= read -r line; do
      [[ -n "${line}" ]] || continue
      found=$((found + 1))
      if ! grep -qxF "${line}" <<<"${actual}"; then
        err "抽查不一致：${line}"
        bad=$((bad + 1))
      fi
    done <<<"${expected}"
    ok "${table} 抽查 ${found} 行（比对整行 md5）"
  done

  (( bad == 0 )) || die 3 "抽查有 ${bad} 行内容与备份不一致——服务保持停止" \
    "整行 md5 不一致说明数据被改动过（或时区/口径不同）" \
    "先确认备份文件与本次恢复用的是同一份（meta.json 的 dump_sha256）"

  ok "抽查内容全部一致（共 10 行：消息 5 + 记忆 5）"
}

# ---------------------------------------------------------------------------
# 参数与主流程
# ---------------------------------------------------------------------------
parse_args_restore() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --from)
        [[ -n "${2:-}" ]] || abort_env "--from 需要一个备份目录"
        BACKUP_FROM="$2"
        shift 2
        ;;
      --stop-apps)
        STOP_APPS=1
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

main_restore() {
  parse_args_restore "$@"
  export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"

  echo "=================================================================="
  echo " MOY 轻量版恢复"
  echo " 环境文件：${ENV_FILE}"
  echo " 项目名  ：${PROJECT_NAME}"
  echo "=================================================================="

  # ① 维护模式闸门：必须最先做，且此时一个 docker 命令都还没执行
  require_maintenance_mode

  echo
  echo "--- 一、前置检查 ---"
  check_prereqs_restore
  resolve_db
  local dir
  if [[ -n "${BACKUP_FROM}" ]]; then
    dir="${BACKUP_FROM}"
  else
    dir="$(pick_latest_backup)"
    info "未指定 --from，使用最新一份备份：${dir}"
  fi
  validate_backup_dir "${dir}"

  echo
  echo "--- 二、拦流量 ---"
  stop_services_for_restore

  echo
  echo "--- 三、整库恢复 ---"
  terminate_other_connections
  do_pg_restore "${dir}"

  echo
  echo "--- 四、核对（行数 + 抽查内容）---"
  # 注意：这里任何 die 3 都会让服务保持停止——这是刻意的（见文件头）
  verify_row_counts "${dir}"
  verify_sample_rows "${dir}"

  echo
  echo "--- 五、放流量 ---"
  start_services_after_restore

  echo
  echo "=================================================================="
  ok "恢复完成并通过核对（来源：${dir}）"
  echo "   下一步：确认 /readyz 返回 200，再解除维护模式"
  echo "   http://127.0.0.1:$(env_get OA_LITE_PROXY_PORT 80)/readyz"
  echo "=================================================================="
  echo "RESTORED ${dir}"
}

# 只在直接执行时跑（被别的脚本 source 时不跑）
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main_restore "$@"
fi
