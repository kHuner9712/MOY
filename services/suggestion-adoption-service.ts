import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapFeedbackToAdoptionType } from "@/lib/suggestion-adoption";
import { mapSuggestionAdoptionRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { SuggestionAdoption, SuggestionAdoptionContext, SuggestionAdoptionType, SuggestionTargetType } from "@/types/outcome";

type DbClient = ServerSupabaseClient;

export { mapFeedbackToAdoptionType };

export async function trackSuggestionAdoption(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  targetType: SuggestionTargetType;
  targetId: string;
  adoptionType: SuggestionAdoptionType;
  adoptionContext: SuggestionAdoptionContext;
  editDistanceHint?: number | null;
  linkedOutcomeId?: string | null;
}): Promise<SuggestionAdoption> {
  const payload: Database["public"]["Tables"]["suggestion_adoptions"]["Insert"] = {
    org_id: params.orgId,
    user_id: params.userId,
    target_type: params.targetType,
    target_id: params.targetId,
    adoption_type: params.adoptionType,
    edit_distance_hint: params.editDistanceHint ?? null,
    adoption_context: params.adoptionContext,
    linked_outcome_id: params.linkedOutcomeId ?? null
  };

  const { data, error } = await params.supabase.from("suggestion_adoptions").insert(payload).select("*").single();
  if (error || !data) throw new Error(error?.message ?? "Failed to track suggestion adoption");
  return mapSuggestionAdoptionRow(data as Database["public"]["Tables"]["suggestion_adoptions"]["Row"]);
}

export async function listSuggestionAdoptions(params: {
  supabase: DbClient;
  orgId: string;
  userId?: string;
  targetType?: SuggestionTargetType;
  linkedOutcomeId?: string;
  limit?: number;
}): Promise<SuggestionAdoption[]> {
  let query = params.supabase
    .from("suggestion_adoptions")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 80);

  if (params.userId) query = query.eq("user_id", params.userId);
  if (params.targetType) query = query.eq("target_type", params.targetType);
  if (params.linkedOutcomeId) query = query.eq("linked_outcome_id", params.linkedOutcomeId);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: Database["public"]["Tables"]["suggestion_adoptions"]["Row"]) => mapSuggestionAdoptionRow(row));
}

export async function linkSuggestionAdoptionsToOutcome(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  adoptionIds: string[];
  outcomeId: string;
}): Promise<number> {
  if (params.adoptionIds.length === 0) return 0;
  const { data, error } = await params.supabase
    .from("suggestion_adoptions")
    .update({ linked_outcome_id: params.outcomeId })
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .in("id", params.adoptionIds)
    .select("id");

  if (error) throw new Error(error.message);
  return (data ?? []).length;
}
