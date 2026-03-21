"use client";

import { useEffect, useMemo, useState } from "react";
import { RefreshCw, RotateCcw, Save, Search } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  extractOrgConfigConflictPayload,
  resolveOrgConfigEditorAccess
} from "@/lib/org-config-editor-ui";
import {
  settingsClientService,
  type OrgConfigConflictPayload,
  type OrgConfigEditorStatePayload,
  type OrgConfigEditorTargetType,
  type OrgConfigExpectedVersion,
  type OrgConfigRollbackExecuteSuccessPayload,
  type OrgConfigRollbackPreviewPayload,
  type OrgConfigWritePreviewPayload,
  type OrgConfigWriteSuccessPayload
} from "@/services/settings-client-service";
import type { OrgFeatureKey } from "@/types/productization";

const FEATURE_FLAG_KEYS: OrgFeatureKey[] = [
  "ai_auto_analysis",
  "ai_auto_planning",
  "ai_morning_brief",
  "ai_deal_command",
  "external_touchpoints",
  "prep_cards",
  "playbooks",
  "manager_quality_view",
  "outcome_learning",
  "demo_seed_tools"
];

type PreviewMap = Partial<Record<OrgConfigEditorTargetType, OrgConfigWritePreviewPayload["preview"]>>;
type ConflictMap = Partial<Record<OrgConfigEditorTargetType, OrgConfigConflictPayload>>;
type SuccessMap = Partial<Record<OrgConfigEditorTargetType, OrgConfigWriteSuccessPayload>>;
type RollbackPreviewMap = Partial<Record<OrgConfigEditorTargetType, OrgConfigRollbackPreviewPayload["preview"]>>;
type RollbackConflictMap = Partial<Record<OrgConfigEditorTargetType, OrgConfigConflictPayload>>;
type RollbackSuccessMap = Partial<Record<OrgConfigEditorTargetType, OrgConfigRollbackExecuteSuccessPayload>>;

interface OrgSettingsFormState {
  orgDisplayName: string;
  brandName: string;
  timezone: string;
  locale: string;
  defaultFollowupSlaDays: number;
  noFollowupTimeout: number;
  quotedButStalled: number;
  highProbabilityStalled: number;
}

interface OrgAiSettingsFormState {
  modelDefault: string;
  modelReasoning: string;
  fallbackMode: "strict_provider_first" | "provider_then_rules" | "rules_only";
  humanReviewRequiredForSensitiveActions: boolean;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asNumber(value: unknown, fallback: number): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function toOrgSettingsForm(value: Record<string, unknown>): OrgSettingsFormState {
  const rules = asObject(value.defaultAlertRules);
  return {
    orgDisplayName: asString(value.orgDisplayName),
    brandName: asString(value.brandName),
    timezone: asString(value.timezone, "Asia/Shanghai"),
    locale: asString(value.locale, "zh-CN"),
    defaultFollowupSlaDays: asNumber(value.defaultFollowupSlaDays, 3),
    noFollowupTimeout: asNumber(rules.no_followup_timeout, 7),
    quotedButStalled: asNumber(rules.quoted_but_stalled, 10),
    highProbabilityStalled: asNumber(rules.high_probability_stalled, 5)
  };
}

function toOrgAiSettingsForm(value: Record<string, unknown>): OrgAiSettingsFormState {
  const fallbackMode = asString(value.fallbackMode, "provider_then_rules");
  return {
    modelDefault: asString(value.modelDefault),
    modelReasoning: asString(value.modelReasoning),
    fallbackMode:
      fallbackMode === "strict_provider_first" ||
      fallbackMode === "provider_then_rules" ||
      fallbackMode === "rules_only"
        ? fallbackMode
        : "provider_then_rules",
    humanReviewRequiredForSensitiveActions: asBoolean(value.humanReviewRequiredForSensitiveActions, true)
  };
}

function toFeatureFlagForm(value: Record<string, unknown>): Record<OrgFeatureKey, boolean> {
  const result: Partial<Record<OrgFeatureKey, boolean>> = {};
  for (const key of FEATURE_FLAG_KEYS) {
    result[key] = asBoolean(value[key], false);
  }
  return result as Record<OrgFeatureKey, boolean>;
}

function buildOrgSettingsPatch(form: OrgSettingsFormState): Record<string, unknown> {
  return {
    orgDisplayName: form.orgDisplayName,
    brandName: form.brandName,
    timezone: form.timezone,
    locale: form.locale,
    defaultFollowupSlaDays: form.defaultFollowupSlaDays,
    defaultAlertRules: {
      no_followup_timeout: form.noFollowupTimeout,
      quoted_but_stalled: form.quotedButStalled,
      high_probability_stalled: form.highProbabilityStalled
    }
  };
}

function buildOrgAiSettingsPatch(form: OrgAiSettingsFormState): Record<string, unknown> {
  return {
    modelDefault: form.modelDefault,
    modelReasoning: form.modelReasoning,
    fallbackMode: form.fallbackMode,
    humanReviewRequiredForSensitiveActions: form.humanReviewRequiredForSensitiveActions
  };
}

function renderDiagnosticsSummary(preview: OrgConfigWritePreviewPayload["preview"] | null): JSX.Element {
  if (!preview) {
    return <p className="text-xs text-muted-foreground">No preview yet.</p>;
  }

  const summary = asObject(preview.diagnosticsSummary);
  const acceptedFields = (summary.acceptedFields as string[] | undefined) ?? [];
  const ignoredFields = (summary.ignoredFields as string[] | undefined) ?? [];
  const forbiddenFields = (summary.forbiddenFields as string[] | undefined) ?? [];
  const diagnostics = (summary.diagnostics as string[] | undefined) ?? [];
  const runtimeImpactSummary = asString(summary.runtimeImpactSummary, "-");

  return (
    <div className="space-y-1 rounded border border-sky-200 bg-sky-50 p-2 text-xs">
      <p>runtime impact: {runtimeImpactSummary}</p>
      <p>accepted: {acceptedFields.join(" / ") || "-"}</p>
      <p>ignored: {ignoredFields.join(" / ") || "-"}</p>
      <p>forbidden: {forbiddenFields.join(" / ") || "-"}</p>
      <p>diagnostics: {diagnostics.join(" / ") || "-"}</p>
    </div>
  );
}

function renderRollbackSummary(
  preview: OrgConfigRollbackPreviewPayload["preview"] | null
): JSX.Element {
  if (!preview) {
    return <p className="text-xs text-muted-foreground">No rollback preview selected.</p>;
  }
  const targetSummary = asObject(preview.targetValue.summary);
  const restoredSummary = asObject(preview.targetValue.restoredSummary);
  const targetKeys = asStringArray(targetSummary.payloadKeys);
  const restoredKeys = asStringArray(restoredSummary.payloadKeys);
  const redactedFields = asStringArray(targetSummary.redactedFields);

  return (
    <div className="space-y-1 rounded border border-amber-200 bg-amber-50 p-2 text-xs">
      <p>
        status: <span className="font-medium">{preview.status}</span> / can execute:{" "}
        <span className="font-medium">{preview.canExecute ? "yes" : "no"}</span>
      </p>
      <p>reason: {preview.reason ?? "-"}</p>
      <p>target version: {preview.targetVersion.versionLabel ?? "-"}</p>
      <p>restored accepted: {preview.restorePlan.acceptedFields.join(" / ") || "-"}</p>
      <p>ignored: {preview.restorePlan.ignoredFields.join(" / ") || "-"}</p>
      <p>forbidden: {preview.restorePlan.forbiddenFields.join(" / ") || "-"}</p>
      <p>diagnostics: {preview.diagnostics.join(" / ") || "-"}</p>
      <p>target payload keys: {targetKeys.join(" / ") || "-"}</p>
      <p>restored payload keys: {restoredKeys.join(" / ") || "-"}</p>
      <p>redacted fields: {redactedFields.join(" / ") || "-"}</p>
    </div>
  );
}

export default function OrgConfigEditorPage(): JSX.Element {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [savingTarget, setSavingTarget] = useState<OrgConfigEditorTargetType | null>(null);
  const [previewTarget, setPreviewTarget] = useState<OrgConfigEditorTargetType | null>(null);
  const [rollbackPreviewTarget, setRollbackPreviewTarget] = useState<OrgConfigEditorTargetType | null>(null);
  const [rollbackExecuteTarget, setRollbackExecuteTarget] = useState<OrgConfigEditorTargetType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<OrgConfigEditorStatePayload | null>(null);
  const [previews, setPreviews] = useState<PreviewMap>({});
  const [conflicts, setConflicts] = useState<ConflictMap>({});
  const [successResult, setSuccessResult] = useState<SuccessMap>({});
  const [rollbackPreviews, setRollbackPreviews] = useState<RollbackPreviewMap>({});
  const [rollbackConflicts, setRollbackConflicts] = useState<RollbackConflictMap>({});
  const [rollbackSuccessResult, setRollbackSuccessResult] = useState<RollbackSuccessMap>({});
  const [orgSettingsForm, setOrgSettingsForm] = useState<OrgSettingsFormState | null>(null);
  const [orgAiSettingsForm, setOrgAiSettingsForm] = useState<OrgAiSettingsFormState | null>(null);
  const [featureFlagsForm, setFeatureFlagsForm] = useState<Record<OrgFeatureKey, boolean> | null>(null);

  const access = useMemo(
    () =>
      resolveOrgConfigEditorAccess({
        user,
        stateRole: state?.role ?? null
      }),
    [user, state?.role]
  );

  async function loadState(): Promise<void> {
    try {
      setLoading(true);
      setError(null);
      const payload = await settingsClientService.getOrgConfigState();
      setState(payload);
      setOrgSettingsForm(toOrgSettingsForm(payload.state.sections.orgSettings.currentValue));
      setOrgAiSettingsForm(toOrgAiSettingsForm(payload.state.sections.orgAiSettings.currentValue));
      setFeatureFlagsForm(toFeatureFlagForm(payload.state.sections.orgFeatureFlags.currentValue));
      setConflicts({});
      setPreviews({});
      setSuccessResult({});
      setRollbackPreviews({});
      setRollbackConflicts({});
      setRollbackSuccessResult({});
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load org config editor state");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!access.canAccess) {
      setLoading(false);
      return;
    }
    void loadState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access.canAccess]);

  function getPatch(targetType: OrgConfigEditorTargetType): Record<string, unknown> {
    if (targetType === "org_settings") return buildOrgSettingsPatch(orgSettingsForm ?? toOrgSettingsForm({}));
    if (targetType === "org_ai_settings") return buildOrgAiSettingsPatch(orgAiSettingsForm ?? toOrgAiSettingsForm({}));
    return asObject(featureFlagsForm);
  }

  function getExpectedVersion(targetType: OrgConfigEditorTargetType): OrgConfigExpectedVersion {
    if (!state) return {};
    if (targetType === "org_settings") return state.state.sections.orgSettings.expectedVersion;
    if (targetType === "org_ai_settings") return state.state.sections.orgAiSettings.expectedVersion;
    return state.state.sections.orgFeatureFlags.expectedVersion;
  }

  async function handlePreview(targetType: OrgConfigEditorTargetType): Promise<void> {
    if (!access.canPreview) return;
    try {
      setPreviewTarget(targetType);
      setError(null);
      const payload = await settingsClientService.previewOrgConfigWrite({
        targetType,
        patch: getPatch(targetType)
      });
      setPreviews((prev) => ({
        ...prev,
        [targetType]: payload.preview
      }));
      setConflicts((prev) => {
        const next = { ...prev };
        delete next[targetType];
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to preview org config write");
    } finally {
      setPreviewTarget(null);
    }
  }

  async function handleExecute(targetType: OrgConfigEditorTargetType): Promise<void> {
    if (!access.canWrite || !state) return;
    try {
      setSavingTarget(targetType);
      setError(null);
      const result = await settingsClientService.executeOrgConfigWrite({
        targetType,
        patch: getPatch(targetType),
        expectedVersion: getExpectedVersion(targetType)
      });
      if (result.status === "conflict") {
        const parsed = extractOrgConfigConflictPayload(result.conflict);
        if (parsed) {
          setConflicts((prev) => ({
            ...prev,
            [targetType]: parsed
          }));
          return;
        }
        setError("Write rejected because version baseline drifted. Please refresh and retry.");
        return;
      }

      if (result.status !== "success") {
        setError("Unexpected write response. Please refresh and retry.");
        return;
      }

      setSuccessResult((prev) => ({
        ...prev,
        [targetType]: result.data
      }));
      await loadState();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to execute org config write");
    } finally {
      setSavingTarget(null);
    }
  }

  async function handleRollbackPreview(targetType: OrgConfigEditorTargetType, targetAuditId: string): Promise<void> {
    if (!access.canAccess) return;
    try {
      setRollbackPreviewTarget(targetType);
      setError(null);
      const payload = await settingsClientService.previewOrgConfigRollback({
        targetType,
        targetAuditId
      });
      setRollbackPreviews((prev) => ({
        ...prev,
        [targetType]: payload.preview
      }));
      setRollbackConflicts((prev) => {
        const next = { ...prev };
        delete next[targetType];
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to preview rollback");
    } finally {
      setRollbackPreviewTarget(null);
    }
  }

  async function handleRollbackExecute(targetType: OrgConfigEditorTargetType): Promise<void> {
    if (!access.canWrite) return;
    const preview = rollbackPreviews[targetType];
    const expectedVersion = preview?.concurrency.expectedVersion ?? null;
    const targetAuditId = preview?.targetVersion.auditId ?? null;
    if (!preview || !preview.canExecute || !expectedVersion || !targetAuditId) {
      setError("Rollback preview is required and must be executable before rollback execution.");
      return;
    }

    try {
      setRollbackExecuteTarget(targetType);
      setError(null);
      const result = await settingsClientService.executeOrgConfigRollback({
        targetType,
        targetAuditId,
        expectedVersion
      });
      if (result.status === "conflict") {
        const parsed = extractOrgConfigConflictPayload(result.conflict);
        if (parsed) {
          setRollbackConflicts((prev) => ({
            ...prev,
            [targetType]: parsed
          }));
          return;
        }
        setError("Rollback was rejected because the version baseline drifted. Please refresh and retry.");
        return;
      }

      if (result.status !== "success") {
        setError("Unexpected rollback response. Please refresh and retry.");
        return;
      }

      setRollbackSuccessResult((prev) => ({
        ...prev,
        [targetType]: result.data
      }));
      await loadState();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to execute rollback");
    } finally {
      setRollbackExecuteTarget(null);
    }
  }

  if (!access.canAccess) {
    return <div className="text-sm text-muted-foreground">Only owner/admin/manager can access org config editor.</div>;
  }

  if (loading || !state || !orgSettingsForm || !orgAiSettingsForm || !featureFlagsForm) {
    return <div className="text-sm text-muted-foreground">Loading org config editor...</div>;
  }

  return (
    <div>
      <PageHeader
        title="Org Config Editor v1"
        description="Minimal governed editor for org settings, AI settings and feature flags with expectedVersion and diagnostics."
        action={
          <Button variant="outline" onClick={() => void loadState()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        }
      />

      {error ? <p className="mb-4 text-sm text-rose-600">{error}</p> : null}

      <Card className="mb-4 border-sky-100 bg-sky-50/60">
        <CardHeader>
          <CardTitle className="text-base">Access Summary</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <p>
            role: <Badge variant="secondary">{state.role}</Badge>
          </p>
          <p>
            can write: <Badge variant={access.canWrite ? "default" : "secondary"}>{access.canWrite ? "yes" : "read-only"}</Badge>
          </p>
          <p>
            manager can view summary/audits but cannot submit write operations.
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Org Settings</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="space-y-1">
              <Label>Display Name</Label>
              <Input
                value={orgSettingsForm.orgDisplayName}
                disabled={!access.canWrite || savingTarget === "org_settings"}
                onChange={(event) =>
                  setOrgSettingsForm({
                    ...orgSettingsForm,
                    orgDisplayName: event.target.value
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label>Brand Name</Label>
              <Input
                value={orgSettingsForm.brandName}
                disabled={!access.canWrite || savingTarget === "org_settings"}
                onChange={(event) =>
                  setOrgSettingsForm({
                    ...orgSettingsForm,
                    brandName: event.target.value
                  })
                }
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>Timezone</Label>
                <Input
                  value={orgSettingsForm.timezone}
                  disabled={!access.canWrite || savingTarget === "org_settings"}
                  onChange={(event) =>
                    setOrgSettingsForm({
                      ...orgSettingsForm,
                      timezone: event.target.value
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>Locale</Label>
                <Input
                  value={orgSettingsForm.locale}
                  disabled={!access.canWrite || savingTarget === "org_settings"}
                  onChange={(event) =>
                    setOrgSettingsForm({
                      ...orgSettingsForm,
                      locale: event.target.value
                    })
                  }
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>SLA Days</Label>
                <Input
                  type="number"
                  value={orgSettingsForm.defaultFollowupSlaDays}
                  disabled={!access.canWrite || savingTarget === "org_settings"}
                  onChange={(event) =>
                    setOrgSettingsForm({
                      ...orgSettingsForm,
                      defaultFollowupSlaDays: Number(event.target.value || "3")
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>No Followup Timeout</Label>
                <Input
                  type="number"
                  value={orgSettingsForm.noFollowupTimeout}
                  disabled={!access.canWrite || savingTarget === "org_settings"}
                  onChange={(event) =>
                    setOrgSettingsForm({
                      ...orgSettingsForm,
                      noFollowupTimeout: Number(event.target.value || "7")
                    })
                  }
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>Quoted Stalled</Label>
                <Input
                  type="number"
                  value={orgSettingsForm.quotedButStalled}
                  disabled={!access.canWrite || savingTarget === "org_settings"}
                  onChange={(event) =>
                    setOrgSettingsForm({
                      ...orgSettingsForm,
                      quotedButStalled: Number(event.target.value || "10")
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>High Prob Stalled</Label>
                <Input
                  type="number"
                  value={orgSettingsForm.highProbabilityStalled}
                  disabled={!access.canWrite || savingTarget === "org_settings"}
                  onChange={(event) =>
                    setOrgSettingsForm({
                      ...orgSettingsForm,
                      highProbabilityStalled: Number(event.target.value || "5")
                    })
                  }
                />
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!access.canPreview || previewTarget === "org_settings"}
                onClick={() => void handlePreview("org_settings")}
              >
                <Search className="mr-1 h-3 w-3" />
                Preview
              </Button>
              <Button
                size="sm"
                disabled={!access.canWrite || savingTarget === "org_settings"}
                onClick={() => void handleExecute("org_settings")}
              >
                <Save className="mr-1 h-3 w-3" />
                Save
              </Button>
            </div>
            {renderDiagnosticsSummary(previews.org_settings ?? null)}
            <p className="text-xs text-muted-foreground">
              expected token: {state.state.sections.orgSettings.expectedVersion.compareToken ?? "-"}
            </p>
            <p className="text-xs text-muted-foreground">
              latest audit: {state.state.sections.orgSettings.latestPersistedVersion.versionLabel ?? "-"}
            </p>
            <p className="text-xs text-muted-foreground">
              latest diagnostics: {state.state.sections.orgSettings.latestDiagnosticsSummary?.diagnostics.join(" / ") || "-"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Org AI Settings</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="space-y-1">
              <Label>Model Default</Label>
              <Input
                value={orgAiSettingsForm.modelDefault}
                disabled={!access.canWrite || savingTarget === "org_ai_settings"}
                onChange={(event) =>
                  setOrgAiSettingsForm({
                    ...orgAiSettingsForm,
                    modelDefault: event.target.value
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label>Model Reasoning</Label>
              <Input
                value={orgAiSettingsForm.modelReasoning}
                disabled={!access.canWrite || savingTarget === "org_ai_settings"}
                onChange={(event) =>
                  setOrgAiSettingsForm({
                    ...orgAiSettingsForm,
                    modelReasoning: event.target.value
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label>Fallback Mode</Label>
              <select
                className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm"
                value={orgAiSettingsForm.fallbackMode}
                disabled={!access.canWrite || savingTarget === "org_ai_settings"}
                onChange={(event) =>
                  setOrgAiSettingsForm({
                    ...orgAiSettingsForm,
                    fallbackMode: event.target.value as OrgAiSettingsFormState["fallbackMode"]
                  })
                }
              >
                <option value="strict_provider_first">strict_provider_first</option>
                <option value="provider_then_rules">provider_then_rules</option>
                <option value="rules_only">rules_only</option>
              </select>
            </div>
            <div className="flex items-center justify-between rounded border border-slate-200 p-2">
              <Label>Human Review Required</Label>
              <Switch
                checked={orgAiSettingsForm.humanReviewRequiredForSensitiveActions}
                disabled={!access.canWrite || savingTarget === "org_ai_settings"}
                onCheckedChange={(checked) =>
                  setOrgAiSettingsForm({
                    ...orgAiSettingsForm,
                    humanReviewRequiredForSensitiveActions: checked
                  })
                }
              />
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!access.canPreview || previewTarget === "org_ai_settings"}
                onClick={() => void handlePreview("org_ai_settings")}
              >
                <Search className="mr-1 h-3 w-3" />
                Preview
              </Button>
              <Button
                size="sm"
                disabled={!access.canWrite || savingTarget === "org_ai_settings"}
                onClick={() => void handleExecute("org_ai_settings")}
              >
                <Save className="mr-1 h-3 w-3" />
                Save
              </Button>
            </div>
            {renderDiagnosticsSummary(previews.org_ai_settings ?? null)}
            <p className="text-xs text-muted-foreground">
              expected token: {state.state.sections.orgAiSettings.expectedVersion.compareToken ?? "-"}
            </p>
            <p className="text-xs text-muted-foreground">
              latest audit: {state.state.sections.orgAiSettings.latestPersistedVersion.versionLabel ?? "-"}
            </p>
            <p className="text-xs text-muted-foreground">
              latest diagnostics: {state.state.sections.orgAiSettings.latestDiagnosticsSummary?.diagnostics.join(" / ") || "-"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Org Feature Flags</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {FEATURE_FLAG_KEYS.map((key) => (
              <div key={key} className="flex items-center justify-between rounded border border-slate-200 p-2">
                <Label>{key}</Label>
                <Switch
                  checked={featureFlagsForm[key]}
                  disabled={!access.canWrite || savingTarget === "org_feature_flags"}
                  onCheckedChange={(checked) =>
                    setFeatureFlagsForm({
                      ...featureFlagsForm,
                      [key]: checked
                    })
                  }
                />
              </div>
            ))}
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!access.canPreview || previewTarget === "org_feature_flags"}
                onClick={() => void handlePreview("org_feature_flags")}
              >
                <Search className="mr-1 h-3 w-3" />
                Preview
              </Button>
              <Button
                size="sm"
                disabled={!access.canWrite || savingTarget === "org_feature_flags"}
                onClick={() => void handleExecute("org_feature_flags")}
              >
                <Save className="mr-1 h-3 w-3" />
                Save
              </Button>
            </div>
            {renderDiagnosticsSummary(previews.org_feature_flags ?? null)}
            <p className="text-xs text-muted-foreground">
              expected token: {state.state.sections.orgFeatureFlags.expectedVersion.compareToken ?? "-"}
            </p>
            <p className="text-xs text-muted-foreground">
              latest audit: {state.state.sections.orgFeatureFlags.latestPersistedVersion.versionLabel ?? "-"}
            </p>
            <p className="text-xs text-muted-foreground">
              latest diagnostics: {state.state.sections.orgFeatureFlags.latestDiagnosticsSummary?.diagnostics.join(" / ") || "-"}
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        {(["org_settings", "org_ai_settings", "org_feature_flags"] as OrgConfigEditorTargetType[]).map((targetType) => {
          const section =
            targetType === "org_settings"
              ? state.state.sections.orgSettings
              : targetType === "org_ai_settings"
                ? state.state.sections.orgAiSettings
                : state.state.sections.orgFeatureFlags;
          const writeConflict = conflicts[targetType];
          const writeSuccess = successResult[targetType];
          const rollbackPreview = rollbackPreviews[targetType] ?? null;
          const rollbackConflict = rollbackConflicts[targetType];
          const rollbackSuccess = rollbackSuccessResult[targetType];
          return (
            <Card key={targetType}>
              <CardHeader>
                <CardTitle className="text-base">{targetType} Recent Audits</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                <p>
                  availability:{" "}
                  <Badge variant={section.recentAudits.availability === "available" ? "default" : "secondary"}>
                    {section.recentAudits.availability}
                  </Badge>
                </p>
                <p className="text-muted-foreground">{section.recentAudits.note}</p>
                {section.recentAudits.items.length === 0 ? (
                  <p className="text-muted-foreground">No persisted audit records.</p>
                ) : (
                  section.recentAudits.items.slice(0, 4).map((item) => (
                    <div key={item.id} className="rounded border border-slate-200 bg-slate-50 p-2">
                      <p>{item.actionType} / {item.versionLabel}</p>
                      <p>created: {item.createdAt}</p>
                      <p>runtime impact: {item.runtimeImpactSummary ?? "-"}</p>
                      <p>diagnostics: {item.diagnosticsPreview.join(" / ") || "-"}</p>
                      <div className="mt-1 flex items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={rollbackPreviewTarget === targetType}
                          onClick={() => void handleRollbackPreview(targetType, item.id)}
                        >
                          <RotateCcw className="mr-1 h-3 w-3" />
                          Rollback Preview
                        </Button>
                        {rollbackPreview?.targetVersion.auditId === item.id ? (
                          <Badge variant="secondary">selected</Badge>
                        ) : null}
                      </div>
                    </div>
                  ))
                )}
                {renderRollbackSummary(rollbackPreview)}
                {rollbackPreview ? (
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!access.canWrite || !rollbackPreview.canExecute || rollbackExecuteTarget === targetType}
                      onClick={() => void handleRollbackExecute(targetType)}
                    >
                      <RotateCcw className="mr-1 h-3 w-3" />
                      Execute Rollback
                    </Button>
                    <p className="self-center text-[11px] text-muted-foreground">
                      {access.canWrite
                        ? "Execute requires preview expectedVersion baseline."
                        : "Manager can preview rollback but cannot execute."}
                    </p>
                  </div>
                ) : null}
                {writeConflict ? (
                  <div className="rounded border border-rose-200 bg-rose-50 p-2 text-rose-700">
                    <p className="font-medium">Write Conflict: {writeConflict.conflictReason ?? "-"}</p>
                    <p>expected: {String(asObject(writeConflict.expectedVersion).compareToken ?? "-")}</p>
                    <p>current: {String(asObject(writeConflict.currentVersion).compareToken ?? "-")}</p>
                    <p>diagnostics: {writeConflict.diagnostics.join(" / ") || "-"}</p>
                    <p className="mt-1">建议：刷新状态后重新预览再提交。</p>
                  </div>
                ) : null}
                {rollbackConflict ? (
                  <div className="rounded border border-rose-200 bg-rose-50 p-2 text-rose-700">
                    <p className="font-medium">Rollback Conflict: {rollbackConflict.conflictReason ?? "-"}</p>
                    <p>expected: {String(asObject(rollbackConflict.expectedVersion).compareToken ?? "-")}</p>
                    <p>current: {String(asObject(rollbackConflict.currentVersion).compareToken ?? "-")}</p>
                    <p>diagnostics: {rollbackConflict.diagnostics.join(" / ") || "-"}</p>
                    <p className="mt-1">建议：刷新状态并重新选择目标版本做 preview 后再执行。</p>
                  </div>
                ) : null}
                {writeSuccess ? (
                  <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-emerald-700">
                    <p className="font-medium">Last write succeeded</p>
                    <p>runtime impact: {String(asObject(writeSuccess.writeDiagnostics).runtimeImpactSummary ?? "-")}</p>
                    <p>persisted audit: {String(asObject(writeSuccess.persistedAudit).status ?? "-")}</p>
                  </div>
                ) : null}
                {rollbackSuccess ? (
                  <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-emerald-700">
                    <p className="font-medium">Rollback executed</p>
                    <p>status: {String(asObject(rollbackSuccess.execution).status ?? "-")}</p>
                    <p>
                      persisted audit:{" "}
                      {String(asObject(asObject(rollbackSuccess.execution).writeResult).persistedAuditStatus ?? "-")}
                    </p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
