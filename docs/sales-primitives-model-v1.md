# MOY 销售共性原语模型 v1（内部）

更新时间：2026-03-21  
适用范围：产品、设计、工程在“底座/模板/企业配置”三层中的需求拆解与实现对齐  
关联文档：`docs/product-architecture-principles-v1.md`

---

## 1. 为什么 MOY 需要“销售共性原语”

MOY 当前能力已经覆盖获客、跟进、风控、任务、管理可见、复盘与商业化转化，但对象分散在多个模块。  
如果没有统一“原语层”，会出现三个问题：

- 新需求反复“按页面思维”实现，难复用到底座。
- 模板层与企业配置层容易越界，侵入核心语义。
- AI 输出 schema、数据库字段、前端文案逐步漂移，造成同名异义。

因此，需要先定义跨行业稳定原语，再让模板和企业配置在原语之上扩展。

---

## 2. 原语设计原则

### 2.1 跨行业稳定

- 任何行业都要有：客户、商机、沟通输入、跟进、任务、风险、结果、负责人、状态流。
- 原语可扩字段，但不改主语义。

### 2.2 可被模板扩展

- 模板只能改阈值、话术、推荐策略、显示层聚合。
- 模板不能改对象关系与关键状态机。

### 2.3 不被单客户定制破坏

- 企业定制走配置，不走分叉代码。
- 不接受“单客户独享状态语义”或“绕过底座约束”。

### 2.4 与当前仓库对象保持映射

- 原语定义必须落到已有 `types/*`、`services/*`、`tests/*`。
- 无法映射的能力标记为 `fragmented`，作为后续建设项，而非直接宣称“已完成”。

---

## 3. 核心实体（逐项定义）

### 3.1 客户 customer

- 当前模型：`types/customer.ts` 的 `Customer`。
- 语义：销售推进的核心业务对象，承载阶段、风险、负责人、下一次跟进时间、AI摘要。
- 现状：稳定。

### 3.2 联系人 / 关键人 contact / decision actor

- 当前模型现状：
  - 客户联系人在 `Customer.contactName/phone/email`（客户级扁平字段）。
  - 决策链信息以 `Customer.hasDecisionMaker`、AI 抽取结果 `decision_makers`（`types/ai.ts`）存在。
  - Deal 内部协作者是 `DealParticipant`（`types/deal.ts`），但这是内部团队角色，不是客户侧联系人模型。
- 结论：当前没有独立 `contact` 原语，属于 `fragmented`。

### 3.3 商机 opportunity

- 当前模型：`types/opportunity.ts` 的 `Opportunity`。
- 语义：客户商业机会及金额、阶段、风险、预计关闭时间。
- 现状：稳定。

### 3.4 沟通输入 communication input

- 当前模型：`types/communication.ts` 的 `CommunicationInputItem`。
- 语义：销售原始沟通事实输入（手记、粘贴聊天、会议纪要、语音转写等）。
- 与跟进衔接：`FollowupRecord.sourceInputId`。
- 现状：稳定但使用深度仍在演进。

### 3.5 跟进记录 followup

- 当前模型：`types/followup.ts` 的 `FollowupRecord`。
- 语义：客户推进动作与事实沉淀，是 AI 分析、任务自动完成、风险判断的核心输入。
- 现状：稳定。

### 3.6 工作项 work item

- 当前模型：`types/work.ts` 的 `WorkItem`。
- 语义：可执行动作单元，连接“风险/决策/介入”与“执行闭环”。
- 现状：稳定。

### 3.7 风险预警 / 事件 alert / business event

- 当前模型：
  - 预警：`types/alert.ts` 的 `AlertItem`。
  - 经营事件：`types/automation.ts` 的 `BusinessEvent`。
- 语义差异：
  - `Alert` 更偏销售 owner 执行风险。
  - `BusinessEvent` 更偏管理视角运营事件总线。
- 现状：双轨并存，已可用但语义边界仍在演进。

### 3.8 管理介入 intervention / manager action

- 当前模型：
  - `InterventionRequest`（`types/deal.ts`）+ `intervention-request-service.ts`。
  - `DecisionRecord` 批准后联动工作项与 checkpoint（`decision-record-service.ts`）。
  - `deal_room.managerAttentionNeeded`、`business_event.manager_attention_escalated`。
- 结论：能力已落地，但对象分散在 Deal、Event、WorkItem 三侧，属于 `evolving`。

### 3.9 结果 / 复盘 outcome / review

- 当前模型：
  - 行动结果：`types/outcome.ts` 的 `ActionOutcome`。
  - 建议采纳：`SuggestionAdoption`。
  - 复盘对象：`OutcomeReview`。
- 语义：把执行动作回写为结果，并支持复盘与教练化建议。
- 现状：稳定。

### 3.10 商业化入口对象 inbound lead / demo request

- 当前模型：`types/commercialization.ts` 的 `InboundLead`、`DemoRequest`、`TrialRequest`、`TrialConversionTrack`。
- 语义：公域获客到试用/转化链路对象。
- 现状：稳定。

### 3.11 AI 治理对象 ai run / prompt version / fallback result

- 当前模型：
  - `AiRun`、`AiPromptVersion`（`types/ai.ts`）。
  - 服务：`services/ai-run-service.ts`、`services/ai-prompt-service.ts`。
- 核心语义：场景化调用、可审计、可追踪 provider/fallback 来源、失败可回退。
- 现状：稳定。

---

## 4. 核心状态

跨原语的关键状态统一如下：

- 阶段 `stage`：
  - 客户：`CustomerStage`（lead -> ... -> won/lost）
  - 商机：`OpportunityStage`
  - DealRoom：`DealRoomStatus`
  - 转化：`TrialConversionStage`
- 下一步 `next_step`：
  - 跟进：`Followup.nextPlan/nextFollowupAt`
  - 工作项：`WorkItem.title/description/dueAt/scheduledFor`
  - 结果回写：`ActionOutcome.nextStepText/followupDueAt`
- 阻塞点 `blocker`：
  - Deal checkpoint `blocked`
  - Deal room `currentBlockers`
  - 事件 `deal_blocked`
- 风险等级 `risk`：
  - 客户/商机：`low|medium|high`
  - Alert/Event：`info|warning|critical`
- 优先级 `priority`：
  - WorkItem：`priorityScore + priorityBand`
  - Deal：`priorityBand`
  - Intervention：`priorityBand`
- 负责人 `owner`：
  - customer/opportunity/followup/work item/lead/demo/trial track 均有 owner 语义
- 管理关注状态 `manager_attention`：
  - `deal_room.manager_attention_needed`
  - `business_event.manager_attention_escalated`
  - `intervention_requests.status`

---

## 5. 核心动作

MOY 底座动作链路可抽象为：

- 录入：写入 `communication_inputs` / `followups` / `inbound_leads`。
- 提取：AI 场景 `communication_extraction` 从输入提结构化信息。
- 分析：`followup_analysis` / `customer_health` / `leak_risk_inference` 等场景分析。
- 生成建议：输出 `next_best_actions`、`suggested_action`、brief/review。
- 触发预警：规则 + AI 生成 alert，事件总线生成 business event。
- 创建任务：alert/decision/intervention 联动 `work_items`。
- 升级给经理：`manager_attention` 标志、`intervention_request`、executive events。
- 结果回写：`action_outcomes` 更新客户阶段、风险、下一步。
- 形成管理可见性：executive cockpit / manager views / growth summary。

---

## 6. 原语之间的关系图（文字结构）

- `InboundLead` -> `DemoRequest` / `TrialRequest` -> `TrialConversionTrack` -> `Customer + Opportunity + DealRoom`
- `CommunicationInput` -> `Followup` -> `AiRun(followup_analysis)` -> `Customer risk/summary update`
- `Customer + Followup + Opportunity` -> `AlertRuleHit` -> `Alert`
- `Alert` -> `WorkItem(resolve_alert)` -> `ActionOutcome`
- `DealRoom` -> `Checkpoint/Decision/Intervention/Thread` -> `WorkItem` + `Alert` + `BusinessEvent`
- `CustomerHealthSnapshot + RenewalWatch` -> `BusinessEvent` -> `ExecutiveBrief`
- `ActionOutcome + SuggestionAdoption` -> `OutcomeReview` -> `Playbook/Coaching`
- 全链路 AI 调用统一落 `AiRun`，提示词版本由 `AiPromptVersion` 决定，失败进入 fallback

---

## 7. 哪些原语属于底座，哪些适合模板化扩展

### 7.1 底座原语（必须稳定）

- customer / opportunity / communication input / followup
- work item / alert / business event
- action outcome / outcome review
- inbound lead / demo request / trial track
- ai run / prompt version / fallback 标记

### 7.2 模板化扩展原语（可变参数层）

- 阶段提示文案、行业术语、风险证据表达
- 推荐动作优先级、模板化节奏阈值
- briefing/review 的展现结构与强调点
- 行业化导入字段映射策略

### 7.3 当前已是共性原语

- customer、opportunity、followup、work item、action outcome、ai run、inbound lead/demo request

### 7.4 当前仍是模块能力，尚未沉淀为统一原语

- contact/decision actor（客户侧关键人模型）
- manager action（介入、决策、关注信号仍分散）
- alert 与 business event 的统一事件语义层

---

## 8. 哪些字段或语义必须稳定，不允许行业模板或企业定制破坏

以下字段/语义必须视为“底座契约”：

- 主键与租户边界：`id`、`orgId`。
- 关联键：`customerId`、`opportunityId`、`ownerId`、`workItemId`、`followupId`、`sourceInputId`。
- 时间与审计：`createdAt`、`updatedAt`、`startedAt`、`completedAt`。
- 核心状态枚举：
  - customer/opportunity/deal/outcome/work/lead/demo/trial/event/ai_run 状态。
- 风险与优先级语义：
  - `riskLevel`、`severity`、`priorityBand`、`priorityScore`。
- AI 治理语义：
  - `scenario`、`promptVersion`、`resultSource(provider|fallback)`、`fallbackReason`。

禁止行为：

- 用模板改写核心状态枚举含义。
- 用企业定制绕过 `org` 隔离与 owner 责任边界。
- 把 fallback 结果混淆成 provider 结果。

---

## 9. 当前仓库对象命名、边界、语义歧义

1. 角色语义压缩  
`types/auth.ts` 仅 `sales|manager`，但 `org_memberships` 真实角色有 owner/admin/manager/sales/viewer，前后端角色语义不对齐。

2. contact 与 decision actor 未独立  
客户关键人仅部分存在于客户字段和 AI 输出中，缺少稳定实体与生命周期。

3. alert 与 business event 双轨  
两者都表达风险/事件，但状态流与使用主体不同，跨模块易出现“同一风险双语义”。

4. manager action 分散  
manager 介入同时出现在 `intervention_requests`、`decision_records`、`deal_room.manager_attention_needed`、`business_events`，缺统一抽象。

5. Demo 语义串联略紧  
`createDemoRequest` 创建 `demo_status=pending` 时可推进 lead 到 `demo_scheduled`，语义上“申请”和“排期”边界不够清晰。

6. work 类型和业务口径有混名风险  
例如 `review_customer`、`manager_checkin`、`schedule_demo` 同属 work item，但对应业务阶段含义不完全同层。

7. 字段命名风格跨层不统一  
类型层 camelCase 与数据库 snake_case 正常，但部分对象存在历史别名与双语义（如 `name`/`customerName`、`summary` 多域复用）。

---

## 10. 建议后续补做的类型定义、注释、命名统一项

1. 新增 `types/primitives.ts`（仅类型聚合，不改业务逻辑）  
统一导出底座原语别名和跨对象公共状态字段，降低跨模块理解成本。

2. 补 `types/auth.ts` 角色注释与映射注释  
明确“前端展示角色”与“组织权限角色”的映射关系与限制。

3. 为 `types/communication.ts` 与 `types/followup.ts` 增加链路注释  
明确 `sourceInputId` 是原始沟通事实与跟进记录的主链接。

4. 在 `types/alert.ts` 与 `types/automation.ts` 补边界注释  
明确 alert（执行风险）与 business event（管理事件）的职责边界。

5. 为 `types/deal.ts` 中 intervention/decision/checkpoint 增加“管理动作语义”注释  
减少“对象存在但语义分散”的理解成本。

6. 增加最小命名对齐文档段落  
统一 `next_step / nextPlan`、`risk_level / severity`、`owner / assignee` 的用词字典。

---

## 11. 给产品/设计/工程共同使用的实施建议

### 产品

- 新需求先映射到原语，再决定落底座、模板、配置还是拒绝。
- PRD 里必须写“涉及原语”与“不允许改变的语义”。

### 设计

- 页面稿件以原语状态和动作为中心，不以页面模块孤立定义语义。
- 风险、优先级、管理关注状态使用统一标签体系。

### 工程

- 新功能先复用现有 type/service，不新增平行对象。
- 所有 AI 新输出必须先对齐原语字段，再扩 schema。
- 变更必须附最小测试，至少覆盖状态流和对象关系不被破坏。

---

## 当前实现映射表

| 原语名称 | 当前对应类型 / 服务 / 测试文件 | 当前成熟度 |
|---|---|---|
| customer | `types/customer.ts` / `services/customer-service.ts` / `tests/golden-path-smoke.test.ts` | stable |
| contact / decision actor | `Customer.contactName`、`Customer.hasDecisionMaker`、`types/ai.ts`(`decision_makers`) | fragmented |
| opportunity | `types/opportunity.ts` / `services/customer-service.ts`(关联) / `tests/golden-path-smoke.test.ts` | stable |
| communication input | `types/communication.ts` / capture 与 followup 链路 (`sourceInputId`) / `tests/run-tests.ts`(extraction schema) | evolving |
| followup | `types/followup.ts` / `services/followup-service.ts` / `services/ai-analysis-service.ts` | stable |
| work item | `types/work.ts` / `services/work-item-service.ts` / `tests/golden-path-smoke.test.ts` | stable |
| alert | `types/alert.ts` / `services/alert-service.ts` + `services/alert-workflow-service.ts` | stable |
| business event | `types/automation.ts` / `services/business-event-service.ts` / `services/executive-cockpit-service.ts` | evolving |
| intervention / manager action | `types/deal.ts` / `services/intervention-request-service.ts` + `services/decision-record-service.ts` | evolving |
| outcome / review | `types/outcome.ts` / `services/action-outcome-service.ts` + `services/outcome-review-service.ts` | stable |
| inbound lead / demo request | `types/commercialization.ts` / `services/inbound-lead-service.ts` + `services/demo-request-service.ts` + `app/api/public/request-demo/route.ts` | stable |
| ai run / prompt / fallback | `types/ai.ts` / `services/ai-run-service.ts` + `services/ai-prompt-service.ts` + 各场景服务 | stable |
| deal collaboration primitives | `types/deal.ts` / `services/deal-room-service.ts` + `services/deal-command-service.ts` + `services/deal-checkpoint-service.ts` + `services/collaboration-thread-service.ts` | evolving |
| memory primitives | `types/memory.ts` / `services/memory-compile-service.ts` + `services/user-memory-service.ts` | evolving |
| growth primitives | `types/commercialization.ts` / `services/growth-pipeline-service.ts` + `services/lead-assignment-service.ts` | evolving |

---

## 需求分流建议表

| 需求类型 | 建议分流 | 判定规则 | 示例 |
|---|---|---|---|
| 新增核心对象关系 | 底座 | 影响 customer/opportunity/followup/work/alert/event/outcome 主关系 | 新增“关键人实体”并连接 followup 与 decision |
| 行业话术与推荐差异 | 模板 | 仅改内容、阈值、策略，不改状态机 | 制造业版风险阈值与话术包 |
| 企业权限与阈值偏好 | 企业配置 | 在现有对象语义内调整开关/配额/规则 | 某企业关闭自动生成 morning brief |
| 要求绕过 org/权限边界 | 拒绝 | 破坏底座安全与租户隔离 | 跨组织读取全部客户 |
| 要求改造为通用聊天 | 拒绝 | 偏离销售推进系统定位 | 自由问答机器人入口替代业务流 |
| 新增管理动作看板 | 底座或模板 | 若新增对象关系走底座；仅展示聚合走模板 | Manager intervention board |
| 新增企业专属报表维度 | 企业配置 | 不改核心指标语义，只加筛选与维度 | 按区域经理查看转化漏斗 |
| AI 输出扩展字段 | 底座先审后模板 | 必须先映射已有原语字段，再扩 schema | 新增 `decision_actor_confidence` |

