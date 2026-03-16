import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { generatePrepCard } from "@/services/preparation-engine-service";
import { trackSuggestionAdoption } from "@/services/suggestion-adoption-service";

const requestSchema = z.object({
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  workItemId: z.string().uuid().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);
  if (!isManager(auth.profile)) return fail("Manager access required", 403);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const result = await generatePrepCard({
      supabase: auth.supabase,
      profile: auth.profile,
      cardType: "manager_attention",
      customerId: parsed.data.customerId ?? null,
      opportunityId: parsed.data.opportunityId ?? null,
      workItemId: parsed.data.workItemId ?? null
    });

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "prep_card",
      targetId: result.prepCard.id,
      adoptionType: "viewed",
      adoptionContext: "after_review"
    }).catch(() => null);

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "generate_manager_attention_failed", 500);
  }
}
