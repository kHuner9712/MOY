import assert from "node:assert/strict";

import {
  getDefaultOrgCustomizationConfig,
  getOrgCustomizationSeedByKey,
  listOrgCustomizationSeeds,
  mergeTemplateWithOrgCustomization
} from "../lib/org-customization";
import { safeParseOrgCustomizationConfig } from "../types/customization";
import { getIndustryTemplateSeedByKey } from "../services/industry-template-seed-service";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function runEnterpriseCustomizationFrameworkTests(logPass: (name: string) => void): void {
  const seeds = listOrgCustomizationSeeds();
  assert.ok(seeds.length >= 3);
  logPass("enterprise customization: seed count");

  for (const seed of seeds) {
    assert.match(seed.customizationKey, /^[a-z0-9_]+$/);
    assert.match(seed.version, /^v\d+\.\d+\.\d+$/);
    assert.ok(["draft", "active", "archived"].includes(seed.status));
    assert.ok(seed.scope.length > 0);
    assert.ok(seed.featurePreferences.length > 0);
    assert.ok(seed.thresholdPreferences.length > 0);
  }
  logPass("enterprise customization: key/version/status constraints");

  const defaultConfig = getDefaultOrgCustomizationConfig();
  assert.equal(defaultConfig.customizationKey, "default_org_customization");
  assert.ok(defaultConfig.guardrails.disallowCoreStateMachineOverride);
  logPass("enterprise customization: default config getter");

  const saasConfig = getOrgCustomizationSeedByKey("saas_org_overlay");
  const saasTemplate = getIndustryTemplateSeedByKey("saas_subscription");
  assert.ok(saasConfig);
  assert.ok(saasTemplate);
  if (!saasConfig || !saasTemplate) {
    throw new Error("seed_not_found");
  }

  const mergedSaas = mergeTemplateWithOrgCustomization({
    template: saasTemplate,
    orgCustomization: saasConfig
  });
  assert.equal(mergedSaas.templateEnabled, true);
  assert.deepEqual(mergedSaas.baseStateMachineGuards, saasTemplate.baseStateMachineGuards);
  assert.ok(
    mergedSaas.effectiveStageHints.some(
      (item) =>
        item.primitive === "opportunity" &&
        item.baseStage === "qualification" &&
        item.stageVocabulary === "trial_activation"
    )
  );
  assert.ok(mergedSaas.effectiveManagerFocusMetrics.includes("trial_activation_rate_7d"));
  logPass("enterprise customization: legal overlay merge");

  const manufacturingConfig = getOrgCustomizationSeedByKey("manufacturing_key_account_org_overlay");
  const manufacturingTemplate = getIndustryTemplateSeedByKey("manufacturing_key_account");
  assert.ok(manufacturingConfig);
  assert.ok(manufacturingTemplate);
  if (!manufacturingConfig || !manufacturingTemplate) {
    throw new Error("seed_not_found");
  }

  const mergedManufacturing = mergeTemplateWithOrgCustomization({
    template: manufacturingTemplate,
    orgCustomization: manufacturingConfig
  });
  assert.equal(mergedManufacturing.templateEnabled, true);
  assert.ok(mergedManufacturing.effectiveManagerFocusMetrics.includes("technical_checkpoint_pass_rate"));
  assert.ok(
    mergedManufacturing.effectiveStageHints.some(
      (item) =>
        item.primitive === "opportunity" &&
        item.baseStage === "business_review" &&
        item.stageVocabulary === "technical_procurement_dual_track"
    )
  );
  logPass("enterprise customization: manufacturing overlay merge");

  const deterministicLeft = mergeTemplateWithOrgCustomization({
    template: saasTemplate,
    orgCustomization: saasConfig
  });
  const deterministicRight = mergeTemplateWithOrgCustomization({
    template: saasTemplate,
    orgCustomization: saasConfig
  });
  assert.deepEqual(deterministicLeft, deterministicRight);
  logPass("enterprise customization: deterministic merge");

  const illegalOverride = clone(defaultConfig) as Record<string, unknown>;
  illegalOverride.templateSelection = {
    ...clone(defaultConfig.templateSelection),
    coreStateMachineOverrides: ["hack_stage"]
  };
  const parsedIllegal = safeParseOrgCustomizationConfig(illegalOverride);
  assert.equal(parsedIllegal.success, false);
  logPass("enterprise customization: reject core state machine override");

  const illegalGuardrail = clone(defaultConfig);
  illegalGuardrail.guardrails.disallowCoreStateMachineOverride = false as true;
  const parsedGuardrail = safeParseOrgCustomizationConfig(illegalGuardrail);
  assert.equal(parsedGuardrail.success, false);
  logPass("enterprise customization: guardrail cannot be disabled");
}

