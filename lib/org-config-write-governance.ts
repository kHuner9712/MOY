import { MOY_CORE_CUSTOMER_STAGES, MOY_CORE_OPPORTUNITY_STAGES } from "@/types/template";
import type { OrgFeatureKey } from "@/types/productization";

export type OrgConfigGovernanceTargetType =
  | "org_settings"
  | "org_ai_settings"
  | "org_feature_flags";

export type OrgConfigRuntimeImpactSummary =
  | "runtime_consumed"
  | "runtime_partial_ignored"
  | "runtime_ignored_non_runtime_fields"
  | "write_rejected";

export type OrgSettingsGovernancePatch = Partial<{
  orgDisplayName: string;
  brandName: string;
  industryHint: string | null;
  timezone: string;
  locale: string;
  defaultCustomerStages: string[];
  defaultOpportunityStages: string[];
  defaultAlertRules: Record<string, number>;
  defaultFollowupSlaDays: number;
  onboardingCompleted: boolean;
  onboardingStepState: Record<string, boolean>;
}>;

export type OrgAiSettingsGovernancePatch = Partial<{
  provider: "deepseek" | "openai" | "qwen" | "zhipu";
  modelDefault: string;
  modelReasoning: string;
  fallbackMode: "strict_provider_first" | "provider_then_rules" | "rules_only";
  autoAnalysisEnabled: boolean;
  autoPlanEnabled: boolean;
  autoBriefEnabled: boolean;
  autoTouchpointReviewEnabled: boolean;
  humanReviewRequiredForSensitiveActions: boolean;
  maxDailyAiRuns: number | null;
  maxMonthlyAiRuns: number | null;
}>;

export type OrgFeatureFlagsGovernancePatch = Partial<Record<OrgFeatureKey, boolean>>;

export interface OrgConfigWriteDiagnostics<TPatch extends Record<string, unknown>> {
  targetType: OrgConfigGovernanceTargetType;
  acceptedForWrite: boolean;
  normalizedPatch: TPatch;
  acceptedFields: string[];
  ignoredFields: string[];
  forbiddenFields: string[];
  diagnostics: string[];
  reason: string | null;
  runtimeConsumedFields: string[];
  runtimeIgnoredFields: string[];
  runtimeImpactSummary: OrgConfigRuntimeImpactSummary;
}

export interface OrgConfigWriteDiagnosticsSummary {
  acceptedFields: string[];
  ignoredFields: string[];
  forbiddenFields: string[];
  diagnostics: string[];
  runtimeConsumedFields: string[];
  runtimeIgnoredFields: string[];
  runtimeImpactSummary: OrgConfigRuntimeImpactSummary;
}

export interface OrgConfigWriteRejectionInfo {
  targetType: OrgConfigGovernanceTargetType;
  reason: string;
  diagnostics: string[];
  acceptedFields: string[];
  ignoredFields: string[];
  forbiddenFields: string[];
  runtimeImpactSummary: OrgConfigRuntimeImpactSummary;
}

const ALERT_RULE_ALLOWED_KEYS = new Set([
  "no_followup_timeout",
  "quoted_but_stalled",
  "high_probability_stalled"
]);

const CUSTOMER_STAGE_SET = new Set(MOY_CORE_CUSTOMER_STAGES as readonly string[]);
const OPPORTUNITY_STAGE_SET = new Set(MOY_CORE_OPPORTUNITY_STAGES as readonly string[]);
const ORG_AI_PROVIDER_SET = new Set(["deepseek", "openai", "qwen", "zhipu"]);
const ORG_AI_FALLBACK_MODE_SET = new Set([
  "strict_provider_first",
  "provider_then_rules",
  "rules_only"
]);
const FEATURE_KEY_SET = new Set<OrgFeatureKey>([
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
]);

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values.map((item) => item.trim()).filter((item) => item.length > 0)));
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value.trim() : null;
}

function asInteger(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (!Number.isInteger(value)) return null;
  return value;
}

function resolveRuntimeImpactSummary(params: {
  acceptedFields: string[];
  runtimeConsumedFields: string[];
  runtimeIgnoredFields: string[];
}): OrgConfigRuntimeImpactSummary {
  if (params.acceptedFields.length === 0) return "write_rejected";
  if (params.runtimeConsumedFields.length === 0) return "runtime_ignored_non_runtime_fields";
  if (params.runtimeIgnoredFields.length > 0) return "runtime_partial_ignored";
  return "runtime_consumed";
}

function buildWriteDiagnostics<TPatch extends Record<string, unknown>>(params: {
  targetType: OrgConfigGovernanceTargetType;
  normalizedPatch: TPatch;
  acceptedFields: string[];
  ignoredFields: string[];
  forbiddenFields: string[];
  diagnostics: string[];
  runtimeConsumedFields: string[];
  runtimeIgnoredFields: string[];
  reasonWhenRejected: string;
}): OrgConfigWriteDiagnostics<TPatch> {
  const acceptedFields = uniqueStrings(params.acceptedFields).sort((left, right) => left.localeCompare(right));
  const ignoredFields = uniqueStrings(params.ignoredFields).sort((left, right) => left.localeCompare(right));
  const forbiddenFields = uniqueStrings(params.forbiddenFields).sort((left, right) => left.localeCompare(right));
  const diagnostics = uniqueStrings(params.diagnostics);
  const runtimeConsumedFields = uniqueStrings(params.runtimeConsumedFields).sort((left, right) =>
    left.localeCompare(right)
  );
  const runtimeIgnoredFields = uniqueStrings(params.runtimeIgnoredFields).sort((left, right) =>
    left.localeCompare(right)
  );
  const acceptedForWrite = acceptedFields.length > 0;
  const runtimeImpactSummary = resolveRuntimeImpactSummary({
    acceptedFields,
    runtimeConsumedFields,
    runtimeIgnoredFields
  });

  return {
    targetType: params.targetType,
    acceptedForWrite,
    normalizedPatch: params.normalizedPatch,
    acceptedFields,
    ignoredFields,
    forbiddenFields,
    diagnostics,
    reason: acceptedForWrite ? null : params.reasonWhenRejected,
    runtimeConsumedFields,
    runtimeIgnoredFields,
    runtimeImpactSummary
  };
}

function normalizeStageList(
  value: unknown,
  kind: "defaultCustomerStages" | "defaultOpportunityStages"
): {
  accepted: boolean;
  normalized: string[];
  diagnostics: string[];
  forbidden: string[];
} {
  if (!Array.isArray(value)) {
    return {
      accepted: false,
      normalized: [],
      diagnostics: [`invalid_field_type:${kind}`],
      forbidden: []
    };
  }

  const items = uniqueStrings(value.filter((item): item is string => typeof item === "string"));
  if (items.length < 3) {
    return {
      accepted: false,
      normalized: [],
      diagnostics: [`invalid_field_value:${kind}:minimum_3_items_required`],
      forbidden: []
    };
  }

  const stageSet = kind === "defaultCustomerStages" ? CUSTOMER_STAGE_SET : OPPORTUNITY_STAGE_SET;
  const forbiddenValues = items.filter((item) => !stageSet.has(item));
  if (forbiddenValues.length > 0) {
    return {
      accepted: false,
      normalized: [],
      diagnostics: forbiddenValues.map((item) => `forbidden_core_semantic_value:${kind}:${item}`),
      forbidden: [kind]
    };
  }

  return {
    accepted: true,
    normalized: items,
    diagnostics: [],
    forbidden: []
  };
}

function normalizeAlertRules(value: unknown): {
  accepted: boolean;
  normalized: Record<string, number>;
  diagnostics: string[];
} {
  const ruleObject = asObject(value);
  const normalized: Record<string, number> = {};
  const diagnostics: string[] = [];

  for (const [key, rawValue] of Object.entries(ruleObject)) {
    if (!ALERT_RULE_ALLOWED_KEYS.has(key)) {
      diagnostics.push(`ignored_alert_rule_key:${key}`);
      continue;
    }
    const numeric = asInteger(rawValue);
    if (numeric === null || numeric < 1 || numeric > 90) {
      diagnostics.push(`invalid_alert_rule_value:${key}`);
      continue;
    }
    normalized[key] = numeric;
  }

  return {
    accepted: Object.keys(normalized).length > 0,
    normalized,
    diagnostics
  };
}

export function prepareOrgSettingsWrite(params: {
  patch: unknown;
}): OrgConfigWriteDiagnostics<OrgSettingsGovernancePatch> {
  const patch = asObject(params.patch);
  const normalizedPatch: OrgSettingsGovernancePatch = {};
  const acceptedFields: string[] = [];
  const ignoredFields: string[] = [];
  const forbiddenFields: string[] = [];
  const diagnostics: string[] = [];
  const runtimeConsumedFields: string[] = [];
  const runtimeIgnoredFields: string[] = [];

  for (const [key, value] of Object.entries(patch)) {
    if (key === "orgDisplayName") {
      const text = asString(value);
      if (!text || text.length > 120) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.orgDisplayName = text;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "brandName") {
      const text = asString(value);
      if (!text || text.length > 80) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.brandName = text;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "industryHint") {
      if (value === null) {
        normalizedPatch.industryHint = null;
        acceptedFields.push(key);
        runtimeIgnoredFields.push(key);
        continue;
      }
      const text = asString(value);
      if (!text || text.length > 120) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.industryHint = text;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "timezone") {
      const text = asString(value);
      if (!text || text.length < 2 || text.length > 64) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.timezone = text;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "locale") {
      const text = asString(value);
      if (!text || text.length < 2 || text.length > 24) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.locale = text;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "defaultCustomerStages" || key === "defaultOpportunityStages") {
      const normalized = normalizeStageList(value, key);
      if (!normalized.accepted) {
        ignoredFields.push(key);
        forbiddenFields.push(...normalized.forbidden);
        diagnostics.push(...normalized.diagnostics);
        continue;
      }
      if (key === "defaultCustomerStages") {
        normalizedPatch.defaultCustomerStages = normalized.normalized;
      } else {
        normalizedPatch.defaultOpportunityStages = normalized.normalized;
      }
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "defaultAlertRules") {
      const normalized = normalizeAlertRules(value);
      diagnostics.push(...normalized.diagnostics);
      if (!normalized.accepted) {
        ignoredFields.push(key);
        diagnostics.push("invalid_field_value:defaultAlertRules");
        continue;
      }
      normalizedPatch.defaultAlertRules = normalized.normalized;
      acceptedFields.push(key);
      runtimeConsumedFields.push(key);
      continue;
    }

    if (key === "defaultFollowupSlaDays") {
      const numeric = asInteger(value);
      if (numeric === null || numeric < 1 || numeric > 30) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.defaultFollowupSlaDays = numeric;
      acceptedFields.push(key);
      runtimeConsumedFields.push(key);
      continue;
    }

    if (key === "onboardingCompleted") {
      if (typeof value !== "boolean") {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_type:${key}`);
        continue;
      }
      normalizedPatch.onboardingCompleted = value;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "onboardingStepState") {
      const object = asObject(value);
      const normalized: Record<string, boolean> = {};
      const invalidEntries: string[] = [];
      for (const [stepKey, rawFlag] of Object.entries(object)) {
        if (typeof rawFlag !== "boolean") {
          invalidEntries.push(stepKey);
          continue;
        }
        normalized[stepKey] = rawFlag;
      }
      if (invalidEntries.length > 0) {
        diagnostics.push(...invalidEntries.map((item) => `ignored_onboarding_step_state_key:${item}`));
      }
      if (Object.keys(object).length > 0 && Object.keys(normalized).length === 0) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.onboardingStepState = normalized;
      acceptedFields.push(key);
      runtimeConsumedFields.push(key);
      continue;
    }

    if (key === "expectedVersion") {
      continue;
    }

    ignoredFields.push(key);
    diagnostics.push(`ignored_unknown_field:${key}`);
  }

  return buildWriteDiagnostics({
    targetType: "org_settings",
    normalizedPatch,
    acceptedFields,
    ignoredFields,
    forbiddenFields,
    diagnostics,
    runtimeConsumedFields,
    runtimeIgnoredFields,
    reasonWhenRejected: "org_settings_write_rejected_no_valid_fields"
  });
}

export function prepareOrgAiSettingsWrite(params: {
  patch: unknown;
}): OrgConfigWriteDiagnostics<OrgAiSettingsGovernancePatch> {
  const patch = asObject(params.patch);
  const normalizedPatch: OrgAiSettingsGovernancePatch = {};
  const acceptedFields: string[] = [];
  const ignoredFields: string[] = [];
  const diagnostics: string[] = [];
  const runtimeConsumedFields: string[] = [];
  const runtimeIgnoredFields: string[] = [];

  for (const [key, value] of Object.entries(patch)) {
    if (key === "provider") {
      if (typeof value !== "string" || !ORG_AI_PROVIDER_SET.has(value)) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.provider = value as OrgAiSettingsGovernancePatch["provider"];
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "modelDefault" || key === "modelReasoning") {
      const text = asString(value);
      if (!text || text.length > 120) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      if (key === "modelDefault") {
        normalizedPatch.modelDefault = text;
      } else {
        normalizedPatch.modelReasoning = text;
      }
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "fallbackMode") {
      if (typeof value !== "string" || !ORG_AI_FALLBACK_MODE_SET.has(value)) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }
      normalizedPatch.fallbackMode = value as OrgAiSettingsGovernancePatch["fallbackMode"];
      acceptedFields.push(key);
      runtimeConsumedFields.push(key);
      continue;
    }

    if (
      key === "autoAnalysisEnabled" ||
      key === "autoPlanEnabled" ||
      key === "autoBriefEnabled" ||
      key === "autoTouchpointReviewEnabled"
    ) {
      if (typeof value !== "boolean") {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_type:${key}`);
        continue;
      }
      if (key === "autoAnalysisEnabled") normalizedPatch.autoAnalysisEnabled = value;
      if (key === "autoPlanEnabled") normalizedPatch.autoPlanEnabled = value;
      if (key === "autoBriefEnabled") normalizedPatch.autoBriefEnabled = value;
      if (key === "autoTouchpointReviewEnabled") normalizedPatch.autoTouchpointReviewEnabled = value;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "humanReviewRequiredForSensitiveActions") {
      if (typeof value !== "boolean") {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_type:${key}`);
        continue;
      }
      normalizedPatch.humanReviewRequiredForSensitiveActions = value;
      acceptedFields.push(key);
      runtimeConsumedFields.push(key);
      continue;
    }

    if (key === "maxDailyAiRuns" || key === "maxMonthlyAiRuns") {
      if (value === null) {
        if (key === "maxDailyAiRuns") normalizedPatch.maxDailyAiRuns = null;
        if (key === "maxMonthlyAiRuns") normalizedPatch.maxMonthlyAiRuns = null;
        acceptedFields.push(key);
        runtimeIgnoredFields.push(key);
        continue;
      }

      const numeric = asInteger(value);
      const min = key === "maxDailyAiRuns" ? 10 : 100;
      const max = key === "maxDailyAiRuns" ? 200000 : 2000000;
      if (numeric === null || numeric < min || numeric > max) {
        ignoredFields.push(key);
        diagnostics.push(`invalid_field_value:${key}`);
        continue;
      }

      if (key === "maxDailyAiRuns") normalizedPatch.maxDailyAiRuns = numeric;
      if (key === "maxMonthlyAiRuns") normalizedPatch.maxMonthlyAiRuns = numeric;
      acceptedFields.push(key);
      runtimeIgnoredFields.push(key);
      continue;
    }

    if (key === "featureFlags" || key === "expectedVersion") {
      continue;
    }

    ignoredFields.push(key);
    diagnostics.push(`ignored_unknown_field:${key}`);
  }

  return buildWriteDiagnostics({
    targetType: "org_ai_settings",
    normalizedPatch,
    acceptedFields,
    ignoredFields,
    forbiddenFields: [],
    diagnostics,
    runtimeConsumedFields,
    runtimeIgnoredFields,
    reasonWhenRejected: "org_ai_settings_write_rejected_no_valid_fields"
  });
}

export function prepareOrgFeatureFlagsWrite(params: {
  patch: unknown;
}): OrgConfigWriteDiagnostics<OrgFeatureFlagsGovernancePatch> {
  const patch = asObject(params.patch);
  const normalizedPatch: OrgFeatureFlagsGovernancePatch = {};
  const acceptedFields: string[] = [];
  const ignoredFields: string[] = [];
  const diagnostics: string[] = [];

  for (const [key, value] of Object.entries(patch)) {
    if (!FEATURE_KEY_SET.has(key as OrgFeatureKey)) {
      ignoredFields.push(key);
      diagnostics.push(`ignored_unknown_feature_key:${key}`);
      continue;
    }
    if (typeof value !== "boolean") {
      ignoredFields.push(key);
      diagnostics.push(`invalid_feature_flag_value:${key}`);
      continue;
    }
    normalizedPatch[key as OrgFeatureKey] = value;
    acceptedFields.push(key);
  }

  return buildWriteDiagnostics({
    targetType: "org_feature_flags",
    normalizedPatch,
    acceptedFields,
    ignoredFields,
    forbiddenFields: [],
    diagnostics,
    runtimeConsumedFields: [...acceptedFields],
    runtimeIgnoredFields: [],
    reasonWhenRejected: "org_feature_flags_write_rejected_no_valid_fields"
  });
}

export function buildOrgConfigWriteDiagnosticsSummary(
  diagnostics: OrgConfigWriteDiagnostics<Record<string, unknown>>
): OrgConfigWriteDiagnosticsSummary {
  return {
    acceptedFields: [...diagnostics.acceptedFields],
    ignoredFields: [...diagnostics.ignoredFields],
    forbiddenFields: [...diagnostics.forbiddenFields],
    diagnostics: [...diagnostics.diagnostics],
    runtimeConsumedFields: [...diagnostics.runtimeConsumedFields],
    runtimeIgnoredFields: [...diagnostics.runtimeIgnoredFields],
    runtimeImpactSummary: diagnostics.runtimeImpactSummary
  };
}

export class OrgConfigWriteRejectedError extends Error {
  readonly rejection: OrgConfigWriteRejectionInfo;

  constructor(rejection: OrgConfigWriteRejectionInfo) {
    super("org_config_write_rejected");
    this.name = "OrgConfigWriteRejectedError";
    this.rejection = rejection;
  }
}

export function isOrgConfigWriteRejectedError(error: unknown): error is OrgConfigWriteRejectedError {
  return error instanceof OrgConfigWriteRejectedError;
}
