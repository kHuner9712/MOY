# MOY 行业模板 / 打法包框架 v1（内部）

更新时间：2026-03-21  
适用范围：产品、设计、工程在“通用底座 + 行业模板 + 企业配置”三层中的需求评审与实现对齐  
关联文档：
- `docs/product-architecture-principles-v1.md`
- `docs/sales-primitives-model-v1.md`

---

## 1. 为什么 MOY 需要行业模板层

MOY 当前已经有跨行业可复用的销售底座，但不同行业在以下方面差异明显：

- 阶段术语（同样是 `qualification`，SaaS 更强调试用激活，制造业更强调技术澄清）
- 风险信号（SaaS 常见“试用无激活/续费风险”，制造业常见“样品反馈延迟/采购推进滞后”）
- 推荐动作（SaaS 偏高频价值回顾，制造业偏技术评审 + 采购路径并行）
- 经理关注点（SaaS 关注试用转化与续费，制造业关注关键节点阻塞与大客户推进节奏）

如果没有模板层，差异会被散落到页面、文案和临时规则，最终导致：

- 同一底座对象在各模块被“软分叉”
- 新行业接入只能复制代码而不是参数化扩展
- 企业定制容易越过边界，侵入底座状态机和权限语义

---

## 2. 行业模板在三层架构中的位置

MOY 三层结构分工如下：

1. 通用销售底座（稳定层）
- 对象关系、状态机、权限、AI 治理、审计与 fallback
- 典型实现：`types/customer.ts`、`types/opportunity.ts`、`types/followup.ts`、`types/work.ts`、`types/ai.ts`

2. 行业模板 / 打法包（适配层）
- 在不改底座语义前提下，注入行业化内容、阈值、策略、提示增强
- 典型实现：`types/productization.ts`、`data/industry-templates.ts`、`services/industry-template-service.ts`、`services/template-application-service.ts`

3. 企业定制层（受控配置层）
- 同一模板上的组织级 override 与开关
- 典型实现：`org_template_overrides` 相关服务、`services/org-feature-service.ts`、`services/org-ai-settings-service.ts`

---

## 3. 模板解决哪些差异，不解决哪些差异

模板应解决：

- 行业阶段词汇与阶段提示（词汇层，不改底层枚举语义）
- 风险模式、阈值建议、处置策略
- 异议库、推荐动作库、经理关注指标
- onboarding 提示、import 映射提示、AI prompt 增强片段

模板不解决：

- 不改底座对象关系（customer/followup/opportunity/work-item/alert 等）
- 不改底座状态机定义与含义（如 `CustomerStage`、`OpportunityStage`）
- 不改权限边界（org 隔离、角色能力）
- 不绕过 AI run 审计与 fallback 机制

---

## 4. 模板与销售共性原语的关系

模板不是新原语，而是对“共性原语”的行业化解释层。

- 模板输入：共性原语当前状态（客户、商机、跟进、任务、预警、管理动作、结果）
- 模板输出：行业化“解释与建议”（词汇、阈值、策略、提示增强）
- 模板边界：输出可以影响内容与参数，不可以改变底座原语语义

当前仓库已体现该方向：

- 模板选择与应用：`/settings/templates` + `services/template-application-service.ts`
- 模板匹配推荐：`services/template-fit-service.ts`
- 模板到 playbook seed：`services/template-seed-service.ts`
- 模板参与 onboarding：`services/onboarding-service.ts`（检查 `org_template_assignments`）

---

## 5. 模板的最小结构建议

### 5.1 推荐最小字段

- `templateKey` / `name` / `version` / `status`
- `applicableSalesMode`
- `stageVocabulary` / `stageHints`
- `commonRiskPatterns`
- `objectionLibrary`
- `recommendedActionLibrary`
- `managerFocusMetrics`
- `onboardingHints`
- `importMappingHints`
- `promptAugmentationHooks`

### 5.2 模板字段建议表

| 字段 | 建议类型 | 作用 | 对应现状 |
|---|---|---|---|
| `templateKey` | `string`（稳定主键） | 模板唯一识别 | `types/productization.ts` 已有 `templateKey` |
| `name` | `string` | 展示名称 | `displayName` 语义已存在 |
| `version` | `string`（建议 `vX.Y.Z`） | 版本治理、回滚依据 | 当前模板主数据缺显式语义版本字段 |
| `status` | `draft/active/archived` | 生命周期控制 | 现有 `IndustryTemplateStatus` 已支持 |
| `applicableSalesMode` | `string[]` | 模板适配销售模式边界 | 当前 `templatePayload` 可承载但未强类型 |
| `stageHints` | `object[]` | 行业阶段词汇与退出条件提示 | 现有仅有 `suggested_checkpoints`，颗粒度不足 |
| `commonRiskPatterns` | `object[]` | 风险信号与建议动作 | 现有 `scenario_packs:risk_signals` 有基础能力 |
| `objectionLibrary` | `string[]` 或 `object[]` | 统一异议库 | 现有 `scenario_packs:objections` 有基础能力 |
| `recommendedActionLibrary` | `object[]` | 行业动作建议 | 现有 playbook seed 可承载 |
| `managerFocusMetrics` | `string[]` | 经理看板关注口径 | 现有模板 payload 有 `manager_attention_signals` 基础 |
| `onboardingHints` | `string[]` | onboarding 指南 | `services/onboarding-service.ts` 可消费 |
| `importMappingHints` | `string[]` 或 `object[]` | 导入字段映射提示 | `services/import-template-service.ts` 可接入 |
| `promptAugmentationHooks` | `object[]` | 指定场景 prompt 追加增强 | `services/ai-prompt-service.ts` 场景体系可承接 |

---

## 6. 模板生命周期

### 6.1 状态

- `draft`：可编辑、不可全量发布
- `active`：可推荐、可应用
- `archived`：不可新分配，仅历史可追溯

### 6.2 版本与发布建议

- 版本号语义化：`vX.Y.Z`
  - `X`：不兼容语义变更（应极少）
  - `Y`：可兼容策略增强
  - `Z`：文案/阈值微调
- 先 `preview` 再 `apply`：复用现有 `preview-apply` API 路径
- rollout：优先 onboarding 新组织和 demo/trial 组织
- rollback：保留最近稳定版本，回滚时仅回滚模板参数，不变更底座对象

---

## 7. 模板如何与现有模块结合

### 7.1 模板接入点建议表

| 模块 | 当前代码锚点 | 建议接入方式 | 实施优先级 |
|---|---|---|---|
| playbooks | `services/template-seed-service.ts`、`services/playbook-service.ts` | 模板驱动 seeded playbook，保留人工反馈闭环 | 高 |
| prompts | `services/ai-prompt-service.ts` | 按场景追加模板 hook（仅增强，不改 schema） | 中 |
| reports | `services/report-generation-service.ts` | 报告文案与关注点按模板偏置 | 中 |
| imports | `services/import-template-service.ts` | 提供模板化映射提示，不改导入核心流程 | 高 |
| onboarding | `services/onboarding-service.ts` | 模板应用作为 checklist 关键步骤 | 高 |
| growth/demo/trial | `services/growth-pipeline-service.ts`、`services/demo-request-service.ts`、`services/trial-request-service.ts` | 模板影响建议动作与阈值，不改转化对象语义 | 高 |

---

## 8. 哪些模板配置只能影响内容和参数，不能影响底座状态机

模板允许影响：

- 文案（阶段提示、异议话术、建议措辞）
- 阈值（提醒时机、风险触发阈值建议）
- 策略（推荐动作优先级、经理关注权重）
- 提示增强（prompt augmentation hooks）

模板禁止影响：

- 底座对象关系（customer-opportunity-followup-work-item-alert）
- 底座状态机语义（`CustomerStage`、`OpportunityStage`、任务状态流）
- 权限语义（角色边界、org 隔离）
- AI 治理（run 审计、result source、fallback 链路）

---

## 9. 当前仓库最适合先支持哪些模板切入点

最先落地的切入点（已具备代码承接能力）：

1. 模板 seed 标准化（统一字段 + 强类型）
2. 模板到 playbook seed 的对齐强化
3. onboarding 中模板推荐与应用闭环强化
4. growth/demo/trial 的模板化建议增强
5. import 映射提示模板化

当前关键架构风险（具体）：

- `templatePayload: Record<string, unknown>` 过于宽松，容易产生“字段同名异义”
- `applyTemplateConfig` 对 stage 字段为字符串数组，缺少类型守卫，存在语义漂移风险
- “模板 / playbook / 企业 override”概念在命名上容易混淆（同属配置层但职责不同）
- 模板版本语义不显式，发布与回滚审计粒度不足

---

## 10. 后续如何支持企业在模板之上做受控配置

建议采用“模板默认值 + 企业 override 白名单”：

- 企业可配：阈值、文案偏好、关注指标权重、导入映射偏好、prompt hook 开关
- 企业不可配：底座状态机、对象关系、权限语义、AI 治理链路
- 所有 override 要求：可审计、可回滚、可对比 diff

命名边界建议（避免混淆）：

- `IndustryTemplate`：行业默认打法包（跨企业可复用）
- `Playbook`：从模板/历史结果沉淀的具体执行手册（可反馈迭代）
- `OrgTemplateOverride`：企业在模板之上的受控参数改写

---

## 后续接入优先级建议

### low effort, high value

1. 为模板骨架增加强类型与 seed 校验测试（先在代码侧，不改 DB）
2. onboarding 页面显示“模板差异摘要 + 推荐 apply 策略”
3. 模板到 playbook seed 的字段对齐（减少 payload 自由拼接）
4. growth summary 增加模板化“行业关注项”提示

### medium effort

1. prompt augmentation hooks 与 `AiScenario` 做受控映射
2. import mapping hints 接入导入向导推荐
3. 模板版本发布/回滚日志面板（先只读）

### later

1. 模板灰度发布与效果对比（组织分组）
2. 模板效果归因（转化率、风险处理时效、经理介入成功率）
3. 模板市场化能力（模板包审核与分发）

