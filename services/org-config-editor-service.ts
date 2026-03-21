import {
  buildOrgConfigWriteDiagnosticsSummary,
  prepareOrgAiSettingsWrite,
  prepareOrgFeatureFlagsWrite,
  prepareOrgSettingsWrite,
  type OrgConfigWriteDiagnostics,
  type OrgAiSettingsGovernancePatch,
  type OrgFeatureFlagsGovernancePatch,
  type OrgSettingsGovernancePatch
} from "@/lib/org-config-write-governance";
import {
  buildExpectedVersionFromOrgConfigBaseline,
  buildOrgConfigConcurrencyBaseline,
  type OrgConfigExpectedVersion
} from "@/lib/override-concurrency-guard";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  getLatestOrgConfigAuditVersion,
  listRecentOrgConfigAuditLogs,
  type LatestOrgConfigAuditVersionResult,
  type RecentOrgConfigAuditLogResult
} from "@/services/org-config-audit-service";
import { getOrgAiControlStatus } from "@/services/org-ai-settings-service";
import { getOrgFeatureFlagMap, getOrgFeatureFlags } from "@/services/org-feature-service";
import { getOrgSettings } from "@/services/org-settings-service";
import type {
  OrgAiSettings,
  OrgConfigAuditTargetType,
  OrgFeatureFlag,
  OrgFeatureKey,
  OrgSettings
} from "@/types/productization";

type DbClient = ServerSupabaseClient;

export type OrgConfigEditorTargetType =
  | "org_settings"
  | "org_ai_settings"
  | "org_feature_flags";

export interface OrgConfigEditorSectionAuditItem {
  id: string;
  actionType: string;
  versionLabel: string;
  versionNumber: number;
  createdAt: string;
  actorUserId: string;
  diagnosticsPreview: string[];
  runtimeImpactSummary: string | null;
}

export interface OrgConfigEditorSectionState {
  targetType: OrgConfigEditorTargetType;
  expectedVersion: Required<OrgConfigExpectedVersion>;
  concurrencyBaseline: ReturnType<typeof buildOrgConfigConcurrencyBaseline>;
  latestPersistedVersion: {
    availability: "available" | "empty" | "not_available";
    versionLabel: string | null;
    versionNumber: number | null;
    note: string;
  };
  recentAudits: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: OrgConfigEditorSectionAuditItem[];
  };
  latestDiagnosticsSummary: {
    diagnostics: string[];
    acceptedFields: string[];
    ignoredFields: string[];
    forbiddenFields: string[];
    runtimeImpactSummary: string | null;
  } | null;
  currentValue: Record<string, unknown>;
}

export interface OrgConfigEditorState {
  generatedAt: string;
  sections: {
    orgSettings: OrgConfigEditorSectionState;
    orgAiSettings: OrgConfigEditorSectionState;
    orgFeatureFlags: OrgConfigEditorSectionState;
  };
}

export interface OrgConfigWritePreviewResult {
  targetType: OrgConfigEditorTargetType;
  writeDiagnostics: OrgConfigWriteDiagnostics<Record<string, unknown>>;
  diagnosticsSummary: ReturnType<typeof buildOrgConfigWriteDiagnosticsSummary>;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
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

function toFeatureFlagPayload(flags: Record<OrgFeatureKey, boolean>): Record<string, unknown> {
  return { ...flags };
}

function buildRecentAuditItems(result: RecentOrgConfigAuditLogResult): OrgConfigEditorSectionAuditItem[] {
  return result.items.map((item) => {
    const diagnosticsSummary = asObject(item.diagnosticsSummary);
    return {
      id: item.id,
      actionType: item.actionType,
      versionLabel: item.versionLabel,
      versionNumber: item.versionNumber,
      createdAt: item.createdAt,
      actorUserId: item.actorUserId,
      diagnosticsPreview: asStringArray(diagnosticsSummary.diagnostics).slice(0, 6),
      runtimeImpactSummary:
        typeof diagnosticsSummary.runtimeImpactSummary === "string"
          ? diagnosticsSummary.runtimeImpactSummary
          : null
    };
  });
}

function buildLatestDiagnosticsSummary(result: RecentOrgConfigAuditLogResult): OrgConfigEditorSectionState["latestDiagnosticsSummary"] {
  const latest = result.items[0];
  if (!latest) return null;
  const diagnosticsSummary = asObject(latest.diagnosticsSummary);
  return {
    diagnostics: asStringArray(diagnosticsSummary.diagnostics).slice(0, 10),
    acceptedFields: asStringArray(diagnosticsSummary.acceptedFields),
    ignoredFields: asStringArray(diagnosticsSummary.ignoredFields),
    forbiddenFields: asStringArray(diagnosticsSummary.forbiddenFields),
    runtimeImpactSummary:
      typeof diagnosticsSummary.runtimeImpactSummary === "string"
        ? diagnosticsSummary.runtimeImpactSummary
        : null
  };
}

export function buildOrgConfigEditorSectionState(params: {
  targetType: OrgConfigEditorTargetType;
  targetKey: string;
  currentPayload: Record<string, unknown>;
  updatedAt: string | null;
  latestVersion: LatestOrgConfigAuditVersionResult;
  recentAudits: RecentOrgConfigAuditLogResult;
}): OrgConfigEditorSectionState {
  const baseline = buildOrgConfigConcurrencyBaseline({
    targetType: params.targetType,
    targetKey: params.targetKey,
    auditAvailability: params.latestVersion.availability,
    currentVersionLabel: params.latestVersion.item?.versionLabel ?? null,
    currentVersionNumber: params.latestVersion.item?.versionNumber ?? null,
    currentUpdatedAt: params.updatedAt,
    currentPayload: params.currentPayload
  });

  return {
    targetType: params.targetType,
    expectedVersion: buildExpectedVersionFromOrgConfigBaseline(baseline),
    concurrencyBaseline: baseline,
    latestPersistedVersion: {
      availability: params.latestVersion.availability,
      versionLabel: params.latestVersion.item?.versionLabel ?? null,
      versionNumber: params.latestVersion.item?.versionNumber ?? null,
      note: params.latestVersion.note
    },
    recentAudits: {
      availability: params.recentAudits.availability,
      note: params.recentAudits.note,
      items: buildRecentAuditItems(params.recentAudits)
    },
    latestDiagnosticsSummary: buildLatestDiagnosticsSummary(params.recentAudits),
    currentValue: params.currentPayload
  };
}

function toRecordWriteDiagnostics<TPatch extends Record<string, unknown>>(
  diagnostics: OrgConfigWriteDiagnostics<TPatch>
): OrgConfigWriteDiagnostics<Record<string, unknown>> {
  return {
    targetType: diagnostics.targetType,
    acceptedForWrite: diagnostics.acceptedForWrite,
    normalizedPatch: asObject(diagnostics.normalizedPatch),
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

export async function getOrgConfigEditorState(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<OrgConfigEditorState> {
  const [orgSettings, aiStatus, featureFlagMap, latestOrgSettingsVersion, latestOrgAiSettingsVersion, latestFeatureFlagsVersion, orgSettingsAudits, orgAiSettingsAudits, orgFeatureFlagsAudits] =
    await Promise.all([
      getOrgSettings({
        supabase: params.supabase,
        orgId: params.orgId
      }),
      getOrgAiControlStatus({
        supabase: params.supabase,
        orgId: params.orgId
      }),
      getOrgFeatureFlagMap({
        supabase: params.supabase,
        orgId: params.orgId
      }),
      getLatestOrgConfigAuditVersion({
        supabase: params.supabase,
        orgId: params.orgId,
        targetType: "org_settings",
        targetKey: "default"
      }),
      getLatestOrgConfigAuditVersion({
        supabase: params.supabase,
        orgId: params.orgId,
        targetType: "org_ai_settings",
        targetKey: "default"
      }),
      getLatestOrgConfigAuditVersion({
        supabase: params.supabase,
        orgId: params.orgId,
        targetType: "org_feature_flags",
        targetKey: "default"
      }),
      listRecentOrgConfigAuditLogs({
        supabase: params.supabase,
        orgId: params.orgId,
        targetType: "org_settings",
        targetKey: "default",
        limit: 5
      }),
      listRecentOrgConfigAuditLogs({
        supabase: params.supabase,
        orgId: params.orgId,
        targetType: "org_ai_settings",
        targetKey: "default",
        limit: 5
      }),
      listRecentOrgConfigAuditLogs({
        supabase: params.supabase,
        orgId: params.orgId,
        targetType: "org_feature_flags",
        targetKey: "default",
        limit: 5
      })
    ]);

  return {
    generatedAt: new Date().toISOString(),
    sections: {
      orgSettings: buildOrgConfigEditorSectionState({
        targetType: "org_settings",
        targetKey: "default",
        currentPayload: toOrgSettingsPayload(orgSettings),
        updatedAt: orgSettings.updatedAt,
        latestVersion: latestOrgSettingsVersion,
        recentAudits: orgSettingsAudits
      }),
      orgAiSettings: buildOrgConfigEditorSectionState({
        targetType: "org_ai_settings",
        targetKey: "default",
        currentPayload: toOrgAiSettingsPayload(aiStatus.settings),
        updatedAt: aiStatus.settings.updatedAt,
        latestVersion: latestOrgAiSettingsVersion,
        recentAudits: orgAiSettingsAudits
      }),
      orgFeatureFlags: buildOrgConfigEditorSectionState({
        targetType: "org_feature_flags",
        targetKey: "default",
        currentPayload: toFeatureFlagPayload(featureFlagMap),
        updatedAt: latestFeatureFlagsVersion.item?.createdAt ?? null,
        latestVersion: latestFeatureFlagsVersion,
        recentAudits: orgFeatureFlagsAudits
      })
    }
  };
}

export function previewOrgConfigWrite(params: {
  targetType: OrgConfigEditorTargetType;
  patch: Record<string, unknown>;
}): OrgConfigWritePreviewResult {
  if (params.targetType === "org_settings") {
    const writeDiagnostics = toRecordWriteDiagnostics(prepareOrgSettingsWrite({
      patch: params.patch as OrgSettingsGovernancePatch
    }));
    return {
      targetType: params.targetType,
      writeDiagnostics,
      diagnosticsSummary: buildOrgConfigWriteDiagnosticsSummary(writeDiagnostics)
    };
  }

  if (params.targetType === "org_ai_settings") {
    const writeDiagnostics = toRecordWriteDiagnostics(prepareOrgAiSettingsWrite({
      patch: params.patch as OrgAiSettingsGovernancePatch
    }));
    return {
      targetType: params.targetType,
      writeDiagnostics,
      diagnosticsSummary: buildOrgConfigWriteDiagnosticsSummary(writeDiagnostics)
    };
  }

  const writeDiagnostics = toRecordWriteDiagnostics(prepareOrgFeatureFlagsWrite({
    patch: params.patch as OrgFeatureFlagsGovernancePatch
  }));
  return {
    targetType: params.targetType,
    writeDiagnostics,
    diagnosticsSummary: buildOrgConfigWriteDiagnosticsSummary(writeDiagnostics)
  };
}

export const ORG_CONFIG_EDITOR_SUPPORTED_TARGETS: OrgConfigAuditTargetType[] = [
  "org_settings",
  "org_ai_settings",
  "org_feature_flags"
];
