import { AlertTriangle, Inbox } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/** 加载骨架屏（总方案 3.5：所有等待有进度） */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return <div className={cn('skeleton', className)} {...props} />;
}

/** 三态的公共外壳：图标 + 文案 + 操作，保证三种状态视觉一致 */
function StateBlock({
  icon,
  title,
  description,
  action,
  tone,
  className,
}: {
  icon: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  tone: 'neutral' | 'danger';
  className?: string;
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-lg border py-12 text-center',
        tone === 'danger' ? 'border-destructive/30 bg-destructive/5' : 'border-dashed',
        className,
      )}
    >
      {icon}
      <div className={cn('font-medium', tone === 'danger' ? 'text-destructive' : '')}>{title}</div>
      {description ? (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action}
    </div>
  );
}

/**
 * 加载状态容器。
 *
 * @param props - 组件属性
 * @returns 加载态元素
 */
export function LoadingState({
  label = '正在加载…',
  description,
  className,
}: {
  /** 主文案 */
  label?: string;
  /** 补充说明（长等待时告诉用户在等什么） */
  description?: string;
  className?: string;
}): React.JSX.Element {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'flex flex-col items-center justify-center gap-2 py-12 text-sm text-muted-foreground',
        className,
      )}
    >
      <span className="flex items-center gap-3">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground" />
        <span>{label}</span>
      </span>
      {description ? <p className="max-w-md text-xs">{description}</p> : null}
    </div>
  );
}

/**
 * 空状态。
 *
 * 约定：`title` 说清「没有什么」，`description` 说清「为什么没有、下一步做什么」；
 * 有筛选条件时应改用「没有符合条件的…」这类文案，与「还没有…」区分开。
 *
 * @param props - 组件属性
 * @returns 空态元素
 */
export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  /** 主文案（必填：不允许空白空态） */
  title: string;
  /** 补充说明 */
  description?: string;
  /** 行动按钮 */
  action?: React.ReactNode;
  className?: string;
}): React.JSX.Element {
  return (
    <StateBlock
      tone="neutral"
      icon={<Inbox aria-hidden="true" className="h-6 w-6 text-muted-foreground/60" />}
      title={title}
      description={description}
      action={action}
      className={className}
    />
  );
}

/**
 * 错误状态（只展示业务语言文案与解决建议）。
 *
 * `onRetry` 是**必填**：任何错误态都必须给用户一条出路，
 * 不允许出现「报错了但没有重试入口」的死角（B14 三态统一约定）。
 *
 * @param props - 组件属性
 * @returns 错误态元素
 */
export function ErrorState({
  message,
  onRetry,
  description = '如果多次重试仍不成功，可联系团队管理员查看服务状态',
  retryLabel = '重试',
  className,
}: {
  /** 业务语言错误文案（禁止出现错误码与堆栈） */
  message: string;
  /** 重试回调（必填） */
  onRetry: () => void;
  /** 补充建议 */
  description?: string;
  /** 重试按钮文案 */
  retryLabel?: string;
  className?: string;
}): React.JSX.Element {
  return (
    <StateBlock
      tone="danger"
      icon={<AlertTriangle aria-hidden="true" className="h-6 w-6 text-destructive" />}
      title={message}
      description={description}
      className={className}
      action={
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border px-4 py-2 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {retryLabel}
        </button>
      }
    />
  );
}
