'use client';

import { useCallback, useRef, useState } from 'react';

import { modelApi } from '@/lib/api-client';
import type { Citation, GenerateRequest, ModelProfile } from '@/lib/types';

/** 一次发送的参数（中断后可原样重发） */
export interface ChatSendParams {
  conversationId: string;
  profile: ModelProfile;
  content: string;
  knowledgeBaseIds?: string[];
}

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
  /** 是否被用户中断（用于展示「重新发送」） */
  interrupted: boolean;
}

/** `useChatStream` 返回值 */
export interface UseChatStreamResult extends ChatStreamState {
  /** 发起一次流式生成 */
  send: (params: ChatSendParams) => Promise<void>;
  /** 中断当前生成（会标记为「已中断」，可重发） */
  abort: () => void;
  /** 重发上一次请求（中断或失败后使用） */
  resend: () => Promise<void>;
  /** 是否有可重发的请求 */
  canResend: boolean;
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
  interrupted: false,
};

/** 兜底错误文案（与 10001 同口径，不展示码值） */
const FALLBACK_ERROR = 'AI 暂时没有给出回答，请重试';

/**
 * 流式对话 Hook（自研 SSE 事件：start / delta / citation / done / error）。
 *
 * 设计要点：
 * - 逐字渲染由适配层的 `delta` 事件驱动，不做本地节流，保证流畅无卡顿；
 * - 引用随答案位置穿插下发并去重（附录 A-6 / D-01）；阶段二引用额外带
 *   `base_id / base_name / segment_id`，用于 KB4 回链（引用详情抽屉）；
 * - 用户点「停止」或流式异常时保留 `interrupted` 标记，界面据此提供「重新发送」，
 *   不需要用户重新打一遍问题；
 * - 错误一律转换为业务语言文案，不展示 code 与技术细节。
 *
 * @returns 流式状态与操作方法
 */
export function useChatStream(): UseChatStreamResult {
  const [state, setState] = useState<ChatStreamState>(INITIAL_STATE);
  const cancelRef = useRef<(() => void) | null>(null);
  const streamingRef = useRef<boolean>(false);
  const lastRequestRef = useRef<ChatSendParams | null>(null);
  const hasRequestRef = useRef<boolean>(false);
  const [canResend, setCanResend] = useState<boolean>(false);

  /** 内部中断（不标记 interrupted，用于重发前清理） */
  const cancelCurrent = useCallback((): void => {
    cancelRef.current?.();
    cancelRef.current = null;
    streamingRef.current = false;
  }, []);

  /** 用户主动停止：标记为已中断，允许重发 */
  const abort = useCallback((): void => {
    const wasStreaming = streamingRef.current;
    cancelCurrent();
    if (wasStreaming) {
      setCanResend(hasRequestRef.current);
    }
    setState((prev) => ({
      ...prev,
      streaming: false,
      interrupted: wasStreaming ? true : prev.interrupted,
    }));
  }, [cancelCurrent]);

  const reset = useCallback((): void => {
    cancelCurrent();
    lastRequestRef.current = null;
    hasRequestRef.current = false;
    setCanResend(false);
    setState(INITIAL_STATE);
  }, [cancelCurrent]);

  const send = useCallback<UseChatStreamResult['send']>(
    async ({ conversationId, profile, content, knowledgeBaseIds }) => {
      lastRequestRef.current = { conversationId, profile, content, knowledgeBaseIds };
      hasRequestRef.current = true;
      cancelCurrent();
      setCanResend(false);
      setState({ ...INITIAL_STATE, streaming: true });
      streamingRef.current = true;

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
                  document_id: data.document_id ?? '',
                  document_name: data.document_name,
                  segment_content: data.segment_content,
                  // 相关度不上屏（业务语言界面不展示分值与任何打分口径）
                  score: 0,
                  base_id: data.base_id,
                  base_name: data.base_name,
                  segment_id: data.segment_id,
                  position: data.position ?? null,
                },
              ],
            };
          });
        },
        onDone: () => {
          streamingRef.current = false;
          cancelRef.current = null;
          setState((prev) => ({ ...prev, streaming: false, interrupted: false }));
        },
        onError: (data) => {
          streamingRef.current = false;
          cancelRef.current = null;
          setCanResend(hasRequestRef.current);
          setState((prev) => ({
            ...prev,
            streaming: false,
            errorMessage: data.message || FALLBACK_ERROR,
          }));
        },
      });
    },
    [cancelCurrent],
  );

  const resend = useCallback(async (): Promise<void> => {
    const last = lastRequestRef.current;
    if (!last) return;
    await send(last);
  }, [send]);

  return { ...state, send, abort, resend, canResend, reset };
}
