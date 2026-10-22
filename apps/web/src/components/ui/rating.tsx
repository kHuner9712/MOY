'use client';

import { Star } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/** 星级尺寸 */
export type RatingSize = 'sm' | 'md';

/** 星级评分属性 */
export interface RatingProps {
  /** 当前评分（0-5，可带小数；只读时按四舍五入填充） */
  value: number;
  /** 评价数量（只读场景展示为「4.6（12 条评价）」） */
  count?: number;
  /** 只读：仅展示，不可点 */
  readOnly?: boolean;
  /** 选择回调（1-5）；只读时忽略 */
  onChange?: (value: number) => void;
  /** 尺寸，默认 `md` */
  size?: RatingSize;
  /** 是否展示数值文本，默认 true */
  showValue?: boolean;
  /** 无障碍名称（读屏用，如「插件评分」） */
  label?: string;
  /** 无评分时的空态文案，默认「暂无评分」 */
  emptyLabel?: string;
  className?: string;
}

/** 评分最大值 */
const MAX_STARS = 5;

/** 星星尺寸映射 */
const STAR_SIZE_CLASS: Record<RatingSize, string> = {
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
};

/** 把任意评分收敛到 0-5 */
function clampRating(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(Math.max(value, 0), MAX_STARS);
}

/**
 * 星级评分（只读 / 可点两种形态）。
 *
 * 设计要点：
 * - 只读形态展示聚合评分（可含小数，按四舍五入填星，数值文本给精确值，避免「4.6 显示 4 颗」的误解）；
 * - 可点形态用 `radiogroup` 语义，键盘可操作，每颗星有「N 星」文案；
 * - 颜色用设计令牌（amber 家族与既有 warning 徽标同源），不引入新色值。
 *
 * @param props - 组件属性
 * @returns 星级评分元素
 */
export function Rating({
  value,
  count,
  readOnly = false,
  onChange,
  size = 'md',
  showValue = true,
  label = '评分',
  emptyLabel = '暂无评分',
  className,
}: RatingProps): React.JSX.Element {
  const safeValue = clampRating(value);
  const filledStars = Math.round(safeValue);
  const starClass = STAR_SIZE_CLASS[size];
  const [hovered, setHovered] = React.useState<number>(0);

  const interactive = !readOnly && typeof onChange === 'function';
  const displayStars = interactive && hovered > 0 ? hovered : filledStars;

  /** 星星本体（同一份视觉，避免两套样式漂移） */
  const renderStar = (index: number): React.JSX.Element => {
    const active = index <= displayStars;
    return (
      <Star
        aria-hidden="true"
        className={cn(
          starClass,
          'shrink-0 transition-colors',
          active ? 'fill-amber-400 text-amber-500' : 'text-muted-foreground/40',
        )}
      />
    );
  };

  const isEmpty = safeValue <= 0 && (count ?? 0) === 0;

  if (!interactive) {
    if (isEmpty) {
      return (
        <span
          className={cn('inline-flex items-center gap-1 text-xs text-muted-foreground', className)}
          aria-label={`${label}：${emptyLabel}`}
        >
          <Star aria-hidden="true" className={cn(starClass, 'text-muted-foreground/30')} />
          {emptyLabel}
        </span>
      );
    }
    return (
      <span
        className={cn('inline-flex items-center gap-1', className)}
        aria-label={`${label}：${safeValue.toFixed(1)} 分（满分 ${MAX_STARS} 分）`}
      >
        <span className="inline-flex items-center gap-0.5">
          {Array.from({ length: MAX_STARS }, (_, index) => (
            <React.Fragment key={`star-${index + 1}`}>{renderStar(index + 1)}</React.Fragment>
          ))}
        </span>
        {showValue ? (
          <span className="text-xs tabular-nums text-muted-foreground">
            {safeValue > 0 ? safeValue.toFixed(1) : '暂无评分'}
            {typeof count === 'number' && count > 0 ? `（${count} 条评价）` : ''}
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <span
      role="radiogroup"
      aria-label={label}
      className={cn('inline-flex items-center gap-1', className)}
      onMouseLeave={() => setHovered(0)}
    >
      {Array.from({ length: MAX_STARS }, (_, index) => {
        const starValue = index + 1;
        return (
          <button
            key={`rating-${starValue}`}
            type="button"
            role="radio"
            aria-checked={starValue === filledStars}
            aria-label={`${starValue} 星`}
            onMouseEnter={() => setHovered(starValue)}
            onFocus={() => setHovered(starValue)}
            onClick={() => onChange(starValue)}
            className="rounded p-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {renderStar(starValue)}
          </button>
        );
      })}
      {showValue ? (
        <span className="ml-1 text-xs tabular-nums text-muted-foreground">
          {hovered > 0 ? `${hovered} 星` : filledStars > 0 ? `${filledStars} 星` : '请选择星级'}
        </span>
      ) : null}
    </span>
  );
}
