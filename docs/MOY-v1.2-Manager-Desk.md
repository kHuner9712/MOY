# MOY v1.2 销售经理作战台

> **历史版本：v1.2** | v1.3 ~ v1.5 均已并行完成 | 基于 v1.1 的增量升级
>
> 产品定位：让销售经理第一次真正能"少催、少猜、少救火"

---

## 1. 目标与原则

**核心目标**：让经理知道今天该盯什么、为什么要盯、怎么介入

**产品原则**：
1. 经理入口必须是"今天该盯哪些单"，不是报表大全
2. 不做重 BI，不做复杂自定义看板
3. 不做老板视角经营分析
4. 不做大规模 schema 重构，优先复用现有能力
5. 输出必须是"可干预"的，而不是"只可查看"的

---

## 2. 经理作战台入口

**入口**：`/manager` 页面

**布局**：在现有 Manager Dashboard 下方嵌入经理作战台模块，包含三个 Tab：
- **风险队列**：本周最需要关注的客户/商机
- **Pipeline 真相**：每个客户/商机的健康度判断
- **介入建议**：针对高风险项的具体行动建议

---

## 3. 核心能力 A：经理风险队列

### 队列来源

| 队列类型 | 数据来源 | 触发条件 |
|----------|----------|----------|
| 本周最危险客户/商机 | deal_rooms + executive alerts | managerAttentionNeeded=true 或 riskFlags 存在 |
| 高意向但长时间无推进 | deal_rooms + touchpointHub | 超过 5 天无外部触点 |
| 报价后停滞 | deal_rooms + checkpoints | 有报价 checkpoint 但超过 7 天未推进 |
| 长时间没有明确 next step | deal_rooms + work_items | 当前 goal 超期且无 nextMilestone |
| 需要经理介入 | deal_rooms | managerAttentionNeeded=true |

### 每项展示内容

- 客户名 / 商机标题
- 风险原因
- 最近动作时间
- 当前阶段（currentGoal）
- 风险级别（critical / high / medium / low）
- 推荐下一步
- 跳转入口（客户详情 / 商机详情）

---

## 4. 核心能力 B：Pipeline 真实性判断

### Truth Band 机制

基于启发式规则（heuristic）对客户/商机进行健康度分层：

| Truth Band | 标签 | 判断条件 |
|------------|------|----------|
| `healthy` | 健康 | 7 天内有更新 |
| `watch` | 观察 | 7-14 天无更新 |
| `suspicious` | 可疑 | 14-30 天无更新 |
| `stalled` | 停滞 | 超过 30 天无更新 |

### 信号采集

当前版本通过以下信号计算 Health Score（满分 100）：

| 信号 | 权重 | 说明 |
|------|------|------|
| 近期有活动（≤7天） | +30 | 7 天内有更新 |
| 最近有活动（7-30天） | +10 | 7-30 天内有更新 |
| 等待客户回复 | +15 | 有邮件 thread 处于 waiting_reply |
| 即将有会议 | +20 | 有已安排的日历会议 |
| 报价/合同阶段 | +20 | 当前阶段为 quote 或 contract |
| 长期无活动（>30天） | -20 | 超过 30 天无更新 |
| 无活动记录 | -20 | 尚无任何活动记录 |

Health Score = 50 + 正向信号权重之和 - 负向信号权重绝对值之和，clamp [0, 100]

### 可解释性

每个判断结果都附带 `reason` 字段，用自然语言说明"为什么这样判断"，避免黑盒分数。

---

## 5. 核心能力 C：经理介入建议

### 介入类型

| 类型 | 说明 | 触发条件 |
|------|------|----------|
| `coach` | 辅导销售 | 销售能力不足导致推进慢 |
| `escalate` | 升级处理 | 需要经理亲自出面 |
| `follow_up` | 督促跟进 | 销售节奏问题 |
| `support` | 提供支持 | 需要资源/信息支持 |

### 每项介入建议包含

- **为什么建议介入**：具体原因（可向销售说明）
- **建议介入对象**：销售负责人姓名
- **建议介入方式**：coach / escalate / follow_up / support
- **建议说法 / 建议动作**：具体可操作的建议
- **介入后应关注什么结果**：后续验证指标

### 生成方式

当前版本采用启发式生成（heuristic），基于风险项的上下文信息组合生成建议。后续版本可扩展为 DeepSeek prompt 生成。

### 介入闭环追踪

介入建议与 work_item + intervention_record 联动，形成完整闭环：

**介入状态**：`new` → `task_created` → `in_progress` → `completed` / `dismissed`

| 状态 | 说明 | UI 行为 |
|------|------|---------|
| `new` | 新建议，未处理 | 显示"创建任务"按钮 |
| `task_created` | 已创建 work_item | 显示"标记完成"按钮 + 任务跳转链接 |
| `in_progress` | 任务进行中 | 来自 work_item 状态同步 |
| `completed` | 已完成 | 显示"已完成"徽章（来自 intervention_record） |
| `dismissed` | 已忽略 | 显示"已忽略"徽章（来自 intervention_record） |

**状态持久化**：使用 `manager_desk_intervention_records` 表存储介入决议：
- 字段：`intervention_key`（唯一键）、`resolution_status`、`resolved_by`、`resolved_at`、`outcome_note`、`risk_reason`、`work_item_id`、`deal_room_id` 等
- 刷新页面后状态不丢失

**状态优先级**：`intervention_record.resolution_status` > `work_item.status` 映射 > fallback

**去重机制**：基于 `intervention_key`（干预项 ID）做唯一性约束，避免重复创建相同任务的建议。

**work_item 映射**：`work_type` 字段映射：
- `escalate` 类型 → `revive_stalled_deal`
- 其他类型 → `manager_checkin`

**PATCH API**：`PATCH /api/manager-desk` 接受 `{ interventionKey, resolution, outcomeNote, intervention }`，更新干预决议。

---

## 6. 技术实现

### 新增文件

| 文件路径 | 说明 | 状态 |
|----------|------|------|
| `types/manager-desk.ts` | 类型定义（含 ManagerDeskInterventionStatus） | ✅ 完成 |
| `services/manager-desk-service.ts` | 核心聚合服务（客户端路由用） | ✅ 完成 |
| `services/manager-desk-client-service.ts` | 客户端服务（含 POST createWorkItem + PATCH resolveIntervention） | ✅ 完成 |
| `services/manager-desk-server-service.ts` | 服务端专用聚合服务（解决 browser-client 问题） | ✅ 完成 |
| `hooks/use-manager-desk.ts` | React Hook（含 createWorkItem + resolveIntervention） | ✅ 完成 |
| `components/manager/manager-desk-queue.tsx` | UI 组件（含介入操作按钮） | ✅ 完成 |
| `app/api/manager-desk/route.ts` | API Route（含 GET + POST + PATCH） | ✅ 完成 |
| `tests/manager-desk.test.ts` | 测试 | ✅ 完成 |
| `supabase/migrations/202603270001_manager_desk_intervention_layer.sql` | P3 新增表 migration | ✅ 完成 |
| `types/database.ts` | 新增 manager_desk_intervention_records 表类型定义 | ✅ 完成 |

### 修改文件

| 文件路径 | 说明 | 状态 |
|----------|------|------|
| `app/(app)/manager/page.tsx` | 集成作战台 | ✅ 完成 |

### 复用能力

| 能力 | 复用方式 | 状态 |
|------|----------|------|
| deal_rooms | managerDeskServerService 直接调用 deal-room-service | ✅ 真实数据 |
| business_events | 通过 executiveClientService.getExecutiveEvents() | ✅ 真实数据 |
| customer 基本信息 | 通过 server-side 直接查询 customers 表 | ✅ 真实数据 |
| touchpointHub | 直接查询 email_threads + calendar_events 表 | ✅ 真实数据 |
| work_items | 通过 source_ref_type/source_ref_id 追踪介入状态 | ✅ P2 接入 |

### 当前数据说明

- **真实数据接入**：deal_rooms、business_events、touchpointHub、customer 基本信息均为真实 API 数据
- **Truth Band 判断依据**：以 deal_room.updatedAt 为主要时间信号，辅以 touchpointHub 邮件/日历状态
- **风险队列来源**：blocked/escalated roomStatus + stalled/suspicious TruthBand + warning/critical severity 事件
- **介入建议生成**：基于风险原因和房间状态生成，为 heuristic fallback
- **仍为 heuristic**：Truth Band 和介入建议均为规则/启发式，非 ML 模型

---

## 7. 明确排除范围

以下内容不在 v1.2 MVP 范围内：

| 排除项 | 原因 |
|--------|------|
| 老板经营总览 | 不是"今天该盯什么" |
| 合同/回款全链路扩展 | 超出经理作战台范围 |
| 大型 BI 面板 | 与产品原则冲突 |
| 复杂审批系统 | 不属于作战台范畴 |
| 组织级权限大改 | 复用现有权限体系 |
| 新 App 端 | 专注 Web 端 |
| 复杂自定义看板 | 与产品原则冲突 |

---

## 8. v1.2 后续保留项

| 功能 | 目标版本 | 说明 |
|------|----------|------|
| DeepSeek prompt 生成介入建议 | v1.2 下一阶段 | 当前为 heuristic fallback |
| Truth Band 与实际转化率对照 | v1.2 下一阶段 | 校准评分准确性 |
| 团队整体 Pipeline 健康度报表 | v1.2 下一阶段 | 聚合视图 |
| 介入效果跟踪（outcome_note 分析） | v1.2 下一阶段 | 基于 resolved_at + outcome_note 做效果分析 |
| work_item 状态同步（in_progress） | v1.2 下一阶段 | 当前 in_progress 依赖 work_item done 后的状态映射 |

---

## 9. 验证路径

```
/manager → 看到三个 Tab
  → 风险队列 Tab：看到本周最危险客户列表
    → 点击客户名 → 跳转客户详情
    → 查看风险原因和推荐下一步
  → Pipeline 真相 Tab：看到客户健康度分层
    → 查看信号解释和 health score
  → 介入建议 Tab：看到可执行建议列表
    → 查看介入原因、方式、说法建议
```

---

_文档版本：v1.2 MVP_
_更新日期：2026-03-20_
