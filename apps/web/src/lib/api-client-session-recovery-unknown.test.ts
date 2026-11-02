/**
 * 会话恢复的**未知异常**兜底测试（P0-5 路线 A · 三态口径的最后一道口子）。
 *
 * 为什么值得单独一个文件：`api-client` 里的 `recoverSession()` 把刷新结局翻译成
 * 三态（`refreshed | dead | unavailable`），其中 `unavailable` 决定「不广播、
 * 不登出」。若将来有人把 `refreshWithRetry` 改成会抛异常的实现（或加了新的
 * 失败分支），异常会绕过三态翻译直接落到拦截层 —— 那种情况下**最安全的默认是
 * 当成临时故障**：宁可让用户看到「服务暂时不可用 + 重试」，也不能因为一次
 * 语义不明的异常把在线用户集体踢下线（错的代价不对称：重试一次 vs 重新登录全员）。
 *
 * 这条兜底在正常路径上不可达（`refreshWithRetry` 自身不抛，已由
 * `auth-refresh.test.ts` 的「任何结局都不抛异常」钉住），所以只能靠模块替身触发。
 * 本文件用 `vi.mock('./auth-refresh')` 把它变成**可达**，从而让这条契约有回归网。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  refreshWithRetry: vi.fn(),
}));

// 只替换刷新策略：三态翻译与拦截逻辑仍跑真实实现
vi.mock('./auth-refresh', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./auth-refresh')>()),
  refreshWithRetry: mocks.refreshWithRetry,
}));

type ApiClientModule = typeof import('./api-client');

/**
 * 重新加载模块（`recoveryInFlight` 是模块级状态，必须按用例隔离）。
 *
 * @returns api-client 与 session-events
 */
async function loadModules(): Promise<{
  api: ApiClientModule;
  events: typeof import('./session-events');
}> {
  const api = await import('./api-client');
  const events = await import('./session-events');
  return { api, events };
}

/** 网关了 401：无信封（真实网关的形态） */
function gateway401(): Response {
  return new Response('', { status: 401 });
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('recoverSession · 未知异常兜底', () => {
  it('刷新实现抛非预期异常时：按「暂时不可用」处理，**不广播**（不登出用户）', async () => {
    mocks.refreshWithRetry.mockRejectedValue(new Error('第三方库炸了'));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => gateway401()),
    );
    const { api, events } = await loadModules();
    let broadcast = 0;
    events.onUnauthorized(() => {
      broadcast += 1;
    });

    const error = await api.identityApi.profile().catch((caught: unknown) => caught);

    // 语义不明 → 保持会话：抛「暂时不可用」，绝不是 20002，也绝不广播
    expect(error).toBeInstanceOf(api.AdapterUnavailableError);
    expect(error).not.toBeInstanceOf(api.BusinessError);
    expect((error as Error).message).not.toContain('登录状态已失效');
    expect(broadcast).toBe(0);
    // 底层异常不外泄给用户
    expect((error as Error).message).not.toContain('第三方库炸了');
  });

  it('同一轮里的并发 401 共享这一次兜底结论（不重复刷新）', async () => {
    mocks.refreshWithRetry.mockRejectedValue(new Error('第三方库炸了'));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => gateway401()),
    );
    const { api } = await loadModules();

    const results = await Promise.allSettled([
      api.identityApi.profile(),
      api.pluginsApi.installed(),
      api.statsApi.tasks('7d'),
    ]);

    expect(results.every((item) => item.status === 'rejected')).toBe(true);
    // 三个 401 只触发一次刷新（去重口径不因兜底而失效）
    expect(mocks.refreshWithRetry).toHaveBeenCalledTimes(1);
  });
});
