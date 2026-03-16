import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { trackSuggestionAdoption } from "@/services/suggestion-adoption-service";
import type { Database } from "@/types/database";

const requestSchema = z.object({
  targetType: z.enum(["prep_card", "content_draft", "task_action_suggestion", "morning_brief"]),
  targetId: z.string().uuid(),
  adoptionType: z.enum(["viewed", "copied", "edited", "adopted", "dismissed", "partially_used"]),
  adoptionContext: z.enum(["before_followup", "before_quote", "before_meeting", "during_task_execution", "after_review"]),
  editDistanceHint: z.number().min(0).max(1).optional(),
  linkedOutcomeId: z.string().uuid().optional()
});

async function ensureTargetAccess(params: {
  supabase: ServerSupabaseClient;
  orgId: string;
  userId: string;
  role: Database["public"]["Enums"]["app_role"];
  targetType: Database["public"]["Enums"]["suggestion_target_type"];
  targetId: string;
}): Promise<boolean> {
  if (params.role === "manager") return true;

  if (params.targetType === "prep_card") {
    const { data, error } = await params.supabase
      .from("prep_cards")
      .select("owner_id")
      .eq("org_id", params.orgId)
      .eq("id", params.targetId)
      .maybeSingle();
    if (error) throw new Error(error.message);
    return (data as { owner_id: string | null } | null)?.owner_id === params.userId;
  }

  if (params.targetType === "content_draft") {
    const { data, error } = await params.supabase
      .from("content_drafts")
      .select("owner_id")
      .eq("org_id", params.orgId)
      .eq("id", params.targetId)
      .maybeSingle();
    if (error) throw new Error(error.message);
    return (data as { owner_id: string } | null)?.owner_id === params.userId;
  }

  if (params.targetType === "morning_brief") {
    const { data, error } = await params.supabase
      .from("morning_briefs")
      .select("target_user_id")
      .eq("org_id", params.orgId)
      .eq("id", params.targetId)
      .maybeSingle();
    if (error) throw new Error(error.message);
    return (data as { target_user_id: string | null } | null)?.target_user_id === params.userId;
  }

  const { data, error } = await params.supabase
    .from("work_items")
    .select("owner_id")
    .eq("org_id", params.orgId)
    .eq("id", params.targetId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  return (data as { owner_id: string } | null)?.owner_id === params.userId;
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const allowed = await ensureTargetAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      role: auth.profile.role,
      targetType: parsed.data.targetType,
      targetId: parsed.data.targetId
    });

    if (!allowed && !isManager(auth.profile)) {
      return fail("No permission for this target", 403);
    }

    const adoption = await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: parsed.data.targetType,
      targetId: parsed.data.targetId,
      adoptionType: parsed.data.adoptionType,
      adoptionContext: parsed.data.adoptionContext,
      editDistanceHint: parsed.data.editDistanceHint ?? null,
      linkedOutcomeId: parsed.data.linkedOutcomeId ?? null
    });

    return ok(adoption);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "track_adoption_failed", 500);
  }
}
