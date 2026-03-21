import { z } from "zod";
import type { AiScenario } from "@/types/ai";

export const ORG_CUSTOMIZATION_STATUS_VALUES = ["draft", "active", "archived"] as const;
export type OrgCustomizationStatus = (typeof ORG_CUSTOMIZATION_STATUS_VALUES)[number];

export const ORG_CUSTOMIZATION_SCOPE_VALUES = [
  "org_identity_boundary",
  "org_configurable_preferences",
  "template_selection_overlay",
  "reporting_visibility_preferences"
] as const;
export type OrgCustomizationScope = (typeof ORG_CUSTOMIZATION_SCOPE_VALUES)[number];

export const ORG_FEATURE_KEY_VALUES = [
  "ai_auto_analysis",
  "ai_auto_planning",
  "ai_morning_brief",
  "ai_deal_command",
  "external_touchpoints",
  "prep_cards",
  "playbooks",
  "manager_quality_view",
  "outcome_learning",
  "demo_seed_tools"
] as const;

export const ORG_THRESHOLD_PREFERENCE_KEY_VALUES = [
  "followup_sla_days",
  "alert_no_followup_days",
  "alert_stalled_opportunity_days",
  "manager_attention_score",
  "rhythm_inactivity_days",
  "renewal_watch_window_days",
  "risk_signal_threshold"
] as const;
export type OrgThresholdPreferenceKey = (typeof ORG_THRESHOLD_PREFERENCE_KEY_VALUES)[number];

export const ORG_THRESHOLD_UNIT_VALUES = ["days", "hours", "score", "count"] as const;
export type OrgThresholdUnit = (typeof ORG_THRESHOLD_UNIT_VALUES)[number];

export const ORG_PROMPT_STRATEGY_MODE_VALUES = ["template_first", "org_overlay"] as const;
export type OrgPromptStrategyMode = (typeof ORG_PROMPT_STRATEGY_MODE_VALUES)[number];

export const ORG_PROMPT_HOOK_STRATEGY_VALUES = [
  "prepend_context",
  "append_checklist",
  "inject_constraints"
] as const;
export type OrgPromptHookStrategy = (typeof ORG_PROMPT_HOOK_STRATEGY_VALUES)[number];

export const ORG_OWNER_MATCH_MODE_VALUES = ["strict", "balanced", "lenient"] as const;
export type OrgOwnerMatchMode = (typeof ORG_OWNER_MATCH_MODE_VALUES)[number];

export const ORG_CUSTOMIZATION_FORBIDDEN_OVERRIDE_KEYS = [
  "baseStateMachineGuards",
  "coreStateMachineOverrides",
  "defaultCustomerStages",
  "defaultOpportunityStages",
  "objectRelationships",
  "permissionSemantics",
  "aiGovernance",
  "resultSource",
  "fallbackReason"
] as const;

export const orgFeaturePreferenceSchema = z
  .object({
    featureKey: z.enum(ORG_FEATURE_KEY_VALUES),
    enabled: z.boolean(),
    source: z.enum(["entitlement_default", "org_override"]),
    note: z.string().max(200).nullable()
  })
  .strict();
export type OrgFeaturePreference = z.infer<typeof orgFeaturePreferenceSchema>;

export const orgThresholdPreferenceSchema = z
  .object({
    thresholdKey: z.enum(ORG_THRESHOLD_PREFERENCE_KEY_VALUES),
    value: z.number().finite(),
    minValue: z.number().finite(),
    maxValue: z.number().finite(),
    unit: z.enum(ORG_THRESHOLD_UNIT_VALUES),
    description: z.string().min(1).max(200),
    targetPatternKeys: z.array(z.string().min(1).max(64)).max(20)
  })
  .strict()
  .superRefine((value, ctx) => {
    if (value.minValue > value.maxValue) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["minValue"],
        message: "minValue cannot be greater than maxValue"
      });
    }
    if (value.value < value.minValue || value.value > value.maxValue) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["value"],
        message: "value must be within [minValue, maxValue]"
      });
    }
  });
export type OrgThresholdPreference = z.infer<typeof orgThresholdPreferenceSchema>;

export const orgTemplateSelectionSchema = z
  .object({
    enabledTemplateKeys: z.array(z.string().regex(/^[a-z0-9_]+$/)).max(100),
    disabledTemplateKeys: z.array(z.string().regex(/^[a-z0-9_]+$/)).max(100),
    defaultTemplateKey: z.string().regex(/^[a-z0-9_]+$/).nullable(),
    stageVocabularyOverrides: z.record(z.string().min(1).max(80)),
    stageHintOverrides: z.record(z.string().min(1).max(240)),
    managerFocusMetricOverrides: z.array(z.string().min(1).max(80)).max(80),
    importMappingHintOverrides: z.array(z.string().min(1).max(200)).max(80)
  })
  .strict()
  .superRefine((value, ctx) => {
    const enabled = new Set(value.enabledTemplateKeys);
    const disabled = new Set(value.disabledTemplateKeys);
    for (const key of enabled) {
      if (disabled.has(key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["disabledTemplateKeys"],
          message: `Template key ${key} cannot be both enabled and disabled`
        });
      }
    }
    if (value.defaultTemplateKey && disabled.has(value.defaultTemplateKey)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["defaultTemplateKey"],
        message: "defaultTemplateKey cannot be in disabledTemplateKeys"
      });
    }
    if (value.defaultTemplateKey && enabled.size > 0 && !enabled.has(value.defaultTemplateKey)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["defaultTemplateKey"],
        message: "defaultTemplateKey must be included in enabledTemplateKeys"
      });
    }
  });
export type OrgTemplateSelection = z.infer<typeof orgTemplateSelectionSchema>;

const industryTemplatePromptHookSchema = z
  .object({
    hookKey: z.string().min(1).max(80),
    scenario: z.string().regex(/^[a-z0-9_]+$/) as z.ZodType<AiScenario>,
    strategy: z.enum(ORG_PROMPT_HOOK_STRATEGY_VALUES),
    promptPatch: z.string().min(1).max(1200)
  })
  .strict();

export const orgPromptStrategyPreferenceSchema = z
  .object({
    mode: z.enum(ORG_PROMPT_STRATEGY_MODE_VALUES),
    additionalPromptHooks: z.array(industryTemplatePromptHookSchema).max(100),
    scenarioStrategy: z.record(
      z
        .object({
          mode: z.enum(ORG_PROMPT_HOOK_STRATEGY_VALUES),
          promptPatch: z.string().min(1).max(1200)
        })
        .strict()
    )
  })
  .strict();
export type OrgPromptStrategyPreference = z.infer<typeof orgPromptStrategyPreferenceSchema>;

export const orgReportingPreferenceSchema = z
  .object({
    managerMetricFilters: z.array(z.string().min(1).max(80)).max(80),
    executiveMetricFilters: z.array(z.string().min(1).max(80)).max(80),
    hideLowConfidenceAiInsights: z.boolean(),
    defaultDateRangeDays: z.number().int().min(1).max(365)
  })
  .strict();
export type OrgReportingPreference = z.infer<typeof orgReportingPreferenceSchema>;

export const orgCustomizationConfigSchema = z
  .object({
    customizationKey: z.string().regex(/^[a-z0-9_]+$/),
    name: z.string().min(1).max(80),
    version: z.string().regex(/^v\d+\.\d+\.\d+$/),
    status: z.enum(ORG_CUSTOMIZATION_STATUS_VALUES),
    scope: z.array(z.enum(ORG_CUSTOMIZATION_SCOPE_VALUES)).min(1),
    description: z.string().min(1).max(300),
    featurePreferences: z.array(orgFeaturePreferenceSchema),
    thresholdPreferences: z.array(orgThresholdPreferenceSchema),
    automationRulePreferences: z.record(
      z
        .object({
          enabled: z.boolean(),
          thresholdOverride: z.number().finite().nullable(),
          note: z.string().max(200).nullable()
        })
        .strict()
    ),
    onboardingPreferences: z
      .object({
        preferredChecklistKeys: z.array(z.string().min(1).max(80)).max(80),
        prioritizeTemplateSelection: z.boolean(),
        importFirstMode: z.boolean(),
        customHints: z.array(z.string().min(1).max(200)).max(50)
      })
      .strict(),
    importMappingPreferences: z
      .object({
        preferredColumnAliases: z.record(z.array(z.string().min(1).max(80)).max(50)),
        ownerMatchMode: z.enum(ORG_OWNER_MATCH_MODE_VALUES),
        customHints: z.array(z.string().min(1).max(200)).max(50)
      })
      .strict(),
    templateSelection: orgTemplateSelectionSchema,
    promptStrategyPreference: orgPromptStrategyPreferenceSchema,
    reportingPreference: orgReportingPreferenceSchema,
    guardrails: z
      .object({
        disallowCoreStateMachineOverride: z.literal(true),
        disallowObjectRelationshipOverride: z.literal(true),
        disallowPermissionSemanticOverride: z.literal(true),
        disallowAiGovernanceOverride: z.literal(true)
      })
      .strict()
  })
  .strict()
  .superRefine((value, ctx) => {
    const forbiddenPaths = findForbiddenOverridePaths(value);
    if (forbiddenPaths.length > 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `Org customization cannot override base semantics fields: ${forbiddenPaths.join(", ")}`
      });
    }
  });
export type OrgCustomizationConfig = z.infer<typeof orgCustomizationConfigSchema>;

export function parseOrgCustomizationConfig(input: unknown): OrgCustomizationConfig {
  return orgCustomizationConfigSchema.parse(input);
}

export function safeParseOrgCustomizationConfig(input: unknown) {
  return orgCustomizationConfigSchema.safeParse(input);
}

function findForbiddenOverridePaths(value: unknown, path: string[] = []): string[] {
  if (!value || typeof value !== "object") return [];

  if (Array.isArray(value)) {
    const results: string[] = [];
    value.forEach((item, index) => {
      results.push(...findForbiddenOverridePaths(item, [...path, String(index)]));
    });
    return results;
  }

  const objectValue = value as Record<string, unknown>;
  const results: string[] = [];

  for (const [key, child] of Object.entries(objectValue)) {
    if ((ORG_CUSTOMIZATION_FORBIDDEN_OVERRIDE_KEYS as readonly string[]).includes(key)) {
      results.push([...path, key].join("."));
    }
    results.push(...findForbiddenOverridePaths(child, [...path, key]));
  }

  return results;
}
