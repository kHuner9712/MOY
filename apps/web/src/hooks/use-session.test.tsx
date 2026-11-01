/**
 * 前端补测第三十四批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：useSession 兼容壳的映射合同 ——
 * 阶段→loading、user/errorMessage 直通、reload 只转发刷新、logout 直通。
 * 会话门本体已由第三十二批覆盖，这里把上下文换成最小替身。
 */

import { describe, expect, it, vi } from 'vitest';

import { act, renderHook } from '@/test/render-hook';

import { useSession } from './use-session';

const mocks = vi.hoisted(() => ({
  context: {
    phase: 'restoring' as string,
    user: null as unknown,
    errorMessage: '',
    refreshProfile: vi.fn(),
    logout: vi.fn(),
  },
}));

vi.mock('@/components/layout/session-gate', () => ({
  useSessionContext: () => mocks.context,
}));

describe('useSession 兼容壳', () => {
  it('restoring 阶段映射为 loading，其余阶段不加载', () => {
    mocks.context.phase = 'restoring';
    const first = renderHook(() => useSession());
    expect(first.result.current.loading).toBe(true);
    first.unmount();

    mocks.context.phase = 'ready';
    const second = renderHook(() => useSession());
    expect(second.result.current.loading).toBe(false);
    second.unmount();
  });

  it('用户与错误文案直通上下文', () => {
    mocks.context.phase = 'ready';
    mocks.context.user = { display_name: '陈麦' };
    mocks.context.errorMessage = '';
    const ok = renderHook(() => useSession());
    expect(ok.result.current.user?.display_name).toBe('陈麦');
    expect(ok.result.current.errorMessage).toBe('');
    ok.unmount();

    mocks.context.phase = 'error';
    mocks.context.user = null;
    mocks.context.errorMessage = '资料暂时取不回来';
    const failed = renderHook(() => useSession());
    expect(failed.result.current.user).toBeNull();
    expect(failed.result.current.errorMessage).toBe('资料暂时取不回来');
    failed.unmount();
  });

  it('reload 转发刷新且回传 Promise（旧签名不变）', async () => {
    const { result, unmount } = renderHook(() => useSession());
    let done = false;
    act(() => {
      void result.current.reload().then(() => {
        done = true;
      });
    });
    await act(async () => {});
    expect(mocks.context.refreshProfile).toHaveBeenCalled();
    expect(done).toBe(true);
    unmount();
  });

  it('logout 直通上下文', () => {
    const { result, unmount } = renderHook(() => useSession());
    act(() => {
      result.current.logout();
    });
    expect(mocks.context.logout).toHaveBeenCalled();
    unmount();
  });
});
