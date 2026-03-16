"use client";

import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useGrowthPipeline } from "@/hooks/use-growth-pipeline";
import { formatDateTime } from "@/lib/format";
import { AlertTriangle, CheckCircle2, LineChart, Rocket, UserRoundCheck, Users } from "lucide-react";

export default function ManagerConversionPage(): JSX.Element {
  const { user } = useAuth();
  const { summary, loading, error, reload } = useGrowthPipeline(30, user?.role === "manager");

  if (user?.role !== "manager") {
    return <div className="text-sm text-muted-foreground">Only manager can access conversion view.</div>;
  }

  if (loading) return <div className="text-sm text-muted-foreground">Loading conversion view...</div>;
  if (error || !summary) return <div className="text-sm text-rose-600">Failed to load conversion data: {error}</div>;

  return (
    <div>
      <PageHeader
        title="Commercial Conversion View"
        description="Track lead -> demo -> trial -> conversion readiness and identify where the pipeline stalls."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void reload()}>
              Refresh
            </Button>
            <Button asChild variant="outline">
              <Link href="/executive">Executive Cockpit</Link>
            </Button>
            <Button asChild>
              <Link href="/growth">Open Growth Workspace</Link>
            </Button>
          </div>
        }
      />

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="New Leads" value={summary.leadsNew} icon={<Users className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Qualified Leads" value={summary.leadsQualified} icon={<UserRoundCheck className="h-4 w-4 text-emerald-700" />} />
        <StatCard title="Demo Completion" value={`${(summary.demoCompletionRate * 100).toFixed(0)}%`} icon={<LineChart className="h-4 w-4 text-indigo-700" />} />
        <StatCard title="Trial Activation" value={`${(summary.trialActivationRate * 100).toFixed(0)}%`} icon={<Rocket className="h-4 w-4 text-violet-700" />} />
        <StatCard title="Onboarding Completion" value={`${(summary.onboardingCompletionRate * 100).toFixed(0)}%`} icon={<CheckCircle2 className="h-4 w-4 text-emerald-700" />} />
        <StatCard title="Conversion Ready" value={summary.conversionReadyCount} icon={<LineChart className="h-4 w-4 text-slate-700" />} />
        <StatCard title="Converted" value={summary.convertedCount} icon={<CheckCircle2 className="h-4 w-4 text-green-700" />} />
        <StatCard title="High-risk Trials" value={summary.highRiskTracks.length} icon={<AlertTriangle className="h-4 w-4 text-rose-700" />} />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Lead Source Distribution</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.bySource.length === 0 ? <p className="text-sm text-muted-foreground">No source data in this period.</p> : null}
            {summary.bySource.map((item) => (
              <div key={item.source} className="flex items-center justify-between rounded-md border p-2 text-sm">
                <span>{item.source}</span>
                <Badge variant="outline">{item.count}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Industry Conversion Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.byIndustry.length === 0 ? <p className="text-sm text-muted-foreground">No industry data yet.</p> : null}
            {summary.byIndustry.map((item) => (
              <div key={item.industry} className="rounded-md border p-2 text-sm">
                <p className="font-semibold text-slate-900">{item.industry}</p>
                <p className="text-xs text-muted-foreground">
                  leads={item.leadCount} · trials={item.trialCount} · converted={item.convertedCount}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.2fr_1.2fr]">
        <Card>
          <CardHeader>
            <CardTitle>Conversion Risk List</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.highRiskTracks.length === 0 ? <p className="text-sm text-muted-foreground">No high-risk trial conversion tracks.</p> : null}
            {summary.highRiskTracks.map((track) => (
              <div key={track.id} className="rounded-md border p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{track.ownerName ?? track.ownerId}</p>
                  <Badge variant="destructive">{track.conversionReadinessScore}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">stage={track.currentStage}</p>
                <p className="text-xs text-slate-700">{track.summary}</p>
                {track.targetOrgId ? (
                  <Button asChild size="sm" variant="outline" className="mt-2">
                    <Link href={`/growth`}>Open in Growth</Link>
                  </Button>
                ) : null}
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent Conversion Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.recentEvents.length === 0 ? <p className="text-sm text-muted-foreground">No conversion events yet.</p> : null}
            {summary.recentEvents.slice(0, 14).map((event) => (
              <div key={event.id} className="rounded-md border p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <Badge variant="outline">{event.eventType}</Badge>
                  <p className="text-xs text-muted-foreground">{formatDateTime(event.createdAt)}</p>
                </div>
                <p className="text-sm text-slate-700">{event.eventSummary}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
