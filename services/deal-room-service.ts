import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapDealRoomRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { DealRoom, DealRoomStatus } from "@/types/deal";

type DbClient = ServerSupabaseClient;

interface ProfileLite {
  id: string;
  display_name: string;
}

interface CustomerLite {
  id: string;
  company_name: string;
}

interface OpportunityLite {
  id: string;
  title: string;
}

type DealRoomRow = Database["public"]["Tables"]["deal_rooms"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function listDealRooms(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  statuses?: DealRoomStatus[];
  priorityBands?: Database["public"]["Enums"]["deal_room_priority_band"][];
  managerAttentionNeeded?: boolean;
  limit?: number;
}): Promise<DealRoom[]> {
  let query = params.supabase
    .from("deal_rooms")
    .select(
      "*, owner:profiles!deal_rooms_owner_id_fkey(id, display_name), customer:customers!deal_rooms_customer_id_fkey(id, company_name), opportunity:opportunities!deal_rooms_opportunity_id_fkey(id, title)"
    )
    .eq("org_id", params.orgId)
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 120);

  if (params.ownerId) query = query.eq("owner_id", params.ownerId);
  if (params.statuses?.length) query = query.in("room_status", params.statuses);
  if (params.priorityBands?.length) query = query.in("priority_band", params.priorityBands);
  if (params.managerAttentionNeeded !== undefined) query = query.eq("manager_attention_needed", params.managerAttentionNeeded);

  const { data, error } = await query;
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as Array<DealRoomRow & { owner?: ProfileLite | null; customer?: CustomerLite | null; opportunity?: OpportunityLite | null }>;
  return rows.map((row) => mapDealRoomRow(row));
}

export async function getDealRoomById(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
}): Promise<DealRoom | null> {
  const { data, error } = await params.supabase
    .from("deal_rooms")
    .select(
      "*, owner:profiles!deal_rooms_owner_id_fkey(id, display_name), customer:customers!deal_rooms_customer_id_fkey(id, company_name), opportunity:opportunities!deal_rooms_opportunity_id_fkey(id, title)"
    )
    .eq("org_id", params.orgId)
    .eq("id", params.dealRoomId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;

  return mapDealRoomRow(data as DealRoomRow & { owner?: ProfileLite | null; customer?: CustomerLite | null; opportunity?: OpportunityLite | null });
}

export async function getDealRoomByCustomer(params: {
  supabase: DbClient;
  orgId: string;
  customerId: string;
}): Promise<DealRoom | null> {
  const { data, error } = await params.supabase
    .from("deal_rooms")
    .select(
      "*, owner:profiles!deal_rooms_owner_id_fkey(id, display_name), customer:customers!deal_rooms_customer_id_fkey(id, company_name), opportunity:opportunities!deal_rooms_opportunity_id_fkey(id, title)"
    )
    .eq("org_id", params.orgId)
    .eq("customer_id", params.customerId)
    .in("room_status", ["active", "watchlist", "escalated", "blocked"])
    .order("updated_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapDealRoomRow(data as DealRoomRow & { owner?: ProfileLite | null; customer?: CustomerLite | null; opportunity?: OpportunityLite | null });
}

export async function createDealRoom(params: {
  supabase: DbClient;
  orgId: string;
  ownerId: string;
  createdBy: string;
  customerId: string;
  opportunityId?: string | null;
  title?: string;
  roomStatus?: Database["public"]["Enums"]["deal_room_status"];
  priorityBand?: Database["public"]["Enums"]["deal_room_priority_band"];
  currentGoal?: string;
  currentBlockers?: string[];
  nextMilestone?: string | null;
  nextMilestoneDueAt?: string | null;
  managerAttentionNeeded?: boolean;
  sourceSnapshot?: Record<string, unknown>;
}): Promise<{ room: DealRoom; created: boolean }> {
  const existing = await getDealRoomByCustomer({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.customerId
  });
  if (existing) return { room: existing, created: false };

  const customerRes = await params.supabase
    .from("customers")
    .select("id, company_name, owner_id")
    .eq("org_id", params.orgId)
    .eq("id", params.customerId)
    .maybeSingle();
  if (customerRes.error) throw new Error(customerRes.error.message);
  if (!customerRes.data) throw new Error("customer_not_found");

  const title = params.title ?? `${customerRes.data.company_name} | Deal Room`;
  const payload: Database["public"]["Tables"]["deal_rooms"]["Insert"] = {
    org_id: params.orgId,
    customer_id: params.customerId,
    opportunity_id: params.opportunityId ?? null,
    owner_id: params.ownerId,
    room_status: params.roomStatus ?? "active",
    priority_band: params.priorityBand ?? "important",
    title,
    command_summary: "",
    current_goal: params.currentGoal ?? "Clarify next milestone and assign concrete owner action.",
    current_blockers: (params.currentBlockers ?? []) as unknown as Database["public"]["Tables"]["deal_rooms"]["Insert"]["current_blockers"],
    next_milestone: params.nextMilestone ?? null,
    next_milestone_due_at: params.nextMilestoneDueAt ?? null,
    manager_attention_needed: params.managerAttentionNeeded ?? false,
    source_snapshot: (params.sourceSnapshot ?? {}) as unknown as Database["public"]["Tables"]["deal_rooms"]["Insert"]["source_snapshot"],
    created_by: params.createdBy
  };

  const { data, error } = await params.supabase
    .from("deal_rooms")
    .insert(payload)
    .select(
      "*, owner:profiles!deal_rooms_owner_id_fkey(id, display_name), customer:customers!deal_rooms_customer_id_fkey(id, company_name), opportunity:opportunities!deal_rooms_opportunity_id_fkey(id, title)"
    )
    .single();
  if (error || !data) throw new Error(error?.message ?? "create_deal_room_failed");

  const room = mapDealRoomRow(data as DealRoomRow & { owner?: ProfileLite | null; customer?: CustomerLite | null; opportunity?: OpportunityLite | null });

  const participantsPayload: Database["public"]["Tables"]["deal_participants"]["Insert"][] = [
    {
      org_id: params.orgId,
      deal_room_id: room.id,
      user_id: params.ownerId,
      role_in_room: "owner",
      is_active: true
    }
  ];
  if (params.createdBy !== params.ownerId) {
    participantsPayload.push({
      org_id: params.orgId,
      deal_room_id: room.id,
      user_id: params.createdBy,
      role_in_room: "manager",
      is_active: true
    });
  }

  await params.supabase.from("deal_participants").upsert(participantsPayload, { onConflict: "deal_room_id,user_id" });

  const defaultCheckpoints: Database["public"]["Tables"]["deal_checkpoints"]["Insert"][] = [
    { org_id: params.orgId, deal_room_id: room.id, checkpoint_type: "need_confirmed", title: "Need confirmed", description: "Confirm critical business need", owner_id: params.ownerId },
    { org_id: params.orgId, deal_room_id: room.id, checkpoint_type: "decision_maker_confirmed", title: "Decision maker confirmed", description: "Confirm who signs off", owner_id: params.ownerId },
    { org_id: params.orgId, deal_room_id: room.id, checkpoint_type: "quote_sent", title: "Quote sent", description: "Deliver quote/proposal and collect reaction", owner_id: params.ownerId },
    { org_id: params.orgId, deal_room_id: room.id, checkpoint_type: "closing", title: "Closing", description: "Move to final commitment", owner_id: params.ownerId }
  ];
  await params.supabase.from("deal_checkpoints").upsert(defaultCheckpoints, { onConflict: "deal_room_id,checkpoint_type" });

  const threadPayload: Database["public"]["Tables"]["collaboration_threads"]["Insert"] = {
    org_id: params.orgId,
    deal_room_id: room.id,
    thread_type: "strategy",
    title: "Initial strategy alignment",
    status: "open",
    summary: "Deal room created.",
    created_by: params.createdBy
  };
  await params.supabase.from("collaboration_threads").insert(threadPayload);

  return { room, created: true };
}

export async function updateDealRoomCommand(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  commandSummary?: string;
  currentGoal?: string;
  currentBlockers?: string[];
  nextMilestone?: string | null;
  nextMilestoneDueAt?: string | null;
  managerAttentionNeeded?: boolean;
  sourceSnapshot?: Record<string, unknown>;
}): Promise<DealRoom> {
  const patch: Database["public"]["Tables"]["deal_rooms"]["Update"] = {
    updated_at: nowIso()
  };
  if (params.commandSummary !== undefined) patch.command_summary = params.commandSummary;
  if (params.currentGoal !== undefined) patch.current_goal = params.currentGoal;
  if (params.currentBlockers !== undefined) patch.current_blockers = params.currentBlockers as unknown as Database["public"]["Tables"]["deal_rooms"]["Update"]["current_blockers"];
  if (params.nextMilestone !== undefined) patch.next_milestone = params.nextMilestone;
  if (params.nextMilestoneDueAt !== undefined) patch.next_milestone_due_at = params.nextMilestoneDueAt;
  if (params.managerAttentionNeeded !== undefined) patch.manager_attention_needed = params.managerAttentionNeeded;
  if (params.sourceSnapshot !== undefined) patch.source_snapshot = params.sourceSnapshot as unknown as Database["public"]["Tables"]["deal_rooms"]["Update"]["source_snapshot"];

  const { data, error } = await params.supabase
    .from("deal_rooms")
    .update(patch)
    .eq("org_id", params.orgId)
    .eq("id", params.dealRoomId)
    .select(
      "*, owner:profiles!deal_rooms_owner_id_fkey(id, display_name), customer:customers!deal_rooms_customer_id_fkey(id, company_name), opportunity:opportunities!deal_rooms_opportunity_id_fkey(id, title)"
    )
    .single();
  if (error || !data) throw new Error(error?.message ?? "update_deal_room_failed");
  return mapDealRoomRow(data as DealRoomRow & { owner?: ProfileLite | null; customer?: CustomerLite | null; opportunity?: OpportunityLite | null });
}

