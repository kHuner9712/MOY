'use client';

import * as React from 'react';

import { EmptyState, LoadingState } from '@/components/ui/feedback';
import { Rating } from '@/components/ui/rating';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { PluginReviewItem } from '@/lib/types';

/** 评价列表属性 */
export interface ReviewListProps {
  /** 当前页评价 */
  items: PluginReviewItem[];
  /** 是否加载中 */
  loading?: boolean;
  /** 总条数（页面据此渲染分页器） */
  total: number;
  className?: string;
}

/**
 * 评价列表（PL11）。
 *
 * 0 条评价时必须给空态文案「还没有人评价…」，**不允许**渲染空表格
 * （PRD TC-P3-07 ③ 明确断言「不存在空 `<table>`」）。
 *
 * @param props - 组件属性
 * @returns 评价列表元素
 */
export function ReviewList({
  items,
  loading = false,
  total,
  className,
}: ReviewListProps): React.JSX.Element {
  if (loading) {
    return <LoadingState label="正在加载评价…" className={className} />;
  }

  if (items.length === 0) {
    return (
      <EmptyState
        className={className}
        title="还没有人评价"
        description={
          total > 0
            ? '当前页没有评价，可以翻到其他页看看'
            : '用过后可以来说说体验，帮助同事判断是否适合自己'
        }
      />
    );
  }

  return (
    <Table className={className}>
      <TableHeader>
        <TableRow>
          <TableHead>评价人</TableHead>
          <TableHead>评分</TableHead>
          <TableHead>内容</TableHead>
          <TableHead>时间</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((review) => (
          <TableRow key={review.review_id}>
            <TableCell className="whitespace-nowrap text-sm">
              {orPlaceholder(review.author_name, '某位同事')}
              {review.department_name ? (
                <span className="ml-1 text-xs text-muted-foreground">
                  （{review.department_name}）
                </span>
              ) : null}
            </TableCell>
            <TableCell>
              <Rating
                value={review.rating}
                readOnly
                size="sm"
                showValue={false}
                label="这条评价的评分"
              />
            </TableCell>
            <TableCell className="max-w-[24rem] text-sm">{orPlaceholder(review.content)}</TableCell>
            <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
              {formatDateTime(review.created_at)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
