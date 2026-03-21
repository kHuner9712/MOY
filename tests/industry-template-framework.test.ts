import assert from "node:assert/strict";

import {
  getIndustryTemplateSeedByKey,
  listIndustryTemplateSeeds
} from "../services/industry-template-seed-service";
import {
  MOY_CORE_CUSTOMER_STAGES,
  MOY_CORE_OPPORTUNITY_STAGES,
  MOY_NON_OVERRIDABLE_SEMANTICS
} from "../types/template";

function sortedCopy(items: readonly string[]): string[] {
  return [...items].sort((a, b) => a.localeCompare(b));
}

function assertBaseSemanticsGuard(): void {
  const seeds = listIndustryTemplateSeeds();
  const allowedStages = new Set([...MOY_CORE_CUSTOMER_STAGES, ...MOY_CORE_OPPORTUNITY_STAGES]);
  const expectedCustomerStages = sortedCopy(MOY_CORE_CUSTOMER_STAGES);
  const expectedOpportunityStages = sortedCopy(MOY_CORE_OPPORTUNITY_STAGES);
  const expectedSemantics = sortedCopy(MOY_NON_OVERRIDABLE_SEMANTICS);

  for (const seed of seeds) {
    assert.deepEqual(sortedCopy(seed.baseStateMachineGuards.customerStages), expectedCustomerStages);
    assert.deepEqual(sortedCopy(seed.baseStateMachineGuards.opportunityStages), expectedOpportunityStages);
    assert.deepEqual(sortedCopy(seed.baseStateMachineGuards.nonOverridableSemantics), expectedSemantics);

    for (const hint of seed.stageHints) {
      assert.equal(allowedStages.has(hint.baseStage), true);
    }
  }
}

export function runIndustryTemplateFrameworkTests(logPass: (name: string) => void): void {
  const seeds = listIndustryTemplateSeeds();
  assert.ok(seeds.length >= 2);
  logPass("industry template framework: seed count");

  const keySet = new Set<string>();
  for (const seed of seeds) {
    assert.match(seed.templateKey, /^[a-z0-9_]+$/);
    assert.match(seed.version, /^v\d+\.\d+\.\d+$/);
    assert.ok(seed.name.trim().length > 0);
    assert.ok(["draft", "active", "archived"].includes(seed.status));

    assert.ok(seed.applicableSalesMode.length > 0);
    assert.ok(seed.stageHints.length > 0);
    assert.ok(seed.commonRiskPatterns.length > 0);
    assert.ok(seed.objectionLibrary.length > 0);
    assert.ok(seed.recommendedActionLibrary.length > 0);
    assert.ok(seed.managerFocusMetrics.length > 0);
    assert.ok(seed.onboardingHints.length > 0);
    assert.ok(seed.importMappingHints.length > 0);
    assert.ok(seed.promptAugmentationHooks.length > 0);

    assert.equal(keySet.has(seed.templateKey), false);
    keySet.add(seed.templateKey);
  }
  logPass("industry template framework: key/version/status constraints");

  const saas = getIndustryTemplateSeedByKey("saas_subscription");
  const manufacturing = getIndustryTemplateSeedByKey("manufacturing_key_account");

  assert.ok(saas);
  assert.ok(manufacturing);
  assert.equal(getIndustryTemplateSeedByKey("not_exists"), null);

  const saasStageTerms = new Set(saas?.stageHints.map((item) => item.stageVocabulary) ?? []);
  const manuStageTerms = new Set(manufacturing?.stageHints.map((item) => item.stageVocabulary) ?? []);
  assert.equal(saasStageTerms.has("trial_activation"), true);
  assert.equal(manuStageTerms.has("technical_procurement_dual_track"), true);

  const saasRiskKeys = new Set(saas?.commonRiskPatterns.map((item) => item.patternKey) ?? []);
  const manuRiskKeys = new Set(manufacturing?.commonRiskPatterns.map((item) => item.patternKey) ?? []);
  assert.equal(saasRiskKeys.has("renewal_window_silent"), true);
  assert.equal(manuRiskKeys.has("procurement_gate_stalled"), true);

  const saasMetrics = new Set(saas?.managerFocusMetrics ?? []);
  const manuMetrics = new Set(manufacturing?.managerFocusMetrics ?? []);
  assert.equal(saasMetrics.has("trial_activation_rate_7d"), true);
  assert.equal(manuMetrics.has("technical_checkpoint_pass_rate"), true);
  logPass("industry template framework: saas/manufacturing differentiation");

  assertBaseSemanticsGuard();
  logPass("industry template framework: base state machine guard");
}

