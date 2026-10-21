'use client';

import { usePathname } from 'next/navigation';
import * as React from 'react';

import { AppSidebar, MobileNav, resolveNavItem } from '@/components/layout/sidebar';
import { QueryProvider } from '@/lib/query-client';

/**
 * 主界面布局：左侧导航 + 内容区，并在最外层挂载数据查询容器。
 *
 * 标题来源：`resolveNavItem(pathname)`，与侧栏菜单共用同一份 `NAV_ITEMS`，
 * 因此「菜单叫什么、页面顶部就叫什么」，不会出现两套文案。
 *
 * @param props - 布局属性
 * @returns 主布局元素
 */
export default function MainLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  const pathname = usePathname();
  const current = resolveNavItem(pathname);

  return (
    <QueryProvider>
      <div className="flex min-h-screen">
        <AppSidebar />

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-16 items-center justify-between border-b px-6">
            <h1 className="text-base font-semibold">{current?.label ?? '办公 AI 平台'}</h1>
            <MobileNav />
          </header>
          <main className="flex-1 overflow-auto p-6">{children}</main>
        </div>
      </div>
    </QueryProvider>
  );
}
