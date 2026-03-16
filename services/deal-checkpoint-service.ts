import { buildBlockedCheckpointLinkage } from "@/lib/deal-checkpoint-linkage";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { upsertLeakAlert } from "@/services/alert-workflow-service";
import { addSystemEventMessage, createCollaborationThread } from "@/services/collaboration-thread-service";
import { mapDealCheckpointRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { DealCheckpoint } from "@/types/deal";

type DbClient = ServerSupabaseClient;
type CheckpointRow = Database["public"]["Tables"]["deal_checkpoints"]["Row"];

interface ProfileLite {
  id: string;
  display_name: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

async function ensureCheckpointThread(params: {
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
    .eq("thread_type", "next_step")
    .eq("status", "open")
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (data?.id) return data.id;

  const thread = await createCollaborationThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId,
    threadType: "next_step",
    title: "Checkpoint execution",
    createdBy: params.createdBy,
    summary: "Checkpoint progress and blockers."
  });
  return thread.id;
}

export async function listDealCheckpoints(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
}): Promise<DealCheckpoint[]> {
  const { data, error } = await params.supabase
    .from("deal_checkpoints")
    .select("*, owner:profiles!deal_checkpoints_owner_id_fkey(id, display_name)")
    .eq("org_id", params.orgId)
    .eq("deal_room_id", params.dealRoomId)
    .order("created_at", { ascending: true });
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: CheckpointRow & { owner?: ProfileLite | null }) => mapDealCheckpointRow(row));
}

export async function createDealCheckpoint(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  checkpointType: Database["public"]["Enums"]["deal_checkpoint_type"];
  title: string;
  description?: string;
  status?: Database["public"]["Enums"]["deal_checkpoint_status"];
  ownerId?: string | null;
  dueAt?: string | null;
  evidenceSnapshot?: Record<string, unknown>;
  actorUserId: string;
}): Promise<DealCheckpoint> {
  const payload: Database["public"]["Tables"]["deal_checkpoints"]["Insert"] = {
    org_id: params.orgId,
    deal_room_id: params.dealRoomId,
    checkpoint_type: params.checkpointType,
    status: params.status ?? "pending",
    title: params.title,
    description: params.description ?? "",
    due_at: params.dueAt ?? null,
    completed_at: params.status === "completed" ? nowIso() : null,
    owner_id: params.ownerId ?? null,
    evidence_snapshot: (params.evidenceSnapshot ?? {}) as unknown as Database["public"]["Tables"]["deal_checkpoints"]["Insert"]["evidence_snapshot"]
  };
  const { data, error } = await params.supabase
    .from("deal_checkpoints")
    .upsert(payload, { onConflict: "deal_room_id,checkpoint_type" })
    .select("*, owner:profiles!deal_checkpoints_owner_id_fkey(id, display_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "create_deal_checkpoint_failed");
  const checkpoint = mapDealCheckpointRow(data as CheckpointRow & { owner?: ProfileLite | null });

  const threadId = await ensureCheckpointThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId,
    createdBy: params.actorUserId
  });
  await addSystemEventMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId,
    actorUserId: params.actorUserId,
    eventText: `Checkpoint updated: **${checkpoint.title}** -> ${checkpoint.status}.`,
    sourceRefType: "deal_checkpoint",
    sourceRefId: checkpoint.id
  });

  if (checkpoint.status === "blocked") {
    await handleBlockedCheckpoint({
      supabase: params.supabase,
      orgId: params.orgId,
      checkpoint,
      actorUserId: params.actorUserId
    });
  }

  return checkpoint;
}

export async function updateDealCheckpointStatus(params: {
  supabase: DbClient;
  orgId: string;
  checkpointId: string;
  status: Database["public"]["Enums"]["deal_checkpoint_status"];
  actorUserId: string;
  blockedReason?: string | null;
  dueAt?: string | null;
}): Promise<DealCheckpoint> {
  const patch: Database["public"]["Tables"]["deal_checkpoints"]["Update"] = {
    status: params.status,
    updated_at: nowIso()
  };
  if (params.status === "completed") patch.completed_at = nowIso();
  if (params.status !== "completed") patch.completed_at = null;
  if (params.dueAt !== undefined) patch.due_at = params.dueAt;
  if (params.blockedReason !== undefined) {
    patch.description = params.blockedReason ?? "";
  }

  const { data, error } = await params.supabase
    .from("deal_checkpoints")
    .update(patch)
    .eq("org_id", params.orgId)
    .eq("id", params.checkpointId)
    .select("*, owner:profiles!deal_checkpoints_owner_id_fkey(id, display_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "update_checkpoint_status_failed");
  const checkpoint = mapDealCheckpointRow(data as CheckpointRow & { owner?: ProfileLite | null });

  const threadId = await ensureCheckpointThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: checkpoint.dealRoomId,
    createdBy: params.actorUserId
  });
  await addSystemEventMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId,
    actorUserId: params.actorUserId,
    eventText: `Checkpoint status changed: **${checkpoint.title}** -> ${checkpoint.status}.`,
    sourceRefType: "deal_checkpoint",
    sourceRefId: checkpoint.id
  });

  if (checkpoint.status === "blocked") {
    await handleBlockedCheckpoint({
      supabase: params.supabase,
      orgId: params.orgId,
      checkpoint,
      actorUserId: params.actorUserId
    });
  }

  return checkpoint;
}

async function handleBlockedCheckpoint(params: {
  supabase: DbClient;
  orgId: string;
  checkpoint: DealCheckpoint;
  actorUserId: string;
}): Promise<void> {
  const roomRes = await params.supabase
    .from("deal_rooms")
    .select("id, customer_id, owner_id, title")
    .eq("org_id", params.orgId)
    .eq("id", params.checkpoint.dealRoomId)
    .single();
  if (roomRes.error || !roomRes.data) throw new Error(roomRes.error?.message ?? "deal_room_not_found");

  const linkage = buildBlockedCheckpointLinkage({
    checkpointTitle: params.checkpoint.title,
    checkpointType: params.checkpoint.checkpointType,
    checkpointDescription: params.checkpoint.description,
    checkpointDueAt: params.checkpoint.dueAt,
    dealRoomTitle: roomRes.data.title
  });

  await params.supabase
    .from("deal_rooms")
    .update({
      manager_attention_needed: linkage.roomPatch.managerAttentionNeeded,
      room_status: linkage.roomPatch.roomStatus,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.checkpoint.dealRoomId);

  await upsertLeakAlert({
    supabase: params.supabase,
    input: {
      orgId: params.orgId,
      customerId: roomRes.data.customer_id,
      opportunityId: null,
      ownerId: roomRes.data.owner_id,
      ruleType: linkage.alert.ruleType,
      source: linkage.alert.source,
      level: linkage.alert.level,
      title: linkage.alert.title,
      description: linkage.alert.description,
      evidence: linkage.alert.evidence,
      suggestedOwnerAction: linkage.alert.suggestedOwnerAction,
      dueAt: linkage.alert.dueAt,
      aiRunId: null
    }
  });
}
