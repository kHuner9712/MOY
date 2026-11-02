/**
 * 前端补测第九批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：api-client 剩余插件方法、format 边界分支。
 *
 * 注：本批原有的 `session-store` 存储用例已随 P0-5（令牌不进 JavaScript）删除——
 * 该模块现在只剩「清理历史键」，其行为由 `session-store.test.ts` 单独覆盖。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ApiClientModule = typeof import('./api-client');

async function load(): Promise<ApiClientModule> {
  return import('./api-client');
}

function envelope(
  data: unknown,
  init: { status?: number; code?: number; message?: string } = {},
): Response {
  const { status = 200, code = 0, message = '' } = init;
  return new Response(JSON.stringify({ code, message, data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function callOf(
  fetchMock: ReturnType<typeof vi.fn>,
  index = 0,
): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[index];
  return { url: call?.[0] as string, init: call?.[1] as RequestInit };
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// api-client · pluginsApi 剩余方法
// ---------------------------------------------------------------------------

describe('api-client · pluginsApi 剩余', () => {
  it('upgrade POST /plugins/{id}/upgrade', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ upgraded: true })),
    );
    await api.pluginsApi.upgrade('plg_1', '2.0.0');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/upgrade');
    expect(init.method).toBe('POST');
  });

  it('rollback POST /plugins/{id}/rollback', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ rolled_back: true })),
    );
    await api.pluginsApi.rollback('plg_1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/rollback');
    expect(init.method).toBe('POST');
  });

  it('setSettings PUT /plugins/{id}/settings', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ settings: {} })),
    );
    await api.pluginsApi.setSettings('plg_1', { key: 'value' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/settings');
    expect(init.method).toBe('PUT');
  });

  it('reviews GET /plugins/{id}/reviews', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.pluginsApi.reviews('plg_1', { page: 1 });
    expect(callOf(vi.mocked(fetch)).url).toContain('/plugins/plg_1/reviews');
  });

  it('submitReview POST /plugins/{id}/reviews', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ review_id: 'r1' })),
    );
    await api.pluginsApi.submitReview('plg_1', { rating: 5, content: '好用', anonymous: false });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/reviews');
    expect(init.method).toBe('POST');
  });

  it('setCapabilityPolicy PUT /plugins/capability-policy', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ policies: [] })),
    );
    await api.pluginsApi.setCapabilityPolicy({
      policies: [
        {
          capability_key: 'org.repo.write',
          org_switch_enabled: true,
        },
      ],
    });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/capability-policy');
    expect(init.method).toBe('PUT');
  });
});

// ---------------------------------------------------------------------------
// format 边界分支
// ---------------------------------------------------------------------------

describe('format · 边界分支', () => {
  it('formatCents 零值', async () => {
    const { formatCents } = await import('./format');
    expect(formatCents(0)).toBe('¥0.00');
  });

  it('formatCents 负数', async () => {
    const { formatCents } = await import('./format');
    expect(formatCents(-12345)).toBe('-¥123.45');
  });

  it('formatUnits 零值', async () => {
    const { formatUnits } = await import('./format');
    expect(formatUnits(0)).toBe('0 字');
  });

  it('formatPercent 零值（去掉多余 .0）', async () => {
    const { formatPercent } = await import('./format');
    expect(formatPercent(0)).toBe('0%');
    expect(formatPercent(82.5)).toBe('82.5%');
  });

  it('formatDurationSeconds 零值', async () => {
    const { formatDurationSeconds } = await import('./format');
    expect(formatDurationSeconds(0)).toBe('0 秒');
  });

  it('formatRemainingTime null', async () => {
    const { formatRemainingTime } = await import('./format');
    expect(formatRemainingTime(null)).toBeTruthy();
  });

  it('orPlaceholder 空值使用默认占位', async () => {
    const { orPlaceholder, PLACEHOLDER } = await import('./format');
    expect(orPlaceholder('')).toBe(PLACEHOLDER);
    expect(orPlaceholder(null)).toBe(PLACEHOLDER);
    expect(orPlaceholder(undefined)).toBe(PLACEHOLDER);
    expect(orPlaceholder('有值')).toBe('有值');
    expect(orPlaceholder('', '自定义')).toBe('自定义');
  });
});
