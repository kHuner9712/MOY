/**
 * 跨标签单飞与 20002 误判回归（审计 2026-11-02 M1）。
 *
 * 守住三条（改 `auth-refresh.ts` / `/sso/refresh` 前先读）：
 * 1. **多标签页同时刷新只发一次请求、后到者拿到同一结果**（跨标签单飞 +
 *    结算广播复用）；同标签并发同样只发一次（本标签单飞）；
 * 2. **20002 误踢回归**：服务端标了 `data.retryable = true` 的 20002（被新轮换
 *    取代的旧令牌迟到）**绝不**判「会话已死」——判死就是把多标签用户踢下线；
 * 3. **真死语义不变**：未标注重试的 20002/401 仍然判 `dead`（跳登录页）。
 *
 * 跨标签用两个**独立模块实例**模拟两个标签页（`vi.resetModules` 重载），
 * 共享同一个可注入的 `RefreshFlightBus` 替身（互斥锁队列 + 结算广播 + 时钟）。
 */

import { describe, expect, it, vi } from 'vitest';

import {
  classifyRefreshFailure,
  refreshShared,
  refreshWithRetry,
  REFRESH_SETTLE_WINDOW_MS,
  type RefreshFlightBus,
  type RefreshOutcome,
  type RefreshSettleMarker,
} from './auth-refresh';
import { SessionRequestError, refreshSession, type SessionGrant } from './session-client';

// ---------------------------------------------------------------------------
// 替身
// ---------------------------------------------------------------------------

/** 互斥锁队列 + 结算广播 + 可推进时钟的 `RefreshFlightBus` 替身 */
function makeFakeBus(seed: RefreshSettleMarker | null = null): {
  bus: RefreshFlightBus;
  clock: { now: number };
  announces: RefreshSettleMarker[];
} {
  const clock = { now: 1_000_000 };
  const listeners = new Set<(marker: RefreshSettleMarker) => void>();
  const announces: RefreshSettleMarker[] = [];
  let chain: Promise<unknown> = Promise.resolve();
  const bus: RefreshFlightBus = {
    requestLock<T>(callback: () => Promise<T>): Promise<T> {
      // 队列化 = Web Locks 的互斥语义：同一时刻只有一个临界区在执行
      const run = chain.then(() => callback());
      chain = run.then(
        () => undefined,
        () => undefined,
      );
      return run;
    },
    onSettled(listener: (marker: RefreshSettleMarker) => void): () => void {
      listeners.add(listener);
      if (seed !== null) {
        // 预置的「他标签页已结算」消息：模拟订阅前刚广播到的场景
        listener(seed);
      }
      return () => {
        listeners.delete(listener);
      };
    },
    announceSettled(marker: RefreshSettleMarker): void {
      announces.push(marker);
      for (const listener of listeners) {
        listener(marker);
      }
    },
    now(): number {
      return clock.now;
    },
  };
  return { bus, clock, announces };
}

/** 计数用的刷新替身（成功返回新有效期） */
function makeGrantRefresh(expiresIn: number = 300): {
  refresh: () => Promise<SessionGrant>;
  calls: () => number;
} {
  let calls = 0;
  return {
    refresh: async () => {
      calls += 1;
      // 微任务级延迟：让并发调用真的重叠，而不是串行擦肩
      await new Promise((resolve) => setTimeout(resolve, 5));
      return { ok: true, expires_in: expiresIn };
    },
    calls: () => calls,
  };
}

/** 载入一份独立的 `auth-refresh` 模块（= 模拟一个独立标签页） */
async function loadAuthRefresh(): Promise<typeof import('./auth-refresh')> {
  vi.resetModules();
  return await import('./auth-refresh');
}

// ---------------------------------------------------------------------------
// 1. 单飞：只发一次请求、后到者拿到同一结果
// ---------------------------------------------------------------------------

describe('M1 · 跨标签单飞', () => {
  it('两个标签页同时刷新：只发一次请求，后到者拿到同一结果', async () => {
    const { bus, announces } = makeFakeBus();
    const grant = makeGrantRefresh(300);
    const tabA = await loadAuthRefresh();
    const tabB = await loadAuthRefresh();

    const [outcomeA, outcomeB] = await Promise.all([
      tabA.refreshShared({ refresh: grant.refresh, bus }),
      tabB.refreshShared({ refresh: grant.refresh, bus }),
    ]);

    // 只发一次请求（后到者靠结算广播复用，零请求）
    expect(grant.calls()).toBe(1);
    // 同一结果：kind / expires_in 逐字段一致
    expect({ kind: outcomeB.kind, expires_in: outcomeB.expires_in }).toEqual({
      kind: outcomeA.kind,
      expires_in: outcomeA.expires_in,
    });
    expect(outcomeA.kind).toBe('refreshed');
    // 后到者 attempts = 0 = 未发请求；赢家 = 1
    expect([outcomeA.attempts, outcomeB.attempts].sort()).toEqual([0, 1]);
    // 结算广播只发一次（赢家）
    expect(announces).toHaveLength(1);
  });

  it('同标签并发调用（定时刷新 × 401 恢复）：本标签单飞，只发一次', async () => {
    const { bus } = makeFakeBus();
    const grant = makeGrantRefresh(240);
    const tab = await loadAuthRefresh();

    const [first, second] = await Promise.all([
      tab.refreshShared({ refresh: grant.refresh, bus }),
      tab.refreshShared({ refresh: grant.refresh, bus }),
    ]);

    expect(grant.calls()).toBe(1);
    expect(first).toBe(second); // 同一份在途任务，连对象都是同一个
  });

  it('收到「他标签页刚结算」消息：复用结果、零请求（后到者形态）', async () => {
    const settled: RefreshSettleMarker = { ts: 1_000_000 - 2_000, expires_in: 280 };
    const { bus } = makeFakeBus(settled);
    const grant = makeGrantRefresh(300);
    const tab = await loadAuthRefresh();

    const outcome = await tab.refreshShared({ refresh: grant.refresh, bus });

    expect(grant.calls()).toBe(0);
    expect(outcome).toEqual({ kind: 'refreshed', expires_in: 280, attempts: 0, error: null });
  });

  it('结算消息落在复用窗口之外：照常真刷新（复用不是永久的）', async () => {
    const stale: RefreshSettleMarker = {
      ts: 1_000_000 - REFRESH_SETTLE_WINDOW_MS - 1,
      expires_in: 280,
    };
    const { bus } = makeFakeBus(stale);
    const grant = makeGrantRefresh(300);
    const tab = await loadAuthRefresh();

    const outcome = await tab.refreshShared({ refresh: grant.refresh, bus });

    expect(grant.calls()).toBe(1);
    expect(outcome).toMatchObject({ kind: 'refreshed', expires_in: 300 });
  });
});

// ---------------------------------------------------------------------------
// 2. 20002 误踢回归 + 3. 真死语义不变
// ---------------------------------------------------------------------------

describe('M1 · 20002 误判回归', () => {
  it('服务端标注 retryable 的 20002：归「可重试」，绝不判死（不踢用户）', () => {
    const error = new SessionRequestError('刷新令牌已失效', 401, 20002, true);
    expect(classifyRefreshFailure(error)).toBe('retry');
  });

  it('retryable 的 20002 走完整重试循环后仍是 transient（不触发跳登录页）', async () => {
    const error = new SessionRequestError('刷新令牌已失效', 401, 20002, true);
    const outcome: RefreshOutcome = await refreshWithRetry({
      refresh: async () => {
        throw error;
      },
      maxRetries: 1,
      sleep: async () => undefined,
    });
    expect(outcome.kind).toBe('transient');
    expect(outcome.attempts).toBe(2);
  });

  it('真死语义不变：未标注重试的 20002 仍判 dead（QA-02 契约）', async () => {
    const error = new SessionRequestError('刷新令牌已失效', 401, 20002, false);
    expect(classifyRefreshFailure(error)).toBe('dead');
    const outcome = await refreshWithRetry({
      refresh: async () => {
        throw error;
      },
      maxRetries: 2,
      sleep: async () => undefined,
    });
    expect(outcome.kind).toBe('relogin');
    expect(outcome.attempts).toBe(1); // 判死后不再白打下游
  });

  it('失败（含 retryable）不广播结算：下一轮其他标签页仍会真刷新', async () => {
    const { bus, announces } = makeFakeBus();
    const tab = await loadAuthRefresh();
    const error = new SessionRequestError('刷新令牌已失效', 401, 20002, true);

    const outcome = await tab.refreshShared({
      refresh: async () => {
        throw error;
      },
      bus,
      maxRetries: 0,
      sleep: async () => undefined,
    });

    expect(outcome.kind).toBe('transient');
    expect(announces).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 默认共享通道（生产实现：Web Locks + BroadcastChannel）与 retryable 解析
// ---------------------------------------------------------------------------

/** 进程内的 BroadcastChannel 假实现（模拟同源跨标签广播） */
class FakeBroadcastChannel {
  /** 通道名 → 订阅者集合（同名 = 同源跨标签） */
  private static readonly registry = new Map<string, Set<FakeBroadcastChannel>>();
  onmessage: ((event: { data: unknown }) => void) | null = null;

  constructor(readonly name: string) {
    const peers = FakeBroadcastChannel.registry.get(name) ?? new Set();
    peers.add(this);
    FakeBroadcastChannel.registry.set(name, peers);
  }

  /** 向同名的**其他**订阅者投递（浏览器语义：不投给自己） */
  postMessage(data: unknown): void {
    for (const peer of FakeBroadcastChannel.registry.get(this.name) ?? []) {
      if (peer !== this) {
        peer.onmessage?.({ data });
      }
    }
  }

  close(): void {
    FakeBroadcastChannel.registry.get(this.name)?.delete(this);
  }
}

/** 串行化的 Web Locks 假实现（互斥语义） */
function makeFakeLocks(): {
  request: (name: string, cb: () => Promise<unknown>) => Promise<unknown>;
} {
  let chain: Promise<unknown> = Promise.resolve();
  return {
    request(_name: string, cb: () => Promise<unknown>): Promise<unknown> {
      const run = chain.then(() => cb());
      chain = run.then(
        () => undefined,
        () => undefined,
      );
      return run;
    },
  };
}

describe('M1 · 默认共享通道（生产路径）', () => {
  it('Web Locks + BroadcastChannel 默认实现：跨标签同样只发一次、复用结果', async () => {
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel);
    vi.stubGlobal('navigator', { locks: makeFakeLocks() });
    try {
      const grant = makeGrantRefresh(300);
      const tabA = await loadAuthRefresh();
      const tabB = await loadAuthRefresh();

      const [outcomeA, outcomeB] = await Promise.all([
        tabA.refreshShared({ refresh: grant.refresh }),
        tabB.refreshShared({ refresh: grant.refresh }),
      ]);

      expect(grant.calls()).toBe(1);
      expect(outcomeA.kind).toBe('refreshed');
      expect({ kind: outcomeB.kind, expires_in: outcomeB.expires_in }).toEqual({
        kind: outcomeA.kind,
        expires_in: outcomeA.expires_in,
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('通道投递的结算消息缺字段/是垃圾：忽略它，照常真刷新（不复用脏数据）', async () => {
    class GarbageChannel extends FakeBroadcastChannel {
      constructor(name: string) {
        super(name);
        // 模拟迟到投递的脏消息：ts 缺失，必须被忽略
        queueMicrotask(() => {
          this.onmessage?.({ data: 'garbage-not-an-object' });
        });
      }
    }
    vi.stubGlobal('BroadcastChannel', GarbageChannel);
    vi.stubGlobal('navigator', { locks: makeFakeLocks() });
    try {
      const grant = makeGrantRefresh(300);
      const tab = await loadAuthRefresh();
      const outcome = await tab.refreshShared({ refresh: grant.refresh });
      expect(grant.calls()).toBe(1);
      expect(outcome).toMatchObject({ kind: 'refreshed', expires_in: 300 });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('无 Web Locks / 无 BroadcastChannel 的环境：降级为本标签单飞，仍能刷新', async () => {
    vi.stubGlobal('navigator', {});
    vi.stubGlobal('BroadcastChannel', undefined);
    try {
      const grant = makeGrantRefresh(300);
      const tab = await loadAuthRefresh();
      const outcome = await tab.refreshShared({ refresh: grant.refresh });
      expect(grant.calls()).toBe(1);
      expect(outcome).toMatchObject({ kind: 'refreshed', expires_in: 300 });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('M1 · SessionRequestError 的 retryable 解析', () => {
  it('服务端信封 data.retryable=true 被解析进错误对象（误踢判据的来源）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              code: 20002,
              message: '刷新令牌已失效',
              data: { retryable: true, reason: 'superseded' },
            }),
            {
              status: 401,
            },
          ),
      ),
    );
    try {
      const caught = await refreshSession().catch((error: unknown) => error);
      expect(caught).toBeInstanceOf(SessionRequestError);
      expect((caught as SessionRequestError).code).toBe(20002);
      expect((caught as SessionRequestError).retryable).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('信封 data 缺失或不含 retryable：retryable=false（真死语义）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ code: 20002, message: '刷新令牌已失效', data: null }), {
            status: 401,
          }),
      ),
    );
    try {
      const caught = await refreshSession().catch((error: unknown) => error);
      expect(caught).toBeInstanceOf(SessionRequestError);
      expect((caught as SessionRequestError).retryable).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
