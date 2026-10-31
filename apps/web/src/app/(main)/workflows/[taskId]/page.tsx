'use client';

import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import * as React from 'react';

import { ChecklistPanel } from '@/components/workflow/checklist-panel';
import { RunStatusBadge } from '@/components/workflow/run-status-badge';
import { StepTimeline } from '@/components/workflow/step-timeline';
import { TaskHeader } from '@/components/workflow/task-header';
import { ConfirmBar } from '@/components/workbench/confirm-bar';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { BusinessError, agentApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { orPlaceholder } from '@/lib/format';
import type { TaskWarning } from '@/lib/types';

/** 进行中状态（决定事件驱动的轮询兜底开关） */
const ACTIVE_STATUS: readonly string[] = ['queued', 'pending', 'running', 'waiting_confirm'];

/** 事件不可用时的兜底轮询间隔 */
const POLL_INTERVAL_MS = 5000;

/** SSE 事件批量刷新的节流窗口（毫秒）——后端一次性重放全部步骤时合并请求 */
const REFETCH_THROTTLE_MS = 300;

/** 「任务不存在」错误码（12001） */
const TASK_NOT_FOUND_CODE = 12001;

/** 「无权限」错误码（20003） */
const FORBIDDEN_CODE = 20003;

/**
 * 任务详情页（P-12）。
 *
 * 数据通道：A3（任务）+ A4（步骤）为主，A6 事件做增量刷新；事件断线时退回 5 秒轮询，
 * 重连后按 A3+A4 全量补齐（查询客户端已开启 `refetchOnReconnect`）。
 *
 * `waiting_confirm` 是**任务状态**：顶部给继续/重做/放弃三条动作，绝不渲染成错误页。
 *
 * @returns 任务详情页元素
 */
export default function TaskDetailPage(): React.JSX.Element {
  const params = useParams<{ taskId: string }>();
  const taskId = params.taskId;

  const [polling, setPolling] = React.useState<boolean>(false);

  const taskQuery = useApiQuery(QUERY_KEYS.pluginTask(taskId), () => agentApi.getTask(taskId), {
    enabled: Boolean(taskId),
    refetchInterval: polling ? POLL_INTERVAL_MS : false,
  });
  const stepsQuery = useApiQuery(
    QUERY_KEYS.pluginTaskSteps(taskId),
    () => agentApi.listSteps(taskId),
    { enabled: Boolean(taskId), refetchInterval: polling ? POLL_INTERVAL_MS : false },
  );

  const task = taskQuery.data;

  // 进行中才开兜底轮询；进终态立刻停
  React.useEffect(() => {
    const status = task?.status;
    setPolling(Boolean(status && ACTIVE_STATUS.includes(status)));
  }, [task?.status]);

  // A6 事件增量：收到任何步骤/任务事件就失效 A3+A4，由查询层合并最新数据
  // 审计 L6：SSE 事件密集（后端一次性重放全部步骤）时合并 refetch，避免请求突发
  const refetchTimer = React.useRef<ReturnType<typeof setTimeout>>(undefined);
  const throttledRefetch = React.useCallback(() => {
    if (refetchTimer.current !== undefined) return;
    refetchTimer.current = setTimeout(() => {
      refetchTimer.current = undefined;
      void taskQuery.refetch();
      void stepsQuery.refetch();
    }, REFETCH_THROTTLE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    return () => {
      if (refetchTimer.current !== undefined) clearTimeout(refetchTimer.current);
    };
  }, []);

  React.useEffect(() => {
    if (!taskId) return undefined;
    const stop = agentApi.streamTaskEvents(taskId, {
      onStepStarted: throttledRefetch,
      onStepFinished: throttledRefetch,
      onTaskCompleted: throttledRefetch,
      // 事件通道异常不弹错：兜底轮询会继续把状态拉回来
      onError: () => undefined,
    });
    return stop;
    // 只在任务切换时重建订阅；refetch 函数引用变化不需要重订阅
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  const continueMutation = useApiMutation(
    (decision: 'continue' | 'redo') => agentApi.retryTask(taskId, { decision }),
    {
      invalidateKeys: [QUERY_KEYS.pluginTask(taskId), QUERY_KEYS.pluginTaskSteps(taskId)],
      onSuccess: () => toast.success('已提交你的选择'),
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const cancelMutation = useApiMutation(() => agentApi.cancelTask(taskId), {
    invalidateKeys: [QUERY_KEYS.pluginTask(taskId), QUERY_KEYS.pluginTasks()],
    onSuccess: () => {
      toast.success('已放弃本次执行');
      void taskQuery.refetch();
    },
    onError: (error) => toast.error(toBusinessMessage(error)),
  });

  const handleRefetch = React.useCallback((): void => {
    void taskQuery.refetch();
    void stepsQuery.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------- 三态
  if (taskQuery.isLoading) {
    return (
      <div className="mx-auto max-w-5xl">
        <LoadingState label="正在加载任务详情…" />
      </div>
    );
  }

  if (taskQuery.error) {
    const error = taskQuery.error;
    const notFound = error instanceof BusinessError && error.code === TASK_NOT_FOUND_CODE;
    const forbidden = error instanceof BusinessError && error.code === FORBIDDEN_CODE;
    return (
      <div className="mx-auto max-w-5xl space-y-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/workflows">
            <ArrowLeft className="h-4 w-4" />
            返回任务列表
          </Link>
        </Button>
        {forbidden ? (
          <EmptyState
            title="你没有权限查看该任务"
            description="任务详情仅发起人本人或团队管理员可查看；如需协助请联系管理员"
          />
        ) : notFound ? (
          <EmptyState
            title="任务不存在或已过期"
            description="可能是链接失效或任务已被清理，去任务列表看看其他记录"
            action={
              <Button asChild>
                <Link href="/workflows">去任务列表</Link>
              </Button>
            }
          />
        ) : (
          <ErrorState message={toBusinessMessage(error)} onRetry={() => void taskQuery.refetch()} />
        )}
      </div>
    );
  }

  if (!task) {
    return (
      <div className="mx-auto max-w-5xl">
        <EmptyState
          title="没有找到这个任务"
          description="去任务列表看看其他记录"
          action={
            <Button asChild>
              <Link href="/workflows">去任务列表</Link>
            </Button>
          }
        />
      </div>
    );
  }

  const steps = stepsQuery.data?.steps ?? [];
  const checklist = task.result?.checklist ?? [];
  const warnings: TaskWarning[] = task.result?.warnings ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <Button asChild variant="ghost" size="sm">
        <Link href="/workflows">
          <ArrowLeft className="h-4 w-4" />
          返回任务列表
        </Link>
      </Button>

      <ConfirmBar
        open={task.status === 'waiting_confirm'}
        confirm={task.confirm ?? null}
        busy={continueMutation.isPending}
        onContinue={() => continueMutation.mutate('continue')}
        onRedo={() => continueMutation.mutate('redo')}
        onCancel={() => cancelMutation.mutate(undefined)}
      />

      <TaskHeader task={task} />

      {warnings.length > 0 ? (
        <div className="space-y-1 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="font-medium">本次执行有 {warnings.length} 处需要你留意</p>
          <ul className="list-inside list-disc">
            {warnings.map((warning) => (
              <li key={`${warning.step_seq}-${warning.message}`}>
                第 {warning.step_seq} 步：{orPlaceholder(warning.message)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>执行步骤</CardTitle>
          <CardDescription>
            失败或暂停的步骤会展开显示原因与建议动作；已完成步骤的产物可直接查看与下载
          </CardDescription>
        </CardHeader>
        <CardContent>
          {stepsQuery.isLoading ? (
            <LoadingState label="正在加载步骤…" />
          ) : stepsQuery.error ? (
            <ErrorState
              message={toBusinessMessage(stepsQuery.error)}
              onRetry={() => void stepsQuery.refetch()}
            />
          ) : (
            <StepTimeline taskId={taskId} steps={steps} onRefetch={handleRefetch} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>结果核查清单</CardTitle>
            <RunStatusBadge status={task.status} />
          </div>
          <CardDescription>逐条确认本次产出是否齐备，待人工确认项需你补充处理</CardDescription>
        </CardHeader>
        <CardContent>
          <ChecklistPanel items={checklist} />
        </CardContent>
      </Card>
    </div>
  );
}
