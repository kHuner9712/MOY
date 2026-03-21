import assert from "node:assert/strict";

import { getDefaultAutomationRuleSeeds } from "../lib/automation-ops";
import type { TemplateConfigDraft } from "../lib/template-application";
import {
  applyRuntimeTemplateConfigOverlay,
  buildPromptAugmentationContext,
  buildResolvedIndustryTemplateContext,
  buildRuntimePlaybookSeedEntries,
  resolveAutomationRuleSeedsWithRuntime,
  sortOnboardingChecklistByPreferredKeys
} from "../services/template-org-runtime-bridge-service";

function buildBaseTemplateConfig(): TemplateConfigDraft {
  return {
    customerStages: ["lead", "initial_contact", "proposal"],
    opportunityStages: ["discovery", "qualification", "proposal"],
    alertRules: {
      no_followup_timeout: 7,
      quoted_but_stalled: 10
    },
    checkpoints: ["need_confirmed", "quote_sent"],
    managerAttentionSignals: ["high_risk_customer"],
    prepPreferences: ["value-first opening"],
    briefPreferences: ["today priorities"],
    recommendedOnboardingPath: ["configure org profile", "import first customers"],
    demoSeedProfile: "generic_demo"
  };
}

export function runTemplateOrgRuntimeBridgeTests(logPass: (name: string) => void): void {
  const saasContext = buildResolvedIndustryTemplateContext({
    templateKey: "b2b_software"
  });
  assert.equal(saasContext.resolvedTemplateKey, "saas_subscription");
  assert.equal(saasContext.appliedOrgCustomizationKey, "saas_org_overlay");
  assert.equal(saasContext.fallbackToBase, false);
  assert.ok(saasContext.merged);
  logPass("runtime bridge: template alias + customization resolution");

  const baseConfig = buildBaseTemplateConfig();
  const runtimeMergedConfig = applyRuntimeTemplateConfigOverlay(baseConfig, saasContext);
  assert.deepEqual(runtimeMergedConfig.customerStages, baseConfig.customerStages);
  assert.deepEqual(runtimeMergedConfig.opportunityStages, baseConfig.opportunityStages);
  assert.equal(runtimeMergedConfig.alertRules.no_followup_timeout, 3);
  assert.equal(runtimeMergedConfig.alertRules.high_probability_stalled, 2);
  assert.equal(runtimeMergedConfig.demoSeedProfile, "saas_subscription_demo");
  logPass("runtime bridge: template config overlay keeps base state-machine stages");

  const runtimeEntries = buildRuntimePlaybookSeedEntries(saasContext);
  assert.ok(runtimeEntries.length > 0);
  assert.ok(runtimeEntries[0]?.entry_title.includes("[Runtime]"));
  logPass("runtime bridge: playbook seed entries derived from merged context");

  const sortedChecklist = sortOnboardingChecklistByPreferredKeys(
    [
      { key: "team_invite", label: "Team invite" },
      { key: "industry_template", label: "Industry template" },
      { key: "first_data", label: "First data" },
      { key: "org_profile", label: "Org profile" }
    ],
    saasContext.orgCustomization.onboardingPreferences.preferredChecklistKeys
  );
  assert.equal(sortedChecklist[0]?.key, "industry_template");
  assert.equal(sortedChecklist[1]?.key, "first_data");
  logPass("runtime bridge: onboarding order follows org preference overlay");

  const runtimeAutomationSeeds = resolveAutomationRuleSeedsWithRuntime({
    baseSeeds: getDefaultAutomationRuleSeeds(),
    context: saasContext
  });
  const highRiskInactivity = runtimeAutomationSeeds.find((item) => item.seed.ruleKey === "high_risk_customer_inactivity");
  const trialStalled = runtimeAutomationSeeds.find((item) => item.seed.ruleKey === "trial_activated_no_first_value");
  assert.ok(highRiskInactivity);
  assert.ok(trialStalled);
  assert.equal(highRiskInactivity?.seed.conditionsJson.daysWithoutFollowup, 3);
  assert.equal(trialStalled?.seed.conditionsJson.daysAfterActivation, 2);
  logPass("runtime bridge: automation seed thresholds consume org/template overlay");

  const manufacturingContext = buildResolvedIndustryTemplateContext({
    templateKey: "manufacturing"
  });
  const manufacturingSeeds = resolveAutomationRuleSeedsWithRuntime({
    baseSeeds: getDefaultAutomationRuleSeeds(),
    context: manufacturingContext
  });
  const manufacturingTrialRule = manufacturingSeeds.find((item) => item.seed.ruleKey === "trial_activated_no_first_value");
  assert.ok(manufacturingTrialRule);
  assert.equal(manufacturingTrialRule?.isEnabled, false);
  logPass("runtime bridge: automation seed enablement consumes org overlay switch");

  const promptAugmentation = buildPromptAugmentationContext({
    scenario: "followup_analysis",
    context: saasContext
  });
  assert.ok(promptAugmentation);
  assert.ok(promptAugmentation?.includes("template_key: saas_subscription"));
  assert.ok(promptAugmentation?.includes("append_checklist"));
  logPass("runtime bridge: prompt augmentation hooks resolved by scenario");

  const fallbackContext = buildResolvedIndustryTemplateContext({
    templateKey: "unknown_template_key"
  });
  assert.equal(fallbackContext.fallbackToBase, true);
  assert.equal(fallbackContext.merged, null);
  assert.equal(
    buildPromptAugmentationContext({
      scenario: "followup_analysis",
      context: fallbackContext
    }),
    null
  );

  const fallbackSeeds = resolveAutomationRuleSeedsWithRuntime({
    baseSeeds: getDefaultAutomationRuleSeeds(),
    context: fallbackContext
  });
  assert.equal(fallbackSeeds.every((item) => item.isEnabled), true);
  assert.deepEqual(
    fallbackSeeds.map((item) => item.seed.conditionsJson),
    getDefaultAutomationRuleSeeds().map((item) => item.conditionsJson)
  );
  logPass("runtime bridge: unknown template falls back to base runtime behavior");
}
