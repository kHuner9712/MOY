# 能力差距矩阵与分阶段路线（2026-11-03）

> **本文用途**：把「先全对齐大厂，再做垂直」与「先收窄垂直」的分歧，变成可逐项核对的工程量。
> **不做**：不修改业务代码、不重排已完成工作、不把「公开资料」冒充「实测」。

## 0. 阅读口径（先看这一节，避免误读数字）

| 标记 | 含义 |
| --- | --- |
| **实测** | 本轮或同 SHA（`38d0cd4`）真实跑出的结果，可复现 |
| **估算** | 工程人日/周，按「1 名熟悉代码的全职工程 1 天，含单测与自测，不含需求评审/UI 设计」计 |
| **未核实** | 仓库内没有可引用的竞品一手/二手核实材料，不编 |
| **代码事实** | 带 `路径:行号`，可直接打开核对 |

- 竞品事实来源：`docs/feature-parity-plan-2026-10-25.md:31-76`（公开资料整理，非一手实测，引用时保留原文日期）。该文件覆盖 WorkBuddy / AccioWork / Muse / Claude Code / Codex。
- 另已查阅 `docs/research-2026-10-25-ai-workbench-benchmark.md`：其对标对象是 **DeerFlow / Dify / EvoFlow**（开源底座），不含豆包工作、千问办公、ChatGPT dots，因此这三项在本文一律标「未核实」。
- 质量基线（**实测，SHA `38d0cd4`**）：后端 2559 passed / 238 skipped（本地无 PG），覆盖率 86.13%；前端 72 个测试文件，覆盖率 89.78/85.06/84.93/90.98；16 道部署门禁全绿。
- 对准对象中，**腾讯 WorkBuddy、AccioWork、Muse、Claude Code、Codex** 有仓库内核实材料；**字节豆包工作、阿里千问办公、ChatGPT dots** 在仓库内**未核实**，矩阵中不参与判定。

---

## 1. 对准对象与核实状态

| 对象 | 形态（已核实口径） | 核实状态 | 来源 |
| --- | --- | --- | --- |
| 腾讯 WorkBuddy | Windows/macOS 桌面，成果物导向，本地文件操作、人机双写、连接器/MCP | 已核实（公开资料） | `docs/feature-parity-plan-2026-10-25.md:31-39` |
| AccioWork | Electron 桌面，本地优先，Agent Hub、Automations、Browser、四层权限、Teams Beta | 已核实（公开资料） | 同上 `:41-48` |
| Muse（MuseWork） | Web + 桌面 + 移动，Skills 三段式、任务队列、Artifacts、Goals | 已核实（公开资料） | 同上 `:50-57` |
| Anthropic Claude Code | CLI + IDE，计划模式、MCP、Skills、Subagent、Hooks、检查点 | 已核实（公开资料） | 同上 `:59-66` |
| OpenAI Codex | CLI + 云端沙箱，并行任务、AGENTS.md、MCP、代码审查 | 已核实（公开资料） | 同上 `:68-75` |
| 字节豆包工作 | — | **未核实** | 仓库无材料 |
| 阿里千问办公 | — | **未核实** | 仓库无材料 |
| ChatGPT dots | — | **未核实** | 仓库无材料 |

> 结论使用方式：只对「已核实」的 5 个产品做竞品覆盖判断；另外 3 个对象不参与矩阵结论，只作为待补调研项。

---

## 2. 能力差距矩阵（14 项）

**分类口径**：
- **准入项**：客户在 POC/采购现场会因缺失直接淘汰；
- **主场项**：大厂用资金/团队/分发堆出来，Moy 追进去是消耗战；
- **尖刀项**：Moy 可以靠私有化/治理/可审计做出差异，且大厂结构性不愿意做。

| # | 能力域 | 分类 | 竞品覆盖（仅已核实 5 家） | 麦子现状 | 代码证据 | 补齐成本（估算，人日） | 主要模块 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 身份与权限（SSO/SCIM/角色） | 准入 | WorkBuddy/Accio 未披露 SSO/SCIM（未核实细节）；CC/Codex 企业身份未核实 | **半有**：OIDC 登录/会话/刷新/登出齐全，RBAC 有；SCIM 无 | `apps/adapter/app/api/sso_routes.py:29-39`、`app/core/security.py:541`（`require_permission`）；全仓 `scim` 0 命中（实测检索） | SCIM 10–15；其余 0–2 | `sso_routes.py`、`oidc_server.py`、`security.py`、身份域仓储 |
| 2 | 多租户与数据隔离 | 准入 | 5 家均为 SaaS/本地形态，隔离实现未核实 | **有**：`X-Tenant-Id` 入口 + 应用层过滤 + 跨租户攻击用例 | `apps/adapter/app/core/security.py:485-488`（`resolve_tenant_id`）、`app/core/security.py:31`、`tests/integration/test_tenant_isolation.py`、`tests/security/test_tenant_spoofing.py` | 0–3（RLS 加固可选） | `db/repositories/*`、`core/security.py` |
| 3 | 对话与任务执行 | 准入 | 5 家全部有「下发→规划→执行→状态可见」 | **半有**：对话/SSE/任务队列/取消/续跑/插件步骤计划有；**通用任务无「先出计划再确认」** | `app/business/task/runner.py:207-218`、`runner.py:915-997`、`app/plugins/engine.py:266`；通用规划缺失（`services/goal_router.py` 只做能力匹配） | 计划生成+确认 8–12；其余 0 | `business/task/*`、`services/goal_router.py`、`plugins/engine.py` |
| 4 | 文档与知识（上传解析/RAG/引用） | 准入 | WorkBuddy/Accio/Muse/CC/Codex 均有文件输入；细节未核实 | **有（文本类）**：pdf/docx/xlsx/md/txt 上传 + 魔数校验 + 异步解析 + RAG 检索 + 引用；图片 OCR 无 | `app/api/v1/rag.py:3-13`、`app/services/upload_content.py:1-30`、`app/registry/profile_routes.yaml:75-80` | OCR 8–12（非必须） | `rag.py`、`rag_service.py`、`upload_content.py` |
| 5 | 办公成果物（docx/xlsx/pptx/预览/双写） | 准入 | WorkBuddy 文档/表格/PPT；Muse Artifacts；Accio 报告/代码/网页 | **半有**：docx/md/csv 生成 + 文本/表格预览 + 下载；**无 xlsx/pptx/pdf、无 Office 预览、无双写** | `app/plugins/renderer.py:43`（`RENDER_FORMATS=("docx","md","csv")`）、`apps/web/src/components/workflow/artifact-card.tsx:161-201` | xlsx 14–20；pptx 14–22；Office 预览 6–10；pdf 4–8；双写 60+（不计入基线） | `plugins/renderer.py`、`actions.py`、`api/v1/artifact_files.py`、前端产物卡片 |
| 6 | 集成与连接器（MCP/API/IM） | 准入 | WorkBuddy/Accio/CC/Codex 有 MCP；Muse 部分；IM 渠道仅 Accio 明确 | **半有**：5 个连接器（飞书/企微/公众号/邮件/GitHub），密钥自填；**MCP 客户端已落地**（2026-11-06，外部服务登记/探测/白名单/超时与输出上限，默认关闭）；仍无连接器 OAuth 回调、无 IM 入站渠道 | `app/clients/mcp_client.py`、`app/business/mcp_service.py`、`app/services/mcp_runner.py`、`app/api/v1/ops_mcp.py`、`app/tools/base.py:55-236`、`app/tools/{feishu,wecom,wechat_mp,email,github}_connector.py` | MCP 客户端 0（已完成）；OAuth 3 个 10–15；IM 入站 8–12 | `app/clients/*`、`app/tools/*`、`tool_service.py`、`api/v1/tools_ext.py` |
| 7 | 插件与技能 | 尖刀（对生态）/准入（对官方能力） | 5 家均有 Skills；3 家有市场 | **有骨架（官方 6 个）**：声明式 manifest、安装即启用、6 原语引擎、护栏；无第三方发布/签名/SDK | `app/plugins/schema.py:208`（`extra="forbid"` 基类）、`actions.py:28-36`、`business/plugin_service.py:487-504`、`app/plugins/manifests/*.json`（6 份） | 官方深化按垂直计；第三方生态 V1.1（见 §5） | `plugins/*`、`business/plugin_service.py`、前端 plugins 页 |
| 8 | 用量与计费 | 准入 | 5 家计费口径未核实（WorkBuddy 有费用统计宣称需一手核验） | **半有**：usage_event、配额、套餐/订单、月度对账；**无按插件归因、无发票/结算** | `deploy/init-schema-phase2.sql:288-305`、`app/api/v1/billing.py`、`app/services/model_service.py:897`、`app/core/audit_actions.py:70-71` | 插件归因 8–12；账单导出 8–12 | `business/usage_service.py`、`billing.py`、`db/repositories/usage/*` |
| 9 | 审计与合规 | 准入 | Accio 明示本地审计；其余未核实 | **半有**：审计查询/动作枚举/权限拒绝留痕；**无导出；合规文档待建** | `app/api/v1/identity.py:236-266`、`app/db/repositories/audit_repo.py:75-123`（`ACTION_GROUP_NAMES`/`ALL_ACTIONS`）、`business/audit_service.py` | 审计导出 6–10；合规三件套 10–15（含法务） | `identity.py`、`audit_service.py`、导出 worker |
| 10 | 部署形态（SaaS/私有化/气隙） | **尖刀** | WorkBuddy/Accio/Muse 为桌面/云；气隙私有化未核实 | **半有**：compose 9 容器、k8s base、`install.sh --offline` 加载侧；**离线包构建脚本未实现**，`OA_OFFLINE_MODE` 死配置已于 2026-11-04 审计删除 | `deploy/docker-compose.lite.yml`、`deploy/k8s/base/`、`deploy/install.sh:657-709`、`deploy/offline/README.md:1-22`、`deploy/validate_config_names.py:145/459` | 离线包构建+校验 12–18；气隙验收 3–5 | `deploy/offline/*`、`install.sh`、`images.lock`、SBOM |
| 11 | 可观测与运维 | 准入 | 5 家运维能力未核实 | **有**：`/healthz` `/readyz` `/metrics`、Prometheus 规则、队列深度、ops 端点、备份/恢复 | `app/api/v1/router.py:90-147`（`/healthz`/`/readyz`）、`app/core/metrics.py`、`deploy/prometheus.yml`、`deploy/alert.rules.yml`、`docs/deployment-guide.md:41-42` | 支持包/诊断导出 3–5 | `core/metrics.py`、`workers/queue_depth.py`、`api/v1/ops.py`、deploy 监控 |
| 12 | 客户端形态（Web/桌面/移动） | 主场 | WorkBuddy/Accio 桌面；Muse Web+桌面+移动；CC/Codex CLI | **半有**：仅 Web（`apps/web`）与后端；无桌面、无移动、无 CLI | 仓库顶层仅 `apps/adapter`、`apps/web`（实测目录）；前端 `apps/web/src/app/(main)/*` | 桌面 40–60；移动 50–80（均不计入基线） | 新增 `apps/desktop` / `apps/mobile`，共享 API 契约 |
| 13 | 可靠性与性能 | 主场 | 5 家容量承诺未核实（不可比） | **半有**：任务重试/续跑、队列上界、原语超时、备份恢复；**吞吐/首字 P95 全部待压测** | `business/task/runner.py:915-997`、`plugins/actions.py:165`、`docs/perf-disclosure.md:128-139`（明确写「待压测」） | 压测+SLO 调优 8–12 | `scripts/bench/*`、`perf-disclosure.md`、task/queue |
| 14 | 协作与分享 | 主场 | Accio Teams（Beta）；WorkBuddy 多任务；其余未核实 | **无（仅组织管理）**：成员/邀请/角色有；无分享链接、评论、@、协同编辑 | `app/api/v1/identity_ext.py:156-584`（部门/角色/成员端点）；`app/api/v1` 与 `apps/web/src/app/(main)` 检索 `share/comment` 0 命中（实测检索） | 分享 10–15；评论 15–20；双写 60+ | 新增分享/评论域 + 前端组件 |

**统计（实测计数）**：矩阵 **14 项**；准入 **9 项**（#1–6、#8、#9、#11）；主场 **3 项**（#12–14）；尖刀 **2 项**（#7、#10）。

> 说明：`#7 插件与技能` 对「官方能力」是准入（客户会问有没有技能），对「第三方生态」是尖刀；`#10 部署形态` 对气隙客户是准入，对通用客户是差异化。分类按「竞争姿态」记，不按「是否必须做」记。

---

## 3. 准入基线：打平到「客户不淘汰」的最小集合

### 3.1 基线清单（只列「当前缺失/半有且客户会当场对比」的项）

| # | 基线项 | 为什么进基线 | 成本（估算，人日） |
| --- | --- | --- | --- |
| B0 | SSO/OIDC + RBAC + 租户隔离 + 备份恢复（已有） | 企业采购硬门槛 | **0（已完成）** |
| B1 | 通用任务「计划生成→确认→执行」 | 5 家已核实产品全部具备；缺失会被当成「聊天框」 | 8–12 |
| B2 | xlsx 成果物 + Office 产物预览 | 办公客户当场对比 WorkBuddy/Muse；只有 docx/csv 会被淘汰 | 20–30 |
| B3 | MCP 客户端（工具命名空间 + 管理员审批） | 扩展性的最低成本路径；CC/Codex/WB/Accio 都有 | **0（已完成，2026-11-06）** |
| B4 | 审计日志导出（CSV/JSON + 导出留痕） | 私有化/受监管客户必问；当前只有查询 | **0（已完成，2026-11-06）** |
| B5 | 气隙离线包构建 + 校验（不是加载侧） | 「数据不出内网」的交付前提；当前构建侧为空 | 12–18 |
| B6 | 会话级能力集 + 四层授权（自动/每次问/记住/拒绝） | 敏感操作客户会问；Accio/CC/Codex 明示 | 8–12 |
| B7 | 性能基线：首字 P95、检索 P95、100/200 并发 | 客户 POC 会看；当前全部「待压测」 | 8–12 |

**B2 状态（2026-11-05）**：xlsx 成果物生成 / 按需导出、docx·xlsx 离线 PDF 预览已交付。
**验证边界**：LibreOffice 真实转换**仅做 mock 与接口层验证，真机效果未验证**（本机无 soffice、
CI 无 Docker），需部署环境按 [交付文档](file:///c:/Users/15864/Desktop/麦子/docs/delivery-2026-11-05-b2-xlsx-preview.md) 的
5 分钟步骤验收；**不得把 CI 绿读成"预览已可用"**。pptx 不支持预览（仅下载）。

**B1 状态（2026-11-06）**：通用任务「计划生成 → 确认 → 执行」已交付——`plan_first=true` 创建
`waiting_plan` 任务，前端读取结构化计划（步骤/工具/预计产物）、可勾选编辑/确认/放弃；
**确认前不执行任何有副作用能力**（`waiting_plan` 不在 `_LIVE_TASK_STATUSES`，runner 抢不到锁）。
默认关闭（`OA_TASK_PLAN_ENABLED=false`），计划超时（默认 900 秒）后台清理为 `cancelled`。
验证边界：工具副作用为**替身网关（EchoGateway）验证**，真实第三方工具调用未在 CI 触发；
人工 5 分钟验收见 [交付文档](file:///c:/Users/15864/Desktop/麦子/docs/delivery-2026-11-06-task-plan-confirm.md)。
矩阵第 2 项（B1）状态：**已完成（2026-11-06）**。

**B3 状态（2026-11-06）**：MCP 客户端已交付——4 个运营端点（登记/列表/删除/探测）+
`mcp_server` 表（strict RLS），插件步骤以 `tool_type="mcp"` 调用白名单工具；凭据只存
`cred_xxx` 引用、响应不回引用；stdio 默认关且生产/预发拒绝，legacy SSE 登记即拒；超时、
输出上限、错误隔离均为显式收敛。默认关闭（`OA_MCP_ENABLED=false`）。
验证边界：真实第三方 MCP server **未验证**（只连本地最小 server，HTTP + 真 stdio）；
PG 仓储/RLS 与多副本并发未在本地验证，详见
[交付文档](file:///c:/Users/15864/Desktop/麦子/docs/delivery-2026-11-06-mcp-client.md)。
矩阵第 3 项（B3）状态：**已完成（2026-11-06）**。

**B4 状态（2026-11-06）**：操作记录导出已交付——`GET /api/v1/identity/audit-logs/export`
按租户 + 时间范围导出 CSV / JSON Lines；起止必填、跨度 ≤90 天、行数上限（默认 20 万）报错不截断、
限流（默认 3 次/小时），键集分页 + 流式写出（不整表进内存）。每次导出写一条 `audit_log.export`
留痕（人/范围/条数），且该动作被排除在导出集之外（不递归）。权限复用 `audit:log:read`，
平台令牌拒绝，`tenant_id` 恒取令牌，`tenant_scope` + RLS 双层生效；迁移 0016 只加索引并复核
RLS 留存，回滚只删索引。默认关闭（`OA_AUDIT_LOG_EXPORT_ENABLED=false`）。
验证边界：**PG 键集分页/RLS 与真实大数据量未在本地验证**（本地无 PostgreSQL、未做 20 万行压测），
由 CI 的 PG 服务与部署环境按 [交付文档](file:///c:/Users/15864/Desktop/麦子/docs/delivery-2026-11-06-audit-export.md)
5 分钟步骤验收。
矩阵第 4 项（B4）状态：**已完成（2026-11-06）**。

**合计（估算）**：**72–109 人日，中位 90 人日**（含 B2 的 Office 预览，不含 pptx、双写、桌面/移动）。

### 3.2 6–10 周能否达成（直接回答）

- **按现状：1 名全职开发（独立开发者）= 不能。**
  - 90 人日 ÷ 5 人日/周 ≈ **18 周**（区间 14–22 周），6–10 周只能完成 30–50 人日，约为基线的 33%–56%。
- **增援到 2 名全职（且需求冻结）**：90 ÷ 10 ≈ **9 周**（区间 7–11 周），**可以落在 6–10 周区间**，但没有缓冲；任何一项超 30% 就会滑出。
- **增援到 3 名全职**：90 ÷ 15 ≈ **6 周**，可留 1–2 周缓冲；但新增人手有 1–2 周上手成本，实际约 7–8 周。

**判断**：不是「6–10 周能不能做完」，而是「**几个人做**」。按 1 人，答案是否定的；按 2 人且冻结范围，答案是「勉强可以」；按 3 人，答案是可以。

### 3.3 若只能保 5 项（最小可演示基线）

B2 xlsx+预览（22）→ B1 计划确认（10）→ B3 MCP（12）→ B4 审计导出（8）→ B5 离线包（15），合计 **67 人日（估算，区间 56–85）**。这是「客户不当场淘汰」的下限，不含性能调优与四层授权。

---

## 4. 分阶段路线

> 与「先收窄垂直」的差别：本路线承认**准入基线必须先打平**，但把「全对齐」限制在 §3 的 8 项，不做大厂的主场项。
> 与「先全对齐」的差别：基线期一结束就必须起尖刀，不允许无限期补功能。

### 阶段一：准入基线冲刺（目标 8–10 周，2 名全职；1 人则 16–20 周）

- **范围**：§3.1 的 B1–B7，需求冻结；pptx、双写、桌面、移动、OAuth 扩展不进本阶段。
- **验收口径（全部可测）**：
  1. 一份 10 分钟演示脚本：企业 SSO 登录 → 一句话生成计划并确认 → 上传 xlsx 提问 → 产出 xlsx 并预览 → 调用 1 个 MCP 工具 → 导出审计日志；全程无阻断。
  2. 气隙机器：`install.sh --offline` 一次装成，断网可用，篡改包退出码 3。
  3. 性能：首字 P95（mock 上游）≤1s、检索 P95 ≤800ms、100 并发错误率 <1%（实测产物落 `results/`，不得手填）。
  4. 质量门禁：后端/前端覆盖率不低于现值（86.13 / 89.78/85.06/84.93/90.98），16 道门禁全绿。
  5. 审计导出：导出行为本身留痕，导出文件不含密钥/内部路径。

### 阶段二：同期起一个尖刀（与阶段一并行，从第 1 周开始）

- **选哪一个**：**气隙私有化一体机**（#10）。理由：已半有（加载侧/监控/备份都在），补齐成本最低（12–18 人日）；且它是大厂结构性不愿意做的交付形态，越早形成销售话术越值钱。
- **验收口径**：
  1. 无外网环境 3 次重复安装成功率 3/3；
  2. 交付物含 SBOM + 第三方许可证 + 离线介质清单；
  3. 客户自带模型 Key 的注入契约文档化，交付物零供应商 Key；
  4. 许可证/节点授权最小闭环（错节点/过期拒启，正常可用）。
- **不选垂直深化的原因**：垂直深化依赖阶段一的成果物与计划能力；在基线未定时做深，会反复返工。垂直放阶段三。

### 阶段三：垂直深化与市场开放（基线达成后启动，V1.1）

- **垂直深化**：选 1 条垂直线（行政人事/内容运营/研发工作台，待拍板），把 2–6 步的浅工作流做成 10+ 步、带领域护栏与人工接管点的深工作流；验收：20 条端到端样例、产物抽检、失败可续跑。
- **市场开放**：第三方提交/审核/签名/分账/SDK（见 `docs/platform-strategy-2026-11-03.md` §4.2）；验收：未签名包拒绝加载、审核状态可查、账单可追溯到事件。
- **准入**：阶段一验收全绿 + 至少 1 个付费私有化 POC。

---

## 5. 明确不做清单（附理由，避免反复拉扯）

| 不做项 | 理由（工程/商业） |
| --- | --- |
| 桌面客户端（Windows/macOS） | 40–60 人日，且要与 WorkBuddy/Accio 在分发与本地文件权限上正面竞争；目标 20–100 席私有化客户可用 Web + API 交付 |
| 移动客户端 | 50–80 人日；目标客户办公场景以桌面/Web 为主；Muse 的移动优势不是本细分客户的淘汰项 |
| Office 人机双写/多人协同编辑 | 60+ 人日；WorkBuddy 的杀手级能力，追进去是主场消耗战；先做「生成 + 预览 + 下载」即可交付 |
| 浏览器自动化 / 终端代码执行 | 25–40 人日 + 持续安全责任；不属于 20–100 席办公客户的准入项；需要时用底座 Dify Sandbox，不另造 |
| 多 Agent 团队（Lead + 成员） | 30–50 人日；Accio Teams 仍是 Beta；目标客户更看重单任务可靠交付 |
| 1000+ 连接器市场 | 生态第二阶段；V1.0 只需 3–5 个刚需连接器 + MCP |
| 消费级分发 / 免费版裂变 | 与「20–100 席私有化」客单价模型冲突 |
| 编码 IDE | Claude Code/Codex 的主场，Moy 不做正面竞争 |
| 跨境电商全链路 | 已有明确垂直玩家（Accio），且与当前客户群不重合 |
| 追 500 并发的大厂容量数字 | 目标客户规模 20–100 席；只需证明 100–200 并发 SLO，不为不可比数字投入 |

---

## 6. 如果按「先全对齐」走：三种最可能的失败模式

### 6.1 追移动靶

- **机制**：大厂季度级发版，基线定义随竞品漂移；每加一项 P0，交付再延 2–4 周。
- **可观测信号**：
  - 4 周内新增「必须对齐」项 ≥3 个；
  - 基线完成率连续 2 周 <70%；
  - 同一客户 Demo 仍反复问最初那 3 个功能。
- **止损线**：第 8 周基线完成率 <60%，或新增 P0 ≥3 个 → 冻结对齐，转入「5 项最小基线 + 尖刀」，其余进 V1.1。

### 6.2 工期失控

- **机制**：跨模块改造（任务规划、产物渲染、权限模型）互相牵连；估算方差累积。
- **可观测信号**：
  - 周实际消耗 > 计划 20% 连续两周；
  - 单模块估算偏差 >30% 连续两个迭代；
  - 为赶工期出现「跳过门禁/降覆盖率」的提议；
  - 回归缺陷率上升（每周 P1 ≥2）。
- **止损线**：第 6 周基线完成 <50% → 砍到 §3.3 的 5 项；仍超期则承认 6–10 周不可达，改为 12–14 周并同步客户预期。

### 6.3 差异化窗口关闭

- **机制**：把 8–10 周全投在「打平」，期间没有可售的私有化/垂直样板；等基线做完，客户已被大厂私有化版本或垂直玩家拿走。
- **可观测信号**：
  - 基线期内 0 个付费 POC / 0 个意向书；
  - 目标客户开始问「你们和 XX 私有化版有什么区别」而答不出；
  - 竞品发布气隙/私有化能力（需每两周核实一次）。
- **止损线**：第 6 周仍无付费 POC → 立即把阶段二尖刀提到 50% 人力，基线降为「5 项最小集」；若第 10 周仍无 POC，停止泛化补齐，回到垂直深耕。

---

## 7. 需要用户拍板

1. **准入范围是否包含 SCIM**（+10–15 人日）？20–100 席客户是否强制要求组织自动同步？
2. **V1.0 是否必须支持 xlsx + Office 预览**（+20–30 人日）？如果客户以「生成 Word/PDF 为主」，可降级到 V1.1，基线缩到 50–79 人日。
   → **已拍板（2026-11-05）**：进 V1.0，按 B2 实施；xlsx 导出默认开、预览默认关（部署验收后开启）。
   其余默认决策：openpyxl（MIT，延迟导入，缺依赖降级 CSV）、pptx 仅下载、无 DB 迁移（复用既有
   `content_type` 列）、API 只增不改（`StepArtifact` 新增 3 个可空字段 + 2 条 `/files/` 路由）。
3. **是否接受桌面客户端延后**（不做 40–60 人日）？目标客户能否接受 Web 交付？
4. **是否接受 6–10 周基线期**，并在此期间**冻结需求**（不新增大厂对齐项）？
5. **投入几个人**：1 人（现状，基线 16–20 周）/ 2 人（8–10 周）/ 3 人（7–8 周，含上手）？
6. **连接器 OAuth 是否进基线**（+10–15 人日）？私有化客户能否接受管理员手填密钥？
7. **阶段二尖刀是否确认为「气隙私有化一体机」**，而不是先做垂直深化？
8. **协作/分享是否确认不做**（分享 10–15、评论 15–20、双写 60+）？

---

## 附：本矩阵的计数与复算

- 矩阵项：**14**（准入 9 / 主场 3 / 尖刀 2）。
- 准入基线：**8 项（B0–B7）**，其中 B0 已完成；新增成本 **72–109 人日（估算，中位 90）**。
- 最小可演示基线（5 项）：**56–85 人日（估算，中位 67）**。
- 6–10 周可行性：**1 人不可；2 人勉强可（需冻结范围）；3 人可**。
- 实测质量基线：后端 2559 passed / 238 skipped、覆盖率 86.13%；前端 72 文件、覆盖率 89.78/85.06/84.93/90.98；16 道门禁全绿（SHA `38d0cd4`）。
