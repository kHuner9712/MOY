# 仓库体检：从"积木式办公 AI 产品"视角的不足评估（OA-REVIEW-002）

| 项 | 内容 |
| --- | --- |
| 评估日期 | 2026-10-22 |
| 代码基线 | `main` @ `fc3df7b`（322 次提交；工作区另有 4 项已改未提交 + 4 个游离文件） |
| 评估范围 | `apps/adapter`（117 个 .py / 55,103 行）、`apps/web`（19,234 行 TS/TSX）、`deploy/`、`docs/`、`.github/workflows/` |
| 评估方式 | 源码通读 + 门禁脚本复跑 + 配置/文档三向交叉核对 + 多轮交叉验证；**不采信任何文档自述绿灯** |
| 一句话结论 | 工程质量（类型、测试、错误码、镜像治理）明显高于同类独立项目，但**产品内核（记忆与上下文、档位路由）、成本护栏、可观测性、生产鉴权、合规门禁五处存在结构性缺口**——它们共同决定这到底是"能卖的商用产品"还是"跑得很稳的骨架" |

> 与同日 `docs/audit-repo-2026-10-22.md`（OA-AUDIT-001）的关系：那份聚焦"17 道门禁是否跑得通"，
> 本次聚焦"产品与架构是否支撑定位"。OA-AUDIT-001 的 P0-1/P0-2/P0-3 已在 `a5893dd`、`fc3df7b` 修复，
> 本报告不再重复；**P0-4（CI 无 Postgres）未修**，下文列为 S6。

---

## 严重（S）：阻塞商用交付或动摇产品定位

### S1 · 生产配置里鉴权等于关闭，且代码没有任何防呆

- **文件/模块**：`deploy/.env:116/128/129`（**该文件未被 git 跟踪**：`.gitignore:52-53`，`git ls-files deploy/` 无——风险在"本地与交付时使用"，不在仓库泄露）；`docker-compose.yml:455` / `docker-compose.saas.yml:355`；`app/core/config.py:68`；`app/core/security.py:148-150/314-316`
- **问题**：本地实际部署用的 `.env` 同时写着 `OA_ENV=production`、`OA_AUTH_MODE=stub`、`OA_JWT_SECRET=dev-jwt-secret`（另有 `OA_DATABASE_URL` 内嵌明文口令、`OA_CREDENTIAL_ENCRYPTION_KEY` 真实主密钥、真实 Dify 应用密钥，以及 5 处未改的 `CHANGE_ME_*`）。
  `security.py:148-150` 是 `options = {"verify_signature": conf.auth_mode != "stub"}` 且 stub 下 `verify_aud/iss=False` —— **不验签**。
  关键在默认值：`config.py:68` 的 `auth_mode: AuthMode = "stub"` 就是默认态，而两份 compose 的 adapter `environment`（private `454-477`、saas `354-365`）**只写死 `OA_ENV: production`，没有任何一处写 `OA_AUTH_MODE`**，全仓也无 production 防呆。`config.py:327-329` 与 `security.py:314-316` 的注释还自述曾踩过"注入的 oidc 被降级为 stub、伪造令牌可登录"。算法已限单一值（`security.py:157`），无 alg=none 面。
- **影响**：**不验签是默认态而非异常态**——任意人可自造 JWT 声明任意 `tenant_id` / `roles:["*"]`，读写全部知识库、文档、第三方凭据、计费与审计数据；单机集中式弱口令一旦泄露即全泄露。
- **改进建议**：`Settings` 增加 `@model_validator(mode="after")`——`env in {"staging","production"}` 且 `auth_mode == "stub"` 时直接抛 `30004` 启动失败；两份 compose 的 adapter environment 在 `OA_ENV=production` 时显式写 `OA_AUTH_MODE: oidc`；轮换 `OA_JWT_SECRET` 与 `.env` 内全部弱口令/主密钥。

### S2 · "记忆与上下文系统"这个自有核心，仓库里完全不存在

- **文件/模块**：`deploy/init-schema-phase2.sql` + `init-schema-phase3.sql`（全库 34 张表）；`app/services/model_service.py:431/752`；`apps/web/src/hooks/use-conversation-store.ts:77`；`apps/web/src/components/chat/chat-window.tsx:39`
- **问题**：34 张表中**没有** `conversation` / `message` / `user_memory` 任何一张。上下文只以 `conversation_id` 透传给底座，消息内容从不落自研库；前端会话列表是 zustand 内存 store（无 `persist`），消息只在组件内存里，页面挂载也不回拉历史。接口面只有 M1 生成 / M2 流式 / M3 目录 / M4 用量，**没有"拉取历史消息"端点**。
- **影响**：用户刷新即失忆；跨会话记忆、偏好记忆（PRD `P0-02`、`prd-v1.0.md:82`）零实现。更要命的是上下文资产沉淀在 Dify 侧，与"自有核心＝记忆与上下文、数据重力"的战略完全相反，换底座即失忆。
- **改进建议**：新增 `conversation` / `message` / `user_memory` 三表（含 `tenant_id` + RLS），M1/M2 落库消息，新增 `/api/v1/model/conversations*` 端点做历史回拉与删除（呼应 PRD 5.4 数据权利）；前端 store 接 `persist` 并改为接口加载。

### S3 · 四个能力档位名存实亡：档位→底座应用的路由从未接线

- **文件/模块**：`app/registry/profile_routes.yaml`；`app/services/model_service.py:238/393-406/427/451-453`；`app/registry/loader.py:97`
- **问题**：`profile_routes.yaml` 精心定义了 general / code / long_context / reasoning 四档及各自 `default_app_env` + `fallback_app_env`，`loader.py:97` 也有 `resolve_app_key()`，`model_service.py:393` 还包了一层同名方法——但**这一层没有任何调用点**。`generate` 在 `:427` 只调 `resolve_route(request.profile)` 做"档位是否可用"的校验，随后 `:451` 用构造时注入的**单一 client**（`:238` `self._dify = dify or get_dify_client()`）发请求，**不传 api_key**。也就是说四档共用默认应用密钥。`chunk_policy_defaults` 同样只被 loader 解析、无消费者。
- **影响**：M3 目录对外展示 4 个档位（"编程助手""长文档助手""深度推理助手"），用户切换后行为完全一致——对付费用户是**功能欺骗**；也让"多模型矩阵/Q2 决策"全部落空。
- **改进建议**：`model_service.generate` 中调用 `resolve_app_key(profile)` 并把结果传给 `DifyClient.for_app_key(...)`；补一条契约测试断言"不同 profile 解析出不同 app key"，否则 CI 红灯。

### S4 · 用量计量可被绕过，且没有任何硬止损

- **文件/模块**：`app/business/usage_service.py:154`（`over_quota_warning` 注释自认"软提示，不停服"）；`app/core/config.py:168-172`（`usage_estimate_prompt_units=800` 兜底）
- **问题**：计量单位是字符数折算而非真实 token；流式中断、下游未回传时按 800 字估算；缺少 `user_id` 的调用路径存在零计量分支；超额只有前端软提示，服务层不拒绝。
- **影响**：对独立开发者是**直接的现金流风险**——一个脚本就能无限刷对话，账单先于告警到来；计费口径与实际模型成本（token）脱钩，毛利算不准。
- **改进建议**：M1/M2 入口强制校验 `user_id`（缺失即 20001），`remaining_units <= 0` 时返回业务错误码硬拒绝；`usage_event` 增加 `profile` / `provider` / `prompt_tokens` / `completion_tokens` 维度，做租户月度预算上限。

### S5 · 监控栈是空转的：Prometheus 抓的全是不存在的目标

- **文件/模块**：`deploy/prometheus.yml`；`deploy/docker-compose.yml:517-528`
- **问题**：4 个抓取 job 里——`adapter` 用 `metrics_path: /healthz`（返回 `{code,message,data}` JSON，Prometheus 解析必然失败；`pyproject.toml` 里根本没 `prometheus_client`）；`valkey-exporter:9121` 与 `postgres-exporter:9187` **两个服务在 compose 里不存在**（grep `exporter` 命中 0）；`milvus:9091` 端口未暴露。全仓无告警规则文件、无 Alertmanager、无 Grafana（ADR-0002 因 AGPL 移除后无替代）。
- **影响**：生产环境零有效指标、零告警、零可视化；故障只能等用户投诉，用量突增（S4）也无法被第一时间发现。
- **改进建议**：适配层引入 `prometheus_client` 暴露标准 `/metrics`（请求量/延迟/错误码/任务队列深度/用量），compose 补两个官方 exporter，新增 `deploy/alert.rules.yml` 覆盖"5xx 率、任务积压、额度超阈值"。

### S6 · SaaS 编排仍用超级用户 DSN，多租户隔离在最该生效的模式里失效

- **文件/模块**：`deploy/docker-compose.saas.yml:361` vs `deploy/docker-compose.yml:463-469`；`deploy/init-app-role.sql:25-33`
- **问题**：私有化编排已在 `a5893dd` 改为优先取 `.env` 的 `OA_DATABASE_URL`（`officeai_app`，NOSUPERUSER + NOBYPASSRLS）；SaaS 编排第 361 行仍写死 `postgresql+asyncpg://${OA_POSTGRES_USER}...@postgres/...`——库属主/超级用户，`FORCE ROW LEVEL SECURITY` 也约束不住。而 SaaS 恰恰是多租户场景。
- **影响**：按 SaaS 编排部署时，`init-schema-phase2.sql:642-661` 给全部业务表装的 RLS 策略全部被绕过，跨租户可读。
- **改进建议**：`docker-compose.saas.yml:361` 改为 `OA_DATABASE_URL: ${OA_DATABASE_URL:?必须使用 officeai_app 应用角色}`；CI 增加断言：两个 compose 中 `OA_DATABASE_URL` 不得引用 `${OA_POSTGRES_USER}`。

### S7 · CI 从未真正跑通，且当前配置下必然红灯

- **文件/模块**：`.github/workflows/ci-adapter.yml:111/157/165`；`README.md:202-204`
- **问题**：workflow **没有 `services: postgres`**（第 111 行只有注释说明需手工 `--run-pg`），却要求 `--cov-fail-under=85`。实测无 PG 时覆盖率 84.19% 且必现 1 条 failed（依赖未注入的 `OA_BATCH`）；141 条 PG 依赖用例恒被跳过——含 `tests/integration/test_tenant_isolation.py` 的 15 条租户隔离用例、`test_task_repo_pg.py` 28 条。README 对外写的却是"1573 passed / 0 failed / 90.78%"。叠加 Actions 额度耗尽，CI 历史上一遍都没跑过。
- **影响**：**多租户隔离这条最高红线从未被自动验证**；对外数字不可复现，客户复跑即穿帮。
- **改进建议**：`ci-adapter.yml` 增加 `services: postgres:18.6-alpine` + `OA_TEST_DATABASE_URL` + `OA_BATCH`；README 与验收报告数字全部加口径脚注（含 PG / 含 OA_BATCH / 耗时）。

### S8 · 合规门禁 100% 恒红（三个独立故障点），且交付物没有 SBOM

- **文件/模块**：`.github/workflows/dependency-scan.yml:234-239/275/280/291-303/353`；`deploy/images.lock:120/123/128/133`；`deploy/offline/plugins/deepseek-0.0.25.difypkg`
- **问题**：`license-scan` job 有三个各自足以判红的缺陷——① `:280` squid 在 `images.lock:133` 明写 `license: GPL-2.0`，命中 `:234` 的 `BLACKLIST`（`\bGPL\b`），而 `:239` 的 `GREYLIST` 只含 LGPL/MPL/CDDL/EPL，**不含 GPL**（与 `images.lock:128` 注释"按灰名单独立进程处理"的口径自相矛盾）；② `:291-303` 遗留 MinIO 死规则，强制 images.lock 登记 `minio/minio` 且 tag 恰为 `RELEASE.2021-04-22T15-44-28Z`，而镜像早已换 SeaweedFS；③ `:275` 用 `yaml.safe_load` 但该 job（`:179`）只装 `pip-licenses`、**未装 pyyaml** → 直接 `NameError`。全 workflow `continue-on-error` 零命中。此外离线包 `.difypkg` 内含 36 个精确 pin 依赖（`dify-plugin`/`flask`/`gevent`/`jinja2`/`werkzeug` 等）无任何 SCA 覆盖；产物只是两份 license 清单，**不是 SBOM**（无 CycloneDX/SPDX、不覆盖镜像层）。
- **影响**：整个开源合规验收（ADR-0002 AC-11）**无任何自动化背书**，只有人工文档声明；私有化客户索要合规材料时现有产物不足以交付。注意：squid 以独立容器运行、不引入核心代码，**不违反 AC-11 字面**——这是"门禁实现落后于 ADR 判定口径"的工程缺陷，不是法律违规。
- **改进建议**：三处**必须同修**才会变绿——删 MinIO 死规则 + 给 images.lock 加白名单式 `distribution: independent-container` 豁免字段（缺字段 = 默认判黑，切勿把 GPL 塞进 GREYLIST 正则）+ 补 pyyaml；新增 `deploy/validate_offline_pkgs.py` 解包 `.difypkg` 扫描协议与 CVE；产出 CycloneDX SBOM 覆盖 py/npm/镜像/difypkg 四层。

### S9 · 产物下载端点无鉴权，且令牌永久有效

- **文件/模块**：`app/api/v1/router.py:274-275`（挂载时**未带 `business_dependencies`**）；`app/api/v1/artifact_files.py`；`app/core/artifact_token.py:26-40/63-92`；`app/db/repositories/artifact_repo.py:220`
- **问题**：产物下载走独立路由、不经 `/api/v1`、**不挂鉴权与限流**。令牌形如 `base64(tenant|artifact|version) + HMAC`——签名是有的（`hmac.compare_digest`，这点做得对），但**载荷里根本没有时间戳字段**，`parse_token` 也不可能校验过期，因此令牌一旦泄漏即**永久有效**；且 `jwt_secret` 为空时回落硬编码 `_DEV_KEY`（`artifact_token.py:27/40`）。服务端取产物时也不看 `expires_at`（`artifact_repo.get()` 直接返回记录），所以"180 天产物保留期"只是前端的展示约定，不是访问控制。
- **影响**：产物含 Offer 薪资、简历、合同草稿等最高敏数据——URL 一旦外泄（浏览器历史、日志、转发、客服截图）即永久可下载，且无法追溯是谁下的、也无法主动吊销。
- **改进建议**：下载端点纳入统一鉴权 + 限流；令牌载荷加 `exp`（短 TTL，如 10 分钟）并在 `parse_token` 强制校验；`jwt_secret` 为空时**拒绝签发**而非回落 `_DEV_KEY`；`artifact_repo.get()` 增加 `expires_at > now()` 条件；日志只记 `artifact_id` 不记令牌。

---

## 中等（M）：影响可扩展性、可维护性与长期成本

| # | 文件 / 模块 | 问题描述 | 影响范围 | 改进建议 |
| --- | --- | --- | --- | --- |
| M1 | `app/core/ratelimit.py:151`；`app/core/config.py:220-223` | `RedisRateLimitStore` 是死代码——`RateLimiter.__init__` 硬编码 `InMemoryRateLimitStore()`，文档声称的 `OA_RATELIMIT_BACKEND` **在 `Settings` 里根本不存在**（`extra="ignore"` 静默丢弃），实际引用点只有单测。幂等同理默认 `memory`。 | 多副本下每副本独立计数，真实配额 = 120 × 副本数；幂等键重启即失效 | `Settings` 增 `ratelimit_backend: Literal["memory","redis"]`，`create_app` 按配置构造 store；生产默认 redis |
| M2 | `app/business/task_service.py:334/757`；`app/core/config.py:211` | 无 celery/arq/BackgroundTasks，任务在 HTTP 请求内同步推进（单步上限 600s × 最多 8 步）；`plugin_task_timeout_seconds=900` **全仓仅出现在定义处，从未被读取**；`engine.py:1046` 把 `timeout_seconds` 塞进 params 后 `engine.py:557-558` 直接 `await gateway`，**无 `wait_for`**，实际不强制超时；无跨进程锁。 | 一次创建请求可能阻塞数分钟→网关超时、worker 饥饿；单步超时形同虚设；多副本重复推进同一任务 | 引入 Valkey 队列 + 独立 worker；`_advance_record` 外层加 `asyncio.wait_for` 与分布式锁 |
| M3 | `app/core/config.py:79`；`app/services/model_service.py:61/1085-1092`；`agent_service.py:50`；`rag_service.py:67-69` | **底座地址出口已收口**（`dify_base_url` 现在只出现在 `core/config.py` 与 `clients/dify_client.py`，`agent_service` 已改用 `DifyClient.for_app_key()`，OA-AUDIT-001 的 P1-1 已修）。但**协议路径常量仍散落在 3 个 service**：`model_service.py:61` 的 `/v1/chat-messages`、`agent_service.py:50` 的 `/v1/workflows/run`、`rag_service.py:67-69` 的 `/v1/datasets` 与 `/v1/files/upload`——底座 API 形态并未真正被 `clients` 收拢；`model_service.py:1085-1092` 还硬编码解析 `<!--dify-deepseek-reasoning-->` 标记。全仓含 `dify` 字样 110 处 / 11 文件。 | 换底座要改的是 clients + 3 个 service 的路径与载荷，不是"只改适配层一句话"；DeepSeek 的推理标记把单一厂商格式写进了服务层 | 把 3 处路径常量与载荷构造下沉到 `clients/dify_client.py`（暴露 `chat()` / `run_workflow()` / `datasets()` 语义方法）；厂商专属标记解析抽成可插拔 parser，按 profile 注册 |
| M4 | `app/api/v1/artifact_files.py:18/48`；`identity_ext.py:40/655`；`router.py:49`；`app/business/task_service.py:175`；`app/services/rag_service.py:33-38`；`app/db/repositories/audit_repo.py:28-62`、`role_repo.py:313/550` | 分层不是声称的线性链，而是交叉图：API 层直接 import repositories（3 处，`artifact_files` 甚至直接 `get_artifact_store(...)`）；business 层 18 个文件直接 import repositories，却 **0 个 import services / clients**（"business→services"这条链实际不存在）；`task_service.py:175` 写 `from app.api.v1.plugin_deps import build_action_gateway`——**business 反向依赖 api**（用函数内延迟 import 规避了加载期循环，但组合根仍错在 api 层）；services 反向依赖 business（`rag_service` 一次依赖 6 个业务服务）且直连 repo；repo 层还反向 import business（`audit_repo`/`role_repo`）。 | 新增功能时无法判断"该写在哪一层"；装配逻辑错位让"替换实现"无从下手；延迟 import 把循环藏起来，问题只是从导入期推迟到调用期 | 把 `build_action_gateway` 等装配逻辑抽到 `app/bootstrap.py`，business/services 只依赖抽象 Protocol；共享 domain types/constants 下沉到 `app/domain/`；CI 加 import 方向门禁（禁止 api→db、business→api、repo→business） |
| M5 | `app/db/repositories/billing_repo.py`（2272 行 / 92 函数 / 15 类）；`app/plugins/engine.py`（1517 / 42）；`app/business/task_service.py`（1522 / 36）；`app/business/plugin_service.py`（1422 / 31）；`app/business/order_service.py:44` ↔ `subscription_service.py:148-163` | 四个上帝文件各自混 5-8 类职责：`billing_repo` 把 5 类实体 + 默认套餐 + Protocol + 内存实现 + PG 实现 + row mapper 全塞一起；`task_service` 同时做准入/并发队列/状态机/产物落库/审计/计量结算/重试重放/取消查询/版本化；`engine.py:703-748` 按 `output_key`（offer/schedule/screening）硬编码三个业务域护栏。横向依赖 42 条，`ops_service.py:36-40` 单模块扇出 6 个；`order_service`↔`subscription_service` 用"函数内延迟 import"掩盖真实双向环。 | 改一处牵一片；新人定位成本高；单测必须 mock 半仓 | `task_service` 拆 Admission/Runner/Queue/Retry/Query/Artifact 六服务，审计与计量改领域事件；`engine` 拆通用执行器 + `GuardRegistry`（场景 guard 以注册方式注入，不要按 output_key 写死）；`billing_repo` 按聚合拆 6 个 repo |
| M6 | `app/plugins/schema.py:3-6/81-116/183-186/311-324`；`app/plugins/loader.py:231-330/583-606`；`app/plugins/actions.py:28-36/54-62`；`app/plugins/engine.py:703-748` | 插件是"**受限声明模板**"而非可装载代码：禁 entry/module/image/command/deps/URL/凭据且 `extra=forbid`；原语与授权各封闭 6 项；UI 仅固定 `task_workspace` 一种、无自有前端；渲染格式仅 docx/md/csv；`loader` 首次加载后单例缓存、**无热插拔**。隔离边界是"根本不执行插件代码"（RunContext 不含凭据、只有 Protocol 网关），没有 subprocess/容器/CPU 内存限制/导入白名单——是 schema 级隔离，不是 OS 沙箱。 | 保守设计换来安全性（这点值得肯定），但天花板很低：无法接入需要自有 UI、自有依赖或复杂交互的场景；第三方开发者几乎无发挥空间 | 短期保留封闭集合；中期提供"渲染组件注册表"（插件可引用平台注册的 React 组件）与"受审白名单依赖"，把 `engine` 的场景护栏改为可注册策略 |
| M7 | `app/services/tool_service.py:75-82`；`app/registry/tool_registry.yaml:3-16`；`app/plugins/actions.py:112-149`；`apps/adapter/README.md:170-175` | 新增工具至少改 3 处（YAML 条目 + connector.py + 手工加入 `_CONNECTOR_MODULES` 元组），涉及外发/目录/写权限还要改 `actions.py` 的能力映射——README 却写"新增工具只加这里"。`tool_service` 确无 `if tool_type==...`（按 registry + ConnectorRegistry 分派，这点好），但 `action_gateway.py:167-176` 仍按 6 个原语 if/elif；全仓 grep `tool_calls`/`function_call` **命中 0**，即工具调用不是 function calling，而是平台预定义操作白名单。 | 宣称的"数据驱动零改动"打折扣；能力天花板远低于 Claude Code / Codex 的通用工具调用与 MCP 生态 | `_CONNECTOR_MODULES` 改 `pkgutil` 自动发现；中期接入 MCP 作为扩展总线，把现有 5 个连接器改写为 MCP server |
| M8 | `app/db/migrate.py:12/224-284/425-480`；`deploy/migrations/`；`deploy/init-schema-phase3.sql`；`deploy/.env.example:186-187` | 迁移有四坑（口径已定稿）：① 回滚目录**代码里写了（`migrate.py:12`）但仓库没有任何一条 down SQL**，CLI 也无自动回滚；② `OA_DB_AUTO_MIGRATE` 的**代码默认值是 `False`（`config.py:124`），但 `.env.example:187` 与 `deploy/.env` 都写 `true`**，而 README 明确要求 `cp .env.example .env` → 照抄模板即开启"每个实例启动都迁移"，且 `apply_migrations` **无 advisory lock**，多副本启动存在 DDL 竞态；③ 0004/0005 在**新库零 DDL 不 RAISE**（全部 `IF NOT EXISTS`），只在存量旧库缺列时阻断，风险窗口取决于交付对象是新库还是存量库；④ `init-schema-phase3.sql` 建了 9 张插件表却**没播 `0003_phase3_plugin_tables` 哨兵版本行**，而 `migrations/0003` 的守卫是"表存在就跳过"→ 版本表与库真实状态失真，`--check` 的 pending/orphan 结论不可信。（已澄清：迁移用的平台哨兵 `SET` 是**事务级**、结束自动复位，**不存在**污染连接池的越权问题。） | 私有化升级无退路；多副本部署可能并发改表；版本台账不可信会让后续所有迁移判断失效 | 补真实 down SQL 与回滚演练；生产改为独立单实例迁移 Job、禁 Web Pod auto-migrate（`.env.example` 改回 `false`）；`apply_migrations` 加 `pg_advisory_lock` 并把 DDL 与台账写入同一事务；phase3 initdb 基线补哨兵种子行 |
| M9 | `apps/adapter/pyproject.toml`（16 条依赖 / 9 条无上界）；`apps/adapter/Dockerfile:32`；`apps/web/Dockerfile:17-19` | Python **零 lock 文件**（无 uv.lock/requirements.txt），容器内 `pip install .` 每次解析；`fastapi>=0.156,<0.157` 之外还有 `uvicorn>=0.30`、`redis>=5.0`、`httpx>=0.27,<0.29` 等无上界或宽区间。前端 `apps/web` 无 `package-lock.json`，Dockerfile 恒回退 `npm install`。 | 交付物不可复现、不可回滚、版本不可审计；上游发新版即可让客户环境漂移 | 生成并提交 `uv.lock` + `requirements.txt`；容器改 `pip install --require-hashes -r requirements.txt`；前端构建统一在仓库根用 `npm ci` |
| M10 | `apps/web/package.json`（无 test 脚本 / 无 jest·vitest·playwright）；`README.md:42`（写 Next.js 14，实际 `next ^16.4.2` + `react ^19.3.1`）；`app/api/v1/router.py:69`（`PHASE3=14` vs README 的 16）；`apps/adapter/README.md:186-193`（仍 T01 口径） | 前端零自动化测试；多处文档与代码漂移（框架版本、端点数、阶段口径）。 | 前端回归靠人眼；新人按 README 上手即被误导 | 引入 vitest + Playwright 覆盖登录/对话/装插件/下单四条主路径；把"README 声明的框架版本与端点数"纳入 CI 校验 |
| M11 | `app/business/usage_service.py`；`app/business/task_service.py:714` | 计量与成本无观测维度：units = 字数折算，不区分模型档位与 provider，无法核算"哪个功能烧钱"；聚合查询分散在多处，M4/BL5 单次请求多次查表。 | 定价与成本优化缺数据支撑；毛利不可核算 | `usage_event` 增 `profile`/`provider`/token 维度；出"按功能/按租户"的成本看板；聚合查询合并为单条 CTE |
| M12 | `deploy/docker-compose.yml`（`memory:`/`cpus:` 命中 0；18 服务仅 6 个 healthcheck） | 全栈容器无资源约束，Milvus + PG + etcd + Dify 全家桶同机争抢。 | 私有化单机部署极易 OOM；故障服务不会被发现 | 为 milvus / postgres / dify-api / adapter 补 `deploy.resources.limits`；healthcheck 补齐到全部有状态服务 |
| M13 | 全库 34 张表；`deploy/init-schema-phase*`（无保留期/删除策略）；`app/core/config.py:150/211` | **没有数据保留与删除的实现**：`segment_map_retention_days=90`（仅 stale 映射行）与 `plugin_artifact_retention_days=180`（仅产物链接失效）是唯二留存配置；审计、用量、文档、知识库、第三方凭据**均无 TTL、无定时清理任务**；全仓也**找不到任何租户删除或数据导出能力**（`grep delete_tenant` 零命中，组织域只有部门软删除）。 | 无法满足 GDPR/个保法的删除权与存储期限要求；客户退租时数据无退出通道，也无法自证"已删除" | 定义分级保留策略（审计 180 天、用量 13 个月）并落地定时清理任务；补租户级"导出 + 删除"端点（删除需覆盖审计/用量/outbox/回调流水/凭据五类） |
| M14 | `app/core/security.py:263-267` | 函数级权限检查在**解析不到任何角色时直接放行**（`if not roles: logger.debug("permission_check_deferred"); return`），代码注释声明"由业务能力层做最终判权（对象级 fail-closed）"。设计意图清楚，但它把安全责任下推给了每个端点的对象级校验——**任何一个端点漏做对象级判权，就是静默越权**。 | 权限体系一旦配置缺失（角色未同步、组织未初始化、令牌缺 claims），函数级检查退回"放行"；风险面随端点数线性增长 | 无角色时按最小权限拒绝并记审计（失败关闭），确需委派则加显式白名单并纳入 CI 断言：所有写端点的对象级校验必须存在 |
| M15 | 仓库 `docs/`（51 个文件：架构/PRD/QA/验收/ADR/接口规范）；`docs/prd-v1.0.md:379-383` | **零合规文档**：无隐私政策、无数据处理协议（DPA）、无等保/GDPR 合规说明、无安全白皮书。PRD 5.4 声明了数据权利（可查看/可删除），但既无文档也无对应接口（见 M13）。跨境调用境外模型（GPT-4o/Claude，Q2 已定为 V1.1）在代码里**没有强制开关或出境管控点**，只能靠 `OA_DEPLOY_MODE=private` 间接约束——而部署模式是运维填的，不是技术强制。 | 无法向客户/监管提供合规材料；"数据不出境"只是一句部署约定，没有技术兜底 | 补隐私政策 + DPA 模板 + 安全白皮书；把"数据不出境"做成显式开关（私有化模式下 `Settings` 校验拒绝境外 provider）并在 `/readyz` 自检 |
| M16 | `deploy/docker-compose.yml:50-62` vs `docker-compose.saas.yml:34-43` | **两套编排的库初始化路径不等价**（实测挂载）：私有化挂 4 个脚本（`00-init-databases` / `20-phase2` / `22-phase3` / `30-init-app-role`），SaaS 挂 5 个（多一个 `10-init-rls.sql`）。多出来的那个是**孤儿文件**：其内容为 `audit_log` 建表 + `tenant_isolation_*` 旧策略，而这些在 phase2 已由 `p_tenant_isolation` 取代（私有化 compose 第 53 行注释也点明"用 01- 会先于 10-init-rls.sql 执行，导致新旧策略共存"）。另私有化第 53 行注释引用了已不存在的 `10-init-rls.sql`。 | 两套部署形态产出的库结构不同；SaaS 侧新旧策略共存（均为租户级、permissive OR，不会削弱隔离，但策略翻倍、审计口径分裂），后续排障与合规核查要区分两套库 | 删除 `init-rls.sql` 或重命名为 `99-archive-*.sql` 并加"历史存档、勿挂载"声明；两套 compose 的 initdb 挂载集合由门禁脚本强制一致 |

---

## 轻微（L）：技术债与体验细节

| # | 文件 / 模块 | 问题 | 改进建议 |
| --- | --- | --- | --- |
| L1 | `deploy/init-app-role.sql` 第 4 节；`app/business/audit_service.py` | 审计"只增不改"靠 DB 层 `REVOKE UPDATE, DELETE`（有 PG 实跑测试 `test_audit_log_immutable.py` 证明，这点做得好），但无 hash 链、无导出端点，DBA 仍可改 | 增加 `prev_hash`/`row_hash` 链 + 运营后台 CSV 导出 |
| L2 | `deploy/docker-compose.yml:79/87`（saas `:59/67`） | 缓存与对象存储口令以命令行参数传入，`ps` / `docker inspect` 明文可见 | 改环境变量或配置文件挂载 |
| L3 | 工作区游离物 | `apps/adapter/build/`、`.whlextract/`、`.whlprobe/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、根 `node_modules/`（29,199 文件）、`qa_probe/`、`scripts/demo_phase3.sh`、`deploy/.env.bak.2255`、根 `.seed.sql` | 除 `qa_probe/` 长期资产外全部清理或补 `.gitignore` |
| L4 | `apps/adapter/pyproject.toml:36` | `slowapi` 已声明但全仓零引用（限流是自研 `RateLimiter`） | 删除该依赖，减少供应链面与认知负担 |
| L5 | `app/services/document_service.py`；`app/tools/*` | 上传无 MIME 白名单校验与病毒扫描；SSRF 代理只覆盖底座出站，适配层 5 个连接器出站不受约束 | 上传按扩展名 + 魔数双校验；连接器出站走统一 SSRF 白名单 |
| L6 | `app/main.py:140-166` | `/docs`、`/redoc`、`/openapi.json` 无条件开放（生产也开）；`env == "development"` 时 CORS 全开且带凭据，无防呆 | 非开发环境关闭文档端点或加 Basic 保护；CORS 白名单改为显式配置且禁止 `allow_credentials` 与 `*` 共存 |
| L7 | 分支与文档 | 文档要求 `develop` 主线，实际只有 `main`（322 提交）；验收报告基线写 `develop @ sha`；`architecture-v1.0.md` 版本号三处不自洽、部分文档日期晚于审计日；README 内"分支覆盖率余量为 0：2176/2840 刚好达到 76.6%"与同页"分支 2315/2862 = 80.89%"两处口径并存 | 二选一：恢复 `develop` 或统一改文档口径；版本号、日期与质量数字全部改为 CI 生成回填，禁止手写 |
| L8 | `apps/web/src` | 1.9 万行仅 86 处断点用法（响应式薄弱）；无 i18n；**缺"工具连接"页面**（工具授权只能在插件内隐式完成） | 补工具连接页与移动端断点；i18n 可延后到有海外客户时 |
| L9 | `apps/adapter/pyproject.toml:187-193`（coverage `exclude_lines`） | 排除规则里有一条 `"\\.\\.\\."`，它会命中所有 `...` 字面量（`cast("ApiResponse[...]", ...)`、`_ = (self, ...)` 之类），把真实代码行从覆盖率分母里剔掉；同一配置还排除了 `NotImplementedError` 与 `@abstractmethod`。这三条都是"没实现就走人"的典型写法，保留其一是合理的，但用 `...` 泛匹配代价最大、收益最小。 | 把 `"\\.\\.\\."` 换成 `"^\s*\.\.\.$"`（只匹配纯省略号行），其余改 `# pragma: no cover` 精确标注；重跑一次确认数字变化并回填 README |

---

## 值得保留的做法（勿在整改中误伤）

1. **租户隔离是双保险且自检到位**：RLS 通过 DO 块批量施加（strict 21 表 + shared 3 表），`officeai_app` 强制 `NOSUPERUSER NOBYPASSRLS`，`/readyz` 带 `db_privilege` 自检——主动识别"超级用户绕过 RLS"这个隐蔽陷阱，投入超出常规。
2. **镜像供应链治理扎实**：`images.lock` 唯一事实来源，`latest` 浮动标签 0 命中，且记录了 MinIO 仓库下架、合成标签事故等**实测结论**（缺陷在门禁代码，不在这份清单）。
3. **静态门禁零妥协**：mypy 在 `disallow_untyped_defs=true` 下 117 文件零 error；ruff 开 12 组规则仍全绿；测试 52 unit + 42 integration + 11 e2e、共 1598 条。
4. **技术黑盒化落到实处**：错误码三处一致自动化、内部码 17002 零外泄、前端术语扫描 0 命中。
5. **插件"不含可执行代码"的保守设计**：虽然是能力天花板（M6），但在没有沙箱能力的前提下，这是正确的安全取舍，不要在补齐能力时轻易放弃。
6. **工具分派确实数据驱动**：`tool_service.py` 零 `if tool_type == ...` 分支，按 registry + ConnectorRegistry 分派。

---

## 最优先的下一步行动（5 条，建议两周内完成）

| 优先级 | 行动项 | 验收判据 |
| --- | --- | --- |
| **P1** | **安全收口**：`Settings` 加 `production + stub` 启动校验；轮换 `OA_JWT_SECRET` 与部署侧弱口令；`docker-compose.saas.yml:361` 改走 `officeai_app`；产物下载端点纳入鉴权 + 一次性短 TTL 签名令牌 | 伪造 JWT 打生产配置时服务拒绝启动；SaaS 编排下 `/readyz` 的 `db_privilege=true`；产物 URL 过期后不可下载 |
| **P2** | **补上产品内核**：① 新增 `conversation`/`message`/`user_memory` 三表 + 历史回拉端点 + 前端持久化；② 把 `resolve_app_key(profile)` 真正接进 `model_service.generate` | 刷新页面后会话完整恢复；不同档位解析出不同底座应用密钥并有契约测试守护 |
| **P3** | **让门禁真的绿一次**：CI 加 `postgres` service + `OA_TEST_DATABASE_URL` + `OA_BATCH` 并恢复 Actions 额度；许可证门禁**三处同修**（删 MinIO 死规则 + 加 `distribution` 豁免字段 + 补 pyyaml）；补 `uv.lock` 与 CycloneDX SBOM | CI 全绿且 `test_tenant_isolation.py` 15 条在 CI 中真实执行；`license-scan` job EXIT 0 |
| **P4** | **装成本护栏**：M1/M2 强制 `user_id` 计量、`remaining_units<=0` 硬拒绝、`usage_event` 增 profile/token 维度、租户月度预算 | 模拟超额调用返回业务错误码；运营后台能看到"哪个功能烧了多少" |
| **P5** | **让监控与任务引擎可用**：适配层暴露标准 `/metrics`、compose 补两个 exporter、新增告警规则；任务推进移出 HTTP 请求（Valkey 队列 + worker）并让 `plugin_task_timeout_seconds` 真正生效 | 4 个抓取 job 全部 UP；故意触发 5xx 能收到告警；创建任务请求 200ms 内返回 |

> 顺序说明：P1 是"今天就能改完、不改就是裸奔"；P2 是唯一一条**决定产品是否真有护城河**的事——越晚做沉没成本越高（用户数据越沉淀在底座侧，迁移越痛）；P3 是把"纸面绿灯"变成"真绿灯"，否则前面所有整改都无法被验证；P4–P5 决定上线后能否稳定运营。

---

*本报告结论均基于 2026-10-22 对 `main` @ `fc3df7b` 的源码通读、门禁脚本复跑与多轮交叉验证，可复现。与 OA-AUDIT-001 互补，冲突处以代码事实为准。*
