# 交付说明：通用任务「计划生成 → 确认 → 执行」（矩阵第 2 项，2026-11-06）

对应 [gap-matrix-2026-11-03.md](file:///c:/Users/15864/Desktop/麦子/docs/gap-matrix-2026-11-03.md) §3.1 的准入基线第 2 项（B1）。
实施计划：[plan-b2-task-plan-confirm.md](file:///c:/Users/15864/Desktop/麦子/.trae/documents/plan-b2-task-plan-confirm.md)。

**矩阵第 2 项状态：已完成（2026-11-06，默认关闭，需显式开启）。**

---

## 1. 交付内容

| 能力 | 行为 | 默认开关 |
| --- | --- | --- |
| 计划任务创建 | `POST /agent/tasks` 带 `plan_first=true` → 任务进入 `waiting_plan`，**不推进任何步骤、不占额度、不调用工具**；开关开启时缺省（不传）同样走计划模式 | `OA_TASK_PLAN_ENABLED=false`（默认关） |
| 计划读取 | `GET /agent/tasks/{task_id}` 返回 `plan`（步骤 / 所需工具 / 预计产物 / 必做与否 / 说明）+ `plan_version` / `plan_expires_at` / `plan_confirmed_at` | 跟随后端开关 |
| 计划编辑（P1） | `PUT /agent/tasks/{task_id}/plan`：勾选 / 取消勾选可跳过步骤；取消 = 置 `skipped`，**不执行** | 同上 |
| 计划确认（P2） | `POST /agent/tasks/{task_id}/plan/confirm`：确认后才进入 `queued` / `running` 并开始执行 | 同上 |
| 放弃 / 可中断 | 既有 `POST /agent/tasks/{task_id}/cancel` 对 `waiting_plan` 直接取消（无执行进程可停） | 无 |
| 超时清理 | 超过 `OA_TASK_PLAN_TIMEOUT_SECONDS`（默认 900 秒）仍未确认 → 后台清理器置 `cancelled`（`error_message=计划已过期，未执行`），**不计入用户主动取消** | 同 `OA_TASK_PLAN_ENABLED` |
| 前端交互 | 任务详情页 `waiting_plan` 时展示计划面板：步骤勾选、工具/预计产物标签、必做步骤置灰、确认截止倒计时、保存调整 / 确认并执行 / 放弃本次 | 跟随后端字段 |

**明确不支持**：修改步骤顺序、新增自定义步骤（那等于让前端写编排 DSL）；计划阶段执行任何工具（含"预检"类调用）。

---

## 2. 接口与数据模型变更（只增不改）

| 变更 | 位置 | 兼容性 |
| --- | --- | --- |
| `CreateTaskRequest.plan_first?: bool \| null` | `app/schemas/agent.py` | 可选；`null`（缺省）= 跟随服务端开关 |
| `TaskStatus.WAITING_PLAN` | 同上 | 加性枚举值；旧前端未识别时按状态兜底文案展示 |
| `TaskDetailData` 追加 `plan_version` / `plan_expires_at` / `plan_confirmed_at` / `plan` | 同上 | 均带默认值（`0` / `""` / `null`） |
| `PUT /api/v1/agent/tasks/{task_id}/plan` | `app/api/v1/agent.py` | 新增端点（P1），幂等键必需 |
| `POST /api/v1/agent/tasks/{task_id}/plan/confirm` | 同上 | 新增端点（P2），幂等键必需 |
| `TaskPlan` / `TaskPlanStep` 等 5 个模型 | `app/schemas/task.py` | 新增模型，无既有调用方受影响 |
| `plugin_task` 增三列 + 状态 CHECK 扩 `waiting_plan` + 部分索引 | `deploy/migrations/0014_task_plan.sql` | 迁移带幂等守卫；回滚 `rollback/0014_task_plan.down.sql` |

**RLS**：不新增表、不改策略，`plugin_task` 既有 `p_tenant_isolation` 继续生效；`validate_schema_retention.py` 口径不变（新列不参与保留期裁剪）。

**状态机与幂等**：

- `waiting_plan` **不在** `_LIVE_TASK_STATUSES` → 既有 `advance` 的条件写永远抢不到该状态，从机制上保证确认前零执行；
- 编辑与确认均使用 `expected_status + expected_plan_version` 条件写：版本落后 → 20005「计划已变更，请刷新」；
- 确认幂等：`plan_confirmed_at` 已存在 → 返回 `already_confirmed=true`，不再推进；
- 清理幂等：重复扫描只对仍为 `waiting_plan` 且已过期的行做条件写，第二轮 0 变更；
- 确认后若同插件并发额度已满 → `queued`（既有 `promote_queued` 接管），进程重启不丢计划（已落库）。

---

## 3. 验证边界（**必读，不许把 CI 绿当功能已验证**）

| 环节 | 验证程度 | 说明 |
| --- | --- | --- |
| 计划投影 / 可跳过判定 / 编辑校验 / 确认幂等 / 懒过期 | ✅ 已覆盖 | `apps/adapter/tests/unit/test_task_plan.py` |
| HTTP 全链路（创建 → 读取 → 编辑 → 确认 → 只执行选中步骤） | ✅ 已覆盖 | `apps/adapter/tests/integration/test_task_plan_api.py`（开关开启） |
| 确认前零副作用 | ✅ 已覆盖 | 断言替身网关 `EchoGateway.calls == []`；确认后才 >0 |
| 超时清理路径 | ✅ 已覆盖 | sweeper 过期清理 + 重复扫描幂等；系统超时不置 `cancel_requested`，保持完成率口径 |
| 前端计划面板交互 | ✅ 已覆盖 | `apps/web/src/components/workflow/task-plan-panel.test.tsx` |
| **真实第三方工具副作用** | ❌ **未验证** | CI 全程使用替身网关，未真实调用飞书/邮件等外部服务；部署环境需按第 5 节验收 |
| 多副本清理器竞争 | ⚠️ 逻辑验证 | 条件写保证只有一个副本生效；未做多副本真机压测 |

结论：**代码链路与状态机已由测试锁定；真实外部工具的副作用与多副本并发需按第 5 节在部署环境验收。**

---

## 4. 回退方式（出事 1 分钟内可退回）

| 现象 | 处置 |
| --- | --- |
| 计划确认异常 / 面板异常 | `.env` 置 `OA_TASK_PLAN_ENABLED=false` 并重启 adapter → 显式 `plan_first=true` 返回 20001，既有创建路径完全不变 |
| 已存在的 `waiting_plan` 需要立即放行 | 打开开关后由用户确认；或直接把任务 `cancel`（不影响存量任务） |
| 迁移需要回滚 | 执行 `deploy/migrations/rollback/0014_task_plan.down.sql`：先取消全部 `waiting_plan` → 还原旧 CHECK → 删索引 → 删三列 |

> 开关只影响**新建的计划路径**，不回滚既有任务创建/执行/取消/重试链路。

---

## 5. 部署后 5 分钟手工验收

前置：`.env` 设 `OA_TASK_PLAN_ENABLED=true`、`OA_TASK_PLAN_TIMEOUT_SECONDS=900`，重启 adapter。

### 5.1 创建计划任务（约 1 分钟）

1. 在 Web 端发起一个**飞书日程**插件任务（开关开启后缺省即计划模式；也可显式带 `plan_first=true`）。
2. 预期：任务详情页顶部出现**计划面板**，列出步骤、所需工具、预计产物、确认倒计时；任务状态为「计划待确认」。

### 5.2 验证确认前零执行（约 2 分钟）

1. 在飞书侧确认**没有**新建任何日程 / 没有收到任何外部消息。
2. 在面板里取消勾选一个**可跳过**步骤（必做步骤应置灰），点「保存调整」→ 面板刷新且该步骤标注「未纳入本次计划」。
3. 刷新页面：计划仍在、版本号 +1、倒计时刷新；任务状态仍为 `waiting_plan`。

### 5.3 确认并执行（约 1 分钟）

1. 点「确认并执行」。
2. 预期：状态进入「排队中 / 执行中」，随后按**选中步骤**执行；被取消勾选的步骤显示 `skipped`，且不产生外部副作用。
3. 再次点确认（或刷新后重复调用 P2）：返回 `already_confirmed=true`，**不会二次执行**。

### 5.4 放弃与超时（约 1 分钟）

1. 重新创建一个计划任务，点「放弃本次」→ 状态 `cancelled`，无任何步骤执行。
2. 再创建一个计划任务，把 `OA_TASK_PLAN_TIMEOUT_SECONDS` 临时改为 60 并重启，等待 60 秒 → 状态自动变为 `cancelled`，错误信息为「计划已过期，未执行」。

### 5.5 判定

| 结果 | 结论 |
| --- | --- |
| 5.1–5.4 全部符合 | 计划确认能力验收通过，可对外说明「先出计划再执行」 |
| 确认前已产生外部副作用 | **严重缺陷**：立即关闭开关并排查 `waiting_plan` 是否误入 `_LIVE_TASK_STATUSES` |
| 确认后重复执行 | 确认幂等失效：检查 `plan_confirmed_at` 条件写与 API 幂等键 |
| 超时不清理 | 检查清理循环是否随 `task_plan_enabled` 启动（`app/main.py`） |

---

## 6. 残余风险与取舍

| 项 | 说明 |
| --- | --- |
| 计划不落快照 JSON | 计划由 `plugin_step` + manifest 投影；插件升级后新建任务用新声明，存量任务的插件版本已固化，投影稳定 |
| 编辑能力有限 | 只支持勾选/取消勾选，不支持排序/自定义步骤（避免前端承担编排 DSL） |
| 默认关闭 | 关闭时与旧行为完全一致；需要产品侧显式开启后才对用户可见 |
| 超时默认 900 秒 | 与既有任务超时口径一致；过短会让用户来不及阅读计划，过长会让挂起任务堆积 |
| 清理器单进程扫描 | 条件写保证多副本安全，但每副本都会扫；租户数量极大时需改为分片（当前量级不触发） |

---

## 7. CI 记录（同 SHA 双绿）

| 项 | 值 |
| --- | --- |
| 功能提交 | 待提交后回填 |
| 收口 SHA | 待回填 |
| CI Adapter | 待回填 |
| CI Web | 待回填 |

**CI 能证明与不能证明的**：以上 run 只证明「代码 / 契约 / 门禁一致」；CI 中的工具调用全部指向
替身网关，**不能证明真实第三方工具副作用可用**——后者只由第 5 节部署验收判定。
