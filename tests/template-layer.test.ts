import assert from "node:assert/strict";

import { BUILTIN_INDUSTRY_TEMPLATE_SEEDS } from "../data/industry-templates";
import { buildFallbackTemplateApplicationSummary, buildFallbackTemplateFitRecommendation } from "../lib/productization-fallback";
import { applyTemplateConfig, type TemplateConfigDraft } from "../lib/template-application";

function buildConfig(overrides: Partial<TemplateConfigDraft> = {}): TemplateConfigDraft {
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
    demoSeedProfile: "generic_demo",
    ...overrides
  };
}

export function runTemplateLayerTests(logPass: (name: string) => void): void {
  const existing = buildConfig();
  const incoming = buildConfig({
    customerStages: ["lead", "needs_confirmed", "negotiation"],
    opportunityStages: ["qualification", "business_review", "negotiation"],
    alertRules: {
      quoted_but_stalled: 6,
      high_probability_stalled: 4
    },
    checkpoints: ["need_confirmed", "proposal_sent", "closing"],
    managerAttentionSignals: ["blocked_checkpoint", "high_risk_customer"],
    prepPreferences: ["trial acceptance criteria"],
    briefPreferences: ["high-risk customers"],
    recommendedOnboardingPath: ["choose template", "run first brief"],
    demoSeedProfile: "software_demo"
  });

  const additive = applyTemplateConfig({
    existing,
    incoming,
    strategy: "additive_only"
  });
  assert.equal(additive.merged.alertRules.quoted_but_stalled, 10);
  assert.equal(additive.merged.alertRules.high_probability_stalled, 4);
  assert.ok(additive.merged.customerStages.includes("proposal"));
  assert.ok(additive.merged.customerStages.includes("needs_confirmed"));
  assert.equal(additive.merged.demoSeedProfile, "generic_demo");
  logPass("template apply additive_only");

  const mergePreferExisting = applyTemplateConfig({
    existing,
    incoming,
    strategy: "merge_prefer_existing"
  });
  assert.equal(mergePreferExisting.merged.alertRules.quoted_but_stalled, 10);
  assert.equal(mergePreferExisting.merged.alertRules.high_probability_stalled, 4);
  assert.ok(mergePreferExisting.merged.checkpoints.includes("quote_sent"));
  assert.ok(mergePreferExisting.merged.checkpoints.includes("proposal_sent"));
  assert.equal(mergePreferExisting.merged.demoSeedProfile, "generic_demo");
  logPass("template apply merge_prefer_existing");

  const overrideExisting = applyTemplateConfig({
    existing,
    incoming,
    strategy: "template_override_existing"
  });
  assert.deepEqual(overrideExisting.merged.customerStages, ["lead", "needs_confirmed", "negotiation"]);
  assert.equal(overrideExisting.merged.alertRules.quoted_but_stalled, 6);
  assert.equal(overrideExisting.merged.demoSeedProfile, "software_demo");
  logPass("template apply template_override_existing");

  const fitFallback = buildFallbackTemplateFitRecommendation({
    industryHint: "saas solution",
    availableTemplateKeys: BUILTIN_INDUSTRY_TEMPLATE_SEEDS.map((item) => item.templateKey),
    teamSize: 6
  });
  assert.equal(fitFallback.recommendedTemplateKey, "b2b_software");
  assert.equal(fitFallback.recommendedApplyMode, "onboarding_default");

  const fitFallbackSmallTeam = buildFallbackTemplateFitRecommendation({
    industryHint: null,
    availableTemplateKeys: ["generic"],
    teamSize: 1
  });
  assert.equal(fitFallbackSmallTeam.recommendedTemplateKey, "generic");
  assert.equal(fitFallbackSmallTeam.recommendedApplyMode, "trial_bootstrap");
  logPass("template fit fallback");

  const summaryFallback = buildFallbackTemplateApplicationSummary({
    changedKeys: ["customer_stages", "default_alert_rules"],
    unchangedKeys: ["brief_preferences"],
    strategy: "additive_only"
  });
  assert.ok(summaryFallback.whatWillChange.includes("customer_stages"));
  assert.ok(summaryFallback.whatWillNotChange.includes("brief_preferences"));
  assert.ok(summaryFallback.cautionNotes.some((item) => item.includes("additive_only")));
  logPass("template diff summary fallback");

  assert.ok(BUILTIN_INDUSTRY_TEMPLATE_SEEDS.length >= 6);
  const requiredKeys = [
    "generic",
    "b2b_software",
    "education_training",
    "manufacturing",
    "channel_sales",
    "consulting_services"
  ];
  for (const key of requiredKeys) {
    assert.ok(BUILTIN_INDUSTRY_TEMPLATE_SEEDS.some((item) => item.templateKey === key));
  }
  logPass("industry template seed coverage");

  const profiles = BUILTIN_INDUSTRY_TEMPLATE_SEEDS.map((item) => String(item.templatePayload.demo_seed_profile ?? ""));
  assert.ok(profiles.every((item) => item.length > 0));
  assert.ok(new Set(profiles).size >= 6);
  logPass("demo seed industry profile branching");

  for (const template of BUILTIN_INDUSTRY_TEMPLATE_SEEDS) {
    assert.ok(template.seededPlaybookTemplates.length > 0);
    assert.ok(template.scenarioPacks.length >= 5);
    assert.ok(Array.isArray(template.templatePayload.suggested_checkpoints));
    assert.ok(Array.isArray(template.templatePayload.manager_attention_signals));
  }
  logPass("seeded playbook and template payload markers");
}
