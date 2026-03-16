"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useExecutiveCockpit } from "@/hooks/use-executive-cockpit";
import { formatDateTime } from "@/lib/format";
import { executiveClientService, type ExecutiveHealthPayload } from "@/services/executive-client-service";
import type { ExecutiveBriefType } from "@/types/automation";
import { Activity, AlertTriangle, ArrowUpRight, CalendarClock, HeartPulse, RefreshCw, ShieldAlert } from "lucide-react";

export default function ExecutiveCockpitPage(): JSX.Element {
  const { user } = useAuth();
  const { summary, events, briefs, loading, error, reload } = useExecutiveCockpit(user?.role === "manager");
  const [health, setHealth] = useState<ExecutiveHealthPayload | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busyEventId, setBusyEventId] = useState<string | null>(null);
  const [generatingBrief, setGeneratingBrief] = useState(false);
  const [briefType, setBriefType] = useState<ExecutiveBriefType>("executive_daily");

  const loadHealth = async (): Promise<void> => {
    setHealthLoading(true);
    try {
      const payload = await executiveClientService.getExecutiveHealth();
      setHealth(payload);
    } catch {
      setHealth({
        healthSnapshots: [],
        renewalWatch: []
      });
    } finally {
      setHealthLoading(false);
    }
  };

  useEffect(() => {
    if (user?.role === "manager") {
      void loadHealth();
    }
  }, [user?.role]);

  const runEventAction = async (eventId: string, action: "ack" | "resolve" | "ignore"): Promise<void> => {
    setBusyEventId(eventId);
    setActionMessage(null);
    try {
      if (action === "ack") await executiveClientService.ackBusinessEvent(eventId);
      if (action === "resolve") await executiveClientService.resolveBusinessEvent(eventId);
      if (action === "ignore") await executiveClientService.ignoreBusinessEvent(eventId);
      setActionMessage(`Event ${action} completed.`);
      await reload(false);
    } catch (cause) {
      setActionMessage(cause instanceof Error ? cause.message : "Failed to update event");
    } finally {
      setBusyEventId(null);
    }
  };

  const runRefresh = async (): Promise<void> => {
    setActionMessage(null);
    await Promise.all([reload(true), loadHealth()]);
  };

  const generateBrief = async (): Promise<void> => {
    setGeneratingBrief(true);
    setActionMessage(null);
    try {
      const result = await executiveClientService.generateExecutiveBrief({ briefType });
      setActionMessage(
        result.usedFallback
          ? `Executive brief generated with fallback (${result.fallbackReason ?? "rule"})`
          : "Executive brief generated."
      );
      await reload(false);
    } catch (cause) {
      setActionMessage(cause instanceof Error ? cause.message : "Failed to generate executive brief");
    } finally {
      setGeneratingBrief(false);
    }
  };

  const riskSnapshots = useMemo(
    () => (health?.healthSnapshots ?? []).filter((item) => item.healthBand === "critical" || item.healthBand === "at_risk").slice(0, 12),
    [health?.healthSnapshots]
  );

  if (user?.role !== "manager") {
    return <div className="text-sm text-muted-foreground">Only manager can access executive cockpit.</div>;
  }

  if (loading || !summary) return <div className="text-sm text-muted-foreground">Loading executive cockpit...</div>;
  if (error) return <div className="text-sm text-rose-600">Failed to load executive cockpit: {error}</div>;

  return (
    <div>
      <PageHeader
        title="Executive Cockpit"
        description="Unified operations cockpit for risks, opportunities, rule hits and actionable management moves."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void runRefresh()}>
              <RefreshCw className="mr-1 h-4 w-4" />
              Refresh Signals
            </Button>
            <Select value={briefType} onValueChange={(value) => setBriefType(value as ExecutiveBriefType)}>
              <SelectTrigger className="w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="executive_daily">executive_daily</SelectItem>
                <SelectItem value="executive_weekly">executive_weekly</SelectItem>
                <SelectItem value="retention_watch">retention_watch</SelectItem>
                <SelectItem value="trial_watch">trial_watch</SelectItem>
                <SelectItem value="deal_watch">deal_watch</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={() => void generateBrief()} disabled={generatingBrief}>
              {generatingBrief ? "Generating..." : "Generate Brief"}
            </Button>
            <Button asChild variant="outline">
              <Link href="/settings/automation">Automation Rules</Link>
            </Button>
          </div>
        }
      />

      {actionMessage ? <p className="mb-3 text-sm text-muted-foreground">{actionMessage}</p> : null}

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        <StatCard title="Open Events" value={summary.openEvents} icon={<Activity className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Critical Risks" value={summary.criticalRisks} icon={<ShieldAlert className="h-4 w-4 text-rose-700" />} />
        <StatCard title="Trial Stalled" value={summary.trialStalled} icon={<AlertTriangle className="h-4 w-4 text-amber-700" />} />
        <StatCard title="Deal Blocked" value={summary.dealBlocked} icon={<AlertTriangle className="h-4 w-4 text-rose-500" />} />
        <StatCard title="Renewal At Risk" value={summary.renewalAtRisk} icon={<HeartPulse className="h-4 w-4 text-red-700" />} />
        <StatCard title="Manager Attention" value={summary.managerAttentionRequired} icon={<CalendarClock className="h-4 w-4 text-indigo-700" />} />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Health Distribution</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {summary.healthBandDistribution.map((item) => (
              <div key={item.band} className="flex items-center justify-between rounded border px-2 py-1">
                <span>{item.band}</span>
                <Badge variant={item.band === "critical" ? "destructive" : item.band === "at_risk" ? "default" : "secondary"}>{item.count}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Deal / Trial Health</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>strategic deals: {summary.dealHealth.strategicDeals}</p>
            <p>blocked checkpoints: {summary.dealHealth.blockedCheckpoints}</p>
            <p>manager attention deals: {summary.dealHealth.managerAttentionDeals}</p>
            <p>activated trial tracks: {summary.trialHealth.activated}</p>
            <p>onboarding completed: {summary.trialHealth.onboardingCompleted}</p>
            <p>first value reached: {summary.trialHealth.firstValue}</p>
            <p>conversion risk tracks: {summary.trialHealth.conversionRisk}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Team Execution Health</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>overdue work: {summary.teamExecution.overdueWork}</p>
            <p>timeliness score: {summary.teamExecution.followupTimelinessScore}</p>
            <p>shallow activity ratio: {(summary.teamExecution.shallowActivityRatio * 100).toFixed(0)}%</p>
            <Button asChild size="sm" variant="outline">
              <Link href="/manager/rhythm">Open Rhythm View</Link>
            </Button>
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recommended Actions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {summary.recommendations.length === 0 ? <p className="text-sm text-muted-foreground">No recommendation yet.</p> : null}
            {summary.recommendations.map((item) => (
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
            {briefs.length === 0 ? <p className="text-sm text-muted-foreground">No executive brief generated yet.</p> : null}
            {briefs.slice(0, 6).map((brief) => (
              <div key={brief.id} className="rounded-md border p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{brief.headline ?? brief.briefType}</p>
                  <Badge variant={brief.status === "completed" ? "default" : brief.status === "failed" ? "destructive" : "secondary"}>{brief.status}</Badge>
                </div>
                <p className="text-xs text-slate-700">{brief.summary ?? "-"}</p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(brief.createdAt)}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Open Business Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {events.length === 0 ? <p className="text-sm text-muted-foreground">No open business events.</p> : null}
            {events.slice(0, 20).map((event) => (
              <div key={event.id} className="rounded-md border p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{event.eventType}</Badge>
                    <Badge variant={event.severity === "critical" ? "destructive" : event.severity === "warning" ? "default" : "secondary"}>
                      {event.severity}
                    </Badge>
                    <Badge variant={event.status === "open" ? "destructive" : event.status === "acknowledged" ? "default" : "secondary"}>
                      {event.status}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{formatDateTime(event.createdAt)}</p>
                </div>
                <p className="text-sm text-slate-700">{event.eventSummary}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" disabled={busyEventId === event.id} onClick={() => void runEventAction(event.id, "ack")}>
                    Ack
                  </Button>
                  <Button size="sm" variant="outline" disabled={busyEventId === event.id} onClick={() => void runEventAction(event.id, "resolve")}>
                    Resolve
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busyEventId === event.id} onClick={() => void runEventAction(event.id, "ignore")}>
                    Ignore
                  </Button>
                  {event.entityType === "deal_room" ? (
                    <Button asChild size="sm" variant="outline">
                      <Link href={`/deals/${event.entityId}`}>
                        Open Deal
                        <ArrowUpRight className="ml-1 h-3 w-3" />
                      </Link>
                    </Button>
                  ) : null}
                  {event.entityType === "customer" ? (
                    <Button asChild size="sm" variant="outline">
                      <Link href={`/customers/${event.entityId}`}>
                        Open Customer
                        <ArrowUpRight className="ml-1 h-3 w-3" />
                      </Link>
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Risk Customers & Renewal Watch</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {healthLoading ? <p className="text-sm text-muted-foreground">Loading health snapshots...</p> : null}

            {!healthLoading && riskSnapshots.length === 0 ? <p className="text-sm text-muted-foreground">No at-risk customer snapshot.</p> : null}
            {riskSnapshots.slice(0, 8).map((item) => (
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
              {(health?.renewalWatch ?? []).slice(0, 8).map((item) => (
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
      </section>

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
                {summary.recentRuleRuns.map((run) => (
                  <tr key={run.id} className="border-b">
                    <td className="px-2 py-2">{formatDateTime(run.createdAt)}</td>
                    <td className="px-2 py-2">
                      <Badge variant={run.runStatus === "completed" ? "default" : run.runStatus === "failed" ? "destructive" : "secondary"}>
                        {run.runStatus}
                      </Badge>
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
    </div>
  );
}

