/**
 * 会话端点客户端测试（P0-5 路线 A：令牌不进 JavaScript）。
 *
 * 覆盖口径：五函数的成功 / 业务码失败 / 网络失败路径，外加两条**结构性断言**：
 * 1. 每个请求都必须带 `credentials: 'include'`（否则浏览器不会附加 httpOnly Cookie，
 *    表现为「登录了但每个请求都匿名」）；
 * 2. 请求头里**没有** `Authorization`，响应体里**不取**任何令牌字段 ——
 *    这两条是本次改造的全部意义，退化一次就等于白改。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ANONYMOUS_SESSION_STATE,
  completeLogin,
  fetchSession,
  logoutSession,
  normalizeSessionState,
  refreshSession,
  SessionRequestError,
  startLogin,
} from './session-client';

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

/** 取出第 N 次 fetch 调用的 URL 与 init */
function callOf(index = 0): { url: string; init: RequestInit } {
  const call = vi.mocked(fetch).mock.calls[index];
  return { url: call?.[0] as string, init: call?.[1] as RequestInit };
}

/** 取出第 N 次 fetch 调用的请求头 */
function headersOf(index = 0): Headers {
  return new Headers(callOf(index).init.headers);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('session-client · 请求形状（全部走 Cookie，不带任何令牌）', () => {
  it('startLogin 以 POST /sso/start 取授权地址，带 include + no-store', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ authorize_url: 'https://sso.example.com/auth?state=s1' })),
    );

    const url = await startLogin('/team');

    const { url: calledUrl, init } = callOf();
    expect(calledUrl).toBe('/sso/start');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(init.cache).toBe('no-store');
    expect(JSON.parse(String(init.body))).toEqual({ next: '/team' });
    // 授权地址原样返回，前端只负责跳过去
    expect(url).toBe('https://sso.example.com/auth?state=s1');
  });

  it('startLogin 无 next 时发空对象（不伪造深链）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ authorize_url: 'https://sso.example.com/auth' })),
    );
    await startLogin();
    expect(JSON.parse(String(callOf().init.body))).toEqual({});
  });

  it('startLogin 缺授权地址时明确失败（不跳一个空地址）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ authorize_url: '   ' })),
    );
    const error = await startLogin().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(SessionRequestError);
    expect((error as SessionRequestError).message).toContain('登录地址');
  });

  it('completeLogin 以 POST /sso/complete 递 code 与 state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ ok: true, expires_in: 300 })),
    );

    const grant = await completeLogin('c_1', 's_1');

    const { url, init } = callOf();
    expect(url).toBe('/sso/complete');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(JSON.parse(String(init.body))).toEqual({ code: 'c_1', state: 's_1' });
    expect(grant).toEqual({ ok: true, expires_in: 300 });
  });

  it('completeLogin 授权码为空时不发请求（兜底，正常路径不可达）', async () => {
    const fetchMock = vi.fn(async () => envelope({ ok: true, expires_in: 1 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(completeLogin('   ', 's_1')).rejects.toBeInstanceOf(SessionRequestError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('fetchSession 以 GET /sso/session 取四元组', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        envelope({ authenticated: true, expires_in: 240, refreshable: true, expired: false }),
      ),
    );

    const state = await fetchSession();

    const { url, init } = callOf();
    expect(url).toBe('/sso/session');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('include');
    expect(init.cache).toBe('no-store');
    expect(state).toEqual({
      authenticated: true,
      expires_in: 240,
      refreshable: true,
      expired: false,
    });
  });

  it('refreshSession 以 POST /sso/refresh 取新的有效期', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ ok: true, expires_in: 300 })),
    );

    const grant = await refreshSession();

    expect(callOf().url).toBe('/sso/refresh');
    expect(callOf().init.method).toBe('POST');
    expect(callOf().init.credentials).toBe('include');
    expect(grant).toEqual({ ok: true, expires_in: 300 });
  });

  it('logoutSession 以 POST /sso/logout 通知服务端删 Cookie', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ ok: true })),
    );

    await logoutSession();

    expect(callOf().url).toBe('/sso/logout');
    expect(callOf().init.method).toBe('POST');
    expect(callOf().init.credentials).toBe('include');
  });

  it('五个请求都不带 Authorization 头（令牌不进 JS ⇒ 不进请求头）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope({ authorize_url: 'https://sso.example.com/a', ok: true })),
    );

    await startLogin();
    await fetchSession();
    await refreshSession();
    await logoutSession();

    for (let index = 0; index < 4; index += 1) {
      expect(headersOf(index).get('Authorization')).toBeNull();
      expect(callOf(index).init.credentials).toBe('include');
    }
  });
});

describe('session-client · 失败分类所需的双信息', () => {
  it('业务码 30001（统一登录未启用）带 HTTP 503 与业务码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope(null, { status: 503, code: 30001, message: '统一登录未启用' })),
    );

    const error = await refreshSession().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(SessionRequestError);
    expect((error as SessionRequestError).status).toBe(503);
    expect((error as SessionRequestError).code).toBe(30001);
    expect((error as SessionRequestError).message).toBe('统一登录未启用');
  });

  it('业务码 20002（刷新令牌已死）带 HTTP 401', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        envelope(null, { status: 401, code: 20002, message: '登录状态已失效，请重新登录' }),
      ),
    );

    const error = await refreshSession().catch((caught: unknown) => caught);
    expect((error as SessionRequestError).status).toBe(401);
    expect((error as SessionRequestError).code).toBe(20002);
  });

  it('网络失败（fetch 抛错）收敛为 status=0 的业务语言异常', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    const error = await fetchSession().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(SessionRequestError);
    expect((error as SessionRequestError).status).toBe(0);
    expect((error as SessionRequestError).code).toBe(0);
    // 不把底层技术错误直接透给用户
    expect((error as SessionRequestError).message).not.toContain('Failed to fetch');
  });

  it('超时（AbortError）同样收敛为 status=0', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new DOMException('aborted', 'AbortError');
      }),
    );
    const error = await fetchSession().catch((caught: unknown) => caught);
    expect((error as SessionRequestError).status).toBe(0);
  });

  it('真的等满超时后由 AbortController 中止请求（不会无限挂着）', async () => {
    vi.useFakeTimers();
    const aborted = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () => {
              aborted();
              reject(new DOMException('aborted', 'AbortError'));
            });
          }),
      ),
    );

    const pending = fetchSession().catch((caught: unknown) => caught);
    await vi.advanceTimersByTimeAsync(15_000);
    const error = await pending;

    expect(aborted).toHaveBeenCalledTimes(1);
    expect((error as SessionRequestError).status).toBe(0);
    vi.useRealTimers();
  });

  it('无信封的网关 5xx（HTML 错误页）保留 HTTP 状态供上层判 5xx', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>502 Bad Gateway</html>', { status: 502 })),
    );

    const error = await fetchSession().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(SessionRequestError);
    expect((error as SessionRequestError).status).toBe(502);
    expect((error as SessionRequestError).code).toBe(0);
  });

  it('业务码非 0 且无 message 时给兜底文案（不出现空白提示）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => envelope(null, { status: 200, code: 20001, message: '  ' })),
    );
    const error = await fetchSession().catch((caught: unknown) => caught);
    expect((error as SessionRequestError).code).toBe(20001);
    expect((error as SessionRequestError).message.trim()).not.toBe('');
  });
});

describe('session-client · normalizeSessionState', () => {
  it('data 为 null / 非对象时回落「从未登录」', () => {
    expect(normalizeSessionState(null)).toEqual(ANONYMOUS_SESSION_STATE);
    expect(normalizeSessionState('nope')).toEqual(ANONYMOUS_SESSION_STATE);
    expect(normalizeSessionState(undefined)).toEqual(ANONYMOUS_SESSION_STATE);
  });

  it('缺字段时按「假 / null」补齐（不会把 undefined 当 true）', () => {
    expect(normalizeSessionState({})).toEqual({
      authenticated: false,
      expires_in: null,
      refreshable: false,
      expired: false,
    });
  });

  it('expires_in 非法值归 null，合法值取整', () => {
    expect(normalizeSessionState({ expires_in: 'abc' }).expires_in).toBeNull();
    expect(normalizeSessionState({ expires_in: -5 }).expires_in).toBeNull();
    expect(normalizeSessionState({ expires_in: Number.NaN }).expires_in).toBeNull();
    expect(normalizeSessionState({ expires_in: 12.9 }).expires_in).toBe(12);
  });

  it('布尔字段严格按 true 判定（字符串 "true" / 1 都不算）', () => {
    const state = normalizeSessionState({
      authenticated: 'true',
      refreshable: 1,
      expired: 'yes',
    });
    expect(state.authenticated).toBe(false);
    expect(state.refreshable).toBe(false);
    expect(state.expired).toBe(false);
  });

  it('过期但可刷新的组合原样保留（三态判据的输入）', () => {
    const state = normalizeSessionState({
      authenticated: false,
      expires_in: null,
      refreshable: true,
      expired: true,
    });
    expect(state).toEqual({
      authenticated: false,
      expires_in: null,
      refreshable: true,
      expired: true,
    });
  });
});
