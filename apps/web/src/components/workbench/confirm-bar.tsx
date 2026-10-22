'use client';

import { AlertTriangle } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { orPlaceholder } from '@/lib/format';
import type { TaskConfirmInfo } from '@/lib/types';

/** 人工接管确认条属性 */
export interface ConfirmBarProps {
  /** 是否展示（任务处于 waiting_confirm） */
  open: boolean;
  /** 接管信息（A3 的 confirm） */
  confirm?: TaskConfirmInfo | null;
  /** 是否处理中 */
  busy?: boolean;
  /** 确认继续（原地推进，不新建任务） */
  onContinue: () => void;
  /** 补充要求后重做（新建任务，只重跑该步及下游） */
  onRedo: () => void;
  /** 放弃本次（取消任务） */
  onCancel: () => void;
}

/**
 * 人工接管确认条（`waiting_confirm`）。
 *
 * 三个动作语义来自 WF1：继续 = `decision=continue`（同任务续跑）、
 * 重做 = `decision=redo`（新任务，仅重跑该步及下游）、放弃 = 取消任务（A5）。
 * 这里是**任务状态**而不是错误：不弹错误态、不用红色警示语。
 *
 * @param props - 组件属性
 * @returns 确认条元素
 */
export function ConfirmBar({
  open,
  confirm,
  busy = false,
  onContinue,
  onRedo,
  onCancel,
}: ConfirmBarProps): React.JSX.Element | null {
  if (!open) return null;

  const summaryEntries = Object.entries(confirm?.summary ?? {});

  return (
    <section
      aria-label="等待你确认"
      className="sticky top-0 z-20 space-y-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="space-y-1">
          <p className="font-medium">
            任务已暂停，等你确认后继续（第 {confirm?.step_seq ?? '—'} 步）
          </p>
          <p className="text-amber-900/80">
            {orPlaceholder(confirm?.reason, '确认内容后可继续执行；这不是故障，确认后任务会从当前步骤继续')}
          </p>
        </div>
      </div>

      {summaryEntries.length > 0 ? (
        <dl className="grid gap-1.5 rounded-md bg-amber-100/60 p-2.5 sm:grid-cols-2">
          {summaryEntries.map(([key, value]) => (
            <div key={key} className="flex flex-col">
              <dt className="text-[11px] text-amber-900/70">{key}</dt>
              <dd className="break-words text-sm">{String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" disabled={busy} onClick={onContinue}>
          {busy ? '处理中…' : '确认继续'}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onRedo}>
          补充要求后重做
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
          放弃本次
        </Button>
        <span className="text-xs text-amber-900/70">15 分钟内不会自动继续，你可以先去处理别的事</span>
      </div>
    </section>
  );
}
