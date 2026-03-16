import { deriveMemoryItemStatusFromFeedback } from "@/lib/memory-feedback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  mapMemoryFeedbackRow,
  mapUserMemoryItemRow,
  mapUserMemoryProfileRow
} from "@/services/mappers";
import type { Database } from "@/types/database";
import type { MemoryFeedbackType, UserMemoryItem, UserMemoryProfile } from "@/types/memory";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["user_memory_profiles"]["Row"];
type ItemRow = Database["public"]["Tables"]["user_memory_items"]["Row"];
type FeedbackRow = Database["public"]["Tables"]["memory_feedback"]["Row"];

export async function getUserMemoryProfile(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
}): Promise<UserMemoryProfile | null> {
  const { data, error } = await params.supabase
    .from("user_memory_profiles")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .maybeSingle();

  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapUserMemoryProfileRow(data as ProfileRow);
}

export async function listUserMemoryItems(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  includeHidden?: boolean;
  limit?: number;
}): Promise<UserMemoryItem[]> {
  let query = params.supabase
    .from("user_memory_items")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 80);

  if (!params.includeHidden) {
    query = query.eq("status", "active");
  }

  const { data, error } = await query;
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as ItemRow[];
  return rows.map((item) => mapUserMemoryItemRow(item));
}

export async function upsertUserMemoryProfile(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  profile: {
    memoryVersion: string;
    summary: string;
    preferredCustomerTypes: string[];
    preferredCommunicationStyles: string[];
    commonObjections: string[];
    effectiveTactics: string[];
    commonFollowupRhythm: string[];
    quotingStyleNotes: string[];
    riskBlindSpots: string[];
    managerCoachingFocus: string[];
    confidenceScore: number;
    sourceWindowDays: number;
    lastCompiledAt: string;
  };
}): Promise<UserMemoryProfile> {
  const payload: Database["public"]["Tables"]["user_memory_profiles"]["Insert"] = {
    org_id: params.orgId,
    user_id: params.userId,
    memory_version: params.profile.memoryVersion,
    summary: params.profile.summary,
    preferred_customer_types: params.profile.preferredCustomerTypes,
    preferred_communication_styles: params.profile.preferredCommunicationStyles,
    common_objections: params.profile.commonObjections,
    effective_tactics: params.profile.effectiveTactics,
    common_followup_rhythm: params.profile.commonFollowupRhythm,
    quoting_style_notes: params.profile.quotingStyleNotes,
    risk_blind_spots: params.profile.riskBlindSpots,
    manager_coaching_focus: params.profile.managerCoachingFocus,
    confidence_score: params.profile.confidenceScore,
    source_window_days: params.profile.sourceWindowDays,
    last_compiled_at: params.profile.lastCompiledAt
  };

  const { data, error } = await params.supabase
    .from("user_memory_profiles")
    .upsert(payload, { onConflict: "org_id,user_id" })
    .select("*")
    .single();

  if (error || !data) {
    throw new Error(error?.message ?? "Failed to upsert user memory profile");
  }

  return mapUserMemoryProfileRow(data as ProfileRow);
}

export async function replaceUserMemoryItems(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  items: Array<{
    memoryType: Database["public"]["Enums"]["memory_item_type"];
    title: string;
    description: string;
    evidenceSnapshot: Record<string, unknown>;
    confidenceScore: number;
    sourceCount: number;
  }>;
}): Promise<UserMemoryItem[]> {
  await params.supabase.from("user_memory_items").delete().eq("org_id", params.orgId).eq("user_id", params.userId).eq("created_by_system", true);

  if (params.items.length === 0) return [];

  const payload: Database["public"]["Tables"]["user_memory_items"]["Insert"][] = params.items.map((item) => ({
    org_id: params.orgId,
    user_id: params.userId,
    memory_type: item.memoryType,
    title: item.title,
    description: item.description,
    evidence_snapshot: item.evidenceSnapshot as Database["public"]["Tables"]["user_memory_items"]["Insert"]["evidence_snapshot"],
    confidence_score: item.confidenceScore,
    source_count: item.sourceCount,
    status: "active",
    created_by_system: true
  }));

  const { data, error } = await params.supabase.from("user_memory_items").insert(payload).select("*");
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as ItemRow[];
  return rows.map((item) => mapUserMemoryItemRow(item));
}

export async function submitMemoryFeedback(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  memoryItemId: string;
  feedbackType: MemoryFeedbackType;
  feedbackText?: string;
}): Promise<{ item: UserMemoryItem; feedbackId: string }> {
  const { data: itemRaw, error: itemError } = await params.supabase
    .from("user_memory_items")
    .select("*")
    .eq("id", params.memoryItemId)
    .eq("org_id", params.orgId)
    .maybeSingle();

  if (itemError) throw new Error(itemError.message);
  if (!itemRaw) throw new Error("memory_item_not_found");

  const item = itemRaw as ItemRow;
  if (item.user_id !== params.userId) {
    throw new Error("cannot_feedback_other_user_memory");
  }

  const { data: feedbackRaw, error: feedbackError } = await params.supabase
    .from("memory_feedback")
    .insert({
      org_id: params.orgId,
      user_id: params.userId,
      memory_item_id: params.memoryItemId,
      feedback_type: params.feedbackType,
      feedback_text: params.feedbackText ?? null
    })
    .select("*")
    .single();

  if (feedbackError || !feedbackRaw) throw new Error(feedbackError?.message ?? "Failed to save memory feedback");

  const nextStatus = deriveMemoryItemStatusFromFeedback(params.feedbackType);

  let updated = itemRaw as ItemRow;
  if (nextStatus) {
    const { data: updatedRaw, error: updateError } = await params.supabase
      .from("user_memory_items")
      .update({
        status: nextStatus
      })
      .eq("id", params.memoryItemId)
      .select("*")
      .single();
    if (updateError || !updatedRaw) throw new Error(updateError?.message ?? "Failed to update memory item status");
    updated = updatedRaw as ItemRow;
  }

  const feedback = mapMemoryFeedbackRow(feedbackRaw as FeedbackRow);
  return {
    item: mapUserMemoryItemRow(updated),
    feedbackId: feedback.id
  };
}
