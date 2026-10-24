# Moy（麦子）代码质量、结构与安全审计报告

| 项 | 内容 |
| --- | --- |
| 报告编号 | OA-ARCH-001 |
| 审计日期 | 2026-10-23 |
| 审计范围 | `apps/adapter`（适配层）、`apps/web`（前端）、`deploy/`（编排与门禁）、`docs/`（架构与验收文档） |
| 代码基线 | 工作区 `main` 分支（含已提交修复，与 2026-10-22 两份审计的修复后状态一致） |
| 审计方式 | **独立分层抽样**：Glob/Grep/Read 覆盖 API 路由、鉴权中间件、db/repositories、clients、plugins、workers/tasks、web lib/api 与 auth 页面、deploy init-*.sql / migrations / validate_*.py、tests/security；每个问题附可核验文件路径（及行号/函数名） |
| 审计结论 | **有条件通过**：工程治理成熟度高（类型、测试、错误码、RLS、门禁体系显著优于同类），但存在 **1 项高危安全缺陷（租户伪造）+ 2 项「代码存在≠功能可运行」缺口（连接器桩实现、登录占位）**，以及多项可维护性技术债 |

---

## 一、审计范围与方法

### 1.1 覆盖目录

| 目录 | 覆盖方式 | 抽样重点 |
| --- | --- | --- |
| `apps/adapter/app/api/v1/` | 路由层全量 Grep + 抽读 router.py | 端点挂载、鉴权依赖、幂等 |
| `apps/adapter/app/core/` | security.py / config.py / artifact_token.py / ratelimit.py 全文精读 | 鉴权、配置防呆、令牌、限流 |
| `apps/adapter/app/middleware/` | error_handler.py 全文精读 | 错误兜底、免鉴权白名单 |
| `apps/adapter/app/db/` | session.py / advisory_lock.py / migrate.py 关键段 + repositories Grep | RLS 会话变量、迁移锁、SQL 注入面 |
| `apps/adapter/app/business/` | task_service.py / usage_service.py 关键段 + 全量 import 方向 Grep | 分层依赖、超时、额度硬止损 |
| `apps/adapter/app/services/` | model_service.py / agent_service.py / rag_service.py 路径常量 Grep | 底座协议泄漏 |
| `apps/adapter/app/plugins/` | sanitizer.py / engine.py / schema.py 抽读 | 脱敏、护栏、声明模型 |
| `apps/adapter/app/tools/` | 全 5 个 connector 头部 + Grep | 桩实现确认、SSRF 面 |
| `apps/adapter/app/clients/` | dify_client.py / keycloak_client.py Grep | 出口唯一性 |
| `apps/adapter/app/workers/` | job_runner.py Grep | 超时、并发 |
| `apps/web/src/` | api-client.ts / login/page.tsx 精读 + 全量安全 Grep | Token 存储、XSS、术语泄漏 |
| `deploy/` | docker-compose*.yml / init-*.sql / migrations/ / validate_secrets.py / images.lock | RLS、密钥、许可证、迁移 |
| `apps/adapter/tests/` | security/ 全量 Grep + expected_xfail.py 精读 | 测试实质、缺陷台账 |
| `.github/workflows/` | dependency-scan.yml 头部 | 门禁规则 |

### 1.2 抽样策略

- **优先覆盖高风险面**：鉴权/租户隔离、外部调用、密钥管理、SQL 执行、前端 Token/XSS
- **分层抽样**：每层至少精读 1-2 个核心文件 + 全量 Grep 模式扫描
- **独立复核**：不采信文档自述绿灯，全部结论以代码事实为准；对 2026-10-22 两份审计的结论逐条验证修复状态
- **区分确认/疑点**：每个问题标注「确认」或「疑似待验证」

### 1.3 与既有审计的关系

本报告独立复核了 `docs/audit-repo-2026-10-22.md`（OA-AUDIT-001）与 `docs/repo-gap-assessment-2026-10-22.md`（OA-REVIEW-002）的全部结论，确认以下问题**已修复**：

| 原问题 | 修复证据 | 当前状态 |
| --- | --- | --- |
| S1 生产 stub 鉴权无防呆 | `security.py:701-744` `validate_auth_startup()` + `tests/unit/test_auth_startup.py` | ✅ 已修复 |
| S3 档位路由未接线 | `model_service.py:520-558` `resolve_app_key()` + `DifyClient.for_app_key()` + `tests/unit/test_profile_routing_contract.py` | ✅ 已修复 |
| S4 用量计量无硬止损 | `usage_service.py:611-656` `check_generation_allowed()` → 15006/402 | ✅ 已修复 |
| S6 SaaS DSN 超级用户 | `docker-compose.saas.yml:381` 改 `OA_DATABASE_URL:?必须...` | ✅ 已修复 |
| S9 产物令牌永久有效 | `artifact_token.py:12-18/90-178` 加 exp + 拒绝空密钥 | ✅ 已修复 |
| M14 权限无角色放行 | `security.py:251-313` fail-closed + `PERMISSION_WHITELIST` 仅 2 条 | ✅ 已修复 |
| M8④ phase3 哨兵缺失 | `init-schema-phase3.sql:403-404` 插入 `schema_migration` 行 | ✅ 已修复 |
| P3-B22-02 任务超时死配置 | `task_service.py:176-179/547-566` `wait_for` 消费 `plugin_task_timeout_seconds` | ✅ 已修复 |
| P0-2 RLS DSN 修复未入库 | `docker-compose.yml:484` 已用 `:?必须...` | ✅ 已修复 |
| P0-3 密钥明文兜底 | `validate_secrets.py` G-33 门禁 + compose 改 `:?` | ✅ 已修复（密钥待轮换） |
| P0-4 CI 无 PG | `.github/workflows/ci-adapter.yml` 已加 postgres service | ✅ 已修复 |
| P1-1 底座出口违规 | `agent_service.py:155-157` 改用 `DifyClient.for_app_key()` | ✅ 已修复 |
| M16 init-rls 孤儿挂载 | `docker-compose.saas.yml:38` 已停止挂载 | ✅ 已修复（文件本身仍在） |

以下问题**仍存在或部分存在**，列入本报告。

---

## 二、按模块问题清单

### A. 架构与模块化（对总纲 7.4/7.6）

| 模块 | 问题ID | 问题描述 | 证据路径 | 影响 | 严重程度 | 类别 |
| --- | --- | --- | --- | --- | --- | --- |
| clients/services | **AR-001** | 底座协议路径常量仍散落在 3 个 service 层文件，未收拢到 `clients/dify_client.py`。`model_service.py:79` `_DIFY_CHAT_PATH="/v1/chat-messages"`、`agent_service.py:50` `_DIFY_WORKFLOWS_PATH="/v1/workflows"`、`rag_service.py:67-69` 三个路径常量。换底座需改 clients + 3 个 service 的路径与载荷构造。 | `app/services/model_service.py:79`、`app/services/agent_service.py:50`、`app/services/rag_service.py:67-69` | 换底座成本高；「替换底座仅改适配层」承诺打折扣 | 中 | A |
| business→api | **AR-002** | business 层反向依赖 api 层：`task_service.py:188` `from app.api.v1.plugin_deps import build_action_gateway`（函数内延迟 import 规避加载期循环，但组合根错位在 api 层）。 | `app/business/task_service.py:188` | 分层不清；新增功能无法判断"该写在哪一层"；装配逻辑错位 | 中 | A |
| services→db | **AR-003** | services 层直接 import repositories（`tool_service.py:33`、`model_service.py:48`、`identity_service.py:415`、`memory_extractor.py:39`），绕过 business 层。 | `app/services/tool_service.py:33`、`app/services/model_service.py:48` | 分层链断裂；business→services 这条链实际不存在 | 中 | A |
| plugins/engine | **AR-004** | 引擎护栏按 `output_key` 硬编码三个业务域（offer/schedule/screening），非数据驱动可注册。 | `app/plugins/engine.py:737-746` | 新增业务域需改引擎代码；天花板低 | 低 | A |
| tools | **AR-005** | 新增工具需改 3 处（YAML + connector.py + `_CONNECTOR_MODULES` 元组），README 却写"新增工具只加这里"。 | `app/services/tool_service.py:75-82`、`apps/adapter/README.md:170-175` | 宣称的"数据驱动零改动"打折扣 | 低 | A |

### B. 代码质量

| 模块 | 问题ID | 问题描述 | 证据路径 | 影响 | 严重程度 | 类别 |
| --- | --- | --- | --- | --- | --- | --- |
| 多模块 | **AR-006** | 上帝文件：`billing_repo.py` 2272 行/92 函数、`task_service.py` 1597 行、`plugin_service.py` 1422 行、`engine.py` 1517 行、`model_service.py` 1585 行。`billing_repo` 混 5 类实体 + Protocol + 内存/PG 双实现。 | `app/db/repositories/billing_repo.py`、`app/business/task_service.py`、`app/plugins/engine.py` | 改一处牵一片；新人定位成本高；单测必须 mock 半仓 | 中 | B |
| core/ratelimit | **AR-007** | 限流器硬编码 `InMemoryRateLimitStore`，`RedisRateLimitStore` 是死代码；`Settings` 无 `ratelimit_backend` 字段（`extra="ignore"` 静默丢弃）。多副本部署时每副本独立计数，真实配额 = 120 × 副本数。 | `app/core/ratelimit.py:151`、`app/core/config.py`（无 ratelimit_backend） | 多副本下限流失效；文档声称的 `OA_RATELIMIT_BACKEND` 在配置里不存在 | 中 | B |
| pyproject | **AR-008** | `slowapi>=0.1.9` 已声明但全仓零引用（限流是自研 `RateLimiter`）。 | `apps/adapter/pyproject.toml:36` | 供应链面扩大；认知负担 | 低 | B |
| 文档 | **AR-009** | README 写 "Next.js 14"，实际 `package.json` 为 `next ^16.4.2` + `react ^19.3.1`。 | `README.md:53` vs `apps/web/package.json` | 新人按 README 上手即被误导 | 低 | B |
| web | **AR-010** | 前端零自动化测试（无 vitest/playwright/jest）。 | `apps/web/package.json`（无 test 脚本） | 前端回归靠人眼 | 低 | B |
| core/config | **AR-011** | `idempotency_backend` 默认 `"memory"`，多副本部署或重启后幂等键失效，与"写接口必须带 Idempotency-Key"的强约束不匹配。 | `app/core/config.py:312` | 多副本下幂等保护失效 | 中 | B |

### C. 潜在缺陷

| 模块 | 问题ID | 问题描述 | 证据路径 | 影响 | 严重程度 | 类别 |
| --- | --- | --- | --- | --- | --- | --- |
| tools | **AR-012** | **5 个工具连接器全部为桩实现**：`wecom_connector.py:13`、`wechat_mp_connector.py:13`、`github_connector.py:6`、`email_connector.py:6`、`feishu_connector.py` 均自述"不发起任何真实第三方调用"，`params["simulate"]` 是唯一异常注入入口。 | `app/tools/wecom_connector.py:13`、`app/tools/github_connector.py:6` | 「代码存在 ≠ 功能可运行」；工具连接能力对用户不可用 | **高** | C |
| web/auth | **AR-013** | 前端登录页仍是占位流程：`login/page.tsx:34` 注释"T01 阶段先提供占位流程，T03 接入 OIDC 授权码流程"，但 T03 未落地。`handleEnter` 直接 `router.push('/chat')`，无真实 OIDC 授权码流程。 | `apps/web/src/app/(auth)/login/page.tsx:30-40` | 生产环境无真实登录能力；「首版即商用」不满足 | **高** | C |
| migrations | **AR-014** | 迁移回滚 SQL 仅覆盖 0006-0011，0002-0005 无 down SQL。 | `deploy/migrations/rollback/`（仅 6 个 .down.sql） | 私有化升级对早期迁移无退路 | 低 | C |
| expected_xfail | **AR-015** | PRD 5.3 额度用尽软限制四项行为（对话限长/文档暂停/自动化排队/管理员通知）未实现，缺陷台账 `P3-B22-01` 状态 `open`，到期批次 B30。 | `tests/expected_xfail.py:121-138` | 超额租户可无限跑文档与自动化 | 中 | C |
| billing/usage | **AR-016** | `usage_service.py:589-593` 存在**重复代码块**（`TENANT_BUDGET_USED_RATIO.set` 连续两次），疑似复制粘贴缺陷。 | `app/business/usage_service.py:589-593` | 重复执行 gauge.set（无害但表明代码审查疏漏） | 低 | C |

### D. 安全隐患

| 模块 | 问题ID | 问题描述 | 证据路径 | 影响 | 严重程度 | 类别 |
| --- | --- | --- | --- | --- | --- | --- |
| core/security | **AR-017** | **SaaS 模式租户伪造**：`resolve_tenant_id()` 中 `X-Tenant-Id` 请求头**无条件覆盖**令牌 `tenant_id` 声明，且无任何"用户是否属于该租户"的成员关系校验。攻击者持有效 JWT（tenant A）+ 伪造 `X-Tenant-Id: tenant_B` 即可访问 tenant B 全部数据（RLS 会话变量被设为 tenant_B）。`get_request_context()` 用 `replace(auth, tenant_id=tenant_id)` 直接覆写，无一致性校验。 | `app/core/security.py:217-219`（header 优先）、`app/core/security.py:467-470`（覆写 auth.tenant_id） | **跨租户数据泄露**：知识库、文档、凭据、计费、审计数据均可被未授权读写 | **高** | D |
| web | **AR-018** | Token 存储在 `localStorage`（`officeai_access_token`），XSS 漏洞可直接窃取令牌。 | `apps/web/src/app/(auth)/login/page.tsx:51` | 一旦存在 XSS，令牌即被窃取 | 中 | D |
| deploy | **AR-019** | `init-rls.sql` 是孤儿文件：compose 已停止挂载，但文件本身仍存在且含旧策略 `tenant_isolation_*`（已被 `p_tenant_isolation` 取代）。`docker-compose.yml:56` 注释仍引用不存在的 `10-init-rls.sql`。 | `deploy/init-rls.sql`、`deploy/docker-compose.yml:56` | 后续排障与合规核查易混淆 | 低 | D |
| plugins | **AR-020** | 插件隔离是 schema 级（禁 entry/module/command/deps），非 OS 沙箱；无 subprocess/容器/CPU 内存限制。 | `app/plugins/schema.py:3-6/81-116` | 保守设计换来安全性（正面），但天花板低 | 低 | D |

### E. 与总纲原则的符合性（工程视角）

| 模块 | 问题ID | 问题描述 | 证据路径 | 影响 | 严重程度 | 类别 |
| --- | --- | --- | --- | --- | --- | --- |
| 多处 | **AR-021** | 「首版即商用」原则受损：连接器桩实现（AR-012）+ 登录占位（AR-013）= 核心能力"代码存在 ≠ 功能可运行 ≠ 集成已验证"。 | `app/tools/*.py`、`apps/web/src/app/(auth)/login/page.tsx` | 商用交付存在能力缺口 | **高** | E |
| services | **AR-022** | 「技术黑盒化」基本落实（前端术语扫描 0 命中），但 `model_service.py:1536/1543` 硬编码 `<!--dify-deepseek-reasoning-->` 标记，厂商专属格式写进服务层。 | `app/services/model_service.py:1536/1543` | 换模型需改服务层解析逻辑 | 低 | E |
| 全仓 | **AR-023** | 「模块化与可替换性」部分达成：`dify_base_url` 出口已收口到 `core/config.py` + `clients/dify_client.py`（P1-1 已修），但协议路径常量仍散落 3 个 service（AR-001），换底座需改多处。 | `app/services/model_service.py:79`、`app/services/agent_service.py:50`、`app/services/rag_service.py:67-69` | 「替换底座仅改适配层」承诺打折扣 | 中 | E |

---

## 三、安全隐患专表

| 风险场景 | 攻击面 | 当前缓解措施 | 缺口 | 问题ID |
| --- | --- | --- | --- | --- |
| **跨租户数据泄露（SaaS）** | `X-Tenant-Id` 请求头可伪造任意租户 ID；RLS 会话变量被设为攻击者指定的租户 | RLS 双保险（`p_tenant_isolation` + `app.tenant_id`）；私有化模式强制 `default` | **无成员关系校验**：不验证"当前用户是否属于请求的租户"；`X-Tenant-Id` 无条件覆盖令牌声明 | **AR-017** |
| 伪造 JWT | `auth_mode=stub` 时不验签；`jwt_secret` 为空时签名退化 | `validate_auth_startup()`：production/staging + stub → 30004 拒绝启动；空 jwt_secret 同理 | 无（已修复） | — |
| Token 窃取（XSS） | `localStorage` 存储访问令牌 | React 默认转义（无 `dangerouslySetInnerHTML`/`eval`） | Token 在 `localStorage`，XSS 可直接窃取；应改 httpOnly Cookie 或内存存储 | AR-018 |
| 越权访问（水平） | 跨租户对象 ID 枚举 | 对象级判权（`test_attack_object_scope.py` 覆盖 task/conversation/memory）；`assert_tenant_allowed` | 覆盖良好（正面） | — |
| 越权访问（垂直） | 无角色令牌绕过端点权限 | `require_permission` fail-closed + `PERMISSION_WHITELIST` 仅 2 条 + lock 文件锁定 | 无（已修复） | — |
| SQL 注入 | repositories 动态 SQL | 表名为模块常量（非用户输入）；`assert_identifier` 校验动态标识符；参数化查询 | 无明显风险 | — |
| 命令注入 | `migrate.py` 调用 `pg_dump` | `shutil.which` 解析绝对路径；`subprocess.run` 列表参数（无 `shell=True`） | 无明显风险 | — |
| SSRF | 连接器出站调用外部 URL | 当前 5 个连接器均为桩实现，无真实出站 | 连接器真实实现后需加 SSRF 白名单 | AR-012 |
| 秘密管理 | compose 硬编码密钥兜底 | G-33 门禁（`validate_secrets.py`）扫描 `${VAR:-高熵串}`；`.env.example` 用 `CHANGE_ME_*` 占位 | 旧密钥待轮换（运维执行） | — |
| 外部操作控制 | 发消息/删数据/写外部系统 | 部分操作有 `confirmed` 参数（如 `wechat_mp_connector.py:291`）；审计记录 | 连接器桩实现后无法验证真实外部操作的确认与审计覆盖 | AR-012 |
| 依赖与镜像供应链 | 镜像浮动标签、GPL 传染 | `images.lock` 唯一事实来源，`latest` 0 命中；squid GPL-2.0 用 `distribution: independent-container` 豁免；Valkey 取代 Redis 规避 SSPL | `slowapi` 死依赖（AR-008）；`.difypkg` 离线包内 36 个依赖无 SCA 覆盖 | AR-008 |
| 多副本幂等失效 | 幂等键默认 `memory` 后端 | `idempotency_backend` 可配 `redis` | 默认值为 `memory`，多副本下失效 | AR-011 |
| 多副本限流失效 | 限流器硬编码内存存储 | `RateLimiter` 可注入 store | 无 `ratelimit_backend` 配置；`RedisRateLimitStore` 死代码 | AR-007 |

---

## 四、架构符合性评估

### 4.1 对总纲 7.4「模块化与可替换性」

| 要求 | 落实情况 | 证据 |
| --- | --- | --- |
| 更换模型不影响上层业务工作流 | **部分达成**：档位路由已接线（`resolve_app_key` + `DifyClient.for_app_key`），但协议路径常量散落 3 个 service | AR-001 |
| 替换向量库控制在检索适配层 | **基本达成**：Milvus 仅在 `rag_service` + 配置中出现 | — |
| 更换工作流引擎保留任务状态和交付逻辑 | **基本达成**：任务状态机在 `task_service` + `task_repo`，与底座解耦 | — |
| 引入新插件不重复实现权限和任务管理 | **达成**：插件声明式（schema 级），权限/任务由平台统一管理 | — |
| 适配层唯一原则（上层严禁直连 Dify） | **基本达成**：`dify_base_url` 出口已收口；但协议路径常量仍在 service 层 | AR-001 |

### 4.2 对总纲第十章「质量安全原则」

| 要求 | 落实情况 | 证据 |
| --- | --- | --- |
| 权限与数据隔离 | **优秀**：RLS 双保险 + `FORCE ROW LEVEL SECURITY` + `officeai_app` NOSUPERUSER NOBYPASSRLS + `/readyz` 自检 + fail-closed 权限 | — |
| 外部操作控制 | **部分达成**：部分操作有确认参数；连接器桩实现无法验证真实覆盖 | AR-012 |
| 失败处理与可追溯性 | **优秀**：trace_id 全链路透传；审计记录；错误码注册制 63 码 | — |
| 隐私与数据管理 | **部分达成**：产物脱敏（PII 掩码 + 凭据拒绝）；保留期配置存在 | — |
| 质量验证 | **优秀**：1598 条测试（含 PG 全量）；security 测试覆盖攻击面；缺陷台账 append-only | — |

### 4.3 对核心原则四六八

| 原则 | 落实情况 | 证据 |
| --- | --- | --- |
| **原则四（模块化与可替换性）** | **B+**：出口收口良好，但协议常量泄漏 + 分层交叉依赖（AR-001/002/003） | — |
| **原则六（可靠交付）** | **B**：门禁体系完善（G-13/26/27/30/32/33），但连接器桩实现 + 登录占位 = 能力缺口（AR-012/013） | — |
| **原则八（安全与信任）** | **B+**：RLS/鉴权/脱敏/审计优秀，但 SaaS 租户伪造是高危缺口（AR-017） | — |

### 4.4 「代码存在 ≠ 功能可运行」混同迹象

| 维度 | 现状 | 判定 |
| --- | --- | --- |
| 代码存在 | 5 个连接器 + 登录页均有完整代码 | ✅ |
| 功能可运行 | 连接器返回 mock 结果；登录直接跳转 | ❌ |
| 集成已验证 | e2e 测试通过（但走桩实现路径） | ⚠️ |
| 生产可用 | 工具连接 + 登录不可用 | ❌ |

---

## 五、优先级排序的改进建议

### P0（本周内，阻塞商用交付）

| 编号 | 行动项 | 预估工作量 | 验收判据 |
| --- | --- | --- | --- |
| **P0-1** | **修复 SaaS 租户伪造**：`resolve_tenant_id()` 中 `X-Tenant-Id` 与令牌 `tenant_id` 不一致时，校验用户是否属于请求的租户（查成员关系），不属于则拒绝（20003）。或改为"令牌声明优先、请求头仅在一致时生效"。 | **M** | 持 tenant A 的 JWT + `X-Tenant-Id: tenant_B` 请求 → 20003 拒绝；新增安全测试覆盖此攻击面 |
| **P0-2** | **连接器真实实现或明确降级**：5 个连接器当前为桩实现，需要么接入真实第三方 API，要么在产品文档/UI 中明确标注"演示模式"，不得声称"工具连接已就绪"。 | **L** | 至少 1 个连接器（如企业微信）真实可用；或 UI 明确标注演示模式 |
| **P0-3** | **登录流程真实化**：接入 OIDC 授权码流程（T03 承诺），替换当前占位跳转。 | **M** | 生产环境可通过身份服务完成真实登录；Token 不再手动粘贴 |

### P1（阶段四启动前）

| 编号 | 行动项 | 预估工作量 | 验收判据 |
| --- | --- | --- | --- |
| **P1-1** | 底座协议路径常量收拢到 `clients/dify_client.py`（暴露 `chat()` / `run_workflow()` / `datasets()` 语义方法）；厂商专属标记解析抽成可插拔 parser。 | **M** | `grep -r "v1/chat-messages\|v1/workflows\|v1/datasets" app/services/` 命中 0 |
| **P1-2** | 分层依赖收口：`build_action_gateway` 抽到 `app/bootstrap.py`；services 层只依赖 business 抽象；CI 加 import 方向门禁（禁止 api→db、business→api、repo→business）。 | **L** | import 方向门禁 0 违规 |
| **P1-3** | 限流器后端可配：`Settings` 加 `ratelimit_backend: Literal["memory","redis"]`，`create_app` 按配置构造 store；生产默认 redis。 | **S** | 多副本部署时限流共享计数 |
| **P1-4** | Token 存储改 httpOnly Cookie 或内存 + 刷新令牌机制，不再用 `localStorage`。 | **M** | XSS 场景下令牌不可被 `document.cookie`/`localStorage` 窃取 |
| **P1-5** | 幂等后端生产默认改 Valkey；启动时对 `memory` + 多副本发出告警。 | **S** | 多副本部署幂等键跨副本有效 |

### P2（技术债窗口）

| 编号 | 行动项 | 预估工作量 | 验收判据 |
| --- | --- | --- | --- |
| **P2-1** | 上帝文件拆分：`task_service` 拆 Admission/Runner/Queue/Retry/Query/Artifact；`billing_repo` 按聚合拆分。 | **L** | 单文件 ≤ 800 行 |
| **P2-2** | 补 0002-0005 迁移回滚 SQL + 回滚演练。 | **M** | 每条迁移有对应 .down.sql |
| **P2-3** | 前端引入 vitest + Playwright 覆盖登录/对话/装插件/下单四条主路径。 | **M** | 四条主路径有自动化回归 |
| **P2-4** | 清理死依赖 `slowapi`；修正 README "Next.js 14" → "Next.js 16"。 | **S** | 文档与代码一致 |
| **P2-5** | PRD 5.3 额度软限制四项行为补全（P3-B22-01，到期 B30）。 | **M** | 对话限长/文档暂停/自动化排队/管理员通知均生效 |
| **P2-6** | 清理 `init-rls.sql`（删除或重命名为 `99-archive-*.sql`）；修正 `docker-compose.yml:56` 注释。 | **S** | 无孤儿文件 |
| **P2-7** | `usage_service.py:589-593` 重复代码块去重。 | **S** | 无重复执行 |
| **P2-8** | 引擎护栏改可注册策略（`GuardRegistry`），不再按 `output_key` 硬编码。 | **M** | 新增业务域无需改引擎代码 |

---

## 六、假设与不确定性声明

| # | 假设/疑点 | 影响 | 验证方式 |
| --- | --- | --- | --- |
| 1 | **AR-017 租户伪造**：假设 SaaS 模式下无其他隐藏的成员关系校验层（如网关层）。若网关层有校验，则此问题降级为中。 | 高危判定依赖此假设 | 需确认部署架构中是否有 API 网关层做租户校验 |
| 2 | **AR-012 连接器桩实现**：假设生产环境不会以桩模式交付。若产品定位为"演示版"，则此问题降级。 | 高危判定依赖产品定位 | 需确认 V1.0 商用范围是否包含工具连接 |
| 3 | **AR-013 登录占位**：假设生产环境通过其他方式（如反向代理注入令牌）完成认证。若如此，登录页仅为开发辅助。 | 高危判定依赖部署方式 | 需确认生产环境认证流程 |
| 4 | **AR-007 限流失效**：假设生产部署为单副本。若多副本，限流实际配额 = 配置值 × 副本数。 | 中等判定依赖部署拓扑 | 需确认生产副本数 |
| 5 | **测试覆盖率数字**：本报告未独立复跑全量测试，覆盖率数字引用自 README 口径脚注（含 PG 全量 90.78% / CI 无 PG 子集 84.19%）。 | 数字准确性依赖既有报告 | 可复跑 `pytest --run-pg` 验证 |
| 6 | **密钥轮换状态**：P0-3 修复后旧密钥是否已轮换，本报告无法验证（运维操作）。 | 安全状态依赖运维执行 | 需运维确认 |
| 7 | **`.difypkg` 离线包 SCA**：未解包扫描其内部 36 个依赖的 CVE 状态。 | 供应链风险未知 | 需 SCA 工具扫描 |

---

## 七、审计亮点（显著优于同类项目，建议保持）

1. **RLS 多租户隔离是真双保险**：strict 表全部 `ENABLE + FORCE ROW LEVEL SECURITY`，`officeai_app` 强制 `NOSUPERUSER NOBYPASSRLS`，`/readyz` 带 `db_privilege` 自检——主动识别"超级用户绕过 RLS"这一极易被忽略的陷阱。
2. **权限 fail-closed + 白名单锁定**：`PERMISSION_WHITELIST` 恰好 2 条，lock 文件快照锁定，`test_permission_whitelist.py` 断言未扩张——杜绝"无角色=放行"的静默越权。
3. **错误码三处一致自动化**：63 个码在架构文档 / `errors.py` / `business-errors.ts` 之间自动核对，含内部码外泄终检。
4. **缺陷台账 append-only**：`expected_xfail.py` 的 `DEFECT_LEDGER` + `OPEN_CASES_HISTORY` + `RED_HISTORY` 单调不增——防"把红变黄"洗标记。
5. **门禁"必须能红"的机械证明**：G-26/G-18/G-27/G-30/迁移门禁都配了负样本自检（断言退出码 = 1）。
6. **技术黑盒化落到实处**：前端 95 个源文件术语扫描 0 命中；内部错误码 17002 零外泄。
7. **类型与静态检查零妥协**：mypy `disallow_untyped_defs=true` 零 error；ruff 12 组规则全绿。
8. **镜像供应链治理扎实**：`images.lock` 唯一事实来源，`latest` 0 命中，含实测结论（MinIO 下架、Grafana AGPL 不可获取）。
9. **产物脱敏双保险**：`mask_pii()` + `assert_no_secret()`，WF2 编辑后必须重跑脱敏——不把用户手工输入当可信输入。
10. **审计不可篡改**：DB 层 `REVOKE UPDATE, DELETE` + PG 实跑测试 `test_audit_log_immutable.py`。

---

*本报告全部结论基于 2026-10-23 对工作区 `main` 分支的独立分层抽样审计，每个问题附可核验文件路径（及行号/函数名），可复现。与 OA-AUDIT-001、OA-REVIEW-002 互补，冲突处以代码事实为准。*
