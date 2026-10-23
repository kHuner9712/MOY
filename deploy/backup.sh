#!/usr/bin/env bash
# ===========================================================================
# 轻量版备份（阶段四 T4-05 第三批 3b-2）
#
# 一句话：**在不动业务的前提下，把业务库（含产物二进制）整库导出，并留下
# 一份可机器比对的指纹（行数 + 抽查行内容）**，供 restore.sh 事后核对。
#
# 用法：
#   bash deploy/backup.sh                       # 备份到 deploy/backups/<UTC 时间戳>/
#   bash deploy/backup.sh --out-dir /data/bak   # 换备份根目录（建议放宿主机独立磁盘）
#   bash deploy/backup.sh --artifacts-dir /srv/artifacts   # 额外打包一个产物目录
#   bash deploy/backup.sh --project-name oalinst --env-file deploy/.env.lite
#
# 产物（目录名是 UTC 时间戳，排序即时间序）：
#   backups/<ts>/db.dump           pg_dump -Fc 整库导出（含 plugin_artifact.content 的 BYTEA）
#   backups/<ts>/meta.json         人读/机读摘要（版本、镜像、行数、dump sha256）
#   backups/<ts>/meta.counts.tsv   表<TAB>行数，restore.sh 逐表比对用
#   backups/<ts>/meta.samples.tsv  表<TAB>租户<TAB>主键<TAB>行 md5，restore.sh 抽查用
#   backups/<ts>/artifacts.tar     仅当显式给了 --artifacts-dir 且目录存在
#
# 退出码：
#   0  备份成功
#   1  备份失败（**不会留下半份备份**：整份目录以 .partial 结尾，成功后原子改名）
#   2  前置检查未通过（缺命令、环境文件缺失、库不可达、参数不对）
#
# 三个设计决定（为什么）：
#   1. **pg_dump 在容器里跑，不依赖宿主装 postgres 客户端**。客户机上通常只有
#      docker；而 postgres 镜像自带 pg_dump，版本与库严格一致（跨大版本 dump 会失败）。
#   2. **先写 .partial 再改名**。备份写到一半被打断（磁盘满、Ctrl-C、SSH 断）时，
#      留一个看起来完整的目录比没有目录危险——restore.sh 会拿它去恢复。
#   3. **meta 用 JSON + TSV 两份**：JSON 给人看、给 upgrade 工具读；TSV 给本脚本
#      和 restore.sh 比对用。为什么要 TSV：客户机上不保证有 jq/python，
#      而"从 JSON 里取数组"用 grep 是做不干净的。TSV 一个 awk 就够。
#
# 复用说明：本脚本 source deploy/install.sh 以复用 env_get / compose / die /
# abort_env（install.sh 与 validate_lite_up.sh 的 main 都有 BASH_SOURCE 守卫，
# 被 source 时不会真的去装栈）。restore.sh 再 source 本脚本以复用 db_counts /
# db_sample_rows —— 这几段 SQL 涉及时区与 md5 口径，复制第二份必然漂移。
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 复用 install.sh 的公共函数（其 main 有守卫，不会被执行）
# 变量名必须与 install.sh / restore.sh 的**都不相同**：source 是同一 shell，
# 共用变量名会让后 source 的一方把前者的参数数组覆盖成空（实测 `--help`
# 会被静默吞掉、`--out-dir` 失效，脚本仍按默认路径备份）。
_BACKUP_SAVED_ARGS=("$@")
set --
# shellcheck source=./install.sh
source "${SCRIPT_DIR}/install.sh"
set -- "${_BACKUP_SAVED_ARGS[@]}"
unset _BACKUP_SAVED_ARGS

# ---------------------------------------------------------------------------
# 常量与可覆盖参数
# ---------------------------------------------------------------------------
BACKUP_ROOT="${SCRIPT_DIR}/backups"
ARTIFACTS_DIR=""
PROJECT_NAME="officeai-lite"
DB_SUPERUSER=""
DB_NAME=""

#: 行数快照覆盖的表（PRD/架构里"丢了就是事故"的那几张）。
#: restore.sh 按这份名单逐表比对，所以**顺序即报告顺序**。
SNAPSHOT_TABLES=(
  tenant
  conversation
  message
  user_memory
  plugin_task
  plugin_artifact
  usage_event
  audit_log
)

#: 抽查行数（团队要求 10 行：消息 5 + 记忆 5）
SAMPLE_MESSAGE_ROWS=5
SAMPLE_MEMORY_ROWS=5

usage() {
  cat <<'USAGE'
轻量版备份（deploy/backup.sh）

用法：
  bash deploy/backup.sh [选项]

选项：
  --out-dir <目录>        备份根目录（默认 deploy/backups）
  --artifacts-dir <目录>  额外打包的产物目录（默认不打包，见下）
  --env-file <路径>       环境文件（默认 deploy/.env.lite）
  --project-name <名称>   compose 项目名（默认 officeai-lite）
  -h, --help              显示本帮助

关于产物目录：轻量版**没有独立的产物目录**——plugin_artifact.content 是 BYTEA，
已经包含在 db.dump 里。只有在你另外挂了外部产物目录时才需要 --artifacts-dir。

退出码：0 成功 / 1 备份失败 / 2 前置检查未过
USAGE
}

# ---------------------------------------------------------------------------
# 库访问（全部走容器内 psql，宿主不需要 postgres 客户端）
# ---------------------------------------------------------------------------
#: 解析库连接信息：超级用户与库名都从环境文件取，避免"备份了错库"。
resolve_db() {
  DB_SUPERUSER="$(env_get OA_POSTGRES_USER officeai)"
  DB_NAME="$(env_get OA_POSTGRES_DB officeai)"
  [[ -n "${DB_SUPERUSER}" ]] || abort_env "环境文件里 OA_POSTGRES_USER 为空：${ENV_FILE}"
  [[ -n "${DB_NAME}" ]] || abort_env "环境文件里 OA_POSTGRES_DB 为空：${ENV_FILE}"
}

#: 单值查询（-tA：无表头、| 分隔、对齐关闭）
psql_value() {
  compose exec -T postgres psql -U "${DB_SUPERUSER}" -d "${DB_NAME}" \
    -tA -v ON_ERROR_STOP=1 -c "$1" | tr -d '\r'
}

#: 过滤出真实存在的表。为什么要过滤：老版本库可能没有 plugin_artifact 等表，
#: 直接 SELECT count(*) 会让整条查询失败，备份就永远做不成。
db_existing_tables() {
  local array_sql list
  array_sql="ARRAY["
  local t first=1
  for t in "${SNAPSHOT_TABLES[@]}"; do
    if (( first == 1 )); then first=0; else array_sql+=","; fi
    array_sql+="'${t}'"
  done
  array_sql+="]"
  list="$(psql_value "SELECT string_agg(t, ' ' ORDER BY t) FROM unnest(${array_sql}) AS t WHERE to_regclass('public.' || t) IS NOT NULL")"
  printf '%s' "${list}"
}

#: 逐表行数，输出 `表|行数`。
#: 必须显式 `SET TIME ZONE 'UTC'`：不设的话会话时区取决于容器/客户端环境，
#: 抽查用的 `md5(行::text)` 会因 timestamptz 的文本化差异对不上（假失败）。
db_counts() {
  local tables="$1" sql="" t
  [[ -n "${tables}" ]] || return 0
  for t in ${tables}; do
    [[ -n "${sql}" ]] && sql+=" UNION ALL "
    sql+="SELECT '${t}' AS tbl, count(*) AS n FROM ${t}"
  done
  psql_value "SET TIME ZONE 'UTC'; ${sql} ORDER BY 1"
}

#: 抽若干行，输出 `表|租户|主键|行 md5`。
#: 为什么用 md5(整行::text)：比"挑几个字段"更难糊弄——列新增/内容变化都会变；
#: 为什么按主键排序取前 N 行：结果确定，备份与恢复两次调用拿到的是同一批行。
db_sample_rows() {
  local table="$1"
  local id_col="$2"
  local limit="$3"
  psql_value "SET TIME ZONE 'UTC';
    SELECT '${table}' || '|' || tenant_id || '|' || ${id_col} || '|' || md5(t::text)
      FROM ${table} t
     ORDER BY tenant_id, ${id_col}
     LIMIT ${limit}"
}

#: 按给定主键回查行 md5（restore.sh 抽查用）。
#: 用 JOIN + VALUES 而不是按序 limit：恢复后如果新增了行，按序 limit 可能换一批，
#: 那就变成"拿不同的行比不同的事"，测不出任何东西。
db_verify_rows() {
  local table="$1"
  local id_col="$2"
  local values="$3" # 形如 ('t1','id1'),('t1','id2')
  psql_value "SET TIME ZONE 'UTC';
    WITH k(tenant_id, key) AS (VALUES ${values})
    SELECT '${table}' || '|' || k.tenant_id || '|' || k.key || '|' || md5(t::text)
      FROM k JOIN ${table} t ON t.tenant_id = k.tenant_id AND t.${id_col} = k.key
     ORDER BY 1"
}

# ---------------------------------------------------------------------------
# SQL 值转义（主键与库名会拼进 SQL）
# ---------------------------------------------------------------------------
#: 单引号翻倍。主键都是 ULID/ascii，理论上不会出现单引号，但拼 SQL 不做转义
#: 是坏习惯——一次手工造的脏数据就能让备份脚本报奇怪的语法错。
sql_literal() {
  local v="${1//\'/\'\'}"
  printf "'%s'" "${v}"
}

# ---------------------------------------------------------------------------
# 前置检查
# ---------------------------------------------------------------------------
check_prereqs_backup() {
  command -v docker >/dev/null 2>&1 || abort_env "找不到 docker 命令"
  local docker_ver
  docker_ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  [[ -n "${docker_ver}" ]] || abort_env \
    "连不上 Docker 守护进程" \
    "Windows：启动 Docker Desktop 并等托盘变绿" \
    "Linux ：systemctl start docker"
  command -v sha256sum >/dev/null 2>&1 || abort_env "找不到 sha256sum" "用于登记 db.dump 指纹"

  [[ -f "${ENV_FILE}" ]] || abort_env "缺环境文件：${ENV_FILE}" \
    "备份要连哪个库由环境文件决定；没有它无法确定库名与超级用户"

  # postgres 必须在跑：备份连的是一个停机库时，pg_dump 会报连接失败，
  # 但更糟的是"连上了却连到别的实例"。先确认本项目容器在运行。
  local cid state
  cid="$(compose ps -q postgres 2>/dev/null | head -n 1 || true)"
  [[ -n "${cid}" ]] || abort_env "本项目（${PROJECT_NAME}）的 postgres 容器不存在" \
    "先安装：bash deploy/install.sh --online（或 --offline）" \
    "或确认 --project-name 与服务当前使用的项目名一致"
  state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${cid}" 2>/dev/null || true)"
  [[ "${state}" == "healthy" ]] || abort_env "postgres 状态是 ${state}，不是 healthy" \
    "等它健康再备份：docker compose ps" \
    "备份一个正在 initdb 的库会拿到残缺快照"
  ok "postgres 容器就绪（${state}）"
}

# ---------------------------------------------------------------------------
# 备份步骤
# ---------------------------------------------------------------------------
#: 整库导出。写进 .partial 目录；导出后立刻用 PGDMP 魔数与大小做一次自检——
#: 这一步能挡住"磁盘满了但命令返回 0"这种最坑的情况（重定向到满盘时
#: pg_dump 的退出码不可靠，文件却是 0 字节）。
do_pg_dump() {
  local target="$1"
  info "导出业务库 ${DB_NAME}（pg_dump -Fc，容器内执行）…"
  compose exec -T postgres pg_dump \
    -U "${DB_SUPERUSER}" \
    --no-owner \
    -Fc \
    -d "${DB_NAME}" >"${target}" \
    || die 1 "pg_dump 失败（上面是 pg_dump 的错误输出）" \
      "常见原因：库名/超级用户不对、磁盘满、postgres 正在重启"

  local size magic
  size="$(wc -c <"${target}" | tr -d ' ')"
  magic="$(head -c 5 "${target}" || true)"
  [[ "${magic}" == "PGDMP" ]] || die 1 "导出的文件不是 pg_dump 自定义格式（前 5 字节 = '${magic}'）" \
    "通常是重定向写到了满盘，或 pg_dump 报错但被 || 吃掉" \
    "这个文件不能用于恢复，已丢弃"
  (( size > 1024 )) || die 1 "导出的文件只有 ${size} 字节，不足以包含任何数据"
  ok "业务库已导出（$(( size / 1024 )) KiB，PGDMP 格式校验通过）"
}

#: 可选：打包外部产物目录。
#: 轻量版把产物存在库里（plugin_artifact.content BYTEA），所以默认没有目录可打。
#: 只有运维显式给 --artifacts-dir 时才打包，避免"备份脚本自己发明一个路径"。
do_artifacts_tar() {
  local target="$1"
  if [[ -z "${ARTIFACTS_DIR}" ]]; then
    printf '%s' "in_db"
    return 0
  fi
  if [[ ! -d "${ARTIFACTS_DIR}" ]]; then
    warn "--artifacts-dir 指向的目录不存在：${ARTIFACTS_DIR}（跳过，不影响库备份）"
    printf '%s' "missing"
    return 0
  fi
  info "打包产物目录：${ARTIFACTS_DIR} …"
  tar -czf "${target}" -C "$(dirname "${ARTIFACTS_DIR}")" "$(basename "${ARTIFACTS_DIR}")" \
    || die 1 "打包产物目录失败：${ARTIFACTS_DIR}"
  ok "产物目录已打包（$(( $(wc -c <"${target}" | tr -d ' ') / 1024 )) KiB）"
  printf '%s' "tarred"
}

#: 写 meta.json（人读/机读摘要）。用 printf 手工拼而不是 jq：客户机不保证有 jq，
#: 而这里的所有值都是我们自己算出来的 ASCII（表名、数字、sha256、时间戳）。
write_meta_json() {
  local dir="$1"
  local version dump_sha dump_size artifacts_mode tables counts_tsv
  version="$(env_get OA_IMAGE_TAG 1.0.0)"
  dump_sha="$(sha256sum "${dir}/db.dump" | cut -d' ' -f1)"
  dump_size="$(wc -c <"${dir}/db.dump" | tr -d ' ')"
  artifacts_mode="$2"
  tables="$3"
  counts_tsv="${dir}/meta.counts.tsv"

  {
    printf '{\n'
    printf '  "meta_version": 1,\n'
    printf '  "product": "officeai-lite",\n'
    printf '  "version": "%s",\n' "${version}"
    printf '  "created_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "project_name": "%s",\n' "${PROJECT_NAME}"
    printf '  "database": "%s",\n' "${DB_NAME}"
    printf '  "superuser": "%s",\n' "${DB_SUPERUSER}"
    printf '  "dump_format": "pg_dump -Fc --no-owner",\n'
    printf '  "dump_bytes": %s,\n' "${dump_size}"
    printf '  "dump_sha256": "sha256:%s",\n' "${dump_sha}"
    printf '  "artifacts": {\n'
    printf '    "mode": "%s",\n' "${artifacts_mode}"
    if [[ "${artifacts_mode}" == "tarred" ]]; then
      printf '    "dir": "%s",\n' "${ARTIFACTS_DIR}"
    else
      printf '    "dir": null,\n'
    fi
    printf '    "note": "轻量版产物（plugin_artifact.content）存在库内 BYTEA，已包含在 db.dump 中"\n'
    printf '  },\n'
    printf '  "row_counts": {\n'
    local line first=1 tbl n
    while IFS='|' read -r tbl n; do
      [[ -n "${tbl}" ]] || continue
      if (( first == 1 )); then first=0; else printf ',\n'; fi
      printf '    "%s": %s' "${tbl}" "${n}"
    done <"${counts_tsv}"
    printf '\n  },\n'
    printf '  "counts_file": "meta.counts.tsv",\n'
    printf '  "samples_file": "meta.samples.tsv",\n'
    printf '  "counted_tables": "%s"\n' "$(printf '%s' "${tables}" | tr ' ' ',')"
    printf '}\n'
  } >"${dir}/meta.json"
}

#: 打印摘要
print_summary() {
  local dir="$1"
  echo
  echo "------------------------------------------------------------------"
  echo " 备份完成：${dir}"
  echo "------------------------------------------------------------------"
  printf ' %-22s %s\n' "db.dump" "$(( $(wc -c <"${dir}/db.dump" | tr -d ' ') / 1024 )) KiB（pg_dump -Fc）"
  while IFS='|' read -r tbl n; do
    [[ -n "${tbl}" ]] || continue
    printf ' %-22s %s 行\n' "${tbl}" "${n}"
  done <"${dir}/meta.counts.tsv"
  printf ' %-22s %s 行（抽查指纹）\n' "meta.samples.tsv" "$(grep -c . "${dir}/meta.samples.tsv" || true)"
  [[ -f "${dir}/artifacts.tar" ]] && printf ' %-22s %s\n' "artifacts.tar" "$(( $(wc -c <"${dir}/artifacts.tar" | tr -d ' ') / 1024 )) KiB"
  echo
  echo " 恢复：bash deploy/restore.sh --from ${dir}"
  echo " 注意：备份含全部业务数据与产物，按客户数据密级存放（建议 chmod 700 目录）"
  echo "------------------------------------------------------------------"
}

# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
parse_args_backup() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --out-dir)
        [[ -n "${2:-}" ]] || abort_env "--out-dir 需要一个目录"
        BACKUP_ROOT="$2"
        shift 2
        ;;
      --artifacts-dir)
        [[ -n "${2:-}" ]] || abort_env "--artifacts-dir 需要一个目录"
        ARTIFACTS_DIR="$2"
        shift 2
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

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
main_backup() {
  parse_args_backup "$@"
  export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"

  echo "=================================================================="
  echo " MOY 轻量版备份"
  echo " 环境文件：${ENV_FILE}"
  echo " 项目名  ：${PROJECT_NAME}"
  echo " 备份根  ：${BACKUP_ROOT}"
  echo "=================================================================="

  check_prereqs_backup
  resolve_db

  local ts partial final
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  final="${BACKUP_ROOT}/${ts}"
  partial="${BACKUP_ROOT}/.${ts}.partial"

  # 同一秒内跑两次也不会互相覆盖：final 已存在时加序号
  local suffix=1
  while [[ -e "${final}" ]]; do
    final="${BACKUP_ROOT}/${ts}-${suffix}"
    suffix=$((suffix + 1))
  done
  partial="${final}.partial"

  mkdir -p "${partial}" || die 1 "无法创建备份目录：${partial}"
  chmod 700 "${BACKUP_ROOT}" "${partial}" 2>/dev/null || true

  # 1) 整库导出
  do_pg_dump "${partial}/db.dump"

  # 2) 行数快照
  local tables
  tables="$(db_existing_tables)"
  [[ -n "${tables}" ]] || die 1 "库里没有一张待统计的表（库名对了吗：${DB_NAME}）"
  info "统计行数：${tables// /, }"
  db_counts "${tables}" >"${partial}/meta.counts.tsv" || die 1 "统计行数失败"
  ok "行数快照完成（$(grep -c . "${partial}/meta.counts.tsv" || true) 张表）"

  # 3) 抽查指纹（消息 5 行 + 记忆 5 行，共 10 行）
  : >"${partial}/meta.samples.tsv"
  case " ${tables} " in
    *" message "*)
      db_sample_rows message message_id "${SAMPLE_MESSAGE_ROWS}" >>"${partial}/meta.samples.tsv" \
        || die 1 "抽样 message 失败"
      ;;
    *)
      warn "库里没有 message 表，跳过消息抽查"
      ;;
  esac
  case " ${tables} " in
    *" user_memory "*)
      db_sample_rows user_memory memory_id "${SAMPLE_MEMORY_ROWS}" >>"${partial}/meta.samples.tsv" \
        || die 1 "抽样 user_memory 失败"
      ;;
    *)
      warn "库里没有 user_memory 表，跳过记忆抽查"
      ;;
  esac
  ok "抽查指纹完成（$(grep -c . "${partial}/meta.samples.tsv" || true) 行）"

  # 4) 可选产物目录
  local artifacts_mode
  artifacts_mode="$(do_artifacts_tar "${partial}/artifacts.tar")"
  if [[ "${artifacts_mode}" != "tarred" ]]; then
    # 没打包时删掉可能存在的空 tar（tar 失败会留半份，do_artifacts_tar 已 die，
    # 但 0 字节文件在 ${partial} 里没有任何意义）
    [[ -s "${partial}/artifacts.tar" ]] || rm -f "${partial}/artifacts.tar" 2>/dev/null || true
  fi

  # 5) meta.json
  write_meta_json "${partial}" "${artifacts_mode}" "${tables}"
  ok "meta.json 已写入"

  # 6) 原子改名：只有全部步骤成功，才让这份备份"存在"
  mv "${partial}" "${final}" || die 1 "备份目录改名失败：${partial} -> ${final}"
  chmod -R go-rwx "${final}" 2>/dev/null || true

  print_summary "${final}"
  echo "BACKUP_OK ${final}"
}

# 只在直接执行时跑（被 restore.sh source 复用函数时不跑）
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main_backup "$@"
fi
