"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { canViewManagerWorkspace } from "@/lib/role-capability";
import { settingsClientService, type RuntimeExplainDebugPanelPayload } from "@/services/settings-client-service";

interface RuntimeDebugPageState {
  role: string;
  panel: RuntimeExplainDebugPanelPayload;
}

function prettySourceLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function boolBadge(value: boolean): JSX.Element {
  return <Badge variant={value ? "default" : "secondary"}>{value ? "yes" : "no"}</Badge>;
}

function JsonDetails(props: { title: string; value: unknown }): JSX.Element {
  return (
    <details className="rounded border border-slate-200 p-2 text-xs">
      <summary className="cursor-pointer text-slate-700">{props.title}</summary>
      <pre className="mt-2 whitespace-pre-wrap break-all text-slate-700">{JSON.stringify(props.value, null, 2)}</pre>
    </details>
  );
}

export default function RuntimeDebugPage(): JSX.Element {
  const { user } = useAuth();
  const canAccess = canViewManagerWorkspace(user);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<RuntimeDebugPageState | null>(null);

  async function load(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getRuntimeDebugPanel();
      setState(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load runtime debug panel");
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
    return <div className="text-sm text-muted-foreground">Only owner/admin/manager roles can access runtime explain debug panel.</div>;
  }

  if (loading || !state) {
    return <div className="text-sm text-muted-foreground">Loading runtime explain debug panel...</div>;
  }

  const runtime = state.panel.runtime;
  const preferences = state.panel.effectivePreferenceSummary;
  const consumerSummary = state.panel.consumerExplainSummary;
  const governance = state.panel.overrideWriteGovernance;
  const recentAudits = state.panel.recentPersistedAudits;
  const recentRollbackAudits = state.panel.recentRollbackAudits;
  const concurrencyGuard = state.panel.concurrencyGuard;
  const orgConfigGovernance = state.panel.orgConfigGovernance;

  return (
    <div>
      <PageHeader
        title="Runtime Explain Debug"
        description="Read-only runtime source explain and governance diagnostics for current organization."
        action={
          <Button variant="outline" onClick={() => void load()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {error ? <p className="mb-4 text-sm text-rose-600">{error}</p> : null}

      <div className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Runtime Source Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              role: <Badge variant="secondary">{state.role}</Badge>
            </p>
            <p>resolved template: {runtime.resolvedTemplateKey ?? "none"}</p>
            <p>fallback profile: {runtime.fallbackProfileKey}</p>
            <p>org customization: {runtime.appliedOrgCustomizationKey}</p>
            <p>resolved mode: {runtime.resolvedMode}</p>
            <p>generated at: {state.panel.generatedAt}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Source Priority & Key Fields</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>priority: {runtime.sourcePriority.join(" -> ")}</p>
            <p>template source: {prettySourceLabel(runtime.keyFieldSources.resolvedTemplateKey)}</p>
            <p>customization source: {prettySourceLabel(runtime.keyFieldSources.orgCustomizationProfile)}</p>
            <p>threshold source: {prettySourceLabel(runtime.keyFieldSources.thresholdPreferences)}</p>
            <p>prompt source: {prettySourceLabel(runtime.keyFieldSources.promptPreference)}</p>
            <p>feature source: {prettySourceLabel(runtime.keyFieldSources.featurePreferences)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Persisted Usage</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="flex items-center justify-between">assignment {boolBadge(Boolean(runtime.persistedUsage.assignment))}</p>
            <p className="flex items-center justify-between">overrides {boolBadge(Boolean(runtime.persistedUsage.overrides))}</p>
            <p className="flex items-center justify-between">org settings {boolBadge(Boolean(runtime.persistedUsage.orgSettings))}</p>
            <p className="flex items-center justify-between">org ai settings {boolBadge(Boolean(runtime.persistedUsage.orgAiSettings))}</p>
            <p className="flex items-center justify-between">feature flags {boolBadge(Boolean(runtime.persistedUsage.orgFeatureFlags))}</p>
            <p className="flex items-center justify-between">automation rules {boolBadge(Boolean(runtime.persistedUsage.automationRules))}</p>
          </CardContent>
        </Card>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Effective Preference Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>manager focus metrics: {preferences.managerFocusMetrics.join(" / ") || "-"}</p>
            <p>report metric filters: {preferences.reportMetricFilters.join(" / ") || "-"}</p>
            <p>executive metric filters: {preferences.executiveMetricFilters.join(" / ") || "-"}</p>
            <p>recommended actions: {preferences.recommendedActionTitles.join(" / ") || "-"}</p>
            <p>onboarding checklist preference: {preferences.onboardingPreferredChecklistKeys.join(" / ") || "-"}</p>
            <p>onboarding hint preview: {preferences.onboardingHints.slice(0, 4).join(" / ") || "-"}</p>
            <p>default date range days: {preferences.defaultDateRangeDays ?? "-"}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Override Diagnostics</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>applied overrides: {runtime.appliedOverrides.length}</p>
            <p>ignored overrides: {runtime.ignoredOverrides.length}</p>
            <p>diagnostic items: {runtime.diagnostics.length}</p>
            {runtime.ignoredOverrides.length === 0 ? (
              <p className="text-muted-foreground">No ignored overrides.</p>
            ) : (
              runtime.ignoredOverrides.map((item, index) => (
                <div key={`${item.overrideType}-${index}`} className="rounded border border-amber-200 bg-amber-50 p-2 text-xs">
                  <p className="font-medium text-amber-900">
                    {item.overrideType} ({item.layer})
                  </p>
                  <p className="text-amber-800">reason: {item.reason}</p>
                  <p className="text-amber-800">diagnostics: {item.diagnostics.join(" / ") || "-"}</p>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Onboarding Consumer</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="flex items-center justify-between">
              prompt augmentation {boolBadge(consumerSummary.onboarding.promptAugmentationEnabled)}
            </p>
            <p>prompt source: {prettySourceLabel(consumerSummary.onboarding.explainSource)}</p>
            <p>preferred checklist keys: {consumerSummary.onboarding.preferredChecklistKeys.join(" / ") || "-"}</p>
            <p>hint preview: {consumerSummary.onboarding.hintPreview.join(" / ") || "-"}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Automation Seed Consumer</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>resolution source: {prettySourceLabel(consumerSummary.automationSeed.resolutionSource)}</p>
            <p>resolved mode: {consumerSummary.automationSeed.resolvedMode}</p>
            <p>ignored override count: {consumerSummary.automationSeed.ignoredOverrideCount}</p>
            <p>total seed count: {consumerSummary.automationSeed.totalSeedCount}</p>
            <p>disabled seed count: {consumerSummary.automationSeed.disabledSeedCount}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Executive/Report Consumer</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="flex items-center justify-between">
              fallback to base {boolBadge(consumerSummary.executiveReport.fallbackToBase)}
            </p>
            <p>manager focus priority: {consumerSummary.executiveReport.managerFocusMetricPriority.join(" / ") || "-"}</p>
            <p>report focus priority: {consumerSummary.executiveReport.reportMetricPriority.join(" / ") || "-"}</p>
            <p>recommended action priority: {consumerSummary.executiveReport.recommendedActionPriority.join(" / ") || "-"}</p>
            <p>default date range days: {consumerSummary.executiveReport.defaultDateRangeDays ?? "-"}</p>
          </CardContent>
        </Card>
      </div>

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Latest Override Governance Snapshot</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              availability:{" "}
              <Badge variant={governance.availability === "available_from_template_apply_snapshot" ? "default" : "secondary"}>
                {governance.availability}
              </Badge>
            </p>
            <p>latest run id: {governance.latestRunId ?? "-"}</p>
            <p>latest run at: {governance.latestRunAt ?? "-"}</p>
            <p>diagnostics count: {governance.diagnosticsCount}</p>
            <p>audit draft count: {governance.auditDraftCount}</p>
            <p className="text-muted-foreground">{governance.note}</p>
          </CardContent>
        </Card>
      </div>

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Persisted Config Audit Logs</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              availability: <Badge variant={recentAudits.availability === "available" ? "default" : "secondary"}>{recentAudits.availability}</Badge>
            </p>
            <p className="text-muted-foreground">{recentAudits.note}</p>
            {recentAudits.items.length === 0 ? (
              <p className="text-muted-foreground">No persisted audit records to display.</p>
            ) : (
              recentAudits.items.map((item) => (
                <div key={item.id} className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
                  <p className="font-medium text-slate-900">
                    {item.targetType} / {item.actionType} / {item.versionLabel}
                  </p>
                  <p className="text-slate-700">target key: {item.targetKey ?? "-"}</p>
                  <p className="text-slate-700">runtime impact: {item.runtimeImpactSummary ?? "-"}</p>
                  <p className="text-slate-700">ignored by runtime: {String(item.ignoredByRuntime ?? false)}</p>
                  <p className="text-slate-700">forbidden for runtime: {String(item.forbiddenForRuntime ?? false)}</p>
                  <p className="text-slate-700">created at: {item.createdAt}</p>
                  <p className="text-slate-700">actor: {item.actorUserId}</p>
                  <p className="text-slate-700">diagnostics: {item.diagnosticsPreview.join(" / ") || "-"}</p>
                  <p className="text-slate-700">
                    has drift/conflict signal: {String(item.hasConcurrencyConflictDiagnostic)}
                  </p>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Override Rollback Summaries</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              availability:{" "}
              <Badge variant={recentRollbackAudits.availability === "available" ? "default" : "secondary"}>
                {recentRollbackAudits.availability}
              </Badge>
            </p>
            <p className="text-muted-foreground">{recentRollbackAudits.note}</p>
            {recentRollbackAudits.items.length === 0 ? (
              <p className="text-muted-foreground">No rollback records to display.</p>
            ) : (
              recentRollbackAudits.items.map((item) => (
                <div key={item.id} className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
                  <p className="font-medium text-slate-900">
                    {item.targetType} / rollback / {item.versionLabel}
                  </p>
                  <p className="text-slate-700">target key: {item.targetKey ?? "-"}</p>
                  <p className="text-slate-700">restored from audit: {item.restoredFromAuditId ?? "-"}</p>
                  <p className="text-slate-700">
                    restored from version: {item.restoredFromVersionLabel ?? "-"} / {item.restoredFromVersionNumber ?? "-"}
                  </p>
                  <p className="text-slate-700">created at: {item.createdAt}</p>
                  <p className="text-slate-700">actor: {item.actorUserId}</p>
                  <p className="text-slate-700">diagnostics: {item.diagnosticsPreview.join(" / ") || "-"}</p>
                  <p className="text-slate-700">
                    has drift/conflict signal: {String(item.hasConcurrencyConflictDiagnostic)}
                  </p>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Concurrency Guard Diagnostics</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>latest conflict at: {concurrencyGuard.latestConflictAt ?? "-"}</p>
            <p>latest conflict reason: {concurrencyGuard.latestConflictReason ?? "-"}</p>
            <p className="text-muted-foreground">{concurrencyGuard.note}</p>
          </CardContent>
        </Card>
      </div>

      <div className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Org Config Governance Expansion Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="text-muted-foreground">{orgConfigGovernance.note}</p>
            {orgConfigGovernance.items.map((item) => (
              <div key={item.targetType} className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
                <p className="font-medium text-slate-900">{item.targetType}</p>
                <p className="text-slate-700">availability: {item.availability}</p>
                <p className="text-slate-700">has persisted audit: {String(item.hasPersistedAudit)}</p>
                <p className="text-slate-700">latest action: {item.latestActionType ?? "-"}</p>
                <p className="text-slate-700">latest version: {item.latestVersionLabel ?? "-"} / {item.latestVersionNumber ?? "-"}</p>
                <p className="text-slate-700">latest changed at: {item.latestChangedAt ?? "-"}</p>
                <p className="text-slate-700">runtime impact summary: {item.runtimeImpactSummary ?? "-"}</p>
                <p className="text-slate-700">
                  ignored/forbidden diagnostics: {item.ignoredOrForbiddenDiagnosticsCount}
                </p>
                <p className="text-slate-700">conflict diagnostics: {item.conflictDiagnosticsCount}</p>
                <p className="text-slate-700">diagnostics: {item.diagnosticsPreview.join(" / ") || "-"}</p>
                <p className="text-muted-foreground">{item.note}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-3">
        <JsonDetails title="Runtime Explain (raw)" value={runtime} />
        <JsonDetails title="Onboarding Prompt Augmentation Preview (raw)" value={consumerSummary.onboarding.promptAugmentationPreview} />
        <JsonDetails title="Automation Seed Sample (raw)" value={consumerSummary.automationSeed.sample} />
        <JsonDetails title="Governance Summary (raw)" value={governance.summary} />
        <JsonDetails title="Recent Persisted Audit Logs (raw)" value={recentAudits} />
        <JsonDetails title="Recent Rollback Summaries (raw)" value={recentRollbackAudits} />
        <JsonDetails title="Concurrency Guard Summary (raw)" value={concurrencyGuard} />
        <JsonDetails title="Org Config Governance (raw)" value={orgConfigGovernance} />
      </div>
    </div>
  );
}
