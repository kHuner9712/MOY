'use client';

import { WifiOff } from 'lucide-react';
import { usePathname } from 'next/navigation';
import * as React from 'react';

import { AppSidebar, MobileNav, resolveNavItem } from '@/components/layout/sidebar';
import { installErrorGuard } from '@/lib/error-guard';
import { QueryProvider } from '@/lib/query-client';

/**
 * 主界面布局：左侧导航 + 内容区，并在最外层挂载数据查询容器与全局兜底。
 *
 * 三件事：
 * 1. 标题与侧栏菜单共用 `NAV_ITEMS`，不会出现两套文案；
 * 2. 断网时顶部常驻提示条（`online` / `offline` 事件驱动，恢复后自动消失）；
 * 3. 安装全局错误兜底：未捕获异常 / 未处理的 Promise 拒绝统一转成业务语言提示，
 *    同一错误 3 秒内只提示一次（详见 `lib/error-guard.ts`）。
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
  const [offline, setOffline] = React.useState<boolean>(false);

  // 断网检测：初始按 navigator.onLine 同步一次，之后由浏览器事件驱动
  React.useEffect(() => {
    const sync = (): void => {
      setOffline(typeof navigator !== 'undefined' && navigator.onLine === false);
    };
    sync();
    window.addEventListener('online', sync);
    window.addEventListener('offline', sync);
    return () => {
      window.removeEventListener('online', sync);
      window.removeEventListener('offline', sync);
    };
  }, []);

  // 全局错误兜底（卸载时移除监听）
  React.useEffect(() => installErrorGuard(), []);

  return (
    <QueryProvider>
      <div className="flex min-h-screen">
        <AppSidebar />

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-16 items-center justify-between border-b px-6">
            <h1 className="text-base font-semibold">{current?.label ?? '办公 AI 平台'}</h1>
            <MobileNav />
          </header>

          {offline ? (
            <div
              role="status"
              aria-live="polite"
              className="flex items-center gap-2 border-b border-amber-300 bg-amber-50 px-6 py-2 text-sm text-amber-900"
            >
              <WifiOff aria-hidden="true" className="h-4 w-4 shrink-0" />
              <span>网络连接已断开，请检查网络后重试</span>
            </div>
          ) : null}

          <main className="flex-1 overflow-auto p-6">{children}</main>
        </div>
      </div>
    </QueryProvider>
  );
}
