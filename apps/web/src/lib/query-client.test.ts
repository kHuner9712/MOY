/** 全局查询默认策略回归网（缓存策略是体感与请求量的直接来源）。 */

import { describe, expect, it } from 'vitest';

import { QUERY_DEFAULTS, createQueryClient } from './query-client';

describe('createQueryClient', () => {
  it('查询默认策略与 QUERY_DEFAULTS 一致', () => {
    const client = createQueryClient();
    const options = client.getDefaultOptions().queries;
    expect(options?.staleTime).toBe(QUERY_DEFAULTS.staleTime);
    expect(options?.retry).toBe(QUERY_DEFAULTS.retry);
    expect(options?.refetchOnWindowFocus).toBe(QUERY_DEFAULTS.refetchOnWindowFocus);
  });

  it('缓存保留 5 分钟且断网重连自动刷新', () => {
    const client = createQueryClient();
    const options = client.getDefaultOptions().queries;
    expect(options?.gcTime).toBe(5 * 60 * 1000);
    expect(options?.refetchOnReconnect).toBe(true);
  });

  it('写操作不自动重试（避免重复提交）', () => {
    const client = createQueryClient();
    expect(client.getDefaultOptions().mutations?.retry).toBe(0);
  });

  it('每次调用返回独立实例（会话间不串数据）', () => {
    expect(createQueryClient()).not.toBe(createQueryClient());
  });
});
