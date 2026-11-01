/**
 * 前端补测第十二批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：流式对话 Hook 的事件拼接（start/delta/citation/done/error）、
 * 引用去重、中断与重发状态机。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { GenerateRequest } from '@/lib/types';
import { act, renderHook } from '@/test/render-hook';
import { useChatStream } from './use-chat-stream';

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
}));

vi.mock('@/lib/api-client', () => ({
  modelApi: { stream: mocks.stream },
}));

/** 流式回调句柄（由 stream mock 捕获，测试手动触发） */
interface StreamHandlers {
  onStart?: (data: { generation_id: string }) => void;
  onDelta?: (data: { text: string }) => void;
  onCitation?: (data: {
    document_name: string;
    segment_content: string;
    document_id?: string;
    base_id?: string;
    base_name?: string;
    segment_id?: string;
    position?: { page: number | null; paragraph: number | null } | null;
  }) => void;
  onDone?: (data: {
    finish_reason: string;
    usage: { prompt_units: number; completion_units: number; total_units: number };
  }) => void;
  onError?: (data: { code: number; message: string }) => void;
}

let handlers: StreamHandlers;
let cancel: ReturnType<typeof vi.fn>;

const SEND_PARAMS = {
  conversationId: 'conv_1',
  profile: 'general' as const,
  content: '帮我写周报',
  knowledgeBaseIds: ['kb_1'],
};

beforeEach(() => {
  handlers = {};
  cancel = vi.fn();
  mocks.stream.mockReset();
  mocks.stream.mockImplementation((_body: GenerateRequest, h: StreamHandlers) => {
    handlers = h;
    return cancel;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useChatStream · 发送与拼接', () => {
  it('send 进入流式中并按参数发起生成', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });

    expect(result.current.streaming).toBe(true);
    const body = mocks.stream.mock.calls[0]?.[0] as GenerateRequest;
    expect(body.conversation_id).toBe('conv_1');
    expect(body.messages[0]?.content).toBe('帮我写周报');
    expect(body.knowledge_base_ids).toEqual(['kb_1']);
    unmount();
  });

  it('start 写入生成记录 ID，delta 逐字拼接', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });

    act(() => handlers.onStart?.({ generation_id: 'gen_1' }));
    expect(result.current.generationId).toBe('gen_1');

    act(() => handlers.onDelta?.({ text: '周报' }));
    act(() => handlers.onDelta?.({ text: '要点' }));
    expect(result.current.text).toBe('周报要点');
    unmount();
  });

  it('相同引用只收一次（按文档名 + 片段内容去重）', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });

    const citation = {
      document_name: '手册',
      segment_content: '片段 A',
      document_id: 'doc_1',
    };
    act(() => handlers.onCitation?.(citation));
    act(() => handlers.onCitation?.(citation));
    act(() => handlers.onCitation?.({ document_name: '手册', segment_content: '片段 B' }));

    expect(result.current.citations).toHaveLength(2);
    expect(result.current.citations[0]?.document_id).toBe('doc_1');
    // 缺省回链字段允许为空串（阶段一引用无回链）
    expect(result.current.citations[1]?.document_id).toBe('');
    unmount();
  });

  it('done 结束流式并清除中断标记', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });

    act(() =>
      handlers.onDone?.({
        finish_reason: 'stop',
        usage: { prompt_units: 1, completion_units: 2, total_units: 3 },
      }),
    );
    expect(result.current.streaming).toBe(false);
    expect(result.current.interrupted).toBe(false);
    unmount();
  });

  it('error 以业务语言提示；空 message 用兜底文案', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });

    act(() => handlers.onError?.({ code: 30001, message: '' }));
    expect(result.current.errorMessage).toBe('AI 暂时没有给出回答，请重试');
    expect(result.current.streaming).toBe(false);

    act(() => handlers.onError?.({ code: 30001, message: '生成服务繁忙' }));
    expect(result.current.errorMessage).toBe('生成服务繁忙');
    unmount();
  });
});

describe('useChatStream · 中断与重发', () => {
  it('abort 标记已中断并允许重发', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });

    act(() => {
      result.current.abort();
    });
    expect(cancel).toHaveBeenCalled();
    expect(result.current.streaming).toBe(false);
    expect(result.current.interrupted).toBe(true);
    expect(result.current.canResend).toBe(true);
    unmount();
  });

  it('非流式中 abort 不产生中断标记', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    act(() => {
      result.current.abort();
    });
    expect(result.current.interrupted).toBe(false);
    expect(result.current.canResend).toBe(false);
    unmount();
  });

  it('resend 用上一次参数原样重发', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });
    act(() => {
      result.current.abort();
    });

    await act(async () => {
      await result.current.resend();
    });
    expect(mocks.stream).toHaveBeenCalledTimes(2);
    const body = mocks.stream.mock.calls[1]?.[0] as GenerateRequest;
    expect(body.conversation_id).toBe('conv_1');
    expect(body.messages[0]?.content).toBe('帮我写周报');
    unmount();
  });

  it('resend 前清掉旧流与中断状态（不残留上一轮文本）', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });
    act(() => handlers.onDelta?.({ text: '旧文本' }));
    act(() => {
      result.current.abort();
    });

    await act(async () => {
      await result.current.resend();
    });
    expect(result.current.text).toBe('');
    expect(result.current.interrupted).toBe(false);
    unmount();
  });

  it('reset 回到初始状态', async () => {
    const { result, unmount } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send(SEND_PARAMS);
    });
    act(() => handlers.onDelta?.({ text: '一段回答' }));

    act(() => {
      result.current.reset();
    });
    expect(result.current.text).toBe('');
    expect(result.current.streaming).toBe(false);
    expect(result.current.generationId).toBe('');
    unmount();
  });
});
