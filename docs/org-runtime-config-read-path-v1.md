# MOY 组织运行时配置读路径 v1

更新时间：2026-03-21  
关联文档：
- `docs/industry-template-framework-v1.md`
- `docs/enterprise-customization-framework-v1.md`
- `docs/template-and-org-customization-runtime-bridge-v1.md`
- `docs/manager-executive-runtime-preference-bridge-v1.md`
- `docs/role-and-permission-model-v1.md`

## 1. 为什么要从 code seed 过渡到真实组织配置读路径
- 之前 runtime bridge 主要依赖代码 seed（模板 seed + 企业配置 seed），可以验证框架方向，但无法反映组织实际开关、阈值、模板分配状态。
- 这会导致同一组织在设置页改了配置，运行时推荐/排序/增强提示不一定优先消费真实数据。
- 本次目标是把“读路径”先打通：先读组织真实配置，再回退 seed/default，不做大规模 UI 和写路径重构。

## 2. 当前 runtime 配置来源现状（改造前）
- `buildResolvedIndustryTemplateContext` 以 `templateKey + seed 映射` 为主。
- 组织配置主要来自 `data/org-customization-seeds-v1.ts`，真实组织数据只在个别业务服务内局部使用，未统一进入 runtime bridge。
- 消费点（onboarding/automation/reports/executive/template apply 等）多数是“先拿模板 key，再走 seed overlay”。

## 3. 新的配置来源优先级
统一优先级（已落到 `services/template-org-runtime-bridge-service.ts`）：
1. persisted org assignment / override / settings（真实组织配置）
2. org-level fallback profile（基于模板映射的组织配置 seed 轮廓）
3. code seed / default（最后兜底）

说明：
- 底座语义（对象关系/状态机/权限/AI 治理）仍不可覆盖。
- 模板层与组织层只作用于参数、偏好、提示增强层。

## 4. 本次接入范围
- 新增统一入口：`buildResolvedOrgRuntimeConfig({ supabase, orgId, templateKey?, orgCustomizationKey? })`
- 新接入真实来源：
  - `org_template_assignments`（经 `getCurrentOrgTemplateContext`）
  - `org_template_overrides`（经 `getCurrentOrgTemplateContext`）
  - `org_settings`（经 `getOrgSettings`）
  - `org_ai_settings`（经 `getOrgAiSettings`）
  - `org_feature_flags`（经 `getOrgFeatureFlagMap`）
  - `automation_rules`（runtime bridge 直接只读查询）
- 已切换消费点（真实来源优先）：
  - `template-application-service`
  - `onboarding-service`
  - `automation-rule-service`
  - `report-generation-service`
  - `executive-cockpit-service`
  - `executive-brief-service`
  - `growth-pipeline-service`
  - `manager-insight-service`

## 5. 当前真实来源到 runtime 字段映射关系
### 运行时配置真实来源映射表
| 运行时字段/语义 | 真实来源 | 映射方式 | 当前状态 |
| --- | --- | --- | --- |
| `resolvedTemplateKey` | `org_template_assignments` | 未显式传 `templateKey` 时优先读取组织当前 active template | 已生效 |
| `templateSelection.defaultTemplateKey` | `org_template_assignments` | active template 映射为组织默认模板 | 已生效 |
| `templateSelection.enabledTemplateKeys` | `org_template_assignments` | active template 作为 enabled 列表 | 已生效 |
| 阈值：`alert_no_followup_days` / `alert_stalled_opportunity_days` / `followup_sla_days` | `org_settings.default_alert_rules` + `org_settings.default_followup_sla_days` | 映射并按阈值上下限 clamp | 已生效 |
| 组织功能偏好 `featurePreferences` | `org_feature_flags` | 同名 featureKey 覆盖 seed feature preference | 已生效 |
| AI 提示策略偏好 | `org_ai_settings.fallback_mode` + `human_review_required_for_sensitive_actions` | 注入 prompt strategy/hook（仅附加约束） | 已生效 |
| 自动化偏好 `automationRulePreferences` | `automation_rules` | 从 `rule_key + is_enabled + conditions_json` 映射到 preference | 已生效 |
| 组织模板覆盖：告警阈值 | `org_template_overrides` (`alert_rules`) | 覆盖阈值（优先于 `org_settings`） | 已生效 |
| 组织模板覆盖：管理关注指标 | `org_template_overrides` (`brief_preferences`) | 映射到 manager metric filters | 已生效 |
| 组织模板覆盖：敏感语义拦截 | `org_template_overrides` (`customer_stages`/`opportunity_stages`) | 标记 `ignoredOverrides`，不改状态机语义 | 已生效 |

## 6. 哪些配置已真正从组织数据读取
- 模板分配（assignment）已优先从组织真实数据读取。
- 组织 feature flag 已优先从真实数据读取。
- 组织默认告警阈值 / 跟进 SLA 已优先从 `org_settings` 读取。
- 自动化规则启停与阈值已优先从 `automation_rules` 读取并映射到 runtime preference。
- AI 运行策略相关偏好已从 `org_ai_settings` 读取并转为 prompt augmentation 约束。
- 模板 override 中可安全映射的字段（`alert_rules`、`brief_preferences`）已接线。

## 7. 哪些配置仍然只能从 seed/default 回退
### 仍待持久化或仍待接线字段表
| 字段/语义 | 当前来源 | 原因 | 后续建议 |
| --- | --- | --- | --- |
| `stageVocabularyOverrides` / `stageHintOverrides` | seed/default | 现有持久化表无等价结构化字段 | 增加白名单化 JSON 配置并做 schema 校验 |
| `managerFocusMetricOverrides`（模板选择层） | seed/default | 当前 `org_template_overrides` 无专门 overrideType | 新增受控 overrideType（避免复用含糊字段） |
| `importMappingHintOverrides` | seed/default | 组织级导入偏好尚未统一入 runtime bridge | 与 import mapping 服务对齐字段后接线 |
| objection/recommended action 细粒度组织覆盖 | seed/default + 模板默认 | 当前仅有 `prep_preferences` 的弱映射 | 建议增加专门 action preference 覆盖结构 |
| reporting `defaultDateRangeDays` 的组织持久化来源 | seed/default | `org_settings` 暂无该字段 | 在组织偏好配置中补持久化字段（带审计） |

## 8. 风险与后续演进建议
主要风险（具体）：
- `assignment / override / customization / seed` 命名边界仍可能混淆：`org_template_overrides` 里部分 overrideType 是模板应用参数，不等于通用组织偏好。
- `org_feature_flags` 与 `entitlement` 都会影响“功能是否可用”，必须保持“先 entitlement，再 feature preference”。
- 部分 overrideType（如 `customer_stages`）历史上可写，但在 runtime 层必须忽略以保护底座语义；需要持续在文档和 API 校验层一致约束。

后续建议：
1. 增加 runtime source explain（返回“本次字段来自 persisted 还是 seed”），用于设置页只读调试。
2. 为 `org_template_overrides` 增加白名单 schema 分层，区分“模板应用参数”与“运行时偏好覆盖”。
3. 把 `defaultDateRangeDays`、import mapping 偏好等仍依赖 seed 的字段补齐持久化结构与审计。
4. 对高权限写路径继续复用 capability helper，避免散落角色判断。
5. 写路径阶段补 `version/audit/rollback`，保证组织配置可回滚且可追责。
