import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  mapMobileDeviceSessionRow,
  mapMobileDraftSyncJobRow,
  mapOfflineActionQueueRow
} from "@/services/mappers";
import type { Database } from "@/types/database";
import type { MobileDeviceSession, MobileDraftSyncJob, OfflineActionQueueItem } from "@/types/mobile";

type DbClient = ServerSupabaseClient;

export async function createMobileDraftSyncJob(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  draftType: Database["public"]["Enums"]["mobile_draft_type"];
  localDraftId: string;
  summary?: string | null;
  payloadSnapshot?: Record<string, unknown>;
  targetEntityType?: string | null;
  targetEntityId?: string | null;
  syncStatus?: Database["public"]["Enums"]["mobile_draft_sync_status"];
}): Promise<MobileDraftSyncJob> {
  const res = await params.supabase
    .from("mobile_draft_sync_jobs")
    .insert({
      org_id: params.orgId,
      user_id: params.userId,
      draft_type: params.draftType,
      local_draft_id: params.localDraftId,
      sync_status: params.syncStatus ?? "pending",
      summary: params.summary ?? null,
      payload_snapshot:
        (params.payloadSnapshot ?? {}) as Database["public"]["Tables"]["mobile_draft_sync_jobs"]["Insert"]["payload_snapshot"],
      target_entity_type: params.targetEntityType ?? null,
      target_entity_id: params.targetEntityId ?? null
    })
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapMobileDraftSyncJobRow(res.data as Database["public"]["Tables"]["mobile_draft_sync_jobs"]["Row"]);
}

export async function updateMobileDraftSyncJob(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  jobId: string;
  patch: Database["public"]["Tables"]["mobile_draft_sync_jobs"]["Update"];
}): Promise<MobileDraftSyncJob> {
  const res = await params.supabase
    .from("mobile_draft_sync_jobs")
    .update(params.patch)
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .eq("id", params.jobId)
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapMobileDraftSyncJobRow(res.data as Database["public"]["Tables"]["mobile_draft_sync_jobs"]["Row"]);
}

export async function listMobileDraftSyncJobs(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  statuses?: Database["public"]["Enums"]["mobile_draft_sync_status"][];
  limit?: number;
}): Promise<MobileDraftSyncJob[]> {
  let query = params.supabase
    .from("mobile_draft_sync_jobs")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 30);
  if (params.statuses?.length) query = query.in("sync_status", params.statuses);
  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return (res.data ?? []).map((row: Database["public"]["Tables"]["mobile_draft_sync_jobs"]["Row"]) => mapMobileDraftSyncJobRow(row));
}

export async function upsertMobileDeviceSession(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  deviceLabel: string;
  installType?: Database["public"]["Enums"]["mobile_install_type"];
  appVersion?: string | null;
  pushCapable?: boolean;
  metadata?: Record<string, unknown>;
}): Promise<MobileDeviceSession> {
  const existing = await params.supabase
    .from("mobile_device_sessions")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .eq("device_label", params.deviceLabel)
    .maybeSingle();
  if (existing.error) throw new Error(existing.error.message);

  if (existing.data) {
    const updated = await params.supabase
      .from("mobile_device_sessions")
      .update({
        install_type: params.installType ?? existing.data.install_type,
        app_version: params.appVersion ?? existing.data.app_version,
        push_capable: params.pushCapable ?? existing.data.push_capable,
        metadata: (params.metadata ?? existing.data.metadata ?? {}) as Database["public"]["Tables"]["mobile_device_sessions"]["Update"]["metadata"],
        last_seen_at: new Date().toISOString()
      })
      .eq("id", existing.data.id)
      .select("*")
      .single();
    if (updated.error) throw new Error(updated.error.message);
    return mapMobileDeviceSessionRow(updated.data as Database["public"]["Tables"]["mobile_device_sessions"]["Row"]);
  }

  const inserted = await params.supabase
    .from("mobile_device_sessions")
    .insert({
      org_id: params.orgId,
      user_id: params.userId,
      device_label: params.deviceLabel,
      install_type: params.installType ?? "browser",
      app_version: params.appVersion ?? null,
      push_capable: params.pushCapable ?? false,
      metadata: (params.metadata ?? {}) as Database["public"]["Tables"]["mobile_device_sessions"]["Insert"]["metadata"],
      last_seen_at: new Date().toISOString()
    })
    .select("*")
    .single();
  if (inserted.error) throw new Error(inserted.error.message);
  return mapMobileDeviceSessionRow(inserted.data as Database["public"]["Tables"]["mobile_device_sessions"]["Row"]);
}

export async function listMobileDeviceSessions(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  limit?: number;
}): Promise<MobileDeviceSession[]> {
  const res = await params.supabase
    .from("mobile_device_sessions")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .order("last_seen_at", { ascending: false })
    .limit(params.limit ?? 10);
  if (res.error) throw new Error(res.error.message);
  return (res.data ?? []).map((row: Database["public"]["Tables"]["mobile_device_sessions"]["Row"]) => mapMobileDeviceSessionRow(row));
}

export async function enqueueOfflineAction(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  actionType: Database["public"]["Enums"]["offline_action_type"];
  actionPayload?: Record<string, unknown>;
  queueStatus?: Database["public"]["Enums"]["offline_action_queue_status"];
  targetEntityType?: string | null;
  targetEntityId?: string | null;
}): Promise<OfflineActionQueueItem> {
  const res = await params.supabase
    .from("offline_action_queue")
    .insert({
      org_id: params.orgId,
      user_id: params.userId,
      action_type: params.actionType,
      action_payload: (params.actionPayload ?? {}) as Database["public"]["Tables"]["offline_action_queue"]["Insert"]["action_payload"],
      queue_status: params.queueStatus ?? "queued",
      target_entity_type: params.targetEntityType ?? null,
      target_entity_id: params.targetEntityId ?? null
    })
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapOfflineActionQueueRow(res.data as Database["public"]["Tables"]["offline_action_queue"]["Row"]);
}

export async function updateOfflineActionQueueItem(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  queueId: string;
  patch: Database["public"]["Tables"]["offline_action_queue"]["Update"];
}): Promise<OfflineActionQueueItem> {
  const res = await params.supabase
    .from("offline_action_queue")
    .update(params.patch)
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .eq("id", params.queueId)
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapOfflineActionQueueRow(res.data as Database["public"]["Tables"]["offline_action_queue"]["Row"]);
}
