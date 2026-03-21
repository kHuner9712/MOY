# MOY 模板层与企业定制层运行时桥接 v1

更新时间：2026-03-21  
适用范围：`template application`、`playbook seed`、`onboarding`、`automation`、`AI prompt augmentation` 最小运行时接入  
关联文档：
- `docs/product-architecture-principles-v1.md`
- `docs/sales-primitives-model-v1.md`
- `docs/industry-template-framework-v1.md`
- `docs/enterprise-customization-framework-v1.md`
- `docs/role-and-permission-model-v1.md`

---

## 1. 本次运行时接入范围

本次只做“最小可运行桥接”，不做平台化重构，不改数据库结构，不引入新管理 UI。  
接入目标：让“底座 -> 行业模板 -> 企业配置”第一次在真实运行时路径中生效。

本次新增的运行时桥接入口：
- `services/template-org-runtime-bridge-service.ts`
- 基于该入口接入：
  - `template application / playbook seed`
  - `onboarding checklist + recommendation`
  - `automation default rule seed`
  - `AI prompt augmentation`（先在 onboarding/growth 场景生效）

---

## 2. 为什么只选这些接入点

选择原则：低耦合、高可验证、对现有主流程风险最小。

- `template application / playbook seed`：已有模板应用主路径，最适合作为“先铺桥、再接线”的入口。
- `onboarding`：天然需要“推荐顺序与提示”能力，适合先消费模板/企业配置的内容层差异。
- `automation default seed`：规则创建点集中，便于把阈值/启停偏好注入参数层。
- `prompt augmentation`：在不改输出 schema 的前提下注入策略补丁，风险可控且效果可观测。

---

## 3. 运行时合并优先级

统一优先级（代码中固定）：
1. `base semantics`（底座语义最高）
2. `industry template`
3. `org customization`（仅允许覆盖参数/内容层）

边界约束：
- 不允许覆盖底座对象关系。
- 不允许改写底座状态机语义。
- 不允许改写权限语义。
- 不允许改写 AI provider/fallback 治理语义。

---

## 4. 当前接入点清单

### 已接入运行时消费点表

| 消费点 | 接入文件 | 已生效行为 | 生效层级 |
|---|---|---|---|
| 模板应用参数桥接 | `services/template-application-service.ts` | 模板应用时会先构建 resolved runtime context，并把模板+企业配置参数层覆盖到应用草案（如 alert 阈值、manager signal、onboarding path） | template + org |
| Playbook seed 运行时增强 | `services/template-seed-service.ts` | 种子 playbook 在创建时附加 runtime action entries，并写入 runtime context 快照 | template + org |
| Onboarding checklist 排序与提示 | `services/onboarding-service.ts` | 已应用模板时，checklist 支持按组织偏好重排，并注入模板/组织 onboarding hint | template + org |
| Onboarding 推荐 Prompt 增强 | `services/onboarding-service.ts` | `onboarding_recommendation` 场景会注入模板/组织 prompt augmentation（仅补充上下文，不改 schema） | template + org |
| Growth 推荐 Prompt 增强 | `services/growth-pipeline-service.ts` | `growth_pipeline_summary` 场景会注入模板/组织 prompt augmentation（仅补充上下文，不改 schema） | template + org |
| Automation 默认规则参数化 | `services/automation-rule-service.ts` | 初始化默认规则时，阈值与启停会消费模板+企业配置 overlay，影响默认 rule seed 条件与 `is_enabled` | template + org |

---

## 5. 暂不接入的点及原因

当前明确暂不接入（避免高耦合硬接）：
- `executive cockpit` 全量指标口径重写：当前统计链路较深，先保留底座指标口径。
- `reports` 全场景模板化文案：先保留现有 report 生成路径，避免报表回归风险。
- `import` 全流程动态映射策略：当前导入链路涉及校验与幂等，先保留现有模板机制。
- 模板/企业配置数据库持久化平台：本次先用代码 seed 骨架验证运行时机制。

这些点属于“先铺桥，后接线”的下一阶段工作。

---

## 6. 配置/模板如何进入运行时

本次入口链路：
1. 读取当前模板 key（来自模板应用上下文或当前组织模板分配）
2. 通过 alias 映射到可用 `IndustryTemplateDefinition` seed
3. 选择组织配置 seed（优先显式 key，其次模板默认映射，否则 default）
4. 执行合并：`mergeTemplateWithOrgCustomization`
5. 在各消费点读取 resolved context：
   - 模板应用：配置覆盖
   - playbook：runtime entries
   - onboarding：顺序/提示
   - automation：阈值/启停
   - prompt：hook augmentation
6. 无模板或模板不可用时回退到底座默认行为

---

## 7. helper / adapter / mapping 层职责

核心桥接服务：`services/template-org-runtime-bridge-service.ts`

职责分解：
- `buildResolvedIndustryTemplateContext`：统一解析 template + org customization + merge 结果。
- `applyRuntimeTemplateConfigOverlay`：只覆盖允许覆盖的参数层，不触碰底座状态机字段。
- `resolveAutomationRuleSeedsWithRuntime`：把阈值/启停偏好映射到 automation seed 条件。
- `buildPromptAugmentationContext`：按场景输出 prompt patch（只追加上下文）。
- `sortOnboardingChecklistByPreferredKeys`：把组织偏好映射到 onboarding 步骤顺序。
- `buildRuntimePlaybookSeedEntries`：把推荐动作库映射为 playbook seed entry。

该层是“桥接层”，不替代底座 service，不改变底座数据模型。

---

## 8. 风险与后续扩展建议

当前风险（具体）：
- 模板 alias 目前只覆盖 `SaaS` 与 `制造业大客户` 两条映射，其他行业暂回退到底座。
- 企业配置仍是代码 seed 语义，尚未进入持久化配置中心与审计链。
- prompt augmentation 目前只接入 onboarding/growth 场景，覆盖范围有限。
- automation 参数化仅影响“默认 seed 初始化”，对已存在规则不做强制迁移。

缓解方式：
- 保持回退路径：无模板/无可用配置时完全保持原有行为。
- 保持边界守卫：禁止覆盖底座状态机/权限/AI 治理语义。
- 用测试锁定：正反向用例覆盖 merge 可预测性、非法语义无效化、默认回退。

---

## 后续可扩展消费点表

| 消费点 | 建议阶段 | 说明 |
|---|---|---|
| `report-generation-service` 场景化提示增强 | low effort, high value | 在 report prompt 追加模板/组织关注点，不改报表 schema |
| `executive-cockpit-service` 关注指标偏好 | medium effort | 先做“展示层筛选偏好”，不改底层事件语义 |
| `import-template-service` 映射 hint overlay | medium effort | 只增强映射建议，不改导入状态机 |
| `template-fit-service` 融合企业策略偏好 | medium effort | 提升推荐精度，保持可回退 |
| 配置持久化与审计回滚 | later | 在组织配置中心落地版本化、审计、回滚能力 |

---

## 本次结论

本次不是“搭平台”，而是把模板层和企业定制层第一次接上真实运行时链路。  
已生效：模板应用、playbook seed、onboarding、automation seed、部分 prompt 场景。  
尚未生效但已铺桥：report/executive/import 的全量消费链。  
