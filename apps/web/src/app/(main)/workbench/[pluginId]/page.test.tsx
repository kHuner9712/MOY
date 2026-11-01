/**
 * 前端补测第二十九批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：岗位工作台宿主（P-11）的编排合同 ——
 * 1. 声明驱动：buildSections 按插件声明推导区块（纯函数直测）；
 * 2. 三态与两种「进不去」：16003 未添加专门空态 vs 普通失败可重试；
 * 3. 提交链路：必填拦截、goal 深链预填与下发、排队文案三档（队首/排队中/直接执行）；
 * 4. 任务跟进：waiting_confirm 三条动作、进行中才轮询、进终态停并刷最近执行；
 * 5. 区块内容：步骤三态、产物类型业务用词、帮助栏收最近任务。
 *
 * DeclarationForm / ConfirmBar / SubmitBar / HelpPanel 的内部合同已由
 * 补测第十三、十八、二十批覆盖，本批用最小替身回显关键 props 并回放回调，
 * 表单状态机走真实 useDeclarationForm。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import WorkbenchPage, { buildSections } from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  detail: vi.fn(),
  listTasks: vi.fn(),
  createTask: vi.fn(),
  getTask: vi.fn(),
  listSteps: vi.fn(),
  retryTask: vi.fn(),
  cancelTask: vi.fn(),
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
  pluginsApi: { detail: mocks.detail },
  agentApi: {
    listTasks: mocks.listTasks,
    createTask: mocks.createTask,
    getTask: mocks.getTask,
    listSteps: mocks.listSteps,
    retryTask: mocks.retryTask,
    cancelTask: mocks.cancelTask,
  },
}));

// 子组件用最小替身：回显关键 props 并回放回调（表单状态机走真实 hook）
vi.mock('@/components/workbench/declaration-form', async () => {
  const react = await import('react');
  return {
    DeclarationForm: (props: {
      inputs: Array<{ key: string; required?: boolean }>;
      submitLabel: string;
      onChange: (key: string, value: string) => void;
      onSubmit: () => void;
    }) =>
      react.createElement(
        'div',
        null,
        `表单|提交按钮${props.submitLabel}`,
        react.createElement(
          'button',
          {
            type: 'button',
            onClick: () => {
              for (const input of props.inputs) {
                if (input.required) props.onChange(input.key, '已填');
              }
            },
          },
          '模拟填必填项',
        ),
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onSubmit() },
          '模拟提交',
        ),
      ),
  };
});

vi.mock('@/components/workbench/confirm-bar', async () => {
  const react = await import('react');
  return {
    ConfirmBar: (props: {
      open: boolean;
      onContinue: () => void;
      onRedo: () => void;
      onCancel: () => void;
    }) =>
      props.open
        ? react.createElement(
            'div',
            null,
            '确认条',
            react.createElement(
              'button',
              { type: 'button', onClick: props.onContinue },
              '模拟继续',
            ),
            react.createElement('button', { type: 'button', onClick: props.onRedo }, '模拟重做'),
            react.createElement('button', { type: 'button', onClick: props.onCancel }, '模拟放弃'),
          )
        : null,
  };
});

vi.mock('@/components/workbench/submit-bar', async () => {
  const react = await import('react');
  return {
    SubmitBar: (props: {
      status: string;
      queuePosition: number | undefined;
      taskId: string;
      onCancel: () => void;
    }) =>
      react.createElement(
        'div',
        null,
        `提交条|状态${props.status}|排队${String(props.queuePosition)}|任务${props.taskId}`,
        react.createElement(
          'button',
          { type: 'button', onClick: () => props.onCancel() },
          '模拟取消',
        ),
      ),
  };
});

vi.mock('@/components/workbench/help-panel', async () => {
  const react = await import('react');
  return {
    HelpPanel: (props: { recentTasks: unknown[]; loading: boolean }) =>
      react.createElement(
        'div',
        null,
        `帮助栏|最近${props.recentTasks.length}|加载中${String(props.loading)}`,
      ),
  };
});

/** 地址栏参数（?goal=）用假 location 替身 */
interface FakeLocation {
  origin: string;
  pathname: string;
  search: string;
  hash: string;
  assign: ReturnType<typeof vi.fn>;
}

function installFakeLocation(search = ''): FakeLocation {
  const fake: FakeLocation = {
    origin: 'http://localhost:3000',
    pathname: '/workbench/plg_weekly',
    search,
    hash: '',
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
  vi.useRealTimers();
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

/** 等异步链路的 DOM 落盘（act 窗口内睡眠、窗口外断言） */
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
  capabilities: [],
  inputs: [
    { key: 'period', label: '汇报周期', type: 'text', required: true },
    { key: 'focus', label: '本周重点', type: 'textarea', required: false },
  ],
  step_titles: ['整理本周要点', '生成周报初稿'],
  outputs: [
    { key: 'report', label: '周报文档', type: 'file' },
    { key: 'share', label: '', type: 'link' },
    { key: 'grid', label: '数据表', type: 'table' },
    { key: 'note', label: '备注', type: 'text' },
  ],
  settings_schema: [],
  versions: [],
  installed_status: 'enabled',
  installed_version: '2.0.0',
  has_update: false,
  installable: true,
  install_block_reason: '',
};

const STEPS = {
  steps: [
    {
      step_id: 's1',
      sequence: 1,
      title: '整理本周要点',
      status: 'completed',
      output_summary: '要点 3 条',
    },
  ],
};

/** 显式传 null 表示「空体」 */
function pick(key: string, fallback: unknown, overrides: Record<string, unknown>): unknown {
  return key in overrides ? overrides[key] : fallback;
}

function mockWorkbenchData(
  overrides: { plugin?: unknown; tasks?: unknown; steps?: unknown; task?: unknown } = {},
): void {
  mocks.detail.mockResolvedValue(pick('plugin', PLUGIN, overrides));
  mocks.listTasks.mockResolvedValue(
    pick('tasks', { items: [], page: 1, page_size: 3, total: 0, total_pages: 1 }, overrides),
  );
  mocks.listSteps.mockResolvedValue(pick('steps', STEPS, overrides));
  // 提交后 taskQuery 会拉任务详情：给终态默认值，避免 react-query 报 undefined 数据
  mocks.getTask.mockResolvedValue(
    pick('task', { task_id: 'task_1', status: 'completed', progress: 100 }, overrides),
  );
}

describe('岗位工作台', () => {
  it('buildSections 按声明推导区块，缺什么少什么', () => {
    const full = { ...PLUGIN } as never as Parameters<typeof buildSections>[0];
    expect(buildSections(full)).toEqual(['form', 'steps', 'outputs']);

    const noInputs = { ...PLUGIN, inputs: [] } as never as Parameters<typeof buildSections>[0];
    expect(buildSections(noInputs)).toEqual(['steps', 'outputs']);

    const bare = {
      ...PLUGIN,
      inputs: [],
      step_titles: [],
      outputs: [],
    } as never as Parameters<typeof buildSections>[0];
    expect(buildSections(bare)).toEqual([]);
  });

  it('加载中给提示；未添加（16003）给专门空态', async () => {
    const { BusinessError } = await import('@/lib/api-client');

    mockWorkbenchData();
    mocks.detail.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<WorkbenchPage />);
    expect(loading.container.textContent).toContain('正在打开工作台…');
    loading.unmount();

    mockWorkbenchData();
    mocks.detail.mockRejectedValue(new BusinessError(16003, '插件未添加'));
    const notInstalled = renderWithClient(<WorkbenchPage />);
    await waitForText(notInstalled.container, '该插件还没有添加');
    const addLink = Array.from(notInstalled.container.querySelectorAll('a')).find((item) =>
      item.textContent?.includes('去添加'),
    );
    expect(addLink?.getAttribute('href')).toBe('/plugins/plg_weekly');
    notInstalled.unmount();
  });

  it('普通失败可原地重试；详情为空给没有找到空态', async () => {
    mockWorkbenchData();
    mocks.detail.mockRejectedValue(new Error('插件服务暂时不可用'));
    const failed = renderWithClient(<WorkbenchPage />);
    await waitForText(failed.container, '插件服务暂时不可用');
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.detail).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();

    mockWorkbenchData({ plugin: null });
    const empty = renderWithClient(<WorkbenchPage />);
    await waitForText(empty.container, '没有找到这个工作台');
    empty.unmount();
  });

  it('未安装的插件进不去工作台，引导去添加', async () => {
    mockWorkbenchData({
      plugin: { ...PLUGIN, installed_status: 'none' },
    });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '该插件还没有添加');
    expect(container.textContent).toContain('先到插件市场添加，添加后即可在这里发起任务');
    unmount();
  });

  it('头部给返回与管理入口，简介缺失给占位', async () => {
    mockWorkbenchData({ plugin: { ...PLUGIN, summary: '' } });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '周报助手');

    expect(container.textContent).toContain('按下面填写后即可开始');
    const links = Array.from(container.querySelectorAll('a'));
    expect(
      links.find((item) => item.textContent?.includes('返回插件详情'))?.getAttribute('href'),
    ).toBe('/plugins/plg_weekly');
    expect(links.find((item) => item.textContent?.includes('管理插件'))?.getAttribute('href')).toBe(
      '/plugins/plg_weekly',
    );
    unmount();
  });

  it('已停用的插件给提示条', async () => {
    mockWorkbenchData({ plugin: { ...PLUGIN, installed_status: 'disabled' } });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '该插件已停用，请联系管理员启用后再发起任务。');
    unmount();
  });

  it('必填没填好拦截提交并提示标红', async () => {
    mockWorkbenchData();
    mocks.createTask.mockResolvedValue({ task_id: 'task_1', status: 'running' });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '表单|提交按钮开始执行');

    click(findButton(container, '模拟提交'));
    await act(async () => {});
    expect(mocks.toast.error).toHaveBeenCalledWith('还有必填项没填好，请检查标红的字段');
    expect(mocks.createTask).not.toHaveBeenCalled();
    unmount();
  });

  it('提交按声明拼装任务参数，goal 用页面默认口径', async () => {
    mockWorkbenchData();
    mocks.createTask.mockResolvedValue({ task_id: 'task_1', status: 'running' });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '表单|提交按钮开始执行');

    click(findButton(container, '模拟填必填项'));
    click(findButton(container, '模拟提交'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createTask).toHaveBeenCalledWith({
          conversation_id: 'conv_plg_weekly',
          goal: '周报助手 · 手动发起',
          plugin_id: 'plg_weekly',
          // 声明里的全部输入都随任务下发：必填已填，选填缺省为空串
          inputs: { period: '已填', focus: '' },
        });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('任务已发起，正在执行');
      });
    });
    unmount();
  });

  it('从对话页带 goal 进来即预填并随任务下发', async () => {
    installFakeLocation('?goal=%E6%95%B4%E7%90%86%E5%91%A8%E6%8A%A5');
    mockWorkbenchData();
    mocks.createTask.mockResolvedValue({ task_id: 'task_1', status: 'queued', queue_position: 1 });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '表单|提交按钮开始执行');

    click(findButton(container, '模拟填必填项'));
    click(findButton(container, '模拟提交'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.createTask).toHaveBeenCalledWith(
          expect.objectContaining({ goal: '整理周报' }),
        );
      });
    });
    unmount();
  });

  it('排队文案按位置分档', async () => {
    mockWorkbenchData();
    // 提交条只在任务处于进行中时回显排队位置，故 getTask 给排队态
    mocks.getTask.mockResolvedValue({ task_id: 'task_1', status: 'queued', progress: 0 });
    mocks.createTask.mockResolvedValue({ task_id: 'task_1', status: 'queued', queue_position: 3 });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '表单|提交按钮开始执行');

    click(findButton(container, '模拟填必填项'));
    click(findButton(container, '模拟提交'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith(
          '已排队，前面还有 2 个任务在跑，稍后自动开始',
        );
      });
    });
    // 提交条回显排队位置
    expect(container.textContent).toContain('排队3');
    unmount();
  });

  it('待确认是任务状态，三条动作各有出口', async () => {
    mockWorkbenchData();
    mocks.createTask.mockResolvedValue({ task_id: 'task_1', status: 'running' });
    mocks.getTask.mockResolvedValue({
      task_id: 'task_1',
      status: 'waiting_confirm',
      progress: 50,
      confirm: { step_seq: 2, title: '确认初稿' },
    });
    mocks.listSteps.mockResolvedValue(STEPS);
    mocks.retryTask.mockResolvedValue({ task_id: 'task_1', from_step: 2 });
    mocks.cancelTask.mockResolvedValue({ task_id: 'task_1', status: 'cancelled' });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '表单|提交按钮开始执行');

    click(findButton(container, '模拟填必填项'));
    click(findButton(container, '模拟提交'));
    await waitForText(container, '确认条');

    click(findButton(container, '模拟继续'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.retryTask).toHaveBeenCalledWith('task_1', { decision: 'continue' });
      });
    });
    click(findButton(container, '模拟放弃'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.cancelTask).toHaveBeenCalledWith('task_1');
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已放弃本次执行');
      });
    });
    unmount();
  });

  it('进行中才轮询，进终态停并刷新最近执行', async () => {
    vi.useFakeTimers();
    mockWorkbenchData();
    mocks.createTask.mockResolvedValue({ task_id: 'task_1', status: 'running' });
    let nextStatus = 'running';
    mocks.getTask.mockImplementation(() =>
      Promise.resolve({
        task_id: 'task_1',
        status: nextStatus,
        progress: nextStatus === 'completed' ? 100 : 10,
      }),
    );
    mocks.listSteps.mockResolvedValue(STEPS);

    // 假定时器推进必须切短窗：一个长 act 窗口里，查询落定的提交要等窗口退出
    // 才落盘（补测第二十四批同款），数据滞后会让「进终态停轮询」看起来失灵。
    const advance = async (ms: number): Promise<void> => {
      for (let elapsed = 0; elapsed < ms; elapsed += 100) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(100);
        });
      }
    };

    const { container, unmount } = renderWithClient(<WorkbenchPage />);

    await advance(20);
    click(findButton(container, '模拟填必填项'));
    click(findButton(container, '模拟提交'));
    await advance(20);
    expect(mocks.getTask).toHaveBeenCalledTimes(1);

    // 进行中：3 秒兜底轮询
    await advance(3600);
    expect(mocks.getTask).toHaveBeenCalledTimes(2);

    // 进终态：状态落盘后立刻停轮询，并刷一次最近执行
    nextStatus = 'completed';
    await advance(7000);
    expect(container.textContent).toContain('提交条|状态completed');
    const after = mocks.getTask.mock.calls.length;
    await advance(9000);
    expect(mocks.getTask.mock.calls.length).toBe(after);
    // 最近执行刷新（初始 1 次 + 终态 1 次）
    expect(mocks.listTasks.mock.calls.length).toBeGreaterThanOrEqual(2);
    unmount();
  });

  it('步骤区三态齐备', async () => {
    mockWorkbenchData();
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '还没有执行记录');
    expect(container.textContent).toContain('发起任务后这里会按步骤展示进度');
    unmount();

    mockWorkbenchData();
    mocks.createTask.mockResolvedValue({ task_id: 'task_1', status: 'running' });
    mocks.getTask.mockResolvedValue({ task_id: 'task_1', status: 'running', progress: 10 });
    mocks.listSteps.mockRejectedValue(new Error('步骤服务暂时不可用'));
    const running = renderWithClient(<WorkbenchPage />);
    await waitForText(running.container, '表单|提交按钮开始执行');
    click(findButton(running.container, '模拟填必填项'));
    click(findButton(running.container, '模拟提交'));
    await waitForText(running.container, '步骤服务暂时不可用');
    running.unmount();
  });

  it('产物区用业务用词，帮助栏收最近任务', async () => {
    mockWorkbenchData({
      tasks: {
        items: [{ task_id: 't1' }, { task_id: 't2' }],
        page: 1,
        page_size: 3,
        total: 2,
        total_pages: 1,
      },
    });
    const { container, unmount } = renderWithClient(<WorkbenchPage />);
    await waitForText(container, '本次会产出');

    expect(container.textContent).toContain('周报文档');
    expect(container.textContent).toContain('share');
    expect(container.textContent).toContain('文件');
    expect(container.textContent).toContain('链接');
    expect(container.textContent).toContain('表格');
    expect(container.textContent).toContain('文本');
    expect(container.textContent).toContain('帮助栏|最近2');
    unmount();
  });
});
