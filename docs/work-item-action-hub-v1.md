# MOY Work Item Action Hub v1

## 1. 定位

Work Item 是 MOY 执行系统的统一动作对象，用于把以下动作生产链路收敛到同一可执行层：

- AI 风险/建议输出（followup 分析、leak 推断）
- 自动化规则命中后的动作生成（business event -> action）
- 经理干预动作（intervention request、manager desk intervention）
- 前线跟进行为驱动的待办（followup_due、draft_confirmation）

目标不是新增并行系统，而是让动作都能回到一个可追踪、可去重、可审计的执行中心。

## 2. 收敛规则

### 2.1 Work Item 优先落点

- `runFollowupAnalysis` 内触发的 leak alert，会尝试自动链接/创建 Work Item（通过 `createWorkItemFromAlert` 去重）。
- automation rule 对 business event 的动作创建，统一走 `createOrReuseWorkItemBySourceRef`。
- intervention request 在 `accepted` 时，统一走 `createOrReuseWorkItemBySourceRef`。
- manager desk intervention 创建任务，统一走 `createOrReuseWorkItemBySourceRef`。

### 2.2 去重口径（关键）

统一按以下条件复用任务，避免关键链路重复开单：

- `source_ref_type` 相同
- `source_ref_id` 相同
- 已存在任务状态属于活跃态：`todo | in_progress | snoozed`

已完成/已取消任务不参与复用。

## 3. Trace 模型（WorkItem.traceContext）

`WorkItem.traceContext` 为运行时补充字段（不破坏现有表结构）：

- `sourceType`
- `sourceRefType`
- `sourceRefId`
- `triggerOrigin`：`ai | rule | manager | manual | system`
- `triggerEntityType`
- `triggerEntityId`
- `linkedCustomerId`
- `linkedOpportunityId`
- `linkedDealRoomId`
- `linkedBusinessEventId`
- `linkedAlertId`
- `linkedInterventionRequestId`

说明：

- `business_event` 来源会回填触发实体（如 customer / deal_room）和关联 ID。
- `intervention_request` 来源会回填 `deal_room_id`（如有）。
- `alert` 来源会回填客户/商机关联。

## 4. 责任边界

- Work Item 负责动作执行与状态流转，不替代业务事件、告警或干预请求本体。
- Business Event 负责“信号/风险检测”。
- Alert 负责“风险告警对象”。
- Intervention Request 负责“管理介入请求流程”。
- Work Item 负责把这些信号请求转化为明确动作并承接执行结果。

## 5. 商业化与治理价值

- 降低“有分析无执行”的落空率。
- 减少重复任务和冲突任务，提升执行效率。
- 强化可审计性：从事件/告警/干预可追溯到具体执行任务，再到结果回传。
- 为后续 SLA、责任归属、ROI 归因打基础。
