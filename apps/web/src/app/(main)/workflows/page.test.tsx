/**
 * 前端补测第二十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：工作流列表页（P-13）的渲染与查询合同 ——
 * 表格三态、四维筛选（插件 / 状态 / 发起人 / 时间范围）的请求参数拼装、
 * 插件名映射与通用任务兜底、清空筛选复位。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import WorkflowsPage from './page';

const mocks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  installed: vi.fn(),
  taskStats: vi.fn(),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  agentApi: { listTasks: mocks.listTasks },
  pluginsApi: { installed: mocks.installed },
  statsApi: { tasks: mocks.taskStats },
}));

// next/link 依赖 App Router 上下文，jsdom 里没有；换成渲染 <a> 的最小替身，
// href 合同（导航去向）仍然可断言。
vi.mock('next/link', async () => {
  const react = await import('react');
  return {
    default: ({ href, children }: { href: string; children?: unknown }) =>
      react.createElement('a', { href }, children as never),
  };
});

afterEach(() => {
  document.body.innerHTML = '';
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

function typeInto(container: ParentNode, selector: string, value: string): void {
  const field = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector);
  act(() => {
    if (field) {
      const proto =
        field instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      setter?.call(field, value);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

/** 受控下拉换值（原生 select） */
function setSelectValue(select: HTMLSelectElement | null | undefined, value: string): void {
  if (!select) return;
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

/** 任务统计卡（页首）只要能出数就行，本批不重复测它 */
const STATS = {
  range: '7d',
  total: 20,
  completed: 14,
  failed: 4,
  cancelled: 2,
  active_users: 10,
  completion_rate: 77.78,
  avg_duration_seconds: 3661,
  p50_duration_seconds: 1800,
  repeat_user_rate: 40,
  units_per_success: 1234.5,
};

const TASK = {
  task_id: 'task_1',
  conversation_id: 'conv_1',
  goal: '整理本周周报',
  status: 'completed',
  progress: 100,
  created_at: '2026-10-31T09:00:00Z',
  updated_at: '2026-10-31T10:00:00Z',
  plugin_id: 'plg_weekly',
  warning_count: 3,
};

const INSTALLED = {
  items: [
    {
      plugin_id: 'plg_weekly',
      name: '周报助手',
      category: 'efficiency',
      version: '1.0.0',
      latest_version: '1.0.0',
      has_update: false,
      status: 'enabled',
      last_used_at: null,
      running_task_count: 0,
    },
  ],
};

describe('工作流列表页', () => {
  it('加载中给表格骨架', () => {
    mocks.taskStats.mockResolvedValue(STATS);
    mocks.installed.mockResolvedValue(INSTALLED);
    mocks.listTasks.mockImplementation(() => new Promise(() => {}));
    const { container, unmount } = renderWithClient(<WorkflowsPage />);
    expect(container.querySelectorAll('.skeleton').length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain('还没有自动化任务');
    unmount();
  });

  it('加载失败给业务文案并可原地重试', async () => {
    mocks.taskStats.mockResolvedValue(STATS);
    mocks.installed.mockResolvedValue(INSTALLED);
    mocks.listTasks.mockRejectedValue(new Error('任务服务暂时不可用'));
    const { container, unmount } = renderWithClient(<WorkflowsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('任务服务暂时不可用');
      });
    });
    const retry = findButton(container, '重试');
    click(retry);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTasks).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('无任务时给空态并引导去添加工作台', async () => {
    mocks.taskStats.mockResolvedValue(STATS);
    mocks.installed.mockResolvedValue(INSTALLED);
    mocks.listTasks.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<WorkflowsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有自动化任务');
      });
    });
    expect(container.textContent).toContain('添加岗位工作台后即可发起任务');
    const link = Array.from(container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('去添加工作台'),
    );
    expect(link?.getAttribute('href')).toBe('/plugins');
    unmount();
  });

  it('列表行：插件名映射、提醒条数与详情入口', async () => {
    mocks.taskStats.mockResolvedValue(STATS);
    mocks.installed.mockResolvedValue(INSTALLED);
    mocks.listTasks.mockResolvedValue({
      items: [
        TASK,
        {
          ...TASK,
          task_id: 'task_2',
          goal: '',
          plugin_id: null,
          status: 'queued',
          progress: 0,
          warning_count: 0,
        },
        {
          ...TASK,
          task_id: 'task_3',
          plugin_id: 'plg_unknown',
        },
      ],
      meta: { page: 1, page_size: 20, total: 3, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<WorkflowsPage />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('整理本周周报');
      });
    });
    // 已安装插件用产品名，不在界面暴露内部标识
    expect(container.textContent).toContain('周报助手');
    // 未命名任务占位
    expect(container.textContent).toContain('未命名任务');
    // 没有插件的任务标「通用任务」
    expect(container.textContent).toContain('通用任务');
    // 未安装/未知插件回落展示标识本身（至少还能对得上号）
    expect(container.textContent).toContain('plg_unknown');
    // 提醒条数：有则「N 处」，无则占位
    expect(container.textContent).toContain('3 处');
    expect(container.textContent).toContain('100%');
    const detail = Array.from(container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('详情'),
    );
    expect(detail?.getAttribute('href')).toBe('/workflows/task_1');
    unmount();
  });

  it('状态筛选换查询参数并回到第 1 页', async () => {
    mocks.taskStats.mockResolvedValue(STATS);
    mocks.installed.mockResolvedValue(INSTALLED);
    mocks.listTasks.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<WorkflowsPage />);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTasks).toHaveBeenCalledWith({ page: 1, page_size: 20 });
      });
    });

    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="按状态筛选"]'),
      'failed',
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTasks).toHaveBeenCalledWith({ page: 1, page_size: 20, status: 'failed' });
      });
    });

    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="按插件筛选"]'),
      'plg_weekly',
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTasks).toHaveBeenCalledWith({
          page: 1,
          page_size: 20,
          plugin_id: 'plg_weekly',
          status: 'failed',
        });
      });
    });
    unmount();
  });

  it('发起人标识去空白，时间范围补齐当天边界', async () => {
    mocks.taskStats.mockResolvedValue(STATS);
    mocks.installed.mockResolvedValue(INSTALLED);
    mocks.listTasks.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<WorkflowsPage />);

    typeInto(container, 'input[aria-label="按发起人筛选"]', '  usr_9  ');
    typeInto(container, 'input[aria-label="创建时间起"]', '2026-11-01');
    typeInto(container, 'input[aria-label="创建时间止"]', '2026-11-02');

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTasks).toHaveBeenCalledWith({
          page: 1,
          page_size: 20,
          initiator_id: 'usr_9',
          created_from: '2026-11-01T00:00:00Z',
          created_to: '2026-11-02T23:59:59Z',
        });
      });
    });
    unmount();
  });

  it('筛选无结果给筛选空态，清空筛选回到全量', async () => {
    mocks.taskStats.mockResolvedValue(STATS);
    mocks.installed.mockResolvedValue(INSTALLED);
    mocks.listTasks.mockResolvedValue({
      items: [],
      meta: { page: 1, page_size: 20, total: 0, total_pages: 1 },
    });
    const { container, unmount } = renderWithClient(<WorkflowsPage />);

    setSelectValue(
      container.querySelector<HTMLSelectElement>('select[aria-label="按状态筛选"]'),
      'failed',
    );
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('没有符合条件的任务');
      });
    });
    expect(container.textContent).toContain('换个筛选条件再看');

    // 空态里的「清空筛选」与筛选栏的都能复位
    const clear = findButton(container, '清空筛选');
    click(clear);
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listTasks).toHaveBeenCalledWith({ page: 1, page_size: 20 });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('还没有自动化任务');
      });
    });
    // 复位后筛选栏不再显示「清空筛选」
    expect(findButton(container, '清空筛选')).toBeUndefined();
    unmount();
  });
});
