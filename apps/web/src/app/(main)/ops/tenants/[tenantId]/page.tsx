'use client';

import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ErrorState, LoadingState } from '@/components/ui/feedback';
import { QuotaProgress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { opsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatCents, formatDate, formatDateTime, formatPercent, orPlaceholder } from '@/lib/format';

/**
 * 运营-企业详情（OPS2）。
 *
 * 展示范围严格限定为：企业基本信息、订阅摘要、用量摘要、近期订单。
 * 响应体本身就没有对话内容/文档原文/邮箱明文，页面**不得**自行拼接这类信息。
 *
 * @returns 企业详情页元素
 */
export default function OpsTenantDetailPage(): React.JSX.Element {
  const params = useParams<{ tenantId: string }>();
  const tenantId = params.tenantId;

  const detailQuery = useApiQuery(QUERY_KEYS.opsTenant(tenantId), () => opsApi.getTenant(tenantId), {
    enabled: Boolean(tenantId),
  });

  if (detailQuery.isLoading) {
    return <LoadingState label="正在加载企业详情…" />;
  }

  if (detailQuery.error || !detailQuery.data) {
    return (
      <div className="space-y-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/ops/tenants">
            <ArrowLeft className="h-4 w-4" />
            返回企业列表
          </Link>
        </Button>
        <ErrorState
          message={toBusinessMessage(detailQuery.error)}
          onRetry={() => void detailQuery.refetch()}
        />
      </div>
    );
  }

  const { tenant, subscription, usage, recent_orders: recentOrders } = detailQuery.data;

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Button asChild variant="ghost" size="sm">
          <Link href="/ops/tenants">
            <ArrowLeft className="h-4 w-4" />
            返回企业列表
          </Link>
        </Button>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold">{orPlaceholder(tenant.name, tenant.tenant_id)}</h3>
          <StatusBadge
            status={tenant.status}
            label={tenant.status === 'suspended' ? '已停用' : '正常'}
          />
          <span className="text-xs text-muted-foreground">部署方式：{tenant.deploy_mode}</span>
        </div>
        {tenant.suspended_reason ? (
          <p className="text-xs text-muted-foreground">停用原因：{tenant.suspended_reason}</p>
        ) : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>基本信息</CardTitle>
            <CardDescription>企业与套餐概况</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted-foreground">当前套餐</dt>
                <dd className="text-sm font-medium">{orPlaceholder(subscription.plan_name, subscription.plan_code)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">席位</dt>
                <dd className="text-sm font-medium tabular-nums">
                  {subscription.seats_used} / {subscription.seats_total}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">当前账期结束</dt>
                <dd className="text-sm font-medium">{formatDate(subscription.current_period_end)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">自动续费</dt>
                <dd className="text-sm font-medium">{subscription.auto_renew ? '已开启' : '已关闭'}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">创建时间</dt>
                <dd className="text-sm font-medium">{formatDate(tenant.created_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">在册成员</dt>
                <dd className="text-sm font-medium tabular-nums">{usage.member_count}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>用量摘要</CardTitle>
            <CardDescription>仅聚合数字，不含任何内容信息</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <QuotaProgress
              used={usage.quota_units_used}
              total={usage.quota_units_total}
              unit="字"
              label={`账期 ${orPlaceholder(usage.period)}`}
            />
            <p className="text-xs text-muted-foreground">
              使用比例 {formatPercent(usage.usage_percent)}
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>近期订单</CardTitle>
          <CardDescription>最多展示最近 10 笔，可到订单管理处理核销</CardDescription>
        </CardHeader>
        <CardContent>
          {recentOrders.length === 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>订单号</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>金额</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>创建时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableEmpty colSpan={5} title="该企业还没有订单" description="下单后会出现在这里" />
              </TableBody>
            </Table>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>订单号</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>套餐</TableHead>
                  <TableHead>席位</TableHead>
                  <TableHead>金额</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>创建时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentOrders.map((order) => (
                  <TableRow key={order.order_id}>
                    <TableCell className="font-mono text-xs">{order.order_id}</TableCell>
                    <TableCell className="text-sm">{orPlaceholder(order.order_type)}</TableCell>
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
    </div>
  );
}
