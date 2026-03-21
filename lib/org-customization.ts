import { ORG_CUSTOMIZATION_SEEDS_V1 } from "@/data/org-customization-seeds-v1";
import {
  parseOrgCustomizationConfig,
  type OrgCustomizationConfig,
  type OrgFeaturePreference,
  type OrgThresholdPreference,
  type OrgThresholdPreferenceKey
} from "@/types/customization";
import type {
  IndustryTemplateActionHint,
  IndustryTemplateDefinition,
  IndustryTemplatePromptHook,
  IndustryTemplateRiskPattern,
  IndustryTemplateStageHint
} from "@/types/template";

export interface MergedTemplateWithOrgCustomization {
  priorityOrder: readonly ["base_semantics", "industry_template", "org_customization"];
  templateKey: string;
  templateVersion: string;
  customizationKey: string;
  customizationVersion: string;
  templateEnabled: boolean;
  baseStateMachineGuards: IndustryTemplateDefinition["baseStateMachineGuards"];
  effectiveStageHints: IndustryTemplateStageHint[];
  effectiveRiskPatterns: IndustryTemplateRiskPattern[];
  effectiveRecommendedActionLibrary: IndustryTemplateActionHint[];
  effectiveManagerFocusMetrics: string[];
  effectiveOnboardingHints: string[];
  effectiveImportMappingHints: string[];
  effectivePromptHooks: IndustryTemplatePromptHook[];
  effectiveFeaturePreferences: OrgFeaturePreference[];
  effectiveThresholdMap: Record<OrgThresholdPreferenceKey, number>;
  effectiveAutomationRulePreferences: OrgCustomizationConfig["automationRulePreferences"];
  effectiveReportingPreference: OrgCustomizationConfig["reportingPreference"];
}

export function listOrgCustomizationSeeds(): OrgCustomizationConfig[] {
  return ORG_CUSTOMIZATION_SEEDS_V1.map((item) => parseOrgCustomizationConfig(item));
}

export function getOrgCustomizationSeedByKey(customizationKey: string): OrgCustomizationConfig | null {
  const key = customizationKey.trim().toLowerCase();
  if (!key) return null;
  const found = ORG_CUSTOMIZATION_SEEDS_V1.find((item) => item.customizationKey === key);
  return found ? parseOrgCustomizationConfig(found) : null;
}

export function getDefaultOrgCustomizationConfig(): OrgCustomizationConfig {
  const defaultConfig = getOrgCustomizationSeedByKey("default_org_customization");
  if (!defaultConfig) {
    throw new Error("default_org_customization_missing");
  }
  return defaultConfig;
}

export function mergeTemplateWithOrgCustomization(params: {
  template: IndustryTemplateDefinition;
  orgCustomization: OrgCustomizationConfig;
}): MergedTemplateWithOrgCustomization {
  const config = parseOrgCustomizationConfig(params.orgCustomization);
  const template = params.template;

  const thresholdMap = buildThresholdMap(config.thresholdPreferences);
  const riskSignalThreshold = config.thresholdPreferences.find((item) => item.thresholdKey === "risk_signal_threshold") ?? null;
  const stageHintMap = config.templateSelection.stageHintOverrides;
  const stageVocabMap = config.templateSelection.stageVocabularyOverrides;

  const effectiveStageHints = template.stageHints.map((item) => {
    const key = `${item.primitive}:${item.baseStage}`;
    return {
      ...item,
      stageVocabulary: stageVocabMap[key] ?? item.stageVocabulary,
      stageHint: stageHintMap[key] ?? item.stageHint
    };
  });

  const effectiveRiskPatterns = template.commonRiskPatterns.map((item) => {
    if (!riskSignalThreshold) return item;
    if (riskSignalThreshold.targetPatternKeys.length > 0 && !riskSignalThreshold.targetPatternKeys.includes(item.patternKey)) {
      return item;
    }
    return {
      ...item,
      signalThresholdHint: `${riskSignalThreshold.value} ${riskSignalThreshold.unit} (org_override)`
    };
  });

  const effectivePromptHooks = dedupeByKey([
    ...template.promptAugmentationHooks,
    ...config.promptStrategyPreference.additionalPromptHooks,
    ...buildScenarioPromptHooks(config)
  ]);

  const effectiveManagerFocusMetrics = uniqueStrings([
    ...template.managerFocusMetrics,
    ...config.templateSelection.managerFocusMetricOverrides,
    ...config.reportingPreference.managerMetricFilters
  ]);

  const effectiveOnboardingHints = uniqueStrings([
    ...template.onboardingHints,
    ...config.onboardingPreferences.customHints
  ]);

  const effectiveImportMappingHints = uniqueStrings([
    ...template.importMappingHints,
    ...config.templateSelection.importMappingHintOverrides,
    ...config.importMappingPreferences.customHints
  ]);

  const templateEnabled = isTemplateEnabled({
    templateKey: template.templateKey,
    enabledTemplateKeys: config.templateSelection.enabledTemplateKeys,
    disabledTemplateKeys: config.templateSelection.disabledTemplateKeys
  });

  return {
    priorityOrder: ["base_semantics", "industry_template", "org_customization"],
    templateKey: template.templateKey,
    templateVersion: template.version,
    customizationKey: config.customizationKey,
    customizationVersion: config.version,
    templateEnabled,
    // Base semantics are always preserved from template guards and never overridden by org config.
    baseStateMachineGuards: template.baseStateMachineGuards,
    effectiveStageHints,
    effectiveRiskPatterns,
    effectiveRecommendedActionLibrary: [...template.recommendedActionLibrary],
    effectiveManagerFocusMetrics,
    effectiveOnboardingHints,
    effectiveImportMappingHints,
    effectivePromptHooks,
    effectiveFeaturePreferences: [...config.featurePreferences],
    effectiveThresholdMap: thresholdMap,
    effectiveAutomationRulePreferences: { ...config.automationRulePreferences },
    effectiveReportingPreference: {
      ...config.reportingPreference,
      managerMetricFilters: [...config.reportingPreference.managerMetricFilters],
      executiveMetricFilters: [...config.reportingPreference.executiveMetricFilters]
    }
  };
}

function buildThresholdMap(
  thresholds: OrgThresholdPreference[]
): Record<OrgThresholdPreferenceKey, number> {
  const map = {} as Record<OrgThresholdPreferenceKey, number>;
  for (const item of thresholds) {
    map[item.thresholdKey] = item.value;
  }
  return map;
}

function buildScenarioPromptHooks(config: OrgCustomizationConfig): IndustryTemplatePromptHook[] {
  return Object.entries(config.promptStrategyPreference.scenarioStrategy).map(([scenario, strategy]) => ({
    hookKey: `org_strategy_${scenario}`,
    scenario: scenario as IndustryTemplatePromptHook["scenario"],
    strategy: strategy.mode,
    promptPatch: strategy.promptPatch
  }));
}

function uniqueStrings(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter((item) => item.length > 0)));
}

function dedupeByKey(hooks: IndustryTemplatePromptHook[]): IndustryTemplatePromptHook[] {
  const map = new Map<string, IndustryTemplatePromptHook>();
  for (const hook of hooks) {
    map.set(hook.hookKey, hook);
  }
  return Array.from(map.values());
}

function isTemplateEnabled(params: {
  templateKey: string;
  enabledTemplateKeys: string[];
  disabledTemplateKeys: string[];
}): boolean {
  const disabled = new Set(params.disabledTemplateKeys);
  if (disabled.has(params.templateKey)) return false;

  if (params.enabledTemplateKeys.length === 0) return true;
  const enabled = new Set(params.enabledTemplateKeys);
  return enabled.has(params.templateKey);
}

