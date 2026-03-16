import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { addContentDraftFeedback } from "@/services/content-draft-service";
import { mapFeedbackToAdoptionType, trackSuggestionAdoption } from "@/services/suggestion-adoption-service";

const requestSchema = z.object({
  feedbackType: z.enum(["useful", "not_useful", "adopted", "inaccurate"]),
  feedbackText: z.string().max(300).optional()
});

export async function POST(request: Request, { params }: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const draftRes = await auth.supabase
      .from("content_drafts")
      .select("id, owner_id, org_id")
      .eq("org_id", auth.profile.org_id)
      .eq("id", params.id)
      .maybeSingle();
    if (draftRes.error) throw new Error(draftRes.error.message);
    if (!draftRes.data) return fail("Draft not found", 404);
    const draftOwnerId = (draftRes.data as { owner_id: string } | null)?.owner_id ?? null;
    if (!isManager(auth.profile) && draftOwnerId !== auth.profile.id) return fail("No permission for this draft", 403);

    const feedback = await addContentDraftFeedback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      draftId: params.id,
      feedbackType: parsed.data.feedbackType,
      feedbackText: parsed.data.feedbackText ?? null
    });

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "content_draft",
      targetId: params.id,
      adoptionType: mapFeedbackToAdoptionType(parsed.data.feedbackType),
      adoptionContext: "after_review"
    });

    return ok({
      feedback,
      draftId: params.id
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "save_draft_feedback_failed", 500);
  }
}
