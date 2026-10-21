'use client';

import type React from "react";

import { useCallback, useEffect, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { toolApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { ToolSpec } from '@/lib/types';

/**
 * 插件市场页（S3 阶段实现）。
 *
 * 设计要点（架构文档 4.1）：
 * - 工具卡片完全由 T1 注册表驱动，前端**不硬编码工具列表**；
 * - 新增工具无需改动本页面代码。
 *
 * @returns 插件市场页面元素
 */
export default function PluginsPage(): React.JSX.Element {
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState('');

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setErrorMessage('');
    try {
      const registry = await toolApi.registry();
      setTools(registry.tools);
    } catch (error) {
      setErrorMessage(toBusinessMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold">可连接的工具</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          连接后即可在对话与任务中直接使用，无需切换到其他系统
        </p>
      </div>

      {loading ? (
        <LoadingState />
      ) : errorMessage ? (
        <ErrorState message={errorMessage} onRetry={() => void load()} />
      ) : tools.length === 0 ? (
        <EmptyState title="暂无可用工具" description="工具正在接入中，请稍后再试" />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {tools.map((tool) => (
            <Card key={tool.tool_type}>
              <CardHeader>
                <div className="flex items-start justify-between">
                  <CardTitle>{tool.display_name}</CardTitle>
                  <Badge variant={tool.status === 'available' ? 'success' : 'secondary'}>
                    {tool.status === 'available' ? '可用' : '即将上线'}
                  </Badge>
                </div>
                <CardDescription>
                  支持 {tool.operations.length} 项操作
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1 text-sm text-muted-foreground">
                  {tool.operations.map((operation) => (
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
          <CardTitle>场景插件</CardTitle>
          <CardDescription>
            岗位级工作流插件正在开发中，将按「开箱即用」原则交付
          </CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="插件即将上线"
            description="首批将提供研发、内容运营、行政人事三个岗位的完整工作流"
          />
        </CardContent>
      </Card>
    </div>
  );
}
