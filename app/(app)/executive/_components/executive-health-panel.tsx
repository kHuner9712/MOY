"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CustomerHealthSnapshot } from "@/types/automation";
import type { ExecutiveHealthPayload } from "@/services/executive-client-service";

export function ExecutiveHealthPanel(props: {
  health: ExecutiveHealthPayload | null;
  healthLoading: boolean;
  riskSnapshots: CustomerHealthSnapshot[];
}): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Risk Customers & Renewal Watch</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {props.healthLoading ? <p className="text-sm text-muted-foreground">Loading health snapshots...</p> : null}

        {!props.healthLoading && props.riskSnapshots.length === 0 ? <p className="text-sm text-muted-foreground">No at-risk customer snapshot.</p> : null}
        {props.riskSnapshots.slice(0, 8).map((item) => (
          <div key={item.id} className="rounded border p-2">
            <div className="mb-1 flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-slate-900">{item.customerName ?? item.customerId}</p>
              <Badge variant={item.healthBand === "critical" ? "destructive" : "default"}>
                {item.healthBand} / {item.overallHealthScore}
              </Badge>
            </div>
            <p className="text-xs text-slate-700">{item.summary ?? "-"}</p>
          </div>
        ))}

        <div className="pt-2">
          <p className="mb-1 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Renewal watch</p>
          {(props.health?.renewalWatch ?? []).slice(0, 8).map((item) => (
            <div key={item.id} className="rounded border p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{item.customerName ?? item.customerId}</p>
                <Badge variant={item.renewalStatus === "at_risk" ? "destructive" : item.renewalStatus === "expansion_candidate" ? "default" : "outline"}>
                  {item.renewalStatus}
                </Badge>
              </div>
              <p className="text-xs text-slate-700">{item.recommendationSummary ?? "-"}</p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
