# MOY 第十一阶段交付说明（Organization & Productization Layer）

## 1. 本阶段目标
- 组织设置中心（Org Admin Center）
- 成员/席位/角色与邀请骨架
- AI 控制中心（组织级开关 + fallback 策略）
- 用量统计与配额（Usage/Quota）
- onboarding/demo/trial 可运行骨架

## 2. 目录变化
- 新增：
  - `app/(app)/settings/org/page.tsx`
  - `app/(app)/settings/team/page.tsx`
  - `app/(app)/settings/ai/page.tsx`
  - `app/(app)/settings/usage/page.tsx`
  - `app/(app)/settings/onboarding/page.tsx`
  - `app/api/settings/org/route.ts`
  - `app/api/settings/team/route.ts`
  - `app/api/settings/team/invite/route.ts`
  - `app/api/settings/team/update-role/route.ts`
  - `app/api/settings/team/update-seat-status/route.ts`
  - `app/api/settings/ai/route.ts`
  - `app/api/settings/usage/route.ts`
  - `app/api/settings/onboarding/route.ts`
  - `app/api/settings/onboarding/run/route.ts`
  - `app/api/settings/demo-seed/run/route.ts`
  - `app/api/settings/summary/route.ts`
  - `services/org-settings-service.ts`
  - `services/org-membership-service.ts`
  - `services/org-feature-service.ts`
  - `services/org-ai-settings-service.ts`
  - `services/usage-metering-service.ts`
  - `services/plan-entitlement-service.ts`
  - `services/onboarding-service.ts`
  - `services/demo-seed-service.ts`
  - `services/feature-access-service.ts`
  - `services/settings-client-service.ts`
  - `lib/productization-fallback.ts`
  - `lib/demo-seed-summary.ts`
  - `hooks/use-org-productization-summary.ts`
- 变更：
  - `types/database.ts`
  - `types/ai.ts`
  - `types/productization.ts`
  - `services/mappers.ts`
  - `services/ai-prompt-service.ts`
  - `app/(app)/dashboard/page.tsx`
  - `app/api/today/generate-plan/route.ts`
  - `app/api/briefings/morning-generate/route.ts`
  - `app/api/ai/followup-analysis/route.ts`
  - `app/api/deals/[id]/command-refresh/route.ts`
  - `app/api/touchpoints/review/route.ts`
  - `.env.example`
  - `README.md`

## 3. Migration
- 新增 migration：
  - `supabase/migrations/202603190001_organization_productization_layer.sql`
- 主要内容：
  - 新增表：
    - `org_settings`
    - `org_feature_flags`
    - `org_ai_settings`
    - `org_memberships`
    - `org_invites`
    - `org_usage_counters`
    - `user_usage_counters`
    - `org_plan_profiles`
    - `onboarding_runs`
  - 新增 enum：
    - `org_member_role`, `org_seat_status`, `org_invite_status`
    - `org_feature_key`, `org_ai_fallback_mode`
    - `org_usage_scope`, `org_plan_tier`, `org_plan_status`
    - `onboarding_run_type`, `onboarding_run_status`
  - 扩展 `ai_scenario`：
    - `onboarding_recommendation`
    - `usage_health_summary`
  - RLS + 索引 + helper function

## 4. 核心实现说明
- 组织设置：
  - `/settings/org` 管理组织基础配置、默认阶段、提醒规则、onboarding 进度信息
- 团队与席位：
  - `/settings/team` 支持邀请、角色调整、seat 状态调整
  - seat 状态流转校验：`isSeatStatusTransitionAllowed`
- AI 控制中心：
  - `/settings/ai` 支持模型、fallback、自动能力开关、feature flags
- 用量与配额：
  - `/settings/usage` 显示组织/用户计数、plan 档位、额度状态
  - 配额逻辑由 `plan-entitlement-service.ts` 统一判断
- onboarding：
  - `/settings/onboarding` 显示 checklist、最近 runs、推荐下一步
  - 支持 run type：`first_time_setup` / `trial_bootstrap` / `demo_seed` / `reinitialize_demo`

## 5. 功能开关与配额回挂
- 关键业务 API 已接入组织开关与配额校验：
  - `/api/today/generate-plan`
  - `/api/briefings/morning-generate`
  - `/api/ai/followup-analysis`
  - `/api/deals/[id]/command-refresh`
  - `/api/touchpoints/review`
- 策略：
  - 功能关闭或配额不足时返回可解释信息
  - 业务流程尽量走 fallback，不做硬崩

## 6. fallback 与审计
- onboarding recommendation：
  - AI 失败时走规则 fallback，页面可继续使用
- usage summary：
  - AI 失败时走静态/规则化总结
- demo seed：
  - 支持部分成功，`onboarding_runs.detail_snapshot` 记录步骤结果
- 所有关键 AI 行为继续写入 `ai_runs`

## 7. 测试补充（Phase 11）
- 在 `tests/run-tests.mts` 增加：
  - feature gate 关闭/降级逻辑
  - entitlement/quota 判断
  - onboarding fallback 推荐
  - usage summary fallback
  - invite/role/seat 基础流转
  - demo seed partial success 汇总逻辑

## 8. 构建与验证
- `npm run lint`：通过
- `npm run test`：通过（含 Phase 11 新增测试）
- `npm run build`：通过

## 9. 本阶段已知取舍
- 邀请流程本阶段不接真实邮件通道，仅提供 invite 记录 + token link
- 不接真实支付，仅完成 plan/entitlement/quota 的产品化骨架
- onboarding 推荐默认走 AI + fallback，保证可用优先
