/**
 * 前端补测第二十八批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：任务详情页（P-12）的编排合同 ——
 * 三态与两种特殊错误（12001 任务不存在 / 20003 无权限）、
 * waiting_confirm 的继续/重做/放弃三条动作（任务状态不是错误页）、
 * 降级告警展示、步骤三态、SSE 事件节流合并刷新、
 * 进行中状态才开 5 秒兜底轮询（进终态立刻停）。
 *
 * ConfirmBar / TaskHeader / StepTimeline / ChecklistPanel 的内部合同已由
 * 补测第十三、十八批覆盖，本批用最小替身回显关键 props 并回放回调。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { act, renderComponent } from '@/test/render-hook';

import TaskDetailPage from './page';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
  getTask: vi.fn(),
  listSteps: vi.fn(),
  retryTask: vi.fn(),
  cancelTask: vi.fn(),
  streamTaskEvents: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('next/navigation', () => ({ useParams: () => ({ taskId: 'task_1' }) }));

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
  agentApi: {
    getTask: mocks.getTask,
    listSteps: mocks.listSteps,
    retryTask: mocks.retryTask,
    cancelTask: mocks.cancelTask,
    streamTaskEvents: mocks.streamTaskEvents,
  },
}));

// 子组件用最小替身：回显关键 props 并回放回调
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

vi.mock('@/components/workflow/task-header', async () => {
  const react = await import('react');
  return {
    TaskHeader: (props: { task: { goal: string } }) =>
      react.createElement('div', null, `任务头|${props.task.goal}`),
  };
});

vi.mock('@/components/workflow/step-timeline', async () => {
  const react = await import('react');
  return {
    StepTimeline: (props: { steps: unknown[] }) =>
      react.createElement('div', null, `步骤时间线${props.steps.length}`),
  };
});

vi.mock('@/components/workflow/checklist-panel', async () => {
  const react = await import('react');
  return {
    ChecklistPanel: (props: { items: unknown[] }) =>
      react.createElement('div', null, `核查清单${props.items.length}`),
  };
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

const TASK_DONE = {
  task_id: 'task_1',
  conversation_id: 'conv_1',
  goal: '整理本周周报',
  status: 'completed',
  progress: 100,
  created_at: '2026-10-31T09:00:00Z',
  updated_at: '2026-10-31T10:00:00Z',
  plugin_id: 'plg_weekly',
  warning_count: 2,
  confirm: null,
  result: {
    checklist: [{ seq: 1, title: '周报已生成', status: 'done', note: '' }],
    warnings: [
      { step_seq: 1, message: '部分对话未能归档' },
      { step_seq: 3, message: '' },
    ],
  },
};

const STEPS = {
  steps: [
    { step_id: 's1', sequence: 1, title: '整理本周要点', status: 'completed' },
    { step_id: 's2', sequence: 2, title: '生成周报初稿', status: 'completed' },
  ],
};

/** 显式传 null 表示「空体」 */
function pick(key: string, fallback: unknown, overrides: Record<string, unknown>): unknown {
  return key in overrides ? overrides[key] : fallback;
}

function mockTaskData(overrides: { task?: unknown; steps?: unknown } = {}): void {
  mocks.getTask.mockResolvedValue(pick('task', TASK_DONE, overrides));
  mocks.listSteps.mockResolvedValue(pick('steps', STEPS, overrides));
  mocks.streamTaskEvents.mockReturnValue(() => {});
}

describe('任务详情页', () => {
  it('加载中给提示；任务不存在与无权限各有专门空态', async () => {
    const { BusinessError } = await import('@/lib/api-client');

    mockTaskData();
    mocks.getTask.mockImplementation(() => new Promise(() => {}));
    const loading = renderWithClient(<TaskDetailPage />);
    expect(loading.container.textContent).toContain('正在加载任务详情…');
    loading.unmount();

    mockTaskData();
    mocks.getTask.mockRejectedValue(new BusinessError(12001, '任务不存在'));
    const gone = renderWithClient(<TaskDetailPage />);
    await waitForText(gone.container, '任务不存在或已过期');
    expect(gone.container.textContent).not.toContain('你没有权限查看该任务');
    gone.unmount();

    mockTaskData();
    mocks.getTask.mockRejectedValue(new BusinessError(20003, '无权限'));
    const forbidden = renderWithClient(<TaskDetailPage />);
    await waitForText(forbidden.container, '你没有权限查看该任务');
    expect(forbidden.container.textContent).not.toContain('任务不存在或已过期');
    forbidden.unmount();
  });

  it('普通失败可原地重试', async () => {
    mockTaskData();
    mocks.getTask.mockRejectedValue(new Error('任务服务暂时不可用'));
    const { container, unmount } = renderWithClient(<TaskDetailPage />);

    await waitForText(container, '任务服务暂时不可用');
    click(findButton(container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.getTask).toHaveBeenCalledTimes(2);
      });
    });
    unmount();
  });

  it('waiting_confirm 是任务状态不是错误页，三条动作各有出口', async () => {
    mockTaskData({
      task: {
        ...TASK_DONE,
        status: 'waiting_confirm',
        confirm: { step_seq: 2, title: '确认周报初稿' },
      },
    });
    mocks.retryTask.mockResolvedValue({ task_id: 'task_1', from_step: 2 });
    mocks.cancelTask.mockResolvedValue({ task_id: 'task_1', status: 'cancelled' });
    const { container, unmount } = renderWithClient(<TaskDetailPage />);
    await waitForText(container, '确认条');

    // 任务主体照常展示，不渲染成错误
    expect(container.textContent).toContain('任务头|整理本周周报');
    expect(container.textContent).not.toContain('本次登录未完成');

    click(findButton(container, '模拟继续'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.retryTask).toHaveBeenCalledWith('task_1', { decision: 'continue' });
      });
    });
    click(findButton(container, '模拟重做'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.retryTask).toHaveBeenCalledWith('task_1', { decision: 'redo' });
      });
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalledWith('已提交你的选择');
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

  it('降级告警按步骤列出，空消息给占位', async () => {
    mockTaskData();
    const { container, unmount } = renderWithClient(<TaskDetailPage />);
    await waitForText(container, '本次执行有 2 处需要你留意');

    expect(container.textContent).toContain('第 1 步：部分对话未能归档');
    // 空消息回落占位，不留空行
    expect(container.textContent).toContain('第 3 步：—');
    unmount();
  });

  it('步骤区三态齐备，清单随任务结果展示', async () => {
    mockTaskData();
    mocks.listSteps.mockRejectedValue(new Error('步骤服务暂时不可用'));
    const failed = renderWithClient(<TaskDetailPage />);
    await waitForText(failed.container, '步骤服务暂时不可用');
    click(findButton(failed.container, '重试'));
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.listSteps).toHaveBeenCalledTimes(2);
      });
    });
    failed.unmount();

    mockTaskData();
    const ok = renderWithClient(<TaskDetailPage />);
    await waitForText(ok.container, '步骤时间线2');
    expect(ok.container.textContent).toContain('核查清单1');
    ok.unmount();
  });

  it('SSE 事件密集时合并为一次刷新', async () => {
    vi.useFakeTimers();
    mockTaskData();
    const { unmount } = renderWithClient(<TaskDetailPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });

    const handlers = mocks.streamTaskEvents.mock.calls[0]?.[1] as {
      onStepStarted: () => void;
      onStepFinished: () => void;
    };
    expect(mocks.streamTaskEvents).toHaveBeenCalledWith('task_1', expect.any(Object));

    // 后端一次性重放多个步骤事件 → 300ms 节流窗口内只补一次数据
    act(() => {
      handlers.onStepStarted();
      handlers.onStepFinished();
      handlers.onStepStarted();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(mocks.getTask).toHaveBeenCalledTimes(2);
    expect(mocks.listSteps).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('进行中开 5 秒兜底轮询，进终态立刻停', async () => {
    vi.useFakeTimers();
    mockTaskData({ task: { ...TASK_DONE, status: 'running' } });
    const { unmount } = renderWithClient(<TaskDetailPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(mocks.getTask).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(mocks.getTask).toHaveBeenCalledTimes(2);
    unmount();

    // 终态不轮询（清掉上半段的调用计数，只看本段）
    vi.useFakeTimers();
    mocks.getTask.mockClear();
    mockTaskData();
    const done = renderWithClient(<TaskDetailPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(mocks.getTask).toHaveBeenCalledTimes(1);
    done.unmount();
  });
});
