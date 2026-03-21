# MOY 组织 Override 写路径治理与审计 v1

更新时间：2026-03-21  
关联文档：
- `docs/runtime-config-explain-and-override-hardening-v1.md`
- `docs/org-runtime-config-read-path-v1.md`
- `docs/role-and-permission-model-v1.md`

## 1. 为什么现在要补写路径治理
- runtime 读路径已经进入“持久化配置优先 + fallback/seed 兜底”模式，写路径如果继续“只要写进表就算成功”，会放大配置误解。
- `org_template_overrides` 当前同时承载模板应用参数与运行时偏好覆盖，必须在写前统一分类和约束，避免入口各自判断。
- 现阶段先补“最小治理能力 + 审计草案”，可以在不做大 UI/大迁移的前提下，把高风险写路径变成可校验、可诊断、可追溯。

## 2. 当前风险点
- 写入成功但运行时忽略：
`customer_stages`、`opportunity_stages` 可被写入，但 runtime 层必须忽略，若无诊断会被误认为“系统没生效”。
- override 类型混用：
运行时偏好与模板应用参数混在同一张表，没有统一分层会导致消费点行为不一致。
- 无版本 / 无审计 / 无 diff：
历史上缺少统一 audit 形态，难以回答“谁改了什么、对 runtime 有什么影响”。
- 难以回滚：
没有“前后摘要 + 诊断摘要”时，回滚只能靠人工猜测和重写 payload。

## 3. 本次治理边界
- 只覆盖高风险关键写路径：优先 `org_template_overrides` 写入链路。
- 不做完整配置中心 UI：仅在现有 API/service 返回中补 diagnostics 与 audit draft。
- 不做大 migration：本次不新增重型审计表，先落地 service 级治理与轻量 evented 记录。

## 4. 推荐写路径流程
1. 输入校验：统一走 `prepareOrgTemplateOverrideWrite(...)`，而不是入口散落 if/else。
2. override 分层归类：基于 `template-override-hardening` 的 layer/type 分类。
3. forbidden override 拒绝/忽略并返回诊断：
   - payload 非法：拒绝写入（`write_rejected`）
   - 语义 forbidden：允许入库但标记 runtime ignored（`runtime_ignored_forbidden_core_semantics`）
4. 持久化：仅写入 `normalizedPayload`，避免脏字段扩散。
5. 审计记录：生成 `auditDraft`（before/after/diagnostics/version/timestamp），并写轻量事件日志。
6. 返回 explain/diagnostic 摘要：返回 accepted/ignored/forbidden/rejected 与 runtime impact 计数。

### 写路径治理字段建议表
| 字段 | 说明 | 本次状态 |
| --- | --- | --- |
| `overrideType` | 写入目标类型 | 已实现 |
| `layer` | `template_application_params / runtime_preference_overrides / forbidden_core_semantic_overrides / unknown` | 已实现 |
| `acceptedForWrite` | 写前校验是否通过 | 已实现 |
| `acceptedForRuntime` | runtime 是否消费该 override | 已实现 |
| `forbiddenForRuntime` | 是否属于核心语义禁止覆盖 | 已实现 |
| `ignoredByRuntime` | 写入后 runtime 会忽略 | 已实现 |
| `normalizedPayload` | 归一化后的可持久化 payload | 已实现 |
| `acceptedFields` | 生效字段路径集合 | 已实现 |
| `ignoredFields` | 被忽略字段路径集合（如未知 alert rule） | 已实现 |
| `reason` | 主诊断原因码 | 已实现 |
| `diagnostics` | 细粒度诊断列表 | 已实现 |
| `runtimeImpactSummary` | `runtime_consumed / runtime_ignored_* / write_rejected` | 已实现 |
| `runtimeImpactCounters` | 诊断摘要计数（consumed/ignored/rejected） | 已实现 |

## 5. 本次已治理的写路径
- `services/industry-template-service.ts`
  - `upsertOrgTemplateOverride` 已接入统一治理：
    - 写前：`prepareOrgTemplateOverrideWrite`
    - 写时：仅写 `normalizedPayload`
    - 写后：返回 `writeDiagnostics + auditDraft`
  - 非法 payload 抛错：`template_override_payload_invalid:*`
- `app/api/settings/templates/overrides/route.ts`
  - 返回 `override + writeDiagnostics + diagnosticsSummary + auditDraft`
  - 调用方可直接识别“写入成功但 runtime 会忽略”的情形
- `services/template-application-service.ts`（`applyTemplate`）
  - 覆盖写入循环已接入新结果结构
  - `resultSnapshot` 新增 `override_write_governance`（summary/diagnostics/auditDrafts）

## 6. 审计信息最小字段建议
本次采用 **draft + evented** 方案：
- draft：通过返回值输出 `auditDraft`
- evented：`console.info("[org.override.write.audit]", ...)` 记录关键摘要
- persisted：暂未做独立审计表落库

### 审计日志字段建议表
| 字段 | 说明 | 本次状态 |
| --- | --- | --- |
| `version` | 审计结构版本号 | 已实现（`1`） |
| `happenedAt` | 写操作时间 | 已实现 |
| `orgId` | 组织 ID | 已实现 |
| `actorUserId` | 操作人 | 已实现 |
| `targetType` | 目标类型（当前为 `org_template_override`） | 已实现 |
| `targetId` | 目标记录 ID | 已实现 |
| `targetRef` | 目标引用（`templateId + overrideType`） | 已实现 |
| `beforeSummary` | 变更前 payload 摘要（keys + preview） | 已实现 |
| `afterSummary` | 变更后 payload 摘要（keys + preview） | 已实现 |
| `diagnosticsSummary` | layer/reason/runtimeImpact/ignored/forbidden/diagnostics | 已实现 |
| `requestId` | 请求链路 ID | 未接入（后续建议） |
| `persistedAuditId` | 持久化审计记录 ID | 未接入（后续建议） |

## 7. 后续回滚机制建议
1. 引入版本化主键：
按 `orgId + templateId + overrideType + version` 保存历史版本，当前版本单独标记。
2. 建立可回滚点：
每次写操作输出 `rollbackCandidate`（上一个 version 的关键索引）。
3. 审计表落地：
将现有 `auditDraft` 结构落库，保留 before/after 摘要与 diagnostics，支持检索和追责。
4. 差异回放：
按版本重放 `normalizedPayload`，禁止重放原始未清洗 payload。
5. 回滚权限：
继续复用 org capability 边界（owner/admin 可写，可回滚）。

## 8. 哪些写路径仍待接线
- `settings/org`、`settings/ai`、`settings/feature-flags` 等组织配置写入口：
  目前主要有权限与 schema 校验，尚未统一输出 write diagnostics 与 audit draft。
- 自动化规则写路径（`automation_rules`）：
  当前有 runtime seed explain，但缺少同等级“写前治理 + audit 草案”。
- 模板分配写路径（`org_template_assignments`）：
  当前有操作结果快照，但缺少统一 before/after 结构化审计对象。

## 9. 当前真实状态声明
- 本次“审计”状态：**draft + evented**（不是全量 persisted）。
- 本次“治理”状态：**已治理核心 override 写路径（service + 1 个直写 API + 1 个模板应用链路快照）**。
- 本次“回滚”状态：**已准备数据形态，尚未形成正式回滚执行 API/UI**。

