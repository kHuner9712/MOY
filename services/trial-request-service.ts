import { createSupabaseAdminClient, hasSupabaseAdminEnv } from "@/lib/supabase/admin";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { appendConversionEvent, buildTrialActivationToken, getInboundLeadById } from "@/services/inbound-lead-service";
import { createWorkItem } from "@/services/work-item-service";
import { upsertTrialConversionTrack } from "@/services/trial-conversion-service";
import { applyTemplate } from "@/services/template-application-service";
import type { TrialRequest, TrialRequestStatus } from "@/types/commercialization";

type DbClient = ServerSupabaseClient;

interface TrialRequestRow {
  id: string;
  org_id: string;
  lead_id: string;
  requested_by_email: string;
  requested_at: string;
  requested_template_id: string | null;
  request_status: TrialRequestStatus;
  target_org_id: string | null;
  activation_token: string | null;
  activation_started_at: string | null;
  activated_at: string | null;
  created_at: string;
  updated_at: string;
}

function mapTrialRequestRow(row: TrialRequestRow): TrialRequest {
  return {
    id: row.id,
    orgId: row.org_id,
    leadId: row.lead_id,
    requestedByEmail: row.requested_by_email,
    requestedAt: row.requested_at,
    requestedTemplateId: row.requested_template_id,
    requestStatus: row.request_status,
    targetOrgId: row.target_org_id,
    activationToken: row.activation_token,
    activationStartedAt: row.activation_started_at,
    activatedAt: row.activated_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function slugify(input: string): string {
  const normalized = input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return normalized.length > 0 ? normalized : "moy-trial";
}

async function ensureUniqueOrgSlug(params: {
  supabase: DbClient;
  baseSlug: string;
}): Promise<string> {
  let slug = params.baseSlug.slice(0, 48);
  let step = 0;
  while (step < 8) {
    const res = await (params.supabase as any).from("organizations").select("id").eq("slug", slug).maybeSingle();
    if (res.error) throw new Error(res.error.message);
    if (!res.data) return slug;
    step += 1;
    slug = `${params.baseSlug.slice(0, 42)}-${Math.floor(Math.random() * 9000 + 1000)}`;
  }
  return `${params.baseSlug.slice(0, 40)}-${Date.now().toString().slice(-5)}`;
}

export async function listTrialRequests(params: {
  supabase: DbClient;
  orgId: string;
  statuses?: TrialRequestStatus[];
  limit?: number;
}): Promise<TrialRequest[]> {
  let query = (params.supabase as any).from("trial_requests").select("*").eq("org_id", params.orgId).order("requested_at", { ascending: false }).limit(params.limit ?? 80);
  if (params.statuses?.length) query = query.in("request_status", params.statuses);
  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as TrialRequestRow[]).map(mapTrialRequestRow);
}

export async function getTrialRequestById(params: {
  supabase: DbClient;
  orgId: string;
  trialRequestId: string;
}): Promise<TrialRequest | null> {
  const res = await (params.supabase as any)
    .from("trial_requests")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("id", params.trialRequestId)
    .maybeSingle();
  if (res.error) throw new Error(res.error.message);
  if (!res.data) return null;
  return mapTrialRequestRow(res.data as TrialRequestRow);
}

export async function createTrialRequest(params: {
  supabase: DbClient;
  orgId: string;
  leadId: string;
  requestedByEmail: string;
  requestedTemplateId?: string | null;
  actorUserId: string | null;
  createWorkItem?: boolean;
}): Promise<{
  trialRequest: TrialRequest;
  workItemCreated: boolean;
}> {
  const lead = await getInboundLeadById({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: params.leadId
  });
  if (!lead) throw new Error("inbound_lead_not_found");

  const insertRes = await (params.supabase as any)
    .from("trial_requests")
    .insert({
      org_id: params.orgId,
      lead_id: lead.id,
      requested_by_email: params.requestedByEmail.trim().toLowerCase(),
      requested_at: new Date().toISOString(),
      requested_template_id: params.requestedTemplateId ?? null,
      request_status: "pending"
    })
    .select("*")
    .single();
  if (insertRes.error) throw new Error(insertRes.error.message);

  await (params.supabase as any)
    .from("inbound_leads")
    .update({ status: "trial_started" })
    .eq("org_id", params.orgId)
    .eq("id", lead.id);

  await appendConversionEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: lead.id,
    eventType: "trial_requested",
    eventSummary: "Trial request submitted.",
    eventPayload: {
      requested_template_id: params.requestedTemplateId ?? null
    }
  });

  let workItemCreated = false;
  if (params.createWorkItem !== false && lead.assignedOwnerId) {
    await createWorkItem({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: lead.assignedOwnerId,
      sourceType: "manual",
      workType: "review_customer",
      title: `Review trial activation for ${lead.companyName}`,
      description: "Qualify trial scope, choose industry template, and activate trial org.",
      rationale: "Inbound lead requested trial.",
      priorityScore: 90,
      priorityBand: "critical",
      dueAt: new Date(Date.now() + 36 * 60 * 60 * 1000).toISOString(),
      sourceRefType: "trial_request",
      sourceRefId: String((insertRes.data as TrialRequestRow).id),
      createdBy: params.actorUserId ?? lead.assignedOwnerId
    });
    workItemCreated = true;
  }

  return {
    trialRequest: mapTrialRequestRow(insertRes.data as TrialRequestRow),
    workItemCreated
  };
}

export async function activateTrialRequest(params: {
  supabase: DbClient;
  orgId: string;
  trialRequestId: string;
  actorUserId: string;
  requestedTemplateId?: string | null;
  trialOrgName?: string | null;
}): Promise<{
  trialRequest: TrialRequest;
  targetOrgId: string;
  conversionTrackId: string;
}> {
  const trialRequest = await getTrialRequestById({
    supabase: params.supabase,
    orgId: params.orgId,
    trialRequestId: params.trialRequestId
  });
  if (!trialRequest) throw new Error("trial_request_not_found");

  const lead = await getInboundLeadById({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: trialRequest.leadId
  });
  if (!lead) throw new Error("inbound_lead_not_found");

  const admin = hasSupabaseAdminEnv() ? (createSupabaseAdminClient() as unknown as DbClient) : params.supabase;
  let targetOrgId = trialRequest.targetOrgId;
  const activationToken = buildTrialActivationToken();

  if (!targetOrgId) {
    const baseName = params.trialOrgName?.trim() || `${lead.companyName} Trial`;
    const slug = await ensureUniqueOrgSlug({
      supabase: admin,
      baseSlug: slugify(baseName)
    });

    const orgRes = await (admin as any)
      .from("organizations")
      .insert({
        name: baseName,
        slug
      })
      .select("id")
      .single();
    if (orgRes.error) throw new Error(orgRes.error.message);
    targetOrgId = String(orgRes.data.id);

    await (admin as any).from("org_plan_profiles").insert({
      org_id: targetOrgId,
      plan_tier: "trial",
      seat_limit: 20,
      ai_run_limit_monthly: 1500,
      document_limit_monthly: 500,
      touchpoint_limit_monthly: 3000,
      advanced_features_enabled: true,
      status: "active",
      expires_at: new Date(Date.now() + 15 * 24 * 60 * 60 * 1000).toISOString()
    });

    await (admin as any).from("org_settings").insert({
      org_id: targetOrgId,
      org_display_name: baseName,
      brand_name: "MOY",
      industry_hint: lead.industryHint,
      timezone: "Asia/Shanghai",
      locale: "zh-CN",
      onboarding_step_state: {
        org_profile: false,
        ai_setup: false,
        team_invite: false,
        first_data: false,
        first_plan_or_brief: false,
        first_deal_room: false,
        manager_view: false,
        industry_template: false
      }
    });

    await (admin as any).from("onboarding_runs").insert({
      org_id: targetOrgId,
      initiated_by: params.actorUserId,
      run_type: "trial_bootstrap",
      status: "completed",
      summary: "Trial bootstrap initialized from commercialization request.",
      detail_snapshot: {
        source: "trial_activation",
        lead_id: lead.id
      }
    });

    const templateCandidate =
      params.requestedTemplateId ??
      trialRequest.requestedTemplateId ??
      (typeof lead.payloadSnapshot.preferred_template_key === "string" ? lead.payloadSnapshot.preferred_template_key : null);
    if (templateCandidate) {
      await applyTemplate({
        supabase: admin,
        orgId: targetOrgId,
        actorUserId: params.actorUserId,
        templateIdOrKey: templateCandidate,
        applyMode: "trial_bootstrap",
        applyStrategy: "merge_prefer_existing",
        generateDemoSeed: false
      }).catch(() => null);
    }
  }

  const updateRes = await (params.supabase as any)
    .from("trial_requests")
    .update({
      request_status: "activated",
      target_org_id: targetOrgId,
      activation_token: activationToken,
      activation_started_at: trialRequest.activationStartedAt ?? new Date().toISOString(),
      activated_at: new Date().toISOString()
    })
    .eq("org_id", params.orgId)
    .eq("id", trialRequest.id)
    .select("*")
    .single();
  if (updateRes.error) throw new Error(updateRes.error.message);

  await appendConversionEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: lead.id,
    targetOrgId,
    eventType: "trial_approved",
    eventSummary: "Trial request approved.",
    eventPayload: {
      trial_request_id: trialRequest.id
    }
  });

  await appendConversionEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: lead.id,
    targetOrgId,
    eventType: "trial_activated",
    eventSummary: "Trial org activated.",
    eventPayload: {
      trial_request_id: trialRequest.id,
      target_org_id: targetOrgId
    }
  });

  const track = await upsertTrialConversionTrack({
    supabase: params.supabase,
    orgId: params.orgId,
    targetOrgId,
    leadId: lead.id,
    ownerId: lead.assignedOwnerId ?? params.actorUserId
  });

  if (lead.assignedOwnerId) {
    await createWorkItem({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: lead.assignedOwnerId,
      customerId: lead.convertedCustomerId,
      opportunityId: lead.convertedOpportunityId,
      sourceType: "manual",
      workType: "manager_checkin",
      title: `Track trial activation for ${lead.companyName}`,
      description: "Ensure onboarding kickoff and first value actions are completed in the trial org.",
      rationale: "Trial organization activated.",
      priorityScore: 84,
      priorityBand: "high",
      dueAt: new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString(),
      sourceRefType: "trial_conversion_track",
      sourceRefId: track.id,
      createdBy: params.actorUserId
    });
  }

  return {
    trialRequest: mapTrialRequestRow(updateRes.data as TrialRequestRow),
    targetOrgId,
    conversionTrackId: track.id
  };
}

export async function updateTrialRequestStatus(params: {
  supabase: DbClient;
  orgId: string;
  trialRequestId: string;
  status: TrialRequestStatus;
}): Promise<TrialRequest> {
  const res = await (params.supabase as any)
    .from("trial_requests")
    .update({
      request_status: params.status
    })
    .eq("org_id", params.orgId)
    .eq("id", params.trialRequestId)
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapTrialRequestRow(res.data as TrialRequestRow);
}
