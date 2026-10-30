'use client';

import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/feedback';
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
import { Tooltip } from '@/components/ui/tooltip';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { InstalledPluginItem } from '@/lib/types';

/** 已安装表格属性 */
export interface InstalledTableProps {
  /** PL3 返回的已安装列表（不分页） */
  items: InstalledPluginItem[];
  /** 是否加载中 */
  loading?: boolean;
  /** 是否有管理权限 */
  canManage?: boolean;
  /** 打开详情 */
  onOpenDetail: (pluginId: string) => void;
  /** 打开工作台（仅 enabled 可用） */
  onOpenWorkbench: (pluginId: string) => void;
  /** 升级 */
  onUpgrade: (plugin: InstalledPluginItem) => void;
  /** 启用 / 停用 */
  onToggleState: (plugin: InstalledPluginItem) => void;
  /** 卸载 */
  onUninstall: (plugin: InstalledPluginItem) => void;
  /** 是否处于筛选态（区分「还没添加」与「筛选无结果」） */
  filtered?: boolean;
}

/** 表格列数 */
const COLUMN_COUNT = 8;

/**
 * 已安装插件表格（7 列信息 + 操作列）。
 *
 * 口径：
 * - 只有 `enabled` 能进工作台；`disabled` 时入口置灰并说明「已停用，请联系管理员启用」；
 * - 有更新时展示目标版本号并提供「去更新」；
 * - 卸载会保留历史任务与产物，`kept_task_count` 由页面在二次确认里展示。
 *
 * @param props - 组件属性
 * @returns 表格元素
 */
export function InstalledTable({
  items,
  loading = false,
  canManage = true,
  onOpenDetail,
  onOpenWorkbench,
  onUpgrade,
  onToggleState,
  onUninstall,
  filtered = false,
}: InstalledTableProps): React.JSX.Element {
  if (!loading && items.length === 0) {
    return (
      <EmptyState
        title={filtered ? '没有符合条件的插件' : '还没有添加插件'}
        description={
          filtered
            ? '换个状态再看，或清空筛选查看全部已添加插件'
            : '去市场看看能帮你做什么，添加后即可在这里管理启停、版本与配置'
        }
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>插件</TableHead>
          <TableHead>分类</TableHead>
          <TableHead>当前版本</TableHead>
          <TableHead>最新版本</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>最近使用</TableHead>
          <TableHead>在跑任务</TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {loading ? (
          <TableLoading columns={COLUMN_COUNT} rows={4} />
        ) : items.length === 0 ? (
          <TableEmpty
            colSpan={COLUMN_COUNT}
            title={filtered ? '没有符合条件的插件' : '还没有添加插件'}
            description={filtered ? '换个状态再看' : '去市场看看能帮你做什么'}
          />
        ) : (
          items.map((plugin) => {
            const enabled = plugin.status === 'enabled';
            const lastUsed = plugin.last_used_at ? formatDateTime(plugin.last_used_at) : '尚未使用';
            return (
              <TableRow key={plugin.plugin_id}>
                <TableCell className="max-w-[12rem]">
                  <button
                    type="button"
                    onClick={() => onOpenDetail(plugin.plugin_id)}
                    className="truncate text-left text-sm font-medium text-primary underline-offset-4 hover:underline"
                  >
                    {orPlaceholder(plugin.name)}
                  </button>
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{orPlaceholder(plugin.category)}</Badge>
                </TableCell>
                <TableCell className="whitespace-nowrap font-mono text-sm">
                  {plugin.version}
                </TableCell>
                <TableCell className="whitespace-nowrap font-mono text-sm text-muted-foreground">
                  {plugin.has_update ? plugin.latest_version : '已是最新'}
                </TableCell>
                <TableCell>
                  <StatusBadge status={plugin.status} label={enabled ? '已启用' : '已停用'} />
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {lastUsed}
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm tabular-nums">
                  {plugin.running_task_count > 0 ? `${plugin.running_task_count} 个` : '—'}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex flex-wrap items-center justify-end gap-1">
                    <Tooltip content="该插件已停用，请联系管理员启用后再使用" disabled={enabled}>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={!enabled}
                        onClick={() => onOpenWorkbench(plugin.plugin_id)}
                      >
                        工作台
                      </Button>
                    </Tooltip>
                    {plugin.has_update ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!canManage}
                        onClick={() => onUpgrade(plugin)}
                      >
                        去更新
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={!canManage}
                      onClick={() => onToggleState(plugin)}
                    >
                      {enabled ? '停用' : '启用'}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={!canManage}
                      onClick={() => onUninstall(plugin)}
                    >
                      卸载
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
