import { mergeCustomerPayload } from "@/lib/import-dedupe";
import { resolveImportExecutionOutcome } from "@/lib/import-execution-utils";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  appendImportAuditEvent,
  getImportJob,
  listImportRows,
  updateImportJob,
  updateImportRow
} from "@/services/import-job-service";
import { generateImportReviewSummary } from "@/services/import-review-service";
import { getOrgAiControlStatus } from "@/services/org-ai-settings-service";
import { getOrgFeatureFlagMap } from "@/services/org-feature-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";
import type { Database } from "@/types/database";
import type { ImportJobRow } from "@/types/import";

type DbClient = ServerSupabaseClient;

type CustomerStage = Database["public"]["Enums"]["customer_stage"];
type OpportunityStage = Database["public"]["Enums"]["opportunity_stage"];
type RiskLevel = Database["public"]["Enums"]["risk_level"];
type CommunicationType = Database["public"]["Enums"]["communication_type"];
type ImportType = Database["public"]["Enums"]["import_type"];

type CustomerInsert = Database["public"]["Tables"]["customers"]["Insert"];
type OpportunityInsert = Database["public"]["Tables"]["opportunities"]["Insert"];
type FollowupInsert = Database["public"]["Tables"]["followups"]["Insert"];

interface ImportExecutionCounters {
  importedRows: number;
  skippedRows: number;
  mergedRows: number;
  errorRows: number;
  importedCustomers: number;
  importedOpportunities: number;
  importedFollowups: number;
}

interface ImportBootstrapResult {
  generatedAlerts: number;
  generatedWorkItems: number;
  suggestedDealRooms: number;
  touchedCustomers: number;
}

type ImportDetailSnapshot = Database["public"]["Tables"]["import_jobs"]["Update"]["detail_snapshot"];

function asImportDetailSnapshot(value: Record<string, unknown>): ImportDetailSnapshot {
  return value as unknown as ImportDetailSnapshot;
}

function nowIso(): string {
  return new Date().toISOString();
}

function plusDaysIso(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

function cleanString(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function toRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0).map((item) => item.trim());
}

function toNullableIso(value: unknown): string | null {
  const text = cleanString(value);
  if (!text) return null;
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function toNullableNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = cleanString(value);
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function toStage(value: unknown): CustomerStage {
  const text = cleanString(value);
  const allowed: CustomerStage[] = ["lead", "initial_contact", "needs_confirmed", "proposal", "negotiation", "won", "lost"];
  return (allowed.includes(text as CustomerStage) ? text : "lead") as CustomerStage;
}

function toOpportunityStage(value: unknown): OpportunityStage {
  const text = cleanString(value);
  const allowed: OpportunityStage[] = ["discovery", "qualification", "proposal", "business_review", "negotiation", "won", "lost"];
  return (allowed.includes(text as OpportunityStage) ? text : "discovery") as OpportunityStage;
}

function toRiskLevel(value: unknown): RiskLevel {
  const text = cleanString(value);
  const allowed: RiskLevel[] = ["low", "medium", "high"];
  return (allowed.includes(text as RiskLevel) ? text : "medium") as RiskLevel;
}

function toCommunicationType(value: unknown): CommunicationType {
  const text = cleanString(value);
  const allowed: CommunicationType[] = ["phone", "wechat", "email", "meeting", "other"];
  return (allowed.includes(text as CommunicationType) ? text : "other") as CommunicationType;
}

function shouldHandleEntity(importType: ImportType, entity: "customer" | "opportunity" | "followup"): boolean {
  if (importType === "mixed") return true;
  if (importType === "customers") return entity === "customer";
  if (importType === "opportunities") return entity === "opportunity";
  return entity === "followup";
}

function pickDuplicateId(row: ImportJobRow, entityType: "customer" | "opportunity"): string | null {
  if (!Array.isArray(row.duplicateCandidates)) return null;
  const item = row.duplicateCandidates.find(
    (candidate) =>
      candidate &&
      typeof candidate === "object" &&
      candidate.entity_type === entityType &&
      typeof candidate.id === "string" &&
      candidate.id.length > 0
  );
  return item && typeof item.id === "string" ? item.id : null;
}

async function findCustomerByName(params: {
  supabase: DbClient;
  orgId: string;
  companyName?: string;
  customerName?: string;
}): Promise<string | null> {
  const company = cleanString(params.companyName);
  const name = cleanString(params.customerName);

  if (company) {
    const res = await params.supabase
      .from("customers")
      .select("id")
      .eq("org_id", params.orgId)
      .ilike("company_name", company)
      .limit(1)
      .maybeSingle();
    if (res.error) throw new Error(res.error.message);
    if (res.data?.id) return res.data.id;
  }

  if (name) {
    const res = await params.supabase
      .from("customers")
      .select("id")
      .eq("org_id", params.orgId)
      .ilike("name", name)
      .limit(1)
      .maybeSingle();
    if (res.error) throw new Error(res.error.message);
    if (res.data?.id) return res.data.id;
  }

  return null;
}

async function upsertCustomerForRow(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  row: ImportJobRow;
  normalizedCustomer: Record<string, unknown>;
  defaultOwnerId: string;
}): Promise<{ customerId: string | null; merged: boolean; created: boolean }> {
  const mergeResolution = params.row.mergeResolution;
  if (mergeResolution === "skip") return { customerId: null, merged: false, created: false };

  const companyName = cleanString(params.normalizedCustomer.company_name) || cleanString(params.normalizedCustomer.name);
  const contactName = cleanString(params.normalizedCustomer.contact_name) || cleanString(params.normalizedCustomer.name) || companyName;

  if (!companyName && !contactName) {
    return { customerId: null, merged: false, created: false };
  }

  const ownerId = cleanString(params.normalizedCustomer.owner_id) || params.defaultOwnerId;

  const mergeTargetId =
    mergeResolution === "merge_existing"
      ? pickDuplicateId(params.row, "customer")
      : mergeResolution === "create_new"
        ? null
        : pickDuplicateId(params.row, "customer");

  if (mergeTargetId) {
    const existingRes = await params.supabase
      .from("customers")
      .select("id,name,company_name,contact_name,phone,email,source_channel,current_stage,next_followup_at,risk_level,tags,ai_summary")
      .eq("org_id", params.orgId)
      .eq("id", mergeTargetId)
      .single();
    if (existingRes.error) throw new Error(existingRes.error.message);

    const existing = existingRes.data;
    const merged = mergeCustomerPayload({
      existing,
      incoming: {
        name: cleanString(params.normalizedCustomer.name) || undefined,
        company_name: companyName || undefined,
        contact_name: contactName || undefined,
        phone: cleanString(params.normalizedCustomer.phone) || null,
        email: cleanString(params.normalizedCustomer.email) || null,
        source_channel: cleanString(params.normalizedCustomer.source_channel) || null,
        current_stage: toStage(params.normalizedCustomer.stage),
        next_followup_at: toNullableIso(params.normalizedCustomer.next_followup_at),
        risk_level: toRiskLevel(params.normalizedCustomer.risk_level),
        tags: toStringArray(params.normalizedCustomer.tags),
        ai_summary: cleanString(params.normalizedCustomer.ai_summary) || null
      }
    });

    const updateRes = await params.supabase
      .from("customers")
      .update({
        ...merged,
        owner_id: ownerId,
        updated_at: nowIso()
      })
      .eq("org_id", params.orgId)
      .eq("id", mergeTargetId)
      .select("id")
      .single();
    if (updateRes.error) throw new Error(updateRes.error.message);

    return {
      customerId: updateRes.data.id,
      merged: true,
      created: false
    };
  }

  const insertPayload: CustomerInsert = {
    org_id: params.orgId,
    owner_id: ownerId,
    name: cleanString(params.normalizedCustomer.name) || contactName || companyName,
    company_name: companyName || contactName,
    contact_name: contactName || companyName,
    phone: cleanString(params.normalizedCustomer.phone) || null,
    email: cleanString(params.normalizedCustomer.email) || null,
    source_channel: cleanString(params.normalizedCustomer.source_channel) || "import",
    current_stage: toStage(params.normalizedCustomer.stage),
    next_followup_at: toNullableIso(params.normalizedCustomer.next_followup_at),
    win_probability: Math.max(1, Math.min(100, Math.round(toNullableNumber(params.normalizedCustomer.win_probability) ?? 30))),
    risk_level: toRiskLevel(params.normalizedCustomer.risk_level),
    tags: toStringArray(params.normalizedCustomer.tags),
    ai_summary: cleanString(params.normalizedCustomer.ai_summary) || null,
    has_decision_maker: false,
    created_by: params.actorUserId
  };

  const insertRes = await params.supabase.from("customers").insert(insertPayload).select("id").single();
  if (insertRes.error) throw new Error(insertRes.error.message);

  return {
    customerId: insertRes.data.id,
    merged: false,
    created: true
  };
}

async function upsertOpportunityForRow(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  row: ImportJobRow;
  normalizedOpportunity: Record<string, unknown>;
  customerId: string | null;
  defaultOwnerId: string;
}): Promise<{ opportunityId: string | null; merged: boolean; created: boolean }> {
  const mergeResolution = params.row.mergeResolution;
  if (mergeResolution === "skip") return { opportunityId: null, merged: false, created: false };

  const title = cleanString(params.normalizedOpportunity.title);
  if (!title) return { opportunityId: null, merged: false, created: false };

  const ownerId = cleanString(params.normalizedOpportunity.owner_id) || params.defaultOwnerId;

  const mergeTargetId =
    mergeResolution === "merge_existing"
      ? pickDuplicateId(params.row, "opportunity")
      : mergeResolution === "create_new"
        ? null
        : pickDuplicateId(params.row, "opportunity");

  if (mergeTargetId) {
    const patch: Database["public"]["Tables"]["opportunities"]["Update"] = {
      owner_id: ownerId,
      title,
      stage: toOpportunityStage(params.normalizedOpportunity.stage),
      risk_level: toRiskLevel(params.normalizedOpportunity.risk_level),
      expected_close_date: toNullableIso(params.normalizedOpportunity.expected_close_date),
      amount: toNullableNumber(params.normalizedOpportunity.amount) ?? 0,
      last_activity_at: nowIso(),
      updated_at: nowIso()
    };
    if (params.customerId) patch.customer_id = params.customerId;

    const updateRes = await params.supabase
      .from("opportunities")
      .update(patch)
      .eq("org_id", params.orgId)
      .eq("id", mergeTargetId)
      .select("id")
      .single();
    if (updateRes.error) throw new Error(updateRes.error.message);

    return {
      opportunityId: updateRes.data.id,
      merged: true,
      created: false
    };
  }

  if (!params.customerId) return { opportunityId: null, merged: false, created: false };

  const insertPayload: OpportunityInsert = {
    org_id: params.orgId,
    customer_id: params.customerId,
    owner_id: ownerId,
    title,
    amount: toNullableNumber(params.normalizedOpportunity.amount) ?? 0,
    stage: toOpportunityStage(params.normalizedOpportunity.stage),
    risk_level: toRiskLevel(params.normalizedOpportunity.risk_level),
    expected_close_date: toNullableIso(params.normalizedOpportunity.expected_close_date),
    last_activity_at: nowIso(),
    created_by: params.actorUserId
  };

  const insertRes = await params.supabase.from("opportunities").insert(insertPayload).select("id").single();
  if (insertRes.error) throw new Error(insertRes.error.message);

  return {
    opportunityId: insertRes.data.id,
    merged: false,
    created: true
  };
}

async function createFollowupForRow(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  normalizedFollowup: Record<string, unknown>;
  customerId: string | null;
  defaultOwnerId: string;
}): Promise<string | null> {
  const summary = cleanString(params.normalizedFollowup.summary);
  if (!summary || !params.customerId) return null;

  const ownerId = cleanString(params.normalizedFollowup.owner_id) || params.defaultOwnerId;
  const occurredAt = toNullableIso(params.normalizedFollowup.occurred_at);
  const nextFollowupAt = toNullableIso(params.normalizedFollowup.next_followup_at);

  const insertPayload: FollowupInsert = {
    org_id: params.orgId,
    customer_id: params.customerId,
    owner_id: ownerId,
    communication_type: toCommunicationType(params.normalizedFollowup.communication_type),
    summary,
    customer_needs: cleanString(params.normalizedFollowup.customer_needs) || cleanString(params.normalizedFollowup.notes) || "Imported followup",
    objections: cleanString(params.normalizedFollowup.objections) || null,
    next_step: cleanString(params.normalizedFollowup.next_step) || "Follow up by plan",
    next_followup_at: nextFollowupAt,
    needs_ai_analysis: false,
    draft_status: "confirmed",
    created_by: params.actorUserId,
    created_at: occurredAt ?? nowIso()
  };

  const insertRes = await params.supabase.from("followups").insert(insertPayload).select("id").single();
  if (insertRes.error) throw new Error(insertRes.error.message);

  await params.supabase
    .from("customers")
    .update({
      last_followup_at: occurredAt ?? nowIso(),
      next_followup_at: nextFollowupAt,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.customerId);

  return insertRes.data.id;
}

async function applyPostImportBootstrap(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  importedCustomerIds: string[];
  importedOpportunityIds: string[];
}): Promise<ImportBootstrapResult> {
  let generatedAlerts = 0;
  let generatedWorkItems = 0;
  let suggestedDealRooms = 0;

  if (params.importedCustomerIds.length > 0) {
    const customerRes = await params.supabase
      .from("customers")
      .select("id, owner_id, company_name, risk_level, next_followup_at")
      .eq("org_id", params.orgId)
      .in("id", params.importedCustomerIds);
    if (customerRes.error) throw new Error(customerRes.error.message);

    const customers =
      (customerRes.data ?? []) as Array<{
        id: string;
        owner_id: string;
        company_name: string;
        risk_level: RiskLevel;
        next_followup_at: string | null;
      }>;

    const alertInserts: Database["public"]["Tables"]["alerts"]["Insert"][] = [];
    const workInserts: Database["public"]["Tables"]["work_items"]["Insert"][] = [];

    for (const customer of customers) {
      if (customer.risk_level === "high" && !customer.next_followup_at) {
        alertInserts.push({
          org_id: params.orgId,
          customer_id: customer.id,
          owner_id: customer.owner_id,
          rule_type: "no_followup_timeout",
          source: "rule",
          severity: "warning",
          status: "open",
          title: `Imported high-risk customer lacks next followup: ${customer.company_name}`,
          description: "Import bootstrap detected high-risk customer without next follow-up date.",
          evidence: ["source=import_bootstrap", "rule=no_followup_timeout"],
          suggested_owner_action: ["Set next followup date", "Create immediate outreach task"]
        });
      }

      workInserts.push({
        org_id: params.orgId,
        owner_id: customer.owner_id,
        customer_id: customer.id,
        source_type: "ai_suggested",
        work_type: "review_customer",
        title: `Review imported customer: ${customer.company_name}`,
        description: "Validate imported data and confirm first follow-up plan.",
        rationale: "Post-import bootstrap task",
        priority_score: customer.risk_level === "high" ? 82 : 64,
        priority_band: customer.risk_level === "high" ? "high" : "medium",
        status: "todo",
        scheduled_for: new Date().toISOString().slice(0, 10),
        due_at: plusDaysIso(2),
        ai_generated: false,
        created_by: params.actorUserId
      });
    }

    if (alertInserts.length > 0) {
      const alertRes = await params.supabase.from("alerts").insert(alertInserts).select("id");
      if (!alertRes.error) generatedAlerts = alertRes.data?.length ?? alertInserts.length;
    }

    if (workInserts.length > 0) {
      const workRes = await params.supabase.from("work_items").insert(workInserts).select("id");
      if (!workRes.error) generatedWorkItems = workRes.data?.length ?? workInserts.length;
    }
  }

  if (params.importedOpportunityIds.length > 0) {
    const oppRes = await params.supabase
      .from("opportunities")
      .select("id, customer_id, owner_id, title, amount, stage")
      .eq("org_id", params.orgId)
      .in("id", params.importedOpportunityIds)
      .gte("amount", 200000);
    if (oppRes.error) throw new Error(oppRes.error.message);

    const opportunities =
      (oppRes.data ?? []) as Array<{
        id: string;
        customer_id: string;
        owner_id: string;
        title: string;
        amount: number;
        stage: OpportunityStage;
      }>;

    for (const opportunity of opportunities) {
      const existRes = await params.supabase
        .from("deal_rooms")
        .select("id")
        .eq("org_id", params.orgId)
        .eq("opportunity_id", opportunity.id)
        .maybeSingle();
      if (existRes.error) throw new Error(existRes.error.message);
      if (existRes.data?.id) continue;

      const createRes = await params.supabase
        .from("deal_rooms")
        .insert({
          org_id: params.orgId,
          customer_id: opportunity.customer_id,
          opportunity_id: opportunity.id,
          owner_id: opportunity.owner_id,
          room_status: "watchlist",
          priority_band: "important",
          title: `Imported Deal: ${opportunity.title}`,
          command_summary: "Auto-created after importing high-value opportunity.",
          current_goal: "Validate imported opportunity data and define next milestone.",
          current_blockers: [],
          next_milestone: "Owner confirmation",
          next_milestone_due_at: plusDaysIso(3),
          manager_attention_needed: opportunity.stage === "proposal" || opportunity.stage === "negotiation",
          source_snapshot: {
            source: "import_bootstrap",
            amount: opportunity.amount
          },
          created_by: params.actorUserId
        })
        .select("id")
        .single();

      if (!createRes.error) suggestedDealRooms += 1;
    }
  }

  return {
    generatedAlerts,
    generatedWorkItems,
    suggestedDealRooms,
    touchedCustomers: params.importedCustomerIds.length
  };
}

export async function executeImportJob(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
  runReviewSummary?: boolean;
}): Promise<{
  job: Awaited<ReturnType<typeof getImportJob>>;
  counters: ImportExecutionCounters;
  bootstrap: ImportBootstrapResult;
  review: {
    review: {
      summary: string;
      issues: string[];
      recommended_cleanup: string[];
      recommended_next_steps: string[];
    };
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
  } | null;
}> {
  const job = await getImportJob({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId
  });

  await updateImportJob({
    supabase: params.supabase,
    jobId: params.jobId,
    patch: {
      job_status: "importing"
    }
  });

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "import_started",
    eventSummary: "Import execution started"
  });

  const rows = await listImportRows({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId
  });

  const counters: ImportExecutionCounters = {
    importedRows: 0,
    skippedRows: 0,
    mergedRows: 0,
    errorRows: 0,
    importedCustomers: 0,
    importedOpportunities: 0,
    importedFollowups: 0
  };

  const importedCustomerIds = new Set<string>();
  const importedOpportunityIds = new Set<string>();

  for (const row of rows) {
    try {
      if (row.rowStatus === "invalid" || row.rowStatus === "failed") {
        counters.errorRows += 1;
        continue;
      }
      if (row.rowStatus === "pending" || row.rowStatus === "skipped") {
        counters.skippedRows += 1;
        continue;
      }
      if (row.rowStatus === "duplicate_candidate" && !row.mergeResolution) {
        counters.skippedRows += 1;
        await updateImportRow({
          supabase: params.supabase,
          rowId: row.id,
          patch: {
            row_status: "skipped",
            validation_errors: [...row.validationErrors, "duplicate_resolution_required"]
          }
        });
        continue;
      }

      const normalizedRoot = toRecord(row.normalizedPayload);
      const normalizedCustomer = toRecord(normalizedRoot.customer);
      const normalizedOpportunity = toRecord(normalizedRoot.opportunity);
      const normalizedFollowup = toRecord(normalizedRoot.followup);
      const defaultOwnerId =
        cleanString(normalizedCustomer.owner_id) || cleanString(normalizedOpportunity.owner_id) || cleanString(normalizedFollowup.owner_id) || params.actorUserId;

      let customerId: string | null = cleanString(normalizedOpportunity.customer_id) || cleanString(normalizedFollowup.customer_id) || null;
      let primaryEntityType: Database["public"]["Enums"]["import_entity_type"] | null = null;
      let primaryEntityId: string | null = null;
      let createdAny = false;
      let mergedAny = false;

      if (shouldHandleEntity(job.importType, "customer")) {
        const customerOutcome = await upsertCustomerForRow({
          supabase: params.supabase,
          orgId: params.orgId,
          actorUserId: params.actorUserId,
          row,
          normalizedCustomer,
          defaultOwnerId
        });

        if (customerOutcome.customerId) {
          customerId = customerOutcome.customerId;
          importedCustomerIds.add(customerOutcome.customerId);
          if (customerOutcome.created) counters.importedCustomers += 1;
          if (customerOutcome.merged) {
            counters.mergedRows += 1;
            mergedAny = true;
          }
          if (customerOutcome.created) createdAny = true;
          primaryEntityType = "customer";
          primaryEntityId = customerOutcome.customerId;
        }
      }

      if (!customerId) {
        customerId = await findCustomerByName({
          supabase: params.supabase,
          orgId: params.orgId,
          companyName: cleanString(normalizedCustomer.company_name),
          customerName: cleanString(normalizedCustomer.name)
        });
      }

      if (shouldHandleEntity(job.importType, "opportunity")) {
        const opportunityOutcome = await upsertOpportunityForRow({
          supabase: params.supabase,
          orgId: params.orgId,
          actorUserId: params.actorUserId,
          row,
          normalizedOpportunity,
          customerId,
          defaultOwnerId
        });

        if (opportunityOutcome.opportunityId) {
          importedOpportunityIds.add(opportunityOutcome.opportunityId);
          if (opportunityOutcome.created) counters.importedOpportunities += 1;
          if (opportunityOutcome.merged) {
            counters.mergedRows += 1;
            mergedAny = true;
          }
          if (opportunityOutcome.created) createdAny = true;
          primaryEntityType = opportunityOutcome.created || opportunityOutcome.merged ? "opportunity" : primaryEntityType;
          primaryEntityId = opportunityOutcome.opportunityId;
        }
      }

      if (shouldHandleEntity(job.importType, "followup")) {
        const followupId = await createFollowupForRow({
          supabase: params.supabase,
          orgId: params.orgId,
          actorUserId: params.actorUserId,
          normalizedFollowup,
          customerId,
          defaultOwnerId
        });
        if (followupId) {
          counters.importedFollowups += 1;
          createdAny = true;
          primaryEntityType = "followup";
          primaryEntityId = followupId;
        }
      }

      if (!createdAny && !mergedAny) {
        counters.skippedRows += 1;
        await updateImportRow({
          supabase: params.supabase,
          rowId: row.id,
          patch: {
            row_status: "skipped",
            validation_errors: [...row.validationErrors, "no_importable_entity"]
          }
        });
        continue;
      }

      counters.importedRows += 1;

      await updateImportRow({
        supabase: params.supabase,
        rowId: row.id,
        patch: {
          row_status: "imported",
          imported_entity_type: createdAny && mergedAny ? "mixed" : primaryEntityType,
          imported_entity_id: primaryEntityId,
          merge_resolution: row.mergeResolution
        }
      });

      await appendImportAuditEvent({
        supabase: params.supabase,
        orgId: params.orgId,
        jobId: params.jobId,
        actorUserId: params.actorUserId,
        eventType: "row_imported",
        eventSummary: `Row ${row.sourceRowNo} imported`,
        eventPayload: {
          row_id: row.id,
          source_row_no: row.sourceRowNo,
          entity_type: createdAny && mergedAny ? "mixed" : primaryEntityType,
          entity_id: primaryEntityId,
          merged: mergedAny
        }
      });
    } catch (error) {
      const reason = error instanceof Error ? error.message : "row_import_failed";
      counters.errorRows += 1;
      await updateImportRow({
        supabase: params.supabase,
        rowId: row.id,
        patch: {
          row_status: "failed",
          validation_errors: [...row.validationErrors, reason]
        }
      }).catch(() => null);

      await appendImportAuditEvent({
        supabase: params.supabase,
        orgId: params.orgId,
        jobId: params.jobId,
        actorUserId: params.actorUserId,
        eventType: "row_failed",
        eventSummary: `Row ${row.sourceRowNo} failed`,
        eventPayload: {
          row_id: row.id,
          source_row_no: row.sourceRowNo,
          reason
        }
      }).catch(() => null);
    }
  }

  const bootstrap = await applyPostImportBootstrap({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    importedCustomerIds: Array.from(importedCustomerIds),
    importedOpportunityIds: Array.from(importedOpportunityIds)
  });

  const executionOutcome = resolveImportExecutionOutcome({
    importedRows: counters.importedRows,
    errorRows: counters.errorRows
  });

  await updateImportJob({
    supabase: params.supabase,
    jobId: params.jobId,
    patch: {
      job_status: executionOutcome.jobStatus,
      imported_rows: counters.importedRows,
      skipped_rows: counters.skippedRows,
      merged_rows: counters.mergedRows,
      error_rows: counters.errorRows,
      valid_rows: job.validRows,
      invalid_rows: job.invalidRows,
      summary: `Import ${executionOutcome.partialSuccess ? "partially completed" : executionOutcome.jobStatus}: imported=${counters.importedRows}, merged=${counters.mergedRows}, skipped=${counters.skippedRows}, failed=${counters.errorRows}`,
      detail_snapshot: asImportDetailSnapshot({
        ...(job.detailSnapshot ?? {}),
        execution_counters: { ...counters },
        bootstrap: { ...bootstrap },
        execution_partial_success: executionOutcome.partialSuccess
      })
    }
  });

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "completed",
    eventSummary: "Import execution completed",
    eventPayload: {
      counters,
      bootstrap
    }
  });

  let review: {
    review: {
      summary: string;
      issues: string[];
      recommended_cleanup: string[];
      recommended_next_steps: string[];
    };
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
  } | null = null;

  const [featureFlags, aiStatus, entitlement] = await Promise.all([
    getOrgFeatureFlagMap({ supabase: params.supabase, orgId: params.orgId }),
    getOrgAiControlStatus({ supabase: params.supabase, orgId: params.orgId }),
    getEntitlementStatus({ supabase: params.supabase, orgId: params.orgId, refreshUsage: true })
  ]);

  const canRunAi = canRunAiByEntitlement(entitlement).allowed && aiStatus.providerConfigured;
  const shouldRunReview = (params.runReviewSummary ?? true) && featureFlags.ai_auto_analysis && canRunAi;

  review = await generateImportReviewSummary({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    jobId: params.jobId,
    forceFallback: !shouldRunReview
  });

  if (!shouldRunReview && review) {
    await updateImportJob({
      supabase: params.supabase,
      jobId: params.jobId,
      patch: {
        detail_snapshot: asImportDetailSnapshot({
          ...(job.detailSnapshot ?? {}),
          execution_counters: { ...counters },
          bootstrap: { ...bootstrap },
          review_note: "Review summary generated with fallback path due org AI settings or quota constraints."
        })
      }
    }).catch(() => null);
  }

  const latestJob = await getImportJob({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId
  });

  return {
    job: latestJob,
    counters,
    bootstrap,
    review
  };
}

