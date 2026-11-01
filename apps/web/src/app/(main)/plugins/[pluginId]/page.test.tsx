/**
 * 前端补测第二十五批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：插件详情页（P-10）的编排合同 ——
 * 1. 三态与两种「找不到」：普通失败可重试 vs 下架码 16002 的专门文案；
 * 2. 四个主按钮态：未安装→添加 / 已安装未启用→启用 / 已启用→打开工作台 /
 *    有更新→更新；套餐不可安装时的拦截提示；
 * 3. 授权抽屉 install/edit 分流、卸载二次确认（历史任务保留口径）、
 *    启停带在跑任务提示；
 * 4. 插件配置草稿初始化（select 取首项）与保存口径、版本说明过滤、
 *    产物类型业务用词、评价校验（<1 星拦截）与分页。
 *
 * CapabilityList / GrantDrawer / ReviewForm / ReviewList / VersionHistory 的
 * 内部合同已由补测第十四、十六、十七、十九批覆盖，本批用最小替身回显关键
 * props 并回放回调，只测页面自身的编排与分流。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import PluginDetailPage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  detail: vi.fn(),
  capabilities: vi.fn(),
  reviews: vi.fn(),
  install: vi.fn(),
  updateGrants: vi.fn(),
  setState: vi.fn(),
  upgrade: vi.fn(),
  rollback: vi.fn(),
  uninstall: vi.fn(),
  setSettings: vi.fn(),
  submitReview: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('next/navigation', () => ({ useParams: () => ({ pluginId: 'plg_weekly' }) }));

// next/link 依赖 App Router 上下文，jsdom 里没有；换成渲染 <a> 的最小替身。
vi.mock('next/link', async () => {
  const react = await import('react');
  return {
    default: ({ href, children }: { href: string; children?: unknown }) =>
      react.createElement('a', { href }, children as never),
  };
});

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  pluginsApi: {
    detail: mocks.detail,
    capabilities: mocks.capabilities,
    reviews: mocks.reviews,
    install: mocks.install,
    updateGrants: mocks.updateGrants,
    setState: mocks.setState,
    upgrade: mocks.upgrade,
    rollback: mocks.rollback,
    uninstall: mocks.uninstall,
    setSettings: mocks.setSettings,
    submitReview: mocks.submitReview,
  },
}));

// ---- 五个子组件的最小替身：回显关键 props + 回放回调 ---------------------

vi.mock('@/components/plugin/capability-list', async () => {
  const react = await import('react');
  return {
    CapabilityList: (props: { items: unknown[]; granted?: string[] }) =>
      react.createElement(
        'div',
        null,
        `能力清单${props.items.length}项|授权${props.granted === undefined ? '无' : props.granted.length}`,
      ),
  };
});

vi.mock('@/components/plugin/grant-drawer', async () => {
  const react = await import('react');
  return {
    GrantDrawer: (props: {
      open: boolean;
      mode: string;
      onSubmit: (payload: { grants: Record<string, boolean> }) => Promise<void>;
    }) =>
      props.open
        ? react.createElement(
            'div',
            null,
            `授权抽屉${props.mode}`,
            react.createElement(
              'button',
              {
                type: 'button',
                onClick: () => void props.onSubmit({ grants: { 'file.read': true } }),
              },
              '模拟提交授权',
            ),
          )
        : null,
  };
});

vi.mock('@/components/plugin/review-form', async () => {
  const react = await import('react');
  return {
    ReviewForm: (props: {
      rating: number;
      content: string;
      canReview: boolean;
      blockedReason: string;
      onRatingChange: (value: number) => void;
      onContentChange: (value: string) => void;
      onSubmit: () => void;
    }) =>
      react.createElement(
        'div',
        null,
        `评价表单|评分${props.rating}|可评价${String(props.canReview)}|${props.blockedReason}`,
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onRatingChange(5) },
          '选5星',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onContentChange('  很好用  ') },
          '填内容',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onSubmit() },
          '模拟提交评价',
        ),
      ),
  };
});

vi.mock('@/components/plugin/review-list', async () => {
  const react = await import('react');
  return {
    ReviewList: (props: { items: unknown[]; total: number }) =>
      react.createElement('div', null, `评价列表${props.items.length}/${props.total}`),
  };
});

vi.mock('@/components/plugin/version-history', async () => {
  const react = await import('react');
  return {
    VersionHistory: (props: {
      canManage: boolean;
      onUpgrade: (version: string) => void;
      onRollback: () => void;
    }) =>
      react.createElement(
        'div',
        null,
        `版本历史|可管理${String(props.canManage)}`,
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onUpgrade('2.0.0') },
          '模拟升级',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onRollback() },
          '模拟回退',
        ),
      ),
  };
});

afterEach(() => {
  document.body.innerHTML = '';
  // autoInstall 读的是地址栏参数，测完复位避免串扰
  window.history.replaceState({}, '', '/');
});

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

function click(element: Element | null | undefined): void {
  act(() => {
    element?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

function findButton(root: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(root.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(text),
  );
}

/** 弹窗走 portal 挂 body，确认按钮要在 `role=dialog` 内找（页面上也有「卸载」） */
function dialogButton(label: string): HTMLButtonElement | undefined {
  const dialog = document.body.querySelector('[role="dialog"]');
  return Array.from(dialog?.querySelectorAll('button') ?? []).find((item) =>
    item.textContent?.includes(label),
  );
}

/** 显式传 null 表示「空体」，不能用 `??`（null 会被吞掉） */
function pick(key: string, fallback: unknown, overrides: Record<string, unknown>): unknown {
  return key in overrides ? overrides[key] : fallback;
}

const CAP_ITEM = {
  capability_key: 'file.read',
  display_name: '读取文件',
  reason: '需要读取你选择的资料',
  denial_effect: '无法基于资料生成周报',
  category: 'personal',
  required: true,
  org_switch_enabled: true,
};

const PLUGIN = {
  plugin_id: 'plg_weekly',
  name: '周报助手',
  summary: '自动整理周报',
  description: '根据本周对话生成周报',
  category: 'efficiency',
  icon_key: 'weekly',
  rating_avg: 4.6,
  rating_count: 12,
  latest_version: '2.0.0',
  capabilities: [CAP_ITEM],
  inputs: [],
  step_titles: ['整理本周要点', '生成周报初稿'],
  outputs: [
    { key: 'report', label: '周报文档', type: 'file' },
    { key: 'share', label: '', type: 'link' },
    { key: 'grid', label: '数据表', type: 'table' },
    { key: 'note', label: '备注', type: 'text' },
  ],
  settings_schema: [
    { key: 'style', label: '周报风格', type: 'select', options: ['简洁', '详细'] },
    { key: 'tone', label: '语气', type: 'text' },
  ],
  versions: [
    { version: '2.0.0', published_at: '2026-10-20T00:00:00Z', changelog: '新增多语言支持' },
    { version: '1.9.0', published_at: '2026-09-01T00:00:00Z', changelog: '' },
  ],
  installed_status: 'enabled',
  installed_version: '1.9.0',
  has_update: true,
  installable: true,
  install_block_reason: '',
};

const CAPABILITIES_DATA = {
  items: [CAP_ITEM],
  granted_capabilities: ['file.read'],
  org_switch_updated_at: '',
  installable: true,
};

const REVIEWS_DATA = {
  items: [],
  page: 1,
  page_size: 10,
  total: 0,
  total_pages: 1,
  rating_avg: 4.6,
  rating_count: 12,
};

/** 三路查询的默认数据（各测试按需覆盖其中一路；显式 null 表示空体） */
function mockPluginData(
  overrides: {
    plugin?: unknown;
    capabilities?: unknown;
    reviews?: unknown;
  } = {},
): void {
  mocks.detail.mockResolvedValue(pick('plugin', PLUGIN, overrides));
  mocks.capabilities.mockResolvedValue(pick('capabilities', CAPABILITIES_DATA, overrides));
  mocks.reviews.mockResolvedValue(pick('reviews', REVIEWS_DATA, overrides));
}

describe('插件详情页', () => {
  it('加载中给提示；详情为空给「没有找到」空态', async () => {
    mockPluginData();
    mocks.detail.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<PluginDetailPage />);
    expect(loading.container.textContent).toContain('正在加载插件信息…');
    loading.unmount();

    mockPluginData({ plugin: null });
    const empty = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(empty.container.textContent).toContain('没有找到这个插件');
      });
    });
    const market = Array.from(empty.container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('去市场看看'),
    );
    expect(market?.getAttribute('href')).toBe('/plugins');
    empty.unmount();
  });

  it('已下架（16002）给专门文案，普通失败可原地重试', async () => {
    const { BusinessError } = await import('@/lib/api-client');
    mockPluginData();
    mocks.detail.mockRejectedValue(new BusinessError(16002, '插件已下架'));
    const gone = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(gone.container.textContent).toContain('该插件已下架');
      });
    });
    expect(gone.container.textContent).toContain('去市场看看其他插件');
    gone.unmount();

    mockPluginData();
    mocks.detail.mockRejectedValue(new Error('插件服务暂时不可用'));
    const failed = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(failed.container.textContent).toContain('插件服务暂时不可用');
      });
    });
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.detail).toHaveBeenCalledTimes(3);
      });
    });
    failed.unmount();
  });

  it('未安装时主按钮是添加，点击打开授权抽屉（安装模式）', async () => {
    mockPluginData({
      plugin: { ...PLUGIN, installed_status: 'none', has_update: false },
      capabilities: { ...CAPABILITIES_DATA, granted_capabilities: [] },
    });
    mocks.install.mockResolvedValue({ plugin_id: 'plg_weekly', status: 'installed' });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('周报助手');
      });
    });

    // 未安装：不出现「调整授权 / 卸载」
    expect(container.textContent).not.toContain('调整授权');
    expect(container.textContent).not.toContain('卸载');

    click(findButton(container, '添加插件'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('授权抽屉install');
      });
    });

    // 抽屉提交走安装接口（不是更新授权）
    click(findButton(container, '模拟提交授权'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.install).toHaveBeenCalledWith('plg_weekly', {
          grants: { 'file.read': true },
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已添加，可以打开工作台试试');
      });
    });
    expect(mocks.updateGrants).not.toHaveBeenCalled();
    unmount();
  });

  it('套餐不可安装时拦截添加并引导看套餐', async () => {
    mockPluginData({
      plugin: {
        ...PLUGIN,
        installed_status: 'none',
        has_update: false,
        installable: false,
        install_block_reason: '当前套餐不含插件能力',
      },
    });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('周报助手');
      });
    });

    const add = findButton(container, '添加插件');
    expect(add?.disabled).toBe(true);
    expect(container.textContent).toContain(
      '当前套餐不含插件能力，升级套餐后即可添加并使用工作台。',
    );
    const billing = Array.from(container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('查看套餐'),
    );
    expect(billing?.getAttribute('href')).toBe('/billing');
    unmount();
  });

  it('已安装未启用时主按钮是启用，在跑任务提示不打断', async () => {
    mockPluginData({
      plugin: {
        ...PLUGIN,
        installed_status: 'disabled',
        has_update: false,
        installed_version: '1.9.0',
      },
    });
    mocks.setState.mockResolvedValue({
      plugin_id: 'plg_weekly',
      status: 'enabled',
      running_task_count: 2,
    });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('周报助手');
      });
    });

    click(findButton(container, '启用'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.setState).toHaveBeenCalledWith('plg_weekly', 'enabled');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已启用（2 个在跑任务会继续完成）');
      });
    });
    unmount();
  });

  it('已启用时给工作台入口，可停用', async () => {
    mockPluginData({ plugin: { ...PLUGIN, has_update: false } });
    mocks.setState.mockResolvedValue({
      plugin_id: 'plg_weekly',
      status: 'disabled',
      running_task_count: 0,
    });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('周报助手');
      });
    });

    const workbench = Array.from(container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('打开工作台'),
    );
    expect(workbench?.getAttribute('href')).toBe('/workbench/plg_weekly');

    click(findButton(container, '停用'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.setState).toHaveBeenCalledWith('plg_weekly', 'disabled');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已停用');
      });
    });
    unmount();
  });

  it('有更新时可一键更新到最新版本', async () => {
    mockPluginData();
    mocks.upgrade.mockResolvedValue({ plugin_id: 'plg_weekly', version: '2.0.0' });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('有新版本');
      });
    });

    click(findButton(container, '更新到最新版本'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.upgrade).toHaveBeenCalledWith('plg_weekly', '2.0.0');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已更新到该版本');
      });
    });
    unmount();
  });

  it('调整授权走编辑模式只更新授权', async () => {
    mockPluginData();
    mocks.updateGrants.mockResolvedValue({ plugin_id: 'plg_weekly', granted_capabilities: [] });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('周报助手');
      });
    });

    click(findButton(container, '调整授权'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('授权抽屉edit');
      });
    });

    click(findButton(container, '模拟提交授权'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.updateGrants).toHaveBeenCalledWith('plg_weekly', {
          grants: { 'file.read': true },
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('授权已更新');
      });
    });
    // 编辑模式不该触发安装
    expect(mocks.install).not.toHaveBeenCalled();
    unmount();
  });

  it('卸载走二次确认并说明历史任务保留口径', async () => {
    mockPluginData();
    mocks.uninstall.mockResolvedValue({ plugin_id: 'plg_weekly', kept_task_count: 3 });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('周报助手');
      });
    });

    click(findButton(container, '卸载'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('卸载该插件？');
      });
    });
    expect(document.body.textContent).toContain('历史任务记录与产物会保留，180 天后统一清理');
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

  it('从市场带 action=install 进来自动打开授权抽屉', async () => {
    window.history.replaceState({}, '', '/plugins/plg_weekly?action=install');
    mockPluginData({
      plugin: { ...PLUGIN, installed_status: 'none', has_update: false },
      capabilities: { ...CAPABILITIES_DATA, granted_capabilities: [] },
    });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('授权抽屉install');
      });
    });
    unmount();

    // 已安装的不自动打开
    window.history.replaceState({}, '', '/plugins/plg_weekly?action=install');
    mockPluginData();
    const installed = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(installed.container.textContent).toContain('周报助手');
      });
    });
    expect(installed.container.textContent).not.toContain('授权抽屉');
    installed.unmount();
  });

  it('介绍区给占位文案，版本说明只列有内容的版本', async () => {
    mockPluginData({ plugin: { ...PLUGIN, description: '' } });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('插件未提供详细介绍');
      });
    });
    expect(container.textContent).toContain('新增多语言支持');
    // 空 changelog 的版本不进「版本说明」
    expect(container.textContent).not.toContain('v1.9.0');
    unmount();
  });

  it('步骤编号展示，产物类型用业务用词', async () => {
    mockPluginData();
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('整理本周要点');
      });
    });
    expect(container.textContent).toContain('生成周报初稿');
    // 产物：label 缺失回落 key，类型全部用业务用词
    expect(container.textContent).toContain('周报文档');
    expect(container.textContent).toContain('share');
    expect(container.textContent).toContain('文件');
    expect(container.textContent).toContain('链接');
    expect(container.textContent).toContain('表格');
    expect(container.textContent).toContain('文本');
    unmount();
  });

  it('步骤与产物为空各给空态', async () => {
    mockPluginData({
      plugin: { ...PLUGIN, step_titles: [], outputs: [] },
    });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有步骤说明');
      });
    });
    expect(container.textContent).toContain('还没有产物说明');
    unmount();
  });

  it('插件配置只在已安装后出现，草稿按声明初始化', async () => {
    mockPluginData({
      plugin: { ...PLUGIN, installed_status: 'none', has_update: false },
      capabilities: { ...CAPABILITIES_DATA, granted_capabilities: [] },
    });
    const notInstalled = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(notInstalled.container.textContent).toContain('周报助手');
      });
    });
    expect(notInstalled.container.textContent).not.toContain('插件配置');
    notInstalled.unmount();

    mockPluginData();
    mocks.setSettings.mockResolvedValue({ plugin_id: 'plg_weekly', settings: {} });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('插件配置');
      });
    });
    // select 草稿默认取第一个选项；text 默认空
    const select = container.querySelector<HTMLSelectElement>('#plugin-setting-style');
    expect(select?.value).toBe('简洁');
    expect(container.querySelector<HTMLInputElement>('#plugin-setting-tone')?.value).toBe('');

    click(findButton(container, '保存配置'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.setSettings).toHaveBeenCalledWith('plg_weekly', {
          style: '简洁',
          tone: '',
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('配置已保存');
      });
    });
    unmount();
  });

  it('版本历史可升级可回退，未安装只读', async () => {
    mockPluginData({ plugin: { ...PLUGIN, has_update: false } });
    mocks.upgrade.mockResolvedValue({ plugin_id: 'plg_weekly', version: '2.0.0' });
    mocks.rollback.mockResolvedValue({ plugin_id: 'plg_weekly', version: '1.9.0' });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('版本历史|可管理true');
      });
    });

    click(findButton(container, '模拟升级'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.upgrade).toHaveBeenCalledWith('plg_weekly', '2.0.0');
      });
    });
    click(findButton(container, '模拟回退'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.rollback).toHaveBeenCalledWith('plg_weekly');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已回退到上一版本');
      });
    });
    unmount();

    mockPluginData({
      plugin: { ...PLUGIN, installed_status: 'none', has_update: false },
      capabilities: { ...CAPABILITIES_DATA, granted_capabilities: [] },
    });
    const guest = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(guest.container.textContent).toContain('版本历史|可管理false');
      });
    });
    guest.unmount();
  });

  it('评价提交前校验星级，参数去空白，成功后清空表单', async () => {
    mockPluginData();
    mocks.submitReview.mockResolvedValue({ review_id: 'r1', rating: 5 });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('评价表单');
      });
    });

    // 没选星就提交：拦截且不发请求
    click(findButton(container, '模拟提交评价'));
    await act(async () => {});
    expect(mocks.toast.error).toHaveBeenCalledWith('请先选择 1-5 星');
    expect(mocks.submitReview).not.toHaveBeenCalled();

    click(findButton(container, '选5星'));
    click(findButton(container, '填内容'));
    click(findButton(container, '模拟提交评价'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.submitReview).toHaveBeenCalledWith('plg_weekly', {
          rating: 5,
          content: '很好用',
          anonymous: false,
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('评价已提交');
      });
    });
    // 成功后表单回零
    expect(container.textContent).toContain('评价表单|评分0');
    unmount();
  });

  it('未安装不能评价并说明原因', async () => {
    mockPluginData({
      plugin: { ...PLUGIN, installed_status: 'none', has_update: false },
      capabilities: { ...CAPABILITIES_DATA, granted_capabilities: [] },
    });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('评价表单');
      });
    });
    expect(container.textContent).toContain('可评价false');
    expect(container.textContent).toContain('使用过这个插件才能评价');
    unmount();
  });

  it('评价超过一页给分页，翻页换查询参数', async () => {
    mockPluginData({
      reviews: { ...REVIEWS_DATA, total: 25, total_pages: 3 },
    });
    const { container, unmount } = renderWithClient(<PluginDetailPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('评价列表0/25');
      });
    });

    click(container.querySelector('button[aria-label="下一页"]'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.reviews).toHaveBeenCalledWith('plg_weekly', { page: 2, page_size: 10 });
      });
    });
    unmount();
  });
});
