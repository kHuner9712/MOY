import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { followupMethodLabel } from "@/lib/constants";
import { formatDateTime } from "@/lib/format";
import type { FollowupRecord } from "@/types/followup";

export function FollowupTimeline({ items }: { items: FollowupRecord[] }): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>跟进时间线</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {items.map((item) => (
            <div key={item.id} className="relative rounded-lg border bg-slate-50/70 p-4">
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{followupMethodLabel[item.method]}</Badge>
                  {item.draftStatus === "draft" ? <Badge variant="secondary">待确认草稿</Badge> : null}
                  <p className="text-sm font-semibold text-slate-900">{item.ownerName}</p>
                </div>
                <p className="text-xs text-muted-foreground">{formatDateTime(item.createdAt)}</p>
              </div>
              <p className="text-sm leading-6 text-slate-700">{item.summary}</p>
              <div className="mt-3 grid gap-2 text-xs text-muted-foreground">
                <p>客户需求：{item.customerNeeds}</p>
                <p>异议/阻碍：{item.objections}</p>
                <p>下一步：{item.nextPlan}</p>
                <p>下次跟进：{formatDateTime(item.nextFollowupAt)}</p>
                {item.sourceInputId ? <p>来源输入：{item.sourceInputId}</p> : null}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
