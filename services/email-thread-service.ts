import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapEmailMessageRow, mapEmailThreadRow } from "@/services/mappers";
import { recordExternalTouchpointEvent, runTouchpointRules } from "@/services/external-touchpoint-service";
import type { Database } from "@/types/database";
import type { EmailMessage, EmailThread } from "@/types/touchpoint";

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

export async function listEmailThreads(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  dealRoomId?: string | null;
  statuses?: Database["public"]["Enums"]["email_thread_status"][];
  limit?: number;
}): Promise<EmailThread[]> {
  let query = params.supabase
    .from("email_threads")
    .select("*, owner:profiles!email_threads_owner_id_fkey(id, display_name), customer:customers!email_threads_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .order("latest_message_at", { ascending: false, nullsFirst: false })
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 60);
  if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
  if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
  if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
  if (params.statuses?.length) query = query.in("thread_status", params.statuses);
  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: Database["public"]["Tables"]["email_threads"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }) =>
    mapEmailThreadRow(row)
  );
}

export async function getEmailThreadById(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
}): Promise<EmailThread | null> {
  const { data, error } = await params.supabase
    .from("email_threads")
    .select("*, owner:profiles!email_threads_owner_id_fkey(id, display_name), customer:customers!email_threads_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .eq("id", params.threadId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapEmailThreadRow(data as Database["public"]["Tables"]["email_threads"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null });
}

export async function createEmailThread(params: {
  supabase: DbClient;
  orgId: string;
  ownerId: string;
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
  externalAccountId?: string | null;
  externalThreadRef?: string | null;
  subject: string;
  participants?: string[];
  summary?: string;
  sourceSnapshot?: Record<string, unknown>;
}): Promise<EmailThread> {
  const payload: Database["public"]["Tables"]["email_threads"]["Insert"] = {
    org_id: params.orgId,
    owner_id: params.ownerId,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    deal_room_id: params.dealRoomId ?? null,
    external_account_id: params.externalAccountId ?? null,
    external_thread_ref: params.externalThreadRef ?? null,
    subject: params.subject,
    participants: (params.participants ?? []) as Database["public"]["Tables"]["email_threads"]["Insert"]["participants"],
    summary: params.summary ?? "",
    source_snapshot: (params.sourceSnapshot ?? {}) as Database["public"]["Tables"]["email_threads"]["Insert"]["source_snapshot"]
  };
  const { data, error } = await params.supabase
    .from("email_threads")
    .insert(payload)
    .select("*, owner:profiles!email_threads_owner_id_fkey(id, display_name), customer:customers!email_threads_customer_id_fkey(id, company_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "create_email_thread_failed");
  return mapEmailThreadRow(data as Database["public"]["Tables"]["email_threads"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null });
}

export async function listEmailMessages(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  limit?: number;
}): Promise<EmailMessage[]> {
  const { data, error } = await params.supabase
    .from("email_messages")
    .select("*, sender:profiles!email_messages_sender_user_id_fkey(id, display_name)")
    .eq("org_id", params.orgId)
    .eq("thread_id", params.threadId)
    .order("created_at", { ascending: true })
    .limit(params.limit ?? 200);
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: Database["public"]["Tables"]["email_messages"]["Row"] & { sender?: ProfileLite | null }) => mapEmailMessageRow(row));
}

export async function addEmailMessage(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  senderUserId?: string | null;
  direction: Database["public"]["Enums"]["email_message_direction"];
  messageSubject: string;
  messageBodyText: string;
  messageBodyMarkdown?: string;
  status: Database["public"]["Enums"]["email_message_status"];
  sourceType?: Database["public"]["Enums"]["email_message_source_type"];
  aiRunId?: string | null;
  externalMessageRef?: string | null;
  triggerRuleReview?: boolean;
}): Promise<EmailMessage> {
  const thread = await getEmailThreadById({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId: params.threadId
  });
  if (!thread) throw new Error("email_thread_not_found");

  const payload: Database["public"]["Tables"]["email_messages"]["Insert"] = {
    org_id: params.orgId,
    thread_id: params.threadId,
    sender_user_id: params.senderUserId ?? null,
    direction: params.direction,
    external_message_ref: params.externalMessageRef ?? null,
    message_subject: params.messageSubject,
    message_body_text: params.messageBodyText,
    message_body_markdown: params.messageBodyMarkdown ?? params.messageBodyText,
    sent_at: params.direction !== "inbound" ? nowIso() : null,
    received_at: params.direction === "inbound" ? nowIso() : null,
    status: params.status,
    source_type: params.sourceType ?? "manual",
    ai_run_id: params.aiRunId ?? null
  };

  const { data, error } = await params.supabase
    .from("email_messages")
    .insert(payload)
    .select("*, sender:profiles!email_messages_sender_user_id_fkey(id, display_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "add_email_message_failed");

  const nextThreadStatus: Database["public"]["Enums"]["email_thread_status"] =
    params.direction === "inbound" ? "replied" : params.status === "draft" ? thread.threadStatus : "waiting_reply";
  const sentimentHint: Database["public"]["Enums"]["email_sentiment_hint"] =
    params.direction === "inbound" && params.messageBodyText.includes("感谢")
      ? "positive"
      : params.direction === "inbound" && params.messageBodyText.includes("暂不")
        ? "negative"
        : thread.sentimentHint;

  await params.supabase
    .from("email_threads")
    .update({
      latest_message_at: nowIso(),
      thread_status: nextThreadStatus,
      sentiment_hint: sentimentHint,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.threadId);

  await recordExternalTouchpointEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId: thread.ownerId,
    customerId: thread.customerId,
    opportunityId: thread.opportunityId,
    dealRoomId: thread.dealRoomId,
    touchpointType: "email",
    eventType:
      params.direction === "inbound" ? "email_received" : params.status === "draft" ? "draft_created" : "email_sent",
    relatedRefType: "email_message",
    relatedRefId: data.id,
    eventSummary: `${params.direction === "inbound" ? "Inbound" : params.status === "draft" ? "Draft" : "Outbound"} email: ${params.messageSubject}`,
    eventPayload: {
      thread_id: params.threadId,
      direction: params.direction,
      status: params.status
    }
  });

  if (params.triggerRuleReview ?? true) {
    await runTouchpointRules({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.senderUserId ?? thread.ownerId
    }).catch(() => {
      // non-blocking in MVP
    });
  }

  return mapEmailMessageRow(data as Database["public"]["Tables"]["email_messages"]["Row"] & { sender?: ProfileLite | null });
}

export async function linkEmailThreadToDeal(params: {
  supabase: DbClient;
  orgId: string;
  threadId: string;
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
}): Promise<EmailThread> {
  const { data, error } = await params.supabase
    .from("email_threads")
    .update({
      customer_id: params.customerId ?? null,
      opportunity_id: params.opportunityId ?? null,
      deal_room_id: params.dealRoomId ?? null,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.threadId)
    .select("*, owner:profiles!email_threads_owner_id_fkey(id, display_name), customer:customers!email_threads_customer_id_fkey(id, company_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "link_email_thread_to_deal_failed");
  return mapEmailThreadRow(data as Database["public"]["Tables"]["email_threads"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null });
}

