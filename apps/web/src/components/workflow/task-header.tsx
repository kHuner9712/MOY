'use client';

import { Clock, Repeat } from 'lucide-react';
import * as React from 'react';

import { RunStatusBadge } from '@/components/workflow/run-status-badge';
import { Progress } from '@/components/ui/progress';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { TaskDetail } from '@/lib/types';

/** 任务头部属性 */
export interface TaskHeaderProps {
  /** 任务详情（A3） */
  task: TaskDetail;
  /** 排队位次 */
  queuePosition?: number;
}

/**
 * 任务头部：标题 + 状态 + 进度 + 关键时间。
 *
 * 说明：A3 目前只返回 `task_id`，没有发起人展示名，因此这里**不臆造**「发起人」字段，
 * 只展示可核对的创建/更新时间与重试来源（B25 回传里已登记该缺口）。
 *
 * @param props - 组件属性
 * @returns 头部元素
 */
export function TaskHeader({ task, queuePosition }: TaskHeaderProps): React.JSX.Element {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <h2 className="truncate text-xl font-semibold" title={task.goal}>
            {orPlaceholder(task.goal, '未命名任务')}
          </h2>
          <p className="text-xs text-muted-foreground">
            任务号：<span className="font-mono">{task.task_id}</span>
          </p>
        </div>
        <RunStatusBadge status={task.status} queuePosition={queuePosition} />
      </div>

      <Progress value={task.progress} label="任务进度" />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <Clock aria-hidden="true" className="h-3 w-3" />
          创建于 {formatDateTime(task.created_at ?? '')}
        </span>
        <span>更新于 {formatDateTime(task.updated_at)}</span>
        {typeof task.steps_total === 'number' ? <span>共 {task.steps_total} 步</span> : null}
        {task.retry_of ? (
          <span className="inline-flex items-center gap-1">
            <Repeat aria-hidden="true" className="h-3 w-3" />
            由任务 {task.retry_of} 重试而来
          </span>
        ) : null}
      </div>
    </div>
  );
}
