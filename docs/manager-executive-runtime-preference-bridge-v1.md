# MOY 管理可见性层 Runtime 偏好桥接 v1

## 1. 本次接入目标
在不改动底层统计口径、对象关系和状态机语义的前提下，让“行业模板 + 企业配置”第一次真实影响管理可见性层输出，重点落在：
- 默认关注重点
- 风险排序优先级
- 推荐动作优先级
- 报告与简报的附加上下文

本次只做偏好层 overlay，不做数据库迁移，不做复杂 UI，不改核心 schema。

## 2. 为什么 manager / executive / reports 是下一步高价值接入点
- 这三层直接面向“管理动作”，偏好变化能快速转化为日常管理行为变化。
- 这三层已经有稳定服务入口（`executive-cockpit-service`、`executive-brief-service`、`report-generation-service`、`manager-insight-service`），适合小步接入。
- 相比改底层指标定义，这里可以先验证“模板/企业配置是否改变管理关注点”，风险更低、验证更快。

## 3. 本次接入边界
可改（已实施）：
- 默认筛选与排序：同级风险事件的排序权重
- 重点指标强调：report/source snapshot 增加 runtime focus overlay
- 推荐动作优先级：管理动作列表与 executive 建议列表增加优先动作
- briefing 附加上下文：给 executive brief 增加 runtime 偏好提示上下文

不可改（明确不做）：
- 底层指标定义（如 `openEvents`、`criticalRisks`、`followupTimelinessScore` 计算公式）
- 底座状态机语义
- 权限语义与页面准入规则
- AI provider/fallback 治理链路和结果来源语义

## 4. 运行时消费优先级
统一保持：
1. `base metrics semantics`（最高优先级，不可覆盖）
2. `industry template preference`
3. `org customization preference`（仅覆盖偏好参数层）

本次实现中，偏好层统一通过 `services/template-org-runtime-bridge-service.ts` 的 helper 进入运行时，避免散落 hardcode。

## 5. 当前实际接入点

| 消费层 | 接入文件 | 本次生效点 | 是否改核心口径 |
| --- | --- | --- | --- |
| Executive cockpit | `services/executive-cockpit-service.ts` | `recentEvents` / 开放事件排序使用 runtime 权重；`recommendations` 注入优先动作 | 否 |
| Executive brief | `services/executive-brief-service.ts` | Prompt 增加 runtime augmentation；`suggestedActions` 注入优先动作；`source_snapshot` 写入 runtime overlay | 否 |
| Reports | `services/report-generation-service.ts` | 报告生成 payload/source snapshot 增加 runtime focus overlay；推荐动作优先级增强 | 否 |
| Manager quality insight | `services/manager-insight-service.ts` | Prompt 增加 runtime augmentation；`management_actions` 注入优先动作；summary 附加 focus metrics | 否 |
| Runtime adapter | `services/template-org-runtime-bridge-service.ts` | 新增 manager/executive/report 偏好 helper（context/build/sort/overlay/augmentation） | 否 |

## 6. 暂未接入点与原因
- `services/manager-desk-server-service.ts` 风险队列排序仍以本地分值为主  
原因：该服务存在较多历史业务分支，直接改排序逻辑风险较高；先通过统一 helper 铺桥，后续分步替换。

- `services/manager-insights-service.ts`（趋势聚合）未接入 runtime 偏好  
原因：当前输出以趋势聚合为主，适合后续在“展示层默认过滤与指标强调”接入，而非立即改聚合。

- 前端页面级默认筛选（`app/(app)/manager/*`, `app/(app)/reports/page.tsx`, `app/(app)/executive/page.tsx`）仅消费既有 API 返回，未新增 UI 控件  
原因：本次目标是运行时桥接，不做 UI 重构。

## 7. helper / adapter / mapping 职责
`services/template-org-runtime-bridge-service.ts` 新增职责：
- `buildManagerVisibilityRuntimeContext`：把模板+企业配置收敛为管理可见性偏好上下文
- `applyExecutiveEventPreference`：只在同级语义内调整风险排序优先级
- `applyManagerActionPreference`：给建议动作注入优先动作前缀（内容层增强）
- `applyReportFocusOverlay`：对 report source snapshot 注入 runtime focus overlay（不改指标值）
- `buildExecutiveBriefAugmentation`：生成 executive brief 的附加上下文

## 8. 管理可见性层已接入消费点表

| 点位 | 输入 | 输出影响 | 回退行为 |
| --- | --- | --- | --- |
| Executive summary 事件排序 | 模板 manager focus metrics + 报告偏好指标 + 风险模式 | 同级 severity 下排序变化 | 无模板/企业配置时保持原顺序 |
| Executive summary 推荐动作 | 模板 recommended action library + org overlay | 建议动作列表前置优先动作 | 无偏好时仅返回原建议 |
| Executive brief prompt | 模板 prompt hooks + manager visibility overlay | 简报生成更偏行业/组织关注点 | 无偏好时仅原 prompt |
| Report source snapshot overlay | reportType + runtime visibility context | source snapshot 增加 `runtime_preference_overlay` | 无偏好时 overlay 为 base/fallback 标记 |
| Manager quality actions | manager visibility context | `management_actions` 前置优先动作 | 无偏好时保持原动作列表 |

## 9. 管理可见性层后续扩展点表

| 候选点 | 建议方式 | 优先级 | 说明 |
| --- | --- | --- | --- |
| Manager desk 风险队列 | 复用 `applyExecutiveEventPreference` 思路，映射到 `ManagerRiskItem` | High | 直接影响经理日常排队顺序，价值高 |
| Manager insights 趋势页默认指标 | 在 API/页面层应用 `reportMetricPriority` 作为默认展示顺序 | Medium | 不改聚合，仅改默认展示 |
| Reports 页面默认 reportType/date range | 读取 `defaultDateRangeDays` 和模板默认模式 | Medium | 可提升开箱效率，改动小 |
| Executive 页面 focus chips | 只读展示当前 runtime focus metrics | Medium | 提升可解释性 |
| 规则中心/自动化中心可视化联动 | 把 runtime event weight 与规则命中结果联动展示 | Later | 需要更多 UI 与解释层设计 |

## 10. 风险与后续演进建议
- 风险 1：当前“指标名 -> 事件类型”映射为轻量映射，仍有语义漂移可能。  
建议：后续将映射抽到单独常量表并补覆盖测试。

- 风险 2：`Priority action: ...` 为内容增强策略，跨语言团队可能需要本地化。  
建议：后续由 i18n/文案层提供可配置前缀。

- 风险 3：当前尚未在前端显式展示“为什么这样排序/强调”。  
建议：后续增加只读 explain 字段（不改主 schema，先放 source snapshot）。

---

本次结论：
- 已真正生效：`executive`、`reports`、`manager quality` 三类管理可见性消费点。
- 已铺桥待接线：`manager desk`、`manager insights trends`、页面层可解释性展示。
