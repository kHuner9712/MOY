import type React from "react";
'use client';

import { useCallback, useEffect, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { agentApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { AgentTask, TaskStep } from '@/lib/types';

/** 任务状态展示映射（业务语言） */
const TASK_STATUS_TEXT: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
  pending: { label: '排队中', variant: 'secondary' },
  running: { label: '执行中', variant: 'warning' },
  completed: { label: '已完成', variant: 'success' },
  failed: { label: '未完成', variant: 'destructive' },
  cancelled: { label: '已取消', variant: 'secondary' },
};

/**
 * 工作流可视化页（S3 阶段实现完整节点视图）。
 *
 * 本期展示任务列表与步骤进度，数据来源为 A2/A4 标准接口。
 *
 * @returns 工作流页面元素
 */
export default function WorkflowsPage(): React.JSX.Element {
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [steps, setSteps] = useState<TaskStep[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState('');

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setErrorMessage('');
    try {
      const page = await agentApi.listTasks({ page: 1, page_size: 20 });
      setTasks(page.items);
      if (page.items.length > 0) {
        setSelectedTaskId((prev) => prev || (page.items[0]?.task_id ?? ''));
      }
    } catch (error) {
      setErrorMessage(toBusinessMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selectedTaskId) {
      setSteps([]);
      return;
    }
    void (async () => {
      try {
        const detail = await agentApi.listSteps(selectedTaskId);
        setSteps(detail.steps);
      } catch {
        setSteps([]);
      }
    })();
  }, [selectedTaskId]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold">任务进度</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          查看长任务的执行步骤与结果，随时可以查看详情或重试
        </p>
      </div>

      {loading ? (
        <LoadingState />
      ) : errorMessage ? (
        <ErrorState message={errorMessage} onRetry={() => void load()} />
      ) : tasks.length === 0 ? (
        <EmptyState
          title="暂无任务"
          description="在对话中发起一个复杂任务后，进度会显示在这里"
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>任务列表</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {tasks.map((task) => {
                  const status = TASK_STATUS_TEXT[task.status] ?? TASK_STATUS_TEXT.pending;
                  return (
                    <li
                      key={task.task_id}
                      onClick={() => setSelectedTaskId(task.task_id)}
                      className={`cursor-pointer py-3 ${
                        task.task_id === selectedTaskId ? 'text-primary' : ''
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium">{task.goal}</span>
                        <Badge variant={status?.variant ?? 'secondary'}>{status?.label}</Badge>
                      </div>
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full bg-primary transition-all"
                          style={{ width: `${task.progress}%` }}
                        />
                      </div>
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>执行步骤</CardTitle>
            </CardHeader>
            <CardContent>
              {steps.length === 0 ? (
                <EmptyState title="暂无步骤信息" description="任务开始执行后会展示详细步骤" />
              ) : (
                <ol className="space-y-3">
                  {steps.map((step) => (
                    <li key={step.step_id} className="flex gap-3">
                      <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary" />
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{step.title}</p>
                        {step.output_summary ? (
                          <p className="mt-0.5 text-sm text-muted-foreground">
                            {step.output_summary}
                          </p>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
