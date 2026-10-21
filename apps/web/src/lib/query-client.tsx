'use client';

import {
  QueryClient,
  QueryClientProvider,
  type QueryClientProviderProps,
} from '@tanstack/react-query';
import * as React from 'react';

/**
 * 全局查询默认策略。
 *
 * - 数据 30 秒内视为新鲜，标签来回切换不重复请求，体感「秒开」；
 * - 失败只重试 1 次，避免用户干等；
 * - 切回窗口不自动刷新，防止正在填写的表单被打断。
 */
export const QUERY_DEFAULTS = {
  staleTime: 30_000,
  retry: 1,
  refetchOnWindowFocus: false,
} as const;

/** 缓存保留时长：5 分钟（离开页面后返回仍可直接命中） */
const GC_TIME = 5 * 60 * 1000;

/**
 * 创建一个带默认策略的查询客户端实例。
 *
 * 每个浏览器会话独立创建（不在模块顶层共享），避免服务端渲染时多个请求互相串数据。
 *
 * @returns 查询客户端实例
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: QUERY_DEFAULTS.staleTime,
        retry: QUERY_DEFAULTS.retry,
        refetchOnWindowFocus: QUERY_DEFAULTS.refetchOnWindowFocus,
        refetchOnReconnect: true,
        gcTime: GC_TIME,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}

/** 查询容器属性（沿用容器自身声明的 children 类型，避免跨包 React 类型不一致） */
export interface QueryProviderProps extends Omit<QueryClientProviderProps, 'client'> {
  /** 外部注入客户端（仅测试或特殊场景使用，首帧后变更不生效） */
  client?: QueryClient;
}

/**
 * 查询容器：应用主布局挂载一次，页面通过 `useQuery` 取数即可。
 *
 * @param props - 组件属性
 * @returns 查询容器元素
 */
export function QueryProvider({ children, client }: QueryProviderProps): React.JSX.Element {
  const [queryClient] = React.useState<QueryClient>(() => client ?? createQueryClient());
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
