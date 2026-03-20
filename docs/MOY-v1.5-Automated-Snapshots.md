# MOY v1.5 自动化快照与趋势稳定化

> **当前版本：v1.5** | 目标：从"手动生成趋势数据"推进到"自动积累趋势数据、趋势质量逐步稳定"

---

## 1. 目标与原则

**核心目标**：让 MOY 的历史快照数据能够自动积累，趋势质量逐步稳定，为后续老板视角打好数据基础。

**产品原则**：
1. 本轮重点是"自动积累"和"趋势可信度提升"，不是更多分析页面
2. 不追求复杂报表，只追求数据层稳定
3. 所有自动化都要有清晰的触发入口与失败兜底
4. weekly 与 monthly 口径必须清楚且一致
5. 对样本不足仍要保留明确提示

---

## 2. 四大核心改动

### A. 自动化周快照（pg_cron-ready）

**新增 Migration**：`202603290001_manager_insights_snapshot_automation.sql`

**自动化机制**：
- 使用 pg_cron 定时调度（需 Supabase Pro Plan 支持 pg_cron 扩展）
- 每周一 02:00 (北京时间) 自动执行 weekly snapshot
- 每月 1 日 02:00 (北京时间) 自动执行 monthly snapshot
- 调用内部 API：`POST /api/internal/manager/insights/snapshot/run`
- 使用 `X-Internal-Snapshot-Key` 请求头鉴权

**配置要求**：
- 需在 Supabase Dashboard 配置：
  - `app.internal_api_base_url`：Supabase Project URL
  - `app.internal_api_key`：自定义密钥（建议 32+ 字符）
- 或通过 `INTERNAL_SNAPSHOT_KEY` 环境变量（部署时配置）

**当前状态**：cron-ready but not cron-bound
- pg_cron migration 已提供，但需要 Supabase Pro Plan + 启用 pg_cron 扩展
- 若暂时无法使用 pg_cron，仍可通过 `/api/internal/manager/insights/snapshot/run` 手动触发所有 org 的快照

**内部 API**：`POST /api/internal/manager/insights/snapshot/run`

请求体：
```json
{
  "snapshotType": "weekly" | "monthly",
  "orgId": "uuid (optional, omit for all orgs)"
}
```

返回：
```json
{
  "snapshotType": "weekly",
  "totalOrgs": 1,
  "successCount": 1,
  "errorCount": 0,
  "results": [{ "orgId": "...", "status": "success", "snapshotId": "..." }]
}
```

### B. monthly snapshot 正式启用

**Period 计算**：
- **weekly**：periodEnd = yesterday, periodStart = yesterday - 6 days（共 7 天，calendar-based）
- **monthly**：periodStart = 上一个完整自然月的第一天, periodEnd = 上一个完整自然月的最后一天（避免生成"未来区间"数据）

**为何 monthly = 上月结算快照**：

- pg_cron 计划在每月 1 日 02:00 执行，此时当月刚过 1 天，数据远未完整
- 若生成"当月"快照，会包含大量未来区间（不存在的交易、不会发生的触点）
- "上月结算快照"：当月 1 日时，上月已完整结束，数据最完整，是最可靠的月度分析口径
- 执行时机对齐：每月 1 日恰好是上月数据最完整的时刻，执行结果最有分析价值

**API 支持**：
- `POST /api/manager/insights/snapshots`：支持 `snapshotType: "monthly"` 参数
- `GET /api/manager/insights/trends`：支持 `snapshotType=monthly` 查询参数
- `POST /api/manager/insights/snapshots/backfill`：支持批量回填 monthly 快照

**Upsert 规则**：同一周期 + 同类型 snapshot 已存在时执行 UPDATE，不产生重复数据。

### C. signalQuality 规则优化

**分层阈值**：

| 类型 | insufficient | early | sufficient |
|------|-------------|-------|------------|
| weekly | < 3 个 | 3-11 个 | ≥ 12 个 |
| monthly | < 2 个 | 2-5 个月 | ≥ 6 个月 |

**新增 `isEarlySignal` 标记**：
- 每个 trend 维度（truthBandTrends / interventionTrends / improvementTrend）均新增 `isEarlySignal: boolean`
- 当快照数 < 4 时，标记为 `true`，前端显示"早期"Tag
- 目的：让用户在只看 1-3 条数据时，不被趋势箭头误导

**信号质量文案**：

| 质量 | 文案示例 |
|------|---------|
| insufficient | 周快照数据不足（需要至少 3 个，当前 0 个），趋势暂无参考意义 |
| early | 当前仅有 5 个周快照，趋势为早期信号，请谨慎参考 |
| sufficient | 周数据量充足（12+ 周），趋势分析具有参考价值 |

### D. Backfill 辅助能力

**Backfill API**：`POST /api/manager/insights/snapshots/backfill`

请求体：
```json
{
  "snapshotType": "weekly" | "monthly",
  "periodsToBackfill": 8
}
```

返回：
```json
{
  "snapshotType": "weekly",
  "totalRequested": 8,
  "createdCount": 6,
  "skippedCount": 2,
  "errorCount": 0,
  "results": [...]
}
```

**Backfill 逻辑**：
- 从当前周期向前回溯，依次生成历史快照
- 已有快照的周期自动跳过（`skippedCount`）
- 最多支持 52 个周期回填
- 使用真实的 `periodDays` 计算每个周期的指标（monthly 按实际天数）

**使用场景**：
- Demo / 开发环境：快速生成历史数据
- 新接入客户：一次性补全历史快照
- 月度总结：补全上月或上上月的 monthly snapshot

---

## 3. UI 增强

### 趋势组件（/manager 页面）

**新增 weekly/monthly 切换 Tab**：
- 标题旁新增"周/月"切换按钮
- 切换后自动重新加载对应类型的 trends 数据

**新增"回填历史"按钮**：
- 当 `signalQuality !== sufficient` 时显示
- 点击后调用 backfill API，快速填充历史数据
- 显示 `backfilling` 加载状态

**新增"早期"Tag**：
- 当 `isEarlySignal === true` 时，每个 trend 区块标题旁显示"早期"Tag
- 提醒用户该维度的结论为早期信号

**生成快照按钮**：
- 根据当前选中的 weekly/monthly 类型，使用对应 periodDays（weekly=7, monthly=30）
- 按钮文字从"生成本周快照"改为"生成快照"

---

## 4. API 总览

| 端点 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/manager/insights/snapshots` | POST | 生成当前周期快照 | Manager role |
| `/api/manager/insights/snapshots/backfill` | POST | 回填历史快照 | Manager role |
| `/api/manager/insights/trends` | GET | 读取趋势数据 | Auth |
| `/api/internal/manager/insights/snapshot/run` | POST | 内部触发（全 org 或指定 org） | X-Internal-Snapshot-Key |

---

## 5. Metric Definitions

### weekly Period
- periodEnd = yesterday（北京时间当日 0 点）
- periodStart = periodEnd - 6 days
- 共 7 天

### monthly Period
- periodStart = 上一个完整自然月的第一天
- periodEnd = 上一个完整自然月的最后一天
- 天数不固定（28-31 天）
- 设计原则：绝不生成"未来区间"数据，每月 1 日执行时生成上月结算数据

### signalQuality 规则

| 类型 | insufficient | early | sufficient |
|------|-------------|-------|------------|
| weekly | < 3 | 3-11 | ≥ 12 |
| monthly | < 2 | 2-5 | ≥ 6 |

### isEarlySignal 规则
- `snapshots.length < 4` → `true`
- ≥ 4 → `false`

### Trend Direction 阈值
- TruthBand count：`|Δ| ≤ 1` → stable
- Intervention count：`|Δ| < 1` → stable
- Rate：`|Δrate| < 5%` → stable

---

## 6. 已排除范围

- ❌ 老板经营驾驶舱（/executive 大改）
- ❌ pg_cron 定时任务（已在 migration 中定义但需 Pro Plan）
- ❌ 自定义报表平台
- ❌ DeepSeek 文案优化
- ❌ 复杂 agent
- ❌ 合同/回款扩展
- ❌ 趋势告警机制（下一阶段）

---

## 7. 验证路径

### /manager 手动验证

```
1. 访问 /manager 页面（manager 或 admin 角色）
2. 滚动到"趋势分析"卡片
3. 默认显示"周"视图，点击"月"切换到月度视图
4. 点击"生成快照" → 观察 loading 状态
5. 点击"回填历史"（信号不足时显示）→ 观察批量生成
6. 切换周/月视图，确认趋势数据正确加载
7. 确认各 trend 区块显示"早期"Tag（当 snapshots < 4）
```

### API 验证

```bash
# 手动触发 snapshot（Manager role）
curl -X POST http://localhost:3000/api/manager/insights/snapshots \
  -H "Content-Type: application/json" \
  -d '{"snapshotType": "weekly"}'

# 读取 trends
curl "http://localhost:3000/api/manager/insights/trends?snapshotType=weekly"

# Backfill
curl -X POST http://localhost:3000/api/manager/insights/snapshots/backfill \
  -H "Content-Type: application/json" \
  -d '{"snapshotType": "weekly", "periodsToBackfill": 8}'
```

---

## 8. 后续合理保留项

| 优先级 | 内容 | 说明 |
|--------|------|------|
| P0 | pg_cron 启用与监控 | 需要 Supabase Pro Plan + pg_cron 扩展 + cron job 监控 |
| P0 | 实际运行 weekly/monthly cron job | 验证 production 环境下正常执行 |
| P1 | 趋势告警机制 | 当 stalled 数超过阈值时触发通知 |
| P1 | /manager 趋势页独立路由 | 当前嵌入 /manager，后续可拆为 /manager/trends |
| P2 | 介入效果按销售分组趋势 | 按 individual contributor 分组看干预效果 |
| P2 | Truth Band 历史同比 | 对比去年同期分布（需年度数据积累） |
| P3 | /executive 老板经营驾驶舱 | v1.6/v2.0 目标 |

---

_文档版本：v1.5_
_更新日期：2026-03-20_
