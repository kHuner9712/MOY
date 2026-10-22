'use client';

import * as React from 'react';

import { ArtifactCard } from '@/components/workflow/artifact-card';
import { FailureActions } from '@/components/workflow/failure-actions';
import { Timeline, type TimelineStep } from '@/components/ui/timeline';
import type { TaskStep } from '@/lib/types';

/** 步骤时间线属性 */
export interface StepTimelineProps {
  /** 所属任务 */
  taskId: string;
  /** A4 步骤列表 */
  steps: TaskStep[];
  /** 失败步骤动作完成后的刷新回调 */
  onRefetch?: () => void;
}

/**
 * 任务步骤时间线（业务版）。
 *
 * 在通用 `ui/timeline` 之上补两件事：
 * - 每步产物用 `ArtifactCard` 渲染（含 WF2 编辑入口与只读约束）；
 * - 失败步骤给 `FailureActions`（重试本步 / 从头重试 / 复制诊断说明）。
 *
 * @param props - 组件属性
 * @returns 时间线元素
 */
export function StepTimeline({ taskId, steps, onRefetch }: StepTimelineProps): React.JSX.Element {
  const timelineSteps: TimelineStep[] = steps.map((step) => ({
    id: step.step_id,
    sequence: step.sequence,
    title: step.title,
    status: step.status,
    summary: step.output_summary,
    failure: step.failure
      ? {
          reason_text: step.failure.reason_text,
          retryable: step.failure.retryable,
          operator_action: step.failure.operator_action,
        }
      : null,
    durationMs: step.duration_ms ?? null,
    artifacts:
      step.artifacts && step.artifacts.length > 0 ? (
        <div className="space-y-2">
          {step.artifacts.map((artifact) => (
            <ArtifactCard
              key={artifact.artifact_id}
              taskId={taskId}
              artifact={artifact}
              onUpdated={onRefetch}
            />
          ))}
        </div>
      ) : null,
    actions:
      step.status === 'failed' ? (
        <FailureActions taskId={taskId} step={step} onDone={onRefetch} />
      ) : null,
  }));

  return <Timeline steps={timelineSteps} emptyHint="任务刚开始，步骤马上就会出现" />;
}
