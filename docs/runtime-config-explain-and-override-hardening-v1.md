# MOY Runtime Explain 与 Override Schema Hardening v1

更新时间：2026-03-21  
关联文档：
- `docs/enterprise-customization-framework-v1.md`
- `docs/template-and-org-customization-runtime-bridge-v1.md`
- `docs/manager-executive-runtime-preference-bridge-v1.md`
- `docs/org-runtime-config-read-path-v1.md`

## 1. 为什么 runtime explain 很重要
- 当前 MOY 已进入“真实组织配置优先”读路径，配置来源不再单一。
- 没有 explain 时，产品/运营/工程很难快速回答“为什么本组织拿到的是这个阈值、这个排序、这个推荐动作”。
- explain 是后续配置中心、审计、回滚的前置能力：先可解释，再可治理。

## 2. 为什么 override schema 必须硬化
- `org_template_overrides` 已同时承载模板应用参数与运行时偏好，天然有混用风险。
- 如果没有分层和白名单，运行时容易误读非法字段，写路径也无法稳定约束 payload。
- hardening 的目标是：写入可校验、读取可分层、非法可诊断。

## 3. 当前 override 混用风险
- `customer_stages` / `opportunity_stages` 这类核心语义字段如果进入 runtime 偏好层，会破坏底座状态机稳定性。
- 旧 payload 中可能存在“结构合法但语义不安全”字段，导致不同消费点行为不一致。
- 消费点自己判断 override 安全性会形成散落 if/else，难以统一治理。

## 4. 本次实现边界
- 只做 runtime explain + override schema hardening。
- 不做数据库 migration，不改大范围 UI。
- 不重写底座指标定义/状态机/权限语义。
- explain 先落在 source snapshot / debug context。

## 5. source explain 应包含哪些信息
### runtime explain 字段建议表
| 字段 | 说明 | 来源 |
| --- | --- | --- |
| `sourcePriority` | 固定优先级链路 | runtime bridge |
| `resolvedMode` | `persisted_preferred` 或 `seed_only` | runtime bridge |
| `fallbackProfileKey` | 当前生效 fallback profile key | runtime bridge |
| `resolvedTemplateKey` | 最终模板 key | assignment/request/fallback |
| `appliedOrgCustomizationKey` | 最终 customization profile | fallback profile/seed |
| `keyFieldSources` | 关键字段来源（persisted/fallback/seed） | explain builder |
| `persistedUsage` | assignment/overrides/settings/ai/feature/automation 是否参与 | runtime source snapshot |
| `appliedOverrides` | 进入 runtime 的合法 override 及影响字段 | override hardening + runtime bridge |
| `ignoredOverrides` | 被拒绝/忽略 override（含 layer/reason/diagnostics） | override hardening + runtime bridge |
| `diagnostics` | 可读调试信息 | explain builder |

## 6. override 分层建议
- `template application params`：用于模板应用过程，不直接进入 runtime 偏好层。
- `runtime preference overrides`：允许进入 runtime 参数/推荐/强调层。
- `forbidden core semantic overrides`：不允许进入 runtime（显式忽略并记诊断）。

### override 类型分层与处理策略表
| overrideType | 分层 | 写路径策略 | runtime 读取策略 |
| --- | --- | --- | --- |
| `alert_rules` | runtime preference overrides | 白名单 key + 数值校验后允许写入 | 允许进入 runtime，映射阈值 |
| `brief_preferences` | runtime preference overrides | items 数组校验后允许写入 | 允许进入 runtime，映射 manager metrics |
| `prep_preferences` | runtime preference overrides | items 数组校验后允许写入 | 允许进入 runtime，映射 prompt strategy |
| `checkpoints` | template application params | 校验后允许写入 | runtime 忽略（template apply 使用） |
| `playbook_seed` | template application params | payload 非空校验后允许写入 | runtime 忽略（template apply 使用） |
| `demo_seed_profile` | template application params | `value` 校验后允许写入 | runtime 忽略（template apply 使用） |
| `customer_stages` | forbidden core semantic overrides | 仅做结构校验后可写入模板应用层 | runtime 显式忽略并输出 diagnostic |
| `opportunity_stages` | forbidden core semantic overrides | 仅做结构校验后可写入模板应用层 | runtime 显式忽略并输出 diagnostic |
| unknown type | unknown | 拒绝写入 | runtime 忽略并输出 diagnostic |

## 7. 本次哪些服务开始携带 explain 信息
- `services/template-org-runtime-bridge-service.ts`
  - 新增 `buildRuntimeConfigExplainSnapshot`
  - `summarizeResolvedIndustryTemplateContext` 统一携带 `runtime_config_explain`
  - `applyReportFocusOverlay` 在 report overlay 中附带 explain
- `services/onboarding-service.ts`
  - onboarding recommendation 的 AI run input snapshot 带 `runtime_config_explain`
- `services/executive-brief-service.ts`
  - executive brief source snapshot 带 `runtime_config_explain`
- `services/report-generation-service.ts`
  - report source snapshot（runtime_preference_overlay）带 explain
- `services/automation-rule-service.ts`
  - 默认规则 seed 初始化输出 runtime debug context（resolved mode / threshold source / ignored override 计数）

## 8. 暂不做的内容与原因
- 不做 explain 的前端专用调试页面：本次先通过 snapshot/debug payload 提供可观测性。
- 不做 override 历史版本 UI：当前先把写读边界硬化，避免先建 UI 再补治理。
- 不做 override 全量迁移清洗：先保证新写入可校验、读路径可忽略非法，再做历史治理。

## 9. 后续写路径 / 审计 / 回滚建议
1. 在 override 写路径补 version/audit 记录（操作者、变更前后 diff、原因）。  
2. 为 runtime explain 增加“字段级最终值+来源”快照，支持问题定位。  
3. 为 override 引入回滚点（按 org/template/version 回退）。  
4. 在 settings/API 层统一返回 hardening 诊断，减少“写入成功但 runtime 不生效”的黑箱感。  
5. 将 `template application params` 与 `runtime preference overrides` 拆成更清晰的持久化语义边界。  
