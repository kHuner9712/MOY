#!/bin/sh
# ===========================================================================
# Alertmanager 启动脚本（H-01，2026-11-07）
#
# 职责只有一件：把 `OA_ALERT_WEBHOOK_URL` 渲染进配置。Alertmanager 本身不展开
# 环境变量，直接写 `${...}` 会被当成字面量 URL，因此必须在这里替换后再加载。
#
# 两种形态：
#   - 设置了 OA_ALERT_WEBHOOK_URL：critical 路由发往该 webhook（含恢复通知）；
#   - 未设置：critical 路由改回 officeai-null —— 告警仍进 UI/API，不会因缺 URL 起不来。
#
# 只替换**占位 URL**，不替换别处：避免 webhook 值里恰好包含 `receiver:` 之类片段。
# ===========================================================================
set -eu

SRC="${ALERTMANAGER_SRC:-/etc/alertmanager/alertmanager.yml}"
OUT="${ALERTMANAGER_OUT:-/tmp/alertmanager.yml}"
PLACEHOLDER='http://127.0.0.1:9/alertmanager-webhook-not-configured'
WEBHOOK="${OA_ALERT_WEBHOOK_URL:-}"

if [ -n "${WEBHOOK}" ]; then
    # sed 替换串里 `&` 表示"整个匹配"，`|` 是分隔符：两者都必须转义，
    # 否则 URL 里的查询参数（?a=1&b=2）会把配置写坏。
    escaped=$(printf '%s' "${WEBHOOK}" | sed -e 's/[&|\\]/\\&/g')
    sed "s|${PLACEHOLDER}|${escaped}|g" "${SRC}" > "${OUT}"
    echo "[alertmanager] webhook 已启用（critical 路由会外发通知）"
else
    # 未配置：critical 与默认路由都落到 null receiver（占位 URL 永远不会被请求）。
    sed 's|receiver: officeai-webhook|receiver: officeai-null|g' "${SRC}" > "${OUT}"
    echo "[alertmanager] 未设置 OA_ALERT_WEBHOOK_URL：告警仅进入 UI/API，不对外发送"
fi

# CI 用：只渲染不启动（`promtool` 无法解析 Alertmanager 配置，必须用 amtool）。
if [ "${1:-}" = "--render-only" ]; then
    echo "[alertmanager] 已渲染到 ${OUT}（--render-only，不启动）"
    exit 0
fi

exec /bin/alertmanager \
    --config.file="${OUT}" \
    --storage.path=/alertmanager \
    --web.listen-address=:9093 \
    "$@"
