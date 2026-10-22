'use client';

import * as React from 'react';

import { LoadingState } from '@/components/ui/feedback';
import { Skeleton } from '@/components/ui/skeleton';

/**
 * 工作台路由级加载态（骨架屏）。
 *
 * 三态里的「加载」在服务端切路由时就要有反馈，因此单独提供 `loading.tsx`：
 * 表单区给 3 行字段骨架、右侧帮助栏给 2 块骨架，避免白屏。
 *
 * @returns 加载态元素
 */
export default function WorkbenchLoading(): React.JSX.Element {
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <Skeleton className="h-6 w-48" />
      <LoadingState label="正在打开工作台…" description="插件表单与执行记录加载中" />
      <div className="flex gap-4">
        <div className="min-w-0 flex-1 space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
        <div className="hidden w-80 shrink-0 space-y-3 xl:block">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    </div>
  );
}
