#!/usr/bin/env bash
# 麦子 V1.0 · 四阶段终验 9 项（可复跑）
# =============================================================================
# 为什么要有这个脚本：终验是"验收证据"，不能靠人肉一条条敲、再手抄进报告。
# 手敲的问题是：命令会记错、退出码会被管道吃掉、耗时没人记、下次复跑对不上。
# 这个脚本把这些都钉死，产出可复跑的 PASS/FAIL 表。
#
# 用法：
#   bash qa/phase4-verify.sh                      # 默认：第 1 项跑无 PG 全量
#   bash qa/phase4-verify.sh --pytest-mode pg     # 第 1 项跑带 PG 全量（需 OA_TEST_DATABASE_URL）
#   bash qa/phase4-verify.sh --pytest-mode both   # 两轮都跑（时间约翻倍）
#   bash qa/phase4-verify.sh --pytest-mode skip   # 跳过 pytest（只验其余 8 项，约 40s）
#   bash qa/phase4-verify.sh --out /tmp/myreport  # 指定结果目录
#
# 退出码：0 = 9 项全 PASS；1 = 有 FAIL；2 = 用法错误。
#
# --- 三条纪律（都是踩过坑才写进来的）----------------------------------------
# 1. **退出码不许经管道取**：`cmd | tail; echo $?` 拿到的是 `tail` 的码。
#    本脚本一律 `cmd > log 2>&1; code=$?`，先落码再读日志。
# 2. **self-test 分族判定要内置**（`--self-test` 的退出码语义与门禁本体相反）：
#      族A（成功时 exit 1）：batch_clock deploy gate_wiring migrations openapi
#                            packaging plugins schema_retention secrets
#      族B（成功时 exit 0）：config_names deps images pg_markers
#                            repository_parity import_directions offline_pkgs
#    按族A 的期望去判族B，会把 7 个通过的门禁判成失败（反之亦然）。
# 3. **环境约束要显式处理，不要当成"通过"**：
#      - pytest 的 `--basetemp` 必须指到仓库外。仓库内的 basetemp 会被宿主
#        safe-delete 钩子在收尾时拦进程，结果是没有 summary 行（不是失败，是没跑完）。
#      - 前端 `vitest --coverage` 默认把产物写进仓库内 `apps/web/coverage`，vitest 每次
#        都会先清那个目录，于是被同一个钩子拦下（`count:163 > threshold:50`）→ exit 1——
#        **不是用例失败**（55 个用例全过）。本脚本用 `--coverage.reportsDirectory`
#        把产物写到结果目录，既不碰仓库也不触发钩子；CI 无钩子，原始命令同样通过。
# =============================================================================

set -uo pipefail

# CRLF 自检：含 \r 的 .sh 在 bash 下会**静默跳过部分函数体**（不报语法错，函数直接返回 0）。
# 这不是理论风险：本脚本第一版就是 CRLF，导致第 9c 项恒为"通过"（函数体没执行，退出码 0），
# 差点把一个本该 FAIL 的检查记成 PASS。宁可在入口拦下。
if [ "$(tr -cd '\r' < "${BASH_SOURCE[0]}" | wc -c | tr -d ' ')" != "0" ]; then
  echo "错误：$(basename "${BASH_SOURCE[0]}") 含 CRLF 行尾，bash 会静默跳过函数体。" >&2
  echo "修法：python -c \"import pathlib;p=pathlib.Path('$0');p.write_bytes(p.read_bytes().replace(b'\\\\r\\\\n',b'\\\\n'))\"" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

PYTEST_MODE="nopg"
OUT_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --pytest-mode) PYTEST_MODE="${2:-}"; shift 2 ;;
    --pytest-mode=*) PYTEST_MODE="${1#*=}"; shift ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --out=*) OUT_DIR="${1#*=}"; shift ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1（-h 看用法）" >&2; exit 2 ;;
  esac
done

case "$PYTEST_MODE" in
  nopg|pg|both|skip) ;;
  *) echo "--pytest-mode 只允许 nopg|pg|both|skip，实际 '$PYTEST_MODE'" >&2; exit 2 ;;
esac

# 关掉彩色输出：vitest 会给 "Tests  55 passed" 插 ANSI 码，导致提取数字的正则抓不到
#（实测过：`grep -Eo 'Tests +[0-9]+ passed'` 对彩色输出零命中）。宁可少点颜色，也不要空数字。
export NO_COLOR=1
export FORCE_COLOR=0
export CLICOLOR=0

OUT_DIR="${OUT_DIR:-/tmp/phase4-verify-$(date +%Y%m%dT%H%M%S)}"
mkdir -p "$OUT_DIR" || exit 2

# --- 解释器：优先用适配层 venv（依赖版本与 pyproject 对齐）---------------------
if [ -x "$ROOT/apps/adapter/.venv/Scripts/python.exe" ]; then
  PY="$ROOT/apps/adapter/.venv/Scripts/python.exe"
  MYPY="$ROOT/apps/adapter/.venv/Scripts/mypy.exe"
  RUFF="$ROOT/apps/adapter/.venv/Scripts/ruff.exe"
elif [ -x "$ROOT/apps/adapter/.venv/bin/python" ]; then
  PY="$ROOT/apps/adapter/.venv/bin/python"
  MYPY="$ROOT/apps/adapter/.venv/bin/mypy"
  RUFF="$ROOT/apps/adapter/.venv/bin/ruff"
else
  echo "找不到 apps/adapter/.venv 的解释器；请先按 README 建 venv" >&2
  exit 2
fi

# --- 结果表 -------------------------------------------------------------------
N_STEP=0
N_PASS=0
N_FAIL=0
SUMMARY="$OUT_DIR/summary.txt"
: > "$SUMMARY"

# check <序号> <名称> <期望退出码，逗号分隔> <关键数字提取函数名或 -> <命令…>
# 输出一行 PASS/FAIL，并追加到 summary.txt。
check() {
  local idx="$1" name="$2" expected="$3" extract="$4"
  shift 4
  N_STEP=$((N_STEP + 1))
  local log="$OUT_DIR/step${idx}.log"
  local t0 t1 secs code detail status

  t0=$(date +%s)
  "$@" > "$log" 2>&1
  code=$?
  t1=$(date +%s)
  secs=$((t1 - t0))

  if printf ',%s,' "$expected" | grep -q ",$code,"; then
    status="PASS"
    N_PASS=$((N_PASS + 1))
  else
    status="FAIL"
    N_FAIL=$((N_FAIL + 1))
  fi

  if [ "$extract" = "-" ]; then
    detail="exit=$code"
  else
    detail="$("$extract" "$log")"
  fi

  printf '[%s] %-38s %s  %-46s %4ss  exit=%s  期望=%s\n' \
    "$idx" "$name" "$status" "$detail" "$secs" "$code" "$expected"
  {
    printf '[%s] %s\n' "$idx" "$name"
    printf '    status  : %s\n' "$status"
    printf '    detail  : %s\n' "$detail"
    printf '    command : %s\n' "$*"
    printf '    exit    : %s（期望 %s）\n' "$code" "$expected"
    printf '    elapsed : %ss\n' "$secs"
    printf '    log     : %s\n\n' "$log"
  } >> "$SUMMARY"
}

# --- 关键数字提取器 -----------------------------------------------------------
# 统一先过一遍 ANSI 过滤：上游工具即使被重定向也可能吐颜色码
strip_ansi() { sed -e 's/\x1b\[[0-9;]*[A-Za-z]//g' -e 's/\x1b(B//g' "$1"; }

x_pytest()  {
  local line
  line=$(strip_ansi "$1" | grep -Eo '[0-9]+ passed(, [0-9]+ skipped)?[^)]*' | tail -1)
  if [ -z "$line" ]; then strip_ansi "$1" | tail -3 | tr '\n' ' '; else echo "$line"; fi
}
x_mypy()    { strip_ansi "$1" | grep -Eo 'no issues found in [0-9]+ source files' | tail -1; }
x_ruff()    { if strip_ansi "$1" | grep -q 'All checks passed\|already formatted'; then strip_ansi "$1" | grep -Eo 'All checks passed|[0-9]+ files already formatted' | tail -1; else strip_ansi "$1" | tail -2 | tr '\n' ' '; fi; }
x_head()    { strip_ansi "$1" | tail -1; }
x_manifest(){ strip_ansi "$1" | grep -Eo '[0-9]+ 行；已建 [0-9]+（[^）]*）；待建 [0-9]+' | tail -1; }
x_fe_test() {
  local tests cov
  tests=$(strip_ansi "$1" | grep -Eo 'Tests +[0-9]+ passed \([0-9]+\)' | tr -s ' ' | tail -1)
  # 列序是 `% Stmts | % Branch | % Funcs | % Lines`，**Lines 是第 5 列、Funcs 是第 4 列**。
  # 别把 Stmts 当 Lines 报出去：vitest 的 `lines` 阈值卡的是 % Lines 这一列。
  cov=$(strip_ansi "$1" | grep -E '^All files' | tail -1 | awk -F'|' \
    '{gsub(/ /,"",$2); gsub(/ /,"",$3); gsub(/ /,"",$4); gsub(/ /,"",$5); \
      print "stmts "$2"% / branch "$3"% / funcs "$4"% / lines "$5"%"}')
  echo "${tests:-Tests 未解析}；${cov:-coverage 未解析}"
}
x_goldset() { strip_ansi "$1" | tail -1; }

# --- 第 1 项：pytest 全量 ------------------------------------------------------
run_pytest_nopg() {
  (
    cd "$ROOT/apps/adapter" || exit 2
    rm -rf /tmp/oapytest_p4
    OA_BATCH=B21 "$PY" -m pytest tests -q --no-header --basetemp=/tmp/oapytest_p4
  )
}

run_pytest_pg() {
  if [ -z "${OA_TEST_DATABASE_URL:-}" ]; then
    echo "缺少 OA_TEST_DATABASE_URL：带 PG 的终验必须指向真实测试库" >&2
    echo "例：export OA_TEST_DATABASE_URL=postgresql://officeai_app:***@127.0.0.1:55433/officeai" >&2
    return 3
  fi
  (
    cd "$ROOT/apps/adapter" || exit 2
    rm -rf /tmp/oapytest_p4_pg
    OA_BATCH=B21 "$PY" -m pytest tests -q --no-header -rs --run-pg \
      --basetemp=/tmp/oapytest_p4_pg
  )
}

echo "仓库根：$ROOT"
echo "解释器：$PY"
echo "结果目录：$OUT_DIR"
echo "pytest 模式：$PYTEST_MODE"
echo "============================================================================="

case "$PYTEST_MODE" in
  nopg)
    check "1a" "pytest 全量（无 PG）" "0" x_pytest run_pytest_nopg
    ;;
  pg)
    check "1" "pytest 全量（带 PG）" "0" x_pytest run_pytest_pg
    ;;
  both)
    check "1a" "pytest 全量（无 PG）" "0" x_pytest run_pytest_nopg
    check "1b" "pytest 全量（带 PG）" "0" x_pytest run_pytest_pg
    ;;
  skip)
    echo '[1] pytest                                   SKIP  （--pytest-mode skip）'
    printf '[1] pytest\n    status  : SKIP\n\n' >> "$SUMMARY"
    ;;
esac

# --- 第 2 项：mypy -------------------------------------------------------------
run_mypy() { ( cd "$ROOT/apps/adapter" && "$MYPY" app ); }
check "2" "mypy app" "0" x_mypy run_mypy

# --- 第 3 项：ruff 六条（app / deploy / 全仓 各 check+format）------------------
run_ruff_app_c()  { ( cd "$ROOT/apps/adapter" && "$RUFF" check app ); }
run_ruff_app_f()  { ( cd "$ROOT/apps/adapter" && "$RUFF" format --check app ); }
run_ruff_dep_c()  { ( cd "$ROOT/apps/adapter" && "$RUFF" check ../../deploy --config pyproject.toml ); }
run_ruff_dep_f()  { ( cd "$ROOT/apps/adapter" && "$RUFF" format --check ../../deploy --config pyproject.toml ); }
run_ruff_all_c()  { ( cd "$ROOT/apps/adapter" && "$RUFF" check . ); }
run_ruff_all_f()  { ( cd "$ROOT/apps/adapter" && "$RUFF" format --check . ); }
check "3a" "ruff check app" "0" x_ruff run_ruff_app_c
check "3b" "ruff format --check app" "0" x_ruff run_ruff_app_f
check "3c" "ruff check deploy" "0" x_ruff run_ruff_dep_c
check "3d" "ruff format --check deploy" "0" x_ruff run_ruff_dep_f
check "3e" "ruff check .（apps/adapter）" "0" x_ruff run_ruff_all_c
check "3f" "ruff format --check .（apps/adapter）" "0" x_ruff run_ruff_all_f

# --- 第 4 项：16 个 deploy/validate_*.py 门禁本体 ------------------------------
# 两条需要 --baseline（不给就用默认基线文件，显式给是为了让"用了哪份基线"可追溯）
i=0
for f in "$ROOT"/deploy/validate_*.py; do
  i=$((i + 1))
  base="$(basename "$f")"
  case "$base" in
    validate_import_directions.py)
      run_v() { ( cd "$ROOT" && OA_BATCH=B21 "$PY" deploy/"$base" --baseline deploy/import_direction_baseline.txt ); } ;;
    validate_repository_parity.py)
      run_v() { ( cd "$ROOT" && OA_BATCH=B21 "$PY" deploy/"$base" --baseline deploy/repository_parity_baseline.txt ); } ;;
    *)
      run_v() { ( cd "$ROOT" && OA_BATCH=B21 "$PY" deploy/"$base" ); } ;;
  esac
  check "4.$i" "gate $base" "0" x_head run_v
done

# --- 第 5 项：16 个 --self-test（分族判定）------------------------------------
# 族A：成功 = exit 1；族B：成功 = exit 0。判错族 = 把通过的门禁判成失败。
FAMILY_A="validate_batch_clock.py validate_deploy.py validate_gate_wiring.py
validate_migrations.py validate_openapi.py validate_packaging.py validate_plugins.py
validate_schema_retention.py validate_secrets.py"
FAMILY_B="validate_config_names.py validate_deps.py validate_images.py validate_pg_markers.py
validate_repository_parity.py validate_import_directions.py validate_offline_pkgs.py"

i=0
for base in $FAMILY_A; do
  i=$((i + 1))
  run_s() { ( cd "$ROOT" && OA_BATCH=B21 "$PY" deploy/"$base" --self-test ); }
  check "5a.$i" "self-test $base（族A 期望 1）" "1" x_head run_s
done
i=0
for base in $FAMILY_B; do
  i=$((i + 1))
  run_s() { ( cd "$ROOT" && OA_BATCH=B21 "$PY" deploy/"$base" --self-test ); }
  check "5b.$i" "self-test $base（族B 期望 0）" "0" x_head run_s
done

# --- 第 6 项：交付清单一致性 --------------------------------------------------
run_manifest() { ( cd "$ROOT" && "$PY" scripts/gen_delivery_manifest.py --check ); }
check "6" "交付清单 --check" "0" x_manifest run_manifest

# --- 第 7 项：前端 lint / typecheck / test+coverage ---------------------------
# 先删 coverage：不放行的话 vitest 清目录会被宿主 safe-delete 钩子拦成 exit 1（不是用例失败）
run_fe_lint() {
  ( cd "$ROOT" && npm run lint:web )
}
run_fe_typecheck() {
  ( cd "$ROOT" && npm run typecheck:web )
}
# coverage 目录**输出到结果目录**（$OUT_DIR/coverage-web），不落到仓库里。
# 原因有两层：
#  1. 宿主 safe-delete 钩子会在 vitest 清理旧 `apps/web/coverage` 时拦下整个进程
#     （`count:163 > threshold:50` → exit 1），那是环境拦截，不是用例失败；
#  2. 复跑证据本来就该跟报告放一起，而不是散在仓库里被 .gitignore 掉。
# CI（无钩子）跑原始命令同样通过；这里只是把产物位置换掉，覆盖率统计口径不变。
run_fe_test() {
  ( cd "$ROOT" && npm run test --workspace apps/web -- --coverage \
      --coverage.reportsDirectory="$OUT_DIR/coverage-web" )
}
check "7a" "前端 lint" "0" - run_fe_lint
check "7b" "前端 typecheck" "0" - run_fe_typecheck
check "7c" "前端 test --coverage" "0" x_fe_test run_fe_test

# --- 第 8 项：三套 compose config ---------------------------------------------
run_compose_base() { ( cd "$ROOT/deploy" && docker compose --env-file .env.example -f docker-compose.yml config --quiet ); }
run_compose_saas() { ( cd "$ROOT/deploy" && docker compose --env-file .env.example -f docker-compose.saas.yml config --quiet ); }
run_compose_lite() { ( cd "$ROOT/deploy" && docker compose --env-file .env.lite.example -f docker-compose.lite.yml config --quiet ); }
check "8a" "compose base config --quiet" "0" - run_compose_base
check "8b" "compose saas config --quiet" "0" - run_compose_saas
check "8c" "compose lite config --quiet" "0" - run_compose_lite

# --- 第 9 项：本轮新增交付物自检 ---------------------------------------------
run_goldset_yaml() {
  "$PY" - <<'PYEOF'
import pathlib
import sys

import yaml

n = 0
for path in sorted(pathlib.Path("qa/goldset").glob("T[0-9][0-9].yaml")):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != path.stem:
        print(f"非法：{path}")
        sys.exit(1)
    n += 1
if n != 20:
    print(f"数量不对：{n} != 20")
    sys.exit(1)
print(f"{n} 个 yaml 合法且 id 与文件名一致")
PYEOF
}
run_goldset_validate() { ( cd "$ROOT" && "$PY" qa/goldset/run.py --validate ); }
# 9c/9e 用**一条 shell 命令**直接验，不经过"包装函数再读 $?"的写法：
# 包一层函数再 `code=$?` 在本仓库的 bash 上出现过分不清状态来源的情况
#（函数体没执行却返回 0），排查成本远高于收益。一条命令一个断言最省事。
# 另外用「置空」而不是 `env -u`：run.py 把空串与未设置同样当作缺凭据（`if not token`）。
BLOCKED_PROBE_OUT="/tmp/phase4-verify-blocked-probe"
# 函数体只留一条命令：函数的退出状态 = 该命令的状态，`check` 直接取，
# 不需要在函数内部再读一次 `$?`（那一步在本仓库的 bash 上出过状态丢失的怪事）。
run_goldset_blocked() {
  rm -rf "$BLOCKED_PROBE_OUT"
  ( cd "$ROOT" && OA_GOLDSET_TOKEN= OA_GOLDSET_BASE_URL= \
      "$PY" qa/goldset/run.py --case T07 --out "$BLOCKED_PROBE_OUT" )
}
run_blocked_no_files() {
  if [ -e "$BLOCKED_PROBE_OUT" ]; then
    echo "blocked 却创建了产物目录：$BLOCKED_PROBE_OUT"
    return 1
  fi
  echo "未创建任何产物目录（blocked 不留半成品）"
}
run_bench_help() {
  local s
  for s in bench_chat bench_kb bench_plugin; do
    "$PY" "scripts/bench/$s.py" --help > /dev/null 2>&1 || {
      echo "$s --help 失败"
      return 1
    }
  done
  echo "3 个 bench 脚本 --help 均 exit 0"
}

check "9a" "goldset 20 个 yaml 合法" "0" x_goldset run_goldset_yaml
check "9b" "goldset run.py --validate" "0" x_goldset run_goldset_validate
check "9c" "goldset 无凭据 exit 3" "3" x_head run_goldset_blocked
check "9d" "bench 三个脚本 --help" "0" x_goldset run_bench_help
check "9e" "goldset blocked 不落盘" "0" x_head run_blocked_no_files

# --- 汇总 ---------------------------------------------------------------------
echo "============================================================================="
printf '合计 %d 项：PASS %d / FAIL %d\n' "$N_STEP" "$N_PASS" "$N_FAIL"
echo "明细：$SUMMARY"
{
  printf '合计 %d 项：PASS %d / FAIL %d\n' "$N_STEP" "$N_PASS" "$N_FAIL"
  printf '窗口：%s  pytest 模式：%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$PYTEST_MODE"
} >> "$SUMMARY"

if [ "$N_FAIL" -gt 0 ]; then
  echo "FAIL 项："
  grep -B4 'status  : FAIL' "$SUMMARY" | grep '^\[' | head -20
  exit 1
fi
echo "9 项全 PASS（环境约束已在脚本内处理：basetemp 出仓、coverage 出仓）"
exit 0
