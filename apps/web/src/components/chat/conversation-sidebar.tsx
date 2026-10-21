import type React from "react";
'use client';

import { MessageSquarePlus } from 'lucide-react';
import { useCallback } from 'react';

import { Button } from '@/components/ui/button';
import { useConversationStore } from '@/hooks/use-conversation-store';

/**
 * 会话侧边栏：多会话切换。
 *
 * 说明：会话列表由上层自持（架构文档 10.2 D-03），
 * 适配层保持无状态，不代理会话存储。
 *
 * @returns 会话侧边栏元素
 */
export default function ConversationSidebar(): React.JSX.Element {
  const { conversations, conversationId, newConversation } = useConversationStore();

  const handleNew = useCallback(() => {
    newConversation();
  }, [newConversation]);

  return (
    <div className="flex h-full flex-col gap-2">
      <Button variant="outline" className="w-full justify-start" onClick={handleNew}>
        <MessageSquarePlus className="h-4 w-4" />
        新建对话
      </Button>

      <div className="mt-2 flex-1 space-y-1 overflow-y-auto">
        {conversations.length === 0 ? (
          <p className="px-2 py-4 text-center text-xs text-muted-foreground">
            暂无历史对话
          </p>
        ) : (
          conversations.map((item) => (
            <div
              key={item.conversation_id}
              className={`rounded-md px-3 py-2 text-sm ${
                item.conversation_id === conversationId
                  ? 'bg-primary/10 font-medium text-primary'
                  : 'text-muted-foreground hover:bg-accent'
              }`}
            >
              {item.title}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
