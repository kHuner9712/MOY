'use client';

import { ArrowRight, Search } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { RunStatusBadge } from '@/components/workflow/run-status-badge';
import { TaskMetricCards } from '@/components/workflow/task-metric-cards';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorState } from '@/components/ui/feedback';
import { Input } from '@/components/ui/input';
import { Pagination } from '@/components/ui/pagination';
import { CompactSelect } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableLoading,
  TableRow,
} from '@/components/ui/table';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { agentApi, pluginsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { TaskItem, TaskStatus } from '@/lib/types';

/** 任务状态筛选项（含任务级 `queued` / `waiting_confirm`） */
const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'queued', label: '排队中' },
  { value: 'pending', label: '等待开始' },
  { value: 'running', label: '执行中' },
  { value: 'waiting_confirm', label: '已暂停，待确认' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '未完成' },
  { value: 'cancelled', label: '已取消' },
];

/** 表格列数 */
const COLUMN_COUNT = 8;

/** 每页条数 */
const PAGE_SIZE = 20;

/**
 * 工作流列表页（P-13）：四维筛选 + 8 列表格。
 *
 * 四个筛选维度：插件 / 状态 / 发起人 / 时间范围（对应 A2 的加性 query）。
 * 列表只展示聚合信息；步骤、产物与核查清单在任务详情页。
 *
 * @returns 任务列表页元素
 */
export default function WorkflowsPage(): React.JSX.Element {
  const [pluginId, setPluginId] = React.useState<string>('');
  const [status, setStatus] = React.useState<string>('');
  const [initiatorId, setInitiatorId] = React.useState<string>('');
  const [createdFrom, setCreatedFrom] = React.useState<string>('');
  const [createdTo, setCreatedTo] = React.useState<string>('');
  const [page, setPage] = React.useState<number>(1);

  const installedQuery = useApiQuery(QUERY_KEYS.installedPlugins(), () => pluginsApi.installed());

  const params = {
    page,
    page_size: PAGE_SIZE,
    ...(pluginId ? { plugin_id: pluginId } : {}),
    ...(status ? { status } : {}),
    ...(initiatorId.trim() ? { initiator_id: initiatorId.trim() } : {}),
    ...(createdFrom ? { created_from: `${createdFrom}T00:00:00Z` } : {}),
    ...(createdTo ? { created_to: `${createdTo}T23:59:59Z` } : {}),
  };
  const tasksQuery = useApiQuery(QUERY_KEYS.pluginTasks(params), () => agentApi.listTasks(params));

  const tasks = tasksQuery.data?.items ?? [];
  const total = tasksQuery.data?.meta.total ?? 0;
  const hasFilter = Boolean(pluginId || status || initiatorId.trim() || createdFrom || createdTo);

  const pluginNameMap = React.useMemo<Record<string, string>>(() => {
    const map: Record<string, string> = {};
    for (const plugin of installedQuery.data?.items ?? []) {
      map[plugin.plugin_id] = plugin.name;
    }
    return map;
  }, [installedQuery.data]);

  const resetFilters = (): void => {
    setPluginId('');
    setStatus('');
    setInitiatorId('');
    setCreatedFrom('');
    setCreatedTo('');
    setPage(1);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <TaskMetricCards />

      <div className="flex flex-wrap items-end gap-2">
        <div className="sm:w-48">
          <CompactSelect
            aria-label="按插件筛选"
            value={pluginId}
            onChange={(event) => {
              setPluginId(event.target.value);
              setPage(1);
            }}
            options={[
              { value: '', label: '全部插件' },
              ...(installedQuery.data?.items ?? []).map((plugin) => ({
                value: plugin.plugin_id,
                label: plugin.name,
              })),
            ]}
          />
        </div>
        <div className="sm:w-40">
          <CompactSelect
            aria-label="按状态筛选"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            options={STATUS_OPTIONS}
          />
        </div>
        <div className="relative sm:w-44">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={initiatorId}
            onChange={(event) => {
              setInitiatorId(event.target.value);
              setPage(1);
            }}
            placeholder="发起人标识"
            aria-label="按发起人筛选"
            className="h-9 pl-9 text-xs"
          />
        </div>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Input
            type="date"
            value={createdFrom}
            aria-label="创建时间起"
            className="h-9 w-36 text-xs"
            onChange={(event) => {
              setCreatedFrom(event.target.value);
              setPage(1);
            }}
          />
          <span>至</span>
          <Input
            type="date"
            value={createdTo}
            aria-label="创建时间止"
            className="h-9 w-36 text-xs"
            onChange={(event) => {
              setCreatedTo(event.target.value);
              setPage(1);
            }}
          />
        </div>
        {hasFilter ? (
          <Button type="button" variant="ghost" size="sm" onClick={resetFilters}>
            清空筛选
          </Button>
        ) : null}
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>任务</TableHead>
            <TableHead>插件</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>进度</TableHead>
            <TableHead>提醒</TableHead>
            <TableHead>创建时间</TableHead>
            <TableHead>更新时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasksQuery.isLoading ? (
            <TableLoading columns={COLUMN_COUNT} rows={5} />
          ) : tasksQuery.error ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={COLUMN_COUNT} className="p-0">
                <ErrorState
                  message={toBusinessMessage(tasksQuery.error)}
                  onRetry={() => void tasksQuery.refetch()}
                />
              </TableCell>
            </TableRow>
          ) : tasks.length === 0 ? (
            <TableEmpty
              colSpan={COLUMN_COUNT}
              title={hasFilter ? '没有符合条件的任务' : '还没有自动化任务'}
              description={
                hasFilter
                  ? '换个筛选条件再看，也可以清空筛选查看全部任务'
                  : '添加岗位工作台后即可发起任务，执行记录会出现在这里'
              }
              action={
                hasFilter ? (
                  <Button type="button" variant="outline" size="sm" onClick={resetFilters}>
                    清空筛选
                  </Button>
                ) : (
                  <Button asChild size="sm">
                    <Link href="/plugins">去添加工作台</Link>
                  </Button>
                )
              }
            />
          ) : (
            tasks.map((task: TaskItem) => (
              <TableRow key={task.task_id}>
                <TableCell className="max-w-[16rem]">
                  <p className="truncate text-sm font-medium" title={task.goal}>
                    {orPlaceholder(task.goal, '未命名任务')}
                  </p>
                  <p className="truncate font-mono text-[11px] text-muted-foreground">
                    {task.task_id}
                  </p>
                </TableCell>
                <TableCell className="max-w-[10rem] truncate text-sm">
                  {task.plugin_id ? (
                    orPlaceholder(pluginNameMap[task.plugin_id], task.plugin_id)
                  ) : (
                    <Badge variant="outline">通用任务</Badge>
                  )}
                </TableCell>
                <TableCell>
                  <RunStatusBadge status={task.status as TaskStatus} />
                </TableCell>
                <TableCell className="text-sm tabular-nums">{task.progress}%</TableCell>
                <TableCell className="text-sm tabular-nums text-muted-foreground">
                  {task.warning_count > 0 ? `${task.warning_count} 处` : '—'}
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {formatDateTime(task.created_at)}
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {formatDateTime(task.updated_at)}
                </TableCell>
                <TableCell className="text-right">
                  <Button asChild variant="ghost" size="sm">
                    <Link href={`/workflows/${task.task_id}`}>
                      详情
                      <ArrowRight className="h-3.5 w-3.5" />
                    </Link>
                  </Button>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      <Pagination
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        onPageChange={setPage}
        disabled={tasksQuery.isLoading}
      />

    </div>
  );
}
