"use client";

import Link from "next/link";

import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ExecutiveCockpitSummary } from "@/types/automation";
import { Activity, AlertTriangle, CalendarClock, HeartPulse, ShieldAlert } from "lucide-react";

export function ExecutiveStats(props: { summary: ExecutiveCockpitSummary }): JSX.Element {
  const { summary } = props;

  return (
    <>
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
    </>
  );
}
