'use client';

import { ArrowUpCircle, Plug, Power, Rocket, Settings2 } from 'lucide-react';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Rating } from '@/components/ui/rating';
import { Tooltip } from '@/components/ui/tooltip';
import { orPlaceholder } from '@/lib/format';
import type { PluginMarketItem } from '@/lib/types';

/** 插件卡片属性 */
export interface PluginCardProps {
  /** 市场列表项（PL1） */
  plugin: PluginMarketItem;
  /** 打开详情 */
  onOpenDetail: (pluginId: string) => void;
  /** 安装（`installable=false` 时由页面负责提示套餐与「查看套餐」入口） */
  onInstall: (plugin: PluginMarketItem) => void;
  /** 打开工作台（已启用时） */
  onOpenWorkbench: (pluginId: string) => void;
}

/**
 * 取主按钮文案与图标（四态映射，PRD TC-P3-07 ④）。
 *
 * @param plugin - 市场条目
 * @returns 按钮配置
 */
function primaryActionOf(plugin: PluginMarketItem): {
  label: string;
  icon: typeof Plug;
  kind: 'install' | 'workbench';
} {
  if (plugin.has_update) {
    return { label: '去更新', icon: ArrowUpCircle, kind: 'install' };
  }
  if (plugin.installed_status === 'enabled') {
    return { label: '打开工作台', icon: Rocket, kind: 'workbench' };
  }
  if (plugin.installed_status === 'disabled') {
    return { label: '去启用', icon: Power, kind: 'install' };
  }
  return { label: '查看并安装', icon: Settings2, kind: 'install' };
}

/**
 * 插件市场卡片。
 *
 * 设计约束：
 * - **免费版不降级**：`installable=false` 时卡片照常展示（不隐藏、不灰整卡），
 *   只有主按钮旁出现「当前套餐不含插件能力」的说明；
 * - 评分统一走 `ui/rating`（无评价时给「暂无评分」空态），不自己画星；
 * - 分类与安装状态用统一徽标，颜色只用设计令牌。
 *
 * @param props - 组件属性
 * @returns 卡片元素
 */
export function PluginCard({
  plugin,
  onOpenDetail,
  onInstall,
  onOpenWorkbench,
}: PluginCardProps): React.JSX.Element {
  const action = primaryActionOf(plugin);
  const ActionIcon = action.icon;

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-start gap-2">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Plug aria-hidden="true" className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <CardTitle className="truncate" title={plugin.name}>
                {plugin.name}
              </CardTitle>
              <CardDescription className="line-clamp-2 min-h-[2.5rem]">
                {orPlaceholder(plugin.summary, '暂无简介')}
              </CardDescription>
            </div>
          </div>
          <Badge variant="outline" className="shrink-0">
            {orPlaceholder(plugin.category)}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Rating
            value={plugin.rating_avg}
            count={plugin.rating_count}
            readOnly
            size="sm"
            label={`${plugin.name} 评分`}
          />
          <span className="text-xs text-muted-foreground">v{orPlaceholder(plugin.latest_version)}</span>
          {plugin.installed_status !== 'none' ? (
            <StatusBadge
              status={plugin.installed_status}
              label={plugin.installed_status === 'enabled' ? '已添加' : '已停用'}
            />
          ) : null}
          {plugin.has_update ? (
            <Badge variant="warning" className="whitespace-nowrap">
              有新版本
            </Badge>
          ) : null}
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="flex-1"
              onClick={() => onOpenDetail(plugin.plugin_id)}
            >
              详情
            </Button>
            <Tooltip
              content="当前套餐不含插件能力，升级后即可安装"
              disabled={plugin.installable}
              className="flex-1"
            >
              <Button
                type="button"
                size="sm"
                className="w-full"
                onClick={() =>
                  action.kind === 'workbench'
                    ? onOpenWorkbench(plugin.plugin_id)
                    : onInstall(plugin)
                }
              >
                <ActionIcon className="h-4 w-4" />
                {action.label}
              </Button>
            </Tooltip>
          </div>
          {!plugin.installable ? (
            <p className="text-xs text-muted-foreground">当前套餐不含插件能力，升级后可安装</p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
