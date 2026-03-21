import type {
  OrgTemplateOverrideWriteAuditDraft,
  OrgTemplateOverrideWriteDiagnostics
} from "@/lib/org-override-write-governance";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapOrgConfigAuditLogRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type {
  OrgConfigAuditLog,
  OrgConfigAuditTargetType,
  OrgConfigVersionSnapshotSummary
} from "@/types/productization";

type DbClient = ServerSupabaseClient;
type OrgConfigAuditLogRow = Database["public"]["Tables"]["org_config_audit_logs"]["Row"];
type OrgConfigAuditLogInsert = Database["public"]["Tables"]["org_config_audit_logs"]["Insert"];

export interface OrgConfigAuditRecordDraft {
  orgId: string;
  actorUserId: string;
  targetType: OrgConfigAuditTargetType;
  targetId: string | null;
  targetKey: string | null;
  actionType: string;
  beforeSummary: Record<string, unknown>;
  afterSummary: Record<string, unknown>;
  diagnosticsSummary: Record<string, unknown>;
  snapshotSummary: Record<string, unknown>;
  versionLabelPrefix: string;
}

export interface OrgTemplateOverrideRollbackSource {
  sourceAuditId: string;
  sourceVersionLabel: string;
  sourceVersionNumber: number;
  previewGeneratedAt: string | null;
}

export interface PersistOrgConfigAuditRecordResult {
  status: "persisted" | "not_available";
  record: OrgConfigAuditLog | null;
  reason: string | null;
}

export interface RecentOrgConfigAuditLogResult {
  availability: "available" | "empty" | "not_available";
  items: OrgConfigAuditLog[];
  note: string;
}

export interface FindOrgTemplateOverrideAuditRecordResult {
  status: "found" | "not_found" | "not_available";
  item: OrgConfigAuditLog | null;
  reason: string | null;
}

export interface FindOrgConfigAuditRecordResult {
  status: "found" | "not_found" | "not_available";
  item: OrgConfigAuditLog | null;
  reason: string | null;
}

export interface LatestOrgTemplateOverrideAuditVersionResult {
  availability: "available" | "empty" | "not_available";
  item: {
    id: string;
    versionLabel: string;
    versionNumber: number;
    createdAt: string;
  } | null;
  note: string;
}

export interface LatestOrgConfigAuditVersionResult {
  availability: "available" | "empty" | "not_available";
  item: {
    id: string;
    versionLabel: string;
    versionNumber: number;
    createdAt: string;
  } | null;
  note: string;
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function isAuditTableUnavailableError(message: string): boolean {
  return message.includes("org_config_audit_logs") || message.includes("does not exist");
}

function buildAuditFilterNote(params: {
  targetType?: OrgConfigAuditTargetType;
  actionType?: string;
  targetKey?: string | null;
}): string {
  const filters: string[] = [];
  if (params.targetType) filters.push(`target_type=${params.targetType}`);
  if (params.actionType) filters.push(`action_type=${params.actionType}`);
  if (params.targetKey) filters.push(`target_key=${params.targetKey}`);
  if (filters.length === 0) return "";
  return ` (filtered by ${filters.join(", ")})`;
}

function resolveVersionLabel(params: {
  versionLabelPrefix: string;
  versionNumber: number;
}): string {
  return `${params.versionLabelPrefix}:v${params.versionNumber}`;
}

async function resolveNextOrgConfigAuditVersion(params: {
  supabase: DbClient;
  orgId: string;
  targetType: OrgConfigAuditTargetType;
  targetKey: string | null;
}): Promise<{
  status: "ok" | "not_available";
  versionNumber: number;
  reason: string | null;
}> {
  let query = (params.supabase as any)
    .from("org_config_audit_logs")
    .select("version_number")
    .eq("org_id", params.orgId)
    .eq("target_type", params.targetType);

  query = params.targetKey ? query.eq("target_key", params.targetKey) : query.is("target_key", null);
  const res = await query.order("version_number", { ascending: false }).limit(1);
  if (res.error) {
    if (isAuditTableUnavailableError(res.error.message)) {
      return {
        status: "not_available",
        versionNumber: 1,
        reason: "org_config_audit_logs_not_available"
      };
    }
    throw new Error(res.error.message);
  }

  const latestVersion = Number((res.data?.[0] as { version_number?: unknown } | undefined)?.version_number ?? 0);
  return {
    status: "ok",
    versionNumber: Number.isFinite(latestVersion) && latestVersion > 0 ? latestVersion + 1 : 1,
    reason: null
  };
}

export function buildOrgConfigAuditRecord(params: OrgConfigAuditRecordDraft): OrgConfigAuditRecordDraft {
  return {
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    targetType: params.targetType,
    targetId: params.targetId,
    targetKey: params.targetKey,
    actionType: params.actionType,
    beforeSummary: asObject(params.beforeSummary),
    afterSummary: asObject(params.afterSummary),
    diagnosticsSummary: asObject(params.diagnosticsSummary),
    snapshotSummary: asObject(params.snapshotSummary),
    versionLabelPrefix: params.versionLabelPrefix
  };
}

export function buildOrgTemplateOverrideVersionSnapshot(params: {
  templateId: string;
  overrideType: string;
  normalizedPayload: Record<string, unknown>;
  writeDiagnostics: OrgTemplateOverrideWriteDiagnostics;
}): OrgConfigVersionSnapshotSummary {
  return {
    snapshotType: "org_template_override_normalized_payload_v1",
    targetType: "org_template_override",
    targetKey: `${params.templateId}:${params.overrideType}`,
    payloadSummary: {
      templateId: params.templateId,
      overrideType: params.overrideType,
      normalizedPayload: asObject(params.normalizedPayload),
      runtimeImpactSummary: params.writeDiagnostics.runtimeImpactSummary,
      acceptedForRuntime: params.writeDiagnostics.acceptedForRuntime,
      forbiddenForRuntime: params.writeDiagnostics.forbiddenForRuntime,
      ignoredByRuntime: params.writeDiagnostics.ignoredByRuntime,
      diagnostics: [...params.writeDiagnostics.diagnostics]
    }
  };
}

export function buildOrgTemplateOverrideAuditRecord(params: {
  orgId: string;
  actorUserId: string;
  templateId: string;
  targetId: string | null;
  overrideType: string;
  actionType: "create" | "update" | "rollback";
  auditDraft: OrgTemplateOverrideWriteAuditDraft;
  writeDiagnostics: OrgTemplateOverrideWriteDiagnostics;
  rollbackSource?: OrgTemplateOverrideRollbackSource | null;
}): OrgConfigAuditRecordDraft {
  const snapshot = buildOrgTemplateOverrideVersionSnapshot({
    templateId: params.templateId,
    overrideType: params.overrideType,
    normalizedPayload: params.writeDiagnostics.normalizedPayload,
    writeDiagnostics: params.writeDiagnostics
  });
  const rollbackSourceSummary =
    params.actionType === "rollback" && params.rollbackSource
      ? {
          sourceAuditId: params.rollbackSource.sourceAuditId,
          sourceVersionLabel: params.rollbackSource.sourceVersionLabel,
          sourceVersionNumber: params.rollbackSource.sourceVersionNumber,
          previewGeneratedAt: params.rollbackSource.previewGeneratedAt
        }
      : null;

  return buildOrgConfigAuditRecord({
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    targetType: "org_template_override",
    targetId: params.targetId,
    targetKey: `${params.templateId}:${params.overrideType}`,
    actionType: params.actionType,
    beforeSummary: asObject(params.auditDraft.beforeSummary),
    afterSummary: asObject(params.auditDraft.afterSummary),
    diagnosticsSummary: {
      ...asObject(params.auditDraft.diagnosticsSummary),
      acceptedFields: [...params.writeDiagnostics.acceptedFields],
      ignoredFields: [...params.writeDiagnostics.ignoredFields],
      ...(rollbackSourceSummary ? { rollbackSource: rollbackSourceSummary } : {})
    },
    snapshotSummary: {
      snapshot,
      ...(rollbackSourceSummary ? { rollbackSource: rollbackSourceSummary } : {})
    },
    versionLabelPrefix: `org_template_override:${params.templateId}:${params.overrideType}`
  });
}

export async function findOrgTemplateOverrideAuditRecord(params: {
  supabase: DbClient;
  orgId: string;
  templateId: string;
  overrideType: string;
  auditId?: string | null;
  versionLabel?: string | null;
  versionNumber?: number | null;
}): Promise<FindOrgTemplateOverrideAuditRecordResult> {
  let query = (params.supabase as any)
    .from("org_config_audit_logs")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("target_type", "org_template_override")
    .eq("target_key", `${params.templateId}:${params.overrideType}`);

  if (params.auditId) {
    query = query.eq("id", params.auditId);
  } else if (params.versionLabel) {
    query = query.eq("version_label", params.versionLabel);
  } else if (Number.isFinite(params.versionNumber ?? Number.NaN) && Number(params.versionNumber) > 0) {
    query = query.eq("version_number", Number(params.versionNumber));
  } else {
    return {
      status: "not_found",
      item: null,
      reason: "rollback_selector_required"
    };
  }

  const res = await query.maybeSingle();
  if (res.error) {
    if (isAuditTableUnavailableError(res.error.message)) {
      return {
        status: "not_available",
        item: null,
        reason: "org_config_audit_logs_not_available"
      };
    }
    throw new Error(res.error.message);
  }

  if (!res.data) {
    return {
      status: "not_found",
      item: null,
      reason: "target_audit_record_not_found"
    };
  }

  return {
    status: "found",
    item: mapOrgConfigAuditLogRow(res.data as OrgConfigAuditLogRow),
    reason: null
  };
}

export async function findOrgConfigAuditRecord(params: {
  supabase: DbClient;
  orgId: string;
  targetType: OrgConfigAuditTargetType;
  targetKey?: string | null;
  auditId?: string | null;
  versionLabel?: string | null;
  versionNumber?: number | null;
}): Promise<FindOrgConfigAuditRecordResult> {
  let query = (params.supabase as any)
    .from("org_config_audit_logs")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("target_type", params.targetType);

  query = params.targetKey ? query.eq("target_key", params.targetKey) : query.is("target_key", null);
  if (params.auditId) {
    query = query.eq("id", params.auditId);
  } else if (params.versionLabel) {
    query = query.eq("version_label", params.versionLabel);
  } else if (Number.isFinite(params.versionNumber ?? Number.NaN) && Number(params.versionNumber) > 0) {
    query = query.eq("version_number", Number(params.versionNumber));
  } else {
    return {
      status: "not_found",
      item: null,
      reason: "rollback_selector_required"
    };
  }

  const res = await query.maybeSingle();
  if (res.error) {
    if (isAuditTableUnavailableError(res.error.message)) {
      return {
        status: "not_available",
        item: null,
        reason: "org_config_audit_logs_not_available"
      };
    }
    throw new Error(res.error.message);
  }

  if (!res.data) {
    return {
      status: "not_found",
      item: null,
      reason: "target_audit_record_not_found"
    };
  }

  return {
    status: "found",
    item: mapOrgConfigAuditLogRow(res.data as OrgConfigAuditLogRow),
    reason: null
  };
}

export async function getLatestOrgTemplateOverrideAuditVersion(params: {
  supabase: DbClient;
  orgId: string;
  templateId: string;
  overrideType: string;
}): Promise<LatestOrgTemplateOverrideAuditVersionResult> {
  const targetKey = `${params.templateId}:${params.overrideType}`;
  return getLatestOrgConfigAuditVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    targetType: "org_template_override",
    targetKey
  });
}

export async function getLatestOrgConfigAuditVersion(params: {
  supabase: DbClient;
  orgId: string;
  targetType: OrgConfigAuditTargetType;
  targetKey?: string | null;
}): Promise<LatestOrgConfigAuditVersionResult> {
  let query = (params.supabase as any)
    .from("org_config_audit_logs")
    .select("id,version_label,version_number,created_at")
    .eq("org_id", params.orgId)
    .eq("target_type", params.targetType);
  query = params.targetKey ? query.eq("target_key", params.targetKey) : query.is("target_key", null);
  const res = await query.order("version_number", { ascending: false }).limit(1);

  if (res.error) {
    if (isAuditTableUnavailableError(res.error.message)) {
      return {
        availability: "not_available",
        item: null,
        note: "Persisted audit table is not available in current environment."
      };
    }
    throw new Error(res.error.message);
  }

  const row = (res.data?.[0] ?? null) as
    | {
        id?: unknown;
        version_label?: unknown;
        version_number?: unknown;
        created_at?: unknown;
      }
    | null;
  if (!row) {
    return {
      availability: "empty",
      item: null,
      note: "No persisted audit version baseline exists for current target."
    };
  }

  const versionNumber = Number(row.version_number ?? 0);
  return {
    availability: "available",
    item: {
      id: String(row.id ?? ""),
      versionLabel: String(row.version_label ?? ""),
      versionNumber: Number.isFinite(versionNumber) && versionNumber > 0 ? versionNumber : 1,
      createdAt: String(row.created_at ?? "")
    },
    note: "Latest persisted audit version baseline is available."
  };
}

export async function persistOrgConfigAuditRecord(params: {
  supabase: DbClient;
  recordDraft: OrgConfigAuditRecordDraft;
}): Promise<PersistOrgConfigAuditRecordResult> {
  const version = await resolveNextOrgConfigAuditVersion({
    supabase: params.supabase,
    orgId: params.recordDraft.orgId,
    targetType: params.recordDraft.targetType,
    targetKey: params.recordDraft.targetKey
  });

  if (version.status === "not_available") {
    return {
      status: "not_available",
      record: null,
      reason: version.reason
    };
  }

  const insertPayload: OrgConfigAuditLogInsert = {
    org_id: params.recordDraft.orgId,
    actor_user_id: params.recordDraft.actorUserId,
    target_type: params.recordDraft.targetType,
    target_id: params.recordDraft.targetId,
    target_key: params.recordDraft.targetKey,
    action_type: params.recordDraft.actionType,
    before_summary: params.recordDraft.beforeSummary as OrgConfigAuditLogInsert["before_summary"],
    after_summary: params.recordDraft.afterSummary as OrgConfigAuditLogInsert["after_summary"],
    diagnostics_summary: params.recordDraft.diagnosticsSummary as OrgConfigAuditLogInsert["diagnostics_summary"],
    version_number: version.versionNumber,
    version_label: resolveVersionLabel({
      versionLabelPrefix: params.recordDraft.versionLabelPrefix,
      versionNumber: version.versionNumber
    }),
    snapshot_summary: params.recordDraft.snapshotSummary as OrgConfigAuditLogInsert["snapshot_summary"]
  };

  const res = await (params.supabase as any)
    .from("org_config_audit_logs")
    .insert(insertPayload)
    .select("*")
    .single();

  if (res.error) {
    if (isAuditTableUnavailableError(res.error.message)) {
      return {
        status: "not_available",
        record: null,
        reason: "org_config_audit_logs_not_available"
      };
    }
    throw new Error(res.error.message);
  }

  return {
    status: "persisted",
    record: mapOrgConfigAuditLogRow(res.data as OrgConfigAuditLogRow),
    reason: null
  };
}

export async function listRecentOrgConfigAuditLogs(params: {
  supabase: DbClient;
  orgId: string;
  limit?: number;
  targetType?: OrgConfigAuditTargetType;
  actionType?: string;
  targetKey?: string | null;
}): Promise<RecentOrgConfigAuditLogResult> {
  let query = (params.supabase as any)
    .from("org_config_audit_logs")
    .select("*")
    .eq("org_id", params.orgId);

  if (params.targetType) {
    query = query.eq("target_type", params.targetType);
  }
  if (params.actionType) {
    query = query.eq("action_type", params.actionType);
  }
  if (params.targetKey) {
    query = query.eq("target_key", params.targetKey);
  }

  const res = await query.order("created_at", { ascending: false }).limit(params.limit ?? 5);

  if (res.error) {
    if (isAuditTableUnavailableError(res.error.message)) {
      return {
        availability: "not_available",
        items: [],
        note: "Persisted audit table is not available in current environment."
      };
    }
    throw new Error(res.error.message);
  }

  const items = ((res.data ?? []) as OrgConfigAuditLogRow[]).map(mapOrgConfigAuditLogRow);
  const filterNote = buildAuditFilterNote({
    targetType: params.targetType,
    actionType: params.actionType,
    targetKey: params.targetKey ?? null
  });
  if (items.length === 0) {
    return {
      availability: "empty",
      items: [],
      note: `No persisted org config audit history yet${filterNote}.`
    };
  }

  return {
    availability: "available",
    items,
    note: `Showing latest ${items.length} persisted org config audit records${filterNote}.`
  };
}
