/**
 * 会话本地状态（上层自持，不经适配层）。
 *
 * 架构依据（10.2 D-03）：会话列表与历史消息由前端/业务能力层自持，
 * 适配层只做无状态生成。因此本 store 属于前端交互层，
 * 与适配层无状态设计不冲突。
 */

import { create } from 'zustand';

import type { ChatMessage, Conversation, ModelProfile } from '@/lib/types';

/** 会话 store 状态 */
export interface ConversationState {
  /** 当前会话 ID */
  conversationId: string;
  /** 会话列表 */
  conversations: Conversation[];
  /** 当前会话消息 */
  messages: ChatMessage[];
  /** 当前选中的能力档位 */
  profile: ModelProfile;
  /** 关联知识库 ID 列表 */
  knowledgeBaseIds: string[];
}

/** 会话 store 操作 */
export interface ConversationActions {
  /** 新建会话 */
  newConversation: () => void;
  /** 切换会话 */
  selectConversation: (conversationId: string) => void;
  /** 追加消息 */
  appendMessage: (message: ChatMessage) => void;
  /** 更新最后一条消息（流式拼接） */
  updateLastMessage: (patch: Partial<ChatMessage>) => void;
  /** 切换能力档位 */
  setProfile: (profile: ModelProfile) => void;
  /** 关联知识库 */
  toggleKnowledgeBase: (baseId: string) => void;
  /** 清空当前会话消息 */
  clearMessages: () => void;
}

/** 生成会话 ID（`conv_` + 24 位十六进制，架构文档 9.1） */
function newConversationId(): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '')
      : Math.random().toString(16).slice(2).padEnd(24, '0');
  return `conv_${random.slice(0, 24).toUpperCase()}`;
}

/** 生成消息 ID */
function newMessageId(): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '')
      : Math.random().toString(16).slice(2).padEnd(16, '0');
  return `msg_${random.slice(0, 16)}`;
}

/** 初始状态 */
const INITIAL_STATE: ConversationState = {
  conversationId: newConversationId(),
  conversations: [],
  messages: [],
  profile: 'general',
  knowledgeBaseIds: [],
};

/**
 * 会话状态 store。
 *
 * @returns 会话状态与操作方法
 */
export const useConversationStore = create<ConversationState & ConversationActions>()(
  (set, get) => ({
    ...INITIAL_STATE,

    newConversation: () => {
      const conversationId = newConversationId();
      set({ conversationId, messages: [] });
    },

    selectConversation: (conversationId) => {
      const target = get().conversations.find((item) => item.conversation_id === conversationId);
      set({
        conversationId,
        messages: target ? [] : get().messages,
      });
    },

    appendMessage: (message) => {
      set((prev) => ({ messages: [...prev.messages, message] }));
    },

    updateLastMessage: (patch) => {
      set((prev) => {
        if (prev.messages.length === 0) {
          return prev;
        }
        const next = [...prev.messages];
        const last = next[next.length - 1];
        next[next.length - 1] = { ...last, ...patch };
        return { messages: next };
      });
    },

    setProfile: (profile) => set({ profile }),

    toggleKnowledgeBase: (baseId) => {
      set((prev) => ({
        knowledgeBaseIds: prev.knowledgeBaseIds.includes(baseId)
          ? prev.knowledgeBaseIds.filter((item) => item !== baseId)
          : [...prev.knowledgeBaseIds, baseId],
      }));
    },

    clearMessages: () => set({ messages: [] }),
  }),
);

/** 导出会话 ID 生成器，供其他模块复用 */
export { newConversationId, newMessageId };
