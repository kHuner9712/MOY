# MOY Runtime Explain 只读调试面板 v1

更新时间：2026-03-21  
关联文档：
- `docs/org-runtime-config-read-path-v1.md`
- `docs/runtime-config-explain-and-override-hardening-v1.md`
- `docs/org-override-write-path-governance-v1.md`
- `docs/role-and-permission-model-v1.md`

## 1. 为什么需要只读 explain/debug 面板
- 当前 runtime 已经是“持久化优先 + fallback/seed 兜底”，不再是单一来源。
- 仅看设置页无法快速回答“当前生效值来自哪里、为何某些 override 被忽略”。
- 先做只读调试面板，可以降低排障成本，为后续配置编辑器提供稳定读模型。

## 2. 本次面板的目标用户
- 一线排障与验收用户：`owner/admin/manager`。
- 目标是让这些角色在不改配置的前提下，快速确认当前 runtime 解释与治理状态。

## 3. 本次面板展示范围
- 只读展示当前组织 runtime explain 聚合结果。
- 聚合当前关键消费点摘要（onboarding / automation seed / executive-report）。
- 展示“最近可读到的 override 写治理摘要状态”（若可读）。

## 4. 哪些信息会展示
- `resolvedTemplateKey / fallbackProfileKey / appliedOrgCustomizationKey / resolvedMode / sourcePriority`
- `keyFieldSources / persistedUsage`
- `appliedOverrides / ignoredOverrides / diagnostics`
- 生效偏好摘要（manager/report/onboarding/action）
- 消费点 explain 摘要（onboarding、automation seed、executive-report）
- 最近一次可读治理摘要状态（可用性、计数、说明）

### 面板展示信息清单
| 展示模块 | 字段 | 来源 | 当前状态 |
| --- | --- | --- | --- |
| Runtime Source Summary | `resolvedTemplateKey/fallbackProfileKey/appliedOrgCustomizationKey/resolvedMode/sourcePriority` | `template-org-runtime-bridge-service` explain | 已展示 |
| Key Field Sources | `keyFieldSources` | runtime explain snapshot | 已展示 |
| Persisted Usage | `assignment/overrides/orgSettings/orgAiSettings/orgFeatureFlags/automationRules` | runtime source snapshot | 已展示 |
| Override Diagnostics | `appliedOverrides/ignoredOverrides/diagnostics` | hardening + runtime bridge | 已展示 |
| Effective Preference Summary | manager/report/onboarding/action 关键偏好摘要 | merged runtime context | 已展示 |
| Onboarding Consumer | prompt augment 是否生效、checklist/hint 摘要 | onboarding 场景 runtime 逻辑 | 已展示 |
| Automation Seed Consumer | source/resolvedMode/ignoredCount/seed 样例 | automation seed runtime 逻辑 | 已展示 |
| Executive/Report Consumer | metric/action priority 与 fallback 状态 | manager visibility runtime 逻辑 | 已展示 |
| Governance Snapshot Status | latest run id/time、diagnostics/auditDraft 计数、说明 | `template_application_runs.result_snapshot.override_write_governance`（若存在） | 已展示（可空） |

## 5. 哪些信息暂不展示
- 不提供配置编辑入口，不支持写入。
- 不提供完整历史时间线审计查询（仅展示“最近可读治理摘要状态”）。
- 不做复杂图表与跨周期趋势分析。

### 暂不纳入面板的能力清单
| 能力 | 未纳入原因 | 后续建议 |
| --- | --- | --- |
| 配置编辑（template/org overrides） | 本次目标是只读调试，避免写路径与 UI 同步扩张 | 在治理/审计稳定后接入编辑器 |
| 全量历史审计检索 | 当前审计以 draft/evented 为主，未形成完整 persisted 历史查询 | 增加专用 audit 表与索引 |
| 一键回滚 | 尚未提供 versioned rollback 执行 API | 先补 version + rollback action service |
| 跨周期 explain 对比图 | 当前是当前态排障，不做复杂图表 | 后续按版本/时间窗口做 diff 视图 |
| 低权限只读共享 | 当前按 manager workspace 边界控制 | 后续可按 capability 拆分更细粒度可见范围 |

## 6. 权限边界
- 页面与 API 访问边界统一为 manager workspace 可见：
  - `owner/admin/manager` 可访问
  - `sales/viewer` 默认不可访问
- 前端路径准入复用 `canViewManagerWorkspace`（`lib/auth.ts` 路径规则）。
- 服务端读取复用现有组织权限断言（`assertOrgManagerAccess`），避免仅靠前端控制。

## 7. 后续如何演进到可编辑配置中心
1. 保持本次聚合结构作为读模型，新增写模型时继续复用 hardening/gating。  
2. 先补 persisted 审计历史查询，再接“单项编辑 + 预览 + 应用”。  
3. 编辑器写入返回继续复用 `writeDiagnostics + runtimeImpact + auditDraft`。  
4. 回滚能力优先接入 `template overrides`，再扩展到 org settings/ai/feature/automation。  

## 8. 风险与限制
- 历史治理摘要依赖 `template_application_runs` 快照中是否存在 `override_write_governance`；不存在时只能展示“不可用状态说明”。
- 当前展示以“当前态 explain”与“样例摘要”为主，不代表完整审计追溯。
- 面板结果依赖现有 runtime bridge 读路径，若后续新增 runtime 消费点未接 bridge，面板不会自动覆盖。

