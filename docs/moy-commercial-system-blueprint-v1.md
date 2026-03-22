# MOY 商业级销售执行系统蓝图 v1

更新时间：2026-03-22  
适用范围：MOY 仓库后续架构演进、需求评审、实现落地与验收对齐

---

## 1. 目标与边界

本蓝图不是抽象战略文档，而是基于当前仓库已落地能力，定义 MOY 作为“商业级销售执行系统”的统一系统定义。  
文中所有结论均可在现有代码中找到对应路由、服务、类型或 migration 依据。

---

## 2. MOY 是什么 / 不是什么

### 2.1 MOY 是什么

MOY 是面向 B2B 团队的销售执行系统（Sales Execution System），核心是把销售过程变成可记录、可执行、可判断、可管理、可治理、可商业化运营的闭环系统。

在仓库中，这一定位已对应到：

- 记录与执行主链路：`/capture`、`/today`、`/deals`、`/touchpoints`、`/outcomes`
- 管理与经营视角：`/manager/*`、`/executive`、`/reports`、`/growth`
- 控制平面：`/settings/*`（模板、组织配置、AI 控制、自动化、配置治理、审计与回滚）
- 商业化入口：`/request-demo`、`/start-trial` + `app/api/public/*`

### 2.2 MOY 不是什么

MOY 不是：

- 通用聊天工具（AI 调用以业务场景为单位，而非开放问答）
- 只做记录的静态 CRM（系统会触发动作、生成任务、跟踪结果）
- 只做看板的数据工具（包含执行与干预路径）
- 零治理的“功能集合”（已存在配置写入治理、并发保护、审计、回滚）

---

## 3. 六层系统定义（与仓库映射）

## 3.1 Record System（记录系统）

系统职责：把一线真实动作和经营事实沉淀为可追踪事实层。

已落地点：

- 业务记录对象：`communication_inputs`、`followups`、`customers`、`opportunities`、`deal_rooms`
- 外部触点记录：`email_threads`、`calendar_events`、`document_assets`、`external_touchpoint_events`
- 结果记录：`action_outcomes`、`suggestion_adoptions`
- 类型与服务：`types/communication.ts`、`types/work.ts`、`types/outcome.ts`、`services/*-service.ts`

当前结论：记录系统主体已形成，具备业务闭环输入输出基础。

## 3.2 Execution System（执行系统）

系统职责：把问题转成行动，把行动变成完成状态。

已落地点：

- 任务与计划：`work_items`、`daily_work_plans`、`daily_work_plan_items`、`task_execution_logs`
- 执行入口：`/today`、`/alerts`、`/deals`、`/followups/new`
- 核心服务：`services/work-item-service.ts`、`services/work-agent-service.ts`、`services/alert-rule-engine.ts`
- 自动动作：`app/api/work-items/from-alert/route.ts`、`app/api/today/generate-plan/route.ts`

当前结论：执行层具备“触发-分配-状态流转-日志”的系统化能力。

## 3.3 Judgment System（判断系统）

系统职责：对执行与经营状态做规则判断和 AI 判断，形成可执行信号。

已落地点：

- 规则判断：`alert_rules`、`automation_rules`、`business_events`
- AI 场景判断：`communication_extraction`、`followup_analysis`、`customer_health_summary`、`trial_conversion_review` 等
- 健康与预警对象：`customer_health_snapshots`、`renewal_watch_items`
- 核心服务：`services/ai-analysis-service.ts`、`services/business-event-service.ts`、`services/customer-health-service.ts`

当前结论：判断系统已具备“双引擎”（规则 + AI）并支持 fallback。

## 3.4 Management System（管理系统）

系统职责：支持经理与高管进行干预、校准与经营节奏管理。

已落地点：

- 经理视角：`/manager`、`/manager/quality`、`/manager/rhythm`、`/manager/outcomes`、`/manager/conversion`
- 高管视角：`/executive`
- 干预对象：`intervention_requests`、`decision_records`、`manager_desk_intervention_records`
- 聚合服务：`services/manager-desk-service.ts`、`services/manager-insights-service.ts`、`services/executive-cockpit-service.ts`

当前结论：管理系统已能从“看数据”走向“发起干预+跟踪结果”。

## 3.5 Governance System（治理系统）

系统职责：保障配置变更可控、可解释、可审计、可回滚。

已落地点：

- 组织配置域：`org_settings`、`org_ai_settings`、`org_feature_flags`
- 模板与覆盖：`industry_templates`、`org_template_assignments`、`org_template_overrides`
- 写入治理与并发保护：`services/org-config-governance-service.ts`、`lib/override-concurrency-guard.ts`
- 审计与回滚：`org_config_audit_logs`、`/settings/config-ops`、`/settings/config-timeline`、rollback preview/execute API

当前结论：治理系统已形成“预览-写入-审计-回滚-时间线”的闭环雏形。

## 3.6 Commercialization System（商业化系统）

系统职责：承接公域获客、试用转化与计划配额经营。

已落地点：

- 公域入口：`/request-demo`、`/start-trial`、`app/api/public/request-demo/route.ts`、`app/api/public/start-trial/route.ts`
- 商业化实体：`inbound_leads`、`demo_requests`、`trial_requests`、`trial_conversion_tracks`、`conversion_events`
- 计划与配额：`org_plan_profiles`、`org_usage_counters`、`user_usage_counters`
- 服务：`services/inbound-lead-service.ts`、`services/trial-request-service.ts`、`services/plan-entitlement-service.ts`

当前结论：商业化系统已不只是市场页面，已接入执行系统与管理系统。

---

## 4. 三条核心闭环（系统级）

## 4.1 闭环 A：communication -> analysis -> action -> outcome

仓库映射：

1. `communication_inputs` 写入（`/api/capture/extract`）
2. 提取与分析（`communication_extraction` + `followup_analysis`）
3. 动作生成与执行（`alerts` / `work_items` / `daily_work_plans`）
4. 结果回写（`/api/outcomes/capture` -> `action_outcomes`）
5. 价值归因（`/api/attribution`、`/api/value-metrics`）

系统要求：任何 AI 失败不应中断闭环，必须有规则或人工兜底路径。

## 4.2 闭环 B：frontline behavior -> risk/event -> manager intervention -> result

仓库映射：

1. 一线行为进入记录层（followup / touchpoint / work_item）
2. 风险事件生成（`business_events` + `alerts`）
3. 管理介入（`manager_desk`、`intervention_requests`、`executive`）
4. 干预动作执行并回写 outcome（`action_outcomes` + event status）
5. 结果再进入管理视图（manager insights / executive cockpit）

系统要求：干预必须带来源、责任人、状态流转，避免“只有提醒、没有处理”。

## 4.3 闭环 C：template/config -> runtime behavior -> feedback -> system optimization

仓库映射：

1. 模板与配置变更（templates / org-config / ai / automation）
2. 运行时生效（runtime bridge + feature access + rule seeds）
3. 反馈采集（runtime debug / config-ops / config-timeline / usage / adoptions）
4. 优化动作（governed write + rollback + reassignment/threshold tuning）

系统要求：控制平面变更必须可追溯、可比较、可回滚，不允许黑箱配置漂移。

---

## 5. 一等实体（First-Class Entities）

以下实体已在仓库明确存在或已被当前流程强依赖（implied）：

| 领域 | 一等实体 | 主要类型/服务 | 主要表 |
| --- | --- | --- | --- |
| 组织与权限 | Organization, Profile, OrgMembership, OrgInvite | `types/productization.ts`, `services/org-membership-service.ts` | `organizations`, `profiles`, `org_memberships`, `org_invites` |
| 销售记录 | Customer, Opportunity, CommunicationInput, Followup | `types/customer.ts`, `types/communication.ts` | `customers`, `opportunities`, `communication_inputs`, `followups` |
| 执行 | WorkItem, DailyWorkPlan, TaskExecutionLog | `types/work.ts`, `services/work-item-service.ts` | `work_items`, `daily_work_plans`, `task_execution_logs` |
| 协同成交 | DealRoom, DealCheckpoint, DecisionRecord, InterventionRequest | `types/deal.ts` | `deal_rooms`, `deal_checkpoints`, `decision_records`, `intervention_requests` |
| 外部触点 | EmailThread, CalendarEvent, DocumentAsset, ExternalTouchpointEvent | `types/touchpoint.ts` | `email_threads`, `calendar_events`, `document_assets`, `external_touchpoint_events` |
| 判断与事件 | Alert, BusinessEvent, AutomationRule | `types/alert.ts`, `types/automation.ts` | `alerts`, `business_events`, `automation_rules`, `automation_rule_runs` |
| 结果与归因 | ActionOutcome, SuggestionAdoption, OutcomeReview | `types/outcome.ts`, `services/attribution-service.ts` | `action_outcomes`, `suggestion_adoptions`, `outcome_reviews` |
| 商业化 | InboundLead, DemoRequest, TrialRequest, TrialConversionTrack, ConversionEvent | `types/commercialization.ts` | `inbound_leads`, `demo_requests`, `trial_requests`, `trial_conversion_tracks`, `conversion_events` |
| 治理控制 | OrgSettings, OrgAiSettings, OrgFeatureFlag, OrgTemplateOverride, OrgConfigAuditLog | `services/org-config-governance-service.ts` | `org_settings`, `org_ai_settings`, `org_feature_flags`, `org_template_overrides`, `org_config_audit_logs` |
| 系统运行 | AiRun, AiPromptVersion, UsageCounter, EntitlementStatus | `types/ai.ts`, `services/plan-entitlement-service.ts` | `ai_runs`, `ai_prompt_versions`, `org_usage_counters`, `org_plan_profiles` |

补充（implied but critical）：

- `source_ref_type/source_ref_id`（跨对象来源追踪）
- `linked_business_event_ids`（事件到结果归因链）
- `expectedVersion/compareToken`（控制平面并发一致性）

---

## 6. 事件模型方向（Event Model Direction）

当前仓库已有三类核心事件流：

- 执行/经营事件：`business_events`
- 商业化漏斗事件：`conversion_events`
- 配置治理审计事件：`org_config_audit_logs`

方向定义（v1）：

1. 事件不是“日志附属”，而是系统一等资产，需具备：`org_id + entity + event_type + status + source + payload + time`
2. 保持现有表不推倒，在服务层建立统一事件语义映射（Business / Conversion / Governance）
3. 强化 source trace：统一要求动作对象保留来源引用（如 `source_ref_*`、`linked_business_event_ids`）
4. 所有关键状态变化走显式状态机（已存在的 open->acknowledged->resolved/ignored 继续扩展）
5. 事件消费要能回到价值归因（attribution / value-metrics / growth）

---

## 7. 控制平面方向（Control-Plane Direction）

控制平面定义：负责“系统如何运行”的配置、权限、配额、模板与治理，不直接承载一线业务数据。

当前控制平面已包含：

- 配置面：`/settings/org`, `/settings/ai`, `/settings/team`, `/settings/templates`, `/settings/automation`
- 治理面：`/settings/org-config`, `/settings/runtime-debug`, `/settings/config-ops`, `/settings/config-timeline`
- 机制面：governed write、expectedVersion 并发保护、persisted audit、rollback preview/execute

方向定义（v1）：

1. 所有高风险配置写入统一接入治理服务（诊断、并发、审计）
2. 管理权限默认“最小授权”，写入以 owner/admin 为主，manager 以可见与预览为主
3. 控制平面 API 输出继续统一为：`data + writeDiagnostics + concurrency + persistedAudit`
4. 运行时解释（runtime explain）作为可观测性标准能力，不是临时 debug 工具

---

## 8. 商业化方向（Commercialization Direction）

商业化目标不是单点“获客表单”，而是“获客 -> 执行 -> 首次价值 -> 转化”系统闭环。

当前主路径：

1. 公域提交（demo/trial/contact）
2. lead 资格评估 + owner 分配
3. 自动进入销售执行（customer/opportunity/deal/work_item）
4. trial org 激活与模板引导（trial_bootstrap + template apply）
5. conversion event 追踪 + growth summary
6. plan/usage 约束与商业策略联动

方向定义（v1）：

1. 持续强化 `conversion_events` 与 `business_events` 的桥接，打通“转化信号 -> 执行动作”
2. 以“首次价值出现（first value）”作为 trial 转化核心里程碑
3. 商业化过程遵循与内部系统一致的权限、审计与治理规则（不是旁路系统）
4. 对外承诺语言保持“系统价值”导向：执行效率、风险降低、转化推进、管理可见

---

## 9. 作为后续任务北极星的落地准则

后续任何需求，默认按以下顺序判定：

1. 是否加强六层系统之一，而不是新增孤立功能
2. 是否补强三条闭环之一的断点
3. 是否复用现有实体与事件，而不是新建平行模型
4. 是否保持权限、API 行为、UI 术语、数据策略一致
5. 是否具备可追溯来源与审计能力

满足以上条件，才视为“系统级增量演进”。

