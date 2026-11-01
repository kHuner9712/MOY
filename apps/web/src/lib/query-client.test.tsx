/**
 * 前端补测第三十四批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：查询容器基建 —— createQueryClient 的默认策略（30 秒新鲜期 /
 * 只重试 1 次 / 切窗口不刷新 / 断线重连补数据 / 5 分钟缓存 / 变更不重试）、
 * QueryProvider 自建与外部注入两条路径。
 */

import { useQueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import { renderComponent } from '@/test/render-hook';

import { QUERY_DEFAULTS, QueryProvider, createQueryClient } from './query-client';

describe('createQueryClient 默认策略', () => {
  it('查询：新鲜期 30 秒、只重试 1 次、切窗口不刷新、断线补数据', () => {
    const client = createQueryClient();
    const queries = client.getDefaultOptions().queries;

    expect(queries?.staleTime).toBe(QUERY_DEFAULTS.staleTime);
    expect(QUERY_DEFAULTS.staleTime).toBe(30_000);
    expect(queries?.retry).toBe(1);
    // 切窗口不自动刷新：正在填的表单不被打断
    expect(queries?.refetchOnWindowFocus).toBe(false);
    expect(queries?.refetchOnReconnect).toBe(true);
    // 缓存保留 5 分钟：离开再回来仍可命中
    expect(queries?.gcTime).toBe(5 * 60 * 1000);
  });

  it('变更：失败不重试（写操作重试要靠幂等键，不靠自动重发）', () => {
    const client = createQueryClient();
    expect(client.getDefaultOptions().mutations?.retry).toBe(0);
  });
});

describe('QueryProvider', () => {
  it('自建客户端并让子树拿到同一实例', () => {
    let seen: unknown = null;
    function Probe(): null {
      seen = useQueryClient().getDefaultOptions().queries?.staleTime;
      return null;
    }
    const { unmount } = renderComponent(
      <QueryProvider>
        <Probe />
      </QueryProvider>,
    );
    expect(seen).toBe(30_000);
    unmount();
  });

  it('外部注入客户端优先（测试与特殊场景用）', () => {
    const custom = createQueryClient();
    let seen: unknown = null;
    function Probe(): null {
      seen = useQueryClient();
      return null;
    }
    const { unmount } = renderComponent(
      <QueryProvider client={custom}>
        <Probe />
      </QueryProvider>,
    );
    expect(seen).toBe(custom);
    unmount();
  });
});
