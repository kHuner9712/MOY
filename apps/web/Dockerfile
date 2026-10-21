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

ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_ADAPTER_BASE_URL=http://adapter:8000

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
