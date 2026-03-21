# Config Timeline & Diff Viewer v1

更新时间：2026-03-22  
关联文档：
- `docs/config-operations-hub-v1.md`
- `docs/persisted-audit-and-version-snapshot-foundation-v1.md`
- `docs/runtime-explain-debug-panel-v1.md`
- `docs/org-config-editor-ui-v1.md`
- `docs/org-config-rollback-v1.md`

## 1. 为什么现在需要 Timeline & Diff Viewer

当前配置治理能力已经具备：
- persisted audit（`org_config_audit_logs`）
- 配置写入治理（hardening/diagnostics/concurrency）
- rollback preview/execute（部分配置域）
- Config Operations Hub 总览

但仍缺少一个统一入口回答“这次到底改了什么”。  
Config Operations Hub 更偏总览信号，Runtime Debug 偏运行态解释，Editor 偏操作入口。  
因此需要一个最小只读的时间线与差异查看器，补齐跨域变更可追溯和可读差异摘要。

## 2. 本次 viewer 的目标用户

- owner：跨配置域回看最近变更与风险
- admin：排查配置异常、核对回滚来源
- manager：只读查看近期变化与差异摘要
- sales/viewer：不提供访问

## 3. 本次 viewer 的展示范围

跨以下 target_type 聚合最近变更：
- `org_template_override`
- `org_settings`
- `org_ai_settings`
- `org_feature_flags`

本次范围仅限 settings 区域只读页面：
- `/settings/config-timeline`

## 4. 本次 viewer 展示哪些信息

- 最近变更时间线（跨配置域）
- 每条记录摘要：`target_type / target_key / action_type / version / created_at / diagnostics`
- rollback source 摘要（如 audit 中可解析）
- 选中记录后的 detail：
  - `before_summary`
  - `after_summary`
  - `snapshot_summary`
  - `diagnostics_summary`
- 结构化 diff 摘要：
  - changed keys
  - added keys
  - removed keys
  - compare source（payload_preview / normalized_payload / summary_object）
  - redacted fields（脱敏路径）
- empty/not_available/degraded/fallback 状态信号

## 5. 本次 viewer 不展示哪些信息

- 可编辑配置能力（本次完全只读）
- 自动 merge 或冲突解决器
- 字段级可视化深度 diff 编辑器
- 批量筛选器/复杂查询器（仅最小可读布局）
- 统一历史回放播放器

## 6. 与 Config Operations Hub / Runtime Debug / Org Config Editor 的关系

- Config Operations Hub：总览“哪里有风险/异常”，并跳转到 Timeline 查看某条变更细节。
- Config Timeline & Diff Viewer：回答“某条变更具体改了什么（摘要级）”。
- Runtime Debug：回答“当前运行时配置从哪来、为何忽略”。
- Org Config Editor / Template Override Editor：执行写入、预览、冲突处理、回滚。

即：Hub 看全局，Timeline 看变更详情，Debug 看运行态来源，Editor 做操作。

## 7. 权限边界

- API：`assertOrgManagerAccess`
- 页面准入：`canViewManagerWorkspace`
- owner/admin/manager 可访问
- sales/viewer 不可访问
- 未新增散落角色判断

## 8. 风险与后续扩展建议

### 当前风险/限制

1. 结构化 diff 基于 persisted audit 摘要字段，不是全量配置对象 diff。  
2. 某些旧记录 before/after 信息不足时，只能显示 `summary_only/not_available`。  
3. 对敏感字段采用路径规则脱敏，仍属于“摘要安全展示”，不是完整数据分级体系。  
4. 时间线当前为最近窗口（默认 20 条），不是完整历史检索系统。  

### 后续建议

1. 增加最小筛选（target_type/action_type/time range）。  
2. 增加字段级 diff 详情（在审计字段完整后逐步接入）。  
3. 增加跨域统一历史时间线与回放视图。  
4. 增加可链接到具体 rollback preview 的深链参数。  

## 时间线展示字段清单

| 字段 | 含义 | 来源 | 当前状态 |
| --- | --- | --- | --- |
| `id` | audit 记录 ID | `org_config_audit_logs` | 已展示 |
| `targetType` | 配置域类型 | `target_type` | 已展示 |
| `targetKey` | 配置目标 key | `target_key` | 已展示 |
| `actionType` | 变更动作 | `action_type` | 已展示 |
| `versionLabel` | 版本标签 | `version_label` | 已展示 |
| `versionNumber` | 版本号 | `version_number` | 已展示 |
| `createdAt` | 变更时间 | `created_at` | 已展示 |
| `actorUserId` | 操作人 | `actor_user_id` | 已展示 |
| `diagnosticsPreview` | 诊断摘要（截断） | `diagnostics_summary.diagnostics` | 已展示 |
| `runtimeImpactSummary` | runtime 影响摘要 | `diagnostics_summary.runtimeImpactSummary` | 已展示 |
| `rollbackSource` | 回滚来源摘要（可选） | `diagnostics_summary.rollbackSource` / `snapshot_summary.rollbackSource` | 条件展示 |
| `availability` | 该记录 diff 可用性 | diff 计算结果 | 已展示 |

## Diff 查看能力与限制清单

| 能力项 | 本次支持情况 | 说明 |
| --- | --- | --- |
| changed/added/removed key 摘要 | 支持 | 由 before/after 可比较载荷计算 |
| compare source 标记 | 支持 | `payload_preview` / `normalized_payload` / `summary_object` |
| before/after/snapshot/diagnostics 详情查看 | 支持 | 均为只读摘要展示 |
| 敏感字段脱敏 | 支持 | 命中 `secret/token/password/apiKey/accessKey/credential` 路径即脱敏 |
| 缺字段时降级显示 | 支持 | `summary_only` 或 `not_available`，并给出说明 |
| 字段级逐项 rich diff | 不支持 | 当前仅摘要差异 |
| 自动 merge / 回滚执行 | 不支持 | 继续在 Editor/rollback 入口处理 |
| 批量时间线筛选 | 不支持 | 当前仅最近窗口展示 |

## 当前真实状态说明

- 本次 viewer 能看清：跨 4 类配置域最近变更、每条变更的 before/after/snapshot/diagnostics 摘要、结构化 key-level diff 摘要。  
- 仍需去子页面查看：
  - Runtime 来源细节：`/settings/runtime-debug`
  - 实际编辑/冲突处理/回滚执行：`/settings/org-config`、`/settings/templates`
- 尚未实现的高级能力：
  - 字段级逐项 rich diff
  - 批量筛选与查询
  - 统一历史回放视图

