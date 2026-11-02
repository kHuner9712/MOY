/**
 * 401 / 20002 会话恢复测试（P0-5 路线 A）。
 *
 * 改造后浏览器不再持有令牌，因此「过期」不再是「跳登录页」的同义词：
 * 收到 401/20002 时先**尝试刷新一次**，成功就把原请求重试一次，失败才广播失效。
 *
 * 这里钉住四条纪律：
 * 1. 只重试**一次**（重试后的响应不再进入恢复分支，防止打不通就无限循环）；
 * 2. 重试必须复用同一把 `Idempotency-Key`（否则一次动作会被后端当成两次）；
 * 3. 并发 401 只触发**一次**刷新（刷新令牌是轮换的，并发刷新会互相打死 = QA-07）；
 * 4. 刷新失败才广播 `emitUnauthorized`，且只广播一次。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ApiClientModule = typeof import('./api-client');
type SessionEventsModule = typeof import('./session-events');

/** 统一信封响应 */
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

/** 刷新成功的响应（`/sso/refresh`） */
function refreshOk(expiresIn = 300): Response {
  return envelope({ ok: true, expires_in: expiresIn });
}

/** 取出第 N 次 fetch 调用 */
function callOf(
  fetchMock: ReturnType<typeof vi.fn>,
  index: number,
): { url: string; init: RequestInit } {
  const call = fetchMock.mock.calls[index];
  return { url: call?.[0] as string, init: call?.[1] as RequestInit };
}

/** 统计打向某个端点的调用次数 */
function countByUrl(fetchMock: ReturnType<typeof vi.fn>, fragment: string): number {
  return fetchMock.mock.calls.filter((call) => String(call[0]).includes(fragment)).length;
}

/**
 * 按 URL 分流的 fetch 替身。
 *
 * `/sso/refresh` 走独立分支（会话端点），其余按 `responses` 顺序回放：
 * 便于表达「第一次 401、刷新成功、重试成功」这种三段式时序。
 */
function sequentialFetch(
  responses: Array<() => Response>,
  refreshResponse: () => Response = () => refreshOk(),
): ReturnType<typeof vi.fn> {
  let index = 0;
  return vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes('/sso/refresh')) {
      return refreshResponse();
    }
    const next = responses[Math.min(index, responses.length - 1)];
    index += 1;
    return next ? next() : envelope({});
  });
}

async function loadModules(): Promise<{ api: ApiClientModule; events: SessionEventsModule }> {
  const api = await import('./api-client');
  const events = await import('./session-events');
  return { api, events };
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('会话恢复 · HTTP 401', () => {
  it('刷新成功则重试原请求一次，并复用同一把幂等键', async () => {
    const { api } = await loadModules();
    const fetchMock = sequentialFetch([
      () => new Response('', { status: 401 }),
      () => envelope({ policies: [] }),
    ]);
    vi.stubGlobal('fetch', fetchMock);

    const result = await api.pluginsApi.setCapabilityPolicy({ policies: [] });

    expect(result.policies).toEqual([]);
    expect(countByUrl(fetchMock, '/sso/refresh')).toBe(1);
    // 原请求 + 刷新 + 重试一次 = 3
    expect(fetchMock).toHaveBeenCalledTimes(3);

    const first = new Headers(callOf(fetchMock, 0).init.headers);
    const retry = new Headers(callOf(fetchMock, 2).init.headers);
    expect(retry.get('Idempotency-Key')).toBe(first.get('Idempotency-Key'));
    expect(retry.get('Authorization')).toBeNull();
    expect(callOf(fetchMock, 2).init.credentials).toBe('include');
  });

  it('刷新也被拒（401/20002）则抛 20002 并广播一次，不再重试', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const fetchMock = sequentialFetch([() => new Response('', { status: 401 })], () =>
      envelope(null, { status: 401, code: 20002, message: '登录状态已失效，请重新登录' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(broadcast).toBe(1);
    // 原请求 + 一次刷新；刷新已是「死透」，不得继续重试
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('刷新遇到 503/30001（统一登录未启用）：抛「暂时不可用」且**不广播**，用户不被踢下线', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const fetchMock = sequentialFetch([() => new Response('', { status: 401 })], () =>
      envelope(null, { status: 503, code: 30001, message: '统一登录未启用' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    // 临时故障 ≠ 登录失效：错误类型必须是「服务不可用」，不是 20002
    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect(error).not.toBeInstanceOf(api.BusinessError);
    expect((error as Error).message).toContain('暂时不可用');
    // 文案不许诱导用户去重新登录（此时他根本登不上）
    expect((error as Error).message).not.toContain('登录状态已失效');
    // 关键：**不广播**。广播 = 清会话 + 跳登录页 = 把网关抖动升级成强制重新登录
    expect(broadcast).toBe(0);
    // 「统一登录未启用」是配置决定的必然失败 → 立即放弃，不重试
    expect(countByUrl(fetchMock, '/sso/refresh')).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('刷新遇到 504/30002：退避重试用尽后抛「暂时不可用」，仍然不广播', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const fetchMock = sequentialFetch([() => new Response('', { status: 401 })], () =>
      envelope(null, { status: 504, code: 30002, message: '上游服务超时' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    vi.useFakeTimers();
    const pending = api.identityApi.profile().catch((caught: unknown) => caught);
    // 覆盖刷新内部两次退避（1s + 2s）与 ≤300ms 抖动，再留余量
    await vi.advanceTimersByTimeAsync(8_000);
    const error = await pending;

    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect(broadcast).toBe(0);
    // 退避重试确实发生了（1 次首发 + 2 次重试），但业务请求**没有**被重试
    expect(countByUrl(fetchMock, '/sso/refresh')).toBe(3);
    const businessCalls = fetchMock.mock.calls.filter((call) => !String(call[0]).includes('/sso/'));
    expect(businessCalls).toHaveLength(1);
    vi.useRealTimers();
  });

  it('刷新请求断网（无响应）：抛「暂时不可用」，不广播', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const fetchMock = sequentialFetch([() => new Response('', { status: 401 })], () => {
      throw new TypeError('Failed to fetch');
    });
    vi.stubGlobal('fetch', fetchMock);

    vi.useFakeTimers();
    const pending = api.identityApi.profile().catch((caught: unknown) => caught);
    await vi.advanceTimersByTimeAsync(8_000);
    const error = await pending;

    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect((error as Error).message).not.toContain('登录状态已失效');
    expect(broadcast).toBe(0);
    vi.useRealTimers();
  });
});

describe('会话恢复 · 信封 20002', () => {
  it('刷新成功则重试一次并拿到正常结果', async () => {
    const { api } = await loadModules();
    const fetchMock = sequentialFetch([
      () => envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' }),
      () => envelope({ items: [] }),
    ]);
    vi.stubGlobal('fetch', fetchMock);

    const result = await api.pluginsApi.installed();

    expect(result.items).toEqual([]);
    expect(countByUrl(fetchMock, '/sso/refresh')).toBe(1);
  });

  it('重试后仍是 20002 就抛出（只重试一次，绝不循环）', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const fetchMock = sequentialFetch([
      () => envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' }),
      () => envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' }),
    ]);
    vi.stubGlobal('fetch', fetchMock);

    const error = await api.pluginsApi.installed().catch((caught: unknown) => caught);

    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    // 原请求 + 刷新 + 重试一次 = 3，不会继续
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(broadcast).toBe(1);
  });
});

describe('会话恢复 · 并发去重', () => {
  it('并发两个 401 只刷新一次（轮换令牌不能被并发刷新打死）', async () => {
    const { api } = await loadModules();
    const fetchMock = sequentialFetch([
      () => new Response('', { status: 401 }),
      () => new Response('', { status: 401 }),
      () => envelope({ user_id: 'u_1' }),
      () => envelope({ items: [] }),
    ]);
    vi.stubGlobal('fetch', fetchMock);

    const [profile, installed] = await Promise.all([
      api.identityApi.profile(),
      api.pluginsApi.installed(),
    ]);

    expect(profile).toEqual({ user_id: 'u_1' });
    expect(installed.items).toEqual([]);
    expect(countByUrl(fetchMock, '/sso/refresh')).toBe(1);
  });
});

describe('会话恢复 · 上传通道', () => {
  it('上传遇到 401 同样刷新后重试一次，且幂等键不变', async () => {
    const { api } = await loadModules();
    const fetchMock = sequentialFetch([
      () => new Response('', { status: 401 }),
      () => envelope({ document_id: 'd1' }),
    ]);
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['内容'], '笔记.md', { type: 'text/markdown' });
    const result = await api.knowledgeApi.uploadDocument('kb_1', file);

    expect(result.document_id).toBe('d1');
    expect(countByUrl(fetchMock, '/sso/refresh')).toBe(1);

    const uploadCalls = fetchMock.mock.calls.filter(
      (call) => !String(call[0]).includes('/sso/refresh'),
    );
    expect(uploadCalls).toHaveLength(2);
    const first = new Headers((uploadCalls[0]?.[1] as RequestInit).headers);
    const retry = new Headers((uploadCalls[1]?.[1] as RequestInit).headers);
    expect(retry.get('Idempotency-Key')).toBe(first.get('Idempotency-Key'));
  });

  it('上传遇到 401 且刷新失败：抛 20002 并广播一次', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const fetchMock = sequentialFetch([() => new Response('', { status: 401 })], () =>
      envelope(null, { status: 401, code: 20002, message: '登录状态已失效，请重新登录' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['x'], 'a.txt');
    const error = await api.knowledgeApi
      .uploadDocument('kb_1', file)
      .catch((caught: unknown) => caught);

    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(broadcast).toBe(1);
  });

  it('已知租户时上传也带 X-Tenant-Id（与 request 同口径）', async () => {
    const { api } = await loadModules();
    api.setTenantId('tnt_9');
    const fetchMock = sequentialFetch([() => envelope({ document_id: 'd1' })]);
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['x'], 'a.txt');
    await api.knowledgeApi.uploadDocument('kb_1', file);

    const headers = new Headers(callOf(fetchMock, 0).init.headers);
    expect(headers.get('X-Tenant-Id')).toBe('tnt_9');
  });

  it('上传遇到信封 20002 且刷新失败：抛 20002，不重复上传', async () => {
    const { api } = await loadModules();
    const fetchMock = sequentialFetch(
      [() => envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' })],
      () => envelope(null, { status: 401, code: 20002, message: '登录状态已失效，请重新登录' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['x'], 'a.txt');
    const error = await api.knowledgeApi
      .uploadDocument('kb_1', file)
      .catch((caught: unknown) => caught);

    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    const uploadCalls = fetchMock.mock.calls.filter(
      (call) => !String(call[0]).includes('/sso/refresh'),
    );
    expect(uploadCalls).toHaveLength(1);
  });

  it('上传重试后仍 20002：广播一次后抛出（不无限重试）', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const fetchMock = sequentialFetch([
      () => new Response('', { status: 401 }),
      () => envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' }),
    ]);
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['x'], 'a.txt');
    const error = await api.knowledgeApi
      .uploadDocument('kb_1', file)
      .catch((caught: unknown) => caught);

    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(broadcast).toBe(1);
    // 原上传 + 刷新 + 一次重试 = 3，不再继续
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('上传首响就是信封 20002（HTTP 200）：刷新后重试一次，重试也 20002 才收口', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => (broadcast += 1));
    const unauthorizedBody = () =>
      envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' });
    const fetchMock = sequentialFetch([unauthorizedBody, unauthorizedBody]);
    vi.stubGlobal('fetch', fetchMock);

    const file = new File(['x'], 'a.txt');
    const error = await api.knowledgeApi
      .uploadDocument('kb_1', file)
      .catch((caught: unknown) => caught);

    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(broadcast).toBe(1);
    const uploadCalls = fetchMock.mock.calls.filter(
      (call) => !String(call[0]).includes('/sso/refresh'),
    );
    // 原上传 + 重试一次 = 2（信封 20002 与网关 401 是同一件事的两种形状）
    expect(uploadCalls).toHaveLength(2);
  });
});

describe('链路追踪头', () => {
  it('响应带 X-Trace-Id 时只进控制台，不进任何界面数据', async () => {
    const { api } = await loadModules();
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ code: 0, message: '', data: { items: [] } }), {
            status: 200,
            headers: { 'Content-Type': 'application/json', 'X-Trace-Id': 'trace-1' },
          }),
      ),
    );

    const result = await api.pluginsApi.installed();

    expect(result.items).toEqual([]);
    expect(info).toHaveBeenCalledWith('[trace]', 'trace-1', 'GET', '/plugins/installed');
    info.mockRestore();
  });
});
