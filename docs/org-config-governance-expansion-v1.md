# org config governance expansion v1

## 1. 为什么现在要把治理扩到其他 org 配置

`org_template_overrides` 已经具备了最小治理闭环（写前 hardening、diagnostics、persisted audit、并发漂移保护、debug 可见）。  
但 `org_settings`、`org_ai_settings`、`org_feature_flags` 仍存在能力不对齐，导致：

- 写入成功但运行时只消费部分字段，调用方看不出“哪些生效、哪些只是存储”
- 不同配置写入口各自校验，行为不一致
- 并发写覆盖缺少明确 compare baseline
- runtime debug 对三类 org 配置治理状态缺少单独摘要

本次目标是让这三类配置在 service/API/debug 层具备可复用的最小一致模式，而不是立即做完整配置中心。

## 2. 当前 org_template_overrides 已具备哪些能力

- 写前：`template-override-hardening` + `org-override-write-governance`
- 写时：`diagnostics`（accepted / ignored / forbidden / runtimeImpact）
- 写后：`org_config_audit_logs` persisted audit + version label
- 并发：`expectedVersion / compareToken` + drift 409
- 调试：runtime debug 可看 recent persisted audits / rollback / conflict signal

## 3. 三类配置差异点

- `org_settings`
  - 字段混合了组织展示信息与运行时偏好字段
  - 运行时实际消费字段有限（例如告警阈值、SLA、onboarding step state）
- `org_ai_settings`
  - 包含模型/开关/额度等多类字段，运行时桥接只消费部分
- `org_feature_flags`
  - 本质是 key-value 布尔开关，天然适合做严格 key 白名单和 drift guard

## 4. 本次统一到哪些层

- `lib`：新增统一写前 hardening/helper（`org-config-write-governance.ts`）
- `services`：新增治理编排服务（`org-config-governance-service.ts`）
- `audit`：复用 `org_config_audit_logs`，并补通用 latest version 查询
- `concurrency`：复用并扩展 compare baseline helper 到 org config 场景
- `api`：接入代表性写入口
  - `POST /api/settings/org`
  - `POST /api/settings/ai`
- `runtime debug`：新增 org config 治理摘要区块

## 5. 哪些能力已真正统一

- 三类配置统一具备：
  - 写前 accepted / ignored / forbidden / diagnostics 结构
  - runtime impact summary（最小版）
  - persisted audit 写入（`target_type` 区分）
  - optimistic concurrency expectedVersion baseline
  - 409 冲突结构化返回（`currentVersion / expectedVersion / diagnostics`）
  - runtime debug 可见最近治理摘要（按 target_type 聚合）

## 6. 哪些能力仍未统一

- rollback 仍仅对 `org_template_overrides` 落地，三类 org 配置未做 rollback execute
- 三类 org 配置暂无独立编辑 UI（本次仅 service/API/debug 层）
- 并发冲突后仍是“刷新重试”，未做自动 merge/diff 解决
- 历史变更检索仍为“最近若干条摘要”，未做完整筛选分页与字段级 diff

## 7. 权限边界

- 写入：继续要求 `assertOrgAdminAccess`（owner/admin）
- 读取 runtime debug：继续要求 `assertOrgManagerAccess`（owner/admin/manager）
- 未新增散落角色判断；沿用现有 capability helper 与 membership 断言

## 8. 风险与后续建议

- 风险
  - 旧调用若提交无效字段，现在会收到更严格 diagnostics/reject
  - `org_settings` 中核心语义字段（stage）出现非核心值时会被硬化拒绝并记录 diagnostics
- 建议
  - 下一步优先补 `org_ai_settings/org_feature_flags` 的只读历史过滤 API（按 target/action）
  - 再补三类 org 配置 rollback preview（先 dry-run，不直接 execute）
  - 最后将 settings 页面逐步迁移到“基于 expectedVersion 的显式提交”

## 三类 org 配置治理能力对齐表

| 配置类型 | 写前 hardening | diagnostics | persisted audit | optimistic concurrency | runtime debug 摘要 |
| --- | --- | --- | --- | --- | --- |
| `org_settings` | 已接入（白名单 + 类型约束 + 核心语义值检查） | 已接入（accepted/ignored/forbidden/runtimeImpact） | 已接入（`target_type=org_settings`） | 已接入（expectedVersion + compareToken） | 已接入 |
| `org_ai_settings` | 已接入（白名单 + 类型范围约束） | 已接入 | 已接入（`target_type=org_ai_settings`） | 已接入 | 已接入 |
| `org_feature_flags` | 已接入（feature key 白名单 + bool 校验） | 已接入 | 已接入（`target_type=org_feature_flags`） | 已接入 | 已接入 |

## 仍待统一的能力清单

| 能力 | 当前状态 | 原因 | 建议后续接入点 |
| --- | --- | --- | --- |
| 三类 org 配置 rollback execute | 未实现 | 本次范围聚焦写入治理一致性，避免一次性扩展回滚执行面 | `org-config-governance-service.ts` 增补 preview/execute |
| 三类 org 配置独立编辑 UI | 未实现 | 本次明确不做完整编辑器 | 复用 `settings/*` 页，按 target_type 分步接入 |
| 字段级 diff/冲突可视化 | 未实现 | 当前只返回结构化冲突，不做复杂前端 merge | 在 settings 客户端增加 conflict panel |
| 审计分页与高级筛选 | 部分实现 | 当前仅最近 N 条摘要读取 | 在 runtime debug API 增补 filter/pagination |

