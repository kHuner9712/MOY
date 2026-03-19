# MOY 第九阶段交付说明（Collaboration & Deal Command Layer）

更新时间：2026-03-15  
适用代码目录：`C:\Users\15864\Desktop\MOY`

---

## 1. 阶段目标

本阶段将 MOY 从“个人工作台 + 结果闭环”升级为“关键商机团队协同中枢”，重点落地：

1. Deal Room 商机作战室
2. 协同线程与消息（内部协作）
3. 决策流（报价/折扣/试用/介入等）
4. manager 介入请求机制
5. checkpoint 推进节点与 blocked 联动
6. prep / outcome / playbook / task 在 deal 维度聚合
7. AI 生成 + fallback 的 command/thread/decision/intervention 支撑

---

## 2. 目录变化（本阶段）

### 2.1 新增（核心）

- `supabase/migrations/202603170001_deal_command_layer.sql`
- `types/deal.ts`
- `services/deal-room-service.ts`
- `services/collaboration-thread-service.ts`
- `services/decision-record-service.ts`
- `services/intervention-request-service.ts`
- `services/deal-checkpoint-service.ts`
- `services/deal-command-service.ts`
- `services/deal-room-client-service.ts`
- `hooks/use-deals.ts`
- `hooks/use-deal-room.ts`
- `app/(app)/deals/page.tsx`
- `app/(app)/deals/[id]/page.tsx`
- `app/api/deals/route.ts`
- `app/api/deals/create/route.ts`
- `app/api/deals/[id]/route.ts`
- `app/api/deals/[id]/threads/route.ts`
- `app/api/deals/[id]/messages/route.ts`
- `app/api/deals/[id]/decision-records/route.ts`
- `app/api/deals/[id]/interventions/route.ts`
- `app/api/deals/[id]/checkpoints/route.ts`
- `app/api/deals/[id]/command-refresh/route.ts`
- `app/api/deals/[id]/thread-summary/route.ts`
- `lib/deal-command-fallback.ts`
- `lib/deal-decision-linkage.ts`
- `lib/intervention-request-flow.ts`
- `lib/deal-checkpoint-linkage.ts`

### 2.2 改造（关键）

- `types/database.ts`
- `services/mappers.ts`
- `services/ai-prompt-service.ts`
- `app/(app)/customers/[id]/page.tsx`
- `app/(app)/today/page.tsx`
- `app/(app)/manager/rhythm/page.tsx`
- `app/(app)/dashboard/page.tsx`
- `app/(app)/manager/page.tsx`
- `app/(app)/opportunities/page.tsx`
- `components/opportunities/opportunity-table.tsx`
- `components/layout/app-sidebar.tsx`
- `lib/auth.ts`
- `tests/run-tests.mts`
- `README.md`

---

## 3. 数据库与 RLS 交付

### 3.1 Migration

已新增：

- `202603170001_deal_command_layer.sql`

覆盖内容：

1. 新增 enum（deal room / thread / decision / participant / checkpoint / intervention）
2. 新增表：
   - `deal_rooms`
   - `collaboration_threads`
   - `collaboration_messages`
   - `decision_records`
   - `deal_participants`
   - `deal_checkpoints`
   - `intervention_requests`
3. 高频索引（`org_id`、`customer_id`、`opportunity_id`、`deal_room_id`、`owner_id`、`status`、`priority_band`、`due_at`、`created_at`）
4. RLS + policy
   - sales：仅可访问自己参与/负责 deal 及其线程、消息、checkpoint、介入请求
   - manager：组织范围可见并可执行管理动作
5. AI 场景 prompt version seed：
   - `deal_room_command_summary`
   - `thread_summary`
   - `decision_support`
   - `intervention_recommendation`
   - `deal_playbook_mapping`

---

## 4. 服务层与 API 交付

### 4.1 服务层职责

1. `deal-room-service.ts`：deal room 创建、列表、详情、command 更新
2. `collaboration-thread-service.ts`：线程/消息 CRUD、system event、线程摘要更新
3. `decision-record-service.ts`：决策记录与批准联动（任务 + checkpoint）
4. `intervention-request-service.ts`：介入请求与状态流转、manager checkin 任务衔接
5. `deal-checkpoint-service.ts`：checkpoint 状态变更、blocked 升级联动
6. `deal-command-service.ts`：AI 编排（command/thread/decision/intervention/playbook mapping）

### 4.2 API 清单

- `POST /api/deals/create`
- `GET /api/deals`
- `GET /api/deals/[id]`
- `POST /api/deals/[id]/threads`
- `POST /api/deals/[id]/messages`
- `POST /api/deals/[id]/decision-records`
- `POST /api/deals/[id]/interventions`
- `POST /api/deals/[id]/checkpoints`
- `POST /api/deals/[id]/command-refresh`
- `POST /api/deals/[id]/thread-summary`

统一返回结构：`{ success, data, error }`，并执行角色权限校验。

---

## 5. 页面交付

### 5.1 新页面

1. `/deals`
   - 状态筛选
   - 优先级筛选
   - owner 筛选（manager）
   - manager_attention 筛选
   - 创建/复用 deal room

2. `/deals/[id]`
   - command summary
   - participants
   - checkpoints
   - decision records
   - intervention requests
   - threads/messages
   - related task/prep/outcome/playbook/alert 聚合信号

### 5.2 现有页面增强

1. `/customers/[id]`：显示 deal room 状态摘要 + 一键进入作战室
2. `/today`：任务关联关键 deal 时可直接打开作战室上下文
3. `/manager/rhythm`：新增 escalated/blocked deal 指挥信号
4. `/dashboard`、`/manager`：增加 Deal Command 入口

---

## 6. 关键业务联动（已实现）

1. **decision approved 联动**
   - 自动创建执行型 `work_item`
   - 自动创建/更新 `deal_checkpoint`
   - 自动写入 `collaboration_messages` system event

2. **checkpoint blocked 联动**
   - `deal_room.room_status -> blocked`
   - `manager_attention_needed -> true`
   - 触发关键 `alert`（critical, ai_detected）
   - 自动写入 thread system event

3. **intervention 状态流转**
   - 增加状态流转校验：非法流转直接拒绝
   - `accepted` 时创建 `manager_checkin` 类型任务承接

---

## 7. AI 与 Fallback（第九阶段）

### 7.1 AI 场景

1. `deal_room_command_summary`
2. `thread_summary`
3. `decision_support`
4. `intervention_recommendation`
5. `deal_playbook_mapping`

### 7.2 Fallback 策略

1. command summary 失败：规则化生成 room summary
2. thread summary 失败：最近消息规则摘要
3. decision support 失败：备选方案+风险+补信息建议
4. intervention recommendation 失败：介入时机与动作建议兜底
5. playbook mapping 失败：基于现有 playbook 规则映射兜底

均写入 `ai_runs`，并标记 `result_source=fallback` 与 `fallback_reason`。

---

## 8. 测试补充（本阶段）

`tests/run-tests.mts` 已新增并通过：

1. deal room fallback summary
2. thread summary fallback
3. decision support fallback
4. intervention recommendation fallback
5. deal playbook mapping result
6. decision -> work_item/checkpoint linkage mapping
7. intervention request status flow
8. checkpoint blocked linkage

---

## 9. 本轮报错与处理记录

### 9.1 构建报错（TypeScript）

1. `app/(app)/deals/[id]/page.tsx` 缺少 `threadType` 状态  
处理：补充 `threadType` 的 `useState` 与联合类型。

2. 多处 service 文件存在 `implicit any`（`row/item`）  
处理：为 map 回调补全显式类型，修复 strict 模式下编译失败。

3. `deal-command-service.ts` 中 AI 输出对象数组字段可能 `undefined`  
处理：增加 normalize 层（`?? []` + 显式映射）确保返回类型稳定。

### 9.2 状态一致性增强

介入请求状态更新增加前置校验，防止非法状态流转导致脏数据。

---

## 10. 验证结果

已通过：

1. `npm run test`
2. `npm run lint`
3. `npm run build`

备注：测试执行时存在 Node 的 `MODULE_TYPELESS_PACKAGE_JSON` warning，不影响功能和构建结果。

---

## 11. 快速验收建议（第九阶段）

1. 执行 migration 至 `202603170001`
2. 登录 sales 进入 `/deals` 创建或复用 deal room
3. 在 `/deals/[id]` 新建 thread 并发消息，执行 thread summary
4. 创建并批准 decision，验证 work_item/checkpoint 自动联动
5. 创建 intervention request 并流转状态，验证 manager checkin 任务
6. 标记 checkpoint 为 blocked，验证 room + alert + system event 联动
7. 在 `/customers/[id]`、`/today`、`/manager/rhythm` 验证 deal 回挂入口与信号

