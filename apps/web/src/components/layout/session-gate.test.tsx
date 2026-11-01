/**
 * 前端补测第三十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：会话门（F-01）的状态机合同 ——
 * 挂载读本地会话三路（无会话/过期/有效）、以 profile 为就绪判据并先写租户头、
 * 五态渲染映射（骨架不闪「未登录」文案）、20002 与广播两条失效入口收敛、
 * error 态重试但不清会话、跳登录只跳一次、logout 清会话清缓存、
 * 门外调用 useSessionContext 抛错。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { buildLoginHref } from '@/lib/redirect';
import { act, renderComponent } from '@/test/render-hook';

import { SessionGate, useSessionContext } from './session-gate';

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  setAccessToken: vi.fn(),
  setTenantId: vi.fn(),
  profile: vi.fn(),
  loadSession: vi.fn(),
  clearSession: vi.fn(),
  isSessionExpired: vi.fn(),
  onUnauthorized: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mocks.replace }),
  usePathname: () => '/team',
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  setAccessToken: mocks.setAccessToken,
  setTenantId: mocks.setTenantId,
  identityApi: { profile: mocks.profile },
}));

vi.mock('@/lib/session-store', () => ({
  loadSession: mocks.loadSession,
  clearSession: mocks.clearSession,
  isSessionExpired: mocks.isSessionExpired,
}));

vi.mock('@/lib/session-events', () => ({ onUnauthorized: mocks.onUnauthorized }));

const PROFILE = {
  user_id: 'u_1',
  display_name: '陈麦',
  email: 'chenmai@example.com',
  tenant: { tenant_id: 't_1', name: '华农科技', plan: 'professional' },
  organizations: [],
};

const STORED = { access_token: 'tok_1', refresh_token: null, expires_at: null };

/** 探针：把会话上下文映射成可断言的 DOM */
function Probe(): React.JSX.Element {
  const session = useSessionContext();
  return (
    <div>
      阶段{session.phase}|用户{session.user?.display_name ?? '无'}|文案{session.errorMessage}
      <button type="button" onClick={() => session.refreshProfile()}>
        刷新资料
      </button>
      <button type="button" onClick={() => session.logout()}>
        退出
      </button>
    </div>
  );
}

function renderGate(): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderComponent(
    <QueryClientProvider client={client}>
      <SessionGate>
        <Probe />
      </SessionGate>
    </QueryClientProvider>,
  );
}

function click(element: Element | null | undefined): void {
  act(() => {
    element?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

async function waitForText(root: ParentNode, text: string): Promise<void> {
  for (let tick = 0; tick < 60; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    if (root.textContent?.includes(text)) return;
  }
  expect(root.textContent).toContain(text);
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('会话门', () => {
  it('没有本地会话：只跳登录不发请求，骨架不闪未登录文案', async () => {
    mocks.loadSession.mockReturnValue(null);
    const { container, unmount } = renderGate();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalledWith(buildLoginHref('/team', ''));
      });
    });
    expect(mocks.profile).not.toHaveBeenCalled();
    expect(mocks.setAccessToken).toHaveBeenCalledWith(null);
    // 跳转生效前的那一帧也是骨架，组件树里不得出现「未登录/请登录」
    expect(container.textContent).not.toContain('未登录');
    expect(container.textContent).not.toContain('请登录');
    unmount();
  });

  it('本地会话已过期：清会话清租户头再跳登录', async () => {
    mocks.loadSession.mockReturnValue(STORED);
    mocks.isSessionExpired.mockReturnValue(true);
    const { container, unmount } = renderGate();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalled();
      });
    });
    expect(mocks.clearSession).toHaveBeenCalled();
    expect(mocks.setTenantId).toHaveBeenCalledWith(null);
    // 过期令牌不再发请求
    expect(mocks.profile).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain('阶段');
    unmount();
  });

  it('有效会话：注入令牌取资料，就绪后先有租户头再落缓存', async () => {
    mocks.loadSession.mockReturnValue(STORED);
    mocks.isSessionExpired.mockReturnValue(false);
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();

    await waitForText(container, '阶段ready');
    expect(container.textContent).toContain('用户陈麦');
    expect(mocks.setAccessToken).toHaveBeenCalledWith('tok_1');
    // 租户头由 profile 教：请求前保持未知（null），就绪时写入真租户
    expect(mocks.setTenantId).toHaveBeenCalledWith('t_1');
    expect(mocks.replace).not.toHaveBeenCalled();
    unmount();
  });

  it('资料返回 20002 视为失效：清会话并跳登录', async () => {
    const { BusinessError } = await import('@/lib/api-client');
    mocks.loadSession.mockReturnValue(STORED);
    mocks.isSessionExpired.mockReturnValue(false);
    mocks.profile.mockRejectedValue(new BusinessError(20002, '登录状态已失效'));
    const { unmount } = renderGate();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalled();
      });
    });
    expect(mocks.clearSession).toHaveBeenCalled();
    unmount();
  });

  it('资料接口抖动进 error 态：可重试且不清会话', async () => {
    mocks.loadSession.mockReturnValue(STORED);
    mocks.isSessionExpired.mockReturnValue(false);
    mocks.profile.mockRejectedValue(new Error('网络抖动'));
    const { container, unmount } = renderGate();

    await waitForText(container, '网络抖动');
    // 网络抖动 ≠ 会话失效：不得清会话、不得跳登录
    expect(mocks.clearSession).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();

    mocks.profile.mockResolvedValue(PROFILE);
    click(
      Array.from(container.querySelectorAll('button')).find((item) => item.textContent === '重试'),
    );
    await waitForText(container, '阶段ready');
    unmount();
  });

  it('跳登录只跳一次，重复渲染不刷掉用户手动返回', async () => {
    mocks.loadSession.mockReturnValue(null);
    const { container, unmount } = renderGate();
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalledTimes(1);
      });
    });
    // 触发几次重渲染，仍然只 replace 一次
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    await act(async () => {});
    expect(mocks.replace).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('运行期失效广播收敛到过期处置', async () => {
    mocks.loadSession.mockReturnValue(STORED);
    mocks.isSessionExpired.mockReturnValue(false);
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    // 任意请求拿到 20002 → 传输层广播 → 门收敛
    const handler = mocks.onUnauthorized.mock.calls[0]?.[0] as () => void;
    act(() => {
      handler();
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalled();
      });
    });
    expect(container.textContent).not.toContain('阶段ready');
    unmount();
  });

  it('退出登录清会话清查询缓存并回登录页', async () => {
    mocks.loadSession.mockReturnValue(STORED);
    mocks.isSessionExpired.mockReturnValue(false);
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    click(
      Array.from(container.querySelectorAll('button')).find((item) => item.textContent === '退出'),
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.clearSession).toHaveBeenCalled();
      });
    });
    expect(mocks.setAccessToken).toHaveBeenCalledWith(null);
    expect(mocks.setTenantId).toHaveBeenCalledWith(null);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalled();
      });
    });
    unmount();
  });

  it('刷新资料只重发这一个请求', async () => {
    mocks.loadSession.mockReturnValue(STORED);
    mocks.isSessionExpired.mockReturnValue(false);
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    click(
      Array.from(container.querySelectorAll('button')).find(
        (item) => item.textContent === '刷新资料',
      ),
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.profile).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('在会话门之外取上下文直接抛错', () => {
    expect(() => {
      renderComponent(<Probe />);
    }).toThrow('会话状态只能在主界面布局内使用');
  });
});
