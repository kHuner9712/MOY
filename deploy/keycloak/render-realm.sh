#!/bin/sh
# realm 模板渲染（`realm-officeai.json` → `rendered/realm-officeai.json`）。
#
# 为什么要有这一步，而不是把域名直接写进模板：
#   模板要入库，而 `redirectUris` / `rootUrl` / `webOrigins` 必须**逐字**等于现场
#   实际访问的域名；`sslRequired` 又要按「有没有 TLS」三态取 different 值。
#   把这三类值写死进版本库，等于让"换个域名"变成一次改仓库。
#
# 为什么不用 Keycloak 的 `--import-realm`：它的语义是"服务每次启动都尝试导入"，
#   而我们要的是"安装期一次性动作"。已存在 realm 时 `--import-realm` 的行为依赖
#   导入策略默认值——**客户已有 realm 被覆盖是不可挽回的事故**，不能把安全性押在
#   工具默认值上。真正的"不覆盖"保证在 `keycloak-import` 服务的 `kcadm get` 探测里，
#   本脚本只负责把模板变成可提交给 API 的 JSON。
#
# 用法：
#   sh deploy/keycloak/render-realm.sh                      # 从环境变量/默认值渲染
#   OA_PUBLIC_BASE_URL=https://moy.example.com \
#   KC_SSL_REQUIRED=external sh deploy/keycloak/render-realm.sh
#   sh deploy/keycloak/render-realm.sh --check              # 只校验模板占位是否都被覆盖
#
# 退出码：0 = 渲染成功；1 = 占位未替换/模板缺失/产物仍含 `${`。

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TEMPLATE="${SCRIPT_DIR}/realm-officeai.json"
RENDERED_DIR="${SCRIPT_DIR}/rendered"
RENDERED="${RENDERED_DIR}/realm-officeai.json"

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
  CHECK_ONLY=1
fi

# 产物目录必须先建：临时文件就写在里面（否则 `> file.tmp` 直接 No such file）
mkdir -p "${RENDERED_DIR}"

# 对外基址：缺省与 `.env.example` 的 `OA_PUBLIC_BASE_URL` 同口径（本地起得来）。
# **生产必须显式设置**：`validate_auth_startup` 在生产/预发环境会拒绝空值（30004）。
PUBLIC_BASE_URL="${OA_PUBLIC_BASE_URL:-http://localhost}"
# 去掉尾斜杠：否则 `redirectUris` 会变成 `https://host//auth/callback`，
# Keycloak 精确匹配失败 → 登录报 `Invalid parameter: redirect_uri`（现场极难定位）。
PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"

# sslRequired 三态（与 design §3.4.1 一致）：有 TLS → external；纯 HTTP → none + 告警。
if [ -n "${KC_SSL_REQUIRED:-}" ]; then
  SSL_REQUIRED="${KC_SSL_REQUIRED}"
elif [ "${OA_EDGE_TLS:-off}" = "on" ]; then
  SSL_REQUIRED="external"
else
  SSL_REQUIRED="none"
fi

if [ ! -f "${TEMPLATE}" ]; then
  echo "[FAIL] 缺少 realm 模板：${TEMPLATE}" >&2
  exit 1
fi

require_non_empty() {
  name="$1"
  value="$2"
  if [ -z "${value}" ]; then
    echo "[FAIL] ${name} 为空：无法渲染 realm（对外基址决定 redirectUris，不能留空）" >&2
    exit 1
  fi
}

require_non_empty OA_PUBLIC_BASE_URL "${PUBLIC_BASE_URL}"
case "${SSL_REQUIRED}" in
  external|none|all) ;;
  *)
    echo "[FAIL] KC_SSL_REQUIRED 取值非法：${SSL_REQUIRED}（只允许 external / none / all）" >&2
    exit 1
    ;;
esac
if [ "${SSL_REQUIRED}" = "none" ]; then
  # 与 Cookie `Secure` 降级同一处置口径：允许降级，但必须显式、必须告警。
  echo "[WARN] 纯 HTTP 现场：realm sslRequired=none（登录页不加密，仅限内网）" >&2
fi

# sed 用 `|` 作分隔符：值里含 `http://` 与 `/`，用 `/` 会破坏替换。
sed \
  -e "s|\\\${OA_PUBLIC_BASE_URL}|${PUBLIC_BASE_URL}|g" \
  -e "s|\\\${KC_SSL_REQUIRED}|${SSL_REQUIRED}|g" \
  "${TEMPLATE}" > "${RENDERED}.tmp"

# 自证：渲染后**不允许**残留任何 `${`。残留说明模板新增了没人负责的占位，
# 那会把字面量 `${OA_XXX}` 当域名提交给 Keycloak（登录必然失败，且报错看不懂）。
if grep -q '\${' "${RENDERED}.tmp"; then
  echo "[FAIL] 渲染后仍有未替换的占位：" >&2
  grep -n '\${' "${RENDERED}.tmp" | sed 's/^/       /' >&2
  rm -f "${RENDERED}.tmp"
  exit 1
fi

if [ "${CHECK_ONLY}" -eq 1 ]; then
  rm -f "${RENDERED}.tmp"
  echo "[OK]   realm 模板占位可全部渲染（public_base_url=${PUBLIC_BASE_URL} ssl=${SSL_REQUIRED}）"
  exit 0
fi

mkdir -p "${RENDERED_DIR}"
mv "${RENDERED}.tmp" "${RENDERED}"
echo "[OK]   realm 已渲染：${RENDERED}（public_base_url=${PUBLIC_BASE_URL} ssl=${SSL_REQUIRED}）"
