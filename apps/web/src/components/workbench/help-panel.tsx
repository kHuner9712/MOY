'use client';

import { HelpCircle } from 'lucide-react';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { capabilityLabel } from '@/lib/capability-labels';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { PluginDetail, TaskItem } from '@/lib/types';

/** 帮助栏属性 */
export interface HelpPanelProps {
  /** 插件详情（说明 / 能力 / 步骤均来自声明） */
  plugin: PluginDetail;
  /** 最近 3 次任务 */
  recentTasks: TaskItem[];
  /** 是否加载中 */
  loading?: boolean;
}

/**
 * 工作台右侧帮助栏（320px）。
 *
 * 内容全部来自插件声明与任务列表：说明、本次会用到的能力、最近 3 次任务。
 * 不写「常见问题」这类需要人工维护的文案，避免与声明漂移。
 *
 * @param props - 组件属性
 * @returns 帮助栏元素
 */
export function HelpPanel({
  plugin,
  recentTasks,
  loading = false,
}: HelpPanelProps): React.JSX.Element {
  return (
    <aside className="hidden w-80 shrink-0 space-y-3 xl:block">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <HelpCircle aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
            使用说明
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="whitespace-pre-wrap text-muted-foreground">
            {orPlaceholder(plugin.summary, '插件未提供说明')}
          </p>
          {plugin.step_titles.length > 0 ? (
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">会经历这些步骤</p>
              <ol className="list-inside list-decimal space-y-0.5 text-xs text-muted-foreground">
                {plugin.step_titles.map((title) => (
                  <li key={title}>{title}</li>
                ))}
              </ol>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">会用到的权限</CardTitle>
          <CardDescription>只列出本插件声明的能力</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1.5 text-xs text-muted-foreground">
            {plugin.capabilities.map((capability) => (
              <li key={capability.capability_key} className="flex items-start gap-1.5">
                <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                <span>{capabilityLabel(capability.capability_key, capability.display_name)}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">最近执行</CardTitle>
          <CardDescription>最多显示最近 3 次</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-xs text-muted-foreground">正在加载执行记录…</p>
          ) : recentTasks.length === 0 ? (
            <p className="text-xs text-muted-foreground">还没有执行记录，填好左侧内容就能开始</p>
          ) : (
            <ul className="space-y-2">
              {recentTasks.map((task) => (
                <li key={task.task_id} className="space-y-1">
                  <p className="truncate text-xs font-medium" title={task.goal}>
                    {orPlaceholder(task.goal, '未命名任务')}
                  </p>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={task.status} />
                    <span className="text-[11px] text-muted-foreground">
                      {formatDateTime(task.updated_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </aside>
  );
}
