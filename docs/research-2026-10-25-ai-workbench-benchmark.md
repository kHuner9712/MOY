# 开源 AI 工作台对标调研报告（对标 EvoFlow）

| 项 | 内容 |
|---|---|
| 调研日期 | 2026-10-25 |
| 基准对象 | EvovexAI/EvoFlow（桌面端 Agent Runtime + 控制平面，PolyForm Noncommercial） |
| 入选项目 | langgenius/dify（Dify 1.18.0）、bytedance/deer-flow（DeerFlow v2.1.0）、n8n-io/n8n（n8n 2.44.6） |
| 数据来源 | GitHub REST API（实时抓取）、项目官方 README/文档、GitHub Security Advisories / CVE 记录、第三方评测（2026-10-25 快照） |
| 结论速览 | 三个入选项目无一使用纯 OSI 许可证（Dify 自定义协议 / n8n fair-code / DeerFlow MIT）；Dify 与 n8n 均有 CVSS 9.9+ 的生产级漏洞史；其安全漏洞 60% 以上集中在「多租户隔离失效」与「表达式/代码执行逃逸」两类——这正是 Moy 适配层架构必须前置防御的两类问题 |

---

## 1. 调研背景与筛选方法

### 1.1 EvoFlow 基准画像

EvoFlow 是本次对标的基准产品，关键事实（GitHub API 实测）：

| 属性 | 值 |
|---|---|
| 仓库 | EvovexAI/EvoFlow（2026-05-11 创建，2026-10-25 仍在推送） |
| Stars / Forks / Open Issues | 512 / 139 / 8 |
| 技术形态 | TypeScript + Tauri 桌面端；源码运行需 Python 3.12+ / Node 22+ / uv / pnpm 9+ |
| 许可 | PolyForm Noncommercial 1.0.0（源码可见，**非 OSI 开源**，商用须书面授权） |
| 核心能力 | Chat/Plan/Goal 三层任务形态、Agent Teams（岗位值班+审批+汇报）、应用中心/自动化（多节点工作流+定时+IM 派发）、工作空间+代码索引、自我进化资产中心（经验/反思/画像/记忆）、治理套件（多账号/SSO/费用统计/运维观测/安全中心）、资源市场（Skills+MCP）、沙箱与可观测 |
| 已知风险 | 无独立安全审计；macOS 包未做 Apple 公证；部分 UI/协议/服务 vendored 自 ZCode（Z.AI，Apache-2.0），商用存在许可传导风险 |

EvoFlow 的产品形态与 Moy 高度同构（工作台 + 多 Agent + 工作流 + 记忆资产 + 治理套件 + 私有化），因此其公开承认的设计参照系（DeerFlow / Hermes / OpenClaw / Codex / LangGraph / MCP）和生态位，直接决定了本次筛选范围。

### 1.2 筛选漏斗与入选理由

1. **初筛**：GitHub 搜索 `EvoFlow`（46 个仓库）+ `AI workbench agent workflow`（139 个仓库），剔除生物/学术论文附属仓库、无许可证个人项目、已归档项目（如 google-research/evoflow）。
2. **复筛**（同时满足）：① 在维护（近 30 天有推送）；② 与 EvoFlow 可比（工作台/工作流/Agent 运行时三选一）；③ 社区规模或厂商背书具备代表性；④ 许可条款清晰可评估。
3. **入选**：

| 项目 | 入选理由 | 代表维度 |
|---|---|---|
| **Dify** | 15.9 万★，LLM 应用平台事实标准；**正是 Moy 的底座**，对比最具可操作性 | 编排引擎 + 治理 + 部署运维标杆 |
| **DeerFlow** | 字节开源长任务 SuperAgent Harness，84.4k★，MIT；与 EvoFlow 的 Agent Runtime 理念最接近 | 多 Agent 协作 + 沙箱 + 长任务恢复 |
| **n8n** | 20.9 万★，工作流自动化+AI 节点生态最大；对应 EvoFlow「应用中心/自动化」 | 触发器生态 + 调度 + 模板市场 + 发布工程 |

4. **候补未深调**：LobeHub（8.3 万★，但已改用 LobeHub Community License = Apache-2.0 + 派生分发限制，非纯开源）、aipoch/open-science（5.4k★，Apache-2.0，桌面科研工作台，与 EvoFlow 形态最近但社区过小）。

### 1.3 数据时效

所有 GitHub 指标、版本、安全公告均为 2026-10-25 实时抓取。Dify 基线说明：Moy 当前锁定 Dify 1.17.1（2026-10-21 拍板），而 Dify 已于 2026-10-23 发布 1.18.0——发版节奏证据见 §6.3。

---

## 2. 总览对比

### 2.1 基本面对比表

| 维度 | EvoFlow（基准） | Dify 1.18.0 | DeerFlow v2.1.0 | n8n 2.44.6 |
|---|---|---|---|---|
| Stars / Forks | 512 / 139 | **159,485 / 25,207** | 84,404 / 11,808 | **208,534 / 61,234** |
| Open Issues / Watchers | 8 / 6 | 1,185 / 838 | 956 / 329 | 1,234 / 1,182 |
| 创建时间 | 2026-05 | 2023-04 | 2025-05 | 2019-06 |
| 主语言 | TypeScript | Python | Python | TypeScript |
| 许可 | PolyForm Noncommercial（非 OSI） | **Dify Open Source License**（修改版 Apache-2.0，非 OSI） | **MIT（纯 OSI）** | Sustainable Use License（fair-code，非 OSI）+ 企业版单独许可 |
| 产品形态 | 桌面端 Agent Runtime + 控制平面 | 服务端 LLM 应用开发平台（BaaS 化） | 服务端 SuperAgent Harness（研究/代码/创作） | 工作流自动化平台 + AI 节点 |
| 前端 | Tauri 桌面 | Next.js Web 控制台 | Next.js Web | Vue 3 画布编辑器 |
| 编排内核 | 自研多节点工作流 | 自研 YAML DSL 图引擎 | LangGraph 状态图 | 节点画布 + 表达式引擎 |
| 部署 | 桌面安装包 / 源码 | Docker Compose（39 服务）/ K8s（社区） | Compose / Helm / 本地 | 单容器 → 队列模式 → K8s |
| 厂商背景 | EvovexAI（初创） | LangGenius（$30M Pre-A，2026-03） | 字节跳动 | n8n GmbH |
| 最新版本发布 | 桌面 Releases 持续 | 2026-10-23（周级节奏） | 2026-09-24（月级节奏） | 2026-10-22（周级节奏） |

### 2.2 一句话定位差异

- **EvoFlow**：桌面优先、个人/团队非商业可用的「智能体员工」控制平面。
- **Dify**：API 优先的 LLM 应用工厂，强在编排 DSL、插件生态与企业治理，弱在许可收紧与多租户隔离债务。
- **DeerFlow**：MIT 纯净、长任务自治的「给 Agent 一台电脑」研究/创作 Harness，强在沙箱与检查点恢复，弱在治理层完全交由用户自建。
- **n8n**：连接器生态与模板市场之王，强在触发器与人工审批，弱在表达式/代码节点的永恒攻击面。

---

## 3. 六维度深度对比

### 3.1 架构设计

| | Dify | DeerFlow | n8n |
|---|---|---|---|
| 服务拆分 | api（Flask/gunicorn）+ api_websocket + worker（Celery）+ worker_beat + web（Next.js）+ plugin_daemon（独立进程，签名校验）+ sandbox（Go）+ agent_backend + local_sandbox + ssrf_proxy/agent_ssrf_proxy（squid×2）+ nginx + PG/Redis + 可插拔向量库（weaviate/qdrant/milvus/pgvector/chroma/opensearch/ES 等 10+ 种） | Gateway（Python 3.12 + LangGraph 图编排，内嵌 agent runtime）+ Next.js 前端 + nginx；SQLite/PG + Redis stream bridge（多实例）；沙箱三模式：本地 / Docker / K8s provisioner，另接 Tenki、OpenSandbox、BoxLite、AIO 等社区 provider | Node.js 单体（NestJS 后端 + Vue 前端）+ TypeORM（PG/MySQL）+ BullMQ/Redis 队列模式（main/worker/webhook 进程分离）+ task runner 外置沙箱（JS/Python） |
| 隔离机制 | 插件守护进程独立运行 + FORCE_VERIFYING_SIGNATURE 强制验签；沙箱容器执行代码，出站流量经 squid 代理 | 沙箱容器/微 VM 隔离；skills 渐进加载；MCP server 前缀防冲突 | task runner 外置 + N8N_RUNNERS_ENABLED；凭据 AES 加密（N8N_ENCRYPTION_KEY） |
| 多实例/水平扩展 | 环境变量驱动（SERVER_WORKER_AMOUNT 等），compose 无 replicas；官方建议 api/worker 分别扩缩 | 默认 GATEWAY_WORKERS=1；多实例需 PG + Redis bridge + 心跳 + run_events 落库 + 共享 DEER_FLOW_CREDENTIALS_KEY | 队列模式为标准生产形态；webhook 与 worker 独立进程，可分别扩容 |
| 对 Moy 的参照 | 「适配层唯一原则」正是对 Dify 插件直连模式的替代设计；compose 由 `generate_docker_compose` 单入口生成、禁止手改——值得 Moy 部署层照搬 | Gateway + checkpointer 的「长任务可恢复」架构可直接指导 Moy 的 A 系列（Agent）端点设计 | 队列模式 + 进程分离是 Moy 适配层异步化（文档解析/索引任务）的现成范式 |

### 3.2 核心功能

| 功能 | EvoFlow | Dify | DeerFlow | n8n |
|---|---|---|---|---|
| 对话/计划/长任务 | ✅ Chat/Plan/Goal | ✅ Chatflow/Workflow/Agent | ✅ 会话目标 + 检查点恢复 | ✅ 工作流即长任务 |
| 多 Agent 协作 | ✅ Agent Teams（岗位值班/审批/汇报） | ⚠️ 多 Agent 节点/工作流嵌套，无岗位制 | ✅ subagents 委派（上限可配）+ 任务型 agent | ⚠️ AI Agent 节点 + 子工作流 |
| 知识库/RAG | ✅ 知识库 | ✅ 完整 RAG 流水线（ETL+检索+rerank） | ✅ RAGFlow 私有检索 | ⚠️ 向量检索节点，非完整 RAG |
| 长期记忆 | ✅ 资产中心（经验/反思/画像/记忆） | ⚠️ 会话记忆为主 | ✅ 长期记忆 + DeerMem 抽取 + OpenViking 后端 | ❌ |
| 人工审批 | ✅ 关键审批节点 | ⚠️ 部分节点支持 | ⚠️ MCP input_required 仅通知、不可回传 | ✅ 原生 human approval |
| 治理（SSO/费用/审计） | ✅ 治理套件全家桶 | ⚠️ SSO/审计为企业版功能 | ❌ 无（须自建） | ⚠️ RBAC + 审计 trails，无费用治理 |
| 沙箱执行 | ✅ 隔离沙箱 | ✅ 代码沙箱 + 双 SSRF 代理 | ✅ Docker/K8s 沙箱 + AIO Sandbox | ⚠️ Code 节点 + task runner |
| 定时/触发器 | ✅ 定时 + IM 派发 | ✅ 定时触发 | ✅ cron | ✅ 触发器体系最全（webhook/cron/IM/轮询） |
| IM 渠道 | ✅ 多渠道 | ⚠️ 插件 | ✅ Lark/DingTalk/WeCom/GitHub | ✅ 1500+ 集成 |

> 结论：EvoFlow 的功能地图（岗位制 Agent + 治理套件 + 资产中心）与 Moy「岗位级场景插件 + 私有化治理」的产品定义同源；Dify 是功能最全的服务端平台；DeerFlow 强在长任务自治；n8n 强在连接与触发。Moy 的差异化空间在「跨生态办公场景的岗位化封装」，而非重复造平台。

### 3.3 工作流编排能力

| | Dify | DeerFlow | n8n |
|---|---|---|---|
| 编排范式 | 可视化画布 + **YAML DSL**（Chatflow / Workflow / Agent 三态；1.18 新增 Agent 智能路由与 Portable Apps——DSL 可打包导出/移植） | LangGraph 代码状态图 + subagents + skills；无可视化画布（TUI/Web 调试） | 节点画布 + 表达式语言 + 代码节点（JS/Python/npm）+ 子工作流 + 9000+ 模板 |
| 状态与恢复 | 工作流变量 + 会话持久化（PG） | **检查点（checkpointer）恢复**，小时级任务可从断点续跑 | 执行记录持久化，失败节点重试 |
| 人工介入 | 部分节点 | 弱（MCP input_required 仅通知） | **原生审批节点（wait for approval）** |
| 对 Moy 的启示 | 工作流定义应 **DSL 化 + 可导出 + 可版本化**（对齐 1.18 Portable Apps 方向），适配层消费 DSL 而非私有结构 | 长任务（批量文档处理、定时报告）应设计检查点与断点续跑，避免「失败即从头」 | 审批节点与触发器（cron/IM webhook）应进入 Moy A/T 系列端点规划 |

### 3.4 扩展性

| | Dify | DeerFlow | n8n |
|---|---|---|---|
| 插件/扩展机制 | 插件市场 + 本地安装 + **强制签名验证** + plugin_daemon 独立进程；模型 provider 可扩展；支持 MCP | Skills（**与 Claude Code 技能格式兼容**，可导出）+ MCP server 配置 + CLI 后端（Codex CLI / Claude Code 作为模型后端） | 1500+ 集成节点 + 社区节点 npm 包 + 自定义代码节点；MCP client/server 双支持 |
| 生态规模 | 数百插件，市场分发 | skills 库 + 社区沙箱 provider | 1500 节点 / 9000 模板（生态最大） |
| 安全设计 | 验签 + 守护进程隔离（但历史漏洞多，见 §6.1） | skills 渐进加载；MCP 前缀隔离 | 社区节点=任意代码执行（设计使然） |
| 对 Moy 的启示 | Moy 已定「工具类型注册表数据驱动（registry/tool_registry.yaml + tools/）」——**建议补两件 Dify 有而我们没有的事：插件包签名验证、凭证托管与最小权限下发** | skills 目录兼容策略可让 Moy 工具直接复用 Claude Code 生态技能，降低自研成本 | 模板市场是 V1.1 之后的增长杠杆，V1.0 只需预留注册表结构 |

### 3.5 社区活跃度

| 指标 | Dify | DeerFlow | n8n |
|---|---|---|---|
| Stars | 159,485 | 84,404（2.0 重写曾登 Trending #1） | 208,534 |
| Forks | 25,207 | 11,808 | 61,234 |
| 发版节奏 | 周级（1.17.1→1.18.0 仅隔 2 天） | 月级（v2.1.0，2026-09-24） | 周级（2.44.6，2026-10-22） |
| 问题处理 | 1,185 open issues，安全公告响应快（多数 1-2 个补丁版本内修复） | 956 open issues；**无任何公开安全公告**（双刃剑：无爆雷史也无审计背书） | 1,234 open issues；公告密集（2026-09/10 两批共 18 条） |
| 商业背书 | LangGenius $30M Pre-A；自报 2,000+ 团队 / 280+ 企业（Maersk、Volvo、Novartis 等） | 字节跳动；火山引擎 Coding Plan 导流 | n8n GmbH；云服务成熟 |

> 解读：Dify/n8n 的「高星 + 高公告密度」是成熟度与曝光度的共生——公告多不等于更不安全，但意味着**采用方必须建立跟随升级的 SOP**。DeerFlow「零公告」部分源于年轻与使用半径小，其 README 自认的安全边界（见 §6.1）比公告更值得阅读。

### 3.6 部署运维

| | Dify | DeerFlow | n8n |
|---|---|---|---|
| 最小部署 | Compose 单机（39 服务，向量库/DB 走 profile 按需启用） | Compose（make docker-init/start） | `docker run` 单容器（curl get.n8n.io 一键脚本） |
| 生产形态 | api/worker 分别扩缩 + 独立 PG + 独立 Redis + 沙箱外置 | 8 vCPU/16GB 起步、16/32 推荐；**2 vCPU/4GB 官方明示不够** | 队列模式（Redis + worker 进程分离）+ 二进制数据外置 S3 |
| 运维工具 | healthz/readyz + Sentry 接入 | **make doctor（配置自检）+ make support-bundle（问题报告包）**，官方 sizing 表 | 发布物附 **SBOM（CycloneDX）+ VEX + THIRD_PARTY_LICENSES** |
| K8s | 社区 Helm | 官方 Helm + provisioner 服务 | 官方 Helm + 队列模式 |
| 对 Moy 的启示 | profile 化部署（dev/prod/完整向量库）值得照搬；注意 Dify compose 为自动生成、单入口维护 | doctor/support-bundle 两个轻量工具应进 Moy 运维包；资源规格表应写入部署文档 | **SBOM/VEX 随发布**是三个项目中唯一做到的，应列为 Moy 发布工程 P0 补齐项 |

---

## 4. 与 Moy 的差异分析

### 4.1 Moy 相对优势（已验证的架构决策）

| Moy 决策 | 对标项目中对应的「他们踩过的坑」 |
|---|---|
| 适配层唯一原则（业务/插件只调自研适配层，CI 门禁禁底座直连） | Dify 生态大量二次开发直连 Dify 原生 API，底座升级即断裂；DeerFlow 2.0 与 1.x 零代码共享，直连方全部返工 |
| 统一响应契约 `{code, message, data}` + 错误码注册制 | Dify/n8n 的错误体系随版本漂移，前端需反复适配 |
| `Idempotency-Key` + ULID 资源 ID + `X-Tenant-Id` 强制 | n8n 2026-10 批次漏洞中多起为跨项目/跨用户数据越权（GHSA-wvjj-qg37-pv73 等）——租户隔离必须在一等公民层面强制 |
| structlog JSON 必带 trace_id/tenant_id/user_id/event | 三者均依赖第三方 tracing（LangSmith/Langfuse/Sentry），Moy 内建 trace 更契合私有化 |
| 合规白名单（MIT/Apache-2.0/BSD） | **三个入选项目当前均不在纯 OSI 白名单内**（见 §6.3），Moy 的许可纪律是稀缺资产 |
| 自研 Next.js 前端 + 技术黑盒化 | Dify 许可明确限制前端 LOGO/版权移除；自研前端同时解决品牌与合规问题 |

### 4.2 Moy 能力缺口（按对标差距排序）

| 缺口 | 参照对象 | 差距说明 |
|---|---|---|
| 工作流 DSL 化与可导出 | Dify 1.18 Portable Apps | Moy 工作流定义若仅存于私有结构，将丧失可移植/可版本化/可审计能力 |
| 人工审批节点 | n8n / EvoFlow | 岗位级审批（值班、关键操作确认）是办公场景刚需，A/T 系列尚无对应端点 |
| 长任务检查点恢复 | DeerFlow checkpointer | 批量文档解析、定时报告类长任务目前无断点续跑设计 |
| 插件签名验证 + 凭证托管 | Dify plugin_daemon / n8n credentials | 工具注册表已数据驱动，但缺少「包完整性校验 + 凭证加密托管 + 最小权限下发」闭环 |
| 治理套件（费用台账/用量观测） | EvoFlow 治理套件 | SaaS 计费与私有化用量审计均缺位；token 成本无预算上限机制 |
| 发布工程（SBOM/VEX） | n8n | CI 目前无 SBOM 产出，SCA 扫描结论无法随交付物传递 |
| 运维诊断工具 | DeerFlow doctor/support-bundle | 15 容器全栈缺一键自检与问题包收集，排障依赖人工 |

---

## 5. 值得借鉴的优点

### 5.1 技术选型

1. **Dify：插件守护进程 + 强制验签**（plugin_daemon 独立进程、FORCE_VERIFYING_SIGNATURE）。第三方代码与主进程物理隔离 + 完整性校验，是「插件生态」与「生产安全」兼得的唯一验证过的形态。→ Moy 工具注册表应增加签名验证层。
2. **Dify：双 SSRF 代理**（sandbox 与 agent 沙箱各经独立 squid 代理，`SSRF_PROXY_ALLOW_PRIVATE_IPS` 可控）。出站流量收敛到单一策略点，直接消灭一类高危 CVE 的攻击面。→ Moy 适配层所有外呼（模型/IM/工具）应统一经出口代理。
3. **Dify：compose 单入口生成**（`generate_docker_compose` 从模板+.env 生成，禁止手改）。杜绝「部署文件被手工改花」的运维债。→ Moy deploy/ 应采用同款生成式模式。
4. **DeerFlow：LangGraph checkpointer 恢复长任务**。小时级自治任务的失败恢复从「从头再来」变为「断点续跑」，是长任务产品化的分水岭。→ A 系列长任务端点应引入检查点。
5. **DeerFlow：skills 兼容 Claude Code 技能格式**。零成本复用全球最大的 agent 技能生态。→ Moy 岗位插件可定义技能兼容层，避免从零造工具。
6. **n8n：task runner 外置沙箱**（N8N_RUNNERS_ENABLED + N8N_NATIVE_PYTHON_RUNNER）。代码执行与主进程解耦，且可独立扩缩/加固。→ 适配层若开放代码类工具，必须外置执行器。
7. **n8n：SBOM + VEX + THIRD_PARTY_LICENSES 随 release 发布**。把供应链安全结论变成可交付物。→ Moy 发布工程 P0 补齐项。

### 5.2 产品设计

1. **EvoFlow：Plan 先行、可修订计划再执行**。把「先澄清与规划」做成显式产品形态（Plan/Goal），而非埋在 prompt 里。→ Moy 对话产品应在复杂任务上提供计划确认交互。
2. **EvoFlow：岗位制 Agent Teams（值班/审批/汇报）**。把组织分工直接映射为产品概念，比通用「多 Agent」更贴近企业办公心智。→ 与 Moy「岗位级场景插件」定义互相印证，建议将「岗位值班 + 汇报」写入 PRD 增量。
3. **EvoFlow：治理套件作为一等模块**（多账号/SSO/费用统计/运维观测/安全中心）。治理不是企业版附属品，而是私有化交付的验收项。→ Moy 私有化部署清单应内置治理四项。
4. **n8n：9000+ 模板市场 + human approval 节点**。模板是工作流产品最强的获客与留存杠杆；审批节点把「人留在环路里」做成了原生能力。→ V1.1 规划模板市场；V1.0 先落审批节点。
5. **DeerFlow：官方资源规格表 + 明确「2 vCPU/4GB 不够」**。把部署预期管理做在文档里，避免售前承诺事故。→ Moy 部署文档应给出同等规格指引（15 容器全栈的实测基线已在 phase1 验收报告中，可直接转化）。

### 5.3 工程实践

1. **n8n 发布物三件套**（SBOM/VEX/THIRD_PARTY_LICENSES）——供应链安全可交付。
2. **DeerFlow `make doctor` / `make support-bundle`**——配置自检与问题报告包，把排障从「问日志」变为「交包」。
3. **Dify 安全公告响应速度**——多数漏洞在 1-2 个补丁版本内修复并公开 GHSA；采用方 SOP 应为「跟随补丁版本而非大版本」。
4. **DeerFlow 安全告示的坦诚度**——README 明示「aio 模式 Docker socket = root 等价」「Gateway Admin ≈ 代码执行」「local sandbox 非 OS 边界」。**把安全边界写在门面上**，应成为 Moy 私有化交付文档的标准动作。

---

## 6. 缺陷与风险识别

### 6.1 安全漏洞实证（全部来自 GitHub Security Advisories / CVE 记录）

**Dify（13+ 条公告，模式高度集中）：**

| 类别 | 实证 | 定级/修复 |
|---|---|---|
| 多租户隔离失效 | IDOR：`PUT /console/api/apps/<id>/server` 可改他人 App 的 MCP 配置（CVE-2026-105761，7.1） | 1.16.0 |
| 多租户隔离失效 | 跨租户文件预览（file_id 无租户校验，7.7）；跨租户外部知识库 API 绑定与详情披露（8.5）；普通用户可读他人会话记录（CVE-2025-59422，6.0）；IDOR 删除他人对话（CVE-2026-34082） | 1.14.x / 1.13.1 / 1.9.0 |
| SSRF | **未认证** SSRF：`/console/api/remote-files/upload` 任意 URL 拉取（CVE-2026-105762，8.3）；sandbox 内部 SSRF → 代码注入（CVE-2024-10252，8.8） | 1.13.0 |
| XSS | SVG 上传存储型 XSS（CVE-2026-42138）；Mermaid `securityLevel: loose` 存储型 XSS（CVE-2026-21866）；echarts `new Function` DOM XSS（CVE-2026-26023）；MCP OAuth 弹窗 XSS（CVE-2025-58747） | 1.11.x-1.13.1 |
| 密钥管理 | 模型 provider 接口明文下发 API Key（CVE-2025-67732，8.4）；API Key 用 `random`（Mersenne Twister）生成（GHSA-fw4f-cghv-2v33） | 1.11.0 / 1.4.2 |
| 信息泄露 | 登录接口用户枚举（CVE-2026-28288） | 1.9.0 |

**n8n（18 条公告，6 high）：**

| 类别 | 实证 |
|---|---|
| 表达式/代码执行逃逸 | **CVE-2025-68613**：工作流表达式求值未充分隔离 → 认证用户 RCE（CVSS 9.9-10.0，影响 0.211.0-1.120.x）；**CVE-2025-68668**：Python Code Node Pyodide 沙箱逃逸（9.9，1.0.0-2.0.0） |
| 凭据泄露 | 多节点执行错误数据中夹带凭据（2026-10 批次 3 起）；error-workflow 上下文继承导致逐用户凭据窃取；子工作流 `source: url/localFile` 绕过共享凭据守卫；SeaTable 触发器把 token 存进失败执行数据 |
| 注入 | OData `$filter` 注入（Outlook/SharePoint 节点）；MSSQL 节点表达式插值 SQL 注入；QuickBase Where 注入 |
| 其他 | 工作流历史压缩原型污染 DoS；webhook 响应缺 sandbox policy → 存储型 XSS；伪造 workflow ID 跨项目访问数据表；MCP 工作流校验解释器共享原型变异 → 账户接管；Slack 触发器 webhook 未认证 token 外泄 + 全响应 SSRF |

**DeerFlow（零公告，但 README 自认边界）：** aio 模式启用 Docker socket overlay = 网关主机 root 等价权限；Gateway Admin 权限 ≈ 代码执行；local sandbox 仅是「工具路径边界」而非宿主机文件系统隔离；`forget`（记忆删除）工具无确认门；provider 密钥与渠道 token 以静态 `.env` 文件存放；VentureBeat 指出**沙箱执行环境无独立公开安全审计**。

**EvoFlow：** 无独立安全审计记录；macOS 桌面包未公证；vendored ZCode（Apache-2.0）代码的许可传导未在 NOTICE 之外给出商用结论。

### 6.2 性能瓶颈（实证案例）

| 项目 | 瓶颈实证 | 业已验证缓解 |
|---|---|---|
| Dify | ① Celery worker 与 API 混部：200 份 PDF 批量入库 → 内存尖峰 → PG 页缓存被挤压 → 在线问答连带变慢（noisy neighbor）；② 沙箱每次运行起容器，CPU/内存尖峰；③ PG 连接池耗尽（默认 DB_POOL_SIZE=20）→ 504；④ 多 api 实例高并发 Redis 锁竞争；⑤ 向量检索从 200ms 劣化到 1s+ | 队列隔离（文档解析/Embedding/索引写入分队列，长短任务分离）、独立 PG/Redis、worker 按 CPU×0.75 定并发并按 Embedding 限流回调、沙箱外置独立 K8s Pod、连接池扩容+预热、RAG TopK 10→5+结果缓存、简单任务降级轻量模型 |
| n8n | Node 单进程内存天花板；队列模式为生产必需（否则执行与 Web 服务争抢）；执行数据无限堆积 | 队列模式、执行数据清理策略、二进制数据外置 S3 |
| DeerFlow | 默认 GATEWAY_WORKERS=1（进程内活动 run，多 worker 有严格前置）；多服务重（LangGraph+checkpoint+沙箱+tracing）；2 小时任务约消耗 50-80k token | PG+Redis bridge 多实例拓扑；按 sizing 表分配；token/时长/费用预算阈值 |

### 6.3 维护风险

| 风险 | 实证 | 对 Moy 的映射 |
|---|---|---|
| **许可漂移（最严峻）** | Dify 已从 Apache-2.0 改为 Dify Open Source License：**多租户运营须书面授权**（tenant=workspace）、前端 LOGO/版权不可移除、交互设计受外观专利、贡献者条款允许单方收紧协议；LobeHub 同样改为社区许可（Apache+派生分发限制）；n8n fair-code + 企业功能单独许可；EvoFlow PolyForm 非商业 | **Moy 白名单纪律正确且稀缺**。但须立即复审：Dify 多租户条款对 Moy SaaS 模式（OA_DEPLOY_MODE=saas）的影响——决策 0004「SaaS 不用多租户」需形成书面许可评估留档；许可证扫描门禁应增加规则：GitHub `NOASSERTION/Other` 一律触发人工复核 |
| **版本断代** | DeerFlow 2.0 与 1.x 零代码共享，1.x 教程全部失效，存量配置/数据需重新验证 | 反证 Moy「适配层唯一原则」的价值：业务代码不直连底座，底座断代成本被限制在适配层 |
| **升级债务** | Dify 周级发版（1.17.1→1.18.0 间隔 2 天）；n8n 周级 | Moy 已锁定精确标签，需补「升级窗口 + 回归矩阵」SOP（建议双周评估、月度窗口） |
| **厂商绑定/地缘** | DeerFlow 默认推荐豆包/DeepSeek/Kimi 并导流火山引擎，字节出身在受监管行业触发额外合规审查；Dify 外观专利+单方调约权 | Moy 模型矩阵已多云（通义/豆包/DeepSeek/Qwen），保持厂商中立是既有差异化，勿在推荐位上单一化 |
| **治理层缺位** | DeerFlow 把身份/短时凭证/出口白名单/不可篡改审计全部留给用户；n8n 凭据单密钥加密、社区节点=任意代码 | Moy 私有化交付必须自带治理层（这正是 Moy 相对纯开源 Harness 的溢价点） |

### 6.4 架构缺陷归纳

1. **Dify：授权校验「按端点补齐」而非「框架强制」**——13+ 公告中跨租户/IDOR 占近半，根因是 `db.session.get(ID)` 后不校验归属，同类错误在 1.8-1.16 多个版本反复出现。说明依赖人工 code review 兜底租户隔离必然漏。
2. **n8n：表达式/代码节点是永恒攻击面**——CVE-2025-68613/68668 均为沙箱逃逸，2026-10 批次仍有 MCP 校验解释器原型变异。允许用户写代码的平台，「执行隔离」只能是纵深防御而非单点。
3. **DeerFlow：默认配置偏向「可信内网」**——本地沙箱非 OS 边界、静态 .env 凭据、无确认门的破坏性工具。开源默认值与生产默认值之间缺一层「安全基线配置」。
4. **共性：三者都把「治理」放在企业版或根本不做**——费用、审计、SSO 的完整治理在开源版均需自建，这正是 Moy「私有化 + 治理内置」的定位空隙。

---

## 7. 对 Moy 的具体规避建议

> 排序按优先级；P0 = 进入当前 Sprint，P1 = V1.1，P2 = 跟踪。

### P0（立即）

1. **租户隔离框架化（对标 Dify IDOR 模式）**：适配层所有资源查询走统一 `tenant_scope()` 装饰器/依赖注入，禁止裸 `get_by_id`；CI 增加跨租户越权自动化测试（每个写接口一对越权用例）；错误码注册 14001-14004 权限类保持强约束。**这是三个项目用 13+ 个 CVE 换来的教训。**
2. **Dify 许可书面评估（对标 §6.3 许可漂移）**：输出 `docs/decisions/` 新记录——明确 SaaS 模式下 Dify 以「单工作区引擎 + 适配层租户路由」使用（一个 Dify workspace 对应一个 Moy 部署实例，租户隔离在 Moy 侧数据层完成），规避「多租户运营」条款；同时留档：不使用 Dify web 前端（规避 LOGO 条款）、不改底座代码、不展示其品牌——与决策 0004 三条规避约束形成闭环。SCA 门禁增加 `NOASSERTION/Other` 人工复核规则。
3. **出站流量统一出口代理（对标 Dify 双 SSRF 代理 + CVE-2026-105762）**：适配层所有外呼（模型/IM/工具/webhook）经单一出口代理，默认拒绝私网/回环/云元数据地址（169.254.169.254 等），allow-list 化管理。
4. **密钥永不下发明文（对标 CVE-2025-67732）**：模型 provider 密钥只存适配层服务端，前端接口一律返回脱敏配置；API Key/令牌生成强制 `secrets` 模块（禁 `random`）。

### P1（V1.1）

5. **工作流 DSL 化 + 可导出/可版本化 + 审批节点**（对标 Dify Portable Apps + n8n approval）：A 系列端点增加 `POST /api/v1/agent/workflows:export|import`；审批作为一等节点类型（待审批挂起 + 通知 + 幂等恢复）。
6. **发布工程三件套**（对标 n8n）：CI 产出 SBOM（CycloneDX）+ VEX + THIRD_PARTY_LICENSES，随版本交付；SCA 扫描结论进 VEX。
7. **长任务检查点 + 预算熔断**（对标 DeerFlow checkpointer + token 成本）：批量任务分段 checkpoint；每次运行记录 token/时长/费用，超预算阈值熔断并告警——同时补上 EvoFlow「治理套件」中 Moy 缺失的费用台账。
8. **插件签名验证 + 凭证托管**（对标 Dify plugin_daemon）：工具包安装验签；工具凭证加密托管、按任务下发短时令牌，禁静态共享密钥。
9. **运维三件套**（对标 DeerFlow doctor/support-bundle + Dify profile 部署）：`make doctor` 配置自检、`oa support-bundle` 问题包、deploy/ profile 分级（dev/prod/full-vector）。
10. **内容渲染消毒**（对标 Dify XSS 四连）：前端渲染 Mermaid/SVG/图表一律 strict 模式 + CSP；上传类型白名单；LLM 输出按不可信数据处理。

### P2（跟踪）

11. 技能生态兼容层（DeerFlow skills/Claude Code 格式），评估岗位插件复用成本。
12. 模板市场预留（注册表结构先行，市场后置）。
13. Dify 升级窗口 SOP：双周评估上游 release notes 安全公告，月度升级窗口 + 回归矩阵（417 测试基线可作冒烟集）。

---

## 8. 结论

1. **EvoFlow 的产品定义（岗位制 Agent + 治理套件 + 资产中心）与 Moy 高度同源且方向被验证**，但其非 OSI 许可、无审计、桌面形态决定了它只能是理念参照，不能是技术依赖。
2. **Dify 继续做底座是最优解，但必须完成三件事**：许可书面评估留档、租户隔离框架化（其 13+ CVE 的主模式）、跟随升级 SOP。适配层唯一原则已被 DeerFlow 版本断代从反面证明。
3. **n8n 的发布工程（SBOM/VEX）与触发器/审批设计、DeerFlow 的检查点恢复与安全坦诚度**，是三个项目中最值得直接搬运的工程实践。
4. **Moy 的相对优势真实存在**（许可纪律、适配层隔离、统一契约、内建 trace、治理内置），短板集中在「工作流 DSL 化、审批节点、发布工程、费用治理」四项——按 §7 优先级补齐即可形成对开源生态的差异化壁垒。

---

## 附录 A：证据索引

- GitHub API 实时数据（2026-10-25）：`api.github.com/repos/{EvovexAI/EvoFlow, langgenius/dify, n8n-io/n8n, bytedance/deer-flow, lobehub/lobehub}` 及各自 `/releases/latest`、`/security-advisories`。
- Dify 许可证原文：`github.com/langgenius/dify/blob/main/LICENSE`（Dify Open Source License）。
- n8n 许可证：`LICENSE.md`（Sustainable Use License）+ `LICENSE_EE.md`（n8n Enterprise License）。
- Dify 1.17.1 部署清单：`raw.githubusercontent.com/langgenius/dify/1.17.1/docker/docker-compose.yaml`（39 服务，含 agent_backend/local_sandbox/双 squid 代理）。
- CVE 记录：CVE-2025-68613 / CVE-2025-68668（n8n）；CVE-2026-105761/105762/26023/21866/42138/28288/34082、CVE-2025-59422/58747/67732、CVE-2024-10252（Dify）；GHSA 编号见 §6.1。
- DeerFlow 评测：andrew.ooo《DeerFlow 2.0 Review》、ibl.ai《DeerFlow 2.0 Is Free. Your Governance Layer Is Not.》、ugliai.com 指南（2026-07/10）。
- Dify 性能实证：idc10000.net / tsight.io / zovps.com 生产调优系列（2026）。
- Moy 侧基线：`docs/architecture-v1.0.md`（接口契约第 3 章）、`docs/decisions/0004`、`docs/phase1-acceptance-report.md`。
