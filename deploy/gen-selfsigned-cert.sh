#!/usr/bin/env sh
# ===========================================================================
# 生成边缘 TLS 自签证书（**仅限本地验证**，生产必须换真实证书）
#
# 背景（G1，审计 2026-11-02 P0-4）：edge/proxy 已升级为 TLS 终止（80 只做
# 探活与 301 跳转，443 承载全部路由），证书按约定**外部挂载**、不入镜像不入库。
# 本地起栈前先跑本脚本生成一对自签证书，路径与 lite-tls.conf 口径一致：
#
#   deploy/nginx/tls/fullchain.pem   （证书，自签=它同时是链）
#   deploy/nginx/tls/privkey.pem     （私钥，chmod 600）
#
# 用法（仓库根或 deploy/ 下执行均可）：
#   sh deploy/gen-selfsigned-cert.sh [CN]        # CN 默认 localhost
#
# ⚠ 自签证书浏览器会告警（信任它=仅限验证），生产交付用私有 CA / ACME 证书
#   覆盖同名两个文件即可（部署指南 §12.5.2）。私钥权限必须 600。
# ===========================================================================
set -eu

CN="${1:-localhost}"
DIR="$(dirname "$0")/nginx/tls"

if ! command -v openssl >/dev/null 2>&1; then
    echo "[FAIL] 需要 openssl（生产镜像/现场机均自带；本机请先安装）" >&2
    exit 1
fi

mkdir -p "$DIR"

# 一次性生成（已存在则拒绝覆盖：防止把生产真证书顶掉）
if [ -f "$DIR/fullchain.pem" ] || [ -f "$DIR/privkey.pem" ]; then
    echo "[FAIL] $DIR 下已存在证书/私钥，拒绝覆盖（生产证书顶掉就麻烦了）" >&2
    echo "       确认要重建请先手工删除这两个文件" >&2
    exit 1
fi

openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
    -keyout "$DIR/privkey.pem" \
    -out "$DIR/fullchain.pem" \
    -subj "/CN=$CN" \
    -addext "subjectAltName=DNS:$CN,DNS:localhost,IP:127.0.0.1" \
    >/dev/null 2>&1

chmod 600 "$DIR/privkey.pem"
chmod 644 "$DIR/fullchain.pem"

echo "[OK] 自签证书已生成（仅限验证，30 天有效）："
echo "     $DIR/fullchain.pem"
echo "     $DIR/privkey.pem（权限 600）"
echo "     CN=$CN；生产请以真实证书覆盖同名文件"
