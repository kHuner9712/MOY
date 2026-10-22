'use client';

import { Copy, RotateCcw, SkipForward } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation } from '@/hooks/use-api';
import { agentApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { TaskStep } from '@/lib/types';

/** 失败动作区属性 */
export interface FailureActionsProps {
  /** 所属任务 */
  taskId: string;
  /** 失败步骤（A4） */
  step: TaskStep;
  /** 动作完成回调（父级刷新 A3/A4） */
  onDone?: () => void;
}

/**
 * 失败步骤的三个动作：重试本步 / 从头重试 / 复制诊断说明。
 *
 * 语义来自 WF1：
 * - 重试本步 = `from_step=<该步>`，前序产物复制不重跑（`retryable=false` 时不可用）；
 * - 从头重试 = `from_step=1`；
 * - 复制诊断说明给管理员——**文本里不含错误码、不含堆栈**（QA 会用正则扫）。
 *
 * @param props - 组件属性
 * @returns 动作区元素
 */
export function FailureActions({ taskId, step, onDone }: FailureActionsProps): React.JSX.Element {
  const retryMutation = useApiMutation(
    (fromStep: number) => agentApi.retryTask(taskId, { from_step: fromStep }),
    {
      invalidateKeys: [QUERY_KEYS.pluginTasks(), QUERY_KEYS.pluginTask(taskId)],
      onSuccess: (data) => {
        toast.success(`已发起重试（从第 ${data.from_step} 步开始）`);
        onDone?.();
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  /** 复制一段给管理员看的说明（无码值、无堆栈） */
  const copyDiagnostics = async (): Promise<void> => {
    const lines = [
      `任务：${taskId}`,
      `步骤：第 ${step.sequence} 步 ${step.title}`,
      `现象：${step.failure?.reason_text ?? '该步骤未完成'}`,
      `建议动作：${step.failure?.operator_action ?? '可重试本步或补充内容后重新发起'}`,
      `发生时间：${step.finished_at ?? step.started_at ?? '未记录'}`,
    ];
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      toast.success('已复制说明，可直接发给团队管理员');
    } catch {
      toast.error('复制失败，请手动选择文本复制');
    }
  };

  const retryable = step.failure?.retryable ?? false;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Tooltip content="该步骤需要先处理前置条件（如重新授权或连接工具）" disabled={retryable}>
        <span>
          <Button
            type="button"
            size="sm"
            disabled={!retryable || retryMutation.isPending}
            onClick={() => retryMutation.mutate(step.sequence)}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            重试本步
          </Button>
        </span>
      </Tooltip>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={retryMutation.isPending}
        onClick={() => retryMutation.mutate(1)}
      >
        <SkipForward className="h-3.5 w-3.5" />
        从头重试
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={() => void copyDiagnostics()}>
        <Copy className="h-3.5 w-3.5" />
        复制诊断说明
      </Button>
    </div>
  );
}
