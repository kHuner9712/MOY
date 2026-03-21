import {
  OrgConfigWriteRejectedError,
  prepareOrgAiSettingsWrite,
  prepareOrgFeatureFlagsWrite,
  prepareOrgSettingsWrite,
  type OrgAiSettingsGovernancePatch,
  type OrgConfigWriteDiagnostics,
  type OrgFeatureFlagsGovernancePatch,
  type OrgSettingsGovernancePatch
} from "@/lib/org-config-write-governance";
import {
  assertNoOrgConfigDrift,
  buildExpectedVersionFromOrgConfigBaseline,
  buildOrgConfigConcurrencyBaseline,
  hasOrgConfigExpectedVersion,
  type OrgConfigConcurrencyBaseline,
  type OrgConfigExpectedVersion
} from "@/lib/override-concurrency-guard";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  buildOrgConfigAuditRecord,
  getLatestOrgConfigAuditVersion,
  persistOrgConfigAuditRecord,
  type PersistOrgConfigAuditRecordResult
} from "@/services/org-config-audit-service";
import { getOrgAiSettings, updateOrgAiSettings } from "@/services/org-ai-settings-service";
import {
  getOrgFeatureFlagMap,
  getOrgFeatureFlags,
  updateOrgFeatureFlag
} from "@/services/org-feature-service";
import { getOrgSettings, updateOrgSettings } from "@/services/org-settings-service";
import type {
  OrgAiSettings,
  OrgFeatureFlag,
  OrgFeatureKey,
  OrgSettings
} from "@/types/productization";

type DbClient = ServerSupabaseClient;
type OrgConfigAuditActionType = "update" | "rollback";

export interface OrgConfigRollbackSourceSummary {
  sourceAuditId: string;
  sourceVersionLabel: string;
  sourceVersionNumber: number;
  previewGeneratedAt: string | null;
}

interface OrgConfigPayloadSummary {
  payloadKeys: string[];
  payloadPreview: string;
}

export interface OrgConfigGovernedWriteAuditDraft {
  beforeSummary: OrgConfigPayloadSummary | null;
  afterSummary: OrgConfigPayloadSummary | null;
  diagnosticsSummary: Record<string, unknown>;
}

interface OrgConfigGovernedWriteResultBase<TPayload> {
  writeDiagnostics: OrgConfigWriteDiagnostics<Record<string, unknown>>;
  auditDraft: OrgConfigGovernedWriteAuditDraft;
  persistedAudit: PersistOrgConfigAuditRecordResult;
  concurrency: {
    expectedVersion: OrgConfigExpectedVersion | null;
    beforeWrite: OrgConfigConcurrencyBaseline;
    afterWrite: OrgConfigConcurrencyBaseline;
  };
  payload: TPayload;
}

export type GovernedOrgSettingsWriteResult = OrgConfigGovernedWriteResultBase<{
  settings: OrgSettings;
}>;

export type GovernedOrgAiSettingsWriteResult = OrgConfigGovernedWriteResultBase<{
  settings: OrgAiSettings;
}>;

export type GovernedOrgFeatureFlagsWriteResult = OrgConfigGovernedWriteResultBase<{
  featureFlags: OrgFeatureFlag[];
  featureFlagMap: Record<OrgFeatureKey, boolean>;
}>;

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function normalizePath(base: string, child: string): string {
  if (!base) return child;
  return `${base}.${child}`;
}

function collectLeafKeys(value: unknown, path = ""): string[] {
  if (Array.isArray(value)) {
    return path ? [path] : [];
  }
  if (!value || typeof value !== "object") {
    return path ? [path] : [];
  }
  const entries = Object.entries(asObject(value));
  if (entries.length === 0) {
    return path ? [path] : [];
  }
  return entries.flatMap(([key, child]) => collectLeafKeys(child, normalizePath(path, key)));
}

function stableSortObject(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => stableSortObject(item));
  if (!value || typeof value !== "object") return value;
  const entries = Object.entries(asObject(value)).sort(([left], [right]) => left.localeCompare(right));
  return Object.fromEntries(entries.map(([key, child]) => [key, stableSortObject(child)]));
}

function uniqueStrings(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter((item) => item.length > 0)));
}

function summarizePayload(payload: Record<string, unknown> | null): OrgConfigPayloadSummary | null {
  if (!payload) return null;
  const payloadKeys = uniqueStrings(collectLeafKeys(payload)).sort((left, right) => left.localeCompare(right));
  return {
    payloadKeys,
    payloadPreview: JSON.stringify(stableSortObject(payload))
  };
}

function toOrgSettingsPayload(settings: OrgSettings): Record<string, unknown> {
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

function toOrgAiSettingsPayload(settings: OrgAiSettings): Record<string, unknown> {
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

function toFeatureFlagPayload(map: Record<OrgFeatureKey, boolean>): Record<string, unknown> {
  return { ...map };
}

function buildDiagnosticsSummary(params: {
  targetType: "org_settings" | "org_ai_settings" | "org_feature_flags";
  writeDiagnostics: OrgConfigWriteDiagnostics<Record<string, unknown>>;
  expectedVersion: OrgConfigExpectedVersion | null;
  auditActionType: OrgConfigAuditActionType;
  rollbackSource?: OrgConfigRollbackSourceSummary | null;
}): Record<string, unknown> {
  const rollbackSource = params.auditActionType === "rollback" ? params.rollbackSource ?? null : null;
  return {
    targetType: params.targetType,
    actionType: params.auditActionType,
    runtimeImpactSummary: params.writeDiagnostics.runtimeImpactSummary,
    acceptedForWrite: params.writeDiagnostics.acceptedForWrite,
    acceptedFields: [...params.writeDiagnostics.acceptedFields],
    ignoredFields: [...params.writeDiagnostics.ignoredFields],
    forbiddenFields: [...params.writeDiagnostics.forbiddenFields],
    runtimeConsumedFields: [...params.writeDiagnostics.runtimeConsumedFields],
    runtimeIgnoredFields: [...params.writeDiagnostics.runtimeIgnoredFields],
    diagnostics: [...params.writeDiagnostics.diagnostics],
    expectedVersion: params.expectedVersion
      ? {
          compareToken: params.expectedVersion.compareToken ?? null,
          versionLabel: params.expectedVersion.versionLabel ?? null,
          versionNumber: params.expectedVersion.versionNumber ?? null,
          overrideUpdatedAt: params.expectedVersion.overrideUpdatedAt ?? null,
          payloadHash: params.expectedVersion.payloadHash ?? null
        }
      : null,
    ...(rollbackSource ? { rollbackSource } : {})
  };
}

function buildWriteRejectedError(params: {
  targetType: "org_settings" | "org_ai_settings" | "org_feature_flags";
  writeDiagnostics: OrgConfigWriteDiagnostics<Record<string, unknown>>;
}): OrgConfigWriteRejectedError {
  return new OrgConfigWriteRejectedError({
    targetType: params.targetType,
    reason: params.writeDiagnostics.reason ?? "org_config_write_rejected_no_valid_fields",
    diagnostics: [...params.writeDiagnostics.diagnostics],
    acceptedFields: [...params.writeDiagnostics.acceptedFields],
    ignoredFields: [...params.writeDiagnostics.ignoredFields],
    forbiddenFields: [...params.writeDiagnostics.forbiddenFields],
    runtimeImpactSummary: params.writeDiagnostics.runtimeImpactSummary
  });
}

function resolveExpectedVersion(
  expectedVersion: OrgConfigExpectedVersion | null | undefined
): OrgConfigExpectedVersion | null {
  return hasOrgConfigExpectedVersion(expectedVersion) ? expectedVersion ?? null : null;
}

function buildAfterBaseline(params: {
  targetType: "org_settings" | "org_ai_settings" | "org_feature_flags";
  targetKey: string;
  persistedAudit: PersistOrgConfigAuditRecordResult;
  latestBeforeWrite: Awaited<ReturnType<typeof getLatestOrgConfigAuditVersion>>;
  updatedAt: string;
  currentPayload: Record<string, unknown>;
}): OrgConfigConcurrencyBaseline {
  return buildOrgConfigConcurrencyBaseline({
    targetType: params.targetType,
    targetKey: params.targetKey,
    auditAvailability:
      params.persistedAudit.status === "persisted"
        ? "available"
        : params.latestBeforeWrite.availability,
    currentVersionLabel:
      params.persistedAudit.status === "persisted"
        ? params.persistedAudit.record?.versionLabel ?? null
        : params.latestBeforeWrite.item?.versionLabel ?? null,
    currentVersionNumber:
      params.persistedAudit.status === "persisted"
        ? params.persistedAudit.record?.versionNumber ?? null
        : params.latestBeforeWrite.item?.versionNumber ?? null,
    currentUpdatedAt: params.updatedAt,
    currentPayload: params.currentPayload
  });
}

function normalizeRecordPatch<TPatch extends Record<string, unknown>>(patch: TPatch): Record<string, unknown> {
  return asObject(patch);
}

function toRecordWriteDiagnostics<TPatch extends Record<string, unknown>>(
  diagnostics: OrgConfigWriteDiagnostics<TPatch>
): OrgConfigWriteDiagnostics<Record<string, unknown>> {
  return {
    targetType: diagnostics.targetType,
    acceptedForWrite: diagnostics.acceptedForWrite,
    normalizedPatch: normalizeRecordPatch(diagnostics.normalizedPatch),
    acceptedFields: [...diagnostics.acceptedFields],
    ignoredFields: [...diagnostics.ignoredFields],
    forbiddenFields: [...diagnostics.forbiddenFields],
    diagnostics: [...diagnostics.diagnostics],
    reason: diagnostics.reason,
    runtimeConsumedFields: [...diagnostics.runtimeConsumedFields],
    runtimeIgnoredFields: [...diagnostics.runtimeIgnoredFields],
    runtimeImpactSummary: diagnostics.runtimeImpactSummary
  };
}

export async function governedUpdateOrgSettings(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  patch: OrgSettingsGovernancePatch;
  expectedVersion?: OrgConfigExpectedVersion | null;
  auditActionType?: OrgConfigAuditActionType;
  rollbackSource?: OrgConfigRollbackSourceSummary | null;
}): Promise<GovernedOrgSettingsWriteResult> {
  const writeDiagnostics = toRecordWriteDiagnostics(prepareOrgSettingsWrite({ patch: params.patch }));
  if (!writeDiagnostics.acceptedForWrite) {
    throw buildWriteRejectedError({
      targetType: "org_settings",
      writeDiagnostics
    });
  }

  const [before, latestVersion] = await Promise.all([
    getOrgSettings({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    getLatestOrgConfigAuditVersion({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: "org_settings",
      targetKey: "default"
    })
  ]);
  const beforePayload = toOrgSettingsPayload(before);
  const baselineBefore = buildOrgConfigConcurrencyBaseline({
    targetType: "org_settings",
    targetKey: "default",
    auditAvailability: latestVersion.availability,
    currentVersionLabel: latestVersion.item?.versionLabel ?? null,
    currentVersionNumber: latestVersion.item?.versionNumber ?? null,
    currentUpdatedAt: before.updatedAt,
    currentPayload: beforePayload
  });
  const expectedVersion = resolveExpectedVersion(params.expectedVersion);
  if (expectedVersion) {
    assertNoOrgConfigDrift({
      expectedVersion,
      currentBaseline: baselineBefore
    });
  }

  const settings = await updateOrgSettings({
    supabase: params.supabase,
    orgId: params.orgId,
    patch: writeDiagnostics.normalizedPatch as OrgSettingsGovernancePatch
  });
  const afterPayload = toOrgSettingsPayload(settings);

  const diagnosticsSummary = buildDiagnosticsSummary({
    targetType: "org_settings",
    writeDiagnostics,
    expectedVersion,
    auditActionType: params.auditActionType ?? "update",
    rollbackSource: params.rollbackSource ?? null
  });
  const auditDraft: OrgConfigGovernedWriteAuditDraft = {
    beforeSummary: summarizePayload(beforePayload),
    afterSummary: summarizePayload(afterPayload),
    diagnosticsSummary
  };
  const persistedAudit = await persistOrgConfigAuditRecord({
    supabase: params.supabase,
    recordDraft: buildOrgConfigAuditRecord({
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      targetType: "org_settings",
      targetId: settings.id,
      targetKey: "default",
      actionType: params.auditActionType ?? "update",
      beforeSummary: asObject(auditDraft.beforeSummary),
      afterSummary: asObject(auditDraft.afterSummary),
      diagnosticsSummary,
      snapshotSummary: {
        snapshot: {
          snapshotType: "org_settings_normalized_patch_v1",
          targetType: "org_settings",
          targetKey: "default",
          payloadSummary: {
            normalizedPatch: writeDiagnostics.normalizedPatch,
            runtimeImpactSummary: writeDiagnostics.runtimeImpactSummary,
            diagnostics: [...writeDiagnostics.diagnostics]
          }
        },
        ...(params.auditActionType === "rollback" && params.rollbackSource
          ? { rollbackSource: params.rollbackSource }
          : {})
      },
      versionLabelPrefix: "org_settings:default"
    })
  });
  const baselineAfter = buildAfterBaseline({
    targetType: "org_settings",
    targetKey: "default",
    persistedAudit,
    latestBeforeWrite: latestVersion,
    updatedAt: settings.updatedAt,
    currentPayload: afterPayload
  });

  return {
    payload: {
      settings
    },
    writeDiagnostics,
    auditDraft,
    persistedAudit,
    concurrency: {
      expectedVersion,
      beforeWrite: baselineBefore,
      afterWrite: baselineAfter
    }
  };
}

export async function governedUpdateOrgAiSettings(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  patch: OrgAiSettingsGovernancePatch;
  expectedVersion?: OrgConfigExpectedVersion | null;
  auditActionType?: OrgConfigAuditActionType;
  rollbackSource?: OrgConfigRollbackSourceSummary | null;
}): Promise<GovernedOrgAiSettingsWriteResult> {
  const writeDiagnostics = toRecordWriteDiagnostics(prepareOrgAiSettingsWrite({ patch: params.patch }));
  if (!writeDiagnostics.acceptedForWrite) {
    throw buildWriteRejectedError({
      targetType: "org_ai_settings",
      writeDiagnostics
    });
  }

  const [before, latestVersion] = await Promise.all([
    getOrgAiSettings({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    getLatestOrgConfigAuditVersion({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: "org_ai_settings",
      targetKey: "default"
    })
  ]);
  const beforePayload = toOrgAiSettingsPayload(before);
  const baselineBefore = buildOrgConfigConcurrencyBaseline({
    targetType: "org_ai_settings",
    targetKey: "default",
    auditAvailability: latestVersion.availability,
    currentVersionLabel: latestVersion.item?.versionLabel ?? null,
    currentVersionNumber: latestVersion.item?.versionNumber ?? null,
    currentUpdatedAt: before.updatedAt,
    currentPayload: beforePayload
  });
  const expectedVersion = resolveExpectedVersion(params.expectedVersion);
  if (expectedVersion) {
    assertNoOrgConfigDrift({
      expectedVersion,
      currentBaseline: baselineBefore
    });
  }

  const settings = await updateOrgAiSettings({
    supabase: params.supabase,
    orgId: params.orgId,
    patch: writeDiagnostics.normalizedPatch as OrgAiSettingsGovernancePatch
  });
  const afterPayload = toOrgAiSettingsPayload(settings);

  const diagnosticsSummary = buildDiagnosticsSummary({
    targetType: "org_ai_settings",
    writeDiagnostics,
    expectedVersion,
    auditActionType: params.auditActionType ?? "update",
    rollbackSource: params.rollbackSource ?? null
  });
  const auditDraft: OrgConfigGovernedWriteAuditDraft = {
    beforeSummary: summarizePayload(beforePayload),
    afterSummary: summarizePayload(afterPayload),
    diagnosticsSummary
  };
  const persistedAudit = await persistOrgConfigAuditRecord({
    supabase: params.supabase,
    recordDraft: buildOrgConfigAuditRecord({
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      targetType: "org_ai_settings",
      targetId: settings.id,
      targetKey: "default",
      actionType: params.auditActionType ?? "update",
      beforeSummary: asObject(auditDraft.beforeSummary),
      afterSummary: asObject(auditDraft.afterSummary),
      diagnosticsSummary,
      snapshotSummary: {
        snapshot: {
          snapshotType: "org_ai_settings_normalized_patch_v1",
          targetType: "org_ai_settings",
          targetKey: "default",
          payloadSummary: {
            normalizedPatch: writeDiagnostics.normalizedPatch,
            runtimeImpactSummary: writeDiagnostics.runtimeImpactSummary,
            diagnostics: [...writeDiagnostics.diagnostics]
          }
        },
        ...(params.auditActionType === "rollback" && params.rollbackSource
          ? { rollbackSource: params.rollbackSource }
          : {})
      },
      versionLabelPrefix: "org_ai_settings:default"
    })
  });
  const baselineAfter = buildAfterBaseline({
    targetType: "org_ai_settings",
    targetKey: "default",
    persistedAudit,
    latestBeforeWrite: latestVersion,
    updatedAt: settings.updatedAt,
    currentPayload: afterPayload
  });

  return {
    payload: {
      settings
    },
    writeDiagnostics,
    auditDraft,
    persistedAudit,
    concurrency: {
      expectedVersion,
      beforeWrite: baselineBefore,
      afterWrite: baselineAfter
    }
  };
}

export async function governedUpdateOrgFeatureFlags(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  patch: OrgFeatureFlagsGovernancePatch;
  expectedVersion?: OrgConfigExpectedVersion | null;
  auditActionType?: OrgConfigAuditActionType;
  rollbackSource?: OrgConfigRollbackSourceSummary | null;
}): Promise<GovernedOrgFeatureFlagsWriteResult> {
  const writeDiagnostics = toRecordWriteDiagnostics(prepareOrgFeatureFlagsWrite({ patch: params.patch }));
  if (!writeDiagnostics.acceptedForWrite) {
    throw buildWriteRejectedError({
      targetType: "org_feature_flags",
      writeDiagnostics
    });
  }

  const [beforeMap, latestVersion] = await Promise.all([
    getOrgFeatureFlagMap({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    getLatestOrgConfigAuditVersion({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: "org_feature_flags",
      targetKey: "default"
    })
  ]);
  const beforePayload = toFeatureFlagPayload(beforeMap);
  const baselineBefore = buildOrgConfigConcurrencyBaseline({
    targetType: "org_feature_flags",
    targetKey: "default",
    auditAvailability: latestVersion.availability,
    currentVersionLabel: latestVersion.item?.versionLabel ?? null,
    currentVersionNumber: latestVersion.item?.versionNumber ?? null,
    currentUpdatedAt: latestVersion.item?.createdAt ?? null,
    currentPayload: beforePayload
  });
  const expectedVersion = resolveExpectedVersion(params.expectedVersion);
  if (expectedVersion) {
    assertNoOrgConfigDrift({
      expectedVersion,
      currentBaseline: baselineBefore
    });
  }

  await Promise.all(
    Object.entries(writeDiagnostics.normalizedPatch).map(([featureKey, isEnabled]) =>
      updateOrgFeatureFlag({
        supabase: params.supabase,
        orgId: params.orgId,
        featureKey: featureKey as OrgFeatureKey,
        isEnabled: Boolean(isEnabled)
      })
    )
  );
  const [featureFlags, afterMap] = await Promise.all([
    getOrgFeatureFlags({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    getOrgFeatureFlagMap({
      supabase: params.supabase,
      orgId: params.orgId
    })
  ]);
  const afterPayload = toFeatureFlagPayload(afterMap);

  const diagnosticsSummary = buildDiagnosticsSummary({
    targetType: "org_feature_flags",
    writeDiagnostics,
    expectedVersion,
    auditActionType: params.auditActionType ?? "update",
    rollbackSource: params.rollbackSource ?? null
  });
  const auditDraft: OrgConfigGovernedWriteAuditDraft = {
    beforeSummary: summarizePayload(beforePayload),
    afterSummary: summarizePayload(afterPayload),
    diagnosticsSummary
  };
  const persistedAudit = await persistOrgConfigAuditRecord({
    supabase: params.supabase,
    recordDraft: buildOrgConfigAuditRecord({
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      targetType: "org_feature_flags",
      targetId: null,
      targetKey: "default",
      actionType: params.auditActionType ?? "update",
      beforeSummary: asObject(auditDraft.beforeSummary),
      afterSummary: asObject(auditDraft.afterSummary),
      diagnosticsSummary,
      snapshotSummary: {
        snapshot: {
          snapshotType: "org_feature_flags_normalized_patch_v1",
          targetType: "org_feature_flags",
          targetKey: "default",
          payloadSummary: {
            normalizedPatch: writeDiagnostics.normalizedPatch,
            runtimeImpactSummary: writeDiagnostics.runtimeImpactSummary,
            diagnostics: [...writeDiagnostics.diagnostics]
          }
        },
        ...(params.auditActionType === "rollback" && params.rollbackSource
          ? { rollbackSource: params.rollbackSource }
          : {})
      },
      versionLabelPrefix: "org_feature_flags:default"
    })
  });
  const baselineAfter = buildAfterBaseline({
    targetType: "org_feature_flags",
    targetKey: "default",
    persistedAudit,
    latestBeforeWrite: latestVersion,
    updatedAt: persistedAudit.record?.createdAt ?? latestVersion.item?.createdAt ?? new Date().toISOString(),
    currentPayload: afterPayload
  });

  return {
    payload: {
      featureFlags,
      featureFlagMap: afterMap
    },
    writeDiagnostics,
    auditDraft,
    persistedAudit,
    concurrency: {
      expectedVersion,
      beforeWrite: baselineBefore,
      afterWrite: baselineAfter
    }
  };
}

export function buildExpectedVersionFromGovernedResult(result: {
  concurrency: {
    afterWrite: OrgConfigConcurrencyBaseline;
  };
}): Required<OrgConfigExpectedVersion> {
  return buildExpectedVersionFromOrgConfigBaseline(result.concurrency.afterWrite);
}
