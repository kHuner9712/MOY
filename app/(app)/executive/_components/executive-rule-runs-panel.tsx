"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { AutomationRuleRun } from "@/types/automation";

export function ExecutiveRuleRunsPanel(props: { runs: AutomationRuleRun[] }): JSX.Element {
  return (
    <section>
      <Card>
        <CardHeader>
          <CardTitle>Recent Rule Runs</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="min-w-[860px] text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-[0.14em] text-slate-500">
                <th className="px-2 py-2">Created At</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-2 py-2">Matched</th>
                <th className="px-2 py-2">Actions</th>
                <th className="px-2 py-2">Summary</th>
              </tr>
            </thead>
            <tbody>
              {props.runs.map((run) => (
                <tr key={run.id} className="border-b">
                  <td className="px-2 py-2">{formatDateTime(run.createdAt)}</td>
                  <td className="px-2 py-2">
                    <Badge variant={run.runStatus === "completed" ? "default" : run.runStatus === "failed" ? "destructive" : "secondary"}>{run.runStatus}</Badge>
                  </td>
                  <td className="px-2 py-2">{run.matchedCount}</td>
                  <td className="px-2 py-2">{run.createdActionCount}</td>
                  <td className="px-2 py-2">{run.summary ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </section>
  );
}
