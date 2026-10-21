'use client';

import { Building2, ChartLine, FileText, Settings } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as React from 'react';

import { ErrorState, LoadingState } from '@/components/ui/feedback';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { BusinessError, opsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { cn } from '@/lib/utils';

/** 运营侧导航（页面标题与菜单文案一一对应） */
const OPS_NAV: Array<{ href: string; label: string; icon: typeof Building2 }> = [
  { href: '/ops/tenants', label: '企业管理', icon: Building2 },
  { href: '/ops/orders', label: '订单管理', icon: FileText },
  { href: '/ops/metrics', label: '平台统计', icon: ChartLine },
  { href: '/ops/settings', label: '系统配置', icon: Settings },
];

/** 无权访问时的提示文案（与后端 20003 同口径） */
const FORBIDDEN_MESSAGE = '当前账号没有该操作的权限';

/**
 * 运营后台布局。
 *
 * 权限前置：进页面先探测一次运营配置接口，
 * - 企业账号会拿到 20003（后端刻意不返回 404，避免探测），此处直接展示提示，
 *   **不渲染运营菜单**，避免用户点进去一路报错；
 * - 其余错误按业务语言展示并可重试。
 *
 * @param props - 布局属性
 * @returns 运营后台布局元素
 */
export default function OpsLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  const pathname = usePathname();
  const accessQuery = useApiQuery(QUERY_KEYS.opsAccess(), () => opsApi.getSettings(), { retry: 0 });

  const forbidden =
    accessQuery.error instanceof BusinessError && accessQuery.error.code === 20003;

  if (accessQuery.isLoading) {
    return (
      <div className="mx-auto max-w-6xl">
        <LoadingState label="正在确认运营权限…" />
      </div>
    );
  }

  if (accessQuery.error) {
    return (
      <div className="mx-auto max-w-6xl space-y-2">
        <h2 className="text-xl font-semibold">运营后台</h2>
        {forbidden ? (
          <ErrorState
            message={FORBIDDEN_MESSAGE}
            onRetry={() => void accessQuery.refetch()}
          />
        ) : (
          <ErrorState
            message={toBusinessMessage(accessQuery.error)}
            onRetry={() => void accessQuery.refetch()}
          />
        )}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">运营后台</h2>
        <p className="text-sm text-muted-foreground">
          仅展示企业、订单与聚合用量，不展示任何对话内容与文档原文。
        </p>
      </header>

      <nav aria-label="运营功能" className="flex flex-wrap items-center gap-1 border-b pb-2">
        {OPS_NAV.map((item) => {
          const Icon = item.icon;
          const active = pathname?.startsWith(item.href) ?? false;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors',
                active
                  ? 'bg-primary/10 font-medium text-primary'
                  : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div>{children}</div>
    </div>
  );
}
