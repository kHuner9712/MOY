/**
 * 前端补测第十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：通用数据请求层 useApiQuery / useApiMutation / useApiInvalidate 的
 * 取数、失效键声明（数组与函数两种形式）与回调时序。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderHook } from '@/test/render-hook';
import { useApiInvalidate, useApiMutation, useApiQuery } from './use-api';

function createClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }): ReactNode {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  queryClient = createClient();
});

afterEach(() => {
  queryClient.clear();
});

describe('useApiQuery', () => {
  it('按取数函数拉取数据', async () => {
    const fetcher = vi.fn(async () => ({ name: '麦子' }));
    const { result, unmount } = renderHook(() => useApiQuery(['identity', 'me'], fetcher), {
      wrapper,
    });

    // react-query 的状态写入与渲染是异步批处理，轮询等待数据就绪
    await act(async () => {
      await vi.waitFor(() => {
        expect(result.current.data).toEqual({ name: '麦子' });
      });
    });
    expect(fetcher).toHaveBeenCalled();
    unmount();
  });

  it('enabled 为 false 时不发起请求', async () => {
    const fetcher = vi.fn(async () => 'x');
    const { unmount } = renderHook(
      () => useApiQuery(['identity', 'me'], fetcher, { enabled: false }),
      { wrapper },
    );

    await act(async () => {});
    expect(fetcher).not.toHaveBeenCalled();
    unmount();
  });
});

describe('useApiMutation · 失效键声明', () => {
  it('成功后按声明的键数组逐个失效', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined);
    const mutationFn = vi.fn(async () => ({ id: '1' }));

    const { result, unmount } = renderHook(
      () =>
        useApiMutation(mutationFn, {
          invalidateKeys: [
            ['kb', 'kb_1', 'documents'],
            ['kb', 'kb_1', 'grants'],
          ],
        }),
      { wrapper },
    );

    await act(async () => {
      await result.current.mutateAsync(undefined as never);
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['kb', 'kb_1', 'documents'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['kb', 'kb_1', 'grants'] });
    unmount();
  });

  it('失效键可由返回数据动态计算', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined);
    const mutationFn = vi.fn(async () => ({ base_id: 'kb_9' }));

    const { result, unmount } = renderHook(
      () =>
        useApiMutation(mutationFn, {
          invalidateKeys: (data) => [['kb', data.base_id]],
        }),
      { wrapper },
    );

    await act(async () => {
      await result.current.mutateAsync(undefined as never);
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['kb', 'kb_9'] });
    unmount();
  });

  it('未声明失效键时不触发失效', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined);
    const { result, unmount } = renderHook(() => useApiMutation(async () => 'ok'), { wrapper });

    await act(async () => {
      await result.current.mutateAsync(undefined as never);
    });
    expect(invalidate).not.toHaveBeenCalled();
    unmount();
  });
});

describe('useApiMutation · 回调时序', () => {
  it('成功依次触发 onSuccess 与 onSettled', async () => {
    const calls: string[] = [];
    const { result, unmount } = renderHook(
      () =>
        useApiMutation(async () => 'ok', {
          onSuccess: () => calls.push('success'),
          onError: () => calls.push('error'),
          onSettled: () => calls.push('settled'),
        }),
      { wrapper },
    );

    await act(async () => {
      await result.current.mutateAsync(undefined as never);
    });
    expect(calls).toEqual(['success', 'settled']);
    unmount();
  });

  it('失败依次触发 onError 与 onSettled', async () => {
    const calls: string[] = [];
    const { result, unmount } = renderHook(
      () =>
        useApiMutation(async () => Promise.reject(new Error('写入失败')), {
          onSuccess: () => calls.push('success'),
          onError: () => calls.push('error'),
          onSettled: () => calls.push('settled'),
        }),
      { wrapper },
    );

    await act(async () => {
      await expect(result.current.mutateAsync(undefined as never)).rejects.toThrow();
    });
    expect(calls).toEqual(['error', 'settled']);
    unmount();
  });
});

describe('useApiInvalidate', () => {
  it('并发失效一组查询键', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined);
    const { result, unmount } = renderHook(() => useApiInvalidate(), { wrapper });

    await act(async () => {
      await result.current([
        ['identity', 'members', 'org_1'],
        ['identity', 'roles', 'org_1'],
      ]);
    });
    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['identity', 'members', 'org_1'] });
    unmount();
  });
});
