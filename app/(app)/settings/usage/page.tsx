"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { canViewOrgUsage } from "@/lib/role-capability";
import { settingsClientService } from "@/services/settings-client-service";
import type { EntitlementStatus, OrgUsageCounter, UserUsageCounter } from "@/types/productization";

interface UsageState {
  daily: OrgUsageCounter | null;
  monthly: OrgUsageCounter | null;
  topUsersMonthly: UserUsageCounter[];
  entitlement: EntitlementStatus;
  summary: {
    summary: {
      usageSummary: string;
      hotFeatures: string[];
      underusedFeatures: string[];
      quotaRisks: string[];
      recommendedAdjustments: string[];
    };
    usedFallback: boolean;
    fallbackReason: string | null;
  };
}

export default function UsageSettingsPage(): JSX.Element {
  const { user } = useAuth();
  const canAccess = canViewOrgUsage(user);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<UsageState | null>(null);

  async function load(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getUsageSettings();
      setState({
        daily: payload.usage.daily,
        monthly: payload.usage.monthly,
        topUsersMonthly: payload.usage.topUsersMonthly,
        entitlement: payload.entitlement,
        summary: payload.summary
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load usage settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!canAccess) {
      setLoading(false);
      setState(null);
      return;
    }
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canAccess]);

  if (!canAccess) {
    return <div className="text-sm text-muted-foreground">Only owner/admin/manager roles can view organization usage and quota.</div>;
  }

  if (loading || !state) {
    return <div className="text-sm text-muted-foreground">Loading usage dashboard...</div>;
  }

  const monthly = state.monthly;
  const entitlement = state.entitlement;

  return (
    <div>
      <PageHeader
        title="Usage & Quota"
        description="Track AI and module usage at organization and user levels, with quota health visibility."
        action={
          <Button variant="outline" onClick={() => void load()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {error ? <p className="mb-4 text-sm text-red-600">{error}</p> : null}

      <div className="mb-4 grid gap-4 xl:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Plan Tier</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge>{entitlement.planTier}</Badge>
            <p className="mt-2 text-xs text-muted-foreground">status={entitlement.status}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">AI Runs / Month</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-lg font-semibold text-slate-900">
              {entitlement.aiRunUsedMonthly}/{entitlement.aiRunLimitMonthly}
            </p>
            <p className="text-xs text-muted-foreground">remaining={entitlement.remainingAiRunsMonthly}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Seat Usage</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-lg font-semibold text-slate-900">
              {entitlement.seatUsed}/{entitlement.seatLimit}
            </p>
            <p className="text-xs text-muted-foreground">active + invited seats</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Quota Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Badge variant={entitlement.quotaExceeded ? "destructive" : entitlement.quotaNearLimit ? "default" : "secondary"}>
              {entitlement.quotaExceeded ? "Exceeded" : entitlement.quotaNearLimit ? "Near Limit" : "Healthy"}
            </Badge>
            <p className="text-xs text-muted-foreground">doc={entitlement.documentUsedMonthly}/{entitlement.documentLimitMonthly}</p>
            <p className="text-xs text-muted-foreground">touchpoints={entitlement.touchpointUsedMonthly}/{entitlement.touchpointLimitMonthly}</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Monthly Module Counters</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p>AI runs: {monthly?.aiRunsCount ?? 0}</p>
            <p>Prep cards: {monthly?.prepCardsCount ?? 0}</p>
            <p>Drafts: {monthly?.draftsCount ?? 0}</p>
            <p>Reports: {monthly?.reportsCount ?? 0}</p>
            <p>Touchpoint events: {monthly?.touchpointEventsCount ?? 0}</p>
            <p>Documents processed: {monthly?.documentProcessedCount ?? 0}</p>
            <p>Work plan generations: {monthly?.workPlanGenerationsCount ?? 0}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Usage Health Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="font-medium text-slate-900">{state.summary.summary.usageSummary}</p>
            <p>Hot features: {state.summary.summary.hotFeatures.join(" / ") || "-"}</p>
            <p>Underused features: {state.summary.summary.underusedFeatures.join(" / ") || "-"}</p>
            <p>Quota risks: {state.summary.summary.quotaRisks.join(" / ") || "-"}</p>
            <p>Recommended: {state.summary.summary.recommendedAdjustments.join(" / ") || "-"}</p>
            {state.summary.usedFallback ? (
              <p className="text-xs text-amber-700">Fallback summary used: {state.summary.fallbackReason ?? "unknown"}</p>
            ) : null}
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Top Active Users (Monthly)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {state.topUsersMonthly.length === 0 ? <p className="text-sm text-muted-foreground">No user usage counters yet.</p> : null}
            {state.topUsersMonthly.map((item) => (
              <div key={item.id} className="rounded-md border border-slate-200 p-2 text-sm">
                <p className="font-medium text-slate-900">{item.userName ?? item.userId}</p>
                <p className="text-xs text-muted-foreground">
                  ai={item.aiRunsCount}, prep={item.prepCardsCount}, drafts={item.draftsCount}, reports={item.reportsCount}, touchpoints={item.touchpointEventsCount}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
