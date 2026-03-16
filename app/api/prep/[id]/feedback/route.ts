import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { derivePrepCardStatusFromFeedback } from "@/lib/preparation-feedback";
import { getServerAuthContext } from "@/lib/server-auth";
import { addPrepFeedback, getPrepCardById, markPrepCardStatus } from "@/services/prep-card-service";
import { mapFeedbackToAdoptionType, trackSuggestionAdoption } from "@/services/suggestion-adoption-service";

const requestSchema = z.object({
  feedbackType: z.enum(["useful", "not_useful", "inaccurate", "outdated", "adopted"]),
  feedbackText: z.string().max(300).optional()
});

export async function POST(request: Request, { params }: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const parsedBody = requestSchema.safeParse(await request.json().catch(() => null));
  if (!parsedBody.success) return fail("Invalid request payload", 400);

  try {
    const card = await getPrepCardById({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      prepCardId: params.id
    });
    if (!card) return fail("Prep card not found", 404);
    if (!isManager(auth.profile) && card.ownerId !== auth.profile.id) return fail("No permission for this prep card", 403);

    const nextStatus = derivePrepCardStatusFromFeedback(parsedBody.data.feedbackType);
    if (nextStatus) {
      await markPrepCardStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        prepCardId: card.id,
        status: nextStatus
      });
    }

    const feedback = await addPrepFeedback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "prep_card",
      targetId: card.id,
      feedbackType: parsedBody.data.feedbackType,
      feedbackText: parsedBody.data.feedbackText ?? null
    });

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "prep_card",
      targetId: card.id,
      adoptionType: mapFeedbackToAdoptionType(parsedBody.data.feedbackType),
      adoptionContext: "after_review"
    });

    return ok({
      feedback,
      prepCardId: card.id
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "save_prep_feedback_failed", 500);
  }
}
