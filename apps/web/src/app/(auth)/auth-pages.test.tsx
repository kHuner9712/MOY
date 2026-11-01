/**
 * 前端补测第二十六批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：认证域的登录闭环 ——
 * 1. 登录页：生产/开发双构建分支（生产只给统一登录入口、未配置只提示；
 *    开发给凭证入口并标注）、统一登录「先存后跳」与 PKCE 参数拼装、
 *    存储不可用/凭证为空的诚实降级；
 * 2. 回跳流程：未配置提示、失败态不碰存储、过期作废、成功落库注入并按
 *    深链回跳、落库失败明确失败、未预期异常收敛统一文案；
 * 3. 根落地页：直接重定向到对话主页。
 *
 * oidc/session-store 的纯函数口径已由既有单测覆盖，本批把它们整体换成
 * 最小替身，只测页面编排与安全顺序（先存后跳、失败不碰存储）。
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
  setAccessToken: vi.fn(),
  getAdapterBaseUrl: vi.fn(),
  createPkcePair: vi.fn(),
  createState: vi.fn(),
  buildAuthorizeUrl: vi.fn(),
  completeAuthorization: vi.fn(),
  savePending: vi.fn(),
  saveSession: vi.fn(),
  loadPending: vi.fn(),
  clearPending: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  redirect: mocks.redirect,
}));

// 部分 mock：保留 BusinessError 类（business-errors 的 instanceof 依赖它）
vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  setAccessToken: mocks.setAccessToken,
  getAdapterBaseUrl: mocks.getAdapterBaseUrl,
}));

vi.mock('@/lib/oidc', () => ({
  createPkcePair: mocks.createPkcePair,
  createState: mocks.createState,
  buildAuthorizeUrl: mocks.buildAuthorizeUrl,
  completeAuthorization: mocks.completeAuthorization,
}));

vi.mock('@/lib/session-store', () => ({
  savePending: mocks.savePending,
  saveSession: mocks.saveSession,
  loadPending: mocks.loadPending,
  clearPending: mocks.clearPending,
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
  delete process.env.NEXT_PUBLIC_OIDC_ISSUER;
  delete process.env.NEXT_PUBLIC_OIDC_CLIENT_ID;
  document.body.innerHTML = '';
});

/**
 * 登录页的分支在**模块级常量**里（构建模式、统一登录配置），
 * 必须先定环境变量再重新加载模块，才能覆盖生产/开发两套渲染。
 */
async function loadLoginPage(
  options: { production?: boolean; issuer?: string; clientId?: string } = {},
): Promise<ComponentType> {
  vi.resetModules();
  vi.stubEnv('NODE_ENV', options.production ? 'production' : 'test');
  process.env.NEXT_PUBLIC_OIDC_ISSUER = options.issuer ?? '';
  process.env.NEXT_PUBLIC_OIDC_CLIENT_ID = options.clientId ?? '';
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
 * 与补测第二十四批同款：promise 续体驱动的提交在一个长 act 内部轮询永远
 * 观察不到（提交被压到 act 退出才落盘），必须「act 内逐拍放行」再断言。
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

/** 在受控输入框里输入文本 */
function typeInto(container: ParentNode, selector: string, value: string): void {
  const field = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
  act(() => {
    if (field) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(field, value);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

const PENDING = {
  state: 'st_1',
  codeVerifier: 'ver_1',
  redirectUri: 'http://localhost:3000/auth/callback',
  createdAt: 1_700_000_000_000,
  returnTo: '/team',
};

const SESSION = {
  access_token: 'tok_1',
  refresh_token: null,
  expires_at: null,
};

// ---------------------------------------------------------------------------
// 登录页
// ---------------------------------------------------------------------------

describe('登录页', () => {
  it('开发构建给凭证入口并标注仅开发环境，未配置统一登录不给假入口', async () => {
    mocks.getAdapterBaseUrl.mockReturnValue('http://127.0.0.1:8000');
    const LoginPage = await loadLoginPage();
    const { container, unmount } = renderComponent(<LoginPage />);

    expect(container.textContent).toContain('欢迎回到麦子');
    expect(container.textContent).toContain(
      '仅开发环境：以下两种方式都绕过身份校验，正式环境不提供',
    );
    expect(container.textContent).toContain('进入工作台');
    expect(container.textContent).toContain('使用凭证进入');
    // 未配置统一登录：不渲染联调用的统一登录按钮
    expect(container.textContent).not.toContain('统一登录（联调验证用）');
    // 开发构建可见服务状态（后端地址），生产构建不显示
    expect(container.textContent).toContain('服务状态：http://127.0.0.1:8000');
    unmount();
  });

  it('开发构建配置了统一登录时多一个联调入口', async () => {
    const LoginPage = await loadLoginPage({
      issuer: 'https://sso.example.com',
      clientId: 'web',
    });
    const { container, unmount } = renderComponent(<LoginPage />);
    expect(container.textContent).toContain('统一登录（联调验证用）');
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

  it('凭证为空给提示，不写存储', async () => {
    const LoginPage = await loadLoginPage();
    const { container, unmount } = renderComponent(<LoginPage />);

    typeInto(container, '#access-token', '   ');
    click(findButton(container, '使用凭证进入'));
    expect(mocks.toast.error).toHaveBeenCalledWith('请输入访问凭证');
    expect(mocks.saveSession).not.toHaveBeenCalled();
    unmount();
  });

  it('手粘凭证写入会话并注入内存后进入', async () => {
    installFakeLocation('?next=%2Fteam');
    mocks.saveSession.mockReturnValue(true);
    const LoginPage = await loadLoginPage();
    const { container, unmount } = renderComponent(<LoginPage />);

    typeInto(container, '#access-token', '  tok_manual  ');
    click(findButton(container, '使用凭证进入'));

    // 手粘凭证没有过期信息：按「无过期信息」处理
    expect(mocks.saveSession).toHaveBeenCalledWith({
      access_token: 'tok_manual',
      refresh_token: null,
      expires_at: null,
    });
    expect(mocks.setAccessToken).toHaveBeenCalledWith('tok_manual');
    expect(mocks.toast.success).toHaveBeenCalledWith('已进入工作台');
    // 深链回到用户原本要去的页面
    expect(mocks.push).toHaveBeenCalledWith('/team');
    unmount();
  });

  it('存储不可用时明确失败，不给「看起来登录了」的假象', async () => {
    mocks.saveSession.mockReturnValue(false);
    const LoginPage = await loadLoginPage();
    const { container, unmount } = renderComponent(<LoginPage />);

    typeInto(container, '#access-token', 'tok_manual');
    click(findButton(container, '使用凭证进入'));

    expect(mocks.toast.error).toHaveBeenCalledWith('当前环境无法保存登录状态，请更换浏览器后重试');
    expect(mocks.setAccessToken).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
    unmount();
  });

  it('生产构建未配置统一登录只给提示，不提供任何进入入口', async () => {
    const LoginPage = await loadLoginPage({ production: true });
    const { container, unmount } = renderComponent(<LoginPage />);

    expect(container.textContent).toContain('统一登录尚未配置，请联系管理员');
    // 诚实降级：既不给「进入工作台」，也不给凭证输入框
    expect(container.textContent).not.toContain('进入工作台');
    expect(container.textContent).not.toContain('使用凭证进入');
    expect(container.querySelector('#access-token')).toBeNull();
    // 后端地址不上屏
    expect(container.textContent).not.toContain('服务状态');
    unmount();
  });

  it('生产构建先存一次性状态与 PKCE 再跳统一登录', async () => {
    const fake = installFakeLocation('?next=%2Fteam');
    mocks.createPkcePair.mockResolvedValue({ codeVerifier: 'ver_1', codeChallenge: 'ch_1' });
    mocks.createState.mockReturnValue('st_1');
    mocks.savePending.mockReturnValue(true);
    mocks.buildAuthorizeUrl.mockReturnValue('https://sso.example.com/authorize?x=1');

    const LoginPage = await loadLoginPage({
      production: true,
      issuer: 'https://sso.example.com',
      clientId: 'web',
    });
    const { container, unmount } = renderComponent(<LoginPage />);
    expect(container.textContent).toContain('统一登录');

    click(findButton(container, '统一登录'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(fake.assign).toHaveBeenCalledWith('https://sso.example.com/authorize?x=1');
      });
    });

    // 一次性状态与 PKCE 挑战随深链一起存好
    expect(mocks.savePending).toHaveBeenCalledWith({
      state: 'st_1',
      codeVerifier: 'ver_1',
      redirectUri: 'http://localhost:3000/auth/callback',
      createdAt: expect.any(Number),
      returnTo: '/team',
    });
    expect(mocks.buildAuthorizeUrl).toHaveBeenCalledWith({
      issuer: 'https://sso.example.com',
      clientId: 'web',
      redirectUri: 'http://localhost:3000/auth/callback',
      state: 'st_1',
      codeChallenge: 'ch_1',
      scope: 'openid profile email',
    });
    // 先存后跳：存不下就跳等于回跳页没有可比对状态
    expect(mocks.savePending.mock.invocationCallOrder[0] ?? 0).toBeLessThan(
      fake.assign.mock.invocationCallOrder[0] ?? 0,
    );
    unmount();
  });

  it('存不下一次性状态就不跳转并明确提示', async () => {
    const fake = installFakeLocation();
    mocks.createPkcePair.mockResolvedValue({ codeVerifier: 'ver_1', codeChallenge: 'ch_1' });
    mocks.createState.mockReturnValue('st_1');
    mocks.savePending.mockReturnValue(false);

    const LoginPage = await loadLoginPage({
      production: true,
      issuer: 'https://sso.example.com',
      clientId: 'web',
    });
    const { container, unmount } = renderComponent(<LoginPage />);

    click(findButton(container, '统一登录'));
    await act(async () => {});

    expect(fake.assign).not.toHaveBeenCalled();
    expect(mocks.toast.error).toHaveBeenCalledWith('当前环境无法保存登录状态，请更换浏览器后重试');
    // 按钮恢复可用，用户可换环境重试
    expect(findButton(container, '统一登录')?.disabled).toBe(false);
    unmount();
  });

  it('生成 PKCE 失败按业务文案提示并恢复按钮', async () => {
    installFakeLocation();
    mocks.createPkcePair.mockRejectedValue(new Error('浏览器不支持所需的加密能力'));

    const LoginPage = await loadLoginPage({
      production: true,
      issuer: 'https://sso.example.com',
      clientId: 'web',
    });
    const { container, unmount } = renderComponent(<LoginPage />);

    click(findButton(container, '统一登录'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('浏览器不支持所需的加密能力');
      });
    });
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
  beforeEach(() => {
    process.env.NEXT_PUBLIC_OIDC_ISSUER = 'https://sso.example.com';
    process.env.NEXT_PUBLIC_OIDC_CLIENT_ID = 'web';
  });

  it('完成前只显示进度，不渲染任何中间数据', () => {
    mocks.loadPending.mockReturnValue(PENDING);
    mocks.completeAuthorization.mockImplementation(() => new Promise(() => {}));
    installFakeLocation('?code=c_1&state=st_1');
    const { container, unmount } = renderCallbackFlow();

    expect(container.textContent).toContain('正在完成登录');
    expect(container.textContent).toContain('请不要关闭页面');
    // 授权码等参数不上屏
    expect(container.textContent).not.toContain('c_1');
    unmount();
  });

  it('未配置统一登录直接失败，不发起兑换', async () => {
    process.env.NEXT_PUBLIC_OIDC_ISSUER = '';
    const { container, unmount } = renderCallbackFlow();

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('本次登录未完成');
      });
    });
    expect(container.textContent).toContain('统一登录尚未配置，请联系管理员');
    expect(mocks.completeAuthorization).not.toHaveBeenCalled();
    unmount();
  });

  it('校验失败给统一文案且不碰存储，可回登录页重来', async () => {
    mocks.loadPending.mockReturnValue(PENDING);
    mocks.completeAuthorization.mockResolvedValue({
      kind: 'rejected',
      reason: 'mismatch',
      session: null,
      consumePending: false,
    });
    installFakeLocation('?code=fake&state=other');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '本次登录未完成，请重新登录');
    // 不可信跳转不得动用户存储
    expect(mocks.clearPending).not.toHaveBeenCalled();
    expect(mocks.saveSession).not.toHaveBeenCalled();
    expect(mocks.setAccessToken).not.toHaveBeenCalled();

    click(findButton(container, '重新登录'));
    expect(mocks.replace).toHaveBeenCalledWith('/login');
    unmount();
  });

  it('过期的待完成登录被作废，不留死状态', async () => {
    mocks.loadPending.mockReturnValue(PENDING);
    mocks.completeAuthorization.mockResolvedValue({
      kind: 'rejected',
      reason: 'expired',
      session: null,
      consumePending: true,
    });
    installFakeLocation('?code=c_1&state=st_1');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '本次登录未完成');
    expect(mocks.clearPending).toHaveBeenCalled();
    unmount();
  });

  it('兑换成功写会话、注入内存并按深链回跳', async () => {
    mocks.loadPending.mockReturnValue(PENDING);
    mocks.completeAuthorization.mockResolvedValue({
      kind: 'authorized',
      reason: null,
      session: SESSION,
      consumePending: true,
    });
    mocks.saveSession.mockReturnValue(true);
    installFakeLocation('?code=c_1&state=st_1');
    const { unmount } = renderCallbackFlow();

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.replace).toHaveBeenCalledWith('/team');
      });
    });
    expect(mocks.saveSession).toHaveBeenCalledWith(SESSION);
    expect(mocks.setAccessToken).toHaveBeenCalledWith('tok_1');

    // 兑换入参把「校验依据 + 清理钩子」配齐
    const options = mocks.completeAuthorization.mock.calls[0]?.[0] as {
      search: string;
      pending: unknown;
      issuer: string;
      clientId: string;
      onValidated: () => void;
    };
    expect(options.search).toBe('?code=c_1&state=st_1');
    expect(options.pending).toEqual(PENDING);
    expect(options.issuer).toBe('https://sso.example.com');
    expect(options.clientId).toBe('web');
    expect(typeof options.onValidated).toBe('function');
    unmount();
  });

  it('写不进存储就明确失败，不给「刷新即掉线」的假登录', async () => {
    mocks.loadPending.mockReturnValue(PENDING);
    mocks.completeAuthorization.mockResolvedValue({
      kind: 'authorized',
      reason: null,
      session: SESSION,
      consumePending: true,
    });
    mocks.saveSession.mockReturnValue(false);
    installFakeLocation('?code=c_1&state=st_1');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '当前环境无法保存登录状态，请更换浏览器或联系管理员');
    expect(mocks.setAccessToken).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
    unmount();
  });

  it('未预期异常收敛为统一失败文案', async () => {
    mocks.loadPending.mockReturnValue(PENDING);
    mocks.completeAuthorization.mockRejectedValue(new Error('内部实现细节不应外泄'));
    installFakeLocation('?code=c_1&state=st_1');
    const { container, unmount } = renderCallbackFlow();

    await waitForText(container, '本次登录未完成，请重新登录');
    expect(container.textContent).not.toContain('内部实现细节不应外泄');
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
