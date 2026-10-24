'use client';

import { WifiOff } from 'lucide-react';
import { usePathname } from 'next/navigation';
import * as React from 'react';

import { SessionGate } from '@/components/layout/session-gate';
import { MobileNav } from '@/components/layout/mobile-nav';
import { AppSidebar, resolveNavItem } from '@/components/layout/sidebar';
import { installErrorGuard } from '@/lib/error-guard';
import { QueryProvider } from '@/lib/query-client';

/**
 * 主界面布局：会话门 + 左侧导航 + 内容区，并在最外层挂载数据查询容器与全局兜底。
 *
 * 四件事：
 * 1. **会话门包住整块界面**（含侧栏）：`AppSidebar` 里的插件分组一挂载就发请求，
 *    它必须在门内，否则刷新页面时仍会留下一次无身份请求；
 *    门在 `QueryProvider` **之内**，因为门自己要用 `useQuery` 取资料；
 * 2. 标题与侧栏菜单共用 `NAV_ITEMS`，不会出现两套文案；
 * 3. 断网时顶部常驻提示条（`online` / `offline` 事件驱动，恢复后自动消失）；
 * 4. 安装全局错误兜底：未捕获异常 / 未处理的 Promise 拒绝统一转成业务语言提示，
 *    同一错误 3 秒内只提示一次（详见 `lib/error-guard.ts`）。
 *
 * 留在本文件而不进门的两件事：路径解析（`usePathname`）、断网检测与错误兜底安装
 * —— 它们都不依赖会话，进门只会让骨架期多绕一层。
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
      <SessionGate>
        <div className="flex min-h-screen">
          <AppSidebar />

          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex h-16 items-center justify-between gap-3 border-b px-4 md:px-6">
              <h1 className="min-w-0 flex-1 truncate whitespace-nowrap text-base font-semibold">
                {current?.label ?? ''}
              </h1>
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
      </SessionGate>
    </QueryProvider>
  );
}
