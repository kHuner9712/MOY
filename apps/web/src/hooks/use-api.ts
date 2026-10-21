'use client';

/**
 * 通用数据请求 Hook（基于 react-query，架构文档 8.1 裁决）。
 *
 * 约定（B12/B13/B14 全站统一，勿另造轮子）：
 * 1. **查询键**由 `QUERY_KEYS` 统一生成，键的第一段是域（`kb` / `identity` / `billing` / `ops`），
 *    因此 `invalidateQueries({ queryKey: ['kb', baseId] })` 会连带失效该库下的文档与授权列表；
 * 2. **取数只有 `useApiQuery`**，**写入只有 `useApiMutation`**，两者都走 `lib/api-client.ts`；
 * 3. 变更成功后由 `invalidateKeys` 声明要失效的键，不在组件里手写 `refetch`；
 * 4. 轮询（文档处理进度）通过 `refetchInterval` 传入，页面按「是否仍有文档在处理」动态开关。
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useCallback } from 'react';

/** 查询键：react-query 内部按键的深度哈希做匹配，这里统一用只读数组表达 */
export type ApiQueryKey = readonly unknown[];

/** 查询选项 */
export interface ApiQueryOptions {
  /** 是否发起请求，默认 true（依赖上一步结果时置 false） */
  enabled?: boolean;
  /**
   * 轮询间隔（毫秒）；`false` 表示不轮询。
   *
   * 文档处理进度用 3000ms 轮询，全部进入「可使用 / 处理失败」后由页面传 false 停止。
   */
  refetchInterval?: number | false;
  /** 数据保鲜时长（毫秒），缺省沿用全局 30s */
  staleTime?: number;
  /** 失败重试次数，缺省沿用全局 1 次 */
  retry?: number;
}

/** 变更选项 */
export interface ApiMutationOptions<TData, TVariables> {
  /**
   * 成功后的失效键：可直接给数组，也可按返回数据动态计算。
   *
   * 传前缀键（如 `['kb', baseId, 'documents']`）时，该前缀下所有分页/筛选组合一起失效。
   */
  invalidateKeys?: ApiQueryKey[] | ((data: TData, variables: TVariables) => ApiQueryKey[]);
  /** 成功回调（通常用于 toast） */
  onSuccess?: (data: TData, variables: TVariables) => void;
  /** 失败回调（通常用于 `toast.error(toBusinessMessage(err))`） */
  onError?: (error: Error, variables: TVariables) => void;
  /** 无论成功失败都执行（通常用于关闭弹窗） */
  onSettled?: () => void;
}

/**
 * 全站查询键工厂。
 *
 * 约定：`['kb', baseId]` 是知识库详情域的根键；`['kb', baseId, 'documents']`
 * 与 `['kb', baseId, 'grants']` 为其子键，前缀失效可一次性刷新整个详情页。
 */
export const QUERY_KEYS = {
  /** 会话资料（当前登录用户与所属组织）；键用 `me` 而不是接口名，避免键值混入业务词表 */
  profile: (): ApiQueryKey => ['identity', 'me'],
  /** 知识库列表（含关键字分页） */
  knowledgeBases: (params: Record<string, unknown> = {}): ApiQueryKey => ['kb', 'bases', params],
  /** 知识库详情（名称 / 可见范围 / 我的权限） */
  knowledgeBase: (baseId: string): ApiQueryKey => ['kb', baseId],
  /** 知识库文档清单（分页 + 筛选 + 排序） */
  knowledgeDocuments: (baseId: string, filters: Record<string, unknown> = {}): ApiQueryKey => [
    'kb',
    baseId,
    'documents',
    filters,
  ],
  /** 知识库授权清单 */
  knowledgeGrants: (baseId: string, page: number): ApiQueryKey => ['kb', baseId, 'grants', page],
  /** 引用片段详情（KB4 回链） */
  segment: (baseId: string, documentId: string, segmentId: string): ApiQueryKey => [
    'kb',
    baseId,
    'documents',
    documentId,
    'segments',
    segmentId,
  ],
  /** 部门层级树 */
  departments: (orgId: string): ApiQueryKey => ['identity', 'departments', orgId],
  /** 权限点目录 */
  permissions: (): ApiQueryKey => ['identity', 'permissions'],
  /** 角色列表 */
  roles: (orgId: string): ApiQueryKey => ['identity', 'roles', orgId],
  /** 组织成员列表（U3） */
  members: (orgId: string, params: Record<string, unknown> = {}): ApiQueryKey => [
    'identity',
    'members',
    orgId,
    params,
  ],
  /** 操作动作目录（ID11） */
  auditActions: (): ApiQueryKey => ['identity', 'audit-actions'],
  /** 操作记录查询（U6）；键含查询条件，改筛选即换缓存条目 */
  auditLogs: (body: Record<string, unknown> = {}): ApiQueryKey => ['identity', 'audit-logs', body],

  // ------------------------------------------------------------------ 工具
  /** 工具注册表（T1） */
  tools: (): ApiQueryKey => ['tools', 'registry'],
  /** 工具调用记录（TL1） */
  toolExecutions: (params: Record<string, unknown> = {}): ApiQueryKey => [
    'tools',
    'executions',
    params,
  ],

  // ------------------------------------------------------------------ 计费
  /** 套餐目录 */
  plans: (): ApiQueryKey => ['billing', 'plans'],
  /** 当前订阅（含免费版合成视图） */
  subscription: (): ApiQueryKey => ['billing', 'subscription'],
  /** 用量明细（账期 + 分组维度） */
  billingUsage: (period: string, groupBy: string = 'self'): ApiQueryKey => [
    'billing',
    'usage',
    period,
    groupBy,
  ],
  /** 订单列表（含状态与分页） */
  billingOrders: (params: Record<string, unknown> = {}): ApiQueryKey => [
    'billing',
    'orders',
    params,
  ],
  /** 账单列表 */
  invoices: (period: string = ''): ApiQueryKey => ['billing', 'invoices', period],
  /** 个人本月用量（M4，业务化口径） */
  myUsage: (period: string): ApiQueryKey => ['billing', 'my-usage', period],

  // ------------------------------------------------------------------ 运营
  /** 运营访问探测（企业账号进入运营页会拿到 20003） */
  opsAccess: (): ApiQueryKey => ['ops', 'access'],
  /** 运营-企业列表 */
  opsTenants: (params: Record<string, unknown> = {}): ApiQueryKey => ['ops', 'tenants', params],
  /** 运营-企业详情 */
  opsTenant: (tenantId: string): ApiQueryKey => ['ops', 'tenants', tenantId],
  /** 运营-订单列表 */
  opsOrders: (params: Record<string, unknown> = {}): ApiQueryKey => ['ops', 'orders', params],
  /** 运营-平台统计 */
  opsMetrics: (range: string): ApiQueryKey => ['ops', 'metrics', range],
  /** 运营-系统配置 */
  opsSettings: (): ApiQueryKey => ['ops', 'settings'],
} as const;

/**
 * 通用查询：只做「键 + 取数函数 + 选项」三件事。
 *
 * @param queryKey - 查询键（建议来自 `QUERY_KEYS`）
 * @param fetcher - 取数函数，必须返回 Promise
 * @param options - 查询选项
 * @returns react-query 查询结果（`data` / `isLoading` / `error` / `refetch` 等）
 */
export function useApiQuery<TData>(
  queryKey: ApiQueryKey,
  fetcher: () => Promise<TData>,
  options: ApiQueryOptions = {},
): UseQueryResult<TData, Error> {
  return useQuery<TData, Error>({
    queryKey: queryKey as QueryKey,
    queryFn: fetcher,
    enabled: options.enabled ?? true,
    refetchInterval: options.refetchInterval ?? false,
    ...(options.staleTime === undefined ? {} : { staleTime: options.staleTime }),
    ...(options.retry === undefined ? {} : { retry: options.retry }),
  });
}

/**
 * 通用变更：写接口成功后按声明失效查询，组件不再手写刷新逻辑。
 *
 * @param mutationFn - 写操作函数（入参由调用方决定）
 * @param options - 变更选项（失效键与回调）
 * @returns react-query 变更结果（`mutate` / `mutateAsync` / `isPending` 等）
 */
export function useApiMutation<TData, TVariables>(
  mutationFn: (variables: TVariables) => Promise<TData>,
  options: ApiMutationOptions<TData, TVariables> = {},
): UseMutationResult<TData, Error, TVariables> {
  const queryClient = useQueryClient();

  return useMutation<TData, Error, TVariables>({
    mutationFn,
    onSuccess: (data, variables) => {
      const declared = options.invalidateKeys;
      if (declared) {
        const keys = typeof declared === 'function' ? declared(data, variables) : declared;
        for (const key of keys) {
          void queryClient.invalidateQueries({ queryKey: key as QueryKey });
        }
      }
      options.onSuccess?.(data, variables);
    },
    onError: (error, variables) => {
      options.onError?.(error, variables);
    },
    onSettled: () => {
      options.onSettled?.();
    },
  });
}

/**
 * 手动失效一组查询键（用于「刷新」按钮等场景）。
 *
 * @returns 失效函数：传入键数组，等待全部失效完成
 */
export function useApiInvalidate(): (keys: ApiQueryKey[]) => Promise<void> {
  const queryClient = useQueryClient();

  return useCallback(
    async (keys: ApiQueryKey[]): Promise<void> => {
      await Promise.all(
        keys.map((key) => queryClient.invalidateQueries({ queryKey: key as QueryKey })),
      );
    },
    [queryClient],
  );
}
