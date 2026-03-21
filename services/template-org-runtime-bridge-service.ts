import type { TemplateConfigDraft } from "@/lib/template-application";
import {
  validateOrgTemplateOverride,
  type OrgTemplateOverrideLayer
} from "@/lib/template-override-hardening";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  getDefaultOrgCustomizationConfig,
  getOrgCustomizationSeedByKey,
  mergeTemplateWithOrgCustomization,
  type MergedTemplateWithOrgCustomization
} from "@/lib/org-customization";
import { getCurrentOrgTemplateContext } from "@/services/industry-template-service";
import { getOrgAiSettings } from "@/services/org-ai-settings-service";
import { getOrgFeatureFlagMap } from "@/services/org-feature-service";
import { getOrgSettings } from "@/services/org-settings-service";
import { getIndustryTemplateSeedByKey } from "@/services/industry-template-seed-service";
import type { AiScenario } from "@/types/ai";
import type { BusinessEvent } from "@/types/automation";
import type { AutomationRuleSeed } from "@/types/automation";
import type { OrgCustomizationConfig, OrgThresholdPreferenceKey } from "@/types/customization";
import type { OrgAiSettings, OrgFeatureKey, OrgSettings, OrgTemplateOverride } from "@/types/productization";
import type { ReportType } from "@/types/report";
import type { IndustryTemplateDefinition } from "@/types/template";

const TEMPLATE_KEY_ALIAS_MAP: Record<string, string> = {
  saas_subscription: "saas_subscription",
  b2b_software: "saas_subscription",
  manufacturing: "manufacturing_key_account",
  manufacturing_key_account: "manufacturing_key_account"
};

const TEMPLATE_TO_ORG_CUSTOMIZATION_MAP: Record<string, string> = {
  saas_subscription: "saas_org_overlay",
  manufacturing_key_account: "manufacturing_key_account_org_overlay"
};

const AUTOMATION_RULE_PREFERENCE_ALIAS_MAP: Record<string, string> = {
  trial_activated_no_first_value: "trial_stalled_watch",
  renewal_activity_decline: "renewal_due_watch"
};

export interface ResolvedIndustryTemplateRuntimeContext {
  requestedTemplateKey: string | null;
  resolvedTemplateKey: string | null;
  template: IndustryTemplateDefinition | null;
  appliedOrgCustomizationKey: string;
  orgCustomization: OrgCustomizationConfig;
  merged: MergedTemplateWithOrgCustomization | null;
  fallbackToBase: boolean;
  runtimeSourceSnapshot: RuntimeSourceSnapshot;
}

type DbClient = ServerSupabaseClient;

export interface PersistedAutomationRuleRuntimeSource {
  ruleKey: string;
  isEnabled: boolean;
  conditionsJson: Record<string, unknown>;
}

export interface PersistedOrgRuntimeSources {
  assignedTemplateKey: string | null;
  templateOverrides: OrgTemplateOverride[];
  orgSettings: Pick<OrgSettings, "defaultAlertRules" | "defaultFollowupSlaDays" | "onboardingStepState"> | null;
  orgAiSettings: Pick<OrgAiSettings, "fallbackMode" | "humanReviewRequiredForSensitiveActions"> | null;
  featureFlagMap: Partial<Record<OrgFeatureKey, boolean>> | null;
  automationRuleSources: PersistedAutomationRuleRuntimeSource[];
}

export interface RuntimeSourceSnapshot {
  sourcePriority: readonly ["persisted_org_config", "org_fallback_profile", "code_seed_default"];
  resolvedMode: "seed_only" | "persisted_preferred";
  fallbackProfileKey: string;
  persistedUsage: {
    assignment: boolean;
    overrides: boolean;
    orgSettings: boolean;
    orgAiSettings: boolean;
    orgFeatureFlags: boolean;
    automationRules: boolean;
  };
  ignoredOverrides: Array<{
    overrideType: string;
    layer: OrgTemplateOverrideLayer | "unknown";
    reason: string;
    diagnostics: string[];
  }>;
  appliedOverrides: Array<{
    overrideType: string;
    layer: OrgTemplateOverrideLayer;
    appliedFields: string[];
  }>;
}

export type RuntimeExplainSourceKind = "persisted_source" | "fallback_profile" | "seed_default";

export interface RuntimeConfigExplainSnapshot {
  sourcePriority: RuntimeSourceSnapshot["sourcePriority"];
  resolvedMode: RuntimeSourceSnapshot["resolvedMode"];
  fallbackProfileKey: string;
  resolvedTemplateKey: string | null;
  appliedOrgCustomizationKey: string;
  keyFieldSources: {
    resolvedTemplateKey: RuntimeExplainSourceKind;
    orgCustomizationProfile: RuntimeExplainSourceKind;
    thresholdPreferences: RuntimeExplainSourceKind;
    promptPreference: RuntimeExplainSourceKind;
    featurePreferences: RuntimeExplainSourceKind;
  };
  persistedUsage: RuntimeSourceSnapshot["persistedUsage"];
  appliedOverrides: RuntimeSourceSnapshot["appliedOverrides"];
  ignoredOverrides: RuntimeSourceSnapshot["ignoredOverrides"];
  diagnostics: string[];
}

export interface AutomationSeedResolutionDebugContext {
  source: RuntimeExplainSourceKind;
  resolvedMode: RuntimeSourceSnapshot["resolvedMode"];
  ignoredOverrideCount: number;
  runtimeConfigExplain: RuntimeConfigExplainSnapshot;
}

export interface RuntimeAutomationRuleSeed {
  seed: AutomationRuleSeed;
  isEnabled: boolean;
  resolutionDebug: AutomationSeedResolutionDebugContext;
}

export interface RuntimePlaybookSeedEntry {
  entry_title: string;
  entry_summary: string;
  recommended_actions: string[];
  caution_notes: string[];
}

export interface ManagerVisibilityRuntimeContext {
  templateKey: string | null;
  orgCustomizationKey: string;
  fallbackToBase: boolean;
  runtimeConfigExplain: RuntimeConfigExplainSnapshot | null;
  managerFocusMetricPriority: string[];
  executiveMetricPriority: string[];
  reportMetricPriority: string[];
  recommendedActionPriority: string[];
  defaultDateRangeDays: number | null;
  eventTypePreferenceWeights: Partial<Record<BusinessEvent["eventType"], number>>;
}

const EVENT_TYPE_BY_METRIC_HINT: Record<string, BusinessEvent["eventType"][]> = {
  trial_activation_rate_7d: ["trial_stalled", "trial_activated"],
  days_to_first_value: ["trial_stalled"],
  renewal_risk_account_count: ["renewal_risk_detected", "renewal_due_soon"],
  expansion_pipeline_amount: ["expansion_signal", "conversion_signal"],
  technical_checkpoint_pass_rate: ["deal_blocked", "manager_attention_escalated"],
  procurement_blocked_count: ["deal_blocked"],
  key_account_blocked_amount: ["deal_blocked", "manager_attention_escalated"],
  followup_timeliness_score: ["no_recent_touchpoint", "health_declined"],
  high_risk_unresolved_count: ["health_declined", "renewal_risk_detected"],
  team_execution_score: ["manager_attention_escalated", "onboarding_stuck"],
  open_events: ["deal_blocked", "health_declined", "renewal_risk_detected", "trial_stalled"],
  critical_risks: ["deal_blocked", "health_declined", "renewal_risk_detected"],
  renewal_at_risk: ["renewal_risk_detected", "renewal_due_soon"],
  conversion_readiness: ["trial_stalled", "conversion_signal"],
  converted_count: ["conversion_signal"]
};

const EVENT_TYPE_BY_RISK_PATTERN: Record<string, BusinessEvent["eventType"][]> = {
  trial_no_activation_72h: ["trial_stalled"],
  renewal_window_silent: ["renewal_risk_detected", "renewal_due_soon"],
  sample_feedback_missing: ["no_recent_touchpoint"],
  procurement_gate_stalled: ["deal_blocked", "manager_attention_escalated"]
};

const BUSINESS_EVENT_SEVERITY_RANK: Record<BusinessEvent["severity"], number> = {
  critical: 3,
  warning: 2,
  info: 1
};

function normalizeKey(input: string | null | undefined): string | null {
  if (typeof input !== "string") return null;
  const normalized = input.trim().toLowerCase();
  return normalized.length > 0 ? normalized : null;
}

function uniqueStrings(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter((item) => item.length > 0)));
}

function resolveTemplateAlias(templateKey: string | null): string | null {
  if (!templateKey) return null;
  if (TEMPLATE_KEY_ALIAS_MAP[templateKey]) return TEMPLATE_KEY_ALIAS_MAP[templateKey];
  return getIndustryTemplateSeedByKey(templateKey) ? templateKey : null;
}

function resolveOrgCustomizationKey(params: {
  resolvedTemplateKey: string | null;
  assignedTemplateKey: string | null;
  requestedOrgCustomizationKey: string | null;
}): string {
  const byRequest = params.requestedOrgCustomizationKey;
  if (byRequest && getOrgCustomizationSeedByKey(byRequest)) {
    return byRequest;
  }

  const byAssignedTemplate = inferCustomizationKeyByTemplate(params.assignedTemplateKey);
  if (byAssignedTemplate) {
    return byAssignedTemplate;
  }

  if (params.resolvedTemplateKey && TEMPLATE_TO_ORG_CUSTOMIZATION_MAP[params.resolvedTemplateKey]) {
    return TEMPLATE_TO_ORG_CUSTOMIZATION_MAP[params.resolvedTemplateKey];
  }

  return "default_org_customization";
}

function inferCustomizationKeyByTemplate(templateKey: string | null): string | null {
  const resolved = resolveTemplateAlias(templateKey);
  if (!resolved) return null;
  return TEMPLATE_TO_ORG_CUSTOMIZATION_MAP[resolved] ?? null;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asNumberRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const record = value as Record<string, unknown>;
  const result: Record<string, number> = {};
  for (const [key, raw] of Object.entries(record)) {
    const numeric = Number(raw);
    if (Number.isFinite(numeric)) {
      result[key] = numeric;
    }
  }
  return result;
}

function cloneOrgCustomizationConfig(config: OrgCustomizationConfig): OrgCustomizationConfig {
  return {
    ...config,
    scope: [...config.scope],
    featurePreferences: config.featurePreferences.map((item) => ({ ...item })),
    thresholdPreferences: config.thresholdPreferences.map((item) => ({
      ...item,
      targetPatternKeys: [...item.targetPatternKeys]
    })),
    automationRulePreferences: Object.fromEntries(
      Object.entries(config.automationRulePreferences).map(([key, value]) => [
        key,
        {
          ...value
        }
      ])
    ),
    onboardingPreferences: {
      ...config.onboardingPreferences,
      preferredChecklistKeys: [...config.onboardingPreferences.preferredChecklistKeys],
      customHints: [...config.onboardingPreferences.customHints]
    },
    importMappingPreferences: {
      ...config.importMappingPreferences,
      preferredColumnAliases: Object.fromEntries(
        Object.entries(config.importMappingPreferences.preferredColumnAliases).map(([key, value]) => [
          key,
          [...value]
        ])
      ),
      customHints: [...config.importMappingPreferences.customHints]
    },
    templateSelection: {
      ...config.templateSelection,
      enabledTemplateKeys: [...config.templateSelection.enabledTemplateKeys],
      disabledTemplateKeys: [...config.templateSelection.disabledTemplateKeys],
      stageVocabularyOverrides: { ...config.templateSelection.stageVocabularyOverrides },
      stageHintOverrides: { ...config.templateSelection.stageHintOverrides },
      managerFocusMetricOverrides: [...config.templateSelection.managerFocusMetricOverrides],
      importMappingHintOverrides: [...config.templateSelection.importMappingHintOverrides]
    },
    promptStrategyPreference: {
      ...config.promptStrategyPreference,
      additionalPromptHooks: config.promptStrategyPreference.additionalPromptHooks.map((item) => ({ ...item })),
      scenarioStrategy: Object.fromEntries(
        Object.entries(config.promptStrategyPreference.scenarioStrategy).map(([key, value]) => [
          key,
          { ...value }
        ])
      )
    },
    reportingPreference: {
      ...config.reportingPreference,
      managerMetricFilters: [...config.reportingPreference.managerMetricFilters],
      executiveMetricFilters: [...config.reportingPreference.executiveMetricFilters]
    },
    guardrails: { ...config.guardrails }
  };
}

function createRuntimeSourceSnapshot(params: {
  mode: RuntimeSourceSnapshot["resolvedMode"];
  fallbackProfileKey: string;
  persistedUsage?: Partial<RuntimeSourceSnapshot["persistedUsage"]>;
  ignoredOverrides?: RuntimeSourceSnapshot["ignoredOverrides"];
  appliedOverrides?: RuntimeSourceSnapshot["appliedOverrides"];
}): RuntimeSourceSnapshot {
  return {
    sourcePriority: ["persisted_org_config", "org_fallback_profile", "code_seed_default"],
    resolvedMode: params.mode,
    fallbackProfileKey: params.fallbackProfileKey,
    persistedUsage: {
      assignment: params.persistedUsage?.assignment ?? false,
      overrides: params.persistedUsage?.overrides ?? false,
      orgSettings: params.persistedUsage?.orgSettings ?? false,
      orgAiSettings: params.persistedUsage?.orgAiSettings ?? false,
      orgFeatureFlags: params.persistedUsage?.orgFeatureFlags ?? false,
      automationRules: params.persistedUsage?.automationRules ?? false
    },
    ignoredOverrides: params.ignoredOverrides ?? [],
    appliedOverrides: params.appliedOverrides ?? []
  };
}

function updateThresholdPreference(params: {
  thresholdKey: OrgThresholdPreferenceKey;
  value: number;
  customization: OrgCustomizationConfig;
}): void {
  if (!Number.isFinite(params.value)) return;

  let threshold = params.customization.thresholdPreferences.find((item) => item.thresholdKey === params.thresholdKey);
  if (!threshold) {
    const fallback = getDefaultOrgCustomizationConfig().thresholdPreferences.find(
      (item) => item.thresholdKey === params.thresholdKey
    );
    if (!fallback) return;
    threshold = {
      ...fallback,
      targetPatternKeys: [...fallback.targetPatternKeys]
    };
    params.customization.thresholdPreferences.push(threshold);
  }

  const next = Math.max(threshold.minValue, Math.min(threshold.maxValue, params.value));
  threshold.value = next;
}

function applyAlertRuleMapToThresholds(params: {
  alertRules: Record<string, number>;
  customization: OrgCustomizationConfig;
}): void {
  if (Number.isFinite(params.alertRules.no_followup_timeout)) {
    updateThresholdPreference({
      customization: params.customization,
      thresholdKey: "alert_no_followup_days",
      value: params.alertRules.no_followup_timeout
    });
  }
  if (Number.isFinite(params.alertRules.quoted_but_stalled)) {
    updateThresholdPreference({
      customization: params.customization,
      thresholdKey: "alert_stalled_opportunity_days",
      value: params.alertRules.quoted_but_stalled
    });
  }
  if (Number.isFinite(params.alertRules.high_probability_stalled)) {
    updateThresholdPreference({
      customization: params.customization,
      thresholdKey: "followup_sla_days",
      value: Math.max(1, params.alertRules.high_probability_stalled - 1)
    });
  }
}

function applyTemplateOverridesToCustomization(params: {
  overrides: OrgTemplateOverride[];
  customization: OrgCustomizationConfig;
}): {
  ignoredOverrides: RuntimeSourceSnapshot["ignoredOverrides"];
  appliedOverrides: RuntimeSourceSnapshot["appliedOverrides"];
} {
  const ignored: RuntimeSourceSnapshot["ignoredOverrides"] = [];
  const applied: RuntimeSourceSnapshot["appliedOverrides"] = [];

  for (const override of params.overrides) {
    const validation = validateOrgTemplateOverride({
      overrideType: override.overrideType,
      overridePayload: override.overridePayload
    });

    if (!validation.validForWrite) {
      ignored.push({
        overrideType: override.overrideType,
        layer: validation.layer,
        reason: validation.reason ?? "override_payload_invalid",
        diagnostics: validation.diagnostics
      });
      continue;
    }

    if (!validation.acceptedForRuntime) {
      ignored.push({
        overrideType: override.overrideType,
        layer: validation.layer,
        reason: validation.reason ?? "runtime_layer_rejected",
        diagnostics: validation.diagnostics
      });
      continue;
    }

    if (validation.layer !== "runtime_preference_overrides") {
      ignored.push({
        overrideType: override.overrideType,
        layer: validation.layer,
        reason: "override_layer_not_runtime_preference",
        diagnostics: validation.diagnostics
      });
      continue;
    }

    const payload = asObject(validation.normalizedPayload);
    if (override.overrideType === "alert_rules") {
      const rulePayload = asObject(payload.rules);
      applyAlertRuleMapToThresholds({
        alertRules: asNumberRecord(rulePayload),
        customization: params.customization
      });
      applied.push({
        overrideType: override.overrideType,
        layer: "runtime_preference_overrides",
        appliedFields: ["thresholdPreferences.alert_no_followup_days", "thresholdPreferences.alert_stalled_opportunity_days"]
      });
      continue;
    }

    if (override.overrideType === "brief_preferences") {
      const metrics = uniqueStrings(asStringArray(payload.items));
      if (metrics.length > 0) {
        params.customization.reportingPreference.managerMetricFilters = metrics;
        applied.push({
          overrideType: override.overrideType,
          layer: "runtime_preference_overrides",
          appliedFields: ["reportingPreference.managerMetricFilters"]
        });
      }
      continue;
    }

    if (override.overrideType === "prep_preferences") {
      const prepItems = uniqueStrings(asStringArray(payload.items));
      if (prepItems.length > 0) {
        const joined = prepItems.slice(0, 3).join(", ");
        params.customization.promptStrategyPreference.scenarioStrategy.followup_analysis = {
          mode: "append_checklist",
          promptPatch: `Organization prep focus: ${joined}`
        };
        applied.push({
          overrideType: override.overrideType,
          layer: "runtime_preference_overrides",
          appliedFields: ["promptStrategyPreference.scenarioStrategy.followup_analysis"]
        });
      }
    }
  }

  return {
    ignoredOverrides: ignored,
    appliedOverrides: applied
  };
}

function applyFeatureFlagMapToCustomization(params: {
  featureFlagMap: Partial<Record<OrgFeatureKey, boolean>>;
  customization: OrgCustomizationConfig;
}): void {
  for (const preference of params.customization.featurePreferences) {
    if (typeof params.featureFlagMap[preference.featureKey] === "boolean") {
      preference.enabled = Boolean(params.featureFlagMap[preference.featureKey]);
      preference.source = "org_override";
      preference.note = "resolved_from_org_feature_flags";
    }
  }
}

function applyOrgSettingsToCustomization(params: {
  orgSettings: PersistedOrgRuntimeSources["orgSettings"];
  customization: OrgCustomizationConfig;
}): void {
  if (!params.orgSettings) return;
  const alertRules = asNumberRecord(params.orgSettings.defaultAlertRules);
  applyAlertRuleMapToThresholds({
    alertRules,
    customization: params.customization
  });

  if (Number.isFinite(params.orgSettings.defaultFollowupSlaDays)) {
    updateThresholdPreference({
      customization: params.customization,
      thresholdKey: "followup_sla_days",
      value: Number(params.orgSettings.defaultFollowupSlaDays)
    });
  }

  const completedChecklistKeys = uniqueStrings(
    Object.entries(params.orgSettings.onboardingStepState ?? {})
      .filter(([, value]) => value === true)
      .map(([key]) => key)
  );
  if (completedChecklistKeys.length > 0) {
    params.customization.onboardingPreferences.preferredChecklistKeys = uniqueStrings([
      ...completedChecklistKeys,
      ...params.customization.onboardingPreferences.preferredChecklistKeys
    ]);
  }
}

function applyOrgAiSettingsToCustomization(params: {
  orgAiSettings: PersistedOrgRuntimeSources["orgAiSettings"];
  customization: OrgCustomizationConfig;
}): void {
  if (!params.orgAiSettings) return;

  if (params.orgAiSettings.fallbackMode === "rules_only") {
    params.customization.promptStrategyPreference.mode = "org_overlay";
  }

  if (params.orgAiSettings.humanReviewRequiredForSensitiveActions) {
    params.customization.promptStrategyPreference.scenarioStrategy.followup_analysis = {
      mode: "inject_constraints",
      promptPatch: "Human review required for sensitive actions. Keep escalation path explicit."
    };
    params.customization.promptStrategyPreference.additionalPromptHooks = uniquePromptHooks([
      ...params.customization.promptStrategyPreference.additionalPromptHooks,
      {
        hookKey: "org_human_review_guardrail",
        scenario: "followup_analysis",
        strategy: "inject_constraints",
        promptPatch: "Human review required for sensitive actions. Keep escalation path explicit."
      }
    ]);
  }
}

function uniquePromptHooks(
  hooks: OrgCustomizationConfig["promptStrategyPreference"]["additionalPromptHooks"]
): OrgCustomizationConfig["promptStrategyPreference"]["additionalPromptHooks"] {
  const map = new Map<string, OrgCustomizationConfig["promptStrategyPreference"]["additionalPromptHooks"][number]>();
  for (const hook of hooks) {
    map.set(hook.hookKey, hook);
  }
  return Array.from(map.values());
}

function resolveAutomationPreferenceKey(ruleKey: string): string {
  if (ruleKey in AUTOMATION_RULE_PREFERENCE_ALIAS_MAP) {
    return AUTOMATION_RULE_PREFERENCE_ALIAS_MAP[ruleKey];
  }
  return ruleKey;
}

function applyAutomationRuleSourcesToCustomization(params: {
  automationRuleSources: PersistedAutomationRuleRuntimeSource[];
  customization: OrgCustomizationConfig;
}): void {
  for (const source of params.automationRuleSources) {
    const preferenceKey = resolveAutomationPreferenceKey(source.ruleKey);
    const current =
      params.customization.automationRulePreferences[preferenceKey] ??
      params.customization.automationRulePreferences[source.ruleKey];
    if (!current) continue;

    const primaryConditionKey = pickPrimaryConditionKey(source.ruleKey);
    const thresholdOverride =
      primaryConditionKey && Number.isFinite(Number(source.conditionsJson[primaryConditionKey]))
        ? Number(source.conditionsJson[primaryConditionKey])
        : current.thresholdOverride;
    const next = {
      ...current,
      enabled: source.isEnabled,
      thresholdOverride,
      note: "resolved_from_automation_rules"
    };
    params.customization.automationRulePreferences[preferenceKey] = next;
  }
}

function applyTemplateSelectionFromAssignment(params: {
  assignedTemplateKey: string | null;
  customization: OrgCustomizationConfig;
}): void {
  if (!params.assignedTemplateKey) return;
  const normalized = resolveTemplateAlias(params.assignedTemplateKey) ?? params.assignedTemplateKey;
  if (!normalized) return;

  params.customization.templateSelection.defaultTemplateKey = normalized;
  params.customization.templateSelection.enabledTemplateKeys = uniqueStrings([normalized]);
  params.customization.templateSelection.disabledTemplateKeys = params.customization.templateSelection.disabledTemplateKeys.filter(
    (item) => item !== normalized
  );
}

function applyPersistedSourcesToCustomization(params: {
  baseCustomization: OrgCustomizationConfig;
  persistedSources: PersistedOrgRuntimeSources;
}): {
  orgCustomization: OrgCustomizationConfig;
  runtimeSourceSnapshot: RuntimeSourceSnapshot;
} {
  const customization = cloneOrgCustomizationConfig(params.baseCustomization);

  if (params.persistedSources.featureFlagMap) {
    applyFeatureFlagMapToCustomization({
      featureFlagMap: params.persistedSources.featureFlagMap,
      customization
    });
  }
  applyOrgSettingsToCustomization({
    orgSettings: params.persistedSources.orgSettings,
    customization
  });
  applyOrgAiSettingsToCustomization({
    orgAiSettings: params.persistedSources.orgAiSettings,
    customization
  });
  applyAutomationRuleSourcesToCustomization({
    automationRuleSources: params.persistedSources.automationRuleSources,
    customization
  });
  applyTemplateSelectionFromAssignment({
    assignedTemplateKey: params.persistedSources.assignedTemplateKey,
    customization
  });
  const overrideRuntimeResult = applyTemplateOverridesToCustomization({
    overrides: params.persistedSources.templateOverrides,
    customization
  });

  const persistedUsage = {
    assignment: Boolean(params.persistedSources.assignedTemplateKey),
    overrides: params.persistedSources.templateOverrides.length > 0,
    orgSettings: Boolean(params.persistedSources.orgSettings),
    orgAiSettings: Boolean(params.persistedSources.orgAiSettings),
    orgFeatureFlags: Boolean(params.persistedSources.featureFlagMap),
    automationRules: params.persistedSources.automationRuleSources.length > 0
  };
  const hasPersistedUsage = Object.values(persistedUsage).some(Boolean);

  return {
    orgCustomization: customization,
    runtimeSourceSnapshot: createRuntimeSourceSnapshot({
      mode: hasPersistedUsage ? "persisted_preferred" : "seed_only",
      fallbackProfileKey: params.baseCustomization.customizationKey,
      persistedUsage,
      ignoredOverrides: overrideRuntimeResult.ignoredOverrides,
      appliedOverrides: overrideRuntimeResult.appliedOverrides
    })
  };
}

async function listPersistedAutomationRuleSources(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<PersistedAutomationRuleRuntimeSource[]> {
  const res = await (params.supabase as any)
    .from("automation_rules")
    .select("rule_key,is_enabled,conditions_json")
    .eq("org_id", params.orgId);

  if (res.error) {
    if (String(res.error.message ?? "").includes("automation_rules") || String(res.error.message ?? "").includes("does not exist")) {
      return [];
    }
    throw new Error(res.error.message);
  }

  return ((res.data ?? []) as Array<{ rule_key: string; is_enabled: boolean; conditions_json: Record<string, unknown> | null }>).map(
    (row) => ({
      ruleKey: String(row.rule_key),
      isEnabled: Boolean(row.is_enabled),
      conditionsJson: asObject(row.conditions_json)
    })
  );
}

async function loadPersistedOrgRuntimeSources(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<PersistedOrgRuntimeSources> {
  const [templateContext, orgSettings, orgAiSettings, featureFlagMap, automationRuleSources] = await Promise.all([
    getCurrentOrgTemplateContext({
      supabase: params.supabase,
      orgId: params.orgId
    }).catch(() => null),
    getOrgSettings({
      supabase: params.supabase,
      orgId: params.orgId
    }).catch(() => null),
    getOrgAiSettings({
      supabase: params.supabase,
      orgId: params.orgId
    }).catch(() => null),
    getOrgFeatureFlagMap({
      supabase: params.supabase,
      orgId: params.orgId
    }).catch(() => null),
    listPersistedAutomationRuleSources({
      supabase: params.supabase,
      orgId: params.orgId
    }).catch(() => [])
  ]);

  return {
    assignedTemplateKey: normalizeKey(templateContext?.template?.templateKey ?? null),
    templateOverrides: templateContext?.overrides ?? [],
    orgSettings: orgSettings
      ? {
          defaultAlertRules: orgSettings.defaultAlertRules,
          defaultFollowupSlaDays: orgSettings.defaultFollowupSlaDays,
          onboardingStepState: orgSettings.onboardingStepState
        }
      : null,
    orgAiSettings: orgAiSettings
      ? {
          fallbackMode: orgAiSettings.fallbackMode,
          humanReviewRequiredForSensitiveActions: orgAiSettings.humanReviewRequiredForSensitiveActions
        }
      : null,
    featureFlagMap,
    automationRuleSources
  };
}

function cloneRuleSeed(seed: AutomationRuleSeed): AutomationRuleSeed {
  return {
    ...seed,
    conditionsJson: { ...seed.conditionsJson },
    actionJson: { ...seed.actionJson }
  };
}

function pickPrimaryConditionKey(ruleKey: string): string | null {
  switch (ruleKey) {
    case "high_risk_customer_inactivity":
      return "daysWithoutFollowup";
    case "quoted_no_reply":
      return "daysAfterQuoteNoReply";
    case "trial_activated_no_first_value":
      return "daysAfterActivation";
    case "onboarding_stuck":
      return "onboardingStuckDays";
    case "blocked_checkpoint_timeout":
      return "blockedDays";
    case "high_priority_deal_no_touchpoint":
      return "touchpointMissingDays";
    case "manager_attention_no_new_action":
      return "staleDays";
    case "trial_org_no_core_activity":
      return "inactivityDays";
    case "renewal_activity_decline":
      return "watchWindowDays";
    default:
      return null;
  }
}

function getAutomationPreferenceForRule(params: {
  ruleKey: string;
  preferences: OrgCustomizationConfig["automationRulePreferences"];
}):
  | {
      enabled: boolean;
      thresholdOverride: number | null;
      note: string | null;
    }
  | null {
  const direct = params.preferences[params.ruleKey];
  if (direct) return direct;

  const aliasKey = AUTOMATION_RULE_PREFERENCE_ALIAS_MAP[params.ruleKey];
  if (!aliasKey) return null;
  return params.preferences[aliasKey] ?? null;
}

export function buildResolvedIndustryTemplateContext(params: {
  templateKey?: string | null;
  orgCustomizationKey?: string | null;
  persistedOrgRuntime?: PersistedOrgRuntimeSources | null;
}): ResolvedIndustryTemplateRuntimeContext {
  const requestedTemplateKey = normalizeKey(params.templateKey);
  const requestedOrgCustomizationKey = normalizeKey(params.orgCustomizationKey);
  const assignedTemplateKey = normalizeKey(params.persistedOrgRuntime?.assignedTemplateKey ?? null);

  const resolvedTemplateKey = resolveTemplateAlias(requestedTemplateKey ?? assignedTemplateKey);
  const template = resolvedTemplateKey ? getIndustryTemplateSeedByKey(resolvedTemplateKey) : null;
  const appliedOrgCustomizationKey = resolveOrgCustomizationKey({
    resolvedTemplateKey,
    assignedTemplateKey,
    requestedOrgCustomizationKey
  });

  const baseOrgCustomization =
    getOrgCustomizationSeedByKey(appliedOrgCustomizationKey) ??
    getDefaultOrgCustomizationConfig();
  const persistedResolved = params.persistedOrgRuntime
    ? applyPersistedSourcesToCustomization({
        baseCustomization: baseOrgCustomization,
        persistedSources: params.persistedOrgRuntime
      })
    : null;
  const orgCustomization = persistedResolved?.orgCustomization ?? baseOrgCustomization;
  const runtimeSourceSnapshot =
    persistedResolved?.runtimeSourceSnapshot ??
    createRuntimeSourceSnapshot({
      mode: "seed_only",
      fallbackProfileKey: baseOrgCustomization.customizationKey
    });

  if (!template) {
    return {
      requestedTemplateKey,
      resolvedTemplateKey: null,
      template: null,
      appliedOrgCustomizationKey: orgCustomization.customizationKey,
      orgCustomization,
      merged: null,
      fallbackToBase: true,
      runtimeSourceSnapshot
    };
  }

  const merged = mergeTemplateWithOrgCustomization({
    template,
    orgCustomization
  });

  return {
    requestedTemplateKey,
    resolvedTemplateKey: template.templateKey,
    template,
    appliedOrgCustomizationKey: orgCustomization.customizationKey,
    orgCustomization,
    merged,
    fallbackToBase: !merged.templateEnabled,
    runtimeSourceSnapshot
  };
}

export async function buildResolvedOrgRuntimeConfig(params: {
  supabase: DbClient;
  orgId: string;
  templateKey?: string | null;
  orgCustomizationKey?: string | null;
}): Promise<ResolvedIndustryTemplateRuntimeContext> {
  const persistedOrgRuntime = await loadPersistedOrgRuntimeSources({
    supabase: params.supabase,
    orgId: params.orgId
  });
  return buildResolvedIndustryTemplateContext({
    templateKey: params.templateKey,
    orgCustomizationKey: params.orgCustomizationKey,
    persistedOrgRuntime
  });
}

function resolveExplainSourceKind(params: {
  persistedEnabled: boolean;
  hasFallbackProfileSignal: boolean;
}): RuntimeExplainSourceKind {
  if (params.persistedEnabled) return "persisted_source";
  if (params.hasFallbackProfileSignal) return "fallback_profile";
  return "seed_default";
}

export function buildRuntimeConfigExplainSnapshot(
  context: ResolvedIndustryTemplateRuntimeContext
): RuntimeConfigExplainSnapshot {
  const persistedUsage = context.runtimeSourceSnapshot.persistedUsage;
  const hasPersistedSignal = Object.values(persistedUsage).some(Boolean);
  const hasFallbackProfileSignal =
    context.runtimeSourceSnapshot.fallbackProfileKey !== "default_org_customization" ||
    Boolean(context.requestedTemplateKey) ||
    Boolean(context.resolvedTemplateKey);
  const diagnostics: string[] = [];

  for (const ignored of context.runtimeSourceSnapshot.ignoredOverrides) {
    diagnostics.push(
      `ignored_override:${ignored.overrideType}:${ignored.layer}:${ignored.reason}`
    );
    for (const item of ignored.diagnostics) {
      diagnostics.push(`ignored_override_diagnostic:${ignored.overrideType}:${item}`);
    }
  }

  return {
    sourcePriority: context.runtimeSourceSnapshot.sourcePriority,
    resolvedMode: context.runtimeSourceSnapshot.resolvedMode,
    fallbackProfileKey: context.runtimeSourceSnapshot.fallbackProfileKey,
    resolvedTemplateKey: context.resolvedTemplateKey,
    appliedOrgCustomizationKey: context.appliedOrgCustomizationKey,
    keyFieldSources: {
      resolvedTemplateKey: resolveExplainSourceKind({
        persistedEnabled: persistedUsage.assignment,
        hasFallbackProfileSignal
      }),
      orgCustomizationProfile: resolveExplainSourceKind({
        persistedEnabled: hasPersistedSignal,
        hasFallbackProfileSignal
      }),
      thresholdPreferences: resolveExplainSourceKind({
        persistedEnabled: persistedUsage.orgSettings || persistedUsage.overrides,
        hasFallbackProfileSignal
      }),
      promptPreference: resolveExplainSourceKind({
        persistedEnabled: persistedUsage.orgAiSettings || persistedUsage.overrides,
        hasFallbackProfileSignal
      }),
      featurePreferences: resolveExplainSourceKind({
        persistedEnabled: persistedUsage.orgFeatureFlags,
        hasFallbackProfileSignal
      })
    },
    persistedUsage,
    appliedOverrides: context.runtimeSourceSnapshot.appliedOverrides,
    ignoredOverrides: context.runtimeSourceSnapshot.ignoredOverrides,
    diagnostics
  };
}

export function summarizeResolvedIndustryTemplateContext(context: ResolvedIndustryTemplateRuntimeContext): Record<string, unknown> {
  return {
    requested_template_key: context.requestedTemplateKey,
    resolved_template_key: context.resolvedTemplateKey,
    org_customization_key: context.appliedOrgCustomizationKey,
    fallback_to_base: context.fallbackToBase,
    has_merged_overlay: Boolean(context.merged),
    priority_order: context.merged?.priorityOrder ?? ["base_semantics"],
    runtime_source: context.runtimeSourceSnapshot,
    runtime_config_explain: buildRuntimeConfigExplainSnapshot(context)
  };
}

export function buildRuntimeTemplateConfigOverlay(context: ResolvedIndustryTemplateRuntimeContext): Partial<TemplateConfigDraft> {
  if (!context.merged || context.fallbackToBase) return {};

  const thresholds = context.merged.effectiveThresholdMap;
  const alertRules: Record<string, number> = {};

  if (typeof thresholds.alert_no_followup_days === "number") {
    alertRules.no_followup_timeout = thresholds.alert_no_followup_days;
  }
  if (typeof thresholds.alert_stalled_opportunity_days === "number") {
    alertRules.quoted_but_stalled = thresholds.alert_stalled_opportunity_days;
  }
  if (typeof thresholds.followup_sla_days === "number") {
    alertRules.high_probability_stalled = Math.max(2, thresholds.followup_sla_days + 1);
  }

  return {
    alertRules,
    managerAttentionSignals: context.merged.effectiveManagerFocusMetrics.map((item) => `metric:${item}`),
    prepPreferences: context.merged.effectiveRecommendedActionLibrary.map((item) => item.title),
    briefPreferences: context.merged.effectiveManagerFocusMetrics,
    recommendedOnboardingPath: context.merged.effectiveOnboardingHints,
    demoSeedProfile: context.resolvedTemplateKey ? `${context.resolvedTemplateKey}_demo` : "generic_demo"
  };
}

export function applyRuntimeTemplateConfigOverlay(
  base: TemplateConfigDraft,
  context: ResolvedIndustryTemplateRuntimeContext
): TemplateConfigDraft {
  const overlay = buildRuntimeTemplateConfigOverlay(context);
  if (Object.keys(overlay).length === 0) {
    return {
      ...base,
      customerStages: [...base.customerStages],
      opportunityStages: [...base.opportunityStages],
      alertRules: { ...base.alertRules },
      checkpoints: [...base.checkpoints],
      managerAttentionSignals: [...base.managerAttentionSignals],
      prepPreferences: [...base.prepPreferences],
      briefPreferences: [...base.briefPreferences],
      recommendedOnboardingPath: [...base.recommendedOnboardingPath]
    };
  }

  return {
    ...base,
    customerStages: [...base.customerStages],
    opportunityStages: [...base.opportunityStages],
    alertRules: {
      ...base.alertRules,
      ...(overlay.alertRules ?? {})
    },
    checkpoints: [...base.checkpoints],
    managerAttentionSignals: uniqueStrings([
      ...base.managerAttentionSignals,
      ...(overlay.managerAttentionSignals ?? [])
    ]),
    prepPreferences: uniqueStrings([
      ...base.prepPreferences,
      ...(overlay.prepPreferences ?? [])
    ]),
    briefPreferences: uniqueStrings([
      ...base.briefPreferences,
      ...(overlay.briefPreferences ?? [])
    ]),
    recommendedOnboardingPath: uniqueStrings([
      ...base.recommendedOnboardingPath,
      ...(overlay.recommendedOnboardingPath ?? [])
    ]),
    demoSeedProfile: overlay.demoSeedProfile ?? base.demoSeedProfile
  };
}

export function buildRuntimePlaybookSeedEntries(context: ResolvedIndustryTemplateRuntimeContext): RuntimePlaybookSeedEntry[] {
  if (!context.merged || context.fallbackToBase) return [];

  return context.merged.effectiveRecommendedActionLibrary.slice(0, 2).map((item) => ({
    entry_title: `[Runtime] ${item.title}`,
    entry_summary: item.actionSummary,
    recommended_actions: [item.whenToUse, item.expectedOutcome],
    caution_notes: [
      "Runtime bridge entry from template + org customization.",
      `owner_hint=${item.ownerRoleHint}`
    ]
  }));
}

export function sortOnboardingChecklistByPreferredKeys<T extends { key: string }>(
  items: T[],
  preferredKeys: string[]
): T[] {
  if (preferredKeys.length === 0) return [...items];

  const order = new Map<string, number>();
  preferredKeys.forEach((key, index) => {
    order.set(key, index);
  });

  return [...items].sort((left, right) => {
    const leftOrder = order.get(left.key);
    const rightOrder = order.get(right.key);
    if (leftOrder === undefined && rightOrder === undefined) return 0;
    if (leftOrder === undefined) return 1;
    if (rightOrder === undefined) return -1;
    return leftOrder - rightOrder;
  });
}

export function buildPromptAugmentationContext(params: {
  scenario: AiScenario;
  context: ResolvedIndustryTemplateRuntimeContext;
}): string | null {
  if (!params.context.merged || params.context.fallbackToBase) return null;

  const hooks = params.context.merged.effectivePromptHooks.filter((item) => item.scenario === params.scenario);
  if (hooks.length === 0) return null;

  const lines = [
    "Runtime template/org augmentation (content-only, do not change output schema):",
    `- template_key: ${params.context.resolvedTemplateKey}`,
    `- org_customization_key: ${params.context.appliedOrgCustomizationKey}`
  ];

  for (const hook of hooks) {
    lines.push(`- ${hook.strategy}: ${hook.promptPatch}`);
  }

  return lines.join("\n");
}

function isManagerReportType(reportType: ReportType): boolean {
  return reportType === "manager_daily" || reportType === "manager_weekly";
}

function addEventTypeWeight(
  map: Partial<Record<BusinessEvent["eventType"], number>>,
  eventType: BusinessEvent["eventType"],
  weight: number
): void {
  map[eventType] = Number((map[eventType] ?? 0) + weight);
}

function addMetricDrivenEventTypeWeights(params: {
  metrics: string[];
  multiplier: number;
  target: Partial<Record<BusinessEvent["eventType"], number>>;
}): void {
  for (const [index, metric] of params.metrics.entries()) {
    const hints = EVENT_TYPE_BY_METRIC_HINT[metric] ?? [];
    const weight = Math.max(1, params.metrics.length - index) * params.multiplier;
    for (const eventType of hints) {
      addEventTypeWeight(params.target, eventType, weight);
    }
  }
}

function addRiskPatternDrivenEventTypeWeights(params: {
  patternKey: string;
  severityHint: "info" | "warning" | "critical";
  target: Partial<Record<BusinessEvent["eventType"], number>>;
}): void {
  const hints = EVENT_TYPE_BY_RISK_PATTERN[params.patternKey] ?? [];
  if (hints.length === 0) return;

  const severityWeight = params.severityHint === "critical" ? 6 : params.severityHint === "warning" ? 4 : 2;
  for (const eventType of hints) {
    addEventTypeWeight(params.target, eventType, severityWeight);
  }
}

function getEventPreferenceWeight(params: {
  event: BusinessEvent;
  context: ManagerVisibilityRuntimeContext;
}): number {
  return Number(params.context.eventTypePreferenceWeights[params.event.eventType] ?? 0);
}

export function buildManagerVisibilityRuntimeContext(params: {
  context: ResolvedIndustryTemplateRuntimeContext;
  reportType?: ReportType;
}): ManagerVisibilityRuntimeContext {
  const defaultContext: ManagerVisibilityRuntimeContext = {
    templateKey: params.context.resolvedTemplateKey,
    orgCustomizationKey: params.context.appliedOrgCustomizationKey,
    fallbackToBase: params.context.fallbackToBase || !params.context.merged,
    runtimeConfigExplain: buildRuntimeConfigExplainSnapshot(params.context),
    managerFocusMetricPriority: [],
    executiveMetricPriority: [],
    reportMetricPriority: [],
    recommendedActionPriority: [],
    defaultDateRangeDays: null,
    eventTypePreferenceWeights: {}
  };

  if (!params.context.merged || params.context.fallbackToBase) {
    return defaultContext;
  }

  const managerFocusMetricPriority = uniqueStrings([
    ...params.context.merged.effectiveManagerFocusMetrics,
    ...params.context.merged.effectiveReportingPreference.managerMetricFilters
  ]);

  const executiveMetricPriority = uniqueStrings([
    ...params.context.merged.effectiveReportingPreference.executiveMetricFilters,
    ...params.context.merged.effectiveManagerFocusMetrics
  ]);

  const reportMetricPriority = isManagerReportType(params.reportType ?? "manager_daily")
    ? uniqueStrings([
        ...params.context.merged.effectiveReportingPreference.managerMetricFilters,
        ...params.context.merged.effectiveManagerFocusMetrics
      ])
    : uniqueStrings([
        ...params.context.merged.effectiveReportingPreference.executiveMetricFilters,
        ...params.context.merged.effectiveManagerFocusMetrics
      ]);

  const recommendedActionPriority = uniqueStrings(
    params.context.merged.effectiveRecommendedActionLibrary.map((item) => item.title)
  );

  const eventTypePreferenceWeights: Partial<Record<BusinessEvent["eventType"], number>> = {};
  addMetricDrivenEventTypeWeights({
    metrics: managerFocusMetricPriority,
    multiplier: 3,
    target: eventTypePreferenceWeights
  });
  addMetricDrivenEventTypeWeights({
    metrics: executiveMetricPriority,
    multiplier: 2,
    target: eventTypePreferenceWeights
  });

  for (const riskPattern of params.context.merged.effectiveRiskPatterns) {
    addRiskPatternDrivenEventTypeWeights({
      patternKey: riskPattern.patternKey,
      severityHint: riskPattern.severityHint,
      target: eventTypePreferenceWeights
    });
  }

  return {
    templateKey: params.context.resolvedTemplateKey,
    orgCustomizationKey: params.context.appliedOrgCustomizationKey,
    fallbackToBase: false,
    runtimeConfigExplain: buildRuntimeConfigExplainSnapshot(params.context),
    managerFocusMetricPriority,
    executiveMetricPriority,
    reportMetricPriority,
    recommendedActionPriority,
    defaultDateRangeDays: params.context.merged.effectiveReportingPreference.defaultDateRangeDays,
    eventTypePreferenceWeights
  };
}

export function applyExecutiveEventPreference(params: {
  events: BusinessEvent[];
  context: ManagerVisibilityRuntimeContext;
}): BusinessEvent[] {
  if (params.events.length <= 1) return [...params.events];

  const hasPreferenceWeights = Object.keys(params.context.eventTypePreferenceWeights).length > 0;
  if (!hasPreferenceWeights) return [...params.events];

  return [...params.events].sort((left, right) => {
    const severityDiff = BUSINESS_EVENT_SEVERITY_RANK[right.severity] - BUSINESS_EVENT_SEVERITY_RANK[left.severity];
    if (severityDiff !== 0) return severityDiff;

    const preferenceDiff =
      getEventPreferenceWeight({
        event: right,
        context: params.context
      }) -
      getEventPreferenceWeight({
        event: left,
        context: params.context
      });
    if (preferenceDiff !== 0) return preferenceDiff;

    return new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime();
  });
}

export function applyManagerActionPreference(params: {
  actions: string[];
  context: ManagerVisibilityRuntimeContext;
  limit?: number;
}): string[] {
  if (params.actions.length === 0 && params.context.recommendedActionPriority.length === 0) return [];

  const preferredPrefix = params.context.recommendedActionPriority
    .slice(0, params.limit ?? 2)
    .filter(
      (title) =>
        !params.actions.some((action) => action.toLowerCase().includes(title.toLowerCase()))
    )
    .map((title) => `Priority action: ${title}`);

  return uniqueStrings([...preferredPrefix, ...params.actions]);
}

export function applyReportFocusOverlay(params: {
  reportType: ReportType;
  metricsSnapshot: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  context: ManagerVisibilityRuntimeContext;
}): {
  metricsSnapshot: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
} {
  const reportMetricPriority =
    params.context.reportMetricPriority.length > 0
      ? params.context.reportMetricPriority
      : params.context.managerFocusMetricPriority;

  return {
    metricsSnapshot: { ...params.metricsSnapshot },
    sourceSnapshot: {
      ...params.sourceSnapshot,
      runtime_preference_overlay: {
        template_key: params.context.templateKey,
        org_customization_key: params.context.orgCustomizationKey,
        fallback_to_base: params.context.fallbackToBase,
        report_type: params.reportType,
        highlight_metric_keys: reportMetricPriority,
        recommended_action_priority: params.context.recommendedActionPriority,
        default_date_range_days: params.context.defaultDateRangeDays,
        runtime_config_explain: params.context.runtimeConfigExplain
      }
    }
  };
}

export function buildExecutiveBriefAugmentation(params: {
  context: ManagerVisibilityRuntimeContext;
}): string | null {
  if (params.context.fallbackToBase) return null;
  if (params.context.managerFocusMetricPriority.length === 0 && params.context.recommendedActionPriority.length === 0) {
    return null;
  }

  const lines = [
    "Manager/executive runtime preference overlay (content-only):",
    `- template_key: ${params.context.templateKey ?? "none"}`,
    `- org_customization_key: ${params.context.orgCustomizationKey}`
  ];

  if (params.context.managerFocusMetricPriority.length > 0) {
    lines.push(`- manager_focus_metrics: ${params.context.managerFocusMetricPriority.slice(0, 6).join(", ")}`);
  }

  if (params.context.recommendedActionPriority.length > 0) {
    lines.push(`- preferred_actions: ${params.context.recommendedActionPriority.slice(0, 4).join(" | ")}`);
  }

  if (params.context.runtimeConfigExplain?.ignoredOverrides.length) {
    lines.push(
      `- ignored_overrides: ${params.context.runtimeConfigExplain.ignoredOverrides
        .map((item) => `${item.overrideType}:${item.reason}`)
        .join(", ")}`
    );
  }

  return lines.join("\n");
}

export function resolveAutomationRuleSeedsWithRuntime(params: {
  baseSeeds: AutomationRuleSeed[];
  context: ResolvedIndustryTemplateRuntimeContext;
}): RuntimeAutomationRuleSeed[] {
  const runtimeConfigExplain = buildRuntimeConfigExplainSnapshot(params.context);
  const resolutionDebug: AutomationSeedResolutionDebugContext = {
    source: runtimeConfigExplain.keyFieldSources.thresholdPreferences,
    resolvedMode: runtimeConfigExplain.resolvedMode,
    ignoredOverrideCount: runtimeConfigExplain.ignoredOverrides.length,
    runtimeConfigExplain
  };

  if (!params.context.merged || params.context.fallbackToBase) {
    return params.baseSeeds.map((seed) => ({
      seed: cloneRuleSeed(seed),
      isEnabled: true,
      resolutionDebug
    }));
  }

  const thresholds = params.context.merged.effectiveThresholdMap;
  const automationPreferences = params.context.merged.effectiveAutomationRulePreferences;

  return params.baseSeeds.map((baseSeed) => {
    const seed = cloneRuleSeed(baseSeed);
    let isEnabled = true;

    const preference = getAutomationPreferenceForRule({
      ruleKey: seed.ruleKey,
      preferences: automationPreferences
    });
    if (preference) {
      isEnabled = preference.enabled;
      const primaryConditionKey = pickPrimaryConditionKey(seed.ruleKey);
      if (primaryConditionKey && typeof preference.thresholdOverride === "number") {
        seed.conditionsJson[primaryConditionKey] = preference.thresholdOverride;
      }
    }

    if (seed.ruleKey === "high_risk_customer_inactivity" && typeof thresholds.alert_no_followup_days === "number") {
      seed.conditionsJson.daysWithoutFollowup = thresholds.alert_no_followup_days;
    }

    if (seed.ruleKey === "quoted_no_reply" && typeof thresholds.alert_stalled_opportunity_days === "number") {
      seed.conditionsJson.daysAfterQuoteNoReply = thresholds.alert_stalled_opportunity_days;
    }

    if (seed.ruleKey === "blocked_checkpoint_timeout" && typeof thresholds.alert_stalled_opportunity_days === "number") {
      seed.conditionsJson.blockedDays = Math.max(2, Math.floor(thresholds.alert_stalled_opportunity_days / 2));
    }

    if (seed.ruleKey === "onboarding_stuck" && typeof thresholds.rhythm_inactivity_days === "number") {
      seed.conditionsJson.onboardingStuckDays = thresholds.rhythm_inactivity_days;
    }

    if (seed.ruleKey === "high_priority_deal_no_touchpoint" && typeof thresholds.alert_no_followup_days === "number") {
      seed.conditionsJson.touchpointMissingDays = thresholds.alert_no_followup_days;
    }

    if (seed.ruleKey === "manager_attention_no_new_action" && typeof thresholds.rhythm_inactivity_days === "number") {
      seed.conditionsJson.staleDays = thresholds.rhythm_inactivity_days;
    }

    if (seed.ruleKey === "trial_org_no_core_activity" && typeof thresholds.rhythm_inactivity_days === "number") {
      seed.conditionsJson.inactivityDays = thresholds.rhythm_inactivity_days;
    }

    if (seed.ruleKey === "renewal_activity_decline" && typeof thresholds.renewal_watch_window_days === "number") {
      seed.conditionsJson.watchWindowDays = thresholds.renewal_watch_window_days;
    }

    return {
      seed,
      isEnabled,
      resolutionDebug
    };
  });
}
