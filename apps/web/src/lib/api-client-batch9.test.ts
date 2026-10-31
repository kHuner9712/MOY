/**
 * 前端补测第九批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：api-client 剩余插件方法、session-store 存储降级分支、
 * format 边界分支。
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
// session-store 存储降级分支
// ---------------------------------------------------------------------------

describe('session-store · 存储降级', () => {
  it('saveSession / loadSession 往返', async () => {
    const store = await import('./session-store');
    store.clearSession();
    const ok = store.saveSession({
      access_token: 'tok',
      refresh_token: 'ref',
      expires_at: Date.now() + 60_000,
    });
    expect(ok).toBe(true);
    const loaded = store.loadSession();
    expect(loaded?.access_token).toBe('tok');
    expect(loaded?.refresh_token).toBe('ref');
  });

  it('expires_at 为 null（无过期信息）可存可读', async () => {
    const store = await import('./session-store');
    store.clearSession();
    expect(store.saveSession({ access_token: 'tok', refresh_token: null, expires_at: null })).toBe(
      true,
    );
    expect(store.loadSession()?.access_token).toBe('tok');
  });

  it('loadSession 无数据时返回 null', async () => {
    const store = await import('./session-store');
    store.clearSession();
    expect(store.loadSession()).toBeNull();
  });

  it('clearSession 清除后 loadSession 返回 null', async () => {
    const store = await import('./session-store');
    store.saveSession({
      access_token: 'tok',
      refresh_token: 'ref',
      expires_at: Date.now() + 60_000,
    });
    store.clearSession();
    expect(store.loadSession()).toBeNull();
  });

  it('形状非法的会话拒绝写入', async () => {
    const store = await import('./session-store');
    store.clearSession();
    // 空令牌
    expect(store.saveSession({ access_token: '', refresh_token: null, expires_at: null })).toBe(
      false,
    );
    // 非法 expires_at（既不是数字也不是 null）
    expect(
      store.saveSession({
        access_token: 'tok',
        refresh_token: null,
        expires_at: 'not-a-date' as unknown as number,
      }),
    ).toBe(false);
    expect(store.loadSession()).toBeNull();
  });

  it('存储内容损坏（非法 JSON）读取返回 null', async () => {
    const store = await import('./session-store');
    window.localStorage.setItem(store.SESSION_STORAGE_KEY, '{broken');
    expect(store.loadSession()).toBeNull();
  });

  it('旧键凭证一次性迁移到新键', async () => {
    const store = await import('./session-store');
    store.clearSession();
    window.localStorage.setItem(store.LEGACY_SESSION_STORAGE_KEY, 'legacy-token');
    const loaded = store.loadSession();
    expect(loaded?.access_token).toBe('legacy-token');
    // 迁移后新键存在、旧键已删
    expect(window.localStorage.getItem(store.SESSION_STORAGE_KEY)).not.toBeNull();
    expect(window.localStorage.getItem(store.LEGACY_SESSION_STORAGE_KEY)).toBeNull();
  });

  it('isSessionExpired：无过期信息不判过期', async () => {
    const store = await import('./session-store');
    const session = { access_token: 'tok', refresh_token: null, expires_at: null };
    expect(store.isSessionExpired(session, 1_000)).toBe(false);
  });

  it('isSessionExpired：过去时刻判过期、未来时刻不判', async () => {
    const store = await import('./session-store');
    const now = 1_000_000;
    expect(
      store.isSessionExpired({ access_token: 't', refresh_token: null, expires_at: now - 1 }, now),
    ).toBe(true);
    expect(
      store.isSessionExpired({ access_token: 't', refresh_token: null, expires_at: now + 1 }, now),
    ).toBe(false);
  });

  it('savePending / loadPending / clearPending 往返', async () => {
    const store = await import('./session-store');
    const pending = {
      state: 'st',
      codeVerifier: 'cv',
      redirectUri: 'https://app/cb',
      createdAt: 123,
    };
    expect(store.savePending(pending)).toBe(true);
    expect(store.loadPending()).toEqual(pending);
    store.clearPending();
    expect(store.loadPending()).toBeNull();
  });

  it('形状非法的 pending 拒绝写入', async () => {
    const store = await import('./session-store');
    store.clearPending();
    expect(
      store.savePending({ state: '', codeVerifier: 'cv', redirectUri: 'u', createdAt: 1 }),
    ).toBe(false);
    expect(store.loadPending()).toBeNull();
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
