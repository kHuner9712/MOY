import { findCustomerDuplicateCandidates, findOpportunityDuplicateCandidates } from "@/lib/import-dedupe";
import { buildImportOwnerMap, type ImportOwnerCandidate, resolveImportOwnerId } from "@/lib/import-owner-mapping";
import {
  normalizeCommunicationType,
  normalizeCurrencyAmount,
  normalizeCustomerStage,
  normalizeDateValue,
  normalizeOpportunityStage,
  normalizeRiskLevel,
  normalizeTags
} from "@/lib/import-normalization";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { appendImportAuditEvent, listImportColumns, listImportRows, updateImportJob } from "@/services/import-job-service";
import type { Database } from "@/types/database";
import type { ImportJobRow } from "@/types/import";

type DbClient = ServerSupabaseClient;

function cleanString(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function normalizeMappedField(params: {
  entity: string;
  field: string;
  rawValue: unknown;
  ownerMap: Map<string, ImportOwnerCandidate>;
}): { value: unknown; error: string | null } {
  const raw = params.rawValue;
  const field = params.field;

  if (field === "owner") {
    const ownerId = resolveImportOwnerId(raw, params.ownerMap);
    return ownerId ? { value: ownerId, error: null } : { value: null, error: "owner_not_found" };
  }
  if (field === "amount") {
    const amount = normalizeCurrencyAmount(raw);
    return amount === null ? { value: null, error: "invalid_amount" } : { value: amount, error: null };
  }
  if (field === "next_followup_at" || field === "expected_close_date" || field === "created_at" || field === "occurred_at") {
    const date = normalizeDateValue(raw);
    return date ? { value: date, error: null } : { value: null, error: "invalid_date" };
  }
  if (field === "stage") {
    if (params.entity === "customer") {
      const stage = normalizeCustomerStage(raw);
      return stage ? { value: stage, error: null } : { value: null, error: "invalid_customer_stage" };
    }
    if (params.entity === "opportunity") {
      const stage = normalizeOpportunityStage(raw);
      return stage ? { value: stage, error: null } : { value: null, error: "invalid_opportunity_stage" };
    }
  }
  if (field === "risk_level") {
    const risk = normalizeRiskLevel(raw);
    return risk ? { value: risk, error: null } : { value: null, error: "invalid_risk_level" };
  }
  if (field === "communication_type") {
    const method = normalizeCommunicationType(raw);
    return method ? { value: method, error: null } : { value: null, error: "invalid_communication_type" };
  }
  if (field === "tags") {
    return { value: normalizeTags(raw), error: null };
  }
  return { value: cleanString(raw), error: null };
}

function buildBaseNormalized(): Record<string, any> {
  return {
    customer: {},
    opportunity: {},
    followup: {}
  };
}

function validateNormalizedRow(params: {
  jobType: Database["public"]["Enums"]["import_type"];
  normalized: Record<string, any>;
  errors: string[];
}): string[] {
  const issues = [...params.errors];
  const customer = params.normalized.customer ?? {};
  const opportunity = params.normalized.opportunity ?? {};
  const followup = params.normalized.followup ?? {};

  if (params.jobType === "customers" || params.jobType === "mixed") {
    if (!cleanString(customer.company_name) && !cleanString(customer.name)) issues.push("customer_name_or_company_required");
    if (!cleanString(customer.owner_id)) issues.push("customer_owner_required");
  }

  if (params.jobType === "opportunities" || params.jobType === "mixed") {
    if (!cleanString(opportunity.title)) issues.push("opportunity_title_required");
    if (opportunity.amount === null || opportunity.amount === undefined || Number.isNaN(Number(opportunity.amount))) {
      issues.push("opportunity_amount_required");
    }
  }

  if (params.jobType === "followups" || params.jobType === "mixed") {
    if (!cleanString(followup.summary)) issues.push("followup_summary_required");
  }

  return Array.from(new Set(issues));
}

export async function validateImportJob(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
}): Promise<{
  totalRows: number;
  validRows: number;
  invalidRows: number;
  duplicateRows: number;
}> {
  const [jobRes, columns, rows] = await Promise.all([
    params.supabase.from("import_jobs").select("*").eq("id", params.jobId).eq("org_id", params.orgId).single(),
    listImportColumns({ supabase: params.supabase, orgId: params.orgId, jobId: params.jobId }),
    listImportRows({ supabase: params.supabase, orgId: params.orgId, jobId: params.jobId })
  ]);
  if (jobRes.error) throw new Error(jobRes.error.message);
  const job = jobRes.data;

  const ownerRes = await params.supabase
    .from("org_memberships")
    .select("user_id, role, profile:profiles(id, display_name)")
    .eq("org_id", params.orgId)
    .eq("seat_status", "active");
  if (ownerRes.error) throw new Error(ownerRes.error.message);
  const ownerMap = buildImportOwnerMap(
    ((ownerRes.data ?? []) as any[]).map((row) => ({
      user_id: row.user_id,
      profile: row.profile ?? null,
      email: null
    }))
  );

  const existingCustomersRes = await params.supabase
    .from("customers")
    .select("id, company_name, contact_name, phone, email, owner_id")
    .eq("org_id", params.orgId);
  if (existingCustomersRes.error) throw new Error(existingCustomersRes.error.message);

  const existingOppRes = await params.supabase
    .from("opportunities")
    .select("id, customer_id, title, amount, stage, owner_id")
    .eq("org_id", params.orgId);
  if (existingOppRes.error) throw new Error(existingOppRes.error.message);

  const existingCustomerLite = (
    (existingCustomersRes.data ?? []) as Array<{
      id: string;
      company_name: string;
      contact_name: string;
      phone: string | null;
      email: string | null;
      owner_id: string;
    }>
  ).map((row: { id: string; company_name: string; contact_name: string; phone: string | null; email: string | null; owner_id: string }) => ({
    id: row.id,
    companyName: row.company_name,
    contactName: row.contact_name,
    phone: row.phone,
    email: row.email,
    ownerId: row.owner_id
  }));
  const existingOpportunityLite = (
    (existingOppRes.data ?? []) as Array<{
      id: string;
      customer_id: string;
      title: string;
      amount: number;
      stage: string;
      owner_id: string;
    }>
  ).map((row: { id: string; customer_id: string; title: string; amount: number; stage: string; owner_id: string }) => ({
    id: row.id,
    customerId: row.customer_id,
    title: row.title,
    amount: row.amount,
    stage: row.stage,
    ownerId: row.owner_id
  }));

  const updates: Array<{
    id: string;
    rowStatus: Database["public"]["Enums"]["import_row_status"];
    normalized: Record<string, unknown>;
    errors: string[];
    duplicates: Array<Record<string, unknown>>;
  }> = [];

  for (const row of rows) {
    const normalized = buildBaseNormalized();
    const errors: string[] = [];

    for (const column of columns) {
      if (!column.mappedTargetEntity || !column.mappedTargetField) continue;
      const raw = row.rawPayload[column.sourceColumnName];
      if (raw === undefined || raw === null || cleanString(raw) === "") continue;

      const value = normalizeMappedField({
        entity: column.mappedTargetEntity,
        field: column.mappedTargetField,
        rawValue: raw,
        ownerMap
      });
      if (value.error) errors.push(`${column.sourceColumnName}:${value.error}`);

      if (column.mappedTargetEntity === "customer") {
        if (column.mappedTargetField === "owner") normalized.customer.owner_id = value.value;
        else normalized.customer[column.mappedTargetField] = value.value;
      } else if (column.mappedTargetEntity === "opportunity") {
        if (column.mappedTargetField === "owner") normalized.opportunity.owner_id = value.value;
        else normalized.opportunity[column.mappedTargetField] = value.value;
      } else if (column.mappedTargetEntity === "followup") {
        if (column.mappedTargetField === "owner") normalized.followup.owner_id = value.value;
        else normalized.followup[column.mappedTargetField] = value.value;
      }
    }

    const issues = validateNormalizedRow({
      jobType: job.import_type,
      normalized,
      errors
    });

    let duplicateCandidates: Array<Record<string, unknown>> = [];
    if (issues.length === 0) {
      if (normalized.customer?.company_name || normalized.customer?.name) {
        duplicateCandidates = findCustomerDuplicateCandidates({
          incoming: {
            companyName: cleanString(normalized.customer?.company_name || normalized.customer?.name),
            contactName: cleanString(normalized.customer?.contact_name),
            phone: cleanString(normalized.customer?.phone) || null,
            email: cleanString(normalized.customer?.email) || null,
            ownerId: cleanString(normalized.customer?.owner_id) || null
          },
          existing: existingCustomerLite
        }).map((item) => ({ entity_type: "customer", ...item }));
      }

      if (duplicateCandidates.length === 0 && (normalized.opportunity?.title || normalized.opportunity?.amount)) {
        duplicateCandidates = findOpportunityDuplicateCandidates({
          incoming: {
            customerId: cleanString(normalized.opportunity?.customer_id) || null,
            title: cleanString(normalized.opportunity?.title),
            amount: typeof normalized.opportunity?.amount === "number" ? normalized.opportunity.amount : null,
            stage: cleanString(normalized.opportunity?.stage) || null,
            ownerId: cleanString(normalized.opportunity?.owner_id) || null
          },
          existing: existingOpportunityLite
        }).map((item) => ({ entity_type: "opportunity", ...item }));
      }
    }

    const rowStatus: Database["public"]["Enums"]["import_row_status"] =
      issues.length > 0 ? "invalid" : duplicateCandidates.length > 0 ? "duplicate_candidate" : "valid";

    updates.push({
      id: row.id,
      rowStatus,
      normalized,
      errors: issues,
      duplicates: duplicateCandidates
    });
  }

  for (const item of updates) {
    const res = await params.supabase
      .from("import_job_rows")
      .update({
        normalized_payload: item.normalized,
        row_status: item.rowStatus,
        validation_errors: item.errors,
        duplicate_candidates: item.duplicates
      })
      .eq("id", item.id);
    if (res.error) throw new Error(res.error.message);
  }

  const totalRows = rows.length;
  const validRows = updates.filter((item) => item.rowStatus === "valid").length;
  const invalidRows = updates.filter((item) => item.rowStatus === "invalid").length;
  const duplicateRows = updates.filter((item) => item.rowStatus === "duplicate_candidate").length;

  await updateImportJob({
    supabase: params.supabase,
    jobId: params.jobId,
    patch: {
      job_status: "preview_ready",
      total_rows: totalRows,
      valid_rows: validRows,
      invalid_rows: invalidRows,
      duplicate_rows: duplicateRows
    }
  });

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "validation_run",
    eventSummary: `Validation done: valid=${validRows}, invalid=${invalidRows}, duplicate=${duplicateRows}`,
    eventPayload: { totalRows, validRows, invalidRows, duplicateRows }
  });

  return {
    totalRows,
    validRows,
    invalidRows,
    duplicateRows
  };
}

