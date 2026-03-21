import { getDefaultAutomationRuleSeeds } from "@/lib/automation-ops";
import { canViewManagerWorkspace } from "@/lib/role-capability";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  listRecentOrgConfigAuditLogs,
  type RecentOrgConfigAuditLogResult
} from "@/services/org-config-audit-service";
import {
  buildManagerVisibilityRuntimeContext,
  buildPromptAugmentationContext,
  buildRuntimeConfigExplainSnapshot,
  buildResolvedOrgRuntimeConfig,
  resolveAutomationRuleSeedsWithRuntime,
  type ResolvedIndustryTemplateRuntimeContext,
  type RuntimeConfigExplainSnapshot
} from "@/services/template-org-runtime-bridge-service";

type DbClient = ServerSupabaseClient;

interface RuntimeExplainTemplateRunRow {
  id: string;
  created_at: string;
  result_snapshot: Record<string, unknown> | null;
}

export interface RuntimeExplainDebugPanelAccessInput {
  role?: "sales" | "manager" | null;
  orgRole?: "owner" | "admin" | "manager" | "sales" | "viewer" | null;
}

export interface RuntimeOverrideWriteGovernanceSnapshot {
  availability: "available_from_template_apply_snapshot" | "not_available";
  latestRunId: string | null;
  latestRunAt: string | null;
  summary: Record<string, unknown> | null;
  diagnosticsCount: number;
  auditDraftCount: number;
  note: string;
}

export interface RuntimeExplainDebugPanelData {
  generatedAt: string;
  runtime: {
    resolvedTemplateKey: string | null;
    fallbackProfileKey: string;
    appliedOrgCustomizationKey: string;
    resolvedMode: RuntimeConfigExplainSnapshot["resolvedMode"];
    sourcePriority: RuntimeConfigExplainSnapshot["sourcePriority"];
    keyFieldSources: RuntimeConfigExplainSnapshot["keyFieldSources"];
    persistedUsage: RuntimeConfigExplainSnapshot["persistedUsage"];
    appliedOverrides: RuntimeConfigExplainSnapshot["appliedOverrides"];
    ignoredOverrides: RuntimeConfigExplainSnapshot["ignoredOverrides"];
    diagnostics: RuntimeConfigExplainSnapshot["diagnostics"];
  };
  effectivePreferenceSummary: {
    managerFocusMetrics: string[];
    reportMetricFilters: string[];
    executiveMetricFilters: string[];
    recommendedActionTitles: string[];
    onboardingPreferredChecklistKeys: string[];
    onboardingHints: string[];
    defaultDateRangeDays: number | null;
  };
  consumerExplainSummary: {
    onboarding: {
      promptAugmentationEnabled: boolean;
      promptAugmentationPreview: string | null;
      preferredChecklistKeys: string[];
      hintPreview: string[];
      explainSource: RuntimeConfigExplainSnapshot["keyFieldSources"]["promptPreference"];
    };
    automationSeed: {
      resolutionSource: RuntimeConfigExplainSnapshot["keyFieldSources"]["thresholdPreferences"];
      resolvedMode: RuntimeConfigExplainSnapshot["resolvedMode"];
      ignoredOverrideCount: number;
      totalSeedCount: number;
      disabledSeedCount: number;
      sample: Array<{
        ruleKey: string;
        isEnabled: boolean;
        conditionKeys: string[];
      }>;
    };
    executiveReport: {
      fallbackToBase: boolean;
      managerFocusMetricPriority: string[];
      reportMetricPriority: string[];
      recommendedActionPriority: string[];
      defaultDateRangeDays: number | null;
    };
  };
  overrideWriteGovernance: RuntimeOverrideWriteGovernanceSnapshot;
  recentPersistedAudits: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: Array<{
      id: string;
      createdAt: string;
      actorUserId: string;
      targetType: string;
      targetKey: string | null;
      actionType: string;
      versionLabel: string;
      versionNumber: number;
      runtimeImpactSummary: string | null;
      forbiddenForRuntime: boolean | null;
      ignoredByRuntime: boolean | null;
      diagnosticsPreview: string[];
      hasConcurrencyConflictDiagnostic: boolean;
    }>;
  };
  recentRollbackAudits: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: Array<{
      id: string;
      createdAt: string;
      actorUserId: string;
      targetType: string;
      targetKey: string | null;
      versionLabel: string;
      versionNumber: number;
      restoredFromAuditId: string | null;
      restoredFromVersionLabel: string | null;
      restoredFromVersionNumber: number | null;
      diagnosticsPreview: string[];
      hasConcurrencyConflictDiagnostic: boolean;
    }>;
  };
  concurrencyGuard: {
    latestConflictReason: string | null;
    latestConflictAt: string | null;
    note: string;
  };
  orgConfigGovernance: {
    note: string;
    items: Array<{
      targetType: "org_settings" | "org_ai_settings" | "org_feature_flags";
      availability: "available" | "empty" | "not_available";
      hasPersistedAudit: boolean;
      latestChangedAt: string | null;
      latestActionType: string | null;
      latestVersionLabel: string | null;
      latestVersionNumber: number | null;
      runtimeImpactSummary: string | null;
      diagnosticsPreview: string[];
      ignoredOrForbiddenDiagnosticsCount: number;
      conflictDiagnosticsCount: number;
      note: string;
    }>;
  };
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  if (!Array.isArray(value)) return [];
  return value;
}

function asStringArray(value: unknown): string[] {
  return asArray(value).filter((item): item is string => typeof item === "string");
}

function buildUnavailableGovernance(note: string): RuntimeOverrideWriteGovernanceSnapshot {
  return {
    availability: "not_available",
    latestRunId: null,
    latestRunAt: null,
    summary: null,
    diagnosticsCount: 0,
    auditDraftCount: 0,
    note
  };
}

function buildUnavailablePersistedAudits(note: string): RuntimeExplainDebugPanelData["recentPersistedAudits"] {
  return {
    availability: "not_available",
    note,
    items: []
  };
}

function buildUnavailableRollbackAudits(note: string): RuntimeExplainDebugPanelData["recentRollbackAudits"] {
  return {
    availability: "not_available",
    note,
    items: []
  };
}

function toNullableBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  return null;
}

function toNullableNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function buildRecentPersistedAudits(result: RecentOrgConfigAuditLogResult): RuntimeExplainDebugPanelData["recentPersistedAudits"] {
  return {
    availability: result.availability,
    note: result.note,
    items: result.items.map((item) => {
      const diagnosticsSummary = asObject(item.diagnosticsSummary);
      const diagnosticsPreview = asStringArray(diagnosticsSummary.diagnostics).slice(0, 6);
      const hasConcurrencyConflictDiagnostic = diagnosticsPreview.some((line) =>
        line.includes("concurrency_conflict:")
      );
      return {
        id: item.id,
        createdAt: item.createdAt,
        actorUserId: item.actorUserId,
        targetType: item.targetType,
        targetKey: item.targetKey,
        actionType: item.actionType,
        versionLabel: item.versionLabel,
        versionNumber: item.versionNumber,
        runtimeImpactSummary:
          typeof diagnosticsSummary.runtimeImpactSummary === "string"
            ? diagnosticsSummary.runtimeImpactSummary
            : null,
        forbiddenForRuntime: toNullableBoolean(diagnosticsSummary.forbiddenForRuntime),
        ignoredByRuntime: toNullableBoolean(diagnosticsSummary.ignoredByRuntime),
        diagnosticsPreview,
        hasConcurrencyConflictDiagnostic
      };
    })
  };
}

function toNullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function buildRecentRollbackAudits(result: RecentOrgConfigAuditLogResult): RuntimeExplainDebugPanelData["recentRollbackAudits"] {
  return {
    availability: result.availability,
    note: result.note,
    items: result.items.map((item) => {
      const diagnosticsSummary = asObject(item.diagnosticsSummary);
      const rollbackSource = asObject(diagnosticsSummary.rollbackSource);
      const diagnosticsPreview = asStringArray(diagnosticsSummary.diagnostics).slice(0, 6);
      const hasConcurrencyConflictDiagnostic = diagnosticsPreview.some((line) =>
        line.includes("concurrency_conflict:")
      );
      return {
        id: item.id,
        createdAt: item.createdAt,
        actorUserId: item.actorUserId,
        targetType: item.targetType,
        targetKey: item.targetKey,
        versionLabel: item.versionLabel,
        versionNumber: item.versionNumber,
        restoredFromAuditId: toNullableString(rollbackSource.sourceAuditId),
        restoredFromVersionLabel: toNullableString(rollbackSource.sourceVersionLabel),
        restoredFromVersionNumber: toNullableNumber(rollbackSource.sourceVersionNumber),
        diagnosticsPreview,
        hasConcurrencyConflictDiagnostic
      };
    })
  };
}

function buildConcurrencyGuardSummary(params: {
  recentPersistedAudits: RuntimeExplainDebugPanelData["recentPersistedAudits"];
  recentRollbackAudits: RuntimeExplainDebugPanelData["recentRollbackAudits"];
}): RuntimeExplainDebugPanelData["concurrencyGuard"] {
  const candidates = [
    ...params.recentRollbackAudits.items,
    ...params.recentPersistedAudits.items
  ];
  const latestConflict = candidates.find((item) => item.hasConcurrencyConflictDiagnostic);
  if (!latestConflict) {
    return {
      latestConflictReason: null,
      latestConflictAt: null,
      note:
        "No persisted drift/conflict diagnostics found in recent audit logs. Current write/rollback conflict details are returned inline by APIs."
    };
  }

  const reason =
    latestConflict.diagnosticsPreview.find((item) => item.includes("concurrency_conflict:")) ?? null;
  return {
    latestConflictReason: reason,
    latestConflictAt: latestConflict.createdAt,
    note: "Latest persisted drift/conflict signal is detected from diagnostics preview."
  };
}

function buildOrgConfigGovernanceItem(params: {
  targetType: "org_settings" | "org_ai_settings" | "org_feature_flags";
  result: RecentOrgConfigAuditLogResult;
}): RuntimeExplainDebugPanelData["orgConfigGovernance"]["items"][number] {
  const latest = params.result.items[0] ?? null;
  if (!latest) {
    return {
      targetType: params.targetType,
      availability: params.result.availability,
      hasPersistedAudit: false,
      latestChangedAt: null,
      latestActionType: null,
      latestVersionLabel: null,
      latestVersionNumber: null,
      runtimeImpactSummary: null,
      diagnosticsPreview: [],
      ignoredOrForbiddenDiagnosticsCount: 0,
      conflictDiagnosticsCount: 0,
      note: params.result.note
    };
  }

  const diagnosticsSummary = asObject(latest.diagnosticsSummary);
  const diagnosticsPreview = asStringArray(diagnosticsSummary.diagnostics).slice(0, 6);
  const ignoredOrForbiddenDiagnosticsCount = diagnosticsPreview.filter(
    (item) => item.includes("ignored_") || item.includes("forbidden_")
  ).length;
  const conflictDiagnosticsCount = diagnosticsPreview.filter((item) =>
    item.includes("concurrency_conflict:")
  ).length;

  return {
    targetType: params.targetType,
    availability: params.result.availability,
    hasPersistedAudit: true,
    latestChangedAt: latest.createdAt,
    latestActionType: latest.actionType,
    latestVersionLabel: latest.versionLabel,
    latestVersionNumber: latest.versionNumber,
    runtimeImpactSummary:
      typeof diagnosticsSummary.runtimeImpactSummary === "string"
        ? diagnosticsSummary.runtimeImpactSummary
        : null,
    diagnosticsPreview,
    ignoredOrForbiddenDiagnosticsCount,
    conflictDiagnosticsCount,
    note: params.result.note
  };
}

export function buildOrgConfigGovernanceSummary(params: {
  orgSettingsAudits: RecentOrgConfigAuditLogResult;
  orgAiSettingsAudits: RecentOrgConfigAuditLogResult;
  orgFeatureFlagsAudits: RecentOrgConfigAuditLogResult;
}): RuntimeExplainDebugPanelData["orgConfigGovernance"] {
  const items = [
    buildOrgConfigGovernanceItem({
      targetType: "org_settings",
      result: params.orgSettingsAudits
    }),
    buildOrgConfigGovernanceItem({
      targetType: "org_ai_settings",
      result: params.orgAiSettingsAudits
    }),
    buildOrgConfigGovernanceItem({
      targetType: "org_feature_flags",
      result: params.orgFeatureFlagsAudits
    })
  ];

  return {
    note:
      "This block summarizes persisted governance coverage for org settings / ai settings / feature flags. It is sourced from recent org_config_audit_logs records.",
    items
  };
}

function buildOverrideWriteGovernanceFromRun(run: RuntimeExplainTemplateRunRow): RuntimeOverrideWriteGovernanceSnapshot {
  const resultSnapshot = asObject(run.result_snapshot);
  const governance = asObject(resultSnapshot.override_write_governance);
  const summary = asObject(governance.summary);
  const diagnosticsCount = asArray(governance.diagnostics).length;
  const auditDraftCount = asArray(governance.auditDrafts).length;
  return {
    availability: "available_from_template_apply_snapshot",
    latestRunId: run.id,
    latestRunAt: run.created_at,
    summary: Object.keys(summary).length > 0 ? summary : null,
    diagnosticsCount,
    auditDraftCount,
    note:
      "Governance summary is read from latest template apply snapshot. Direct override API writes are not yet fully queryable as historical audit records."
  };
}

export function canAccessRuntimeExplainDebugPanel(
  input: RuntimeExplainDebugPanelAccessInput | null | undefined
): boolean {
  return canViewManagerWorkspace(input);
}

export function buildRuntimeExplainDebugPanelDataFromContext(params: {
  context: ResolvedIndustryTemplateRuntimeContext;
  overrideWriteGovernance?: RuntimeOverrideWriteGovernanceSnapshot | null;
  recentPersistedAudits?: RuntimeExplainDebugPanelData["recentPersistedAudits"] | null;
  recentRollbackAudits?: RuntimeExplainDebugPanelData["recentRollbackAudits"] | null;
  orgConfigGovernance?: RuntimeExplainDebugPanelData["orgConfigGovernance"] | null;
  generatedAt?: string;
}): RuntimeExplainDebugPanelData {
  const runtimeConfigExplain = buildRuntimeConfigExplainSnapshot(params.context);
  const managerVisibilityContext = buildManagerVisibilityRuntimeContext({
    context: params.context
  });
  const automationSeeds = resolveAutomationRuleSeedsWithRuntime({
    baseSeeds: getDefaultAutomationRuleSeeds(),
    context: params.context
  });
  const automationDebug = automationSeeds[0]?.resolutionDebug;
  const onboardingPromptAugmentation = buildPromptAugmentationContext({
    scenario: "onboarding_recommendation",
    context: params.context
  });
  const merged = params.context.merged;
  const fallbackToBase = params.context.fallbackToBase || !merged;
  const recentPersistedAudits =
    params.recentPersistedAudits ??
    buildUnavailablePersistedAudits(
      "Persisted audit table is not available in current environment."
    );
  const recentRollbackAudits =
    params.recentRollbackAudits ??
    buildUnavailableRollbackAudits(
      "Persisted rollback history is not available in current environment."
    );

  return {
    generatedAt: params.generatedAt ?? new Date().toISOString(),
    runtime: {
      resolvedTemplateKey: runtimeConfigExplain.resolvedTemplateKey,
      fallbackProfileKey: runtimeConfigExplain.fallbackProfileKey,
      appliedOrgCustomizationKey: runtimeConfigExplain.appliedOrgCustomizationKey,
      resolvedMode: runtimeConfigExplain.resolvedMode,
      sourcePriority: runtimeConfigExplain.sourcePriority,
      keyFieldSources: runtimeConfigExplain.keyFieldSources,
      persistedUsage: runtimeConfigExplain.persistedUsage,
      appliedOverrides: runtimeConfigExplain.appliedOverrides,
      ignoredOverrides: runtimeConfigExplain.ignoredOverrides,
      diagnostics: runtimeConfigExplain.diagnostics
    },
    effectivePreferenceSummary: {
      managerFocusMetrics: merged?.effectiveManagerFocusMetrics ?? [],
      reportMetricFilters: merged?.effectiveReportingPreference.managerMetricFilters ?? [],
      executiveMetricFilters: merged?.effectiveReportingPreference.executiveMetricFilters ?? [],
      recommendedActionTitles:
        merged?.effectiveRecommendedActionLibrary.map((item) => item.title).slice(0, 6) ?? [],
      onboardingPreferredChecklistKeys: params.context.orgCustomization.onboardingPreferences.preferredChecklistKeys,
      onboardingHints: merged?.effectiveOnboardingHints ?? [],
      defaultDateRangeDays: merged?.effectiveReportingPreference.defaultDateRangeDays ?? null
    },
    consumerExplainSummary: {
      onboarding: {
        promptAugmentationEnabled: Boolean(onboardingPromptAugmentation),
        promptAugmentationPreview: onboardingPromptAugmentation
          ? onboardingPromptAugmentation.split("\n").slice(0, 4).join("\n")
          : null,
        preferredChecklistKeys: params.context.orgCustomization.onboardingPreferences.preferredChecklistKeys,
        hintPreview: (merged?.effectiveOnboardingHints ?? []).slice(0, 5),
        explainSource: runtimeConfigExplain.keyFieldSources.promptPreference
      },
      automationSeed: {
        resolutionSource:
          automationDebug?.source ?? runtimeConfigExplain.keyFieldSources.thresholdPreferences,
        resolvedMode: automationDebug?.resolvedMode ?? runtimeConfigExplain.resolvedMode,
        ignoredOverrideCount:
          automationDebug?.ignoredOverrideCount ?? runtimeConfigExplain.ignoredOverrides.length,
        totalSeedCount: automationSeeds.length,
        disabledSeedCount: automationSeeds.filter((item) => !item.isEnabled).length,
        sample: automationSeeds.slice(0, 6).map((item) => ({
          ruleKey: item.seed.ruleKey,
          isEnabled: item.isEnabled,
          conditionKeys: Object.keys(item.seed.conditionsJson)
        }))
      },
      executiveReport: {
        fallbackToBase,
        managerFocusMetricPriority: managerVisibilityContext.managerFocusMetricPriority,
        reportMetricPriority: managerVisibilityContext.reportMetricPriority,
        recommendedActionPriority: managerVisibilityContext.recommendedActionPriority,
        defaultDateRangeDays: managerVisibilityContext.defaultDateRangeDays
      }
    },
    overrideWriteGovernance:
      params.overrideWriteGovernance ??
      buildUnavailableGovernance(
        "No persisted governance snapshot is currently available. Runtime explain and governance capability status are shown instead."
      ),
    recentPersistedAudits,
    recentRollbackAudits,
    concurrencyGuard: buildConcurrencyGuardSummary({
      recentPersistedAudits,
      recentRollbackAudits
    }),
    orgConfigGovernance:
      params.orgConfigGovernance ?? {
        note:
          "Governance summary for org settings / ai settings / feature flags is not available in current data context.",
        items: [
          {
            targetType: "org_settings",
            availability: "not_available",
            hasPersistedAudit: false,
            latestChangedAt: null,
            latestActionType: null,
            latestVersionLabel: null,
            latestVersionNumber: null,
            runtimeImpactSummary: null,
            diagnosticsPreview: [],
            ignoredOrForbiddenDiagnosticsCount: 0,
            conflictDiagnosticsCount: 0,
            note: "Not available in current context."
          },
          {
            targetType: "org_ai_settings",
            availability: "not_available",
            hasPersistedAudit: false,
            latestChangedAt: null,
            latestActionType: null,
            latestVersionLabel: null,
            latestVersionNumber: null,
            runtimeImpactSummary: null,
            diagnosticsPreview: [],
            ignoredOrForbiddenDiagnosticsCount: 0,
            conflictDiagnosticsCount: 0,
            note: "Not available in current context."
          },
          {
            targetType: "org_feature_flags",
            availability: "not_available",
            hasPersistedAudit: false,
            latestChangedAt: null,
            latestActionType: null,
            latestVersionLabel: null,
            latestVersionNumber: null,
            runtimeImpactSummary: null,
            diagnosticsPreview: [],
            ignoredOrForbiddenDiagnosticsCount: 0,
            conflictDiagnosticsCount: 0,
            note: "Not available in current context."
          }
        ]
      }
  };
}

async function loadLatestOverrideWriteGovernance(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<RuntimeOverrideWriteGovernanceSnapshot> {
  const runsRes = await (params.supabase as any)
    .from("template_application_runs")
    .select("id,created_at,result_snapshot")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(20);

  if (runsRes.error) {
    return buildUnavailableGovernance(
      "Unable to read template application snapshot history in current environment. Governance history remains unavailable."
    );
  }

  const rows = ((runsRes.data ?? []) as RuntimeExplainTemplateRunRow[]).filter((item) => item.result_snapshot);
  for (const row of rows) {
    const governance = asObject(asObject(row.result_snapshot).override_write_governance);
    if (Object.keys(governance).length === 0) continue;
    return buildOverrideWriteGovernanceFromRun(row);
  }

  return buildUnavailableGovernance(
    "No template apply snapshot currently contains override_write_governance. Historical write diagnostics are not yet fully persisted."
  );
}

export async function getRuntimeExplainDebugPanelData(params: {
  supabase: DbClient;
  orgId: string;
  templateKey?: string | null;
}): Promise<RuntimeExplainDebugPanelData> {
  const [
    runtimeContext,
    overrideWriteGovernance,
    recentPersistedAudits,
    recentRollbackAudits,
    orgSettingsAudits,
    orgAiSettingsAudits,
    orgFeatureFlagsAudits
  ] = await Promise.all([
    buildResolvedOrgRuntimeConfig({
      supabase: params.supabase,
      orgId: params.orgId,
      templateKey: params.templateKey ?? null
    }),
    loadLatestOverrideWriteGovernance({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    listRecentOrgConfigAuditLogs({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: 5
    }),
    listRecentOrgConfigAuditLogs({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: "org_template_override",
      actionType: "rollback",
      limit: 5
    }),
    listRecentOrgConfigAuditLogs({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: "org_settings",
      limit: 3
    }),
    listRecentOrgConfigAuditLogs({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: "org_ai_settings",
      limit: 3
    }),
    listRecentOrgConfigAuditLogs({
      supabase: params.supabase,
      orgId: params.orgId,
      targetType: "org_feature_flags",
      limit: 3
    })
  ]);

  return buildRuntimeExplainDebugPanelDataFromContext({
    context: runtimeContext,
    overrideWriteGovernance,
    recentPersistedAudits: buildRecentPersistedAudits(recentPersistedAudits),
    recentRollbackAudits: buildRecentRollbackAudits(recentRollbackAudits),
    orgConfigGovernance: buildOrgConfigGovernanceSummary({
      orgSettingsAudits,
      orgAiSettingsAudits,
      orgFeatureFlagsAudits
    })
  });
}
