import type React from "react";
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { knowledgeApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { SearchSegment } from '@/lib/types';

/** 检索输入防抖（毫秒） */
const SEARCH_DEBOUNCE_MS = 400;

/**
 * 文档检索组件：带引用的独立检索。
 *
 * @param props - 组件属性
 * @returns 检索组件元素
 */
export default function DocumentSearch({
  baseId,
}: {
  baseId: string;
}): React.JSX.Element {
  const [query, setQuery] = useState('');
  const [segments, setSegments] = useState<SearchSegment[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    },
    [],
  );

  /** 执行检索 */
  const search = useCallback(
    async (keyword: string): Promise<void> => {
      const trimmed = keyword.trim();
      if (!trimmed || !baseId) {
        setSegments([]);
        return;
      }
      setLoading(true);
      setErrorMessage('');
      try {
        const result = await knowledgeApi.search({
          base_id: baseId,
          query: trimmed,
          top_k: 5,
          score_threshold: 0.5,
        });
        setSegments(result.segments);
      } catch (error) {
        setErrorMessage(toBusinessMessage(error));
      } finally {
        setLoading(false);
      }
    },
    [baseId],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>资料检索</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <input
          type="search"
          value={query}
          onChange={(event) => {
            const value = event.target.value;
            setQuery(value);
            if (timerRef.current) {
              clearTimeout(timerRef.current);
            }
            timerRef.current = setTimeout(() => void search(value), SEARCH_DEBOUNCE_MS);
          }}
          placeholder="输入关键词查找资料内容"
          className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />

        {loading ? <LoadingState label="正在查找…" /> : null}
        {!loading && errorMessage ? (
          <ErrorState message={errorMessage} onRetry={() => void search(query)} />
        ) : null}
        {!loading && !errorMessage && segments.length === 0 && query.trim() ? (
          <EmptyState title="没有找到相关内容" description="换个关键词试试" />
        ) : null}
        {!loading && segments.length > 0 ? (
          <ul className="space-y-3">
            {segments.map((segment, index) => (
              <li key={`${segment.document_id}-${index}`} className="rounded-md border p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-sm font-medium">{segment.document_name}</span>
                  <Badge variant="secondary">
                    相关度 {(segment.score * 100).toFixed(0)}%
                  </Badge>
                </div>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {segment.segment_content}
                </p>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}
