'use client';

import { ExternalLink, Quote } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Drawer,
  DrawerBody,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { knowledgeExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { orPlaceholder } from '@/lib/format';
import type { Citation, SegmentDetail } from '@/lib/types';

/** 旧版本引用的降级文案（PRD 原文，前端不得改写） */
export const STALE_HINT = '该引用来自旧版本内容，原文已更新';

/** 引用抽屉属性 */
export interface CitationDrawerProps {
  /** 是否打开 */
  open: boolean;
  /** 开关回调 */
  onOpenChange: (open: boolean) => void;
  /** 当前查看的引用；null 时展示空态 */
  citation: Citation | null;
}

/**
 * 把引用里的位置信息转成业务语言。
 *
 * @param position - 页码与段落
 * @returns 形如「第 3 页 · 第 2 段」的文案；无信息时返回空串
 */
function formatPosition(
  position: { page: number | null; paragraph: number | null } | null | undefined,
): string {
  if (!position) return '';
  const parts: string[] = [];
  if (position.page !== null && position.page !== undefined) parts.push(`第 ${position.page} 页`);
  if (position.paragraph !== null && position.paragraph !== undefined) {
    parts.push(`第 ${position.paragraph} 段`);
  }
  return parts.join(' · ');
}

/**
 * 引用详情抽屉（KB4 回链，宽 480）。
 *
 * 三种情况分别处理：
 * 1. 引用带 `base_id` + `segment_id`：拉 KB4 取片段全文与前后文；
 * 2. 引用来自旧版本（`status=stale`）：**正常响应**，顶部给降级提示，仍展示保留期内的旧内容；
 * 3. 引用缺少定位信息（历史数据）：直接展示答案里的片段摘要，并引导打开知识库。
 *
 * @param props - 组件属性
 * @returns 引用抽屉
 */
export function CitationDrawer({
  open,
  onOpenChange,
  citation,
}: CitationDrawerProps): React.JSX.Element {
  const baseId = citation?.base_id ?? '';
  const documentId = citation?.document_id ?? '';
  const segmentId = citation?.segment_id ?? '';
  const canTrace = Boolean(baseId && documentId && segmentId);

  const detailQuery = useApiQuery(
    QUERY_KEYS.segment(baseId, documentId, segmentId),
    () => knowledgeExtApi.segmentDetail(baseId, documentId, segmentId, true),
    { enabled: open && canTrace },
  );

  const detail: SegmentDetail | undefined = detailQuery.data;
  const stale = detail?.status === 'stale';
  const position = formatPosition(detail?.position ?? citation?.position ?? null);

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent size="md">
        <DrawerHeader>
          <DrawerTitle className="flex items-center gap-2">
            <Quote aria-hidden="true" className="h-4 w-4 text-primary" />
            引用详情
          </DrawerTitle>
          <DrawerDescription>
            {orPlaceholder(detail?.document_name ?? citation?.document_name, '来源文档')}
            {detail?.base_name ? ` · ${detail.base_name}` : ''}
          </DrawerDescription>
        </DrawerHeader>

        <DrawerBody className="space-y-4">
          {!citation ? (
            <EmptyState
              title="还没有选择引用"
              description="在回答下方点任意一条引用来源，即可在这里查看原文片段与上下文"
            />
          ) : (
            <>
              {stale ? (
                <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {STALE_HINT}
                </p>
              ) : null}

              {canTrace ? (
                detailQuery.isLoading ? (
                  <LoadingState label="正在加载原文片段…" />
                ) : detailQuery.error ? (
                  <ErrorState
                    message={toBusinessMessage(detailQuery.error)}
                    onRetry={() => void detailQuery.refetch()}
                  />
                ) : detail ? (
                  <div className="space-y-4">
                    {position ? (
                      <p className="text-xs text-muted-foreground">位置：{position}</p>
                    ) : (
                      <p className="text-xs text-muted-foreground">位置：该文档未记录页码信息</p>
                    )}

                    {detail.context_before ? (
                      <p className="rounded-md bg-muted/40 p-3 text-sm text-muted-foreground">
                        {detail.context_before}
                      </p>
                    ) : null}

                    <div className="rounded-md border-l-2 border-primary bg-muted/30 p-3 text-sm leading-relaxed">
                      {detail.content ?? citation.segment_content}
                    </div>

                    {detail.context_after ? (
                      <p className="rounded-md bg-muted/40 p-3 text-sm text-muted-foreground">
                        {detail.context_after}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <EmptyState
                    title="该片段暂时无法展示"
                    description="可能已超出保留期，可打开所属知识库查看最新内容"
                  />
                )
              ) : (
                <div className="space-y-3">
                  <div className="rounded-md border-l-2 border-primary bg-muted/30 p-3 text-sm leading-relaxed">
                    {citation.segment_content}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    这条引用来自较早的回答，未记录定位信息，可打开知识库查看原文。
                  </p>
                </div>
              )}
            </>
          )}
        </DrawerBody>

        <DrawerFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          {baseId ? (
            <Button asChild>
              <Link href={`/knowledge/${baseId}?tab=documents`}>
                <ExternalLink className="h-4 w-4" />
                在文档中定位
              </Link>
            </Button>
          ) : null}
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
