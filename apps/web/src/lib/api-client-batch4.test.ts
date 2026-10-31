/**
 * `api-client` 补测第四批（审计 2026-10-31 H1 续）。
 *
 * 第三批覆盖了令牌/请求头/查询/网关拦截/超时/各组代表方法；
 * 本批补充：toolApi、knowledgeExtApi、identityExtApi、opsApi、
 * upload 通道、以及剩余错误路径。
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

function headersOf(fetchMock: ReturnType<typeof vi.fn>, index = 0): Headers {
  return callOf(fetchMock, index).init.headers as Headers;
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// toolApi
// ---------------------------------------------------------------------------

describe('api-client · toolApi', () => {
  it('registry 调用 /tools/registry', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ tools: [] })),
    );
    const result = await api.toolApi.registry();
    expect(result.tools).toEqual([]);
    expect(callOf(vi.mocked(fetch)).url).toContain('/tools/registry');
  });

  it('createConnection POST /tools/connections', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ connection_id: 'c1' })),
    );
    await api.toolApi.createConnection({ tool_type: 'wecom', auth_code: 'code' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/tools/connections');
    expect(init.method).toBe('POST');
  });

  it('listConnections GET /tools/connections', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.toolApi.listConnections({ page: 1 });
    expect(callOf(vi.mocked(fetch)).url).toContain('/tools/connections');
  });

  it('deleteConnection DELETE /tools/connections/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ id: 'c1', deleted: true })),
    );
    await api.toolApi.deleteConnection('c1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/tools/connections/c1');
    expect(init.method).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// knowledgeExtApi
// ---------------------------------------------------------------------------

describe('api-client · knowledgeExtApi', () => {
  it('updateBase PATCH /knowledge/bases/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ base_id: 'kb_1' })),
    );
    await api.knowledgeExtApi.updateBase('kb_1', { name: '新名字' });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/knowledge/bases/kb_1');
    expect(init.method).toBe('PATCH');
  });

  it('deleteBase DELETE /knowledge/bases/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ id: 'kb_1', deleted: true })),
    );
    await api.knowledgeExtApi.deleteBase('kb_1');
    const { init } = callOf(vi.mocked(fetch));
    expect(init.method).toBe('DELETE');
  });

  it('listDocuments GET /knowledge/bases/{id}/documents', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.knowledgeExtApi.listDocuments('kb_1', {});
    expect(callOf(vi.mocked(fetch)).url).toContain('/knowledge/bases/kb_1/documents');
  });
});

// ---------------------------------------------------------------------------
// identityExtApi
// ---------------------------------------------------------------------------

describe('api-client · identityExtApi', () => {
  it('listDepartments GET /identity/organizations/{orgId}/departments', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ departments: [] })),
    );
    await api.identityExtApi.listDepartments('org_1');
    expect(callOf(vi.mocked(fetch)).url).toContain('/identity/organizations/org_1/departments');
  });

  it('listRoles GET /identity/organizations/{orgId}/roles', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [] })),
    );
    await api.identityExtApi.listRoles('org_1');
    expect(callOf(vi.mocked(fetch)).url).toContain('/identity/organizations/org_1/roles');
  });

  it('listPermissions GET /identity/permissions', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ groups: [] })),
    );
    await api.identityExtApi.listPermissions();
    expect(callOf(vi.mocked(fetch)).url).toContain('/identity/permissions');
  });
});

// ---------------------------------------------------------------------------
// opsApi
// ---------------------------------------------------------------------------

describe('api-client · opsApi', () => {
  it('listTenants GET /ops/tenants', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ items: [], total: 0 })),
    );
    await api.opsApi.listTenants({ page: 1 });
    expect(callOf(vi.mocked(fetch)).url).toContain('/ops/tenants');
  });

  it('getMetrics GET /ops/metrics', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.opsApi.getMetrics('7d');
    expect(callOf(vi.mocked(fetch)).url).toContain('/ops/metrics');
  });

  it('getSettings GET /ops/settings', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({})),
    );
    await api.opsApi.getSettings();
    expect(callOf(vi.mocked(fetch)).url).toContain('/ops/settings');
  });
});

// ---------------------------------------------------------------------------
// pluginsApi 补充
// ---------------------------------------------------------------------------

describe('api-client · pluginsApi 补充', () => {
  it('detail GET /plugins/market/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ plugin_id: 'plg_1' })),
    );
    await api.pluginsApi.detail('plg_1');
    expect(callOf(vi.mocked(fetch)).url).toContain('/plugins/market/plg_1');
  });

  it('install POST /plugins/{id}/install', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ installed: true })),
    );
    await api.pluginsApi.install('plg_1', { grants: { 'personal.tool.use': true } });
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1/install');
    expect(init.method).toBe('POST');
  });

  it('uninstall DELETE /plugins/{id}', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ uninstalled: true })),
    );
    await api.pluginsApi.uninstall('plg_1');
    const { url, init } = callOf(vi.mocked(fetch));
    expect(url).toContain('/plugins/plg_1');
    expect(init.method).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// 错误码分流补充
// ---------------------------------------------------------------------------

describe('api-client · 错误码分流', () => {
  it('业务码 15006（额度用尽）保持 BusinessError', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope(null, { status: 402, code: 15006, message: '本月可用量已用完' })),
    );
    const error = await api.modelApi
      .generate({ conversation_id: 'c', profile: 'general' as const, messages: [] })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(15006);
    expect((error as Error).message).toBe('本月可用量已用完');
  });

  it('业务码 20005（冲突）保持 BusinessError', async () => {
    const api = await load();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope(null, { code: 20005, message: '操作冲突，请刷新后重试' })),
    );
    const error = await api.pluginsApi.detail('plg_1').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20005);
  });
});
