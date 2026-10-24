'use client';

/**
 * 移动端顶部导航（窄屏替代左侧栏）。
 *
 * 为什么从 `sidebar.tsx` 拆出来单独一个文件（而不是原地改）：
 * 桌面端侧栏是本产品最稳的一块，拆开后「桌面端零影响」的证明成本从
 * 「逐断点回归」降成「看 diff —— `AppSidebar` 一行没动」。
 *
 * 收敛口径（F-06）：390px 宽度下把 9 个入口压成一排，标题必然被挤成竖排、
 * 图标也会换行。因此顶部只留 4 个高频主项，其余进「更多」抽屉。
 *
 * 两条不可让步的实现约束：
 * 1. **主项与溢出项都从 `NAV_ITEMS` 派生**，不复制 label / icon。否则「菜单叫 A、
 *    页面标题叫 B」这条既有约定会从移动端破掉；溢出项用差集计算，结构上不可能漏项。
 * 2. **抽屉里不放运营后台入口**：`/ops/*` 由 URL 直达且运营布局自行判权，
 *    对企业账号来说那个入口是一个点了必然被拒的按钮。
 */

import { MoreHorizontal } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as React from 'react';

import { NAV_ITEMS, resolveNavItem } from '@/components/layout/sidebar';
import { Drawer, DrawerBody, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/drawer';
import { cn } from '@/lib/utils';

/**
 * 移动端主项路由（按使用频率：先创作、再扩展、最后个人）。
 *
 * 只写 href，label 与图标一律从 `NAV_ITEMS` 取——这里多写一个字，
 * 就多一处可能与页面标题不一致的文案。
 */
const MOBILE_PRIMARY_HREFS: readonly string[] = ['/chat', '/knowledge', '/plugins', '/profile'];

/** 触达尺寸（44×44，移动端最小可点区域） */
const TOUCH_TARGET_CLASS = 'inline-flex h-11 w-11 items-center justify-center rounded-md';

/**
 * 移动端图标导航。
 *
 * @returns 图标导航元素（含「更多」抽屉）
 */
export function MobileNav(): React.JSX.Element {
  const pathname = usePathname();
  const active = resolveNavItem(pathname);
  const [open, setOpen] = React.useState<boolean>(false);

  // 主项与溢出项都由 NAV_ITEMS 派生：改菜单只需改一处，移动端自动跟随
  const primaryItems = NAV_ITEMS.filter((item) => MOBILE_PRIMARY_HREFS.includes(item.href));
  const overflowItems = NAV_ITEMS.filter((item) => !MOBILE_PRIMARY_HREFS.includes(item.href));

  return (
    <div className="flex shrink-0 items-center gap-1 md:hidden">
      {primaryItems.map((item) => {
        const Icon = item.icon;
        const isActive = item.href === active?.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-label={item.label}
            aria-current={isActive ? 'page' : undefined}
            className={cn(
              TOUCH_TARGET_CLASS,
              isActive ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-accent',
            )}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
          </Link>
        );
      })}

      <Drawer open={open} onOpenChange={setOpen}>
        <button
          type="button"
          aria-label="更多功能"
          aria-expanded={open}
          aria-haspopup="dialog"
          onClick={() => setOpen(true)}
          className={cn(
            TOUCH_TARGET_CLASS,
            'text-muted-foreground hover:bg-accent',
            overflowItems.some((item) => item.href === active?.href) && 'text-primary',
          )}
        >
          <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
        </button>

        <DrawerContent size="md">
          <DrawerHeader>
            <DrawerTitle>更多功能</DrawerTitle>
          </DrawerHeader>
          <DrawerBody>
            <nav aria-label="更多导航" className="space-y-1">
              {overflowItems.map((item) => {
                const Icon = item.icon;
                const isActive = item.href === active?.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    aria-current={isActive ? 'page' : undefined}
                    // 点完立刻关抽屉：路由切换后抽屉滞留会挡住新页面
                    onClick={() => setOpen(false)}
                    className={cn(
                      'flex min-h-11 items-center gap-3 rounded-md px-3 text-sm transition-colors',
                      isActive
                        ? 'bg-primary/10 font-medium text-primary'
                        : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </DrawerBody>
        </DrawerContent>
      </Drawer>
    </div>
  );
}
