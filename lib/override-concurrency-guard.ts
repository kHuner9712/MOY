import { createHash } from "crypto";

export interface OrgTemplateOverrideExpectedVersion {
  compareToken?: string | null;
  versionLabel?: string | null;
  versionNumber?: number | null;
  overrideUpdatedAt?: string | null;
  payloadHash?: string | null;
}

export interface OrgTemplateOverrideConcurrencyBaseline {
  generatedAt: string;
  targetKey: string;
  auditAvailability: "available" | "empty" | "not_available";
  currentVersionLabel: string | null;
  currentVersionNumber: number | null;
  currentOverrideUpdatedAt: string | null;
  currentPayloadHash: string;
  compareToken: string;
}

export interface OverrideDriftConflictInfo {
  conflict: true;
  conflictReason:
    | "compare_token_mismatch"
    | "version_label_mismatch"
    | "version_number_mismatch"
    | "override_updated_at_mismatch"
    | "payload_hash_mismatch";
  diagnostics: string[];
  expectedVersion: {
    compareToken: string | null;
    versionLabel: string | null;
    versionNumber: number | null;
    overrideUpdatedAt: string | null;
    payloadHash: string | null;
  };
  currentVersion: {
    compareToken: string;
    versionLabel: string | null;
    versionNumber: number | null;
    overrideUpdatedAt: string | null;
    payloadHash: string;
    auditAvailability: "available" | "empty" | "not_available";
  };
}

export interface OverrideDriftValidationResult {
  conflict: boolean;
  diagnostics: string[];
  info: OverrideDriftConflictInfo | null;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function toNullableNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function toNullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

function stableSortObject(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => stableSortObject(item));
  }
  if (!value || typeof value !== "object") return value;
  const entries = Object.entries(asObject(value)).sort(([left], [right]) => left.localeCompare(right));
  return Object.fromEntries(entries.map(([key, child]) => [key, stableSortObject(child)]));
}

function hashText(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function uniqueStrings(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter((item) => item.length > 0)));
}

export function buildOrgTemplateOverridePayloadHash(payload: Record<string, unknown> | null | undefined): string {
  const normalizedPayload = stableSortObject(asObject(payload));
  return hashText(JSON.stringify(normalizedPayload));
}

export function buildOverrideConcurrencyToken(params: {
  targetKey: string;
  auditAvailability: "available" | "empty" | "not_available";
  currentVersionLabel: string | null;
  currentVersionNumber: number | null;
  currentOverrideUpdatedAt: string | null;
  currentPayloadHash: string;
}): string {
  const signature = {
    targetKey: params.targetKey,
    auditAvailability: params.auditAvailability,
    currentVersionLabel: params.currentVersionLabel,
    currentVersionNumber: params.currentVersionNumber,
    currentOverrideUpdatedAt: params.currentOverrideUpdatedAt,
    currentPayloadHash: params.currentPayloadHash
  };
  return `ovc_v1_${hashText(JSON.stringify(signature)).slice(0, 24)}`;
}

export function buildOverrideConcurrencyBaseline(params: {
  templateId: string;
  overrideType: string;
  auditAvailability: "available" | "empty" | "not_available";
  currentVersionLabel: string | null;
  currentVersionNumber: number | null;
  currentOverrideUpdatedAt: string | null;
  currentPayload: Record<string, unknown> | null;
  generatedAt?: string;
}): OrgTemplateOverrideConcurrencyBaseline {
  const targetKey = `${params.templateId}:${params.overrideType}`;
  const currentPayloadHash = buildOrgTemplateOverridePayloadHash(params.currentPayload);
  const compareToken = buildOverrideConcurrencyToken({
    targetKey,
    auditAvailability: params.auditAvailability,
    currentVersionLabel: params.currentVersionLabel,
    currentVersionNumber: params.currentVersionNumber,
    currentOverrideUpdatedAt: params.currentOverrideUpdatedAt,
    currentPayloadHash
  });

  return {
    generatedAt: params.generatedAt ?? new Date().toISOString(),
    targetKey,
    auditAvailability: params.auditAvailability,
    currentVersionLabel: params.currentVersionLabel,
    currentVersionNumber: params.currentVersionNumber,
    currentOverrideUpdatedAt: params.currentOverrideUpdatedAt,
    currentPayloadHash,
    compareToken
  };
}

export function buildExpectedVersionFromConcurrencyBaseline(
  baseline: OrgTemplateOverrideConcurrencyBaseline
): Required<OrgTemplateOverrideExpectedVersion> {
  return {
    compareToken: baseline.compareToken,
    versionLabel: baseline.currentVersionLabel,
    versionNumber: baseline.currentVersionNumber,
    overrideUpdatedAt: baseline.currentOverrideUpdatedAt,
    payloadHash: baseline.currentPayloadHash
  };
}

export function hasOverrideExpectedVersion(
  expected: OrgTemplateOverrideExpectedVersion | null | undefined
): boolean {
  if (!expected) return false;
  return Boolean(
    toNullableString(expected.compareToken) ??
      toNullableString(expected.versionLabel) ??
      toNullableString(expected.overrideUpdatedAt) ??
      toNullableString(expected.payloadHash) ??
      toNullableNumber(expected.versionNumber)
  );
}

function normalizeExpectedVersion(
  expected: OrgTemplateOverrideExpectedVersion | null | undefined
): OverrideDriftConflictInfo["expectedVersion"] {
  return {
    compareToken: toNullableString(expected?.compareToken),
    versionLabel: toNullableString(expected?.versionLabel),
    versionNumber: toNullableNumber(expected?.versionNumber),
    overrideUpdatedAt: toNullableString(expected?.overrideUpdatedAt),
    payloadHash: toNullableString(expected?.payloadHash)
  };
}

function buildConflictInfo(params: {
  reason: OverrideDriftConflictInfo["conflictReason"];
  diagnostics: string[];
  expected: OverrideDriftConflictInfo["expectedVersion"];
  current: OrgTemplateOverrideConcurrencyBaseline;
}): OverrideDriftConflictInfo {
  return {
    conflict: true,
    conflictReason: params.reason,
    diagnostics: uniqueStrings(params.diagnostics),
    expectedVersion: params.expected,
    currentVersion: {
      compareToken: params.current.compareToken,
      versionLabel: params.current.currentVersionLabel,
      versionNumber: params.current.currentVersionNumber,
      overrideUpdatedAt: params.current.currentOverrideUpdatedAt,
      payloadHash: params.current.currentPayloadHash,
      auditAvailability: params.current.auditAvailability
    }
  };
}

export function validateOverrideExpectedVersion(params: {
  expectedVersion: OrgTemplateOverrideExpectedVersion | null | undefined;
  currentBaseline: OrgTemplateOverrideConcurrencyBaseline;
}): OverrideDriftValidationResult {
  const expected = normalizeExpectedVersion(params.expectedVersion);
  const diagnostics: string[] = [];

  if (expected.compareToken && expected.compareToken !== params.currentBaseline.compareToken) {
    diagnostics.push(
      `concurrency_conflict:compare_token_mismatch:expected=${expected.compareToken}:current=${params.currentBaseline.compareToken}`
    );
    return {
      conflict: true,
      diagnostics,
      info: buildConflictInfo({
        reason: "compare_token_mismatch",
        diagnostics,
        expected,
        current: params.currentBaseline
      })
    };
  }

  if (expected.versionLabel && expected.versionLabel !== params.currentBaseline.currentVersionLabel) {
    diagnostics.push(
      `concurrency_conflict:version_label_mismatch:expected=${expected.versionLabel}:current=${params.currentBaseline.currentVersionLabel ?? "null"}`
    );
    return {
      conflict: true,
      diagnostics,
      info: buildConflictInfo({
        reason: "version_label_mismatch",
        diagnostics,
        expected,
        current: params.currentBaseline
      })
    };
  }

  if (
    typeof expected.versionNumber === "number" &&
    expected.versionNumber !== params.currentBaseline.currentVersionNumber
  ) {
    diagnostics.push(
      `concurrency_conflict:version_number_mismatch:expected=${expected.versionNumber}:current=${params.currentBaseline.currentVersionNumber ?? "null"}`
    );
    return {
      conflict: true,
      diagnostics,
      info: buildConflictInfo({
        reason: "version_number_mismatch",
        diagnostics,
        expected,
        current: params.currentBaseline
      })
    };
  }

  if (
    expected.overrideUpdatedAt &&
    expected.overrideUpdatedAt !== params.currentBaseline.currentOverrideUpdatedAt
  ) {
    diagnostics.push(
      `concurrency_conflict:override_updated_at_mismatch:expected=${expected.overrideUpdatedAt}:current=${params.currentBaseline.currentOverrideUpdatedAt ?? "null"}`
    );
    return {
      conflict: true,
      diagnostics,
      info: buildConflictInfo({
        reason: "override_updated_at_mismatch",
        diagnostics,
        expected,
        current: params.currentBaseline
      })
    };
  }

  if (expected.payloadHash && expected.payloadHash !== params.currentBaseline.currentPayloadHash) {
    diagnostics.push(
      `concurrency_conflict:payload_hash_mismatch:expected=${expected.payloadHash}:current=${params.currentBaseline.currentPayloadHash}`
    );
    return {
      conflict: true,
      diagnostics,
      info: buildConflictInfo({
        reason: "payload_hash_mismatch",
        diagnostics,
        expected,
        current: params.currentBaseline
      })
    };
  }

  return {
    conflict: false,
    diagnostics,
    info: null
  };
}

export class OverrideDriftConflictError extends Error {
  readonly conflict: OverrideDriftConflictInfo;

  constructor(conflict: OverrideDriftConflictInfo) {
    super("override_drift_conflict");
    this.name = "OverrideDriftConflictError";
    this.conflict = conflict;
  }
}

export function assertNoOverrideDrift(params: {
  expectedVersion: OrgTemplateOverrideExpectedVersion | null | undefined;
  currentBaseline: OrgTemplateOverrideConcurrencyBaseline;
}): void {
  const result = validateOverrideExpectedVersion({
    expectedVersion: params.expectedVersion,
    currentBaseline: params.currentBaseline
  });
  if (result.conflict && result.info) {
    throw new OverrideDriftConflictError(result.info);
  }
}

export function isOverrideDriftConflictError(error: unknown): error is OverrideDriftConflictError {
  return error instanceof OverrideDriftConflictError;
}

export type OrgConfigExpectedVersion = OrgTemplateOverrideExpectedVersion;
export type OrgConfigConcurrencyBaseline = OrgTemplateOverrideConcurrencyBaseline;
export type OrgConfigDriftConflictInfo = OverrideDriftConflictInfo;
export type OrgConfigDriftValidationResult = OverrideDriftValidationResult;

export function buildOrgConfigConcurrencyBaseline(params: {
  targetType: string;
  targetKey?: string | null;
  auditAvailability: "available" | "empty" | "not_available";
  currentVersionLabel: string | null;
  currentVersionNumber: number | null;
  currentUpdatedAt: string | null;
  currentPayload: Record<string, unknown> | null;
  generatedAt?: string;
}): OrgConfigConcurrencyBaseline {
  const targetKey = `${params.targetType}:${params.targetKey ?? "default"}`;
  const currentPayloadHash = buildOrgTemplateOverridePayloadHash(params.currentPayload);
  const compareToken = buildOverrideConcurrencyToken({
    targetKey,
    auditAvailability: params.auditAvailability,
    currentVersionLabel: params.currentVersionLabel,
    currentVersionNumber: params.currentVersionNumber,
    currentOverrideUpdatedAt: params.currentUpdatedAt,
    currentPayloadHash
  });

  return {
    generatedAt: params.generatedAt ?? new Date().toISOString(),
    targetKey,
    auditAvailability: params.auditAvailability,
    currentVersionLabel: params.currentVersionLabel,
    currentVersionNumber: params.currentVersionNumber,
    currentOverrideUpdatedAt: params.currentUpdatedAt,
    currentPayloadHash,
    compareToken
  };
}

export function buildExpectedVersionFromOrgConfigBaseline(
  baseline: OrgConfigConcurrencyBaseline
): Required<OrgConfigExpectedVersion> {
  return buildExpectedVersionFromConcurrencyBaseline(baseline);
}

export function hasOrgConfigExpectedVersion(expected: OrgConfigExpectedVersion | null | undefined): boolean {
  return hasOverrideExpectedVersion(expected);
}

export function validateOrgConfigExpectedVersion(params: {
  expectedVersion: OrgConfigExpectedVersion | null | undefined;
  currentBaseline: OrgConfigConcurrencyBaseline;
}): OrgConfigDriftValidationResult {
  return validateOverrideExpectedVersion({
    expectedVersion: params.expectedVersion,
    currentBaseline: params.currentBaseline
  });
}

export class OrgConfigDriftConflictError extends Error {
  readonly conflict: OrgConfigDriftConflictInfo;

  constructor(conflict: OrgConfigDriftConflictInfo) {
    super("org_config_drift_conflict");
    this.name = "OrgConfigDriftConflictError";
    this.conflict = conflict;
  }
}

export function assertNoOrgConfigDrift(params: {
  expectedVersion: OrgConfigExpectedVersion | null | undefined;
  currentBaseline: OrgConfigConcurrencyBaseline;
}): void {
  const result = validateOrgConfigExpectedVersion({
    expectedVersion: params.expectedVersion,
    currentBaseline: params.currentBaseline
  });
  if (result.conflict && result.info) {
    throw new OrgConfigDriftConflictError(result.info);
  }
}

export function isOrgConfigDriftConflictError(error: unknown): error is OrgConfigDriftConflictError {
  return error instanceof OrgConfigDriftConflictError || error instanceof OverrideDriftConflictError;
}
