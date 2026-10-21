'use client';

import { Send, Square } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { useChatStream } from '@/hooks/use-chat-stream';
import { newMessageId, useConversationStore } from '@/hooks/use-conversation-store';
import type { ChatMessage, ModelProfile } from '@/lib/types';

/** 能力档位展示配置（只展示业务名称，不暴露模型信息） */
const PROFILE_LABELS: Record<ModelProfile, string> = {
  general: '通用助手',
  code: '编程助手',
  long_context: '长文档助手',
  reasoning: '深度推理助手',
};

/** 可选档位（顺序即展示顺序） */
const PROFILES: ModelProfile[] = ['general', 'code', 'long_context', 'reasoning'];

/**
 * 对话窗口：消息列表 + 输入区。
 *
 * 设计要点（总方案 3.5）：
 * - 逐字渲染由流式事件驱动，无卡顿；
 * - 引用来源随答案穿插展示（FLOW-02）；
 * - 所有错误使用业务语言提示，不出现任何技术名词。
 *
 * @returns 对话窗口元素
 */
export default function ChatWindow(): JSX.Element {
  const { messages, conversationId, profile, appendMessage, updateLastMessage, setProfile } =
    useConversationStore();
  const stream = useChatStream();
  const [input, setInput] = useState('');
  const assistantMessageIdRef = useRef<string>('');

  // 流式增量同步到最后一条助手消息（在 effect 中更新，避免渲染期副作用）
  useEffect(() => {
    const targetId = assistantMessageIdRef.current;
    if (!targetId || (!stream.text && stream.citations.length === 0)) {
      return;
    }
    updateLastMessage({
      content: stream.text,
      citations: stream.citations,
      pending: false,
    });
  }, [stream.citations, stream.text, updateLastMessage]);

  // 流式结束且无内容时给出兜底提示
  useEffect(() => {
    if (!stream.streaming && stream.errorMessage) {
      toast.error(stream.errorMessage);
    }
  }, [stream.errorMessage, stream.streaming]);

  const handleSend = useCallback(async () => {
    const content = input.trim();
    if (!content || stream.streaming) {
      return;
    }

    setInput('');
    appendMessage({ id: newMessageId(), role: 'user', content });
    const assistantId = newMessageId();
    assistantMessageIdRef.current = assistantId;
    appendMessage({ id: assistantId, role: 'assistant', content: '', pending: true });

    stream.reset();
    await stream.send({ conversationId, profile, content });
  }, [appendMessage, conversationId, input, profile, stream]);

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col">
      <div className="mb-4 flex flex-wrap gap-2">
        {PROFILES.map((item) => (
          <Badge
            key={item}
            variant={item === profile ? 'default' : 'outline'}
            className="cursor-pointer px-3 py-1"
            onClick={() => setProfile(item)}
          >
            {PROFILE_LABELS[item]}
          </Badge>
        ))}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto rounded-lg border bg-card p-6">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="text-base font-medium">开始你的第一个问题</p>
            <p className="max-w-md text-sm text-muted-foreground">
              可以直接提问，也可以先上传资料再提问，回答会自动标注引用来源
            </p>
          </div>
        ) : (
          messages.map((message) => (
            <article
              key={message.id}
              className={message.role === 'user' ? 'flex justify-end' : 'space-y-2'}
            >
              <div
                className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-4 py-3 text-sm leading-relaxed ${
                  message.role === 'user'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted/50 text-foreground'
                }`}
              >
                {message.content || (message.pending ? '正在思考…' : '')}
              </div>
              {message.citations && message.citations.length > 0 ? (
                <ul className="space-y-1 text-xs text-muted-foreground">
                  {message.citations.map((citation, index) => (
                    <li key={`${citation.document_name}-${index}`}>
                      引用来源：{citation.document_name}
                    </li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))
        )}
      </div>

      <div className="mt-4 flex items-end gap-2">
        <Textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void handleSend();
            }
          }}
          placeholder="输入你的问题，Ctrl/⌘ + Enter 发送"
          className="min-h-[56px]"
          rows={2}
        />
        {stream.streaming ? (
          <Button variant="outline" size="lg" onClick={stream.abort}>
            <Square className="h-4 w-4" />
            停止
          </Button>
        ) : (
          <Button size="lg" onClick={() => void handleSend()} disabled={!input.trim()}>
            <Send className="h-4 w-4" />
            发送
          </Button>
        )}
      </div>
    </div>
  );
}
