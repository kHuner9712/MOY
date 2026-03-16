import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  mapDedupeMatchGroupRow,
  mapImportAuditEventRow,
  mapImportJobColumnRow,
  mapImportJobRow,
  mapImportJobRowRow
} from "@/services/mappers";
import { assertOrgAdminAccess, assertOrgManagerAccess } from "@/services/org-membership-service";
import type { Database } from "@/types/database";
import type { DedupeMatchGroup, ImportAuditEvent, ImportJob, ImportJobColumn, ImportJobRow, ImportType } from "@/types/import";

type DbClient = ServerSupabaseClient;

type ImportJobRowDb = Database["public"]["Tables"]["import_jobs"]["Row"];
type ImportJobUpdate = Database["public"]["Tables"]["import_jobs"]["Update"];
type ImportColumnInsert = Database["public"]["Tables"]["import_job_columns"]["Insert"];
type ImportRowInsert = Database["public"]["Tables"]["import_job_rows"]["Insert"];

export async function assertImportReadAccess(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
}) {
  return assertOrgManagerAccess(params);
}

export async function assertImportWriteAccess(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
}) {
  return assertOrgAdminAccess(params);
}

export async function createImportJob(params: {
  supabase: DbClient;
  orgId: string;
  initiatedBy: string;
  importType: ImportType;
  sourceType: Database["public"]["Enums"]["import_source_type"];
  fileName: string;
  storagePath?: string | null;
}): Promise<ImportJob> {
  const res = await params.supabase
    .from("import_jobs")
    .insert({
      org_id: params.orgId,
      initiated_by: params.initiatedBy,
      import_type: params.importType,
      source_type: params.sourceType,
      file_name: params.fileName,
      storage_path: params.storagePath ?? null,
      job_status: "uploaded"
    })
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapImportJobRow(res.data as ImportJobRowDb);
}

export async function updateImportJob(params: {
  supabase: DbClient;
  jobId: string;
  patch: ImportJobUpdate;
}): Promise<ImportJob> {
  const res = await params.supabase.from("import_jobs").update(params.patch).eq("id", params.jobId).select("*").single();
  if (res.error) throw new Error(res.error.message);
  return mapImportJobRow(res.data as ImportJobRowDb);
}

export async function getImportJob(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
}): Promise<ImportJob> {
  const res = await params.supabase.from("import_jobs").select("*").eq("org_id", params.orgId).eq("id", params.jobId).single();
  if (res.error) throw new Error(res.error.message);
  return mapImportJobRow(res.data as ImportJobRowDb);
}

export async function listImportJobs(params: {
  supabase: DbClient;
  orgId: string;
  limit?: number;
}): Promise<ImportJob[]> {
  const res = await params.supabase
    .from("import_jobs")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 30);
  if (res.error) throw new Error(res.error.message);
  return (res.data ?? []).map((row: ImportJobRowDb) => mapImportJobRow(row));
}

export async function replaceImportColumns(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
  columns: ImportColumnInsert[];
}): Promise<ImportJobColumn[]> {
  const deleteRes = await params.supabase.from("import_job_columns").delete().eq("org_id", params.orgId).eq("import_job_id", params.jobId);
  if (deleteRes.error) throw new Error(deleteRes.error.message);

  if (params.columns.length === 0) return [];

  const insertRes = await params.supabase.from("import_job_columns").insert(params.columns).select("*");
  if (insertRes.error) throw new Error(insertRes.error.message);
  return (insertRes.data ?? []).map((row: Database["public"]["Tables"]["import_job_columns"]["Row"]) => mapImportJobColumnRow(row));
}

export async function replaceImportRows(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
  rows: ImportRowInsert[];
}): Promise<ImportJobRow[]> {
  const deleteRes = await params.supabase.from("import_job_rows").delete().eq("org_id", params.orgId).eq("import_job_id", params.jobId);
  if (deleteRes.error) throw new Error(deleteRes.error.message);

  if (params.rows.length === 0) return [];

  const batchSize = 400;
  const all: ImportJobRow[] = [];
  for (let i = 0; i < params.rows.length; i += batchSize) {
    const chunk = params.rows.slice(i, i + batchSize);
    const res = await params.supabase.from("import_job_rows").insert(chunk).select("*");
    if (res.error) throw new Error(res.error.message);
    for (const row of (res.data ?? []) as Database["public"]["Tables"]["import_job_rows"]["Row"][]) {
      all.push(mapImportJobRowRow(row));
    }
  }
  return all;
}

export async function listImportColumns(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
}): Promise<ImportJobColumn[]> {
  const res = await params.supabase
    .from("import_job_columns")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("import_job_id", params.jobId)
    .order("source_column_index", { ascending: true });
  if (res.error) throw new Error(res.error.message);
  return (res.data ?? []).map((row: Database["public"]["Tables"]["import_job_columns"]["Row"]) => mapImportJobColumnRow(row));
}

export async function listImportRows(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
  limit?: number;
  offset?: number;
}): Promise<ImportJobRow[]> {
  let query = params.supabase
    .from("import_job_rows")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("import_job_id", params.jobId)
    .order("source_row_no", { ascending: true });

  if (typeof params.offset === "number" && typeof params.limit === "number") {
    query = query.range(params.offset, params.offset + params.limit - 1);
  } else if (typeof params.limit === "number") {
    query = query.limit(params.limit);
  }

  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return (res.data ?? []).map((row: Database["public"]["Tables"]["import_job_rows"]["Row"]) => mapImportJobRowRow(row));
}

export async function updateImportRow(params: {
  supabase: DbClient;
  rowId: string;
  patch: Database["public"]["Tables"]["import_job_rows"]["Update"];
}): Promise<void> {
  const res = await params.supabase.from("import_job_rows").update(params.patch).eq("id", params.rowId);
  if (res.error) throw new Error(res.error.message);
}

export async function replaceDedupeGroups(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
  groups: Database["public"]["Tables"]["dedupe_match_groups"]["Insert"][];
}): Promise<DedupeMatchGroup[]> {
  const del = await params.supabase.from("dedupe_match_groups").delete().eq("org_id", params.orgId).eq("import_job_id", params.jobId);
  if (del.error) throw new Error(del.error.message);
  if (params.groups.length === 0) return [];

  const ins = await params.supabase.from("dedupe_match_groups").insert(params.groups).select("*");
  if (ins.error) throw new Error(ins.error.message);
  return (ins.data ?? []).map((row: Database["public"]["Tables"]["dedupe_match_groups"]["Row"]) => mapDedupeMatchGroupRow(row));
}

export async function listDedupeGroups(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
}): Promise<DedupeMatchGroup[]> {
  const res = await params.supabase
    .from("dedupe_match_groups")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("import_job_id", params.jobId)
    .order("created_at", { ascending: false });
  if (res.error) throw new Error(res.error.message);
  return (res.data ?? []).map((row: Database["public"]["Tables"]["dedupe_match_groups"]["Row"]) => mapDedupeMatchGroupRow(row));
}

export async function appendImportAuditEvent(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
  actorUserId?: string | null;
  eventType: Database["public"]["Enums"]["import_audit_event_type"];
  eventSummary: string;
  eventPayload?: Record<string, unknown>;
}): Promise<void> {
  const res = await params.supabase.from("import_audit_events").insert({
    org_id: params.orgId,
    import_job_id: params.jobId,
    actor_user_id: params.actorUserId ?? null,
    event_type: params.eventType,
    event_summary: params.eventSummary,
    event_payload: params.eventPayload ?? {}
  });
  if (res.error) throw new Error(res.error.message);
}

export async function listImportAuditEvents(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
  limit?: number;
}): Promise<ImportAuditEvent[]> {
  const res = await params.supabase
    .from("import_audit_events")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("import_job_id", params.jobId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 100);
  if (res.error) throw new Error(res.error.message);
  return (res.data ?? []).map((row: Database["public"]["Tables"]["import_audit_events"]["Row"]) => mapImportAuditEventRow(row));
}

