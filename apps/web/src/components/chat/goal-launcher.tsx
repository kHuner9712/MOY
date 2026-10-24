'use client';

/**
 * 目标式发起器（W1 切片 B，对话页顶部）。
 *
 * 职责（架构 §1.5.6 / PRD AC-B1~B8）：
 * - 输入业务语言目标，300ms 防抖后拉取规则推荐；**空目标不请求**；
 * - 每张卡展示命中理由（「匹配到：周报、本周（来自名称、简介）」）与所需输入
 *   （「需要准备：汇报周期（必填）、本周重点（选填）」），用户看得到「为什么给我这个」；
 * - 插件卡「开始」→ `/workbench/{pluginId}?goal=…`（工作台预填并随任务提交）；
 *   工具卡不是可发起任务的工作台，落地到插件市场（诚实动作）；
 * - 无命中走目录兜底 + 引导卡：不得空列表、不得占位词；
 * - 三态齐备：空输入引导 / 加载骨架 / 错误可重试。
 */

import { ArrowRight, Search } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/feedback';
import { Input } from '@/components/ui/input';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { capabilitiesApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { orPlaceholder } from '@/lib/format';
import type { CapabilityCard, CapabilityInputBrief } from '@/lib/types';

/** 输入防抖间隔（毫秒） */
const DEBOUNCE_MS = 300;

/** 推荐返回条数（与接口缺省一致） */
const RECOMMEND_LIMIT = 3;

/** 示例目标（点击即填入并立即推荐） */
const GOAL_EXAMPLES: readonly string[] = ['整理本周周报', '起草一封邮件', '整理会议纪要'];

/** 命中字段展示名（契约字段名 → 业务语言，用户看不到字段名） */
const FIELD_LABELS: Readonly<Record<string, string>> = Object.freeze({
  name: '名称',
  summary: '简介',
  category: '分类',
  'inputs.label': '输入项',
  operations: '能力清单',
});

/**
 * 命中理由行：「匹配到：周报、本周（来自名称、简介）」。
 *
 * 兜底目录卡没有命中信息（空数组），返回空串即不渲染该行。
 *
 * @param card - 推荐卡
 * @returns 理由文案；无命中信息时返回空串
 */
function buildMatchReason(card: CapabilityCard): string {
  const terms = card.matched_terms.filter((term) => term.trim().length > 0);
  if (terms.length === 0) {
    return '';
  }
  const fields = card.matched_fields
    .map((field) => FIELD_LABELS[field])
    .filter((label): label is string => Boolean(label));
  const suffix = fields.length > 0 ? `（来自${fields.join('、')}）` : '';
  return `匹配到：${terms.join('、')}${suffix}`;
}

/**
 * 所需输入行：「需要准备：汇报周期（必填）、本周重点（选填）」。
 *
 * 工具卡 `inputs` 为空数组（无「所需输入」概念），返回空串即不渲染该区。
 *
 * @param inputs - 所需输入提示列表
 * @returns 所需输入文案；无输入时返回空串
 */
function buildInputsHint(inputs: CapabilityInputBrief[]): string {
  const parts = inputs
    .filter((input) => input.label.trim().length > 0)
    .map((input) => `${input.label}（${input.required ? '必填' : '选填'}）`);
  return parts.length > 0 ? `需要准备：${parts.join('、')}` : '';
}

/**
 * 单张能力推荐卡。
 *
 * @param props - 组件属性
 * @returns 推荐卡元素
 */
function CapabilityCardView({ card, goal }: { card: CapabilityCard; goal: string }): React.JSX.Element {
  const router = useRouter();
  const reason = buildMatchReason(card);
  const inputsHint = buildInputsHint(card.inputs);
  // 工具卡当前由演示连接器支撑（CAP-01：5 个连接器均未真实化）。
  // D-38 要求「标注演示 + 不引导发起连接」：徽标照实标出成熟度，
  // 但**不**给工具卡加「开始/发起」按钮——点下去不会有真实结果，
  // 那是把演示当可用（PRD §2.3 原则 1）。插件卡有真实工作台，故不加此徽标。
  const isDemoTool = card.kind === 'tool';

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">{orPlaceholder(card.name, '未命名能力')}</CardTitle>
          <div className="flex items-center gap-1.5">
            <Badge variant="outline">{card.kind === 'plugin' ? '工作台' : '办公工具'}</Badge>
            {isDemoTool ? <Badge variant="outline">演示</Badge> : null}
          </div>
        </div>
        {card.summary ? <CardDescription>{card.summary}</CardDescription> : null}
      </CardHeader>
      <CardContent className="space-y-2">
        {reason ? <p className="text-sm">{reason}</p> : null}
        {inputsHint ? <p className="text-sm text-muted-foreground">{inputsHint}</p> : null}
        <div className="flex items-center gap-2 pt-1">
          {card.kind === 'plugin' ? (
            <Button
              type="button"
              size="sm"
              onClick={() => {
                router.push(`/workbench/${card.id}?goal=${encodeURIComponent(goal)}`);
              }}
            >
              开始
              <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <Button asChild size="sm" variant="outline">
              <Link href="/plugins">去插件市场了解</Link>
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * 目标式发起器。
 *
 * @returns 发起器元素
 */
export function GoalLauncher(): React.JSX.Element {
  const [goal, setGoal] = React.useState<string>('');
  // 实际用于查询的目标：由输入防抖 300ms 后落地，Enter / 示例点击立即落地
  const [queryGoal, setQueryGoal] = React.useState<string>('');
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearDebounce = React.useCallback((): void => {
    if (debounceRef.current !== null) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
  }, []);

  // 卸载时清掉未触发的定时器，避免对已卸载组件 setState
  React.useEffect(() => clearDebounce, [clearDebounce]);

  // 空目标不请求：`enabled` 守住「没有目标就没有推荐」的边界
  const recommendQuery = useApiQuery(
    QUERY_KEYS.capabilityRecommend(queryGoal),
    () => capabilitiesApi.recommend(queryGoal, RECOMMEND_LIMIT),
    { enabled: queryGoal.length > 0 },
  );

  /** 输入变更：防抖 300ms 后触发推荐 */
  const handleGoalChange = (value: string): void => {
    setGoal(value);
    clearDebounce();
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null;
      setQueryGoal(value.trim());
    }, DEBOUNCE_MS);
  };

  /** Enter / 示例点击 / 查找按钮：立即触发推荐 */
  const submitGoalNow = (value: string): void => {
    setGoal(value);
    clearDebounce();
    setQueryGoal(value.trim());
  };

  const data = recommendQuery.data;

  return (
    <section className="space-y-2" aria-label="目标式发起">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={goal}
            onChange={(event) => {
              handleGoalChange(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                submitGoalNow(goal);
              }
            }}
            placeholder="说说你想完成的事，例如：把本周的记录整理成周报"
            aria-label="输入你想完成的事"
            className="pl-9"
          />
        </div>
        <Button
          type="button"
          onClick={() => {
            submitGoalNow(goal);
          }}
          disabled={goal.trim().length === 0}
        >
          查找能力
        </Button>
      </div>

      {queryGoal.length === 0 ? (
        <div className="rounded-lg border border-dashed px-4 py-5 text-center">
          <p className="text-sm font-medium">说说你想完成的事</p>
          <p className="mt-1 text-sm text-muted-foreground">
            用业务语言描述目标，就能找到合适的工作台与办公工具，也可以点下面的例子试试
          </p>
          <div className="mt-3 flex flex-wrap justify-center gap-2">
            {GOAL_EXAMPLES.map((example) => (
              <Button
                key={example}
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  submitGoalNow(example);
                }}
              >
                {example}
              </Button>
            ))}
          </div>
        </div>
      ) : recommendQuery.isLoading ? (
        <div className="grid gap-3 lg:grid-cols-3">
          {Array.from({ length: RECOMMEND_LIMIT }, (_, index) => (
            <Skeleton key={index} className="h-32 w-full" />
          ))}
        </div>
      ) : recommendQuery.error ? (
        <ErrorState
          message={toBusinessMessage(recommendQuery.error)}
          onRetry={() => {
            void recommendQuery.refetch();
          }}
        />
      ) : data ? (
        <div className="max-h-72 space-y-3 overflow-y-auto">
          {data.fallback ? (
            <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900">
              <p className="font-medium">没有找到完全匹配的能力</p>
              <p className="mt-1">
                {orPlaceholder(data.fallback_hint, '可以换种说法，或去插件市场浏览全部能力')}
              </p>
              <Button asChild size="sm" variant="outline" className="mt-2">
                <Link href="/plugins">去插件市场浏览</Link>
              </Button>
            </div>
          ) : null}

          {data.items.length > 0 ? (
            <div className="grid gap-3 lg:grid-cols-3">
              {data.items.map((card) => (
                <CapabilityCardView key={card.id} card={card} goal={queryGoal} />
              ))}
            </div>
          ) : (
            <EmptyState
              title="没有取到推荐结果"
              description="可以去插件市场浏览全部能力，或换个说法再试"
              action={
                <Button asChild size="sm">
                  <Link href="/plugins">去插件市场</Link>
                </Button>
              }
            />
          )}
        </div>
      ) : null}
    </section>
  );
}
