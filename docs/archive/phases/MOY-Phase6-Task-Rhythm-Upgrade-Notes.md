# MOY Phase 6 交付说明（Task Rhythm）

## 1. 本阶段目标

将 MOY 从“会分析”升级为“会主动编排今天该做什么”：
- 销售端：新增今日任务代理 `/today`
- 管理端：新增团队执行节奏视图 `/manager/rhythm`
- 数据层：新增任务、计划、执行日志、代理运行审计
- AI 层：新增任务编排与节奏洞察场景 + fallback

---

## 2. 目录与文件变化（核心）

### 2.1 新增/升级数据结构
- `supabase/migrations/202603140006_work_rhythm.sql`
- `types/work.ts`
- `types/ai.ts`（新增 phase6 场景 schema）
- `types/database.ts`（新增表、枚举、字段映射）

### 2.2 新增规则与 fallback
- `lib/task-priority.ts`
- `lib/work-item-state.ts`
- `lib/daily-plan-fallback.ts`
- `lib/team-rhythm-fallback.ts`
- `lib/work-item-builder.ts`
- `lib/work-item-linkage.ts`

### 2.3 新增服务层
- `services/task-priority-service.ts`
- `services/work-item-service.ts`
- `services/work-plan-service.ts`
- `services/work-agent-service.ts`
- `services/task-action-service.ts`
- `services/team-rhythm-service.ts`
- `services/work-plan-client-service.ts`
- `services/work-item-client-service.ts`
- `services/team-rhythm-client-service.ts`

### 2.4 新增 API
- `app/api/today/generate-plan/route.ts`
- `app/api/today/plan/route.ts`
- `app/api/work-items/from-alert/route.ts`
- `app/api/work-items/[id]/start/route.ts`
- `app/api/work-items/[id]/complete/route.ts`
- `app/api/work-items/[id]/snooze/route.ts`
- `app/api/work-items/[id]/cancel/route.ts`
- `app/api/work-items/[id]/action-suggestion/route.ts`
- `app/api/manager/rhythm/route.ts`
- `app/api/manager/rhythm/generate/route.ts`

### 2.5 新增页面与前端 Hook
- `app/(app)/today/page.tsx`
- `app/(app)/manager/rhythm/page.tsx`
- `hooks/use-today-plan.ts`
- `hooks/use-manager-rhythm.ts`

### 2.6 现有页面增强
- `app/(app)/dashboard/page.tsx`
- `app/(app)/manager/page.tsx`
- `app/(app)/alerts/page.tsx`
- `components/alerts/alert-list.tsx`
- `app/(app)/customers/[id]/page.tsx`
- `app/(app)/followups/new/*`（保留并联动任务能力）
- `components/layout/app-sidebar.tsx`
- `lib/auth.ts`（路由菜单含 `/today`、`/manager/rhythm`）

---

## 3. Migration 要点

`202603140006_work_rhythm.sql` 包含：

1. `ai_scenario` 新增值
- `daily_work_plan_generation`
- `task_action_suggestion`
- `manager_team_rhythm_insight`
- `weekly_task_review`

2. 新增枚举
- `work_item_source_type`
- `work_item_type`
- `work_priority_band`
- `work_item_status`
- `daily_plan_status`
- `plan_time_block`
- `task_action_type`
- `work_agent_run_scope`
- `work_agent_run_status`

3. 新增表
- `work_items`
- `daily_work_plans`
- `daily_work_plan_items`
- `task_execution_logs`
- `work_agent_runs`

4. 约束与索引
- 覆盖 `org_id/owner_id/status/due_at/scheduled_for/plan_date/priority_band/created_at` 等高频查询

5. RLS
- sales：仅本人任务与计划
- manager：可看组织范围任务/计划/运行审计

6. prompt seed
- phase6 4 个场景 prompt 默认写入 `ai_prompt_versions`

---

## 4. 任务编排与状态流转

### 4.1 今日计划生成
1. 规则层生成候选任务（到期跟进、高风险提醒、草稿确认、停滞商机等）
2. 优先级评分（可解释）
3. AI 编排（focus、must-do、时间块、顺序）
4. 落库：
- `work_items`
- `daily_work_plans`
- `daily_work_plan_items`
- `work_agent_runs`

### 4.2 状态流转
- `todo -> in_progress`
- `in_progress -> done`
- `todo/in_progress -> snoozed`
- `snoozed -> todo`
- `todo/in_progress -> cancelled`

所有关键动作写入 `task_execution_logs`。

### 4.3 关键联动
- `alert -> work_item` 转换与去重
- 解决 alert 自动完成关联任务
- `work_item -> followup` 一键转化
- followup 完成后自动联动任务完成
- capture 草稿确认后自动完成 `draft_confirmation` 任务

---

## 5. Fallback 设计

1. daily plan fallback  
DeepSeek 失败时，规则优先级仍可生成基础今日计划并落库。

2. task action suggestion fallback  
返回规则模板：`why_now / suggested_action / risk_if_delayed`。

3. manager rhythm fallback  
至少返回：
- 超期任务数
- 高风险未承接客户
- 任务堆积销售名单
- 管理动作建议

---

## 6. 本次接入中遇到的报错与处理

### 6.1 `build` 类型错误：`Property ... does not exist on type never`
- 场景：Route Handler 查询返回类型推断失败（如 `source_ref_id`、`owner_id`）
- 处理：对查询结果加显式行类型/局部类型断言，避免 `never`

### 6.2 `jsonb` 字段类型不匹配（`Record<string, unknown>` -> `Json`）
- 场景：`work_agent_runs`、`task_execution_logs` 快照写入
- 处理：
  - 服务层统一引入 `Json` 类型
  - 对快照结果进行 `Json` 兼容转换（必要时 `unknown as Json`）

### 6.3 `implicit any` 报错
- 场景：`.map((item) => ...)` 在严格 TS 下触发
- 处理：对 map 回调参数补充 DB Row 类型标注

### 6.4 `/today` 页面类型错误（`planView | null`）
- 场景：`typeof planView["workItems"][number]` 触发空值联合问题
- 处理：改为显式 `WorkItem` 类型

### 6.5 构建过程发现服务文案乱码
- 场景：`work-agent-service` 部分字符串编码异常
- 处理：统一替换为稳定英文文案，避免 UI 乱码及匹配误差

---

## 7. 验证结果

本地验证已通过：
- `npm run lint`
- `npm run test`
- `npm run build`

并且新增路由成功构建：
- `/today`
- `/manager/rhythm`
- 对应 API 路由全部进入 build 路由清单

