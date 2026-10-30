'use client';

import * as React from 'react';

import { EmptyState, ErrorState } from '@/components/ui/feedback';
import { MEMORY_EMPTY_COPY, type MemoryEmptyKind } from '@/lib/memory-labels';

/** 空态 / 错误态属性 */
export interface MemoryEmptyProps {
  /** 空态种类（6 套文案，见 `MEMORY_EMPTY_COPY`） */
  kind: MemoryEmptyKind;
  /** 重试回调（`kind=failed` 时必用：错误态不允许没有出路） */
  onRetry: () => void;
  /** 「去看对话」回调（`kind=never` 时使用） */
  onGoChat?: () => void;
  /** 「重新开启」回调（`kind=disabled` 时使用） */
  onEnable?: () => void;
  /** 是否正在开启（禁用按钮） */
  enabling?: boolean;
  /** 附加类名 */
  className?: string;
}

/**
 * 记忆页的空态 / 错误态渲染器。
 *
 * 为什么把 6 套状态收在一个组件里：它们互斥且穷尽（`MemoryEmptyKind` 是联合类型，
 * 新增一种状态时 TypeScript 会逼着补文案），比散在页面里的三元表达式更不容易漏。
 *
 * @param props - 组件属性
 * @returns 空态或错误态元素
 */
export function MemoryEmpty({
  kind,
  onRetry,
  onGoChat,
  onEnable,
  enabling = false,
  className,
}: MemoryEmptyProps): React.JSX.Element {
  const copy = MEMORY_EMPTY_COPY[kind];

  if (kind === 'failed') {
    return (
      <ErrorState
        message={copy.title}
        description={copy.description}
        onRetry={onRetry}
        className={className}
      />
    );
  }

  let action: React.ReactNode;
  if (kind === 'never' && onGoChat) {
    action = (
      <button
        type="button"
        onClick={onGoChat}
        className="rounded-md border px-4 py-2 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {copy.actionLabel}
      </button>
    );
  } else if (kind === 'disabled' && onEnable) {
    action = (
      <button
        type="button"
        onClick={onEnable}
        disabled={enabling}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {enabling ? '正在开启…' : copy.actionLabel}
      </button>
    );
  }

  return (
    <EmptyState
      title={copy.title}
      description={copy.description}
      action={action}
      className={className}
    />
  );
}
