'use client';

import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Field, Textarea } from '@/components/ui/input';
import { Rating } from '@/components/ui/rating';
import { Tooltip } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/** 评价内容长度上限（与后端 ≤200 一致） */
const CONTENT_MAX_LENGTH = 200;

/** 评价表单属性 */
export interface ReviewFormProps {
  /** 已选星级（受控） */
  rating: number;
  /** 星级变更 */
  onRatingChange: (rating: number) => void;
  /** 评价内容（受控） */
  content: string;
  /** 内容变更 */
  onContentChange: (content: string) => void;
  /** 是否匿名（受控） */
  anonymous: boolean;
  /** 匿名开关变更 */
  onAnonymousChange: (anonymous: boolean) => void;
  /** 提交 */
  onSubmit: () => void;
  /** 是否提交中 */
  submitting?: boolean;
  /** 是否可评价（未使用过插件时后端返回 16011，这里提前置灰） */
  canReview?: boolean;
  /** 不可评价时的原因文案 */
  blockedReason?: string;
  className?: string;
}

/**
 * 评价表单（PL12）。
 *
 * 校验：星级 1-5（必选）、内容 ≤200 字、匿名开关可选；
 * 前端只拦「空星级」这一条（后端对 rating 越界返回参数类错误，文案为「1 到 5 星」）。
 *
 * @param props - 组件属性
 * @returns 表单元素
 */
export function ReviewForm({
  rating,
  onRatingChange,
  content,
  onContentChange,
  anonymous,
  onAnonymousChange,
  onSubmit,
  submitting = false,
  canReview = true,
  blockedReason = '使用过这个插件才能评价',
  className,
}: ReviewFormProps): React.JSX.Element {
  const disabled = !canReview || submitting;

  return (
    <form
      className={cn('space-y-3', className)}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <Field label="你的评分" required>
        <Rating value={rating} onChange={onRatingChange} readOnly={!canReview} label="选择评分" />
      </Field>

      <Field
        label="评价内容"
        htmlFor="review-content"
        hint={`最多 ${CONTENT_MAX_LENGTH} 字，可以说说哪一步最省事、哪里还需要改进`}
      >
        <Textarea
          id="review-content"
          value={content}
          rows={3}
          maxLength={CONTENT_MAX_LENGTH}
          disabled={disabled}
          onChange={(event) => onContentChange(event.target.value)}
          placeholder="例如：方案确认那一步很省时间，希望能支持自定义模板"
        />
      </Field>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="h-4 w-4 cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-60"
          checked={anonymous}
          disabled={disabled}
          onChange={(event) => onAnonymousChange(event.target.checked)}
        />
        匿名评价（列表显示为「某位同事」）
      </label>

      <div className="flex items-center gap-2">
        <Tooltip content={blockedReason} disabled={canReview}>
          <span>
            <Button type="submit" size="sm" disabled={disabled || rating < 1}>
              {submitting ? '提交中…' : '提交评价'}
            </Button>
          </span>
        </Tooltip>
        {rating < 1 ? (
          <span className="text-xs text-muted-foreground">先点星星选出 1-5 星</span>
        ) : null}
      </div>
    </form>
  );
}
