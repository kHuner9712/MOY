import assert from "node:assert/strict";

import {
  buildRuntimeExplainDebugPanelDataFromContext,
  canAccessRuntimeExplainDebugPanel
} from "../services/runtime-explain-debug-service";
import {
  buildResolvedIndustryTemplateContext,
  type PersistedOrgRuntimeSources
} from "../services/template-org-runtime-bridge-service";
import type { OrgTemplateOverride } from "../types/productization";

function buildTemplateOverride(params: {
  overrideType: OrgTemplateOverride["overrideType"];
  payload: Record<string, unknown>;
}): OrgTemplateOverride {
  const now = "2026-03-21T00:00:00.000Z";
  return {
    id: `override_${params.overrideType}`,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: params.overrideType,
    overridePayload: params.payload,
    createdBy: "user-1",
    createdAt: now,
    updatedAt: now
  };
}

function buildPersistedSources(
  overrides: Partial<PersistedOrgRuntimeSources> = {}
): PersistedOrgRuntimeSources {
  return {
    assignedTemplateKey: "saas_subscription",
    templateOverrides: [
      buildTemplateOverride({
        overrideType: "alert_rules",
        payload: {
          rules: {
            no_followup_timeout: 3
          }
        }
      }),
      buildTemplateOverride({
        overrideType: "customer_stages",
        payload: {
          items: ["lead", "proposal", "won"]
        }
      })
    ],
    orgSettings: {
      defaultAlertRules: {
        no_followup_timeout: 5
      },
      defaultFollowupSlaDays: 2,
      onboardingStepState: {
        industry_template: true
      }
    },
    orgAiSettings: {
      fallbackMode: "rules_only",
      humanReviewRequiredForSensitiveActions: true
    },
    featureFlagMap: {
      ai_auto_analysis: false
    },
    automationRuleSources: [],
    ...overrides
  };
}

export function runRuntimeExplainDebugPanelTests(logPass: (name: string) => void): void {
  assert.equal(canAccessRuntimeExplainDebugPanel({ role: "manager", orgRole: "owner" }), true);
  assert.equal(canAccessRuntimeExplainDebugPanel({ role: "manager", orgRole: "admin" }), true);
  assert.equal(canAccessRuntimeExplainDebugPanel({ role: "manager", orgRole: "manager" }), true);
  assert.equal(canAccessRuntimeExplainDebugPanel({ role: "sales", orgRole: "sales" }), false);
  assert.equal(canAccessRuntimeExplainDebugPanel({ role: "sales", orgRole: "viewer" }), false);
  logPass("runtime debug panel: permission boundary by capability");

  const persistedContext = buildResolvedIndustryTemplateContext({
    templateKey: "saas_subscription",
    persistedOrgRuntime: buildPersistedSources()
  });
  const panel = buildRuntimeExplainDebugPanelDataFromContext({
    context: persistedContext,
    recentPersistedAudits: {
      availability: "available",
      note: "Showing latest 1 persisted org config audit records.",
      items: [
        {
          id: "audit-1",
          createdAt: "2026-03-21T12:00:00.000Z",
          actorUserId: "user-1",
          targetType: "org_template_override",
          targetKey: "template-1:alert_rules",
          actionType: "update",
          versionLabel: "org_template_override:template-1:alert_rules:v2",
          versionNumber: 2,
          runtimeImpactSummary: "runtime_consumed",
          forbiddenForRuntime: false,
          ignoredByRuntime: false,
          diagnosticsPreview: ["ignored_alert_rule_key:unknown"],
          hasConcurrencyConflictDiagnostic: false
        }
      ]
    },
    recentRollbackAudits: {
      availability: "available",
      note: "Showing latest 1 rollback records.",
      items: [
        {
          id: "rollback-audit-1",
          createdAt: "2026-03-21T13:00:00.000Z",
          actorUserId: "user-1",
          targetType: "org_template_override",
          targetKey: "template-1:alert_rules",
          versionLabel: "org_template_override:template-1:alert_rules:v3",
          versionNumber: 3,
          restoredFromAuditId: "audit-1",
          restoredFromVersionLabel: "org_template_override:template-1:alert_rules:v2",
          restoredFromVersionNumber: 2,
          diagnosticsPreview: ["rollback_recovered_from:org_template_override:template-1:alert_rules:v2"],
          hasConcurrencyConflictDiagnostic: false
        }
      ]
    }
  });
  assert.equal(panel.runtime.resolvedMode, "persisted_preferred");
  assert.equal(panel.runtime.resolvedTemplateKey, "saas_subscription");
  assert.equal(panel.runtime.keyFieldSources.thresholdPreferences, "persisted_source");
  assert.ok(panel.runtime.ignoredOverrides.some((item) => item.overrideType === "customer_stages"));
  assert.ok(panel.runtime.diagnostics.some((item) => item.includes("ignored_override:customer_stages")));
  assert.ok(panel.consumerExplainSummary.automationSeed.totalSeedCount > 0);
  assert.ok(panel.consumerExplainSummary.executiveReport.managerFocusMetricPriority.length >= 0);
  assert.equal(panel.recentPersistedAudits.availability, "available");
  assert.equal(panel.recentPersistedAudits.items.length, 1);
  assert.equal(panel.recentPersistedAudits.items[0]?.versionNumber, 2);
  assert.equal(panel.recentRollbackAudits.availability, "available");
  assert.equal(panel.recentRollbackAudits.items.length, 1);
  assert.equal(panel.recentRollbackAudits.items[0]?.restoredFromVersionNumber, 2);
  assert.equal(panel.concurrencyGuard.latestConflictReason, null);
  assert.equal(panel.orgConfigGovernance.items.length, 3);
  assert.equal(panel.orgConfigGovernance.items[0]?.targetType, "org_settings");
  assert.equal(panel.orgConfigGovernance.items[0]?.availability, "not_available");
  logPass("runtime debug panel: aggregated runtime explain fields for persisted context");

  const fallbackSeedContext = buildResolvedIndustryTemplateContext({
    persistedOrgRuntime: buildPersistedSources({
      assignedTemplateKey: null,
      templateOverrides: [],
      orgSettings: null,
      orgAiSettings: null,
      featureFlagMap: null,
      automationRuleSources: []
    })
  });
  const fallbackPanel = buildRuntimeExplainDebugPanelDataFromContext({
    context: fallbackSeedContext
  });
  assert.equal(fallbackPanel.runtime.resolvedMode, "seed_only");
  assert.equal(fallbackPanel.runtime.keyFieldSources.resolvedTemplateKey, "seed_default");
  assert.equal(fallbackPanel.runtime.keyFieldSources.thresholdPreferences, "seed_default");
  assert.equal(fallbackPanel.runtime.ignoredOverrides.length, 0);
  assert.equal(fallbackPanel.recentPersistedAudits.availability, "not_available");
  assert.equal(fallbackPanel.recentPersistedAudits.items.length, 0);
  assert.equal(fallbackPanel.recentRollbackAudits.availability, "not_available");
  assert.equal(fallbackPanel.recentRollbackAudits.items.length, 0);
  assert.equal(fallbackPanel.concurrencyGuard.latestConflictReason, null);
  assert.equal(fallbackPanel.orgConfigGovernance.items.length, 3);
  assert.equal(fallbackPanel.orgConfigGovernance.items[2]?.targetType, "org_feature_flags");
  logPass("runtime debug panel: fallback/seed/default source is visible when no persisted config");
}
