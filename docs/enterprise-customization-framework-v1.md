# MOY 企业定制层框架 v1（内部）

更新时间：2026-03-21  
适用范围：产品、设计、工程在“通用底座 + 行业模板 + 企业定制”三层中的需求评审与实现对齐  
关联文档：
- `docs/product-architecture-principles-v1.md`
- `docs/sales-primitives-model-v1.md`
- `docs/industry-template-framework-v1.md`

---

## 1. 为什么 MOY 需要企业定制层

MOY 当前已具备跨行业底座能力和行业模板能力，但真实上线组织仍有组织级偏好差异：
- 组织级功能开关、配额与计划约束（`org_feature_flags`、`org_plan_profiles`）
- 自动化规则阈值与开关（`automation_rules`）
- onboarding 路径与优先项（`org_settings.onboarding_step_state`、`onboarding_runs`）
- 模板启用与覆盖（`org_template_assignments`、`org_template_overrides`）
- 组织级 AI 策略与 fallback 模式（`org_ai_settings`）

如果没有企业定制层，这些差异会散落在页面逻辑、临时 if/else、人工 SOP 中，最终破坏“可复用 + 可运维 + 可审计”。

---

## 2. 企业定制层在“三层架构”中的位置

1. 通用底座：对象关系、状态机、权限语义、AI 治理（不可按企业改写）  
2. 行业模板层：行业共性策略、术语、阈值建议、提示增强（可跨企业复用）  
3. 企业定制层：组织级偏好与限制，在模板之上做受控覆盖（不可突破底座约束）

当前代码映射：
- 底座：`types/customer.ts`、`types/opportunity.ts`、`types/followup.ts`、`types/work.ts`、`types/automation.ts`
- 模板：`types/template.ts`、`services/template-application-service.ts`
- 企业配置：`types/productization.ts`、`services/org-feature-service.ts`、`services/org-settings-service.ts`、`services/org-ai-settings-service.ts`

---

## 3. 企业定制与行业模板的关系

- 模板是“行业共性适配”：面向一类行业，复用术语/风险模式/动作策略。
- 企业配置是“组织级偏好与限制”：同一模板下，不同组织可调整开关、阈值、展示口径。
- playbook 是“场景打法沉淀”：来自模板 seed 或组织实践，属于执行知识资产，不等于模板本体，也不等于组织配置。

统一边界：
- `IndustryTemplate`：行业默认打法包（跨企业复用）
- `OrgTemplateOverride` / 企业配置：组织级受控覆盖
- `Playbook`：可迭代、可反馈的执行手册

---

## 4. 企业定制解决哪些差异，不解决哪些差异

企业定制解决：
- 组织治理偏好（开关、阈值、可见口径、流程优先级）
- 组织经营节奏差异（快节奏 SaaS vs 长周期大客户）
- 组织内部策略偏好（提示增强策略、管理关注优先级）

企业定制不解决：
- 新对象关系设计
- 新状态机语义定义
- 权限底线变更
- AI provider/fallback 审计链路改写

---

## 5. 企业可以定制什么

已存在代码承载或可小步接入的定制项：
- 组织级 feature switches：`org_feature_flags`
- 自动化规则阈值/开关：`automation_rules.conditions_json` + `is_enabled`
- onboarding 偏好：`org_settings.onboarding_step_state`
- manager / executive 指标筛选与展示偏好：`reports`、`executive` 聚合参数层
- import mapping 偏好：`import-template`/`import-mapping` 参数与提示层
- 模板启用/禁用与默认模板：`org_template_assignments` + `org_template_overrides`
- prompt augmentation / scenario strategy 偏好：`ai_prompt_versions` + 模板 hook 的组织覆盖
- 风险阈值、节奏阈值等参数：`org_settings.default_alert_rules` + `automation_rules` + 组织级阈值配置

### 企业配置项建议表

| 配置项 | 当前可复用承载 | 推荐落层 | 允许覆盖范围 | 不允许覆盖范围 |
|---|---|---|---|---|
| 组织级功能开关 | `org_feature_flags` | 企业配置 | 开关状态、说明文本 | 新增底座能力语义 |
| 自动化阈值 | `automation_rules` | 企业配置 | 条件阈值、启停、动作参数 | 事件状态机流转 |
| onboarding 偏好 | `org_settings.onboarding_step_state` | 企业配置 | 步骤优先级、提示顺序 | onboarding run 状态语义 |
| 模板启用/默认 | `org_template_assignments` | 企业配置 | enabled/disabled/default | 模板对象定义本身 |
| 模板覆盖参数 | `org_template_overrides` | 企业配置 | 文案、阈值、提示增强 | 底座状态机枚举 |
| 报表展示偏好 | `report-generation-service` 参数层 | 企业配置 | 维度筛选、默认窗口 | 核心指标定义 |
| Prompt 策略偏好 | `ai_prompt_versions` + template hooks | 企业配置 | 场景补充约束、附加检查项 | provider/fallback 结果语义 |
| 导入映射偏好 | `import-mapping-service` | 企业配置 | 列映射提示、owner 匹配策略 | 导入审计链路 |

---

## 6. 企业不能定制什么

- 核心对象关系：customer/followup/opportunity/work-item/alert/event
- 底座状态机语义：客户阶段、商机阶段、事件状态流、任务状态流
- org 边界与权限底线：组织隔离、角色边界、seat 状态约束
- provider vs fallback 结果语义：不能伪造“provider 成功”掩盖 fallback
- AI 审计链路：`ai_runs` 记录、result_source、fallback_reason

---

## 7. 企业配置的推荐分层

1. org-level immutable identity / boundary  
- 组织 ID、成员边界、权限底线、审计主体（只读，不可配置）

2. org-level configurable preferences  
- feature flags、阈值、onboarding 偏好、导入偏好

3. template selection / overlay  
- 模板启用/默认、组织级模板参数覆盖（仅内容和参数）

4. reporting / visibility preferences  
- 看板维度筛选、默认时间窗口、管理视角优先项

---

## 8. 企业配置如何覆盖模板，但不破坏模板与底座

推荐合并优先级（必须稳定）：
1. 底座语义最高  
2. 行业模板其次  
3. 企业配置仅覆盖允许字段（参数层）

本次骨架在代码层体现：
- 新增 `types/customization.ts`：定义可覆盖字段白名单 + schema 校验
- 新增 `lib/org-customization.ts`：`mergeTemplateWithOrgCustomization()` 固定优先级
- 合并后保留 `baseStateMachineGuards` 原样，不允许组织配置改写

---

## 9. 哪些企业诉求应拒绝，哪些应进入底座演进评审

默认拒绝：
- “给我们单独 fork 一套代码”
- “改成我们自己的阶段枚举与状态流”
- “绕过 org 权限，给跨组织全局可见”
- “把 fallback 结果当 provider 结果”

应进入底座演进评审：
- 多个行业/多个组织反复出现的同类对象语义缺口
- 现有底座无法表达且会影响核心链路可见性的能力
- 需要新增通用审计字段或通用状态语义的场景

### 企业诉求分流建议表

| 诉求类型 | 分流建议 | 判定标准 | 当前示例 |
|---|---|---|---|
| 调整阈值/开关 | 企业配置 | 仅参数变化，不改对象语义 | 自动化规则阈值 |
| 行业术语和建议差异 | 行业模板 | 跨企业可复用 | SaaS/制造业阶段提示 |
| 组织报表口径偏好 | 企业配置 | 仅展示筛选 | manager 关注指标过滤 |
| 新的核心对象关系 | 底座评审 | 影响通用原语 | 独立 contact/decision actor |
| 绕过权限/边界 | 拒绝 | 破坏安全底线 | 跨 org 直接读取 |
| 单客户代码分叉 | 拒绝 | 不可维护 | 私有分支功能 |

---

## 10. 建议的配置生命周期

状态：
- `draft`：配置编辑中，不生效
- `active`：配置生效并可审计
- `archived`：配置归档，仅供回溯

生命周期建议：
- versioning：配置版本号 `vX.Y.Z`
- rollout：先小范围组织灰度，再全量
- rollback：保留上一个稳定版本，一键回退参数层
- audit：每次启用/变更记录变更人、时间、差异快照

---

## 11. 当前仓库最适合先支持哪些企业配置切入点

优先切入点（改动小、价值高）：
1. `org_feature_flags` 的组织策略预设（按团队类型一键套用）
2. `automation_rules` 阈值模板化 + 组织覆盖
3. `org_template_assignments` + `org_template_overrides` 的白名单校验强化
4. `onboarding_step_state` 增加组织级“优先步骤策略”
5. 报表/executive 默认指标筛选偏好配置

当前命名与边界风险（需在后续收敛）：
- `template` vs `override` vs `playbook` 在页面层容易混用
- `entitlement`（计划能力）与 `org feature preference`（组织偏好）概念接近，需明确“先 entitlement 后 preference”
- `types/auth.ts` 的 `sales|manager` 与组织角色模型不完全一致，可能导致配置可见性表达过粗

---

## 12. 后续接入建议（low effort / medium effort / later）

### low effort, high value
1. 在模板应用与组织覆盖接口增加统一 schema 校验（白名单 + 禁止字段）
2. 在 `/settings/templates` 显式展示“模板默认值 vs 组织覆盖值”对比
3. 自动化规则中心支持“组织阈值预设包”一键应用

### medium effort
1. 新增企业配置审计日志表（变更 diff + 回滚点）
2. 报表与 executive 看板接入组织级指标偏好配置
3. prompt 场景策略接入组织配置（仅追加约束，不改输出 schema）

### later
1. 组织配置灰度发布与回滚面板
2. 企业配置效果归因（阈值调整前后命中率/完成率变化）
3. 跨组织配置模板市场（仍受底座与模板边界约束）

---

## 实施原则（明确）

- 企业配置优先参数化、配置化、模块化。
- 不采用单客户代码分叉，不引入“客户私有状态机”。
- 模板适配的是行业共性策略；企业配置适配的是组织偏好与限制；playbook 沉淀的是场景打法。三者必须分层治理，不可混用。

