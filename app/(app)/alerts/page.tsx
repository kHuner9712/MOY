"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { AlertList } from "@/components/alerts/alert-list";
import { useAuth } from "@/components/auth/auth-provider";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { alertService } from "@/services/alert-service";
import { AlertTriangle, ShieldAlert, Siren, TimerReset } from "lucide-react";

const alertRules = [
  "No followup for N days",
  "Positive response but quote not progressing",
  "Quoted but no stage movement",
  "Multiple touchpoints but no clear decision maker",
  "High probability customer remains stalled"
];

export default function AlertsPage(): JSX.Element {
  const { user } = useAuth();
  const { alerts, updateAlertStatus, runAlertScan, loading, error } = useAppData();
  const [scanLoading, setScanLoading] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [coverageMap, setCoverageMap] = useState<Record<string, boolean>>({});
  const [convertLoadingId, setConvertLoadingId] = useState<string | null>(null);

  const scopedAlerts = useMemo(() => {
    if (!user) return [];
    return alerts;
  }, [alerts, user]);

  const openCount = scopedAlerts.filter((item) => item.status === "open").length;
  const criticalCount = scopedAlerts.filter((item) => item.level === "critical" && item.status !== "resolved").length;
  const resolvedCount = scopedAlerts.filter((item) => item.status === "resolved").length;

  const loadCoverage = useCallback(async () => {
    try {
      const map = await alertService.getAlertCoverage(scopedAlerts.map((item) => item.id));
      setCoverageMap(map);
    } catch {
      // non-blocking
    }
  }, [scopedAlerts]);

  useEffect(() => {
    void loadCoverage();
  }, [loadCoverage]);

  const runScan = async (): Promise<void> => {
    setScanLoading(true);
    setScanMessage(null);
    try {
      const result = await runAlertScan();
      setScanMessage(`Scan finished: +${result.createdAlertCount} new, ${result.dedupedAlertCount} deduped, ${result.resolvedAlertCount} resolved.`);
      await loadCoverage();
    } catch (cause) {
      setScanMessage(cause instanceof Error ? cause.message : "Leak scan failed");
    } finally {
      setScanLoading(false);
    }
  };

  const convertToTask = async (alertId: string): Promise<void> => {
    setConvertLoadingId(alertId);
    setScanMessage(null);
    try {
      const result = await alertService.convertToWorkItem(alertId);
      setScanMessage(result.created ? "Alert has been converted into a work item." : "Alert already has an active linked work item.");
      setCoverageMap((prev) => ({
        ...prev,
        [alertId]: true
      }));
    } catch (cause) {
      setScanMessage(cause instanceof Error ? cause.message : "Failed to convert alert");
    } finally {
      setConvertLoadingId(null);
    }
  };

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading alerts...</div>;
  }

  if (error) {
    return <div className="text-sm text-rose-600">Failed to load alerts: {error}</div>;
  }

  return (
    <div>
      <PageHeader
        title="Leak Alerts"
        description="Rule-first and AI-enhanced risk monitoring with direct task handoff."
        action={
          user?.role === "manager" ? (
            <Button onClick={() => void runScan()} disabled={scanLoading}>
              {scanLoading ? "Scanning..." : "Run Leak Scan"}
            </Button>
          ) : undefined
        }
      />

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Total Alerts" value={scopedAlerts.length} icon={<Siren className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Open" value={openCount} icon={<TimerReset className="h-4 w-4 text-amber-600" />} />
        <StatCard title="Critical" value={criticalCount} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
        <StatCard title="Resolved" value={resolvedCount} icon={<ShieldAlert className="h-4 w-4 text-emerald-600" />} />
      </section>

      {scanMessage ? <p className="mb-3 text-sm text-muted-foreground">{scanMessage}</p> : null}
      {convertLoadingId ? <p className="mb-3 text-xs text-muted-foreground">Converting alert {convertLoadingId.slice(0, 8)}...</p> : null}

      <section className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle>Active Rules</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 md:grid-cols-2">
            {alertRules.map((rule) => (
              <div key={rule} className="rounded-lg border bg-slate-50/80 px-3 py-2 text-sm text-slate-700">
                {rule}
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <AlertList items={scopedAlerts} onChangeStatus={updateAlertStatus} onConvertToWorkItem={convertToTask} coverageMap={coverageMap} />
    </div>
  );
}
