# MOY Phase 4 迭代说明（Capture + Reports）

## 1. 目录变化

- 新增页面
  - `app/(app)/capture/page.tsx`
  - `app/(app)/reports/page.tsx`
- 新增 API
  - `app/api/capture/extract/route.ts`
  - `app/api/capture/confirm/route.ts`
  - `app/api/reports/generate/route.ts`
  - `app/api/reports/route.ts`
  - `app/api/reports/[id]/route.ts`
- 新增服务
  - `services/communication-input-service.ts`
  - `services/communication-extraction-service.ts`
  - `services/customer-match-service.ts`
  - `services/report-generation-service.ts`
  - `services/report-client-service.ts`
- 新增类型与工具
  - `types/communication.ts`
  - `types/report.ts`
  - `lib/capture-flow.ts`
  - `lib/report-fallback.ts`

## 2. Migration 变更

- 新增：
  - `supabase/migrations/202603140004_capture_reports.sql`
- 主要内容：
  - 新表 `communication_inputs`
  - 新表 `generated_reports`
  - `followups` 新增 `source_input_id`、`draft_status`
  - 扩展 `ai_scenario`：
    - `communication_extraction`
    - `sales_daily_report`
    - `sales_weekly_report`
    - `manager_daily_report`
    - `manager_weekly_report`
  - 新增枚举与索引
  - 新表 RLS policy（sales/manager 范围控制）
  - 新增 prompt seed（抽取与报告场景）

## 3. 工作流落地

- Capture 流程：
  1. 写入 `communication_inputs`
  2. DeepSeek 抽取 + schema 校验
  3. 客户匹配
  4. 自动落库（`confirmed`）或待确认（`draft`）
  5. 写回 `communication_inputs.extraction_status` 与 `extracted_data`
  6. 记录 `ai_runs`

- Reports 流程：
  1. 聚合业务快照
  2. DeepSeek 生成结构化报告
  3. 落库 `generated_reports`
  4. 记录 `ai_runs`
  5. 失败时 fallback 到规则化报告

## 4. 关键代码改造

- `services/customer-service.ts`
  - 新增 `createQuickFromCapture`
  - 修复 Supabase Insert 类型推断问题
- `services/communication-extraction-service.ts`
  - 抽取服务串联客户匹配/草稿分流/联动分析
  - 分流逻辑统一复用 `lib/capture-flow.ts`
- `services/report-generation-service.ts`
  - 报告聚合 + AI 生成 + 审计
  - fallback 逻辑迁移为 `lib/report-fallback.ts` 以便测试
- `components/shared/app-data-provider.tsx`
  - 接入 `communicationInputs` / `reports` 状态与操作
- `app/(app)/dashboard/page.tsx`、`app/(app)/manager/page.tsx`
  - 升级为行动优先工作台

## 5. 测试补充

- `tests/run-tests.mts` 覆盖：
  - alert 规则
  - AI schema（含 communication extraction）
  - dedupe 逻辑
  - provider 输出解析
  - customer match
  - 自动落库/人工确认分流
  - report fallback

## 6. README 更新

- 已升级 `README.md`：
  - Capture 使用说明
  - communication_inputs 数据流
  - 自动落库与人工确认逻辑
  - 报告生成与 fallback 说明
  - 本地验证清单与排错建议

## 7. 本轮报错与处理

1. `next build` 报 `spawn EPERM`
   - 原因：受限执行环境进程权限
   - 处理：提权重跑 `npm run build`

2. `customer-service.ts` 中 Supabase Insert 推断为 `never`
   - 原因：当前 Supabase 复杂 select/relation 推断导致类型收窄
   - 处理：增加显式 `Insert` payload 类型与必要断言，保留业务类型安全

3. `report-generation-service.ts` implicit any
   - 原因：`map` 回调参数未显式类型
   - 处理：先强转数组再映射，消除 `any`

## 8. 最终验证结果

- `npm run test` 通过
- `npm run lint` 通过
- `npm run build` 通过
