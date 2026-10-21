import type React from "react";
'use client';

import dynamic from 'next/dynamic';

import ConversationSidebar from '@/components/chat/conversation-sidebar';

// 对话窗口仅在客户端渲染（依赖 SSE 与浏览器 API）
const ChatWindow = dynamic(() => import('@/components/chat/chat-window'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      正在准备对话…
    </div>
  ),
});

/**
 * 对话主页：多会话侧边栏 + 对话窗口。
 *
 * @returns 对话页面元素
 */
export default function ChatPage(): React.JSX.Element {
  return (
    <div className="flex h-[calc(100vh-7.5rem)] gap-6">
      <aside className="hidden w-64 shrink-0 lg:block">
        <ConversationSidebar />
      </aside>
      <div className="min-w-0 flex-1">
        <ChatWindow />
      </div>
    </div>
  );
}
