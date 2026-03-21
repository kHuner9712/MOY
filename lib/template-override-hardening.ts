import { z } from "zod";

import {
  MOY_CORE_CUSTOMER_STAGES,
  MOY_CORE_OPPORTUNITY_STAGES
} from "@/types/template";
import type { OrgTemplateOverride } from "@/types/productization";

export type OrgTemplateOverrideLayer =
  | "template_application_params"
  | "runtime_preference_overrides"
  | "forbidden_core_semantic_overrides";

export interface OrgTemplateOverrideValidationResult {
  overrideType: string;
  layer: OrgTemplateOverrideLayer | "unknown";
  validForWrite: boolean;
  acceptedForRuntime: boolean;
  normalizedPayload: Record<string, unknown>;
  reason: string | null;
  diagnostics: string[];
}

const OVERRIDE_TYPE_LAYER_MAP: Record<string, OrgTemplateOverrideLayer> = {
  customer_stages: "forbidden_core_semantic_overrides",
  opportunity_stages: "forbidden_core_semantic_overrides",
  alert_rules: "runtime_preference_overrides",
  prep_preferences: "runtime_preference_overrides",
  brief_preferences: "runtime_preference_overrides",
  checkpoints: "template_application_params",
  playbook_seed: "template_application_params",
  demo_seed_profile: "template_application_params"
};

const ALERT_RULE_ALLOWED_KEYS = new Set([
  "no_followup_timeout",
  "quoted_but_stalled",
  "high_probability_stalled"
]);

const CUSTOMER_STAGE_VALUES = [...MOY_CORE_CUSTOMER_STAGES] as [
  (typeof MOY_CORE_CUSTOMER_STAGES)[number],
  ...(typeof MOY_CORE_CUSTOMER_STAGES)[number][]
];

const OPPORTUNITY_STAGE_VALUES = [...MOY_CORE_OPPORTUNITY_STAGES] as [
  (typeof MOY_CORE_OPPORTUNITY_STAGES)[number],
  ...(typeof MOY_CORE_OPPORTUNITY_STAGES)[number][]
];

const customerStagesSchema = z.object({
  items: z.array(z.enum(CUSTOMER_STAGE_VALUES)).min(1).max(20)
});

const opportunityStagesSchema = z.object({
  items: z.array(z.enum(OPPORTUNITY_STAGE_VALUES)).min(1).max(20)
});

const checkpointsSchema = z.object({
  items: z.array(z.string().min(1).max(80)).min(1).max(40)
});

const stringPreferenceItemsSchema = z.object({
  items: z.array(z.string().min(1).max(120)).min(1).max(40)
});

const demoSeedProfileSchema = z.object({
  value: z.string().min(1).max(80)
});

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function uniqueStrings(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter((item) => item.length > 0)));
}

function normalizeItemsPayload(payload: Record<string, unknown>, aliases: string[]): string[] {
  for (const alias of aliases) {
    const raw = payload[alias];
    if (!raw) continue;
    const items = uniqueStrings(asStringArray(raw));
    if (items.length > 0) return items;
  }
  return [];
}

function parseAlertRulesPayload(payload: Record<string, unknown>): {
  normalized: Record<string, unknown>;
  diagnostics: string[];
  valid: boolean;
} {
  const diagnostics: string[] = [];
  const rawRules = asObject(payload.rules ?? payload);
  const rules: Record<string, number> = {};
  for (const [key, value] of Object.entries(rawRules)) {
    if (!ALERT_RULE_ALLOWED_KEYS.has(key)) {
      diagnostics.push(`ignored_alert_rule_key:${key}`);
      continue;
    }
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      diagnostics.push(`invalid_alert_rule_value:${key}`);
      continue;
    }
    rules[key] = numeric;
  }

  return {
    normalized: { rules },
    diagnostics,
    valid: Object.keys(rules).length > 0
  };
}

export function classifyOrgTemplateOverrideType(overrideType: string): OrgTemplateOverrideLayer | "unknown" {
  return OVERRIDE_TYPE_LAYER_MAP[overrideType] ?? "unknown";
}

export function validateOrgTemplateOverride(params: {
  overrideType: string;
  overridePayload: unknown;
}): OrgTemplateOverrideValidationResult {
  const layer = classifyOrgTemplateOverrideType(params.overrideType);
  const payloadObject = asObject(params.overridePayload);

  if (layer === "unknown") {
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: false,
      acceptedForRuntime: false,
      normalizedPayload: {},
      reason: "unknown_override_type",
      diagnostics: [`unknown_override_type:${params.overrideType}`]
    };
  }

  if (params.overrideType === "alert_rules") {
    const parsed = parseAlertRulesPayload(payloadObject);
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: parsed.valid,
      acceptedForRuntime: parsed.valid,
      normalizedPayload: parsed.normalized,
      reason: parsed.valid ? null : "alert_rules_payload_invalid",
      diagnostics: parsed.diagnostics
    };
  }

  if (params.overrideType === "prep_preferences" || params.overrideType === "brief_preferences") {
    const items = normalizeItemsPayload(payloadObject, ["items", params.overrideType]);
    const parsed = stringPreferenceItemsSchema.safeParse({ items });
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: parsed.success,
      acceptedForRuntime: parsed.success,
      normalizedPayload: parsed.success ? parsed.data : {},
      reason: parsed.success ? null : "preference_items_payload_invalid",
      diagnostics: parsed.success ? [] : ["invalid_preference_items_payload"]
    };
  }

  if (params.overrideType === "customer_stages") {
    const items = normalizeItemsPayload(payloadObject, ["items", "customer_stages"]);
    const parsed = customerStagesSchema.safeParse({ items });
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: parsed.success,
      acceptedForRuntime: false,
      normalizedPayload: parsed.success ? parsed.data : {},
      reason: parsed.success ? "forbidden_for_runtime_core_semantics" : "customer_stages_payload_invalid",
      diagnostics: parsed.success ? [] : ["invalid_customer_stages_payload"]
    };
  }

  if (params.overrideType === "opportunity_stages") {
    const items = normalizeItemsPayload(payloadObject, ["items", "opportunity_stages"]);
    const parsed = opportunityStagesSchema.safeParse({ items });
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: parsed.success,
      acceptedForRuntime: false,
      normalizedPayload: parsed.success ? parsed.data : {},
      reason: parsed.success ? "forbidden_for_runtime_core_semantics" : "opportunity_stages_payload_invalid",
      diagnostics: parsed.success ? [] : ["invalid_opportunity_stages_payload"]
    };
  }

  if (params.overrideType === "checkpoints") {
    const items = normalizeItemsPayload(payloadObject, ["items", "checkpoints"]);
    const parsed = checkpointsSchema.safeParse({ items });
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: parsed.success,
      acceptedForRuntime: false,
      normalizedPayload: parsed.success ? parsed.data : {},
      reason: parsed.success ? "template_application_only" : "checkpoints_payload_invalid",
      diagnostics: parsed.success ? [] : ["invalid_checkpoints_payload"]
    };
  }

  if (params.overrideType === "demo_seed_profile") {
    const parsed = demoSeedProfileSchema.safeParse({
      value: String(payloadObject.value ?? payloadObject.demo_seed_profile ?? "").trim()
    });
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: parsed.success,
      acceptedForRuntime: false,
      normalizedPayload: parsed.success ? parsed.data : {},
      reason: parsed.success ? "template_application_only" : "demo_seed_profile_payload_invalid",
      diagnostics: parsed.success ? [] : ["invalid_demo_seed_profile_payload"]
    };
  }

  if (params.overrideType === "playbook_seed") {
    return {
      overrideType: params.overrideType,
      layer,
      validForWrite: Object.keys(payloadObject).length > 0,
      acceptedForRuntime: false,
      normalizedPayload: payloadObject,
      reason: Object.keys(payloadObject).length > 0 ? "template_application_only" : "playbook_seed_payload_invalid",
      diagnostics: Object.keys(payloadObject).length > 0 ? [] : ["invalid_playbook_seed_payload"]
    };
  }

  return {
    overrideType: params.overrideType,
    layer,
    validForWrite: false,
    acceptedForRuntime: false,
    normalizedPayload: {},
    reason: "override_validation_not_implemented",
    diagnostics: ["override_validation_not_implemented"]
  };
}

export function validateOrgTemplateOverrideForWrite(params: {
  overrideType: OrgTemplateOverride["overrideType"];
  overridePayload: Record<string, unknown>;
}): OrgTemplateOverrideValidationResult {
  return validateOrgTemplateOverride({
    overrideType: params.overrideType,
    overridePayload: params.overridePayload
  });
}
