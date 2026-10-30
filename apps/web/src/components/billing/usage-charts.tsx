'use client';

import * as React from 'react';

import { EmptyState } from '@/components/ui/feedback';
import { formatDate, formatPercent, formatUnits } from '@/lib/format';
import type { UsageBreakdownItem, UsageDailyTrendItem } from '@/lib/types';

/** 用量图表属性 */
export interface UsageChartsProps {
  /** 分项明细（成员 / 部门 / 能力） */
  breakdown: UsageBreakdownItem[];
  /** 按日趋势 */
  dailyTrend: UsageDailyTrendItem[];
  /** 额度单位文案（后端直出，缺省用「字」） */
  quotaUnitLabel?: string;
}

/** 单根柱子的最大高度（px） */
const BAR_MAX_HEIGHT = 96;

/**
 * 用量图表：按日趋势（柱状）+ 按维度分项（横向条）。
 *
 * 设计取舍：
 * - 不引入图表库（「不新增依赖」），用纯 CSS 高度百分比画柱状图，数据量与形态固定；
 * - **无数据必须给空态**，不允许渲染成空白区域（用户会以为页面卡住）；
 * - 限额相关文案只讲「用掉多少、还剩多少」，不讲任何计费术语。
 *
 * @param props - 组件属性
 * @returns 两个图表区块
 */
export function UsageCharts({
  breakdown,
  dailyTrend,
  quotaUnitLabel = '字',
}: UsageChartsProps): React.JSX.Element {
  const maxDaily = dailyTrend.reduce((max, item) => Math.max(max, item.units), 0);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="space-y-3 rounded-lg border p-4">
        <header className="space-y-0.5">
          <h3 className="text-sm font-semibold">按日用量</h3>
          <p className="text-xs text-muted-foreground">每个自然日的实际使用量</p>
        </header>
        {dailyTrend.length === 0 ? (
          <EmptyState
            title="这段时间还没有用量"
            description="开始对话或处理文档后，这里会按天展示使用情况"
          />
        ) : (
          <div className="flex h-[7.5rem] items-end gap-1" role="img" aria-label="按日用量柱状图">
            {dailyTrend.map((item) => {
              const height =
                maxDaily > 0
                  ? Math.max(4, Math.round((item.units / maxDaily) * BAR_MAX_HEIGHT))
                  : 4;
              return (
                <div
                  key={item.date}
                  className="group relative flex flex-1 flex-col items-center justify-end"
                  title={`${formatDate(item.date)}：${formatUnits(item.units, quotaUnitLabel)}`}
                >
                  <div
                    className="w-full rounded-t bg-primary/70 transition-colors group-hover:bg-primary"
                    style={{ height: `${height}px` }}
                  />
                  <span className="mt-1 w-full truncate text-center text-[10px] text-muted-foreground">
                    {item.date.slice(5)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <header className="space-y-0.5">
          <h3 className="text-sm font-semibold">用量构成</h3>
          <p className="text-xs text-muted-foreground">按所选维度看谁在用、用在哪</p>
        </header>
        {breakdown.length === 0 ? (
          <EmptyState
            title="还没有分项数据"
            description="切换统计维度或换一个账期后再看，也可以先发起一次对话产生用量"
          />
        ) : (
          <ul className="space-y-2.5">
            {breakdown.map((item) => {
              const percent = Number.isFinite(item.percent) ? Math.min(item.percent, 100) : 0;
              return (
                <li key={item.key} className="space-y-1">
                  <div className="flex items-baseline justify-between gap-2 text-sm">
                    <span className="truncate">{item.display_name || '未命名分项'}</span>
                    <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                      {formatUnits(item.units, quotaUnitLabel)} · {formatPercent(item.percent)}
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${percent}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="rounded-lg border p-4 lg:col-span-2">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>统计口径：与「本月用量」同源，按自然日汇总，单位 {quotaUnitLabel}</span>
          <span className="tabular-nums">
            区间合计{' '}
            {formatUnits(
              dailyTrend.reduce((sum, item) => sum + item.units, 0),
              quotaUnitLabel,
            )}
          </span>
        </div>
      </section>
    </div>
  );
}
