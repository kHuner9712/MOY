/**
 * 会话刷新策略测试 —— 三个坑逐条实测（P0-5 路线 A）。
 *
 * 这三组断言分别对应服务端刚修掉的三类缺陷的**客户端版本**，任何一组退化都会
 * 把缺陷原样搬回浏览器：
 * 1. 三态判据：单布尔会让「过期可刷新」与「从未登录」同处置 → 用户每 5 分钟被踢；
 * 2. 四分类：把 429/5xx 判成凭证失效 → 一次抖动强制重新登录；
 * 3. 主动刷新抖动：多标签页同一毫秒刷新 → 轮换令牌互相打死（刷新风暴）。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  classifyRefreshFailure,
  decideSession,
  DEFAULT_REFRESH_DELAY_MS,
  isSsoDisabled,
  JITTER_MAX_MS,
  LEAD_TIME_MIN_MS,
  LEAD_TIME_SPREAD_MS,
  MAX_REFRESH_RETRIES,
  MIN_REFRESH_DELAY_MS,
  nextRefreshDelay,
  RETRY_AFTER_FAILURE_MS,
  RETRY_BASE_DELAY_MS,
  retryDelay,
  refreshWithRetry,
  scheduleRefresh,
  type RefreshOutcome,
  shouldRefresh,
} from './auth-refresh';
import { SessionRequestError, type SessionState } from './session-client';

/** 构造四元组 */
function state(overrides: Partial<SessionState> = {}): SessionState {
  return {
    authenticated: false,
    expires_in: null,
    refreshable: false,
    expired: false,
    ...overrides,
  };
}

/** 构造一次「失败的刷新」 */
function grantFailure(status: number, code: number, message = '失败了'): SessionRequestError {
  return new SessionRequestError(message, status, code);
}

/** 记录 sleep 调用而不真的等 */
function recorder(): { sleep: (ms: number) => Promise<void>; delays: number[] } {
  const delays: number[] = [];
  return {
    sleep: async (ms: number) => {
      delays.push(ms);
    },
    delays,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// 坑 1：三态判据
// ---------------------------------------------------------------------------

describe('decideSession · 三态判据（四组全测）', () => {
  it('有效会话：直接用（refreshable 与 expired 的取值无关）', () => {
    expect(decideSession(state({ authenticated: true, expires_in: 240, refreshable: false }))).toBe(
      'use',
    );
    expect(decideSession(state({ authenticated: true, expires_in: 240, refreshable: true }))).toBe(
      'use',
    );
  });

  it('过期但可刷新：去刷新（**不是**跳登录页）', () => {
    const expiredButRefreshable = state({
      authenticated: false,
      expires_in: null,
      refreshable: true,
      expired: true,
    });
    expect(decideSession(expiredButRefreshable)).toBe('refresh');
    expect(shouldRefresh(expiredButRefreshable)).toBe(true);
  });

  it('过期且刷新令牌也没了：跳登录页', () => {
    const dead = state({
      authenticated: false,
      expires_in: null,
      refreshable: false,
      expired: true,
    });
    expect(decideSession(dead)).toBe('login');
    expect(shouldRefresh(dead)).toBe(false);
  });

  it('从未登录：跳登录页', () => {
    const anonymous = state({
      authenticated: false,
      expires_in: null,
      refreshable: false,
      expired: false,
    });
    expect(decideSession(anonymous)).toBe('login');
    expect(shouldRefresh(anonymous)).toBe(false);
  });

  it('矛盾组合（authenticated 与 expired 同时为真）按「失效」处理，不当作可直用', () => {
    expect(
      decideSession(
        state({ authenticated: true, expires_in: 100, refreshable: true, expired: true }),
      ),
    ).toBe('refresh');
    expect(
      decideSession(
        state({ authenticated: true, expires_in: 100, refreshable: false, expired: true }),
      ),
    ).toBe('login');
  });

  it('authenticated 但 expires_in 未知仍然可用（有效期只影响何时刷新）', () => {
    expect(decideSession(state({ authenticated: true, expires_in: null }))).toBe('use');
  });
});

// ---------------------------------------------------------------------------
// 坑 2：四分类
// ---------------------------------------------------------------------------

describe('classifyRefreshFailure · 四分类', () => {
  it('401 / 20002 → 死透（跳登录页）', () => {
    expect(classifyRefreshFailure(grantFailure(401, 20002))).toBe('dead');
    // 信封形态：HTTP 200 + 业务码 20002 同样是「刷新令牌已死」
    expect(classifyRefreshFailure(grantFailure(200, 20002))).toBe('dead');
  });

  it('429 / 20006 → 可重试（绝不跳登录页）', () => {
    expect(classifyRefreshFailure(grantFailure(429, 20006))).toBe('retry');
    expect(classifyRefreshFailure(grantFailure(200, 20006))).toBe('retry');
  });

  it('503 / 30001 与 504 / 30002 → 可重试', () => {
    expect(classifyRefreshFailure(grantFailure(503, 30001))).toBe('retry');
    expect(classifyRefreshFailure(grantFailure(504, 30002))).toBe('retry');
  });

  it('其他 5xx（含无信封的 HTML 错误页）→ 可重试', () => {
    expect(classifyRefreshFailure(grantFailure(500, 0))).toBe('retry');
    expect(classifyRefreshFailure(grantFailure(502, 0))).toBe('retry');
  });

  it('网络层失败（status=0）→ 可重试（断网不等于令牌失效）', () => {
    expect(classifyRefreshFailure(grantFailure(0, 0))).toBe('retry');
  });

  it('未知 4xx 与非预期异常 → 可重试（语义不明时不登出用户）', () => {
    expect(classifyRefreshFailure(grantFailure(403, 20003))).toBe('retry');
    expect(classifyRefreshFailure(new Error('boom'))).toBe('retry');
    expect(classifyRefreshFailure('字符串')).toBe('retry');
  });
});

describe('isSsoDisabled · 只认「统一登录未启用」这一种配置态', () => {
  it('文案命中时返回 true（auth_mode=stub 的固定提示）', () => {
    expect(isSsoDisabled(new Error('统一登录未启用'))).toBe(true);
    expect(isSsoDisabled(grantFailure(503, 30001, '统一登录未启用'))).toBe(true);
  });

  it('其它 5xx 文案不命中（该重试就重试）', () => {
    expect(isSsoDisabled(new Error('下游服务不可用'))).toBe(false);
    expect(isSsoDisabled(grantFailure(503, 30001, '服务暂时不可用'))).toBe(false);
  });

  it('非 Error 值一律不命中（不做字符串嗅探）', () => {
    expect(isSsoDisabled('统一登录未启用')).toBe(false);
    expect(isSsoDisabled(null)).toBe(false);
    expect(isSsoDisabled(undefined)).toBe(false);
  });
});

describe('retryDelay · 指数退避 + 抖动', () => {
  it('退避序列为 1s → 2s（random=0 时无抖动）', () => {
    expect(retryDelay(0, RETRY_BASE_DELAY_MS, 300, () => 0)).toBe(1_000);
    expect(retryDelay(1, RETRY_BASE_DELAY_MS, 300, () => 0)).toBe(2_000);
  });

  it('抖动落在 0–300ms 内，且逐次可不同', () => {
    const jittered = retryDelay(0, 1_000, 300, () => 0.5);
    expect(jittered).toBeGreaterThanOrEqual(1_000);
    expect(jittered).toBeLessThan(1_000 + 300);
  });

  it('随机源残缺（NaN / 负数 / ≥1）也不会算出负数或翻倍', () => {
    for (const random of [() => Number.NaN, () => -1, () => 2, () => 1]) {
      const delay = retryDelay(0, 1_000, 300, random);
      expect(delay).toBeGreaterThanOrEqual(1_000);
      expect(delay).toBeLessThan(1_300);
    }
  });
});

// ---------------------------------------------------------------------------
// 坑 2 的执行面：refreshWithRetry
// ---------------------------------------------------------------------------

describe('refreshWithRetry · 四分类处置', () => {
  it('首次成功：refreshed 且带新有效期', async () => {
    const outcome = await refreshWithRetry({
      refresh: async () => ({ ok: true, expires_in: 300 }),
      sleep: recorder().sleep,
    });
    expect(outcome.kind).toBe('refreshed');
    expect(outcome.expires_in).toBe(300);
    expect(outcome.attempts).toBe(1);
  });

  it('401：立刻 relogin，不重试、不等待', async () => {
    const refresh = vi.fn(async () => {
      throw grantFailure(401, 20002);
    });
    const { sleep, delays } = recorder();

    const outcome = await refreshWithRetry({ refresh, sleep });

    expect(outcome.kind).toBe('relogin');
    expect(outcome.attempts).toBe(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(delays).toEqual([]);
  });

  it('429：退避重试，重试用尽才报 transient（绝不 relogin）', async () => {
    const refresh = vi.fn(async () => {
      throw grantFailure(429, 20006);
    });
    const { sleep, delays } = recorder();

    const outcome = await refreshWithRetry({ refresh, sleep, random: () => 0 });

    expect(outcome.kind).toBe('transient');
    expect(outcome.attempts).toBe(MAX_REFRESH_RETRIES + 1);
    expect(refresh).toHaveBeenCalledTimes(MAX_REFRESH_RETRIES + 1);
    expect(delays).toEqual([1_000, 2_000]);
  });

  it('503/30001（非 stub 文案）：限次重试后放弃，且**不跳登录页**', async () => {
    const refresh = vi.fn(async () => {
      throw grantFailure(503, 30001, '下游服务不可用');
    });
    const { sleep, delays } = recorder();

    const outcome = await refreshWithRetry({ refresh, sleep, random: () => 0 });

    expect(outcome.kind).toBe('transient');
    // 关键：限次（最多 2 次重试），不会打成死循环
    expect(refresh).toHaveBeenCalledTimes(MAX_REFRESH_RETRIES + 1);
    expect(delays).toHaveLength(MAX_REFRESH_RETRIES);
  });

  it('504/30002 同样退避重试后放弃', async () => {
    const refresh = vi.fn(async () => {
      throw grantFailure(504, 30002);
    });
    const { sleep } = recorder();
    const outcome = await refreshWithRetry({ refresh, sleep, random: () => 0 });
    expect(outcome.kind).toBe('transient');
    expect(refresh).toHaveBeenCalledTimes(MAX_REFRESH_RETRIES + 1);
  });

  it('30001 且文案为「统一登录未启用」（auth_mode=stub）：立即放弃，不重试', async () => {
    const refresh = vi.fn(async () => {
      throw grantFailure(503, 30001, '统一登录未启用');
    });
    const { sleep, delays } = recorder();

    const outcome = await refreshWithRetry({ refresh, sleep });

    expect(outcome.kind).toBe('transient');
    // stub 环境下五个端点永远 503：重试只会打穿联调环境
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(delays).toEqual([]);
  });

  it('先 429 后成功：第二次拿到 refreshed', async () => {
    let call = 0;
    const refresh = vi.fn(async () => {
      call += 1;
      if (call === 1) throw grantFailure(429, 20006);
      return { ok: true, expires_in: 180 };
    });
    const { sleep } = recorder();

    const outcome = await refreshWithRetry({ refresh, sleep, random: () => 0 });

    expect(outcome.kind).toBe('refreshed');
    expect(outcome.expires_in).toBe(180);
    expect(outcome.attempts).toBe(2);
  });

  it('maxRetries=0 时只发一次请求（调用方可显式关掉重试）', async () => {
    const refresh = vi.fn(async () => {
      throw grantFailure(500, 0);
    });
    const { sleep } = recorder();
    const outcome = await refreshWithRetry({ refresh, sleep, maxRetries: 0 });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(outcome.kind).toBe('transient');
  });

  it('任何结局都不抛异常（调用点漏 catch 也不会崩页）', async () => {
    const refresh = vi.fn(async () => {
      throw new Error('未预期异常');
    });
    await expect(refreshWithRetry({ refresh, sleep: recorder().sleep })).resolves.toMatchObject({
      kind: 'transient',
    });
  });

  it('真实等待走 setTimeout：假定时器下按 1s→2s 推进', async () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => {
      throw grantFailure(503, 30001, '下游服务不可用');
    });

    const pending: Promise<RefreshOutcome> = refreshWithRetry({ refresh, random: () => 0 });

    // 首次调用立刻发出，随后进入 1s 退避
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(999);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(refresh).toHaveBeenCalledTimes(3);

    await expect(pending).resolves.toMatchObject({ kind: 'transient' });
  });
});

// ---------------------------------------------------------------------------
// 坑 3：主动刷新抖动
// ---------------------------------------------------------------------------

describe('nextRefreshDelay · 提前量 + 抖动', () => {
  it('random=0：提前 30s，无抖动', () => {
    expect(nextRefreshDelay(300, { random: () => 0 })).toBe(300_000 - LEAD_TIME_MIN_MS);
  });

  it('random→1：提前量 30+30s、抖动近 30s，仍严格早于过期', () => {
    const delay = nextRefreshDelay(300, { random: () => 0.999 });
    expect(delay).toBeGreaterThan(0);
    // 至少提前 30s 刷新，最多提前 30+30+30s
    expect(delay).toBeLessThanOrEqual(
      300_000 - LEAD_TIME_MIN_MS - Math.floor(0.999 * JITTER_MAX_MS),
    );
    expect(delay).toBeGreaterThanOrEqual(
      300_000 - LEAD_TIME_MIN_MS - LEAD_TIME_SPREAD_MS - JITTER_MAX_MS,
    );
  });

  it('抖动区间为 0–30s：同一有效期的延迟是散开的（刷新风暴的解法）', () => {
    const samples = new Set<number>();
    for (let index = 0; index < 20; index += 1) {
      samples.add(nextRefreshDelay(300, { random: () => index / 20 }));
    }
    expect(samples.size).toBeGreaterThan(1);
    const all = Array.from(samples);
    expect(Math.max(...all) - Math.min(...all)).toBeLessThanOrEqual(
      LEAD_TIME_SPREAD_MS + JITTER_MAX_MS,
    );
  });

  it('有效期未知（null）时给保守默认值', () => {
    expect(nextRefreshDelay(null, { random: () => 0 })).toBe(DEFAULT_REFRESH_DELAY_MS);
  });

  it('有效期极短时不打成紧循环（不低于 1s）', () => {
    expect(nextRefreshDelay(1, { random: () => 0 })).toBe(MIN_REFRESH_DELAY_MS);
    expect(nextRefreshDelay(0, { random: () => 0 })).toBe(MIN_REFRESH_DELAY_MS);
    expect(nextRefreshDelay(-10, { random: () => 0 })).toBe(MIN_REFRESH_DELAY_MS);
  });
});

describe('scheduleRefresh · 到点刷新与重排', () => {
  it('到点才刷新；成功后带新有效期回调并按新有效期重排', async () => {
    vi.useFakeTimers();
    const onRefreshed = vi.fn();
    const refresh = vi.fn(async () => ({
      kind: 'refreshed' as const,
      expires_in: 300,
      attempts: 1,
      error: null,
    }));

    const cancel = scheduleRefresh(120, { refresh, onRefreshed, delay: 1_000, random: () => 0 });

    await vi.advanceTimersByTimeAsync(999);
    expect(refresh).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(onRefreshed).toHaveBeenCalledWith(300);

    // 重排间隔 = 300s - 30s（提前量）- 0（抖动）= 270s
    await vi.advanceTimersByTimeAsync(269_999);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(2);

    cancel();
  });

  it('relogin：回调一次后不再排下一轮（不刷死服务端）', async () => {
    vi.useFakeTimers();
    const onRelogin = vi.fn();
    const onRefreshed = vi.fn();
    const refresh = vi.fn(async () => ({
      kind: 'relogin' as const,
      expires_in: null,
      attempts: 1,
      error: null,
    }));

    const cancel = scheduleRefresh(120, { refresh, onRelogin, onRefreshed, delay: 10 });

    await vi.advanceTimersByTimeAsync(10);
    expect(onRelogin).toHaveBeenCalledTimes(1);
    expect(onRefreshed).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    expect(refresh).toHaveBeenCalledTimes(1);
    cancel();
  });

  it('transient：回调 onUnavailable 并按固定间隔重排（**不登出**）', async () => {
    vi.useFakeTimers();
    const onUnavailable = vi.fn();
    const onRelogin = vi.fn();
    const refresh = vi.fn(async () => ({
      kind: 'transient' as const,
      expires_in: null,
      attempts: MAX_REFRESH_RETRIES + 1,
      error: null,
    }));

    const cancel = scheduleRefresh(120, { refresh, onUnavailable, onRelogin, delay: 10 });

    await vi.advanceTimersByTimeAsync(10);
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    expect(onRelogin).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(RETRY_AFTER_FAILURE_MS - 1);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(2);

    cancel();
  });

  it('取消后不再触发任何回调，也不再发请求', async () => {
    vi.useFakeTimers();
    const onRefreshed = vi.fn();
    const refresh = vi.fn(async () => ({
      kind: 'refreshed' as const,
      expires_in: 300,
      attempts: 1,
      error: null,
    }));

    const cancel = scheduleRefresh(120, { refresh, onRefreshed, delay: 100 });
    cancel();

    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    expect(refresh).not.toHaveBeenCalled();
    expect(onRefreshed).not.toHaveBeenCalled();
  });

  it('刷新过程中的取消不再触发回调（卸载竞态）', async () => {
    vi.useFakeTimers();
    const onRefreshed = vi.fn();
    // 用可变对象持有 resolve：闭包里赋值不会触发「值恒为 null」的类型收窄
    const deferred: { resolve?: (outcome: RefreshOutcome) => void } = {};
    const refresh = vi.fn(
      () =>
        new Promise<RefreshOutcome>((resolve) => {
          deferred.resolve = resolve;
        }),
    );

    const cancel = scheduleRefresh(120, { refresh, onRefreshed, delay: 10 });
    await vi.advanceTimersByTimeAsync(10);
    expect(refresh).toHaveBeenCalledTimes(1);

    cancel();
    deferred.resolve?.({
      kind: 'refreshed',
      expires_in: 300,
      attempts: 1,
      error: null,
    });
    await vi.advanceTimersByTimeAsync(0);

    expect(onRefreshed).not.toHaveBeenCalled();
  });

  it('未注入 refresh 时默认走真实刷新端点（/sso/refresh）', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({ code: 0, message: '', data: { ok: true, expires_in: 300 } }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const onRefreshed = vi.fn();

    const cancel = scheduleRefresh(120, { onRefreshed, delay: 10, random: () => 0 });
    await vi.advanceTimersByTimeAsync(10);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/sso/refresh');
    expect(onRefreshed).toHaveBeenCalledWith(300);

    cancel();
    vi.unstubAllGlobals();
  });
});
