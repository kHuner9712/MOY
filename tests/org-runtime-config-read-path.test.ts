import assert from "node:assert/strict";

import { getDefaultAutomationRuleSeeds } from "../lib/automation-ops";
import {
  buildPromptAugmentationContext,
  buildResolvedIndustryTemplateContext,
  resolveAutomationRuleSeedsWithRuntime,
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
            no_followup_timeout: 2,
            quoted_but_stalled: 6
          }
        }
      }),
      buildTemplateOverride({
        overrideType: "brief_preferences",
        payload: {
          items: ["renewal_at_risk", "critical_risks"]
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
        no_followup_timeout: 4,
        quoted_but_stalled: 9
      },
      defaultFollowupSlaDays: 2,
      onboardingStepState: {
        industry_template: true,
        first_data: true
      }
    },
    orgAiSettings: {
      fallbackMode: "rules_only",
      humanReviewRequiredForSensitiveActions: true
    },
    featureFlagMap: {
      ai_auto_analysis: false,
      manager_quality_view: false
    },
    automationRuleSources: [
      {
        ruleKey: "trial_activated_no_first_value",
        isEnabled: false,
        conditionsJson: {
          daysAfterActivation: 5
        }
      }
    ],
    ...overrides
  };
}

export function runOrgRuntimeConfigReadPathTests(logPass: (name: string) => void): void {
  const persistedContext = buildResolvedIndustryTemplateContext({
    templateKey: "saas_subscription",
    persistedOrgRuntime: buildPersistedSources()
  });

  assert.equal(persistedContext.runtimeSourceSnapshot.resolvedMode, "persisted_preferred");
  assert.equal(persistedContext.runtimeSourceSnapshot.persistedUsage.assignment, true);
  assert.ok(persistedContext.merged);
  assert.equal(persistedContext.merged?.effectiveThresholdMap.alert_no_followup_days, 2);
  assert.equal(persistedContext.merged?.effectiveThresholdMap.alert_stalled_opportunity_days, 6);
  assert.equal(
    persistedContext.orgCustomization.featurePreferences.find((item) => item.featureKey === "ai_auto_analysis")?.enabled,
    false
  );
  assert.ok(
    persistedContext.runtimeSourceSnapshot.ignoredOverrides.some(
      (item) => item.overrideType === "customer_stages" && item.reason === "forbidden_for_runtime_core_semantics"
    )
  );
  logPass("org runtime read path: persisted org sources override seed defaults");

  const automationSeeds = resolveAutomationRuleSeedsWithRuntime({
    baseSeeds: getDefaultAutomationRuleSeeds(),
    context: persistedContext
  });
  const trialRule = automationSeeds.find((item) => item.seed.ruleKey === "trial_activated_no_first_value");
  assert.ok(trialRule);
  assert.equal(trialRule?.isEnabled, false);
  assert.equal(trialRule?.seed.conditionsJson.daysAfterActivation, 5);

  const promptAugmentation = buildPromptAugmentationContext({
    scenario: "followup_analysis",
    context: persistedContext
  });
  assert.ok(promptAugmentation?.includes("Human review required for sensitive actions"));
  logPass("org runtime read path: automation and prompt consumption use persisted org sources");

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
  assert.equal(fallbackContext.runtimeSourceSnapshot.resolvedMode, "seed_only");
  assert.ok(fallbackContext.merged);
  assert.equal(fallbackContext.merged?.effectiveThresholdMap.alert_no_followup_days, 3);
  logPass("org runtime read path: missing persisted config falls back to seed/default");
}
