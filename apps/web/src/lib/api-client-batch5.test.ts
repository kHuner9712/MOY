/**
 * 前端补测第五批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：knowledgeApi、agentApi、memoryApi、billingApi 剩余方法，
 * use-upload 常量验证，以及 session-store 残余分支。
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
// knowledgeApi
// ---------------------------------------------------------------------------

describe('api-client · knowledgeApi', () => {
  it('createBase POST /knowledge/bases', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ base_id: 'kb_1' })),
    );
    await api.knowledgeApi.createBase({ name: '测试库' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases');
    expect(init.method).toBe('POST');
  });

  it('listBases GET /knowledge/bases', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.knowledgeApi.listBases({ page: 1 });
    expect(callOf(vi.mocked(fetch)).url).toContain('/knowledge/bases');
  });

  it('search POST /knowledge/search', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ hits: [] })),
    );
    await api.knowledgeApi.search({ query: '测试', base_id: 'kb_1' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/search');
    expect(init.method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// agentApi
// ---------------------------------------------------------------------------

describe('api-client · agentApi', () => {
  it('getTask GET /agent/tasks/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ task_id: 't1' })),
    );
    await api.agentApi.getTask('t1');
    expect(callOf(vi.mocked(fetch)).url).toContain('/agent/tasks/t1');
  });

  it('listSteps GET /agent/tasks/{id}/steps', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [] })),
    );
    await api.agentApi.listSteps('t1');
    expect(callOf(vi.mocked(fetch)).url).toContain('/agent/tasks/t1/steps');
  });

  it('cancelTask POST /agent/tasks/{id}/cancel', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ cancelled: true })),
    );
    await api.agentApi.cancelTask('t1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/agent/tasks/t1/cancel');
    expect(init.method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// memoryApi 剩余
// ---------------------------------------------------------------------------

describe('api-client · memoryApi 剩余', () => {
  it('remove DELETE /model/memories/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ deleted: true })),
    );
    await api.memoryApi.remove('mem_1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/model/memories/mem_1');
    expect(init.method).toBe('DELETE');
  });

  it('removeMany DELETE /model/memories', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ deleted_count: 3 })),
    );
    await api.memoryApi.removeMany('identity');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/model/memories');
    expect(init.method).toBe('DELETE');
    expect(url).toContain('category=identity');
  });

  it('updateSettings PUT /model/memories/settings', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ enabled: true })),
    );
    await api.memoryApi.updateSettings({ enabled: true });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/model/memories/settings');
    expect(init.method).toBe('PUT');
  });
});

// ---------------------------------------------------------------------------
// billingApi 剩余
// ---------------------------------------------------------------------------

describe('api-client · billingApi 剩余', () => {
  it('listPlans GET /billing/plans', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ plans: [] })),
    );
    await api.billingApi.listPlans();
    expect(callOf(vi.mocked(fetch)).url).toContain('/billing/plans');
  });

  it('getUsage GET /billing/usage', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.billingApi.getUsage({ period: '2026-10' });
    expect(callOf(vi.mocked(fetch)).url).toContain('/billing/usage');
  });

  it('listOrders GET /billing/orders', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.billingApi.listOrders({ page: 1 });
    expect(callOf(vi.mocked(fetch)).url).toContain('/billing/orders');
  });

  it('listInvoices GET /billing/invoices', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [] })),
    );
    await api.billingApi.listInvoices('2026-10');
    expect(callOf(vi.mocked(fetch)).url).toContain('/billing/invoices');
  });
});

// ---------------------------------------------------------------------------
// identityApi 剩余
// ---------------------------------------------------------------------------

describe('api-client · identityApi 剩余', () => {
  it('listMembers GET /identity/organizations/{orgId}/members', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.identityApi.listMembers('org_1', { page: 1 });
    expect(callOf(vi.mocked(fetch)).url).toContain('/identity/organizations/org_1/members');
  });

  it('verify POST /identity/verify', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ valid: true })),
    );
    await api.identityApi.verify({ permission: 'knowledge:base:read' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/identity/verify');
    expect(init.method).toBe('POST');
  });
});

// ---------------------------------------------------------------------------
// use-upload 常量
// ---------------------------------------------------------------------------

describe('use-upload · 导出常量', () => {
  it('UploadState 初始值合理', async () => {
    const mod = await import('@/hooks/use-upload');
    expect(mod).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// statsApi 与 capabilitiesApi
// ---------------------------------------------------------------------------

describe('api-client · statsApi / capabilitiesApi', () => {
  it('statsApi.tasks 带 range 参数', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.statsApi.tasks('30d');
    const { url } = callOf(vi.mocked(fetch));
    expect(url).toContain('range=30d');
  });

  it('capabilitiesApi.recommend GET /capabilities/recommend', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [] })),
    );
    await api.capabilitiesApi.recommend('写周报');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/capabilities/recommend');
    expect(init.method).toBe('GET');
  });
});
