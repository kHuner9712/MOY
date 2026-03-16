"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useAutomationCenter } from "@/hooks/use-automation-center";
import { formatDateTime } from "@/lib/format";
import { executiveClientService } from "@/services/executive-client-service";
import { settingsClientService } from "@/services/settings-client-service";
import { Activity, BellRing, PlayCircle, ShieldCheck } from "lucide-react";

export default function AutomationSettingsPage(): JSX.Element {
  const { user } = useAuth();
  const { data, loading, error, reload } = useAutomationCenter(user?.role === "manager");
  const [canManageRules, setCanManageRules] = useState(false);
  const [membershipRole, setMembershipRole] = useState<string>("manager");
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busyRuleId, setBusyRuleId] = useState<string | null>(null);
  const [runningRules, setRunningRules] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const loadRole = async () => {
      if (user?.role !== "manager") return;
      try {
        const team = await settingsClientService.getTeamSettings();
        if (cancelled) return;
        setMembershipRole(team.role);
        setCanManageRules(Boolean(team.canManageTeam));
      } catch {
        if (!cancelled) {
          setMembershipRole("manager");
          setCanManageRules(false);
        }
      }
    };
    void loadRole();
    return () => {
      cancelled = true;
    };
  }, [user?.role]);

  const enabledRuleCount = useMemo(() => (data?.rules ?? []).filter((item) => item.isEnabled).length, [data?.rules]);

  if (user?.role !== "manager") {
    return <div className="text-sm text-muted-foreground">Only manager can access automation center.</div>;
  }

  if (loading) return <div className="text-sm text-muted-foreground">Loading automation rule center...</div>;
  if (error || !data) return <div className="text-sm text-rose-600">Failed to load automation center: {error}</div>;

  const toggleRule = async (ruleId: string, isEnabled: boolean): Promise<void> => {
    setActionMessage(null);
    setBusyRuleId(ruleId);
    try {
      await executiveClientService.toggleAutomationRule(ruleId, isEnabled);
      setActionMessage(isEnabled ? "Rule enabled." : "Rule disabled.");
      await reload();
    } catch (cause) {
      setActionMessage(cause instanceof Error ? cause.message : "Failed to update rule");
    } finally {
      setBusyRuleId(null);
    }
  };

  const runAllRules = async (): Promise<void> => {
    setActionMessage(null);
    setRunningRules(true);
    try {
      const result = await executiveClientService.runAutomationRules();
      setActionMessage(
        `Run finished: ${result.totalRules} rules, ${result.totalMatches} matches, ${result.totalActions} actions${
          result.failedRules.length > 0 ? `, failed=${result.failedRules.length}` : ""
        }.`
      );
      await reload();
    } catch (cause) {
      setActionMessage(cause instanceof Error ? cause.message : "Failed to run automation rules");
    } finally {
      setRunningRules(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Automation Rule Center"
        description="Configure and execute explainable operating rules. Rules generate business events and suggested operational actions."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void reload()}>
              Refresh
            </Button>
            <Button onClick={() => void runAllRules()} disabled={runningRules || !canManageRules}>
              <PlayCircle className="mr-1 h-4 w-4" />
              {runningRules ? "Running..." : "Run Rules"}
            </Button>
            <Button asChild variant="outline">
              <Link href="/executive">Open Executive Cockpit</Link>
            </Button>
          </div>
        }
      />

      {actionMessage ? <p className="mb-3 text-sm text-muted-foreground">{actionMessage}</p> : null}

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Total Rules" value={data.rules.length} icon={<Activity className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Enabled Rules" value={enabledRuleCount} icon={<ShieldCheck className="h-4 w-4 text-emerald-600" />} />
        <StatCard title="Open Business Events" value={data.openEvents} icon={<BellRing className="h-4 w-4 text-rose-600" />} />
        <StatCard title="Recent Rule Runs" value={data.recentRuns.length} icon={<PlayCircle className="h-4 w-4 text-indigo-600" />} />
      </section>

      <section className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle>Permission Scope</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              Membership role: <Badge variant="secondary">{membershipRole}</Badge>
            </p>
            <p>{canManageRules ? "You can enable/disable rules and maintain automation strategy." : "Read-only mode: only owner/admin can modify rules."}</p>
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Rule Catalog</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.rules.length === 0 ? <p className="text-sm text-muted-foreground">No rules found.</p> : null}
            {data.rules.map((rule) => (
              <div key={rule.id} className="rounded-lg border p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">{rule.ruleName}</p>
                    <p className="text-xs text-muted-foreground">
                      key={rule.ruleKey} | scope={rule.ruleScope} | trigger={rule.triggerType}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge variant={rule.severity === "critical" ? "destructive" : rule.severity === "warning" ? "default" : "secondary"}>{rule.severity}</Badge>
                    <Switch
                      checked={rule.isEnabled}
                      disabled={!canManageRules || busyRuleId === rule.id}
                      onCheckedChange={(checked) => void toggleRule(rule.id, checked)}
                    />
                  </div>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  <div className="rounded-md border bg-slate-50 p-2">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Conditions</p>
                    <pre className="overflow-auto text-[11px] text-slate-700">{JSON.stringify(rule.conditionsJson, null, 2)}</pre>
                  </div>
                  <div className="rounded-md border bg-slate-50 p-2">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">Actions</p>
                    <pre className="overflow-auto text-[11px] text-slate-700">{JSON.stringify(rule.actionJson, null, 2)}</pre>
                  </div>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent Run Audit</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.recentRuns.length === 0 ? <p className="text-sm text-muted-foreground">No rule run audit yet.</p> : null}
            {data.recentRuns.slice(0, 12).map((run) => (
              <div key={run.id} className="rounded-md border p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-xs text-muted-foreground">{formatDateTime(run.createdAt)}</p>
                  <Badge variant={run.runStatus === "completed" ? "default" : run.runStatus === "failed" ? "destructive" : "secondary"}>
                    {run.runStatus}
                  </Badge>
                </div>
                <p className="text-xs text-slate-700">
                  matched={run.matchedCount} · actions={run.createdActionCount}
                </p>
                <p className="mt-1 text-xs text-slate-700">{run.summary ?? "-"}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}


