"use client";

import Link from "next/link";
import { RefreshCw } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { canViewManagerWorkspace } from "@/lib/role-capability";
import { settingsClientService, type ConfigOperationsHubPayload } from "@/services/settings-client-service";
import { useEffect, useState } from "react";

interface ConfigOpsPageState {
  role: string;
  hub: ConfigOperationsHubPayload;
}

function statusVariant(status: "available" | "degraded" | "not_available"): "default" | "secondary" | "destructive" {
  if (status === "available") return "default";
  if (status === "degraded") return "secondary";
  return "destructive";
}

function availabilityVariant(status: "available" | "empty" | "not_available"): "default" | "secondary" | "destructive" {
  if (status === "available") return "default";
  if (status === "empty") return "secondary";
  return "destructive";
}

export default function ConfigOperationsHubPage(): JSX.Element {
  const { user } = useAuth();
  const canAccess = canViewManagerWorkspace(user);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<ConfigOpsPageState | null>(null);

  async function load(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getConfigOperationsHub();
      setState(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load config operations hub");
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
    return <div className="text-sm text-muted-foreground">Only owner/admin/manager roles can access Config Operations Hub.</div>;
  }

  if (loading || !state) {
    return <div className="text-sm text-muted-foreground">Loading Config Operations Hub...</div>;
  }

  const hub = state.hub;
  return (
    <div>
      <PageHeader
        title="Config Operations Hub v1"
        description="Read-focused operations cockpit for runtime explain, recent config changes, rollback readiness and governance signals."
        action={
          <Button variant="outline" onClick={() => void load()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {error ? <p className="mb-4 text-sm text-rose-600">{error}</p> : null}

      <div className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Recent Changes</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">{hub.healthSummary.recentChangeCount}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Ignored/Forbidden</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">{hub.healthSummary.recentIgnoredOrForbiddenCount}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Conflicts</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">{hub.healthSummary.recentConflictCount}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Recent Rollbacks</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">{hub.healthSummary.recentRollbackCount}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Fallback/Unavailable</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">{hub.healthSummary.fallbackOrUnavailableCount}</CardContent>
        </Card>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Runtime Overview</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p>role: <Badge variant="secondary">{state.role}</Badge></p>
            <p>resolved template: {hub.runtimeOverview.resolvedTemplateKey ?? "-"}</p>
            <p>fallback profile: {hub.runtimeOverview.fallbackProfileKey}</p>
            <p>customization key: {hub.runtimeOverview.appliedOrgCustomizationKey}</p>
            <p>resolved mode: <Badge variant={hub.runtimeOverview.resolvedMode === "persisted_preferred" ? "default" : "secondary"}>{hub.runtimeOverview.resolvedMode}</Badge></p>
            <p>ignored overrides: {hub.runtimeOverview.ignoredOverridesCount}</p>
            <p>runtime diagnostics: {hub.runtimeOverview.runtimeDiagnosticsCount}</p>
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Config Domain Entrypoints</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-3">
            {hub.domainCards.map((card) => (
              <div key={card.domainKey} className="rounded border border-slate-200 bg-slate-50 p-3 text-sm">
                <p className="mb-1 font-medium text-slate-900">{card.title}</p>
                <p className="mb-1">
                  status:{" "}
                  <Badge variant={statusVariant(card.status)}>{card.status}</Badge>
                </p>
                <p className="mb-1 text-slate-700">{card.summary}</p>
                <p className="mb-1 text-xs text-slate-600">latest changed: {card.latestChangedAt ?? "-"}</p>
                <p className="mb-2 text-xs text-slate-600">{card.rollbackSupportSummary}</p>
                <p className="mb-2 text-xs text-muted-foreground">{card.note}</p>
                <Link className="text-xs text-sky-700 underline" href={card.href}>
                  Open {card.title}
                </Link>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Timeline & Diff Viewer</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              Cross-domain timeline and structured before/after diff summary are available in the
              dedicated read-only viewer.
            </p>
            <Link className="text-sky-700 underline" href="/settings/config-timeline">
              Open Config Timeline & Diff Viewer
            </Link>
          </CardContent>
        </Card>
      </div>

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Config Changes</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              availability:{" "}
              <Badge variant={availabilityVariant(hub.recentChanges.availability)}>
                {hub.recentChanges.availability}
              </Badge>
            </p>
            <p className="text-muted-foreground">{hub.recentChanges.note}</p>
            {hub.recentChanges.items.length === 0 ? (
              <p className="text-muted-foreground">No recent persisted audit summary in current window.</p>
            ) : (
              hub.recentChanges.items.map((item) => (
                <div key={item.id} className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
                  <p className="font-medium text-slate-900">
                    {item.targetType} / {item.actionType} / {item.versionLabel}
                  </p>
                  <p className="text-slate-700">target key: {item.targetKey ?? "-"}</p>
                  <p className="text-slate-700">rollback: {item.rollbackAvailability}</p>
                  <p className="text-slate-700">runtime impact: {item.runtimeImpactSummary ?? "-"}</p>
                  <p className="text-slate-700">ignored/forbidden: {String(item.hasIgnoredOrForbiddenDiagnostics)}</p>
                  <p className="text-slate-700">conflict signal: {String(item.hasConflictDiagnostics)}</p>
                  <p className="text-slate-700">diagnostics: {item.diagnosticsPreview.join(" / ") || "-"}</p>
                  <p className="text-slate-700">created: {item.createdAt}</p>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Status Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {hub.statusSignals.length === 0 ? (
              <p className="text-muted-foreground">No fallback/not_available/degraded signal in current snapshot.</p>
            ) : (
              hub.statusSignals.map((signal, index) => (
                <div key={`${signal.domain}-${index}`} className="rounded border border-amber-200 bg-amber-50 p-2 text-xs">
                  <p className="font-medium text-amber-900">
                    {signal.domain} / {signal.status}
                  </p>
                  <p className="text-amber-800">{signal.detail}</p>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Known Limitations</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {hub.limitations.map((line, index) => (
              <p key={index} className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
                {line}
              </p>
            ))}
            <p className="text-xs text-muted-foreground">Generated at: {hub.generatedAt}</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
