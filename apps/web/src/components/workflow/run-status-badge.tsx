'use client';

import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import type { TaskStatus } from '@/lib/types';

/** 任务状态徽标属性 */
export interface RunStatusBadgeProps {
  /** 任务状态 */
  status: TaskStatus;
  /** 排队位次（`queued` 时展示「前面还有 N 个」） */
  queuePosition?: number;
  className?: string;
}

/** 任务状态 → 业务文案（与 DDL 七态一一对应） */
const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: '排队中',
  pending: '等待开始',
  running: '执行中',
  waiting_confirm: '已暂停，待确认',
  /** 矩阵第 2 项：计划已生成，等用户确认（确认前不执行任何步骤） */
  waiting_plan: '计划待确认',
  completed: '已完成',
  failed: '未完成',
  cancelled: '已取消',
};

/**
 * 任务状态徽标。
 *
 * 两个刻意为之的细节：
 * - `queued` 用「排队中 + 前面还有 N 个」而不是 loading 动画——用户在等一个**真实队列**，
 *   不是等一个不确定的加载；
 * - `waiting_confirm` 文案是「已暂停，待确认」而不是「失败/异常」——它是正常业务状态。
 *
 * @param props - 组件属性
 * @returns 徽标元素
 */
export function RunStatusBadge({
  status,
  queuePosition,
  className,
}: RunStatusBadgeProps): React.JSX.Element {
  const label = STATUS_LABELS[status] ?? '待确认';
  const isQueued = status === 'queued';

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusBadge status={status} label={label} className={className} />
      {isQueued && typeof queuePosition === 'number' && queuePosition > 1 ? (
        <span className="text-xs text-muted-foreground">前面还有 {queuePosition - 1} 个</span>
      ) : null}
    </span>
  );
}
