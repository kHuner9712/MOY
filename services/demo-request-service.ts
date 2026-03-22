import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { readPublicCommercialEntryTrace } from "@/lib/commercial-entry";
import { getInboundLeadById, appendConversionEvent } from "@/services/inbound-lead-service";
import { createWorkItem } from "@/services/work-item-service";
import type { DemoOutcomeStatus, DemoRequest, DemoRequestStatus, InboundLeadStatus } from "@/types/commercialization";

type DbClient = ServerSupabaseClient;

interface DemoRequestRow {
  id: string;
  org_id: string;
  lead_id: string;
  requested_by_email: string;
  requested_at: string;
  preferred_time_text: string | null;
  demo_status: DemoRequestStatus;
  scheduled_event_id: string | null;
  owner_id: string | null;
  demo_summary: string | null;
  outcome_status: DemoOutcomeStatus | null;
  created_at: string;
  updated_at: string;
}

function mapDemoRequestRow(row: DemoRequestRow): DemoRequest {
  return {
    id: row.id,
    orgId: row.org_id,
    leadId: row.lead_id,
    requestedByEmail: row.requested_by_email,
    requestedAt: row.requested_at,
    preferredTimeText: row.preferred_time_text,
    demoStatus: row.demo_status,
    scheduledEventId: row.scheduled_event_id,
    ownerId: row.owner_id,
    demoSummary: row.demo_summary,
    outcomeStatus: row.outcome_status,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function readString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

export async function listDemoRequests(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  statuses?: DemoRequestStatus[];
  limit?: number;
}): Promise<DemoRequest[]> {
  let query = (params.supabase as any).from("demo_requests").select("*").eq("org_id", params.orgId).order("requested_at", { ascending: false }).limit(params.limit ?? 80);
  if (params.ownerId) query = query.eq("owner_id", params.ownerId);
  if (params.statuses?.length) query = query.in("demo_status", params.statuses);
  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as DemoRequestRow[]).map(mapDemoRequestRow);
}

export async function getDemoRequestById(params: {
  supabase: DbClient;
  orgId: string;
  demoRequestId: string;
}): Promise<DemoRequest | null> {
  const res = await (params.supabase as any)
    .from("demo_requests")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("id", params.demoRequestId)
    .maybeSingle();
  if (res.error) throw new Error(res.error.message);
  if (!res.data) return null;
  return mapDemoRequestRow(res.data as DemoRequestRow);
}

export async function createDemoRequest(params: {
  supabase: DbClient;
  orgId: string;
  leadId: string;
  requestedByEmail: string;
  preferredTimeText?: string | null;
  actorUserId: string | null;
  createWorkItem?: boolean;
}): Promise<{
  demoRequest: DemoRequest;
  workItemCreated: boolean;
}> {
  const lead = await getInboundLeadById({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: params.leadId
  });
  if (!lead) throw new Error("inbound_lead_not_found");
  const leadSnapshot = asRecord(lead.payloadSnapshot);
  const entryTrace = readPublicCommercialEntryTrace(leadSnapshot);
  const scenarioFocus = readString(leadSnapshot, "scenario_focus");
  const preferredTime = params.preferredTimeText ?? readString(leadSnapshot, "preferred_time_text");

  const insertRes = await (params.supabase as any)
    .from("demo_requests")
    .insert({
      org_id: params.orgId,
      lead_id: lead.id,
      requested_by_email: params.requestedByEmail.trim().toLowerCase(),
      requested_at: new Date().toISOString(),
      preferred_time_text: params.preferredTimeText ?? null,
      demo_status: "pending",
      owner_id: lead.assignedOwnerId
    })
    .select("*")
    .single();
  if (insertRes.error) throw new Error(insertRes.error.message);

  if (lead.status === "new" || lead.status === "qualified") {
    const nextLeadStatus: InboundLeadStatus = "demo_scheduled";
    await (params.supabase as any)
      .from("inbound_leads")
      .update({ status: nextLeadStatus })
      .eq("org_id", params.orgId)
      .eq("id", lead.id);
  }

  await appendConversionEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: lead.id,
    eventType: "demo_requested",
    eventSummary: "A demo request was created from inbound lead.",
    eventPayload: {
      preferred_time_text: preferredTime ?? null,
      scenario_focus: scenarioFocus,
      entry_trace_id: entryTrace?.traceId ?? null,
      entry_landing_page: entryTrace?.landingPage ?? null,
      source_campaign: entryTrace?.sourceCampaign ?? null,
      lead_source: lead.leadSource,
      assigned_owner_id: lead.assignedOwnerId
    }
  });

  let workItemCreated = false;
  if (params.createWorkItem !== false && lead.assignedOwnerId) {
    const scenarioNote = scenarioFocus ? `Scenario focus: ${scenarioFocus}.` : null;
    const preferredTimeNote = preferredTime ? `Preferred time: ${preferredTime}.` : null;
    const detailNotes = [scenarioNote, preferredTimeNote].filter((item): item is string => Boolean(item));
    await createWorkItem({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: lead.assignedOwnerId,
      sourceType: "manual",
      workType: "schedule_demo",
      title: `Schedule demo for ${lead.companyName}`,
      description: ["Confirm agenda and schedule a product demo call with this inbound lead.", ...detailNotes].join(" "),
      rationale: `Public demo request submitted${entryTrace?.traceId ? ` (trace: ${entryTrace.traceId})` : ""}.`,
      priorityScore: 82,
      priorityBand: "high",
      dueAt: new Date(Date.now() + 48 * 60 * 60 * 1000).toISOString(),
      sourceRefType: "demo_request",
      sourceRefId: String((insertRes.data as DemoRequestRow).id),
      createdBy: params.actorUserId ?? lead.assignedOwnerId
    });
    workItemCreated = true;
  }

  return {
    demoRequest: mapDemoRequestRow(insertRes.data as DemoRequestRow),
    workItemCreated
  };
}

export async function updateDemoRequest(params: {
  supabase: DbClient;
  orgId: string;
  demoRequestId: string;
  actorUserId: string;
  patch: Partial<{
    demoStatus: DemoRequestStatus;
    ownerId: string | null;
    scheduledEventId: string | null;
    demoSummary: string | null;
    outcomeStatus: DemoOutcomeStatus | null;
  }>;
}): Promise<DemoRequest> {
  const patch: Record<string, unknown> = {};
  if (params.patch.demoStatus !== undefined) patch.demo_status = params.patch.demoStatus;
  if (params.patch.ownerId !== undefined) patch.owner_id = params.patch.ownerId;
  if (params.patch.scheduledEventId !== undefined) patch.scheduled_event_id = params.patch.scheduledEventId;
  if (params.patch.demoSummary !== undefined) patch.demo_summary = params.patch.demoSummary;
  if (params.patch.outcomeStatus !== undefined) patch.outcome_status = params.patch.outcomeStatus;

  const res = await (params.supabase as any)
    .from("demo_requests")
    .update(patch)
    .eq("org_id", params.orgId)
    .eq("id", params.demoRequestId)
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);

  const updated = mapDemoRequestRow(res.data as DemoRequestRow);
  if (updated.demoStatus === "completed") {
    await appendConversionEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      leadId: updated.leadId,
      eventType: "demo_completed",
      eventSummary: "Demo has been marked completed.",
      eventPayload: {
        outcome_status: updated.outcomeStatus,
        owner_id: updated.ownerId
      }
    });
  } else if (updated.demoStatus === "scheduled") {
    await appendConversionEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      leadId: updated.leadId,
      eventType: "demo_scheduled",
      eventSummary: "Demo has been scheduled.",
      eventPayload: {
        owner_id: updated.ownerId
      }
    });
  }

  return updated;
}
