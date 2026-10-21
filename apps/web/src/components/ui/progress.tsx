import * as React from 'react';

import { cn } from '@/lib/utils';

/** 进度色调 */
export type ProgressTone = 'primary' | 'warning' | 'danger';

/** 进度条高度 */
export type ProgressSize = 'sm' | 'md' | 'lg';

/** 主色（正常）、琥珀（接近用尽）、红色（已超出） */
const TONE_BAR_CLASS: Record<ProgressTone, string> = {
  primary: 'bg-primary',
  warning: 'bg-amber-500',
  danger: 'bg-destructive',
};

/** 进度条轨道高度 */
const SIZE_TRACK_CLASS: Record<ProgressSize, string> = {
  sm: 'h-1.5',
  md: 'h-2.5',
  lg: 'h-3.5',
};

/**
 * 按用量百分比决定色调：低于 80% 用主色，80%~99% 用琥珀提醒，达到或超过 100% 用红色。
 *
 * @param percent - 用量百分比（可大于 100）
 * @returns 色调标识
 */
export function getProgressTone(percent: number): ProgressTone {
  if (!Number.isFinite(percent)) return 'primary';
  if (percent >= 100) return 'danger';
  if (percent >= 80) return 'warning';
  return 'primary';
}

/**
 * 把数值折算为便于阅读的中文量级（超过 1 万时按「万」展示）。
 *
 * @param value - 原始数值
 * @returns 展示字符串，如 `12.5万` / `860`
 */
export function formatAmount(value: number): string {
  if (!Number.isFinite(value)) return '0';
  const abs = Math.abs(value);
  if (abs >= 10000) {
    const scaled = Math.round((value / 10000) * 100) / 100;
    return `${scaled} 万`;
  }
  return String(Math.round(value));
}

/** 进度条属性 */
export interface ProgressProps {
  /** 已用量 */
  value: number;
  /** 总量上限，默认 100 */
  max?: number;
  /** 色调，默认按用量自动判断 */
  tone?: ProgressTone | 'auto';
  /** 高度，默认 `md` */
  size?: ProgressSize;
  /** 是否在右侧展示百分比，默认 true */
  showValue?: boolean;
  /** 无障碍名称（如「本月用量」） */
  label?: string;
  /** 覆盖右侧展示文案（如「1.2 万 / 2 万」） */
  valueText?: string;
  className?: string;
}

/**
 * 额度进度条（阈值变色，配合「用量与额度」页面使用）。
 *
 * @param props - 组件属性
 * @returns 进度条元素
 */
export function Progress({
  value,
  max = 100,
  tone = 'auto',
  size = 'md',
  showValue = true,
  label,
  valueText,
  className,
}: ProgressProps): React.JSX.Element {
  const safeMax = Number.isFinite(max) && max > 0 ? max : 100;
  const safeValue = Number.isFinite(value) ? value : 0;
  const rawPercent = (safeValue / safeMax) * 100;
  const displayPercent = Math.round(Number.isFinite(rawPercent) ? rawPercent : 0);
  const barPercent = Math.min(100, Math.max(0, displayPercent));
  const resolvedTone: ProgressTone = tone === 'auto' ? getProgressTone(displayPercent) : tone;

  return (
    <div className={cn('flex items-center gap-3', className)}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={safeMax}
        aria-valuenow={Math.round(safeValue)}
        aria-valuetext={valueText ?? `${displayPercent}%`}
        className={cn(
          'relative w-full overflow-hidden rounded-full bg-muted',
          SIZE_TRACK_CLASS[size],
        )}
      >
        <div
          className={cn(
            'h-full rounded-full transition-all duration-300',
            TONE_BAR_CLASS[resolvedTone],
          )}
          style={{ width: `${barPercent}%` }}
        />
      </div>
      {showValue ? (
        <span
          className={cn(
            'shrink-0 text-xs font-medium tabular-nums',
            resolvedTone === 'danger'
              ? 'text-destructive'
              : resolvedTone === 'warning'
                ? 'text-amber-600'
                : 'text-muted-foreground',
          )}
        >
          {valueText ?? `${displayPercent}%`}
        </span>
      ) : null}
    </div>
  );
}

/** 额度进度属性 */
export interface QuotaProgressProps {
  /** 已用量 */
  used: number;
  /** 总量 */
  total: number;
  /** 计量单位，如「字」「次」，默认空 */
  unit?: string;
  /** 标题，如「本月用量」 */
  label?: string;
  /** 补充说明 */
  hint?: string;
  /** 高度，默认 `md` */
  size?: ProgressSize;
  className?: string;
}

/**
 * 额度用量条：标题 + 进度 + 「已用 / 总量」说明，超限时给出业务语言提示。
 *
 * @param props - 组件属性
 * @returns 额度进度元素
 */
export function QuotaProgress({
  used,
  total,
  unit = '',
  label,
  hint,
  size = 'md',
  className,
}: QuotaProgressProps): React.JSX.Element {
  const safeTotal = Number.isFinite(total) && total > 0 ? total : 0;
  const safeUsed = Number.isFinite(used) && used > 0 ? used : 0;
  const percent = safeTotal > 0 ? (safeUsed / safeTotal) * 100 : 0;
  const exceeded = safeTotal > 0 && safeUsed > safeTotal;
  const summary = `已用 ${formatAmount(safeUsed)}${unit} / ${formatAmount(safeTotal)}${unit}`;

  return (
    <div className={cn('space-y-2', className)}>
      {label ? (
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-medium">{label}</span>
          <span className="text-xs text-muted-foreground tabular-nums">{summary}</span>
        </div>
      ) : null}
      <Progress
        value={safeUsed}
        max={safeTotal > 0 ? safeTotal : 100}
        size={size}
        tone="auto"
        showValue={false}
        label={label ?? summary}
      />
      {exceeded ? (
        <p className="text-xs font-medium text-destructive">
          用量已超出当前额度，可调整用量或联系团队管理员提升额度
        </p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}
