"use client";

import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useManagerOutcomes } from "@/hooks/use-manager-outcomes";
import { useManagerQuality } from "@/hooks/use-manager-quality";
import { Activity, AlertTriangle, Gauge, Users } from "lucide-react";

export default function ManagerQualityPage(): JSX.Element {
  const { user } = useAuth();
  const { periodType, setPeriodType, data, loading, error, reload } = useManagerQuality("weekly");
  const { data: outcomeData } = useManagerOutcomes("weekly");

  if (user?.role !== "manager") {
    return <div className="text-sm text-muted-foreground">Only manager can access quality view.</div>;
  }

  if (loading) return <div className="text-sm text-muted-foreground">Loading manager quality view...</div>;
  if (error || !data) return <div className="text-sm text-rose-600">Failed to load quality view: {error}</div>;

  const avgScore =
    data.userRows.length === 0 ? 0 : data.userRows.reduce((sum, item) => sum + item.activityQualityScore, 0) / data.userRows.length;
  const avgShallow =
    data.userRows.length === 0 ? 0 : data.userRows.reduce((sum, item) => sum + item.shallowActivityRatio, 0) / data.userRows.length;
  const highRiskUnhandled = data.userRows.reduce((sum, item) => sum + item.highRiskUnhandledCount, 0);

  return (
    <div>
      <PageHeader
        title="Operating Quality View"
        description="Focus on execution quality and coaching leverage, not employee surveillance."
        action={
          <div className="flex items-center gap-2">
            <Button variant={periodType === "weekly" ? "default" : "outline"} onClick={() => setPeriodType("weekly")}>
              Weekly
            </Button>
            <Button variant={periodType === "monthly" ? "default" : "outline"} onClick={() => setPeriodType("monthly")}>
              Monthly
            </Button>
            <Button variant="outline" onClick={() => void reload(periodType)}>
              Refresh
            </Button>
            <Button asChild variant="outline">
              <Link href="/manager/outcomes">Outcome Intelligence</Link>
            </Button>
          </div>
        }
      />

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Team Quality Score" value={avgScore.toFixed(1)} icon={<Gauge className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Shallow Activity Ratio" value={`${(avgShallow * 100).toFixed(0)}%`} icon={<Activity className="h-4 w-4 text-amber-600" />} />
        <StatCard title="High-risk Unhandled" value={highRiskUnhandled} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
        <StatCard title="Sales in Snapshot" value={data.userRows.length} icon={<Users className="h-4 w-4 text-emerald-600" />} />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Team Insight</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm leading-6 text-slate-700">{data.aiInsight.executive_summary}</p>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.15em] text-slate-500">Replicable patterns</p>
              {data.aiInsight.replicable_patterns.map((item) => (
                <p key={item} className="text-sm text-slate-700">
                  - {item}
                </p>
              ))}
            </div>
            {data.usedFallback ? <Badge variant="secondary">Fallback insight</Badge> : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Needs Coaching</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.aiInsight.needs_coaching.length === 0 ? <p className="text-sm text-muted-foreground">No urgent coaching target now.</p> : null}
            {data.aiInsight.needs_coaching.map((item) => (
              <div key={item.user_id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-900">{item.user_name}</p>
                  <Badge variant={item.priority === "high" ? "destructive" : "secondary"}>{item.priority}</Badge>
                </div>
                <p className="text-xs text-slate-700">{item.reason}</p>
                <Link href={`/manager?sales=${item.user_id}`} className="mt-1 inline-block text-xs text-sky-700">
                  View sales customer detail
                </Link>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Outcome Lens</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>Positive progress rate: {(((outcomeData?.summary.positiveProgressRate ?? 0) * 100)).toFixed(0)}%</p>
            <p>Suggestion adoption rate: {(((outcomeData?.summary.adoptionRate ?? 0) * 100)).toFixed(0)}%</p>
            <p>Adoption to positive rate: {(((outcomeData?.summary.adoptionPositiveRate ?? 0) * 100)).toFixed(0)}%</p>
            <p className="text-xs text-muted-foreground">Quality and outcomes are shown together to avoid busy-but-ineffective bias.</p>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Sales Operating Quality Matrix</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="min-w-[980px] text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-[0.14em] text-slate-500">
                <th className="px-2 py-2">Sales</th>
                <th className="px-2 py-2">Quality</th>
                <th className="px-2 py-2">On-time</th>
                <th className="px-2 py-2">Completeness</th>
                <th className="px-2 py-2">Stage Progress</th>
                <th className="px-2 py-2">High-value Focus</th>
                <th className="px-2 py-2">Risk Response</th>
                <th className="px-2 py-2">Shallow Ratio</th>
                <th className="px-2 py-2">Stalled Customers</th>
                <th className="px-2 py-2">High-risk Unhandled</th>
              </tr>
            </thead>
            <tbody>
              {data.userRows
                .slice()
                .sort((a, b) => b.activityQualityScore - a.activityQualityScore)
                .map((item) => (
                  <tr key={item.userId} className="border-b">
                    <td className="px-2 py-2 font-medium text-slate-900">{item.userName}</td>
                    <td className="px-2 py-2">{item.activityQualityScore.toFixed(1)}</td>
                    <td className="px-2 py-2">{(item.onTimeFollowupRate * 100).toFixed(0)}%</td>
                    <td className="px-2 py-2">{item.followupCompletenessScore.toFixed(1)}</td>
                    <td className="px-2 py-2">{item.stageProgressionScore.toFixed(1)}</td>
                    <td className="px-2 py-2">{item.highValueFocusScore.toFixed(1)}</td>
                    <td className="px-2 py-2">{item.riskResponseScore.toFixed(1)}</td>
                    <td className="px-2 py-2">{(item.shallowActivityRatio * 100).toFixed(0)}%</td>
                    <td className="px-2 py-2">{item.stalledCustomerCount}</td>
                    <td className="px-2 py-2">{item.highRiskUnhandledCount}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
