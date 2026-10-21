'use client';

import * as React from 'react';

import { cn } from '@/lib/utils';

/** 气泡出现方向 */
export type TooltipSide = 'top' | 'bottom';

/** 提示气泡属性 */
export interface TooltipProps {
  /** 气泡内容（业务语言；为空时不展示气泡） */
  content: React.ReactNode;
  /** 触发元素（任意节点，内部包一层 `inline-flex` 容器） */
  children: React.ReactNode;
  /** 出现方向，默认 `top` */
  side?: TooltipSide;
  /** 关闭气泡（例如按钮可用、无需解释原因时） */
  disabled?: boolean;
  /** 外层容器类名 */
  className?: string;
}

/**
 * 轻量提示气泡。
 *
 * 为什么不用第三方浮层：本阶段不新增依赖，且这里的用法集中在
 * 「按钮为什么是灰的」「字段是什么口径」两类静态说明，`hover` / `focus` 足够，
 * 不需要定位跟随与滚动重排。真正需要浮层的场景（下拉、弹窗）已由既有组件承担。
 *
 * @param props - 组件属性
 * @returns 触发元素 + 气泡
 */
export function Tooltip({
  content,
  children,
  side = 'top',
  disabled = false,
  className,
}: TooltipProps): React.JSX.Element {
  const hasContent = Boolean(content);
  if (disabled || !hasContent) {
    return <>{children}</>;
  }

  return (
    <span className={cn('group relative inline-flex', className)}>
      {children}
      <span
        role="tooltip"
        className={cn(
          'pointer-events-none absolute left-1/2 z-50 w-max max-w-[16rem] -translate-x-1/2 rounded-md border bg-popover px-2.5 py-1.5 text-left text-xs leading-relaxed text-popover-foreground opacity-0 shadow-md transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100',
          side === 'top' ? 'bottom-full mb-2' : 'top-full mt-2',
        )}
      >
        {content}
      </span>
    </span>
  );
}
