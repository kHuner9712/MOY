import assert from "node:assert/strict";

import { getDefaultAutomationRuleSeeds } from "../lib/automation-ops";
import { validateOrgTemplateOverride } from "../lib/template-override-hardening";
import {
  applyReportFocusOverlay,
  buildManagerVisibilityRuntimeContext,
  buildResolvedIndustryTemplateContext,
  buildRuntimeConfigExplainSnapshot,
  resolveAutomationRuleSeedsWithRuntime,
  summarizeResolvedIndustryTemplateContext,
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
            no_followup_timeout: 2
          }
        }
      }),
      buildTemplateOverride({
        overrideType: "customer_stages",
        payload: {
          items: ["lead", "initial_contact", "won"]
        }
      })
    ],
    orgSettings: {
      defaultAlertRules: {
        no_followup_timeout: 4
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

export function runRuntimeConfigExplainHardeningTests(logPass: (name: string) => void): void {
  const runtimeLayerValid = validateOrgTemplateOverride({
    overrideType: "alert_rules",
    overridePayload: {
      rules: {
        no_followup_timeout: 3,
        unknown_key: 5
      }
    }
  });
  assert.equal(runtimeLayerValid.layer, "runtime_preference_overrides");
  assert.equal(runtimeLayerValid.validForWrite, true);
  assert.equal(runtimeLayerValid.acceptedForRuntime, true);
  assert.ok(runtimeLayerValid.diagnostics.some((item) => item.includes("ignored_alert_rule_key:unknown_key")));
  logPass("override hardening: runtime preference override validation");

  const templateLayerValid = validateOrgTemplateOverride({
    overrideType: "checkpoints",
    overridePayload: {
      items: ["need_confirmed", "proposal_shared"]
    }
  });
  assert.equal(templateLayerValid.layer, "template_application_params");
  assert.equal(templateLayerValid.validForWrite, true);
  assert.equal(templateLayerValid.acceptedForRuntime, false);
  logPass("override hardening: template application override validation");

  const forbiddenRuntime = validateOrgTemplateOverride({
    overrideType: "customer_stages",
    overridePayload: {
      items: ["lead", "proposal", "won"]
    }
  });
  assert.equal(forbiddenRuntime.layer, "forbidden_core_semantic_overrides");
  assert.equal(forbiddenRuntime.validForWrite, true);
  assert.equal(forbiddenRuntime.acceptedForRuntime, false);

  const invalidUnknown = validateOrgTemplateOverride({
    overrideType: "unknown_override_type",
    overridePayload: { any: "value" }
  });
  assert.equal(invalidUnknown.validForWrite, false);
  assert.equal(invalidUnknown.acceptedForRuntime, false);
  logPass("override hardening: forbidden and unknown override handling");

  const persistedContext = buildResolvedIndustryTemplateContext({
    templateKey: "saas_subscription",
    persistedOrgRuntime: buildPersistedSources()
  });
  const explain = buildRuntimeConfigExplainSnapshot(persistedContext);
  assert.equal(explain.resolvedMode, "persisted_preferred");
  assert.equal(explain.keyFieldSources.thresholdPreferences, "persisted_source");
  assert.ok(explain.ignoredOverrides.some((item) => item.overrideType === "customer_stages"));
  assert.ok(explain.diagnostics.some((item) => item.includes("ignored_override:customer_stages")));
  logPass("runtime explain: persisted source and ignored override diagnostics");

  const fallbackContext = buildResolvedIndustryTemplateContext({
    templateKey: "saas_subscription",
    persistedOrgRuntime: buildPersistedSources({
      assignedTemplateKey: null,
      templateOverrides: [],
      orgSettings: null,
      orgAiSettings: null,
      featureFlagMap: null,
      automationRuleSources: []
    })
  });
  const fallbackExplain = buildRuntimeConfigExplainSnapshot(fallbackContext);
  assert.equal(fallbackExplain.resolvedMode, "seed_only");
  assert.equal(fallbackExplain.keyFieldSources.resolvedTemplateKey, "fallback_profile");
  assert.equal(fallbackExplain.keyFieldSources.thresholdPreferences, "fallback_profile");
  logPass("runtime explain: fallback profile mode");

  const seedDefaultContext = buildResolvedIndustryTemplateContext({
    persistedOrgRuntime: buildPersistedSources({
      assignedTemplateKey: null,
      templateOverrides: [],
      orgSettings: null,
      orgAiSettings: null,
      featureFlagMap: null,
      automationRuleSources: []
    })
  });
  const seedDefaultExplain = buildRuntimeConfigExplainSnapshot(seedDefaultContext);
  assert.equal(seedDefaultExplain.keyFieldSources.resolvedTemplateKey, "seed_default");
  logPass("runtime explain: seed/default fallback mode");

  const visibilityContext = buildManagerVisibilityRuntimeContext({
    context: persistedContext,
    reportType: "manager_weekly"
  });
  const reportOverlay = applyReportFocusOverlay({
    reportType: "manager_weekly",
    metricsSnapshot: {
      open_alerts: 3
    },
    sourceSnapshot: {
      period: {
        start: "2026-03-01",
        end: "2026-03-21"
      }
    },
    context: visibilityContext
  });
  const overlayExplain = (reportOverlay.sourceSnapshot.runtime_preference_overlay as Record<string, unknown>).runtime_config_explain;
  assert.ok(overlayExplain);

  const automationSeeds = resolveAutomationRuleSeedsWithRuntime({
    baseSeeds: getDefaultAutomationRuleSeeds(),
    context: persistedContext
  });
  assert.ok(automationSeeds.length > 0);
  assert.equal(automationSeeds[0]?.resolutionDebug.source, "persisted_source");
  assert.equal(automationSeeds[0]?.resolutionDebug.resolvedMode, "persisted_preferred");
  assert.ok(automationSeeds[0]?.resolutionDebug.runtimeConfigExplain);

  const summary = summarizeResolvedIndustryTemplateContext(persistedContext);
  assert.ok(summary.runtime_config_explain);
  logPass("runtime explain: report snapshot, summary snapshot and automation seed debug carry explain payload");
}
