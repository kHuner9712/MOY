# 全仓审计报告（第五轮）

- 审计对象：`c:\Users\15864\Desktop\麦子`
- 审计日期：2026-11-03（Asia/Shanghai）
- 审计方式：本轮重新取证，不沿用旧报告结论；只读审计，未修改产品代码与配置
- 代码基线：`main` 分支 HEAD `01b53cd`（2026-11-03 14:23）
- 工作区状态：**不干净**（5 个未提交修改 + 1 个未跟踪归档文件 + 3 个历史 stash），详见第 2 节
- 取证手段：`git`/`gh`（CI 实跑日志与工件）、本地静态门禁、本地单测与覆盖率、`npm audit`/`npm view`、YAML/端口/镜像清单静态核对、`deploy/validate_*.py` 门禁实跑

> 结论先行：G1/M1/M2 已真闭环，M3 的 action pin 已闭环；但 **CI Adapter 与 CI Web 在 HEAD 上仍然是红的**，三条红的修复全部停留在工作区未提交、未在 CI 验证。这是本轮唯一"高"级问题，也是阻断"可发布"结论的关键。

---

## 1. 状态核实（第一步）

### 1.1 git 基线

```
HEAD 01b53cd fix(ci): 交付清单尺寸改 LF 归一口径（M3 取证，CI 恒红根因）
1443c61 fix(ci,web): prettier 格式补齐 6 文件 + 清单门禁打印差异行（M3 取证）
2760883 fix(ci): Dependency Scan 三处存量 bug（M3 取证发现，门禁此前从未跑通过）
411915e fix(ci,web): CI 首跑取证修复——交付清单漂移 + G-27 误扫测试文件（M3）
111be00 fix(ci): Dependency Scan 的 pip-licenses 参数 bug（M3 取证发现）
7c20647 chore(ci): 38 处 action 引用 pin 到 commit SHA（M3）
23e9f69 fix(deploy): 部署形态收口——端口绑回环 + 边缘 TLS + k8s 最小清单（G1）
83cec37 fix(adapter,web): 并发刷新竞态收口——刷新幂等窗口 + 跨标签单飞（M1）
47a8c99 chore(ci): PG 仓储覆盖下限 50→88（M2 收口）
6ba156b test(web): 入库四站点三态矩阵，补上重构未逐站点验证的洞
```

- 上一轮报告的 HEAD `5e8ff2b` 之后新增 8 个提交（6ba156b…01b53cd），M1/M2/G1/M3 收口提交均在 HEAD 历史内。
- 用户基线提到的归档提交 `880df56` **确实存在且可达**（`git branch --contains` 显示在 main 上），但位于 `-15` 之外；当前工作区又出现一份**未跟踪**的 round3 归档，见 2.2。
- 分支：仅本地 `main`（无 `origin`，无远端分支）。
- stash：**3 条历史 stash 滞留**（见 2.3）。

### 1.2 未提交改动清点（`git status --porcelain`）

| 文件 | 增/删行 | 归属工作线 | 完成度 | 说明 |
|---|---|---|---|---|
| `.github/workflows/ci-adapter.yml` | +5/-1 | M3 | **修复已写、未提交** | 注册表校验由 `yaml.safe_load` 改为 `yaml.safe_load_all`，逐文档校验（见 4.4） |
| `apps/adapter/tests/integration/test_org_endpoints.py` | +9/-1 | M3（CI 红） | 修复已写、未提交 | PG 引擎事件循环隔离，修 `attached to a different loop` |
| `apps/adapter/tests/integration/test_org_repo_pg.py` | +9/-1 | M3（CI 红） | 修复已写、未提交 | 同上 |
| `apps/adapter/tests/integration/test_usage_endpoints.py` | +15/-7 | M3（CI 红） | 修复已写、未提交 | 事件 ID 唯一化，避开 `usage_event_pkey` 冲突 |
| `apps/adapter/tests/integration/test_usage_repo_pg.py` | +18/-2 | M3（CI 红） | 修复已写、未提交 | 同上 |
| `apps/web/e2e/session-redirect.spec.ts` | +33/-3 | M3（e2e 红） | 修复已写、未提交 | 新增 `/sso/session` 路由替身，断言未放松 |
| `apps/web/e2e/workflow.spec.ts` | +56/-33 | M3（e2e 红） | 修复已写、未提交 | 同上，覆盖两条匿名段用例 |
| `docs/archive/audit-2026-11-02-full-repo-audit.md` | +212/-201 | 上一轮报告落盘 | **未提交** | 工作区内容是 round4 报告，HEAD 里仍是 round3 文本 |
| `docs/archive/audit-2026-11-02-full-repo-audit-round3.md` | 未跟踪 | 归档 | **未提交** | 41,385 字节；与 1.1 的 `880df56` 归档动作重复/冲突 |

**归属结论**：
- **M2**：无未提交改动，阈值已在 `47a8c99` 提交。
- **M1**：无未提交改动，实现与用例已在 `83cec37` 提交。
- **G1**：无未提交改动，部署收口已在 `23e9f69` 提交。
- **M3**：全部残留都在工作区（CI 修复 1 个 + PG 用例修复 2 个文件 ×2 处 + e2e 修复 2 个），**未提交、未在 CI 验证**。
- 另有"上一轮审计报告/归档"文档未提交，导致报告自身不可溯源。

### 1.3 历史 stash（3 条，均为陈旧遗留）

```
stash@{0}: On refactor/wave3-deploy-lint: WIP: B22-01 b批① 软限制改造（2026-10-27 中断半成品，SyntaxError:505 ruff errors，勿直接 apply）
stash@{1}: On develop: s2-wip
stash@{2}: On develop: pre-b20-dirty
```

- 均与当前 M1/M2/M3/G1 无关；`stash@{0}` 自带"勿直接 apply"警告。
- 处置建议见第 7 节；不建议在本轮审计中 apply/丢弃。

---

## 2. 六维度结论

### 2.1 项目整体架构与目录结构

**结论：分层与边界基本健康，门禁可执行；结构性债务集中在"超大文件 + 文档/部署双口径"。**

- 单仓双应用：`apps/web`（Next.js，93 个非测试 `.tsx`，其中 **89 个** `'use client'`）、`apps/adapter`（FastAPI，428 个 `.py`，非测试源码约 6.75 万行；前端 `src` 约 4.25 万行）。全仓 878 个跟踪文件，`docs/` 111 个。
- 适配层分层 `api → business → db/repositories → core/schemas/tools/workers`，由导入方向门禁（`deploy/validate_import_directions.py`）与仓储对称门禁（`validate_repository_parity.py`）约束。
- 本轮实跑 10 个部署/架构门禁（images/deps/config_names/deploy/secrets/packaging/import_directions/repository_parity/gate_wiring）**全部 EXIT=0**；镜像清单 `deploy/images.lock` 20 条。
- 部署编排三套：`docker-compose.yml`（私有化）、`docker-compose.saas.yml`、`docker-compose.lite.yml`；端口/网络口径本轮已对齐（见 3.1）。
- k8s 清单为 **V1.1 准备物**，`deploy/k8s/README.md:1-25` 明确"未在真实集群做过端到端验收，不得作为 V1.0 验收依据"；与 compose 的语义一致性存在缺口（QA-02）。

**风险点**：超大文件、`'use client'` 泛化（89/93，QA-20）、文档数量与陈旧数字（QA-17）。

### 2.2 代码质量与规范一致性

**结论：静态门禁全绿；未提交改动是唯一"未完成"状态；无代码 TODO 债务。**

本轮本地实跑：

| 门禁 | 命令 | 结果 |
|---|---|---|
| ruff lint | `ruff check .` | 通过（0 错） |
| ruff format | `ruff format --check .` | 通过 |
| mypy | `mypy app` | 通过（但 `strict=false`，见 QA-08） |
| eslint | `npm run lint` | 通过 |
| tsc | `npm run typecheck` | 通过 |
| prettier | `prettier --check "src/**/*.{ts,tsx,css}" "*.{json,js}"`（CI 精确口径） | 通过 |
| 部署/架构门禁 | `deploy/validate_*.py` 10 项 | 全部通过 |

- 代码 TODO/FIXME：适配层 0 处（唯一 TODO 字面量是插件枚举值）。
- 超大文件：后端 13 个 >800 行（最大 `apps/adapter/app/business/model_service.py` 1,529 行）；前端 4 个 >500 行（`api-client.ts` 1,890、`types.ts` 1,751、`team/page.tsx` 760、`auth-refresh.ts` 681）。
- 重复实现：`now_iso` 有 7 份拷贝（跨模块），无重复代码门禁。
- Prettier 漂移与交付清单 `{size}` 问题：已由 `1443c61`/`01b53cd` 提交修复；`gen_delivery_manifest.py:329-330` 明确"按 LF 归一后计字节"，本地精确门禁通过。
- **未提交改动完成度**：M3 的 CI/测试/e2e 修复逻辑上完整、E2E 单条本地已验证通过，但未提交、未跑 CI（QA-01）。

### 2.3 潜在安全漏洞

**结论：核心鉴权/令牌/边缘面已收口；剩余风险集中在"错误信息回传、上传校验、合规取证与发布治理"。**

已核实的安全控制：

- 认证 Cookie：`apps/adapter/app/core/session_cookie.py:194-247` 使用 `HttpOnly`、`Secure`（按配置/模式）、`SameSite`、受限 `Path`；访问令牌不下发到响应体。
- CSRF / 同站校验：`apps/adapter/app/core/security.py:454`；SSO 端点 `_require_same_site`（`sso_routes.py:707`）。
- CORS：`apps/adapter/app/main.py:487-499` 非 dev 默认空允许源，未使用 `*`。
- 端口暴露面：基础设施/明文服务全部 `127.0.0.1` 绑定（见 3.1）；仅 edge 发布 80/443。
- 边缘：80 → 301，443 TLS；HSTS/X-Content-Type-Options/X-Frame-Options/Referrer-Policy 全站下发；`server_tokens off`（`deploy/nginx/edge.conf:38-86,101-104,172-175,198-201,227-230`）。
- SSRF：微信插件对"外部链接"直接拒绝并落审计（`apps/adapter/app/tools/wechat_mp_connector.py:320-333`），未发现用户可控 URL 直连。
- 上传：有流式大小上限与 413（`apps/adapter/app/api/v1/rag.py:172-192`）；但类型判定仅按扩展名（QA-10）。
- 密钥：`validate_secrets.py` 通过；`.env.example` 保留弱默认（`officeai`/`admin`，`deploy/.env.example:119,224`），由部署门禁阻断生产使用（QA-21 已降级为低）。
- 刷新令牌：见 3.2，M1 已闭环，无"空 Cookie 覆盖新令牌"路径。

未闭环/新增：错误文案直传（QA-09）、上传仅扩展名（QA-10）、无项目 LICENSE + 依赖许可证元数据缺口（QA-11）、分支保护不可取证（QA-12）。

### 2.4 性能隐患

**结论：上一轮的 4 个性能问题全部仍在，均为"低并发可用、规模化会痛"的结构性问题。**

| 位置 | 现状 | 影响 |
|---|---|---|
| `apps/adapter/app/db/repositories/task/postgres.py:438-461` | `list_queued` 无 `LIMIT`、`SELECT *` 全量返回 | 队列积压时 worker 内存/延迟线性上升（QA-03） |
| `apps/adapter/app/business/ops_service.py:216-237` | `list_tenants` 先全量拉取再内存切片分页 | 租户数增长后 O(N) 拉取与内存（QA-04） |
| `apps/adapter/app/db/repositories/artifact_repo.py:423-470` | `add_artifacts` 对每条记录单独 `tenant_scope`/事务（`for record` + `async with tenant_scope`） | N 次连接/事务，批越大越慢（QA-05） |
| `apps/adapter/app/business/tenant_data_service.py:679-694` | 导出在内存 `BytesIO` 拼 ZIP | 大租户导出时内存峰值（QA-06） |

补充：前端 `'use client'` **89/93**；构建产物 JS chunks 合计 **1,334 KB**（未压缩，`.next/static/chunks`），无 first-load 预算门禁（QA-20/22）；SaaS 编排 0 个 `mem_limit`/`cpus`（QA-21）。

### 2.5 依赖版本与许可

**结论：锁文件完整、已知漏洞分级可控，但"过期版本/许可证取证/镜像摘要"三项不完整。**

- 后端：`apps/adapter/uv.lock` 存在，CI 用 `uv sync --frozen`；`pip-audit` 与 `pip-licenses` 在 Dependency Scan 中执行。`pip list --outdated` 实测 **11 项**（均 minor/patch）：fastapi 0.156.8→0.162.0、anyio 4.15.1→4.16.0、pydantic 2.14.1→2.14.2、pydantic-core 2.50.1→2.50.2、pydantic-settings 2.16.1→2.16.2、websockets 17.3→17.4、python-multipart 0.0.32→0.0.33、opentelemetry-api 1.45.1→1.45.2、mypy 2.4.0→2.4.1、pytest-asyncio 1.4.1→1.4.1.post1、pip 26.1.2→26.3。
- 前端：`package-lock.json` 覆盖；运行时依赖 **168** 个：MIT 119、Apache-2.0 22、LGPL-3.0-or-later 14（含 4 个组合表达式，均为 `@img/sharp-*` 可选平台包）、ISC 8、CC-BY-4.0 1、BSD-3-Clause 1、0BSD 1、UNLICENSED 1、UNSPECIFIED 1。
  - `npm audit`：**16 个 moderate / 0 high / 0 critical**，全部来自 eslint/minimatch 开发链（`eslint`、`eslint-config-next` 为直接依赖）；`npm audit --omit=dev` = **0 漏洞**。CI 以 `--audit-level=high` 运行（`dependency-scan.yml:252`），因此 16 项不会阻断（QA-15）。
  - `npm outdated` 首次完整实测 **16 项，其中 8 个大版本**（已用 `npm view` 复核关键项）：typescript 5.9.3→7.0.2、date-fns 3.6.0→4.4.0、zustand 4.5.7→5.0.10、sonner 1.7.4→2.0.7、lucide-react 0.454.0→1.67、tailwind-merge 2.6.1→3.3.x、tailwindcss 3.4.19→4.x、@types/node 20→26。
  - LGPL 说明：生产镜像 `npm ci --omit=optional`（`apps/web/Dockerfile:43`），14 个 `@img/sharp-*` 不进入产物；但仓库无独立许可证策略文档，若后续去掉 `--omit=optional` 会引入 LGPL 原生模块，需复核。
- 镜像：compose 侧 **51 个 image 引用、0 个 `@sha256`**；k8s 侧 2 个应用镜像固定 digest；`images.lock` 20 条（QA-16）。
- 项目自身：**无 `LICENSE` 文件**；后端 53 个已安装包中 42 个 METADATA 无 `License`/`License-Expression` 字段（QA-11）。

### 2.6 测试覆盖

**结论：阈值与实测匹配，但 CI 在 HEAD 上红；前端存在 6 个 0 覆盖组件，后端 235 个 PG 用例本地默认跳过。**

实测（本轮 + 最新 CI 工件）：

| 项 | 实测 | 阈值 | 判定 |
|---|---|---|---|
| 后端总覆盖 | 18,330/19,440 = **94.29% 行**，分支 84.30% | `--cov-fail-under=85`（`ci-adapter.yml:557`） | 通过，余量 +9.29pp |
| PG 仓储覆盖 | **92.46%**（从 CI `coverage.xml` 逐文件复算） | `--fail-under=88`（`ci-adapter.yml:596-601`） | 通过，余量 +4.46pp |
| 前端语句/分支/函数/行 | **87.35% / 81.38% / 82.25% / 88.56%** | 86.4 / 80.4 / 81.1 / 87.6（`vitest.config.mts:302-306`） | 通过，行余量仅 +0.96pp |
| 后端用例 | CI：**4 failed / 2763 passed**；本地无 PG：**1 failed / 2531 passed / 235 skipped** | — | HEAD 红（QA-01） |
| 前端用例 | **71 文件 / 1093 用例全绿** | — | 通过 |
| E2E | CI：**3 failed / 2 passed**；本地带未提交修复单跑 `session-redirect` **1 passed** | — | CI 红，本地部分验证 |

缺口：
- 前端 6 个 workbench 组件 0%：`components/workbench/confirm-bar.tsx`、`declaration-form.tsx`、`file-picker.tsx`、`help-panel.tsx`、`submit-bar.tsx`、`components/workflow/artifact-card.tsx`。
- 后端低覆盖：`queue_depth.py` 52.4%、`wechat` 75.3%、`plugin_review_repo` 76.8%、`main.py` 77.3%、`payment_gateway` 78.1%。
- 本地裸跑 `pytest tests` 因缺 `OA_BATCH` 有 1 条失败（`test_plugin_contract_skeleton.py`），README 已要求设置 `OA_BATCH`（QA-19）。

---

## 3. 已知四项修复复核（本轮实测，不采信自述）

### 3.1 G1 部署形态 —— **已闭环**

| 复核点 | 实测值/证据 | 判定 |
|---|---|---|
| adapter 端口 | `deploy/docker-compose.yml:694-697` → `127.0.0.1:${OA_ADAPTER_PORT:-8000}:8000`；SaaS `:498-501` 同口径 | 闭环 |
| web 端口 | `docker-compose.yml:748-750` → `127.0.0.1:${OA_WEB_PORT:-3000}:3000`；SaaS `:531-533` 同口径 | 闭环 |
| milvus 19530 | `docker-compose.yml:189-193`、SaaS `:154-158` → `127.0.0.1`（含 metrics 9091） | 闭环 |
| 对象存储 8333/admin 23646 | `docker-compose.yml:224-227`、SaaS `:174-178` → `127.0.0.1`；**仓库未发布 8332/8334/8335/8336** | 闭环 |
| Prometheus 9090 | `docker-compose.yml:810-812` → `127.0.0.1` | 闭环 |
| edge 80→443 | `deploy/nginx/edge.conf:38-52`（80 仅探活 + `return 301`）、`:60-69`（443 TLS） | 闭环 |
| 证书外部挂载 | compose 挂载 `./nginx/tls/fullchain.pem`/`privkey.pem`；仓库不内置证书，提供 `deploy/gen-selfsigned-cert.sh` 生成脚本；`deploy/nginx/tls/` 目录不在仓库 | 闭环（部署前置需先建证书，README/脚本已覆盖） |
| 安全响应头 | HSTS / nosniff / DENY / Referrer-Policy 在 80、443 及 5 个 location 全部 `always` 下发；`server_tokens off` | 闭环 |
| k8s 清单可用性 | 2 个应用 Deployment `image@sha256`；2 个 Ingress 均含 `tls.secretName`；NetworkPolicy 存在；`deploy/k8s/README.md:1-25` 明确"V1.1 准备物，未在真实集群端到端验收，不作 V1.0 验收依据" | 部分闭环（语法/结构可用，语义有 QA-02） |

**新增缺口（口径不一致）**：`deploy/k8s/base/configmap.yaml:119-120` 的 `OA_WEB_ADAPTER_BASE_URL=http://localhost:8000`、`OA_WEB_OIDC_ISSUER=http://localhost/realms/officeai` 是运行期 ConfigMap 键；但 web 实际只读 `NEXT_PUBLIC_ADAPTER_BASE_URL`/`NEXT_PUBLIC_OIDC_ISSUER`（构建期固化，`apps/web/Dockerfile:58-60`；compose 在 `docker-compose.yml:734-743` 以 build args 注入）。仅改 ConfigMap **不会生效**，k8s 上线需重新构建镜像并传对应 build args → QA-02。

### 3.2 M1 并发刷新竞态 —— **已闭环**

| 复核点 | 证据 | 判定 |
|---|---|---|
| 前端跨标签单飞 | `apps/web/src/lib/auth-refresh.ts:441-484` 默认通道 = Web Locks 互斥 + BroadcastChannel 结算广播；`refreshShared`（`:504-547`）本标签单飞 + 跨标签复用；无锁环境降级为标签内单飞 | 闭环 |
| 失败不使用空 Cookie 覆盖新令牌 | 前端在服务端未判死前不清 Cookie；`auth-refresh.ts:384` 明确"窗口外迟到者由服务端被取代不清 Cookie + 可重试兜底"；`sso_routes.py:940-977`：`digest in _superseded_tokens` 时返回 `20002 + data.retryable=true` 且**不带清除头** | 闭环 |
| 后端一次性令牌幂等/可重试 | `sso_routes.py:618-667`：同令牌在途请求 `asyncio.shield` 共享同一结果，120s 重放窗口，成功后记 `_superseded_tokens` 1800s | 闭环 |
| 对应用例在册 | 前端 `apps/web/src/lib/auth-refresh-flight.test.ts:274-323`（Web Locks + BroadcastChannel 默认实现、无锁降级）；后端 `apps/adapter/tests/unit/test_sso_refresh_race.py` | 闭环 |

### 3.3 M2 覆盖率阈值 —— **已闭环**

- 提交：`47a8c99 chore(ci): PG 仓储覆盖下限 50→88`。
- 阈值现状：`ci-adapter.yml:557` `--cov-fail-under=85`（G-05 硬地板）；`:596-601` 单步 `--fail-under=88`。
- 实测：总覆盖 94.29%（余量 +9.29pp）；PG 仓储 92.46%（余量 +4.46pp，优于阈值制定时记录的 90.66%）。
- 前端棘轮阈值与实测匹配（最紧的是行覆盖 +0.96pp）。

### 3.4 M3 CI action pin / 取证 —— **pin 已闭环；"有绿结果"未闭环**

| 复核点 | 实测 | 判定 |
|---|---|---|
| action 是否 pin 到 SHA | `uses:` 总数 **38**，SHA 固定 **38**（含 `actions/checkout@11d5960…`、`astral-sh/setup-uv@e58605a…`） | 闭环 |
| CI 是否真跑过 | 是：`CI Adapter` 5 次、`CI Web` 8 次，最新一次均为 HEAD `01b53cd` | 已跑 |
| 是否有绿结果 | **否**：`CI Adapter` 最新 run `41589534839`（workflow_dispatch，9m12s）失败；`CI Web` 最新 run `41588777484` 失败；仅 `Dependency Scan`/`SBOM` 绿 | **未闭环** |
| 注册表校验根因 | 日志：`[FAIL] yaml deploy/k8s/base/ingress.yaml: expected a single document in the stream`、`deploy/k8s/base/web/networkpolicy.yaml: ...`；修复已写工作区：`ci-adapter.yml:652-655` 改 `safe_load_all` 逐文档校验 | 根因已定位，修复未提交 |
| pytest 红因 | `4 failed / 2763 passed`：2 条 `attached to a different loop`（org/usage PG 用例）+ 2 条 `usage_event_pkey` 唯一键冲突；对应 4 个测试文件已改未提交 | 根因已定位，修复未提交 |
| e2e 红因 | `3 failed / 2 passed`：`session-redirect.spec.ts:18` + `workflow.spec.ts:111/115` 三条"未登录 → 回登录页"断言，因探针 `/sso/session` 在测试拓扑恒不可用而停在错误态；工作区已加 `answerNeverLoggedIn` 路由替身（`session-redirect.spec.ts:35,48`），断言未放松；本地单跑该条 **passed** | 修复已写，未提交/未全量验证 |
| Prettier / 清单 `{size}` | Prettier 漂移已由 `1443c61` 修复，本地精确门禁通过；`{size}` 已由 `01b53cd` 改 LF 归一（`gen_delivery_manifest.py:329-330`） | 闭环 |

**判定：M3 的"pin/取证基础设施"闭环，但"CI 绿"未闭环，转为 QA-01（高）。**

---

## 4. 分级问题清单

> 状态定义：**已闭环** = 对应提交/实测证据成立；**仍存在** = 本轮复现；**新增** = 本轮首次记录；**更新** = 旧问题状态/证据变化。

### 高（1 项）

#### QA-01【新增/未闭环】HEAD 上 CI Adapter 与 CI Web 全红，修复全部停留在工作区
- 位置：`.github/workflows/ci-adapter.yml:652-655`（未提交）、`apps/adapter/tests/integration/test_{org_endpoints,org_repo_pg,usage_endpoints,usage_repo_pg}.py`（未提交）、`apps/web/e2e/session-redirect.spec.ts` / `workflow.spec.ts`（未提交）
- 证据：GitHub run `41589534839`（CI Adapter）失败，`41588777484`（CI Web）失败；`4 failed / 2763 passed`、e2e `3 failed / 2 passed`；工作区修复未 commit/push，故上述红与 HEAD 一一对应。
- 影响面：主干不可验证；"修复已写"无法防止回退；任何基于 HEAD 的发布结论不成立。
- 判定依据：CI 日志 + `git status`（第 1.2 节）。

### 中（11 项）

#### QA-02【新增】k8s 运行期 ConfigMap 与 Next 构建期常量错位
- 位置：`deploy/k8s/base/configmap.yaml:119-120`；`apps/web/Dockerfile:58-60`；`deploy/docker-compose.yml:734-743`；`deploy/k8s/README.md:1-25`
- 影响：只改 ConfigMap 无法改变 web 的适配层基址/OIDC issuer，k8s 环境易出现"配置看似改对、浏览器仍请求 localhost/adapter"的登录不可用。
- 判定依据：web 全仓仅读取 `NEXT_PUBLIC_*`（构建期注入）；ConfigMap 的 `OA_WEB_*` 键无消费方。

#### QA-03【仍存在】`list_queued` 无 LIMIT + `SELECT *`
- 位置：`apps/adapter/app/db/repositories/task/postgres.py:438-461`
- 影响：队列积压时单次查询把全部任务（含大字段）读入内存；worker 轮询放大。
- 判定依据：函数签名无分页参数，SQL 为 `SELECT * ...` 且无 `LIMIT`。

#### QA-04【仍存在】ops 租户列表全量拉取后内存分页
- 位置：`apps/adapter/app/business/ops_service.py:216-237`
- 影响：租户规模 O(N) 内存与 DB 拉取；分页仅前端切片，不能下推。
- 判定依据：`tenants = await self._store.list_tenants(...)` 后 `tenants[start:start+page_size]`。

#### QA-05【仍存在】`add_artifacts` 逐条记录独立事务
- 位置：`apps/adapter/app/db/repositories/artifact_repo.py:423-470`（循环 `:436`，`tenant_scope` `:441`）
- 影响：N 条工件 = N 次连接/事务；批量写入延迟与连接池占用线性上升。
- 判定依据：循环体内 `async with tenant_scope(...)`。

#### QA-06【仍存在】租户导出全量 `BytesIO` + ZIP
- 位置：`apps/adapter/app/business/tenant_data_service.py:679-694`
- 影响：大租户导出内存峰值；并发导出可触发 OOM。
- 判定依据：`buffer = io.BytesIO()` 聚合全部数据后打包。

#### QA-07【仍存在】235 个 PG 集成/安全用例本地默认跳过
- 位置：`apps/adapter/tests/integration/*`、`tests/security/*`
- 影响：本地/离线环境无法闭环多租户隔离、PG 仓储语义；仅 CI 覆盖。
- 判定依据：本地 `pytest tests` 输出 `235 skipped`（均提示"需要真实 PostgreSQL：加 --run-pg"）。

#### QA-08【仍存在】mypy 非严格模式
- 位置：`apps/adapter/pyproject.toml:159,169,174`（`strict = false`、`ignore_missing_imports = true`）
- 影响：类型门禁存在但约束弱，Any 逃逸与第三方缺类型不会被拦截。
- 判定依据：配置原文。

#### QA-09【仍存在】前端错误文案直接回传后端 `message`
- 位置：`apps/web/src/lib/business-errors.ts:223-226`
- 影响：后端内部错误/上游错误文案可直达用户，存在信息泄露与文案失控。
- 判定依据：`return error.message || ...`、`if (error instanceof Error && error.message) return error.message`。

#### QA-10【仍存在】上传类型仅按扩展名判定
- 位置：`apps/adapter/app/services/rag_service.py:594-599`（大小限制在 `apps/adapter/app/api/v1/rag.py:172-192`）
- 影响：改扩展名可绕过类型白名单；下游解析器暴露面扩大。
- 判定依据：`extension = file_name.rsplit('.', 1)[-1]` + `allowed_document_extensions`，未做 magic/MIME 嗅探。

#### QA-11【新增】项目无 LICENSE；后端依赖许可证元数据缺口
- 位置：仓库根（无 `LICENSE`）；后端 53 个包中 42 个 METADATA 无 `License`/`License-Expression`
- 影响：对外分发/私有化交付缺少授权依据；依赖合规扫描（pip-licenses）虽有 CI，但元数据缺失使结论不完整。
- 判定依据：`Test-Path LICENSE` = False；METADATA 解析统计。

#### QA-12【新增】分支保护不可取证，CI 红不阻断合并无法排除
- 位置：GitHub 仓库设置（API 返回 403：`Upgrade to GitHub Pro or make this repository public`）
- 影响：即使 CI 继续红，也可能直接合入；与 QA-01 叠加放大风险。
- 判定依据：`gh api .../branches/main/protection` 403。

### 低（10 项）

#### QA-13【仍存在】前端覆盖率阈值余量 < 1pp
- 位置：`apps/web/vitest.config.mts:302-306`
- 实测 87.35/81.38/82.25/88.56 vs 86.4/80.4/81.1/87.6；行余量 +0.96pp。任一低覆盖新增即红。

#### QA-14【仍存在】6 个 workbench 组件 0% 覆盖
- `components/workbench/{confirm-bar,declaration-form,file-picker,help-panel,submit-bar}.tsx`、`components/workflow/artifact-card.tsx`。核心交互（二次确认、声明表单、文件选择、提交）无测试。

#### QA-15【更新】npm 16 个 moderate（全为开发链），CI 只拦 high
- `dependency-scan.yml:252` `npm audit --audit-level=high`；生产依赖 0 漏洞。上一轮"0 漏洞"表述不准确（应为"生产 0、开发 16 moderate"）。

#### QA-16【仍存在】镜像摘要口径不一致
- compose 51 个 `image:`、0 个 `@sha256`；k8s 2 个应用镜像固定 digest；`images.lock` 20 条。compose 侧仍可被 tag 漂移影响。

#### QA-17【更新】README 测试/覆盖率数字滞后
- `README.md:23-24,235-236` 仍写 2343/2211、91.94%/85.87%；本轮实测 CI 2763、94.29% 行、前端 1093。

#### QA-18【新增】3 条历史 stash 滞留
- `stash@{0}` 自带 `SyntaxError:505 ruff errors，勿直接 apply`；`stash@{1/2}` 为 develop 半成品。存在误 apply 风险。

#### QA-19【新增】本地裸 `pytest tests` 有 1 条环境性失败
- `tests/integration/test_plugin_contract_skeleton.py::test_d1_deferred_registry_matches_markers` 需 `OA_BATCH`；本地未设置即 `1 failed`。README 已写入前置，但裸跑仍不友好。

#### QA-20【更新】`'use client'` 泛化与超大文件
- **89/93** 非测试 `.tsx` 为客户端组件（上轮 81/93，本轮新增 8 个）；后端 13 个 >800 行（最大 1529）；前端 4 个 >500 行（最大 1890）。渲染开销与维护成本。

#### QA-21【仍存在】SaaS 编排无资源上限；回滚演练证据缺失
- `docker-compose.saas.yml` 0 个 `mem_limit`/`cpus`；`docs/drills/` 不存在；`deploy/migrations/rollback/` 有 7 个 down 脚本但无演练记录。

#### QA-22【新增】前端包体无预算门禁
- `.next/static/chunks` JS 合计 1,334 KB（未压缩）；构建成功（3.7s、20 个静态页），但无 first-load 预算/体积回归门禁。

---

## 5. 与 2026-11-02 轮次的差分表

上一轮统计：高 1 / 中 16 / 低 13（共 30）。本轮：高 1 / 中 11 / 低 10（共 22）。

| 上轮项 | 本轮状态 | 证据/说明 |
|---|---|---|
| G1 部署形态（上轮唯一"高"） | **已闭环** | `23e9f69`；端口回环、80→443、TLS 外挂、安全头、k8s 清单（V1.1 定位）；新增 QA-02 语义口径 |
| M1 并发刷新竞态 | **已闭环** | `83cec37`；Web Locks + BroadcastChannel + 服务端幂等窗口/被取代不清 Cookie + 前后端用例 |
| M2 覆盖率阈值（50→88） | **已闭环** | `47a8c99`；实测 PG 92.46% ≥ 88，总 94.29% ≥ 85 |
| M3 action pin | **已闭环** | `7c20647`；38/38 SHA |
| M3 "CI 有绿结果" | **未闭环 → QA-01（高）** | Adapter/Web 最新 run 均红；修复未提交 |
| M3 注册表校验红 | 根因定位，修复未提交 | k8s ingress/networkpolicy 多文档；`safe_load_all` 已写工作区 |
| M3 e2e 3 红 | 修复未提交 | 路由替身；本地单条通过 |
| M3 Prettier 漂移 / `{size}` CRLF | **已闭环** | `1443c61` / `01b53cd`（LF 归一） |
| M4 租户导出内存 | 仍存在 → QA-06 | 代码未变 |
| M5 ops 全量列表 | 仍存在 → QA-04 | 代码未变 |
| M6 `list_queued` 无 LIMIT | 仍存在 → QA-03 | 代码未变 |
| M7 `add_artifacts` 逐条事务 | 仍存在 → QA-05 | 代码未变 |
| P1-06 `use client` 81/93 | 更新 → QA-20 | 本轮复测 **89/93**（新增 8 个） |
| P1-13 workbench 0 覆盖 | 仍存在 → QA-14 | 6 个文件 0% |
| P1-15 阈值余量薄 | 仍存在 → QA-13 | 行余量 +0.96pp |
| P1-16 235 PG 跳过 | 仍存在 → QA-07 | 本地 235 skipped |
| P1-17 弱默认账号 | 降级 → QA-21 附注 | `validate_deploy` 已阻断生产使用 |
| P1-18 7 个回滚 | 仍存在 → QA-21 | 有脚本无演练 |
| P1-19 缺演练证据 | 仍存在 → QA-21 | `docs/drills/` 不存在 |
| P1-21 npm 过期 | 更新 → 2.5 节 | 16 项/8 大版本（上轮 14 项/8） |
| P2-03 mypy 非严格 | 仍存在 → QA-08 | `strict=false` |
| P2-07 错误文案直传 | 仍存在 → QA-09 | `business-errors.ts:223-226` |
| P2-16 action pin | **已闭环** | 38/38 |
| P2-18 `now_iso` 7 份 | 仍存在 → QA-20 附注 | 7 份拷贝 |
| L5 compose 无 digest | 仍存在 → QA-16 | 0 个 `@sha256` |
| M8 许可证"无 GPL" | 更新 → QA-11/QA-15 | LGPL 14 个为可选 sharp；但项目无 LICENSE、后端元数据 42/53 缺失 |
| — | **新增 QA-02** | k8s ConfigMap 与 Next 构建期常量错位 |
| — | **新增 QA-12** | 分支保护无法取证（403） |
| — | **新增 QA-17/18/19/22** | README 滞后、历史 stash、裸跑缺 OA_BATCH、包体无预算 |

---

## 6. 问题统计

| 级别 | 数量 | 编号 |
|---|---|---|
| 高 | **1** | QA-01 |
| 中 | **11** | QA-02 ~ QA-12 |
| 低 | **10** | QA-13 ~ QA-22 |
| 合计 | **22** | — |

按状态：22 项中，新增 7 项（QA-01、QA-02、QA-11、QA-12、QA-18、QA-19、QA-22）、更新 3 项（QA-15、QA-17、QA-20）、仍存在 12 项（QA-03~QA-10、QA-13、QA-14、QA-16、QA-21）。另有 4 项旧问题已闭环、不计入上述 22 项（G1、M1、M2、M3-pin）。

---

## 7. 按优先级的改进建议与验收口径

### P0（阻断发布，先做）

1. **提交并验证 M3 全部工作区修复（QA-01）**
   - 动作：按工作线拆成 2~3 个提交（CI 注册表校验；PG 用例隔离；e2e 探针夹具），推送到远端后各触发一次 `CI Adapter`、`CI Web`。
   - 验收口径：**同一 commit SHA** 上两个 workflow `conclusion=success`；注册表 job 无 `[FAIL] yaml`；pytest `0 failed`（预期 2767 passed；含 PG）；e2e `5 passed / 0 failed`（或按凭据条件 `3 passed / 2 skipped / 0 failed`）。
   - 补充：把上一轮报告/归档一并提交，消除"报告自身未提交"（第 1.2 节）。

2. **明确合并门禁（QA-12）**
   - 动作：在仓库设置开启 main 分支保护（或至少把 CI 作为合并前置条件并写入 README 流程）。
   - 验收口径：`gh api repos/.../branches/main/protection` 返回必需检查列表；故意红一次验证无法合并。

### P1（下一批必须收口）

3. **修正 k8s 配置口径（QA-02）**
   - 动作：从 ConfigMap 删除无消费方的 `OA_WEB_ADAPTER_BASE_URL`；把 `OA_WEB_OIDC_ISSUER` 改为构建期参数并在 k8s README 写明 `docker build --build-arg`；或在 web 运行期改为读取服务端变量（二选一，不能两套并存）。
   - 验收口径：`validate_config_names.py` 增加"ConfigMap 键必须有消费方"检查；k8s README 的部署步骤能在空环境按文档产出可用 Pod（本地 kind/minikube 冒烟）。

4. **性能四项（QA-03~QA-06）**
   - `list_queued` 加 `LIMIT`/游标 + 只取必要列；
   - `list_tenants` 分页下推到仓储（`LIMIT/OFFSET` + `count`），服务层不再切片；
   - `add_artifacts` 单事务批量插入（一个 `tenant_scope` 内 executemany/批量 upsert）；批次设上限；
   - 导出改为临时文件/流式 ZIP，限制单次导出行数与并发。
   - 验收口径：10k 任务/10k 租户/1k 工件的基准测试记录（P95 延迟、峰值内存、事务数）；导出 1GB 数据峰值内存 < 容器上限的 50%。

5. **测试缺口（QA-07/13/14）**
   - 为 6 个 0 覆盖组件补交互测试；把前端覆盖率余量拉到 ≥2pp（或按新增代码覆盖率门槛）。
   - 提供 `make test-pg`（docker compose 起 PG）让 235 条本地可跑；CI 保留 nightly 全量。
   - 验收口径：workbench 组件行覆盖 ≥80%；`npm run test -- --coverage` 余量 ≥2pp；本地 `make test-pg` 0 skipped。

### P2（质量与合规）

6. **类型与错误面（QA-08/09/10）**
   - mypy 逐步 `strict=true`（按模块白名单推进）；前端仅对白名单业务码映射文案，未知 message 落统一兜底；上传增加 magic/MIME 嗅探与解析器白名单。
   - 验收口径：mypy 严格模式覆盖 `app/business` 与 `app/db`；错误文案快照测试；改扩展名样本被 415/422 拒绝。

7. **许可与依赖（QA-11/15/16）**
   - 增加根 `LICENSE`；后端依赖以 `License-Expression` 补齐或维护豁免清单；npm 开发链升级到修复 minimatch 的版本（或将 audit 阈值改为 moderate 并接受基线）；compose 关键镜像改 digest。
   - 验收口径：`pip-licenses` 输出中 UNSPECIFIED ≤5%（当前 79%）；`npm audit --omit=dev` 0；`npm audit` 0 high/critical；compose `@sha256` 覆盖率 100%（基础设施镜像）。

### P3（整洁度）

8. **QA-17~22**：README 数字改为"由 CI 产物生成/引用 SHA"；清理/归档 3 条 stash（先人工 diff 确认）；README 明确 `OA_BATCH` 与 `make test` 前置；前端加 first-load 预算门禁；SaaS 编排补 `mem_limit`/`cpus`；补 `docs/drills/` 回滚演练记录。

---

## 8. 量化附表

### 8.1 代码规模与结构

| 指标 | 数值 |
|---|---|
| 跟踪文件 | 878 |
| 后端 `.py` / 非测试源码行 | 428 / ~67,487 |
| 前端 `.ts`+`.tsx` / `src` 行 | 194 / ~42,510 |
| 前端非测试 `.tsx` / `'use client'` | 93 / 89 |
| `docs/` 跟踪文件 | 111 |
| 后端 >800 行文件 / 最大 | 13 / 1,529 |
| 前端 >500 行文件 / 最大 | 4 / 1,890 |
| `now_iso` 重复实现 | 7 |
| 后端代码 TODO/FIXME | 0 |

### 8.2 门禁与测试

| 指标 | 数值 |
|---|---|
| ruff / ruff format / mypy / eslint / tsc / prettier | 6/6 通过 |
| `deploy/validate_*.py` 实跑 | 10/10 EXIT=0 |
| 后端 CI 用例 | 2763 passed / 4 failed |
| 后端本地用例（无 PG） | 2531 passed / 1 failed / 235 skipped |
| 前端用例 | 1093 passed / 71 files |
| 后端覆盖率（行/分支） | 94.29% / 84.30% |
| PG 仓储覆盖率 | 92.46% |
| 前端覆盖率（语/分/函/行） | 87.35% / 81.38% / 82.25% / 88.56% |
| E2E（CI / 本地单条） | 3 failed, 2 passed / 1 passed |

### 8.3 依赖与安全

| 指标 | 数值 |
|---|---|
| 后端过期依赖 | 11（全 minor/patch） |
| 前端过期依赖 | 16（8 个大版本） |
| npm 漏洞（dev / prod） | 16 moderate / 0 |
| 前端运行时依赖许可证 | MIT 119、Apache-2.0 22、LGPL 14（可选 sharp）、ISC 8、CC-BY-4.0 1、BSD-3 1、0BSD 1、UNLICENSED 1、UNSPECIFIED 1 |
| 后端许可证元数据缺失 | 42/53 |
| CI action 引用 / SHA pin | 38 / 38 |
| compose image / `@sha256` | 51 / 0 |
| k8s 应用镜像 digest | 2 |
| `images.lock` 条目 | 20 |

### 8.4 部署端口（G1 实测）

| 服务 | compose 私有化 | compose SaaS | 判定 |
|---|---|---|---|
| milvus 19530 / metrics 9091 | `127.0.0.1`（:189-193） | `127.0.0.1`（:154-158） | 闭环 |
| 对象存储 8333 / admin 23646 | `127.0.0.1`（:224-227） | `127.0.0.1`（:174-178） | 闭环 |
| adapter 8000 | `127.0.0.1`（:694-697） | `127.0.0.1`（:498-501） | 闭环 |
| web 3000 | `127.0.0.1`（:748-750） | `127.0.0.1`（:531-533） | 闭环 |
| Prometheus 9090 | `127.0.0.1`（:810-812） | — | 闭环 |
| edge 80/443 | `0.0.0.0` 发布（:606-610） | `0.0.0.0` 发布（:423-427） | 闭环（对外入口） |
| lite 代理 80/443 | — | — | `docker-compose.lite.yml:607-610` 发布；Dify 3001 回环（:650-653） |
| 8332/8334/8335/8336 | 未发布 | 未发布 | 闭环（不暴露） |

---

## 9. 审计局限与复现说明

- k8s 未做真实集群 `kubectl apply`（userId 环境无集群；README 亦声明 V1.1 前不验收）；结论仅基于清单静态结构与门禁。
- 分支保护 API 受 GitHub 套餐限制返回 403，无法取证必需检查。
- `npm outdated` 首次输出 16 项；随后复测因 npm 缓存/网络波动返回空集，关键版本已用 `npm view` 单独复核（见 2.5）。
- 后端全量 PG 覆盖率取自最新 CI 工件 `coverage.xml`（HEAD `01b53cd`）；本地无 PG 仅验证非 PG 用例。
- 本轮未修改任何产品代码/配置；工作区仅新增本报告。审计用临时目录已清理。

---

## 10. 整改状态（2026-11-03 批次执行后）

> 本节是**执行后状态**，不覆盖第 1-7 节的历史取证；整改动作与证据见
> `docs/remediation-2026-11-03.md`。批次 1 的 CI 双绿为同一 SHA `e122537`
> （CI Adapter run 41609501419 / CI Web run 41609507565，均 success）。
> 批次 2/3 的提交在 `e122537` 之后，CI 结论见整改文档的最终记录。

| 编号 | 级别 | 整改状态 | 证据 |
|---|---|---|---|
| QA-01 | 高 | **已闭环** | 5 个提交（`57f0429`/`ce1b14c`/`f37d091`/`585d35f`/`e122537`）；同 SHA 双绿 |
| QA-02 | 中 | **已闭环** | `3c91218`：ConfigMap 移除无消费方的 `OA_WEB_*`；README §5.7 写明 build-arg |
| QA-03 | 中 | **已闭环** | `d616022`：`list_queued(limit)` 下推 `LIMIT`，内存/PG 双用例 |
| QA-04 | 中 | **已闭环** | `8f02a3b`：`page_tenants` 的 `LIMIT/OFFSET + COUNT` 下推存储层 |
| QA-05 | 中 | **已闭环** | `41b0e2b`：同租户产物单事务批量写入 |
| QA-06 | 中 | **已整改（含残留口径）** | `0538a95`：ZIP 溢写磁盘；`bytea` 存储契约决定的单份 `bytes` 已写明 |
| QA-07 | 中 | 已闭环（文档） | `9cb6140`：`--run-pg` 四前置条件 + `skipped == 0` 验收口径 |
| QA-08 | 中 | 未动（登记） | mypy `strict=false` 属类型债，独立批次 |
| QA-09 | 中 | 未动（登记） | 超大文件拆分，重构级 |
| QA-10 | 中 | 未动（登记） | 上传类型仅按扩展名判定，需内容嗅探，独立安全批次 |
| QA-11 | 中 | **已闭环** | `94df114`：根目录 `LICENSE`（私有保留权利） |
| QA-12 | 中 | **阻塞（平台套餐，非权限）** | 账号对仓库 `viewerPermission=ADMIN`、令牌含 `repo`；`GET protection` 与 `GET rulesets` 均返回 `Upgrade to GitHub Pro or make this repository public…`（403），`branches/main` 为 `protected=false`。私有 Free 仓库不支持分支保护，需用户升级 Pro/转 public，原文与端点已记入整改文档「QA-12 403 成因取证」 |
| QA-13 | 低 | **已闭环** | `51af723`：行覆盖 90.98（目标 ≥89） |
| QA-14 | 低 | **已闭环** | `51af723`：6 个 workbench 组件由 0% 升至 92% 行覆盖 |
| QA-15 | 低 | **已评估，不升级** | 16 moderate 同源 `minimatch<11.0.1`，仅开发链；最新 eslint 仍未升级，理由已记录 |
| QA-16 | 低 | 已闭环（此前） | 38 处 action pin + 镜像 digest |
| QA-17 | 低 | **部分闭环** | 本次相关 `72→68` 已改；其余文档陈旧数字登记 |
| QA-18 | 低 | **已闭环** | `94df114`：UNKNOWN 许可证显式灰名单 + 基线 42/上限 45 |
| QA-19 | 低 | **已闭环** | `9cb6140`：`OA_BATCH` 默认 B21，裸跑 0 failed |
| QA-20 | 低 | 未动（登记） | `'use client'` 89/93，重构级 |
| QA-21 | 低 | 保持（门禁拦截） | `.env.example` 弱默认由部署门禁阻断 |
| QA-22 | 低 | 未做（登记） | 前端包体预算无基线，需先测量再定阈值 |
