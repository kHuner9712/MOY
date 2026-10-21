'use client';

import { Check, Minus } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { formatCents, formatUnits, orPlaceholder } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { PlanItem } from '@/lib/types';

/** 套餐对比属性 */
export interface PlanCompareProps {
  /** 套餐清单（BL1 返回，已按展示顺序排列） */
  plans: PlanItem[];
  /** 当前套餐标识（用于高亮） */
  currentPlanCode: string;
  /** 选择某个套餐 */
  onChoose?: (plan: PlanItem) => void;
  /** 是否有变更套餐权限（无权限时按钮置灰） */
  canPurchase?: boolean;
}

/**
 * 套餐对比：三档并排，当前档高亮。
 *
 * 口径：
 * - 价格与额度全部由 BL1 返回，前端只做**展示换算**（分 → 元、字数 → 万字），
 *   不硬编码任何额度数值；
 * - 功能开关逐项列出，未包含的项用灰色「不含」表达，不用「暂不支持」这类占位说法；
 * - 当前套餐按钮置灰并标注「当前套餐」，避免用户误点后产生无意义订单。
 *
 * @param props - 组件属性
 * @returns 套餐对比卡片
 */
export function PlanCompare({
  plans,
  currentPlanCode,
  onChoose,
  canPurchase = true,
}: PlanCompareProps): React.JSX.Element {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {plans.map((plan) => {
        const isCurrent = plan.plan_code === currentPlanCode;
        const purchasable = plan.status === 'available';
        return (
          <section
            key={plan.plan_code}
            aria-current={isCurrent ? 'true' : undefined}
            className={cn(
              'flex flex-col gap-3 rounded-lg border p-4',
              isCurrent ? 'border-primary ring-2 ring-primary/30' : 'border-border',
            )}
          >
            <header className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">
                  {orPlaceholder(plan.display_name, plan.plan_code)}
                </h3>
                {isCurrent ? (
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                    当前套餐
                  </span>
                ) : null}
              </div>
              <p className="text-2xl font-semibold tabular-nums">
                {formatCents(plan.price_per_seat_month_cents, plan.currency)}
                <span className="ml-1 text-xs font-normal text-muted-foreground">/席位/月</span>
              </p>
              <p className="text-xs text-muted-foreground">
                每位成员每月 {formatUnits(plan.quota_units_per_seat_month, '字')}
                {plan.trial_days > 0 ? `，可试用 ${plan.trial_days} 天` : ''}
              </p>
            </header>

            <ul className="flex-1 space-y-1.5 text-sm">
              {plan.features.map((feature) => (
                <li key={feature.feature_key} className="flex items-start gap-2">
                  {feature.included ? (
                    <Check aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                  ) : (
                    <Minus aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <span className={feature.included ? '' : 'text-muted-foreground'}>
                    {feature.display_name}
                    {feature.included && feature.limit !== null
                      ? `（上限 ${feature.limit}）`
                      : ''}
                  </span>
                </li>
              ))}
            </ul>

            <Button
              type="button"
              variant={isCurrent ? 'outline' : 'default'}
              disabled={isCurrent || !purchasable || !canPurchase}
              onClick={() => onChoose?.(plan)}
            >
              {isCurrent ? '当前套餐' : purchasable ? '选择该套餐' : '该套餐已下架'}
            </Button>
          </section>
        );
      })}
    </div>
  );
}
