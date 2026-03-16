"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { customerStageLabel, riskTone } from "@/lib/constants";
import { formatDateTime, getRelativeDaysLabel } from "@/lib/format";
import type { AlertItem } from "@/types/alert";
import type { Customer } from "@/types/customer";
import type { FollowupRecord } from "@/types/followup";

export function SalesDashboard({
  keyCustomers,
  recentFollowups,
  aiSuggestions,
  riskAlerts
}: {
  keyCustomers: Customer[];
  recentFollowups: FollowupRecord[];
  aiSuggestions: string[];
  riskAlerts: AlertItem[];
}): JSX.Element {
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>重点客户提醒</CardTitle>
          <Badge variant="outline">按成交概率排序</Badge>
        </CardHeader>
        <CardContent className="space-y-3">
          {keyCustomers.map((customer) => (
            <div key={customer.id} className="rounded-lg border bg-slate-50/70 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{customer.companyName}</p>
                  <p className="text-xs text-muted-foreground">
                    {customer.contactName} · {customer.ownerName} · {customerStageLabel[customer.stage]}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-semibold text-slate-900">{customer.winProbability}%</p>
                  <p className="text-xs text-muted-foreground">{getRelativeDaysLabel(customer.stalledDays)}</p>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Badge variant={riskTone[customer.riskLevel]}>{customer.riskLevel === "high" ? "高风险" : customer.riskLevel === "medium" ? "中风险" : "低风险"}</Badge>
                <span className="text-xs text-muted-foreground">下次跟进 {formatDateTime(customer.nextFollowupAt)}</span>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>AI 建议卡片</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {aiSuggestions.map((tip, index) => (
            <div key={tip} className="rounded-lg border border-sky-100 bg-sky-50/60 p-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-700">建议 {index + 1}</p>
              <p className="text-sm leading-6 text-slate-700">{tip}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle>最近沟通记录</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {recentFollowups.map((followup) => (
            <div key={followup.id} className="rounded-lg border p-3">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{followup.ownerName}</p>
                <p className="text-xs text-muted-foreground">{formatDateTime(followup.createdAt)}</p>
              </div>
              <p className="text-sm text-slate-700">{followup.summary}</p>
              <p className="mt-2 text-xs text-muted-foreground">下一步：{followup.nextPlan}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>漏单风险提示</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {riskAlerts.length === 0 ? (
            <p className="text-sm text-muted-foreground">当前暂无高风险提醒。</p>
          ) : (
            riskAlerts.map((alert) => (
              <div key={alert.id} className="rounded-lg border border-rose-100 bg-rose-50/70 p-3">
                <p className="text-sm font-semibold text-rose-800">{alert.customerName}</p>
                <p className="mt-1 text-xs leading-5 text-rose-700">{alert.message}</p>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
