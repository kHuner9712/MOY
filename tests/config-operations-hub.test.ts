import assert from "node:assert/strict";

import {
  buildConfigOperationsHubDataFromSource,
  canAccessConfigOperationsHub
} from "../services/config-operations-hub-service";
import type { RecentOrgConfigAuditLogResult } from "../services/org-config-audit-service";
import type { RuntimeExplainDebugPanelData } from "../services/runtime-explain-debug-service";
import type { OrgConfigAuditLog } from "../types/productization";

function buildAuditLog(overrides: Partial<OrgConfigAuditLog>): OrgConfigAuditLog {
  return {
    id: "audit-1",
    orgId: "org-1",
    actorUserId: "user-1",
    targetType: "org_settings",
    targetId: "target-1",
    targetKey: "default",
    actionType: "update",
    beforeSummary: {},
    afterSummary: {},
    diagnosticsSummary: {
      diagnostics: [],
      runtimeImpactSummary: "runtime_consumed"
    },
    versionNumber: 1,
    versionLabel: "org_settings:default:v1",
    snapshotSummary: {},
    createdAt: "2026-03-22T10:00:00.000Z",
    ...overrides
  };
}

function buildRuntimePanel(overrides?: {
  generatedAt?: string;
  runtime?: Partial<RuntimeExplainDebugPanelData["runtime"]>;
  overrideWriteGovernance?: Partial<RuntimeExplainDebugPanelData["overrideWriteGovernance"]>;
  recentPersistedAudits?: Partial<RuntimeExplainDebugPanelData["recentPersistedAudits"]>;
  recentRollbackAudits?: Partial<RuntimeExplainDebugPanelData["recentRollbackAudits"]>;
  concurrencyGuard?: Partial<RuntimeExplainDebugPanelData["concurrencyGuard"]>;
  orgConfigGovernance?: Partial<RuntimeExplainDebugPanelData["orgConfigGovernance"]>;
}): RuntimeExplainDebugPanelData {
  const base: RuntimeExplainDebugPanelData = {
    generatedAt: "2026-03-22T10:05:00.000Z",
    runtime: {
      resolvedTemplateKey: "saas_subscription",
      fallbackProfileKey: "generic_b2b_growth",
      appliedOrgCustomizationKey: "default",
      resolvedMode: "persisted_preferred",
      sourcePriority: ["persisted_source", "fallback_profile", "seed_default"],
      keyFieldSources: {
        resolvedTemplateKey: "persisted_source",
        orgCustomizationProfile: "persisted_source",
        thresholdPreferences: "persisted_source",
        promptPreference: "persisted_source",
        featurePreferences: "persisted_source"
      },
      persistedUsage: {
        assignment: true,
        overrides: true,
        orgSettings: true,
        orgAiSettings: true,
        orgFeatureFlags: true,
        automationRules: true
      },
      appliedOverrides: [],
      ignoredOverrides: [],
      diagnostics: []
    },
    effectivePreferenceSummary: {
      managerFocusMetrics: [],
      reportMetricFilters: [],
      executiveMetricFilters: [],
      recommendedActionTitles: [],
      onboardingPreferredChecklistKeys: [],
      onboardingHints: [],
      defaultDateRangeDays: null
    },
    consumerExplainSummary: {
      onboarding: {
        promptAugmentationEnabled: false,
        promptAugmentationPreview: null,
        preferredChecklistKeys: [],
        hintPreview: [],
        explainSource: "persisted_source"
      },
      automationSeed: {
        resolutionSource: "persisted_source",
        resolvedMode: "persisted_preferred",
        ignoredOverrideCount: 0,
        totalSeedCount: 0,
        disabledSeedCount: 0,
        sample: []
      },
      executiveReport: {
        fallbackToBase: false,
        managerFocusMetricPriority: [],
        reportMetricPriority: [],
        recommendedActionPriority: [],
        defaultDateRangeDays: null
      }
    },
    overrideWriteGovernance: {
      availability: "available_from_template_apply_snapshot",
      latestRunId: "run-1",
      latestRunAt: "2026-03-22T09:00:00.000Z",
      summary: {},
      diagnosticsCount: 0,
      auditDraftCount: 0,
      note: "available"
    },
    recentPersistedAudits: {
      availability: "available",
      note: "available",
      items: []
    },
    recentRollbackAudits: {
      availability: "available",
      note: "available",
      items: []
    },
    concurrencyGuard: {
      latestConflictReason: null,
      latestConflictAt: null,
      note: "none"
    },
    orgConfigGovernance: {
      note: "available",
      items: [
        {
          targetType: "org_settings",
          availability: "available",
          hasPersistedAudit: true,
          latestChangedAt: "2026-03-22T10:00:00.000Z",
          latestActionType: "update",
          latestVersionLabel: "org_settings:default:v1",
          latestVersionNumber: 1,
          runtimeImpactSummary: "runtime_consumed",
          diagnosticsPreview: [],
          ignoredOrForbiddenDiagnosticsCount: 0,
          conflictDiagnosticsCount: 0,
          note: "available"
        },
        {
          targetType: "org_ai_settings",
          availability: "available",
          hasPersistedAudit: true,
          latestChangedAt: "2026-03-22T10:00:00.000Z",
          latestActionType: "update",
          latestVersionLabel: "org_ai_settings:default:v1",
          latestVersionNumber: 1,
          runtimeImpactSummary: "runtime_consumed",
          diagnosticsPreview: [],
          ignoredOrForbiddenDiagnosticsCount: 0,
          conflictDiagnosticsCount: 0,
          note: "available"
        },
        {
          targetType: "org_feature_flags",
          availability: "available",
          hasPersistedAudit: true,
          latestChangedAt: "2026-03-22T10:00:00.000Z",
          latestActionType: "update",
          latestVersionLabel: "org_feature_flags:default:v1",
          latestVersionNumber: 1,
          runtimeImpactSummary: "runtime_consumed",
          diagnosticsPreview: [],
          ignoredOrForbiddenDiagnosticsCount: 0,
          conflictDiagnosticsCount: 0,
          note: "available"
        }
      ]
    }
  };

  return {
    ...base,
    ...(overrides?.generatedAt ? { generatedAt: overrides.generatedAt } : {}),
    runtime: {
      ...base.runtime,
      ...overrides?.runtime
    },
    overrideWriteGovernance: {
      ...base.overrideWriteGovernance,
      ...overrides?.overrideWriteGovernance
    },
    recentPersistedAudits: {
      ...base.recentPersistedAudits,
      ...overrides?.recentPersistedAudits
    },
    recentRollbackAudits: {
      ...base.recentRollbackAudits,
      ...overrides?.recentRollbackAudits
    },
    concurrencyGuard: {
      ...base.concurrencyGuard,
      ...overrides?.concurrencyGuard
    },
    orgConfigGovernance: {
      ...base.orgConfigGovernance,
      ...overrides?.orgConfigGovernance
    }
  };
}

export function runConfigOperationsHubTests(logPass: (name: string) => void): void {
  assert.equal(canAccessConfigOperationsHub({ role: "manager", orgRole: "owner" }), true);
  assert.equal(canAccessConfigOperationsHub({ role: "manager", orgRole: "admin" }), true);
  assert.equal(canAccessConfigOperationsHub({ role: "manager", orgRole: "manager" }), true);
  assert.equal(canAccessConfigOperationsHub({ role: "sales", orgRole: "sales" }), false);
  assert.equal(canAccessConfigOperationsHub({ role: "sales", orgRole: "viewer" }), false);
  logPass("config operations hub: permission boundary manager+ only");

  const availableRecentAudits: RecentOrgConfigAuditLogResult = {
    availability: "available",
    note: "latest 3",
    items: [
      buildAuditLog({
        id: "audit-template",
        targetType: "org_template_override",
        targetKey: "template-1:alert_rules",
        actionType: "update",
        versionLabel: "org_template_override:template-1:alert_rules:v4",
        versionNumber: 4,
        diagnosticsSummary: {
          diagnostics: ["ignored_override:customer_stages", "forbidden_override:opportunity_stages"],
          runtimeImpactSummary: "runtime_partial_ignored"
        }
      }),
      buildAuditLog({
        id: "audit-settings",
        targetType: "org_settings",
        targetKey: "default",
        actionType: "rollback",
        versionLabel: "org_settings:default:v7",
        versionNumber: 7,
        diagnosticsSummary: {
          diagnostics: [
            "concurrency_conflict:compare_token_mismatch:expected=a:current=b",
            "rollback_restored_from:org_settings:default:v6"
          ],
          runtimeImpactSummary: "runtime_consumed"
        }
      }),
      buildAuditLog({
        id: "audit-ai",
        targetType: "org_ai_settings",
        targetKey: "default",
        actionType: "update",
        versionLabel: "org_ai_settings:default:v2",
        versionNumber: 2,
        diagnosticsSummary: {
          diagnostics: ["accepted_fields:modelDefault"],
          runtimeImpactSummary: "runtime_consumed"
        }
      })
    ]
  };
  const availableHub = buildConfigOperationsHubDataFromSource({
    runtimePanel: buildRuntimePanel(),
    recentAudits: availableRecentAudits
  });
  assert.equal(availableHub.recentChanges.availability, "available");
  assert.equal(availableHub.recentChanges.items.length, 3);
  assert.equal(availableHub.healthSummary.recentChangeCount, 3);
  assert.equal(availableHub.healthSummary.recentRollbackCount, 1);
  assert.equal(availableHub.healthSummary.recentConflictCount, 1);
  assert.equal(availableHub.healthSummary.recentIgnoredOrForbiddenCount, 1);
  assert.equal(availableHub.recentChanges.items[0]?.rollbackAvailability, "supported");
  logPass("config operations hub: aggregate recent changes/diagnostics/conflicts/rollback summary");

  const degradedHub = buildConfigOperationsHubDataFromSource({
    runtimePanel: buildRuntimePanel({
      runtime: {
        resolvedMode: "seed_only",
        ignoredOverrides: [
          {
            overrideType: "customer_stages",
            layer: "template_override",
            reason: "forbidden_core_semantic_override",
            diagnostics: ["ignored_override:customer_stages"]
          }
        ],
        diagnostics: ["runtime_fallback_to_seed_default"]
      },
      overrideWriteGovernance: {
        availability: "not_available",
        note: "override_write_governance not persisted"
      },
      orgConfigGovernance: {
        note: "mixed",
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
            note: "not available"
          },
          {
            targetType: "org_ai_settings",
            availability: "empty",
            hasPersistedAudit: false,
            latestChangedAt: null,
            latestActionType: null,
            latestVersionLabel: null,
            latestVersionNumber: null,
            runtimeImpactSummary: null,
            diagnosticsPreview: [],
            ignoredOrForbiddenDiagnosticsCount: 0,
            conflictDiagnosticsCount: 0,
            note: "empty"
          },
          {
            targetType: "org_feature_flags",
            availability: "available",
            hasPersistedAudit: true,
            latestChangedAt: "2026-03-22T10:00:00.000Z",
            latestActionType: "update",
            latestVersionLabel: "org_feature_flags:default:v1",
            latestVersionNumber: 1,
            runtimeImpactSummary: "runtime_consumed",
            diagnosticsPreview: [],
            ignoredOrForbiddenDiagnosticsCount: 0,
            conflictDiagnosticsCount: 0,
            note: "available"
          }
        ]
      }
    }),
    recentAudits: {
      availability: "not_available",
      note: "Persisted audit table is not available in current environment.",
      items: []
    }
  });
  assert.equal(degradedHub.recentChanges.availability, "not_available");
  assert.equal(degradedHub.recentChanges.items.length, 0);
  assert.ok(degradedHub.statusSignals.some((item) => item.status === "fallback" && item.domain === "runtime"));
  assert.ok(degradedHub.statusSignals.some((item) => item.domain === "audit_history"));
  assert.ok(degradedHub.statusSignals.some((item) => item.domain === "org_settings"));
  assert.ok(degradedHub.domainCards.some((item) => item.domainKey === "org_config" && item.status === "not_available"));
  logPass("config operations hub: empty/not_available/degraded states are surfaced");

  const cards = availableHub.domainCards;
  assert.equal(cards.length, 3);
  assert.equal(cards[0]?.href, "/settings/templates");
  assert.equal(cards[1]?.href, "/settings/org-config");
  assert.equal(cards[2]?.href, "/settings/runtime-debug");
  assert.ok(cards[0]?.rollbackSupportSummary.length);
  assert.ok(cards[1]?.rollbackSupportSummary.length);
  logPass("config operations hub: domain cards expose links and status summaries");
}
