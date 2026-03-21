import assert from "node:assert/strict";

import {
  buildAuditDiffSummary,
  buildConfigTimelineViewerDataFromSource,
  canAccessConfigTimelineDiffViewer
} from "../services/config-timeline-diff-viewer-service";
import type { RecentOrgConfigAuditLogResult } from "../services/org-config-audit-service";
import type { RuntimeExplainDebugPanelData } from "../services/runtime-explain-debug-service";
import type { OrgConfigAuditLog } from "../types/productization";

function buildRuntimePanel(overrides?: {
  resolvedMode?: RuntimeExplainDebugPanelData["runtime"]["resolvedMode"];
  overrideWriteGovernanceAvailability?: RuntimeExplainDebugPanelData["overrideWriteGovernance"]["availability"];
}): RuntimeExplainDebugPanelData {
  return {
    generatedAt: "2026-03-22T11:00:00.000Z",
    runtime: {
      resolvedTemplateKey: "saas_subscription",
      fallbackProfileKey: "generic_b2b_growth",
      appliedOrgCustomizationKey: "default",
      resolvedMode: overrides?.resolvedMode ?? "persisted_preferred",
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
      availability: overrides?.overrideWriteGovernanceAvailability ?? "available_from_template_apply_snapshot",
      latestRunId: "run-1",
      latestRunAt: "2026-03-22T10:00:00.000Z",
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
}

function buildAudit(overrides: Partial<OrgConfigAuditLog>): OrgConfigAuditLog {
  return {
    id: "audit-1",
    orgId: "org-1",
    actorUserId: "user-1",
    targetType: "org_settings",
    targetId: "target-1",
    targetKey: "default",
    actionType: "update",
    beforeSummary: {
      payloadPreview: JSON.stringify({
        enabled: true
      })
    },
    afterSummary: {
      payloadPreview: JSON.stringify({
        enabled: false
      })
    },
    diagnosticsSummary: {
      diagnostics: ["accepted_fields:enabled"],
      runtimeImpactSummary: "runtime_consumed"
    },
    versionNumber: 1,
    versionLabel: "org_settings:default:v1",
    snapshotSummary: {},
    createdAt: "2026-03-22T11:00:00.000Z",
    ...overrides
  };
}

export function runConfigTimelineDiffViewerTests(logPass: (name: string) => void): void {
  assert.equal(canAccessConfigTimelineDiffViewer({ role: "manager", orgRole: "owner" }), true);
  assert.equal(canAccessConfigTimelineDiffViewer({ role: "manager", orgRole: "admin" }), true);
  assert.equal(canAccessConfigTimelineDiffViewer({ role: "manager", orgRole: "manager" }), true);
  assert.equal(canAccessConfigTimelineDiffViewer({ role: "sales", orgRole: "sales" }), false);
  assert.equal(canAccessConfigTimelineDiffViewer({ role: "sales", orgRole: "viewer" }), false);
  logPass("config timeline viewer: permission boundary manager+ only");

  const diff = buildAuditDiffSummary({
    beforeSummary: {
      payloadPreview: JSON.stringify({
        a: 1,
        b: "keep"
      })
    },
    afterSummary: {
      payloadPreview: JSON.stringify({
        a: 2,
        c: "new"
      })
    }
  });
  assert.equal(diff.status, "available");
  assert.ok(diff.changedKeys.includes("a"));
  assert.ok(diff.addedKeys.includes("c"));
  assert.ok(diff.removedKeys.includes("b"));
  logPass("config timeline viewer: structured diff summary (changed/added/removed) works");

  const recentAvailable: RecentOrgConfigAuditLogResult = {
    availability: "available",
    note: "latest 4",
    items: [
      buildAudit({
        id: "a1",
        targetType: "org_template_override",
        targetKey: "template-1:alert_rules",
        versionLabel: "org_template_override:template-1:alert_rules:v3",
        versionNumber: 3
      }),
      buildAudit({
        id: "a2",
        targetType: "org_settings",
        targetKey: "default",
        actionType: "rollback",
        diagnosticsSummary: {
          diagnostics: ["rollback_restored_from:org_settings:default:v2"],
          runtimeImpactSummary: "runtime_consumed",
          rollbackSource: {
            sourceAuditId: "a0",
            sourceVersionLabel: "org_settings:default:v2",
            sourceVersionNumber: 2,
            previewGeneratedAt: "2026-03-22T10:59:00.000Z"
          }
        }
      }),
      buildAudit({
        id: "a3",
        targetType: "org_ai_settings",
        targetKey: "default",
        versionLabel: "org_ai_settings:default:v4",
        versionNumber: 4
      }),
      buildAudit({
        id: "a4",
        targetType: "org_feature_flags",
        targetKey: "default",
        versionLabel: "org_feature_flags:default:v7",
        versionNumber: 7
      })
    ]
  };
  const viewer = buildConfigTimelineViewerDataFromSource({
    recentAudits: recentAvailable,
    runtimePanel: buildRuntimePanel()
  });
  assert.equal(viewer.timeline.availability, "available");
  assert.equal(viewer.timeline.items.length, 4);
  assert.ok(viewer.timeline.items.some((item) => item.targetType === "org_template_override"));
  assert.ok(viewer.timeline.items.some((item) => item.targetType === "org_settings"));
  assert.ok(viewer.timeline.items.some((item) => item.targetType === "org_ai_settings"));
  assert.ok(viewer.timeline.items.some((item) => item.targetType === "org_feature_flags"));
  assert.equal(viewer.timeline.items.find((item) => item.id === "a2")?.rollbackSource?.sourceAuditId, "a0");
  logPass("config timeline viewer: multi-target timeline aggregation with rollback source summary");

  const aiSensitive = buildConfigTimelineViewerDataFromSource({
    recentAudits: {
      availability: "available",
      note: "latest 1",
      items: [
        buildAudit({
          id: "ai-sensitive",
          targetType: "org_ai_settings",
          beforeSummary: {
            payloadPreview: JSON.stringify({
              modelDefault: "deepseek-chat",
              apiKey: "plain_secret_key"
            })
          },
          afterSummary: {
            payloadPreview: JSON.stringify({
              modelDefault: "deepseek-reasoner",
              apiKey: "new_secret_key"
            })
          }
        })
      ]
    },
    runtimePanel: buildRuntimePanel()
  });
  const sensitiveItem = aiSensitive.timeline.items[0];
  const beforePreview = String(sensitiveItem?.detail.beforeSummary?.payloadPreview ?? "");
  const afterPreview = String(sensitiveItem?.detail.afterSummary?.payloadPreview ?? "");
  assert.ok(beforePreview.includes("***REDACTED***"));
  assert.ok(afterPreview.includes("***REDACTED***"));
  assert.ok(
    (sensitiveItem?.detail.diffSummary.redactedFields ?? []).some((field) =>
      field.includes("payloadPreview.apiKey")
    )
  );
  logPass("config timeline viewer: sensitive fields are redacted in summary and diff metadata");

  const degraded = buildConfigTimelineViewerDataFromSource({
    recentAudits: {
      availability: "not_available",
      note: "Persisted audit table is not available in current environment.",
      items: []
    },
    runtimePanel: buildRuntimePanel({
      resolvedMode: "seed_only",
      overrideWriteGovernanceAvailability: "not_available"
    })
  });
  assert.equal(degraded.timeline.availability, "not_available");
  assert.equal(degraded.timeline.items.length, 0);
  assert.ok(
    degraded.statusSignals.some((signal) => signal.status === "fallback" && signal.domain === "runtime")
  );
  assert.ok(
    degraded.statusSignals.some((signal) => signal.status === "not_available" && signal.domain === "timeline")
  );
  assert.ok(
    degraded.statusSignals.some((signal) => signal.status === "degraded" && signal.domain === "template_override")
  );
  logPass("config timeline viewer: empty/not_available/degraded states are surfaced honestly");
}

