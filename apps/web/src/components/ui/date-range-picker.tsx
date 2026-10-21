'use client';

import {
  differenceInCalendarDays,
  endOfMonth,
  format,
  isValid,
  parseISO,
  startOfMonth,
  subDays,
  subMonths,
} from 'date-fns';
import { CalendarDays } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/** 时间范围（ISO 日期字符串 `yyyy-MM-dd`，空串表示未限制） */
export interface DateRange {
  /** 开始日期 */
  start: string;
  /** 结束日期 */
  end: string;
}

/** 快捷区间定义 */
export interface DateRangePreset {
  /** 稳定标识 */
  key: string;
  /** 展示文案 */
  label: string;
  /** 按「今天」计算区间 */
  resolve: (today: Date) => DateRange;
}

/** 日期格式化模板（与接口交互统一使用该格式） */
const ISO_DATE_PATTERN = 'yyyy-MM-dd';

/**
 * 把日期对象格式化为接口使用的日期字符串。
 *
 * @param date - 日期对象
 * @returns `yyyy-MM-dd` 字符串
 */
export function toIsoDate(date: Date): string {
  return format(date, ISO_DATE_PATTERN);
}

/** 常用快捷区间（顺序即界面展示顺序） */
export const DATE_RANGE_PRESETS: DateRangePreset[] = [
  {
    key: 'last7',
    label: '近 7 天',
    resolve: (today) => ({ start: toIsoDate(subDays(today, 6)), end: toIsoDate(today) }),
  },
  {
    key: 'last30',
    label: '近 30 天',
    resolve: (today) => ({ start: toIsoDate(subDays(today, 29)), end: toIsoDate(today) }),
  },
  {
    key: 'last90',
    label: '近 90 天',
    resolve: (today) => ({ start: toIsoDate(subDays(today, 89)), end: toIsoDate(today) }),
  },
  {
    key: 'thisMonth',
    label: '本月',
    resolve: (today) => ({
      start: toIsoDate(startOfMonth(today)),
      end: toIsoDate(endOfMonth(today)),
    }),
  },
  {
    key: 'lastMonth',
    label: '上月',
    resolve: (today) => {
      const previous = subMonths(today, 1);
      return {
        start: toIsoDate(startOfMonth(previous)),
        end: toIsoDate(endOfMonth(previous)),
      };
    },
  },
  {
    key: 'all',
    label: '全部时间',
    resolve: () => ({ start: '', end: '' }),
  },
];

/** 查询参数结构（空值不下发，避免后端把空串当条件） */
export interface DateRangeQuery {
  start_date?: string;
  end_date?: string;
}

/**
 * 把时间范围转换为列表接口的查询参数。
 *
 * @param range - 时间范围
 * @returns 仅包含已填写字段的查询参数
 */
export function toDateRangeQuery(range: DateRange): DateRangeQuery {
  const query: DateRangeQuery = {};
  if (range.start) query.start_date = range.start;
  if (range.end) query.end_date = range.end;
  return query;
}

/**
 * 判断日期字符串是否为合法日期。
 *
 * @param value - 待校验字符串
 * @returns 是否合法
 */
function isValidDateString(value: string): boolean {
  if (!value) return false;
  const parsed = parseISO(value);
  return isValid(parsed);
}

/**
 * 计算区间包含的自然日天数（含首尾）。
 *
 * @param range - 时间范围
 * @returns 天数，区间不完整或非法时为 0
 */
export function countDaysInRange(range: DateRange): number {
  if (!isValidDateString(range.start) || !isValidDateString(range.end)) return 0;
  const start = parseISO(range.start);
  const end = parseISO(range.end);
  return Math.abs(differenceInCalendarDays(end, start)) + 1;
}

/**
 * 规整区间：起止都填写且顺序颠倒时自动交换，保证 `start <= end`。
 *
 * @param range - 原始区间
 * @returns 规整后的区间
 */
function normalizeRange(range: DateRange): DateRange {
  if (range.start && range.end && range.start > range.end) {
    return { start: range.end, end: range.start };
  }
  return range;
}

/** 时间范围选择器属性 */
export interface DateRangePickerProps {
  /** 当前区间（受控） */
  value: DateRange;
  /** 变更回调 */
  onChange: (range: DateRange) => void;
  /** 自定义快捷区间，默认使用 `DATE_RANGE_PRESETS` */
  presets?: DateRangePreset[];
  /** 是否禁用 */
  disabled?: boolean;
  /** 是否展示「共 N 天」汇总，默认 true */
  showSummary?: boolean;
  /** 开始标签文案，默认「开始日期」 */
  startLabel?: string;
  /** 结束标签文案，默认「结束日期」 */
  endLabel?: string;
  className?: string;
}

/**
 * 时间范围选择器：两个原生日期输入 + 常用快捷区间（基于 date-fns，无额外依赖）。
 *
 * 选原生日期输入而不是自绘双月历：移动端可直接调用系统日历，体积与可访问性成本最低；
 * 快捷区间覆盖了绝大多数列表筛选场景。
 *
 * @param props - 组件属性
 * @returns 选择器元素
 */
export function DateRangePicker({
  value,
  onChange,
  presets = DATE_RANGE_PRESETS,
  disabled = false,
  showSummary = true,
  startLabel = '开始日期',
  endLabel = '结束日期',
  className,
}: DateRangePickerProps): React.JSX.Element {
  const inputId = React.useId();
  const [activePresetKey, setActivePresetKey] = React.useState<string>('');

  // 挂载后（以及区间变化时）回填「当前命中的快捷区间」，避免在首屏渲染时依赖系统时间
  React.useEffect(() => {
    const today = new Date();
    const matched = presets.find((preset) => {
      const resolved = preset.resolve(today);
      return resolved.start === value.start && resolved.end === value.end;
    });
    setActivePresetKey(matched ? matched.key : '');
  }, [presets, value.start, value.end]);

  /** 应用快捷区间 */
  const applyPreset = (preset: DateRangePreset): void => {
    onChange(normalizeRange(preset.resolve(new Date())));
  };

  /** 修改开始日期 */
  const handleStartChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
    onChange(normalizeRange({ start: event.target.value, end: value.end }));
  };

  /** 修改结束日期 */
  const handleEndChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
    onChange(normalizeRange({ start: value.start, end: event.target.value }));
  };

  const days = countDaysInRange(value);
  const hasRange = Boolean(value.start || value.end);

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <label htmlFor={`${inputId}-start`} className="text-xs font-medium text-muted-foreground">
            {startLabel}
          </label>
          <input
            id={`${inputId}-start`}
            type="date"
            value={value.start}
            max={value.end || undefined}
            disabled={disabled}
            onChange={handleStartChange}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>
        <span className="pb-2 text-sm text-muted-foreground">至</span>
        <div className="space-y-1">
          <label htmlFor={`${inputId}-end`} className="text-xs font-medium text-muted-foreground">
            {endLabel}
          </label>
          <input
            id={`${inputId}-end`}
            type="date"
            value={value.end}
            min={value.start || undefined}
            disabled={disabled}
            onChange={handleEndChange}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>
        {showSummary ? (
          <span className="flex items-center gap-1 pb-2 text-xs text-muted-foreground tabular-nums">
            <CalendarDays aria-hidden="true" className="h-3.5 w-3.5" />
            {days > 0 ? `共 ${days} 天` : hasRange ? '请补全起止日期' : '未限定时间范围'}
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {presets.map((preset) => {
          const active = preset.key === activePresetKey;
          return (
            <button
              key={preset.key}
              type="button"
              disabled={disabled}
              aria-pressed={active}
              onClick={() => applyPreset(preset)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
                active
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              {preset.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
