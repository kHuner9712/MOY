/**
 * 会话门（F-01）状态机测试 —— P0-5 路线 A 版本。
 *
 * 覆盖：三态判据的四组各自走向、刷新四分类在界面上的落点、五态渲染映射
 * （骨架不闪「未登录」文案）、两条失效入口收敛、error 态「不清会话」、
 * 跳登录只跳一次、主动刷新排期、logout 清理、门外误用抛错。
 *
 * 关键点（改这条先想清后果）：**临时故障不登出**。`fetchSession` 失败或刷新
 * 用尽重试时只能进 error 态给「重试」，一旦改成跳登录页，一次网关抖动就会把
 * 全站用户踢下线（服务端 QA-02 的客户端等价物）。
 *
 * 假定时器/act 口径：本门的状态翻转至少经过两层 await（探针 → 刷新 → setState），
 * `act` 内的轮询观察不到压到窗口退出才落盘的提交，因此统一用 `eventually()`
 * ——「act 内逐拍放行 + 窗口外断言」，与既有页面级测试的写法一致。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { buildLoginHref } from '@/lib/redirect';
import { act, renderComponent } from '@/test/render-hook';

import { SessionGate, SESSION_UNAVAILABLE_MESSAGE, useSessionContext } from './session-gate';

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  setTenantId: vi.fn(),
  profile: vi.fn(),
  purgeLegacySessionStorage: vi.fn(),
  fetchSession: vi.fn(),
  logoutSession: vi.fn(),
  refreshWithRetry: vi.fn(),
  scheduleRefresh: vi.fn(),
  onUnauthorized: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mocks.replace }),
  usePathname: () => '/team',
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  setTenantId: mocks.setTenantId,
  identityApi: { profile: mocks.profile },
}));

vi.mock('@/lib/session-client', () => ({
  fetchSession: mocks.fetchSession,
  // 真实实现返回 Promise；替身也必须返回 Promise，否则组件里的 `.catch` 会炸
  logoutSession: (...args: unknown[]) => Promise.resolve(mocks.logoutSession(...args)),
}));

vi.mock('@/lib/session-store', () => ({
  purgeLegacySessionStorage: mocks.purgeLegacySessionStorage,
}));

vi.mock('@/lib/session-events', () => ({ onUnauthorized: mocks.onUnauthorized }));

// 部分 mock：`decideSession` 用真实实现（三态判据本身就是被测对象）
vi.mock('@/lib/auth-refresh', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth-refresh')>()),
  refreshWithRetry: mocks.refreshWithRetry,
  scheduleRefresh: mocks.scheduleRefresh,
}));

const PROFILE = {
  user_id: 'u_1',
  display_name: '陈麦',
  email: 'chenmai@example.com',
  tenant: { tenant_id: 't_1', name: '华农科技', plan: 'professional' },
  organizations: [],
};

/** 四元组构造器：与 `/sso/session` 的响应字段逐字对应 */
function sessionState(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    authenticated: false,
    expires_in: null,
    refreshable: false,
    expired: false,
    ...overrides,
  };
}

/** 刷新结局构造器 */
function outcome(kind: 'refreshed' | 'relogin' | 'transient', expiresIn: number | null = null) {
  return { kind, expires_in: expiresIn, attempts: 1, error: null };
}

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

/** 放行一拍微任务/宏任务（必须在 act 内，否则 React 的更新会漏在外面） */
async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/**
 * 逐拍放行后断言，直到断言通过（最多 60 拍）。
 *
 * @param check - 断言函数（抛出即视为未通过）
 */
async function eventually(check: () => void): Promise<void> {
  let lastError: unknown = null;
  for (let tick = 0; tick < 60; tick += 1) {
    await flush();
    try {
      check();
      return;
    } catch (error) {
      lastError = error;
    }
  }
  if (lastError) throw lastError;
  check();
}

/**
 * 等某段文案出现在 DOM 里。
 *
 * @param root - 断言范围
 * @param text - 期望文案
 */
async function waitForText(root: ParentNode, text: string): Promise<void> {
  await eventually(() => {
    expect(root.textContent).toContain(text);
  });
}

/** 点某个文案的按钮 */
function clickButton(root: ParentNode, text: string): void {
  click(Array.from(root.querySelectorAll('button')).find((item) => item.textContent === text));
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('会话门 · 三态判据的落点', () => {
  it('有效会话：直接用，不问刷新，资料就绪后才渲染子页面', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();

    await waitForText(container, '阶段ready');
    expect(container.textContent).toContain('用户陈麦');
    // 租户头由 profile 教，且在数据落缓存前写好
    expect(mocks.setTenantId).toHaveBeenCalledWith('t_1');
    // 有效会话不该触发刷新
    expect(mocks.refreshWithRetry).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
    unmount();
  });

  it('过期但可刷新：先刷新再取资料（**不是**跳登录页）', async () => {
    mocks.fetchSession.mockResolvedValue(
      sessionState({ authenticated: false, refreshable: true, expired: true }),
    );
    mocks.refreshWithRetry.mockResolvedValue(outcome('refreshed', 300));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();

    await waitForText(container, '阶段ready');
    expect(mocks.refreshWithRetry).toHaveBeenCalledTimes(1);
    expect(mocks.replace).not.toHaveBeenCalled();
    unmount();
  });

  it('过期且刷新令牌已死：跳登录页', async () => {
    mocks.fetchSession.mockResolvedValue(
      sessionState({ authenticated: false, refreshable: true, expired: true }),
    );
    mocks.refreshWithRetry.mockResolvedValue(outcome('relogin'));
    const { container, unmount } = renderGate();

    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalledWith(buildLoginHref('/team', ''));
    });
    // 骨架里不得出现「未登录 / 请登录」这类内部状态词
    expect(container.textContent).not.toContain('未登录');
    expect(container.textContent).not.toContain('请登录');
    expect(mocks.profile).not.toHaveBeenCalled();
    unmount();
  });

  it('从未登录：跳登录页且不发资料请求', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState());
    const { unmount } = renderGate();

    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalledWith(buildLoginHref('/team', ''));
    });
    expect(mocks.refreshWithRetry).not.toHaveBeenCalled();
    expect(mocks.profile).not.toHaveBeenCalled();
    unmount();
  });
});

describe('会话门 · 临时故障不登出', () => {
  it('会话探针失败：进 error 态给重试，绝不跳登录页', async () => {
    mocks.fetchSession.mockRejectedValue(new Error('网关 503'));
    const { container, unmount } = renderGate();

    await waitForText(container, SESSION_UNAVAILABLE_MESSAGE);
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.profile).not.toHaveBeenCalled();
    unmount();
  });

  it('刷新重试用尽（transient）：进 error 态，不登出、不清租户', async () => {
    mocks.fetchSession.mockResolvedValue(
      sessionState({ authenticated: false, refreshable: true, expired: true }),
    );
    mocks.refreshWithRetry.mockResolvedValue(outcome('transient'));
    const { container, unmount } = renderGate();

    await waitForText(container, SESSION_UNAVAILABLE_MESSAGE);
    expect(mocks.replace).not.toHaveBeenCalled();
    unmount();
  });

  it('错误态点重试会重跑握手（探针恢复后即可就绪）', async () => {
    mocks.fetchSession.mockRejectedValueOnce(new Error('网关 503'));
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();

    await waitForText(container, SESSION_UNAVAILABLE_MESSAGE);
    clickButton(container, '重试');
    await waitForText(container, '阶段ready');
    expect(mocks.fetchSession).toHaveBeenCalledTimes(2);
    unmount();
  });
});

describe('会话门 · 运行期失效', () => {
  it('资料返回 20002 视为失效：清租户头并跳登录', async () => {
    const { BusinessError } = await import('@/lib/api-client');
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockRejectedValue(new BusinessError(20002, '登录状态已失效'));
    const { unmount } = renderGate();

    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalled();
    });
    expect(mocks.setTenantId).toHaveBeenCalledWith(null);
    unmount();
  });

  it('资料抖动进 error 态：可重试且不跳登录', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockRejectedValue(new Error('网络抖动'));
    const { container, unmount } = renderGate();

    await waitForText(container, '网络抖动');
    expect(mocks.replace).not.toHaveBeenCalled();

    mocks.profile.mockResolvedValue(PROFILE);
    clickButton(container, '重试');
    await waitForText(container, '阶段ready');
    unmount();
  });

  it('运行期失效广播收敛到过期处置（跳登录页）', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    const handler = mocks.onUnauthorized.mock.calls[0]?.[0] as () => void;
    act(() => {
      handler();
    });
    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalled();
    });
    expect(container.textContent).not.toContain('阶段ready');
    unmount();
  });

  it('跳登录只跳一次，重复渲染不刷掉用户手动返回', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState());
    const { unmount } = renderGate();
    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalledTimes(1);
    });
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    await flush();
    expect(mocks.replace).toHaveBeenCalledTimes(1);
    unmount();
  });
});

describe('会话门 · 主动刷新与登出', () => {
  it('就绪后按有效期排下一次刷新，卸载时取消', async () => {
    const cancel = vi.fn();
    mocks.scheduleRefresh.mockReturnValue(cancel);
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();

    await waitForText(container, '阶段ready');
    expect(mocks.scheduleRefresh).toHaveBeenCalledTimes(1);
    // 排期拿到的就是服务端给的有效期（提前量与抖动在 auth-refresh 内部算）
    expect(mocks.scheduleRefresh.mock.calls[0]?.[0]).toBe(240);

    unmount();
    expect(cancel).toHaveBeenCalled();
  });

  it('就绪前不排刷新（未登录时不打刷新端点）', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState());
    const { unmount } = renderGate();
    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalled();
    });
    expect(mocks.scheduleRefresh).not.toHaveBeenCalled();
    unmount();
  });

  it('排期回调：成功只更新有效期、临时故障保持会话、刷新令牌已死才跳登录', async () => {
    mocks.scheduleRefresh.mockReturnValue(vi.fn());
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    const options = mocks.scheduleRefresh.mock.calls[0]?.[1] as {
      onRefreshed: (expiresIn: number | null) => void;
      onRelogin: () => void;
      onUnavailable: () => void;
    };

    // 刷新成功：界面不变、不跳转
    act(() => {
      options.onRefreshed(300);
    });
    await flush();
    expect(container.textContent).toContain('阶段ready');
    expect(mocks.replace).not.toHaveBeenCalled();

    // 临时故障：保持会话（**不登出**，排期内部会在固定间隔后重试）
    act(() => {
      options.onUnavailable();
    });
    await flush();
    expect(container.textContent).toContain('阶段ready');
    expect(mocks.replace).not.toHaveBeenCalled();

    // 刷新令牌已死：这是唯一「必须重新登录」的结论
    act(() => {
      options.onRelogin();
    });
    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalled();
    });
    unmount();
  });

  it('退出登录：通知服务端 + 清理历史键 + 清租户头 + 回登录页', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    clickButton(container, '退出');

    await eventually(() => {
      expect(mocks.logoutSession).toHaveBeenCalledTimes(1);
    });
    expect(mocks.purgeLegacySessionStorage).toHaveBeenCalled();
    expect(mocks.setTenantId).toHaveBeenCalledWith(null);
    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalled();
    });
    unmount();
  });

  it('启动时清理一次历史令牌键', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState());
    const { unmount } = renderGate();
    await eventually(() => {
      expect(mocks.purgeLegacySessionStorage).toHaveBeenCalled();
    });
    unmount();
  });

  it('服务端登出失败也继续本地清理并回登录页（用户点了退出就得退出去）', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    mocks.logoutSession.mockRejectedValue(new Error('网关 503'));
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    clickButton(container, '退出');

    await eventually(() => {
      expect(mocks.purgeLegacySessionStorage).toHaveBeenCalled();
    });
    expect(mocks.setTenantId).toHaveBeenCalledWith(null);
    await eventually(() => {
      expect(mocks.replace).toHaveBeenCalled();
    });
    unmount();
  });
});

describe('会话门 · 使用约束', () => {
  it('刷新资料只重发这一个请求', async () => {
    mocks.fetchSession.mockResolvedValue(sessionState({ authenticated: true, expires_in: 240 }));
    mocks.profile.mockResolvedValue(PROFILE);
    const { container, unmount } = renderGate();
    await waitForText(container, '阶段ready');

    clickButton(container, '刷新资料');
    await eventually(() => {
      expect(mocks.profile).toHaveBeenCalledTimes(2);
    });
    unmount();
  });

  it('在会话门之外取上下文直接抛错', () => {
    expect(() => {
      renderComponent(<Probe />);
    }).toThrow('会话状态只能在主界面布局内使用');
  });

  it('子页面在就绪前不挂载（避免无身份请求）', async () => {
    mocks.fetchSession.mockReturnValue(new Promise(() => {}));
    const { container, unmount } = renderGate();
    await flush();
    expect(container.textContent).not.toContain('阶段');
    unmount();
  });
});
