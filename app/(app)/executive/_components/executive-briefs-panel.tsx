"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { ExecutiveBrief } from "@/types/automation";

export function ExecutiveBriefsPanel(props: {
  recommendations: string[];
  briefs: ExecutiveBrief[];
}): JSX.Element {
  return (
    <section className="mb-4 grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Recommended Actions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {props.recommendations.length === 0 ? <p className="text-sm text-muted-foreground">No recommendation yet.</p> : null}
          {props.recommendations.map((item) => (
            <p key={item} className="text-sm text-slate-700">
              - {item}
            </p>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Latest Executive Briefs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {props.briefs.length === 0 ? <p className="text-sm text-muted-foreground">No executive brief generated yet.</p> : null}
          {props.briefs.slice(0, 6).map((brief) => (
            <div key={brief.id} className="rounded-md border p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{brief.headline ?? brief.briefType}</p>
                <Badge variant={brief.status === "completed" ? "default" : brief.status === "failed" ? "destructive" : "secondary"}>
                  {brief.status}
                </Badge>
              </div>
              <p className="text-xs text-slate-700">{brief.summary ?? "-"}</p>
              <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(brief.createdAt)}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    </section>
  );
}
