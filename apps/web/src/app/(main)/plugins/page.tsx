'use client';

import { ExternalLink, Plug } from 'lucide-react';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { TabPanel, Tabs, useUrlTab } from '@/components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { toolApi, toolsExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';

/** 最近调用展示条数 */
const RECENT_EXECUTION_SIZE = 20;

/**
 * 插件市场页（P-03 深化）。
 *
 * 两个页签：
 * - **插件市场**：工具卡片完全由 T1 注册表驱动，前端不硬编码工具列表；
 * - **办公工具**：已接入工具的能力清单 + 最近调用记录（TL1，只读）。
 *
 * 空态与失败都用业务语言说明「为什么空、下一步做什么」，不使用占位式说法。
 *
 * @returns 插件市场页元素
 */
export default function PluginsPage(): React.JSX.Element {
  const [tab, setTab] = useUrlTab('tab', 'market');

  const toolsQuery = useApiQuery(QUERY_KEYS.tools(), () => toolApi.registry());
  const executionsQuery = useApiQuery(
    QUERY_KEYS.toolExecutions(),
    () => toolsExtApi.listExecutions({ page: 1, page_size: RECENT_EXECUTION_SIZE }),
    { enabled: tab === 'tools' },
  );

  const tools = toolsQuery.data?.tools ?? [];
  const executions = executionsQuery.data?.items ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <Tabs
        items={[
          { value: 'market', label: '插件市场' },
          { value: 'tools', label: '办公工具', count: tools.length },
        ]}
        value={tab}
        onValueChange={setTab}
        ariaLabel="插件视图切换"
      />

      <TabPanel value="market" activeValue={tab}>
        <div className="space-y-4">
          <div>
            <h2 className="text-lg font-semibold">可连接的工具</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              连接后即可在对话与任务中直接使用，无需切换到其他系统
            </p>
          </div>

          {toolsQuery.isLoading ? (
            <LoadingState label="正在加载工具清单…" />
          ) : toolsQuery.error ? (
            <ErrorState
              message={toBusinessMessage(toolsQuery.error)}
              onRetry={() => void toolsQuery.refetch()}
            />
          ) : tools.length === 0 ? (
            <EmptyState
              title="还没有可连接的工具"
              description="工具由平台统一接入，接入后会出现在这里；如需接入内部系统请联系平台管理员"
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {tools.map((tool) => (
                <Card key={tool.tool_type}>
                  <CardHeader>
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle className="flex items-center gap-2">
                        <Plug aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
                        {tool.display_name}
                      </CardTitle>
                      <Badge variant={tool.status === 'available' ? 'success' : 'secondary'}>
                        {tool.status === 'available' ? '可连接' : '接入中'}
                      </Badge>
                    </div>
                    <CardDescription>支持 {tool.operations.length} 项能力</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <ul className="space-y-1 text-sm text-muted-foreground">
                      {tool.operations.slice(0, 5).map((operation) => (
                        <li key={operation.operation}>· {operation.display_name}</li>
                      ))}
                      {tool.operations.length > 5 ? (
                        <li>· 另有 {tool.operations.length - 5} 项能力</li>
                      ) : null}
                    </ul>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </TabPanel>

      <TabPanel value="tools" activeValue={tab}>
        <Card>
          <CardHeader>
            <CardTitle>最近调用</CardTitle>
            <CardDescription>
              只展示调用结果与业务化名称，不含任何参数内容与账号敏感信息
            </CardDescription>
          </CardHeader>
          <CardContent>
            {executionsQuery.isLoading ? (
              <LoadingState label="正在加载调用记录…" />
            ) : executionsQuery.error ? (
              <ErrorState
                message={toBusinessMessage(executionsQuery.error)}
                onRetry={() => void executionsQuery.refetch()}
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>工具</TableHead>
                    <TableHead>操作</TableHead>
                    <TableHead>结果</TableHead>
                    <TableHead>操作人</TableHead>
                    <TableHead>调用时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {executions.length === 0 ? (
                    <TableEmpty
                      colSpan={5}
                      title="还没有调用记录"
                      description="在对话或任务里使用过工具后，这里会留下最近 20 次记录"
                    />
                  ) : (
                    executions.map((execution) => (
                      <TableRow key={execution.execution_id}>
                        <TableCell className="text-sm">
                          {orPlaceholder(execution.tool_display_name, execution.tool_type)}
                        </TableCell>
                        <TableCell className="text-sm">
                          {orPlaceholder(execution.operation_display_name, execution.operation)}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={execution.status} />
                          {execution.error_message ? (
                            <p className="mt-1 max-w-[16rem] text-xs text-destructive">
                              {execution.error_message}
                            </p>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {orPlaceholder(execution.operator_name)}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                          {formatDateTime(execution.executed_at)}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </TabPanel>

      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <ExternalLink aria-hidden="true" className="h-3 w-3" />
        需要接入新的内部系统？把系统名称与用途发给平台管理员即可评估。
      </p>
    </div>
  );
}
