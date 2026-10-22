'use client';

import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { orPlaceholder } from '@/lib/format';
import type { TaskStatus } from '@/lib/types';

/** 提交后状态条属性 */
export interface SubmitBarProps {
  /** 任务状态；空串表示还没有发起 */
  status: TaskStatus | '';
  /** 进度（0-100） */
  progress: number;
  /** 排队位次（`status=queued` 时下发） */
  queuePosition?: number;
  /** 任务标识（用于展示与后续跳转） */
  taskId?: string;
  /** 是否取消中 */
  cancelling?: boolean;
  /** 放弃本次（取消任务） */
  onCancel?: () => void;
}

/** 状态 → 提示文案（业务语言，不出现内部取值） */
function hintOf(status: TaskStatus | '', queuePosition?: number): string {
  switch (status) {
    case 'queued':
      return typeof queuePosition === 'number' && queuePosition > 1
        ? `前面还有 ${queuePosition - 1} 个任务在跑，稍后自动开始`
        : '任务已排队，马上开始';
    case 'pending':
      return '任务已创建，正在准备执行';
    case 'running':
      return '执行中，可以离开此页，稍后回来继续查看';
    case 'waiting_confirm':
      return '有一个步骤需要你确认后才会继续';
    case 'completed':
      return '本次执行已完成';
    case 'failed':
      return '本次执行未能完成，可按提示重试或补充内容后重新发起';
    case 'cancelled':
      return '本次执行已取消';
    default:
      return '';
  }
}

/**
 * 提交后的状态条：排队 / 执行中 / 需确认 / 完成 / 未完成。
 *
 * 设计要点：
 * - **排队不是错误**：`status=queued` 时只提示位次，不弹错误、不显示重试；
 * - 执行中明确告知「可以离开此页」（长任务不希望用户干等）；
 * - 只有进行类状态才给「放弃本次」，终态不再展示取消入口。
 *
 * @param props - 组件属性
 * @returns 状态条元素
 */
export function SubmitBar({
  status,
  progress,
  queuePosition,
  taskId,
  cancelling = false,
  onCancel,
}: SubmitBarProps): React.JSX.Element | null {
  if (!status) return null;

  const hint = hintOf(status, queuePosition);
  const running = status === 'queued' || status === 'pending' || status === 'running' || status === 'waiting_confirm';
  const safeProgress = Math.min(Math.max(Number.isFinite(progress) ? progress : 0, 0), 100);

  return (
    <section
      aria-label="执行状态"
      className="space-y-2 rounded-lg border bg-muted/20 px-4 py-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{hint}</p>
        {running && onCancel ? (
          <Button type="button" size="sm" variant="ghost" disabled={cancelling} onClick={onCancel}>
            {cancelling ? '取消中…' : '放弃本次'}
          </Button>
        ) : null}
      </div>
      {status !== 'queued' ? (
        <Progress value={safeProgress} size="sm" label="执行进度" />
      ) : null}
      {taskId ? (
        <p className="text-xs text-muted-foreground">
          任务号：<span className="font-mono">{orPlaceholder(taskId)}</span>
          （可在「工作流」里查看全部执行记录）
        </p>
      ) : null}
    </section>
  );
}
