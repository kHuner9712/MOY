/**
 * 前端补测第十六批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：对话域组件的渲染合同 —— ConversationSidebar 会话切换与空态、
 * CitationDrawer 三种引用情况（可回链拉原文 / 旧版本降级 / 缺定位降级）。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CapabilityCard, Citation, GenerateRequest, SegmentDetail } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { useConversationStore } from '@/hooks/use-conversation-store';
import { CitationDrawer, STALE_HINT } from './citation-drawer';
import ChatWindow from './chat-window';
import ConversationSidebar from './conversation-sidebar';
import { GoalLauncher } from './goal-launcher';

const mocks = vi.hoisted(() => ({
  segmentDetail: vi.fn(),
  recommend: vi.fn(),
  stream: vi.fn(),
  reportStreamFailure: vi.fn(),
  push: vi.fn(),
}));

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeExtApi: { segmentDetail: mocks.segmentDetail },
  capabilitiesApi: { recommend: mocks.recommend },
  modelApi: { stream: mocks.stream },
}));

vi.mock('@/lib/error-guard', () => ({ reportStreamFailure: mocks.reportStreamFailure }));

vi.mock('next/navigation', () => ({
  usePathname: () => '/chat',
  useRouter: () => ({ push: mocks.push }),
}));

import { capabilitiesApi, knowledgeExtApi } from '@/lib/api-client';

function renderWithClient(element: ReactNode): ReturnType<typeof renderComponent> {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return renderComponent(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

// ---------------------------------------------------------------------------
// ConversationSidebar
// ---------------------------------------------------------------------------

describe('ConversationSidebar', () => {
  beforeEach(() => {
    useConversationStore.setState({
      conversationId: 'conv_A',
      conversations: [],
      messages: [],
    });
  });

  it('无历史对话时给空态', () => {
    const { container, unmount } = renderComponent(<ConversationSidebar />);
    expect(container.textContent).toContain('暂无历史对话');
    unmount();
  });

  it('渲染会话列表，当前会话高亮', () => {
    useConversationStore.setState({
      conversationId: 'conv_A',
      conversations: [
        { conversation_id: 'conv_A', title: '周报讨论', updated_at: '', message_count: 3 },
        { conversation_id: 'conv_B', title: '合同审查', updated_at: '', message_count: 1 },
      ],
    });
    const { container, unmount } = renderComponent(<ConversationSidebar />);
    expect(container.textContent).toContain('周报讨论');
    expect(container.textContent).toContain('合同审查');

    const items = Array.from(container.querySelectorAll('div')).filter((el) =>
      el.className.includes('rounded-md'),
    );
    const current = items.find((el) => el.textContent === '周报讨论');
    expect(current?.className).toContain('bg-primary/10');
    unmount();
  });

  it('新建对话清空消息并换会话 ID', () => {
    useConversationStore.setState({
      conversationId: 'conv_A',
      conversations: [],
      messages: [{ id: 'm1', role: 'user', content: '旧消息' }],
    });
    const { container, unmount } = renderComponent(<ConversationSidebar />);

    act(() => {
      const button = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('新建对话'),
      );
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const state = useConversationStore.getState();
    expect(state.conversationId).not.toBe('conv_A');
    expect(state.messages).toEqual([]);
    unmount();
  });
});

// ---------------------------------------------------------------------------
// CitationDrawer
// ---------------------------------------------------------------------------

const CITATION: Citation = {
  document_id: 'doc_1',
  document_name: '员工手册.pdf',
  segment_content: '年假按工龄计算',
  score: 0,
  base_id: 'kb_1',
  base_name: '制度库',
  segment_id: 'seg_1',
  position: { page: 3, paragraph: 2 },
};

const DETAIL: SegmentDetail = {
  segment_id: 'seg_1',
  document_id: 'doc_1',
  document_name: '员工手册.pdf',
  base_id: 'kb_1',
  base_name: '制度库',
  content: '年假按工龄计算，满一年 5 天',
  context_before: '第一章 总则',
  context_after: '第二章 考勤',
  position: { page: 3, paragraph: 2 },
  status: 'valid',
};

describe('CitationDrawer', () => {
  it('未选择引用时给空态引导', () => {
    const { unmount } = renderWithClient(
      <CitationDrawer open onOpenChange={vi.fn()} citation={null} />,
    );
    expect(document.body.textContent).toContain('还没有选择引用');
    unmount();
  });

  it('可回链引用拉取原文并展示前后文与位置', async () => {
    vi.mocked(knowledgeExtApi.segmentDetail).mockResolvedValue(DETAIL);
    const { unmount } = renderWithClient(
      <CitationDrawer open onOpenChange={vi.fn()} citation={CITATION} />,
    );

    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('年假按工龄计算，满一年 5 天');
      });
    });
    expect(document.body.textContent).toContain('第一章 总则');
    expect(document.body.textContent).toContain('第二章 考勤');
    expect(document.body.textContent).toContain('第 3 页 · 第 2 段');
    unmount();
  });

  it('旧版本引用展示降级提示（PRD 原文不得改写）', async () => {
    vi.mocked(knowledgeExtApi.segmentDetail).mockResolvedValue({
      ...DETAIL,
      status: 'stale',
    });
    const { unmount } = renderWithClient(
      <CitationDrawer open onOpenChange={vi.fn()} citation={CITATION} />,
    );

    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain(STALE_HINT);
      });
    });
    expect(STALE_HINT).toBe('该引用来自旧版本内容，原文已更新');
    // 降级仍展示旧内容
    expect(document.body.textContent).toContain('年假按工龄计算，满一年 5 天');
    unmount();
  });

  it('缺定位信息的历史引用直接展示片段并引导打开知识库', () => {
    const legacy: Citation = {
      document_id: '',
      document_name: '',
      segment_content: '旧回答里的片段摘要',
      score: 0,
    };
    const { unmount } = renderWithClient(
      <CitationDrawer open onOpenChange={vi.fn()} citation={legacy} />,
    );
    expect(document.body.textContent).toContain('旧回答里的片段摘要');
    expect(document.body.textContent).toContain('未记录定位信息');
    // 不发起回链请求
    expect(knowledgeExtApi.segmentDetail).not.toHaveBeenCalled();
    unmount();
  });

  it('拉取失败时给业务文案与重试入口', async () => {
    vi.mocked(knowledgeExtApi.segmentDetail).mockRejectedValue(new Error('片段服务不可用'));
    const { unmount } = renderWithClient(
      <CitationDrawer open onOpenChange={vi.fn()} citation={CITATION} />,
    );

    await act(async () => {
      await vi.waitFor(() => {
        expect(document.body.textContent).toContain('重试');
      });
    });
    unmount();
  });
});

// ---------------------------------------------------------------------------
// GoalLauncher
// ---------------------------------------------------------------------------

const CAP_CARD: CapabilityCard = {
  id: 'plg_weekly',
  kind: 'plugin',
  name: '周报助手',
  summary: '自动整理周报',
  category: 'efficiency',
  score: 0.9,
  matched_terms: ['周报', '本周'],
  matched_fields: ['name', 'summary'],
  inputs: [
    { key: 'period', label: '汇报周期', type: 'text', required: true },
    { key: 'focus', label: '本周重点', type: 'textarea', required: false },
  ],
};

describe('GoalLauncher', () => {
  it('空目标给引导与示例，不发起请求', async () => {
    const { container, unmount } = renderWithClient(<GoalLauncher />);
    expect(container.textContent).toContain('说说你想完成的事');
    expect(container.textContent).toContain('整理本周周报');

    await act(async () => {});
    expect(capabilitiesApi.recommend).not.toHaveBeenCalled();
    unmount();
  });

  it('输入防抖 300ms 后发起推荐', async () => {
    vi.useFakeTimers();
    vi.mocked(capabilitiesApi.recommend).mockResolvedValue({
      goal: '整理周报',
      matched: true,
      fallback: false,
      fallback_hint: '',
      items: [CAP_CARD],
    } as never);
    const { container, unmount } = renderWithClient(<GoalLauncher />);

    const input = container.querySelector<HTMLInputElement>('input[aria-label="输入你想完成的事"]');
    act(() => {
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        setter?.call(input, '整理周报');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    expect(capabilitiesApi.recommend).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(capabilitiesApi.recommend).toHaveBeenCalledWith('整理周报', 3);
    unmount();
    vi.useRealTimers();
  });

  it('点击示例立即推荐（不等防抖）', async () => {
    vi.mocked(capabilitiesApi.recommend).mockResolvedValue({
      goal: '整理本周周报',
      matched: true,
      fallback: false,
      fallback_hint: '',
      items: [],
    } as never);
    const { container, unmount } = renderWithClient(<GoalLauncher />);

    act(() => {
      const example = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('整理本周周报'),
      );
      example?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(capabilitiesApi.recommend).toHaveBeenCalledWith('整理本周周报', 3);
      });
    });
    unmount();
  });

  it('推荐卡展示命中理由与所需输入', async () => {
    vi.mocked(capabilitiesApi.recommend).mockResolvedValue({
      goal: '整理周报',
      matched: true,
      fallback: false,
      fallback_hint: '',
      items: [CAP_CARD],
    } as never);
    const { container, unmount } = renderWithClient(<GoalLauncher />);

    act(() => {
      const example = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('整理本周周报'),
      );
      example?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('匹配到：周报、本周（来自名称、简介）');
      });
    });
    expect(container.textContent).toContain('需要准备：汇报周期（必填）、本周重点（选填）');
    unmount();
  });

  it('插件卡「开始」跳工作台并携带目标', async () => {
    vi.mocked(capabilitiesApi.recommend).mockResolvedValue({
      goal: '整理周报',
      matched: true,
      fallback: false,
      fallback_hint: '',
      items: [CAP_CARD],
    } as never);
    const { container, unmount } = renderWithClient(<GoalLauncher />);

    act(() => {
      const example = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('整理本周周报'),
      );
      example?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('周报助手');
      });
    });

    act(() => {
      const start = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('开始'),
      );
      start?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(mocks.push).toHaveBeenCalledWith(
      '/workbench/plg_weekly?goal=%E6%95%B4%E7%90%86%E6%9C%AC%E5%91%A8%E5%91%A8%E6%8A%A5',
    );
    unmount();
  });

  it('工具卡标注演示且不给发起按钮（诚实动作）', async () => {
    vi.mocked(capabilitiesApi.recommend).mockResolvedValue({
      goal: '整理周报',
      matched: true,
      fallback: false,
      fallback_hint: '',
      items: [{ ...CAP_CARD, id: 'tool_1', kind: 'tool', name: '邮件工具', inputs: [] }],
    } as never);
    const { container, unmount } = renderWithClient(<GoalLauncher />);

    act(() => {
      const example = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('整理本周周报'),
      );
      example?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('演示');
      });
    });
    expect(container.textContent).toContain('去插件市场了解');
    expect(container.textContent).not.toContain('开始');
    unmount();
  });

  it('无命中走目录兜底并给引导（不得空列表）', async () => {
    vi.mocked(capabilitiesApi.recommend).mockResolvedValue({
      goal: '不存在的目标',
      matched: false,
      fallback: true,
      fallback_hint: '可以换个说法',
      items: [],
    } as never);
    const { container, unmount } = renderWithClient(<GoalLauncher />);

    act(() => {
      const example = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('整理本周周报'),
      );
      example?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('没有找到完全匹配的能力');
      });
    });
    expect(container.textContent).toContain('去插件市场浏览');
    unmount();
  });

  it('推荐失败给业务文案与重试', async () => {
    vi.mocked(capabilitiesApi.recommend).mockRejectedValue(new Error('推荐服务不可用'));
    const { container, unmount } = renderWithClient(<GoalLauncher />);

    act(() => {
      const example = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('整理本周周报'),
      );
      example?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await act(async () => {
      await vi.waitFor(() => {
        expect(container.textContent).toContain('重试');
      });
    });
    unmount();
  });
});

// ---------------------------------------------------------------------------
// ChatWindow
// ---------------------------------------------------------------------------

describe('ChatWindow', () => {
  beforeEach(() => {
    useConversationStore.setState({
      conversationId: 'conv_1',
      conversations: [],
      messages: [],
      profile: 'general',
    });
    mocks.stream.mockReset();
    mocks.stream.mockImplementation(() => vi.fn());
  });

  it('空输入不发送', () => {
    const { container, unmount } = renderWithClient(<ChatWindow />);
    act(() => {
      const send = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('发送'),
      );
      send?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(mocks.stream).not.toHaveBeenCalled();
    unmount();
  });

  it('发送后追加用户消息与助手占位并发起流式', async () => {
    const { container, unmount } = renderWithClient(<ChatWindow />);
    const textarea = container.querySelector('textarea');
    act(() => {
      if (textarea) {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
        setter?.call(textarea, '帮我写周报');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    await act(async () => {
      const send = Array.from(container.querySelectorAll('button')).find((item) =>
        item.textContent?.includes('发送'),
      );
      send?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const messages = useConversationStore.getState().messages;
    expect(messages).toHaveLength(2);
    expect(messages[0]?.content).toBe('帮我写周报');
    expect(messages[1]?.role).toBe('assistant');
    expect(mocks.stream).toHaveBeenCalledWith(
      expect.objectContaining({ conversation_id: 'conv_1' }),
      expect.any(Object),
    );
    unmount();
  });

  it('渲染历史消息与档位业务名（不暴露内部信息）', () => {
    useConversationStore.setState({
      conversationId: 'conv_1',
      conversations: [],
      messages: [{ id: 'm1', role: 'assistant', content: '这是回答' }],
      profile: 'general',
    });
    const { container, unmount } = renderWithClient(<ChatWindow />);
    expect(container.textContent).toContain('这是回答');
    expect(container.textContent).toContain('通用助手');
    unmount();
  });
});
