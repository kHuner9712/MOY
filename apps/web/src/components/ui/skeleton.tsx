import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * 骨架屏基础块。
 *
 * 说明：`components/ui/feedback.tsx` 中的 `Skeleton` 为早期版本，保持原样不改动
 * （既有页面仍在使用）；本文件的 `Skeleton` 为其超集，新增页面请统一从本文件引入。
 *
 * @param props - 组件属性
 * @returns 骨架块元素
 */
export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return <div className={cn('skeleton', className)} {...props} />;
}

/** 多行文本骨架属性 */
export interface SkeletonTextProps {
  /** 行数，默认 3 */
  lines?: number;
  /** 最后一行宽度比例，默认 0.6（模拟段落收尾） */
  lastLineWidth?: number;
  className?: string;
}

/**
 * 段落文本骨架。
 *
 * @param props - 组件属性
 * @returns 文本骨架元素
 */
export function SkeletonText({
  lines = 3,
  lastLineWidth = 0.6,
  className,
}: SkeletonTextProps): React.JSX.Element {
  const safeLines = Math.max(1, Math.trunc(lines));
  const clampWidth = Math.min(1, Math.max(0.1, lastLineWidth));
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: safeLines }).map((_, index) => {
        const isLast = index === safeLines - 1;
        return (
          <Skeleton
            key={`skeleton-text-line-${index}`}
            className="h-4"
            style={isLast ? { width: `${Math.round(clampWidth * 100)}%` } : undefined}
          />
        );
      })}
    </div>
  );
}

/** 表格骨架属性 */
export interface SkeletonTableProps {
  /** 列数，默认 4 */
  columns?: number;
  /** 行数，默认 5 */
  rows?: number;
  className?: string;
}

/**
 * 表格骨架（列表页首屏加载使用，保证「所有等待有进度」）。
 *
 * @param props - 组件属性
 * @returns 表格骨架元素
 */
export function SkeletonTable({
  columns = 4,
  rows = 5,
  className,
}: SkeletonTableProps): React.JSX.Element {
  const safeColumns = Math.max(1, Math.trunc(columns));
  const safeRows = Math.max(1, Math.trunc(rows));
  return (
    <div
      className={cn('w-full overflow-hidden rounded-lg border', className)}
      aria-busy="true"
      aria-live="polite"
    >
      <div className="flex items-center gap-4 border-b bg-muted/40 px-4 py-3">
        {Array.from({ length: safeColumns }).map((_, colIndex) => (
          <Skeleton key={`skeleton-head-${colIndex}`} className="h-3.5 flex-1" />
        ))}
      </div>
      <div className="divide-y">
        {Array.from({ length: safeRows }).map((_, rowIndex) => (
          <div key={`skeleton-row-${rowIndex}`} className="flex items-center gap-4 px-4 py-3">
            {Array.from({ length: safeColumns }).map((__, colIndex) => (
              <Skeleton key={`skeleton-cell-${rowIndex}-${colIndex}`} className="h-4 flex-1" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 卡片骨架属性 */
export interface SkeletonCardProps {
  /** 是否展示底部按钮占位，默认 true */
  withFooter?: boolean;
  className?: string;
}

/**
 * 卡片骨架。
 *
 * @param props - 组件属性
 * @returns 卡片骨架元素
 */
export function SkeletonCard({
  withFooter = true,
  className,
}: SkeletonCardProps): React.JSX.Element {
  return (
    <div className={cn('rounded-lg border bg-card p-6 shadow-sm', className)} aria-busy="true">
      <div className="flex items-center justify-between gap-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <SkeletonText className="mt-4" lines={2} />
      {withFooter ? (
        <div className="mt-6 flex items-center gap-2">
          <Skeleton className="h-9 w-24" />
          <Skeleton className="h-9 w-20" />
        </div>
      ) : null}
    </div>
  );
}

/** 列表骨架属性 */
export interface SkeletonListProps {
  /** 行数，默认 4 */
  rows?: number;
  className?: string;
}

/**
 * 简单列表骨架（成员列表、记录列表等）。
 *
 * @param props - 组件属性
 * @returns 列表骨架元素
 */
export function SkeletonList({ rows = 4, className }: SkeletonListProps): React.JSX.Element {
  const safeRows = Math.max(1, Math.trunc(rows));
  return (
    <div className={cn('divide-y rounded-lg border', className)} aria-busy="true">
      {Array.from({ length: safeRows }).map((_, index) => (
        <div key={`skeleton-list-${index}`} className="flex items-center gap-3 px-4 py-3">
          <Skeleton className="h-8 w-8 rounded-full" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-4 w-16" />
        </div>
      ))}
    </div>
  );
}
