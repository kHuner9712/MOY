import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { customerStageLabel, riskTone } from "@/lib/constants";
import { formatDateTime } from "@/lib/format";
import type { Customer } from "@/types/customer";

export function SalesDetailTable({ salesName, customers }: { salesName: string; customers: Customer[] }): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{salesName} 的客户明细（Mock）</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {customers.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无客户数据。</p>
        ) : (
          customers.map((customer) => (
            <div key={customer.id} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{customer.companyName}</p>
                  <p className="text-xs text-muted-foreground">{customer.contactName}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{customerStageLabel[customer.stage]}</Badge>
                  <Badge variant={riskTone[customer.riskLevel]}>
                    {customer.riskLevel === "high" ? "高风险" : customer.riskLevel === "medium" ? "中风险" : "低风险"}
                  </Badge>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                <span>成交概率 {customer.winProbability}%</span>
                <span>最近跟进 {formatDateTime(customer.lastFollowupAt)}</span>
                <span>下次跟进 {formatDateTime(customer.nextFollowupAt)}</span>
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
