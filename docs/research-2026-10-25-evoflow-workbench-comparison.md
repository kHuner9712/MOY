# 与 EvoFlow 同类开源 AI 工作台对比分析报告

| 项 | 内容 |
|---|---|
| 报告编号 | R-2026-10-25-02 |
| 调研日期 | 2026-10-25（GitHub REST API 与官方文档实时抓取，快照时间 22:55 GMT+8） |
| 基准对象 | EvovexAI/EvoFlow（桌面端 Agent Runtime + 控制平面，PolyForm Noncommercial 1.0.0） |
| 入选项目 | **coze-dev/coze-studio**（v0.5.1）、**lobehub/lobehub**（nightly）、**chaitin/MonkeyCode**（v26072801） |
| 对比主体 | Moy（麦子）V1.0 —— `docs/architecture-v1.0.md` 契约基线 + `docs/phase1-acceptance-report.md` 实跑基线 |
| 与既有报告分工 | `research-2026-10-25-ai-workbench-benchmark.md` 覆盖 **Dify / DeerFlow / n8n**（底座与自动化维度）；本报告覆盖 **三个"工作台"形态新对象**，不重复前述结论，仅在其结论需要交叉验证时引用 |
| 结论速览 | ① 工作台竞争焦点已从"对话 UI"迁移到"**Agent 团队运营**"（LobeHub CAO / EvoFlow Agent Teams 同向）；② 许可最干净的项目（Coze Apache-2.0）恰恰是维护最不活跃的（90 天 1 commit、0 release），**许可干净 ≠ 可持续**；③ 三个项目的安全缺陷全部集中在"用户可控输入进入执行面"，与 Moy 的 R/T 系列端点属同一攻击面；④ Moy 的"记忆与上下文系统"核心卖点已被 LobeHub 白盒化产品化，必须升级为可查看/可编辑/可导出的资产，否则差异化失守 |

---

## 1. 调研方法与筛选

### 1.1 EvoFlow 基准画像（复述关键事实，作为筛选标尺）

| 属性 | 值 | 对筛选的含义 |
|---|---|---|
| 形态 | 桌面端 Agent Runtime + 控制平面 | 优先选"工作台/控制平面"而非纯 SDK/框架 |
| 任务模型 | Chat / Plan / Goal 三层 + Agent Teams（值班、审批、汇报） | 需具备"多 Agent 组织化"能力 |
| 能力面 | 应用中心/自动化（多节点工作流 + 定时 + IM 派发）、工作空间 + 代码索引、自我进化资产中心（经验/反思/画像/记忆）、治理套件、资源市场（Skills + MCP）、沙箱 | 六维度评分必须覆盖"记忆/市场/治理"三项 |
| 许可 | PolyForm Noncommercial（非 OSI，商用须授权） | 许可必须单独成维度评估 |
| 规模 | 512★ / 139 fork / 8 open issues / 2026-05 创建 | 说明该赛道**新品多、成熟者少**，对标需兼顾"理念先进"与"工程可持续" |

### 1.2 筛选漏斗

1. **初筛（GitHub Search，3 组查询，2026-10-25）**
   - `ai workbench agent`（stars>3000）→ 2 个；
   - `agent workspace workflow memory`（stars>5000）→ 0 个（说明该组合词尚未成为主流命名）；
   - `AI 工作台`（stars>1000）→ 11 个（中文生态命名集中，多为垂直创作/科研工作台）。
2. **扩池**：按 EvoFlow 的五个能力簇（应用编排 / Agent 团队 / 记忆资产 / 资源市场 / 治理）补充已知同类，形成 15 个候选池（含 Dify、DeerFlow、n8n、Langflow、RAGFlow、FastGPT、Bisheng、OpenHands、AutoGen、Agno、LobeHub、Coze Studio、MonkeyCode、cc-haha、Flowise）。
3. **硬性剔除**
   - `FlowiseAI/Flowise`：**仓库已 archived**（最后推送 2026-08-13）——归档项目不具对标价值，仅作为"低代码工作流赛道退潮"的旁证；
   - `microsoft/autogen`：License 字段为 CC-BY-4.0（文档许可），且定位为框架而非工作台；
   - `ComposioHQ/composio`：工具集成层，无工作台形态；
   - 垂直工作台（视频/量化/科研）：场景过窄，与办公工作台不可比。
4. **终选（3 个，覆盖三种形态）**

| 项目 | 形态代表 | 入选理由 | 与 EvoFlow 的重合度 |
|---|---|---|---|
| **Coze Studio** | 平台型（Agent + 应用 + 工作流 + 插件 + 知识库） | Apache-2.0 唯一纯 OSI 的大型工作台；Go + DDD 微服务架构样本；21,758★ | 高：应用中心、工作流、插件市场、发布渠道 |
| **LobeHub** | 工作台型 → Agent 运营型（CAO） | 83,479★ / 354 贡献者；2026 年从"聊天 UI"转型为"7×24 Agent 团队运营"，与 EvoFlow Agent Teams 同构 | 极高：Agent 团队、排班汇报、Skills/MCP 市场、白盒记忆、Workspace |
| **MonkeyCode** | 企业私有化交付型 | 长亭科技（安全厂商）出品；企业内网私有化 + 模型/任务/环境治理；与 Moy 商业模式正面重叠 | 中高：私有化、模型管理、任务治理、桌面端 |

> 补充观察对象：`NanmiCoder/cc-haha`（15,162★，桌面 Agent 工作台，质量门禁体系极完整）与 `EvovexAI/EvoFlow` 形态最接近，但其许可为 Community Source、且产品本质是 Claude Code 的桌面外壳，**不可复用代码**，仅在 §5.3 引用其工程实践。

### 1.3 数据口径声明

- 社区指标（stars/forks/issues/contributors/release）均来自 GitHub REST API 与 shields.io，快照 2026-10-25；
- 「30 天提交数」用 `/commits?since=` 抓取，上限 100，故标注 `100+` 表示达到抓取上限；
- 安全证据仅采信 **GitHub Security Advisories（GHSA）** 与 **项目官方声明/issue 原文**，不采信二手博客的定级；
- 性能结论仅采纳有 issue/官方文档/实测数据支撑的条目，推测一律标注「推断」。

---

## 2. 总览对比

### 2.1 基本面对比

| 维度 | EvoFlow（基准） | Coze Studio | LobeHub | MonkeyCode |
|---|---|---|---|---|
| Stars / Forks | 512 / 139 | **21,758 / 3,133** | **83,479 / 16,009** | 4.9k / 743 |
| Open Issues | 8 | 582 | 1,167 | 371 |
| Watchers | 6 | 122 | 299 | — |
| 创建时间 | 2026-05 | 2025-06 | 2023-05 | 2026（仓库 2,345 commits） |
| 主语言 | TypeScript（Tauri） | **Go 后端 + React/TS 前端**（GitHub 语言统计以 TS 计） | TypeScript（Next.js） | Go 后端 + Tauri/Rust 桌面 + TS 前端 |
| 许可 | PolyForm Noncommercial（非 OSI） | **Apache-2.0（纯 OSI）** | LobeHub Community License（Apache-2.0 + 附加限制，非 OSI） | **AGPL-3.0（黑名单）** |
| 贡献者 | — | 61 | **354** | 8 |
| 最后推送 | 2026-10-25 | **2026-07-29（86 天前）** | 2026-10-25 | 2026-10-23 |
| 近 30 天提交 | 活跃 | **0**（90 天 1 次） | **100+**（日更） | **100+** |
| 最新发布 | 桌面 Releases 持续 | v0.5.1（2026-02-05） | nightly 每日构建 | v26072801（2026-07-28） |
| 产品定位 | 桌面 Agent 控制平面 | 一站式 AI Agent 可视化开发平台 | Chief Agent Operator（Agent 团队运营） | 企业级 AI 开发平台 |

### 2.2 一句话定位差异

- **Coze Studio**：把商业版 Coze 的"核心引擎"开源出来——能力面最完整，但开源仓库已进入低频同步状态，**是"能力样本"而非"可持续依赖"**。
- **LobeHub**：从"最好用的开源 ChatGPT UI"完成向"**Agent 雇主**"的跃迁——卖点不再是聊天，而是"替你招募、排班、汇报一整支 AI 团队"。
- **MonkeyCode**：把 AI 编码能力装进企业内网，用"控制台 + 开发环境宿主"双层部署解决私有化，**是 Moy 在私有化交付形态上最直接的参照**。
- **Moy**：跨生态、岗位级场景插件、适配层唯一、许可白名单、治理内置——差异化在"办公场景的岗位化封装 + 合规可私有化"，不在通用 Agent 平台。

---

## 3. 六维度深度对比

### 3.1 架构设计

| | Coze Studio | LobeHub | MonkeyCode | 对 Moy 的参照 |
|---|---|---|---|---|
| 服务拆分 | Go 单体仓库内按 DDD 分层（domain / application / infra），运行期以微服务形态部署：`coze-server` + `coze-web` + MySQL 8.4 + Redis 8 + Elasticsearch 8.18 + MinIO + etcd + **Milvus 2.5.10** + **NSQ 三件套**（nsqlookupd/nsqd/nsqadmin），共 10 个容器 | Next.js 应用（Web + API 同进程）+ **独立 `lobehub-gateway` 服务**（Gateway Mode 自托管默认启用）+ PostgreSQL（ParadeDB，启用 `pg_search`/`pg_cron`/`pg_stat_statements`）+ RustFS（S3 兼容）+ Redis；monorepo 将 `agent-runtime`、`agent-gateway-client`、`builtin-agents`、`builtin-skills` 拆为独立包 | 双层角色：**控制台（console）** + **开发环境宿主（dev host）**，分别给出 2C4G / 8C16G 规格；Agent 引擎以 git submodule 引入（`chaitin/OhMyAgent`），官方技能库同为 submodule；桌面端 Tauri（Rust）+ 浏览器扩展协议以 `protocol.rs` 为权威源、`protocol.ts` 由 ts-rs 生成 | Moy 的"适配层 + 底座"两层比三者都更克制；但三者的**进程/角色分离粒度**值得借鉴：LobeHub 把 Agent 执行面抽成独立 gateway，MonkeyCode 把"重资源执行"与"轻控制台"拆成两个部署单元 |
| 隔离机制 | 无租户隔离框架，README 官方 WARNING 自认存在「API 水平权限提升」与「SSRF」；代码节点 Python 执行环境需自行加固 | Gateway 模式让浏览器不直连 runtime；`setup.sh` 每次部署生成独立 `JWKS_KEY`，且**只把公钥 JWK 下发给 gateway**（修复过"下发整套私钥"的设计缺陷） | 云开发环境（每任务独立后端环境，推断为容器）；桌面端 `driver → engine` 通过 RPC（`session/create`、`subagent/list`、`subagent/cancel`）通信 | LobeHub 的"公私钥分离下发"是低成本高收益做法，Moy 的 Keycloak realm 密钥管理应对齐；MonkeyCode 的 RPC 白名单方法名比 Electron 的通用 IPC 安全 |
| 多实例/水平扩展 | 微服务天然可拆，但 compose 为单机编排，无 replicas/健康拓扑说明 | Next.js 无状态可多副本，但 **PG 单点**（全文检索 + 向量 + 业务表同库）；gateway 可独立扩 | console 与 dev host 分离，天然可按宿主扩；但 8 贡献者团队难以支撑复杂拓扑 | Moy 已定"无状态适配层 + 状态外置"，方向正确；需补"**PG 是单点**"的显式风险登记（向量与业务同库时尤甚） |
| 部署编排 | `docker/docker-compose.yml` 单文件 10 服务；**镜像使用 `latest` 浮动标签**（`cozedev/coze-studio-server:latest`）与 `bitnamilegacy/*` 遗留仓库 | `docker-compose/{deploy,dev,eval,production/grafana}` + `setup.sh` 一键脚本；**按用途分档**，部署/开发/评测/生产可观测四套 | 官方在线安装脚本 + 外部文档站；README 只给两类入口，未展开编排细节 | Moy 已有 profile 化思路，可再学 LobeHub 的"**eval 独立档**"——把评测环境和生产编排同源管理 |

**架构结论**：三者都在解决"重执行面与轻控制面分离"，但只有 LobeHub 把"浏览器不直连执行面"落成了默认架构（Gateway Mode）。Moy 的适配层天然具备这一性质，应在文档中把它从"实现细节"上升为"安全边界声明"。

### 3.2 核心功能

| 能力 | EvoFlow | Coze Studio | LobeHub | MonkeyCode | Moy V1.0 现状 |
|---|---|---|---|---|---|
| 对话 / 计划 / 长任务 | Chat/Plan/Goal | 应用 + 工作流 | 长周期任务（Long-horizon Task）+ 排班 | AI 任务（需求→开发→验证） | M1-M4 + A1-A6（无 Plan 形态） |
| 多 Agent 协作 | Agent Teams（值班/审批/汇报） | 多 Agent 节点 | **Agent Group（自动组队 + 并行 + 迭代改进）** | 子代理（subagent list/cancel） | ❌ 未产品化 |
| 知识库 / RAG | ✅ | ✅（ES + Milvus 双引擎） | ✅（PG 全文 + 向量） | 项目上下文 | ✅ R1-R7 |
| 长期记忆 | 资产中心（经验/反思/画像/记忆） | ❌ | **Personal Memory + White-Box Memory（结构化可编辑）+ Continual Learning** | ❌ | 规划中（核心卖点） |
| 人工审批 | ✅ 关键审批 | ⚠️ 无原生审批节点 | ⚠️ 依赖权限/确认交互 | ✅ 任务确认 | ❌ |
| 治理（SSO/费用/审计） | ✅ 治理套件 | ❌（企业版在闭源侧） | ⚠️ 组织/工作区/配额，SSO 需自建 | ✅ 模型管理 + 扣费明细 | 部分（Keycloak + 计费 S2） |
| 插件 / 技能市场 | 资源市场（Skills + MCP） | 插件商店（官方 + 自定义） | **Skills 33 万+ / MCP 9.6 万+** | 官方技能库（submodule 分发） | 工具注册表（5 个首批工具） |
| 沙箱执行 | ✅ | Python 代码节点（需自行加固） | ⚠️ 无强沙箱（Electron 侧 IPC 风险） | ✅ 云端开发环境 | Dify sandbox |
| 定时 / 触发器 | ✅ 定时 + IM 派发 | ⚠️ 有限 | ✅ Schedule | ❌ | ❌ |
| IM 渠道 | ✅ 多渠道 | ❌ | ✅ IM Gateway（Discord/Slack/Telegram/飞书） | ⚠️ 移动端 | ❌ |
| 桌面端 | ✅ | ❌ | ✅（Windows 版可下载） | ✅（Tauri） | ❌（V1.0 不做） |

**功能结论**：EvoFlow 的"岗位制 Agent + 治理 + 资产中心"三件套已被 LobeHub 以更彻底的方式产品化（CAO）。Moy 若把"记忆系统"仅作为内部实现，将正面撞上 LobeHub 的"白盒记忆"叙事。

### 3.3 工作流编排能力

| | Coze Studio | LobeHub | MonkeyCode | Moy |
|---|---|---|---|---|
| 编排范式 | 可视化画布（节点 + 连线），支持**代码节点（Python）**、**SQL 自定义节点**、插件节点、知识库节点；工作流可被应用/Agent 引用 | "Multimodal Workflow" + Pages（多 Agent 协同编辑）+ Schedule；偏"任务流"而非"节点流" | 任务流（需求 → 环境 → 执行 → 验证），非通用编排器 | 依赖 Dify 工作流（YAML DSL），适配层 A 系列转发 |
| 状态与恢复 | 执行记录落库；错误处理路径存在 `concurrent map writes` 崩溃（issue #2713） | 长周期任务 + Gateway 会话；断点能力未公开 | 任务级重试 | ❌ 无检查点 |
| 人工介入 | 无原生审批节点 | 权限确认 | 任务确认 | ❌ |
| 表达能力上限 | 高（可写 SQL/Python），**代价是攻击面最大** | 中（Agent 编排为主） | 中 | 中（DSL 受底座约束） |
| 可移植性 | 工作流与应用绑定平台，无导出标准 | 无导出标准 | 无 | ❌ 未 DSL 化 |

**编排结论**：Coze 用"代码/SQL 节点"换来了最强的表达能力，也换来了本次调研中最严重的两个漏洞（跨租户 SQL、插件 SSRF）。**表达能力与隔离强度必须同时定价**——这是 Moy 设计 T 系列工具与 A 系列工作流时的核心取舍。

### 3.4 扩展性

| | Coze Studio | LobeHub | MonkeyCode |
|---|---|---|---|
| 扩展机制 | 插件（OpenAPI schema 描述 + 鉴权配置）、自定义插件、工作流复用、Chat SDK / OpenAPI 双出口 | **Skills 市场（33 万+）+ MCP 市场（9.6 万+）**；`builtin-skills` 与 `builtin-agents` 作为独立包发布；Composio 集成 | 官方技能库 submodule + 自定义技能（`app_config_dir/skills/`，会话前物化到引擎目录） |
| 生态规模 | 官方插件商店（需配置第三方密钥） | 生态规模本报告调研对象中最大 | 小（8 贡献者） |
| 扩展的隔离设计 | 插件执行在服务端，**无出站白名单**（SSRF 实证） | 技能以声明式为主，执行依赖 runtime | 技能文件级隔离，随会话物化 |
| 对 Moy 的启示 | Moy 的 `registry/tool_registry.yaml + tools/` 数据驱动方向正确，但缺"**插件声明 schema 校验 + 出站白名单 + 凭证托管**"三件套 | **技能市场是获客杠杆**：33 万 skills 的规模效应无法自研追赶，Moy 应做"兼容层"而非"自建市场" | "技能库权威 + 按会话物化"可避免技能串味，值得用于 Moy 岗位插件的运行时隔离 |

### 3.5 社区活跃度

| 指标 | Coze Studio | LobeHub | MonkeyCode |
|---|---|---|---|
| Stars 增速 | 高开后停滞（21.7k，但 90 天 1 commit） | 持续高位（83.5k，日更） | 新项目爬坡（4.9k） |
| 贡献者集中度 | 61 人，字节主导 | **354 人，社区 + 商业化双驱动** | 8 人，厂商主导 |
| 发版节奏 | v0.5.1（2026-02）后再无 release | nightly 每日 | release 停在 2026-07，但代码仍在提交 |
| 问题处理 | 582 open；issue #2729 直言"这个项目已经死了,不更新 不修复" | 1,167 open；**10 条 GHSA 主动披露** | 371 open；无公开安全公告 |
| 治理透明度 | 安全问题上要求走字节安全中心、**禁止公开 issue** | 公开复盘供应链事件（LOL #10） | 无公开安全流程 |
| 商业背书 | 字节跳动（商业版 Coze 活跃） | LobeHub LLC（Product Hunt #1） | 长亭科技（安全厂商） |

**活跃度结论**：**Star 数是滞后指标，提交节奏与发版频率才是先行指标**。Coze Studio 的 21.7k★ 与 0 release/90 天形成强烈反差；LobeHub 的 354 贡献者 + 日更 + 主动披露构成当前最健康的开源工作台样本。

### 3.6 部署运维

| | Coze Studio | LobeHub | MonkeyCode |
|---|---|---|---|
| 最小部署 | Docker Compose，**官方最低 2C/4G**（10 容器同机，实际生产明显不足） | Compose 一键脚本（`setup.sh` 自动生成 JWKS/网关 token） | console 2C/4G/40G + dev host **8C/16G/100G** 两段式 |
| 生产形态 | 官方未给生产拓扑；README 明确"公网部署需自行评估风险并加固" | `docker-compose/production/`（含 Grafana 可观测档）+ gateway 独立扩缩 | 企业内网/离线私有化，安装脚本在线拉取 |
| 配置管理 | `.env.example` → `.env`；无配置自检 | `setup.sh` 生成密钥 + 启动时**主动告警**"缺失网关配置 / 仍在使用示例 JWKS_KEY" | 外部文档站 |
| 镜像治理 | `latest` 浮动 + `bitnamilegacy/*` 遗留仓库（供应链风险） | 固定 tag + nightly 通道 | 官方镜像 + 安装脚本 |
| 可观测 | 无内置（依赖外部） | production 档内置 Grafana | 无公开说明 |
| 备份/升级 | 未文档化升级路径与兼容性承诺 | 文档化升级注意事项（网关模式迁移） | 未公开 |

**运维结论**：三者的运维成熟度排序为 **LobeHub > MonkeyCode > Coze Studio**。Coze 的 `latest` + `bitnamilegacy` 组合是"能跑起来但不可复现"的典型，恰好反证 Moy「所有组件锁精确标签 + `deploy/images.lock` 双通道核验」的价值。

---

## 4. 与 Moy 的差异分析

### 4.1 Moy 的相对优势（被本次调研反向验证）

| Moy 决策 | 本次调研中的反证 |
|---|---|
| **适配层唯一原则**（业务/插件只调适配层，CI 禁底座直连） | 三个项目均为"前端直连后端 API"：Coze 的 Web 直接调用 Go 服务；LobeHub 的 Web 与 API 同进程；MonkeyCode 前端直连 backend。一旦协议变更或需要换引擎，改动面覆盖全栈 |
| **统一响应契约 `{code,message,data}` + 错误码注册制** | Coze 的 issue 中大量"前端 500 但无业务语义"报告（如 #2725 CRLF 导致 ES 初始化失败→前端 500）；LobeHub 安全公告中的绕过均发生在自定义 header/路由层，缺少统一入口校验 |
| **`X-Tenant-Id` 强制 + 租户隔离一等公民** | Coze issue #2710：经工作流 **SQL 自定义节点**可跨租户读/写/删任意 workspace 的 Database 数据；官方 README 自认"部分 API 存在水平权限提升" |
| **许可白名单（MIT/Apache-2.0/BSD）** | 4 个对象中 3 个非 OSI：EvoFlow PolyForm、LobeHub 社区许可（衍生分发须商业授权）、MonkeyCode **AGPL-3.0**（Moy 黑名单，禁止入核心库） |
| **内建 structlog trace（trace_id/tenant_id/user_id/event）** | 三者均无统一 trace 规范：Coze 依赖 ES 日志检索，LobeHub 依赖外部 Langfuse/Sentry，MonkeyCode 无公开说明 |
| **治理内置（私有化交付验收项）** | Coze 治理在闭源商业版；LobeHub SSO/审计需自建；MonkeyCode 有模型/扣费但无审计导出 |
| **锁精确镜像标签 + 依赖门禁** | Coze 用 `latest` + `bitnamilegacy`；LobeHub 因开发依赖被投毒导致仓库被攻击者操作（LOL #10 自述） |

### 4.2 Moy 的能力缺口（按紧迫度排序）

| # | 缺口 | 对标证据 | 影响 |
|---|---|---|---|
| G-1 | **Agent 团队运营形态缺失**（排班、汇报、值班、预算） | LobeHub CAO：hire / schedule / report；EvoFlow Agent Teams | 产品叙事落后一个形态：Moy 卖"插件"，对手卖"AI 同事" |
| G-2 | **记忆系统未白盒化** | LobeHub：Personal Memory + **White-Box Memory（结构化、可编辑）** + Continual Learning | Moy 的核心资产若不可见/不可编辑，企业客户无法验收，也无法与竞品区隔 |
| G-3 | 工作流无检查点、无审批节点、无 DSL 导出 | DeerFlow checkpointer、n8n approval、Dify Portable Apps（见 R-2026-10-25-01） | 长任务失败即从头；办公场景关键操作无法留痕确认 |
| G-4 | 工具/插件扩展缺"声明 schema + 出站白名单 + 凭证托管" | Coze 插件 SSRF（#2711）、LobeHub 预签名越权（GHSA-wrrr） | T 系列端点开放后面临同类攻击面 |
| G-5 | 无技能市场兼容层 | LobeHub 33 万 skills / 9.6 万 MCP | 生态规模无法自研追赶，只能兼容 |
| G-6 | 发布工程缺 SBOM/VEX | n8n（R-01 结论）；本次三项目亦均无 | 私有化交付时供应链结论不可交付 |
| G-7 | 桌面端/移动端形态缺位 | EvoFlow / LobeHub / MonkeyCode 均有桌面端，MonkeyCode 另有移动端 | V1.0 可接受；但若企业客户要求"本地文件 + 本地应用操作"，将失去场景 |

---

## 5. 值得借鉴的优点

### 5.1 技术选型

1. **Coze：用 NSQ 而非 Kafka 做消息队列**。10 容器规模下，NSQ 的运维成本远低于 Kafka，且足够支撑工作流异步节点。→ Moy 若要为文档解析/索引引入队列，优先评估 NSQ/Valkey Stream，而非默认上 Kafka。
2. **Coze：ES 与 Milvus 分工**。全文检索走 Elasticsearch、向量检索走 Milvus，避免"用向量库硬做关键词检索"的精度损失。→ Moy 当前 RAG 仅 Milvus，建议在 R5 检索接口保留"关键词通道"的抽象位（可由 PG `pg_search` 或 ES 实现，不锁死）。
3. **LobeHub：PostgreSQL 单库承载业务 + 向量 + 全文（ParadeDB）**。以 `pg_search`/`pg_cron`/`pg_stat_statements` 扩展替代独立向量库，把"运维组件数"压到最低。→ 与 Moy 私有化"15 容器"形成对照：**小规模私有化应提供"精简档"**（PG 单库 + SeaweedFS + Valkey），把 Milvus 列为"规模档"可选。
4. **LobeHub：Agent 执行面抽成独立 gateway 包**（`agent-runtime` / `agent-gateway-client` 独立 npm 包），并默认启用 Gateway Mode。→ Moy 可将"Agent 执行"在适配层内部做成独立模块/进程，为后续独立扩缩留出接缝，而不破坏"适配层唯一"原则。
5. **MonkeyCode：控制台与重资源执行环境分离，并给出分级规格**（2C4G vs 8C16G）。→ Moy 部署文档应把"控制面 / 执行面"资源分开列，避免客户用一套规格承诺全部负载。
6. **MonkeyCode：以 `protocol.rs` 为协议唯一真源，`protocol.ts` 由 ts-rs 生成**。跨语言协议不手写双份。→ Moy 已有 OpenAPI 契约基线，可再进一步：前端类型由 OpenAPI 生成，禁止手写类型（当前 `apps/web` 若手写类型即为漂移源）。

### 5.2 产品设计

1. **LobeHub：把"运营一支 Agent 团队"做成产品主语**（CAO：招募 / 排班 / 汇报 / 你不在线也在跑）。这不是功能堆叠，而是**叙事升级**：从"工具"变成"组织"。→ Moy 的岗位级插件应升级为"**岗位 + 值班 + 汇报**"三件套叙事，与 EvoFlow 互相印证。
2. **LobeHub：白盒记忆**（结构化、可查看、可编辑）与"持续学习"分离。→ Moy 记忆系统设计应拆成"事实层（结构化，可编辑）/ 经验层（可反思，可丢弃）/ 画像层（可导出）"，并给出用户可见的"记忆管理"页面。
3. **LobeHub：Agent Group 自动组队**（系统按任务自动挑选 Agent 组合，支持并行与迭代改进）。→ Moy 可在 A 系列增加"任务→岗位组合"的路由策略，而非要求用户手工挑插件。
4. **LobeHub：Skills 市场 + MCP 市场双市场**。→ Moy 的"资源市场"应同时容纳"声明式技能（低风险）"与"MCP 服务（高风险）"，并**分级授权**（MCP 默认需管理员审批）。
5. **Coze：应用 / Agent / 工作流 / 插件 / 知识库 / 数据库 / 提示词七类资源统一纳管**。→ Moy 的资源模型应显式区分"可复用资源"与"一次性配置"，否则插件市场无法沉淀。
6. **EvoFlow / MonkeyCode：Plan/需求先行的任务形态**。→ Moy 对话产品在复杂任务上应提供"计划确认"交互，把"先规划后执行"变成显式状态机。

### 5.3 工程实践（本次最值得直接搬运的部分）

1. **LobeHub：部署即生成密钥，且公私钥分离下发**。`setup.sh` 每次部署生成独立 `JWKS_KEY`，只把公钥 JWK 交给 gateway；启动时主动告警"仍在使用示例密钥"。→ Moy 的 Keycloak/适配层密钥应同款处理：**部署脚本生成 + 启动自检 + 公钥最小下发**。
2. **LobeHub：主动披露供应链事件并给出处置清单**（撤销 token → 切断访问 → 审计依赖 → 轮换同信任边界全部凭证 → 恢复数据）。→ Moy 应预置《供应链事件响应 SOP》，并把"依赖投毒"列为威胁模型的一等条目。
3. **LobeHub：10 条 GHSA 全部走公开流程**（含 Critical 级 XSS→RCE），修复版本明确。→ Moy 应建立"安全公告模板 + 修复版本映射"，私有化交付时附"已知漏洞与修复状态"清单。
4. **MonkeyCode：技能库权威 + 按会话物化**（会话创建前把技能重写到引擎目录）。→ Moy 岗位插件运行时用同款"会话级物化"，避免多租户技能串味。
5. **cc-haha：质量门禁工程化到极致**——`quality-gate` 分 pr/baseline/release 三模式，含 provider-smoke、desktop-smoke、mock-llm 回放、覆盖率、quarantine（隔离不稳定用例）、persistence-upgrade（持久化升级）等专项。→ Moy 的 CI 目前是"ruff + mypy + pytest + eslint + tsc + SCA"，缺**回放测试**与**升级兼容测试**两项，建议按此补齐。
6. **Coze：一键起 + 官方最低配置明示（2C4G）**。→ 降低试用门槛是开源获客的关键；Moy 应提供"体验档 compose"（精简容器集）。
7. **LobeHub：`docker-compose/eval` 独立档**。把评测环境与生产编排同源管理。→ Moy 的 QA 环境应复用同一 compose，仅换 profile，避免"测试环境与交付环境两套编排"的漂移。

---

## 6. 缺陷与风险识别

### 6.1 安全漏洞实证

#### Coze Studio（官方自认 + issue 级实证，无公开 GHSA）

| 类别 | 证据 | 状态 |
|---|---|---|
| **跨租户数据越权** | issue #2710（2026-07-06）：经工作流 **SQL 自定义节点**，可对任意 workspace 的 Database（memory table）数据执行读/写/删 | 无 release（最后 v0.5.1 2026-02），公开渠道无修复版本 |
| **SSRF** | issue #2711（2026-07-06）：插件工具执行存在**无限制 SSRF**，任意已认证用户可访问仅内网可达的服务 | 同上 |
| **水平权限提升（官方承认）** | README `[!WARNING]`：公网部署需自行评估，风险含"账号注册功能、工作流代码节点 Python 执行环境、Coze Server 监听地址配置、**SSRF**、**部分 API 的水平权限提升**" | 官方将加固责任转移给部署方 |
| **漏洞披露流程封闭** | README：安全问题请走字节安全中心，**"Please do not create public GitHub Issues"** | 导致 0 条公开 GHSA，采用方无法评估存量风险 |
| **Python 代码节点执行环境** | README 明确列为公网风险项 | 无沙箱默认值 |

#### LobeHub（10 条已公开 GHSA，2025-09 ~ 2026-10）

| GHSA | 类型 | 定级 | 披露时间 | 关键点 |
|---|---|---|---|---|
| GHSA-4gpc-rhpj-9443 | **XSS → RCE** | **Critical** | 2026-01-17 | 渲染层 XSS 后可触达 Electron 主进程 |
| GHSA-xq4x-622m-q8fq | XSS → RCE | Moderate | 2026-04-27 | Electron 主进程暴露 `runCommand` IPC，**command 参数无过滤**，XSS 取得 `window.parent.electronAPI` 即可任意命令执行 |
| GHSA-2g9j-v25c-4j97 | **路径穿越 → 任意文件写 → RCE** | Moderate | **2026-10-17** | 上传文件名未规范化 |
| GHSA-xmwj-c75x-6346 | **未认证 SSRF**（`/webapi/proxy`） | Moderate | 2026-06-03 | 代理端点无鉴权、无出站白名单 |
| GHSA-5mwj-v5jw-5c97 | **未认证认证绕过** | Moderate | 2026-04-08 | 伪造 `X-lobe-chat-auth` header 即可绕过 webapi 鉴权 |
| GHSA-wrrr-8jcv-wjf5 | 预签名上传授权不当 | Moderate | 2026-01-30 | 用户可控 object key → 跨命名空间上传 + 配额绕过 |
| GHSA-j7xp-4mg9-x28r | IDOR（知识库文件删除） | Low | 2026-01-19 | 跨用户删除文件 |
| GHSA-fgx4-p8xf-qhp9 | SSRF（native web fetch） | Low | 2025-10-17 | 原生 fetch 模块 |
| GHSA-m79r-r765-5f9j | 桌面端聊天消息 XSS → RCE | Moderate | 2025-09-18 | 同 Electron IPC 缺陷同源 |
| GHSA-xph5-278p-26qx | 开放重定向 | Moderate | 2025-09-24 | — |

**模式归纳（对 Moy 最有价值的部分）**：10 条中 **7 条**属于"用户可控输入 → 服务端/主进程执行面"（SSRF×2、XSS→RCE×3、路径穿越×1、预签名 key×1），**1 条**属于"自定义认证 header 被信任"（GHSA-5mwj 未认证绕过）、**1 条** IDOR、**1 条**开放重定向。这与 Coze 的 SQL 节点/插件 SSRF 是**同一类根因**：`输入校验` 与 `执行隔离` 没有在框架层强制。

#### MonkeyCode（无公开公告，风险来自许可与形态）

- **AGPL-3.0**：Moy 合规白名单明令禁止入核心库；即使独立进程调用，AGPL 的网络服务条款仍可能触发源码义务；
- **submodule 依赖**（`chaitin/OhMyAgent`、`MonkeyCodeOfficialPlugins`）：版本与主仓解耦，审计面扩大；
- **8 贡献者 + 商业公司主导**：私有化交付的长期维护承诺需合同保障，不宜作为技术依赖。

### 6.2 架构缺陷归纳

| # | 缺陷 | 证据 | 根因 |
|---|---|---|---|
| A-1 | **租户隔离靠端点补齐，不靠框架强制** | Coze #2710、官方 WARNING"水平权限提升"；LobeHub IDOR/预签名越权 | 资源查询用 `get_by_id` 后不校验归属；Moy 必须以 `tenant_scope()` 依赖注入强制 |
| A-2 | **执行面与表达能力同步放大，但隔离未同步** | Coze SQL 节点跨租户、Python 代码节点；LobeHub Electron IPC `runCommand` 无过滤 | "给用户代码执行能力"却没有"能力白名单 + 参数校验 + 用户确认"三道闸 |
| A-3 | **认证信任边界模糊** | LobeHub GHSA-5mwj（伪造 `X-lobe-chat-auth`） | 网关信任自定义 header，而非验证签名令牌；Moy 必须只信 JWT 签名与 Keycloak 校验结果 |
| A-4 | **单库承载全部检索，规模上限被隐藏** | LobeHub PG 同时承载业务/全文/向量 | 小规模优雅、大规模成瓶颈；需在文档中给"何时拆库"的阈值 |
| A-5 | **供应链是开发侧而非运行时侧的盲区** | LobeHub LOL #10：开发依赖被投毒 → 攻击者操作 GitHub repo，关闭全部 issue；2026-05 npm 投毒事件（国家通报）为宏观背景 | 锁文件 + `ignore-scripts` + token 最小权限 + 快速轮换四件事缺一不可 |

### 6.3 性能瓶颈（实证 / 推断标注）

| 项目 | 证据 | 结论 |
|---|---|---|
| Coze Studio | issue #2713（2026-07-10）：工作流错误处理时 `fatal error: concurrent map writes`，进程直接崩溃 | Go 并发写 map 属**未加锁的共享状态**，是"错误处理路径"这种低频分支最易漏检的缺陷；Moy 若引入 Go 组件必须开 `-race` 门禁 |
| Coze Studio | 10 容器（MySQL + Redis + ES + MinIO + etcd + Milvus + NSQ×3 + server + web）跑在官方最低 2C4G | **推断**：2C4G 仅够"点亮"，任何真实知识库导入都会触发 ES/Milvus 内存竞争；官方规格不可作为生产承诺 |
| Coze Studio | issue #2704/#2708：`InitializeInfra failed`、MinIO bucket 探测失败导致启动 panic | 启动强依赖外部组件可达性，无降级/重试退避 → 冷启动脆弱 |
| LobeHub | PG 单库承载 pg_search + 向量 + 业务 + pg_cron | **推断**：写入放大与索引维护会在数据量上升后拖慢在线请求；需 `pg_stat_statements` 基线监控 |
| MonkeyCode | dev host 8C16G 起，每任务独立环境 | 资源成本随并发任务线性上升，私有化客户需按"并发任务数 × 8C16G"做容量规划 |
| EvoFlow（基准） | 桌面端本地执行 | 不受服务端瓶颈约束，但受终端算力与模型配额约束 |

### 6.4 维护风险

| 风险 | 实证 | 对 Moy 的映射 |
|---|---|---|
| **"开源承诺"撤回 / 低频同步** | Coze Studio：90 天 1 commit、0 release、issue #2729 直言"项目已经死了"；第三方健康分 21/100、Zombie Risk 48/100；生态月报称"自 4 月以来没有推送，让人对其开源承诺产生疑问" | **许可干净 ≠ 可持续**。Moy 对 Dify 的依赖必须补"上游活跃度监控 + 退出预案"，并把"底座可替换"写进架构约束（适配层已提供技术前提） |
| **许可漂移 / 单方调约** | LobeHub Community License：衍生作品分发须商业授权；贡献者条款允许生产方"单方收紧或放宽"；MonkeyCode AGPL | Moy 白名单纪律正确，但 SCA 门禁需增加"NOASSERTION / Other / 自定义许可"一律人工复核（与 R-01 建议合并执行） |
| **供应链投毒** | LobeHub 开发依赖被投毒 → GitHub 仓库被操作；2026-05 npm 蠕虫式投毒（国家通报） | 需 `ignore-scripts`、锁文件校验、CI token 最小权限、依赖新增双人复核 |
| **子模块/外部引擎耦合** | MonkeyCode 以 submodule 引 Agent 引擎与技能库 | Moy 若引入外部引擎，必须锁 commit + 本地镜像 + 升级评审 |
| **安全披露流程封闭** | Coze 禁止公开安全 issue；MonkeyCode 无公告 | 采用方无法评估存量风险 → **此类项目不可作为生产依赖**，只能作能力参考 |
| **项目归档** | Flowise 已 archived（2026-08） | 低代码工作流赛道已进入整合期；Moy 不应把"可视化编排"当作差异化卖点 |

---

## 7. 对 Moy 的具体规避建议

> P0 = 进入当前 Sprint；P1 = V1.1；P2 = 跟踪。每条给出落点与验收判据。

### P0（立即，安全与合规底线）

**P0-1 工作流"表达式 / SQL / 代码"节点的安全基线**
- 落点：`apps/adapter` A 系列 + T 系列；`docs/architecture-v1.0.md` 新增"执行面安全"小节。
- 动作：① 禁止把用户输入拼进 SQL，一律参数化 + 租户条件由框架注入（`WHERE tenant_id = :ctx_tenant` 强制追加，业务不可覆盖）；② 代码/表达式节点默认关闭，开启需管理员 + 白名单能力原语（Moy 已有 G-28 能力原语白名单门禁，扩展其覆盖面）；③ 所有节点执行前输出"将要访问的资源清单"供审计。
- 验收：跨租户越权测试用例（读/写/删各 1 条）全部返回 14003；CI 中 `tenant_scope` 静态检查 0 例外。
- 对标：Coze #2710、官方 WARNING。

**P0-2 认证信任边界收敛**
- 落点：适配层鉴权中间件 + 网关配置。
- 动作：只信 Keycloak 签发的 JWT（验签 + `aud`/`iss`/`exp` 校验）；**禁止信任任何自定义身份 header**（`X-User-Id`、`X-Tenant-Id` 仅在网关注入后由适配层复核 JWT 一致性）；网关层剥离外部传入的同名 header。
- 验收：伪造 `X-Tenant-Id`/`X-User-Id` 的请求 100% 被拒（401/403）；安全测试用例入 CI。
- 对标：LobeHub GHSA-5mwj-v5jw-5c97。

**P0-3 上传与对象存储 key 服务端生成**
- 落点：R 系列上传接口 + SeaweedFS 适配。
- 动作：① 文件名仅作展示，存储 key 由服务端 `ULID + 扩展名白名单` 生成；② 预签名 URL 的 object key 由服务端签发，禁止客户端传入；③ 上传大小/类型/数量按租户配额校验（配额校验在服务端，不信任前端）。
- 验收：路径穿越 payload（`../`、URL 编码、Unicode 归一化）全部拒绝；跨租户 object key 访问 403。
- 对标：LobeHub GHSA-2g9j-v25c-4j97、GHSA-wrrr-8jcv-wjf5。

**P0-4 出站流量统一代理 + 私网 deny**
- 落点：适配层所有外呼（模型/IM/工具/webhook/插件）。
- 动作：单一出口代理（Moy 已有 squid 容器），默认 deny 私网/回环/链路本地/云元数据（169.254.169.254），allow-list 化；插件工具执行必须经代理，禁止直连。
- 验收：SSRF 测试集（内网 IP、元数据地址、DNS rebinding、302 跳转）全部拦截；插件出站 100% 有代理日志。
- 对标：Coze #2711、LobeHub GHSA-xmwj-c75x-6346。

**P0-5 依赖供应链门禁**
- 落点：CI + `deploy/validate_deps.py`。
- 动作：① Node 侧 `ignore-scripts=true` + 锁文件强校验（`pnpm-lock`/`package-lock` 差异即 fail）；② Python 侧 `pip --require-hashes`；③ CI token 最小权限（只读 + 单仓）；④ 新增依赖双人复核 + 来源核验（官方 registry、近期发布记录、`preinstall/postinstall` 脚本检查）；⑤ 密钥轮换 SOP（GitHub/npm/云凭据）。
- 验收：CI 中存在"新增依赖未复核"即阻断；季度轮换演练记录留档。
- 对标：LobeHub LOL #10 供应链事件、2026-05 npm 投毒国家通报。

### P1（V1.1，产品形态与工程能力）

**P1-1 Agent 团队运营视图（补齐 G-1）**
- 动作：在 A 系列之上增加"岗位值班表 + 任务排班 + 周期汇报 + 预算上限"四要素；汇报以结构化卡片输出（不是聊天流）。
- 验收：一个"周报岗位"可配置 cron、自动执行、生成汇报并推送 IM；超预算自动暂停并告警。

**P1-2 白盒记忆（补齐 G-2，保护核心卖点）**
- 动作：记忆拆三层——事实层（结构化 KV，可编辑）、经验层（反思条目，可丢弃）、画像层（可导出/可删除）；提供"记忆管理"页面与导出 API；所有记忆写入带来源（会话 ID / 文档 ID）与时间。
- 验收：用户可查看/编辑/删除任一记忆条目；导出格式含来源；删除后检索不可召回。

**P1-3 工作流 DSL 化 + 审批节点 + 检查点（补齐 G-3）**
- 动作：A 系列增加 `POST /api/v1/agent/workflows:export|import`；审批作为一等节点（挂起 + 通知 + 幂等恢复）；长任务分段 checkpoint（对齐 DeerFlow）。
- 验收：工作流可导出为文件并在另一实例导入执行；审批节点挂起 24h 后可恢复；失败任务可从最近 checkpoint 续跑。

**P1-4 工具包签名 + 凭证托管（补齐 G-4）**
- 动作：工具包安装验签（cosign/签名清单）；凭证加密存储（KMS/Vault 或 PG + 应用层加密）；按任务下发短时令牌，禁静态共享密钥。
- 验收：未签名工具包无法安装；任务日志中不出现任何明文凭证。

**P1-5 技能兼容层（补齐 G-5）**
- 动作：定义"技能声明"格式，兼容 Claude Skills / MCP 两种来源；MCP 默认需管理员审批 + 出站白名单。
- 验收：可导入一个外部 Skills 包并在岗位插件中调用；MCP 未审批时不可用。

**P1-6 发布工程三件套 + 升级回归（补齐 G-6）**
- 动作：CI 产出 SBOM（CycloneDX）+ VEX + THIRD_PARTY_LICENSES；增加"持久化升级测试"（PG schema 迁移 + 数据兼容）与"回放测试"（录制 LLM 响应，离线回归）。
- 验收：每个 release 附带三件套；升级测试覆盖 N-1 → N。

### P2（跟踪）

- **P2-1 精简部署档**：PG（`pg_search`）+ SeaweedFS + Valkey 三组件档，面向 50 人以下私有化客户；Milvus 列为规模档。对标 LobeHub 单库策略。
- **P2-2 桌面端可行性预研**：若做，IPC 必须"方法名白名单 + 参数 schema 校验 + 高危操作二次确认"，绝不暴露通用 `runCommand`。对标 LobeHub Critical XSS→RCE。
- **P2-3 移动端 / IM 网关**：优先 IM 网关（企业微信/飞书已有首批工具），移动端后置。
- **P2-4 上游健康度监控**：对 Dify 及任何底座依赖建立"月提交数 / release 间隔 / 安全公告响应时长"三项指标 + 阈值告警 + 退出预案演练。

---

## 8. 结论

1. **形态判断**：开源 AI 工作台已从"对话界面"进入"**Agent 团队运营**"阶段。LobeHub CAO 与 EvoFlow Agent Teams 指向同一方向：用户要的不是"一个更聪明的对话框"，而是"一支可排班、可汇报、可问责的 AI 团队"。Moy 的岗位级场景插件具备这个基因，但当前表述仍停留在"插件"，需要升级为"岗位 + 值班 + 汇报"。

2. **选型判断**：本次三个对象没有一个是"可直接采用的技术依赖"——Coze 许可最干净但已停滞，LobeHub 活跃但许可限制衍生分发，MonkeyCode 直接命中 AGPL 黑名单。**这反过来证明 Moy 的"自研适配层 + 白名单底座 + 锁精确版本"路线是当前唯一可商用且可持续的组合**。

3. **风险判断**：三个项目的安全问题高度同源——**用户可控输入进入执行面**（SQL 节点、插件、代理、上传、IPC）。Moy 的 T 系列工具与 R 系列上传是同一攻击面，必须在开放前完成 P0-1 ~ P0-4 四道闸；否则将复刻 Coze #2710/#2711 与 LobeHub 的 10 条 GHSA。

4. **差异化判断**：Moy 的真实壁垒不是"功能更多"，而是"**合规可私有化 + 治理内置 + 适配层可换底座**"三者的组合。但"记忆系统"这一核心卖点已被 LobeHub 白盒化，必须尽快把记忆做成"可见、可编辑、可导出、可删除"的资产（P1-2），否则壁垒会被叙事更快的对手抹平。

5. **执行判断**：本报告 5 条 P0 全部可在当前 Sprint 内以"测试用例 + 门禁规则"的形式落地，不需要架构变更；P1 六项对应 V1.1 路线图，其中 P1-2（白盒记忆）与 P1-1（团队运营）应优先于 P1-6（发布工程），因为它们直接决定产品叙事。

---

## 附录 A：证据索引

| 证据 | 来源 |
|---|---|
| 社区指标（stars/forks/issues/contributors/pushed_at/license） | GitHub REST API `/repos/{owner}/{repo}`、`/releases`、`/commits?since=`、`/contributors`，快照 2026-10-25 22:55 GMT+8 |
| Coze Studio 官方风险声明 | `github.com/coze-dev/coze-studio/blob/main/README.md` `[!WARNING]` 段（公网部署风险：注册、Python 代码节点、监听地址、SSRF、水平权限提升） |
| Coze Studio 跨租户漏洞 | issue #2710（2026-07-06）"Cross-tenant read, write and delete of any workspace's Database (memory table) data via the workflow SQL Customization node" |
| Coze Studio 插件 SSRF | issue #2711（2026-07-06）"Unrestricted SSRF in plugin tool execution..." |
| Coze Studio 并发崩溃 | issue #2713（2026-07-10）"工作流错误处理时引发 fatal error: concurrent map writes" |
| Coze Studio 停滞旁证 | issue #2729（2026-08-13）"这个项目已经死了,不更新 不修复"；ToolVitals 健康分 21/100（2026-10-24）；生态月报"自 4 月以来没有推送" |
| Coze Studio 部署清单 | `docker/docker-compose.yml`（10 服务：mysql 8.4.5 / redis / elasticsearch 8.18 / minio / etcd / milvus 2.5.10 / nsq×3 / coze-server / coze-web） |
| LobeHub 安全公告 | 10 条 GHSA：4gpc-rhpj-9443（Critical）、xq4x-622m-q8fq、2g9j-v25c-4j97、xmwj-c75x-6346、5mwj-v5jw-5c97、wrrr-8jcv-wjf5、j7xp-4mg9-x28r、fgx4-p8xf-qhp9、m79r-r765-5f9j、xph5-278p-26qx |
| LobeHub Electron IPC 缺陷细节 | GHSA-XQ4X-622M-Q8FQ 公告正文（`runCommand` IPC 无参数过滤，XSS 经 `window.parent.electronAPI` 触发任意命令） |
| LobeHub 供应链事件自述 | LobeHub Blog《LOL #10》: 开发依赖被投毒 → 攻击者操作 GitHub repo → 关闭全部 open issue；处置含 token 撤销、凭证轮换 |
| LobeHub 部署与密钥设计 | `docker-compose/setup.sh` 相关 commit（生成 `JWKS_KEY`，仅向 gateway 下发公钥 JWK；启动告警缺失/示例密钥） |
| LobeHub 许可 | `LICENSE`（LobeHub Community License：Apache-2.0 + 衍生分发须商业授权 + 贡献者同意单方调约） |
| MonkeyCode 许可与形态 | `LICENSE`（AGPL-3.0）；README（console 2C4G vs dev host 8C16G；私有化部署；模型支持 GLM/Kimi/MiniMax/Qwen/DeepSeek） |
| MonkeyCode 架构线索 | 仓库结构（backend / desktop(Tauri) / frontend / mobile / browser-extension / agent submodule / plugins submodule）；`desktop/ARCHITECTURE.md` 契约 |
| Flowise 归档 | GitHub API `archived=true`，最后推送 2026-08-13 |
| 2026 npm 投毒宏观背景 | 国家网络与信息安全信息通报中心通报（2026-05-26），蠕虫式传播 + preinstall/postinstall 脚本 |
| Moy 基线 | `docs/architecture-v1.0.md` §1-§3、§9.6 CI 门禁；`docs/phase1-acceptance-report.md`（15 容器实跑）；`docs/decisions/0004` |
| 前序报告 | `docs/research-2026-10-25-ai-workbench-benchmark.md`（Dify / DeerFlow / n8n 六维度对比） |

## 附录 B：数据快照原始表

```
repo                              stars    forks   issues  watchers language   license         created    pushed     30d-commits contributors
coze-dev/coze-studio              21758    3133    582     122      TypeScript Apache-2.0      2025-06-26 2026-07-29 0           61
lobehub/lobehub                   83479    16009   1167    299      TypeScript NOASSERTION    2023-05-21 2026-10-25 100+        354
chaitin/MonkeyCode                4.9k     743     371     -        Go/TS/Rust AGPL-3.0       2026       2026-10-23 100+        8
EvovexAI/EvoFlow                  512      139     8       6        TypeScript NOASSERTION    2026-05-11 2026-10-25 -           -
langgenius/dify                   159494   25207   1183    838      Python     NOASSERTION    2023-04-12 2026-10-25 -          -
bytedance/deer-flow               84410    11808   951     328      Python     MIT            2025-05-07 2026-10-25 -          -
FlowiseAI/Flowise                 55527    25081   1041    382      TypeScript NOASSERTION    2023-03-31 2026-08-13 archived   -
```

> 说明：`30d-commits` 为 `/commits?since=2026-09-25&per_page=100` 的返回条数，100 表示达到抓取上限（实际更多）；`-` 表示本次未抓取该字段。
