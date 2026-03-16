import type { TemplateApplyStrategy } from "@/types/productization";

function uniqueStrings(input: string[]): string[] {
  return Array.from(new Set(input.map((item) => item.trim()).filter((item) => item.length > 0)));
}

function mergeNumberRules(params: {
  existing: Record<string, number>;
  incoming: Record<string, number>;
  strategy: TemplateApplyStrategy;
}): Record<string, number> {
  const result: Record<string, number> = { ...params.existing };

  for (const [key, value] of Object.entries(params.incoming)) {
    if (params.strategy === "template_override_existing") {
      result[key] = value;
      continue;
    }
    if (!(key in result)) {
      result[key] = value;
      continue;
    }
    if (params.strategy === "merge_prefer_existing") {
      continue;
    }
    if (params.strategy === "additive_only") {
      continue;
    }
  }

  return result;
}

export interface TemplateConfigDraft {
  customerStages: string[];
  opportunityStages: string[];
  alertRules: Record<string, number>;
  checkpoints: string[];
  managerAttentionSignals: string[];
  prepPreferences: string[];
  briefPreferences: string[];
  recommendedOnboardingPath: string[];
  demoSeedProfile: string;
}

export interface TemplateApplyResult {
  merged: TemplateConfigDraft;
  diff: {
    changedKeys: string[];
    unchangedKeys: string[];
    notes: string[];
  };
}

export function applyTemplateConfig(params: {
  existing: TemplateConfigDraft;
  incoming: TemplateConfigDraft;
  strategy: TemplateApplyStrategy;
}): TemplateApplyResult {
  const changedKeys: string[] = [];
  const unchangedKeys: string[] = [];
  const notes: string[] = [];

  const mergeList = (existing: string[], incoming: string[]): string[] => {
    if (params.strategy === "template_override_existing") return uniqueStrings(incoming);
    const merged = uniqueStrings([...existing, ...incoming]);
    if (params.strategy === "merge_prefer_existing") return merged;
    if (params.strategy === "additive_only") return merged;
    return merged;
  };

  const merged: TemplateConfigDraft = {
    customerStages: mergeList(params.existing.customerStages, params.incoming.customerStages),
    opportunityStages: mergeList(params.existing.opportunityStages, params.incoming.opportunityStages),
    alertRules: mergeNumberRules({
      existing: params.existing.alertRules,
      incoming: params.incoming.alertRules,
      strategy: params.strategy
    }),
    checkpoints: mergeList(params.existing.checkpoints, params.incoming.checkpoints),
    managerAttentionSignals: mergeList(params.existing.managerAttentionSignals, params.incoming.managerAttentionSignals),
    prepPreferences: mergeList(params.existing.prepPreferences, params.incoming.prepPreferences),
    briefPreferences: mergeList(params.existing.briefPreferences, params.incoming.briefPreferences),
    recommendedOnboardingPath: mergeList(params.existing.recommendedOnboardingPath, params.incoming.recommendedOnboardingPath),
    demoSeedProfile:
      params.strategy === "template_override_existing" || !params.existing.demoSeedProfile
        ? params.incoming.demoSeedProfile
        : params.existing.demoSeedProfile
  };

  const compare = (key: string, left: unknown, right: unknown) => {
    const same = JSON.stringify(left) === JSON.stringify(right);
    if (same) unchangedKeys.push(key);
    else changedKeys.push(key);
  };

  compare("customer_stages", params.existing.customerStages, merged.customerStages);
  compare("opportunity_stages", params.existing.opportunityStages, merged.opportunityStages);
  compare("default_alert_rules", params.existing.alertRules, merged.alertRules);
  compare("suggested_checkpoints", params.existing.checkpoints, merged.checkpoints);
  compare("manager_attention_signals", params.existing.managerAttentionSignals, merged.managerAttentionSignals);
  compare("prep_preferences", params.existing.prepPreferences, merged.prepPreferences);
  compare("brief_preferences", params.existing.briefPreferences, merged.briefPreferences);
  compare("recommended_onboarding_path", params.existing.recommendedOnboardingPath, merged.recommendedOnboardingPath);
  compare("demo_seed_profile", params.existing.demoSeedProfile, merged.demoSeedProfile);

  if (params.strategy === "additive_only") {
    notes.push("Only additive merge was applied; existing values were preserved.");
  } else if (params.strategy === "merge_prefer_existing") {
    notes.push("Merged template defaults while preferring existing org values on conflicts.");
  } else {
    notes.push("Template values were allowed to override existing values.");
  }

  return {
    merged,
    diff: {
      changedKeys,
      unchangedKeys,
      notes
    }
  };
}

