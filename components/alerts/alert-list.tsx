"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { AlertItem } from "@/types/alert";

const ruleTypeLabel: Record<AlertItem["ruleType"], string> = {
  no_followup_overdue: "No Followup Overdue (legacy)",
  active_response_no_quote: "Positive Reply No Quote (legacy)",
  quoted_but_stalled: "Quoted But Stalled",
  missing_decision_maker: "Missing Decision Maker (legacy)",
  high_probability_stalled: "High Probability Stalled",
  no_followup_timeout: "No Followup Timeout",
  positive_reply_but_no_progress: "Positive Reply No Progress",
  no_decision_maker: "No Decision Maker",
  ai_detected: "AI Detected Risk"
};

const sourceLabel: Record<AlertItem["source"], string> = {
  rule: "Rule",
  ai: "AI",
  hybrid: "Hybrid",
  fallback: "Fallback"
};

const levelTone: Record<AlertItem["level"], "secondary" | "default" | "destructive"> = {
  info: "secondary",
  warning: "default",
  critical: "destructive"
};

const statusLabel: Record<AlertItem["status"], string> = {
  open: "Open",
  watching: "Watching",
  resolved: "Resolved"
};

export function AlertList({
  items,
  onChangeStatus,
  onConvertToWorkItem,
  coverageMap
}: {
  items: AlertItem[];
  onChangeStatus: (alertId: string, status: AlertItem["status"]) => Promise<void> | void;
  onConvertToWorkItem?: (alertId: string) => Promise<void> | void;
  coverageMap?: Record<string, boolean>;
}): JSX.Element {
  return (
    <div className="space-y-3">
      {items.map((item) => (
        <Card key={item.id}>
          <CardContent className="p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-slate-900">{item.customerName}</p>
                <Badge variant={levelTone[item.level]}>{item.level}</Badge>
                <Badge variant="outline">{ruleTypeLabel[item.ruleType]}</Badge>
                <Badge variant="secondary">{sourceLabel[item.source]}</Badge>
                {coverageMap?.[item.id] ? <Badge variant="default">Task Linked</Badge> : null}
              </div>
              <p className="text-xs text-muted-foreground">{formatDateTime(item.createdAt)}</p>
            </div>

            <p className="text-sm font-medium text-slate-900">{item.title}</p>
            <p className="mt-1 text-sm leading-6 text-slate-700">{item.message}</p>
            <p className="mt-2 text-xs text-muted-foreground">Owner: {item.ownerName}</p>

            {item.evidence.length > 0 ? (
              <div className="mt-2 rounded-md border bg-slate-50 p-2">
                <p className="text-xs font-medium text-slate-700">Evidence</p>
                <p className="mt-1 text-xs text-slate-600">{item.evidence.slice(0, 3).join("；")}</p>
              </div>
            ) : null}

            {item.suggestedOwnerAction.length > 0 ? (
              <p className="mt-2 text-xs text-slate-600">Suggested actions: {item.suggestedOwnerAction.slice(0, 2).join("；")}</p>
            ) : null}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge variant="secondary">Status: {statusLabel[item.status]}</Badge>
              <Button size="sm" variant="outline" onClick={() => void onChangeStatus(item.id, "watching")}>
                Mark Watching
              </Button>
              <Button size="sm" variant="outline" onClick={() => void onChangeStatus(item.id, "resolved")}>
                Mark Resolved
              </Button>
              {onConvertToWorkItem ? (
                <Button size="sm" variant="secondary" onClick={() => void onConvertToWorkItem(item.id)}>
                  Convert To Task
                </Button>
              ) : null}
              <Button size="sm" variant="ghost" onClick={() => void onChangeStatus(item.id, "open")}>
                Reopen
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
