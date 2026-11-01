/**
 * 前端补测第十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：可重试请求 Hook 的失败计数、30 秒滑动窗口淘汰与降级分支。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderHook } from '@/test/render-hook';
import {
  RETRY_AGGREGATE_HINT,
  RETRY_AGGREGATE_THRESHOLD,
  RETRY_WINDOW_MS,
  useRetryableRequest,
} from './use-retryable-request';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useRetryableRequest · 计数与阈值', () => {
  it('retry 逐次累加失败计数', () => {
    const { result, unmount } = renderHook(() => useRetryableRequest(() => undefined));
    act(() => result.current.retry());
    expect(result.current.attempts).toBe(1);
    act(() => result.current.retry());
    expect(result.current.attempts).toBe(2);
    unmount();
  });

  it('达到阈值（默认 3）后 exhausted', () => {
    const { result, unmount } = renderHook(() => useRetryableRequest(() => undefined));
    expect(result.current.exhausted).toBe(false);
    act(() => result.current.retry());
    act(() => result.current.retry());
    expect(result.current.exhausted).toBe(false);
    act(() => result.current.retry());
    expect(result.current.exhausted).toBe(true);
    unmount();
  });

  it('自定义阈值生效（下限 1）', () => {
    const { result, unmount } = renderHook(() =>
      useRetryableRequest(() => undefined, { threshold: 1 }),
    );
    act(() => result.current.retry());
    expect(result.current.attempts).toBe(1);
    expect(result.current.exhausted).toBe(true);
    unmount();
  });

  it('reset 清零计数与聚合标记', () => {
    const { result, unmount } = renderHook(() => useRetryableRequest(() => undefined));
    act(() => result.current.retry());
    act(() => result.current.retry());
    act(() => result.current.retry());
    expect(result.current.exhausted).toBe(true);

    act(() => result.current.reset());
    expect(result.current.attempts).toBe(0);
    expect(result.current.exhausted).toBe(false);
    unmount();
  });

  it('聚合提示文案为业务语言（不解释技术原因）', () => {
    expect(RETRY_AGGREGATE_HINT).toBe('多次尝试未成功，请稍后再试或联系管理员');
    expect(RETRY_AGGREGATE_THRESHOLD).toBe(3);
    expect(RETRY_WINDOW_MS).toBe(30_000);
  });
});

describe('useRetryableRequest · 滑动窗口', () => {
  it('窗口内的失败持续累加', () => {
    const { result, unmount } = renderHook(() => useRetryableRequest(() => undefined));
    act(() => result.current.retry());
    act(() => {
      vi.setSystemTime(29_000);
    });
    act(() => result.current.retry());
    expect(result.current.attempts).toBe(2);
    unmount();
  });

  it('窗口外的旧失败被淘汰（不误判为「连续 3 次」）', () => {
    const { result, unmount } = renderHook(() => useRetryableRequest(() => undefined));
    act(() => result.current.retry());
    act(() => {
      vi.setSystemTime(10_000);
    });
    act(() => result.current.retry());
    expect(result.current.attempts).toBe(2);

    // 越过窗口后重试：只剩本次失败
    act(() => {
      vi.setSystemTime(10_000 + RETRY_WINDOW_MS + 1);
    });
    act(() => result.current.retry());
    expect(result.current.attempts).toBe(1);
    expect(result.current.exhausted).toBe(false);
    unmount();
  });

  it('自定义窗口生效（最小 1 秒）', () => {
    const { result, unmount } = renderHook(() =>
      useRetryableRequest(() => undefined, { windowMs: 5_000 }),
    );
    act(() => result.current.retry());
    act(() => {
      vi.setSystemTime(6_000);
    });
    act(() => result.current.retry());
    expect(result.current.attempts).toBe(1);
    unmount();
  });
});

describe('useRetryableRequest · 降级分支', () => {
  it('run 同步抛错不向外抛出', () => {
    const { result, unmount } = renderHook(() =>
      useRetryableRequest(() => {
        throw new Error('同步失败');
      }),
    );
    expect(() => {
      act(() => result.current.retry());
    }).not.toThrow();
    expect(result.current.attempts).toBe(1);
    unmount();
  });

  it('run 返回被拒 Promise 时不产生未捕获 rejection', async () => {
    const { result, unmount } = renderHook(() =>
      useRetryableRequest(() => Promise.reject(new Error('异步失败'))),
    );
    await act(async () => {
      result.current.retry();
    });
    expect(result.current.attempts).toBe(1);
    unmount();
  });

  it('run 成功时也计数（计数只跟踪重试动作本身）', async () => {
    const run = vi.fn(async () => undefined);
    const { result, unmount } = renderHook(() => useRetryableRequest(run));
    await act(async () => {
      await result.current.retry();
    });
    expect(run).toHaveBeenCalledTimes(1);
    expect(result.current.attempts).toBe(1);
    unmount();
  });
});
