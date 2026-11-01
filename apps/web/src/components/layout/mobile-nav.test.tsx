/**
 * 前端补测第二十批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：移动端顶部导航的渲染合同 —— 主项/溢出项均从 NAV_ITEMS 派生
 * （不复制文案、结构上不漏项）、抽屉不放运营后台入口、当前路由高亮、
 * 点完即关抽屉。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';
import { NAV_ITEMS } from './sidebar';
import { MobileNav } from './mobile-nav';

const nav = vi.hoisted(() => ({
  pathname: '/chat',
  push: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  nav.pathname = '/chat';
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('MobileNav', () => {
  it('顶部只放 4 个高频主项，从 NAV_ITEMS 派生文案', () => {
    const { container, unmount } = renderComponent(<MobileNav />);
    const links = Array.from(container.querySelectorAll('a'));
    expect(links).toHaveLength(4);

    const labels = links.map((link) => link.getAttribute('aria-label'));
    for (const label of labels) {
      expect(NAV_ITEMS.some((item) => item.label === label)).toBe(true);
    }
    // 主项为「对话/知识库/插件与工作台/个人中心」
    expect(labels).toContain('对话');
    expect(labels).toContain('知识库');
    unmount();
  });

  it('溢出项 = NAV_ITEMS 与主项的差集（结构上不漏项）', () => {
    const { unmount } = renderComponent(<MobileNav />);
    // 打开「更多」抽屉
    act(() => {
      const more = document.body.querySelector<HTMLButtonElement>('button[aria-label="更多功能"]');
      more?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const drawerLinks = Array.from(document.body.querySelectorAll('nav[aria-label="更多导航"] a'));
    const primaryHrefs = ['/chat', '/knowledge', '/plugins', '/profile'];
    const expectedOverflow = NAV_ITEMS.filter((item) => !primaryHrefs.includes(item.href));
    expect(drawerLinks).toHaveLength(expectedOverflow.length);
    for (const item of expectedOverflow) {
      expect(document.body.textContent).toContain(item.label);
    }
    unmount();
  });

  it('抽屉里不放运营后台入口（企业账号点了必然被拒）', () => {
    const { unmount } = renderComponent(<MobileNav />);
    act(() => {
      const more = document.body.querySelector<HTMLButtonElement>('button[aria-label="更多功能"]');
      more?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const hrefs = Array.from(document.body.querySelectorAll('nav[aria-label="更多导航"] a')).map(
      (link) => link.getAttribute('href'),
    );
    expect(hrefs.some((href) => href?.startsWith('/ops'))).toBe(false);
    unmount();
  });

  it('当前路由的主项标记 aria-current', () => {
    nav.pathname = '/knowledge';
    const { container, unmount } = renderComponent(<MobileNav />);
    const active = container.querySelector('a[aria-current="page"]');
    expect(active?.getAttribute('aria-label')).toBe('知识库');
    unmount();
  });

  it('溢出区路由命中时「更多」按钮高亮', () => {
    nav.pathname = '/team/organization';
    const { container, unmount } = renderComponent(<MobileNav />);
    const more = container.querySelector('button[aria-label="更多功能"]');
    expect(more?.className).toContain('text-primary');
    unmount();
  });

  it('点击溢出项后关闭抽屉（避免挡住新页面）', () => {
    nav.pathname = '/chat';
    const { unmount } = renderComponent(<MobileNav />);
    act(() => {
      const more = document.body.querySelector<HTMLButtonElement>('button[aria-label="更多功能"]');
      more?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(document.body.textContent).toContain('更多功能');

    act(() => {
      const link = document.body.querySelector('nav[aria-label="更多导航"] a');
      link?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    // 抽屉已收起
    expect(document.body.textContent).not.toContain('更多导航');
    unmount();
  });

  it('子路由同样命中标记（/plugins/xxx 归到插件主项）', () => {
    nav.pathname = '/plugins/plg_1';
    const { container, unmount } = renderComponent(<MobileNav />);
    const active = container.querySelector('a[aria-current="page"]');
    expect(active?.getAttribute('aria-label')).toBe('插件与工作台');
    unmount();
  });
});
