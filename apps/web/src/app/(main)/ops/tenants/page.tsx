'use client';

import { ArrowRight, Search } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { EmptyState, ErrorState } from '@/components/ui/feedback';
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
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { opsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDate, formatUnits, orPlaceholder } from '@/lib/format';
import type { OpsTenantItem } from '@/lib/types';

/** 企业状态筛选项（业务语言） */
const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'active', label: '正常' },
  { value: 'suspended', label: '已停用' },
];

/** 表格列数（加载态/空态占位） */
const COLUMN_COUNT = 7;

/**
 * 运营-企业列表（OPS1 / OPS3）。
 *
 * 最小可见原则：本页只有企业名、套餐、席位、状态与聚合额度，
 * 不含任何成员邮箱明文、对话内容或文档原文。
 *
 * @returns 企业列表页元素
 */
export default function OpsTenantsPage(): React.JSX.Element {
  const [keyword, setKeyword] = React.useState<string>('');
  const [status, setStatus] = React.useState<string>('');
  const [page, setPage] = React.useState<number>(1);
  const [pageSize, setPageSize] = React.useState<number>(20);
  const [target, setTarget] = React.useState<OpsTenantItem | null>(null);

  const params = { keyword: keyword.trim(), status, page, page_size: pageSize };
  const tenantsQuery = useApiQuery(QUERY_KEYS.opsTenants(params), () => opsApi.listTenants(params));

  const suspendMutation = useApiMutation(
    (variables: { tenantId: string; next: 'active' | 'suspended' }) =>
      opsApi.updateTenantStatus(variables.tenantId, {
        status: variables.next,
        reason: variables.next === 'suspended' ? '运营后台人工停用' : '',
      }),
    {
      invalidateKeys: [QUERY_KEYS.opsTenants()],
      onSuccess: (data) => {
        toast.success(data.status === 'suspended' ? '该企业已停用' : '该企业已恢复正常');
        setTarget(null);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const items = tenantsQuery.data?.items ?? [];
  const total = tenantsQuery.data?.total ?? 0;
  const hasFilter = Boolean(keyword.trim()) || Boolean(status);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="relative sm:max-w-xs">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            value={keyword}
            onChange={(event) => {
              setKeyword(event.target.value);
              setPage(1);
            }}
            placeholder="搜索企业名称"
            aria-label="搜索企业名称"
            className="pl-9"
          />
        </div>
        <div className="sm:w-40">
          <CompactSelect
            aria-label="按企业状态筛选"
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
            <TableHead>企业</TableHead>
            <TableHead>套餐</TableHead>
            <TableHead>席位</TableHead>
            <TableHead>当月用量</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>创建时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tenantsQuery.isLoading ? (
            <TableLoading columns={COLUMN_COUNT} rows={5} />
          ) : tenantsQuery.error ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={COLUMN_COUNT} className="p-0">
                <ErrorState
                  message={toBusinessMessage(tenantsQuery.error)}
                  onRetry={() => void tenantsQuery.refetch()}
                />
              </TableCell>
            </TableRow>
          ) : items.length === 0 ? (
            <TableEmpty
              colSpan={COLUMN_COUNT}
              title={hasFilter ? '没有符合条件的企业' : '还没有企业'}
              description={
                hasFilter
                  ? '换个关键字或状态再看，也可以清空筛选查看全部企业'
                  : '企业完成开通后会出现在这里'
              }
            />
          ) : (
            items.map((tenant) => (
              <TableRow key={tenant.tenant_id}>
                <TableCell className="max-w-[14rem]">
                  <p className="truncate text-sm font-medium">{orPlaceholder(tenant.name)}</p>
                  <p className="truncate font-mono text-xs text-muted-foreground">
                    {tenant.tenant_id}
                  </p>
                </TableCell>
                <TableCell className="text-sm">{orPlaceholder(tenant.plan_code)}</TableCell>
                <TableCell className="text-sm tabular-nums">
                  {tenant.seats_used} / {tenant.seats_total}
                </TableCell>
                <TableCell className="text-sm tabular-nums">
                  {formatUnits(tenant.quota_units_used)}
                </TableCell>
                <TableCell>
                  <StatusBadge
                    status={tenant.status}
                    label={tenant.status === 'suspended' ? '已停用' : '正常'}
                  />
                </TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {formatDate(tenant.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  <div className="inline-flex items-center gap-1">
                    <Button asChild variant="ghost" size="sm">
                      <Link href={`/ops/tenants/${tenant.tenant_id}`}>
                        详情
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Link>
                    </Button>
                    <Button
                      type="button"
                      variant={tenant.status === 'suspended' ? 'outline' : 'ghost'}
                      size="sm"
                      onClick={() => setTarget(tenant)}
                    >
                      {tenant.status === 'suspended' ? '恢复' : '停用'}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))
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
        disabled={tenantsQuery.isLoading}
      />

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
        variant={target?.status === 'suspended' ? 'default' : 'danger'}
        title={target?.status === 'suspended' ? '恢复该企业？' : '停用该企业？'}
        description={
          target?.status === 'suspended'
            ? '恢复后该企业成员可继续使用全部已开通能力。'
            : '停用后该企业成员将无法发起对话与上传文档，数据保留不删除。'
        }
        confirmLabel={target?.status === 'suspended' ? '恢复' : '停用'}
        loading={suspendMutation.isPending}
        onConfirm={async () => {
          if (!target) return;
          await suspendMutation.mutateAsync({
            tenantId: target.tenant_id,
            next: target.status === 'suspended' ? 'active' : 'suspended',
          });
        }}
      >
        {target ? <p>{orPlaceholder(target.name, target.tenant_id)}</p> : null}
      </ConfirmDialog>
    </div>
  );
}
