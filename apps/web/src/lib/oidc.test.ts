import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  base64UrlEncode,
  buildAuthorizeUrl,
  completeAuthorization,
  createPkcePair,
  createState,
  exchangeCode,
  isPendingExpired,
  parseCallbackQuery,
  PENDING_TTL_MS,
  randomBase64Url,
  RANDOM_BYTE_LENGTH,
  type PendingAuthorization,
} from './oidc';

/**
 * OIDC / PKCE 纯逻辑层的安全红线回归网。
 *
 * 用例围绕红线组织（不是围绕行号）：
 * 1. PKCE 只允许 S256，环境不支持摘要算法时**必须失败**，不得退化成 `plain`；
 * 2. 兑换请求**永不携带 client_secret**、不带浏览器凭据；
 * 3. 回跳校验未通过时**不得**发起兑换（用 fetch 调用计数断言），
 *    `mismatch` / `no-pending` 时连「待完成登录」都不许清（防止伪造跳转打断正常登录）；
 * 4. 待完成登录 10 分钟 TTL，时间戳异常一律按过期处理。
 *
 * `crypto.subtle` 在 jsdom 里不存在，涉及摘要的用例用 `vi.stubGlobal('crypto', ...)`
 * 注入受控实现；每次用例结束 `vi.unstubAllGlobals()` 还原。
 */

/** 环境不支持时的业务语言提示（不出现技术词） */
const UNSUPPORTED_ENVIRONMENT_MESSAGE = '当前环境无法安全登录，请更换浏览器后重试';

/** 填充确定字节的伪随机源（bytes[i] = i），让随机串可断言 */
function deterministicRandom(bytes: Uint8Array): Uint8Array {
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = index;
  }
  return bytes;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('base64UrlEncode', () => {
  it('标准 base64 → base64url（+ / 替换、去掉尾部 =）', () => {
    expect(base64UrlEncode(new Uint8Array([102, 111, 111]))).toBe('Zm9v');
    expect(base64UrlEncode(new Uint8Array([102]))).toBe('Zg');
    expect(base64UrlEncode(new Uint8Array([102, 111]))).toBe('Zm8');
  });

  it('+ / 全部替换为 URL 安全字符', () => {
    // btoa([251,255,191]) = '+/+/'，替换后必须是 '-_-_'，否则会被二次编码导致校验失败
    expect(base64UrlEncode(new Uint8Array([251, 255, 191]))).toBe('-_-_');
    expect(base64UrlEncode(new Uint8Array([255, 255, 255]))).toBe('____');
  });
});

describe('randomBase64Url / createState', () => {
  it('32 字节 → 43 字符 base64url（RFC 7636 区间下沿）', () => {
    vi.stubGlobal('crypto', { getRandomValues: deterministicRandom });
    const value = randomBase64Url();
    expect(value).toHaveLength(43);
    expect(value).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(randomBase64Url(RANDOM_BYTE_LENGTH)).toBe(value);
  });

  it('state 与 verifier 同口径', () => {
    vi.stubGlobal('crypto', { getRandomValues: deterministicRandom });
    expect(createState()).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it('环境不支持安全随机时抛业务语言异常', () => {
    vi.stubGlobal('crypto', undefined);
    expect(() => createState()).toThrow(UNSUPPORTED_ENVIRONMENT_MESSAGE);

    vi.stubGlobal('crypto', {});
    expect(() => randomBase64Url()).toThrow(UNSUPPORTED_ENVIRONMENT_MESSAGE);
  });
});

describe('createPkcePair', () => {
  it('S256 挑战值 = verifier 的 SHA-256 摘要（base64url）', async () => {
    const digest = vi.fn(async () => new Uint8Array([251, 255, 191]).buffer);
    vi.stubGlobal('crypto', {
      getRandomValues: deterministicRandom,
      subtle: { digest },
    });

    const pair = await createPkcePair();
    expect(pair.codeChallengeMethod).toBe('S256');
    expect(pair.codeVerifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    // 摘要返回 [251,255,191]，base64 '+/+/' → base64url '-_-_'
    expect(pair.codeChallenge).toBe('-_-_');

    const [algorithm, data] = digest.mock.calls[0] as unknown as [string, Uint8Array];
    expect(algorithm).toBe('SHA-256');
    expect(new TextDecoder().decode(data)).toBe(pair.codeVerifier);
    expect(pair.codeChallenge).not.toBe(pair.codeVerifier);
  });

  it('无 subtle 时必须失败，绝不退化成 plain', async () => {
    vi.stubGlobal('crypto', { getRandomValues: deterministicRandom });
    await expect(createPkcePair()).rejects.toThrow(UNSUPPORTED_ENVIRONMENT_MESSAGE);

    vi.stubGlobal('crypto', { getRandomValues: deterministicRandom, subtle: {} });
    await expect(createPkcePair()).rejects.toThrow(UNSUPPORTED_ENVIRONMENT_MESSAGE);
  });

  it('摘要算法异常原样抛出（不吞错后降级）', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: deterministicRandom,
      subtle: {
        digest: () => {
          throw new Error('digest failed');
        },
      },
    });
    await expect(createPkcePair()).rejects.toThrow('digest failed');
  });
});

describe('buildAuthorizeUrl', () => {
  const base = {
    issuer: 'https://idp.example.com',
    clientId: 'web-app',
    redirectUri: 'https://app.example.com/callback',
    state: 'state-123',
    codeChallenge: 'chal-lenge',
    scope: 'openid profile',
  };

  it('参数顺序固定、response_type=code、方法恒为 S256', () => {
    expect(buildAuthorizeUrl(base)).toBe(
      'https://idp.example.com/protocol/openid-connect/auth?' +
        'client_id=web-app' +
        '&redirect_uri=https%3A%2F%2Fapp.example.com%2Fcallback' +
        '&response_type=code' +
        '&scope=openid+profile' +
        '&state=state-123' +
        '&code_challenge=chal-lenge' +
        '&code_challenge_method=S256',
    );
  });

  it('issuer 允许带结尾斜杠，拼接时不产生 //', () => {
    expect(buildAuthorizeUrl({ ...base, issuer: 'https://idp.example.com/' })).toBe(
      buildAuthorizeUrl(base),
    );
    expect(buildAuthorizeUrl({ ...base, issuer: 'https://idp.example.com///' })).toBe(
      buildAuthorizeUrl(base),
    );
  });
});

describe('parseCallbackQuery', () => {
  it('code 与 state 齐全 → ok（含/不含问号均可，值去空白）', () => {
    expect(parseCallbackQuery('?code=abc&state=xyz')).toEqual({
      kind: 'ok',
      code: 'abc',
      state: 'xyz',
    });
    expect(parseCallbackQuery('code=abc&state=xyz')).toEqual({
      kind: 'ok',
      code: 'abc',
      state: 'xyz',
    });
    expect(parseCallbackQuery('?code=%20abc%20&state=s1')).toEqual({
      kind: 'ok',
      code: 'abc',
      state: 's1',
    });
  });

  it('服务端明确拒绝优先于半截参数', () => {
    expect(parseCallbackQuery('?error=access_denied&code=abc&state=xyz')).toEqual({
      kind: 'error',
      reason: 'provider-error',
    });
    expect(parseCallbackQuery('?error=access_denied')).toEqual({
      kind: 'error',
      reason: 'provider-error',
    });
    // error 只是空白不算拒绝，继续按正常参数判定
    expect(parseCallbackQuery('?error=%20&code=abc&state=xyz')).toEqual({
      kind: 'ok',
      code: 'abc',
      state: 'xyz',
    });
  });

  it('code 与 state 都没有 → missing（用户直接打开本页）', () => {
    expect(parseCallbackQuery('')).toEqual({ kind: 'missing' });
    expect(parseCallbackQuery('?')).toEqual({ kind: 'missing' });
    expect(parseCallbackQuery(null as unknown as string)).toEqual({ kind: 'missing' });
  });

  it('缺 state / 缺 code 各自报明确原因', () => {
    expect(parseCallbackQuery('?code=abc')).toEqual({ kind: 'error', reason: 'missing-state' });
    expect(parseCallbackQuery('?state=xyz')).toEqual({ kind: 'error', reason: 'missing-code' });
    expect(parseCallbackQuery('?code=&state=')).toEqual({ kind: 'missing' });
  });
});

describe('isPendingExpired', () => {
  const createdAt = 1_700_000_000_000;
  const pending: PendingAuthorization = {
    state: 's',
    codeVerifier: 'v',
    redirectUri: 'https://app.example.com/callback',
    createdAt,
  };

  it('TTL 内未过期，恰好等于 TTL 也算过期', () => {
    expect(isPendingExpired(pending, createdAt + PENDING_TTL_MS - 1)).toBe(false);
    expect(isPendingExpired(pending, createdAt + PENDING_TTL_MS)).toBe(true);
    expect(isPendingExpired(pending, createdAt + PENDING_TTL_MS + 1)).toBe(true);
  });

  it('时钟回拨超过容差按过期处理，容差内不算', () => {
    expect(isPendingExpired(pending, createdAt - 29_000)).toBe(false);
    expect(isPendingExpired(pending, createdAt - 31_000)).toBe(true);
  });

  it('时间戳非法（NaN / 0 / 负数 / 缺失）一律按过期', () => {
    expect(isPendingExpired({ ...pending, createdAt: Number.NaN }, createdAt)).toBe(true);
    expect(isPendingExpired({ ...pending, createdAt: 0 }, createdAt)).toBe(true);
    expect(isPendingExpired({ ...pending, createdAt: -1 }, createdAt)).toBe(true);
    expect(isPendingExpired({ ...pending, createdAt: 'x' as unknown as number }, createdAt)).toBe(
      true,
    );
    expect(
      isPendingExpired({ ...pending, createdAt: undefined as unknown as number }, createdAt),
    ).toBe(true);
  });
});

describe('exchangeCode', () => {
  const options = {
    issuer: 'https://idp.example.com/',
    clientId: 'web-app',
    code: 'auth-code',
    codeVerifier: 'pkce-verifier',
    redirectUri: 'https://app.example.com/callback',
    now: 1_700_000_000_000,
  };

  /**
   * 构造兑换接口的假响应。
   *
   * @param payload - 响应体
   * @param status - HTTP 状态码
   * @returns fetch 可返回的 Response
   */
  function tokenResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  it('兑换成功：令牌去空白、有效期扣除 30 秒容差', async () => {
    const fetchMock = vi.fn(async () =>
      tokenResponse({ access_token: ' tok ', refresh_token: 'r', expires_in: 300 }),
    );
    const result = await exchangeCode({
      ...options,
      fetchImpl: fetchMock as unknown as typeof fetch,
    });

    expect(result).toEqual({
      kind: 'ok',
      session: {
        access_token: 'tok',
        refresh_token: 'r',
        expires_at: options.now + 300_000 - 30_000,
      },
    });

    const [endpoint, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(endpoint).toBe('https://idp.example.com/protocol/openid-connect/token');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('omit');
    expect(init.cache).toBe('no-store');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe(
      'application/x-www-form-urlencoded',
    );

    const body = new URLSearchParams(String(init.body));
    expect(body.get('grant_type')).toBe('authorization_code');
    expect(body.get('client_id')).toBe('web-app');
    expect(body.get('code')).toBe('auth-code');
    expect(body.get('redirect_uri')).toBe('https://app.example.com/callback');
    expect(body.get('code_verifier')).toBe('pkce-verifier');
    // 红线：公开客户端永不携带客户端密钥
    expect(body.has('client_secret')).toBe(false);
  });

  it('refresh_token 缺失或为空白时存 null', async () => {
    const noRefresh = vi.fn(async () => tokenResponse({ access_token: 'tok', expires_in: 300 }));
    const result = await exchangeCode({
      ...options,
      fetchImpl: noRefresh as unknown as typeof fetch,
    });
    expect(result).toEqual({
      kind: 'ok',
      session: { access_token: 'tok', refresh_token: null, expires_at: options.now + 270_000 },
    });

    const blankRefresh = vi.fn(async () =>
      tokenResponse({ access_token: 'tok', refresh_token: '  ', expires_in: 300 }),
    );
    const result2 = await exchangeCode({
      ...options,
      fetchImpl: blankRefresh as unknown as typeof fetch,
    });
    expect(result2.kind === 'ok' && result2.session.refresh_token).toBe(null);
  });

  it('expires_in 缺失 / 非法回默认 300 秒，超上限按 12 小时封顶，且至少留 1 秒', async () => {
    const missing = vi.fn(async () => tokenResponse({ access_token: 'tok' }));
    const r1 = await exchangeCode({ ...options, fetchImpl: missing as unknown as typeof fetch });
    expect(r1).toEqual({
      kind: 'ok',
      session: {
        access_token: 'tok',
        refresh_token: null,
        expires_at: options.now + 300_000 - 30_000,
      },
    });

    const junk = vi.fn(async () =>
      tokenResponse({ access_token: 'tok', expires_in: 'not-a-number' }),
    );
    const r2 = await exchangeCode({ ...options, fetchImpl: junk as unknown as typeof fetch });
    expect(r2.kind === 'ok' && r2.session.expires_at).toBe(options.now + 270_000);

    const huge = vi.fn(async () => tokenResponse({ access_token: 'tok', expires_in: 999_999_999 }));
    const r3 = await exchangeCode({ ...options, fetchImpl: huge as unknown as typeof fetch });
    expect(r3.kind === 'ok' && r3.session.expires_at).toBe(
      options.now + 12 * 60 * 60 * 1000 - 30_000,
    );

    // expires_in 极小（< 30 秒容差）时至少留 1 秒，避免「刚写进去就过期」
    const tiny = vi.fn(async () => tokenResponse({ access_token: 'tok', expires_in: 1 }));
    const r4 = await exchangeCode({ ...options, fetchImpl: tiny as unknown as typeof fetch });
    expect(r4.kind === 'ok' && r4.session.expires_at).toBe(options.now + 1000);
  });

  it('网络异常 / 非 2xx / 响应体不是 JSON → failed', async () => {
    const network = vi.fn(async () => {
      throw new Error('network down');
    });
    expect(
      await exchangeCode({ ...options, fetchImpl: network as unknown as typeof fetch }),
    ).toEqual({
      kind: 'failed',
    });

    const rejected = vi.fn(async () => tokenResponse({ error: 'invalid_grant' }, 400));
    expect(
      await exchangeCode({ ...options, fetchImpl: rejected as unknown as typeof fetch }),
    ).toEqual({
      kind: 'failed',
    });

    const brokenJson = vi.fn(async () => new Response('not-json', { status: 200 }));
    expect(
      await exchangeCode({ ...options, fetchImpl: brokenJson as unknown as typeof fetch }),
    ).toEqual({
      kind: 'failed',
    });
  });

  it('access_token 缺失 / 空 / 非字符串 → failed', async () => {
    for (const payload of [
      {},
      { access_token: '' },
      { access_token: '   ' },
      { access_token: 42 },
    ]) {
      const fetchMock = vi.fn(async () => tokenResponse(payload));
      expect(
        await exchangeCode({ ...options, fetchImpl: fetchMock as unknown as typeof fetch }),
      ).toEqual({
        kind: 'failed',
      });
    }
  });

  it('环境没有 fetch 且未注入 → failed', async () => {
    vi.stubGlobal('fetch', undefined);
    expect(await exchangeCode({ ...options, fetchImpl: undefined })).toEqual({ kind: 'failed' });
  });
});

describe('completeAuthorization', () => {
  const now = 1_700_000_000_000;
  const pending: PendingAuthorization = {
    state: 'state-123',
    codeVerifier: 'pkce-verifier',
    redirectUri: 'https://app.example.com/callback',
    createdAt: now - 1000,
    returnTo: '/chat',
  };

  /** 假 fetch：只用于断言「有没有被调用」 */
  function fakeFetch(payload: unknown = { access_token: 'tok', expires_in: 300 }, status = 200) {
    return vi.fn(
      async () =>
        new Response(JSON.stringify(payload), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
  }

  it('校验通过才兑换：onValidated 先于兑换执行', async () => {
    const fetchMock = fakeFetch();
    const onValidated = vi.fn();
    const result = await completeAuthorization({
      search: '?code=auth-code&state=state-123',
      pending,
      issuer: 'https://idp.example.com',
      clientId: 'web-app',
      fetchImpl: fetchMock as unknown as typeof fetch,
      now,
      onValidated,
    });

    expect(result.kind).toBe('authorized');
    expect(result.reason).toBe(null);
    expect(result.consumePending).toBe(true);
    expect(result.session).toEqual({
      access_token: 'tok',
      refresh_token: null,
      expires_at: now + 300_000 - 30_000,
    });
    expect(onValidated).toHaveBeenCalledTimes(1);
    expect(onValidated.mock.invocationCallOrder[0]).toBeLessThan(
      fetchMock.mock.invocationCallOrder[0] ?? Number.POSITIVE_INFINITY,
    );
  });

  it('onValidated 可选，不传也能完成兑换', async () => {
    const fetchMock = fakeFetch();
    const result = await completeAuthorization({
      search: '?code=auth-code&state=state-123',
      pending,
      issuer: 'https://idp.example.com',
      clientId: 'web-app',
      fetchImpl: fetchMock as unknown as typeof fetch,
      now,
    });
    expect(result.kind).toBe('authorized');
  });

  it('回跳参数不合格：不兑换、不清待完成登录', async () => {
    const fetchMock = fakeFetch();
    const onValidated = vi.fn();
    const result = await completeAuthorization({
      search: '?error=access_denied',
      pending,
      issuer: 'https://idp.example.com',
      clientId: 'web-app',
      fetchImpl: fetchMock as unknown as typeof fetch,
      now,
      onValidated,
    });

    expect(result).toEqual({
      kind: 'rejected',
      reason: 'callback',
      session: null,
      consumePending: false,
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onValidated).not.toHaveBeenCalled();
  });

  it('本地没有待完成登录：不兑换、不写存储', async () => {
    const fetchMock = fakeFetch();
    const result = await completeAuthorization({
      search: '?code=auth-code&state=state-123',
      pending: null,
      issuer: 'https://idp.example.com',
      clientId: 'web-app',
      fetchImpl: fetchMock as unknown as typeof fetch,
      now,
    });

    expect(result).toEqual({
      kind: 'rejected',
      reason: 'no-pending',
      session: null,
      consumePending: false,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('待完成登录已过期：不兑换，但指示清掉（否则每次回来都撞死状态）', async () => {
    const fetchMock = fakeFetch();
    const result = await completeAuthorization({
      search: '?code=auth-code&state=state-123',
      pending: { ...pending, createdAt: now - PENDING_TTL_MS },
      issuer: 'https://idp.example.com',
      clientId: 'web-app',
      fetchImpl: fetchMock as unknown as typeof fetch,
      now,
    });

    expect(result).toEqual({
      kind: 'rejected',
      reason: 'expired',
      session: null,
      consumePending: true,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('state 不匹配：既不兑换也不清待完成登录（可能是伪造跳转）', async () => {
    const fetchMock = fakeFetch();
    const onValidated = vi.fn();
    const result = await completeAuthorization({
      search: '?code=auth-code&state=forged-state',
      pending,
      issuer: 'https://idp.example.com',
      clientId: 'web-app',
      fetchImpl: fetchMock as unknown as typeof fetch,
      now,
      onValidated,
    });

    expect(result).toEqual({
      kind: 'rejected',
      reason: 'mismatch',
      session: null,
      consumePending: false,
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onValidated).not.toHaveBeenCalled();
  });

  it('兑换请求失败：授权码已消耗，指示清掉待完成登录', async () => {
    const fetchMock = fakeFetch({ error: 'invalid_grant' }, 400);
    const result = await completeAuthorization({
      search: '?code=auth-code&state=state-123',
      pending,
      issuer: 'https://idp.example.com',
      clientId: 'web-app',
      fetchImpl: fetchMock as unknown as typeof fetch,
      now,
    });

    expect(result).toEqual({
      kind: 'rejected',
      reason: 'exchange',
      session: null,
      consumePending: true,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
