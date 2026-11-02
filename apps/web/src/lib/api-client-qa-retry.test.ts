/**
 * QA 独立探针 4：401 / 20002 拦截器的**重试边界**（P0-5 路线 A）。
 *
 * 「刷新成功就重试一次」这句话里有四个独立的失败模式，每一个单独成立都会出事：
 *
 * | 失败模式 | 用户侧后果 | 本文件对应断言 |
 * | --- | --- | --- |
 * | 重试两次或无限重试 | 失效会话的页面把自己打成请求死循环 | 「只重试一次」三条 |
 * | 并发 401 各刷一次 | 轮换刷新令牌被自己打作废 → 全员被踢 | 「并发只刷一次」 |
 * | 重试后仍 20002 静默 | 界面停在错误文案、不跳登录页，用户不知道要重新登录 | 「必须广播 + 抛出」 |
 * | 重试重新生成幂等键 | 一次下单被后端记成两笔（真金白银的事故） | 「复用同一把键」 |
 * | transient 被当成 dead | 网关抖动被判成登录失效 → 发布窗口里全员被踢下线 | 「6e 分流」三条 |
 *
 * 另加一条对比断言：**不同**的用户动作必须拿到**不同**的幂等键——
 * 否则「重试复用」可以被实现成「全局缓存一把键」，那会把重复下单的去重彻底废掉。
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

/** 网关了 401：无信封（真实网关的形态） */
function gateway401(): Response {
  return new Response('', { status: 401 });
}

/** 刷新成功 */
function refreshOk(expiresIn = 300): Response {
  return envelope({ ok: true, expires_in: expiresIn });
}

/** 刷新令牌已死（信封形态） */
function refreshDead(): Response {
  return envelope(null, { status: 401, code: 20002, message: '登录状态已失效，请重新登录' });
}

/** 一次 fetch 调用的可读快照 */
interface Call {
  url: string;
  init: RequestInit;
  headers: Headers;
}

/**
 * 取出全部调用并规范化 headers。
 *
 * @param fetchMock - fetch 桩
 * @returns 调用快照数组
 */
function callsOf(fetchMock: ReturnType<typeof vi.fn>): Call[] {
  return fetchMock.mock.calls.map((call) => {
    const init = (call[1] as RequestInit | undefined) ?? {};
    return { url: String(call[0]), init, headers: new Headers(init.headers) };
  });
}

/**
 * 按「是否打向 /sso/refresh」切分调用。
 *
 * @param calls - 调用快照
 * @returns 业务请求与刷新请求两组
 */
function split(calls: Call[]): { business: Call[]; refresh: Call[] } {
  return {
    business: calls.filter((call) => !call.url.includes('/sso/')),
    refresh: calls.filter((call) => call.url.includes('/sso/refresh')),
  };
}

/**
 * 安装一个「业务请求按序回放、刷新单独分支」的 fetch 桩。
 *
 * @param businessResponses - 业务请求的响应序列（用完后重复最后一项）
 * @param refreshResponse - `/sso/refresh` 的响应
 * @returns fetch 桩
 */
function stubSequential(
  businessResponses: Array<() => Response>,
  refreshResponse: () => Response = () => refreshOk(),
): ReturnType<typeof vi.fn> {
  let index = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes('/sso/refresh')) {
      return refreshResponse();
    }
    const next = businessResponses[Math.min(index, businessResponses.length - 1)];
    index += 1;
    return next ? next() : envelope({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** 下单请求体（类型取自 lib/types 的 CreateOrderRequest） */
const ORDER_BODY = {
  plan_code: 'team_pro',
  seats: 5,
  order_type: 'new',
  payment_method: 'online',
} as const;

/**
 * 假定时器推进窗口（毫秒）。
 *
 * 覆盖刷新内部两次退避（1s + 2s）加每次 ≤300ms 抖动，再留一倍余量。
 */
const RETRY_WINDOW_MS = 8_000;

/**
 * 重新加载模块（`recoveryInFlight` 是模块级状态，必须在用例之间隔离）。
 *
 * @returns api-client 与 session-events 模块
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

describe('验证项 6a · 只重试一次（不多不少）', () => {
  it('401 → 刷新成功 → 重试一次即成功：业务请求恰好 2 次、刷新恰好 1 次', async () => {
    const { api } = await loadModules();
    const fetchMock = stubSequential([gateway401, () => envelope({ items: [] })]);

    const result = await api.pluginsApi.installed();

    expect(result.items).toEqual([]);
    const { business, refresh } = split(callsOf(fetchMock));
    expect(business).toHaveLength(2);
    expect(refresh).toHaveLength(1);
    // 总量也钉住，防止有第三次隐蔽请求
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('401 → 刷新成功 → 重试**又** 401：抛 20002，且不会重试第二次拿到后面的成功响应', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    // 第三发故意给「成功」：若实现悄悄重试了第二次，这里就会成功返回，
    // 断言「必须抛出」随即变红 —— 比让实现死循环更容易观测，也更贴近真实网关行为
    const fetchMock = stubSequential([gateway401, gateway401, () => envelope({ user_id: 'u_1' })]);

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    const { business, refresh } = split(callsOf(fetchMock));
    expect(business).toHaveLength(2); // 原请求 + 重试一次，没有第三次
    expect(refresh).toHaveLength(1); // 只刷一次
    expect(broadcast).toBe(1); // 广播一次，不是零次也不是两次
  });

  it('20002 → 刷新成功 → 重试又 20002：抛 20002 且广播（不是静默）', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    const unauthorizedBody = () =>
      envelope(null, { code: 20002, message: '登录状态已失效，请重新登录' });
    const fetchMock = stubSequential([
      unauthorizedBody,
      unauthorizedBody,
      () => envelope({ items: [] }),
    ]);

    const error = await api.pluginsApi.installed().catch((caught: unknown) => caught);

    // 「静默吞掉」的表现是：错误被 return 掉或变成别的错误码，界面收不到失效信号
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(broadcast).toBe(1);
    const { business, refresh } = split(callsOf(fetchMock));
    expect(business).toHaveLength(2);
    expect(refresh).toHaveLength(1);
  });

  it('刷新失败（429 限流）时也绝不把用户踢下线：不重试业务请求，且刷新自带退避限次', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    const fetchMock = stubSequential([gateway401], () =>
      envelope(null, { status: 429, code: 20006, message: '请求过于频繁' }),
    );

    // 刷新内部有 1s→2s 退避：用假定时器推进，避免真实等待拖慢回归
    vi.useFakeTimers();
    const pending = api.identityApi.profile().catch((caught: unknown) => caught);
    await vi.advanceTimersByTimeAsync(RETRY_WINDOW_MS);
    const error = await pending;

    // 「不踢下线」从口号变成断言：抛的是暂时不可用，且**一次广播都没有**
    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect(error).not.toBeInstanceOf(api.BusinessError);
    expect(broadcast).toBe(0);
    const { business, refresh } = split(callsOf(fetchMock));
    // 刷新本身带 2 次重试（1s/2s 退避），因此刷新端点数 3；关键是**不重试业务请求**
    expect(business).toHaveLength(1);
    expect(refresh).toHaveLength(3);
    // 收尾：没有悬挂的定时器（否则回归跑完进程不退出）
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('验证项 6b · 并发 401 只触发一次刷新', () => {
  it('5 个并发 401 只刷新一次（轮换令牌不能被并发刷新打死）', async () => {
    const { api } = await loadModules();
    let businessCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/sso/refresh')) return refreshOk();
      businessCalls += 1;
      // 前 5 个业务请求全部 401（模拟「令牌刚好过期」），之后的都成功
      return businessCalls <= 5 ? gateway401() : envelope({ items: [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    const results = await Promise.all([
      api.pluginsApi.installed(),
      api.pluginsApi.market(),
      api.statsApi.tasks('7d'),
      api.billingApi.listPlans(),
      api.identityExtApi.listPermissions(),
    ]);

    expect(results).toHaveLength(5);
    const { business, refresh } = split(callsOf(fetchMock));
    expect(refresh).toHaveLength(1);
    expect(business).toHaveLength(10); // 5 次原请求 + 5 次重试
  });

  it('刷新失败时并发请求全部收敛为 20002（不留一个挂着的 promise）', async () => {
    const { api } = await loadModules();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes('/sso/refresh') ? refreshDead() : gateway401(),
      ),
    );

    const settled = await Promise.allSettled([
      api.pluginsApi.installed(),
      api.statsApi.tasks('7d'),
      api.billingApi.listPlans(),
    ]);

    for (const outcome of settled) {
      expect(outcome.status).toBe('rejected');
      const reason = (outcome as PromiseRejectedResult).reason as InstanceType<
        ApiClientModule['BusinessError']
      >;
      expect(reason.code).toBe(20002);
    }
  });
});

describe('验证项 6c · 重试必须复用同一把 Idempotency-Key', () => {
  it('下单 401 后重试：两次请求的幂等键逐字相同（否则后端记成两笔订单）', async () => {
    const { api } = await loadModules();
    const fetchMock = stubSequential([
      gateway401,
      () => envelope({ order_id: 'ord_1', amount_cents: 100, status: 'pending' }),
    ]);

    await api.billingApi.createOrder({ ...ORDER_BODY });

    const { business } = split(callsOf(fetchMock));
    expect(business).toHaveLength(2);
    const [first, retry] = business;
    const firstKey = first?.headers.get('Idempotency-Key');
    const retryKey = retry?.headers.get('Idempotency-Key');
    expect(firstKey).toBeTruthy();
    expect(retryKey).toBe(firstKey);
    // 重试的 URL 与请求体也必须一致（同一笔动作）
    expect(retry?.url).toBe(first?.url);
    expect(retry?.init.body).toBe(first?.init.body);
  });

  it('上传 401 后重试同样复用幂等键', async () => {
    const { api } = await loadModules();
    const fetchMock = stubSequential([gateway401, () => envelope({ document_id: 'doc_1' })]);

    const file = new File(['内容'], '笔记.md', { type: 'text/markdown' });
    await api.knowledgeApi.uploadDocument('kb_1', file);

    const { business } = split(callsOf(fetchMock));
    expect(business).toHaveLength(2);
    expect(business[1]?.headers.get('Idempotency-Key')).toBe(
      business[0]?.headers.get('Idempotency-Key'),
    );
  });

  it('对比断言：两个独立的用户动作必须拿到**不同**的幂等键', async () => {
    const { api } = await loadModules();
    const fetchMock = stubSequential([
      () => envelope({ order_id: 'ord_1' }),
      () => envelope({ order_id: 'ord_2' }),
    ]);

    await api.billingApi.createOrder({ ...ORDER_BODY });
    await api.billingApi.createOrder({ ...ORDER_BODY });

    const { business } = split(callsOf(fetchMock));
    expect(business).toHaveLength(2);
    expect(business[0]?.headers.get('Idempotency-Key')).not.toBe(
      business[1]?.headers.get('Idempotency-Key'),
    );
  });

  it('重试不改 URL：带查询串的请求重试时查询串与首次逐字一致', async () => {
    const { api } = await loadModules();
    const fetchMock = stubSequential([
      gateway401,
      () => envelope({ items: [], meta: { page: 1, page_size: 20, total: 0, total_pages: 0 } }),
    ]);

    await api.pluginsApi.market({ category: 'office', keyword: '文档', page: 2, page_size: 20 });

    const { business } = split(callsOf(fetchMock));
    expect(business).toHaveLength(2);
    expect(business[1]?.url).toBe(business[0]?.url);
    expect(business[0]?.url).toContain('page=2');
  });
});

describe('验证项 6d · 重试不注入 Authorization，且始终带 Cookie 凭据', () => {
  it('原请求与重试都没有 Authorization 头，且 credentials=include', async () => {
    const { api } = await loadModules();
    const fetchMock = stubSequential([gateway401, () => envelope({ items: [] })]);

    await api.pluginsApi.installed();

    const { business } = split(callsOf(fetchMock));
    for (const call of business) {
      expect(call.headers.get('Authorization')).toBeNull();
      expect(call.init.credentials).toBe('include');
    }
  });
});

describe('验证项 6e · transient 与 dead 必须分流（已收口，回归断言）', () => {
  /**
   * 本项原名「已知缺口」，记录的是**修复前**的行为：拦截层把 refresh 的
   * `transient`（429 / 5xx）与 `dead`（401 / 20002）合并处置，
   * 用户因为一次网关抖动被踢回登录页 —— 服务端 QA-02 修掉的那件事在客户端重演。
   *
   * 修复后口径（`recoverSession()` 返回三态 `refreshed | dead | unavailable`）：
   *
   * | refresh 结局 | 拦截层动作 | 断言 |
   * | --- | --- | --- |
   * | `refreshed` | 重试原请求一次 | 6a 三条 |
   * | `dead`（401/20002） | 广播 + 抛 20002 | 6a 第三条 |
   * | `transient`（429/503/504） | **不广播** + 抛「服务暂时不可用」 | 本项两条 |
   *
   * 为什么「不广播」是对的：广播在 `session-gate` 里等于「清会话 + 跳登录页」。
   * 把网关抖动判成它，会把一次可自愈的临时故障升级成强制重新登录，而在发布/扩缩容
   * 窗口里那是**全体在线用户同时被踢**。凭据没坏，就不许动凭据。
   */
  it('refresh 遇 429/20006（限流）且退试用尽：**不广播**，抛「暂时不可用」而不是 20002', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    stubSequential([gateway401], () =>
      envelope(null, { status: 429, code: 20006, message: '请求过于频繁' }),
    );

    vi.useFakeTimers();
    const pending = api.identityApi.profile().catch((caught: unknown) => caught);
    await vi.advanceTimersByTimeAsync(RETRY_WINDOW_MS);
    const error = await pending;

    // 错误类型：服务不可用（不是 20002，也不是任何 BusinessError）
    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect(error).not.toBeInstanceOf(api.BusinessError);
    expect((error as Error).message).toContain('暂时不可用');
    // 文案不许把用户往「重新登录」引（此时他们根本登不上）
    expect((error as Error).message).not.toContain('登录状态已失效');
    // 关键回归点：0 次广播（修复前这里是 1 次）
    expect(broadcast).toBe(0);
    vi.useRealTimers();
  });

  it('refresh 遇 503/30001（网关/配置不可用）：同样不广播、不重试业务请求', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    const fetchMock = stubSequential([gateway401], () =>
      envelope(null, { status: 503, code: 30001, message: '统一登录未启用' }),
    );

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect(broadcast).toBe(0);
    const { business, refresh } = split(callsOf(fetchMock));
    // 没拿到新令牌就不许重试原请求（重试只会再吃一次 401）
    expect(business).toHaveLength(1);
    expect(refresh).toHaveLength(1);
    vi.useRealTimers();
  });

  it('对比：refresh 遇 401/20002（令牌真死）仍然广播并抛 20002（dead 分支没被削弱）', async () => {
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });
    stubSequential([gateway401], refreshDead);

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(api.BusinessError);
    expect((error as InstanceType<ApiClientModule['BusinessError']>).code).toBe(20002);
    expect(broadcast).toBe(1);
  });
});
