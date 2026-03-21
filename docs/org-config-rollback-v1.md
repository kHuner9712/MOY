# org config rollback v1

## 1. 为什么现在要给三类 org config 补 rollback

`org_settings`、`org_ai_settings`、`org_feature_flags` 已经具备：
- 写前 hardening + diagnostics
- persisted audit
- expectedVersion 并发保护
- 最小可用 editor UI

当前缺口是“误写后可恢复能力”。如果只有编辑与审计，没有受控 rollback，运维与管理者仍需要手工回填配置，风险高且效率低。

## 2. 当前已具备的基础

- persisted audit：`org_config_audit_logs` 已覆盖三类配置写入。
- expectedVersion / concurrency：写入链路已支持 compare token / expectedVersion 漂移拒绝。
- editor UI：`/settings/org-config` 已可读、可预览、可提交，并展示最近审计摘要。

## 3. 本次 rollback 的范围边界

- 覆盖范围：仅 `org_settings`、`org_ai_settings`、`org_feature_flags`。
- 提供能力：
  1. rollback preview（dry-run）
  2. guarded rollback execute
  3. rollback persisted audit（`action_type=rollback`）
  4. editor UI 最小回滚入口
- 不做：通用回滚引擎、复杂 diff UI、批量回滚、自动 merge。

## 4. rollback preview 应展示什么

- config type（目标配置类型）
- target version summary（auditId/versionLabel/versionNumber/action/createdAt）
- current value summary（当前值摘要）
- target value summary（目标版本摘要）
- restored field summary（hardening 后可恢复字段）
- diagnostics（包含 ignored/forbidden 等）
- canExecute / reason
- concurrency baseline + expectedVersion

## 5. guarded execution 的策略

- execute 强制要求 `expectedVersion`。
- execute 前会重新跑 preview/hardening，不直接盲写 snapshot。
- execute 前先做漂移校验（preview baseline vs expectedVersion）。
- execute 写入时再次走既有治理写路径（双层保护）。
- 只有在“可无损恢复（无 ignored/forbidden）”时允许执行。

## 6. 权限边界

- preview：`assertOrgManagerAccess`（owner/admin/manager 可看）
- execute：`assertOrgAdminAccess`（仅 owner/admin）
- sales/viewer：不可执行，不可通过 API 绕过
- 页面权限与 API 权限保持一致，不新增散落角色判断

## 7. rollback 审计如何记录

- rollback 执行成功后写入 `org_config_audit_logs`
- `action_type=rollback`
- 保留：
  - before_summary / after_summary
  - diagnostics_summary
  - version_number / version_label
  - snapshot_summary
- 额外记录 rollback source 元信息：
  - sourceAuditId
  - sourceVersionLabel
  - sourceVersionNumber
  - previewGeneratedAt

## 8. 哪些情况拒绝 rollback

- selector 缺失（无 auditId/versionLabel/versionNumber）
- 目标版本不存在
- 审计表不可用（not_available）
- 目标 payload 缺失或不可解析
- hardening 后无有效字段
- 存在 forbidden 字段
- 存在 ignored 字段（v1 要求无损恢复）
- expectedVersion 缺失
- preview 与 execute 间发生 baseline 漂移（409 conflict）

## 9. 哪些配置值应做字段脱敏/摘要化展示

- 所有回滚 preview 仅展示摘要，不直接暴露完整原始 payload。
- 对字段路径命中敏感关键字（`secret/token/password/apiKey/accessKey/credential`）进行脱敏展示。
- 对 `org_ai_settings` 优先应用同一脱敏规则；若审计 payload 内含类似字段，UI 仅显示 `***REDACTED***`。

## 10. 当前不做的内容与后续扩展建议

- 当前不做：
  - 字段级 diff 视图
  - 批量回滚
  - 跨配置类型事务回滚
  - 自动 merge 冲突处理
- 后续建议：
  1. 增加字段级 diff 与回滚前后可视化对比
  2. 增加回滚审批/二次确认策略
  3. 扩展到更多配置类型并支持批量回滚编排

## org config rollback preview 字段建议表

| 字段 | 说明 | 本次状态 |
| --- | --- | --- |
| `targetType` | 回滚目标类型 | 已实现 |
| `status` | `allowed/rejected/not_available` | 已实现 |
| `canExecute` | 当前 preview 是否允许执行 | 已实现 |
| `reason` | 拒绝/不可用原因 | 已实现 |
| `diagnostics` | 结构化诊断列表 | 已实现 |
| `targetVersion.*` | 目标版本摘要 | 已实现 |
| `currentValue.summary` | 当前值摘要（脱敏） | 已实现 |
| `targetValue.summary` | 目标版本值摘要（脱敏） | 已实现 |
| `targetValue.restoredSummary` | hardening 后恢复值摘要（脱敏） | 已实现 |
| `restorePlan.acceptedFields` | 可恢复字段 | 已实现 |
| `restorePlan.ignoredFields` | 将被忽略字段 | 已实现 |
| `restorePlan.forbiddenFields` | 禁止字段 | 已实现 |
| `concurrency.baseline` | preview 基线 | 已实现 |
| `concurrency.expectedVersion` | execute 必带基线 | 已实现 |

## org config rollback 拒绝条件与处理策略表

| 拒绝条件 | 阶段 | 返回策略 | 本次状态 |
| --- | --- | --- | --- |
| selector 缺失 | preview/execute | `status=rejected` + diagnostics | 已实现 |
| 目标版本不存在 | preview | `status=rejected` + diagnostics | 已实现 |
| 审计表不可用 | preview | `status=not_available` | 已实现 |
| payload 缺失 | preview | `status=rejected` | 已实现 |
| hardening 无有效字段 | preview/execute | `status=rejected` | 已实现 |
| 命中 forbidden 字段 | preview/execute | `status=rejected` | 已实现 |
| 命中 ignored 字段（非无损） | preview/execute | `status=rejected` | 已实现 |
| expectedVersion 缺失 | execute | `status=rejected` | 已实现 |
| preview-execute 漂移 | execute | HTTP 409 + conflict 结构化信息 | 已实现 |

## 当前真实状态说明

- 已支持 rollback preview + execute：
  - `org_settings`
  - `org_ai_settings`
  - `org_feature_flags`
- 三类配置都写入 rollback persisted audit（`action_type=rollback`）。
- manager 当前可做 preview，但不可 execute。
- 仍未实现高级能力：字段级 diff、批量回滚、自动 merge、跨配置类型事务回滚。

