'use client';

import { Toaster as SonnerToaster } from 'sonner';

import { cn } from '@/lib/utils';

/**
 * 全局提示组件（基于 sonner）。
 *
 * 铁律：所有提示只使用业务语言文案，禁止展示错误码、堆栈或技术细节
 * （架构文档 9.2）。
 */
export function Toaster(): JSX.Element {
  return (
    <SonnerToaster
      position="top-center"
      richColors
      closeButton
      duration={4000}
      toastOptions={{
        classNames: {
          toast: cn('rounded-lg border shadow-lg'),
          description: 'text-sm',
        },
      }}
    />
  );
}

export { toast } from 'sonner';
