# 全仓审计报告（2026-11-07）

| 项 | 内容 |
| --- | --- |
| 审计起点 | `main @ fea1acd7c83515fa0f139570c905d778917e1dec`（提交时间 2026-11-06 21:27:27 +0800，`fix(audit): PG 用例补 await，并订正交付文档的 CSV 表头口径`） |
| 工作区状态 | **不干净**：审计开始时 25 个已修改 + 3 个未跟踪（`CONTRIBUTING.md`、`docs/adr/0004-open-source-attribution-exemption.md`、`docs/engineering-standards.md`），共 28 项。本报告基于**工作区实际内容**；涉及被并发修改的文件时已单独标注 |
| 审计性质 | 只读。除本文件（`docs/audit-2026-11-07-full-repo-audit.md`）外未修改/新增/删除任何文件；未提交其它文件、未推送、未切分支、未 stash |
| 仓库规模 | 958 个跟踪文件；后端 `app/**/*.py` 229、`tests/**/*.py` 214；前端 `apps/web/src` 195（其中 `.test.ts(x)` 74）；`docs/**.md` 114；`deploy/` 95；标准 compose 21 个 service |
| 测试收集 | `pytest --collect-only -q -p no:cacheprovider` → **3075 tests collected**（未执行任何用例） |

---

## 1. 审计范围与方法

### 1.1 实际执行的动作类型

| 类型 | 具体命令 / 方式 | 说明 |
| --- | --- | --- |
| Git 只读 | `rev-parse HEAD`、`log`、`ls-files`、`status --porcelain`、`git ls-files` 目录统计 | 记录起点与脏区，不做任何写操作 |
| 静态只读门禁 | `python deploy/validate_import_directions.py`、`validate_secrets.py`、`validate_gate_wiring.py`、`validate_repository_parity.py`、`validate_deploy.py`、`validate_migrations.py` | 均只读；`validate_config_names.py` 因系统 Python 缺 `fastapi` 未能运行（见 §7） |
| 测试收集 | `apps/adapter/.venv/Scripts/python -m pytest --collect-only -q -p no:cacheprovider` | 仅收集，不执行；`no:cacheprovider` 避免写 `.pytest_cache` |
| 结构解析 | 用 adapter venv 的 PyYAML 解析 `docker-compose.yml`，统计 service 与观测组件 | 只读 |
| 源码检索 | Grep / Select-String 定位关键字（鉴权豁免、SSRF、分页、CSV、CSP、mock、skip 等） | 只读 |
| 依赖/锁 | 读 `pyproject.toml`、`package.json`、`package-lock.json` 顶层、`uv.lock` 引用关系（静态） | 未执行 `npm install` / `uv sync` |

### 1.2 明确的方法边界

- **未执行**：任何测试用例、`npm install`/`uv sync`、`docker compose up`、`docker build`、迁移应用、备份/恢复演练、压测、真实 IdP 登录。
- **未做动态取证**：所有性能、并发、RLS 生效性、Prometheus 实际标签均属静态推断或设计阅读，未在运行栈上验证；这些一律进入 §5.2「待验证疑点」。
- 结论中的行号指向**审计时工作区内容**；若并发会话随后修改了同一文件（`deploy/.env.example`、`deploy/docker-compose.lite.yml`、`docs/deployment-guide.md`、`apps/adapter/app/core/*` 等），行号可能位移，结论需按文件内容复核。

---

## 2. 整体质量结论

这是一套**工程质量高于同规模平均水平**的代码库：分层与依赖方向有自动化门禁、租户隔离在 SQL 与 RLS 双层设防、计费回调验签+幂等、SSO 令牌不落 JSON、路径与下载令牌有签名与租户绑定、迁移/升级/回滚/备份脚本闭环、CI 门禁覆盖面广。**它可以交付到「私有化试点/受控客户环境」这一步**：核心业务链路（任务、插件、产物、计费、租户）在代码层面自洽，且有 3075 条后端用例与 74 个前端测试文件兜底。

**但它还不能被称为「生产可运维交付」**，卡在三个非功能面上：(1) 5 条告警规则**没有投递通道**（无 Alertmanager，也没有替代通知机制），故障只能靠人盯 Prometheus UI；(2) 适配层自身的外呼（MCP 探测、Webhook/回调）**不走 SSRF 代理、也不拒绝内网地址**，拥有 `ops:settings` 的运营账号可把服务当内网跳板；(3) 最核心的「SSO→兑换→工作台→插件」端到端旅程在 CI **被永久跳过**，同时新增的 4 个 MCP 运营端点**零测试引用**。换言之：**功能正确性有较强证据，安全与运维的"最后一公里"证据不足**。

---

## 3. 仓库结构与模块职责

### 3.1 目录要点

```
apps/
  adapter/           Python 3.13 / FastAPI 适配层（229 个模块 + 214 个测试文件）
    app/api/         23   HTTP 路由（v1 + SSO 会话端点）
    app/business/    39   领域服务（任务/插件/计费/用量/租户/成员/产物令牌…）
    app/db/          70   引擎、会话、迁移、仓储（repositories）
    app/schemas/     21   Pydantic 契约
    app/plugins/     20   插件清单、引擎、守卫、消毒器、动作
    app/core/        18   配置、安全、幂等、CSRF、加密、指标
    app/services/    17   底座/模型/预览等外部能力封装
    app/tools/        7   内部工具集
    app/workers/      6   PG 队列消费者/任务
    app/clients/      4   Dify/MCP 等底座客户端
    app/middleware/   4   trace_id、错误信封、鉴权、访问日志
    app/registry/     4   能力/插件注册表
    app/kernel/       2   跨域内核
  web/               Next.js 16 / React 19（195 个 src 文件，74 个测试文件）
    src/components/  80   UI 组件（按域分组）
    src/lib/         49   统一 API 客户端、格式化、重定向、错误映射
    src/app/         47   路由组 (auth)/(main) + Route Handlers
    src/hooks/       17   查询/变更/重试/上传/SSE
deploy/              95   三套 compose、k8s、nginx、prometheus、迁移、16+ 校验器、install/upgrade/backup/restore
docs/                114  架构、PRD、ADR、运维指南、历史审计与批次台账
.github/workflows/   4    ci-adapter / ci-web / dependency-scan / sbom
```

### 3.2 模块职责与依赖边界（文字图）

- 后端主方向：`api → business → db(repositories) → core`；`plugins/engine → services/clients`；`workers → business`。方向由 `deploy/validate_import_directions.py`（受控层矩阵 `api/services/business/db/core/schemas/plugins/registry/tools/clients/middleware/workers/kernel`）自动校验。
- 该门禁当前结论：**违规 23 条全部在基线内、文件级环 3 个全部在基线内、动态导入盲区 5 处**（详见 M-01）。基线的存在说明"边界并非完全干净"，只是没有新增违规。
- 前端数据流：页面 (`src/app`) → hooks (`use-api`/`use-retryable-request`) → `src/lib/api-client.ts`（统一 `credentials: 'include'`、超时 `AbortController`）→ 适配层；`scripts/check-oidc-auth.mjs` 静态禁止组件自行注入 `Authorization`。
- 前端与后端**没有共享类型包**：契约靠 `docs/api/openapi.snapshot.json` + 手写 TS 类型，属于可接受的重复源风险（未列入问题，因有快照门禁）。

---

## 4. 问题清单（按严重程度分级）

> 统计：**高 5 / 中 6 / 低 4**。每条均为实证（文件:行号或命令输出）；纯推测项不在此表，见 §5.2。

### 4.1 高（High）

#### H-01 5 条告警规则无投递通道，故障不会通知到人

| 项 | 内容 |
| --- | --- |
| 影响范围 | 生产/私有化的全部告警：5xx 率、P95 延迟、队列积压、预算超限、TargetDown |
| 风险等级 | 高（运维不可观测） |
| 证据 | `deploy/prometheus.yml` 全文 63 行，**无 `alerting:` / `alertmanagers:` 段**（`Select-String 'alerting|alertmanagers'` 命中 0）；`deploy/docker-compose.yml` 的 `prometheus` 服务块（约 811-830 行）无 `Alertmanager`；解析 compose 得 21 个 service，观测相关仅 `prometheus`、`postgres-exporter`、`valkey-exporter`；`docs/deployment-guide.md:622` 仍写「内置指标采集 + 5 条告警规则」 |
| 判断依据 | Prometheus 仅**评估**规则并把 ALERTS 存于自身 TSDB；没有 Alertmanager 或任何 webhook 转发时，firing 状态只在 Prometheus UI 可见。`alert.rules.yml` 5 条规则齐备（ApiErrorRateHigh、ApiLatencyP95High、TaskQueueBacklog、TenantBudgetNearlyLimit、TargetDown），但无接收端 |
| 修复方向 | 在 compose 增加 Alertmanager 或让 Prometheus `alerting` 指向客户侧接收端；若不内置，必须在 `deployment-guide` 明确「需客户自备 Alertmanager」并给出接入步骤 |

#### H-02 适配层外呼无 SSRF 内网阻断，`OA_SSRF_PROXY_URL` 只覆盖 Dify

| 项 | 内容 |
| --- | --- |
| 影响范围 | MCP 外部服务登记/探测、支付回调拉取、Webhook 等所有适配层出站请求 |
| 风险等级 | 高（安全） |
| 证据 | `apps/adapter/app/business/mcp_service.py:425-472`：`_validate_url()` 只校验长度、`http/https`、URL 内凭据、敏感 query、fragment；`http` 仅回环放行（`:467`），**对 `https` 目标不做 IP/CIDR/内网域名拒绝**；`apps/adapter/app/api/v1/ops_mcp.py:185-190` 的 `POST /{server_id}/probe` 在服务端发起连接；`deploy/.env.example:201`、`deploy/docker-compose.yml:284-285` 显示 `OA_SSRF_PROXY_URL` 只注入 Dify（`SSRF_PROXY_HTTP(S)_URL`），`apps/` 全面检索该变量 **0 命中** |
| 判断依据 | 任何具备 `ops:console:access` / `ops:settings` 的运营账号可登记 `https://169.254.169.254/…` 或 `https://10.0.0.5/…` 并触发 probe，由适配层发起请求并回显工具列表/错误；SaaS 形态下这是跨租户基础设施探测面。G-9（SSRF 代理）在配置层登记但代码层未闭环 |
| 修复方向 | 在 `_validate_url` 增加「解析后 IP 必须为公网」校验（拒绝回环/私网/链路本地/元数据段），或让适配层所有外呼走统一代理；probe 增加目标域名白名单配置项 |

#### H-03 新增 MCP 运营端点与产物服务在测试中零引用

| 项 | 内容 |
| --- | --- |
| 影响范围 | `api/v1/ops_mcp.py`（MCP1–MCP4）、`api/v1/tools_ext.py`、`business/artifact_service.py`、`plugins/guards/table_parse.py`、`schemas/tool_ext.py` |
| 风险等级 | 高（质量/安全组合风险） |
| 证据 | `ops_mcp.py:74,114,151,185` 共 4 个路由（列表/登记/删除/探测）；对 `apps/adapter/tests/**/*.py`（214 个文件）全文检索 `ops_mcp|tools_ext|artifact_service|table_parse`，**命中 0** |
| 判断依据 | 这些模块均被生产代码引用（非死代码），但没有任何测试文件出现其模块名/路径；`artifact_service.py` 位于 `/files/artifacts/{token}` 读取链路，MCP 端点还承担跨租户运营操作。新功能上线即无回归保护，且与 H-02 的 SSRF 面直接叠加 |
| 修复方向 | 为 MCP1–MCP4 补「非运营 403 / 非法 URL 400 / probe 失败可观测 / 删除幂等」四类用例；为产物服务补「令牌过期 404 / 租户不匹配 404 / 空内容回落 inline_text」用例 |

#### H-04 端到端主旅程在 CI 被永久跳过（已登记未闭环）

| 项 | 内容 |
| --- | --- |
| 影响范围 | SSO 登录 → 兑换 → 工作台 → 插件安装 的整条商业主链路 |
| 风险等级 | 高（交付证据缺口） |
| 证据 | `.github/workflows/ci-web.yml:338-343` 注释自述「缺凭据改为必红…CI 里没有测试账号，设 `OA_E2E_ALLOW_SKIP=1` —— **这是已知缺口，不是已闭环**」；`:379` 实际设置 `OA_E2E_ALLOW_SKIP: '1'`；`apps/web/e2e/workflow.spec.ts` 的贯穿段在无账号时跳过（仅匿名段必跑） |
| 判断依据 | 主旅程在 CI 每次都是 "skipped"，本地也无法复现；插件安装步骤本身也只打开抽屉，未完成真实安装 |
| 修复方向 | 用 Keycloak test realm + 预置租户账号注入 CI；或提供 API 级 E2E fixture 覆盖同一链路，并把 `OA_E2E_ALLOW_SKIP` 置 0 |

#### H-05 前端 CI 路径过滤漏掉根 `package-lock.json`

| 项 | 内容 |
| --- | --- |
| 影响范围 | 所有仅修改依赖锁/根清单的 PR（含依赖收敛、安全补丁、overrides） |
| 风险等级 | 高（门禁盲区） |
| 证据 | `.github/workflows/ci-web.yml:13-23`（push）与 `:25-30`（pull_request）的 `paths` 只含 `apps/web/**`、两份 PRD 源、插件清单与自身；**不含 `package-lock.json` / `package.json`**。而 npm workspaces 的锁在仓库根（P04 双版本收敛即只改根 lock + `apps/web/package.json`） |
| 判断依据 | 若某 PR 只改根 lock（例如 `overrides`、安全补丁），ci-web 不会触发，`vitest`/`next build` 都不会在合并前跑；`sbom.yml` 反而监听 `package-lock.json`，形成"扫 SBOM 但不测前端"的不对称 |
| 修复方向 | 在 ci-web 两个 `paths` 增加 `package-lock.json`、`package.json`、`apps/web/package.json`（后者已被 `apps/web/**` 覆盖，可只补根两文件） |

### 4.2 中（Medium）

#### M-01 分层依赖门禁"基线放行"23 条倒挂 + 3 个环 + 5 处动态导入盲区

- 影响：结构边界长期挂账，重构时易把既有环扩大。
- 证据：`python deploy/validate_import_directions.py` 末段原文「结论：违规 23 条（基线内 23 / 基线外 0 / stale 0），环 3 个（基线内 3 / 新增 0 / stale 0），扫描 227 个文件，批次时钟 未启用」并输出 `[OK]`；`deploy/import_direction_baseline.txt` 共 81 行；警告含 `R2-ARC-07 job_runner.py ↔ workers/tasks/__init__.py + retention_cleanup.py` 互引；动态导入盲区 5 处均在 `app/services/tool_service.py:78-82`。
- 判断：门禁绿只证明"无新增"，不代表分层干净；本地运行 `批次时钟 未启用` 时 `due_batch` 到期不会判红（CI 设 `OA_BATCH: B21`，`ci-adapter.yml:93`）。
- 修复：按 `due_batch` 逐条清零，优先消除 `workers` 环（把任务注册表下沉为 `job_registry`）。

#### M-02 CSP 允许 inline 脚本与任意主机连接

- 影响：一旦出现 XSS/依赖投毒，数据可外传到任意域；`script-src 'unsafe-inline'` 削弱 XSS 防线。
- 证据：`apps/web/next.config.js:51` `script-src 'self' 'unsafe-inline'`；`:55` `connect-src 'self' http: https:`；`:36-46` 注释承认"保守起点、非最终形态"。
- 判断：注释解释的是私有化 HTTP 场景，但 `https:` 是**无主机限制**的放开，攻击者可用任意 HTTPS 域外传；与"令牌不进 JS"的既有防线配套时风险可控，但仍属可收紧项。
- 修复：改用 per-request nonce（middleware 生成）；`connect-src` 收敛为配置化的后端域名（私有化现场注入）。

#### M-03 成员分页在无部门过滤时仍全量载入内存

- 影响：大组织（数千成员）单页请求的内存与延迟随总人数线性增长。
- 证据：`apps/adapter/app/business/member_service.py:165-194`（`list_members_page`）：`:185` 调 `_prepare_candidates`，`:191` 无部门过滤分支直接 `total = len(candidates.entries)` 再切片；`_prepare_candidates` 自 `:196` 起，注释自述"先按角色/状态/关键字在内存中裁剪候选"。
- 判断：分页只优化了"部门关联"的批量读取，候选集本身未下推；`_prepare_candidates` 需要读取 IdP 全量用户。
- 修复：向 IdP 分页下推，或按租户缓存成员快照并服务端分页。

#### M-04 Node 运行时版本声明与实际运行/类型不一致

- 影响：本地 Node 20 可安装成功，但类型与镜像均按 22，行为差异延后暴露。
- 证据：根 `package.json:7-8` `"engines": { "node": ">=20.11.0" }`；`.github/workflows/ci-web.yml` 5 处 `node-version: '22'`；`apps/web/Dockerfile` 三个 stage 均 `node:22-alpine`；`apps/web/package.json` 的 `@types/node` 为 `^22.20.5`。
- 判断：三处真源（CI/Docker/types）一致为 22，仅声明放宽到 20，属于声明漂移。
- 修复：`engines.node` 改 `>=22.0.0`，并加 `.nvmrc` 作为单一真源。

#### M-05 告警规则自证（promtool）未接入 CI

- 影响：规则表达式/时间语义改动无自动回归；规则写错只能等故障时发现。
- 证据：4 个 workflow 中检索 `promtool` **命中 0**；`docs/engineering-standards.md:187,295` 已把 S6.9 标为「规范待固化：文件存在、零引用」。
- 修复：`ci-adapter` 增加 `promtool check rules deploy/alert.rules.yml` 与 `promtool test rules deploy/alert.rules.test.yml`。

#### M-06 无集中日志/追踪，且前端无指标

- 影响：多容器排障只能 `docker logs` 逐容器翻；跨服务请求无法按 trace 关联；前端错误率不可见。
- 证据：compose 21 个 service 中观测相关仅 `prometheus`、`postgres-exporter`、`valkey-exporter`（PyYAML 实测），无 Loki/Promtail/Tempo/Grafana；`deploy/prometheus.yml` 4 个 job（adapter/postgres/valkey/prometheus）**不含 web**；`docs/adr/0002-infrastructure-license-compliance.md:360-369` 只裁定"移除 Grafana"，未给出日志/追踪替代方案。
- 修复：明确客户侧日志方案（如 stdout JSON + 客户 Loki），并补 web 侧指标（至少 Next 服务可用性/5xx 由网关侧采集）。

### 4.3 低（Low）

#### L-01 ESLint 9.x 处于上游 EOL（已被 npm 标记 deprecated）

- 证据：`apps/web/package.json` 的 `eslint: "^9.39.5"`；插件链 peer 上限（react/jsx-a11y/import 均为 `^9`）阻断 v10；`docs/cleanup-audit-2026-11-04.md §8.5` 已记录。
- 影响：安全公告无人修复；升级条件被外部包卡住。
- 修复：跟踪插件链 peer 放宽到 `^10` 后立即迁移；期间订阅 eslint 安全公告。

#### L-02 备份无内置调度模板

- 证据：`deploy/backup.sh:14-24` 设计完整（`.partial` 原子改名、`meta.counts.tsv` / `meta.samples.tsv` 指纹、退出码 0/1/2）；但仓库无 cron/systemd timer 单元，`docs/deployment-guide.md:447-448` 仅给出手工命令。
- 影响：客户忘记配置即长期无备份。
- 修复：提供 `deploy/systemd/officeai-backup.{service,timer}` 或 compose sidecar，并在部署清单勾选。

#### L-03 缺值班/升级/故障分级 runbook

- 证据：`git ls-files docs` 检索 `runbook|incident|故障|应急` 仅命中 `deployment-guide.md` 的入口表；第 12 章（`:654-800`）是 12 条常见故障排查，不含告警分级、升级路径、值班与对外沟通模板。
- 影响：告警即便接通（H-01 修复后），也没有"谁在多长时间内做什么"的处置约定。
- 修复：新增 `docs/runbook-incident-response.md`，与 `alert.rules.yml` 的 severity 对齐。

#### L-04 前端无 middleware，未登录可访问页面壳

- 证据：`Glob apps/web/src/middleware.ts` → 无文件；鉴权由 `api-client` 携带 Cookie 后由适配层返回 401（数据面安全）。
- 影响：未登录用户可看到页面框架/静态文案；不构成越权，但暴露信息结构与导航。
- 修复：若需"未登录直接跳登录"，加轻量 middleware 仅做 Cookie 存在性重定向（不作为安全边界）。

---

## 5. 已确认发现 vs 待验证疑点

### 5.1 已确认发现（可直接复核）

即 §4 的 15 条。复核方式：

| 编号 | 复核命令 / 位置 |
| --- | --- |
| H-01 | `Select-String deploy/prometheus.yml -Pattern 'alerting'`（0 命中）；`docker-compose.yml` prometheus 服务块 |
| H-02 | 读 `business/mcp_service.py:425-472` + `ops_mcp.py:185-190`；`Select-String apps -Pattern 'OA_SSRF_PROXY_URL'`（0 命中） |
| H-03 | `Select-String apps/adapter/tests -Pattern 'ops_mcp|tools_ext|artifact_service|table_parse'`（0 命中）；`ops_mcp.py:74/114/151/185` |
| H-04 | `ci-web.yml:338-343,379` |
| H-05 | `ci-web.yml:13-23,25-30` 的 paths |
| M-01 | `python deploy/validate_import_directions.py` 末段结论 |
| M-02 | `apps/web/next.config.js:51,55` |
| M-03 | `member_service.py:170-175,185-194` |
| M-04 | `package.json:7-8`；`Dockerfile` 三个 stage；ci-web 五处 |
| M-05 | `Select-String .github/workflows -Pattern promtool`（0 命中）；`engineering-standards.md:187` |
| M-06 | PyYAML 解析 compose service 列表；`prometheus.yml` 4 个 job；ADR-0002:370 |
| L-01～L-04 | 见各条证据 |

### 5.2 待验证疑点（本轮无法实证，需条件）

| 编号 | 疑点 | 需要什么条件才能验证 |
| --- | --- | --- |
| U-01 | `deploy/prometheus.yml:21` 用 `${OA_DEPLOY_MODE}` 做 external_label，但 compose 的 prometheus 服务**未注入该环境变量**（服务块无 `environment:`）；标签最终是空值还是字面量取决于 Prometheus 是否展开变量 | 启动 compose 后查 `/targets` 或 `/api/v1/targets` 的标签；或 `promtool check config` |
| U-02 | 真实查询性能与 N+1：仅静态看到 `usage_event` 有两枚复合索引（`(period,user)`、`(period,dept)`），未见 `EXPLAIN` | 真 PG + `EXPLAIN ANALYZE` 跑用量汇总、成员列表、审计导出 |
| U-03 | 进程内 `JobRunner` 多副本下的吞吐、租约回收与饥饿（代码用 `SKIP LOCKED`，设计合理） | 需 docker 起 PG + 多 worker 副本 + 压测 |
| U-04 | RLS 在应用连接角色下是否真正生效（迁移中 `ENABLE ROW LEVEL SECURITY` 齐全，但连接用户可能是 table owner / BYPASSRLS） | 真库用应用角色 `SET ROLE` 后跨租户查询，期望 0 行 |
| U-05 | SSO 刷新令牌复用检测依赖 IdP；适配层只转手，未见本地复用检测 | 真 Keycloak 上用旧 refresh token 重放，观察是否被拒绝 |
| U-06 | 大文件上传/Office 预览（LibreOffice 转换）内存与超时；下载端点 `artifact_files.py:88-93` 把 `record.content` 整块放入 `Response` | 真机传大文件 + 观察 RSS/超时；确认 `content` 的产生侧上限 |
| U-07 | 覆盖率分模块盲区：本轮未跑 `pytest-cov`，无法给出具体 0% 模块；静态只能证明 5 个模块在测试文本中零引用 | 跑一次 `pytest --cov` 并读 `coverage.xml`（会产生 `.coverage`，故未在只读约束内执行） |
| U-08 | 依赖漏洞现状：未运行 `npm audit` / `uv pip audit`（需安装/网络缓存） | 在隔离环境跑 audit，比对 P04 记录的 0 high / 8 moderate |
| U-09 | 插件沙箱隔离强度：引擎以 `asyncio.wait_for` 限时，但未验证动作是否在独立进程/资源限制下运行 | 读部署侧 Plugin Daemon 配置 + 真机跑超时/内存超限动作 |

---

## 6. 整改方向（按优先级）

| 优先级 | 项 | 依赖关系 | 工作量量级 |
| --- | --- | --- | --- |
| **P0** | H-02 外呼 SSRF 阻断（IP/CIDR 拒绝表 + probe 白名单） | 无；与 H-03 用例一起做更稳 | S（1 人日内） |
| **P0** | H-01 告警投递（Alertmanager 或客户接入指引 + 文档订正） | 无 | S–M（0.5–1 人日） |
| **P1** | H-03 补 MCP/产物服务测试（先覆盖 H-02 修复的负样本） | 依赖 H-02 的校验规则定稿 | M（2–3 人日） |
| **P1** | H-05 ci-web paths 补根 lock/清单 | 无 | XS（分钟级） |
| **P1** | H-04 主旅程 E2E 凭据化或 API 级替代 | 依赖测试租户/Keycloak realm 准备 | L（3–5 人日） |
| **P1** | M-01 清理基线倒挂（先 workers 环） | 依赖分层评审；与 H-03 相邻 | M（2–4 人日） |
| **P1** | M-05 promtool 进 CI | 无；顺带补 `alert.rules.test.yml` 引用 | XS（0.5 人日） |
| **P2** | M-02 CSP nonce 化 + connect-src 收敛 | 依赖前端 middleware 设计（L-04 可合并） | M（2–3 人日） |
| **P2** | M-03 成员分页下推/快照 | 依赖 IdP 分页能力确认 | M（2–3 人日） |
| **P2** | M-04 engines/`.nvmrc` 单一真源 | 无 | XS |
| **P2** | M-06 日志/追踪方案落地（客户侧标准或内置） | 依赖 ADR 补充裁定 | L（3–5 人日） |
| **P2** | L-01～L-04（EOL 跟踪、备份定时、runbook、页面壳跳转） | 无强依赖 | S–M 各 |

---

## 7. 未覆盖项与信息不足

| 项 | 原因 | 影响 |
| --- | --- | --- |
| 后端全量/覆盖率、前端 vitest 执行 | 只读约束（运行会写 `.coverage`/`.pytest_cache`，且耗时长） | 无法给出分模块覆盖率与真实通过率；本报告只给收集数 3075 |
| `deploy/validate_config_names.py` 本地运行 | 系统 Python 缺 `fastapi`，adapter venv 也不满足该脚本的导入路径 | 配置名门禁结论转引 CI；未本地复核 |
| 容器/网络暴露面实测 | 禁止 `docker compose up`/`build` | 端口、网络别名、健康检查仅静态核对 |
| 迁移与回滚实操 | 会写库；禁止 | 仅读脚本与文档，未验证 `upgrade.sh` 回滚链路 |
| 备份/恢复演练 | 需 Docker + 磁盘时间 | U-02/U-04 类运行时结论缺口 |
| 压测/长稳/故障注入 | 需环境 | 并发、连接池、告警触发均未实测 |
| 前端生产构建 | 需 `npm ci`/缓存 | 本轮只静态读配置；构建成功性转引最近 CI（`41946759122` 绿） |
| 许可证扫描实跑 | 需网络安装 `pip-licenses`/`license-checker` | 仅核对 P09 已锁版本与 workflow 配置 |
| `docs/` 全量逐篇比对 | 114 篇；另有三篇文档在工作区被并发修改（`deployment-guide.md`、`architecture-v1.0.md`、`plan-v1.0.md`） | 文档漂移只抽查了与运维/告警/备份相关的章节；数字类漂移未全量复算，避免与并发编辑冲突 |
| `qa/` 目录内容 | 本次聚焦运行时代码与配置，未逐份读 QA 报告 | 可能遗漏测试策略层结论 |
