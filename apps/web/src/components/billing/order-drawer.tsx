'use client';

import { CheckCircle2, CreditCard } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Drawer,
  DrawerBody,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer';
import { EmptyState, ErrorState } from '@/components/ui/feedback';
import { Field, Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation } from '@/hooks/use-api';
import { billingApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatCents, formatDateTime, orPlaceholder } from '@/lib/format';
import type { CreateOrderResult, PlanItem } from '@/lib/types';

/** 支付方式选项（业务语言） */
const PAYMENT_OPTIONS: Array<{ value: 'online' | 'offline_transfer'; label: string }> = [
  { value: 'online', label: '在线支付（下单后可立即完成）' },
  { value: 'offline_transfer', label: '对公转账（按页面指引汇款）' },
];

/** 单次购买席位数上限（与后端一致） */
const MAX_SEATS = 100_000;

/** 下单抽屉属性 */
export interface OrderDrawerProps {
  /** 是否打开 */
  open: boolean;
  /** 开关回调 */
  onOpenChange: (open: boolean) => void;
  /** 目标套餐；null 时抽屉展示空态（避免空白抽屉） */
  plan: PlanItem | null;
  /** 订单类型（新购/续费/升级/加席） */
  orderType?: 'new' | 'renew' | 'upgrade' | 'seats';
  /** 默认席位数 */
  defaultSeats?: number;
  /** 下单成功回调（页面可据此刷新订阅与订单列表） */
  onCreated?: (order: CreateOrderResult) => void;
}

/**
 * 下单抽屉（BL6）：一步填信息 → 二步确认 → 三步出支付指引。
 *
 * 设计要点（PRD「3 步内完成」）：
 * - 第 1 步在抽屉里选席位与支付方式，实时显示应付金额（分 → 元）；
 * - 第 2 步用二次确认弹窗说清「买什么、多少钱、何时生效」，避免误下单；
 * - 第 3 步展示在线支付入口或对公转账信息，订单号可复制给财务；
 * - 已存在待支付订单（15004）、套餐下架（15002）等错误直接用业务语言提示。
 *
 * @param props - 组件属性
 * @returns 下单抽屉
 */
export function OrderDrawer({
  open,
  onOpenChange,
  plan,
  orderType = 'new',
  defaultSeats,
  onCreated,
}: OrderDrawerProps): React.JSX.Element {
  const [seats, setSeats] = React.useState<number>(defaultSeats ?? 1);
  const [paymentMethod, setPaymentMethod] = React.useState<'online' | 'offline_transfer'>('online');
  const [confirmOpen, setConfirmOpen] = React.useState<boolean>(false);
  const [created, setCreated] = React.useState<CreateOrderResult | null>(null);
  const [submitError, setSubmitError] = React.useState<string>('');

  // 每次打开重置为干净状态，避免上一次的支付结果串到新订单
  React.useEffect(() => {
    if (!open) return;
    setSeats(Math.max(defaultSeats ?? plan?.min_seats ?? 1, plan?.min_seats ?? 1));
    setPaymentMethod('online');
    setCreated(null);
    setSubmitError('');
  }, [open, defaultSeats, plan]);

  const createOrder = useApiMutation(
    (variables: { planCode: string; seatCount: number; method: 'online' | 'offline_transfer' }) =>
      billingApi.createOrder({
        plan_code: variables.planCode,
        seats: variables.seatCount,
        order_type: orderType,
        payment_method: variables.method,
      }),
    {
      invalidateKeys: [QUERY_KEYS.subscription(), QUERY_KEYS.billingOrders()],
      onSuccess: (data) => {
        setCreated(data);
        setConfirmOpen(false);
        onCreated?.(data);
      },
      onError: (error) => {
        setSubmitError(toBusinessMessage(error));
        setConfirmOpen(false);
        toast.error(toBusinessMessage(error));
      },
    },
  );

  const safeSeats = Math.min(Math.max(Math.trunc(seats) || 1, plan?.min_seats ?? 1), MAX_SEATS);
  const amountCents = plan ? plan.price_per_seat_month_cents * safeSeats : 0;

  /** 提交下单 */
  const submit = (): void => {
    if (!plan) return;
    setSubmitError('');
    createOrder.mutate({ planCode: plan.plan_code, seatCount: safeSeats, method: paymentMethod });
  };

  return (
    <>
      <Drawer open={open} onOpenChange={onOpenChange}>
        <DrawerContent size="md">
          <DrawerHeader>
            <DrawerTitle>确认开通</DrawerTitle>
            <DrawerDescription>
              席位与支付方式确认后即可下单；在线支付完成后套餐立即生效。
            </DrawerDescription>
          </DrawerHeader>

          <DrawerBody className="space-y-4">
            {!plan ? (
              <EmptyState
                title="还没有选择套餐"
                description="先在套餐对比里选择一个套餐，再回到这里完成开通"
              />
            ) : created ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-sm font-medium text-emerald-700">
                  <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
                  订单已创建
                </div>
                <dl className="space-y-2 rounded-md border p-3 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">订单号</dt>
                    <dd className="font-mono text-xs">{created.order_id}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">应付金额</dt>
                    <dd className="tabular-nums">
                      {formatCents(created.amount_cents, created.currency)}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">支付截止</dt>
                    <dd>{formatDateTime(created.expires_at)}</dd>
                  </div>
                </dl>

                {created.pay_url ? (
                  <Button asChild className="w-full">
                    <a href={created.pay_url} target="_blank" rel="noreferrer">
                      <CreditCard className="h-4 w-4" />
                      去支付
                    </a>
                  </Button>
                ) : created.bank_info ? (
                  <div className="space-y-2 rounded-md border p-3 text-sm">
                    <p className="font-medium">对公转账信息</p>
                    <dl className="space-y-1">
                      {Object.entries(created.bank_info).map(([key, value]) => (
                        <div key={key} className="flex justify-between gap-3">
                          <dt className="text-muted-foreground">{key}</dt>
                          <dd className="max-w-[60%] truncate text-right">{String(value)}</dd>
                        </div>
                      ))}
                    </dl>
                    <p className="text-xs text-muted-foreground">
                      汇款时请在备注中填写订单号，财务确认后套餐自动开通
                    </p>
                  </div>
                ) : null}
              </div>
            ) : (
              <>
                <dl className="space-y-2 rounded-md border p-3 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">套餐</dt>
                    <dd className="font-medium">{orPlaceholder(plan.display_name, plan.plan_code)}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">单价</dt>
                    <dd className="tabular-nums">
                      {formatCents(plan.price_per_seat_month_cents, plan.currency)} /席位/月
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">每位成员每月额度</dt>
                    <dd>{plan.quota_units_per_seat_month > 0 ? `${plan.quota_units_per_seat_month} ${plan.quota_unit_label}` : '—'}</dd>
                  </div>
                </dl>

                <Field
                  label="席位数"
                  htmlFor="order-seats"
                  hint={`至少 ${plan.min_seats} 个席位，最多 ${MAX_SEATS} 个`}
                  required
                >
                  <Input
                    id="order-seats"
                    type="number"
                    min={plan.min_seats}
                    max={MAX_SEATS}
                    value={String(safeSeats)}
                    onChange={(event) => setSeats(Number(event.target.value))}
                  />
                </Field>

                <Field label="支付方式" htmlFor="order-payment">
                  <Select
                    id="order-payment"
                    value={paymentMethod}
                    onChange={(event) =>
                      setPaymentMethod(event.target.value as 'online' | 'offline_transfer')
                    }
                    options={PAYMENT_OPTIONS}
                  />
                </Field>

                <div className="rounded-md bg-muted/40 p-3 text-sm">
                  <div className="flex items-baseline justify-between">
                    <span className="text-muted-foreground">应付金额（首月）</span>
                    <span className="text-lg font-semibold tabular-nums">
                      {formatCents(amountCents, plan.currency)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {safeSeats} 个席位 × {formatCents(plan.price_per_seat_month_cents, plan.currency)}
                  </p>
                </div>

                {submitError ? (
                  <ErrorState message={submitError} onRetry={submit} />
                ) : null}
              </>
            )}
          </DrawerBody>

          <DrawerFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {created ? '完成' : '取消'}
            </Button>
            {!created ? (
              <Button
                type="button"
                disabled={!plan || createOrder.isPending}
                onClick={() => setConfirmOpen(true)}
              >
                {createOrder.isPending ? '提交中…' : '确认下单'}
              </Button>
            ) : null}
          </DrawerFooter>
        </DrawerContent>
      </Drawer>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="确认提交这笔订单？"
        description="提交后会生成待支付订单；完成支付前套餐不会变更。"
        confirmLabel="提交订单"
        loading={createOrder.isPending}
        onConfirm={submit}
      >
        {plan ? (
          <ul className="space-y-1">
            <li>套餐：{orPlaceholder(plan.display_name, plan.plan_code)}</li>
            <li>席位：{safeSeats} 个</li>
            <li>金额：{formatCents(amountCents, plan.currency)}</li>
            <li>
              支付方式：{paymentMethod === 'online' ? '在线支付' : '对公转账'}
            </li>
          </ul>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
