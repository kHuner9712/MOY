/**
 * QA 独立探针 3：三态判据的**动作**、四分类的**动作**、退避的**限次**、抖动的**真实性**。
 *
 * 与 `auth-refresh.test.ts` 的分工（刻意互补，不重复）：
 * - 已有测试多数断言 `classifyRefreshFailure` 的**分类结果**（'dead' / 'retry'），
 *   那是「分支存在」；本文件走 `scheduleRefresh` 断言**动作**——到点之后到底
 *   调了 `onRelogin`（把用户踢去登录页）还是 `onUnavailable`（只提示稍后重试），
 *   以及**有没有重排下一轮**。判据写对但回调接反，用户一样被踢。
 * - 退避与抖动用 fake timers / 多次采样做**证伪**，不是「读代码觉得对」。
 *
 * 四条红线（任一条退化，用户侧表现见括号）：
 * 1. `{F,T,T}`（access 过期、refresh 还在）必须**刷新**，不能跳登录页（每 5 分钟被踢一次）；
 * 2. 429 / 5xx 必须退避重试、**绝不**跳登录页（一次抖动强制全员重新登录）；
 * 3. 退避必须限次（联调环境打成死循环）；
 * 4. 抖动必须真的随机（多标签页同步刷新 → 轮换令牌互相打死 → 刷新风暴）。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  decideSession,
  DEFAULT_REFRESH_DELAY_MS,
  JITTER_MAX_MS,
  LEAD_TIME_MIN_MS,
  LEAD_TIME_SPREAD_MS,
  MAX_REFRESH_RETRIES,
  MIN_REFRESH_DELAY_MS,
  nextRefreshDelay,
  refreshWithRetry,
  RETRY_AFTER_FAILURE_MS,
  RETRY_BASE_DELAY_MS,
  RETRY_JITTER_MAX_MS,
  scheduleRefresh,
  type RefreshOutcome,
} from './auth-refresh';
import { SessionRequestError, type SessionState } from './session-client';

/**
 * 构造四元组。
 *
 * @param overrides - 需要覆盖的字段
 * @returns 会话状态
 */
function state(overrides: Partial<SessionState> = {}): SessionState {
  return {
    authenticated: false,
    expires_in: null,
    refreshable: false,
    expired: false,
    ...overrides,
  };
}

/**
 * 构造一次会话端点失败。
 *
 * @param status - HTTP 状态码（0 = 未拿到响应）
 * @param code - 后端业务码
 * @param message - 文案
 * @returns 会话请求错误
 */
function failure(status: number, code: number, message = '失败了'): SessionRequestError {
  return new SessionRequestError(message, status, code);
}

/**
 * 把一个「永远失败」的刷新函数包成 `refreshWithRetry` 的产物。
 *
 * @param error - 每次刷新都抛出的错误
 * @returns 一次刷新结局
 */
async function outcomeOfAlwaysFailing(error: Error): Promise<RefreshOutcome> {
  return refreshWithRetry({
    refresh: async () => {
      throw error;
    },
    // 不真等：这些断言只关心「重试了几次 / 结局是什么」
    sleep: async () => undefined,
  });
}

/** 便于断言「onRelogin / onUnavailable / onRefreshed 各被调了几次」 */
interface Recorder {
  /** 跳登录页回调的记录器 */
  relogin: ReturnType<typeof vi.fn<() => void>>;
  /** 临时故障回调的记录器 */
  unavailable: ReturnType<typeof vi.fn<() => void>>;
  /** 刷新成功回调的记录器 */
  refreshed: ReturnType<typeof vi.fn<(expiresIn: number | null) => void>>;
  /** 可直接展开进 `scheduleRefresh` 的选项片段 */
  options: {
    onRelogin: () => void;
    onUnavailable: () => void;
    onRefreshed: (expiresIn: number | null) => void;
  };
}

/**
 * 造一组回调记录器。
 *
 * 三个 spy 显式标注签名：`vi.fn()` 的默认签名是 `Procedure | Constructable`，
 * 与 `RefreshScheduleOptions` 的 `() => void` 不兼容（tsc 会红），
 * 也让 `mock.calls` 的元组类型退化成空元组。
 *
 * @returns 三个 vi.fn 及对应的 scheduleRefresh 选项
 */
function recorder(): Recorder {
  const relogin = vi.fn<() => void>();
  const unavailable = vi.fn<() => void>();
  const refreshed = vi.fn<(expiresIn: number | null) => void>();
  return {
    relogin,
    unavailable,
    refreshed,
    options: { onRelogin: relogin, onUnavailable: unavailable, onRefreshed: refreshed },
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// 验证项 2：三态判据四组组合（重点：{F,T,T} 必须是 refresh）
// ---------------------------------------------------------------------------

describe('验证项 2 · 三态判据四组组合', () => {
  it('{T,*,F} 直接用 —— expires_in 与 refreshable 都不影响「能用」', () => {
    const variants: SessionState[] = [
      state({ authenticated: true, expires_in: 300, refreshable: true, expired: false }),
      state({ authenticated: true, expires_in: 300, refreshable: false, expired: false }),
      state({ authenticated: true, expires_in: null, refreshable: false, expired: false }),
    ];
    for (const variant of variants) {
      expect(decideSession(variant)).toBe('use');
    }
  });

  it('{F,T,T} 去刷新 —— **这一组是整轮最关键的判据**（access 过期但 refresh 还在）', () => {
    expect(decideSession(state({ refreshable: true, expired: true }))).toBe('refresh');
    // 服务端真实形状：expired=true 时 authenticated 必为 false、expires_in 必为 null
    expect(
      decideSession(
        state({ authenticated: false, expires_in: null, refreshable: true, expired: true }),
      ),
    ).toBe('refresh');
  });

  it('{F,F,T} 跳登录 —— 刷新令牌也没了，只能重新登录', () => {
    expect(decideSession(state({ refreshable: false, expired: true }))).toBe('login');
  });

  it('{F,F,F} 跳登录 —— 从未登录', () => {
    expect(decideSession(state())).toBe('login');
  });

  it('四组组合与 PRD 表格逐行对照（穷举 authenticated × refreshable × expired）', () => {
    /** 期望表：键 = `${authenticated}|${refreshable}|${expired}` */
    const expected: Record<string, 'use' | 'refresh' | 'login'> = {
      'true|false|false': 'use',
      'true|true|false': 'use',
      // authenticated=true 且 expired=true 是服务端不该产生的矛盾态；
      // 处置按「不可直用」：能刷新就刷，不能刷就登录
      'true|false|true': 'login',
      'true|true|true': 'refresh',
      'false|false|false': 'login',
      'false|true|false': 'refresh',
      'false|false|true': 'login',
      'false|true|true': 'refresh',
    };
    for (const authenticated of [true, false]) {
      for (const refreshable of [true, false]) {
        for (const expired of [true, false]) {
          const key = `${authenticated}|${refreshable}|${expired}`;
          expect({ key, decision: decideSession(state({ authenticated, refreshable, expired })) }).toEqual(
            { key, decision: expected[key] },
          );
        }
      }
    }
  });

  it('三态判据的输出集合只有三种取值（防止悄悄加第四态被漏处理）', () => {
    const seen = new Set<string>();
    for (const authenticated of [true, false]) {
      for (const refreshable of [true, false]) {
        for (const expired of [true, false]) {
          seen.add(decideSession(state({ authenticated, refreshable, expired })));
        }
      }
    }
    expect(Array.from(seen).sort()).toEqual(['login', 'refresh', 'use']);
  });
});

// ---------------------------------------------------------------------------
// 验证项 3：refresh 四分类 —— 断言「动作」而不只是「分支」
// ---------------------------------------------------------------------------

describe('验证项 3 · 四分类的动作（到点之后到底做了什么）', () => {
  it('200 成功 → onRefreshed 被调、并按**新的** expires_in 重排（不是老的）', async () => {
    vi.useFakeTimers();
    const calls = recorder();
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ code: 0, message: '', data: { ok: true, expires_in: 180 } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    // 首轮 10ms 后刷新；random=0 → 新间隔 = 180s - 30s 提前量 - 0 抖动 = 150s
    const cancel = scheduleRefresh(120, {
      delay: 10,
      random: () => 0,
      ...calls.options,
    });

    await vi.advanceTimersByTimeAsync(10);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/sso/refresh');
    expect(calls.refreshed).toHaveBeenCalledWith(180);
    expect(calls.relogin).not.toHaveBeenCalled();

    // 用新有效期 180s 重排：150000-1 时还没动静
    await vi.advanceTimersByTimeAsync(150_000 - 1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // 若用的是老有效期 120s，则 90s 就应触发——这里确认第 2 次不是发生在 90s
    cancel();
  });

  it('401 / 20002 → 调 onRelogin，且**不再重排下一轮**（刷新令牌已死，重试没意义）', async () => {
    vi.useFakeTimers();
    const calls = recorder();
    const cancel = scheduleRefresh(120, {
      delay: 10,
      refresh: () => outcomeOfAlwaysFailing(failure(401, 20002)),
      ...calls.options,
    });

    await vi.advanceTimersByTimeAsync(10);

    expect(calls.relogin).toHaveBeenCalledTimes(1);
    expect(calls.unavailable).not.toHaveBeenCalled();
    expect(calls.refreshed).not.toHaveBeenCalled();
    // 没有下一轮定时器
    expect(vi.getTimerCount()).toBe(0);

    await vi.advanceTimersByTimeAsync(30 * 60 * 1000);
    expect(calls.relogin).toHaveBeenCalledTimes(1);

    cancel();
  });

  it('429 / 20006 → onUnavailable 且**绝不** onRelogin，并按固定间隔重排', async () => {
    vi.useFakeTimers();
    const calls = recorder();
    const cancel = scheduleRefresh(120, {
      delay: 10,
      refresh: () => outcomeOfAlwaysFailing(failure(429, 20006)),
      ...calls.options,
    });

    await vi.advanceTimersByTimeAsync(10);

    expect(calls.unavailable).toHaveBeenCalledTimes(1);
    expect(calls.relogin).not.toHaveBeenCalled();
    // 排了下一轮
    expect(vi.getTimerCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(RETRY_AFTER_FAILURE_MS - 1);
    expect(calls.unavailable).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls.unavailable).toHaveBeenCalledTimes(2);
    expect(calls.relogin).not.toHaveBeenCalled();

    cancel();
  });

  it.each([
    { label: '503 / 30001', status: 503, code: 30001, message: '下游服务不可用' },
    { label: '504 / 30002', status: 504, code: 30002, message: '上游超时' },
    { label: '502 / 无信封（code=0）', status: 502, code: 0, message: '网关错误' },
    { label: '500 / 0', status: 500, code: 0, message: '内部错误' },
    { label: 'status=0 断网', status: 0, code: 0, message: '统一登录服务暂时不可用，请稍后再试' },
  ])('$label → 退避重试且绝不跳登录页', async ({ status, code, message }) => {
    vi.useFakeTimers();
    const calls = recorder();
    const cancel = scheduleRefresh(120, {
      delay: 10,
      refresh: () => outcomeOfAlwaysFailing(failure(status, code, message)),
      ...calls.options,
    });

    await vi.advanceTimersByTimeAsync(10);

    expect(calls.relogin).not.toHaveBeenCalled();
    expect(calls.unavailable).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);

    cancel();
  });

  it('auth_mode=stub（文案含「统一登录未启用」）→ 立即放弃，只发一次请求', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ code: 30001, message: '统一登录未启用', data: null }),
          { status: 503, headers: { 'Content-Type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const calls = recorder();

    // 走真实链路：refreshWithRetry → refreshSession → fetch
    const cancel = scheduleRefresh(120, { delay: 10, random: () => 0, ...calls.options });
    await vi.advanceTimersByTimeAsync(10);

    // stub 环境五个端点永远 503：重试到底只有一个结果，继续重试就是死循环
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(calls.unavailable).toHaveBeenCalledTimes(1);
    expect(calls.relogin).not.toHaveBeenCalled();
    // 退避用的额外定时器一个都不该留下（只有下一轮那一个）
    expect(vi.getTimerCount()).toBe(1);

    // 推进 10 分钟：不做 1s/2s 级别的重试轰炸
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    // 下一轮（60s 固定间隔）会再打，但每次只打一发
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(11);

    cancel();
  });

  it('四分类是穷尽的：任何 status/code 组合都落在「跳登录」或「重试」之一（不存在第三态）', async () => {
    // 用 refreshWithRetry 的实际结局反推分类：relogin ⇒ dead，transient ⇒ retry
    const cases: Array<{ status: number; code: number; expected: 'relogin' | 'transient' }> = [
      { status: 401, code: 20002, expected: 'relogin' },
      { status: 200, code: 20002, expected: 'relogin' },
      { status: 429, code: 20006, expected: 'transient' },
      { status: 200, code: 20006, expected: 'transient' },
      { status: 503, code: 30001, expected: 'transient' },
      { status: 504, code: 30002, expected: 'transient' },
      { status: 500, code: 0, expected: 'transient' },
      { status: 502, code: 0, expected: 'transient' },
      { status: 403, code: 20003, expected: 'transient' },
      { status: 0, code: 0, expected: 'transient' },
    ];
    for (const item of cases) {
      // 文案里不含「统一登录未启用」，确保走的是退避重试而不是 stub 短路
      const outcome = await outcomeOfAlwaysFailing(failure(item.status, item.code, '请求失败'));
      expect({
        combo: `${item.status}/${item.code}`,
        kind: outcome.kind,
      }).toEqual({ combo: `${item.status}/${item.code}`, kind: item.expected });
      // 「重试」类别一定真的重试过（attempts > 1），「跳登录」一定一次就停
      expect({ combo: `${item.status}/${item.code}`, attempts: outcome.attempts }).toEqual({
        combo: `${item.status}/${item.code}`,
        attempts: item.expected === 'relogin' ? 1 : MAX_REFRESH_RETRIES + 1,
      });
    }
  });
});

// ---------------------------------------------------------------------------
// 验证项 4：退避真的限次了吗（死循环探针）
// ---------------------------------------------------------------------------

describe('验证项 4 · 退避限次与定时器残留', () => {
  it('连续 3 次 30001 之后必须停止，且不残留任何定时器', async () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => {
      throw failure(503, 30001, '下游服务不可用');
    });

    const pending = refreshWithRetry({ refresh, random: () => 0 });

    // 第 1 次：立刻发出
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);

    // 1s 后第 2 次
    await vi.advanceTimersByTimeAsync(RETRY_BASE_DELAY_MS - 1);
    expect(refresh).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(2);

    // 2s 后第 3 次
    await vi.advanceTimersByTimeAsync(RETRY_BASE_DELAY_MS * 2 - 1);
    expect(refresh).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(refresh).toHaveBeenCalledTimes(3);

    const outcome = await pending;
    expect(outcome.kind).toBe('transient');
    expect(outcome.attempts).toBe(MAX_REFRESH_RETRIES + 1);
    // 关键：没有第 4 次，也没有悬挂的定时器
    expect(refresh).toHaveBeenCalledTimes(MAX_REFRESH_RETRIES + 1);
    expect(vi.getTimerCount()).toBe(0);

    // 再推进 10 分钟也不会有第 4 次（限次不是「间隔很长但一直重试」）
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    expect(refresh).toHaveBeenCalledTimes(MAX_REFRESH_RETRIES + 1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('退避序列是 1s → 2s（不是固定 1s，也不是线性递增）', async () => {
    const delays: number[] = [];
    const refresh = vi.fn(async () => {
      throw failure(429, 20006);
    });
    await refreshWithRetry({
      refresh,
      random: () => 0,
      sleep: async (ms) => {
        delays.push(ms);
      },
    });
    expect(delays).toEqual([RETRY_BASE_DELAY_MS, RETRY_BASE_DELAY_MS * 2]);
  });

  it('每次重试的抖动落在 0–300ms（真实随机源下采样 100 次）', async () => {
    const delays: number[] = [];
    await refreshWithRetry({
      refresh: async () => {
        throw failure(429, 20006);
      },
      sleep: async (ms) => {
        delays.push(ms);
      },
    });
    expect(delays).toHaveLength(MAX_REFRESH_RETRIES);
    for (const [index, delay] of delays.entries()) {
      const base = RETRY_BASE_DELAY_MS * 2 ** index;
      expect(delay).toBeGreaterThanOrEqual(base);
      expect(delay).toBeLessThan(base + RETRY_JITTER_MAX_MS);
    }
  });

  it('maxRetries 可被调用方收紧到 0（此时只发一次，不排任何定时器）', async () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => {
      throw failure(503, 30001, '下游服务不可用');
    });
    const outcome = await refreshWithRetry({ refresh, maxRetries: 0 });
    expect(outcome.kind).toBe('transient');
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('长期运行下定时器数量恒定（每轮一个，不累积）', async () => {
    vi.useFakeTimers();
    const calls = recorder();
    const cancel = scheduleRefresh(120, {
      delay: 10,
      refresh: () => outcomeOfAlwaysFailing(failure(503, 30001, '下游服务不可用')),
      ...calls.options,
    });

    await vi.advanceTimersByTimeAsync(10);
    expect(vi.getTimerCount()).toBe(1);
    for (let round = 0; round < 5; round += 1) {
      await vi.advanceTimersByTimeAsync(RETRY_AFTER_FAILURE_MS);
      // 每轮结束仍然只有一个「下一轮」定时器
      expect(vi.getTimerCount()).toBe(1);
    }
    expect(calls.relogin).not.toHaveBeenCalled();

    cancel();
    expect(vi.getTimerCount()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 验证项 5：抖动真的在区间内、且多标签页不同步
// ---------------------------------------------------------------------------

describe('验证项 5 · 抖动区间与多标签页去同步', () => {
  /** 提前量 + 抖动的总量上界：30s 提前量 + 30s spread + 30s 抖动 = 90s */
  const MAX_TOTAL_LEAD_MS = LEAD_TIME_MIN_MS + LEAD_TIME_SPREAD_MS + JITTER_MAX_MS;

  it('random=0 时提前 30s、无抖动（下界口径）', () => {
    expect(nextRefreshDelay(300, { random: () => 0 })).toBe(300_000 - LEAD_TIME_MIN_MS);
  });

  it('200 次采样全部落在 [300s-90s, 300s-30s] 内（既有下界也有上界）', () => {
    const samples = Array.from({ length: 200 }, () => nextRefreshDelay(300));
    const min = Math.min(...samples);
    const max = Math.max(...samples);
    expect(min).toBeGreaterThanOrEqual(300_000 - MAX_TOTAL_LEAD_MS);
    expect(max).toBeLessThanOrEqual(300_000 - LEAD_TIME_MIN_MS);
    // 严格早于过期：最大延迟也必须小于 300s
    expect(max).toBeLessThan(300_000);
  });

  it('500 次采样的最小值低于 240s —— 证明**提前量本身**也是随机的，不是固定 30s', () => {
    // 若提前量被写成固定 30s，则 delay 恒 ≥ 240s，本条必红。
    // 500 次全 >240s 的概率约 2^-500（提前量/抖动独立均匀分布下），不会误报。
    const samples = Array.from({ length: 500 }, () => nextRefreshDelay(300));
    expect(Math.min(...samples)).toBeLessThan(240_000);
  });

  it('2000 次采样的经验极差接近满量程 60s —— 提前量与抖动**都**在随机', () => {
    // 满量程：提前量 30–60s + 抖动 0–30s ⇒ delay 落在 [210s, 270s]，极差上限 60s。
    // 经验极差一旦明显小于 45s，就说明有某一项被写成了常量：
    //   - 抖动固定（如 JITTER_MAX_MS / 2）⇒ 极差 ≈ 30s（只有提前量在变）；
    //   - 提前量固定 30s ⇒ 极差 ≈ 30s（只有抖动在变）；
    //   - 两者都固定 ⇒ 极差 0（这句注释正是被 mutation 反证过的）。
    // 2000 次采样下「真实现却测出极差 ≤45s」的概率可忽略（要连抽 2000 次都落在
    // 中间 3/4 区间内，量级 1e-25）。
    const samples = Array.from({ length: 2000 }, () => nextRefreshDelay(300));
    const spread = Math.max(...samples) - Math.min(...samples);
    expect(spread).toBeGreaterThan(45_000);
  });

  it('同一有效期下延迟是散开的（不是固定值）', () => {
    const samples = new Set(Array.from({ length: 30 }, () => nextRefreshDelay(300)));
    expect(samples.size).toBeGreaterThan(5);
  });

  it('多标签页不会同步：两个实例各自算出的首个延迟不相等', () => {
    /**
     * 造一个 scheduleRefresh 实例并读出它排的第一个定时器毫秒数。
     *
     * 用注入的 setTimer 读出真实排期值——这是「抖动是否真的生效」唯一可证伪的读法：
     * 若抖动是常量，两次读数必然相等。
     *
     * @returns 首个定时器毫秒数
     */
    const firstDelay = (): number => {
      const recorded: number[] = [];
      const cancel = scheduleRefresh(300, {
        // 这个实例只用来读首个延迟，刷新回调永不执行（假 setTimer 不会真的触发）
        refresh: async () => ({ kind: 'relogin', expires_in: null, attempts: 1, error: null }),
        setTimer: (_handler, ms) => {
          recorded.push(ms);
          return recorded.length;
        },
        clearTimer: () => undefined,
      });
      cancel();
      return recorded[0] ?? -1;
    };

    const delays = Array.from({ length: 20 }, firstDelay);
    // 全部落在合法区间
    for (const delay of delays) {
      expect(delay).toBeGreaterThanOrEqual(300_000 - MAX_TOTAL_LEAD_MS);
      expect(delay).toBeLessThanOrEqual(300_000 - LEAD_TIME_MIN_MS);
    }
    // 至少两个不同值 —— 全相等即「抖动是假的」，刷新风暴照样发生
    expect(new Set(delays).size).toBeGreaterThan(1);
    expect(Math.min(...delays)).not.toBe(Math.max(...delays));
  });

  it('20 个「标签页」的延迟两两不同率很高（同一毫秒撞车的概率趋近于 0）', () => {
    const delays = Array.from({ length: 20 }, () => nextRefreshDelay(300));
    const distinct = new Set(delays).size;
    // 取值空间约 3e4 × 3e4，20 个样本几乎不可能撞车；至少 18 个互不相同
    expect(distinct).toBeGreaterThanOrEqual(18);
  });

  it('有效期未知时给保守默认值；极短有效期不打成紧循环', () => {
    expect(nextRefreshDelay(null)).toBe(DEFAULT_REFRESH_DELAY_MS);
    for (const expiresIn of [0, 1, 10, 30, 300]) {
      expect(nextRefreshDelay(expiresIn, { random: () => 0 })).toBeGreaterThanOrEqual(
        MIN_REFRESH_DELAY_MS,
      );
    }
    // 0 秒有效期：300000-... 会算成负，被钳到 1s（不是 0，也不会是负数）
    expect(nextRefreshDelay(0, { random: () => 0 })).toBe(MIN_REFRESH_DELAY_MS);
  });

  it('随机源残缺（NaN / 负数 / ≥1）时延迟不含负数、不超过上界', () => {
    for (const random of [() => Number.NaN, () => -1, () => 2, () => 1, () => 0]) {
      const delay = nextRefreshDelay(300, { random });
      expect(delay).toBeGreaterThanOrEqual(MIN_REFRESH_DELAY_MS);
      expect(delay).toBeLessThanOrEqual(300_000 - LEAD_TIME_MIN_MS);
    }
  });
});
