# MOY org_template_overrides 并发漂移保护（Optimistic Concurrency Guard）v1

更新时间：2026-03-22  
关联文档：
- `docs/org-template-override-rollback-v1.md`
- `docs/persisted-audit-and-version-snapshot-foundation-v1.md`
- `docs/org-override-write-path-governance-v1.md`
- `docs/runtime-explain-debug-panel-v1.md`

## 1. 为什么现在需要并发漂移保护
- 现有链路已经支持 rollback preview 与执行，但 preview 到 execute 之间仍可能被其他人改写。
- 只做“执行前再查一次”仍不够可解释，调用方需要明确的 compare baseline，才能知道“为什么被拒绝”。
- 为后续配置中心编辑器做准备，必须先有最小的 expected version / compare token 约束能力。

## 2. 当前风险场景
- preview 后被他人改写：用户基于旧快照点击执行，实际目标已变化。
- rollback 目标链路变化：目标版本仍在，但当前覆盖值和确认时不一致。
- 当前值与确认时不一致：接口写入时只知道“失败”，无法定位是版本冲突还是 payload 漂移。

## 3. 本次方案边界
- 仅覆盖 `org_template_overrides` 的写入与 rollback execute。
- 不做分布式锁，不做全局事务编排。
- 不做自动 merge，不做人工 diff 冲突解决 UI。
- 不新增 migration；复用现有 persisted audit/version 基础。

## 4. compare token / expected version 设计思路
- 新增统一 helper：`lib/override-concurrency-guard.ts`。
- compare baseline 由以下字段计算：
  - `targetKey`（`templateId:overrideType`）
  - `auditAvailability`（`available/empty/not_available`）
  - `currentVersionLabel/currentVersionNumber`（来自最新 persisted audit，若可用）
  - `currentOverrideUpdatedAt`
  - `currentPayloadHash`（normalized payload 的 SHA-256）
- `compareToken`：`ovc_v1_<hash>`，由上述基线字段稳定计算。
- `expectedVersion`：执行时回传 compare baseline（支持 token 或显式 version/updatedAt/hash 字段）。

## 5. 写入链路如何使用
- `upsertOrgTemplateOverride(...)` 新增 `expectedVersion` 入参（可选）。
- 若传入 expected baseline：
  - 先加载当前 baseline；
  - 执行 drift 校验（token/version/updatedAt/hash）；
  - 不一致则抛出 `OverrideDriftConflictError`，拒绝写入。
- `/api/settings/templates/overrides` 在冲突时返回 HTTP 409，且包含结构化冲突信息（`currentVersion/expectedVersion/diagnostics`）。

## 6. rollback execute 如何使用
- rollback preview 输出：
  - `preview.concurrency.baseline`
  - `preview.concurrency.expectedVersion`
- rollback execute 要求携带 `expectedVersion`（本次接口层强制）。
- 执行时两次保护：
  1. `executeOrgTemplateOverrideRollback` 先校验 preview 时点的 baseline；
  2. 内部写入 `upsertOrgTemplateOverride` 再次校验，防止执行瞬间漂移。
- 冲突时返回 `status=conflict`，并带结构化冲突信息。

## 7. 拒绝执行时的返回结构建议
- 顶层标记：
  - `conflict: true`
  - `conflictReason`
- 版本对比：
  - `currentVersion`（当前 compare token / version / updatedAt / payloadHash）
  - `expectedVersion`（调用方提交的基线）
- 诊断：
  - `diagnostics`（包含 `concurrency_conflict:*`）

## 8. 当前不解决的问题
- 不保证跨请求严格原子性（非分布式锁）。
- 不做冲突自动合并。
- 不做冲突后的人工差异解决流程（仅结构化拒绝）。
- 不覆盖 `org_settings/org_ai_settings/org_feature_flags`。

## 9. 后续演进方向
1. 在更多配置类型复用同一 guard（统一 compare token 规范）。
2. 增加冲突后的可视化 diff（基于 snapshot/payload hash）。
3. 增加“重试并重新预览”的标准 API 流程封装。
4. 按能力边界扩展到批量变更的冲突检测。

## 并发保护输入字段建议表
| 字段 | 来源 | 用途 | 本次状态 |
| --- | --- | --- | --- |
| `expectedVersion.compareToken` | preview 返回 | 最快冲突判定 | 已实现 |
| `expectedVersion.versionLabel` | persisted audit | 版本级对齐 | 已实现 |
| `expectedVersion.versionNumber` | persisted audit | 版本级对齐（数值） | 已实现 |
| `expectedVersion.overrideUpdatedAt` | 当前 override 行 | 行级漂移判定 | 已实现 |
| `expectedVersion.payloadHash` | normalized payload hash | 内容漂移判定 | 已实现 |
| `baseline.auditAvailability` | audit 查询结果 | 环境降级可解释 | 已实现 |
| `baseline.currentPayloadHash` | helper 计算 | compare token 组成部分 | 已实现 |

## 冲突/漂移拒绝场景表
| 场景 | 触发链路 | 拒绝原因码 | 返回策略 |
| --- | --- | --- | --- |
| compare token 不一致 | 写入 / rollback execute | `compare_token_mismatch` | 409 + 结构化冲突 |
| version label 不一致 | 写入 / rollback execute | `version_label_mismatch` | 409 + 结构化冲突 |
| version number 不一致 | 写入 / rollback execute | `version_number_mismatch` | 409 + 结构化冲突 |
| override updated_at 不一致 | 写入 / rollback execute | `override_updated_at_mismatch` | 409 + 结构化冲突 |
| payload hash 不一致 | 写入 / rollback execute | `payload_hash_mismatch` | 409 + 结构化冲突 |
| execute 未携带 expectedVersion | rollback execute | `rollback_expected_version_required` | 400/拒绝执行 |

## 当前真实状态说明
- compare token 当前基于：`targetKey + auditAvailability + latest version(label/number) + override.updated_at + payloadHash`。
- 已保护链路：
  - `POST /api/settings/templates/overrides`（可选 expectedVersion）
  - rollback preview（返回 baseline）
  - rollback execute（要求 expectedVersion，并做双层校验）
- runtime debug 现状：
  - 新增并发冲突诊断摘要位（从最近审计 diagnostics 中识别）；
  - 若环境无冲突持久化记录，会明确显示“仅 API inline 冲突可见”。
- 尚未实现：
  - 自动 merge
  - 人工 diff 解决工作流
  - 跨配置类型统一冲突编排
