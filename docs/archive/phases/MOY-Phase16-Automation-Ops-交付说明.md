# MOY Phase16 交付说明（Automation Ops & Executive Cockpit）

> ⚠️ **历史阶段文档 (Historical Phase Document)**
>
> 本文档记录的是 **Phase 16** 的功能交付说明，是当前 v1.0 的核心组成部分。
>
> - 主要对应阶段：Phase 16
> - 当前对外交付版本：v1.0
> - 当前有效总览请参考：[README.md](../../README.md)、[MOY-Current-Architecture-and-Phase-Overview.md](../MOY-Current-Architecture-and-Phase-Overview.md)
>
> ---

## 1. 目标

Phase16 将 MOY 从“销售过程系统”升级到“经营自动化 + 管理驾驶舱”，重点交付：

- 自动化规则中心（可配置、可解释、可开关）
- 统一经营事件总线（business events）
- 客户健康度与续费观察骨架
- 经营层简报（executive briefs）
- `/executive` 驾驶舱与关键页面回挂

---

## 2. 核心数据库变更

Migration:

- `supabase/migrations/202603240001_automation_ops_executive_cockpit_layer.sql`

新增表：

- `automation_rules`
- `automation_rule_runs`
- `business_events`
- `customer_health_snapshots`
- `executive_briefs`
- `renewal_watch_items`

并包含：

- Phase16 新枚举类型
- 关键索引
- `updated_at` 触发器
- 全量 RLS policy
- 新 AI 场景枚举扩展

---

## 3. 页面与接口

### 新页面

- `app/(app)/settings/automation/page.tsx`
- `app/(app)/executive/page.tsx`

### 新 API

- `GET/POST /api/settings/automation`
- `POST /api/settings/automation/run`
- `GET /api/executive/summary`
- `GET /api/executive/events`
- `GET /api/executive/health`
- `GET /api/executive/briefs`
- `POST /api/executive/briefs/generate`
- `POST /api/executive/events/[id]/ack`
- `POST /api/executive/events/[id]/resolve`
- `POST /api/executive/events/[id]/ignore`

### 回挂 API

- `GET /api/customers/[id]/health`
- `GET /api/deals/[id]/ops-events`

---

## 4. 新增/改造服务层

- `services/automation-rule-service.ts`
- `services/business-event-service.ts`
- `services/customer-health-service.ts`
- `services/executive-brief-service.ts`
- `services/renewal-watch-service.ts`
- `services/executive-cockpit-service.ts`
- `services/executive-client-service.ts`

支持库：

- `lib/automation-ops.ts`
- `lib/renewal-watch.ts`
- `lib/automation-fallback.ts`

---

## 5. 回挂到现有业务层

已完成以下页面联动：

- `/today`（manager 视角经营事件提醒）
- `/briefings`（executive brief 摘要）
- `/customers/[id]`（health + retention signals）
- `/deals/[id]`（ops events + recommended actions）
- `/manager/outcomes`、`/manager/rhythm`、`/manager/conversion`（驾驶舱入口）

---

## 6. Fallback 与错误处理

### 已实现 fallback

- executive brief 失败 -> 规则化 brief
- health summary 失败 -> 规则化 health summary
- automation action recommendation 失败 -> 静态建议动作
- retention watch review 失败 -> 规则化 retention watch summary
- rule run 部分失败 -> 不阻断其他规则，按 rule run 审计记录

### 近期关键修复

- 修复自动化规则页面中分隔符乱码显示
- 修复 `automation-rule-service` 的干预动作类型不匹配问题（与既有枚举对齐）
- 抽离并复用 `renewal` 推断逻辑到 `lib/renewal-watch.ts` 以增强可测性

---

## 7. 测试与验证

新增测试：

- `tests/automation-ops-layer.test.ts`

覆盖：

- automation rule matching
- business event dedupe + status flow
- customer health fallback
- executive brief fallback
- rule -> action linkage
- renewal watch derivation
- health band mapping

验证命令：

```bash
npm run test
npm run lint
npm run build
```

当前结果：三项通过。

