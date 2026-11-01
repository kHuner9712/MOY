/**
 * 前端补测第三十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：三个布局的组装合同 ——
 * 1. 主界面布局：会话门包住侧栏与子页面、标题与导航共用文案、
 *    断网提示条事件驱动、全局错误兜底安装；
 * 2. 运营后台布局：权限前置探测（20003 不渲染运营菜单）、
 *    其余错误可重试、成功后导航高亮与最小可见口径；
 * 3. 登录布局：独立居中容器。
 *
 * 会话门本身的状态机已由本批 session-gate.test.tsx 覆盖，这里用最小替身。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import AuthLayout from './(auth)/layout';
import MainLayout from './(main)/layout';
import OpsLayout from './(main)/ops/layout';

const mocks = vi.hoisted(() => ({
  pathname: '/ops/tenants',
  installErrorGuard: vi.fn(),
  getSettings: vi.fn(),
}));

vi.mock('next/navigation', () => ({ usePathname: () => mocks.pathname }));

vi.mock('@/components/layout/session-gate', () => ({
  SessionGate: (props: { children?: unknown }) => props.children as never,
}));

vi.mock('@/components/layout/mobile-nav', () => ({ MobileNav: () => null }));

vi.mock('@/components/brand/brand-mark', () => ({ BrandMark: () => null }));

vi.mock('@/lib/error-guard', () => ({ installErrorGuard: mocks.installErrorGuard }));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  opsApi: { getSettings: mocks.getSettings },
}));

// 侧栏只替掉组件本体（它会发请求），保留纯函数 resolveNavItem 供标题断言
vi.mock('@/components/layout/sidebar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/layout/sidebar')>()),
  AppSidebar: () => null,
}));

import { resolveNavItem } from '@/components/layout/sidebar';

afterEach(() => {
  document.body.innerHTML = '';
});

function render(element: ReactNode): ReturnType<typeof renderComponent> {
  return renderComponent(element);
}

/** 运营布局自己用 useApiQuery，需要查询容器 */
function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
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

function findButton(root: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  );
}

describe('主界面布局', () => {
  it('标题与导航共用文案，子页面在门内渲染', () => {
    mocks.pathname = '/chat';
    mocks.installErrorGuard.mockReturnValue(() => {});
    const { container, unmount } = render(
      <MainLayout>
        <div>子页面内容</div>
      </MainLayout>,
    );

    expect(container.textContent).toContain('子页面内容');
    const heading = container.querySelector('h1');
    expect(heading?.textContent).toBe(resolveNavItem('/chat')?.label ?? '');
    unmount();
  });

  it('断网提示条事件驱动：断开出现、恢复消失', () => {
    mocks.installErrorGuard.mockReturnValue(() => {});
    const { container, unmount } = render(<MainLayout>子页面内容</MainLayout>);

    expect(container.textContent).not.toContain('网络连接已断开');
    Object.defineProperty(window.navigator, 'onLine', {
      value: false,
      configurable: true,
    });
    act(() => {
      window.dispatchEvent(new Event('offline'));
    });
    expect(container.textContent).toContain('网络连接已断开，请检查网络后重试');

    Object.defineProperty(window.navigator, 'onLine', {
      value: true,
      configurable: true,
    });
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    expect(container.textContent).not.toContain('网络连接已断开');
    unmount();
  });

  it('安装全局错误兜底', () => {
    mocks.installErrorGuard.mockReturnValue(() => {});
    const { unmount } = render(<MainLayout>子页面内容</MainLayout>);
    expect(mocks.installErrorGuard).toHaveBeenCalled();
    unmount();
  });
});

describe('运营后台布局', () => {
  it('进入前先探测权限', async () => {
    mocks.getSettings.mockImplementation(() => new Promise(() => {}));
    const { container, unmount } = renderWithClient(<OpsLayout>运营内容</OpsLayout>);
    expect(container.textContent).toContain('正在确认运营权限…');
    expect(container.textContent).not.toContain('运营内容');
    unmount();
  });

  it('企业账号（20003）给权限提示且不渲染运营菜单', async () => {
    const { BusinessError } = await import('@/lib/api-client');
    mocks.getSettings.mockRejectedValue(new BusinessError(20003, '无权限'));
    const { container, unmount } = renderWithClient(<OpsLayout>运营内容</OpsLayout>);

    await waitForText(container, '当前账号没有该操作的权限');
    // 不给菜单：避免用户点进去一路报错
    expect(container.textContent).not.toContain('企业管理');
    expect(container.textContent).not.toContain('运营内容');
    unmount();
  });

  it('探测失败可原地重试', async () => {
    mocks.getSettings.mockRejectedValue(new Error('运营服务暂时不可用'));
    const { container, unmount } = renderWithClient(<OpsLayout>运营内容</OpsLayout>);

    await waitForText(container, '运营服务暂时不可用');
    const retry = findButton(container, '重试');
    act(() => {
      retry?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getSettings).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('有权限时给导航与最小可见口径', async () => {
    mocks.getSettings.mockResolvedValue({ quota_warn_threshold: 80 });
    mocks.pathname = '/ops/tenants';
    const { container, unmount } = renderWithClient(<OpsLayout>运营内容</OpsLayout>);

    await waitForText(container, '运营内容');
    expect(container.textContent).toContain('不展示任何对话内容与文档原文');
    expect(container.textContent).toContain('企业管理');
    expect(container.textContent).toContain('订单管理');
    expect(container.textContent).toContain('平台统计');
    expect(container.textContent).toContain('系统配置');

    // 当前路由的导航项高亮
    const active = container.querySelector('a[aria-current="page"]');
    expect(active?.getAttribute('href')).toBe('/ops/tenants');
    unmount();
  });
});

describe('登录布局', () => {
  it('独立居中容器渲染子内容', () => {
    const { container, unmount } = render(<AuthLayout>登录表单</AuthLayout>);
    expect(container.textContent).toContain('登录表单');
    expect(container.querySelector('.max-w-md')).not.toBeNull();
    unmount();
  });
});
