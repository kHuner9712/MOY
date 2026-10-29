#!/usr/bin/env bash
# ===========================================================================
# 轻量版回滚（阶段四 T4-05 第三批 3b-2，架构 §9.3 状态机 / §9.3.2 / §9.4）
#
# 一句话：**这是一个薄封装，不是 DDL 执行器**——它只做三件事：确认维护模式、
# 判定目标版本有没有自动回滚脚本、把版本号交给迁移 CLI；反向 SQL 的执行、
# 倒序、删台账、回滚前强制备份**全部在 `app/db/migrate.py --rollback` 里**。
#
# 为什么刻意"薄"（这是一个硬约束，不是风格偏好）：
#   本文件里**不得出现任何 SQL 语句与删表字样**（验收方式是"在脚本里搜数据库
#   客户端命令名与删表语句，计数必须 = 0"——刻意不把那两个词写在本注释里，
#   否则验收命令会把注释也数进去，永远回不了 0）。
#   一旦脚本自己写 DDL，就会出现"脚本里的回滚"与"CLI 里的回滚"两套实现：
#   备份判据、倒序规则、台账删除、幂等性各写一遍，跑起来谁先谁后无法推理。
#   回滚是唯一会删数据的动作，不允许有两套说法。
#
# 用法：
#   OA_MAINTENANCE=1 bash deploy/rollback.sh 0008            # 退掉 0008
#   OA_MAINTENANCE=1 bash deploy/rollback.sh 0011 0010       # 显式给多个（不自动级联）
#   OA_MAINTENANCE=1 bash deploy/rollback.sh --dry-run 0005   # 只判定，不执行
#   OA_MAINTENANCE=1 bash deploy/rollback.sh                  # 不给参数：退掉台账里最新一条
#
# 退出码：
#   0  回滚成功（CLI 返回 0）
#   1  回滚执行失败（CLI 报错 / 容器不可用）—— 服务状态未被本脚本改动
#   2  前置检查未通过（参数不对、环境文件缺失、版本号格式不对）
#   3  未设置 OA_MAINTENANCE=1 —— **一个容器都没动，未执行任何反向 SQL**
#   7  **目标版本没有自动回滚脚本**（< 0006，见 §9.3.2）——未执行任何反向 SQL，
#      打印「该版本无自动回滚脚本，请 DBA 按 runbook 手工处理」并指向部署手册
#
# 为什么 0002–0005 不提供自动回滚（§9.3.2 的结论，别试图"补上"）：
#   它们建的是业务库基线（建表 + 行级安全 + 产物列）。给已在客户库里的 30 多张表
#   准备一套"删除脚本"，一旦被误跑就是灾难性数据丢失；而它换来的能力（退到
#   阶段二/三）在 V1.0 交付里没有任何客户会用到。风险与收益不成比例。
#   阶段四的升级场景是"从阶段三升到阶段四"，回滚目标天然落在 0006 之后。
#
# 复用说明：source deploy/backup.sh 复用 env_get / compose / die / abort_env /
# ok / info / warn / err（backup.sh 再往下 source install.sh → validate_lite_up.sh）。
# 注意：只复用**编排与文案**函数，不复用任何查库函数——回滚前备份由 CLI 内部做
# （`pg_dump --no-owner -Fc`，缺 dump 工具时 CLI 直接拒绝回滚），本脚本不重复一遍。
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 变量名与 backup.sh / restore.sh / upgrade.sh **五者互不相同**：source 共用同一个
# shell，同名数组会被后 source 的一方清空，表现为"参数静默失效"。
_ROLLBACK_SAVED_ARGS=("$@")
set --
# shellcheck source=./backup.sh
source "${SCRIPT_DIR}/backup.sh"
set -- "${_ROLLBACK_SAVED_ARGS[@]}"
unset _ROLLBACK_SAVED_ARGS

# ---------------------------------------------------------------------------
# 参数与常量
# ---------------------------------------------------------------------------
ROLLBACK_DRY_RUN=0
ROLLBACK_LOG="${SCRIPT_DIR}/backups/rollback.log"

#: 有自动回滚脚本的最小版本号。判据来自 §9.3.2 的实测：rollback 目录里只有
#: 0006–0011 六个 down 文件；0002–0005 没有、且**不补**（理由见文件头）。
ROLLBACK_MIN_VERSION=6

#: 显式给出的目标版本（保持运维给的顺序；CLI 内部自己倒序）
declare -a ROLLBACK_TARGETS=()

usage() {
  cat <<'USAGE'
轻量版回滚（deploy/rollback.sh）

用法：
  OA_MAINTENANCE=1 bash deploy/rollback.sh <版本号…> [选项]

参数：
  <版本号…>               要退掉的迁移版本，如 0008 / 0011 0010（不自动级联）
                          不给时取迁移台账里最新一条

选项：
  --dry-run               只做维护闸门与版本判定，**不执行任何反向 SQL**
  --env-file <路径>       环境文件（默认 deploy/.env.lite）
  --project-name <名称>   compose 项目名（默认 officeai-lite）
  -h, --help              显示本帮助

必须设置 OA_MAINTENANCE=1（意图声明 + 防误触），否则直接拒绝且不动任何容器。

退出码：0 成功 / 1 执行失败 / 2 前置未过 / 3 未确认维护模式 /
        7 目标版本无自动回滚脚本（需 DBA 按 runbook 手工处理）
USAGE
}

rollback_log() {
  local line="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
  mkdir -p "$(dirname "${ROLLBACK_LOG}")" 2>/dev/null || true
  printf '%s\n' "${line}" >>"${ROLLBACK_LOG}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 一、维护闸门（必须在任何 docker / 数据库命令之前）
# ---------------------------------------------------------------------------
require_maintenance_mode_rollback() {
  if [[ "${OA_MAINTENANCE:-}" != "1" ]]; then
    err "拒绝执行：未设置 OA_MAINTENANCE=1"
    cat >&2 <<'TIP'
       回滚会删掉新版本建的表与列：跑在业务时间等于让客户读到残缺数据。
       确认可以停机后，用这条命令重跑：
         OA_MAINTENANCE=1 bash deploy/rollback.sh <版本号>
       只想先判定"这个版本能不能自动回滚"（不动任何东西）：
         OA_MAINTENANCE=1 bash deploy/rollback.sh --dry-run <版本号>
       本检查发生在所有命令之前，因此现在数据库未被改动。
TIP
    rollback_log "ABORT maintenance_gate"
    exit 3
  fi
  ok "维护模式已确认（OA_MAINTENANCE=1）"
  rollback_log "maintenance_gate ok"
}

check_prereqs_rollback() {
  [[ -f "${ENV_FILE}" ]] || abort_env "缺环境文件：${ENV_FILE}" \
    "回滚要连哪个库由它决定"
  command -v docker >/dev/null 2>&1 || abort_env "找不到 docker 命令"
  # --dry-run 刻意不检查 docker 守护进程与库连接：它只回答"该不该走自动回滚"，
  # 在运维的笔记本上也要能给出结论（这正是它能被 CI 断言的原因）。
  if (( ROLLBACK_DRY_RUN == 1 )); then
    info "前置检查：--dry-run 只做版本判定，不检查 Docker 与数据库连接"
    return 0
  fi
  [[ -n "$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)" ]] \
    || abort_env "连不上 Docker 守护进程" \
      "回滚要通过一次性 Job 调用迁移 CLI，需要 Docker"
  ok "前置检查通过（docker 可用、环境文件存在）"
}

# ---------------------------------------------------------------------------
# 二、目标版本判定（纯字符串，不碰数据库）
# ---------------------------------------------------------------------------
#: 把 `0008` / `0008_conversation_message` / `8` 归一成四位版本号。
#: 不做"猜"：非法输入直接 2 退出，避免把 `0008abc` 当成 0008 去退。
normalize_version() {
  local raw="$1"
  local digits="${raw%%_*}"
  if ! [[ "${digits}" =~ ^[0-9]{1,4}$ ]]; then
    abort_env "版本号格式不对：${raw}" \
      "写四位数字即可：0008 / 0011（也可以写 0008_conversation_message）"
  fi
  printf '%04d' "$((10#${digits}))"
}

#: 收集目标版本：显式参数优先；没给时只在"台账恰好追平磁盘"时推断出当前版本。
#:
#: 为什么要"磁盘清单 + CLI 计数"反推，而不是查台账表：本脚本刻意不认识任何
#: 数据库客户端（薄封装约束），只能消费迁移 CLI 的机器可读输出。
#: 而它给的是**计数**（`applied` / `available`），不是版本清单——所以只有在
#: `applied == available`（库里已追平磁盘上的全部脚本）时才能唯一确定
#: "当前版本 = 最大版本号"。差一条都不猜：回滚是删数据的动作，猜错版本等于删错表。
#: 猜不出来时要求显式给版本号——这也是"不自动级联"的另一半含义。
collect_targets() {
  local arg v name base json applied available latest=""
  if (( ${#ROLLBACK_TARGETS[@]} > 0 )); then
    local -a normalized=()
    for arg in "${ROLLBACK_TARGETS[@]}"; do
      normalized+=("$(normalize_version "${arg}")")
    done
    ROLLBACK_TARGETS=("${normalized[@]}")
    return 0
  fi
  if (( ROLLBACK_DRY_RUN == 1 )); then
    abort_env "没给版本号，且 --dry-run 不连数据库" \
      "显式指定要退的版本：bash deploy/rollback.sh --dry-run 0008"
  fi
  info "未指定版本号，按迁移 CLI 的输出推算当前版本…"
  json="$(compose --profile migrate run --rm -T migrate \
    python -m app.db.migrate --check --json 2>/dev/null | grep -m1 '^{' || true)"
  applied="$(sed -n 's/.*"applied":[[:space:]]*\([0-9]\+\).*/\1/p' <<<"${json}" | head -n 1)"
  available="$(sed -n 's/.*"available":[[:space:]]*\([0-9]\+\).*/\1/p' <<<"${json}" | head -n 1)"
  if [[ -z "${json}" || -z "${applied}" || -z "${available}" ]]; then
    abort_env "读不出当前版本号（迁移 CLI 没有返回可用的计数）" \
      "显式指定要退的版本，例如：bash deploy/rollback.sh 0008"
  fi
  if [[ "${applied}" != "${available}" ]]; then
    abort_env "库里有尚未应用的迁移（已应用 ${applied} / 可应用 ${available}），不猜测当前版本" \
      "回滚会删数据，猜错版本等于删错表；请显式指定要退的版本：" \
      "  bash deploy/rollback.sh 0008" \
      "若不确定库里停在哪一版，先按部署手册的「升级前核对」章节查一次台账"
  fi
  for name in "${SCRIPT_DIR}"/migrations/0*.sql; do
    [[ -e "${name}" ]] || continue
    base="$(basename "${name}")"
    v="${base%%_*}"
    if [[ -z "${latest}" ]] || (( 10#${v} > 10#${latest} )); then
      latest="${v}"
    fi
  done
  [[ -n "${latest}" ]] || abort_env "推算不出当前版本：磁盘上没有迁移脚本" \
    "这个库可能还没做过迁移；先跑 install.sh"
  info "当前版本判定为 ${latest}（台账已追平磁盘 ${applied}/${available}）"
  ROLLBACK_TARGETS=("${latest}")
}

#: 判定是否全部有自动回滚脚本。**任何一个低于门槛就整体不动**——
#: 混着退（0005 + 0008）时若只退一半，库会停在一个谁也没验证过的中间状态。
guard_rollback_range() {
  local v num lowest=""
  for v in "${ROLLBACK_TARGETS[@]}"; do
    num="$((10#${v}))"
    if [[ -z "${lowest}" ]] || (( num < lowest )); then
      lowest="${num}"
    fi
  done
  (( lowest >= ROLLBACK_MIN_VERSION )) && return 0

  # 走到这里说明目标里出现了 0002–0005（或更早）：**未执行任何反向 SQL**
  err "该版本无自动回滚脚本，请 DBA 按 runbook 手工处理"
  cat >&2 <<TIP
       目标版本：${ROLLBACK_TARGETS[*]}（低于 0006）
       为什么没有：0006 之前是业务库基线（建表 + 行级安全 + 产物列），
       给已在客户库里的表准备删除脚本，误跑一次就是数据丢失，收益为零。
       怎么做：打开 docs/deployment-guide.md 的「降级到阶段三及更早」章节，
       按其中的逐条清单由 DBA 用超级用户执行。
       说明：本脚本**没有执行任何反向 SQL**，数据库与代码都没变。
       退出码 7：与"回滚执行失败"的 1 区分开——这条不需要排查故障，需要人做决定。
TIP
  rollback_log "ABORT no_down_script ${ROLLBACK_TARGETS[*]}"
  exit 7
}

# ---------------------------------------------------------------------------
# 三、交给迁移 CLI（反向 SQL / 倒序 / 删台账 / 回滚前备份都在里面）
# ---------------------------------------------------------------------------
#: 调用一次性 Job。**命令用 `compose run … python -m app.db.migrate --rollback …`**：
#: compose 的 `run SERVICE CMD` 会**替换**服务里写死的 `--apply`，所以这里必须
#: 把完整命令重写一遍，而不是在 `--apply` 后面追加 `--rollback`（那会变成两个
#: 模式同时给，CLI 会拒绝）。
do_rollback_cli() {
  local -a targets=("$@")
  local out="" rc=0
  info "调用迁移 CLI 回滚：${targets[*]}"
  if (( ROLLBACK_DRY_RUN == 1 )); then
    ok "DRY_RUN：将执行 docker compose … run --rm migrate python -m app.db.migrate --rollback ${targets[*]}"
    echo "DRY_RUN_OK ${targets[*]}"
    return 0
  fi
  out="$(compose --profile migrate run --rm -T migrate \
    python -m app.db.migrate --rollback "${targets[@]}" 2>&1)" || rc=$?
  if (( rc != 0 )); then
    printf '%s\n' "${out}" | sed 's/^/       /' >&2
    rollback_log "cli FAILED rc=${rc} ${targets[*]}"
    die 1 "回滚执行失败（退出码 ${rc}）" \
      "常见原因：① 该版本在本库从未应用过（台账里没有）② 容器起不来" \
      "         ③ 回滚前的强制备份没做成（CLI 会因此拒绝回滚，这是刻意的）" \
      "修好后重跑本脚本；不要手工改表结构绕过它"
  fi
  printf '%s\n' "${out}" | sed 's/^/       /'
  ok "回滚完成：${targets[*]}"
  rollback_log "cli ok ${targets[*]}"
}

# ---------------------------------------------------------------------------
# 参数解析与主流程
# ---------------------------------------------------------------------------
parse_args_rollback() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        ROLLBACK_DRY_RUN=1
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
      -*)
        usage >&2
        abort_env "未知参数：$1"
        ;;
      *)
        ROLLBACK_TARGETS+=("$1")
        shift
        ;;
    esac
  done
}

main_rollback() {
  parse_args_rollback "$@"
  export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"

  echo "=================================================================="
  echo " MOY 轻量版回滚"
  echo " 环境文件：${ENV_FILE}"
  echo " 项目名  ：${PROJECT_NAME}"
  echo " 模式    ：$([[ ${ROLLBACK_DRY_RUN} == 1 ]] && echo '仅判定（--dry-run）' || echo '执行回滚')"
  echo " 日志    ：${ROLLBACK_LOG}"
  echo "=================================================================="

  # ① 维护闸门：最先做，此时一个 docker / 数据库命令都还没执行
  require_maintenance_mode_rollback
  rollback_log "=== rollback start dry_run=${ROLLBACK_DRY_RUN} targets=${ROLLBACK_TARGETS[*]:-auto} ==="

  # ② 前置检查（--dry-run 下不检查 Docker 与库）
  echo
  echo "--- 一、前置检查 ---"
  check_prereqs_rollback

  # ③ 目标版本判定（低于 0006 → 7 退出，未执行任何反向 SQL）
  echo
  echo "--- 二、目标版本判定 ---"
  collect_targets
  guard_rollback_range
  ok "目标版本通过判定（全部 ≥ 0006，有自动回滚脚本）：${ROLLBACK_TARGETS[*]}"

  # ④ 交给 CLI
  echo
  echo "--- 三、回滚（反向 SQL / 倒序 / 删台账 / 回滚前备份均在迁移 CLI 内）---"
  do_rollback_cli "${ROLLBACK_TARGETS[@]}"

  echo
  echo "=================================================================="
  ok "回滚流程结束（目标：${ROLLBACK_TARGETS[*]}）"
  echo "   下一步：① 把镜像标签切回对应版本后重启（编排层）"
  echo "           ② 用 validate_lite_up.sh 复核就绪与行数（行数核对不在本脚本内）"
  echo "           ③ 确认无误再解除维护模式"
  echo "=================================================================="
  rollback_log "RESULT DONE ${ROLLBACK_TARGETS[*]}"
  echo "ROLLBACK_DONE ${ROLLBACK_TARGETS[*]}"
}

# 只在直接执行时跑（被别的脚本 source 时不跑）
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main_rollback "$@"
fi
