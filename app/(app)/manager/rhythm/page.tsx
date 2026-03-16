"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useManagerOutcomes } from "@/hooks/use-manager-outcomes";
import { useManagerRhythm } from "@/hooks/use-manager-rhythm";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { dealRoomClientService } from "@/services/deal-room-client-service";
import { Activity, AlertTriangle, CalendarClock, TrendingUp } from "lucide-react";

export default function ManagerRhythmPage(): JSX.Element {
  const { user } = useAuth();
  const { periodType, setPeriodType, data, loading, error, generate, reload } = useManagerRhythm("daily");
  const { data: outcomeData } = useManagerOutcomes("weekly");
  const { summary: touchpointSummary } = useTouchpoints({
    enabled: user?.role === "manager"
  });
  const [escalatedDeals, setEscalatedDeals] = useState<Array<{ id: string; title: string; ownerName: string; roomStatus: string }>>([]);
  const [blockedDeals, setBlockedDeals] = useState<Array<{ id: string; title: string; ownerName: string; roomStatus: string }>>([]);

  useEffect(() => {
    let cancelled = false;
    const loadDeals = async () => {
      try {
        const rooms = await dealRoomClientService.list({
          statuses: ["escalated", "blocked", "active", "watchlist"],
          limit: 60
        });
        if (cancelled) return;
        setEscalatedDeals(rooms.filter((item) => item.roomStatus === "escalated").slice(0, 8));
        setBlockedDeals(rooms.filter((item) => item.roomStatus === "blocked").slice(0, 8));
      } catch {
        if (!cancelled) {
          setEscalatedDeals([]);
          setBlockedDeals([]);
        }
      }
    };
    void loadDeals();
    return () => {
      cancelled = true;
    };
  }, []);

  if (user?.role !== "manager") {
    return <div className="text-sm text-muted-foreground">Only manager can access rhythm view.</div>;
  }

  if (loading) return <div className="text-sm text-muted-foreground">Loading manager rhythm...</div>;
  if (error || !data) return <div className="text-sm text-rose-600">Failed to load manager rhythm: {error}</div>;

  return (
    <div>
      <PageHeader
        title="Manager Execution Rhythm"
        description="See team execution tempo, overdue pressure, and critical task follow-through."
        action={
          <div className="flex items-center gap-2">
            <Button variant={periodType === "daily" ? "default" : "outline"} onClick={() => setPeriodType("daily")}>
              Daily
            </Button>
            <Button variant={periodType === "weekly" ? "default" : "outline"} onClick={() => setPeriodType("weekly")}>
              Weekly
            </Button>
            <Button variant="outline" onClick={() => void reload(periodType)}>
              Refresh
            </Button>
            <Button onClick={() => void generate(periodType)}>Generate Insight</Button>
            <Button asChild variant="outline">
              <Link href="/manager/outcomes">Outcome Intelligence</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/executive">Executive Cockpit</Link>
            </Button>
          </div>
        }
      />

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Total Tasks" value={data.teamTotals.totalTasks} icon={<Activity className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Completion Rate" value={`${(data.teamTotals.completionRate * 100).toFixed(0)}%`} icon={<TrendingUp className="h-4 w-4 text-emerald-600" />} />
        <StatCard title="Overdue Tasks" value={data.teamTotals.overdueTasks} icon={<CalendarClock className="h-4 w-4 text-amber-600" />} />
        <StatCard title="Critical Open" value={data.teamTotals.criticalOpenTasks} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
      </section>

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Prep Coverage" value={`${(data.teamTotals.prepCoverageRate * 100).toFixed(0)}%`} icon={<Activity className="h-4 w-4 text-indigo-600" />} />
        <StatCard title="High-Value Without Prep" value={data.teamTotals.highValueWithoutPrepCount} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
        <StatCard title="Need Support" value={data.aiInsight.who_needs_support.length} icon={<TrendingUp className="h-4 w-4 text-amber-600" />} />
        <StatCard title="Unattended Critical" value={data.unattendedCriticalCustomers.length} icon={<CalendarClock className="h-4 w-4 text-slate-700" />} />
        <StatCard title="Escalated Deals" value={escalatedDeals.length} icon={<AlertTriangle className="h-4 w-4 text-rose-700" />} />
        <StatCard title="Blocked Deals" value={blockedDeals.length} icon={<AlertTriangle className="h-4 w-4 text-rose-500" />} />
        <StatCard title="Waiting Replies" value={touchpointSummary.waitingReplyThreads} icon={<AlertTriangle className="h-4 w-4 text-amber-700" />} />
        <StatCard title="Upcoming Meetings" value={touchpointSummary.upcomingMeetings} icon={<CalendarClock className="h-4 w-4 text-indigo-700" />} />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Deal Command Alerts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Escalated deals</p>
            {escalatedDeals.length === 0 ? <p className="text-sm text-muted-foreground">No escalated deal now.</p> : null}
            {escalatedDeals.map((item) => (
              <Link key={item.id} href={`/deals/${item.id}`} className="block rounded border px-2 py-1 text-sm text-slate-700 hover:bg-slate-50">
                {item.title} · {item.ownerName}
              </Link>
            ))}
            <p className="pt-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Blocked deals</p>
            {blockedDeals.length === 0 ? <p className="text-sm text-muted-foreground">No blocked deal now.</p> : null}
            {blockedDeals.map((item) => (
              <Link key={item.id} href={`/deals/${item.id}`} className="block rounded border px-2 py-1 text-sm text-slate-700 hover:bg-slate-50">
                {item.title} · {item.ownerName}
              </Link>
            ))}
            <Button asChild variant="outline" className="mt-2">
              <Link href="/deals">Open Deal Command View</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Closed-Loop Signal</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>Positive progress rate: {(((outcomeData?.summary.positiveProgressRate ?? 0) * 100)).toFixed(0)}%</p>
            <p>Suggestion adoption rate: {(((outcomeData?.summary.adoptionRate ?? 0) * 100)).toFixed(0)}%</p>
            <p>Adoption after positive rate: {(((outcomeData?.summary.adoptionPositiveRate ?? 0) * 100)).toFixed(0)}%</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Weekly Effective Patterns</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {(outcomeData?.effectivePatterns ?? []).slice(0, 5).map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
            {(outcomeData?.effectivePatterns ?? []).length === 0 ? <p className="text-sm text-muted-foreground">No pattern yet.</p> : null}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Team Execution Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm leading-6 text-slate-700">{data.aiInsight.team_execution_summary}</p>

            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Overdue patterns</p>
              {data.aiInsight.overdue_patterns.map((item) => (
                <p key={item} className="text-sm text-slate-700">
                  - {item}
                </p>
              ))}
            </div>

            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Manager actions</p>
              {data.aiInsight.managerial_actions.map((item) => (
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
            <CardTitle>Need Support</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.aiInsight.who_needs_support.length === 0 ? <p className="text-sm text-muted-foreground">No urgent support target.</p> : null}
            {data.aiInsight.who_needs_support.map((item) => (
              <div key={item.user_id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-900">{item.user_name}</p>
                  <Badge variant={item.priority === "high" ? "destructive" : "secondary"}>{item.priority}</Badge>
                </div>
                <p className="text-xs text-slate-700">{item.reason}</p>
                <Link className="mt-1 inline-block text-xs text-sky-700" href={`/manager?sales=${item.user_id}`}>
                  View customer details
                </Link>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Preparation Quality</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-sm text-slate-700">Current prep coverage is {(data.teamTotals.prepCoverageRate * 100).toFixed(0)}% across open team tasks.</p>
            <p className="text-sm text-slate-700">High-value customers without prep cards: {data.teamTotals.highValueWithoutPrepCount}</p>
            {data.aiInsight.which_actions_should_be_prioritized.map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Manager Intervention Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.unattendedCriticalCustomers.slice(0, 6).map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
            {data.unattendedCriticalCustomers.length === 0 ? <p className="text-sm text-muted-foreground">No unattended critical customer now.</p> : null}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Unattended Critical Customers</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.unattendedCriticalCustomers.length === 0 ? <p className="text-sm text-muted-foreground">All critical customers have active task ownership.</p> : null}
            {data.unattendedCriticalCustomers.map((item) => (
              <p key={item} className="rounded border px-2 py-1 text-sm text-slate-700">
                {item}
              </p>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Execution Stability</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.stableUsers.slice(0, 5).map((item) => (
              <p key={item.userId} className="text-sm text-slate-700">
                - {item.userName}: completion {(item.completionRate * 100).toFixed(0)}%, overdue {(item.overdueRate * 100).toFixed(0)}%
              </p>
            ))}
            {data.stableUsers.length === 0 ? <p className="text-sm text-muted-foreground">No stable rhythm profile yet.</p> : null}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Team Task Matrix</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="min-w-[900px] text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-[0.14em] text-slate-500">
                <th className="px-2 py-2">Sales</th>
                <th className="px-2 py-2">Todo</th>
                <th className="px-2 py-2">In Progress</th>
                <th className="px-2 py-2">Done</th>
                <th className="px-2 py-2">Overdue</th>
                <th className="px-2 py-2">Critical Open</th>
                <th className="px-2 py-2">Completion</th>
                <th className="px-2 py-2">Overdue Rate</th>
                <th className="px-2 py-2">Backlog Score</th>
              </tr>
            </thead>
            <tbody>
              {data.userRows
                .slice()
                .sort((a, b) => b.backlogScore - a.backlogScore)
                .map((item) => (
                  <tr key={item.userId} className="border-b">
                    <td className="px-2 py-2 font-medium text-slate-900">{item.userName}</td>
                    <td className="px-2 py-2">{item.todoCount}</td>
                    <td className="px-2 py-2">{item.inProgressCount}</td>
                    <td className="px-2 py-2">{item.doneCount}</td>
                    <td className="px-2 py-2">{item.overdueCount}</td>
                    <td className="px-2 py-2">{item.criticalOpenCount}</td>
                    <td className="px-2 py-2">{(item.completionRate * 100).toFixed(0)}%</td>
                    <td className="px-2 py-2">{(item.overdueRate * 100).toFixed(0)}%</td>
                    <td className="px-2 py-2">{item.backlogScore.toFixed(1)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
