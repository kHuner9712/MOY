'use client';

import { ExternalLink } from 'lucide-react';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { CategoryBar } from '@/components/plugin/category-bar';
import { InstalledTable } from '@/components/plugin/installed-table';
import { PluginCard } from '@/components/plugin/plugin-card';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Pagination } from '@/components/ui/pagination';
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TabPanel, Tabs, useUrlTab } from '@/components/ui/tabs';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { pluginsApi, toolApi, toolsExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { InstalledPluginItem, PluginCategoryItem, PluginMarketItem, PluginMarketSort } from '@/lib/types';

/** 市场每页条数（与后端默认一致） */
const MARKET_PAGE_SIZE = 12;

/** 最近调用展示条数 */
const RECENT_EXECUTION_SIZE = 20;

/** 分类兜底（后端未返回时用空数组，避免 undefined 判断散落） */
const EMPTY_CATEGORIES: PluginCategoryItem[] = [];

/**
 * 插件市场页（P-09）：三标签「插件市场 / 已添加 / 办公工具」。
 *
 * 硬约束：
 * - **免费版不降级**：`installable=false` 的插件照常出现在列表里，
 *   只有点安装时提示「当前套餐不含插件能力」并给「查看套餐」入口；
 * - 空态区分「没有匹配的插件」（有筛选）与「还没有可用的插件」（无筛选）；
 * - 写操作成功后按 `['plugins']` 前缀失效，市场与已添加列表一起刷新。
 *
 * @returns 插件市场页元素
 */
export default function PluginsPage(): React.JSX.Element {
  const router = useRouter();
  const [tab, setTab] = useUrlTab('tab', 'market');

  // ---------------------------------------------------------------- 市场
  const [category, setCategory] = React.useState<string>('');
  const [keyword, setKeyword] = React.useState<string>('');
  const [sort, setSort] = React.useState<PluginMarketSort>('recommended');
  const [page, setPage] = React.useState<number>(1);
  const [upgradePromptOpen, setUpgradePromptOpen] = React.useState<boolean>(false);

  const marketParams = {
    page,
    page_size: MARKET_PAGE_SIZE,
    sort,
    ...(category ? { category } : {}),
    ...(keyword.trim() ? { keyword: keyword.trim() } : {}),
  };
  const marketQuery = useApiQuery(QUERY_KEYS.plugins(marketParams), () =>
    pluginsApi.market(marketParams),
  );

  // -------------------------------------------------------------- 已添加
  const installedQuery = useApiQuery(QUERY_KEYS.installedPlugins(), () => pluginsApi.installed(), {
    enabled: tab === 'installed',
  });

  const [uninstallTarget, setUninstallTarget] = React.useState<InstalledPluginItem | null>(null);

  const toggleStateMutation = useApiMutation(
    (variables: { pluginId: string; next: 'enabled' | 'disabled' }) =>
      pluginsApi.setState(variables.pluginId, variables.next),
    {
      invalidateKeys: [['plugins']],
      onSuccess: (data) => {
        const suffix =
          data.running_task_count > 0
            ? `（${data.running_task_count} 个在跑任务不受影响，会继续完成）`
            : '';
        toast.success(data.status === 'enabled' ? `已启用${suffix}` : `已停用${suffix}`);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const uninstallMutation = useApiMutation((pluginId: string) => pluginsApi.uninstall(pluginId), {
    invalidateKeys: [['plugins']],
    onSuccess: (data) => {
      toast.success(
        data.kept_task_count > 0
          ? `已卸载，${data.kept_task_count} 个历史任务记录已保留`
          : '已卸载',
      );
      setUninstallTarget(null);
    },
    onError: (error) => toast.error(toBusinessMessage(error)),
  });

  const upgradeMutation = useApiMutation(
    (variables: { pluginId: string; version: string }) =>
      pluginsApi.upgrade(variables.pluginId, variables.version),
    {
      invalidateKeys: [['plugins']],
      onSuccess: () => toast.success('已更新到最新版本'),
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  // ------------------------------------------------- 办公工具（沿用既有页面）
  const toolsQuery = useApiQuery(QUERY_KEYS.tools(), () => toolApi.registry(), {
    enabled: tab === 'tools',
  });
  const executionsQuery = useApiQuery(
    QUERY_KEYS.toolExecutions(),
    () => toolsExtApi.listExecutions({ page: 1, page_size: RECENT_EXECUTION_SIZE }),
    { enabled: tab === 'tools' },
  );

  const marketItems = marketQuery.data?.items ?? [];
  const categories = marketQuery.data?.categories ?? EMPTY_CATEGORIES;
  const marketTotal = marketQuery.data?.meta.total ?? 0;
  const installedItems = installedQuery.data?.items ?? [];
  const tools = toolsQuery.data?.tools ?? [];
  const executions = executionsQuery.data?.items ?? [];
  const hasMarketFilter = Boolean(category) || Boolean(keyword.trim());

  /** 卡片主按钮：统一进详情页完成（详情页里有授权抽屉与二次确认） */
  const handlePrimaryAction = (plugin: PluginMarketItem): void => {
    if (!plugin.installable) {
      setUpgradePromptOpen(true);
      return;
    }
    router.push(`/plugins/${plugin.plugin_id}?action=install`);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <Tabs
        items={[
          { value: 'market', label: '插件市场' },
          { value: 'installed', label: '已添加', count: installedItems.length },
          { value: 'tools', label: '办公工具', count: tools.length },
        ]}
        value={tab}
        onValueChange={setTab}
        ariaLabel="插件视图切换"
      />

      <TabPanel value="market" activeValue={tab}>
        <div className="space-y-4">
          <CategoryBar
            categories={categories}
            value={category}
            onValueChange={(key) => {
              setCategory(key);
              setPage(1);
            }}
            sort={sort}
            onSortChange={(next) => {
              setSort(next);
              setPage(1);
            }}
            keyword={keyword}
            onKeywordChange={(next) => {
              setKeyword(next);
              setPage(1);
            }}
          />

          {marketQuery.isLoading ? (
            <LoadingState label="正在加载插件…" />
          ) : marketQuery.error ? (
            <ErrorState
              message={toBusinessMessage(marketQuery.error)}
              onRetry={() => void marketQuery.refetch()}
            />
          ) : marketItems.length === 0 ? (
            <EmptyState
              title={hasMarketFilter ? '没有匹配的插件，换个关键字试试' : '还没有可用的插件'}
              description={
                hasMarketFilter
                  ? '可以清空分类或关键字，看看全部插件'
                  : '平台会陆续上架岗位插件，稍后再来看看'
              }
              action={
                hasMarketFilter ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setCategory('');
                      setKeyword('');
                      setPage(1);
                    }}
                  >
                    清空筛选
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {marketItems.map((plugin) => (
                  <PluginCard
                    key={plugin.plugin_id}
                    plugin={plugin}
                    onOpenDetail={(pluginId) => router.push(`/plugins/${pluginId}`)}
                    onInstall={handlePrimaryAction}
                    onOpenWorkbench={(pluginId) => router.push(`/workbench/${pluginId}`)}
                  />
                ))}
              </div>
              <Pagination
                page={page}
                pageSize={MARKET_PAGE_SIZE}
                total={marketTotal}
                onPageChange={setPage}
                disabled={marketQuery.isFetching}
              />
            </>
          )}
        </div>
      </TabPanel>

      <TabPanel value="installed" activeValue={tab}>
        <Card>
          <CardHeader>
            <CardTitle>已添加的插件</CardTitle>
            <CardDescription>
              停用不会打断正在跑的任务；卸载会保留历史任务记录与产物
            </CardDescription>
          </CardHeader>
          <CardContent>
            {installedQuery.isLoading ? (
              <LoadingState label="正在加载已添加插件…" />
            ) : installedQuery.error ? (
              <ErrorState
                message={toBusinessMessage(installedQuery.error)}
                onRetry={() => void installedQuery.refetch()}
              />
            ) : (
              <InstalledTable
                items={installedItems}
                canManage
                onOpenDetail={(pluginId) => router.push(`/plugins/${pluginId}`)}
                onOpenWorkbench={(pluginId) => router.push(`/workbench/${pluginId}`)}
                onUpgrade={(plugin) =>
                  upgradeMutation.mutate({
                    pluginId: plugin.plugin_id,
                    version: plugin.latest_version,
                  })
                }
                onToggleState={(plugin) =>
                  toggleStateMutation.mutate({
                    pluginId: plugin.plugin_id,
                    next: plugin.status === 'enabled' ? 'disabled' : 'enabled',
                  })
                }
                onUninstall={setUninstallTarget}
              />
            )}
          </CardContent>
        </Card>
      </TabPanel>

      <TabPanel value="tools" activeValue={tab}>
        <div className="space-y-4">
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
                    <CardTitle className="text-base">{tool.display_name}</CardTitle>
                    <CardDescription>支持 {tool.operations.length} 项能力</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <ul className="space-y-1 text-sm text-muted-foreground">
                      {tool.operations.slice(0, 5).map((operation) => (
                        <li key={operation.operation}>· {operation.display_name}</li>
                      ))}
                    </ul>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          <Card>
            <CardHeader>
              <CardTitle>最近调用</CardTitle>
              <CardDescription>只展示调用结果与业务化名称，不含参数内容</CardDescription>
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
                        description="在对话或工作台里使用过工具后，这里会留下最近 20 次记录"
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
        </div>
      </TabPanel>

      <Dialog open={upgradePromptOpen} onOpenChange={setUpgradePromptOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>当前套餐不含插件能力</DialogTitle>
            <DialogDescription>
              升级套餐后即可添加插件并使用岗位工作台；已上架的插件仍可随时查看详情。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => setUpgradePromptOpen(false)}>
              知道了
            </Button>
            <Button asChild>
              <a href="/billing">
                查看套餐
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={uninstallTarget !== null}
        onOpenChange={(open) => {
          if (!open) setUninstallTarget(null);
        }}
        variant="danger"
        title="卸载该插件？"
        description="卸载后无法再从工作台发起任务；历史任务记录会保留，180 天后统一清理。"
        confirmLabel="卸载"
        loading={uninstallMutation.isPending}
        onConfirm={async () => {
          if (!uninstallTarget) return;
          await uninstallMutation.mutateAsync(uninstallTarget.plugin_id);
        }}
      >
        {uninstallTarget ? (
          <p>
            {orPlaceholder(uninstallTarget.name)}
            {uninstallTarget.running_task_count > 0
              ? `（${uninstallTarget.running_task_count} 个在跑任务会继续完成）`
              : ''}
          </p>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}
