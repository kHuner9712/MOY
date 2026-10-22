'use client';

import * as React from 'react';

import { LoadingState } from '@/components/ui/feedback';
import { Skeleton } from '@/components/ui/skeleton';

/**
 * 任务详情路由级加载态。
 *
 * @returns 加载态元素
 */
export default function TaskDetailLoading(): React.JSX.Element {
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <Skeleton className="h-6 w-40" />
      <LoadingState label="正在加载任务详情…" description="步骤与产物加载中" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
