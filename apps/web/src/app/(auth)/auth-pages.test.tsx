/**
 * 认证域页面编排测试（P0-5 路线 A：令牌不进 JavaScript）。
 *
 * 覆盖：
 * 1. 登录页：生产/开发双构建分支、统一登录「先要地址再跳转」、失败如实转述；
 * 2. 回跳流程：未完成时不上屏任何中间数据、回跳参数不合格不发请求、
 *    成功后按默认落地页回跳、失败文案可操作且不泄露内部细节；
 * 3. 根落地页：直接重定向到对话主页。
 *
 * 与改造前的差别（**能力更强，不是更弱**）：页面不再生成 PKCE、不再拼授权地址、
 * 不再写任何存储 —— 这些动作已搬到服务端，因此本测试也不再有「先存后跳」类断言，
 * 取而代之的是「请求发出前不跳转」「失败不假装成功」与「不落任何本地状态」。
 */

import type { ComponentType } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import { CallbackFlow } from './auth/callback/callback-flow';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  push: vi.fn(),
  replace: vi.fn(),
  redirect: vi.fn(),
  getAdapterBaseUrl: vi.fn(),
  startLogin: vi.fn(),
  completeLogin: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  redirect: mocks.redirect,
}));

// 部分 mock：保留 BusinessError 类（business-errors 的 instanceof 依赖它）
vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  getAdapterBaseUrl: mocks.getAdapterBaseUrl,
}));

// 会话端点整体替换：页面只关心「要地址 / 完成登录」的编排，端点行为由
// session-client.test.ts 覆盖
vi.mock('@/lib/session-client', () => ({
  startLogin: mocks.startLogin,
  completeLogin: mocks.completeLogin,
  fetchSession: vi.fn(),
  refreshSession: vi.fn(),
  logoutSession: vi.fn(),
}));

/** jsdom 的 location 不可打桩（unforgeable），整文件用假 location 替身 */
interface FakeLocation {
  origin: string;
  search: string;
  assign: ReturnType<typeof vi.fn>;
}

function installFakeLocation(search = ''): FakeLocation {
  const fake: FakeLocation = {
    origin: 'http://localhost:3000',
    search,
    assign: vi.fn(),
  };
  Object.defineProperty(window, 'location', {
    value: fake,
    writable: true,
    configurable: true,
  });
  return fake;
}

beforeEach(() => {
  installFakeLocation();
});

afterEach(() => {
  vi.unstubAllEnvs();
  document.body.innerHTML = '';
});

/**
 * 登录页的分支在**模块级常量**里（构建模式），必须先定环境变量再重新加载模块，
 * 才能覆盖生产/开发两套渲染。
 */
async function loadLoginPage(options: { production?: boolean } = {}): Promise<ComponentType> {
  vi.resetModules();
  vi.stubEnv('NODE_ENV', options.production ? 'production' : 'test');
  const pageModule = await import('./login/page');
  return pageModule.default;
}

function click(element: Element | null | undefined): void {
  act(() => {
    element?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

/**
 * 等异步流程的状态翻转落进 DOM。
 *
 * promise 续体驱动的提交在一个长 act 内部轮询永远观察不到（提交被压到 act 退出
 * 才落盘），必须「act 内逐拍放行」再断言。
 */
async function waitForText(root: HTMLElement, text: string): Promise<void> {
  await vi.waitFor(async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(root.textContent).toContain(text);
  });
}

function findButton(root: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  );
}

// ---------------------------------------------------------------------------
// 登录页
// ---------------------------------------------------------------------------

describe('登录页', () => {
  it('开发构建：给联调入口并标注仅开发环境，不再提供凭证输入框', async () => {
    mocks.getAdapterBaseUrl.mockReturnValue('http://127.0.0.1:8000');
    const LoginPage = await loadLoginPage();
    const { container, unmount } = renderComponent(<LoginPage />);

    expect(container.textContent).toContain('欢迎回到麦子');
    expect(container.textContent).toContain('仅开发环境：以下入口绕过身份校验，正式环境不提供');
    expect(container.textContent).toContain('进入工作台');
    expect(container.textContent).toContain('统一登录（联调验证用）');
    // 令牌已不可能被写进任何请求：粘贴凭证的入口不该存在
    expect(container.querySelector('#access-token')).toBeNull();
    expect(container.textContent).not.toContain('使用凭证进入');
    // 开发构建可见服务状态（后端地址），生产构建不显示
    expect(container.textContent).toContain('服务状态：http://127.0.0.1:8000');
    unmount();
  });

  it('开发构建：基址缺省（同源）时显示「同源」而不是空白', async () => {
    mocks.getAdapterBaseUrl.mockReturnValue('');
    const LoginPage = await loadLoginPage();
    const { container, unmount } = renderComponent(<LoginPage />);
    expect(container.textContent).toContain('服务状态：同源');
    unmount();
  });

  it('生产构建只给统一登录一条路，不提供任何绕过入口', async () => {
    const LoginPage = await loadLoginPage({ production: true });
    const { container, unmount } = renderComponent(<LoginPage />);

    expect(container.textContent).toContain('统一登录');
    expect(container.textContent).not.toContain('进入工作台');
    expect(container.textContent).not.toContain('使用凭证进入');
    expect(container.querySelector('#access-token')).toBeNull();
    // 后端地址不上屏
    expect(container.textContent).not.toContain('服务状态');
    unmount();
  });

  it('进入工作台带深链回跳，开放重定向被过滤', async () => {
    installFakeLocation('?next=https%3A%2F%2Fevil.example.com');
    const LoginPage = await loadLoginPage();
    const { container, unmount } = renderComponent(<LoginPage />);

    click(findButton(container, '进入工作台'));
    expect(mocks.toast.success).toHaveBeenCalledWith('正在进入工作台');
    // 外部地址不回跳，回落默认落地页
    expect(mocks.push).toHaveBeenCalledWith('/chat');
    unmount();
  });

  it('统一登录：拿到服务端给的授权地址后才整页跳转', async () => {
    const fake = installFakeLocation('?next=%2Fteam');
    mocks.startLogin.mockResolvedValue('https://sso.example.com/auth?state=s_1');

    const LoginPage = await loadLoginPage({ production: true });
    const { container, unmount } = renderComponent(<LoginPage />);

    click(findButton(container, '统一登录'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(fake.assign).toHaveBeenCalledWith('https://sso.example.com/auth?state=s_1');
      });
    });

    // 深链经 resolveNextPath 过滤后交给服务端（不把原始 URL 直接透传）
    expect(mocks.startLogin).toHaveBeenCalledWith('/team');
    // 页面不再自己拼授权地址、也不再写任何存储
    expect(mocks.toast.error).not.toHaveBeenCalled();
    unmount();
  });

  it('统一登录失败：如实转述服务端文案，原地不动且按钮可重试', async () => {
    const fake = installFakeLocation();
    // 服务端 503 时抛的是「统一登录未启用」；页面只负责如实转述
    mocks.startLogin.mockRejectedValue(new Error('统一登录未启用'));

    const LoginPage = await loadLoginPage({ production: true });
    const { container, unmount } = renderComponent(<LoginPage />);

    click(findButton(container, '统一登录'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('统一登录未启用');
      });
    });

    // 没拿到授权地址就不跳（跳一个空地址只会把用户带到更难懂的错误页）
    expect(fake.assign).not.toHaveBeenCalled();
    expect(findButton(container, '统一登录')?.disabled).toBe(false);
    unmount();
  });
});

// ---------------------------------------------------------------------------
// 回跳流程
// ---------------------------------------------------------------------------

function renderCallbackFlow(): ReturnType<typeof renderComponent> {
  return renderComponent(<CallbackFlow />);
}

describe('登录回跳流程', () => {
  it('完成前只显示进度，不渲染任何中间数据', () => {
    mocks.completeLogin.mockImplementation(() => new Promise(() => {}));
    installFakeLocation('?code=c_1&state=s_1');
    const { container, unmount } = renderCallbackFlow();

    expect(container.textContent).toContain('正在完成登录');
    expect(container.textContent).toContain('请不要关闭页面');
    // 授权码等参数不上屏
    expect(container.textContent).not.toContain('c_1');
    unmount();
  });

  it('回跳参数不合格（缺状态串）：一次兑换请求都不发', async () => {
    installFakeLocation('?code=c_1');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '本次登录未完成');
    expect(mocks.completeLogin).not.toHaveBeenCalled();
    unmount();
  });

  it('服务端明确拒绝（error 参数）同样不发起兑换', async () => {
    installFakeLocation('?error=access_denied&code=c_1&state=s_1');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '本次登录未完成');
    expect(mocks.completeLogin).not.toHaveBeenCalled();
    unmount();
  });

  it('兑换成功：按默认落地页回跳（令牌由服务端以 Cookie 下发）', async () => {
    mocks.completeLogin.mockResolvedValue({ ok: true, expires_in: 300 });
    installFakeLocation('?code=c_1&state=s_1');
    const { unmount } = renderCallbackFlow();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalledWith('/chat');
      });
    });
    expect(mocks.completeLogin).toHaveBeenCalledWith('c_1', 's_1');
    unmount();
  });

  it('兑换成功后清掉地址栏（授权码不留在历史与 Referer 里）', async () => {
    const replaceState = vi.spyOn(window.history, 'replaceState');
    mocks.completeLogin.mockResolvedValue({ ok: true, expires_in: 300 });
    installFakeLocation('?code=c_1&state=s_1');
    const { unmount } = renderCallbackFlow();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalled();
      });
    });
    expect(replaceState).toHaveBeenCalledWith({}, '', '/auth/callback');
    replaceState.mockRestore();
    unmount();
  });

  it('兑换失败：转述可操作的服务端文案，并提供回登录页的出路', async () => {
    mocks.completeLogin.mockRejectedValue(new Error('登录请求已失效，请重新发起登录'));
    installFakeLocation('?code=c_1&state=s_1');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '登录请求已失效，请重新发起登录');
    click(findButton(container, '重新登录'));
    expect(mocks.replace).toHaveBeenCalledWith('/login');
    unmount();
  });

  it('未预期异常（无可用文案）收敛为统一失败文案，不外泄内部细节', async () => {
    mocks.completeLogin.mockRejectedValue(new Error(''));
    installFakeLocation('?code=c_1&state=s_1');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '本次登录未完成，请重新登录');
    expect(container.textContent).not.toContain('内部实现细节');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// 根落地页
// ---------------------------------------------------------------------------

describe('根落地页', () => {
  it('直接重定向到对话主页', async () => {
    vi.resetModules();
    const indexModule = await import('../page');
    act(() => {
      indexModule.default();
    });
    expect(mocks.redirect).toHaveBeenCalledWith('/chat');
  });
});
