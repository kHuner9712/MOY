# MOY 商业化入口系统蓝图 v1

## 1. 文档目标

本文件定义 MOY 公共商业化入口层（`/request-demo` 与 `/start-trial`）的系统化约束，确保其不是“表单收集器”，而是可追踪、可交接、可运营的商业化系统入口。

适用代码范围：

- 页面：`app/request-demo/page.tsx`、`app/start-trial/page.tsx`
- 公共 API：`app/api/public/request-demo/route.ts`、`app/api/public/start-trial/route.ts`
- 商业化服务：`services/inbound-lead-service.ts`、`services/demo-request-service.ts`、`services/trial-request-service.ts`
- 入口模型 helper：`lib/commercial-entry.ts`

## 2. MOY 公共入口“是什么”

MOY 公共入口层是商业化系统的控制点，负责把外部意向输入转换为内部可执行对象，并触发可见的下游执行链路：

1. 线索记录（`inbound_leads`）
2. 资格评估与分配（qualification + assignment）
3. 管道交接（客户/商机/Deal Room 草稿）
4. 下游请求对象（`demo_requests` 或 `trial_requests`）
5. 事件留痕（`conversion_events`）
6. 执行动作（`work_items`）

## 3. MOY 公共入口“不是什么”

- 不是只返回“提交成功”的黑箱接口
- 不是营销页面与内部系统割裂的孤岛流程
- 不是无来源、无上下文、无责任人的线索池

## 4. 核心对象与追踪契约

### 4.1 Public Entry Trace（来源追踪）

入口上下文由 `lib/commercial-entry.ts` 统一构建，落入 `inbound_leads.payload_snapshot.entry_trace`：

- `trace_id`
- `lead_source`
- `source_campaign`
- `landing_page`
- `referrer`
- `utm_*`
- `locale` / `timezone`
- `submitted_at`

该 trace id 会继续进入 `conversion_events` 关键节点 payload，用于运营排障与链路追踪。

### 4.2 Qualification Snapshot（资格元数据）

`createInboundLead` 会写入完整资格快照：

- 资格评估结论与评分
- 风险标记与建议动作
- AI fallback 使用情况
- 负责人分配结果与命中规则

### 4.3 Pipeline Handoff（交接状态）

`payload_snapshot.pipeline_handoff` 统一描述 lead 到内部销售管道的状态：

- `requested` / `attempted`
- `status`（`requested_pending` / `created_new_pipeline` / `reused_existing_pipeline` / `skipped_unqualified` 等）
- `customer_id` / `opportunity_id` / `deal_room_id`
- `evaluated_at`

该字段使“是否完成交接”从隐式行为变为显式系统状态。

### 4.4 Trial Onboarding Intent（试用启动意图）

`/start-trial` 在入口阶段构建并落库：

- `need_import_data`
- `preferred_template_key`
- `use_case_hint`
- `onboarding_motion`
- `kickoff_priority`
- `expected_first_outcome`

该意图贯穿 `trial_requested`、`trial_approved`、`trial_activated` 事件与 onboarding run detail，避免试用激活与后续执行脱节。

## 5. 主链路定义

### 5.1 Request Demo 链路

`request-demo page` -> `POST /api/public/request-demo` -> `createInboundLead` -> `createDemoRequest` -> `createWorkItem` -> `conversion_events`

系统保证：

- 入口来源可追踪（trace）
- 资格与分配结果可解释
- demo 请求与后续工作项可关联 lead/source

### 5.2 Start Trial 链路

`start-trial page` -> `POST /api/public/start-trial` -> `createInboundLead` -> `createTrialRequest` -> （后续）`activateTrialRequest`

系统保证：

- 入口来源 + onboarding intent 同步进入 lead snapshot
- trial 请求事件包含启动意图与来源 trace
- trial 激活时 onboarding run/detail 与 conversion event 持续携带关键上下文

## 6. API 响应中的可运营信息

公共 API 除保留 `accepted` 与对象 ID 外，新增系统化返回：

- `entryTraceId`
- `handoff`（lead/pipeline/downstream 状态）
- `onboardingIntent`（trial 场景）

作用：让调用方、运营团队与后续自动化可以直接消费“交接状态”，而不是二次推断。

## 7. 运营可观测性最小闭环

最小可观测闭环由三层组成：

1. `inbound_leads.payload_snapshot`：入口事实与资格/交接事实
2. `conversion_events.event_payload`：关键节点事件化
3. `work_items`：责任落地到执行对象

## 8. v1 边界

当前 v1 不做的事情：

- 不引入新导航或营销视觉改版
- 不重构 auth/permission 栈
- 不新增重型基础设施

在现有架构下，v1 已把公共入口从“提交动作”升级为“商业化系统入口状态机”。
