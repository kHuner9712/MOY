'use client';

import * as React from 'react';

import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
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
import { formatNumber, formatUnits, orPlaceholder } from '@/lib/format';
import type { OpsMetricsRange } from '@/lib/types';

/** 统计区间选项 */
const RANGE_OPTIONS: Array<{ value: OpsMetricsRange; label: string }> = [
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
];

/**
 * 运营-平台统计（OPS6）。
 *
 * 口径：所有数字直接取后端聚合结果，**前端不做二次汇总**
 * （`quota_units_total` 与企业维度求和误差 0 由后端保证）。
 *
 * @returns 平台统计页元素
 */
export default function OpsMetricsPage(): React.JSX.Element {
  const [range, setRange] = React.useState<OpsMetricsRange>('7d');
  const metricsQuery = useApiQuery(QUERY_KEYS.opsMetrics(range), () => opsApi.getMetrics(range));

  const metrics = metricsQuery.data;
  const cards = metrics
    ? [
        { label: '企业总数', value: formatNumber(metrics.tenant_count), unit: '家' },
        { label: '区间活跃企业', value: formatNumber(metrics.active_tenant_count), unit: '家' },
        { label: '区间活跃成员', value: formatNumber(metrics.active_user_count), unit: '人' },
        { label: '区间对话次数', value: formatNumber(metrics.conversation_count), unit: '次' },
        {
          label: '区间文档处理',
          value: formatNumber(metrics.document_count),
          unit: '次',
        },
        {
          label: '区间用量合计',
          value: formatUnits(metrics.quota_units_total),
          unit: '',
        },
      ]
    : [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {RANGE_OPTIONS.map((option) => (
          <Button
            key={option.value}
            type="button"
            size="sm"
            variant={option.value === range ? 'default' : 'outline'}
            onClick={() => setRange(option.value)}
          >
            {option.label}
          </Button>
        ))}
        <span className="text-xs text-muted-foreground">统计口径与账期用量同源</span>
      </div>

      {metricsQuery.isLoading ? (
        <LoadingState label="正在加载平台统计…" />
      ) : metricsQuery.error ? (
        <ErrorState
          message={toBusinessMessage(metricsQuery.error)}
          onRetry={() => void metricsQuery.refetch()}
        />
      ) : !metrics ? (
        <EmptyState
          title="该区间还没有统计数据"
          description="换一个区间再看，或等待企业产生用量后重试"
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {cards.map((card) => (
              <Card key={card.label}>
                <CardContent className="pt-6">
                  <p className="text-xs text-muted-foreground">{card.label}</p>
                  <p className="mt-1 text-2xl font-semibold tabular-nums">
                    {card.value}
                    {card.unit ? (
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        {card.unit}
                      </span>
                    ) : null}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
            <Card>
              <CardHeader>
                <CardTitle>套餐分布</CardTitle>
                <CardDescription>按当前套餐统计企业数量</CardDescription>
              </CardHeader>
              <CardContent>
                {metrics.plan_distribution.length === 0 ? (
                  <EmptyState
                    title="还没有套餐分布数据"
                    description="企业开通套餐后，这里会按档位展示数量"
                  />
                ) : (
                  <ul className="space-y-2">
                    {metrics.plan_distribution.map((item) => {
                      const total = metrics.tenant_count > 0 ? metrics.tenant_count : 1;
                      const percent = Math.round((item.tenant_count / total) * 100);
                      return (
                        <li key={item.plan_code} className="space-y-1">
                          <div className="flex items-baseline justify-between text-sm">
                            <span>{orPlaceholder(item.plan_name, item.plan_code)}</span>
                            <span className="text-xs text-muted-foreground tabular-nums">
                              {item.tenant_count} 家 · {percent}%
                            </span>
                          </div>
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                            <div
                              className="h-full rounded-full bg-primary"
                              style={{ width: `${percent}%` }}
                            />
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>用量前列企业</CardTitle>
                <CardDescription>最多展示 20 家，只含聚合用量</CardDescription>
              </CardHeader>
              <CardContent>
                {metrics.top_tenants.length === 0 ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>企业</TableHead>
                        <TableHead>套餐</TableHead>
                        <TableHead>区间用量</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      <TableEmpty
                        colSpan={3}
                        title="该区间还没有用量"
                        description="企业产生用量后会出现在这里"
                      />
                    </TableBody>
                  </Table>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>企业</TableHead>
                        <TableHead>套餐</TableHead>
                        <TableHead>区间用量</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {metrics.top_tenants.map((tenant) => (
                        <TableRow key={tenant.tenant_id}>
                          <TableCell className="max-w-[14rem] truncate text-sm">
                            {orPlaceholder(tenant.name, tenant.tenant_id)}
                          </TableCell>
                          <TableCell>
                            <StatusBadge status="active" label={orPlaceholder(tenant.plan_code)} />
                          </TableCell>
                          <TableCell className="text-sm tabular-nums">
                            {formatUnits(tenant.quota_units_used)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
