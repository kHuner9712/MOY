'use client';

import * as React from 'react';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Field, Input } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation } from '@/hooks/use-api';
import { opsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatCents, orPlaceholder } from '@/lib/format';
import type { OpsConfirmOrderResult, OpsOrderItem } from '@/lib/types';

/** 备注长度上限（与后端 ≤200 一致） */
const REMARK_MAX_LENGTH = 200;

/** 核销弹窗属性 */
export interface ConfirmOrderDialogProps {
  /** 是否打开（表单步） */
  open: boolean;
  /** 开关回调 */
  onOpenChange: (open: boolean) => void;
  /** 待核销订单；null 时不渲染表单内容 */
  order: OpsOrderItem | null;
  /** 核销成功回调 */
  onConfirmed?: (result: OpsConfirmOrderResult) => void;
}

/**
 * 订单核销弹窗（OPS5）：填金额与备注 → 二次确认 → 提交。
 *
 * 为什么两步：核销会**立即开通套餐且不可撤回**，
 * 所以第一步收集「实收金额 + 备注」，第二步用独立确认弹窗再把「订单号/金额/备注」念一遍；
 * 金额可改是因为对公转账常有手续费差额，但差额会由后端 15009 兜底拦截。
 *
 * @param props - 组件属性
 * @returns 核销弹窗
 */
export function ConfirmOrderDialog({
  open,
  onOpenChange,
  order,
  onConfirmed,
}: ConfirmOrderDialogProps): React.JSX.Element {
  const [amountYuan, setAmountYuan] = React.useState<string>('');
  const [remark, setRemark] = React.useState<string>('');
  const [formError, setFormError] = React.useState<string>('');
  const [confirmStep, setConfirmStep] = React.useState<boolean>(false);

  // 换订单或重新打开时，用订单应付金额回填（元）
  React.useEffect(() => {
    if (!open || !order) return;
    setAmountYuan((order.amount_cents / 100).toFixed(2));
    setRemark('');
    setFormError('');
    setConfirmStep(false);
  }, [open, order]);

  const confirmMutation = useApiMutation(
    (variables: { orderId: string; paidAmountCents: number; remark: string }) =>
      opsApi.confirmOrder(variables.orderId, {
        paid_amount_cents: variables.paidAmountCents,
        remark: variables.remark,
      }),
    {
      invalidateKeys: [QUERY_KEYS.opsOrders(), QUERY_KEYS.opsMetrics('7d'), QUERY_KEYS.opsMetrics('30d')],
      onSuccess: (data) => {
        toast.success('核销完成，该企业套餐已开通');
        setConfirmStep(false);
        onConfirmed?.(data);
        onOpenChange(false);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const amountCents = Math.round(Number(amountYuan) * 100);

  /** 第一步校验：金额与备注都必填 */
  const handleNext = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!Number.isFinite(amountCents) || amountCents <= 0) {
      setFormError('请填写实收金额');
      return;
    }
    if (!remark.trim()) {
      setFormError('请填写核销备注，便于后续对账与追溯');
      return;
    }
    setFormError('');
    setConfirmStep(true);
  };

  return (
    <>
      <Dialog
        open={open && !confirmStep}
        onOpenChange={confirmMutation.isPending ? undefined : onOpenChange}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>订单核销</DialogTitle>
            <DialogDescription>
              核对实收金额与备注后提交；提交即开通对应套餐，不可撤回。
            </DialogDescription>
          </DialogHeader>

          {order ? (
            <form className="space-y-4" onSubmit={handleNext}>
              <dl className="space-y-1 rounded-md border p-3 text-sm">
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">订单号</dt>
                  <dd className="font-mono text-xs">{order.order_id}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">企业</dt>
                  <dd className="max-w-[60%] truncate text-right">
                    {orPlaceholder(order.tenant_name, order.tenant_id)}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">应付金额</dt>
                  <dd className="tabular-nums">{formatCents(order.amount_cents)}</dd>
                </div>
              </dl>

              <Field
                label="实收金额（元）"
                htmlFor="confirm-order-amount"
                hint="与应付金额不一致时后端会拦截并提示核对"
                required
              >
                <Input
                  id="confirm-order-amount"
                  type="number"
                  min="0"
                  step="0.01"
                  value={amountYuan}
                  onChange={(event) => setAmountYuan(event.target.value)}
                />
              </Field>

              <Field
                label="核销备注"
                htmlFor="confirm-order-remark"
                hint={`最多 ${REMARK_MAX_LENGTH} 字，会写入操作记录`}
                required
              >
                <Input
                  id="confirm-order-remark"
                  value={remark}
                  maxLength={REMARK_MAX_LENGTH}
                  invalid={Boolean(formError)}
                  placeholder="例如：对公转账已到账，流水号 6222****"
                  onChange={(event) => setRemark(event.target.value)}
                />
              </Field>

              {formError ? <p className="text-sm text-destructive">{formError}</p> : null}

              <DialogFooter className="gap-2 pt-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => onOpenChange(false)}
                  disabled={confirmMutation.isPending}
                >
                  取消
                </Button>
                <Button type="submit">下一步</Button>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmStep}
        onOpenChange={setConfirmStep}
        title="确认核销这笔订单？"
        description="核销后订单变为已支付、套餐立即生效，且不可撤回。"
        confirmLabel="确认核销"
        loading={confirmMutation.isPending}
        onConfirm={async () => {
          if (!order) return;
          await confirmMutation.mutateAsync({
            orderId: order.order_id,
            paidAmountCents: amountCents,
            remark: remark.trim(),
          });
        }}
      >
        {order ? (
          <ul className="space-y-1">
            <li>订单号：{order.order_id}</li>
            <li>应付金额：{formatCents(order.amount_cents)}</li>
            <li>实收金额：{amountYuan || '—'} 元</li>
            <li>备注：{remark.trim()}</li>
          </ul>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
