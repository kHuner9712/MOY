# Moy（麦子）全仓审计报告（第二轮 · 工作区实况基线）

| 项 | 内容 |
| --- | --- |
| 报告编号 | MOY-AUDIT-2026-10-24-R2 |
| 审计时间 | 2026-10-24 19:50–20:40 |
| 审计基线 | `main@bdba346` + **工作区未提交增量**（40 个已修改文件、40+ 新增未跟踪文件，属 W1「总纲对齐」进行中） |
| 审计范围 | `apps/adapter`（150 源文件 / 69,770 行 + 148 测试文件 / 56,581 行）、`apps/web`（102 个 ts/tsx / 21,033 行）、`deploy/`（3 套 compose、10 个迁移、11 个校验器）、`docs/`（62 份 md / 25,928 行）、`scripts/`、`sql/`、`.github/workflows/` |
| 审计类型 | **只读审计**：未修改任何业务代码、测试或配置 |
| 分工 | 高见远（架构/依赖/配置）、寇豆码（代码质量/安全/重复）、严过关（测试/CI/交付）、许清楚（产品/文档一致性）、主理人齐活林（独立复核 + 汇编） |
| 上一轮对比 | `docs/audit-2026-10-24-full-repo-audit.md`（基线 `51446c2`；2026-10-29 审计清理已删除，结论并入 `docs/audit-2026-10-24-full-repo-audit-r2.md`（本文件）） |

> **重要说明**：本报告所有结论均经主理人二次复核，且包含**对自身三次误判的更正**（详见 §4.5「复核纠错记录」）。团队初稿中有 3 条"高危"经复核**不成立**，主理人自身有 3 条测错（其中 1 条是口径错配，直接影响了 P0 判定），均已从结论中剔除或降级——这正是本轮审计要求"每条必须可复现"的原因。

---

## 一、总体结论

> **工程底盘在国内同类项目中属于上游水平，但「最后一百米」没有闭环：**
> 生产登录走不通、离线包装不起来、迁移无人执行、备份恢复无演练、覆盖率低于自家门禁。
> 这些不是"写得糙"，而是**验证链条断在最后一环**——所有质量结论都建立在"本机跑过一次"，而不是"交付物可复现"。

| 维度 | 得分 | 一句话 |
| --- | --- | --- |
| 架构分层与工程纪律 | 8.5 | 唯一出口 + 唯一协议转换点 + 门禁文化，罕见地克制 |
| 代码可读性与自解释 | 8.0 | docstring 解释"为什么"而非"是什么"；但注释体量已开始拖累信噪比 |
| 安全（多租户/鉴权） | 7.5 | 三层防御设计到位，SEC-01/02 已实修；残余风险在内存态与降级开关 |
| 测试 | 6.5 | 1,892 passed / 0 failed、无伪测试、安全三类攻击面全覆盖；但门禁数字本地不可复现（口径依赖 PG），且 4 个业务核心模块无库覆盖率仅 30–40% |
| 交付就绪 | 3.5 | 登录 / 迁移 / 离线包 / 升级回滚四件套均未闭环，私有化现场无法签收 |
| 文档一致性 | 4.0 | 文档量与代码量 1:3.7，但 README 数字落后两个阶段，权威基线未入库 |

**一句话判定**：**可作为「技术尽调可展示」的工程样板；不可作为「可交付客户」的 V1.0 产品。**

---

## 二、实测证据（本次亲自运行）

| 命令 | 退出码 | 结果 | 与文档声称 |
| --- | --- | --- | --- |
| `ruff check .` | 0 | All checks passed | 一致 |
| `ruff format --check .` | 0 | 298 files already formatted | 一致 |
| `mypy app` | 0 | 150 source files, no issues | 一致 |
| `pytest tests --collect-only` | 0 | **2,095 用例收集** | README「1,573」不一致 |
| `pytest tests/unit` | 0 | **1,309 passed**（49.7s） | — |
| `pytest tests/{integration,security,e2e,qa}`（`OA_BATCH=B21`，无 PG） | 0 | **583 passed / 203 skipped / 0 failed**（325s） | 一致 |
| 合计（拆分实跑） | 0 | **1,892 passed / 203 skipped / 0 failed** | ✅「0 failed」成立 |
| `pytest tests` 单进程全量 | **1** | 用例全绿，但退出码 1：会话结束时临时目录清理钩子（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`）打断汇总，**非用例失败** | ⚠️ 环境层噪声 |
| `coverage report` / `coverage json`（`.coverage` 时间戳 16:10） | 0 | 行覆盖 **81.69%**（14,785/17,657）；**分支 2,609/3,636 = 71.75%**，低于自定 76.6% 门槛 | ❌ 双项低于门禁；README「90.78% / 80.89%」 |
| `deploy/validate_openapi.py` | 0 | core30 + phase2·38 + phase3·16 + phase4·10 + w1·2 = **96** | README「84」不一致 |
| `check_error_catalog.py --strict` | **0** | 66 码三处一致，G-13 通过 | ✅ 上轮"门禁为红"已修复 |
| `deploy/validate_deps.py` | 0 | 17 条运行时依赖协议合规 | 一致 |
| `deploy/validate_secrets.py` + `--self-test` | 0 / 0 | 无明文兜底；负样本可红 | 一致 |
| `scripts/gen_delivery_manifest.py --check` | **1** | stderr `[ERR] 清单内容与生成结果不一致`；门禁本身行为正确 | ⚠️ 但**未接入任何 CI**（workflows 中 0 处引用） |
| `app.openapi()` | — | 81 paths / 99 operations；`securitySchemes` **为空**；`servers` 为空；`X-Tenant-Id` 声明 **0 处**；`Idempotency-Key` 声明 **42 处** | 见 B-07 |
| 环境变量实测 `OA_KEYCLOAK_URL=...` | — | `keycloak_base_url` 仍为默认值（**变量无效**） | ❌ 见 B-01 |

**未验证范围（不得据此判定通过）**：Docker 全栈未启动；备份/恢复未实跑；离线安装未实跑；5 个连接器、微信支付、真实模型密钥均无联调；CI 从未在 GitHub 上执行；**203 条 PG 用例因本机无测试库被 skip，其断言未被验证**。

---

## 三、项目整体理解

### 3.1 定位与目标

Moy（麦子）是**跨生态、可私有化的「工作伙伴」型 AI 平台**，V1.0 首批落地企业办公。差异化三条：跨生态中立、私有化可交付、岗位级场景插件。

技术上「**搭积木 + 自有核心**」：以 Dify（Apache-2.0）提供 AI 原子能力，自研统一适配层屏蔽底座差异，向上输出自研标准接口，承诺「换底座只改适配层，上层零改动」。

### 3.2 规模与形态

| 维度 | 实测 |
| --- | --- |
| 版本控制 | 549 个跟踪文件；`main` 单分支（README 声称有 `develop`，实际只有 `main`） |
| 后端 | `app` 150 源文件 / 69,770 行；13 个文件 >1,000 行（最大 `billing_repo.py` 2,272 行） |
| 测试 | 148 文件 / 56,581 行；测试:源码 ≈ 0.8:1 |
| 前端 | 21,033 行 ts/tsx；20 个页面；7 个自研扫描门禁 |
| 数据库 | phase2/3/4 建表脚本共建 44 张表；10 个迁移 + 6 个回滚 |
| 部署 | 3 套编排：完整 45 个 `OA_*` / SaaS 37 / 轻量 30 |
| 对外接口 | 96 个业务端点（81 path / 99 operation） |

### 3.3 分层与模块职责（实测，与架构文档一致）

```
apps/web ──HTTP──▶ /api/v1/*（统一 {code,message,data}）
                      │
              middleware：Trace → AccessLog → Metrics → CORS
                      │
                  api/v1/*（18 个路由模块，只做校验与编排）
                      │
               business/*（业务服务，唯一写业务规则的地方）
                      │
           db/repositories/*（22 个仓库，19 个 InMemory + SQL 双实现，18,395 行）
                      │
      services/*（唯一协议转换）──▶ clients/dify_client.py（唯一底座出口）──▶ Dify
```

- **api 层零越界**：实测 `api → clients` / `api → db` 直接引用为 **0**。
- **唯一出口纪律成立**：`clients/` 只有 `dify_client.py`。
- **模块级循环依赖 4 组**：`business.policy_service ↔ core.security`、`business.order_service ↔ business.subscription_service`，以及与 `db.repositories.role_repo` 的三角（靠函数内延迟导入绕过，属可接受折衷，但应登记 ADR）。

### 3.4 核心功能（按域）

| 域 | 端点 | 说明 |
| --- | --- | --- |
| 模型 `/model` | 10 | 对话/流式/能力档位/会话历史/我的记忆（技术黑盒化：只暴露档位名） |
| 知识库 `/knowledge` | 10 | 库/文档/索引/带引用检索 |
| Agent `/agent` | 7 | 任务创建/状态/步骤/进度；W1 新增 `/capabilities` 能力推荐 |
| 工具 `/tools` | 7 | 类型注册表、连接授权、执行、调用记录 |
| 身份 `/identity` | 13 | 令牌校验、资料、组织、角色、审计 |
| 计费 `/billing` | 8 | 订阅、订单、用量、支付回调（唯一免鉴权业务端点） |
| 运营 `/ops` | 8 | 平台侧租户/订单/指标/设置 + 租户数据权利 TD1/TD2 |
| 插件 `/plugins` | 13 | 市场、安装、配置、执行、产物 |
| 统计 `/stats` | 1 | W1 新增工作流任务统计 |

插件层：6 份声明（3 工作台 + 3 轻量），声明式 8 层校验、6 项能力封闭集合。

### 3.5 关键流程与调用关系

**任务主链**

```
POST /api/v1/agent/tasks
  → IdempotencyManager（指纹 + 24h 复用）
  → 鉴权 AuthContext(tenant/user/roles) → require_permission
  → task_service.create（task_repo 落库，queued）
  → job_runner（后台轮询，OA_BACKGROUND_JOBS_ENABLED）
  → task_service._advance_record → agent_service（本地桩 / dify_client）
  → running → succeeded/failed → artifact + usage_event
  → GET /agent/tasks/{id}（SEC-01 修复后：租户复合键，未命中即 12001，fail-closed）
```

**鉴权链**：`Bearer JWT` → `verify_token`（stub/oidc 双模式）→ `resolve_tenant`（`X-Tenant-Id` 与声明交叉校验 + 成员关系）→ `require_permission`（角色矩阵 + 2 条对象级白名单）→ 数据层 RLS 兜底。

**多租户三层防御**（令牌声明 → 应用层 `tenant_id` 过滤 → PG RLS + `NOBYPASSRLS` 自检）是全仓最扎实的部分。

### 3.6 依赖与配置

- **后端 27 条依赖**，协议全部在白名单；但写法是 `>=x,<y` 区间而非精确 pin，与"所有开源组件用精确标签"的自定策略不一致。
- **前端 20 条依赖全部 `^` 浮动**，无 `apps/web/package-lock.json`（依赖根 workspace 锁），同一提交在不同时间安装可能得到不同依赖树。
- **基础设施镜像 digest 锁定**（`images.lock`）执行到位。
- **环境变量 `OA_` 前缀统一**，三份样例齐备，无明文弱默认（`CHANGE_ME_*` + 启动期强校验）。

---

## 四、问题清单

> **P0** = 阻塞商用交付或造成不可用；**P1** = 影响可信度/可维护性，交付前应修；**P2** = 技术债。
> **已实测** = 本次亲自复现；**确认** = 代码级证据充分；**待验证** = 依赖部署环境。

### 4.1 P0（5 项）

| ID | 问题 | 证据 | 状态 |
| --- | --- | --- | --- |
| **A-01** | **生产登录链路两处断裂**：① OIDC 回调页是"诚实占位"，明确不兑换授权码；② 登录页把令牌写入 `localStorage`，但 `setAccessToken()` 全仓仅以 `null` 调用，令牌**从未回灌**到 `api-client`（写入点 1 处、读取点 0 处）。结果：`auth_mode=oidc` 下所有请求不带 `Authorization` | `(auth)/auth/callback/page.tsx:13-24`；`login/page.tsx:131`；`use-session.ts:52,59`；`api-client.ts:218` | **已实测** |
| **A-04** | **私有化交付运维闭环缺失**：① `install.sh --offline` 要求 `deploy/offline/images/*.tar`，该目录**不存在**（`offline/` 下只有一个 181KB 插件包）；② **无 `upgrade.sh` / `rollback.sh`**，而 `architecture-phase4.md` 已写明回滚范围与维护闸门——文档承诺了、脚本没交付；③ `restore.sh` 从未实跑，且无迁移兜底。§6.5「一键部署 + 纯离线运行」不成立 | `install.sh:569-592`；`ls deploy/*.sh`；`architecture-phase4.md` | **已实测** |
| **A-03** | **5 个连接器全为桩**（`IMPLEMENTATION_MODE="demo"`，5/5 不发起真实调用），而 `plan-v1.0.md §6.1` 把"5 款工具全链路联调"列为**一票否决项**。当前连"L1 打通"都不成立 | `app/tools/*_connector.py`；`plan-v1.0.md:376` | 确认 |
| **A-05** | **迁移无生产执行者**：`db_auto_migrate` 默认 `false`，三套 compose 均未开启；带 `pg_isready` 的 `migrate` 服务只存在于 `lite.yml`。完整版/SaaS 部署后需人工执行迁移，漏做则服务连到空库 | `config.py:173`；`lite.yml:409-424`；`compose.yml` 无 migrate 服务 | **已实测** |
| **A-06** | **治理信号失真两连**：① 交付清单门禁 `gen_delivery_manifest.py --check` 行为正确（exit 1，当前确实不一致），但**未接入任何 CI**（`.github/workflows/` 中 0 处引用）——门禁存在却没人按开关；② 权威基线 `docs/moy-project-charter-v1.0.md`、决策 `0005/0006`、指标定义等 **20+ 份关键文档全部未入库**，新人 clone 即缺权威基线 | 实测 exit 1；`grep gen_delivery_manifest .github/workflows` = 0；`git ls-files` | **已实测** |

### 4.2 P1（13 项）

| ID | 问题 | 证据 | 状态 |
| --- | --- | --- | --- |
| **B-01** | **配置名漂移**：三套 compose 注入 `OA_KEYCLOAK_URL`，代码字段是 `keycloak_base_url`（需 `OA_KEYCLOAK_BASE_URL`），无 alias。实测设 `OA_KEYCLOAK_URL` **无效**，只因默认值恰好相同而"看起来能用" | `config.py:139`；`compose*.yml:425/376/476` | **已实测** |
| **B-02** | **备份范围不全 + 恢复无迁移兜底**：`backup.sh` 只 dump 业务库 `officeai` 一个库，Keycloak（realm/client/user）与 Dify（应用/密钥）两个库不在备份内；`restore.sh` 无 `alembic`/迁移调用，恢复后依赖人工 | `backup.sh:223-243`；`restore.sh` | 确认 |
| **B-03** | **三套 compose 手工同步已漂移**：完整 45 / SaaS 37 / 轻量 30 个 `OA_*`（差 8 与 15 个），且无一致性门禁；改一处要改三处 | `deploy/docker-compose*.yml` | **已实测** |
| **B-04** | **支付回调验签与微信 APIv3 结构不兼容**：自造 `HMAC-SHA256(api_v3_key, 自定义串)`，而 v3 要求平台证书 RSA 验签 + AES-256-GCM 解密 resource；且 `trade_state` 缺省时按 **success** 处理；下单接口本身也是桩（返回 `pay.invalid` 收银台地址） | `payment_gateway.py:195-299` | 确认 |
| **B-05** | **生产 CORS 硬编码为空**：`allow_origins=[] if env!=development`，无环境变量可配。任何独立前端源站（含 SaaS 多域名）都无法调用 | `main.py:222-229` | 确认 |
| **B-06** | **生产暴露 OpenAPI 文档**：`/docs` `/redoc` `/openapi.json` 全环境开放；nginx 只挡了 `/readyz` 与 `/metrics` | `main.py:206-208`；`nginx/lite.conf` | 确认 |
| **B-07** | **OpenAPI 契约缺公共层**：`securitySchemes` 为空、`servers` 为空、`X-Tenant-Id` 声明 0 处（`Idempotency-Key` 已有 42 处）。响应体本身**已类型化**（96/96 有 `$ref`，无 `Any`），但缺鉴权与公共头声明，无法直接生成可用客户端 | `app.openapi()` 实测 | **已实测** |
| **B-08** | **前端零自动化测试**：21k 行无 vitest/playwright，仅靠 lint/tsc/自研扫描兜底；而 A-01 的破点恰好落在这两块无测试的代码上 | `apps/web/package.json` | 确认 |
| **B-09** | **内存模式让 RLS 成为测试盲区**：`business_store=memory` 下无事务、无 `set_config('app.tenant_id')`，隔离只剩 `WHERE tenant_id=?`；本地与 CI 都验不到 RLS 断点 | `db/session.py:15,39-42` | 确认 |
| **B-10** | **门禁自检能力不对称**：11 个校验器里只有 5 个有 `--self-test`（web 6 个都有）；`validate_images.py --self-test` 全通过却 **exit 0**（反向语义）；`validate_openapi.py`/`validate_deps.py` 干脆没有自检入口，无法证明"改坏一行会变红" | `deploy/validate_*.py` | **已实测** |
| **B-11** | **缺陷到期时钟可自拨**：`.github/workflows/ci-adapter.yml:75` 硬编码 `OA_BATCH: B21`。批次是"缺陷到期"的判据，写死等于让被审对象自己决定什么时候到期；且 CI 仍带 `--allow-missing-phase2`（阶段二已交付，白名单该撤） | `ci-adapter.yml:75` | **已实测** |
| **B-12** | **PG 用例标记漂移**：203 条 skip 中应正好等于 `@pytest.mark.pg` 标记数，实测标记集合 186 条，**17 条未标记**（靠 `pg_requires` fixture 暗跳）；覆盖率阈值因此被这一层"暗跳"规避 | `tests/**/conftest.py`、`pg_fixture.py` | 确认 |
| **B-13** | **覆盖率门禁在本机不可自证（口径错配）**：85% 门禁由 CI 以 `--run-pg` 口径测量（CI 注释记录：带库 89.81% / 无库 84.19%），本机无库凭据、203 条 PG 用例全 skip，测得 81.69%。两者不同口径，**不能据此判定 CI 必红**。真问题是：① 本地永远无法复现门禁数字，只能等 CI；② 203 条 PG 用例的断言在本机从未被执行；③ 无库口径的薄弱点（`tenant_data_service` 29.6% 等）掩盖了真实风险。**→ 2026-10-25 批次 2 已补强：无库口径 81.69%→85.51%，四个业务模块 100%** | `ci-adapter.yml:279-300`；`pyproject.toml [tool.coverage.run]` | **已实测** |
| **B-13 复核**（2026-10-27，W3-2） | **原结论已过期，保留原文作为历史**：无库口径实测 **85.55%**（`cedd3e9` 树：收集 2343 / passed 2133 / skipped 210），`--cov-fail-under=85` **exit 0**。B-13 的前提「无库 84.19% < 85%」是 2055 条时代的分母；现在分母 2343，**"必须带 `--run-pg` 才能过覆盖率门槛"已不成立**。带 `--run-pg` 的正当理由改述为：**真跑那 207→210 条隔离用例**（多租户这条最高红线必须在自动化里跑过），而不是凑覆盖率。含 PG 口径实测合并覆盖 **91.94%**（同树，收集 2343 / passed 2343 / skipped 0）。 | `README.md` §2/§6.2；`pyproject.toml [tool.coverage.run]`；本机实测日志 `C:/tmp/qa-pg/{final,head-cov}.log` | **已复核（W3-2）** |

### 4.3 P2（9 项）

| ID | 问题 | 证据 |
| --- | --- | --- |
| C-01 | 内存态无界增长：`agent_service._tasks`/`_steps`、限流桶均无 TTL/上限 | `services/agent_service.py:169` |
| C-02 | 4 组模块级循环依赖（延迟导入绕过） | `business/policy_service.py`、`core/security.py` |
| C-03 | 13 个 >1,000 行文件（最大 2,272 行）+ 66 个 <300 行碎片；注释/docstring 占比偏高，信噪比下降 | `db/repositories/billing_repo.py` 等 |
| C-04 | 依赖版本策略三分裂：后端区间 (`>=x,<y`)、前端 `^`、基础设施 digest | `pyproject.toml`、`package.json` |
| C-05 | 插件执行器只有出站，无入站/定时触发；`on_uninstall` 是死钩子 | `plugins/engine.py` |
| C-06 | 死代码与常量漂移：`require_idempotency_key` 无调用点（幂等实际走 `IdempotencyManager`）；`PHASE3_DOMAIN_ENDPOINTS = 14` 实际 16 | `core/security.py:646`；`api/v1/router.py:70` |
| C-07 | 仓库卫生：空目录 `deploy;D`（shell 误操作残留）、`.env.bak.2255`、`.env.lite.pre-3b2`、`pytest_run.log`、`coverage.xml`、`__pycache__` 落地未清 | 根目录 / `deploy/` |
| C-08 | `next.config.js` 仍写 `eslint: { ignoreDuringBuilds: true }`，Next 16 已不支持该键——等于"以为在构建期 lint，实际没有"，只有手动 `npm run lint` 才生效 | `apps/web/next.config.js` |
| C-09 | README 质量数字全面过期：端点 84→实际 96、用例 1,573→2,095、覆盖率 90.78%→81.69%、P0 184/184→实测 181 passed/37 skipped、阶段状态「四未启动」→实际已交付 T4 大部分 | `README.md:15-28,210-235` |

### 4.4 值得肯定的地方（避免只列问题）

| 项 | 证据 |
| --- | --- |
| **无伪测试**：抽查 8–10 个用例，未发现「只断言 200 不断言语义」「mock 掉被测逻辑」「断言实现细节」；无 `assert True`、无 `xfail`、无串跑依赖 | 测试有效性抽查 |
| **安全测试覆盖三类真实攻击面**：agent 任务跨租户、平台哨兵伪造、权限点兼容口，三者均有独立用例文件 | `tests/security/` |
| **门禁确实可红**：5 个校验器 `--self-test` 负样本全部命中，不是摆设 | 实测 |
| **代码零重复**：AST 级完全同构函数体 **0 组**；相似文件对仅 3 组（飞书/公众号连接器 0.74、会话/记忆 0.66、身份/工具 0.60），且都是合理的结构相似 | AST 哈希实测 |
| **错误码纪律**：66 个码三处（注册表/文档/前端兜底）严格一致，内部码 17002 零外泄 | `check_error_catalog.py --strict` |

### 4.5 复核纠错记录（含本报告自身的更正）

| 初稿结论 | 复核结果 | 依据 |
| --- | --- | --- |
| 「64 个端点 `data: Any`，OpenAPI 是空壳」 | ❌ **不成立**：96/96 响应体均有 `$ref`，无 `Any` 组件 | 逐 operation 解析 `components.schemas` 实测为 0 |
| 「`require_idempotency_key` 零调用 → 幂等形同虚设」 | ❌ **不成立**：幂等由 `IdempotencyManager` 依赖实现，17 个文件接入，OpenAPI 中 42 处声明 | `grep Idempotency` + OpenAPI 实测 |
| 「`backup.sh` 只备 dify/keycloak，漏业务库」 | ❌ **反了**：只备业务库 `officeai`，漏的是 Keycloak/Dify 两个库 | `backup.sh:223-243` 仅一次 `pg_dump -d ${DB_NAME}` |
| 「清单门禁 exit 0，是假绿灯」（**主理人自己的初判**） | ❌ **我错了**：加管道后 `$?` 被 `tail` 吞掉；无管道实测为 **exit 1**，门禁行为正确，真正的问题是**没接入 CI** | 重跑 `> file 2>&1; echo $?` 得 1 |
| 「分支覆盖 ≈87.8%（3636−445）/3636」（**主理人的第二轮误判**） | ❌ **又错了**：`covered_branches` 已排除部分覆盖分支，正确口径是 2,609/3,636 = **71.75%**。QA 的原始数字正确，我的"修正"反而错了 | `coverage json` 的 `covered_branches=2609 / num_branches=3636` |
| 「覆盖率 81.69% < 85% 门禁，CI 必红」（**主理人的第三处误判**） | ❌ **口径错配**：门禁是 `--run-pg` 口径（CI 注释记录带库 89.81% / 无库 84.19%），81.69% 是本机无库口径，两者不可比。已把该条从 P0 降为"本地不可自证" | `ci-adapter.yml:279-300` 步骤注释原文 |

---

## 五、与上一轮审计的差异（已修复项）

| 上轮 ID | 结论 | 本轮复核 |
| --- | --- | --- |
| SEC-01 | Agent 任务跨租户越权（高·已复现） | ✅ **已修复**：`_tasks` 改 `(tenant_id, task_id)` 复合键，`get_task` 强制 `auth`，未命中 fail-closed，列表按租户过滤 |
| SEC-02 | 平台哨兵仅凭租户声明即获平台视角 | ✅ **已修复**：`is_platform_scope` 追加 `OPS_ROLE_KEYS` 角色要求（`security.py:760-790`） |
| SEC-03 | 「权限点即角色」绕过角色矩阵 | 🟡 **保留但已收窄**：仍存在，风险取决于令牌签发方可信度 |
| G-13 | 错误码门禁实测为红 | ✅ **已转绿**：66 码三处一致，`--strict` exit 0 |
| QAL-01 | 工作区不可合并 | ✅ 已可合并（静态门禁全绿） |
| CAP-02 | 生产登录无法完成 | ❌ **仍存在**，且发现第二处断裂（A-01） |

---

## 六、对照 V1.0 验收标准（`plan-v1.0.md` §6，一票否决项）

| 条款 | 要求 | 现状 | 判定 |
| --- | --- | --- | --- |
| 6.1 | 5 款工具鉴权→调用→回流全链路联调 | 5/5 为 `demo` 桩 | ❌ 不达标 |
| 6.2 | 普通用户无需培训上手 | 页面与三态齐备，但登录走不通 | ❌ 不可验证 |
| 6.3 | 首屏 ≤2s / 首字 ≤1s / 1,000 并发 / 99.5% 可用 | 无性能报告与压测产物 | ❌ 未验证 |
| 6.4 | 渗透无高/中危；合规 100% | 无渗透报告；协议扫描通过 | 🟡 部分 |
| 6.5 | SaaS 可注册付费；私有化一键 + 纯离线；文档齐全 | 支付为桩；离线包缺镜像；备份不全 | ❌ 不达标 |

---

## 七、改进建议（按投入产出排序）

### 第一批：交付前必须闭环（建议 1–2 周）

1. **打通登录闭环**（A-01）：回调页做 state/PKCE 校验 + 授权码兑换 + 会话建立；`api-client` 启动时从存储回灌令牌，并改用 `httpOnly` Cookie 或「内存 + refresh」方案降低 XSS 面。
2. **让交付物可复现**（A-04/A-05/B-02）：产出 `offline/images/*.tar` + `checksums.sha256`；把 `migrate` 服务抽成 compose overlay 三套共用；`backup.sh` 扩到三库，`restore.sh` 增加迁移 + 恢复后行数校验（8 张核心表）。
3. **修三处"假绿灯"**（A-06/A-02/B-01）：把交付清单门禁**接进 CI**（现 0 处引用）；CI 加 Postgres service 或把覆盖率拆成"无 PG 85% / 含 PG 90%"双口径，并把 `pg` 标记补齐到与 skip 数一致；环境变量统一为 `OA_KEYCLOAK_BASE_URL` 并加配置名快照测试（防再漂）。
4. **权威文档入库**（A-06）：charter、decisions 0005-0006、metrics-definition 必须先 commit，再谈"唯一权威"。
5. **离线/私有化实跑一次**：在干净机器上跑 `install.sh --offline` → 登录 → 建任务 → 备份 → 恢复，形成一份带命令输出的记录。**这一步能一次性暴露 A-01/A-04/A-05/B-02 四条。**

### 第二批：提升可信度（2–4 周）

6. **补齐 OpenAPI 公共层**（B-07）：加 `securitySchemes`（Bearer）、`servers`、公共头参数；给插件/计费/运营三域补 1-2 个响应示例。
7. **compose 单一事实源**（B-03）：用 `x-common-env` 锚点 + `env_file` 收敛，三套只保留差异段；加 `validate_compose_parity.py` 断言差异不超白名单。
8. **支付要么真接要么下架**（B-04）：真实对接需 RSA 平台证书验签 + AES-GCM 解密 + `trade_state` 白名单（去掉 success 默认）；否则 UI 明确标注"演示支付"。
9. **前端最小测试网**（B-08）：先给 `api-client` 与 `use-session` 加 vitest 单测（正是 A-01 的破点），再补 1 条 Playwright 登录冒烟。
10. **生产暴露面收敛**（B-05/B-06）：CORS 白名单配置化；nginx 增加 `/docs` `/redoc` `/openapi.json` 内网限制或按环境关闭。

### 第三批：结构性优化（随版本演进）

11. 拆 4 组循环依赖：权限解析下沉到 `core/permissions.py`，`core.security` 只依赖协议。
12. 拆 13 个巨文件（建议 ≤600 行/文件），同时合并 66 个碎片文件中的纯类型/常量模块。
13. 内存态统一走有界 LRU + TTL（`_tasks`/`_steps`/限流桶）。
14. 插件补入站与定时触发，`on_uninstall` 接真实清理，否则"插件生态"只是单向调用。

---

## 八、附：本次审计未覆盖

- Docker 全栈实跑（完整版 30 服务）、备份/恢复实跑、离线安装实跑
- 真实第三方联调（5 连接器 / 微信支付 / 模型密钥）
- 性能压测与渗透测试
- CI 在 GitHub Actions 上的真实执行（额度未恢复）
- 身份服务侧：`tenant_id` / `roles` 声明是否可由终端用户控制（影响 SEC-03 定性）
- 203 条 PG 用例的断言质量（本机无 `OA_TEST_DATABASE_URL`，全部 skip）

> 本报告为只读审计产物，未修改任何业务代码、测试或配置。
