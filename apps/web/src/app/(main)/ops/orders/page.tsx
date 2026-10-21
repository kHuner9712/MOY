'use client';

import { CheckCircle2 } from 'lucide-react';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { ConfirmOrderDialog } from '@/components/ops/confirm-order-dialog';
import { Button } from '@/components/ui/button';
import { ErrorState } from '@/components/ui/feedback';
import { Input } from '@/components/ui/input';
import { Pagination } from '@/components/ui/pagination';
import { CompactSelect } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableLoading,
  TableRow,
} from '@/components/ui/table';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { opsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatCents, formatDateTime, orPlaceholder } from '@/lib/format';
import type { OpsOrderItem } from '@/lib/types';

/** 订单状态筛选项（业务语言，与后端状态机一致） */
const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'pending_payment', label: '待支付' },
  { value: 'paid', label: '已支付' },
  { value: 'closed', label: '已关闭' },
  { value: 'cancelled', label: '已取消' },
  { value: 'refunded', label: '已退款' },
];

/** 表格列数（加载态/空态占位） */
const COLUMN_COUNT = 8;

/**
 * 运营-订单管理（OPS4 / OPS5）。
 *
 * 只有「待支付」订单可以核销；其余状态按钮置灰并用 hover 说明原因，
 * 避免运营同学点了才看到「该订单已处理」。
 *
 * @returns 订单管理页元素
 */
export default function OpsOrdersPage(): React.JSX.Element {
  const [tenantId, setTenantId] = React.useState<string>('');
  const [status, setStatus] = React.useState<string>('');
  const [page, setPage] = React.useState<number>(1);
  const [pageSize, setPageSize] = React.useState<number>(20);
  const [target, setTarget] = React.useState<OpsOrderItem | null>(null);

  const params = { tenant_id: tenantId.trim(), status, page, page_size: pageSize };
  const ordersQuery = useApiQuery(QUERY_KEYS.opsOrders(params), () => opsApi.listOrders(params));

  const items = ordersQuery.data?.items ?? [];
  const total = ordersQuery.data?.total ?? 0;
  const hasFilter = Boolean(tenantId.trim()) || Boolean(status);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          value={tenantId}
          onChange={(event) => {
            setTenantId(event.target.value);
            setPage(1);
          }}
          placeholder="按企业标识筛选（可留空）"
          aria-label="按企业标识筛选"
          className="sm:max-w-xs"
        />
        <div className="sm:w-40">
          <CompactSelect
            aria-label="按订单状态筛选"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
            options={STATUS_OPTIONS}
          />
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>订单号</TableHead>
            <TableHead>企业</TableHead>
            <TableHead>类型</TableHead>
            <TableHead>席位</TableHead>
            <TableHead>金额</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>创建时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ordersQuery.isLoading ? (
            <TableLoading columns={COLUMN_COUNT} rows={5} />
          ) : ordersQuery.error ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={COLUMN_COUNT} className="p-0">
                <ErrorState
                  message={toBusinessMessage(ordersQuery.error)}
                  onRetry={() => void ordersQuery.refetch()}
                />
              </TableCell>
            </TableRow>
          ) : items.length === 0 ? (
            <TableEmpty
              colSpan={COLUMN_COUNT}
              title={hasFilter ? '没有符合条件的订单' : '还没有订单'}
              description={
                hasFilter
                  ? '换个企业标识或状态再看，也可以清空筛选查看全部订单'
                  : '企业下单后会出现在这里，可对待支付订单做核销'
              }
            />
          ) : (
            items.map((order) => {
              const pending = order.status === 'pending_payment';
              return (
                <TableRow key={order.order_id}>
                  <TableCell className="font-mono text-xs">{order.order_id}</TableCell>
                  <TableCell className="max-w-[12rem] truncate text-sm">
                    {orPlaceholder(order.tenant_name, order.tenant_id)}
                  </TableCell>
                  <TableCell className="text-sm">{orPlaceholder(order.order_type)}</TableCell>
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
                  <TableCell className="text-right">
                    <Tooltip content="只有待支付订单可以核销" disabled={pending}>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={!pending}
                        onClick={() => setTarget(order)}
                      >
                        <CheckCircle2 className="h-4 w-4" />
                        核销
                      </Button>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>

      <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        disabled={ordersQuery.isLoading}
      />

      <ConfirmOrderDialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
        order={target}
        onConfirmed={() => void ordersQuery.refetch()}
      />
    </div>
  );
}
