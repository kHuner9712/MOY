'use client';

import {
  BookOpen,
  LayoutGrid,
  MessageSquare,
  Plug,
  User,
  Users,
  Workflow,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

/** 导航项定义 */
const NAV_ITEMS: Array<{ href: string; label: string; icon: typeof MessageSquare }> = [
  { href: '/chat', label: '对话', icon: MessageSquare },
  { href: '/knowledge', label: '知识库', icon: BookOpen },
  { href: '/plugins', label: '插件市场', icon: Plug },
  { href: '/workflows', label: '工作流', icon: Workflow },
  { href: '/team', label: '团队管理', icon: Users },
  { href: '/profile', label: '个人中心', icon: User },
];

/**
 * 主界面布局：左侧导航 + 内容区。
 *
 * @param props - 布局属性
 * @returns 主布局元素
 */
export default function MainLayout({
  children,
}: {
  children: React.ReactNode;
}): JSX.Element {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-muted/20 md:flex">
        <div className="flex h-16 items-center gap-2 border-b px-6">
          <LayoutGrid className="h-5 w-5 text-primary" />
          <span className="text-sm font-semibold">办公 AI 平台</span>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = pathname?.startsWith(item.href) ?? false;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? 'bg-primary/10 font-medium text-primary'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                }`}
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

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center justify-between border-b px-6">
          <h1 className="text-base font-semibold">
            {NAV_ITEMS.find((item) => pathname?.startsWith(item.href))?.label ?? '办公 AI 平台'}
          </h1>
          <div className="flex items-center gap-2 md:hidden">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.label}
                className="rounded-md p-2 text-muted-foreground hover:bg-accent"
              >
                <item.icon className="h-4 w-4" />
              </Link>
            ))}
          </div>
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
