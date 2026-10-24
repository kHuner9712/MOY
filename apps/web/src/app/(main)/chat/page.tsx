'use client';

import { ArrowUpRight } from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import * as React from 'react';

import ConversationSidebar from '@/components/chat/conversation-sidebar';
import { GoalLauncher } from '@/components/chat/goal-launcher';
import { Button } from '@/components/ui/button';
import { ErrorState } from '@/components/ui/feedback';
import { Skeleton } from '@/components/ui/skeleton';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { billingApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatPercent } from '@/lib/format';

// 对话窗口仅在客户端渲染（依赖 SSE 与浏览器 API）
const ChatWindow = dynamic(() => import('@/components/chat/chat-window'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      正在准备对话…
    </div>
  ),
});

/** 额度提醒比例（达到即提示，不阻断使用） */
const QUOTA_WARN_PERCENT = 80;

/** 当前账期 `YYYY-MM` */
function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

/**
 * 对话主页：额度提醒条 + 多会话侧边栏 + 对话窗口。
 *
 * 关于错误态：对话流本身的错误由 `ChatWindow` 逐个回答展示（带「重新发送」），
 * 本页的 `ErrorState` 负责「额度信息加载失败」这类页面级问题——两者都必须可重试，
 * 不允许出现「白屏但不知道发生了什么」的状态。
 *
 * @returns 对话页面元素
 */
export default function ChatPage(): React.JSX.Element {
  const period = currentPeriod();
  const usageQuery = useApiQuery(QUERY_KEYS.billingUsage(period), () => billingApi.getUsage({ period }), {
    retry: 0,
  });

  const usage = usageQuery.data;
  const showQuotaWarning =
    usage !== undefined &&
    Number.isFinite(usage.usage_percent) &&
    usage.usage_percent >= QUOTA_WARN_PERCENT;
  const quotaUnit = usage?.quota_unit || '字';

  return (
    <div className="flex h-[calc(100vh-7.5rem)] flex-col gap-3">
      {usageQuery.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : usageQuery.error ? (
        <ErrorState
          message={toBusinessMessage(usageQuery.error)}
          onRetry={() => void usageQuery.refetch()}
        />
      ) : showQuotaWarning && usage ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          <p>
            本月额度已用 {formatPercent(usage.usage_percent)}（剩余{' '}
            {usage.quota_units_remaining.toLocaleString('zh-CN')} {quotaUnit}），接近上限时会切换为常规模式。
          </p>
          <Button asChild size="sm" variant="outline">
            <Link href="/billing">
              去升级套餐
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </div>
      ) : null}

      <GoalLauncher />

      <div className="flex min-h-0 flex-1 gap-6">
        <aside className="hidden w-64 shrink-0 lg:block">
          <ConversationSidebar />
        </aside>
        <div className="min-w-0 flex-1">
          <ChatWindow />
        </div>
      </div>
    </div>
  );
}
