import { describe, expect, it, vi } from 'vitest';

import { emitUnauthorized, onUnauthorized } from './session-events';

/**
 * 会话失效广播的契约：
 * 1. 订阅者收到通知；
 * 2. 取消订阅后不再收到；
 * 3. 单个监听器抛错不影响其它监听器，也不把异常抛回请求链路。
 *
 * 第 3 条是这层存在的理由：如果广播能把异常抛回 `api-client`，
 * 一次清理失败就会把原本的 401 覆盖成未知异常，排障时看不到真实原因。
 */
describe('session-events', () => {
  it('通知所有订阅者，并在取消订阅后停止通知', () => {
    const first = vi.fn();
    const second = vi.fn();
    const offFirst = onUnauthorized(first);
    const offSecond = onUnauthorized(second);

    emitUnauthorized();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);

    offFirst();
    emitUnauthorized();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(2);

    offSecond();
  });

  it('监听器抛错不阻断其它监听器', () => {
    const healthy = vi.fn();
    const offBroken = onUnauthorized(() => {
      throw new Error('listener failed');
    });
    const offHealthy = onUnauthorized(healthy);

    expect(() => emitUnauthorized()).not.toThrow();
    expect(healthy).toHaveBeenCalledTimes(1);

    offBroken();
    offHealthy();
  });

  it('重复注册同一个监听器只保留一份', () => {
    const listener = vi.fn();
    const offFirst = onUnauthorized(listener);
    const offSecond = onUnauthorized(listener);

    emitUnauthorized();
    expect(listener).toHaveBeenCalledTimes(1);

    offFirst();
    offSecond();
  });
});
