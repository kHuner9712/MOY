'use client';

import { History, Trash2 } from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { formatDate } from '@/lib/format';
import { MEMORY_CATEGORY_LABELS } from '@/lib/memory-labels';
import type { MemoryItem } from '@/lib/types';
import { cn } from '@/lib/utils';

/** 单条信息卡片属性 */
export interface MemoryCardProps {
  /** 列表项 */
  item: MemoryItem;
  /** 删除回调（由页面弹二次确认） */
  onDelete: (item: MemoryItem) => void;
  /** 是否正在删除（禁用按钮，避免重复提交） */
  deleting?: boolean;
  /** 附加类名 */
  className?: string;
}

/**
 * 单条「关于你的信息」卡片。
 *
 * 三个展示口径（对应 PRD §6.1 的界面稿）：
 * 1. 类别用中文名（`MEMORY_CATEGORY_LABELS`），**内部标识不上屏**；
 * 2. 推测性表述（`confidence=low`）标「待确认」——它不参与回答，但用户有权看见；
 * 3. 已被新说法取代的历史值标「历史」，并折叠成「你之前说过…」，不参与当前值。
 *
 * @param props - 组件属性
 * @returns 卡片元素
 */
export function MemoryCard({
  item,
  onDelete,
  deleting = false,
  className,
}: MemoryCardProps): React.JSX.Element {
  const isHistory = Boolean(item.superseded_by);
  const isLowConfidence = item.confidence === 'low';

  return (
    <article
      className={cn(
        'rounded-lg border bg-card p-4 text-card-foreground shadow-sm',
        isHistory ? 'border-dashed opacity-80' : '',
        className,
      )}
      aria-label={MEMORY_CATEGORY_LABELS[item.category]}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{MEMORY_CATEGORY_LABELS[item.category]}</Badge>
        {isLowConfidence ? (
          <Badge variant="warning" title="这条信息是推测出来的，我暂时不会用它回答你">
            待确认
          </Badge>
        ) : null}
        {isHistory ? (
          <Badge variant="outline">
            <History aria-hidden="true" className="mr-1 h-3 w-3" />
            历史
          </Badge>
        ) : null}
        <span className="ml-auto text-xs text-muted-foreground">
          更新于 {formatDate(item.updated_at)}
        </span>
      </div>

      <p className="mt-3 text-sm leading-relaxed">{item.content}</p>

      {isHistory ? (
        <details className="mt-2 text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">你之前说过…</summary>
          <p className="mt-1">这条是历史值，已经有新的说法取代它，不会被用来回答你。</p>
        </details>
      ) : null}

      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {item.source_conversation_id ? '来自一次对话' : '来自设置'}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => onDelete(item)}
          disabled={deleting}
          aria-label={`删除这条信息：${item.content}`}
        >
          <Trash2 aria-hidden="true" className="mr-1 h-4 w-4" />
          删除
        </Button>
      </div>
    </article>
  );
}
