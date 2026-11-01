'use client';

import { AlertTriangle, Check, ChevronDown, Circle, Loader2, SkipForward } from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/**
 * 时间线步骤状态（与 DDL `plugin_task_step.status` 逐字一致，五态）。
 *
 * 说明：`waiting_confirm` 属于**任务级**状态（该步此时仍是 pending），
 * 不在此枚举内；任务级的「等待确认」由页面顶部确认条表达（B24）。
 */
export type TimelineStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

/** 步骤失败信息（业务语言，禁止出现错误码） */
export interface TimelineStepFailure {
  reason_text: string;
  retryable: boolean;
  operator_action: string;
}

/** 时间线单个步骤 */
export interface TimelineStep {
  /** 稳定标识：只用于 React key 与展开状态，**不得写入 DOM**（TC-P3-11 判据⑤） */
  id: string;
  /** 步骤序号（1 起，连续） */
  sequence: number;
  /** 步骤标题（插件声明里的用户可见文案） */
  title: string;
  status: TimelineStatus;
  /** 一句话结果摘要 */
  summary?: string | null;
  /** 失败信息；非失败步骤传 null */
  failure?: TimelineStepFailure | null;
  /** 耗时（毫秒），用于「用了 12 秒」这类说明 */
  durationMs?: number | null;
  /** 产物插槽（由业务侧传 ArtifactCard 列表） */
  artifacts?: React.ReactNode;
  /** 操作插槽（重试本步 / 去授权等） */
  actions?: React.ReactNode;
}

/** 时间线属性 */
export interface TimelineProps {
  /** 步骤列表（按 sequence 升序传入） */
  steps: TimelineStep[];
  /** 失败步骤是否默认展开，默认 true */
  defaultExpandFailed?: boolean;
  /** 空态文案（步骤为 0 时展示） */
  emptyHint?: string;
  className?: string;
}

/** 状态 → 展示文案（业务语言，不出现任何内部取值） */
const STATUS_LABELS: Record<TimelineStatus, string> = {
  pending: '等待开始',
  running: '进行中',
  completed: '已完成',
  failed: '未完成',
  skipped: '已跳过',
};

/** 状态 → 徽标变体（复用既有徽标色板） */
const STATUS_VARIANTS: Record<
  TimelineStatus,
  'success' | 'warning' | 'destructive' | 'secondary' | 'outline'
> = {
  pending: 'outline',
  running: 'secondary',
  completed: 'success',
  failed: 'destructive',
  skipped: 'outline',
};

/** 状态 → 节点图标 */
const STATUS_ICONS: Record<TimelineStatus, typeof Circle> = {
  pending: Circle,
  running: Loader2,
  completed: Check,
  failed: AlertTriangle,
  skipped: SkipForward,
};

/** 状态 → 节点配色（设计令牌） */
const STATUS_DOT_CLASS: Record<TimelineStatus, string> = {
  pending: 'border-muted-foreground/30 bg-background text-muted-foreground',
  running: 'border-primary/40 bg-primary/10 text-primary',
  completed: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  failed: 'border-destructive/40 bg-destructive/10 text-destructive',
  skipped: 'border-muted-foreground/30 bg-muted text-muted-foreground',
};

/**
 * 把耗时可读化。
 *
 * @param durationMs - 耗时（毫秒）
 * @returns 形如「12 秒」/「1 分 20 秒」的文本
 */
function formatDuration(durationMs: number | null | undefined): string {
  if (typeof durationMs !== 'number' || !Number.isFinite(durationMs) || durationMs < 0) return '';
  if (durationMs < 1000) return '不到 1 秒';
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

/**
 * 任务步骤时间线（垂直，失败步骤默认展开并高亮）。
 *
 * 设计约束：
 * - **失败是状态不是错误页**：失败步骤只在本节点内展示原因与建议动作（继续/重试/放弃），
 *   不允许把整页替换成错误态；
 * - 步骤标识只用于 React key，**不写入 DOM**（不出现 `data-step-id` 之类的属性）；
 * - 产物与操作通过插槽注入，时间线本身不感知业务字段。
 *
 * @param props - 组件属性
 * @returns 时间线元素
 */
export function Timeline({
  steps,
  defaultExpandFailed = true,
  emptyHint = '还没有可展示的步骤',
  className,
}: TimelineProps): React.JSX.Element {
  // 手动切换的显式状态：点过就以用户的选择为准，没点过才走默认策略。
  // 旧实现用 `collapsed: Set` + 「失败步骤默认展开」推导展开态，导致**非失败
  // 步骤点「详情」永远展不开**（展开判定恒为 false）——已完成步骤的摘要与
  // 产物下载入口在页面上不可见（第十三批补测发现的缺陷）。
  const [manual, setManual] = React.useState<ReadonlyMap<string, boolean>>(new Map());

  const isExpanded = (step: TimelineStep): boolean => {
    const override = manual.get(step.id);
    if (override !== undefined) {
      return override;
    }
    return step.failure ? defaultExpandFailed : false;
  };

  const toggle = (id: string, expanded: boolean): void => {
    setManual((prev) => new Map(prev).set(id, !expanded));
  };

  if (steps.length === 0) {
    return (
      <p className={cn('py-8 text-center text-sm text-muted-foreground', className)}>{emptyHint}</p>
    );
  }

  return (
    <ol className={cn('space-y-0', className)} aria-label="步骤时间线">
      {steps.map((step, index) => {
        const Icon = STATUS_ICONS[step.status];
        const expanded = isExpanded(step);
        const hasDetail = Boolean(step.failure || step.summary || step.artifacts || step.actions);
        const isLast = index === steps.length - 1;
        const duration = formatDuration(step.durationMs);

        return (
          <li key={step.id} className="relative flex gap-3 pb-4 last:pb-0">
            {/* 连接线：最后一个节点不画 */}
            {!isLast ? (
              <span
                aria-hidden="true"
                className="absolute left-[13px] top-7 h-[calc(100%-1.75rem)] w-px bg-border"
              />
            ) : null}

            <span
              className={cn(
                'relative z-10 mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border',
                STATUS_DOT_CLASS[step.status],
              )}
            >
              <Icon
                aria-hidden="true"
                className={cn('h-3.5 w-3.5', step.status === 'running' && 'animate-spin')}
              />
            </span>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium tabular-nums">{step.sequence}.</span>
                <span className="text-sm font-medium">{step.title}</span>
                <Badge variant={STATUS_VARIANTS[step.status]}>{STATUS_LABELS[step.status]}</Badge>
                {duration ? (
                  <span className="text-xs text-muted-foreground">用了 {duration}</span>
                ) : null}
                {hasDetail ? (
                  <button
                    type="button"
                    aria-expanded={expanded}
                    onClick={() => toggle(step.id, expanded)}
                    className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    {expanded ? '收起' : '详情'}
                    <ChevronDown
                      aria-hidden="true"
                      className={cn('h-3.5 w-3.5 transition-transform', expanded && 'rotate-180')}
                    />
                  </button>
                ) : null}
              </div>

              {expanded ? (
                <div className="mt-2 space-y-2 rounded-md border bg-muted/20 p-3">
                  {step.failure ? (
                    <div className="space-y-1">
                      <p className="flex items-start gap-1.5 text-sm text-destructive">
                        <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <span>{step.failure.reason_text}</span>
                      </p>
                      <p className="text-xs text-muted-foreground">
                        建议：{step.failure.operator_action}
                        {step.failure.retryable ? '（可重试）' : '（需先处理前置条件）'}
                      </p>
                    </div>
                  ) : null}

                  {step.summary ? (
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                      {step.summary}
                    </p>
                  ) : null}

                  {step.artifacts ? <div className="space-y-2">{step.artifacts}</div> : null}

                  {step.actions ? (
                    <div className="flex flex-wrap items-center gap-2 pt-1">{step.actions}</div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
