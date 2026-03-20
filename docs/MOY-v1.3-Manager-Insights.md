# MOY v1.3 介入效果分析与 Truth Band 校准

> **历史版本：v1.3** | v1.4 ~ v1.5 均已完成 | 目标：从"会判断、会建议、会执行"升级到"会验证、会学习"

---

## 1. 目标与原则

**核心目标**：让 MOY 开始知道"哪些建议真的有效、哪些评分真的靠谱"

**产品原则**：
1. 本轮重点不是页面多，而是"可信度提升"
2. 所有分析都基于当前真实数据结构
3. 先做最小可验证闭环，不做完美统计系统
4. 所有口径都要在文档里写清楚，不要假装精确

---

## 2. 三大核心能力

### A. 介入效果分析

**指标体系**：

| 指标 | 计算口径 | 数据来源 | 严格/近似 |
|------|----------|----------|----------|
| 任务创建数 | 周期内 `source_ref_type='manager_desk_intervention'` 的 work_items 总数 | work_items 表 | 严格 |
| 已完成数 | work_item.status = 'done' 的数量 | work_items 表 | 严格 |
| 已忽略数 | intervention_records.resolution_status = 'dismissed' 的数量 | intervention_records 表 | 严格 |
| 完成率 | 已完成数 / 任务创建数 | 计算得出 | 严格 |
| 忽略率 | 已忽略数 / 总干预数 | 计算得出 | 严格 |
| 高频风险原因 | 按 risk_reason 分组统计 top 6 | intervention_records 表 | 严格 |
| 按类型分布 | coach / escalate / follow_up / support 数量 | work_items.work_type 映射 | 严格 |

### B. Truth Band 回测与分布

**分布统计**：统计当前各 Truth Band（healthy / watch / suspicious / stalled）的数量和占比

| Band | 判断规则 | 数据来源 |
|------|----------|----------|
| `stalled` | blocked/escalated 房间 + 超过 30 天无更新的 active 房间 | deal_rooms.updated_at |
| `suspicious` | 14-30 天无更新的 active 房间 | deal_rooms.updated_at |
| `watch` | 7-14 天无更新的 active 房间 | deal_rooms.updated_at |
| `healthy` | 7 天内有更新的 active 房间 | deal_rooms.updated_at |

**回测说明**：
- v1.3 版本：当前仅做分布统计，不做跨周期对比
- 分布结果仅作为"当前状态快照"，不代表评分准确性判断
- v1.4 已建立历史快照层，v1.5 支持 weekly/monthly 趋势积累，跨周期对比请参考 [MOY-v1.5-Automated-Snapshots.md](MOY-v1.5-Automated-Snapshots.md)

### C. 风险改善指标

**指标定义**：

| 指标 | 计算口径 | 说明 |
|------|----------|------|
| 解除阻塞数 | 周期内 room_status 从 blocked 变为 active/watchlist 的房间数 | 近似口径，非严格因果归因 |
| 解除升级数 | 周期内 room_status 从 escalated 变为 active/watchlist 的房间数 | 近似口径 |
| 风险改善率 | 解除阻塞数 / 原有 blocked 总数 | 仅作为参考指标 |
| 新增阻塞数 | 周期内新创建的 blocked 状态房间数 | 严格 |
| 新增危机事件数 | 周期内 severity=critical 且 status=open 的 business_events 数 | 严格 |
| 新增高风险事件数 | 周期内 severity=high 且 status=open 的 business_events 数 | 严格 |

**重要提示**：`improvementRate` 是近似口径，不代表因果关系。房间状态变更可能是销售自然推进，而非经理介入的直接结果。

---

## 3. 技术实现

### 新增文件

| 文件路径 | 说明 | 状态 |
|----------|------|------|
| `services/manager-insights-service.ts` | 核心分析服务（Truth Band 分布 + 干预分析 + 风险改善） | ✅ 完成 |
| `services/manager-insights-client-service.ts` | 客户端服务 | ✅ 完成 |
| `hooks/use-manager-insights.ts` | React Hook | ✅ 完成 |
| `components/manager/manager-insights-summary.tsx` | 轻量分析 UI 组件 | ✅ 完成 |
| `app/api/manager/insights/route.ts` | API Route | ✅ 完成 |
| `tests/manager-insights.test.ts` | 测试 | ✅ 完成 |
| `docs/MOY-v1.3-Manager-Insights.md` | v1.3 专项文档 | ✅ 完成 |

### 复用能力

| 能力 | 复用方式 | 状态 |
|------|----------|------|
| manager_desk_intervention_records | 解析干预决议状态和原因 | ✅ 真实数据 |
| work_items (source_ref_type='manager_desk_intervention') | 统计任务创建和完成 | ✅ 真实数据 |
| deal_rooms | Truth Band 计算 + 风险改善判断 | ✅ 真实数据 |
| business_events | 新增危机/风险事件统计 | ✅ 真实数据 |
| lib/closed-loop.ts | 复用 computeOutcomeOverview 模式 | ✅ 参考 |

---

## 4. 指标计算口径说明

### 介入效果分析口径

```
totalCreated = 周期内创建的任务数（work_items.source_ref_type='manager_desk_intervention'）
totalCompleted = 任务.status='done' 的数量
totalDismissed = intervention_records.resolution_status='dismissed' 的数量
completionRate = totalCompleted / totalCreated
dismissRate = totalDismissed / totalCreated
```

**注意**：completionRate 和 dismissRate 的分母可能不一致（已完成的任务不一定会写 intervention_records，反之亦然），因此两者之和不一定等于 100%。

### Truth Band 分布口径

```
对每个 deal_room：
  1. 如果 room_status = 'blocked' 或 'escalated' → stalled
  2. 否则，计算 (now - updated_at) 天数：
     - > 30 天 → stalled
     - 14-30 天 → suspicious
     - 7-14 天 → watch
     - ≤ 7 天 → healthy

percentage = 该 band 数量 / 总房间数
```

### 风险改善口径

```
improvementRate = 周期内 room_status 从 blocked 变为非 blocked 的房间数 / 周期初的 blocked 总数

重要：这是相关性指标，不代表因果关系。
房间状态变更可能是：
  - 销售自然推进的结果
  - 经理介入的结果
  - 外部因素（如客户主动反馈）
```

---

## 5. 明确排除范围

| 排除项 | 原因 |
|--------|------|
| 跨周期对比分析 | v1.3 数据积累不足；v1.4/v1.5 已通过历史快照和趋势分析解决 |
| 严格归因分析 | 需要控制组和随机化，暂不满足条件 |
| DeepSeek 文案生成 | 不在本轮范围内 |
| 自定义 BI 面板 | 与产品原则冲突 |
| 老板经营驾驶舱 | 属于 v2.0 范围 |

---

## 6. v1.3 后续保留项

| 功能 | 目标版本 | 说明 |
|------|----------|------|
| Truth Band 历史分布对比 | v1.3 下一阶段 | 需要 weekly snapshot 积累 |
| 介入效果严格归因 | v1.3 下一阶段 | 需要对照组设计 |
| 介入效果按销售排名 | v1.3 下一阶段 | 按 resolved_by 分组分析 |
| 高频无效建议模式分析 | v1.3 下一阶段 | 依赖更多 outcome 数据 |
| DeepSeek 文案优化建议 | v1.3 下一阶段 | 基于 pattern 分析生成 |

---

## 7. 验证路径

```
/manager → 经理作战台 → "介入效果分析"卡片
  → 查看 Pipeline 健康度条形图（healthy/watch/suspicious/stalled 分布）
  → 查看创建任务/已完成/已忽略数字
  → 查看完成率和忽略率
  → 查看高频风险原因 top 3
  → 查看风险改善率和说明文字
  → 点击"详情"跳转到 /manager/outcomes
```

---

## 8. 与现有 /manager/outcomes 的关系

| 能力 | /manager/outcomes | /manager（v1.3） |
|------|-------------------|-------------------|
| 关注对象 | action_outcomes + suggestion_adoptions | manager_desk_intervention_records + work_items |
| 时间范围 | weekly/monthly | 固定 7 天 |
| 分析维度 | positive_rate / adoption_rate | completion_rate / dismiss_rate / truth_band |
| 目的 | 了解销售行动效果 | 了解经理干预效果 |

两者互补，不是替代关系。

---

_文档版本：v1.3 MVP_
_更新日期：2026-03-20_
