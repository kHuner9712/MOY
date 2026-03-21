# org_template_overrides 最小可用编辑 UI（v1）

## 1. 为什么现在适合做最小编辑 UI
- 已有 runtime explain、override hardening、写路径治理、persisted audit、rollback preview/execute、concurrency guard 基础能力。
- 当前缺口主要在“可操作入口”：owner/admin 需要在受控页面完成最小写入和回滚闭环，而不是仅靠 API 调试。
- 先做最小 UI 可以把高风险改动收敛到统一入口，并复用已落地的服务能力，降低散落写路径风险。

## 2. 本次 UI 的目标用户
- owner：可读、可预览、可提交 override、可 rollback preview、可执行 rollback。
- admin：与 owner 一致。
- manager：可读、可预览（治理与 rollback preview），不可执行写入与 rollback execute。
- sales/viewer：无该页面访问能力（沿用现有路径权限控制）。

## 3. 本次 UI 的范围边界
- 仅覆盖 `org_template_overrides`。
- 不做通用配置中心。
- 不做字段级 diff 编辑器。
- 不做批量编辑/批量回滚。

## 4. 页面应展示哪些信息
- 当前模板分配信息与模板详情（沿用现有模板中心）。
- 当前 override 状态（存在性、更新时间、当前 payload）。
- 当前 expectedVersion/concurrency baseline 摘要（compare token、version label/number、payload hash）。
- 当前与预览治理摘要（accepted/ignored/forbidden/rejected、diagnostics）。
- 最近持久化审计摘要列表（按 templateId + overrideType 过滤）。
- rollback preview 结果（allowed/rejected、restore plan、diagnostics、expectedVersion）。
- 冲突返回结构（expected/current/diagnostics）。

## 5. 页面应支持哪些操作
- 读取当前 override 状态。
- 编辑 JSON payload（owner/admin）。
- 写入前治理预览（owner/admin/manager 可看）。
- 带 expectedVersion 的写入提交（owner/admin）。
- 从最近审计记录选择目标并做 rollback preview（owner/admin/manager）。
- 基于 preview 返回 expectedVersion 执行 rollback（owner/admin）。

## 6. 页面不支持哪些操作
- 字段级冲突合并。
- 自动 merge 或自动重试。
- 跨 overrideType 的一键回滚。
- 历史全量 diff 可视化。

## 7. 冲突处理策略
- 提交写入或 rollback execute 遇到 409 时，展示结构化冲突信息：
  - `conflictReason`
  - `expectedVersion` 摘要
  - `currentVersion` 摘要
  - `diagnostics`
- 前端不尝试自动合并；提示“刷新状态 -> 重新预览 -> 再执行”。

## 8. rollback 操作策略
- 目标选择：从最近持久化审计记录中选择单条记录。
- preview 阶段必须走服务端 hardening 与 guard 判定，展示是否可执行及原因。
- execute 阶段必须带 preview 产出的 expectedVersion，继续受 drift protection 约束。
- execute 成功后依赖服务端生成新的 rollback 审计记录，保留历史链路。

## 9. 权限边界
- 页面路由：沿用现有 `/settings/templates` 权限规则（manager 级可访问，sales/viewer 不可访问）。
- 写入 API：`assertOrgAdminAccess`（owner/admin）。
- rollback execute API：`assertOrgAdminAccess`（owner/admin）。
- 只读/预览 API：`assertOrgManagerAccess`（owner/admin/manager）。
- 页面内按钮状态与 API 边界保持一致，不新增散落角色判断。

## 10. 风险与后续扩展建议
- 风险：manager 可见但不可执行，若不了解权限意图，可能误以为按钮失效；已通过只读标识和权限提示缓解。
- 风险：当模板选择为非 uuid（seed fallback）时，override 写路径会退回“当前 active template”语义；页面已显示提示。
- 风险：审计历史仅展示最近若干条摘要，非完整历史浏览器。
- 后续建议：
  - 增加字段级 diff 展示。
  - 增加冲突后“对比当前值”辅助视图。
  - 增加 rollback 历史筛选与分页。
  - 与 runtime debug 页面做深链联动。

## 页面功能块清单
| 功能块 | 说明 | 主要数据来源 | 角色 |
| --- | --- | --- | --- |
| 模板中心基础块 | 模板选择、推荐、预览应用、应用 | `settingsClientService.getTemplateCenter/getTemplateDetail/previewTemplateApply/applyTemplate` | manager+（应用仅 owner/admin） |
| 当前 override 状态 | 当前 payload、基线 token、最近审计摘要 | `getCurrentTemplateOverrideState` | manager+ |
| 写入治理预览 | accepted/ignored/forbidden/rejected 与 diagnostics | `previewTemplateOverrideWrite` | manager+ |
| override 提交 | 带 expectedVersion 的提交 | `executeTemplateOverrideWrite` | owner/admin |
| 冲突提示块 | 409 冲突结构化信息展示 | 写入/回滚执行响应 | manager+（可见） |
| rollback preview | 目标版本预览与可执行性判定 | `previewTemplateOverrideRollback` | manager+ |
| rollback execute | 受控执行回滚并触发审计 | `executeTemplateOverrideRollback` | owner/admin |

## 操作权限矩阵
| 操作 | owner | admin | manager | sales | viewer |
| --- | --- | --- | --- | --- | --- |
| 进入页面 `/settings/templates` | 是 | 是 | 是 | 否 | 否 |
| 查看当前 override 状态 | 是 | 是 | 是 | 否 | 否 |
| 预览 override 写入治理摘要 | 是 | 是 | 是 | 否 | 否 |
| 提交 override 写入 | 是 | 是 | 否 | 否 | 否 |
| rollback preview | 是 | 是 | 是 | 否 | 否 |
| rollback execute | 是 | 是 | 否 | 否 | 否 |
| 查看冲突详情 | 是 | 是 | 是 | 否 | 否 |
