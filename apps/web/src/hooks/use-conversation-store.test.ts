/**
 * 前端补测第十一批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：会话本地状态 store（zustand）的动作语义、ID 生成合同与
 * persist 持久化口径（只存身份型状态）。store 是纯 JS 单例，
 * 直接驱动 actions 即可断言，不需要 React 渲染器。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

type StoreModule = typeof import('./use-conversation-store');

async function load(): Promise<StoreModule> {
  return import('./use-conversation-store');
}

/** 持久化存储键（与 store 的 persist 配置一致） */
const PERSIST_KEY = 'moy-conversation';

beforeEach(() => {
  vi.resetModules();
  window.localStorage.clear();
});

// ---------------------------------------------------------------------------
// ID 生成合同（架构文档 9.1）
// ---------------------------------------------------------------------------

describe('ID 生成', () => {
  it('会话 ID 形如 conv_ + 24 位大写十六进制', async () => {
    const { newConversationId } = await load();
    expect(newConversationId()).toMatch(/^conv_[0-9A-F]{24}$/);
  });

  it('会话 ID 不重复', async () => {
    const { newConversationId } = await load();
    expect(newConversationId()).not.toBe(newConversationId());
  });

  it('消息 ID 形如 msg_ + 16 位十六进制', async () => {
    const { newMessageId } = await load();
    expect(newMessageId()).toMatch(/^msg_[0-9a-f0-9]{16}$/);
  });
});

// ---------------------------------------------------------------------------
// 动作语义
// ---------------------------------------------------------------------------

describe('会话 store · 动作', () => {
  it('初始状态：general 档位、空消息、会话 ID 合法', async () => {
    const { useConversationStore } = await load();
    const state = useConversationStore.getState();
    expect(state.profile).toBe('general');
    expect(state.messages).toEqual([]);
    expect(state.conversations).toEqual([]);
    expect(state.conversationId).toMatch(/^conv_[0-9A-F]{24}$/);
  });

  it('newConversation 换新会话并清空消息，保留档位与关联库', async () => {
    const { useConversationStore } = await load();
    const store = useConversationStore.getState();
    store.setProfile('code');
    store.toggleKnowledgeBase('kb_1');
    store.appendMessage({ id: 'm1', role: 'assistant', content: '旧消息' });
    const before = useConversationStore.getState().conversationId;

    useConversationStore.getState().newConversation();
    const after = useConversationStore.getState();
    expect(after.conversationId).not.toBe(before);
    expect(after.messages).toEqual([]);
    expect(after.profile).toBe('code');
    expect(after.knowledgeBaseIds).toEqual(['kb_1']);
  });

  it('selectConversation 只切会话 ID，不清空消息（防闪空合同）', async () => {
    const { useConversationStore } = await load();
    useConversationStore.getState().appendMessage({
      id: 'm1',
      role: 'assistant',
      content: '保留我',
    });

    useConversationStore.getState().selectConversation('conv_AABB');
    const state = useConversationStore.getState();
    expect(state.conversationId).toBe('conv_AABB');
    expect(state.messages).toHaveLength(1);
  });

  it('appendMessage 追加到末尾', async () => {
    const { useConversationStore } = await load();
    const store = useConversationStore.getState();
    store.appendMessage({ id: 'm1', role: 'user', content: '一' });
    store.appendMessage({ id: 'm2', role: 'assistant', content: '二' });
    expect(useConversationStore.getState().messages.map((item) => item.id)).toEqual(['m1', 'm2']);
  });

  it('updateLastMessage 只改最后一条（流式拼接）', async () => {
    const { useConversationStore } = await load();
    const store = useConversationStore.getState();
    store.appendMessage({ id: 'm1', role: 'user', content: '问题' });
    store.appendMessage({ id: 'm2', role: 'assistant', content: '' });

    useConversationStore.getState().updateLastMessage({ content: '答案', pending: false });
    const messages = useConversationStore.getState().messages;
    expect(messages[0]?.content).toBe('问题');
    expect(messages[1]?.content).toBe('答案');
    expect(messages[1]?.pending).toBe(false);
  });

  it('updateLastMessage 在空消息列表时不炸', async () => {
    const { useConversationStore } = await load();
    useConversationStore.getState().updateLastMessage({ content: '无处可写' });
    expect(useConversationStore.getState().messages).toEqual([]);
  });

  it('setProfile 切换能力档位', async () => {
    const { useConversationStore } = await load();
    useConversationStore.getState().setProfile('reasoning');
    expect(useConversationStore.getState().profile).toBe('reasoning');
  });

  it('toggleKnowledgeBase 首次添加、再次移除', async () => {
    const { useConversationStore } = await load();
    const store = useConversationStore.getState();
    store.toggleKnowledgeBase('kb_1');
    store.toggleKnowledgeBase('kb_2');
    expect(useConversationStore.getState().knowledgeBaseIds).toEqual(['kb_1', 'kb_2']);

    useConversationStore.getState().toggleKnowledgeBase('kb_1');
    expect(useConversationStore.getState().knowledgeBaseIds).toEqual(['kb_2']);
  });

  it('clearMessages 清空当前会话消息', async () => {
    const { useConversationStore } = await load();
    const store = useConversationStore.getState();
    store.appendMessage({ id: 'm1', role: 'user', content: 'x' });
    useConversationStore.getState().clearMessages();
    expect(useConversationStore.getState().messages).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// persist 持久化口径
// ---------------------------------------------------------------------------

describe('会话 store · 持久化', () => {
  it('只持久化身份型状态，消息与会话列表不落盘', async () => {
    const { useConversationStore } = await load();
    const store = useConversationStore.getState();
    store.setProfile('code');
    store.toggleKnowledgeBase('kb_1');
    store.appendMessage({ id: 'm1', role: 'user', content: '不落盘' });

    const raw = window.localStorage.getItem(PERSIST_KEY);
    expect(raw).toBeTruthy();
    const persisted = JSON.parse(raw ?? '{}') as Record<string, unknown>;
    const persistedState = (persisted.state ?? {}) as Record<string, unknown>;
    expect(persistedState.profile).toBe('code');
    expect(persistedState.knowledgeBaseIds).toEqual(['kb_1']);
    // messages 的真源在服务端（P4-07），缓存会造成多设备不一致
    expect(persistedState).not.toHaveProperty('messages');
    expect(persistedState).not.toHaveProperty('conversations');
  });

  it('重新加载模块后从存储恢复身份型状态', async () => {
    const first = await load();
    first.useConversationStore.getState().setProfile('long_context');
    first.useConversationStore.getState().toggleKnowledgeBase('kb_9');

    vi.resetModules();
    const second = await load();
    const state = second.useConversationStore.getState();
    expect(state.profile).toBe('long_context');
    expect(state.knowledgeBaseIds).toEqual(['kb_9']);
    // 消息不恢复（服务端是真源）
    expect(state.messages).toEqual([]);
  });

  it('存储为空时以初始状态启动', async () => {
    const { useConversationStore } = await load();
    const state = useConversationStore.getState();
    expect(state.profile).toBe('general');
    expect(state.knowledgeBaseIds).toEqual([]);
    expect(state.conversationId).toMatch(/^conv_[0-9A-F]{24}$/);
  });
});
