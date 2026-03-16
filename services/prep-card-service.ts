import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapPrepCoverageByWorkItem } from "@/lib/briefing-hub";
import { mapPrepCardRow, mapPrepFeedbackRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { PrepCard, PrepFeedback, PrepFeedbackTargetType, PrepFeedbackType } from "@/types/preparation";

type DbClient = ServerSupabaseClient;
type PrepCardRow = Database["public"]["Tables"]["prep_cards"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function listPrepCards(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  workItemId?: string | null;
  cardType?: Database["public"]["Enums"]["prep_card_type"];
  statuses?: Database["public"]["Enums"]["prep_card_status"][];
  limit?: number;
}): Promise<PrepCard[]> {
  let query = params.supabase
    .from("prep_cards")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 30);

  if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
  if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
  if (params.workItemId !== undefined) query = query.eq("work_item_id", params.workItemId);
  if (params.cardType) query = query.eq("card_type", params.cardType);
  if (params.statuses?.length) query = query.in("status", params.statuses);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  const rows = (data ?? []) as PrepCardRow[];
  return rows.map((item) => mapPrepCardRow(item));
}

export async function getPrepCardById(params: {
  supabase: DbClient;
  orgId: string;
  prepCardId: string;
}): Promise<PrepCard | null> {
  const { data, error } = await params.supabase
    .from("prep_cards")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("id", params.prepCardId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapPrepCardRow(data as PrepCardRow);
}

export async function createPrepCard(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  opportunityId?: string | null;
  workItemId?: string | null;
  cardType: Database["public"]["Enums"]["prep_card_type"];
  status?: Database["public"]["Enums"]["prep_card_status"];
  title: string;
  summary: string;
  cardPayload: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  aiRunId?: string | null;
  validUntil?: string | null;
}): Promise<PrepCard> {
  const payload: Database["public"]["Tables"]["prep_cards"]["Insert"] = {
    org_id: params.orgId,
    owner_id: params.ownerId ?? null,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    work_item_id: params.workItemId ?? null,
    card_type: params.cardType,
    status: params.status ?? "ready",
    title: params.title,
    summary: params.summary,
    card_payload: params.cardPayload as Database["public"]["Tables"]["prep_cards"]["Insert"]["card_payload"],
    source_snapshot: params.sourceSnapshot as Database["public"]["Tables"]["prep_cards"]["Insert"]["source_snapshot"],
    generated_by: params.generatedBy,
    ai_run_id: params.aiRunId ?? null,
    valid_until: params.validUntil ?? null
  };

  const { data, error } = await params.supabase.from("prep_cards").insert(payload).select("*").single();
  if (error || !data) throw new Error(error?.message ?? "Failed to create prep card");
  return mapPrepCardRow(data as PrepCardRow);
}

export async function markPrepCardStatus(params: {
  supabase: DbClient;
  orgId: string;
  prepCardId: string;
  status: Database["public"]["Enums"]["prep_card_status"];
}): Promise<void> {
  const { error } = await params.supabase
    .from("prep_cards")
    .update({
      status: params.status,
      updated_at: nowIso()
    })
    .eq("org_id", params.orgId)
    .eq("id", params.prepCardId);
  if (error) throw new Error(error.message);
}

export async function getPrepCoverageForWorkItems(params: {
  supabase: DbClient;
  orgId: string;
  workItemIds: string[];
}): Promise<Record<string, PrepCard>> {
  if (params.workItemIds.length === 0) return {};
  const { data, error } = await params.supabase
    .from("prep_cards")
    .select("*")
    .eq("org_id", params.orgId)
    .in("work_item_id", params.workItemIds)
    .in("status", ["draft", "ready", "stale"])
    .order("created_at", { ascending: false });

  if (error) throw new Error(error.message);
  const rows = (data ?? []) as PrepCardRow[];
  const prepCards = rows.map((row) => mapPrepCardRow(row));
  return mapPrepCoverageByWorkItem(prepCards, params.workItemIds);
}

export async function addPrepFeedback(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  targetType: PrepFeedbackTargetType;
  targetId: string;
  feedbackType: PrepFeedbackType;
  feedbackText?: string | null;
}): Promise<PrepFeedback> {
  const payload: Database["public"]["Tables"]["prep_feedback"]["Insert"] = {
    org_id: params.orgId,
    user_id: params.userId,
    target_type: params.targetType,
    target_id: params.targetId,
    feedback_type: params.feedbackType,
    feedback_text: params.feedbackText ?? null
  };

  const { data, error } = await params.supabase.from("prep_feedback").insert(payload).select("*").single();
  if (error || !data) throw new Error(error?.message ?? "Failed to save prep feedback");
  return mapPrepFeedbackRow(data as Database["public"]["Tables"]["prep_feedback"]["Row"]);
}
