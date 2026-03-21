import { canViewManagerWorkspace, isOrgAdminLike, type RoleCapabilityInput } from "@/lib/role-capability";
import {
  assertNoOrgConfigDrift,
  buildExpectedVersionFromOrgConfigBaseline,
  buildOrgConfigConcurrencyBaseline,
  hasOrgConfigExpectedVersion,
  isOrgConfigDriftConflictError,
  type OrgConfigConcurrencyBaseline,
  type OrgConfigDriftConflictInfo,
  type OrgConfigExpectedVersion
} from "@/lib/override-concurrency-guard";
import {
  prepareOrgAiSettingsWrite,
  prepareOrgFeatureFlagsWrite,
  prepareOrgSettingsWrite,
  type OrgAiSettingsGovernancePatch,
  type OrgFeatureFlagsGovernancePatch,
  type OrgSettingsGovernancePatch
} from "@/lib/org-config-write-governance";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  findOrgConfigAuditRecord,
  getLatestOrgConfigAuditVersion
} from "@/services/org-config-audit-service";
import {
  governedUpdateOrgAiSettings,
  governedUpdateOrgFeatureFlags,
  governedUpdateOrgSettings,
  type OrgConfigRollbackSourceSummary
} from "@/services/org-config-governance-service";
import { getOrgAiSettings } from "@/services/org-ai-settings-service";
import { getOrgFeatureFlagMap } from "@/services/org-feature-service";
import { getOrgSettings } from "@/services/org-settings-service";
import type { OrgFeatureKey } from "@/types/productization";

type DbClient = ServerSupabaseClient;

export type OrgConfigRollbackTargetType =
  | "org_settings"
  | "org_ai_settings"
  | "org_feature_flags";

export interface OrgConfigRollbackSelector {
  targetAuditId?: string | null;
  targetVersionLabel?: string | null;
  targetVersionNumber?: number | null;
}

export interface OrgConfigRollbackPayloadSummary {
  payloadKeys: string[];
  payloadPreview: string;
  hasSensitiveRedaction: boolean;
  redactedFields: string[];
}

export interface OrgConfigRollbackPreview {
  generatedAt: string;
  targetType: OrgConfigRollbackTargetType;
  status: "allowed" | "rejected" | "not_available";
  canExecute: boolean;
  reason: string | null;
  diagnostics: string[];
  request: {
    orgId: string;
    targetType: OrgConfigRollbackTargetType;
    targetKey: string;
  };
  targetVersion: {
    auditId: string | null;
    versionLabel: string | null;
    versionNumber: number | null;
    actionType: string | null;
    createdAt: string | null;
  };
  currentValue: {
    summary: OrgConfigRollbackPayloadSummary | null;
  };
  targetValue: {
    summary: OrgConfigRollbackPayloadSummary | null;
    restoredSummary: OrgConfigRollbackPayloadSummary | null;
    normalizedPatch: Record<string, unknown>;
  };
  restorePlan: {
    acceptedFields: string[];
    ignoredFields: string[];
    forbiddenFields: string[];
    runtimeImpactSummary: string;
  };
  concurrency: {
    baseline: OrgConfigConcurrencyBaseline | null;
    expectedVersion: Required<OrgConfigExpectedVersion> | null;
    note: string;
  };
}

export interface ExecuteOrgConfigRollbackResult {
  status: "executed" | "rejected" | "not_available" | "conflict";
  reason: string | null;
  diagnostics: string[];
  preview: OrgConfigRollbackPreview;
  conflict: OrgConfigDriftConflictInfo | null;
  writeResult: {
    targetType: OrgConfigRollbackTargetType;
    persistedAuditStatus: "persisted" | "not_available";
    persistedAuditVersionLabel: string | null;
    runtimeImpactSummary: string;
  } | null;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.map((item) => item.trim()).filter((item) => item.length > 0)));
}

function normalizePath(base: string, child: string): string {
  return base ? `${base}.${child}` : child;
}

function collectLeafKeys(value: unknown, path = ""): string[] {
  if (Array.isArray(value)) return path ? [path] : [];
  if (!value || typeof value !== "object") return path ? [path] : [];
  const entries = Object.entries(asObject(value));
  if (entries.length === 0) return path ? [path] : [];
  return entries.flatMap(([key, child]) => collectLeafKeys(child, normalizePath(path, key)));
}

function stableSortObject(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => stableSortObject(item));
  if (!value || typeof value !== "object") return value;
  const entries = Object.entries(asObject(value)).sort(([left], [right]) => left.localeCompare(right));
  return Object.fromEntries(entries.map(([key, child]) => [key, stableSortObject(child)]));
}

function shouldRedactField(path: string): boolean {
  return /(secret|token|password|api[_-]?key|access[_-]?key|credential)/i.test(path);
}

function redactPayloadForSummary(value: unknown, path = ""): {
  value: unknown;
  redactedFields: string[];
} {
  if (Array.isArray(value)) {
    const next = value.map((item, index) => redactPayloadForSummary(item, normalizePath(path, String(index))));
    return {
      value: next.map((item) => item.value),
      redactedFields: next.flatMap((item) => item.redactedFields)
    };
  }
  if (!value || typeof value !== "object") {
    if (path && shouldRedactField(path)) {
      return {
        value: "***REDACTED***",
        redactedFields: [path]
      };
    }
    return {
      value,
      redactedFields: []
    };
  }
  const output: Record<string, unknown> = {};
  const redactedFields: string[] = [];
  for (const [key, child] of Object.entries(asObject(value))) {
    const nextPath = normalizePath(path, key);
    if (shouldRedactField(nextPath)) {
      output[key] = "***REDACTED***";
      redactedFields.push(nextPath);
      continue;
    }
    const nested = redactPayloadForSummary(child, nextPath);
    output[key] = nested.value;
    redactedFields.push(...nested.redactedFields);
  }
  return {
    value: output,
    redactedFields
  };
}

function summarizePayload(payload: Record<string, unknown> | null): OrgConfigRollbackPayloadSummary | null {
  if (!payload) return null;
  const redacted = redactPayloadForSummary(payload);
  const payloadKeys = uniqueStrings(collectLeafKeys(payload)).sort((left, right) => left.localeCompare(right));
  return {
    payloadKeys,
    payloadPreview: JSON.stringify(stableSortObject(redacted.value)),
    hasSensitiveRedaction: redacted.redactedFields.length > 0,
    redactedFields: uniqueStrings(redacted.redactedFields).sort((left, right) => left.localeCompare(right))
  };
}

function parsePayloadPreview(value: unknown): Record<string, unknown> | null {
  const text = asString(value);
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    const obj = asObject(parsed);
    return Object.keys(obj).length > 0 ? obj : null;
  } catch {
    return null;
  }
}

function toOrgSettingsPayload(settings: Awaited<ReturnType<typeof getOrgSettings>>): Record<string, unknown> {
  return {
    orgDisplayName: settings.orgDisplayName,
    brandName: settings.brandName,
    industryHint: settings.industryHint,
    timezone: settings.timezone,
    locale: settings.locale,
    defaultCustomerStages: [...settings.defaultCustomerStages],
    defaultOpportunityStages: [...settings.defaultOpportunityStages],
    defaultAlertRules: { ...settings.defaultAlertRules },
    defaultFollowupSlaDays: settings.defaultFollowupSlaDays,
    onboardingCompleted: settings.onboardingCompleted,
    onboardingStepState: { ...settings.onboardingStepState }
  };
}

function toOrgAiSettingsPayload(settings: Awaited<ReturnType<typeof getOrgAiSettings>>): Record<string, unknown> {
  return {
    provider: settings.provider,
    modelDefault: settings.modelDefault,
    modelReasoning: settings.modelReasoning,
    fallbackMode: settings.fallbackMode,
    autoAnalysisEnabled: settings.autoAnalysisEnabled,
    autoPlanEnabled: settings.autoPlanEnabled,
    autoBriefEnabled: settings.autoBriefEnabled,
    autoTouchpointReviewEnabled: settings.autoTouchpointReviewEnabled,
    humanReviewRequiredForSensitiveActions: settings.humanReviewRequiredForSensitiveActions,
    maxDailyAiRuns: settings.maxDailyAiRuns,
    maxMonthlyAiRuns: settings.maxMonthlyAiRuns
  };
}

function toFeatureFlagPayload(flags: Record<OrgFeatureKey, boolean>): Record<string, unknown> {
  return { ...flags };
}

function buildRejectedPreview(params: {
  orgId: string;
  targetType: OrgConfigRollbackTargetType;
  status: "rejected" | "not_available";
  reason: string;
  diagnostics: string[];
  generatedAt?: string;
}): OrgConfigRollbackPreview {
  return {
    generatedAt: params.generatedAt ?? new Date().toISOString(),
    targetType: params.targetType,
    status: params.status,
    canExecute: false,
    reason: params.reason,
    diagnostics: uniqueStrings(params.diagnostics),
    request: {
      orgId: params.orgId,
      targetType: params.targetType,
      targetKey: "default"
    },
    targetVersion: {
      auditId: null,
      versionLabel: null,
      versionNumber: null,
      actionType: null,
      createdAt: null
    },
    currentValue: {
      summary: null
    },
    targetValue: {
      summary: null,
      restoredSummary: null,
      normalizedPatch: {}
    },
    restorePlan: {
      acceptedFields: [],
      ignoredFields: [],
      forbiddenFields: [],
      runtimeImpactSummary: "write_rejected"
    },
    concurrency: {
      baseline: null,
      expectedVersion: null,
      note: "No compare baseline is available because rollback preview is not executable."
    }
  };
}

function resolveCanExecute(params: {
  acceptedForWrite: boolean;
  ignoredFields: string[];
  forbiddenFields: string[];
}): {
  canExecute: boolean;
  reason: string | null;
  diagnostics: string[];
} {
  if (!params.acceptedForWrite) {
    return {
      canExecute: false,
      reason: "rollback_target_payload_rejected_by_hardening",
      diagnostics: ["rollback_rejected:target_payload_rejected_by_hardening"]
    };
  }
  if (params.forbiddenFields.length > 0) {
    return {
      canExecute: false,
      reason: "rollback_forbidden_fields_detected",
      diagnostics: params.forbiddenFields.map((item) => `rollback_rejected:forbidden_field:${item}`)
    };
  }
  if (params.ignoredFields.length > 0) {
    return {
      canExecute: false,
      reason: "rollback_requires_lossless_payload_restore",
      diagnostics: params.ignoredFields.map((item) => `rollback_rejected:ignored_field:${item}`)
    };
  }
  return {
    canExecute: true,
    reason: null,
    diagnostics: []
  };
}

function extractTargetPayload(record: {
  snapshotSummary: Record<string, unknown>;
  afterSummary: Record<string, unknown>;
}): Record<string, unknown> | null {
  const fromAfterSummary = parsePayloadPreview(asObject(record.afterSummary).payloadPreview);
  if (fromAfterSummary && Object.keys(fromAfterSummary).length > 0) {
    return fromAfterSummary;
  }

  const snapshotSummary = asObject(record.snapshotSummary);
  const snapshot = asObject(snapshotSummary.snapshot);
  const payloadSummary = asObject(snapshot.payloadSummary);
  const normalizedPatch = asObject(payloadSummary.normalizedPatch);
  if (Object.keys(normalizedPatch).length > 0) {
    return normalizedPatch;
  }

  const directSummary = asObject(snapshotSummary.payloadSummary);
  const directPatch = asObject(directSummary.normalizedPatch);
  return Object.keys(directPatch).length > 0 ? directPatch : null;
}

function buildRollbackSource(params: {
  targetVersion: OrgConfigRollbackPreview["targetVersion"];
  generatedAt: string;
}): OrgConfigRollbackSourceSummary {
  return {
    sourceAuditId: params.targetVersion.auditId ?? "unknown_audit_id",
    sourceVersionLabel: params.targetVersion.versionLabel ?? "unknown_version_label",
    sourceVersionNumber: params.targetVersion.versionNumber ?? 0,
    previewGeneratedAt: params.generatedAt
  };
}

async function resolveCurrentState(params: {
  supabase: DbClient;
  orgId: string;
  targetType: OrgConfigRollbackTargetType;
}): Promise<{
  payload: Record<string, unknown>;
  updatedAt: string | null;
}> {
  if (params.targetType === "org_settings") {
    const settings = await getOrgSettings({
      supabase: params.supabase,
      orgId: params.orgId
    });
    return {
      payload: toOrgSettingsPayload(settings),
      updatedAt: settings.updatedAt
    };
  }

  if (params.targetType === "org_ai_settings") {
    const settings = await getOrgAiSettings({
      supabase: params.supabase,
      orgId: params.orgId
    });
    return {
      payload: toOrgAiSettingsPayload(settings),
      updatedAt: settings.updatedAt
    };
  }

  const featureFlagMap = await getOrgFeatureFlagMap({
    supabase: params.supabase,
    orgId: params.orgId
  });
  return {
    payload: toFeatureFlagPayload(featureFlagMap),
    updatedAt: null
  };
}

function prepareTargetWrite(params: {
  targetType: OrgConfigRollbackTargetType;
  payload: Record<string, unknown>;
}) {
  if (params.targetType === "org_settings") {
    return prepareOrgSettingsWrite({
      patch: params.payload as OrgSettingsGovernancePatch
    });
  }
  if (params.targetType === "org_ai_settings") {
    return prepareOrgAiSettingsWrite({
      patch: params.payload as OrgAiSettingsGovernancePatch
    });
  }
  return prepareOrgFeatureFlagsWrite({
    patch: params.payload as OrgFeatureFlagsGovernancePatch
  });
}

export function canPreviewOrgConfigRollback(
  input: RoleCapabilityInput | null | undefined
): boolean {
  return canViewManagerWorkspace(input);
}

export function canExecuteOrgConfigRollback(
  input: RoleCapabilityInput | null | undefined
): boolean {
  return isOrgAdminLike(input);
}

export async function previewOrgConfigRollback(params: {
  supabase: DbClient;
  orgId: string;
  targetType: OrgConfigRollbackTargetType;
  selector: OrgConfigRollbackSelector;
}): Promise<OrgConfigRollbackPreview> {
  const generatedAt = new Date().toISOString();
  const selectorProvided =
    Boolean(params.selector.targetAuditId) ||
    Boolean(params.selector.targetVersionLabel) ||
    (typeof params.selector.targetVersionNumber === "number" && params.selector.targetVersionNumber > 0);
  if (!selectorProvided) {
    return buildRejectedPreview({
      orgId: params.orgId,
      targetType: params.targetType,
      status: "rejected",
      reason: "rollback_selector_required",
      diagnostics: ["rollback_selector_required"],
      generatedAt
    });
  }

  const targetAuditResult = await findOrgConfigAuditRecord({
    supabase: params.supabase,
    orgId: params.orgId,
    targetType: params.targetType,
    targetKey: "default",
    auditId: params.selector.targetAuditId ?? null,
    versionLabel: params.selector.targetVersionLabel ?? null,
    versionNumber: params.selector.targetVersionNumber ?? null
  });
  if (targetAuditResult.status === "not_available") {
    return buildRejectedPreview({
      orgId: params.orgId,
      targetType: params.targetType,
      status: "not_available",
      reason: targetAuditResult.reason ?? "org_config_audit_logs_not_available",
      diagnostics: [targetAuditResult.reason ?? "org_config_audit_logs_not_available"],
      generatedAt
    });
  }
  if (targetAuditResult.status === "not_found" || !targetAuditResult.item) {
    return buildRejectedPreview({
      orgId: params.orgId,
      targetType: params.targetType,
      status: "rejected",
      reason: "rollback_target_version_not_found",
      diagnostics: [targetAuditResult.reason ?? "rollback_target_version_not_found"],
      generatedAt
    });
  }

  const targetAudit = targetAuditResult.item;
  const targetPayload = extractTargetPayload({
    snapshotSummary: targetAudit.snapshotSummary,
    afterSummary: targetAudit.afterSummary
  });
  if (!targetPayload || Object.keys(targetPayload).length === 0) {
    return buildRejectedPreview({
      orgId: params.orgId,
      targetType: params.targetType,
      status: "rejected",
      reason: "rollback_target_payload_missing",
      diagnostics: ["rollback_target_payload_missing"],
      generatedAt
    });
  }

  const prepared = prepareTargetWrite({
    targetType: params.targetType,
    payload: targetPayload
  });
  const [current, latestVersion] = await Promise.all([
    resolveCurrentState({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: params.targetType
    }),
    getLatestOrgConfigAuditVersion({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: params.targetType,
      targetKey: "default"
    })
  ]);
  const baseline = buildOrgConfigConcurrencyBaseline({
    targetType: params.targetType,
    targetKey: "default",
    auditAvailability: latestVersion.availability,
    currentVersionLabel: latestVersion.item?.versionLabel ?? null,
    currentVersionNumber: latestVersion.item?.versionNumber ?? null,
    currentUpdatedAt:
      params.targetType === "org_feature_flags"
        ? latestVersion.item?.createdAt ?? null
        : current.updatedAt,
    currentPayload: current.payload
  });
  const expectedVersion = buildExpectedVersionFromOrgConfigBaseline(baseline);
  const canExecuteResult = resolveCanExecute({
    acceptedForWrite: prepared.acceptedForWrite,
    ignoredFields: prepared.ignoredFields,
    forbiddenFields: prepared.forbiddenFields
  });

  return {
    generatedAt,
    targetType: params.targetType,
    status: canExecuteResult.canExecute ? "allowed" : "rejected",
    canExecute: canExecuteResult.canExecute,
    reason: canExecuteResult.reason,
    diagnostics: uniqueStrings([
      ...prepared.diagnostics,
      ...canExecuteResult.diagnostics
    ]),
    request: {
      orgId: params.orgId,
      targetType: params.targetType,
      targetKey: "default"
    },
    targetVersion: {
      auditId: targetAudit.id,
      versionLabel: targetAudit.versionLabel,
      versionNumber: targetAudit.versionNumber,
      actionType: targetAudit.actionType,
      createdAt: targetAudit.createdAt
    },
    currentValue: {
      summary: summarizePayload(current.payload)
    },
    targetValue: {
      summary: summarizePayload(targetPayload),
      restoredSummary: summarizePayload(prepared.normalizedPatch),
      normalizedPatch: prepared.normalizedPatch
    },
    restorePlan: {
      acceptedFields: [...prepared.acceptedFields],
      ignoredFields: [...prepared.ignoredFields],
      forbiddenFields: [...prepared.forbiddenFields],
      runtimeImpactSummary: prepared.runtimeImpactSummary
    },
    concurrency: {
      baseline,
      expectedVersion,
      note:
        latestVersion.availability === "not_available"
          ? "Persisted audit baseline is unavailable in current environment; compare token falls back to payload hash."
          : "Use expectedVersion from preview when executing rollback. Execute rejects on baseline drift."
    }
  };
}

export async function executeOrgConfigRollback(params: {
  supabase: DbClient;
  orgId: string;
  targetType: OrgConfigRollbackTargetType;
  selector: OrgConfigRollbackSelector;
  expectedVersion: OrgConfigExpectedVersion | null;
  actorUserId: string;
}): Promise<ExecuteOrgConfigRollbackResult> {
  const expectedVersion = hasOrgConfigExpectedVersion(params.expectedVersion)
    ? params.expectedVersion ?? null
    : null;
  if (!expectedVersion) {
    return {
      status: "rejected",
      reason: "rollback_expected_version_required",
      diagnostics: ["rollback_expected_version_required"],
      preview: buildRejectedPreview({
        orgId: params.orgId,
        targetType: params.targetType,
        status: "rejected",
        reason: "rollback_expected_version_required",
        diagnostics: ["rollback_expected_version_required"]
      }),
      conflict: null,
      writeResult: null
    };
  }

  const preview = await previewOrgConfigRollback({
    supabase: params.supabase,
    orgId: params.orgId,
    targetType: params.targetType,
    selector: params.selector
  });
  if (preview.status === "not_available") {
    return {
      status: "not_available",
      reason: preview.reason,
      diagnostics: [...preview.diagnostics],
      preview,
      conflict: null,
      writeResult: null
    };
  }
  if (!preview.canExecute) {
    return {
      status: "rejected",
      reason: preview.reason ?? "rollback_guard_rejected",
      diagnostics: [...preview.diagnostics],
      preview,
      conflict: null,
      writeResult: null
    };
  }
  if (!preview.concurrency.baseline) {
    return {
      status: "rejected",
      reason: "rollback_concurrency_baseline_missing",
      diagnostics: ["rollback_concurrency_baseline_missing"],
      preview,
      conflict: null,
      writeResult: null
    };
  }
  if (!preview.targetVersion.auditId || !preview.targetVersion.versionLabel || !preview.targetVersion.versionNumber) {
    return {
      status: "rejected",
      reason: "rollback_target_version_metadata_missing",
      diagnostics: ["rollback_target_version_metadata_missing"],
      preview,
      conflict: null,
      writeResult: null
    };
  }

  try {
    assertNoOrgConfigDrift({
      expectedVersion,
      currentBaseline: preview.concurrency.baseline
    });
  } catch (error) {
    if (isOrgConfigDriftConflictError(error)) {
      return {
        status: "conflict",
        reason: "org_config_drift_conflict",
        diagnostics: uniqueStrings([
          ...preview.diagnostics,
          ...error.conflict.diagnostics
        ]),
        preview,
        conflict: error.conflict,
        writeResult: null
      };
    }
    throw error;
  }

  const rollbackSource = buildRollbackSource({
    targetVersion: preview.targetVersion,
    generatedAt: preview.generatedAt
  });

  try {
    if (params.targetType === "org_settings") {
      const writeResult = await governedUpdateOrgSettings({
        supabase: params.supabase,
        orgId: params.orgId,
        actorUserId: params.actorUserId,
        patch: preview.targetValue.normalizedPatch as OrgSettingsGovernancePatch,
        expectedVersion,
        auditActionType: "rollback",
        rollbackSource
      });
      return {
        status: "executed",
        reason: null,
        diagnostics: uniqueStrings([
          ...preview.diagnostics,
          ...writeResult.writeDiagnostics.diagnostics
        ]),
        preview,
        conflict: null,
        writeResult: {
          targetType: "org_settings",
          persistedAuditStatus: writeResult.persistedAudit.status,
          persistedAuditVersionLabel: writeResult.persistedAudit.record?.versionLabel ?? null,
          runtimeImpactSummary: writeResult.writeDiagnostics.runtimeImpactSummary
        }
      };
    }

    if (params.targetType === "org_ai_settings") {
      const writeResult = await governedUpdateOrgAiSettings({
        supabase: params.supabase,
        orgId: params.orgId,
        actorUserId: params.actorUserId,
        patch: preview.targetValue.normalizedPatch as OrgAiSettingsGovernancePatch,
        expectedVersion,
        auditActionType: "rollback",
        rollbackSource
      });
      return {
        status: "executed",
        reason: null,
        diagnostics: uniqueStrings([
          ...preview.diagnostics,
          ...writeResult.writeDiagnostics.diagnostics
        ]),
        preview,
        conflict: null,
        writeResult: {
          targetType: "org_ai_settings",
          persistedAuditStatus: writeResult.persistedAudit.status,
          persistedAuditVersionLabel: writeResult.persistedAudit.record?.versionLabel ?? null,
          runtimeImpactSummary: writeResult.writeDiagnostics.runtimeImpactSummary
        }
      };
    }

    const writeResult = await governedUpdateOrgFeatureFlags({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      patch: preview.targetValue.normalizedPatch as OrgFeatureFlagsGovernancePatch,
      expectedVersion,
      auditActionType: "rollback",
      rollbackSource
    });
    return {
      status: "executed",
      reason: null,
      diagnostics: uniqueStrings([
        ...preview.diagnostics,
        ...writeResult.writeDiagnostics.diagnostics
      ]),
      preview,
      conflict: null,
      writeResult: {
        targetType: "org_feature_flags",
        persistedAuditStatus: writeResult.persistedAudit.status,
        persistedAuditVersionLabel: writeResult.persistedAudit.record?.versionLabel ?? null,
        runtimeImpactSummary: writeResult.writeDiagnostics.runtimeImpactSummary
      }
    };
  } catch (error) {
    if (isOrgConfigDriftConflictError(error)) {
      return {
        status: "conflict",
        reason: "org_config_drift_conflict",
        diagnostics: uniqueStrings([
          ...preview.diagnostics,
          ...error.conflict.diagnostics
        ]),
        preview,
        conflict: error.conflict,
        writeResult: null
      };
    }
    throw error;
  }
}
