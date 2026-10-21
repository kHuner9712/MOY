'use client';

import {
  BookOpen,
  CreditCard,
  LayoutGrid,
  MessageSquare,
  Network,
  Plug,
  User,
  Users,
  Workflow,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as React from 'react';

import { cn } from '@/lib/utils';

/** 导航项 */
export interface NavItem {
  /** 路由 */
  href: string;
  /** 菜单文案（必须与该页顶部标题完全一致） */
  label: string;
  /** 图标 */
  icon: typeof MessageSquare;
}

/**
 * 全站导航（唯一来源）。
 *
 * 约定：`label` 与页面顶部标题一一对应，改一处必须改另一处，
 * 避免「菜单叫 A、页面叫 B」这种最常见的体验缺陷。
 * 顺序按使用频率排：先创作（对话/知识库），再扩展（插件/工作流），最后管理（团队/账单/个人）。
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { href: '/chat', label: '对话', icon: MessageSquare },
  { href: '/knowledge', label: '知识库', icon: BookOpen },
  { href: '/plugins', label: '插件市场', icon: Plug },
  { href: '/workflows', label: '工作流', icon: Workflow },
  { href: '/team', label: '团队管理', icon: Users },
  { href: '/team/organization', label: '组织架构', icon: Network },
  { href: '/billing', label: '套餐与账单', icon: CreditCard },
  { href: '/profile', label: '个人中心', icon: User },
];

/**
 * 按当前路径解析命中的导航项（最长前缀优先）。
 *
 * 为什么不用简单 `startsWith`：`/team/organization` 同时命中 `/team`，
 * 单一顺序匹配会让「组织架构」页的标题退化成「团队管理」。
 *
 * @param pathname - 当前路径
 * @returns 命中的导航项；未命中返回 undefined
 */
export function resolveNavItem(pathname: string | null | undefined): NavItem | undefined {
  if (!pathname) return undefined;
  let matched: NavItem | undefined;
  for (const item of NAV_ITEMS) {
    if (pathname === item.href || pathname.startsWith(`${item.href}/`)) {
      if (!matched || item.href.length > matched.href.length) {
        matched = item;
      }
    }
  }
  return matched;
}

/**
 * 桌面端左侧导航。
 *
 * @returns 侧栏元素
 */
export function AppSidebar(): React.JSX.Element {
  const pathname = usePathname();
  const active = resolveNavItem(pathname);

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r bg-muted/20 md:flex">
      <div className="flex h-16 items-center gap-2 border-b px-6">
        <LayoutGrid className="h-5 w-5 text-primary" />
        <span className="text-sm font-semibold">办公 AI 平台</span>
      </div>
      <nav aria-label="主导航" className="flex-1 space-y-1 p-3">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = item.href === active?.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive ? 'page' : undefined}
              className={cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                isActive
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
      <div className="border-t p-4 text-xs text-muted-foreground">
        遇到问题？联系你的团队管理员
      </div>
    </aside>
  );
}

/**
 * 移动端顶部图标导航（窄屏替代左侧栏）。
 *
 * @returns 图标导航元素
 */
export function MobileNav(): React.JSX.Element {
  const pathname = usePathname();
  const active = resolveNavItem(pathname);

  return (
    <div className="flex items-center gap-1 md:hidden">
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const isActive = item.href === active?.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-label={item.label}
            aria-current={isActive ? 'page' : undefined}
            className={cn(
              'rounded-md p-2',
              isActive
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:bg-accent',
            )}
          >
            <Icon className="h-4 w-4" />
          </Link>
        );
      })}
    </div>
  );
}
