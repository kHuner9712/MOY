/**
 * 前端补测第十三批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：工作流组件群的渲染合同 —— RunStatusBadge 七态文案与排队位次、
 * TaskHeader 关键字段、ChecklistPanel 三态、StepTimeline 映射规则、
 * TaskMetricCards 三态与口径红线、FailureActions 重试语义。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TaskChecklistItem, TaskDetail, TaskStatsData, TaskStep } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { ChecklistPanel } from './checklist-panel';
import { FailureActions } from './failure-actions';
import { RunStatusBadge } from './run-status-badge';
import { StepTimeline } from './step-timeline';
import { TaskHeader } from './task-header';
import { TaskMetricCards } from './task-metric-cards';

const mocks = vi.hoisted(() => ({
  artifactCard: vi.fn((_props: unknown) => null),
  retryTask: vi.fn(),
  tasks: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/components/workflow/artifact-card', () => ({
  ArtifactCard: mocks.artifactCard,
}));

vi.mock('@/components/ui/toast', () => ({ toast: mocks.toast }));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  agentApi: { retryTask: mocks.retryTask },
  statsApi: { tasks: mocks.tasks },
}));

/** 带 QueryClient 的挂载（TaskMetricCards / FailureActions 走 react-query） */
function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

/** 按可见文案点击按钮 */
function clickButton(container: HTMLElement, label: string): void {
  const button = Array.from(container.querySelectorAll('button')).find((item) =>
    item.textContent?.includes(label),
  );
  if (!button) {
    throw new Error(`按钮未找到：${label}`);
  }
  act(() => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

const TASK: TaskDetail = {
  task_id: 't1',
  goal: '写周报',
  status: 'running',
  progress: 40,
  current_step: '生成',
  result: null,
  created_at: '2026-11-01T09:00:00Z',
  updated_at: '2026-11-01T10:00:00Z',
  steps_total: 3,
  retry_of: 't0',
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// RunStatusBadge
// ---------------------------------------------------------------------------

describe('RunStatusBadge', () => {
  it('七态文案逐字（与 DDL 七态一一对应）', () => {
    const labels: Array<[TaskDetail['status'], string]> = [
      ['queued', '排队中'],
      ['pending', '等待开始'],
      ['running', '执行中'],
      ['waiting_confirm', '已暂停，待确认'],
      ['completed', '已完成'],
      ['failed', '未完成'],
      ['cancelled', '已取消'],
    ];
    for (const [status, label] of labels) {
      const { container, unmount } = renderComponent(<RunStatusBadge status={status} />);
      expect(container.textContent).toContain(label);
      unmount();
    }
  });

  it('排队中且位次大于 1 时展示「前面还有 N 个」', () => {
    const { container, unmount } = renderComponent(
      <RunStatusBadge status="queued" queuePosition={3} />,
    );
    expect(container.textContent).toContain('前面还有 2 个');
    unmount();
  });

  it('排在第 1 位时不展示位次', () => {
    const { container, unmount } = renderComponent(
      <RunStatusBadge status="queued" queuePosition={1} />,
    );
    expect(container.textContent).not.toContain('前面还有');
    unmount();
  });

  it('非排队状态不展示位次', () => {
    const { container, unmount } = renderComponent(
      <RunStatusBadge status="running" queuePosition={3} />,
    );
    expect(container.textContent).not.toContain('前面还有');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// TaskHeader
// ---------------------------------------------------------------------------

describe('TaskHeader', () => {
  it('展示任务目标与任务号', () => {
    const { container, unmount } = renderComponent(<TaskHeader task={TASK} />);
    expect(container.textContent).toContain('写周报');
    expect(container.textContent).toContain('t1');
    unmount();
  });

  it('目标为空时占位「未命名任务」', () => {
    const { container, unmount } = renderComponent(<TaskHeader task={{ ...TASK, goal: '' }} />);
    expect(container.textContent).toContain('未命名任务');
    unmount();
  });

  it('展示进度与创建/更新时间', () => {
    const { container, unmount } = renderComponent(<TaskHeader task={TASK} />);
    // Progress 的 label 是无障碍标签（aria），数值本身落在文本里
    expect(container.querySelector('[aria-label="任务进度"]')).not.toBeNull();
    expect(container.textContent).toContain('40%');
    expect(container.textContent).toContain('创建于');
    expect(container.textContent).toContain('更新于');
    unmount();
  });

  it('有总步数时展示「共 N 步」', () => {
    const { container, unmount } = renderComponent(<TaskHeader task={TASK} />);
    expect(container.textContent).toContain('共 3 步');
    unmount();
  });

  it('重试任务展示来源任务号', () => {
    const { container, unmount } = renderComponent(<TaskHeader task={TASK} />);
    expect(container.textContent).toContain('由任务 t0 重试而来');
    unmount();
  });

  it('排队任务把位次交给状态徽标', () => {
    const { container, unmount } = renderComponent(
      <TaskHeader task={{ ...TASK, status: 'queued' }} queuePosition={4} />,
    );
    expect(container.textContent).toContain('排队中');
    expect(container.textContent).toContain('前面还有 3 个');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// ChecklistPanel
// ---------------------------------------------------------------------------

describe('ChecklistPanel', () => {
  it('空清单展示空态说明', () => {
    const { container, unmount } = renderComponent(<ChecklistPanel items={[]} />);
    expect(container.textContent).toContain('本次没有核查清单');
    unmount();
  });

  it('三种状态各按业务文案展示', () => {
    const items: TaskChecklistItem[] = [
      { key: 'k1', name: '周报正文', source_step: 1, status: 'checked' },
      { key: 'k2', name: '数据核对', source_step: 2, status: 'pending' },
      { key: 'k3', name: '附件齐备', source_step: 3, status: 'unchecked' },
    ];
    const { container, unmount } = renderComponent(<ChecklistPanel items={items} />);
    expect(container.textContent).toContain('已核查');
    expect(container.textContent).toContain('待人工确认');
    expect(container.textContent).toContain('未核查');
    unmount();
  });

  it('名称为空时占位，来源步骤按「第 N 步」展示', () => {
    const { container, unmount } = renderComponent(
      <ChecklistPanel items={[{ key: 'k1', name: '', source_step: 2, status: 'checked' }]} />,
    );
    expect(container.textContent).toContain('未命名核查项');
    expect(container.textContent).toContain('第 2 步');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// StepTimeline
// ---------------------------------------------------------------------------

const STEPS: TaskStep[] = [
  {
    step_id: 's1',
    sequence: 1,
    title: '收集数据',
    status: 'completed',
    started_at: '2026-11-01T09:00:00Z',
    finished_at: '2026-11-01T09:10:00Z',
    output_summary: '已收集 3 份周报素材',
    artifacts: [
      {
        artifact_id: 'a1',
        type: 'draft',
        name: '周报草稿',
      },
    ],
  },
  {
    step_id: 's2',
    sequence: 2,
    title: '生成周报',
    status: 'failed',
    started_at: '2026-11-01T09:10:00Z',
    finished_at: '2026-11-01T09:12:00Z',
    output_summary: null,
    failure: {
      reason_text: '模型服务超时',
      retryable: true,
      operator_action: '可重试本步',
    },
  },
];

describe('StepTimeline', () => {
  it('空步骤展示空提示', () => {
    const { container, unmount } = renderWithClient(<StepTimeline taskId="t1" steps={[]} />);
    expect(container.textContent).toContain('任务刚开始，步骤马上就会出现');
    unmount();
  });

  it('标题常显，摘要在展开详情后才渲染（默认只展开失败步骤）', () => {
    const { container, unmount } = renderWithClient(<StepTimeline taskId="t1" steps={STEPS} />);
    expect(container.textContent).toContain('收集数据');
    expect(container.textContent).not.toContain('已收集 3 份周报素材');

    clickButton(container, '详情');
    expect(container.textContent).toContain('已收集 3 份周报素材');
    unmount();
  });

  it('步骤产物交给 ArtifactCard 渲染（展开详情后）', () => {
    const { container, unmount } = renderWithClient(<StepTimeline taskId="t1" steps={STEPS} />);
    clickButton(container, '详情');
    // 直接检查首参 props（不依赖调用参数个数）
    const props = mocks.artifactCard.mock.calls[0]?.[0] as {
      taskId: string;
      artifact: { artifact_id: string };
    };
    expect(props.taskId).toBe('t1');
    expect(props.artifact.artifact_id).toBe('a1');
    unmount();
  });

  it('失败步骤渲染 FailureActions，成功步骤不渲染', () => {
    const { container, unmount } = renderWithClient(<StepTimeline taskId="t1" steps={STEPS} />);
    const buttons = Array.from(container.querySelectorAll('button')).map(
      (button) => button.textContent ?? '',
    );
    expect(buttons.some((text) => text.includes('重试本步'))).toBe(true);
    // 两个步骤里只有失败步骤带动作区
    expect(buttons.filter((text) => text.includes('从头重试'))).toHaveLength(1);
    unmount();
  });

  it('失败步骤可收起再展开（手动切换优先于默认策略）', () => {
    // 只渲染失败步骤，避免与其它步骤的同名按钮混淆
    const { container, unmount } = renderWithClient(
      <StepTimeline taskId="t1" steps={[STEPS[1]!]} />,
    );
    // 默认展开：失败原因可见
    expect(container.textContent).toContain('模型服务超时');

    clickButton(container, '收起');
    expect(container.textContent).not.toContain('模型服务超时');

    clickButton(container, '详情');
    expect(container.textContent).toContain('模型服务超时');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// TaskMetricCards
// ---------------------------------------------------------------------------

const STATS: TaskStatsData = {
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

describe('TaskMetricCards', () => {
  it('四张卡标题逐字带「工作流任务」限定词', async () => {
    mocks.tasks.mockResolvedValue(STATS);
    const { container, unmount } = renderWithClient(<TaskMetricCards />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('工作流任务完成率');
      });
    });
    expect(container.textContent).toContain('工作流任务平均完成时长');
    expect(container.textContent).toContain('工作流任务重复使用率');
    expect(container.textContent).toContain('工作流任务单位成功用量');
    unmount();
  });

  it('完成率分母剔除用户主动取消', async () => {
    mocks.tasks.mockResolvedValue(STATS);
    const { container, unmount } = renderWithClient(<TaskMetricCards />);

    await act(async () => {
      await vi.waitFor(() => {
        // 分母 = total(20) - cancelled(2) = 18
        expect(container.textContent).toContain('已完成 14 / 18');
      });
    });
    unmount();
  });

  it('样本少于 10 个时展示小样本提示', async () => {
    mocks.tasks.mockResolvedValue({ ...STATS, total: 5 });
    const { container, unmount } = renderWithClient(<TaskMetricCards />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('样本还少，仅供参考（当前样本 5 个任务）');
      });
    });
    unmount();
  });

  it('样本充足时不展示小样本提示', async () => {
    mocks.tasks.mockResolvedValue(STATS);
    const { container, unmount } = renderWithClient(<TaskMetricCards />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('工作流任务完成率');
      });
    });
    expect(container.textContent).not.toContain('样本还少');
    unmount();
  });

  it('加载中展示骨架而非卡片标题', () => {
    mocks.tasks.mockReturnValue(new Promise(() => undefined));
    const { container, unmount } = renderWithClient(<TaskMetricCards />);
    expect(container.textContent).toContain('统计期：近 7 天');
    expect(container.textContent).not.toContain('工作流任务完成率');
    unmount();
  });

  it('失败时展示业务文案与重试入口', async () => {
    mocks.tasks.mockRejectedValue(new Error('统计服务不可用'));
    const { container, unmount } = renderWithClient(<TaskMetricCards />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('重试');
      });
    });
    expect(container.textContent).not.toContain('工作流任务完成率');
    unmount();
  });

  it('切换统计期后按新窗口取数', async () => {
    mocks.tasks.mockResolvedValue(STATS);
    const { container, unmount } = renderWithClient(<TaskMetricCards />);

    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.tasks).toHaveBeenCalledWith('7d');
      });
    });

    await act(async () => {
      clickButton(container, '近 30 天');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.tasks).toHaveBeenCalledWith('30d');
      });
    });
    expect(container.textContent).toContain('统计期：近 30 天');
    unmount();
  });
});

// ---------------------------------------------------------------------------
// FailureActions
// ---------------------------------------------------------------------------

const FAILED_STEP: TaskStep = {
  step_id: 's2',
  sequence: 2,
  title: '生成周报',
  status: 'failed',
  started_at: '2026-11-01T09:10:00Z',
  finished_at: '2026-11-01T09:12:00Z',
  output_summary: null,
  failure: {
    reason_text: '模型服务超时',
    retryable: true,
    operator_action: '可重试本步',
  },
};

describe('FailureActions', () => {
  it('渲染三个动作按钮', () => {
    const { container, unmount } = renderWithClient(
      <FailureActions taskId="t1" step={FAILED_STEP} />,
    );
    expect(container.textContent).toContain('重试本步');
    expect(container.textContent).toContain('从头重试');
    expect(container.textContent).toContain('复制诊断说明');
    unmount();
  });

  it('不可重试的步骤禁用「重试本步」', () => {
    const step: TaskStep = {
      ...FAILED_STEP,
      failure: { reason_text: '参数缺失', retryable: false, operator_action: '补充内容' },
    };
    const { container, unmount } = renderWithClient(<FailureActions taskId="t1" step={step} />);
    const button = Array.from(container.querySelectorAll('button')).find((item) =>
      item.textContent?.includes('重试本步'),
    );
    expect(button?.disabled).toBe(true);
    unmount();
  });

  it('重试本步按当前步骤序号发起', async () => {
    mocks.retryTask.mockResolvedValue({ task_id: 't2', from_step: 2 });
    const { container, unmount } = renderWithClient(
      <FailureActions taskId="t1" step={FAILED_STEP} />,
    );

    await act(async () => {
      clickButton(container, '重试本步');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.retryTask).toHaveBeenCalledWith('t1', { from_step: 2 });
      });
    });
    unmount();
  });

  it('从头重试从第 1 步发起', async () => {
    mocks.retryTask.mockResolvedValue({ task_id: 't2', from_step: 1 });
    const { container, unmount } = renderWithClient(
      <FailureActions taskId="t1" step={FAILED_STEP} />,
    );

    await act(async () => {
      clickButton(container, '从头重试');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.retryTask).toHaveBeenCalledWith('t1', { from_step: 1 });
      });
    });
    unmount();
  });

  it('复制诊断成功后提示可发给管理员', async () => {
    const writeText = vi.fn(async (_text: string) => undefined);
    Object.defineProperty(window.navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    const { container, unmount } = renderWithClient(
      <FailureActions taskId="t1" step={FAILED_STEP} />,
    );

    await act(async () => {
      clickButton(container, '复制诊断说明');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.success).toHaveBeenCalled();
      });
    });
    const copied = writeText.mock.calls[0]?.[0] as string;
    expect(copied).toContain('t1');
    expect(copied).toContain('模型服务超时');
    // 诊断文本不含错误码与堆栈
    expect(copied).not.toContain('30001');
    unmount();
  });

  it('复制失败时提示手动复制', async () => {
    const writeText = vi.fn(async () => Promise.reject(new Error('无剪贴板权限')));
    Object.defineProperty(window.navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    const { container, unmount } = renderWithClient(
      <FailureActions taskId="t1" step={FAILED_STEP} />,
    );

    await act(async () => {
      clickButton(container, '复制诊断说明');
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(mocks.toast.error).toHaveBeenCalledWith('复制失败，请手动选择文本复制');
      });
    });
    unmount();
  });
});
