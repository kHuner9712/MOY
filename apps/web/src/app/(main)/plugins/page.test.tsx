/**
 * 前端补测第三十五批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：插件市场页（P-09）三标签的编排合同 ——
 * 1. 市场：三态、空态双口径与清空筛选、筛选参数拼装（分类/排序/关键字去空白）、
 *    卡片三条出口（详情/工作台/可安装进详情安装），免费版不降级（不可安装
 *    照常展示，点安装弹「查看套餐」入口）；
 * 2. 已添加：懒加载、启停（在跑任务不打断提示）、升级、卸载二次确认（历史保留口径）；
 * 3. 办公工具：懒加载与双空态。
 *
 * PluginCard / CategoryBar / InstalledTable 内部合同已由补测第十四、十七批
 * 覆盖，本批用最小替身回显关键 props 并回放回调。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import PluginsPage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  push: vi.fn(),
  market: vi.fn(),
  installed: vi.fn(),
  setState: vi.fn(),
  uninstall: vi.fn(),
  upgrade: vi.fn(),
  registry: vi.fn(),
  listExecutions: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  pluginsApi: {
    market: mocks.market,
    installed: mocks.installed,
    setState: mocks.setState,
    uninstall: mocks.uninstall,
    upgrade: mocks.upgrade,
  },
  toolApi: { registry: mocks.registry },
  toolsExtApi: { listExecutions: mocks.listExecutions },
}));

// 子组件用最小替身：回显关键 props 并回放回调
vi.mock('@/components/plugin/plugin-card', async () => {
  const react = await import('react');
  return {
    PluginCard: (props: {
      plugin: { plugin_id: string };
      onOpenDetail: (id: string) => void;
      onInstall: (plugin: unknown) => void;
      onOpenWorkbench: (id: string) => void;
    }) =>
      react.createElement(
        'div',
        null,
        `插件卡${props.plugin.plugin_id}`,
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onOpenDetail(props.plugin.plugin_id) },
          '模拟看详情',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onInstall(props.plugin) },
          '模拟安装',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onOpenWorkbench(props.plugin.plugin_id) },
          '模拟进工作台',
        ),
      ),
  };
});

vi.mock('@/components/plugin/category-bar', async () => {
  const react = await import('react');
  return {
    CategoryBar: (props: {
      onValueChange: (key: string) => void;
      onSortChange: (next: string) => void;
      onKeywordChange: (next: string) => void;
    }) =>
      react.createElement(
        'div',
        null,
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onValueChange('efficiency') },
          '模拟选分类',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onSortChange('newest') },
          '模拟换排序',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onKeywordChange('  周报  ') },
          '模拟搜关键字',
        ),
      ),
  };
});

vi.mock('@/components/plugin/installed-table', async () => {
  const react = await import('react');
  return {
    InstalledTable: (props: {
      items: Array<{ plugin_id: string; status: string; latest_version: string }>;
      onToggleState: (plugin: unknown) => void;
      onUpgrade: (plugin: unknown) => void;
      onUninstall: (plugin: unknown) => void;
    }) =>
      react.createElement(
        'div',
        null,
        `已添加表${props.items.length}`,
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onToggleState(props.items[0]) },
          '模拟启停',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onUpgrade(props.items[0]) },
          '模拟升级',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onUninstall(props.items[0]) },
          '模拟卸载',
        ),
      ),
  };
});

/** 标签状态读地址栏（useUrlTab），整文件用假 location 替身 */
function installFakeLocation(search = ''): void {
  Object.defineProperty(window, 'location', {
    value: {
      origin: 'http://localhost:3000',
      pathname: '/plugins',
      search,
      hash: '',
      assign: vi.fn(),
    },
    writable: true,
    configurable: true,
  });
}

beforeEach(() => {
  installFakeLocation();
});

afterEach(() => {
  document.body.innerHTML = '';
});

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
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

function findButton(root: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  );
}

function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

const ITEM = {
  plugin_id: 'plg_weekly',
  name: '周报助手',
  summary: '自动整理周报',
  category: 'efficiency',
  icon_key: 'weekly',
  rating_avg: 4.6,
  rating_count: 12,
  latest_version: '2.0.0',
  installed_status: 'none',
  has_update: false,
  installable: true,
};

const INSTALLED_ITEM = {
  plugin_id: 'plg_weekly',
  name: '周报助手',
  category: 'efficiency',
  version: '1.9.0',
  latest_version: '2.0.0',
  has_update: true,
  status: 'enabled',
  last_used_at: null,
  running_task_count: 0,
};

/** 显式传 null 表示「空体」 */
function pick(key: string, fallback: unknown, overrides: Record<string, unknown>): unknown {
  return key in overrides ? overrides[key] : fallback;
}

function mockPluginsData(
  overrides: { market?: unknown; installed?: unknown; tools?: unknown; executions?: unknown } = {},
): void {
  mocks.market.mockResolvedValue(
    pick(
      'market',
      {
        items: [ITEM],
        categories: [{ key: 'efficiency', label: '效率' }],
        total: 13,
        page: 1,
        page_size: 12,
        total_pages: 2,
      },
      overrides,
    ),
  );
  mocks.installed.mockResolvedValue(pick('installed', { items: [INSTALLED_ITEM] }, overrides));
  mocks.registry.mockResolvedValue(pick('tools', { tools: [] }, overrides));
  mocks.listExecutions.mockResolvedValue(pick('executions', { items: [] }, overrides));
}

describe('插件市场页', () => {
  it('市场三态：加载、失败可重试', async () => {
    mockPluginsData();
    mocks.market.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<PluginsPage />);
    expect(loading.container.textContent).toContain('正在加载插件…');
    loading.unmount();

    mocks.market.mockClear();
    mockPluginsData();
    mocks.market.mockRejectedValue(new Error('插件服务暂时不可用'));
    const failed = renderWithClient(<PluginsPage />);
    await waitForText(failed.container, '插件服务暂时不可用');
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.market).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();
  });

  it('空态双口径：有筛选给清空入口，清空后回到全量口径', async () => {
    mockPluginsData({
      market: { items: [], categories: [], total: 0, page: 1, page_size: 12, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<PluginsPage />);

    click(findButton(container, '模拟选分类'));
    await waitForText(container, '没有匹配的插件，换个关键字试试');
    expect(container.textContent).toContain('可以清空分类或关键字');

    click(findButton(container, '清空筛选'));
    await waitForText(container, '还没有可用的插件');
    expect(container.textContent).toContain('平台会陆续上架岗位插件');
    unmount();
  });

  it('筛选参数拼装：分类/排序/关键字去空白，换筛选回第 1 页', async () => {
    mockPluginsData();
    const { container, unmount } = renderWithClient(<PluginsPage />);
    await waitForText(container, '插件卡plg_weekly');

    click(findButton(container, '模拟选分类'));
    click(findButton(container, '模拟换排序'));
    click(findButton(container, '模拟搜关键字'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.market).toHaveBeenCalledWith({
          page: 1,
          page_size: 12,
          sort: 'newest',
          category: 'efficiency',
          keyword: '周报',
        });
      });
    });
    unmount();
  });

  it('卡片三条出口：详情、工作台、可安装进详情安装', async () => {
    mockPluginsData();
    const { container, unmount } = renderWithClient(<PluginsPage />);
    await waitForText(container, '插件卡plg_weekly');

    click(findButton(container, '模拟看详情'));
    expect(mocks.push).toHaveBeenCalledWith('/plugins/plg_weekly');
    click(findButton(container, '模拟进工作台'));
    expect(mocks.push).toHaveBeenCalledWith('/workbench/plg_weekly');
    click(findButton(container, '模拟安装'));
    // 统一进详情页完成安装（授权抽屉与二次确认都在详情页）
    expect(mocks.push).toHaveBeenCalledWith('/plugins/plg_weekly?action=install');
    unmount();
  });

  it('免费版不降级：不可安装照常展示，点安装弹查看套餐入口', async () => {
    mockPluginsData({
      market: {
        items: [{ ...ITEM, installable: false }],
        categories: [],
        total: 1,
        page: 1,
        page_size: 12,
        total_pages: 1,
      },
    });
    const { container, unmount } = renderWithClient(<PluginsPage />);
    await waitForText(container, '插件卡plg_weekly');

    click(findButton(container, '模拟安装'));
    await waitForText(document.body, '当前套餐不含插件能力');
    expect(document.body.textContent).toContain('已上架的插件仍可随时查看详情');
    const billing = Array.from(document.body.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('查看套餐'),
    );
    expect(billing?.getAttribute('href')).toBe('/billing');
    // 不给假入口：没有跳转
    expect(mocks.push).not.toHaveBeenCalled();
    unmount();
  });

  it('已添加标签才拉取（懒加载）', async () => {
    mockPluginsData();
    const market = renderWithClient(<PluginsPage />);
    await waitForText(market.container, '插件卡plg_weekly');
    expect(mocks.installed).not.toHaveBeenCalled();
    market.unmount();

    installFakeLocation('?tab=installed');
    mockPluginsData();
    const installedView = renderWithClient(<PluginsPage />);
    await waitForText(installedView.container, '已添加表1');
    installedView.unmount();
  });

  it('启停带「在跑任务不受影响」口径', async () => {
    installFakeLocation('?tab=installed');
    mockPluginsData();
    mocks.setState.mockResolvedValue({
      plugin_id: 'plg_weekly',
      status: 'disabled',
      running_task_count: 2,
    });
    const { container, unmount } = renderWithClient(<PluginsPage />);
    await waitForText(container, '已添加表1');

    click(findButton(container, '模拟启停'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.setState).toHaveBeenCalledWith('plg_weekly', 'disabled');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith(
          '已停用（2 个在跑任务不受影响，会继续完成）',
        );
      });
    });
    unmount();
  });

  it('升级到最新版本', async () => {
    installFakeLocation('?tab=installed');
    mockPluginsData();
    mocks.upgrade.mockResolvedValue({ plugin_id: 'plg_weekly', version: '2.0.0' });
    const { container, unmount } = renderWithClient(<PluginsPage />);
    await waitForText(container, '已添加表1');

    click(findButton(container, '模拟升级'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.upgrade).toHaveBeenCalledWith('plg_weekly', '2.0.0');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已更新到最新版本');
      });
    });
    unmount();
  });

  it('卸载二次确认并说明历史保留口径', async () => {
    installFakeLocation('?tab=installed');
    mockPluginsData();
    mocks.uninstall.mockResolvedValue({ plugin_id: 'plg_weekly', kept_task_count: 3 });
    const { container, unmount } = renderWithClient(<PluginsPage />);
    await waitForText(container, '已添加表1');

    click(findButton(container, '模拟卸载'));
    await waitForText(document.body, '卸载该插件？');
    expect(document.body.textContent).toContain('历史任务记录会保留，180 天后统一清理');
    click(dialogButton('卸载'));

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.uninstall).toHaveBeenCalledWith('plg_weekly');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已卸载，3 个历史任务记录已保留');
      });
    });
    unmount();
  });

  it('办公工具标签懒加载并给双空态', async () => {
    mockPluginsData();
    const market = renderWithClient(<PluginsPage />);
    await waitForText(market.container, '插件卡plg_weekly');
    expect(mocks.registry).not.toHaveBeenCalled();
    market.unmount();

    installFakeLocation('?tab=tools');
    mockPluginsData();
    const tools = renderWithClient(<PluginsPage />);
    await waitForText(tools.container, '还没有可连接的工具');
    expect(tools.container.textContent).toContain('如需接入内部系统请联系平台管理员');
    expect(tools.container.textContent).toContain('还没有调用记录');
    tools.unmount();
  });
});
