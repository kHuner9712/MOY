import { isInterventionStatusTransitionAllowed } from "@/lib/intervention-request-flow";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { addSystemEventMessage, createCollaborationThread } from "@/services/collaboration-thread-service";
import { mapInterventionRequestRow } from "@/services/mappers";
import { createOrReuseWorkItemBySourceRef } from "@/services/work-item-service";
import type { Database } from "@/types/database";
import type { InterventionRequest } from "@/types/deal";

type DbClient = ServerSupabaseClient;
type InterventionRow = Database["public"]["Tables"]["intervention_requests"]["Row"];

interface ProfileLite {
  id: string;
  display_name: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

async function ensureInterventionThread(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  createdBy: string;
}): Promise<string> {
  const { data, error } = await params.supabase
    .from("collaboration_threads")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("deal_room_id", params.dealRoomId)
    .eq("thread_type", "manager_intervention")
    .eq("status", "open")
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (data?.id) return data.id;

  const thread = await createCollaborationThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId,
    threadType: "manager_intervention",
    title: "Manager intervention thread",
    createdBy: params.createdBy,
    summary: "Track intervention requests and support actions."
  });
  return thread.id;
}

export async function listInterventionRequests(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  statuses?: Database["public"]["Enums"]["intervention_request_status"][];
}): Promise<InterventionRequest[]> {
  let query = params.supabase
    .from("intervention_requests")
    .select(
      "*, requester:profiles!intervention_requests_requested_by_fkey(id, display_name), target:profiles!intervention_requests_target_user_id_fkey(id, display_name)"
    )
    .eq("org_id", params.orgId)
    .eq("deal_room_id", params.dealRoomId)
    .order("created_at", { ascending: false });
  if (params.statuses?.length) query = query.in("status", params.statuses);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: InterventionRow & { requester?: ProfileLite | null; target?: ProfileLite | null }) =>
    mapInterventionRequestRow(row)
  );
}

export async function createInterventionRequest(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  requestedBy: string;
  targetUserId?: string | null;
  requestType: Database["public"]["Enums"]["intervention_request_type"];
  priorityBand?: Database["public"]["Enums"]["intervention_priority_band"];
  requestSummary: string;
  contextSnapshot?: Record<string, unknown>;
  dueAt?: string | null;
}): Promise<InterventionRequest> {
  const payload: Database["public"]["Tables"]["intervention_requests"]["Insert"] = {
    org_id: params.orgId,
    deal_room_id: params.dealRoomId,
    requested_by: params.requestedBy,
    target_user_id: params.targetUserId ?? null,
    request_type: params.requestType,
    priority_band: params.priorityBand ?? "medium",
    status: "open",
    request_summary: params.requestSummary,
    context_snapshot: (params.contextSnapshot ?? {}) as unknown as Database["public"]["Tables"]["intervention_requests"]["Insert"]["context_snapshot"],
    due_at: params.dueAt ?? null
  };

  const { data, error } = await params.supabase
    .from("intervention_requests")
    .insert(payload)
    .select(
      "*, requester:profiles!intervention_requests_requested_by_fkey(id, display_name), target:profiles!intervention_requests_target_user_id_fkey(id, display_name)"
    )
    .single();
  if (error || !data) throw new Error(error?.message ?? "create_intervention_request_failed");

  const request = mapInterventionRequestRow(data as InterventionRow & { requester?: ProfileLite | null; target?: ProfileLite | null });

  const threadId = await ensureInterventionThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId,
    createdBy: params.requestedBy
  });
  await addSystemEventMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId,
    actorUserId: params.requestedBy,
    eventText: `Intervention request created: **${request.requestType}** (${request.priorityBand}).`,
    sourceRefType: "intervention_request",
    sourceRefId: request.id
  });

  return request;
}

export async function updateInterventionRequestStatus(params: {
  supabase: DbClient;
  orgId: string;
  interventionRequestId: string;
  status: Database["public"]["Enums"]["intervention_request_status"];
  actorUserId: string;
  note?: string | null;
}): Promise<InterventionRequest> {
  const currentRes = await params.supabase
    .from("intervention_requests")
    .select("status")
    .eq("org_id", params.orgId)
    .eq("id", params.interventionRequestId)
    .single();
  if (currentRes.error || !currentRes.data) {
    throw new Error(currentRes.error?.message ?? "intervention_request_not_found");
  }
  const currentStatus = currentRes.data.status as Database["public"]["Enums"]["intervention_request_status"];
  if (!isInterventionStatusTransitionAllowed(currentStatus, params.status)) {
    throw new Error(`invalid_intervention_status_transition: ${currentStatus} -> ${params.status}`);
  }

  const patch: Database["public"]["Tables"]["intervention_requests"]["Update"] = {
    status: params.status,
    updated_at: nowIso()
  };
  if (params.status === "completed") patch.completed_at = nowIso();
  if (params.status !== "completed") patch.completed_at = null;

  const { data, error } = await params.supabase
    .from("intervention_requests")
    .update(patch)
    .eq("org_id", params.orgId)
    .eq("id", params.interventionRequestId)
    .select(
      "*, requester:profiles!intervention_requests_requested_by_fkey(id, display_name), target:profiles!intervention_requests_target_user_id_fkey(id, display_name)"
    )
    .single();
  if (error || !data) throw new Error(error?.message ?? "update_intervention_status_failed");
  const request = mapInterventionRequestRow(data as InterventionRow & { requester?: ProfileLite | null; target?: ProfileLite | null });

  if (request.status === "accepted") {
    await createOrReuseWorkItemBySourceRef({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: request.targetUserId ?? request.requestedBy,
      customerId: null,
      opportunityId: null,
      sourceType: "manager_assigned",
      workType: "manager_checkin",
      title: `[Intervention] ${request.requestType}`,
      description: request.requestSummary,
      rationale: "Intervention request accepted and requires concrete support action.",
      priorityScore: request.priorityBand === "critical" ? 95 : request.priorityBand === "high" ? 86 : 72,
      priorityBand: request.priorityBand === "low" ? "medium" : request.priorityBand,
      dueAt: request.dueAt,
      sourceRefType: "intervention_request",
      sourceRefId: request.id,
      aiGenerated: false,
      createdBy: params.actorUserId
    });
  }

  const threadId = await ensureInterventionThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: request.dealRoomId,
    createdBy: params.actorUserId
  });
  await addSystemEventMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId,
    actorUserId: params.actorUserId,
    eventText: `Intervention request status changed to **${request.status}**.${params.note ? ` Note: ${params.note}` : ""}`,
    sourceRefType: "intervention_request",
    sourceRefId: request.id
  });

  return request;
}
