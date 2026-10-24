'use client';

/**
 * 工作流任务指标卡（W1 切片 A，P-13 工作流页）。
 *
 * 口径红线（架构 §1.4.5 / PRD R1-R4）：
 * - 四张卡标题**逐字**带「工作流任务」限定词，禁简写；
 * - 只展示工作流任务域数字，不与对话/文档域（`/model/usage`）混排；
 * - 界面禁现技术字段名（用量为纯数字，时长走业务语言）；
 * - `total < 10`（含 0）显示小样本提示，数值照常展示（空数据全 0）。
 */

import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState, Skeleton } from '@/components/ui/feedback';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { statsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { PLACEHOLDER, formatDurationSeconds, formatPercent } from '@/lib/format';
import type { TaskStatsData, TaskStatsRange } from '@/lib/types';

/** 小样本阈值：窗口内任务数少于此值时提示「仅供参考」 */
const SMALL_SAMPLE_THRESHOLD = 10;

/** 统计期选项（与接口 `range` 枚举逐字一致） */
const RANGE_OPTIONS: ReadonlyArray<{ value: TaskStatsRange; label: string }> = [
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
];

/** 加载态骨架卡数量（与指标卡数量一致） */
const SKELETON_CARD_COUNT = 4;

/** 单张指标卡的展示形态 */
interface MetricCardView {
  /** 卡片标题（逐字，禁简写） */
  title: string;
  /** 主数值（业务语言） */
  value: string;
  /** 副文案（分子分母口径） */
  hint: string;
}

/**
 * 指标数值展示：最多两位小数并去掉多余的 `.0`。
 *
 * 用于「单位成功用量」这类**非百分比**数值：纯数字，不附加任何技术单位字样。
 *
 * @param value - 接口浮点值
 * @returns 形如 `12.5` / `12` 的文本；非法值返回占位符
 */
function formatMetricNumber(value: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return PLACEHOLDER;
  }
  return String(Number(value.toFixed(2)));
}

/**
 * 由统计数据组装四张指标卡。
 *
 * @param stats - 接口返回的任务统计（空数据时全 0）
 * @returns 卡片展示形态列表
 */
function buildMetricCards(stats: TaskStatsData): MetricCardView[] {
  // 完成率分母 = 终态任务剔除用户主动取消（系统取消与期末未终态仍计入分母）
  const rateDenominator = Math.max(stats.total - stats.cancelled, 0);
  return [
    {
      title: '工作流任务完成率',
      value: formatPercent(stats.completion_rate),
      hint: `已完成 ${stats.completed} / ${rateDenominator}`,
    },
    {
      title: '工作流任务平均完成时长',
      value: formatDurationSeconds(stats.avg_duration_seconds),
      hint: `中位 ${formatDurationSeconds(stats.p50_duration_seconds)}`,
    },
    {
      title: '工作流任务重复使用率',
      value: formatPercent(stats.repeat_user_rate),
      hint: `${stats.active_users} 位用户中有重复使用`,
    },
    {
      title: '工作流任务单位成功用量',
      value: formatMetricNumber(stats.units_per_success),
      hint: `按 ${stats.completed} 个已完成任务平均`,
    },
  ];
}

/**
 * 工作流任务指标卡区。
 *
 * 三态（AC-A8）：加载 → 骨架卡；错误 → `ErrorState`（可重试）；
 * 数据（含空数据全 0）→ 照常渲染卡片。统计期随 `range` 切换并标注。
 *
 * @returns 指标卡区元素
 */
export function TaskMetricCards(): React.JSX.Element {
  const [range, setRange] = React.useState<TaskStatsRange>('7d');

  const statsQuery = useApiQuery(QUERY_KEYS.taskStats(range), () => statsApi.tasks(range));

  const rangeLabel = RANGE_OPTIONS.find((option) => option.value === range)?.label ?? '近 7 天';
  const stats = statsQuery.data;
  const smallSample = stats !== undefined && stats.total < SMALL_SAMPLE_THRESHOLD;

  return (
    <section className="space-y-3" aria-label="工作流任务指标">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">统计期：{rangeLabel}</p>
        <div className="flex items-center gap-1" role="group" aria-label="切换统计期">
          {RANGE_OPTIONS.map((option) => (
            <Button
              key={option.value}
              type="button"
              size="sm"
              variant={option.value === range ? 'default' : 'outline'}
              aria-pressed={option.value === range}
              onClick={() => {
                setRange(option.value);
              }}
            >
              {option.label}
            </Button>
          ))}
        </div>
      </div>

      {smallSample && stats ? (
        <p className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          样本还少，仅供参考（当前样本 {stats.total} 个任务）
        </p>
      ) : null}

      {statsQuery.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: SKELETON_CARD_COUNT }, (_, index) => (
            <Skeleton key={index} className="h-28 w-full" />
          ))}
        </div>
      ) : statsQuery.error ? (
        <ErrorState
          message={toBusinessMessage(statsQuery.error)}
          onRetry={() => {
            void statsQuery.refetch();
          }}
        />
      ) : stats ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {buildMetricCards(stats).map((card) => (
            <Card key={card.title}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">{card.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold tabular-nums">{card.value}</p>
                <p className="mt-1 text-xs text-muted-foreground">{card.hint}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}
    </section>
  );
}
