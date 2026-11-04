# 工程简报（2026-11-03）

> 目的：给后续开发者一份**可逐条抽查**的仓库认知基线。
> 取证方式：只读代码/配置 + `git` 实测；每条结论附 `路径:行号`。未在代码中确认的设计意图，
> 统一放在第 9 节「空白点」，不在此处臆测。
> 基线：`main` HEAD `80f3e08`（2026-11-03），远端仅 `refs/heads/main`，工作区干净。

---

## 1. 系统全景

### 1.1 产品定位

- Moy（麦子）是**面向广泛工作需求的模块化 AI 工作平台**，V1.0 首批落地企业办公场景 —— `README.md:3-6`。
- 架构为五层解耦：「底座层 + 自研统一适配层 + 业务层 + 插件层 + 前端层」—— `README.md:12-13`。
  底座为 Dify（Apache-2.0），适配层负责屏蔽底座差异，目标「替换底座仅改适配层，上层零改动」—— `README.md:12-13`。
- 适配层是**全仓唯一允许接触 Dify 与 Keycloak 的应用** —— `apps/adapter/README.md:3-5`。
- 端点规模：README 声称 96 个端点（core 30 + phase2 38 + phase3 16 + phase4 10 + W1 2）—— `README.md:16`。
  该数字属文档口径，未逐个核对路由表。

### 1.2 用户与角色模型

- 组织内角色枚举为 `owner / org_admin / member / guest`（业务化命名，非 Keycloak 原始角色名）——
  `apps/adapter/app/schemas/identity.py:19-25`。
- 平台运营侧不是一个独立角色枚举，而是**权限点** `ops:console:access`；运营接口统一走
  `require_ops_access(...)`，非运营账号返回 20003 —— `apps/adapter/app/api/v1/ops.py:95,122`。
- 角色/权限数据落表：`role`（唯一键 `uq_role_key`）`deploy/init-schema-phase2.sql:535`、
  `permission_catalog` `deploy/init-schema-phase2.sql:512`，以及 `member_role` / `role_permission`
  仓储 `apps/adapter/app/db/repositories/role/postgres.py:48-66`。
- 组织成员另有一套：`department` / `department_member`（唯一键 `uq_dm_primary`）
  `deploy/init-schema-phase2.sql:464-502`，仓储 `apps/adapter/app/db/repositories/org/postgres.py:43-49`。

---

## 2. 仓库结构与构建编排

### 2.1 顶层目录

| 目录 | 职责 | 证据 |
|---|---|---|
| `apps/adapter` | Python FastAPI 统一适配层（后端唯一应用） | `README.md:43-62` |
| `apps/web` | Next.js 16 App Router 前端交互层 | `README.md:63-69` |
| `deploy/` | 三套 compose、初始化 SQL、迁移、16 个门禁脚本 | `README.md:70-76` |
| `docs/` | 总纲、PRD、阶段增量、审计与整改文档 | `README.md:77-80` |
| `.github/workflows/` | CI Adapter / CI Web / Dependency Scan / SBOM | 第 7 节 |

- 单仓双应用 + npm workspace：根 `package.json` 的 `workspaces: ["apps/*"]` —— `package.json:10-12`。
- 根脚本只做转发：`dev:web` / `build:web` / `lint:web` / `typecheck:web` —— `package.json:17-22`。
- Python 侧不是 workspace，而是单项目 `apps/adapter/pyproject.toml`，锁文件 `uv.lock`；
  生产镜像用 `uv export --frozen --no-dev --no-emit-project` 生成确定性依赖再 `pip install` ——
  `apps/adapter/Dockerfile:30-55`。

### 2.2 语言/运行时版本

- Node `>=20.11.0` —— `package.json:7-9`、`apps/web/package.json:68-70`。
- Python `>=3.11` —— `apps/adapter/pyproject.toml:14`；基础镜像锁定 `python:3.13-slim` digest ——
  `deploy/images.lock:23-40`。

---

## 3. 技术栈与关键依赖

### 3.1 前端

- Next.js `^16.4.3`、React `^19.3.1`、TypeScript `^5.5.0` —— `apps/web/package.json:43-45,65`。
- UI：Tailwind `^3.4.0` + Radix（`@radix-ui/react-dialog` 等）—— `apps/web/package.json:36-49,64`。
- 状态：`@tanstack/react-query ^5.105.0`、`zustand ^4.5.0` —— `apps/web/package.json:38,49`。
- 测试：`vitest ^5.0.5` + `@vitest/coverage-v8 5.0.5`、Playwright `^1.64.1` ——
  `apps/web/package.json:52,56-57,66`。

### 3.2 后端

- FastAPI `>=0.156,<0.157`、Starlette `>=1.3.1,<2.0`（显式下界防 PYSEC-2026-1941）——
  `apps/adapter/pyproject.toml:29-30`。
- Uvicorn `==0.54.0`、Pydantic `>=2.7,<3.0`、SQLAlchemy `[asyncio]>=2.0,<3.0`、asyncpg `==0.32.0`——
  `apps/adapter/pyproject.toml:33-41`。
- Redis 客户端 `==8.1.0`（对接 Valkey）—— `apps/adapter/pyproject.toml:37`；缓存服务在 compose 中是
  `valkey` —— `deploy/docker-compose.yml:92`。
- JWT 用 PyJWT（已从 python-jose 迁移，原因含 CVE-2026-85394）—— `apps/adapter/pyproject.toml:42-48`。
  加密 `cryptography==50.0.2` —— `pyproject.toml:52`；指标 `prometheus-client` —— `pyproject.toml:58`；
  公文导出 `python-docx` —— `pyproject.toml:59-63`。
- 启动入口 `officeai-adapter = app.main:main` —— `pyproject.toml:75-76`。

### 3.3 数据库 / 迁移 / 存储

- PostgreSQL 为主库（compose `postgres` `deploy/docker-compose.yml:37`），**多租户用 PG RLS**（见 5.2）。
- **没有 Alembic**：`apps/adapter` 下无 `alembic.ini`/`alembic/`；迁移是版本化 SQL
  `deploy/migrations/0002…0012` + `apps/adapter/app/db/migrate.py`。最新可见迁移
  `deploy/migrations/0012_quota_hold.sql`。README 自述「0002-0012 迁移（11 个，0001 为 RESERVED 基线别名）」
  —— `README.md:75`。
- 对象存储：SeaweedFS（compose `object-store`）—— `deploy/docker-compose.yml:214`；
  另有 Milvus 专用对象存储 `milvus-object-store` —— `deploy/docker-compose.yml:128`。
- 向量库：Milvus —— `deploy/docker-compose.yml:176`；容器端口 `19530` 绑 `127.0.0.1`——
  `deploy/docker-compose.yml:187-193`。

### 3.4 认证

- Keycloak 为身份底座（compose `keycloak` / `keycloak-import`）——
  `deploy/docker-compose.yml:482,551`。
- 适配层 SSO 流程：`POST /sso/start` 生成 PKCE → 下发 `oa_oidc_pending` → 回授权地址；
  `POST /sso/complete` `{code,state}` → 换令牌 → 下发两个会话 Cookie ——
  `apps/adapter/app/api/sso_routes.py:35-36`，实现 `start_login` `:673-733`，回调校验
  `states_match` `:765-771`。
- Cookie：`oa_session` 存访问令牌、`oa_refresh` 存刷新令牌（`Path` 收窄到 `/sso`）——
  `apps/adapter/app/core/session_cookie.py:17-25`，`Set-Cookie` 构造 `:194-216`。

---

## 4. 核心链路（入口 → 中间层 → 数据落点）

### 4.1 登录 / 会话刷新

- 入口：前端登录页 `apps/web/src/app/(auth)/login/page.tsx`；回调页
  `apps/web/src/app/(auth)/auth/callback/page.tsx`。
- 中间层（后端）：`apps/adapter/app/api/sso_routes.py:673-733`（start）、`:733-787`（complete，
  含 state 比对与统一 20002 错误）、`:789-876`（session 状态）、`:877-…`（refresh）。
- 刷新侧前端：`apps/web/src/lib/auth-refresh.ts:94-134`（会话决策/何时刷新）、`:332-437`
  （带重试的刷新 + 跨标签单飞，服务端还有幂等窗口兜底）；服务端幂等状态 `sso_routes.py:569-638`。
- 数据落点：会话令牌以 HttpOnly Cookie 下发（`session_cookie.py:194-216`）；令牌本身不落业务库；
  用户/组织权威数据在 Keycloak（`apps/adapter/app/schemas/identity.py:3-5`）。

### 4.2 任务创建 / 推进 / SSE

- 入口：`POST /tasks` `apps/adapter/app/api/v1/agent.py:120-183`；
  `GET /tasks/{task_id}/events`（SSE）`apps/adapter/app/api/v1/agent.py:344-375`。
- 中间层（Dify 通道）：`apps/adapter/app/services/agent_service.py`，`stream_events` `:688-707`；
  转换职责（状态映射、节点事件映射）在文件头 `:3-12`。
- 中间层（插件任务通道）：`apps/adapter/app/business/task_service.py:1-32` 是公开入口+单例；
  唯一写路径 `TaskRunner`，职责拆分表同处 `:5-11`；**V1.0 单副本，推进在请求内同步执行** `:23-32`。
- 队列：DB 轮询 `JobRunner`（默认 1s / 单轮 5 条）`apps/adapter/app/workers/job_runner.py:44,53,99`；
  仓储侧 `next_queued()` / `list_queued()` `apps/adapter/app/db/repositories/task/postgres.py:411-472`。
- 数据落点：`task` 表写入时回填 `queue_position` `.../task/postgres.py:106-146`；产物 `plugin_artifact`
  （`deploy/init-schema-phase2.sql` 插件域建表 + `apps/adapter/app/db/repositories/artifact_repo.py`）。

### 4.3 插件安装与调用

- 入口：市场/详情/已安装/能力/安装 `apps/adapter/app/api/v1/plugins.py:109-320`
  （`POST /plugins/{plugin_id}/install` `:263-320`）。
- 中间层：`apps/adapter/app/business/plugin_service.py:12-16`（「安装即启用」`not_installed → enabled`，
  授权与安装解耦、授权写 `plugin_grant`）；市场装配 `:214-247`。
- 调用/执行：插件引擎目录 `apps/adapter/app/plugins/{loader,engine,actions,renderer,sanitizer,schema}.py`
  （目录实证）；声明清单 `app/plugins/manifests/`。
- 数据落点：`plugin_catalog` / `plugin_installation` / `plugin_grant` / `plugin_task` / `plugin_artifact`
  （`deploy/init-schema-phase2.sql` 插件域；保留策略 `validate_schema_retention.py` 校验 `plugin_task.expires_at`
  默认 180 天）。

### 4.4 用量计量与计费

- 入口：用量事件写入 `UsageService.record_event()` `apps/adapter/app/business/usage_service.py:310-343`；
  各业务场景封装（chat / doc_qa / task）`:407-489`。
- 计量维度：`usage_event(tenant_id, event_id, capability, units, period, source, idem_key)` ——
  `deploy/init-schema-phase2.sql:287-305`；`capability ∈ chat/doc_qa/doc_process/automation` `:294-295`。
- 计费数据：`billing_plan` / `billing_subscription` / `billing_order` / `billing_invoice` /
  `payment_callback_log` —— `deploy/init-schema-phase2.sql:337-458`；API `apps/adapter/app/api/v1/billing.py:184-552`。
- 支付网关：`PaymentGateway` Protocol + `FakeGateway`（测试/联调，永不发真实请求）；真实环境缺密钥
  **不静默降级** —— `apps/adapter/app/business/payment_gateway.py:8-9,153-…`。

### 4.5 租户与成员管理

- 平台侧入口：`GET /ops/tenants` `apps/adapter/app/api/v1/ops.py:88-126`（`require_ops_access`）。
  分页已下推存储层：`OpsService.list_tenants` `apps/adapter/app/business/ops_service.py:216-229`，
  仓储 `BillingStore/OpsStore.page_tenants`（`LIMIT/OFFSET + COUNT`）。
- 租户内成员：组织/角色仓储 `app/db/repositories/org/postgres.py`、`.../role/postgres.py`。
- 权限判定：`require_permission()` `apps/adapter/app/core/security.py:541-586`；
  租户归属校验 `assert_tenant_membership()` `apps/adapter/app/core/security.py:780-803`
  （同租户直接放行、平台令牌可跨租户）。
- 数据落点：`tenant` `deploy/init-schema-phase2.sql:52-63`；成员/角色表见 1.2。

### 4.6 多租户事务与批处理

- 业务层唯一事务入口 `tenant_scope(...)` `apps/adapter/app/db/session.py:309-353`；
  平台作用域 `platform_scope(...)` `:357-394`。
- 每个事务首条语句设置会话变量（`set_config('app.tenant_id', …, TRUE)`，事务级，避免连接池复用串租户）——
  `apps/adapter/app/db/session.py:5-13`。
- 批处理观测：`task_queue_depth` 统计 queued + running —— `apps/adapter/app/workers/queue_depth.py:33-38`。

---

## 5. 数据模型与多租户隔离

### 5.1 主要表（SQL 为权威源）

数据访问是**原生 SQL + 仓储层**（`app/db/repositories/**`），`app/db/` 下无 ORM 表模型目录；
表结构以 `deploy/init-schema-phase{2,3,4}.sql` 与 `deploy/migrations/*.sql` 为准。

| 域 | 表 | 证据 |
|---|---|---|
| 租户 | `tenant`（PK `tenant_id`；status active/suspended；deploy_mode saas/private；plan_code） | `deploy/init-schema-phase2.sql:52-63` |
| 知识库 | `knowledge_base_meta`、`knowledge_base_grant`、`document_meta`、`segment_map` | `:68,103,132,177` |
| 工具 | `credential_store`、`tool_connection`、`tool_execution` | `:215,235,262` |
| 用量 | `usage_event`、`usage_monthly` | `:287,317` |
| 计费 | `billing_plan`、`billing_subscription`、`billing_order`、`billing_invoice`、`payment_callback_log` | `:337,360,386,425,443` |
| 组织 | `department`、`department_member`、`role`、`permission_catalog` | `:464,490,512…` |
| 审计 | `audit_log` | `:580` 附近 |

插件/任务域与对话/记忆域分别见 `deploy/init-schema-phase3.sql`、`deploy/init-schema-phase4.sql`
（`README.md:74-76` 说明阶段三为 9 张插件/任务表）。

### 5.2 隔离方式（两层）

1. **应用层**：`tenant_scope` 事务内设置 `app.tenant_id`（事务级）——
   `apps/adapter/app/db/session.py:5-13,42,309-353`。
2. **数据库层 RLS**：对业务表 `ENABLE/FORCE ROW LEVEL SECURITY`，策略
   `USING (tenant_id = current_setting('app.tenant_id', TRUE) OR current_setting(...) = '__platform__')`
   —— `deploy/init-schema-phase2.sql:642-653`；共享目录表走另一条 `p_catalog_shared` 策略 `:656-663`。
3. 连接账号要求 `NOSUPERUSER NOBYPASSRLS`（就绪探针注释明确若带 BYPASSRLS 则 RLS 形同虚设）——
   `deploy/docker-compose.yml:634-641`。

### 5.3 关键索引/约束（抽样）

- `usage_event`：PK `(tenant_id, event_id)` `:304`；幂等唯一索引 `uq_usage_event_idem(tenant_id, capability, idem_key)` `:308`；
  查询索引 `idx_usage_event_period_user` / `idx_usage_event_period_dept` `:311-315`。
- `tenant` PK `tenant_id` `:53`；`role` `uq_role_key` `:535`；`department` `uq_dept_name` `:483`；
  `department_member` `uq_dm_primary` `:502`。
- `knowledge_base_meta` `uq_kb_meta_name` `:88`；`knowledge_base_grant` `uq_grant_active` `:121`；
  `segment_map` `uq_seg_slot` `:201`；`tool_connection` `uq_conn_active` `:257`。
- 计费：`uq_subscription_active` `:382`、`uq_order_pending` `:415`、`uq_invoice_order` `:440`、
  `uq_callback_external` `:458`。
- 审计：`idx_audit_log_tenant_time` `:580`。

---

## 6. 部署形态

### 6.1 compose（V1.0 正式形态）

`deploy/docker-compose.yml` 共 **21 个服务**（另有 8 个数据卷），服务定义整体位于 `:33-810`：
postgres、valkey、milvus-object-store、milvus-etcd、milvus、object-store、dify-api、dify-worker、
dify-worker-beat、dify-web、dify-sandbox、ssrf_proxy、plugin_daemon、keycloak、keycloak-import、
edge、adapter、web、postgres-exporter、valkey-exporter、prometheus。

- 仅 `edge` 对外发布：`80` / `443`（可用 `OA_EDGE_HTTP_PORT`/`OA_EDGE_HTTPS_PORT` 覆盖）——
  `deploy/docker-compose.yml:608-610`。
- `adapter` 绑回环：`127.0.0.1:${OA_ADAPTER_PORT:-8000}` —— `deploy/docker-compose.yml:697`。
- `web` 绑回环：`127.0.0.1:${OA_WEB_PORT:-3000}` —— `deploy/docker-compose.yml:750`。
- 基础设施端口（Milvus 19530/metrics、对象存储 8333/23646、Prometheus 9090）均绑 `127.0.0.1`——
  `deploy/docker-compose.yml:187-193,224-227,810-812`。
- 三套编排定位：私有化（`docker-compose.yml:2-23`）、SaaS 联调（`docker-compose.saas.yml:2-10`）、
  轻量版 8GB/4 核常驻 9 容器（`docker-compose.lite.yml:2-44`）。

### 6.2 边缘 TLS 与安全头

- 80 端口仅健康检查 + `301` 跳转，`server_tokens off` —— `deploy/nginx/edge.conf:38-42`。
- 443 终止 TLS，证书外部挂载 `/etc/nginx/tls/{fullchain,privkey}.pem` —— `edge.conf:60-69`；
  HSTS `max-age=15552000`、`X-Content-Type-Options`、`X-Frame-Options DENY`、`Referrer-Policy`
  —— `edge.conf:83-86`（80 侧亦有 nosniff `:47`）。

### 6.3 k8s 现状

- `deploy/k8s/` 是 **V1.1 准备物**，清单已落仓库但未在真实集群端到端验收；**V1.0 不交付 k8s**——
  `deploy/k8s/README.md:3-8,15-20`。
- ConfigMap 非敏感键 68 个（整改后口径），端口类键如 `OA_ADAPTER_PORT`、`OA_MILVUS_PORT` ——
  `deploy/k8s/base/configmap.yaml:37-66`；Ingress 路径 `/api/`、`/sso/`、`/files/artifacts/` → adapter:8000，
  根路径 → web:3000 —— `deploy/k8s/base/ingress.yaml:63-111`。

### 6.4 环境变量与 `NEXT_PUBLIC_*` 边界

- 运行期配置来自 `deploy/.env`：compose `adapter` 用 `env_file: .env` —— `docker-compose.yml:646-647`。
- 关键变量：`OA_DEPLOY_MODE` `deploy/.env.example:49`、`OA_PUBLIC_BASE_URL` `:85`、
  `OA_AUTH_COOKIE_ENABLED` `:102`、`OA_POSTGRES_*` `:119-121`、`OA_VALKEY_PASSWORD` `:132`、
  `OA_OBJECT_STORE_ACCESS_KEY/SECRET_KEY` `:147-150`。
- **`NEXT_PUBLIC_*` 是构建期**：compose `web.build.args` 注入
  `NEXT_PUBLIC_ADAPTER_BASE_URL` / `NEXT_PUBLIC_OIDC_ISSUER` / `NEXT_PUBLIC_OIDC_CLIENT_ID` ——
  `docker-compose.yml:738-744`；Dockerfile 用 `ARG` 固化进 bundle —— `apps/web/Dockerfile:50-65`。
  注释明确：改后必须 `docker compose build web`，`restart` 不生效 `:739-742`。
- 边缘化后浏览器走同源相对路径 `/api/v1/*`，`NEXT_PUBLIC_ADAPTER_BASE_URL` 置空 —— `:735-738`。

---

## 7. 质量门禁

### 7.1 16 道 deploy 校验（`deploy/validate_*.py`）

| 脚本 | 管什么 | 证据 |
|---|---|---|
| `validate_config_names.py` | 编排注入的 `OA_*`/`NEXT_PUBLIC_*` 必须在 Settings/Dockerfile 有声明 | `:2-43` |
| `validate_deploy.py` | 部署形态一致性（adapter DB URL、environment 显式、initdb 挂载） | `:2-28` |
| `validate_import_directions.py` | 分层依赖方向（自写 AST，禁跨层 import） | `:3-31` |
| `validate_secrets.py` | 密钥兜底扫描（yml/.env/shell 不得明文直赋或缺失可生成路径） | `:2-52` |
| `validate_deps.py` | 后端依赖与架构文档一致、最低安全版本、前端依赖一致性 | `:2-24` |
| `validate_batch_clock.py` | `OA_BATCH` 格式/不落后台账/文档口径一致 | `:2-34` |
| `validate_repository_parity.py` | 内存/PG 双实现协议、方法、签名、record 形状一致 | `:3-28` |
| `validate_openapi.py` | 端点基线（`--self-test` 可红证明） | `:2-59` |
| `validate_pg_markers.py` | 带 PG 夹具的用例必须有 `pytest.mark.pg` | `:3-32` |
| `validate_offline_pkgs.py` | `.difypkg` 许可证/漏洞/结构完整性 | `:2-34` |
| `validate_images.py` | `image:` 与 `images.lock` 一致、禁浮动标签、登记 digest | `:2-23` |
| `validate_gate_wiring.py` | 遍历 `validate_*.py` 检查是否接入 workflow（防「判据存在但没跑」） | `:2-41` |
| `validate_migrations.py` | 迁移静态自检 + 空库/已初始化/回滚双路径实跑 | `:2-34` |
| `validate_packaging.py` | 源码包/资源文件/package-data 声明一致（防容器 ImportError） | `:2-33` |
| `validate_plugins.py` | 插件声明 6 段 schema、能力白名单、禁词、重复 id、digest | `:2-26` |
| `validate_schema_retention.py` | `plugin_task.expires_at` 存在且默认 180 天 | `:2-52` |

「门禁自检（期望退出码 1）」的含义：注入负样本，**只有该红的场景确实红**才算自检通过 ——
`validate_plugins.py:14-17`、`validate_openapi.py:45-50`；CI 步骤名亦写明「期望退出码 1」
—— `.github/workflows/ci-adapter.yml:205-221`。

### 7.2 覆盖率阈值（当前棘轮）

- 后端总覆盖 `--cov-fail-under=85` —— `.github/workflows/ci-adapter.yml:531-557`。
- PG 仓储覆盖下限 `--fail-under=88` —— `ci-adapter.yml:596-601`。
- 前端四指标：statements `88.8` / branches `84.0` / functions `83.9` / lines `90.0` ——
  `apps/web/vitest.config.mts:306-309`（`all: true` 全量源码分母 `:32-40`）。
- 实测值（2026-11-03 本机）：前端 89.78 / 85.06 / 84.93 / 90.98；后端裸跑 2535 passed / 238 skipped / 0 failed。
- README 的用例数字已于 2026-11-04 按实测更新（收集 2933 / PG 258），**唯一权威来源以 CI `junit.xml` 工件为准**。

### 7.3 CI 三个必需 workflow

| workflow | 触发 | 主要 job | 证据 |
|---|---|---|---|
| CI Adapter | push/PR（`apps/adapter/**`、`deploy/**`、`docs/api/**`、`scripts/**`）+ `workflow_dispatch` | `quality-gate`、`registry-validation`、`openapi-export` | `ci-adapter.yml:10-32,44-45,619,802`；`OA_BATCH=B21` `:93` |
| CI Web | push/PR（`apps/web/**`、PRD、插件 actions/manifests、自身）+ `workflow_dispatch` | `quality-gate`、`terminology-gate`、`built-artifact-gate`、`format-check`、`e2e-gate` | `ci-web.yml:10-31,47-48,103,267,309,346` |
| Dependency Scan | PR（`apps/**`、`deploy/**`、`package.json`）、push main/develop、月度 schedule、手动 | `base-layer-isolation`、`image-tag-existence`、`license-scan`、`dependency-manifest` | `dependency-scan.yml:14-27,36-40,132,190,501`；`npm audit --audit-level=high` `:252` |
| SBOM | 手动、PR（deploy/锁文件）、push main、周计划 | — | `sbom.yml:29-48` |

> 注意：分支保护目前**不可用**（私有 Free 仓库，`GET protection` 与 `/rulesets` 均 403
> "Upgrade to GitHub Pro…"），因此「必需检查」尚无平台级强制，证据见
> `docs/remediation-2026-11-03.md` 的「QA-12 403 成因取证」。

---

## 8. 当前状态与未决事项

### 8.1 仓库状态

- `main` HEAD `80f3e08`；远端 `git ls-remote --heads origin` 仅 `refs/heads/main`；工作区干净；
  3 条 stash 保留未动（`stash@{0}` 带 SyntaxError 警告，勿 apply）。
- 2026-11-03 整改：批次 1/2/3 均已提交并推送；代码基线 `0975a9d` 上 CI Adapter
  `41617426516` 与 CI Web `41618757526` 同 SHA 双绿；最终 HEAD `17f2a6d` 手动触发复验
  `41619372357` / `41619294847` 亦双绿（后续仅文档提交）。
- 整改在代码中的落点：`QUEUE_SCAN_LIMIT`（`app/db/repositories/task/records.py`）、
  `page_tenants`（billing/ops 仓储）、`add_artifacts` 单事务、导出 `SpooledTemporaryFile`、
  workbench 组件用例、`OA_BATCH` 默认值、根 `LICENSE`。

### 8.2 遗留待排期

| 编号 | 事项 | 现状 |
|---|---|---|
| QA-08 | mypy `strict=false` | 类型债，未动 |
| QA-09 | 超大文件（后端 13 个 >800 行，最大 `business/model_service.py` 1,529 行） | 重构级，未动 |
| QA-10 | 上传类型仅按扩展名判定 | 未动（安全） |
| QA-12 | 分支保护 | **阻塞（平台套餐）** |
| QA-20 | `'use client'` 89/93 | 重构级，未动 |
| QA-22 | 前端包体预算 | 无基线，未做 |

### 8.3 建议的下一篇切入点

**建议先做 QA-10（上传内容类型校验）**，理由：

1. 它是**真实安全边界**而非重构：上传入口已存在且行为明确（`apps/adapter/app/api/v1/rag.py:172-192`
   已有流式大小上限与 413，但类型只看扩展名）。
2. 改动面小、可验证：在既有上传路径加「魔数/内容嗅探 + 白名单」，配一条拒绝用例与一条正向用例即可；
   不需要动数据模型，不触发大范围回归。
3. 与现有门禁兼容：不会破坏 `validate_*` 的契约，也不影响 E2E。

第二优先是 **QA-22 的「先测量再立门禁」**：当前 `.next/static/chunks` 合计约 1,334 KB（审计口径），
没有基线就无法讨论阈值；先出一份可重复的包体报告，再决定是否进门禁。QA-09/QA-20 属架构级改造，
建议等产品侧对 V1.1 排期拍板后再动。

---

## 9. 尚未理解 / 需澄清的空白点（代码里看不出来）

1. **平台运营账号的绑定机制**：代码只校验权限点 `ops:console:access`（`ops.py:95,122`），
   但该权限点由哪个 Keycloak 角色/客户端映射而来、初始化数据在哪，未在仓库中找到。
2. **Dify 真实调用是否已接通**：`services/agent_service.py:13` 明确写「T01 阶段说明：返回业务化 mock 结果，
   不产生任何真实底座请求」，而 `clients/dify_client.py` 已存在。真实链路的开关/完成度需确认。
3. **SSE 语义**：`agent_service.py:701-707` 是**内存步骤一次性回放 + 终止帧**，未见事件持久化、
   断线续传或跨副本广播；前端多标签/多端一致性如何保证，代码中无答案。
4. **多副本扩展时点**：插件任务推进明确按「V1.0 单副本、请求内同步」设计，多副本前要换 Valkey 队列
   （`task_service.py:23-32`，登记 U-06）。切换时间表与验收口径未在仓库中。
5. **真实支付渠道**：`payment_gateway.py` 只有 Protocol + `FakeGateway`，未找到真实渠道实现；
   V1.0 是否只做线下对账/回调验签，需要产品确认。
6. **README 测试数字长期未同步**：`README.md:23-24` 的 2343 passed / 91.94% 与当前 2535 passed / 238 skipped
   不一致；是「文档待更新」还是「另有含 PG 的独立口径」，需要维护者给出权威口径。
7. **`stash@{0}` 的 B22-01「软限制改造」**是否仍在路线图；当前无对应批次台账。
