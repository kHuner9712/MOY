/**
 * QA 独立探针 5：拦截层三态分流（`refreshed | dead | unavailable`）的**四站点 × 四信号**矩阵。
 *
 * 为什么单独一个文件（与工程师的 `api-client-session-recovery-unknown.test.ts` 互补）：
 * `recoverOrReject()` 是 401 / 20002 的**唯一**分流口，`request()` 与 `upload()`
 * 各有两个入口（网关 401 与信封 20002），共 **4 个站点**。这 4 个站点必须
 * **逐一**验证，理由很实在：
 * - 漏掉任何一个站点，该通道就会在网关抖动时把用户踢下线；
 * - 「4 处收敛成 1 个函数」这件事本身只证明了**代码长在一起**，没证明**行为一致**。
 *   历史上这次缺口的形状就是「一个布尔把两类失败压平」，而它出现在 4 处重复代码里，
 *   修的时候最容易只改 `request()` 忘记 `upload()`。
 *
 * 矩阵（4 站点 × 5 种 refresh 结局）：
 *
 * | refresh 结局 | 期望抛出 | 期望广播 | 业务请求次数 |
 * | --- | --- | --- | --- |
 * | `refreshed` | 无 | 0 | 2（原请求 + 重试一次） |
 * | `dead`（401/20002） | `BusinessError(20002)` | **1** | 1 |
 * | `unavailable`（429/20006） | `AdapterUnavailableError` | **0** | 1 |
 * | `unavailable`（503/30001） | `AdapterUnavailableError` | **0** | 1 |
 * | `unavailable`（504/30002） | `AdapterUnavailableError` | **0** | 1 |
 * | `unavailable`（断网 status=0） | `AdapterUnavailableError` | **0** | 1 |
 *
 * 另有两条只属于 `dead` 的行为断言（对应「`rejectUnauthorized()` 返回 `never`」这条
 * 类型事实）：**必须**是 20002、**绝不能**是 `AdapterUnavailableError`——
 * 若将来有人把 `rejectUnauthorized()` 的 `throw` 删了，控制流会「漏下去」执行到
 * 末尾那句 `throw new AdapterUnavailableError()`，本条即变红。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ApiClientModule = typeof import('./api-client');
type SessionEventsModule = typeof import('./session-events');

/** 造统一信封响应 */
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

/** 网关 401：无信封（真实网关的形态） */
function gateway401(): Response {
  return new Response('', { status: 401 });
}

/** 信封里的 20002（HTTP 200 + 业务码），与网关 401 是同一件事的两种形状 */
function envelope20002(): Response {
  return envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' });
}

/** 刷新成功 */
function refreshOk(): Response {
  return envelope({ ok: true, expires_in: 300 });
}

/** 刷新令牌已死 */
function refreshDead(): Response {
  return envelope(null, { status: 401, code: 20002, message: '登录状态已失效，请重新登录' });
}

/** 假定时器推进窗口：覆盖刷新内部 1s + 2s 退避与 ≤300ms 抖动 */
const RETRY_WINDOW_MS = 8_000;

/** 一个「401/20002 入口」的抽象 */
interface Site {
  /** 站点名（出现在断言消息里） */
  name: string;
  /** 首响（制造 401 或 20002） */
  first: () => Response;
  /** 发起请求 */
  run: (api: ApiClientModule, file: File) => Promise<unknown>;
}

/** 业务请求的四个入口 —— 一个都不能少 */
const SITES: Site[] = [
  {
    name: 'request · 网关 401',
    first: gateway401,
    run: (api) => api.pluginsApi.installed(),
  },
  {
    name: 'request · 信封 20002',
    first: envelope20002,
    run: (api) => api.pluginsApi.installed(),
  },
  {
    name: 'upload · 网关 401',
    first: gateway401,
    run: (api, file) => api.knowledgeApi.uploadDocument('kb_1', file),
  },
  {
    name: 'upload · 信封 20002',
    first: envelope20002,
    run: (api, file) => api.knowledgeApi.uploadDocument('kb_1', file),
  },
];

/** 所有 refresh 侧抛出「临时故障」的信号 */
const TRANSIENT_SIGNALS: Array<{
  label: string;
  respond: () => Response;
  expectShortCircuit: boolean;
}> = [
  {
    label: '429 / 20006（限流）',
    respond: () => envelope(null, { status: 429, code: 20006, message: '请求过于频繁' }),
    expectShortCircuit: false,
  },
  {
    label: '503 / 30001（网关不可用）',
    respond: () => envelope(null, { status: 503, code: 30001, message: '下游服务不可用' }),
    expectShortCircuit: false,
  },
  {
    label: '504 / 30002（上游超时）',
    respond: () => envelope(null, { status: 504, code: 30002, message: '上游服务超时' }),
    expectShortCircuit: false,
  },
  {
    label: '断网 / status=0',
    respond: () => {
      throw new TypeError('Failed to fetch');
    },
    expectShortCircuit: false,
  },
  {
    label: '503 / 30001 且文案含「统一登录未启用」（stub）',
    respond: () => envelope(null, { status: 503, code: 30001, message: '统一登录未启用' }),
    // stub 是「配置决定的必然失败」→ 立即放弃，只发 1 次
    expectShortCircuit: true,
  },
];

/** 一次 fetch 调用的可读快照 */
interface Call {
  url: string;
  headers: Headers;
}

/**
 * 按「是否打向 /sso/」切分调用。
 *
 * @param fetchMock - fetch 桩
 * @returns 业务请求与刷新请求两组
 */
function split(fetchMock: ReturnType<typeof vi.fn>): { business: Call[]; refresh: Call[] } {
  const calls: Call[] = fetchMock.mock.calls.map((call) => ({
    url: String(call[0]),
    headers: new Headers((call[1] as RequestInit | undefined)?.headers),
  }));
  return {
    business: calls.filter((call) => !call.url.includes('/sso/')),
    refresh: calls.filter((call) => call.url.includes('/sso/refresh')),
  };
}

/**
 * 安装「首响固定、刷新走单独分支」的 fetch 桩。
 *
 * @param first - 业务请求首响
 * @param refreshResponse - `/sso/refresh` 的响应（可抛）
 * @returns fetch 桩
 */
function stubFetch(
  first: () => Response,
  refreshResponse: () => Response,
): ReturnType<typeof vi.fn> {
  let businessCalls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes('/sso/refresh')) return refreshResponse();
    businessCalls += 1;
    // 第一次给「坏响」，之后（若实现错误地重试）给成功 —— 让「偷偷重试」也能被观测到
    return businessCalls === 1 ? first() : envelope({ items: [] });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/**
 * 重新加载模块（`recoveryInFlight` 是模块级状态）。
 *
 * @returns api-client 与 session-events
 */
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

describe('三态分流 · unavailable 绝不广播（4 站点 × 5 信号）', () => {
  const cases = SITES.flatMap((site) => TRANSIENT_SIGNALS.map((signal) => ({ site, signal })));

  it.each(cases)(
    '$site.name + refresh $signal.label → 抛「暂时不可用」、广播 0 次、不重试业务请求',
    async ({ site, signal }) => {
      const { api, events } = await loadModules();
      let broadcast = 0;
      events.onUnauthorized(() => {
        broadcast += 1;
      });
      const fetchMock = stubFetch(site.first, signal.respond);

      vi.useFakeTimers();
      const pending = site.run(api, new File(['x'], 'a.txt')).catch((caught: unknown) => caught);
      await vi.advanceTimersByTimeAsync(RETRY_WINDOW_MS);
      const error = await pending;

      // 1) 错误类型：服务不可用，绝不是「登录失效」
      expect({
        site: site.name,
        signal: signal.label,
        isUnavailable: error instanceof api.AdapterUnavailableError,
      }).toEqual({ site: site.name, signal: signal.label, isUnavailable: true });
      expect(error).not.toBeInstanceOf(api.BusinessError);
      // 2) 文案不许把用户往「重新登录」引（此时他们根本登不上）
      expect((error as Error).message).toContain('暂时不可用');
      expect((error as Error).message).not.toContain('登录状态已失效');
      // 3) **关键**：一次广播都没有（广播 = 清会话 + 跳登录页）
      expect({ site: site.name, signal: signal.label, broadcast }).toEqual({
        site: site.name,
        signal: signal.label,
        broadcast: 0,
      });
      // 4) 没拿到新令牌就不许重试原请求（重试只会再吃一次 401）
      const { business, refresh } = split(fetchMock);
      expect(business).toHaveLength(1);
      expect(refresh).toHaveLength(signal.expectShortCircuit ? 1 : 3);
      // 5) 没有悬挂定时器
      expect(vi.getTimerCount()).toBe(0);
    },
  );
});

describe('三态分流 · dead 没有被削弱（4 站点）', () => {
  it.each(SITES)(
    '$name + refresh 401/20002 → 抛 20002、广播 1 次、绝不是 AdapterUnavailableError',
    async (site) => {
      const { api, events } = await loadModules();
      let broadcast = 0;
      events.onUnauthorized(() => {
        broadcast += 1;
      });
      const fetchMock = stubFetch(site.first, refreshDead);

      const error = await site
        .run(api, new File(['x'], 'a.txt'))
        .catch((caught: unknown) => caught);

      // `rejectUnauthorized()` 的返回类型是 `never`：它必须**恒抛** 20002。
      // 若有人删掉里面的 throw，控制流会「漏下去」执行到 `throw new AdapterUnavailableError()`，
      // 下面两条断言会同时变红 —— 这就是「dead 分支没有漏下去」的行为证据。
      expect(error).toBeInstanceOf(api.BusinessError);
      expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
      expect(error).not.toBeInstanceOf(api.AdapterUnavailableError);
      expect((error as Error).message).toBe('登录状态已失效，请重新登录');
      expect(broadcast).toBe(1);

      // dead 时不重试业务请求（刷新令牌已死，重试必败）
      const { business, refresh } = split(fetchMock);
      expect(business).toHaveLength(1);
      expect(refresh).toHaveLength(1);
    },
  );
});

describe('三态分流 · refreshed 仍然只重试一次（4 站点）', () => {
  it.each(SITES)('$name + refresh 成功 → 业务请求恰好 2 次、广播 0 次', async (site) => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    const fetchMock = stubFetch(site.first, refreshOk);

    const result = await site.run(api, new File(['x'], 'a.txt')).catch((caught: unknown) => caught);

    expect(result).not.toBeInstanceOf(Error);
    const { business, refresh } = split(fetchMock);
    expect(business).toHaveLength(2);
    expect(refresh).toHaveLength(1);
    expect(broadcast).toBe(0);
  });
});

describe('三态分流 · 重试后仍 401/20002 走 dead（4 站点）', () => {
  it.each(SITES)('$name + refresh 成功但重试又失败 → 抛 20002 且广播 1 次', async (site) => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    // 两发坏响：原请求 + 重试都是 401/20002
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/sso/refresh')) return refreshOk();
      return site.first();
    });
    vi.stubGlobal('fetch', fetchMock);

    const error = await site.run(api, new File(['x'], 'a.txt')).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(broadcast).toBe(1);
    const { business, refresh } = split(fetchMock);
    // 只重试一次：两次业务请求后收口（偷偷第三次才拿到成功也算错）
    expect(business).toHaveLength(2);
    expect(refresh).toHaveLength(1);
  });
});
