# MOY 持久化审计与版本快照基础 v1

更新时间：2026-03-21  
关联文档：
- `docs/org-override-write-path-governance-v1.md`
- `docs/runtime-config-explain-and-override-hardening-v1.md`
- `docs/runtime-explain-debug-panel-v1.md`
- `docs/role-and-permission-model-v1.md`

## 1. 为什么现在要做 persisted audit
- 当前模板/组织配置已经进入“运行时可解释 + override 硬化”阶段，但高风险写路径仍存在“写了什么、谁写的、对 runtime 有何影响”难追溯问题。
- 仅有 `auditDraft` 或 evented 日志时，线上排障和责任追踪依赖请求链路上下文，无法稳定做历史检索。
- 为后续 rollback 做准备，必须先具备最小、结构化、可查询的持久化审计与版本编号。

## 2. 当前 draft/evented 审计的局限
- `auditDraft` 只存在于接口返回/临时快照，无法保证长期留存。
- evented 日志（如 console）缺少稳定索引与查询条件，不适合产品内“最近变更”展示。
- 无统一 `version_number / version_label` 时，回滚只能人工拼装 payload，风险高且不稳定。

## 3. 本次持久化范围
- 优先覆盖：`org_template_overrides` 写路径（`upsertOrgTemplateOverride`）。
- 已接入：
  - 写前 hardening/分类（延续既有治理）
  - 写后 persisted audit record（新增）
  - version/snapshot 结构化记录（新增）
  - runtime debug 读模型可读取最近若干条 persisted 摘要（新增）
- 未扩展到完整配置中心 UI，也未实现 rollback 执行器。

## 4. 推荐的最小数据模型
- 新增表：`public.org_config_audit_logs`
- 核心字段：组织、操作者、目标对象、动作、before/after 摘要、diagnostics 摘要、版本号/版本标签、快照摘要、创建时间。
- 版本策略：按 `org_id + target_type + target_key` 维度递增 `version_number`，并生成 `version_label`。
- 快照策略：本次采用“快照嵌入审计记录（snapshot_summary jsonb）”，不额外建独立 snapshot 表。

### 持久化审计字段建议表
| 字段 | 说明 | 本次状态 |
| --- | --- | --- |
| `id` | 审计记录主键 | 已持久化 |
| `org_id` | 组织 ID | 已持久化 |
| `actor_user_id` | 操作者用户 ID | 已持久化 |
| `target_type` | 目标类型（当前重点 `org_template_override`） | 已持久化 |
| `target_id` | 目标记录 ID（可空） | 已持久化 |
| `target_key` | 目标稳定键（如 `templateId:overrideType`） | 已持久化 |
| `action_type` | 操作类型（如 create/update） | 已持久化 |
| `before_summary` | 变更前摘要（非全量大 payload） | 已持久化 |
| `after_summary` | 变更后摘要（非全量大 payload） | 已持久化 |
| `diagnostics_summary` | 诊断摘要（含 runtime impact/ignored/forbidden） | 已持久化 |
| `version_number` | 版本号（递增） | 已持久化 |
| `version_label` | 版本标签（可读） | 已持久化 |
| `snapshot_summary` | 版本快照摘要（结构化） | 已持久化（嵌入式） |
| `created_at` | 写入时间 | 已持久化 |

## 5. 审计记录应包含哪些字段
- 最小必需：
  - `org_id`, `actor_user_id`, `target_type`, `target_id/target_key`, `action_type`
  - `before_summary`, `after_summary`
  - `diagnostics_summary`
  - `version_number`, `version_label`, `created_at`
- 设计原则：
  - before/after 记录“可回放所需的最小摘要”，不追求存储超大原始 payload。
  - diagnostics 保留 runtime 影响语义（consumed/ignored/rejected），便于调试面板快速定位。

## 6. 版本快照与审计记录的关系
- 审计记录：回答“谁在什么时候改了什么，以及改动诊断结论”。
- 版本快照：回答“该次变更对应的可回放配置基线是什么”。
- 本次实现方式：`snapshot_summary` 嵌入在 `org_config_audit_logs` 每条记录中；不独立建 snapshot 表。

### 版本快照字段建议表
| 字段 | 说明 | 本次状态 |
| --- | --- | --- |
| `snapshot_type` | 快照类型（如 `org_template_override_normalized_payload_v1`） | 已实现（嵌入） |
| `target_type` | 快照目标类型 | 已实现（嵌入） |
| `target_key` | 快照目标键 | 已实现（嵌入） |
| `payload_summary` | 可回放所需最小 payload 摘要 | 已实现（嵌入） |
| `runtimeImpactSummary` | runtime 影响结论 | 已实现（嵌入） |
| `acceptedForRuntime` | 是否进入 runtime | 已实现（嵌入） |
| `ignoredByRuntime` | 是否被 runtime 忽略 | 已实现（嵌入） |
| `forbiddenForRuntime` | 是否核心语义禁改 | 已实现（嵌入） |
| `diagnostics` | 诊断列表 | 已实现（嵌入） |

## 7. 本次覆盖哪些写路径
- 已覆盖：
  - `services/industry-template-service.ts` 的 `upsertOrgTemplateOverride`
  - `app/api/settings/templates/overrides/route.ts`（返回 persistedAudit 结果）
  - `services/template-application-service.ts`（`override_write_governance` 快照中追加 persisted audit 摘要）
- 已接读模型：
  - `services/runtime-explain-debug-service.ts` 读取最近 5 条 `org_config_audit_logs`
  - `/settings/runtime-debug` 只读展示“Recent Persisted Config Audit Logs”

## 8. 哪些写路径暂不覆盖
- `org_settings` 写路径
- `org_ai_settings` 写路径
- `org_feature_flags` 写路径
- `org_template_assignments` 独立写路径
- `automation_rules` 配置写路径

暂未覆盖原因：本次优先高风险且已具备 hardening 的 `org_template_overrides`，其他写路径后续按同一 builder/writer 模式接入。

## 9. 后续如何演进到 rollback
1. 扩展覆盖范围：把 `org_settings/org_ai_settings/org_feature_flags/org_template_assignments` 统一接入同一审计写入器。  
2. 增加回滚候选查询：按 `target_type + target_key + version_number` 返回可回滚版本链。  
3. 增加 rollback 执行 service：基于 `snapshot_summary.payload_summary` 做受控重放（仍需权限与二次确认）。  
4. 增加冲突防护：回滚前校验“当前版本是否仍为预期版本”。  
5. 增加审计闭环：回滚动作本身也写入 `org_config_audit_logs`。

## 10. 风险与限制
- 本次是“最小基础”：仅覆盖 override 关键写路径，不是全量配置审计。
- `snapshot_summary` 为嵌入式，不是独立 snapshot 表；后续如需复杂 diff/回滚图谱，可能需要独立结构。
- 某些旧环境若未执行 migration，会回退到 `not_available`（写路径不因缺表中断业务写入）。
- 目前无 rollback 执行 API/UI；本次仅提供回滚准备数据，而非回滚能力本身。

## 11. 当前真实状态声明
- 已 persisted：
  - `org_template_overrides` 关键写路径审计记录（含 version/snapshot）
  - runtime debug 可读最近 persisted 摘要
- 仍是 draft/evented：
  - 非 override 的组织配置写路径
  - 完整历史 diff 查询与跨对象版本链
- 版本快照形态：
  - 本次为“嵌入 audit 的结构化 snapshot_summary”
