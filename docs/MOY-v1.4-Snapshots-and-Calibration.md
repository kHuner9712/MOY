# MOY v1.4 历史快照与校准层

> **历史版本：v1.4** | **当前版本：v1.5** | 目标：从"当前快照系统"升级到"有历史纵向比较能力的系统"

---

## 1. 目标与原则

**核心目标**：让 MOY 具备历史趋势分析能力，为可信度校准和后续老板视角打好数据基础

**产品原则**：
1. 本轮重点是"历史趋势能力"，不是"更多卡片"
2. 所有快照都要有明确口径
3. 先做最小可积累的数据层，不做完美归因系统
4. 所有趋势结论都必须标注样本不足/早期信号风险
5. 优先复用 v1.3 已有指标逻辑，不重复发明口径

---

## 2. 三大核心能力

### A. 周 / 月快照层

**快照表**：`manager_insights_snapshots`

| 字段 | 类型 | 说明 |
|------|------|------|
| `period_start` | date | 周期开始日期 |
| `period_end` | date | 周期结束日期 |
| `snapshot_type` | varchar | weekly / monthly |
| `truth_band_distribution` | jsonb | 各 band 数量和百分比 |
| `intervention_stats` | jsonb | 干预统计 |
| `risk_signals` | jsonb | 新增风险信号 |
| `risk_improvement` | jsonb | 风险改善指标 |
| `created_at` | timestamptz | 快照生成时间 |

**快照内容（来自 v1.3 指标逻辑）**：

| 快照指标 | 数据来源 | 计算口径 |
|----------|----------|----------|
| Truth Band 分布 | deal_rooms | blocked→stalled；30天→stalled；14-30天→suspicious；7-14天→watch；≤7天→healthy |
| 干预统计 | work_items + intervention_records | totalCreated / totalCompleted / totalDismissed / completionRate / dismissRate |
| 风险信号 | business_events | newCriticalCount / newHighRiskCount / newBlockedCount |
| 风险改善 | deal_rooms | resolvedBlockedCount / improvementRate（近似口径） |

**手动触发**：通过 `POST /api/manager/insights/snapshots` 手动生成本周快照（当前无 pg_cron 定时任务）

### B. Truth Band 历史回测

**趋势方向计算**：对比相邻两个周期的分布变化

| 方向判断 | stalled / suspicious | healthy / watch |
|----------|---------------------|-----------------|
| `up`（变差） | count 增加 | count 减少 |
| `down`（变好） | count 减少 | count 增加 |
| `stable` | \|Δ\| ≤ 1 | \|Δ\| ≤ 1 |

**数据充分性评估**：

| 快照数量 | 质量评估 | 说明 |
|----------|----------|------|
| ≥ 8 个 | `sufficient` | 数据足够，可用于趋势分析 |
| 3-7 个 | `early` | 早期信号，请谨慎参考 |
| < 3 个 | `insufficient` | 数据不足，趋势分析暂无意义 |

### C. 介入效果趋势分析

**趋势指标**：

| 指标 | 计算 | 方向判断 |
|------|------|----------|
| createdTrend | totalCreated 变化 | up/down/stable |
| completedTrend | totalCompleted 变化 | up/down/stable |
| completionRateTrend | completionRate 变化 | up/down/stable |
| dismissRateTrend | dismissRate 变化 | up/down/stable |

**改善率趋势**：
- `improvementRate` 变化百分比
- `|Δ| < 5%` → stable

---

## 3. API 设计

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/manager/insights/snapshots` | POST | 手动生成快照（manager role） |
| `/api/manager/insights/trends` | GET | 读取趋势数据（periods=8 默认） |

**GET /api/manager/insights/trends 参数**：
- `snapshotType`: weekly | monthly（默认 weekly）
- `periods`: 读取周期数（默认 8）

**POST /api/manager/insights/snapshots 参数**：
- `periodDays`: 周期天数（默认 7）
- `snapshotType`: weekly | monthly（默认 weekly）

---

## 4. 技术实现

### 新增文件

| 文件路径 | 说明 | 状态 |
|----------|------|------|
| `supabase/migrations/202603280001_manager_insights_snapshot_layer.sql` | 快照表 migration | ✅ 完成 |
| `types/database.ts` | 新增 manager_insights_snapshots 表定义 | ✅ 完成 |
| `types/manager-insights-snapshot.ts` | 快照和趋势类型定义 | ✅ 完成 |
| `services/manager-insights-snapshot-service.ts` | 快照生成 + 趋势读取服务 | ✅ 完成 |
| `services/manager-insights-trends-client-service.ts` | 趋势客户端服务 | ✅ 完成 |
| `hooks/use-manager-insights-trends.ts` | 趋势 React Hook | ✅ 完成 |
| `components/manager/manager-insights-trends.tsx` | 趋势展示 UI | ✅ 完成 |
| `app/api/manager/insights/snapshots/route.ts` | POST 快照生成 API | ✅ 完成 |
| `app/api/manager/insights/trends/route.ts` | GET 趋势读取 API | ✅ 完成 |
| `tests/manager-insights-snapshot.test.ts` | 快照和趋势测试 | ✅ 完成 |
| `docs/MOY-v1.4-Snapshots-and-Calibration.md` | v1.4 专项文档 | ✅ 完成 |

### 复用能力

| 能力 | 复用方式 |
|------|----------|
| managerInsightsService | 快照内容直接复用 v1.3 指标逻辑 |
| intervention_records | 快照统计复用 |
| work_items | 快照统计复用 |
| deal_rooms | Truth Band + 风险改善计算复用 |

---

## 5. 指标口径说明

### 快照内容口径

```
Truth Band 分布：
  blocked / escalated → stalled
  updated_at 超过 30 天 → stalled
  14-30 天 → suspicious
  7-14 天 → watch
  ≤ 7 天 → healthy

干预统计（严格）：
  totalCreated = work_items.source_ref_type='manager_desk_intervention'
  totalCompleted = work_item.status='done'
  totalDismissed = intervention_records.resolution_status='dismissed'
  completionRate = totalCompleted / totalCreated
  dismissRate = totalDismissed / totalCreated

风险改善（近似）：
  resolvedBlockedCount = 周期内 blocked→非blocked 的房间数
  improvementRate = resolvedBlockedCount / 原有 blocked 总数
  注意：这是相关性，非因果归因
```

### 趋势方向口径

```
Truth Band 趋势（对比相邻两周）：
  direction = "up"   当 |Δcount| > 1 且方向对当前 band 不利
  direction = "down" 当 |Δcount| > 1 且方向对当前 band 有利
  direction = "stable" 当 |Δcount| ≤ 1

介入效果趋势：
  direction = "up"   当 Δ > 1
  direction = "down" 当 Δ < -1
  direction = "stable" 当 |Δ| ≤ 1

改善率趋势：
  direction = "up"   当 Δrate > 5%
  direction = "down" 当 Δrate < -5%
  direction = "stable" 当 |Δrate| ≤ 5%
```

---

## 6. 明确排除范围

| 排除项 | 原因 |
|--------|------|
| pg_cron 定时任务 | 当前无定时任务基础设施，下轮可考虑 |
| 严格归因分析 | 需要对照组设计，当前数据不满足 |
| 老板经营驾驶舱 | 属于 v2.0 范围 |
| 自定义 BI 面板 | 与产品原则冲突 |
| DeepSeek 文案生成 | 不在本轮范围 |

---

## 7. v1.4 后续保留项

| 功能 | 目标版本 | 说明 |
|------|----------|------|
| pg_cron 定时快照 | v1.4 下一阶段 | 自动化每周快照生成 |
| monthly snapshot 支持 | v1.4 下一阶段 | 结构已支持（snapshot_type），数据积累后可启用 |
| Truth Band 历史分布同比 | v1.4 下一阶段 | 对比去年同期数据 |
| 介入效果按销售分组趋势 | v1.4 下一阶段 | 按 resolved_by 分组 |
| 趋势告警机制 | v1.4 下一阶段 | 当 stalled 超过阈值时告警 |

---

## 8. 验证路径

```
/manager
  → 趋势分析卡片（新增）
    → 查看"数据充足/早期信号/数据不足"标签
    → 查看 Truth Band 变化趋势（↑↓→ 箭头 + 数字）
    → 查看介入效果趋势（创建/完成/完成率/忽略率）
    → 查看风险改善率趋势
    → 点击"生成本周快照"（manager role）
    → 页面刷新后趋势更新

手动验证（API）：
  POST /api/manager/insights/snapshots → 返回 snapshotId
  GET /api/manager/insights/trends → 返回趋势数据
```

---

## 9. 与老板经营驾驶舱的关系

v1.4 的快照数据（manager_insights_snapshots）是后续构建**老板级历史视图**的数据基础。当积累足够多的历史快照后，可以：
- 对比不同周期的 Truth Band 分布变化
- 评估团队整体干预有效性趋势
- 观察风险改善率的长期走向

但这些属于 v2.0 老板经营驾驶舱的范畴，v1.4 仅建立数据基础。

---

_文档版本：v1.4_
_更新日期：2026-03-20_

> **后续版本**：v1.5 在 v1.4 基础上增加了自动化快照、monthly 支持增强、backfill 能力、signalQuality 规则优化。详见 [MOY-v1.5-Automated-Snapshots.md](MOY-v1.5-Automated-Snapshots.md)
