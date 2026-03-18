import { createHash, randomUUID } from "crypto";

import { requireSelfSalesOrgIdEnv } from "@/lib/env";
import { createSupabaseAdminClient, hasSupabaseAdminEnv } from "@/lib/supabase/admin";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { createDealRoom } from "@/services/deal-room-service";
import { resolveLeadOwner, runLeadQualificationAssist } from "@/services/lead-assignment-service";
import { createWorkItem } from "@/services/work-item-service";
import type {
  ConversionEvent,
  ConversionEventType,
  InboundLead,
  InboundLeadSource,
  InboundLeadStatus
} from "@/types/commercialization";

type DbClient = ServerSupabaseClient;

interface InboundLeadRow {
  id: string;
  org_id: string;
  lead_source: InboundLeadSource;
  company_name: string;
  contact_name: string;
  email: string;
  phone: string | null;
  industry_hint: string | null;
  team_size_hint: string | null;
  use_case_hint: string | null;
  source_campaign: string | null;
  landing_page: string | null;
  status: InboundLeadStatus;
  assigned_owner_id: string | null;
  converted_customer_id: string | null;
  converted_opportunity_id: string | null;
  notes: string | null;
  payload_snapshot: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

interface ConversionEventRow {
  id: string;
  org_id: string;
  target_org_id: string | null;
  lead_id: string | null;
  event_type: ConversionEventType;
  event_summary: string;
  event_payload: Record<string, unknown> | null;
  created_at: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function mapInboundLeadRow(row: InboundLeadRow): InboundLead {
  return {
    id: row.id,
    orgId: row.org_id,
    leadSource: row.lead_source,
    companyName: row.company_name,
    contactName: row.contact_name,
    email: row.email,
    phone: row.phone,
    industryHint: row.industry_hint,
    teamSizeHint: row.team_size_hint,
    useCaseHint: row.use_case_hint,
    sourceCampaign: row.source_campaign,
    landingPage: row.landing_page,
    status: row.status,
    assignedOwnerId: row.assigned_owner_id,
    convertedCustomerId: row.converted_customer_id,
    convertedOpportunityId: row.converted_opportunity_id,
    notes: row.notes,
    payloadSnapshot: asRecord(row.payload_snapshot),
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapConversionEventRow(row: ConversionEventRow): ConversionEvent {
  return {
    id: row.id,
    orgId: row.org_id,
    targetOrgId: row.target_org_id,
    leadId: row.lead_id,
    eventType: row.event_type,
    eventSummary: row.event_summary,
    eventPayload: asRecord(row.event_payload),
    createdAt: row.created_at
  };
}

export async function appendConversionEvent(params: {
  supabase: DbClient;
  orgId: string;
  leadId?: string | null;
  targetOrgId?: string | null;
  eventType: ConversionEventType;
  eventSummary: string;
  eventPayload?: Record<string, unknown>;
}): Promise<ConversionEvent> {
  const res = await (params.supabase as any)
    .from("conversion_events")
    .insert({
      org_id: params.orgId,
      lead_id: params.leadId ?? null,
      target_org_id: params.targetOrgId ?? null,
      event_type: params.eventType,
      event_summary: params.eventSummary,
      event_payload: params.eventPayload ?? {}
    })
    .select("*")
    .single();

  if (res.error) throw new Error(res.error.message);
  return mapConversionEventRow(res.data as ConversionEventRow);
}

async function ensureSelfSalesOrgId(params: {
  supabase: DbClient;
}): Promise<string> {
  const envOrgId = requireSelfSalesOrgIdEnv("public_self_sales");

  const res = await (params.supabase as any).from("organizations").select("id").eq("id", envOrgId).maybeSingle();
  if (res.error) throw new Error(res.error.message);
  if (!res.data) throw new Error("self_sales_org_not_found");
  return envOrgId;
}

export async function getSelfSalesOrgId(): Promise<string> {
  if (!hasSupabaseAdminEnv()) throw new Error("supabase_admin_env_missing");
  const admin = createSupabaseAdminClient() as unknown as DbClient;
  return ensureSelfSalesOrgId({ supabase: admin });
}

export async function listInboundLeads(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  statuses?: InboundLeadStatus[];
  sources?: InboundLeadSource[];
  limit?: number;
}): Promise<InboundLead[]> {
  let query = (params.supabase as any)
    .from("inbound_leads")
    .select("*, owner:profiles!inbound_leads_assigned_owner_id_fkey(display_name)")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 100);

  if (params.ownerId) query = query.eq("assigned_owner_id", params.ownerId);
  if (params.statuses?.length) query = query.in("status", params.statuses);
  if (params.sources?.length) query = query.in("lead_source", params.sources);

  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as Array<InboundLeadRow & { owner?: { display_name: string } | null }>).map((row) => ({
    ...mapInboundLeadRow(row),
    assignedOwnerName: row.owner?.display_name ?? undefined
  }));
}

export async function getInboundLeadById(params: {
  supabase: DbClient;
  orgId: string;
  leadId: string;
}): Promise<InboundLead | null> {
  const res = await (params.supabase as any)
    .from("inbound_leads")
    .select("*, owner:profiles!inbound_leads_assigned_owner_id_fkey(display_name)")
    .eq("org_id", params.orgId)
    .eq("id", params.leadId)
    .maybeSingle();
  if (res.error) throw new Error(res.error.message);
  if (!res.data) return null;

  const row = res.data as InboundLeadRow & { owner?: { display_name: string } | null };
  return {
    ...mapInboundLeadRow(row),
    assignedOwnerName: row.owner?.display_name ?? undefined
  };
}

export async function createInboundLead(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string | null;
  leadSource: InboundLeadSource;
  companyName: string;
  contactName: string;
  email: string;
  phone?: string | null;
  industryHint?: string | null;
  teamSizeHint?: string | null;
  useCaseHint?: string | null;
  sourceCampaign?: string | null;
  landingPage?: string | null;
  notes?: string | null;
  payloadSnapshot?: Record<string, unknown>;
  createPipelineDraft?: boolean;
}): Promise<{
  lead: InboundLead;
  qualification: Awaited<ReturnType<typeof runLeadQualificationAssist>>;
  assignment: { ownerId: string; ownerName: string; matchedRuleId: string | null };
  pipelineCreated: boolean;
}> {
  const assignment = await resolveLeadOwner({
    supabase: params.supabase,
    orgId: params.orgId,
    leadSource: params.leadSource,
    industryHint: params.industryHint ?? null,
    teamSizeHint: params.teamSizeHint ?? null
  });

  const qualification = await runLeadQualificationAssist({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    leadSource: params.leadSource,
    companyName: params.companyName,
    contactName: params.contactName,
    industryHint: params.industryHint ?? null,
    teamSizeHint: params.teamSizeHint ?? null,
    useCaseHint: params.useCaseHint ?? null
  });

  const status: InboundLeadStatus = qualification.result.fitScore >= 60 ? "qualified" : "new";

  const insertRes = await (params.supabase as any)
    .from("inbound_leads")
    .insert({
      org_id: params.orgId,
      lead_source: params.leadSource,
      company_name: params.companyName,
      contact_name: params.contactName,
      email: params.email.trim().toLowerCase(),
      phone: params.phone ?? null,
      industry_hint: params.industryHint ?? null,
      team_size_hint: params.teamSizeHint ?? null,
      use_case_hint: params.useCaseHint ?? null,
      source_campaign: params.sourceCampaign ?? null,
      landing_page: params.landingPage ?? null,
      status,
      assigned_owner_id: assignment.ownerId,
      notes: params.notes ?? null,
      payload_snapshot: {
        ...(params.payloadSnapshot ?? {}),
        qualification_run_id: qualification.runId,
        qualification_fit_score: qualification.result.fitScore,
        qualification_risk_flags: qualification.result.riskFlags,
        matched_rule_id: assignment.matchedRuleId
      }
    })
    .select("*")
    .single();

  if (insertRes.error) throw new Error(insertRes.error.message);
  const lead = mapInboundLeadRow(insertRes.data as InboundLeadRow);

  await appendConversionEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: lead.id,
    eventType: "lead_created",
    eventSummary: `Inbound lead created from ${lead.leadSource}.`,
    eventPayload: {
      qualification_fit_score: qualification.result.fitScore,
      assigned_owner_id: assignment.ownerId
    }
  });

  let pipelineCreated = false;
  if (params.createPipelineDraft && lead.status !== "unqualified") {
    const converted = await convertLeadToSalesPipeline({
      supabase: params.supabase,
      orgId: params.orgId,
      leadId: lead.id,
      actorUserId: params.actorUserId ?? assignment.ownerId,
      allowExisting: true
    });
    pipelineCreated = converted.converted;
  }

  return {
    lead: {
      ...lead,
      assignedOwnerName: assignment.ownerName
    },
    qualification,
    assignment,
    pipelineCreated
  };
}

export async function updateInboundLeadStatus(params: {
  supabase: DbClient;
  orgId: string;
  leadId: string;
  status: InboundLeadStatus;
  notes?: string | null;
}): Promise<InboundLead> {
  const res = await (params.supabase as any)
    .from("inbound_leads")
    .update({
      status: params.status,
      notes: params.notes ?? null
    })
    .eq("org_id", params.orgId)
    .eq("id", params.leadId)
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapInboundLeadRow(res.data as InboundLeadRow);
}

export async function convertLeadToSalesPipeline(params: {
  supabase: DbClient;
  orgId: string;
  leadId: string;
  actorUserId: string;
  allowExisting?: boolean;
}): Promise<{
  converted: boolean;
  customerId: string | null;
  opportunityId: string | null;
  dealRoomId: string | null;
}> {
  const lead = await getInboundLeadById({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: params.leadId
  });
  if (!lead) throw new Error("inbound_lead_not_found");

  if (lead.convertedCustomerId && lead.convertedOpportunityId && params.allowExisting) {
    return {
      converted: false,
      customerId: lead.convertedCustomerId,
      opportunityId: lead.convertedOpportunityId,
      dealRoomId: null
    };
  }

  const ownerId = lead.assignedOwnerId ?? params.actorUserId;
  const ownerName = lead.assignedOwnerName ?? ownerId;

  let customerId: string | null = lead.convertedCustomerId;
  if (!customerId) {
    const existingCustomerRes = await (params.supabase as any)
      .from("customers")
      .select("id")
      .eq("org_id", params.orgId)
      .eq("company_name", lead.companyName)
      .limit(1)
      .maybeSingle();
    if (existingCustomerRes.error) throw new Error(existingCustomerRes.error.message);

    if (existingCustomerRes.data) {
      customerId = String(existingCustomerRes.data.id);
    } else {
      const customerInsertRes = await (params.supabase as any)
        .from("customers")
        .insert({
          org_id: params.orgId,
          owner_id: ownerId,
          name: lead.contactName,
          company_name: lead.companyName,
          contact_name: lead.contactName,
          phone: lead.phone,
          email: lead.email,
          source_channel: lead.leadSource,
          current_stage: "lead",
          win_probability: Math.max(35, Math.min(90, Number(lead.payloadSnapshot.qualification_fit_score ?? 60))),
          risk_level: "medium",
          tags: ["moy_self_sales", "inbound_lead"],
          ai_summary: lead.useCaseHint ?? "Inbound lead from public website.",
          ai_suggestion: "Complete qualification call and schedule product demo.",
          has_decision_maker: false,
          created_by: ownerId
        })
        .select("id")
        .single();
      if (customerInsertRes.error) throw new Error(customerInsertRes.error.message);
      customerId = String(customerInsertRes.data.id);
    }
  }

  const opportunityInsertRes = await (params.supabase as any)
    .from("opportunities")
    .insert({
      org_id: params.orgId,
      customer_id: customerId,
      owner_id: ownerId,
      title: `MOY Opportunity | ${lead.companyName}`,
      amount: 12000,
      stage: "qualification",
      risk_level: "medium",
      expected_close_date: new Date(Date.now() + 21 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
      last_activity_at: new Date().toISOString(),
      created_by: ownerId
    })
    .select("id")
    .single();
  if (opportunityInsertRes.error) throw new Error(opportunityInsertRes.error.message);
  const opportunityId = String(opportunityInsertRes.data.id);

  const roomResult = await createDealRoom({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId,
    createdBy: params.actorUserId,
    customerId,
    opportunityId,
    title: `MOY Self-Sales | ${lead.companyName}`,
    currentGoal: lead.leadSource === "website_trial" ? "Activate trial and show first value within 7 days." : "Complete qualification and deliver high-conversion demo.",
    currentBlockers: [],
    priorityBand: lead.leadSource === "website_trial" ? "strategic" : "important",
    managerAttentionNeeded: lead.leadSource === "website_trial",
    sourceSnapshot: {
      source: "commercialization",
      lead_id: lead.id,
      lead_source: lead.leadSource,
      owner_name: ownerName
    }
  });

  await createWorkItem({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId,
    customerId,
    opportunityId,
    sourceType: "manual",
    workType: lead.leadSource === "website_trial" ? "schedule_demo" : "followup_call",
    title: lead.leadSource === "website_trial" ? "Schedule Trial Activation Session" : "Run Demo Qualification Call",
    description:
      lead.leadSource === "website_trial"
        ? "Confirm the industry template and data-import plan, then activate the trial smoothly."
        : "Validate pains and decision chain, then schedule a high-conversion product demo.",
    rationale: "Inbound lead converted to self-sales pipeline.",
    priorityScore: lead.leadSource === "website_trial" ? 88 : 78,
    priorityBand: lead.leadSource === "website_trial" ? "critical" : "high",
    dueAt: new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString(),
    sourceRefType: "inbound_lead",
    sourceRefId: lead.id,
    createdBy: params.actorUserId
  });

  await (params.supabase as any)
    .from("inbound_leads")
    .update({
      status: "converted_to_customer",
      converted_customer_id: customerId,
      converted_opportunity_id: opportunityId
    })
    .eq("org_id", params.orgId)
    .eq("id", lead.id);

  await appendConversionEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: lead.id,
    eventType: "conversion_signal",
    eventSummary: "Lead converted into internal customer/opportunity/deal pipeline.",
    eventPayload: {
      customer_id: customerId,
      opportunity_id: opportunityId,
      deal_room_id: roomResult.room.id
    }
  });

  return {
    converted: true,
    customerId,
    opportunityId,
    dealRoomId: roomResult.room.id
  };
}

export async function listConversionEvents(params: {
  supabase: DbClient;
  orgId: string;
  limit?: number;
  leadId?: string;
  targetOrgId?: string;
}): Promise<ConversionEvent[]> {
  let query = (params.supabase as any)
    .from("conversion_events")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 80);

  if (params.leadId) query = query.eq("lead_id", params.leadId);
  if (params.targetOrgId) query = query.eq("target_org_id", params.targetOrgId);

  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as ConversionEventRow[]).map(mapConversionEventRow);
}

export async function ensurePublicFormAllowed(params: {
  supabase: DbClient;
  orgId: string;
  email: string;
  leadSource: InboundLeadSource;
  fingerprint?: string | null;
  windowMinutesByEmailSource?: number;
  windowMinutesByFingerprint?: number;
}): Promise<void> {
  const emailSince = new Date(Date.now() - (params.windowMinutesByEmailSource ?? 10) * 60 * 1000).toISOString();
  const res = await (params.supabase as any)
    .from("inbound_leads")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("email", params.email.trim().toLowerCase())
    .eq("lead_source", params.leadSource)
    .gte("created_at", emailSince)
    .limit(1);
  if (res.error) throw new Error(res.error.message);
  if ((res.data ?? []).length > 0) {
    throw new Error("lead_submission_too_frequent");
  }

  if (params.fingerprint) {
    const fingerprintSince = new Date(Date.now() - (params.windowMinutesByFingerprint ?? 10) * 60 * 1000).toISOString();
    const fpRes = await (params.supabase as any)
      .from("inbound_leads")
      .select("id,payload_snapshot")
      .eq("org_id", params.orgId)
      .gte("created_at", fingerprintSince)
      .limit(200);
    if (fpRes.error) throw new Error(fpRes.error.message);

    const duplicated = ((fpRes.data ?? []) as Array<{ payload_snapshot: Record<string, unknown> | null }>).some((row) => {
      const snapshot = row.payload_snapshot ?? {};
      return typeof snapshot.request_fingerprint === "string" && snapshot.request_fingerprint === params.fingerprint;
    });

    if (duplicated) {
      throw new Error("lead_submission_too_frequent");
    }
  }
}

export function buildTrialActivationToken(): string {
  return randomUUID().replaceAll("-", "");
}

export function buildPublicSubmissionFingerprint(params: {
  email: string;
  source: InboundLeadSource | "website_contact";
  ip?: string | null;
  userAgent?: string | null;
}): string {
  const email = params.email.trim().toLowerCase();
  const ip = (params.ip ?? "").trim();
  const ua = (params.userAgent ?? "").trim().toLowerCase().slice(0, 240);
  const raw = `${params.source}|${email}|${ip}|${ua}`;
  return createHash("sha256").update(raw).digest("hex");
}


