import {
  validateOrgTemplateOverride,
  type OrgTemplateOverrideLayer,
  type OrgTemplateOverrideValidationResult
} from "@/lib/template-override-hardening";
import type { OrgTemplateOverride } from "@/types/productization";

export type OrgOverrideRuntimeImpactSummary =
  | "runtime_consumed"
  | "runtime_ignored_forbidden_core_semantics"
  | "runtime_ignored_non_runtime_layer"
  | "write_rejected";

export interface OrgTemplateOverrideWriteDiagnostics {
  overrideType: string;
  layer: OrgTemplateOverrideLayer | "unknown";
  acceptedForWrite: boolean;
  acceptedForRuntime: boolean;
  forbiddenForRuntime: boolean;
  ignoredByRuntime: boolean;
  normalizedPayload: Record<string, unknown>;
  acceptedFields: string[];
  ignoredFields: string[];
  diagnostics: string[];
  reason: string | null;
  runtimeImpactSummary: OrgOverrideRuntimeImpactSummary;
}

export interface PreparedOrgTemplateOverrideWrite {
  validation: OrgTemplateOverrideValidationResult;
  writeDiagnostics: OrgTemplateOverrideWriteDiagnostics;
}

export interface OrgOverrideWriteDiagnosticsSummary {
  acceptedOverrides: Array<{
    overrideType: string;
    layer: OrgTemplateOverrideLayer | "unknown";
    acceptedFields: string[];
    runtimeImpactSummary: OrgOverrideRuntimeImpactSummary;
  }>;
  ignoredOverrides: Array<{
    overrideType: string;
    layer: OrgTemplateOverrideLayer | "unknown";
    reason: string | null;
    runtimeImpactSummary: OrgOverrideRuntimeImpactSummary;
  }>;
  forbiddenOverrides: Array<{
    overrideType: string;
    layer: OrgTemplateOverrideLayer | "unknown";
    reason: string | null;
  }>;
  rejectedOverrides: Array<{
    overrideType: string;
    layer: OrgTemplateOverrideLayer | "unknown";
    reason: string | null;
  }>;
  diagnostics: string[];
  runtimeImpactCounters: {
    runtimeConsumed: number;
    runtimeIgnored: number;
    writeRejected: number;
  };
}

export interface OrgTemplateOverridePayloadSummary {
  payloadKeys: string[];
  payloadPreview: string;
}

export interface OrgTemplateOverrideWriteAuditDraft {
  version: 1;
  happenedAt: string;
  orgId: string;
  actorUserId: string;
  targetType: "org_template_override";
  targetId: string | null;
  targetRef: {
    templateId: string;
    overrideType: string;
  };
  beforeSummary: OrgTemplateOverridePayloadSummary | null;
  afterSummary: OrgTemplateOverridePayloadSummary | null;
  diagnosticsSummary: {
    layer: OrgTemplateOverrideLayer | "unknown";
    reason: string | null;
    runtimeImpactSummary: OrgOverrideRuntimeImpactSummary;
    forbiddenForRuntime: boolean;
    ignoredByRuntime: boolean;
    diagnostics: string[];
  };
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function normalizePath(base: string, child: string): string {
  if (!base) return child;
  return `${base}.${child}`;
}

function collectLeafKeys(value: unknown, path = ""): string[] {
  if (Array.isArray(value)) {
    return path ? [path] : [];
  }
  if (!value || typeof value !== "object") {
    return path ? [path] : [];
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) {
    return path ? [path] : [];
  }
  return entries.flatMap(([key, child]) => collectLeafKeys(child, normalizePath(path, key)));
}

function uniqueStrings(items: string[]): string[] {
  return Array.from(new Set(items.filter((item) => item.length > 0)));
}

function collectIgnoredFields(diagnostics: string[]): string[] {
  const ignored: string[] = [];
  for (const item of diagnostics) {
    if (item.startsWith("ignored_alert_rule_key:")) {
      ignored.push(`rules.${item.replace("ignored_alert_rule_key:", "")}`);
    }
  }
  return uniqueStrings(ignored);
}

function resolveRuntimeImpactSummary(validation: OrgTemplateOverrideValidationResult): OrgOverrideRuntimeImpactSummary {
  if (!validation.validForWrite) return "write_rejected";
  if (validation.acceptedForRuntime) return "runtime_consumed";
  if (validation.layer === "forbidden_core_semantic_overrides") {
    return "runtime_ignored_forbidden_core_semantics";
  }
  return "runtime_ignored_non_runtime_layer";
}

function stableSortObject(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => stableSortObject(item));
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) =>
    left.localeCompare(right)
  );
  return Object.fromEntries(entries.map(([key, child]) => [key, stableSortObject(child)]));
}

function summarizePayload(payload: Record<string, unknown> | null | undefined): OrgTemplateOverridePayloadSummary | null {
  if (!payload) return null;
  const payloadObject = asObject(payload);
  const payloadKeys = uniqueStrings(collectLeafKeys(payloadObject)).sort((left, right) => left.localeCompare(right));
  return {
    payloadKeys,
    payloadPreview: JSON.stringify(stableSortObject(payloadObject))
  };
}

export function prepareOrgTemplateOverrideWrite(params: {
  overrideType: OrgTemplateOverride["overrideType"];
  overridePayload: Record<string, unknown>;
}): PreparedOrgTemplateOverrideWrite {
  const validation = validateOrgTemplateOverride({
    overrideType: params.overrideType,
    overridePayload: params.overridePayload
  });
  const runtimeImpactSummary = resolveRuntimeImpactSummary(validation);
  const forbiddenForRuntime = validation.layer === "forbidden_core_semantic_overrides";
  const ignoredByRuntime = validation.validForWrite && !validation.acceptedForRuntime;
  const normalizedPayload = asObject(validation.normalizedPayload);

  return {
    validation,
    writeDiagnostics: {
      overrideType: validation.overrideType,
      layer: validation.layer,
      acceptedForWrite: validation.validForWrite,
      acceptedForRuntime: validation.acceptedForRuntime,
      forbiddenForRuntime,
      ignoredByRuntime,
      normalizedPayload,
      acceptedFields: uniqueStrings(collectLeafKeys(normalizedPayload)).sort((left, right) => left.localeCompare(right)),
      ignoredFields: collectIgnoredFields(validation.diagnostics),
      diagnostics: [...validation.diagnostics],
      reason: validation.reason,
      runtimeImpactSummary
    }
  };
}

export function buildOrgOverrideWriteDiagnosticsSummary(
  diagnosticsList: OrgTemplateOverrideWriteDiagnostics[]
): OrgOverrideWriteDiagnosticsSummary {
  const acceptedOverrides = diagnosticsList
    .filter((item) => item.acceptedForWrite)
    .map((item) => ({
      overrideType: item.overrideType,
      layer: item.layer,
      acceptedFields: [...item.acceptedFields],
      runtimeImpactSummary: item.runtimeImpactSummary
    }));
  const ignoredOverrides = diagnosticsList
    .filter((item) => item.ignoredByRuntime)
    .map((item) => ({
      overrideType: item.overrideType,
      layer: item.layer,
      reason: item.reason,
      runtimeImpactSummary: item.runtimeImpactSummary
    }));
  const forbiddenOverrides = diagnosticsList
    .filter((item) => item.forbiddenForRuntime)
    .map((item) => ({
      overrideType: item.overrideType,
      layer: item.layer,
      reason: item.reason
    }));
  const rejectedOverrides = diagnosticsList
    .filter((item) => !item.acceptedForWrite)
    .map((item) => ({
      overrideType: item.overrideType,
      layer: item.layer,
      reason: item.reason
    }));

  const runtimeConsumed = diagnosticsList.filter((item) => item.runtimeImpactSummary === "runtime_consumed").length;
  const runtimeIgnored = diagnosticsList.filter(
    (item) =>
      item.runtimeImpactSummary === "runtime_ignored_forbidden_core_semantics" ||
      item.runtimeImpactSummary === "runtime_ignored_non_runtime_layer"
  ).length;
  const writeRejected = diagnosticsList.filter((item) => item.runtimeImpactSummary === "write_rejected").length;

  return {
    acceptedOverrides,
    ignoredOverrides,
    forbiddenOverrides,
    rejectedOverrides,
    diagnostics: diagnosticsList.flatMap((item) =>
      item.diagnostics.map((diagnostic) => `${item.overrideType}:${diagnostic}`)
    ),
    runtimeImpactCounters: {
      runtimeConsumed,
      runtimeIgnored,
      writeRejected
    }
  };
}

export function buildOrgTemplateOverrideWriteAuditDraft(params: {
  orgId: string;
  actorUserId: string;
  templateId: string;
  targetId: string | null;
  overrideType: string;
  beforePayload: Record<string, unknown> | null;
  afterPayload: Record<string, unknown> | null;
  writeDiagnostics: OrgTemplateOverrideWriteDiagnostics;
  happenedAt?: string;
}): OrgTemplateOverrideWriteAuditDraft {
  return {
    version: 1,
    happenedAt: params.happenedAt ?? new Date().toISOString(),
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    targetType: "org_template_override",
    targetId: params.targetId,
    targetRef: {
      templateId: params.templateId,
      overrideType: params.overrideType
    },
    beforeSummary: summarizePayload(params.beforePayload),
    afterSummary: summarizePayload(params.afterPayload),
    diagnosticsSummary: {
      layer: params.writeDiagnostics.layer,
      reason: params.writeDiagnostics.reason,
      runtimeImpactSummary: params.writeDiagnostics.runtimeImpactSummary,
      forbiddenForRuntime: params.writeDiagnostics.forbiddenForRuntime,
      ignoredByRuntime: params.writeDiagnostics.ignoredByRuntime,
      diagnostics: [...params.writeDiagnostics.diagnostics]
    }
  };
}
