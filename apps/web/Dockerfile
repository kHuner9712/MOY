# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# 前端交互层镜像（apps/web）
#
# 设计要点：
# - 多阶段构建：deps 装依赖、builder 构建、runner 精简运行；
# - 非 root 用户运行；
# - NEXT_PUBLIC_* 在构建期注入，运行期不再暴露底座信息；
# - 采用 standalone 输出，镜像体积最小。
# ---------------------------------------------------------------------------

# ---------- deps ----------
FROM node:22-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json* ./
# 存在锁文件时使用 npm ci 保证可复现；否则回退到 npm install
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ---------- builder ----------
FROM node:22-alpine AS builder

WORKDIR /app

# NEXT_PUBLIC_* 是**构建期内联**常量（Next 在打包时把值写死进 bundle），
# 因此必须在构建期传进来，运行期改环境变量无效。
#
# ⚠️ 必须先声明 ARG 才能接收 `docker compose` 的 `build.args`：未声明的 ARG
# 会被 Docker **静默忽略**（只在构建日志里留一行 warning），最终镜像静默用了
# 下面的默认值——「配了却不生效」的典型形态，排查时会一直怀疑配置写错。
# 统一登录这两项是公开接入信息（不含任何密钥），留空时登录页只显示
# 「统一登录尚未配置，请联系管理员」，登录入口不可达，所以部署必须显式传值。
ARG NEXT_PUBLIC_ADAPTER_BASE_URL=http://adapter:8000
ARG NEXT_PUBLIC_OIDC_ISSUER=
ARG NEXT_PUBLIC_OIDC_CLIENT_ID=

ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_ADAPTER_BASE_URL=$NEXT_PUBLIC_ADAPTER_BASE_URL \
    NEXT_PUBLIC_OIDC_ISSUER=$NEXT_PUBLIC_OIDC_ISSUER \
    NEXT_PUBLIC_OIDC_CLIENT_ID=$NEXT_PUBLIC_OIDC_CLIENT_ID

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# ---------- runner ----------
FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# standalone 输出已包含精简后的 node_modules 与 server.js
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/ >/dev/null 2>&1 || exit 1

CMD ["node", "server.js"]
