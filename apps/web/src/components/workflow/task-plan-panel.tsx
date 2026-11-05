'use client';

import { AlertTriangle, CalendarClock, CheckCircle2, ListChecks } from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { orPlaceholder } from '@/lib/format';
import type { TaskPlan, TaskPlanStep } from '@/lib/types';

/** 计划面板属性 */
export interface TaskPlanPanelProps {
  /** A3 返回的执行计划 */
  plan: TaskPlan;
  /** 是否有计划写操作在途 */
  busy?: boolean;
  /** 保存勾选调整（调用方负责带 `plan.version` 调 P1） */
  onUpdate: (steps: Array<{ seq: number; selected: boolean }>) => void;
  /** 确认并开始执行（P2） */
  onConfirm: () => void;
  /** 放弃本次（A5 取消任务） */
  onCancel: () => void;
  /** 当前时间（毫秒；测试注入，缺省取 `Date.now()`） */
  now?: number;
}

/** 从选中态构建草稿映射 */
function toDraft(plan: TaskPlan): Record<number, boolean> {
  return Object.fromEntries(plan.steps.map((step) => [step.seq, step.selected]));
}

/** 草稿与计划是否存在差异 */
function isDirty(plan: TaskPlan, draft: Record<number, boolean>): boolean {
  return plan.steps.some((step) => (draft[step.seq] ?? step.selected) !== step.selected);
}

/**
 * 计划剩余时间文案。
 *
 * @param remainingSeconds - 剩余秒数
 * @returns 业务语言文案
 */
function describeRemaining(remainingSeconds: number | null): string {
  if (remainingSeconds === null) return '请尽快确认计划';
  if (remainingSeconds <= 0) return '计划已过期，不会再执行';
  const minutes = Math.floor(remainingSeconds / 60);
  const seconds = remainingSeconds % 60;
  if (minutes <= 0) return `请在 ${seconds} 秒内确认`;
  return `请在 ${minutes} 分 ${seconds} 秒内确认`;
}

/** 单个步骤行属性 */
interface StepRowProps {
  step: TaskPlanStep;
  selected: boolean;
  disabled: boolean;
  onToggle: (seq: number) => void;
}

/**
 * 步骤行：勾选框 + 工具/产物标签 + 说明。
 *
 * @param props - 行属性
 * @returns 行元素
 */
function StepRow({ step, selected, disabled, onToggle }: StepRowProps): React.JSX.Element {
  const checkboxId = `plan-step-${step.seq}`;
  return (
    <li
      className={
        selected
          ? 'rounded-md border border-border bg-card px-3 py-2.5'
          : 'rounded-md border border-dashed border-border px-3 py-2.5 opacity-70'
      }
    >
      <div className="flex items-start gap-2.5">
        <input
          id={checkboxId}
          type="checkbox"
          className="mt-1 h-4 w-4 shrink-0 accent-emerald-600 disabled:cursor-not-allowed"
          checked={selected}
          disabled={disabled || !step.skippable}
          onChange={() => onToggle(step.seq)}
        />
        <div className="min-w-0 flex-1 space-y-1">
          <label htmlFor={checkboxId} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground tabular-nums">第 {step.seq} 步</span>
            <span className="font-medium">{orPlaceholder(step.title, '未命名步骤')}</span>
            {!step.skippable ? (
              <Badge variant="outline" className="text-[11px]">
                必做步骤
              </Badge>
            ) : null}
            {step.tool_type ? (
              <Badge variant="secondary" className="text-[11px]">
                {step.tool_type}
                {step.tool_operation ? ` · ${step.tool_operation}` : ''}
              </Badge>
            ) : (
              <Badge variant="outline" className="text-[11px]">
                {orPlaceholder(step.action, '处理')}
              </Badge>
            )}
          </label>
          {step.expected_artifacts.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              预计产物：{step.expected_artifacts.join('、')}
            </p>
          ) : null}
          {step.note ? <p className="text-xs text-amber-700">{step.note}</p> : null}
        </div>
      </div>
    </li>
  );
}

/**
 * 执行计划确认面板（矩阵第 2 项）。
 *
 * 三条交互：勾选/取消勾选步骤（P1）、确认并执行（P2）、放弃本次（A5）。
 * **确认前面板只读计划，不触发任何有副作用的执行**：页面仅在
 * `status === 'waiting_plan'` 时挂载本面板，后端在该状态下也不会推进任何步骤。
 *
 * 勾选改动必须先「保存调整」才能「确认并执行」：确认请求只带 `plan_version`，
 * 若直接确认会把**服务端旧勾选**执行掉，与用户在界面上看到的不一致。
 *
 * @param props - 组件属性
 * @returns 面板元素
 */
export function TaskPlanPanel({
  plan,
  busy = false,
  onUpdate,
  onConfirm,
  onCancel,
  now,
}: TaskPlanPanelProps): React.JSX.Element {
  const [draft, setDraft] = React.useState<Record<number, boolean>>(() => toDraft(plan));

  // 草稿只随**计划版本**重置：轮询会用同一份计划反复重渲染（对象引用每次都新），
  // 若依赖 `plan` 对象会把用户未保存的勾选每 5 秒清一次。页面用
  // `key={task_id + version}` 挂载本组件，版本变化时整体重挂载即回到服务端事实。

  const [clockMs, setClockMs] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (now !== undefined) return undefined;
    setClockMs(Date.now());
    const timer = setInterval(() => setClockMs(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [now]);

  const currentMs = now ?? clockMs;
  const expiresMs = Date.parse(plan.expires_at);
  const remainingSeconds =
    currentMs !== null && Number.isFinite(expiresMs)
      ? Math.max(0, Math.ceil((expiresMs - currentMs) / 1_000))
      : null;
  const expired = remainingSeconds !== null && remainingSeconds <= 0;

  const dirty = isDirty(plan, draft);
  const selectedCount = plan.steps.filter((step) => draft[step.seq] ?? step.selected).length;

  const toggle = React.useCallback((seq: number): void => {
    setDraft((prev) => ({ ...prev, [seq]: !(prev[seq] ?? false) }));
  }, []);

  const handleSave = React.useCallback((): void => {
    onUpdate(
      plan.steps.map((step) => ({ seq: step.seq, selected: draft[step.seq] ?? step.selected })),
    );
  }, [draft, onUpdate, plan.steps]);

  return (
    <section
      aria-label="执行计划"
      className="sticky top-0 z-20 space-y-3 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-950"
    >
      <div className="flex items-start gap-2">
        <ListChecks aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="space-y-1">
          <p className="font-medium">
            执行计划已生成，共 {plan.steps.length} 步，已纳入 {selectedCount} 步
          </p>
          <p className="text-emerald-950/80">
            确认后才会真正执行；你可以先取消不想要的步骤，再确认执行。
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <CalendarClock aria-hidden="true" className="h-3.5 w-3.5" />
        <span className={expired ? 'font-medium text-red-700' : 'text-emerald-950/80'}>
          {describeRemaining(remainingSeconds)}
        </span>
        {expired ? <Badge variant="destructive">已过期</Badge> : null}
      </div>

      {expired ? (
        <p className="flex items-start gap-2 rounded-md bg-red-50 px-3 py-2 text-xs text-red-800">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          计划在确认前已超时，本次不会执行；如需继续请重新发起任务。
        </p>
      ) : null}

      <ul className="space-y-2">
        {plan.steps.map((step) => (
          <StepRow
            key={step.seq}
            step={step}
            selected={draft[step.seq] ?? step.selected}
            disabled={busy || expired}
            onToggle={toggle}
          />
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy || expired || !dirty}
          onClick={handleSave}
        >
          保存调整
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy || expired || dirty}
          title={dirty ? '请先保存调整，再确认执行' : undefined}
          onClick={onConfirm}
        >
          <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
          {busy ? '处理中…' : '确认并执行'}
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
          放弃本次
        </Button>
        {dirty && !expired ? (
          <span className="text-xs text-amber-700">有未保存的调整，请先保存再确认</span>
        ) : null}
      </div>
    </section>
  );
}
