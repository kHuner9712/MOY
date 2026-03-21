import { isOrgAdminLike, canViewManagerWorkspace, type RoleCapabilityInput } from "@/lib/role-capability";
import {
  assertNoOverrideDrift,
  buildExpectedVersionFromConcurrencyBaseline,
  buildOverrideConcurrencyBaseline,
  hasOverrideExpectedVersion,
  isOverrideDriftConflictError,
  type OrgTemplateOverrideConcurrencyBaseline,
  type OrgTemplateOverrideExpectedVersion,
  type OverrideDriftConflictInfo
} from "@/lib/override-concurrency-guard";
import { prepareOrgTemplateOverrideWrite } from "@/lib/org-override-write-governance";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  findOrgTemplateOverrideAuditRecord,
  getLatestOrgTemplateOverrideAuditVersion,
  type OrgTemplateOverrideRollbackSource
} from "@/services/org-config-audit-service";
import {
  upsertOrgTemplateOverride,
  type OrgTemplateOverrideWriteResult
} from "@/services/industry-template-service";
import type { OrgTemplateOverride } from "@/types/productization";

type DbClient = ServerSupabaseClient;

interface OrgTemplateOverrideRow {
  id: string;
  org_id: string;
  template_id: string;
  override_type: string;
  override_payload: Record<string, unknown> | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface OrgTemplateOverrideRollbackSelector {
  targetAuditId?: string | null;
  targetVersionLabel?: string | null;
  targetVersionNumber?: number | null;
}

export interface OrgTemplateOverrideRollbackPayloadSummary {
  payloadKeys: string[];
  payloadPreview: string;
}

export interface OrgTemplateOverrideRollbackPreview {
  generatedAt: string;
  status: "allowed" | "rejected" | "not_available";
  canExecute: boolean;
  reason: string | null;
  diagnostics: string[];
  request: {
    orgId: string;
    templateId: string;
    overrideType: OrgTemplateOverride["overrideType"];
  };
  targetVersion: {
    auditId: string | null;
    versionLabel: string | null;
    versionNumber: number | null;
    actionType: string | null;
    createdAt: string | null;
  };
  currentValue: {
    exists: boolean;
    targetId: string | null;
    summary: OrgTemplateOverrideRollbackPayloadSummary | null;
  };
  targetValue: {
    summary: OrgTemplateOverrideRollbackPayloadSummary | null;
    normalizedPayload: Record<string, unknown>;
  };
  restorePlan: {
    acceptedFields: string[];
    ignoredFields: string[];
    runtimeImpactSummary: string;
    acceptedForRuntime: boolean;
    forbiddenForRuntime: boolean;
  };
  concurrency: {
    baseline: OrgTemplateOverrideConcurrencyBaseline | null;
    expectedVersion: Required<OrgTemplateOverrideExpectedVersion> | null;
    note: string;
  };
}

export interface ExecuteOrgTemplateOverrideRollbackResult {
  status: "executed" | "rejected" | "not_available" | "conflict";
  reason: string | null;
  diagnostics: string[];
  preview: OrgTemplateOverrideRollbackPreview;
  writeResult: OrgTemplateOverrideWriteResult | null;
  conflict: OverrideDriftConflictInfo | null;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function uniqueStrings(items: string[]): string[] {
  return Array.from(new Set(items.map((item) => item.trim()).filter((item) => item.length > 0)));
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

function stableSortObject(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => stableSortObject(item));
  }
  if (!value || typeof value !== "object") return value;
  const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) =>
    left.localeCompare(right)
  );
  return Object.fromEntries(entries.map(([key, child]) => [key, stableSortObject(child)]));
}

function summarizePayload(payload: Record<string, unknown> | null): OrgTemplateOverrideRollbackPayloadSummary | null {
  if (!payload) return null;
  const payloadKeys = uniqueStrings(collectLeafKeys(payload)).sort((left, right) => left.localeCompare(right));
  return {
    payloadKeys,
    payloadPreview: JSON.stringify(stableSortObject(payload))
  };
}

function parsePayloadPreview(value: unknown): Record<string, unknown> | null {
  const text = asString(value);
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    const payload = asObject(parsed);
    return Object.keys(payload).length > 0 ? payload : null;
  } catch {
    return null;
  }
}

function extractRollbackTargetPayload(record: {
  snapshotSummary: Record<string, unknown>;
  afterSummary: Record<string, unknown>;
}): Record<string, unknown> | null {
  const snapshotSummary = asObject(record.snapshotSummary);
  const snapshot = asObject(snapshotSummary.snapshot);
  const payloadSummary = asObject(snapshot.payloadSummary);
  const normalizedBySnapshot = asObject(payloadSummary.normalizedPayload);
  if (Object.keys(normalizedBySnapshot).length > 0) {
    return normalizedBySnapshot;
  }

  const directPayloadSummary = asObject(snapshotSummary.payloadSummary);
  const normalizedByDirectSummary = asObject(directPayloadSummary.normalizedPayload);
  if (Object.keys(normalizedByDirectSummary).length > 0) {
    return normalizedByDirectSummary;
  }

  const fromAfterSummary = parsePayloadPreview(asObject(record.afterSummary).payloadPreview);
  return fromAfterSummary;
}

function buildRejectedPreview(params: {
  orgId: string;
  templateId: string;
  overrideType: OrgTemplateOverride["overrideType"];
  status: "rejected" | "not_available";
  reason: string;
  diagnostics: string[];
  generatedAt?: string;
}): OrgTemplateOverrideRollbackPreview {
  return {
    generatedAt: params.generatedAt ?? new Date().toISOString(),
    status: params.status,
    canExecute: false,
    reason: params.reason,
    diagnostics: uniqueStrings(params.diagnostics),
    request: {
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType
    },
    targetVersion: {
      auditId: null,
      versionLabel: null,
      versionNumber: null,
      actionType: null,
      createdAt: null
    },
    currentValue: {
      exists: false,
      targetId: null,
      summary: null
    },
    targetValue: {
      summary: null,
      normalizedPayload: {}
    },
    restorePlan: {
      acceptedFields: [],
      ignoredFields: [],
      runtimeImpactSummary: "write_rejected",
      acceptedForRuntime: false,
      forbiddenForRuntime: false
    },
    concurrency: {
      baseline: null,
      expectedVersion: null,
      note: "No compare baseline available because rollback preview is not executable."
    }
  };
}

async function getCurrentOrgTemplateOverride(params: {
  supabase: DbClient;
  orgId: string;
  templateId: string;
  overrideType: OrgTemplateOverride["overrideType"];
}): Promise<OrgTemplateOverride | null> {
  const res = await (params.supabase as any)
    .from("org_template_overrides")
    .select("id,org_id,template_id,override_type,override_payload,created_by,created_at,updated_at")
    .eq("org_id", params.orgId)
    .eq("template_id", params.templateId)
    .eq("override_type", params.overrideType)
    .maybeSingle();

  if (res.error) {
    if (res.error.message.includes("org_template_overrides") || res.error.message.includes("does not exist")) {
      return null;
    }
    throw new Error(res.error.message);
  }

  const row = (res.data ?? null) as OrgTemplateOverrideRow | null;
  if (!row) return null;
  return {
    id: row.id,
    orgId: row.org_id,
    templateId: row.template_id,
    overrideType: row.override_type as OrgTemplateOverride["overrideType"],
    overridePayload: asObject(row.override_payload),
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function resolveCanExecute(params: {
  acceptedForWrite: boolean;
  acceptedForRuntime: boolean;
  forbiddenForRuntime: boolean;
  ignoredFields: string[];
}): {
  canExecute: boolean;
  reason: string | null;
  diagnostics: string[];
} {
  if (!params.acceptedForWrite) {
    return {
      canExecute: false,
      reason: "rollback_target_payload_rejected_by_hardening",
      diagnostics: ["rollback_rejected:target_payload_rejected_by_hardening"]
    };
  }
  if (params.forbiddenForRuntime) {
    return {
      canExecute: false,
      reason: "rollback_forbidden_core_semantic_override",
      diagnostics: ["rollback_rejected:forbidden_core_semantic_override"]
    };
  }
  if (!params.acceptedForRuntime) {
    return {
      canExecute: false,
      reason: "rollback_non_runtime_override_not_supported",
      diagnostics: ["rollback_rejected:non_runtime_override_not_supported_in_v1"]
    };
  }
  if (params.ignoredFields.length > 0) {
    return {
      canExecute: false,
      reason: "rollback_requires_lossless_payload_restore",
      diagnostics: params.ignoredFields.map((item) => `rollback_rejected:ignored_field:${item}`)
    };
  }
  return {
    canExecute: true,
    reason: null,
    diagnostics: []
  };
}

export function canPreviewOrgTemplateOverrideRollback(
  input: RoleCapabilityInput | null | undefined
): boolean {
  return canViewManagerWorkspace(input);
}

export function canExecuteOrgTemplateOverrideRollback(
  input: RoleCapabilityInput | null | undefined
): boolean {
  return isOrgAdminLike(input);
}

export async function previewOrgTemplateOverrideRollback(params: {
  supabase: DbClient;
  orgId: string;
  templateId: string;
  overrideType: OrgTemplateOverride["overrideType"];
  selector: OrgTemplateOverrideRollbackSelector;
}): Promise<OrgTemplateOverrideRollbackPreview> {
  const generatedAt = new Date().toISOString();
  const selectorProvided =
    Boolean(params.selector.targetAuditId) ||
    Boolean(params.selector.targetVersionLabel) ||
    (typeof params.selector.targetVersionNumber === "number" && params.selector.targetVersionNumber > 0);
  if (!selectorProvided) {
    return buildRejectedPreview({
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType,
      status: "rejected",
      reason: "rollback_selector_required",
      diagnostics: ["rollback_selector_required"],
      generatedAt
    });
  }

  const targetAuditResult = await findOrgTemplateOverrideAuditRecord({
    supabase: params.supabase,
    orgId: params.orgId,
    templateId: params.templateId,
    overrideType: params.overrideType,
    auditId: params.selector.targetAuditId ?? null,
    versionLabel: params.selector.targetVersionLabel ?? null,
    versionNumber: params.selector.targetVersionNumber ?? null
  });
  if (targetAuditResult.status === "not_available") {
    return buildRejectedPreview({
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType,
      status: "not_available",
      reason: targetAuditResult.reason ?? "org_config_audit_logs_not_available",
      diagnostics: [targetAuditResult.reason ?? "org_config_audit_logs_not_available"],
      generatedAt
    });
  }
  if (targetAuditResult.status === "not_found" || !targetAuditResult.item) {
    return buildRejectedPreview({
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType,
      status: "rejected",
      reason: "rollback_target_version_not_found",
      diagnostics: [targetAuditResult.reason ?? "rollback_target_version_not_found"],
      generatedAt
    });
  }

  const targetAudit = targetAuditResult.item;
  const targetPayload = extractRollbackTargetPayload({
    snapshotSummary: targetAudit.snapshotSummary,
    afterSummary: targetAudit.afterSummary
  });
  if (!targetPayload || Object.keys(targetPayload).length === 0) {
    return buildRejectedPreview({
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType,
      status: "rejected",
      reason: "rollback_target_payload_missing",
      diagnostics: ["rollback_target_payload_missing"],
      generatedAt
    });
  }

  const prepared = prepareOrgTemplateOverrideWrite({
    overrideType: params.overrideType,
    overridePayload: targetPayload
  });
  const [current, latestAuditVersion] = await Promise.all([
    getCurrentOrgTemplateOverride({
      supabase: params.supabase,
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType
    }),
    getLatestOrgTemplateOverrideAuditVersion({
      supabase: params.supabase,
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType
    })
  ]);
  const concurrencyBaseline = buildOverrideConcurrencyBaseline({
    templateId: params.templateId,
    overrideType: params.overrideType,
    auditAvailability: latestAuditVersion.availability,
    currentVersionLabel: latestAuditVersion.item?.versionLabel ?? null,
    currentVersionNumber: latestAuditVersion.item?.versionNumber ?? null,
    currentOverrideUpdatedAt: current?.updatedAt ?? null,
    currentPayload: current?.overridePayload ?? null
  });
  const expectedVersion = buildExpectedVersionFromConcurrencyBaseline(concurrencyBaseline);
  const currentValueSummary = summarizePayload(current?.overridePayload ?? null);
  const canExecuteResult = resolveCanExecute({
    acceptedForWrite: prepared.writeDiagnostics.acceptedForWrite,
    acceptedForRuntime: prepared.writeDiagnostics.acceptedForRuntime,
    forbiddenForRuntime: prepared.writeDiagnostics.forbiddenForRuntime,
    ignoredFields: prepared.writeDiagnostics.ignoredFields
  });

  const diagnostics = uniqueStrings([
    ...prepared.writeDiagnostics.diagnostics,
    ...canExecuteResult.diagnostics
  ]);

  return {
    generatedAt,
    status: canExecuteResult.canExecute ? "allowed" : "rejected",
    canExecute: canExecuteResult.canExecute,
    reason: canExecuteResult.reason,
    diagnostics,
    request: {
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType
    },
    targetVersion: {
      auditId: targetAudit.id,
      versionLabel: targetAudit.versionLabel,
      versionNumber: targetAudit.versionNumber,
      actionType: targetAudit.actionType,
      createdAt: targetAudit.createdAt
    },
    currentValue: {
      exists: Boolean(current),
      targetId: current?.id ?? null,
      summary: currentValueSummary
    },
    targetValue: {
      summary: summarizePayload(prepared.writeDiagnostics.normalizedPayload),
      normalizedPayload: prepared.writeDiagnostics.normalizedPayload
    },
    restorePlan: {
      acceptedFields: [...prepared.writeDiagnostics.acceptedFields],
      ignoredFields: [...prepared.writeDiagnostics.ignoredFields],
      runtimeImpactSummary: prepared.writeDiagnostics.runtimeImpactSummary,
      acceptedForRuntime: prepared.writeDiagnostics.acceptedForRuntime,
      forbiddenForRuntime: prepared.writeDiagnostics.forbiddenForRuntime
    },
    concurrency: {
      baseline: concurrencyBaseline,
      expectedVersion,
      note:
        latestAuditVersion.availability === "not_available"
          ? "Persisted audit baseline is unavailable in current environment; compare token falls back to override updated_at + payload hash."
          : "Use expectedVersion as compare baseline when executing rollback. Execute will reject on drift."
    }
  };
}

export async function executeOrgTemplateOverrideRollback(params: {
  supabase: DbClient;
  orgId: string;
  templateId: string;
  overrideType: OrgTemplateOverride["overrideType"];
  selector: OrgTemplateOverrideRollbackSelector;
  expectedVersion: OrgTemplateOverrideExpectedVersion | null;
  actorUserId: string;
}): Promise<ExecuteOrgTemplateOverrideRollbackResult> {
  const expectedVersion = hasOverrideExpectedVersion(params.expectedVersion)
    ? params.expectedVersion ?? null
    : null;
  if (!expectedVersion) {
    return {
      status: "rejected",
      reason: "rollback_expected_version_required",
      diagnostics: ["rollback_expected_version_required"],
      preview: buildRejectedPreview({
        orgId: params.orgId,
        templateId: params.templateId,
        overrideType: params.overrideType,
        status: "rejected",
        reason: "rollback_expected_version_required",
        diagnostics: ["rollback_expected_version_required"]
      }),
      writeResult: null,
      conflict: null
    };
  }

  const preview = await previewOrgTemplateOverrideRollback({
    supabase: params.supabase,
    orgId: params.orgId,
    templateId: params.templateId,
    overrideType: params.overrideType,
    selector: params.selector
  });

  if (preview.status === "not_available") {
    return {
      status: "not_available",
      reason: preview.reason,
      diagnostics: [...preview.diagnostics],
      preview,
      writeResult: null,
      conflict: null
    };
  }

  if (!preview.canExecute) {
    return {
      status: "rejected",
      reason: preview.reason ?? "rollback_guard_rejected",
      diagnostics: [...preview.diagnostics],
      preview,
      writeResult: null,
      conflict: null
    };
  }

  if (!preview.targetVersion.auditId || !preview.targetVersion.versionLabel || !preview.targetVersion.versionNumber) {
    return {
      status: "rejected",
      reason: "rollback_target_version_metadata_missing",
      diagnostics: ["rollback_target_version_metadata_missing"],
      preview,
      writeResult: null,
      conflict: null
    };
  }

  if (!preview.concurrency.baseline) {
    return {
      status: "rejected",
      reason: "rollback_concurrency_baseline_missing",
      diagnostics: ["rollback_concurrency_baseline_missing"],
      preview,
      writeResult: null,
      conflict: null
    };
  }

  try {
    assertNoOverrideDrift({
      expectedVersion,
      currentBaseline: preview.concurrency.baseline
    });
  } catch (error) {
    if (isOverrideDriftConflictError(error)) {
      return {
        status: "conflict",
        reason: "override_drift_conflict",
        diagnostics: [...preview.diagnostics, ...error.conflict.diagnostics],
        preview,
        writeResult: null,
        conflict: error.conflict
      };
    }
    throw error;
  }

  const rollbackSource: OrgTemplateOverrideRollbackSource = {
    sourceAuditId: preview.targetVersion.auditId,
    sourceVersionLabel: preview.targetVersion.versionLabel,
    sourceVersionNumber: preview.targetVersion.versionNumber,
    previewGeneratedAt: preview.generatedAt
  };

  try {
    const writeResult = await upsertOrgTemplateOverride({
      supabase: params.supabase,
      orgId: params.orgId,
      templateId: params.templateId,
      overrideType: params.overrideType,
      overridePayload: preview.targetValue.normalizedPayload,
      actorUserId: params.actorUserId,
      auditActionType: "rollback",
      rollbackSource,
      expectedVersion
    });

    return {
      status: "executed",
      reason: null,
      diagnostics: uniqueStrings([
        ...preview.diagnostics,
        ...writeResult.writeDiagnostics.diagnostics
      ]),
      preview,
      writeResult,
      conflict: null
    };
  } catch (error) {
    if (isOverrideDriftConflictError(error)) {
      return {
        status: "conflict",
        reason: "override_drift_conflict",
        diagnostics: uniqueStrings([
          ...preview.diagnostics,
          ...error.conflict.diagnostics
        ]),
        preview,
        writeResult: null,
        conflict: error.conflict
      };
    }
    throw error;
  }
}
