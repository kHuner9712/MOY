"use client";

import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useManagerOutcomes } from "@/hooks/use-manager-outcomes";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { formatDateTime } from "@/lib/format";
import { Activity, AlertTriangle, CheckCircle2, TrendingUp } from "lucide-react";

export default function ManagerOutcomesPage(): JSX.Element {
  const { user } = useAuth();
  const { periodType, setPeriodType, data, loading, error, reload, generateReview } = useManagerOutcomes("weekly");
  const { summary: touchpointSummary, review: touchpointReview, generateReview: generateTouchpointReview } = useTouchpoints({
    enabled: user?.role === "manager"
  });
  const [message, setMessage] = useState<string | null>(null);

  if (user?.role !== "manager") {
    return <div className="text-sm text-muted-foreground">Only manager can access outcome intelligence.</div>;
  }

  if (loading) return <div className="text-sm text-muted-foreground">Loading manager outcome intelligence...</div>;
  if (error || !data) return <div className="text-sm text-rose-600">Failed to load outcome intelligence: {error}</div>;

  const runReview = async () => {
    setMessage(null);
    try {
      const result = await generateReview({
        reviewScope: "team"
      });
      setMessage(result.usedFallback ? "Outcome review generated with fallback logic." : "Outcome review generated.");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Failed to generate outcome review");
    }
  };

  return (
    <div>
      <PageHeader
        title="Manager Outcome Intelligence"
        description="See which actions truly drive progress, and which patterns look busy but fail to move stages."
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
              <Link href="/manager/conversion">Conversion View</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/executive">Executive Cockpit</Link>
            </Button>
            <Button onClick={() => void runReview()}>Generate Review</Button>
            <Button variant="outline" onClick={() => void generateTouchpointReview()}>
              External Review
            </Button>
          </div>
        }
      />

      {message ? <p className="mb-3 text-sm text-muted-foreground">{message}</p> : null}

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Total Outcomes" value={data.summary.totalOutcomes} icon={<Activity className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Positive Progress Rate" value={`${(data.summary.positiveProgressRate * 100).toFixed(0)}%`} icon={<CheckCircle2 className="h-4 w-4 text-emerald-600" />} />
        <StatCard title="Suggestion Adoption Rate" value={`${(data.summary.adoptionRate * 100).toFixed(0)}%`} icon={<TrendingUp className="h-4 w-4 text-indigo-600" />} />
        <StatCard
          title="Adoption -> Positive"
          value={`${(data.summary.adoptionPositiveRate * 100).toFixed(0)}%`}
          icon={<AlertTriangle className="h-4 w-4 text-amber-600" />}
        />
        <StatCard title="Waiting Replies" value={touchpointSummary.waitingReplyThreads} icon={<AlertTriangle className="h-4 w-4 text-rose-700" />} />
        <StatCard title="Upcoming Meetings" value={touchpointSummary.upcomingMeetings} icon={<Activity className="h-4 w-4 text-indigo-700" />} />
        <StatCard title="Touchpoint Events (7d)" value={touchpointSummary.totalEvents} icon={<TrendingUp className="h-4 w-4 text-slate-700" />} />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Effective Patterns</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.effectivePatterns.length === 0 ? <p className="text-sm text-muted-foreground">No clear effective pattern yet.</p> : null}
            {data.effectivePatterns.map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ineffective Patterns</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.ineffectivePatterns.length === 0 ? <p className="text-sm text-muted-foreground">No repeated anti-pattern yet.</p> : null}
            {data.ineffectivePatterns.map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Customer Stall Reasons</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.customerStallReasons.length === 0 ? <p className="text-sm text-muted-foreground">No clear stall reason cluster yet.</p> : null}
            {data.customerStallReasons.map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Sales Effectiveness Matrix</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="min-w-[820px] text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-[0.14em] text-slate-500">
                  <th className="px-2 py-2">Sales</th>
                  <th className="px-2 py-2">Outcomes</th>
                  <th className="px-2 py-2">Positive</th>
                  <th className="px-2 py-2">Adoption</th>
                  <th className="px-2 py-2">Adoption Positive</th>
                </tr>
              </thead>
              <tbody>
                {data.bySales.map((item) => (
                  <tr key={item.userId} className="border-b">
                    <td className="px-2 py-2 font-medium text-slate-900">{item.userName}</td>
                    <td className="px-2 py-2">{item.totalOutcomes}</td>
                    <td className="px-2 py-2">{(item.positiveProgressRate * 100).toFixed(0)}%</td>
                    <td className="px-2 py-2">{(item.adoptionRate * 100).toFixed(0)}%</td>
                    <td className="px-2 py-2">{(item.adoptionPositiveRate * 100).toFixed(0)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Repeated Failure Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.repeatedFailurePatterns.length === 0 ? <p className="text-sm text-muted-foreground">No repeated failure signal yet.</p> : null}
            {data.repeatedFailurePatterns.map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
            <Button asChild variant="outline" className="mt-2">
              <Link href="/playbooks">Open Playbooks</Link>
            </Button>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent Outcome Reviews</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.recentReviews.length === 0 ? <p className="text-sm text-muted-foreground">No outcome review generated yet.</p> : null}
            {data.recentReviews.slice(0, 8).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant={item.status === "completed" ? "default" : item.status === "failed" ? "destructive" : "secondary"}>
                    {item.status}
                  </Badge>
                </div>
                <p className="text-xs text-slate-700">{item.executiveSummary}</p>
                <p className="mt-1 text-xs text-muted-foreground">{item.periodStart} ~ {item.periodEnd}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent Team Playbooks</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.recentPlaybooks.length === 0 ? <p className="text-sm text-muted-foreground">No playbook yet.</p> : null}
            {data.recentPlaybooks.slice(0, 8).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant="outline">{Math.round(item.confidenceScore * 100)}%</Badge>
                </div>
                <p className="text-xs text-slate-700">{item.summary}</p>
                <p className="mt-1 text-xs text-muted-foreground">Updated {formatDateTime(item.updatedAt)}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      {touchpointReview.result ? (
        <section className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>External Progress Assessment</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-sm text-slate-700">{touchpointReview.result.externalProgressAssessment}</p>
              {(touchpointReview.result.stalledTouchpoints ?? []).slice(0, 4).map((item) => (
                <p key={item} className="text-sm text-slate-700">
                  - {item}
                </p>
              ))}
              {touchpointReview.usedFallback ? <Badge variant="secondary">Fallback</Badge> : <Badge variant="default">AI</Badge>}
            </CardContent>
          </Card>
        </section>
      ) : null}
    </div>
  );
}
