"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  buildRollbackExecutePayloadFromPreview,
  resolveTemplateOverrideEditorAccess
} from "@/lib/template-override-editor-ui";
import {
  settingsClientService,
  type TemplateOverrideConflictPayload,
  type TemplateOverrideEditorStatePayload,
  type TemplateOverrideRollbackPreviewPayload,
  type TemplateOverrideWritePreviewPayload,
  type TemplateOverrideWriteSuccessPayload
} from "@/services/settings-client-service";
import type {
  IndustryTemplate,
  IndustryTemplateContext,
  OrgTemplateOverride,
  TemplateApplicationSummary,
  TemplateApplyMode,
  TemplateApplyStrategy,
  TemplateFitRecommendation
} from "@/types/productization";

interface TemplateCenterState {
  role: string;
  templates: IndustryTemplate[];
  currentTemplate: IndustryTemplate | null;
  currentAssignment: IndustryTemplateContext["assignment"];
}

const APPLY_MODES: TemplateApplyMode[] = ["onboarding_default", "manual_apply", "demo_seed", "trial_bootstrap"];
const APPLY_STRATEGIES: TemplateApplyStrategy[] = ["additive_only", "merge_prefer_existing", "template_override_existing"];
const OVERRIDE_TYPES: OrgTemplateOverride["overrideType"][] = [
  "alert_rules",
  "checkpoints",
  "playbook_seed",
  "prep_preferences",
  "brief_preferences",
  "demo_seed_profile",
  "customer_stages",
  "opportunity_stages"
];

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function toOptionalUuid(value: string): string | undefined {
  return UUID_PATTERN.test(value) ? value : undefined;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function readCount(summary: Record<string, unknown> | null, key: string): number {
  if (!summary) return 0;
  const items = summary[key];
  return Array.isArray(items) ? items.length : 0;
}

function parseJsonObject(text: string): {
  value: Record<string, unknown> | null;
  error: string | null;
} {
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {
        value: null,
        error: "Override payload must be a JSON object."
      };
    }
    return {
      value: parsed as Record<string, unknown>,
      error: null
    };
  } catch (error) {
    return {
      value: null,
      error: error instanceof Error ? error.message : "Invalid JSON payload."
    };
  }
}

function pretty(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function JsonDetails(props: { title: string; value: unknown }): JSX.Element {
  return (
    <details className="rounded border border-slate-200 p-2 text-xs">
      <summary className="cursor-pointer text-slate-700">{props.title}</summary>
      <pre className="mt-2 whitespace-pre-wrap break-all text-slate-700">{JSON.stringify(props.value, null, 2)}</pre>
    </details>
  );
}

export default function TemplateSettingsPage(): JSX.Element {
  const { user } = useAuth();

  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [centerState, setCenterState] = useState<TemplateCenterState | null>(null);
  const [selectedTemplateIdOrKey, setSelectedTemplateIdOrKey] = useState<string>("");
  const [templateDetail, setTemplateDetail] = useState<
    Awaited<ReturnType<typeof settingsClientService.getTemplateDetail>> | null
  >(null);
  const [recommendation, setRecommendation] = useState<{
    recommendation: TemplateFitRecommendation;
    usedFallback: boolean;
    fallbackReason: string | null;
  } | null>(null);
  const [preview, setPreview] = useState<{
    diff: {
      changedKeys: string[];
      unchangedKeys: string[];
      notes: string[];
    };
    summary: TemplateApplicationSummary;
    summaryUsedFallback: boolean;
    summaryFallbackReason: string | null;
  } | null>(null);

  const [applyMode, setApplyMode] = useState<TemplateApplyMode>("manual_apply");
  const [applyStrategy, setApplyStrategy] = useState<TemplateApplyStrategy>("merge_prefer_existing");
  const [generateDemoSeed, setGenerateDemoSeed] = useState(false);

  const [selectedOverrideType, setSelectedOverrideType] =
    useState<OrgTemplateOverride["overrideType"]>("alert_rules");
  const [overrideState, setOverrideState] = useState<TemplateOverrideEditorStatePayload | null>(null);
  const [overrideLoading, setOverrideLoading] = useState(false);
  const [overridePayloadText, setOverridePayloadText] = useState("{}");
  const [overridePreview, setOverridePreview] = useState<TemplateOverrideWritePreviewPayload | null>(null);
  const [overrideWriteResult, setOverrideWriteResult] = useState<TemplateOverrideWriteSuccessPayload | null>(null);
  const [overrideConflict, setOverrideConflict] = useState<TemplateOverrideConflictPayload | null>(null);
  const [overrideMessage, setOverrideMessage] = useState<string | null>(null);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [overrideActionLoading, setOverrideActionLoading] = useState(false);

  const [rollbackTargetAuditId, setRollbackTargetAuditId] = useState<string>("");
  const [rollbackPreview, setRollbackPreview] = useState<TemplateOverrideRollbackPreviewPayload | null>(null);
  const [rollbackConflict, setRollbackConflict] = useState<TemplateOverrideConflictPayload | null>(null);
  const [rollbackMessage, setRollbackMessage] = useState<string | null>(null);
  const [rollbackError, setRollbackError] = useState<string | null>(null);
  const [rollbackActionLoading, setRollbackActionLoading] = useState(false);

  const canWrite = useMemo(
    () => centerState?.role === "owner" || centerState?.role === "admin",
    [centerState]
  );
  const editorAccess = useMemo(
    () =>
      resolveTemplateOverrideEditorAccess({
        user,
        templateCenterRole: centerState?.role
      }),
    [centerState?.role, user]
  );
  const selectedTemplateUuid = useMemo(
    () => toOptionalUuid(selectedTemplateIdOrKey),
    [selectedTemplateIdOrKey]
  );

  async function loadTemplateCenter(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getTemplateCenter();
      setCenterState(payload);

      const selected = selectedTemplateIdOrKey || payload.currentTemplate?.id || payload.templates[0]?.id || "";
      setSelectedTemplateIdOrKey(selected);
      if (selected) {
        const detail = await settingsClientService.getTemplateDetail(selected);
        setTemplateDetail(detail);
      } else {
        setTemplateDetail(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load template center");
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(templateIdOrKey: string): Promise<void> {
    setSelectedTemplateIdOrKey(templateIdOrKey);
    setPreview(null);
    setOverridePreview(null);
    setOverrideWriteResult(null);
    setRollbackPreview(null);
    setOverrideConflict(null);
    setRollbackConflict(null);
    try {
      const detail = await settingsClientService.getTemplateDetail(templateIdOrKey);
      setTemplateDetail(detail);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load template detail");
    }
  }

  async function loadOverrideState(): Promise<void> {
    if (!editorAccess.canPreview || !selectedTemplateIdOrKey) return;
    try {
      setOverrideLoading(true);
      setOverrideError(null);
      const payload = await settingsClientService.getCurrentTemplateOverrideState({
        templateId: selectedTemplateUuid,
        overrideType: selectedOverrideType
      });
      setOverrideState(payload);
      if (payload.state.currentOverride.exists) {
        setOverridePayloadText(pretty(payload.state.currentOverride.payload));
      } else {
        setOverridePayloadText("{}");
      }
      const firstAuditId = payload.state.recentAudits.items[0]?.id ?? "";
      setRollbackTargetAuditId((prev) => (prev ? prev : firstAuditId));
    } catch (cause) {
      setOverrideError(cause instanceof Error ? cause.message : "Failed to load current override state");
      setOverrideState(null);
    } finally {
      setOverrideLoading(false);
    }
  }

  useEffect(() => {
    void loadTemplateCenter();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!editorAccess.canPreview || !selectedTemplateIdOrKey) {
      setOverrideState(null);
      return;
    }
    void loadOverrideState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorAccess.canPreview, selectedTemplateIdOrKey, selectedOverrideType]);

  async function runRecommendation(): Promise<void> {
    setActionLoading(true);
    setError(null);
    setMessage(null);
    try {
      const payload = await settingsClientService.recommendTemplate();
      setRecommendation(payload);
      setMessage(
        payload.usedFallback
          ? `Recommendation generated by fallback: ${payload.fallbackReason ?? "unknown"}`
          : "Template recommendation ready."
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to recommend template");
    } finally {
      setActionLoading(false);
    }
  }

  async function runPreview(): Promise<void> {
    if (!selectedTemplateIdOrKey) return;
    setActionLoading(true);
    setError(null);
    setMessage(null);
    try {
      const payload = await settingsClientService.previewTemplateApply({
        templateIdOrKey: selectedTemplateIdOrKey,
        applyMode,
        applyStrategy
      });
      setPreview({
        diff: payload.diff,
        summary: payload.summary,
        summaryUsedFallback: payload.summaryUsedFallback,
        summaryFallbackReason: payload.summaryFallbackReason
      });
      setMessage("Template preview generated.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to preview template apply");
    } finally {
      setActionLoading(false);
    }
  }

  async function runApply(): Promise<void> {
    if (!selectedTemplateIdOrKey || !canWrite) return;
    setActionLoading(true);
    setError(null);
    setMessage(null);
    try {
      const payload = await settingsClientService.applyTemplate({
        templateIdOrKey: selectedTemplateIdOrKey,
        applyMode,
        applyStrategy,
        generateDemoSeed
      });
      setMessage(
        payload.demoSeed.executed
          ? `Template applied. Demo seed: ${payload.demoSeed.summary ?? "done"}`
          : "Template applied successfully."
      );
      await loadTemplateCenter();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to apply template");
    } finally {
      setActionLoading(false);
    }
  }

  async function runOverrideWritePreview(): Promise<void> {
    const parsed = parseJsonObject(overridePayloadText);
    if (!parsed.value) {
      setOverrideError(parsed.error ?? "Invalid override payload");
      return;
    }

    setOverrideActionLoading(true);
    setOverrideError(null);
    setOverrideMessage(null);
    setOverrideConflict(null);
    try {
      const payload = await settingsClientService.previewTemplateOverrideWrite({
        templateId: selectedTemplateUuid,
        overrideType: selectedOverrideType,
        overridePayload: parsed.value
      });
      setOverridePreview(payload);
      setOverrideMessage("Override governance preview generated.");
    } catch (cause) {
      setOverrideError(cause instanceof Error ? cause.message : "Failed to preview override write");
    } finally {
      setOverrideActionLoading(false);
    }
  }

  async function runOverrideWriteExecute(): Promise<void> {
    if (!editorAccess.canWrite) return;
    const parsed = parseJsonObject(overridePayloadText);
    if (!parsed.value) {
      setOverrideError(parsed.error ?? "Invalid override payload");
      return;
    }
    if (!overrideState?.state.expectedVersion) {
      setOverrideError("Missing expected version baseline. Refresh current state and try again.");
      return;
    }

    setOverrideActionLoading(true);
    setOverrideError(null);
    setOverrideMessage(null);
    setOverrideConflict(null);
    try {
      const result = await settingsClientService.executeTemplateOverrideWrite({
        templateId: selectedTemplateUuid,
        overrideType: selectedOverrideType,
        overridePayload: parsed.value,
        expectedVersion: overrideState.state.expectedVersion
      });
      if (result.status === "conflict") {
        setOverrideConflict(result.conflict);
        setOverrideMessage("Write rejected by drift guard. Refresh state before retry.");
        return;
      }
      setOverrideWriteResult(result.data);
      setOverridePayloadText(pretty(result.data.override.overridePayload));
      setOverrideMessage("Override saved successfully.");
      await loadOverrideState();
    } catch (cause) {
      setOverrideError(cause instanceof Error ? cause.message : "Failed to execute override write");
    } finally {
      setOverrideActionLoading(false);
    }
  }

  async function runRollbackPreview(): Promise<void> {
    if (!rollbackTargetAuditId) {
      setRollbackError("Please select a target audit record first.");
      return;
    }
    setRollbackActionLoading(true);
    setRollbackError(null);
    setRollbackMessage(null);
    setRollbackConflict(null);
    try {
      const payload = await settingsClientService.previewTemplateOverrideRollback({
        templateId: selectedTemplateUuid,
        overrideType: selectedOverrideType,
        targetAuditId: rollbackTargetAuditId
      });
      setRollbackPreview(payload);
      setRollbackMessage("Rollback preview generated.");
    } catch (cause) {
      setRollbackError(cause instanceof Error ? cause.message : "Failed to generate rollback preview");
    } finally {
      setRollbackActionLoading(false);
    }
  }

  async function runRollbackExecute(): Promise<void> {
    if (!editorAccess.canExecuteRollback || !rollbackPreview) return;
    const payload = buildRollbackExecutePayloadFromPreview({
      templateId: selectedTemplateUuid,
      overrideType: selectedOverrideType,
      preview: rollbackPreview.preview
    });
    if (!payload) {
      setRollbackError("Rollback execute payload is incomplete. Please regenerate preview.");
      return;
    }

    setRollbackActionLoading(true);
    setRollbackError(null);
    setRollbackMessage(null);
    setRollbackConflict(null);
    try {
      const result = await settingsClientService.executeTemplateOverrideRollback(payload);
      if (result.status === "conflict") {
        setRollbackConflict(result.conflict);
        setRollbackMessage("Rollback rejected by drift guard. Refresh current state and retry preview.");
        return;
      }
      setRollbackMessage("Rollback executed and audited.");
      await loadOverrideState();
    } catch (cause) {
      setRollbackError(cause instanceof Error ? cause.message : "Failed to execute rollback");
    } finally {
      setRollbackActionLoading(false);
    }
  }

  const diagnosticsSummary =
    (overrideWriteResult?.diagnosticsSummary as Record<string, unknown> | null) ??
    (overridePreview?.preview.diagnosticsSummary as Record<string, unknown> | null) ??
    null;
  const currentWriteDiagnostics = overrideState?.state.currentDiagnostics
    ? asObject(overrideState.state.currentDiagnostics)
    : null;
  const conflictDiagnostics = overrideConflict ? asStringArray(overrideConflict.diagnostics) : [];
  const rollbackConflictDiagnostics = rollbackConflict ? asStringArray(rollbackConflict.diagnostics) : [];

  if (!editorAccess.canAccess) {
    return (
      <div className="text-sm text-muted-foreground">
        Only owner/admin/manager roles can access template settings and override diagnostics.
      </div>
    );
  }

  if (loading || !centerState) {
    return <div className="text-sm text-muted-foreground">Loading template center...</div>;
  }

  return (
    <div>
      <PageHeader
        title="Industry Template Center"
        description="Choose industry package and manage org template overrides with runtime diagnostics, conflict guard, and rollback."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void loadTemplateCenter()} disabled={loading || actionLoading}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
            <Button asChild variant="outline">
              <Link href="/settings/runtime-debug">Runtime Debug</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/settings/onboarding">Back To Onboarding</Link>
            </Button>
          </div>
        }
      />

      {error ? <p className="mb-3 text-sm text-rose-600">{error}</p> : null}
      {message ? <p className="mb-3 text-sm text-emerald-700">{message}</p> : null}

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Current Assignment</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>role={centerState.role}</p>
            {centerState.currentTemplate ? (
              <>
                <p>
                  active template:{" "}
                  <Badge variant="secondary">
                    {centerState.currentTemplate.displayName} ({centerState.currentTemplate.templateKey})
                  </Badge>
                </p>
                <p>assignment mode: {centerState.currentAssignment?.applyMode ?? "-"}</p>
                <p>assignment strategy: {centerState.currentAssignment?.applyStrategy ?? "-"}</p>
              </>
            ) : (
              <p className="text-muted-foreground">No active template yet.</p>
            )}
            {!canWrite ? (
              <p className="text-xs text-muted-foreground">
                Read-only apply: owner/admin required for template apply operations.
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Template Selection</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Select value={selectedTemplateIdOrKey} onValueChange={(value) => void loadDetail(value)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {centerState.templates.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.displayName} ({item.templateKey})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <p className="mb-1 text-xs text-muted-foreground">Apply mode</p>
                <Select value={applyMode} onValueChange={(value) => setApplyMode(value as TemplateApplyMode)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {APPLY_MODES.map((item) => (
                      <SelectItem key={item} value={item}>
                        {item}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <p className="mb-1 text-xs text-muted-foreground">Apply strategy</p>
                <Select value={applyStrategy} onValueChange={(value) => setApplyStrategy(value as TemplateApplyStrategy)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {APPLY_STRATEGIES.map((item) => (
                      <SelectItem key={item} value={item}>
                        {item}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-end">
                <Button
                  variant={generateDemoSeed ? "default" : "outline"}
                  className="w-full"
                  onClick={() => setGenerateDemoSeed((prev) => !prev)}
                >
                  {generateDemoSeed ? "Demo Seed: ON" : "Demo Seed: OFF"}
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => void runRecommendation()} disabled={actionLoading}>
                Recommend Template
              </Button>
              <Button variant="outline" onClick={() => void runPreview()} disabled={actionLoading || !selectedTemplateIdOrKey}>
                Preview Apply
              </Button>
              <Button onClick={() => void runApply()} disabled={actionLoading || !selectedTemplateIdOrKey || !canWrite}>
                Apply Template
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {recommendation ? (
        <section className="mb-4">
          <Card>
            <CardHeader>
              <CardTitle>Template Fit Recommendation</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p>
                recommended:{" "}
                <Badge variant="secondary">{recommendation.recommendation.recommendedTemplateKey}</Badge>
              </p>
              <p>apply mode: {recommendation.recommendation.recommendedApplyMode}</p>
              <p>fit reasons: {recommendation.recommendation.fitReasons.join(" / ") || "-"}</p>
              <p>mismatch risks: {recommendation.recommendation.risksOfMismatch.join(" / ") || "-"}</p>
              <p>recommended overrides: {recommendation.recommendation.recommendedOverrides.join(" / ") || "-"}</p>
              {recommendation.usedFallback ? (
                <p className="text-xs text-amber-700">fallback used: {recommendation.fallbackReason ?? "unknown"}</p>
              ) : null}
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Template Detail</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {!templateDetail ? <p className="text-muted-foreground">Select a template to view details.</p> : null}
            {templateDetail ? (
              <>
                <p className="font-semibold text-slate-900">{templateDetail.template.displayName}</p>
                <p className="text-muted-foreground">{templateDetail.template.summary}</p>
                <p>
                  customer stages:{" "}
                  {((templateDetail.template.templatePayload.customer_stages as string[] | undefined) ?? []).join(
                    " -> "
                  ) || "-"}
                </p>
                <p>
                  opportunity stages:{" "}
                  {(
                    (templateDetail.template.templatePayload.opportunity_stages as string[] | undefined) ?? []
                  ).join(" -> ") || "-"}
                </p>
                <p>alert rules: {JSON.stringify(templateDetail.template.templatePayload.default_alert_rules ?? {}, null, 0)}</p>
                <p>
                  checkpoints:{" "}
                  {((templateDetail.template.templatePayload.suggested_checkpoints as string[] | undefined) ?? []).join(
                    " / "
                  ) || "-"}
                </p>
                <p>
                  manager signals:{" "}
                  {(
                    (templateDetail.template.templatePayload.manager_attention_signals as string[] | undefined) ?? []
                  ).join(" / ") || "-"}
                </p>
                <p>demo profile: {String(templateDetail.template.templatePayload.demo_seed_profile ?? "-")}</p>
              </>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Scenario Packs & Seeded Playbooks</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {!templateDetail ? <p className="text-muted-foreground">No template selected.</p> : null}
            {templateDetail ? (
              <>
                <p>scenario packs: {templateDetail.scenarioPacks.length}</p>
                <div className="space-y-2">
                  {templateDetail.scenarioPacks.slice(0, 6).map((item) => (
                    <div key={item.id} className="rounded border p-2">
                      <p className="font-medium text-slate-900">{item.title}</p>
                      <p className="text-xs text-muted-foreground">{item.packType}</p>
                      <p className="text-xs text-slate-700">{item.summary}</p>
                    </div>
                  ))}
                </div>
                <p>seeded playbooks: {templateDetail.seededPlaybookTemplates.length}</p>
                <div className="space-y-2">
                  {templateDetail.seededPlaybookTemplates.slice(0, 4).map((item) => (
                    <div key={item.id} className="rounded border p-2">
                      <p className="font-medium text-slate-900">{item.title}</p>
                      <p className="text-xs text-muted-foreground">{item.playbookType}</p>
                      <p className="text-xs text-slate-700">{item.summary}</p>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>
      </section>

      {preview ? (
        <section className="mb-4">
          <Card>
            <CardHeader>
              <CardTitle>Apply Preview Diff</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p>changed: {preview.diff.changedKeys.join(", ") || "-"}</p>
              <p>unchanged: {preview.diff.unchangedKeys.join(", ") || "-"}</p>
              <p>notes: {preview.diff.notes.join(" / ") || "-"}</p>
              <p>will change: {preview.summary.whatWillChange.join(" / ") || "-"}</p>
              <p>will not change: {preview.summary.whatWillNotChange.join(" / ") || "-"}</p>
              <p>caution: {preview.summary.cautionNotes.join(" / ") || "-"}</p>
              <p>next steps: {preview.summary.recommendedNextSteps.join(" / ") || "-"}</p>
              {preview.summaryUsedFallback ? (
                <p className="text-xs text-amber-700">
                  summary fallback used: {preview.summaryFallbackReason ?? "unknown"}
                </p>
              ) : null}
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Org Template Override Editor</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {overrideError ? <p className="text-rose-600">{overrideError}</p> : null}
            {overrideMessage ? <p className="text-emerald-700">{overrideMessage}</p> : null}
            <p>
              access role: <Badge variant="secondary">{editorAccess.effectiveOrgRole ?? "unknown"}</Badge>
            </p>
            <p>
              write permission:{" "}
              <Badge variant={editorAccess.canWrite ? "default" : "secondary"}>
                {editorAccess.canWrite ? "owner/admin writable" : "read-only"}
              </Badge>
            </p>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Override type</p>
              <Select
                value={selectedOverrideType}
                onValueChange={(value) => {
                  setSelectedOverrideType(value as OrgTemplateOverride["overrideType"]);
                  setOverridePreview(null);
                  setOverrideWriteResult(null);
                  setOverrideConflict(null);
                  setRollbackPreview(null);
                  setRollbackConflict(null);
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {OVERRIDE_TYPES.map((item) => (
                    <SelectItem key={item} value={item}>
                      {item}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="rounded border border-slate-200 p-2 text-xs text-slate-700">
              <p>template id for write APIs: {selectedTemplateUuid ?? "active-template fallback (non-uuid selected)"}</p>
              <p>baseline token: {overrideState?.state.expectedVersion.compareToken ?? "-"}</p>
              <p>baseline version label: {overrideState?.state.expectedVersion.versionLabel ?? "-"}</p>
              <p>baseline version number: {overrideState?.state.expectedVersion.versionNumber ?? "-"}</p>
              <p>baseline payload hash: {overrideState?.state.expectedVersion.payloadHash ?? "-"}</p>
            </div>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Override payload JSON</p>
              <Textarea
                className="min-h-[180px] font-mono text-xs"
                value={overridePayloadText}
                onChange={(event) => setOverridePayloadText(event.target.value)}
                readOnly={!editorAccess.canWrite}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => void loadOverrideState()} disabled={overrideLoading || overrideActionLoading}>
                Refresh State
              </Button>
              <Button variant="outline" onClick={() => void runOverrideWritePreview()} disabled={overrideActionLoading || overrideLoading}>
                Preview Governance
              </Button>
              <Button
                onClick={() => void runOverrideWriteExecute()}
                disabled={overrideActionLoading || overrideLoading || !editorAccess.canWrite}
              >
                Submit Override
              </Button>
            </div>
            {overrideLoading ? <p className="text-xs text-muted-foreground">Loading current override state...</p> : null}
            {overrideState ? (
              <div className="space-y-1 text-xs">
                <p>current override exists: {String(overrideState.state.currentOverride.exists)}</p>
                <p>current override updated at: {overrideState.state.currentOverride.updatedAt ?? "-"}</p>
                <p>
                  latest persisted version: {overrideState.state.latestPersistedVersion.versionLabel ?? "-"} (
                  {overrideState.state.latestPersistedVersion.availability})
                </p>
                <p>current diagnostics acceptedForRuntime: {String(currentWriteDiagnostics?.acceptedForRuntime ?? false)}</p>
                <p>current diagnostics ignoredFields: {asStringArray(currentWriteDiagnostics?.ignoredFields).join(", ") || "-"}</p>
                <p>current diagnostics list: {asStringArray(currentWriteDiagnostics?.diagnostics).join(" / ") || "-"}</p>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Governance Diagnostics & Conflict</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              accepted overrides: <Badge variant="secondary">{readCount(diagnosticsSummary, "acceptedOverrides")}</Badge>
            </p>
            <p>
              ignored overrides: <Badge variant="secondary">{readCount(diagnosticsSummary, "ignoredOverrides")}</Badge>
            </p>
            <p>
              forbidden overrides: <Badge variant="secondary">{readCount(diagnosticsSummary, "forbiddenOverrides")}</Badge>
            </p>
            <p>
              rejected overrides: <Badge variant="secondary">{readCount(diagnosticsSummary, "rejectedOverrides")}</Badge>
            </p>
            <p>diagnostics summary list: {asStringArray(diagnosticsSummary?.diagnostics).join(" / ") || "-"}</p>
            {overrideConflict ? (
              <div className="rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800">
                <p className="font-medium">Conflict detected (HTTP 409)</p>
                <p>reason: {overrideConflict.conflictReason ?? "-"}</p>
                <p>expected compare token: {String(asObject(overrideConflict.expectedVersion).compareToken ?? "-")}</p>
                <p>current compare token: {String(asObject(overrideConflict.currentVersion).compareToken ?? "-")}</p>
                <p>diagnostics: {conflictDiagnostics.join(" / ") || "-"}</p>
                <p className="mt-1">Action: refresh current state, review latest version, then retry submit.</p>
              </div>
            ) : (
              <p className="text-muted-foreground">No write conflict currently.</p>
            )}
            <JsonDetails title="Current State (raw)" value={overrideState?.state ?? null} />
            <JsonDetails title="Write Preview (raw)" value={overridePreview?.preview ?? null} />
            <JsonDetails title="Last Write Result (raw)" value={overrideWriteResult ?? null} />
          </CardContent>
        </Card>
      </section>

      <section className="mt-4">
        <Card>
          <CardHeader>
            <CardTitle>Rollback Preview & Execute (Recent Audits)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {rollbackError ? <p className="text-rose-600">{rollbackError}</p> : null}
            {rollbackMessage ? <p className="text-emerald-700">{rollbackMessage}</p> : null}
            <p>
              execute permission:{" "}
              <Badge variant={editorAccess.canExecuteRollback ? "default" : "secondary"}>
                {editorAccess.canExecuteRollback ? "owner/admin" : "manager read-only"}
              </Badge>
            </p>
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Rollback target (recent persisted audits)</p>
              <Select value={rollbackTargetAuditId} onValueChange={setRollbackTargetAuditId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select audit record" />
                </SelectTrigger>
                <SelectContent>
                  {(overrideState?.state.recentAudits.items ?? []).map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.versionLabel} ({item.actionType}) @ {item.createdAt}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => void runRollbackPreview()}
                disabled={rollbackActionLoading || overrideLoading || !rollbackTargetAuditId}
              >
                Preview Rollback
              </Button>
              <Button
                onClick={() => void runRollbackExecute()}
                disabled={
                  rollbackActionLoading ||
                  overrideLoading ||
                  !editorAccess.canExecuteRollback ||
                  !rollbackPreview?.preview.canExecute
                }
              >
                Execute Rollback
              </Button>
            </div>
            {rollbackPreview ? (
              <div className="space-y-1 rounded border border-slate-200 bg-slate-50 p-2 text-xs">
                <p>status: {rollbackPreview.preview.status}</p>
                <p>can execute: {String(rollbackPreview.preview.canExecute)}</p>
                <p>reason: {rollbackPreview.preview.reason ?? "-"}</p>
                <p>target version: {rollbackPreview.preview.targetVersion.versionLabel ?? "-"}</p>
                <p>accepted fields: {rollbackPreview.preview.restorePlan.acceptedFields.join(", ") || "-"}</p>
                <p>ignored fields: {rollbackPreview.preview.restorePlan.ignoredFields.join(", ") || "-"}</p>
                <p>diagnostics: {rollbackPreview.preview.diagnostics.join(" / ") || "-"}</p>
                <p>
                  preview expected version token:{" "}
                  {rollbackPreview.preview.concurrency.expectedVersion?.compareToken ?? "-"}
                </p>
              </div>
            ) : (
              <p className="text-muted-foreground">No rollback preview yet.</p>
            )}
            {rollbackConflict ? (
              <div className="rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800">
                <p className="font-medium">Rollback conflict detected (HTTP 409)</p>
                <p>reason: {rollbackConflict.conflictReason ?? "-"}</p>
                <p>expected compare token: {String(asObject(rollbackConflict.expectedVersion).compareToken ?? "-")}</p>
                <p>current compare token: {String(asObject(rollbackConflict.currentVersion).compareToken ?? "-")}</p>
                <p>diagnostics: {rollbackConflictDiagnostics.join(" / ") || "-"}</p>
                <p className="mt-1">Action: refresh and re-run rollback preview before execute.</p>
              </div>
            ) : null}
            {overrideState ? (
              <>
                <p>
                  recent audit availability:{" "}
                  <Badge variant="secondary">{overrideState.state.recentAudits.availability}</Badge>
                </p>
                <p className="text-muted-foreground">{overrideState.state.recentAudits.note}</p>
                {overrideState.state.recentAudits.items.length === 0 ? (
                  <p className="text-muted-foreground">No persisted audit records available for rollback target selection.</p>
                ) : (
                  <div className="space-y-2">
                    {overrideState.state.recentAudits.items.map((item) => (
                      <div key={item.id} className="rounded border p-2 text-xs">
                        <p className="font-medium text-slate-900">
                          {item.versionLabel} / {item.actionType}
                        </p>
                        <p>runtime impact: {item.runtimeImpactSummary ?? "-"}</p>
                        <p>diagnostics: {item.diagnosticsPreview.join(" / ") || "-"}</p>
                        <p>created at: {item.createdAt}</p>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : null}
            <JsonDetails title="Rollback Preview (raw)" value={rollbackPreview?.preview ?? null} />
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
