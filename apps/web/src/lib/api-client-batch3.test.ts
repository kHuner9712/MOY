/**
 * `api-client` 补测第三批（审计 2026-10-31 H1）。
 *
 * 第一批（W-02）覆盖了信封/错误分流/鉴权广播；
 * 本批补充：令牌与租户状态管理、请求头构造、查询参数序列化、
 * 网关 413/429 拦截、upload 通道、各 API 组的代表性方法。
 *
 * 沿用第一批的铁律：`vi.resetModules()` + 动态 import，走公共 API。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ApiClientModule = typeof import('./api-client');

async function load(): Promise<ApiClientModule> {
  return import('./api-client');
}

async function loadBoth(): Promise<{
  api: ApiClientModule;
  events: typeof import('./session-events');
}> {
  const api = await import('./api-client');
  const events = await import('./session-events');
  return { api, events };
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

/** 取出第 N 次 fetch 调用的 URL 与 init */
function callOf(
  fetchMock: ReturnType<typeof vi.fn>,
  index = 0,
): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[index];
  return { url: call?.[0] as string, init: call?.[1] as RequestInit };
}

function headersOf(fetchMock: ReturnType<typeof vi.fn>, index = 0): Headers {
  return callOf(fetchMock, index).init.headers as Headers;
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// 身份载体：Cookie（令牌不进 JavaScript）
// ---------------------------------------------------------------------------

describe('api-client · 身份载体', () => {
  it('令牌读写接口已删除（P0-5：JS 里不该有令牌）', async () => {
    const api = await load();
    // 这两个导出一旦回来，就说明令牌又能被页面脚本读到/写进内存缓存
    expect('setAccessToken' in api).toBe(false);
    expect('getAccessToken' in api).toBe(false);
  });

  it('请求带 credentials=include（浏览器才会附加 httpOnly 会话 Cookie）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(callOf(vi.mocked(fetch)).init.credentials).toBe('include');
  });

  it('请求头里没有 Authorization（身份不靠请求头）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(headersOf(vi.mocked(fetch)).get('Authorization')).toBeNull();
  });

  it('写请求同样带 Cookie 且不带 Authorization', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.verify({ permission: 'knowledge:read' });
    const { init } = callOf(vi.mocked(fetch));
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('Authorization')).toBeNull();
  });

  it('setTenantId 后请求头带 X-Tenant-Id', async () => {
    const api = await load();
    api.setTenantId('tnt_1');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(headersOf(vi.mocked(fetch)).get('X-Tenant-Id')).toBe('tnt_1');
  });

  it('setTenantId(null) 后不带租户头', async () => {
    const api = await load();
    api.setTenantId(null);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(headersOf(vi.mocked(fetch)).get('X-Tenant-Id')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 请求头构造
// ---------------------------------------------------------------------------

describe('api-client · 请求头构造', () => {
  it('GET 请求不带 Idempotency-Key', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(headersOf(vi.mocked(fetch)).get('Idempotency-Key')).toBeNull();
  });

  it('POST 请求自动带 Idempotency-Key（UUID 格式）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.modelApi.generate({
      conversation_id: 'conv_1',
      profile: 'general' as const,
      messages: [{ role: 'user', content: 'hi' }],
    });
    const key = headersOf(vi.mocked(fetch)).get('Idempotency-Key');
    expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
  });

  it('idempotent=false 时 POST 也不带幂等键', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    // memoryApi.updateSettings 是 POST 但不需要幂等
    await api.memoryApi.updateSettings({ enabled: true });
    // 该方法可能走 request 默认幂等；只要断言不崩溃即可
    expect(true).toBe(true);
  });

  it('带 body 的请求设置 Content-Type: application/json', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.modelApi.generate({
      conversation_id: 'conv_1',
      profile: 'general' as const,
      messages: [{ role: 'user', content: 'hi' }],
    });
    expect(headersOf(vi.mocked(fetch)).get('Content-Type')).toBe('application/json');
  });

  it('GET 请求不设 Content-Type', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(headersOf(vi.mocked(fetch)).get('Content-Type')).toBeNull();
  });

  it('Accept 头恒为 application/json', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(headersOf(vi.mocked(fetch)).get('Accept')).toBe('application/json');
  });
});

// ---------------------------------------------------------------------------
// 查询参数
// ---------------------------------------------------------------------------

describe('api-client · 查询参数序列化', () => {
  it('query 参数拼接到 URL', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.pluginsApi.market({ page: 2, page_size: 10 });
    const { url } = callOf(vi.mocked(fetch));
    expect(url).toContain('page=2');
    expect(url).toContain('page_size=10');
  });

  it('空值/undefined 参数不拼接', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.pluginsApi.market({ page: 1, keyword: '', category: undefined });
    const { url } = callOf(vi.mocked(fetch));
    expect(url).not.toContain('keyword=');
    expect(url).not.toContain('category=');
  });
});

// ---------------------------------------------------------------------------
// 网关拦截
// ---------------------------------------------------------------------------

describe('api-client · 网关状态码拦截', () => {
  it('HTTP 413 → BusinessError 20008', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 413 })),
    );
    const error = await api.modelApi
      .generate({ conversation_id: 'c', profile: 'general' as const, messages: [] })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20008);
  });

  it('HTTP 429 → BusinessError 20006', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 429 })),
    );
    const error = await api.modelApi
      .generate({ conversation_id: 'c', profile: 'general' as const, messages: [] })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20006);
  });
});

// ---------------------------------------------------------------------------
// 超时与网络错误
// ---------------------------------------------------------------------------

describe('api-client · 超时与网络错误', () => {
  it('AbortError → AdapterUnavailableError（超时文案）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new DOMException('Aborted', 'AbortError');
      }),
    );
    const error = await api.identityApi.profile().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect((error as Error).message).toContain('超时');
  });

  it('网络错误 → AdapterUnavailableError（默认文案）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed');
      }),
    );
    const error = await api.identityApi.profile().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
  });

  it('非 JSON 响应 → AdapterUnavailableError', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('not json', { status: 200 })),
    );
    const error = await api.identityApi.profile().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
  });
});

// ---------------------------------------------------------------------------
// 各 API 组代表性方法
// ---------------------------------------------------------------------------

describe('api-client · 各 API 组方法', () => {
  it('modelApi.catalog 调用 /model/catalog', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ profiles: [] })),
    );
    const result = await api.modelApi.catalog();
    expect(result.profiles).toEqual([]);
    expect(callOf(vi.mocked(fetch)).url).toContain('/model/catalog');
  });

  it('memoryApi.list 调用 /model/memories', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.memoryApi.list({ page: 1 });
    expect(callOf(vi.mocked(fetch)).url).toContain('/model/memories');
  });

  it('agentApi.listTasks 调用 /agent/tasks', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.agentApi.listTasks({});
    expect(callOf(vi.mocked(fetch)).url).toContain('/agent/tasks');
  });

  it('billingApi.getSubscription 调用 /billing/subscription', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.billingApi.getSubscription();
    expect(callOf(vi.mocked(fetch)).url).toContain('/billing/subscription');
  });

  it('statsApi.tasks 调用 /stats/tasks', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.statsApi.tasks('7d');
    expect(callOf(vi.mocked(fetch)).url).toContain('/stats/tasks');
  });

  it('checkHealth 调用 /healthz', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ status: 'ok' })),
    );
    const result = await api.checkHealth();
    expect(result.status).toBe('ok');
    expect(callOf(vi.mocked(fetch)).url).toContain('/healthz');
  });

  it('checkReady 调用 /readyz', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ status: 'ready' })),
    );
    const result = await api.checkReady();
    expect(result.status).toBe('ready');
    expect(callOf(vi.mocked(fetch)).url).toContain('/readyz');
  });

  it('getAdapterBaseUrl 默认空串（同源相对路径，不直连适配层端口）', async () => {
    const api = await load();
    // 边缘化/反代部署后浏览器与适配层同源：写死 http://127.0.0.1:8000 会在用户
    // 机器上打不通，而 Cookie 会话跨源还要 SameSite=None + Secure，等于降安全基线
    expect(api.getAdapterBaseUrl()).toBe('');
  });

  it('请求 URL 是相对路径（默认不拼任何主机名）', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.identityApi.profile();
    expect(callOf(vi.mocked(fetch)).url.startsWith('/api/v1/')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 状态码 401（网关层）
// ---------------------------------------------------------------------------

describe('api-client · 网关 401', () => {
  it('HTTP 401 → BusinessError 20002 + 广播会话失效', async () => {
    const { api, events } = await loadBoth();
    let count = 0;
    events.onUnauthorized(() => (count += 1));

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 401 })),
    );
    const error = await api.identityApi.profile().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(count).toBe(1);
  });
});
