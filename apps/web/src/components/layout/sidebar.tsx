'use client';

import {
  BookOpen,
  CreditCard,
  LayoutGrid,
  MessageSquare,
  Network,
  Plug,
  Sparkles,
  User,
  Users,
  Workflow,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as React from 'react';

import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { pluginsApi } from '@/lib/api-client';
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
  { href: '/plugins', label: '插件与工作台', icon: Plug },
  { href: '/workflows', label: '工作流', icon: Workflow },
  { href: '/team', label: '团队管理', icon: Users },
  { href: '/team/organization', label: '组织架构', icon: Network },
  { href: '/billing', label: '套餐与账单', icon: CreditCard },
  { href: '/profile', label: '个人中心', icon: User },
  { href: '/profile/memories', label: '我的记忆', icon: Sparkles },
];

/** 插件分组的锚点路由（工作台子项挂在它下面） */
const PLUGIN_NAV_HREF = '/plugins';

/**
 * 不在侧栏出现、但需要独立标题的路由前缀。
 *
 * 说明：插件详情与工作台不进一级菜单（它们从市场 / 已添加到进入），
 * 运营后台同理（由 URL 直达，且移动端刻意不给入口——企业账号点进去必然被拒），
 * 但页面顶部标题必须稳定，否则用户跳进来会看到上一个页面的标题。
 */
const EXTRA_TITLES: ReadonlyArray<{ prefix: string; label: string }> = [
  { prefix: '/workbench', label: '工作台' },
  { prefix: '/ops', label: '运营后台' },
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

  // 先看「有独立标题但不进菜单」的路由
  for (const extra of EXTRA_TITLES) {
    if (pathname === extra.prefix || pathname.startsWith(`${extra.prefix}/`)) {
      matched = { href: extra.prefix, label: extra.label, icon: MessageSquare };
    }
  }

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
 * 插件与工作台分组：主导航给市场入口，这里补「已添加」与各个可用工作台。
 *
 * 口径：只列出 `status=enabled` 的插件——停用的插件点进去只会被拦下来，
 * 不如不出现在入口里；启停/卸载后分组自动刷新（与市场共用 `['plugins']` 前缀键）。
 *
 * @returns 分组元素
 */
function PluginWorkbenchGroup(): React.JSX.Element {
  const installedQuery = useApiQuery(QUERY_KEYS.installedPlugins(), () => pluginsApi.installed());
  const enabledPlugins = (installedQuery.data?.items ?? []).filter(
    (item) => item.status === 'enabled',
  );

  return (
    <div className="ml-7 mt-1 space-y-0.5 border-l pl-3">
      <Link
        href="/plugins?tab=installed"
        className="block rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        已添加的插件
      </Link>
      {installedQuery.isLoading ? (
        <span className="block px-2 py-1 text-xs text-muted-foreground">正在加载工作台…</span>
      ) : enabledPlugins.length === 0 ? (
        <span className="block px-2 py-1 text-xs text-muted-foreground">还没有可用的工作台</span>
      ) : (
        enabledPlugins.map((plugin) => (
          <Link
            key={plugin.plugin_id}
            href={`/workbench/${plugin.plugin_id}`}
            title={plugin.name}
            className="block truncate rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            {plugin.name}
          </Link>
        ))
      )}
    </div>
  );
}

/**
 * 桌面端左侧导航。
 *
 * 移动端的图标导航已迁到 `./mobile-nav`（`MobileNav`）：两者数据源相同但排布逻辑不同，
 * 拆开后本文件的逻辑一行不用为窄屏让步。
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
      <nav aria-label="主导航" className="flex-1 space-y-1 overflow-y-auto p-3">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = item.href === active?.href;
          return (
            <div key={item.href}>
              <Link
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
              {item.href === PLUGIN_NAV_HREF ? <PluginWorkbenchGroup /> : null}
            </div>
          );
        })}
      </nav>
      <div className="border-t p-4 text-xs text-muted-foreground">
        遇到问题？联系你的团队管理员
      </div>
    </aside>
  );
}

