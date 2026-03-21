# MOY org_template_overrides 回滚（dry-run + guarded execution）v1

更新时间：2026-03-21  
关联文档：
- `docs/persisted-audit-and-version-snapshot-foundation-v1.md`
- `docs/org-override-write-path-governance-v1.md`
- `docs/runtime-explain-debug-panel-v1.md`
- `docs/runtime-config-explain-and-override-hardening-v1.md`
- `docs/role-and-permission-model-v1.md`

## 1. 为什么先做 override rollback
- `org_template_overrides` 已经是高风险配置写路径，且已接入 persisted audit + version snapshot，具备先落地回滚闭环的条件。
- 相比一次性做“全配置通用回滚平台”，先把单一高风险对象做成可预览、可拒绝、可审计，更符合当前小步迭代策略。
- 先从 override 回滚起步，可以直接降低“误写后难恢复、难追责、难诊断”的运营风险。

## 2. 当前已具备的基础（audit/version/snapshot）
- 已有持久化审计表：`org_config_audit_logs`。
- 已有版本字段：`version_number` + `version_label`（按 `org + target_type + target_key` 递增）。
- 已有 snapshot 结构：`snapshot_summary` 中可读取 normalized payload 摘要。
- 已有 override hardening：`prepareOrgTemplateOverrideWrite(...)`。
- 已有 runtime debug 聚合：可读取最近 persisted audit 摘要。

## 3. 本次 rollback 的边界
- 只覆盖：`org_template_overrides`。
- 支持两步闭环：
  - rollback preview / dry-run
  - guarded rollback execution
- 不做通用全配置回滚引擎，不覆盖 `org_settings/org_ai_settings/org_feature_flags/org_template_assignments`。
- 不做复杂配置中心 UI；仅在现有 runtime debug 面板补“最近 rollback 摘要”只读展示。

## 4. dry-run 应展示什么
- 回滚目标版本信息（auditId/versionLabel/versionNumber/createdAt/actionType）。
- 当前值摘要（当前 override 是否存在、当前 payload key 摘要）。
- 目标值摘要（目标版本恢复 payload 摘要）。
- 预计恢复字段（acceptedFields）。
- hardening 结果（acceptedForRuntime/forbiddenForRuntime/ignoredFields/runtimeImpactSummary）。
- 是否允许执行（`canExecute`）。
- 明确拒绝原因与诊断（`reason` + `diagnostics`）。

### rollback preview 字段建议表
| 字段 | 说明 | 本次状态 |
| --- | --- | --- |
| `status` | `allowed / rejected / not_available` | 已实现 |
| `canExecute` | 当前 preview 是否允许执行回滚 | 已实现 |
| `reason` | 拒绝/不可用主原因 | 已实现 |
| `diagnostics` | 结构化诊断列表 | 已实现 |
| `request.orgId/templateId/overrideType` | 回滚目标定位 | 已实现 |
| `targetVersion.auditId/versionLabel/versionNumber` | 将恢复到哪个版本 | 已实现 |
| `currentValue.summary` | 当前值摘要 | 已实现 |
| `targetValue.summary` | 目标值摘要 | 已实现 |
| `restorePlan.acceptedFields` | 预计恢复字段集合 | 已实现 |
| `restorePlan.ignoredFields` | hardening 将忽略的字段 | 已实现 |
| `restorePlan.forbiddenForRuntime` | 是否命中 forbidden 核心语义 | 已实现 |
| `restorePlan.runtimeImpactSummary` | runtime 影响摘要 | 已实现 |

## 5. guarded execution 的策略
- 执行前必须重新做 preview（不信任客户端传入）。
- 执行时再次走现有写路径 hardening（通过 `upsertOrgTemplateOverride` 内部治理链路）。
- 本版执行守卫（v1）：
  - 必须 `acceptedForWrite = true`
  - 必须 `acceptedForRuntime = true`
  - 必须 `forbiddenForRuntime = false`
  - 必须 `ignoredFields.length = 0`（禁止“部分静默恢复”）
- 执行成功后：
  - 写回 `org_template_overrides`
  - 产生新的 persisted audit，`action_type = rollback`
  - 记录 `rollbackSource`（source audit/version）到审计摘要

## 6. 权限边界
- rollback preview：复用 manager workspace 可见边界（owner/admin/manager 可预览）。
- rollback execute：仅 owner/admin（复用 `assertOrgAdminAccess`）。
- sales/viewer：不可预览、不可执行。
- 不新增散落角色判断，全部复用现有 capability/helper。

## 7. rollback 审计如何记录
- 审计落点：`org_config_audit_logs`。
- 动作类型：`action_type = rollback`。
- 仍保留 before/after/diagnostics/version/snapshot 基础字段。
- 新增回滚来源元信息（结构化）：
  - `diagnostics_summary.rollbackSource`
  - `snapshot_summary.rollbackSource`
- 该元信息至少包含：
  - sourceAuditId
  - sourceVersionLabel
  - sourceVersionNumber
  - previewGeneratedAt

## 8. 哪些情况拒绝 rollback
- 目标选择器缺失（未提供 auditId/versionLabel/versionNumber）。
- 目标版本不存在。
- 审计表不可用（环境未执行 migration 或无表）。
- 目标 payload 无法提取（snapshot 不完整且 after_summary 不可解析）。
- 目标 payload 被 hardening 拒绝。
- 命中 forbidden 核心语义覆盖。
- 命中 ignored fields（会产生非无损恢复）；
- 非 runtime override（本版执行策略不支持）。

### rollback 拒绝条件与处理策略表
| 拒绝条件 | 触发阶段 | 返回策略 | 说明 |
| --- | --- | --- | --- |
| `rollback_selector_required` | preview | `status=rejected` + diagnostics | 明确要求版本选择器 |
| `rollback_target_version_not_found` | preview | `status=rejected` + diagnostics | 目标审计记录不存在 |
| `org_config_audit_logs_not_available` | preview | `status=not_available` | 环境缺表时优雅降级 |
| `rollback_target_payload_missing` | preview | `status=rejected` | 无可恢复 payload，拒绝执行 |
| `rollback_target_payload_rejected_by_hardening` | preview/execute | `status=rejected` | hardening 校验失败 |
| `rollback_forbidden_core_semantic_override` | preview/execute | `status=rejected` | 核心语义覆盖不允许执行回滚 |
| `rollback_requires_lossless_payload_restore` | preview/execute | `status=rejected` | 存在 ignored fields，避免部分静默恢复 |
| `rollback_non_runtime_override_not_supported` | preview/execute | `status=rejected` | v1 仅支持 runtime override 执行 |
| `org_admin_access_required` | execute API | HTTP 403 | 仅 owner/admin 可执行 |

## 9. 当前不做的内容
- 不做批量回滚。
- 不做跨配置类型（org_settings/org_ai_settings 等）回滚。
- 不做通用 diff UI。
- 不做并发冲突检测（如“预览后目标版本被覆盖”校验）。
- 不做自动回滚编排（策略引擎）。

## 10. 后续如何演进到更通用的 config rollback
1. 扩展目标类型：将相同 preview/execution 模型接入 `org_settings/org_ai_settings/org_feature_flags`。
2. 增加并发防护：执行时校验当前版本基线，避免过期 preview 执行。
3. 增加历史 diff 视图：基于 snapshot_summary 生成字段级对比。
4. 增加批量/事务回滚：支持按 target group 回滚并保证原子性。
5. 增加 API/UI 引导：在 settings 配置中心提供“预览 -> 二次确认 -> 执行 -> 审计追踪”统一流程。

## 11. 当前真实状态说明（落地现状）
- rollback preview 粒度：已到字段级摘要（accepted/ignored/forbidden + diagnostics）。
- rollback execution 范围：当前仅允许无损且 runtime 可消费的 `org_template_overrides` 回滚执行。
- runtime debug 可见性：已增加最近 rollback persisted 摘要块（最多最近 5 条）。
- 仍未实现：批量、跨配置类型、可视化 diff、回滚冲突检测与真正通用回滚平台。
