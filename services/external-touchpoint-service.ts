import { evaluateNoRecentTouchpoint, evaluateWaitingReplyNeed } from "@/lib/external-touchpoint-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapCalendarEventRow, mapDocumentAssetRow, mapEmailThreadRow, mapExternalTouchpointEventRow } from "@/services/mappers";
import { createWorkItem } from "@/services/work-item-service";
import type { Database } from "@/types/database";
import type { CalendarEvent, DocumentAsset, EmailThread, ExternalTouchpointEvent, TouchpointHubView } from "@/types/touchpoint";

type DbClient = ServerSupabaseClient;

interface ProfileLite {
  id: string;
  display_name: string;
}

interface CustomerLite {
  id: string;
  company_name: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function plusDays(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString();
}

export async function recordExternalTouchpointEvent(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
  touchpointType: Database["public"]["Enums"]["touchpoint_type"];
  eventType: Database["public"]["Enums"]["touchpoint_event_type"];
  relatedRefType?: string | null;
  relatedRefId?: string | null;
  eventSummary: string;
  eventPayload?: Record<string, unknown>;
}): Promise<ExternalTouchpointEvent> {
  const payload: Database["public"]["Tables"]["external_touchpoint_events"]["Insert"] = {
    org_id: params.orgId,
    owner_id: params.ownerId ?? null,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    deal_room_id: params.dealRoomId ?? null,
    touchpoint_type: params.touchpointType,
    event_type: params.eventType,
    related_ref_type: params.relatedRefType ?? null,
    related_ref_id: params.relatedRefId ?? null,
    event_summary: params.eventSummary,
    event_payload: (params.eventPayload ?? {}) as Database["public"]["Tables"]["external_touchpoint_events"]["Insert"]["event_payload"]
  };

  const { data, error } = await params.supabase
    .from("external_touchpoint_events")
    .insert(payload)
    .select("*, owner:profiles!external_touchpoint_events_owner_id_fkey(id, display_name), customer:customers!external_touchpoint_events_customer_id_fkey(id, company_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "record_touchpoint_event_failed");
  return mapExternalTouchpointEventRow(
    data as Database["public"]["Tables"]["external_touchpoint_events"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }
  );
}

export async function listExternalTouchpointEvents(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  dealRoomId?: string | null;
  touchpointType?: Database["public"]["Enums"]["touchpoint_type"];
  limit?: number;
}): Promise<ExternalTouchpointEvent[]> {
  let query = params.supabase
    .from("external_touchpoint_events")
    .select("*, owner:profiles!external_touchpoint_events_owner_id_fkey(id, display_name), customer:customers!external_touchpoint_events_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 80);
  if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
  if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
  if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
  if (params.touchpointType) query = query.eq("touchpoint_type", params.touchpointType);
  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: Database["public"]["Tables"]["external_touchpoint_events"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }) =>
    mapExternalTouchpointEventRow(row)
  );
}

export async function getTouchpointHubView(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  dealRoomId?: string | null;
  limit?: number;
}): Promise<TouchpointHubView> {
  const limit = params.limit ?? 40;
  const [threadsRes, meetingsRes, docsRes, events] = await Promise.all([
    (async () => {
      let query = params.supabase
        .from("email_threads")
        .select("*, owner:profiles!email_threads_owner_id_fkey(id, display_name), customer:customers!email_threads_customer_id_fkey(id, company_name)")
        .eq("org_id", params.orgId)
        .order("latest_message_at", { ascending: false, nullsFirst: false })
        .order("updated_at", { ascending: false })
        .limit(limit);
      if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
      if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
      if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
      const { data, error } = await query;
      if (error) throw new Error(error.message);
      return (data ?? []).map((row: Database["public"]["Tables"]["email_threads"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }) =>
        mapEmailThreadRow(row)
      );
    })(),
    (async () => {
      let query = params.supabase
        .from("calendar_events")
        .select("*, owner:profiles!calendar_events_owner_id_fkey(id, display_name), customer:customers!calendar_events_customer_id_fkey(id, company_name)")
        .eq("org_id", params.orgId)
        .order("start_at", { ascending: true })
        .limit(limit);
      if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
      if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
      if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
      const { data, error } = await query;
      if (error) throw new Error(error.message);
      return (data ?? []).map((row: Database["public"]["Tables"]["calendar_events"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }) =>
        mapCalendarEventRow(row)
      );
    })(),
    (async () => {
      let query = params.supabase
        .from("document_assets")
        .select("*, owner:profiles!document_assets_owner_id_fkey(id, display_name), customer:customers!document_assets_customer_id_fkey(id, company_name)")
        .eq("org_id", params.orgId)
        .order("updated_at", { ascending: false })
        .limit(limit);
      if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
      if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
      if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
      const { data, error } = await query;
      if (error) throw new Error(error.message);
      return (data ?? []).map((row: Database["public"]["Tables"]["document_assets"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }) =>
        mapDocumentAssetRow(row)
      );
    })(),
    listExternalTouchpointEvents({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      customerId: params.customerId,
      dealRoomId: params.dealRoomId,
      limit
    })
  ]);

  return {
    emailThreads: threadsRes as EmailThread[],
    calendarEvents: meetingsRes as CalendarEvent[],
    documentAssets: docsRes as DocumentAsset[],
    events
  };
}

async function ensureWorkItemNotExists(params: {
  supabase: DbClient;
  orgId: string;
  sourceRefType: string;
  sourceRefId: string;
}): Promise<boolean> {
  const { data, error } = await params.supabase
    .from("work_items")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("source_ref_type", params.sourceRefType)
    .eq("source_ref_id", params.sourceRefId)
    .in("status", ["todo", "in_progress", "snoozed"])
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(error.message);
  return !data;
}

export async function runTouchpointRules(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
}): Promise<{ waitingReplyTasks: number; noRecentTouchpointTasks: number }> {
  let waitingReplyTasks = 0;
  let noRecentTouchpointTasks = 0;

  const { data: waitingThreads, error: waitingError } = await params.supabase
    .from("email_threads")
    .select("id, owner_id, customer_id, opportunity_id, deal_room_id, subject, thread_status, latest_message_at")
    .eq("org_id", params.orgId)
    .eq("thread_status", "waiting_reply");
  if (waitingError) throw new Error(waitingError.message);

  for (const thread of waitingThreads ?? []) {
    const shouldCreate = evaluateWaitingReplyNeed({
      threadStatus: thread.thread_status,
      latestMessageAt: thread.latest_message_at,
      thresholdHours: 48
    });
    if (!shouldCreate) continue;
    const canCreate = await ensureWorkItemNotExists({
      supabase: params.supabase,
      orgId: params.orgId,
      sourceRefType: "email_thread_waiting_reply",
      sourceRefId: thread.id
    });
    if (!canCreate) continue;

    await createWorkItem({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: thread.owner_id,
      customerId: thread.customer_id,
      opportunityId: thread.opportunity_id,
      sourceType: "manual",
      workType: "followup_call",
      title: `[Email] Waiting reply: ${thread.subject}`,
      description: "Customer reply is pending. Follow up externally to keep deal momentum.",
      rationale: "No reply within expected response window.",
      priorityScore: 72,
      priorityBand: "high",
      dueAt: plusDays(1),
      sourceRefType: "email_thread_waiting_reply",
      sourceRefId: thread.id,
      createdBy: params.actorUserId
    });
    waitingReplyTasks += 1;
  }

  const { data: dealRooms, error: dealError } = await params.supabase
    .from("deal_rooms")
    .select("id, owner_id, customer_id, opportunity_id, title, priority_band, room_status")
    .eq("org_id", params.orgId)
    .in("priority_band", ["strategic", "critical"])
    .in("room_status", ["active", "watchlist", "escalated", "blocked"]);
  if (dealError) throw new Error(dealError.message);

  for (const room of dealRooms ?? []) {
    const { data: latestEvent, error: eventError } = await params.supabase
      .from("external_touchpoint_events")
      .select("created_at")
      .eq("org_id", params.orgId)
      .eq("deal_room_id", room.id)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (eventError) throw new Error(eventError.message);

    const shouldCreate = evaluateNoRecentTouchpoint({
      latestTouchpointAt: latestEvent?.created_at ?? null,
      dealPriorityBand: room.priority_band,
      thresholdDays: 5
    });
    if (!shouldCreate) continue;

    const canCreate = await ensureWorkItemNotExists({
      supabase: params.supabase,
      orgId: params.orgId,
      sourceRefType: "deal_no_recent_touchpoint",
      sourceRefId: room.id
    });
    if (!canCreate) continue;

    await createWorkItem({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: room.owner_id,
      customerId: room.customer_id,
      opportunityId: room.opportunity_id,
      sourceType: "manual",
      workType: "review_customer",
      title: `[External] No recent touchpoint: ${room.title}`,
      description: "High-priority deal lacks recent external touchpoint. Create a concrete outbound action.",
      rationale: "External progress signal is weak for a high-priority deal.",
      priorityScore: room.priority_band === "critical" ? 90 : 82,
      priorityBand: room.priority_band === "critical" ? "critical" : "high",
      dueAt: plusDays(1),
      sourceRefType: "deal_no_recent_touchpoint",
      sourceRefId: room.id,
      createdBy: params.actorUserId
    });
    noRecentTouchpointTasks += 1;
  }

  return { waitingReplyTasks, noRecentTouchpointTasks };
}

export async function touchpointEventSummary(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  sinceDays?: number;
}): Promise<{
  totalEvents: number;
  waitingReplyThreads: number;
  upcomingMeetings: number;
  documentUpdates: number;
}> {
  const sinceDays = params.sinceDays ?? 7;
  const sinceAt = new Date(Date.now() - sinceDays * 24 * 60 * 60 * 1000).toISOString();

  let eventsQuery = params.supabase.from("external_touchpoint_events").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).gte("created_at", sinceAt);
  let waitingQuery = params.supabase
    .from("email_threads")
    .select("id", { count: "exact", head: true })
    .eq("org_id", params.orgId)
    .eq("thread_status", "waiting_reply");
  let meetingsQuery = params.supabase
    .from("calendar_events")
    .select("id", { count: "exact", head: true })
    .eq("org_id", params.orgId)
    .eq("meeting_status", "scheduled")
    .gte("start_at", nowIso());
  let docsQuery = params.supabase.from("document_assets").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).gte("updated_at", sinceAt);

  if (params.ownerId !== undefined) {
    eventsQuery = eventsQuery.eq("owner_id", params.ownerId);
    waitingQuery = waitingQuery.eq("owner_id", params.ownerId);
    meetingsQuery = meetingsQuery.eq("owner_id", params.ownerId);
    docsQuery = docsQuery.eq("owner_id", params.ownerId);
  }

  const [eventsRes, waitingRes, meetingsRes, docsRes] = await Promise.all([eventsQuery, waitingQuery, meetingsQuery, docsQuery]);
  if (eventsRes.error) throw new Error(eventsRes.error.message);
  if (waitingRes.error) throw new Error(waitingRes.error.message);
  if (meetingsRes.error) throw new Error(meetingsRes.error.message);
  if (docsRes.error) throw new Error(docsRes.error.message);

  return {
    totalEvents: eventsRes.count ?? 0,
    waitingReplyThreads: waitingRes.count ?? 0,
    upcomingMeetings: meetingsRes.count ?? 0,
    documentUpdates: docsRes.count ?? 0
  };
}

export async function linkTouchpointToDeal(params: {
  supabase: DbClient;
  orgId: string;
  targetType: "email_thread" | "calendar_event" | "document_asset";
  targetId: string;
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
  actorUserId: string;
}): Promise<void> {
  const patch = {
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    deal_room_id: params.dealRoomId ?? null,
    updated_at: nowIso()
  };

  if (params.targetType === "email_thread") {
    const { error } = await params.supabase.from("email_threads").update(patch).eq("org_id", params.orgId).eq("id", params.targetId);
    if (error) throw new Error(error.message);
    await recordExternalTouchpointEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.actorUserId,
      customerId: params.customerId ?? null,
      opportunityId: params.opportunityId ?? null,
      dealRoomId: params.dealRoomId ?? null,
      touchpointType: "email",
      eventType: "document_reviewed",
      relatedRefType: "email_thread",
      relatedRefId: params.targetId,
      eventSummary: "Email thread linked to deal context",
      eventPayload: {
        target_type: params.targetType
      }
    });
    return;
  }

  if (params.targetType === "calendar_event") {
    const { error } = await params.supabase.from("calendar_events").update(patch).eq("org_id", params.orgId).eq("id", params.targetId);
    if (error) throw new Error(error.message);
    await recordExternalTouchpointEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.actorUserId,
      customerId: params.customerId ?? null,
      opportunityId: params.opportunityId ?? null,
      dealRoomId: params.dealRoomId ?? null,
      touchpointType: "meeting",
      eventType: "document_reviewed",
      relatedRefType: "calendar_event",
      relatedRefId: params.targetId,
      eventSummary: "Calendar event linked to deal context",
      eventPayload: {
        target_type: params.targetType
      }
    });
    return;
  }

  const { error } = await params.supabase.from("document_assets").update(patch).eq("org_id", params.orgId).eq("id", params.targetId);
  if (error) throw new Error(error.message);
  await recordExternalTouchpointEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId: params.actorUserId,
    customerId: params.customerId ?? null,
    opportunityId: params.opportunityId ?? null,
    dealRoomId: params.dealRoomId ?? null,
    touchpointType: "document",
    eventType: "document_reviewed",
    relatedRefType: "document_asset",
    relatedRefId: params.targetId,
    eventSummary: "Document asset linked to deal context",
    eventPayload: {
      target_type: params.targetType
    }
  });
}
