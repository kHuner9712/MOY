"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { customerStageLabel, opportunityStageLabel } from "@/lib/constants";
import { formatCurrency, formatDateTime } from "@/lib/format";
import type { Customer } from "@/types/customer";
import type { Opportunity } from "@/types/opportunity";

interface StageBarProps {
  label: string;
  value: number;
  max: number;
}

function StageBar({ label, value, max }: StageBarProps): JSX.Element {
  const ratio = max === 0 ? 0 : Math.round((value / max) * 100);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium text-slate-800">{value}</span>
      </div>
      <div className="h-2 rounded-full bg-slate-200">
        <div className="h-2 rounded-full bg-sky-600" style={{ width: `${Math.max(ratio, value > 0 ? 8 : 0)}%` }} />
      </div>
    </div>
  );
}

export function ManagerDashboard({
  salesWorkload,
  opportunityStageDist,
  highRiskCustomers,
  longTimeNoFollowups,
  highRiskOpportunities
}: {
  salesWorkload: Array<{
    salesName: string;
    customerCount: number;
    followupsLast7Days: number;
  }>;
  opportunityStageDist: Array<{ stage: Opportunity["stage"]; count: number }>;
  highRiskCustomers: Customer[];
  longTimeNoFollowups: Customer[];
  highRiskOpportunities: Opportunity[];
}): JSX.Element {
  const stageMax = Math.max(...opportunityStageDist.map((item) => item.count), 0);

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle>团队推进工作量（近 7 天）</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          {salesWorkload.map((item) => (
            <div key={item.salesName} className="rounded-lg border bg-slate-50/70 p-4">
              <p className="text-sm font-semibold text-slate-900">{item.salesName}</p>
              <p className="mt-2 text-2xl font-semibold text-slate-900">{item.customerCount}</p>
              <p className="text-xs text-muted-foreground">名下客户</p>
              <p className="mt-2 text-sm text-slate-700">跟进 {item.followupsLast7Days} 次</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>商机阶段分布</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {opportunityStageDist.map((item) => (
            <StageBar key={item.stage} label={opportunityStageLabel[item.stage]} value={item.count} max={stageMax} />
          ))}
        </CardContent>
      </Card>

      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle>高风险客户列表</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {highRiskCustomers.map((item) => (
            <div key={item.id} className="rounded-lg border border-rose-100 bg-rose-50/60 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-rose-900">{item.companyName}</p>
                  <p className="text-xs text-rose-700">
                    {item.ownerName} · {customerStageLabel[item.stage]}
                  </p>
                </div>
                <Badge variant="destructive">{item.winProbability}% 概率</Badge>
              </div>
              <p className="mt-2 text-xs text-rose-700">{item.aiRiskJudgement}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>长期未跟进名单</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {longTimeNoFollowups.slice(0, 5).map((item) => (
            <div key={item.id} className="rounded-lg border p-3">
              <p className="text-sm font-semibold text-slate-900">{item.companyName}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {item.ownerName} · 已停滞 {item.stalledDays} 天
              </p>
              <p className="mt-1 text-xs text-muted-foreground">最近跟进：{formatDateTime(item.lastFollowupAt)}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle>高风险商机</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {highRiskOpportunities.map((item) => (
            <div key={item.id} className="rounded-lg border bg-white p-3">
              <p className="text-sm font-semibold text-slate-900">{item.name}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {item.customerName} · {item.ownerName}
              </p>
              <div className="mt-3 flex items-center justify-between">
                <Badge variant="destructive">{opportunityStageLabel[item.stage]}</Badge>
                <span className="text-sm font-semibold text-slate-900">{formatCurrency(item.expectedAmount)}</span>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
