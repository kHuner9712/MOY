'use client';

import { useCallback, useRef, useState } from 'react';

import { modelApi } from '@/lib/api-client';
import type { Citation, GenerateRequest, ModelProfile } from '@/lib/types';

/** 流式生成状态 */
export interface ChatStreamState {
  /** 是否正在生成 */
  streaming: boolean;
  /** 已累积的文本 */
  text: string;
  /** 随答案穿插下发的引用 */
  citations: Citation[];
  /** 业务语言错误提示 */
  errorMessage: string;
  /** 生成记录 ID */
  generationId: string;
}

/** `useChatStream` 返回值 */
export interface UseChatStreamResult extends ChatStreamState {
  /** 发起一次流式生成 */
  send: (params: {
    conversationId: string;
    profile: ModelProfile;
    content: string;
    knowledgeBaseIds?: string[];
  }) => Promise<void>;
  /** 中断当前生成 */
  abort: () => void;
  /** 重置状态 */
  reset: () => void;
}

/** 初始状态 */
const INITIAL_STATE: ChatStreamState = {
  streaming: false,
  text: '',
  citations: [],
  errorMessage: '',
  generationId: '',
};

/**
 * 流式对话 Hook（自研 SSE 事件：start / delta / citation / done / error）。
 *
 * 设计要点：
 * - 逐字渲染由适配层的 `delta` 事件驱动，不做本地节流，保证流畅无卡顿；
 * - 引用随答案位置穿插下发并去重（附录 A-6 / D-01）；
 * - 错误一律转换为业务语言文案，不展示 code 与技术细节。
 *
 * @returns 流式状态与操作方法
 */
export function useChatStream(): UseChatStreamResult {
  const [state, setState] = useState<ChatStreamState>(INITIAL_STATE);
  const cancelRef = useRef<(() => void) | null>(null);

  const abort = useCallback(() => {
    cancelRef.current?.();
    cancelRef.current = null;
    setState((prev) => ({ ...prev, streaming: false }));
  }, []);

  const reset = useCallback(() => {
    abort();
    setState(INITIAL_STATE);
  }, [abort]);

  const send = useCallback<UseChatStreamResult['send']>(
    async ({ conversationId, profile, content, knowledgeBaseIds }) => {
      abort();
      setState({ ...INITIAL_STATE, streaming: true });

      const body: GenerateRequest = {
        conversation_id: conversationId,
        profile,
        messages: [{ role: 'user', content }],
        knowledge_base_ids: knowledgeBaseIds ?? [],
      };

      cancelRef.current = modelApi.stream(body, {
        onStart: (data) => {
          setState((prev) => ({ ...prev, generationId: data.generation_id }));
        },
        onDelta: (data) => {
          setState((prev) => ({ ...prev, text: prev.text + data.text }));
        },
        onCitation: (data) => {
          setState((prev) => {
            const duplicated = prev.citations.some(
              (item) =>
                item.document_name === data.document_name &&
                item.segment_content === data.segment_content,
            );
            if (duplicated) {
              return prev;
            }
            return {
              ...prev,
              citations: [
                ...prev.citations,
                {
                  document_id: '',
                  document_name: data.document_name,
                  segment_content: data.segment_content,
                  score: 0,
                },
              ],
            };
          });
        },
        onDone: () => {
          setState((prev) => ({ ...prev, streaming: false }));
          cancelRef.current = null;
        },
        onError: (data) => {
          setState((prev) => ({
            ...prev,
            streaming: false,
            errorMessage: data.message || 'AI 暂时没有给出回答，请重试',
          }));
          cancelRef.current = null;
        },
      });
    },
    [abort],
  );

  return { ...state, send, abort, reset };
}
