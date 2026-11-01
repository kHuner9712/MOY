/**
 * 前端补测第十六批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：对话域组件的渲染合同 —— ConversationSidebar 会话切换与空态、
 * CitationDrawer 三种引用情况（可回链拉原文 / 旧版本降级 / 缺定位降级）。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Citation, SegmentDetail } from '@/lib/types';
import { act, renderComponent } from '@/test/render-hook';
import { useConversationStore } from '@/hooks/use-conversation-store';
import { CitationDrawer, STALE_HINT } from './citation-drawer';
import ConversationSidebar from './conversation-sidebar';

vi.mock('@/lib/api-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api-client')>()),
  knowledgeExtApi: { segmentDetail: vi.fn() },
}));

import { knowledgeExtApi } from '@/lib/api-client';

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
