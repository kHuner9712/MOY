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
# ENG-04（审计 2026-10-28）：FROM 锁 digest（Hub 实查 2026-10-28；登记见
# deploy/images.lock 的 node 条目）——同 adapter，构建可复现要求。
FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS deps

WORKDIR /app

# H-3（审计 2026-10-28）→ R2-ARC-01（审计 2026-10-29 复审修复）：
#
# 锁文件是 npm **workspaces** 锁（根 `package.json` 声明 `workspaces:["apps/*"]`，
# 锁的根条目是 `officeai`），而 build context 只圈了 `apps/web`。`npm ci` 会校验
# 「锁 ↔ 根 package.json ↔ workspace 包 package.json」三者是否同处一个 workspaces
# 布局，因此**必须先在 /app 还原出仓库根布局**，否则报：
#   npm error Conflicting peer dependency: @types/node@26.6.5 ... from vitest@5.0.5
#
# 还原方式：从 `lockroot`（compose 的 additional_contexts，指向仓库根）取根
# `package.json` 与根锁，把 context 内的子包 package.json 放到 `./apps/web/`。
# 这样 npm 看到的目录结构与仓库一致，`npm ci` 正常通过。
#
# ⚠️ 三套编排（全量 / lite / saas）**都必须**声明
#    `additional_contexts: { lockroot: .. }`，漏配会在解析本行时报
#    `invalid from flag value lockroot: context "lockroot" not found`（比 npm ci 更早失败）。
COPY --from=lockroot package.json ./package.json
COPY --from=lockroot package-lock.json ./package-lock.json
COPY package.json ./apps/web/package.json

# R2-ARC-03（审计 2026-10-29）：`--omit=optional` 排除 sharp 及其拉入的
# `@img/sharp-libvips-*`（`package-lock.json` 标注 `LGPL-3.0-or-later`）。
# 全仓 `next/image` 零使用，构建与运行都不需要 sharp；不排除则 LGPL 原生库会
# 以 Node 原生模块形式**进程内**加载进生产镜像，违反本项目「LGPL 仅可独立进程」
# 的灰名单政策（`docs/architecture-v1.0.md` §8.2）。
RUN npm ci --no-audit --no-fund --omit=optional

# ---------- builder ----------
FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS builder

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
FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS runner

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
