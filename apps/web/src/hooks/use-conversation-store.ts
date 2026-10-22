/**
 * 会话本地状态（上层自持，不经适配层）。
 *
 * 架构依据（10.2 D-03）：会话列表与历史消息由前端/业务能力层自持，
 * 适配层只做无状态生成。因此本 store 属于前端交互层，
 * 与适配层无状态设计不冲突。
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

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
  updateLastMessage: (patch: Partial<Omit<ChatMessage, 'id' | 'role'>>) => void;
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
  // `persist`：只持久化"身份型"状态（会话 ID / 档位 / 关联库），
  // **不持久化 messages**——消息的真源在服务端（阶段四 P4-07），
  // 本地缓存一份会让"刷新后逐字恢复"这件事变成"恢复的是缓存"，
  // 而且多设备下必然不一致。
  persist(
  (set, get) => ({
    ...INITIAL_STATE,

    newConversation: () => {
      const conversationId = newConversationId();
      set({ conversationId, messages: [] });
    },

    selectConversation: (conversationId) => {
      // **不清空 messages**（阶段四 T4-02.6）：历史现在由服务端提供，页面在
      // select 之后异步拉 `MC2` 再写入 store。这里清空会出现两种可见缺陷：
      //   ① 切到同一会话（例如从列表点回来）时，正在拉的请求还没回来，
      //      画面先闪成空，用户以为消息丢了；
      //   ② 刷新/换设备时首帧必然是空的。
      // 换会话时的"旧消息残留"由页面在拉取成功后整体覆盖解决，而不是靠先清空。
      set({ conversationId });
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
        if (!last) {
          return prev;
        }
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
  {
    name: 'moy-conversation',
    // 只持久化身份型状态：`messages` 的真源在服务端，缓存一份会在多设备下不一致
    partialize: (state) => ({
      conversationId: state.conversationId,
      profile: state.profile,
      knowledgeBaseIds: state.knowledgeBaseIds,
    }),
  },
  ),
);

/** 导出会话 ID 生成器，供其他模块复用 */
export { newConversationId, newMessageId };
