'use client';

import { Quote, RotateCcw, Send, Square } from 'lucide-react';
import * as React from 'react';

import { CitationDrawer } from '@/components/chat/citation-drawer';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorState } from '@/components/ui/feedback';
import { Textarea } from '@/components/ui/input';
import { useChatStream } from '@/hooks/use-chat-stream';
import { newMessageId, useConversationStore } from '@/hooks/use-conversation-store';
import { reportStreamFailure } from '@/lib/error-guard';
import type { Citation, ModelProfile } from '@/lib/types';

/** 能力档位展示配置（只展示业务名称，不暴露任何内部信息） */
const PROFILE_LABELS: Record<ModelProfile, string> = {
  general: '通用助手',
  code: '编程助手',
  long_context: '长文档助手',
  reasoning: '深度推理助手',
};

/** 可选档位（顺序即展示顺序） */
const PROFILES: ModelProfile[] = ['general', 'code', 'long_context', 'reasoning'];

/**
 * 对话窗口：消息列表 + 输入区 + 引用详情抽屉。
 *
 * 设计要点（总方案 3.5 / 阶段二 P-06）：
 * - 逐字渲染由流式事件驱动，无卡顿；
 * - 引用随答案穿插展示，**可点击**打开抽屉看片段全文、前后文与位置；旧版本引用给降级提示；
 * - 流式中断或失败后提供「重新发送」，不用用户重新输入；
 * - 所有错误使用业务语言提示，不出现任何技术名词与码值。
 *
 * @returns 对话窗口元素
 */
export default function ChatWindow(): React.JSX.Element {
  const { messages, conversationId, profile, appendMessage, updateLastMessage, setProfile } =
    useConversationStore();
  const stream = useChatStream();
  const [input, setInput] = React.useState<string>('');
  const [activeCitation, setActiveCitation] = React.useState<Citation | null>(null);
  const [drawerOpen, setDrawerOpen] = React.useState<boolean>(false);
  const assistantMessageIdRef = React.useRef<string>('');

  // 流式增量同步到最后一条助手消息（在 effect 中更新，避免渲染期副作用）
  React.useEffect(() => {
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

  // 流式中断/失败：统一走全局兜底（同一失败 3 秒内只提示一次，避免轮询式刷屏）
  React.useEffect(() => {
    if (!stream.streaming && stream.errorMessage) {
      reportStreamFailure(stream.errorMessage);
    }
  }, [stream.errorMessage, stream.streaming]);

  const handleSend = React.useCallback(async (): Promise<void> => {
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

  /** 重新发送上一次问题（中断 / 失败后） */
  const handleResend = async (): Promise<void> => {
    if (stream.streaming || !stream.canResend) return;
    const assistantId = newMessageId();
    assistantMessageIdRef.current = assistantId;
    appendMessage({ id: assistantId, role: 'assistant', content: '', pending: true });
    await stream.resend();
  };

  const openCitation = (citation: Citation): void => {
    setActiveCitation(citation);
    setDrawerOpen(true);
  };

  const showResend = !stream.streaming && stream.canResend;

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
                <ul className="space-y-1">
                  {message.citations.map((citation, index) => (
                    <li key={`${citation.document_name}-${citation.segment_id ?? index}`}>
                      <button
                        type="button"
                        onClick={() => openCitation(citation)}
                        className="inline-flex max-w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                      >
                        <Quote aria-hidden="true" className="h-3 w-3 shrink-0" />
                        <span className="truncate">
                          引用来源：{citation.document_name}
                          {citation.base_name ? `（${citation.base_name}）` : ''}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))
        )}
      </div>

      {stream.errorMessage && !stream.streaming ? (
        <div className="mt-3">
          <ErrorState
            message={stream.errorMessage}
            onRetry={() => {
              void handleResend();
            }}
          />
        </div>
      ) : null}

      <div className="mt-4 space-y-2">
        {stream.interrupted && !stream.errorMessage ? (
          <p className="text-xs text-muted-foreground">
            已停止生成，可以点「重新发送」再问一次，或直接补充新的问题。
          </p>
        ) : null}
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                void handleSend();
              }
            }}
            placeholder="输入你的问题"
            className="min-h-[56px]"
            rows={2}
          />
          {stream.streaming ? (
            <Button variant="outline" size="lg" onClick={stream.abort}>
              <Square className="h-4 w-4" />
              停止
            </Button>
          ) : showResend ? (
            <Button variant="outline" size="lg" onClick={() => void handleResend()}>
              <RotateCcw className="h-4 w-4" />
              重新发送
            </Button>
          ) : (
            <Button size="lg" onClick={() => void handleSend()} disabled={!input.trim()}>
              <Send className="h-4 w-4" />
              发送
            </Button>
          )}
        </div>
        {/* 快捷键提示只在桌面保留：移动端没有 Ctrl/⌘ 键，占一行会把输入区推高 */}
        <p className="mt-1 hidden text-xs text-muted-foreground md:block">Ctrl/⌘ + Enter 发送</p>
      </div>

      <CitationDrawer open={drawerOpen} onOpenChange={setDrawerOpen} citation={activeCitation} />
    </div>
  );
}
