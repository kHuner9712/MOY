/**
 * 前端补测第三十六批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：两个壳层组件 ——
 * 1. 根级兜底页（B15）：只给业务语言与两条出路（重试 / 重新加载），
 *    错误细节只进控制台不上屏，不依赖任何 UI 库（内联样式自洽）；
 * 2. 回跳页服务端壳：只做壳与元信息（不向外部站点传完整地址）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import GlobalError from './global-error';

// 回跳壳只测「壳与元信息」，流程本体已由第二十六批覆盖
vi.mock('@/app/(auth)/auth/callback/callback-flow', () => ({ CallbackFlow: () => null }));

/** jsdom 的 location 不可打桩（unforgeable），整文件用假 location 替身 */
interface FakeLocation {
  reload: ReturnType<typeof vi.fn>;
}

function installFakeLocation(): FakeLocation {
  const fake: FakeLocation = { reload: vi.fn() };
  Object.defineProperty(window, 'location', {
    value: {
      origin: 'http://localhost:3000',
      pathname: '/auth/callback',
      search: '',
      hash: '',
      assign: vi.fn(),
      reload: fake.reload,
    },
    writable: true,
    configurable: true,
  });
  return fake;
}

let fakeLocation: FakeLocation;

beforeEach(() => {
  fakeLocation = installFakeLocation();
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

function click(element: Element | null | undefined): void {
  act(() => {
    element?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

describe('根级兜底页', () => {
  const error = Object.assign(new Error('boom：内部实现细节不应外泄'), { digest: 'abc123' });

  it('只给业务语言与两条出路，不泄任何错误细节', () => {
    const { container, unmount } = renderComponent(<GlobalError error={error} reset={vi.fn()} />);

    expect(container.textContent).toContain('页面暂时无法显示');
    expect(container.textContent).toContain('页面数据没有丢失');
    expect(container.textContent).toContain('请联系团队管理员');
    // 错误信息、digest、技术名词都不上屏
    expect(container.textContent).not.toContain('boom');
    expect(container.textContent).not.toContain('abc123');
    expect(container.textContent).not.toContain('Error');

    const buttons = Array.from(container.querySelectorAll('button')).map(
      (item) => item.textContent,
    );
    expect(buttons).toEqual(['重试', '重新加载']);
    unmount();
  });

  it('重试走 React 重渲染出口', () => {
    const reset = vi.fn();
    const { container, unmount } = renderComponent(<GlobalError error={error} reset={reset} />);

    const retry = Array.from(container.querySelectorAll('button')).find(
      (item) => item.textContent === '重试',
    );
    click(retry);
    expect(reset).toHaveBeenCalled();
    unmount();
  });

  it('重新加载走整页恢复出口', () => {
    const { container, unmount } = renderComponent(<GlobalError error={error} reset={vi.fn()} />);

    const reload = Array.from(container.querySelectorAll('button')).find(
      (item) => item.textContent === '重新加载',
    );
    click(reload);
    expect(fakeLocation.reload).toHaveBeenCalled();
    unmount();
  });

  it('原始错误只进控制台，便于排查', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { unmount } = renderComponent(<GlobalError error={error} reset={vi.fn()} />);

    expect(consoleError).toHaveBeenCalledWith('[global-error]', error);
    unmount();
  });

  it('不依赖 UI 库：内联样式自洽', () => {
    const { container, unmount } = renderComponent(<GlobalError error={error} reset={vi.fn()} />);

    // 兜底层可能连全局样式都没加载：结构与视觉全靠内联样式
    const main = container.querySelector('main');
    expect(main).not.toBeNull();
    expect(main?.getAttribute('style')).toContain('max-width');
    expect(container.querySelector('h1')?.getAttribute('style')).toContain('font-size');
    unmount();
  });
});

describe('回跳页服务端壳', () => {
  it('只做壳与元信息，流程交给客户端组件', async () => {
    const pageModule = await import('./(auth)/auth/callback/page');

    expect(pageModule.metadata.title).toBe('登录');
    // 不向外部站点传递含参数的完整地址
    expect(pageModule.metadata.referrer).toBe('no-referrer');

    const { container, unmount } = renderComponent(<pageModule.default />);
    expect(container).toBeTruthy();
    unmount();
  });
});
