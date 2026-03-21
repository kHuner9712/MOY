import { canViewManagerWorkspace } from "@/lib/role-capability";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  listRecentOrgConfigAuditLogs,
  type RecentOrgConfigAuditLogResult
} from "@/services/org-config-audit-service";
import {
  getRuntimeExplainDebugPanelData,
  type RuntimeExplainDebugPanelData
} from "@/services/runtime-explain-debug-service";
import type { OrgConfigAuditLog } from "@/types/productization";

type DbClient = ServerSupabaseClient;

type HubTargetType =
  | "org_template_override"
  | "org_settings"
  | "org_ai_settings"
  | "org_feature_flags";

const SUPPORTED_HUB_TARGET_TYPES: HubTargetType[] = [
  "org_template_override",
  "org_settings",
  "org_ai_settings",
  "org_feature_flags"
];

const ROLLBACK_SUPPORTED_TARGET_TYPES: HubTargetType[] = [
  "org_template_override",
  "org_settings",
  "org_ai_settings",
  "org_feature_flags"
];

export interface ConfigOperationsHubAccessInput {
  role?: "sales" | "manager" | null;
  orgRole?: "owner" | "admin" | "manager" | "sales" | "viewer" | null;
}

export interface ConfigOperationsHubRecentChangeItem {
  id: string;
  targetType: HubTargetType;
  targetKey: string | null;
  actionType: string;
  versionLabel: string;
  versionNumber: number;
  createdAt: string;
  actorUserId: string;
  runtimeImpactSummary: string | null;
  diagnosticsPreview: string[];
  hasIgnoredOrForbiddenDiagnostics: boolean;
  hasConflictDiagnostics: boolean;
  rollbackAvailability: "supported" | "not_supported";
}

export interface ConfigOperationsHubStatusSignal {
  status: "fallback" | "not_available" | "degraded";
  domain:
    | "runtime"
    | "template_override"
    | "org_settings"
    | "org_ai_settings"
    | "org_feature_flags"
    | "audit_history";
  detail: string;
}

export interface ConfigOperationsHubDomainCard {
  domainKey: "template_override" | "org_config" | "runtime_debug";
  title: string;
  href: string;
  status: "available" | "degraded" | "not_available";
  summary: string;
  latestChangedAt: string | null;
  rollbackSupportSummary: string;
  note: string;
}

export interface ConfigOperationsHubData {
  generatedAt: string;
  runtimeOverview: {
    resolvedTemplateKey: string | null;
    fallbackProfileKey: string;
    appliedOrgCustomizationKey: string;
    resolvedMode: RuntimeExplainDebugPanelData["runtime"]["resolvedMode"];
    ignoredOverridesCount: number;
    runtimeDiagnosticsCount: number;
  };
  recentChanges: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: ConfigOperationsHubRecentChangeItem[];
  };
  healthSummary: {
    recentChangeCount: number;
    recentIgnoredOrForbiddenCount: number;
    recentConflictCount: number;
    recentRollbackCount: number;
    fallbackOrUnavailableCount: number;
    note: string;
  };
  domainCards: ConfigOperationsHubDomainCard[];
  statusSignals: ConfigOperationsHubStatusSignal[];
  limitations: string[];
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function toNullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function isHubTargetType(value: OrgConfigAuditLog["targetType"]): value is HubTargetType {
  return SUPPORTED_HUB_TARGET_TYPES.includes(value as HubTargetType);
}

function buildRecentChangeItem(item: OrgConfigAuditLog): ConfigOperationsHubRecentChangeItem {
  const diagnosticsSummary = asObject(item.diagnosticsSummary);
  const diagnosticsPreview = asStringArray(diagnosticsSummary.diagnostics).slice(0, 8);
  const hasIgnoredOrForbiddenDiagnostics = diagnosticsPreview.some(
    (line) => line.includes("ignored_") || line.includes("forbidden_")
  );
  const hasConflictDiagnostics = diagnosticsPreview.some((line) =>
    line.includes("concurrency_conflict:")
  );

  return {
    id: item.id,
    targetType: item.targetType as HubTargetType,
    targetKey: item.targetKey,
    actionType: item.actionType,
    versionLabel: item.versionLabel,
    versionNumber: item.versionNumber,
    createdAt: item.createdAt,
    actorUserId: item.actorUserId,
    runtimeImpactSummary: toNullableString(diagnosticsSummary.runtimeImpactSummary),
    diagnosticsPreview,
    hasIgnoredOrForbiddenDiagnostics,
    hasConflictDiagnostics,
    rollbackAvailability: ROLLBACK_SUPPORTED_TARGET_TYPES.includes(item.targetType as HubTargetType)
      ? "supported"
      : "not_supported"
  };
}

function buildStatusSignals(params: {
  runtimePanel: RuntimeExplainDebugPanelData;
  recentChangesAvailability: ConfigOperationsHubData["recentChanges"]["availability"];
  recentChangesNote: string;
}): ConfigOperationsHubStatusSignal[] {
  const signals: ConfigOperationsHubStatusSignal[] = [];
  if (params.runtimePanel.runtime.resolvedMode === "seed_only") {
    signals.push({
      status: "fallback",
      domain: "runtime",
      detail:
        "Runtime is currently resolved in seed_only mode. Some preferences are loaded from fallback defaults."
    });
  }

  if (params.recentChangesAvailability === "not_available") {
    signals.push({
      status: "not_available",
      domain: "audit_history",
      detail: params.recentChangesNote
    });
  }

  if (params.runtimePanel.overrideWriteGovernance.availability === "not_available") {
    signals.push({
      status: "degraded",
      domain: "template_override",
      detail: params.runtimePanel.overrideWriteGovernance.note
    });
  }

  if (params.runtimePanel.orgConfigGovernance.items.some((item) => item.availability === "not_available")) {
    const unavailableTargets = params.runtimePanel.orgConfigGovernance.items
      .filter((item) => item.availability === "not_available")
      .map((item) => item.targetType);
    for (const target of unavailableTargets) {
      signals.push({
        status: "not_available",
        domain: target,
        detail: `${target} governance summary is not available from persisted records.`
      });
    }
  }

  if (params.runtimePanel.orgConfigGovernance.items.some((item) => item.availability === "empty")) {
    const emptyTargets = params.runtimePanel.orgConfigGovernance.items
      .filter((item) => item.availability === "empty")
      .map((item) => item.targetType);
    for (const target of emptyTargets) {
      signals.push({
        status: "degraded",
        domain: target,
        detail: `${target} has no persisted audit history yet.`
      });
    }
  }

  return signals;
}

function buildDomainCards(params: {
  runtimePanel: RuntimeExplainDebugPanelData;
  recentChangeItems: ConfigOperationsHubRecentChangeItem[];
}): ConfigOperationsHubDomainCard[] {
  const latestTemplateOverrideChange =
    params.recentChangeItems.find((item) => item.targetType === "org_template_override") ?? null;
  const latestOrgConfigChange =
    params.recentChangeItems.find((item) =>
      item.targetType === "org_settings" ||
      item.targetType === "org_ai_settings" ||
      item.targetType === "org_feature_flags"
    ) ?? null;

  const orgConfigUnavailable = params.runtimePanel.orgConfigGovernance.items.some(
    (item) => item.availability === "not_available"
  );
  const orgConfigDegraded = params.runtimePanel.orgConfigGovernance.items.some(
    (item) => item.availability === "empty"
  );

  return [
    {
      domainKey: "template_override",
      title: "Template Override",
      href: "/settings/templates",
      status:
        params.runtimePanel.overrideWriteGovernance.availability === "not_available"
          ? "degraded"
          : "available",
      summary:
        latestTemplateOverrideChange
          ? `${latestTemplateOverrideChange.actionType} / ${latestTemplateOverrideChange.versionLabel}`
          : "No recent template override change in current audit window.",
      latestChangedAt: latestTemplateOverrideChange?.createdAt ?? null,
      rollbackSupportSummary: "Rollback preview/execute is supported via template override editor.",
      note: params.runtimePanel.overrideWriteGovernance.note
    },
    {
      domainKey: "org_config",
      title: "Org Config",
      href: "/settings/org-config",
      status: orgConfigUnavailable ? "not_available" : orgConfigDegraded ? "degraded" : "available",
      summary:
        latestOrgConfigChange
          ? `${latestOrgConfigChange.targetType} / ${latestOrgConfigChange.actionType} / ${latestOrgConfigChange.versionLabel}`
          : "No recent org config change in current audit window.",
      latestChangedAt: latestOrgConfigChange?.createdAt ?? null,
      rollbackSupportSummary:
        "Rollback preview is available to manager+, execute is admin-only for org settings / ai settings / feature flags.",
      note: params.runtimePanel.orgConfigGovernance.note
    },
    {
      domainKey: "runtime_debug",
      title: "Runtime Debug",
      href: "/settings/runtime-debug",
      status: params.runtimePanel.runtime.resolvedMode === "seed_only" ? "degraded" : "available",
      summary: `resolvedMode=${params.runtimePanel.runtime.resolvedMode}, ignoredOverrides=${params.runtimePanel.runtime.ignoredOverrides.length}`,
      latestChangedAt: params.runtimePanel.generatedAt,
      rollbackSupportSummary: "Runtime Debug is read-only and does not execute rollback.",
      note:
        "Use Runtime Debug for source explain details and deeper per-consumer diagnostics."
    }
  ];
}

function buildLimitations(params: {
  recentChangesAvailability: ConfigOperationsHubData["recentChanges"]["availability"];
  runtimePanel: RuntimeExplainDebugPanelData;
}): string[] {
  const limitations: string[] = [
    "Hub only shows recent audit summaries and lightweight health counters; field-level diff is not included."
  ];

  if (params.recentChangesAvailability !== "available") {
    limitations.push(
      "Recent change list is currently unavailable or empty, so conflict/rollback counters are approximate."
    );
  }
  if (params.runtimePanel.recentRollbackAudits.availability !== "available") {
    limitations.push(
      "Rollback availability is derived from capability and target type support; full rollback history may be incomplete."
    );
  }
  return limitations;
}

export function canAccessConfigOperationsHub(
  input: ConfigOperationsHubAccessInput | null | undefined
): boolean {
  return canViewManagerWorkspace(input);
}

export function buildConfigOperationsHubDataFromSource(params: {
  runtimePanel: RuntimeExplainDebugPanelData;
  recentAudits: RecentOrgConfigAuditLogResult;
  generatedAt?: string;
}): ConfigOperationsHubData {
  const items = params.recentAudits.items
    .filter((item) => isHubTargetType(item.targetType))
    .map(buildRecentChangeItem);
  const statusSignals = buildStatusSignals({
    runtimePanel: params.runtimePanel,
    recentChangesAvailability: params.recentAudits.availability,
    recentChangesNote: params.recentAudits.note
  });
  const recentIgnoredOrForbiddenCount = items.filter(
    (item) => item.hasIgnoredOrForbiddenDiagnostics
  ).length;
  const recentConflictCount = items.filter((item) => item.hasConflictDiagnostics).length;
  const recentRollbackCount = items.filter((item) => item.actionType === "rollback").length;

  return {
    generatedAt: params.generatedAt ?? new Date().toISOString(),
    runtimeOverview: {
      resolvedTemplateKey: params.runtimePanel.runtime.resolvedTemplateKey,
      fallbackProfileKey: params.runtimePanel.runtime.fallbackProfileKey,
      appliedOrgCustomizationKey: params.runtimePanel.runtime.appliedOrgCustomizationKey,
      resolvedMode: params.runtimePanel.runtime.resolvedMode,
      ignoredOverridesCount: params.runtimePanel.runtime.ignoredOverrides.length,
      runtimeDiagnosticsCount: params.runtimePanel.runtime.diagnostics.length
    },
    recentChanges: {
      availability: params.recentAudits.availability,
      note: params.recentAudits.note,
      items
    },
    healthSummary: {
      recentChangeCount: items.length,
      recentIgnoredOrForbiddenCount,
      recentConflictCount,
      recentRollbackCount,
      fallbackOrUnavailableCount: statusSignals.length,
      note:
        "Health counters are computed from recent persisted audit summaries plus runtime explain availability signals."
    },
    domainCards: buildDomainCards({
      runtimePanel: params.runtimePanel,
      recentChangeItems: items
    }),
    statusSignals,
    limitations: buildLimitations({
      recentChangesAvailability: params.recentAudits.availability,
      runtimePanel: params.runtimePanel
    })
  };
}

export async function getConfigOperationsHubData(params: {
  supabase: DbClient;
  orgId: string;
  recentLimit?: number;
}): Promise<ConfigOperationsHubData> {
  const [runtimePanel, recentAudits] = await Promise.all([
    getRuntimeExplainDebugPanelData({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    listRecentOrgConfigAuditLogs({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: params.recentLimit ?? 12
    })
  ]);

  return buildConfigOperationsHubDataFromSource({
    runtimePanel,
    recentAudits
  });
}

