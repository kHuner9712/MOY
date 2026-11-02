/**
 * QA 独立探针 2：**五个会话端点不把令牌交还给 JavaScript**（P0-5 路线 A）。
 *
 * 静态扫描（`qa-token-unreachable.test.ts`）证明「代码里没有读令牌的写法」，
 * 但证明不了「服务端就算把令牌塞进响应体，前端也不会把它传出去」。本文件补这一半：
 * 桩响应体里**故意埋上** `access_token` / `refresh_token` / `id_token`（模拟服务端
 * 契约被改坏），然后断言五个函数的返回值里一个令牌字段都没有。
 *
 * 这条断言的价值在于：改造的实质是「令牌对 JS 不可见」，而唯一的破法就是
 * 「某个端点函数把令牌原样转发出去」。只要有一个函数做了这件事，XSS 就又有东西可偷。
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

/** 桩响应体里埋的假令牌（形状像真的，便于按形状二次扫描） */
const FAKE_ACCESS_TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1XzEifQ.c2lnbmF0dXJl';
const FAKE_REFRESH_TOKEN = 'rt_9f3c1a7b2d4e6f80a1b2c3d4e5f6a7b8';
const FAKE_ID_TOKEN = 'id_0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d';

/** 令牌形态的键名（任何一层出现都要上报） */
const TOKEN_KEY_PATTERN = /token|bearer|jwt|credential/i;

/** JWT 形态的字符串（三段 base64url） */
const JWT_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

/**
 * 递归扫描一个返回值，找出任何「看起来是令牌」的东西。
 *
 * @param value - 待扫描的值
 * @param path - 当前 JSON 路径（用于报错定位）
 * @returns 命中路径清单（空数组 = 干净）
 */
function findTokenLeaks(value: unknown, path = '$'): string[] {
  const leaks: string[] = [];

  if (typeof value === 'string') {
    if (JWT_SHAPE.test(value) || value.includes(FAKE_REFRESH_TOKEN) || value.includes(FAKE_ID_TOKEN)) {
      leaks.push(`${path} = 疑似令牌字符串`);
    }
    return leaks;
  }
  if (typeof value !== 'object' || value === null) {
    return leaks;
  }
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (TOKEN_KEY_PATTERN.test(key)) {
      leaks.push(`${path}.${key} = 令牌字段`);
    }
    leaks.push(...findTokenLeaks(child, `${path}.${key}`));
  }
  return leaks;
}

/**
 * 造一个「服务端违约、把令牌塞进 data」的响应。
 *
 * @param data - 响应体 `data`
 * @param status - HTTP 状态码
 * @returns Response 桩
 */
function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ code: 0, message: '', data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 记录最近一次 fetch 的入参 */
interface Captured {
  url: string;
  init: RequestInit;
}

/**
 * 安装 fetch 桩。
 *
 * @param respond - 每次调用返回的 Response
 * @returns 调用记录
 */
function stubFetch(respond: () => Response): Captured[] {
  const calls: Captured[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init: init ?? {} });
      return respond();
    }),
  );
  return calls;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('令牌不外传 · /sso/session', () => {
  it('服务端即使把三个令牌塞进 data，fetchSession 也只回四个字段', () => {
    stubFetch(() =>
      envelope({
        authenticated: true,
        expires_in: 300,
        refreshable: true,
        expired: false,
        access_token: FAKE_ACCESS_TOKEN,
        refresh_token: FAKE_REFRESH_TOKEN,
        id_token: FAKE_ID_TOKEN,
      }),
    );

    return fetchSession().then((state) => {
      expect(state).toEqual({
        authenticated: true,
        expires_in: 300,
        refreshable: true,
        expired: false,
      });
      // 键集合逐字相等：多一个键都不行
      expect(Object.keys(state).sort()).toEqual([
        'authenticated',
        'expired',
        'expires_in',
        'refreshable',
      ]);
      expect(findTokenLeaks(state)).toEqual([]);
    });
  });

  it('normalizeSessionState 对任意怪异输入都不透传额外字段', () => {
    const weird: unknown[] = [
      { authenticated: 1, expires_in: '300', refreshable: 'yes', expired: 0, access_token: 'x' },
      { authenticated: true, expires_in: -5, refreshable: true, expired: false, token: 'y' },
      { authenticated: true, expires_in: Number.NaN, nested: { access_token: 'z' } },
      null,
      undefined,
      'string',
      42,
    ];
    for (const input of weird) {
      const normalized = normalizeSessionState(input);
      expect(Object.keys(normalized).sort()).toEqual([
        'authenticated',
        'expired',
        'expires_in',
        'refreshable',
      ]);
      expect(findTokenLeaks(normalized)).toEqual([]);
    }
  });
});

describe('令牌不外传 · /sso/refresh 与 /sso/complete', () => {
  it('refreshSession 只回 {ok, expires_in}，令牌字段被丢掉', async () => {
    stubFetch(() =>
      envelope({
        ok: true,
        expires_in: 300,
        access_token: FAKE_ACCESS_TOKEN,
        refresh_token: FAKE_REFRESH_TOKEN,
        id_token: FAKE_ID_TOKEN,
        token_type: 'Bearer',
      }),
    );

    const grant = await refreshSession();

    expect(grant).toEqual({ ok: true, expires_in: 300 });
    expect(Object.keys(grant).sort()).toEqual(['expires_in', 'ok']);
    expect(findTokenLeaks(grant)).toEqual([]);
  });

  it('completeLogin 同样只回 {ok, expires_in}', async () => {
    stubFetch(() =>
      envelope({
        ok: true,
        expires_in: 300,
        access_token: FAKE_ACCESS_TOKEN,
        refresh_token: FAKE_REFRESH_TOKEN,
      }),
    );

    const grant = await completeLogin('auth-code-1', 'state-1');

    expect(grant).toEqual({ ok: true, expires_in: 300 });
    expect(findTokenLeaks(grant)).toEqual([]);
  });
});

describe('令牌不外传 · /sso/start 与 /sso/logout', () => {
  it('startLogin 只回授权地址，且返回值里不含任何令牌', async () => {
    const authorizeUrl = 'https://idp.example.com/oauth2/authorize?client_id=moy&state=abc';
    stubFetch(() => envelope({ authorize_url: authorizeUrl, access_token: FAKE_ACCESS_TOKEN }));

    const url = await startLogin('/workflows');

    expect(url).toBe(authorizeUrl);
    expect(findTokenLeaks(url)).toEqual([]);
  });

  it('startLogin 把空 next 序列化成 {}（不发出 next:""）', async () => {
    const calls = stubFetch(() => envelope({ authorize_url: 'https://idp.example.com/a' }));

    await startLogin();

    expect(calls[0]?.init.body).toBe('{}');
  });

  it('logoutSession 不返回值（连 status 都不外泄）', async () => {
    stubFetch(() => envelope({ ok: true }));

    await expect(logoutSession()).resolves.toBeUndefined();
  });
});

describe('五个端点的传输选项与错误形状', () => {
  const scenarios: Array<{ name: string; run: () => Promise<unknown>; path: string }> = [
    {
      name: 'startLogin',
      run: () => startLogin(''),
      path: '/sso/start',
    },
    {
      name: 'completeLogin',
      run: () => completeLogin('c', 's'),
      path: '/sso/complete',
    },
    { name: 'fetchSession', run: () => fetchSession(), path: '/sso/session' },
    { name: 'refreshSession', run: () => refreshSession(), path: '/sso/refresh' },
    { name: 'logoutSession', run: () => logoutSession(), path: '/sso/logout' },
  ];

  it.each(scenarios)(
    '$name 带 Cookie 凭据、不缓存、且不注入 Authorization',
    async ({ run, path }) => {
      const calls = stubFetch(() =>
        envelope({ authorize_url: 'https://idp.example.com/a', ok: true, expires_in: 300 }),
      );

      await run();

      expect(calls).toHaveLength(1);
      const [call] = calls;
      expect(call?.url).toContain(path);
      // 令牌唯一的通道
      expect(call?.init.credentials).toBe('include');
      // 会话状态不可缓存
      expect(call?.init.cache).toBe('no-store');
      // 身份不再走请求头
      expect(new Headers(call?.init.headers).get('Authorization')).toBeNull();
    },
  );

  it('网络层失败记 status=0（绝不因此推出「登录失效」）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    const error = await refreshSession().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(SessionRequestError);
    expect((error as SessionRequestError).status).toBe(0);
    expect((error as SessionRequestError).code).toBe(0);
  });

  it('无信封的 503 保留 HTTP 状态（上层四分类才能判「临时故障」）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>503</html>', { status: 503 })));

    const error = await refreshSession().catch((caught: unknown) => caught);

    expect((error as SessionRequestError).status).toBe(503);
    expect((error as SessionRequestError).code).toBe(0);
  });
});

describe('兜底常量本身不含令牌', () => {
  it('ANONYMOUS_SESSION_STATE 是四元组且四个键都不可写令牌', () => {
    expect(ANONYMOUS_SESSION_STATE).toEqual({
      authenticated: false,
      expires_in: null,
      refreshable: false,
      expired: false,
    });
    expect(findTokenLeaks(ANONYMOUS_SESSION_STATE)).toEqual([]);
  });
});
