"use client";

import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { canViewManagerWorkspace } from "@/lib/role-capability";
import {
  settingsClientService,
  type ConfigTimelineViewerPayload
} from "@/services/settings-client-service";

interface ConfigTimelinePageState {
  role: string;
  timeline: ConfigTimelineViewerPayload;
}

function availabilityVariant(
  value: "available" | "empty" | "not_available" | "summary_only"
): "default" | "secondary" | "destructive" {
  if (value === "available") return "default";
  if (value === "empty" || value === "summary_only") return "secondary";
  return "destructive";
}

function JsonDetails(props: { title: string; value: unknown }): JSX.Element {
  return (
    <details className="rounded border border-slate-200 p-2 text-xs">
      <summary className="cursor-pointer text-slate-700">{props.title}</summary>
      <pre className="mt-2 whitespace-pre-wrap break-all text-slate-700">
        {JSON.stringify(props.value, null, 2)}
      </pre>
    </details>
  );
}

export default function ConfigTimelinePage(): JSX.Element {
  const { user } = useAuth();
  const canAccess = canViewManagerWorkspace(user);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<ConfigTimelinePageState | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  async function load(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getConfigTimelineViewer();
      setState(payload);
      setSelectedId((current) => {
        const nextItems = payload.timeline.timeline.items;
        if (!current) return nextItems[0]?.id ?? null;
        return nextItems.some((item) => item.id === current) ? current : nextItems[0]?.id ?? null;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load config timeline viewer");
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

  const selectedItem = useMemo(() => {
    if (!state || !selectedId) return state?.timeline.timeline.items[0] ?? null;
    return (
      state.timeline.timeline.items.find((item) => item.id === selectedId) ??
      state.timeline.timeline.items[0] ??
      null
    );
  }, [selectedId, state]);

  if (!canAccess) {
    return (
      <div className="text-sm text-muted-foreground">
        Only owner/admin/manager roles can access Config Timeline & Diff Viewer.
      </div>
    );
  }

  if (loading || !state) {
    return <div className="text-sm text-muted-foreground">Loading Config Timeline & Diff Viewer...</div>;
  }

  const viewer = state.timeline;
  return (
    <div>
      <PageHeader
        title="Config Timeline & Diff Viewer v1"
        description="Read-only cross-domain config timeline with before/after summary and structured diff."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void load()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
            <Button variant="outline" asChild>
              <Link href="/settings/config-ops">Back To Config Ops Hub</Link>
            </Button>
          </div>
        }
      />

      {error ? <p className="mb-4 text-sm text-rose-600">{error}</p> : null}

      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-base">Timeline Status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>
            role: <Badge variant="secondary">{state.role}</Badge>
          </p>
          <p>
            availability:{" "}
            <Badge variant={availabilityVariant(viewer.timeline.availability)}>
              {viewer.timeline.availability}
            </Badge>
          </p>
          <p>items: {viewer.timeline.items.length}</p>
          <p className="text-muted-foreground">{viewer.timeline.note}</p>
          <p className="text-xs text-muted-foreground">generated at: {viewer.generatedAt}</p>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Timeline</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {viewer.timeline.items.length === 0 ? (
              <p className="text-muted-foreground">
                No timeline records available in current window.
              </p>
            ) : (
              viewer.timeline.items.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={`w-full rounded border p-2 text-left text-xs transition ${
                    selectedItem?.id === item.id
                      ? "border-sky-400 bg-sky-50"
                      : "border-slate-200 bg-slate-50 hover:border-sky-200"
                  }`}
                  onClick={() => setSelectedId(item.id)}
                >
                  <p className="font-medium text-slate-900">
                    {item.targetType} / {item.actionType} / {item.versionLabel}
                  </p>
                  <p className="text-slate-700">target key: {item.targetKey ?? "-"}</p>
                  <p className="text-slate-700">created at: {item.createdAt}</p>
                  <p className="text-slate-700">
                    detail availability:{" "}
                    <Badge variant={availabilityVariant(item.availability)}>{item.availability}</Badge>
                  </p>
                  <p className="text-slate-700">runtime impact: {item.runtimeImpactSummary ?? "-"}</p>
                  <p className="text-slate-700">diagnostics: {item.diagnosticsPreview.join(" / ") || "-"}</p>
                  {item.rollbackSource ? (
                    <p className="text-slate-700">
                      rollback source: {item.rollbackSource.sourceVersionLabel ?? "-"}
                    </p>
                  ) : null}
                </button>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Change Detail & Diff</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {!selectedItem ? (
              <p className="text-muted-foreground">Select a timeline record to view detail.</p>
            ) : (
              <>
                <p>
                  selected:{" "}
                  <Badge variant="secondary">
                    {selectedItem.targetType} / {selectedItem.versionLabel}
                  </Badge>
                </p>
                <p>action: {selectedItem.actionType}</p>
                <p>actor: {selectedItem.actorUserId}</p>
                <p>created at: {selectedItem.createdAt}</p>
                <p>
                  diff status:{" "}
                  <Badge variant={availabilityVariant(selectedItem.detail.diffSummary.status)}>
                    {selectedItem.detail.diffSummary.status}
                  </Badge>
                </p>
                <p>compare source: {selectedItem.detail.diffSummary.compareSource}</p>
                <p>changed keys: {selectedItem.detail.diffSummary.changedKeys.join(" / ") || "-"}</p>
                <p>added keys: {selectedItem.detail.diffSummary.addedKeys.join(" / ") || "-"}</p>
                <p>removed keys: {selectedItem.detail.diffSummary.removedKeys.join(" / ") || "-"}</p>
                <p>total changed: {selectedItem.detail.diffSummary.totalChanged}</p>
                <p>
                  redacted fields: {selectedItem.detail.diffSummary.redactedFields.join(" / ") || "-"}
                </p>
                <p className="text-muted-foreground">{selectedItem.detail.diffSummary.note}</p>
                <p className="text-muted-foreground">{selectedItem.detail.note}</p>

                <JsonDetails title="Before Summary (redacted)" value={selectedItem.detail.beforeSummary} />
                <JsonDetails title="After Summary (redacted)" value={selectedItem.detail.afterSummary} />
                <JsonDetails title="Snapshot Summary (redacted)" value={selectedItem.detail.snapshotSummary} />
                <JsonDetails title="Diagnostics Summary (redacted)" value={selectedItem.detail.diagnosticsSummary} />
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Status Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {viewer.statusSignals.length === 0 ? (
              <p className="text-muted-foreground">No fallback/not_available/degraded signal in current snapshot.</p>
            ) : (
              viewer.statusSignals.map((signal, index) => (
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
            {viewer.limitations.map((line, index) => (
              <p key={index} className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
                {line}
              </p>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
