# Config Operations Hub v1

## 1. 为什么现在需要 Config Operations Hub

当前配置治理能力已经分散落在多个入口：
- Runtime Explain Debug（读路径与来源解释）
- Org Config Editor（org_settings / org_ai_settings / org_feature_flags）
- Template Override Editor（org_template_overrides）
- Persisted audit 与 rollback 相关能力

在治理能力逐步补齐后，owner/admin/manager 需要一个统一总览台，快速回答以下问题：
- 最近哪些配置改过
- 哪些变更带有 ignored/forbidden/diagnostics
- 最近是否出现并发冲突信号
- 哪些配置当前支持 rollback
- 当前是否处于 fallback/not_available/degraded 状态

本次 Hub 不替代现有子页面，只提供统一可读入口和跳转。

## 2. 本次 Hub 的目标用户

- owner：查看全局配置治理状态
- admin：查看全局配置治理状态与异常
- manager：只读查看治理摘要、风险信号与趋势入口
- sales/viewer：不提供访问

## 3. 本次 Hub 的展示范围

- 跨配置域最近 persisted audit 摘要：
  - `org_template_override`
  - `org_settings`
  - `org_ai_settings`
  - `org_feature_flags`
- runtime explain 的关键运行态摘要
- 轻量治理健康计数（ignored/forbidden/conflict/rollback/fallback）
- 配置域入口卡片（Template Override / Org Config / Runtime Debug）
- fallback/not_available/degraded 只读告警

## 4. 本次 Hub 展示哪些信息

| 信息块 | 数据来源 | 本次展示内容 | 说明 |
|---|---|---|---|
| 最近配置变更 | `org_config_audit_logs`（通过 `listRecentOrgConfigAuditLogs`） | `target_type`、`action_type`、`version`、`created_at`、diagnostics 摘要、rollback 支持状态 | 仅最近窗口（默认 12 条） |
| 配置健康摘要 | recent audits + runtime explain | ignored/forbidden 数、conflict 数、recent rollback 数、fallback/not_available 信号数 | 轻量统计，不是 BI 精确报表 |
| Runtime 概览 | `getRuntimeExplainDebugPanelData` | resolved template、resolved mode、ignored override 数、runtime diagnostics 数 | 用于快速判断是否 seed/fallback |
| 配置域入口卡片 | Hub 聚合层 | 各域状态（available/degraded/not_available）、最近变更摘要、跳转链接 | 跳转到详情页继续排查 |
| 异常与限制 | Hub 聚合层 | fallback/not_available/degraded 信号列表、当前能力限制说明 | 明确真实可用性，不伪造历史 |

## 5. 本次 Hub 不展示哪些信息

- 字段级 diff 视图
- 批量回滚执行面板
- 统一全链路配置时间线（跨更多 target type）
- 复杂图表、趋势分析和 SLA 统计
- 完整原始 JSON 明细（Hub 仅展示摘要；深度排查去子页面）

## 6. 与 runtime debug 页、org config editor、template override editor 的关系

Hub 是统一入口，不替代子页面：
- Hub：看全局态势、快速定位异常、快速跳转
- Runtime Debug：看 runtime source explain 细节和消费点解释
- Org Config Editor：写入/预览/冲突处理/rollback（org config 三类）
- Template Override Editor：模板覆盖写入/冲突处理/rollback

## 7. 权限边界

- 页面与 API 均复用 `assertOrgManagerAccess` / `canViewManagerWorkspace`
- owner/admin/manager 可访问 Hub
- sales/viewer 默认不可访问 Hub
- 不新增散落角色判断

## 8. 风险与后续扩展建议

### 当前风险/限制

1. rollback 可用性目前按“目标类型支持 + 最近数据状态”给近似判断，不等于逐版本可执行性保证。  
2. recent audit 窗口是有限条数，健康计数只代表近期信号。  
3. 当 `org_config_audit_logs` 不可用时，Hub 会降级为 `not_available/degraded` 提示，不能展示完整历史。  

### 后续建议

1. 增加统一配置时间线（支持筛选 target_type / action_type / actor）。  
2. 增加字段级 diff 摘要和风险等级分层。  
3. 增加“冲突事件视图”和“回滚成功率”统计。  
4. 在 Hub 中增加可选 drill-down（仍复用子页面能力，不重复实现编辑逻辑）。  

## Hub 信息块清单

| 信息块 | 只读/操作 | 权限 | 主要字段 | 当前状态标识 |
|---|---|---|---|---|
| 最近配置变更总览 | 只读 | manager+ | target_type/action_type/version/diagnostics/rollback support | available/empty/not_available |
| 配置健康摘要 | 只读 | manager+ | ignored/forbidden/conflict/rollback/fallback 计数 | always visible（值可为 0） |
| 配置域入口卡片 | 轻操作（跳转） | manager+ | domain status、summary、latest changed、link | available/degraded/not_available |
| 异常信号区 | 只读 | manager+ | fallback/not_available/degraded 列表 | empty 表示当前未检测到信号 |
| 能力限制说明 | 只读 | manager+ | limitation 文本列表 | always visible |

## Hub 与现有页面职责边界表

| 页面 | 主要职责 | Hub 是否替代 | 典型使用场景 |
|---|---|---|---|
| `/settings/config-ops` | 统一只读总览、健康信号、跨域入口 | 否 | 快速判断“哪里异常、先看哪里” |
| `/settings/runtime-debug` | runtime explain 深度调试与来源解释 | 否 | 排查 fallback 原因、ignored override 细节 |
| `/settings/org-config` | org config 编辑、预览、冲突处理、rollback | 否 | 实际执行 org_settings/org_ai_settings/org_feature_flags 修改 |
| `/settings/templates` | template override 编辑、预览、冲突处理、rollback | 否 | 执行模板覆盖和回滚 |

