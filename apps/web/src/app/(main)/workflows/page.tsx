'use client';

import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { useApiQuery } from '@/hooks/use-api';
import { agentApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { TaskItem } from '@/lib/types';

/** 任务列表查询键（工作流域，B13/B14 统一用域名作第一段） */
const TASKS_KEY = ['workflows', 'tasks'] as const;

/**
 * 工作流页（任务进度与执行步骤）。
 *
 * 数据来源：A2 任务列表 + A4 步骤详情；两段数据各有独立三态，
 * 任一段失败都能就地重试，不会把整页拖成空白。
 *
 * @returns 工作流页元素
 */
export default function WorkflowsPage(): React.JSX.Element {
  const [selectedTaskId, setSelectedTaskId] = React.useState<string>('');

  const tasksQuery = useApiQuery(TASKS_KEY, () => agentApi.listTasks({ page: 1, page_size: 20 }));
  const tasks = React.useMemo<TaskItem[]>(() => tasksQuery.data?.items ?? [], [tasksQuery.data]);

  // 默认选中第一个任务（不覆盖用户选择）
  React.useEffect(() => {
    if (selectedTaskId || tasks.length === 0) return;
    const first = tasks[0];
    if (first) setSelectedTaskId(first.task_id);
  }, [selectedTaskId, tasks]);

  const stepsQuery = useApiQuery(
    ['workflows', 'tasks', selectedTaskId, 'steps'],
    () => agentApi.listSteps(selectedTaskId),
    { enabled: Boolean(selectedTaskId) },
  );

  const steps = stepsQuery.data?.steps ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold">工作流</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          查看长任务的执行步骤与结果，可随时中断或重新发起
        </p>
      </div>

      {tasksQuery.isLoading ? (
        <LoadingState label="正在加载任务…" />
      ) : tasksQuery.error ? (
        <ErrorState
          message={toBusinessMessage(tasksQuery.error)}
          onRetry={() => void tasksQuery.refetch()}
        />
      ) : tasks.length === 0 ? (
        <EmptyState
          title="还没有任务"
          description="在对话中发起一个需要多步完成的任务后，执行进度会显示在这里"
        />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>任务列表</CardTitle>
                <Button variant="ghost" size="sm" onClick={() => void tasksQuery.refetch()}>
                  刷新
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {tasks.map((task) => (
                  <li key={task.task_id}>
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelectedTaskId(task.task_id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          setSelectedTaskId(task.task_id);
                        }
                      }}
                      aria-current={task.task_id === selectedTaskId ? 'true' : undefined}
                      className={`w-full cursor-pointer py-3 text-left transition-colors ${
                        task.task_id === selectedTaskId ? 'text-primary' : 'hover:text-foreground'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium">
                          {orPlaceholder(task.goal, '未命名任务')}
                        </span>
                        <StatusBadge status={task.status} />
                      </div>
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary transition-all"
                          style={{ width: `${task.progress}%` }}
                        />
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        更新于 {formatDateTime(task.updated_at)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>执行步骤</CardTitle>
            </CardHeader>
            <CardContent>
              {!selectedTaskId ? (
                <EmptyState
                  title="请选择一个任务"
                  description="左侧点选任务即可查看每一步的执行情况"
                />
              ) : stepsQuery.isLoading ? (
                <LoadingState label="正在加载步骤…" />
              ) : stepsQuery.error ? (
                <ErrorState
                  message={toBusinessMessage(stepsQuery.error)}
                  onRetry={() => void stepsQuery.refetch()}
                />
              ) : steps.length === 0 ? (
                <EmptyState
                  title="暂无步骤信息"
                  description="任务开始执行后会按步骤展示进度，若长时间为空可刷新任务列表"
                />
              ) : (
                <ol className="space-y-3">
                  {steps.map((step) => (
                    <li key={step.step_id} className="flex gap-3">
                      <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary" />
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-medium">
                            {step.sequence}. {step.title}
                          </p>
                          <StatusBadge status={step.status} />
                        </div>
                        {step.output_summary ? (
                          <p className="text-sm text-muted-foreground">{step.output_summary}</p>
                        ) : null}
                        <p className="text-xs text-muted-foreground">
                          {formatDateTime(step.finished_at ?? step.started_at ?? '')}
                        </p>
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
