# org config editor ui v1

## 1. 为什么现在适合做最小 Org Config Editor UI

`org_settings`、`org_ai_settings`、`org_feature_flags` 已经在 service/API 层具备最小治理能力（hardening、diagnostics、persisted audit、expectedVersion 并发保护）。  
当前缺口是“可操作入口”：owner/admin 需要在受控页面完成最小编辑与提交，manager 需要只读查看治理摘要与最近变更。

本次补 UI 的目标是把已有治理链路接出来，而不是新建一套配置系统。

## 2. 本次 UI 覆盖范围

- 仅覆盖三类组织配置：
  - `org_settings`
  - `org_ai_settings`
  - `org_feature_flags`
- 仅覆盖 settings 区域下最小入口：
  - `/settings/org-config`
- 不覆盖模板 override 编辑器能力（已在 `/settings/templates`）。

## 3. 本次 UI 的目标用户

- owner/admin：可编辑、可预览、可提交
- manager：只读查看（当前值、expectedVersion、诊断摘要、最近变更）
- sales/viewer：不可用（无页面访问能力）

## 4. 页面应展示哪些信息

- 三类配置当前值（表单化展示）
- 每类配置当前 expectedVersion / compare token 摘要
- 每类配置最近 persisted audit 摘要（action/version/created_at/diagnostics）
- 最近一次诊断摘要（accepted/ignored/forbidden/runtime impact）
- 冲突信息（若发生 409）

## 5. 页面应支持哪些操作

- owner/admin：
  - 预览写入（preview）
  - 带 expectedVersion 提交写入（execute）
- manager：
  - 只读查看状态、预览结果、历史摘要（不提交）

## 6. 页面不支持哪些操作

- rollback（本页不实现）
- 字段级 diff 可视化
- 自动 merge 冲突解决
- 批量编辑/批量回滚

## 7. 冲突处理策略

- execute 返回 409 时，前端展示结构化冲突信息：
  - `conflictReason`
  - `currentVersion`
  - `expectedVersion`
  - `diagnostics`
- 不做自动 merge，仅提示“刷新状态后重新预览并重试”。

## 8. 权限边界

- 页面准入：`canViewManagerWorkspace`（owner/admin/manager）
- 预览/提交：`canManageOrgCustomization`（owner/admin）
- 服务端边界：
  - 读取 state：`assertOrgManagerAccess`
  - 预览 preview：`assertOrgAdminAccess`
  - 提交 execute：复用既有写 API 的 `assertOrgAdminAccess`

页面和 API 权限保持一致，未新增散落角色判断。

## 9. 风险与后续扩展建议

- 风险
  - manager 能看到编辑表单但提交受限，需明确只读提示（本次已在页面提示）。
  - richer 审计字段当前仍是摘要级，无法提供字段级 diff（页面明确“当前不可用”语义）。
- 后续建议
  1. 为三类 org config 增加 rollback preview/execute（先 dry-run）。
  2. 增加字段级 diff 与冲突比较视图。
  3. 在 `/settings/org` 与 `/settings/ai` 页面逐步接入相同 baseline + preview 模式，减少入口分散。

## 页面功能块清单

| 功能块 | 说明 | 数据来源 | 当前状态 |
| --- | --- | --- | --- |
| Access Summary | 展示当前角色与可写能力 | `/api/settings/org-config/state` + capability helper | 已实现 |
| Org Settings Editor | 编辑组织基础配置与告警阈值 | `/api/settings/org-config/state` + `/api/settings/org` | 已实现 |
| Org AI Settings Editor | 编辑模型与 fallback/human review | `/api/settings/org-config/state` + `/api/settings/ai` | 已实现 |
| Org Feature Flags Editor | 编辑功能开关 | `/api/settings/org-config/state` + `/api/settings/ai` | 已实现 |
| Write Preview Panel | 展示 accepted/ignored/forbidden/diagnostics | `/api/settings/org-config/preview` | 已实现 |
| Conflict Banner | 展示 409 冲突结构化信息 | execute 返回 | 已实现 |
| Recent Audit Summary | 展示最近 persisted audit 摘要 | `org_config_audit_logs` 读模型 | 已实现 |

## 操作权限矩阵

| 操作 | owner | admin | manager | sales | viewer |
| --- | --- | --- | --- | --- | --- |
| 进入 `/settings/org-config` | 是 | 是 | 是 | 否 | 否 |
| 查看当前配置与 expectedVersion | 是 | 是 | 是 | 否 | 否 |
| 预览写入诊断 | 是 | 是 | 否 | 否 | 否 |
| 提交写入 | 是 | 是 | 否 | 否 | 否 |
| 查看最近变更摘要 | 是 | 是 | 是 | 否 | 否 |
| 处理冲突并重试 | 是 | 是 | 否 | 否 | 否 |
