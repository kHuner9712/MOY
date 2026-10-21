'use client';

import { AlertTriangle, FileText, Wallet } from 'lucide-react';
import * as React from 'react';

import { OrderDrawer } from '@/components/billing/order-drawer';
import { PlanCompare } from '@/components/billing/plan-compare';
import { UsageCharts } from '@/components/billing/usage-charts';
import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { QuotaProgress } from '@/components/ui/progress';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { billingApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatCents, formatDate, formatDateTime, formatPercent, orPlaceholder } from '@/lib/format';
import type { PlanItem, SubscriptionDetail } from '@/lib/types';

/** 额度告警阈值（与运营配置默认值一致，>= 该比例显示顶部提示） */
const QUOTA_WARN_PERCENT = 80;

/** 最近订单取几条 */
const RECENT_ORDER_SIZE = 5;

/** 当前账期 `YYYY-MM` */
function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

/**
 * 套餐与账单页（P-02）。
 *
 * 结构：当前套餐 → 额度用量 → 三档对比（升级 3 步内完成）→ 最近订单与账单。
 *
 * 关键口径：
 * - **降级是正常业务结果**（`code=0` + `notice_code=15005`）：页面展示提示条，
 *   绝不当成错误弹窗；
 * - 额度 ≥80% 时顶部给一条提醒，文案只讲「本月额度快用完」，不制造「停服」恐慌；
 * - 金额一律 `formatCents()`（分 → 元），额度一律 `formatUnits()`/进度条。
 *
 * @returns 套餐与账单页元素
 */
export default function BillingPage(): React.JSX.Element {
  const period = currentPeriod();
  const [orderPlan, setOrderPlan] = React.useState<PlanItem | null>(null);
  const [orderType, setOrderType] = React.useState<'new' | 'renew' | 'upgrade' | 'seats'>('upgrade');
  const [orderOpen, setOrderOpen] = React.useState<boolean>(false);
  const [downgradeTarget, setDowngradeTarget] = React.useState<PlanItem | null>(null);
  const [notice, setNotice] = React.useState<string>('');

  const plansQuery = useApiQuery(QUERY_KEYS.plans(), () => billingApi.listPlans());
  const subscriptionQuery = useApiQuery(QUERY_KEYS.subscription(), () => billingApi.getSubscription());
  const usageQuery = useApiQuery(QUERY_KEYS.billingUsage(period), () =>
    billingApi.getUsage({ period }),
  );
  const ordersQuery = useApiQuery(QUERY_KEYS.billingOrders({ page: 1, page_size: RECENT_ORDER_SIZE }), () =>
    billingApi.listOrders({ page: 1, page_size: RECENT_ORDER_SIZE }),
  );
  const invoicesQuery = useApiQuery(QUERY_KEYS.invoices(), () => billingApi.listInvoices());

  const subscription = subscriptionQuery.data;
  const usage = usageQuery.data;
  const plans = plansQuery.data?.plans ?? [];

  const downgradeMutation = useApiMutation(
    (variables: { plan: PlanItem; seats: number }) =>
      billingApi.changeSubscription({
        plan_code: variables.plan.plan_code,
        seats: variables.seats,
        action: 'downgrade',
      }),
    {
      invalidateKeys: [QUERY_KEYS.subscription(), QUERY_KEYS.billingOrders(), QUERY_KEYS.billingUsage(period)],
      onSuccess: (data) => {
        // 15005 是「提示码」而非错误码：展示提示条，不弹错误
        setNotice(
          data.notice_message ||
            `降级将在当前账期结束后生效（${formatDate(data.effective_at)}），期间仍按当前套餐提供服务`,
        );
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  /** 选择套餐：比当前便宜走降级确认（提示周期末生效），其余走下单升级 */
  const handleChoosePlan = (plan: PlanItem): void => {
    const currentPrice =
      plans.find((item) => item.plan_code === subscription?.plan_code)?.price_per_seat_month_cents ??
      0;
    const isDowngrade =
      subscription !== undefined &&
      subscription.plan_code !== 'free' &&
      plan.plan_code !== subscription.plan_code &&
      plan.price_per_seat_month_cents < currentPrice;

    if (isDowngrade) {
      setDowngradeTarget(plan);
      return;
    }
    setOrderPlan(plan);
    setOrderType(subscription && subscription.plan_code === 'free' ? 'new' : 'upgrade');
    setOrderOpen(true);
  };

  /** 顶部警告条：额度接近用完 */
  const quotaWarning =
    usage !== undefined && Number.isFinite(usage.usage_percent) && usage.usage_percent >= QUOTA_WARN_PERCENT;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="space-y-1">
        <h2 className="text-xl font-semibold">套餐与账单</h2>
        <p className="text-sm text-muted-foreground">
          查看当前套餐与额度，随时升级；账单与订单都在本页可查。
        </p>
      </header>

      {notice ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          <p>{notice}</p>
        </div>
      ) : null}

      {quotaWarning && usage ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <Wallet aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          <p>
            本月额度已用 {formatPercent(usage.usage_percent)}，接近上限时会切换为常规模式；升级套餐可恢复完整能力。
          </p>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-2">
              <CardTitle>当前套餐</CardTitle>
              {subscription ? <StatusBadge status="active" label={orPlaceholder(subscription.plan_name, subscription.plan_code)} /> : null}
            </div>
            <CardDescription>席位与账期以企业实际订阅为准</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {subscriptionQuery.isLoading ? (
              <LoadingState label="正在加载套餐…" />
            ) : subscriptionQuery.error ? (
              <ErrorState
                message={toBusinessMessage(subscriptionQuery.error)}
                onRetry={() => void subscriptionQuery.refetch()}
              />
            ) : subscription ? (
              <>
                <SubscriptionSummary subscription={subscription} />
                {subscription.pending_change ? (
                  <p className="rounded-md bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
                    已安排在 {formatDate(subscription.pending_change.effective_at)} 切换到{' '}
                    {orPlaceholder(subscription.pending_change.plan_code)}
                  </p>
                ) : null}
              </>
            ) : (
              <EmptyState
                title="还没有订阅信息"
                description="选择下方任一套餐即可开通，免费版可随时升级"
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>本月额度</CardTitle>
            <CardDescription>额度与对话、文档问答同源统计</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {usageQuery.isLoading ? (
              <LoadingState label="正在加载用量…" />
            ) : usageQuery.error ? (
              <ErrorState
                message={toBusinessMessage(usageQuery.error)}
                onRetry={() => void usageQuery.refetch()}
              />
            ) : usage ? (
              <>
                <QuotaProgress
                  used={usage.quota_units_used}
                  total={usage.quota_units_total}
                  unit={usage.quota_unit || '字'}
                  label={`账期 ${usage.period}`}
                  hint={`剩余 ${usage.quota_units_remaining.toLocaleString('zh-CN')} ${usage.quota_unit || '字'}`}
                />
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>使用比例 {formatPercent(usage.usage_percent)}</span>
                  {usage.over_quota_warning ? (
                    <span className="font-medium text-destructive">已超出当前额度</span>
                  ) : null}
                </div>
              </>
            ) : (
              <EmptyState
                title="还没有用量记录"
                description="发起一次对话或上传文档后，这里会显示本月用量"
              />
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>用量明细</CardTitle>
          <CardDescription>按日与按维度的用量，便于判断是否需要调整套餐</CardDescription>
        </CardHeader>
        <CardContent>
          {usageQuery.isLoading ? (
            <LoadingState label="正在加载用量明细…" />
          ) : usageQuery.error ? (
            <ErrorState
              message={toBusinessMessage(usageQuery.error)}
              onRetry={() => void usageQuery.refetch()}
            />
          ) : usage ? (
            <UsageCharts
              breakdown={usage.breakdown}
              dailyTrend={usage.daily_trend}
              quotaUnitLabel={usage.quota_unit || '字'}
            />
          ) : (
            <EmptyState title="还没有用量明细" description="产生用量后这里会按天与维度展开" />
          )}
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div className="flex items-end justify-between gap-2">
          <div>
            <h3 className="text-base font-semibold">套餐对比</h3>
            <p className="text-sm text-muted-foreground">
              选好套餐后在本页完成确认与支付，无需跳转其他系统。
            </p>
          </div>
        </div>
        {plansQuery.isLoading ? (
          <LoadingState label="正在加载套餐目录…" />
        ) : plansQuery.error ? (
          <ErrorState
            message={toBusinessMessage(plansQuery.error)}
            onRetry={() => void plansQuery.refetch()}
          />
        ) : plans.length === 0 ? (
          <EmptyState
            title="暂时没有可购买的套餐"
            description="套餐目录由平台统一维护，请联系平台管理员确认上下架状态"
          />
        ) : (
          <PlanCompare
            plans={plans}
            currentPlanCode={subscription?.plan_code ?? 'free'}
            onChoose={handleChoosePlan}
          />
        )}
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>最近订单</CardTitle>
            <span className="text-xs text-muted-foreground">
              仅展示最近 {RECENT_ORDER_SIZE} 笔
            </span>
          </div>
          <CardDescription>下单后可在此查看支付状态</CardDescription>
        </CardHeader>
        <CardContent>
          {ordersQuery.isLoading ? (
            <LoadingState label="正在加载订单…" />
          ) : ordersQuery.error ? (
            <ErrorState
              message={toBusinessMessage(ordersQuery.error)}
              onRetry={() => void ordersQuery.refetch()}
            />
          ) : (ordersQuery.data?.items.length ?? 0) === 0 ? (
            <EmptyState title="还没有订单" description="升级或加席后会在这里生成订单记录" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>订单号</TableHead>
                  <TableHead>套餐</TableHead>
                  <TableHead>席位</TableHead>
                  <TableHead>金额</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>创建时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ordersQuery.data?.items.map((order) => (
                  <TableRow key={order.order_id}>
                    <TableCell className="font-mono text-xs">{order.order_id}</TableCell>
                    <TableCell className="text-sm">{orPlaceholder(order.plan_code)}</TableCell>
                    <TableCell className="text-sm tabular-nums">{order.seats}</TableCell>
                    <TableCell className="text-sm tabular-nums">
                      {formatCents(order.amount_cents)}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={order.status} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                      {formatDateTime(order.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>账单</CardTitle>
          <CardDescription>每个账期开具一张账单，可交给财务入账</CardDescription>
        </CardHeader>
        <CardContent>
          {invoicesQuery.isLoading ? (
            <LoadingState label="正在加载账单…" />
          ) : invoicesQuery.error ? (
            <ErrorState
              message={toBusinessMessage(invoicesQuery.error)}
              onRetry={() => void invoicesQuery.refetch()}
            />
          ) : (invoicesQuery.data?.items.length ?? 0) === 0 ? (
            <EmptyState title="还没有账单" description="首个账期结束后会在这里生成账单" />
          ) : (
            <ul className="divide-y">
              {invoicesQuery.data?.items.map((invoice) => (
                <li key={invoice.invoice_id} className="flex items-center justify-between py-2.5">
                  <div className="flex items-center gap-2">
                    <FileText aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm">{invoice.period}</span>
                    <span className="text-sm tabular-nums text-muted-foreground">
                      {formatCents(invoice.amount_cents)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={invoice.status} />
                    <Tooltip
                      content="账单开具后由财务提供下载链接"
                      disabled={Boolean(invoice.download_url)}
                    >
                      <span>
                        <Button
                          asChild={Boolean(invoice.download_url)}
                          variant="ghost"
                          size="sm"
                          disabled={!invoice.download_url}
                        >
                          {invoice.download_url ? (
                            <a href={invoice.download_url} target="_blank" rel="noreferrer">
                              下载
                            </a>
                          ) : (
                            <span>下载</span>
                          )}
                        </Button>
                      </span>
                    </Tooltip>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <OrderDrawer
        open={orderOpen}
        onOpenChange={setOrderOpen}
        plan={orderPlan}
        orderType={orderType}
        defaultSeats={subscription?.seats_total ?? orderPlan?.min_seats ?? 1}
      />

      <ConfirmDialog
        open={downgradeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDowngradeTarget(null);
        }}
        title="确认降级套餐？"
        description="降级在当前账期结束后生效，期间仍按当前套餐提供服务。"
        confirmLabel="确认降级"
        loading={downgradeMutation.isPending}
        onConfirm={async () => {
          if (!downgradeTarget) return;
          await downgradeMutation.mutateAsync({
            plan: downgradeTarget,
            seats: subscription?.seats_total ?? downgradeTarget.min_seats,
          });
          setDowngradeTarget(null);
        }}
      >
        {downgradeTarget ? (
          <p>
            目标套餐：{orPlaceholder(downgradeTarget.display_name, downgradeTarget.plan_code)}，席位{' '}
            {subscription?.seats_total ?? downgradeTarget.min_seats} 个
          </p>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}

/**
 * 订阅摘要块（席位用量 + 账期信息）。
 *
 * @param props - 组件属性
 * @returns 摘要素材
 */
function SubscriptionSummary({ subscription }: { subscription: SubscriptionDetail }): React.JSX.Element {
  return (
    <div className="space-y-3">
      <dl className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-0.5">
          <dt className="text-xs text-muted-foreground">席位</dt>
          <dd className="text-sm font-medium tabular-nums">
            {subscription.seats_used} / {subscription.seats_total}
          </dd>
        </div>
        <div className="space-y-0.5">
          <dt className="text-xs text-muted-foreground">当前账期结束</dt>
          <dd className="text-sm font-medium">{formatDate(subscription.current_period_end)}</dd>
        </div>
        <div className="space-y-0.5">
          <dt className="text-xs text-muted-foreground">自动续费</dt>
          <dd className="text-sm font-medium">{subscription.auto_renew ? '已开启' : '已关闭'}</dd>
        </div>
        <div className="space-y-0.5">
          <dt className="text-xs text-muted-foreground">开始时间</dt>
          <dd className="text-sm font-medium">{formatDate(subscription.started_at)}</dd>
        </div>
      </dl>
      <QuotaProgress
        used={subscription.seats_used}
        total={subscription.seats_total}
        unit="个席位"
        label="席位使用"
        size="sm"
      />
    </div>
  );
}
