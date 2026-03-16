import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapCollaborationMessageRow, mapCollaborationThreadRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { CollaborationMessage, CollaborationThread } from "@/types/deal";

type DbClient = ServerSupabaseClient;

interface ProfileLite {
  id: string;
  display_name: string;
}

type ThreadRow = Database["public"]["Tables"]["collaboration_threads"]["Row"];
type MessageRow = Database["public"]["Tables"]["collaboration_messages"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function listCollaborationThreads(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  statuses?: Database["public"]["Enums"]["collaboration_thread_status"][];
}): Promise<CollaborationThread[]> {
  let query = params.supabase
    .from("collaboration_threads")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("deal_room_id", params.dealRoomId)
    .order("updated_at", { ascending: false });
  if (params.statuses?.length) query = query.in("status", params.statuses);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: ThreadRow) => mapCollaborationThreadRow(row));
}

export async function createCollaborationThread(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  threadType: Database["public"]["Enums"]["collaboration_thread_type"];
  title: string;
  createdBy: string;
  summary?: string;
}): Promise<CollaborationThread> {
  const payload: Database["public"]["Tables"]["collaboration_threads"]["Insert"] = {
    org_id: params.orgId,
    deal_room_id: params.dealRoomId,
    thread_type: params.threadType,
    title: params.title,
    status: "open",
    summary: params.summary ?? "",
    created_by: params.createdBy
  };

  const { data, error } = await params.supabase.from("collaboration_threads").insert(payload).select("*").single();
  if (error || !data) throw new Error(error?.message ?? "create_collaboration_thread_failed");
  return mapCollaborationThreadRow(data as ThreadRow);
}

export async function updateThreadStatus(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  status: Database["public"]["Enums"]["collaboration_thread_status"];
}): Promise<CollaborationThread> {
  const { data, error } = await params.supabase
    .from("collaboration_threads")
    .update({
      status: params.status,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.threadId)
    .select("*")
    .single();
  if (error || !data) throw new Error(error?.message ?? "update_thread_status_failed");
  return mapCollaborationThreadRow(data as ThreadRow);
}

export async function updateThreadSummary(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  summary: string;
}): Promise<CollaborationThread> {
  const { data, error } = await params.supabase
    .from("collaboration_threads")
    .update({
      summary: params.summary,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.threadId)
    .select("*")
    .single();
  if (error || !data) throw new Error(error?.message ?? "update_thread_summary_failed");
  return mapCollaborationThreadRow(data as ThreadRow);
}

export async function listThreadMessages(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  limit?: number;
}): Promise<CollaborationMessage[]> {
  const { data, error } = await params.supabase
    .from("collaboration_messages")
    .select("*, author:profiles!collaboration_messages_author_user_id_fkey(id, display_name)")
    .eq("org_id", params.orgId)
    .eq("thread_id", params.threadId)
    .order("created_at", { ascending: true })
    .limit(params.limit ?? 200);
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: MessageRow & { author?: ProfileLite | null }) => mapCollaborationMessageRow(row));
}

export async function addThreadMessage(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  authorUserId: string;
  messageType?: Database["public"]["Enums"]["collaboration_message_type"];
  bodyMarkdown: string;
  mentions?: string[];
  sourceRefType?: string | null;
  sourceRefId?: string | null;
}): Promise<CollaborationMessage> {
  const payload: Database["public"]["Tables"]["collaboration_messages"]["Insert"] = {
    org_id: params.orgId,
    thread_id: params.threadId,
    author_user_id: params.authorUserId,
    message_type: params.messageType ?? "comment",
    body_markdown: params.bodyMarkdown,
    mentions: (params.mentions ?? []) as unknown as Database["public"]["Tables"]["collaboration_messages"]["Insert"]["mentions"],
    source_ref_type: params.sourceRefType ?? null,
    source_ref_id: params.sourceRefId ?? null
  };
  const { data, error } = await params.supabase
    .from("collaboration_messages")
    .insert(payload)
    .select("*, author:profiles!collaboration_messages_author_user_id_fkey(id, display_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "add_thread_message_failed");

  await params.supabase
    .from("collaboration_threads")
    .update({ updated_at: nowIso() })
    .eq("org_id", params.orgId)
    .eq("id", params.threadId);

  return mapCollaborationMessageRow(data as MessageRow & { author?: ProfileLite | null });
}

export async function addSystemEventMessage(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  actorUserId: string;
  eventText: string;
  sourceRefType?: string | null;
  sourceRefId?: string | null;
}): Promise<CollaborationMessage> {
  return addThreadMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId: params.threadId,
    authorUserId: params.actorUserId,
    messageType: "system_event",
    bodyMarkdown: params.eventText,
    mentions: [],
    sourceRefType: params.sourceRefType ?? null,
    sourceRefId: params.sourceRefId ?? null
  });
}

export async function listDealRoomMessages(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  limit?: number;
}): Promise<CollaborationMessage[]> {
  const { data: threads, error: threadError } = await params.supabase
    .from("collaboration_threads")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("deal_room_id", params.dealRoomId);
  if (threadError) throw new Error(threadError.message);
  const threadIds = (threads ?? []).map((item: { id: string }) => item.id);
  if (threadIds.length === 0) return [];

  const { data, error } = await params.supabase
    .from("collaboration_messages")
    .select("*, author:profiles!collaboration_messages_author_user_id_fkey(id, display_name)")
    .eq("org_id", params.orgId)
    .in("thread_id", threadIds)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 200);
  if (error) throw new Error(error.message);

  return (data ?? []).map((row: MessageRow & { author?: ProfileLite | null }) => mapCollaborationMessageRow(row));
}
