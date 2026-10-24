'use client';

import { ArrowLeft, ExternalLink } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { ConfirmBar } from '@/components/workbench/confirm-bar';
import { DeclarationForm } from '@/components/workbench/declaration-form';
import { HelpPanel } from '@/components/workbench/help-panel';
import { SubmitBar } from '@/components/workbench/submit-bar';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Timeline, type TimelineStep } from '@/components/ui/timeline';
import { toast } from '@/components/ui/toast';
import { useDeclarationForm } from '@/hooks/use-declaration-form';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { BusinessError, agentApi, pluginsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { orPlaceholder } from '@/lib/format';
import type { PluginDetail, PluginInputSpec, TaskStep } from '@/lib/types';

/** 进行中状态集合（用于决定是否轮询） */
const ACTIVE_STATUS: readonly string[] = ['queued', 'pending', 'running', 'waiting_confirm'];

/** 轮询间隔（毫秒） */
const POLL_INTERVAL_MS = 3000;

/** 插件未添加的错误码（16003） */
const PLUGIN_NOT_INSTALLED_CODE = 16003;

/** 最近任务条数 */
const RECENT_TASK_SIZE = 3;

/**
 * 工作台页面的区块定义。
 *
 * **这是「声明驱动」的唯一落点**：区块的种类与顺序由插件声明决定（有没有输入、有没有产物、
 * 有没有步骤、有没有配置），页面本身按 `kind` 渲染，**不含任何针对具体插件的分支**。
 */
type WorkbenchSectionKind = 'form' | 'steps' | 'outputs';

/**
 * 根据插件声明推导区块序列。
 *
 * 如果后端后续在 PL2 里直接下发 `sections[]`，**只需要改这一个函数**，页面不用动。
 *
 * @param plugin - 插件详情
 * @returns 区块序列
 */
export function buildSections(plugin: PluginDetail): WorkbenchSectionKind[] {
  const sections: WorkbenchSectionKind[] = [];
  if (plugin.inputs.length > 0) sections.push('form');
  if (plugin.step_titles.length > 0) sections.push('steps');
  if (plugin.outputs.length > 0) sections.push('outputs');
  return sections;
}

/**
 * 把 A4 的步骤转成时间线节点。
 *
 * @param steps - 步骤列表
 * @returns 时间线节点
 */
function toTimelineSteps(steps: TaskStep[]): TimelineStep[] {
  return steps.map((step) => ({
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
  }));
}

/**
 * 岗位工作台宿主（P-11）。
 *
 * 6 个插件共用**同一个**宿主：表单、步骤、产物、帮助栏全部由插件声明驱动，
 * 代码里没有任何「按具体插件分支」的判断（G-27 脚本会断言）。
 *
 * @returns 工作台页面元素
 */
export default function WorkbenchPage(): React.JSX.Element {
  const params = useParams<{ pluginId: string }>();
  const pluginId = params.pluginId;

  // 从对话页「目标式发起」跳转过来时读取 ?goal=：
  // 地址栏参数在挂载后读取，避免 useSearchParams 在静态渲染阶段要求 Suspense 边界
  const [goalParam, setGoalParam] = React.useState<string>('');
  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    const goal = new URLSearchParams(window.location.search).get('goal');
    if (goal) {
      setGoalParam(goal);
    }
  }, []);

  const detailQuery = useApiQuery(QUERY_KEYS.plugin(pluginId), () => pluginsApi.detail(pluginId), {
    enabled: Boolean(pluginId),
  });
  const recentTasksQuery = useApiQuery(
    QUERY_KEYS.pluginTasks({ plugin_id: pluginId, page: 1, page_size: RECENT_TASK_SIZE }),
    () => agentApi.listTasks({ plugin_id: pluginId, page: 1, page_size: RECENT_TASK_SIZE }),
    { enabled: Boolean(pluginId) },
  );

  const plugin = detailQuery.data;
  // 声明输入项稳定引用：详情未到位时保持同一空数组，避免预填合入逻辑被反复触发
  const formInputs = React.useMemo<PluginInputSpec[]>(() => plugin?.inputs ?? [], [plugin?.inputs]);
  // goal 由地址栏带入：预填首个文本输入，提交时随 A1 既有 goal 字段下发（契约零改）
  const form = useDeclarationForm(formInputs, goalParam || undefined);

  const [taskId, setTaskId] = React.useState<string>('');
  const [cancelling, setCancelling] = React.useState<boolean>(false);

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
  const steps = stepsQuery.data?.steps ?? [];
  const active = Boolean(task && ACTIVE_STATUS.includes(task.status));

  // 任务状态驱动轮询开关：进行中才轮询，进入终态立刻停（避免后台空转）
  React.useEffect(() => {
    const status = task?.status;
    setPolling(Boolean(status && ACTIVE_STATUS.includes(status)));
  }, [task?.status]);

  // 进入终态后刷新一次「最近执行」，用 ref 去重避免抖动
  const refreshedTaskRef = React.useRef<string>('');
  React.useEffect(() => {
    const status = task?.status;
    if (!status || ACTIVE_STATUS.includes(status)) return;
    const marker = `${taskId}:${status}`;
    if (refreshedTaskRef.current === marker) return;
    refreshedTaskRef.current = marker;
    void recentTasksQuery.refetch();
  }, [task?.status, taskId, recentTasksQuery]);

  const createMutation = useApiMutation(
    (inputs: Record<string, string | string[] | number | boolean>) =>
      agentApi.createTask({
        conversation_id: `conv_${pluginId}`,
        goal: goalParam || `${plugin?.name ?? '岗位任务'} · 手动发起`,
        plugin_id: pluginId,
        inputs,
      }),
    {
      onSuccess: (data) => {
        setTaskId(data.task_id);
        if (data.status === 'queued') {
          const position = data.queue_position;
          toast.success(
            typeof position === 'number' && position > 1
              ? `已排队，前面还有 ${position - 1} 个任务在跑，稍后自动开始`
              : '任务已排队，马上开始',
          );
        } else {
          toast.success('任务已发起，正在执行');
        }
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const continueMutation = useApiMutation(
    (decision: 'continue' | 'redo') => agentApi.retryTask(taskId, { decision }),
    {
      onSuccess: () => {
        toast.success('已提交你的选择');
        void taskQuery.refetch();
        void stepsQuery.refetch();
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const cancelMutation = useApiMutation(() => agentApi.cancelTask(taskId), {
    onSuccess: () => {
      toast.success('已放弃本次执行');
      void taskQuery.refetch();
    },
    onError: (error) => toast.error(toBusinessMessage(error)),
  });

  // ---------------------------------------------------------------- 三态
  if (detailQuery.isLoading) {
    return (
      <div className="mx-auto max-w-6xl">
        <LoadingState label="正在打开工作台…" />
      </div>
    );
  }

  if (detailQuery.error) {
    const notInstalled =
      detailQuery.error instanceof BusinessError &&
      detailQuery.error.code === PLUGIN_NOT_INSTALLED_CODE;
    return (
      <div className="mx-auto max-w-6xl space-y-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/plugins">
            <ArrowLeft className="h-4 w-4" />
            返回插件与工作台
          </Link>
        </Button>
        {notInstalled ? (
          <EmptyState
            title="该插件还没有添加"
            description="先到插件市场添加，添加后即可在这里发起任务"
            action={
              <Button asChild>
                <Link href={`/plugins/${pluginId}`}>去添加</Link>
              </Button>
            }
          />
        ) : (
          <ErrorState
            message={toBusinessMessage(detailQuery.error)}
            onRetry={() => void detailQuery.refetch()}
          />
        )}
      </div>
    );
  }

  if (!plugin) {
    return (
      <div className="mx-auto max-w-6xl">
        <EmptyState
          title="没有找到这个工作台"
          description="链接可能已失效，去插件市场重新选择一个"
          action={
            <Button asChild>
              <Link href="/plugins">去插件市场</Link>
            </Button>
          }
        />
      </div>
    );
  }

  const installed = plugin.installed_status !== 'none';
  const disabled = plugin.installed_status === 'disabled';
  const sections = buildSections(plugin);
  const confirm = task?.confirm ?? null;

  if (!installed) {
    return (
      <div className="mx-auto max-w-6xl space-y-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/plugins">
            <ArrowLeft className="h-4 w-4" />
            返回插件与工作台
          </Link>
        </Button>
        <EmptyState
          title="该插件还没有添加"
          description="先到插件市场添加，添加后即可在这里发起任务"
          action={
            <Button asChild>
              <Link href={`/plugins/${pluginId}`}>去添加</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <Button asChild variant="ghost" size="sm">
            <Link href={`/plugins/${pluginId}`}>
              <ArrowLeft className="h-4 w-4" />
              返回插件详情
            </Link>
          </Button>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold">{plugin.name}</h2>
            <StatusBadge status={plugin.installed_status} label="已添加" />
          </div>
          <p className="max-w-2xl text-sm text-muted-foreground">
            {orPlaceholder(plugin.summary, '按下面填写后即可开始')}
          </p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href={`/plugins/${pluginId}`}>
            管理插件
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </div>

      {disabled ? (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          该插件已停用，请联系管理员启用后再发起任务。
        </div>
      ) : null}

      <ConfirmBar
        open={task?.status === 'waiting_confirm'}
        confirm={confirm}
        busy={continueMutation.isPending}
        onContinue={() => continueMutation.mutate('continue')}
        onRedo={() => continueMutation.mutate('redo')}
        onCancel={() => cancelMutation.mutate(undefined)}
      />

      <SubmitBar
        status={task?.status ?? (createMutation.isPending ? 'pending' : '')}
        progress={task?.progress ?? 0}
        queuePosition={active ? createMutation.data?.queue_position : undefined}
        taskId={taskId}
        cancelling={cancelling || cancelMutation.isPending}
        onCancel={() => {
          setCancelling(true);
          cancelMutation.mutate(undefined);
        }}
      />

      <div className="flex gap-4">
        <div className="min-w-0 flex-1 space-y-4">
          {sections.map((kind) => {
            if (kind === 'form') {
              return (
                <Card key={kind}>
                  <CardHeader>
                    <CardTitle>输入信息</CardTitle>
                    <CardDescription>带 * 的为必填项</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <DeclarationForm
                      inputs={plugin.inputs}
                      values={form.values}
                      errors={form.errors}
                      onChange={form.setValue}
                      disabled={disabled || createMutation.isPending}
                      submitLabel={createMutation.isPending ? '提交中…' : '开始执行'}
                      onSubmit={() => {
                        if (!form.validate()) {
                          toast.error('还有必填项没填好，请检查标红的字段');
                          return;
                        }
                        createMutation.mutate(form.buildInputs());
                      }}
                    />
                  </CardContent>
                </Card>
              );
            }

            if (kind === 'steps') {
              return (
                <Card key={kind}>
                  <CardHeader>
                    <CardTitle>执行步骤</CardTitle>
                    <CardDescription>
                      {taskId ? '失败或等待确认的步骤会展开显示原因与建议动作' : '发起任务后这里会按步骤展示进度'}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    {!taskId ? (
                      <EmptyState
                        title="还没有执行记录"
                        description="填好上面内容就能开始，每一步都会显示在这里"
                      />
                    ) : stepsQuery.error ? (
                      <ErrorState
                        message={toBusinessMessage(stepsQuery.error)}
                        onRetry={() => void stepsQuery.refetch()}
                      />
                    ) : (
                      <Timeline
                        steps={toTimelineSteps(steps)}
                        emptyHint="任务刚开始，步骤马上就会出现"
                      />
                    )}
                  </CardContent>
                </Card>
              );
            }

            return (
              <Card key={kind}>
                <CardHeader>
                  <CardTitle>本次会产出</CardTitle>
                  <CardDescription>产物可在任务详情里查看与下载</CardDescription>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-1 text-sm">
                    {plugin.outputs.map((output) => (
                      <li key={output.key} className="flex items-center justify-between gap-2">
                        <span>{output.label || output.key}</span>
                        <span className="text-xs text-muted-foreground">
                          {output.type === 'file'
                            ? '文件'
                            : output.type === 'link'
                              ? '链接'
                              : output.type === 'table'
                                ? '表格'
                                : '文本'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            );
          })}
        </div>

        <HelpPanel
          plugin={plugin}
          recentTasks={recentTasksQuery.data?.items ?? []}
          loading={recentTasksQuery.isLoading}
        />
      </div>
    </div>
  );
}
