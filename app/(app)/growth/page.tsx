"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useGrowthPipeline } from "@/hooks/use-growth-pipeline";
import { formatDateTime } from "@/lib/format";
import { AlertTriangle, CalendarCheck2, ChartColumn, CheckCircle2, Rocket, TrendingUp, Users } from "lucide-react";

export default function GrowthPage(): JSX.Element {
  const { user } = useAuth();
  const { summary, leads, trialRequests, events, loading, error, reload, convertLead, activateTrial } = useGrowthPipeline(30, Boolean(user));
  const [message, setMessage] = useState<string | null>(null);
  const [workingKey, setWorkingKey] = useState<string | null>(null);

  const pendingDemoLeads = useMemo(() => leads.filter((item) => item.status === "qualified" || item.status === "demo_scheduled"), [leads]);
  const trialPending = useMemo(() => trialRequests.filter((item) => item.requestStatus === "pending" || item.requestStatus === "approved"), [trialRequests]);

  const runLeadConvert = async (leadId: string): Promise<void> => {
    setWorkingKey(`lead:${leadId}`);
    setMessage(null);
    try {
      const res = await convertLead(leadId);
      setMessage(res.converted ? "Lead converted into internal pipeline." : "Lead already in internal pipeline.");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "lead_convert_failed");
    } finally {
      setWorkingKey(null);
    }
  };

  const runTrialActivate = async (trialRequestId: string): Promise<void> => {
    setWorkingKey(`trial:${trialRequestId}`);
    setMessage(null);
    try {
      const res = await activateTrial(trialRequestId);
      setMessage(`Trial activated for org ${res.targetOrgId.slice(0, 8)}...`);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "trial_activate_failed");
    } finally {
      setWorkingKey(null);
    }
  };

  if (!user) return <div className="text-sm text-muted-foreground">Please login first.</div>;
  if (loading) return <div className="text-sm text-muted-foreground">Loading growth pipeline...</div>;
  if (error || !summary) return <div className="text-sm text-rose-600">Failed to load growth pipeline: {error}</div>;

  return (
    <div>
      <PageHeader
        title="Growth Pipeline"
        description="Use MOY to sell MOY: lead intake, demo/trial operations, and conversion tracking in one place."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void reload()}>
              Refresh
            </Button>
            <Button asChild>
              <Link href="/request-demo">Open Public Demo Form</Link>
            </Button>
          </div>
        }
      />

      {message ? <p className="mb-3 text-sm text-muted-foreground">{message}</p> : null}

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Leads (30d)" value={summary.leadsTotal} icon={<Users className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Demo Requests" value={summary.demoRequested} icon={<CalendarCheck2 className="h-4 w-4 text-indigo-700" />} />
        <StatCard title="Trial Activated" value={`${summary.trialActivated} (${(summary.trialActivationRate * 100).toFixed(0)}%)`} icon={<Rocket className="h-4 w-4 text-emerald-700" />} />
        <StatCard title="Conversion Ready" value={summary.conversionReadyCount} icon={<TrendingUp className="h-4 w-4 text-violet-700" />} />
        <StatCard title="Converted" value={summary.convertedCount} icon={<CheckCircle2 className="h-4 w-4 text-emerald-700" />} />
        <StatCard title="Onboarding Completion" value={`${(summary.onboardingCompletionRate * 100).toFixed(0)}%`} icon={<ChartColumn className="h-4 w-4 text-slate-700" />} />
        <StatCard title="Pending Demo Followup" value={pendingDemoLeads.length} icon={<AlertTriangle className="h-4 w-4 text-amber-600" />} />
        <StatCard title="Pending Trial Activation" value={trialPending.length} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Inbound Leads</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {leads.length === 0 ? <p className="text-sm text-muted-foreground">No inbound leads yet.</p> : null}
            {leads.slice(0, 12).map((lead) => (
              <div key={lead.id} className="rounded-md border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{lead.companyName}</p>
                  <Badge variant="outline">{lead.status}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {lead.contactName} · {lead.email} · {lead.leadSource}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(lead.createdAt)}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void runLeadConvert(lead.id)}
                    disabled={workingKey === `lead:${lead.id}`}
                  >
                    Convert to Pipeline
                  </Button>
                  {lead.convertedCustomerId ? (
                    <Button size="sm" variant="outline" asChild>
                      <Link href={`/customers/${lead.convertedCustomerId}`}>Open Customer</Link>
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Trial Requests</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {trialRequests.length === 0 ? <p className="text-sm text-muted-foreground">No trial requests yet.</p> : null}
            {trialRequests.slice(0, 12).map((trial) => (
              <div key={trial.id} className="rounded-md border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{trial.requestedByEmail}</p>
                  <Badge variant={trial.requestStatus === "activated" ? "default" : "outline"}>{trial.requestStatus}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">{formatDateTime(trial.requestedAt)}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    onClick={() => void runTrialActivate(trial.id)}
                    disabled={workingKey === `trial:${trial.id}` || trial.requestStatus === "activated"}
                  >
                    Activate Trial
                  </Button>
                  {trial.targetOrgId ? <Badge variant="secondary">org={trial.targetOrgId.slice(0, 8)}...</Badge> : null}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.1fr_1.4fr]">
        <Card>
          <CardHeader>
            <CardTitle>AI Funnel Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-slate-700">
            <p>{summary.aiSummary.funnelSummary}</p>
            {summary.aiSummary.bestChannels.map((item) => (
              <p key={item}>- best channel: {item}</p>
            ))}
            {summary.aiSummary.weakPoints.map((item) => (
              <p key={item}>- weak point: {item}</p>
            ))}
            {summary.aiSummary.nextBestActions.map((item) => (
              <p key={item}>- next action: {item}</p>
            ))}
            {summary.aiSummaryUsedFallback ? <Badge variant="secondary">Fallback</Badge> : <Badge>AI</Badge>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent Conversion Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {events.length === 0 ? <p className="text-sm text-muted-foreground">No conversion events yet.</p> : null}
            {events.slice(0, 14).map((event) => (
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
