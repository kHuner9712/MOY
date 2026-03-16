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

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);
  const { customerId, opportunityId, workItemId } = parsed.data;

  try {
    if (!isManager(auth.profile) && customerId) {
      const check = await auth.supabase.from("customers").select("owner_id").eq("org_id", auth.profile.org_id).eq("id", customerId).maybeSingle();
      if (check.error) throw new Error(check.error.message);
      const customerOwnerId = (check.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) return fail("No permission for this customer", 403);
    }

    const result = await generatePrepCard({
      supabase: auth.supabase,
      profile: auth.profile,
      cardType: "quote_prep",
      customerId: customerId ?? null,
      opportunityId: opportunityId ?? null,
      workItemId: workItemId ?? null
    });

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "prep_card",
      targetId: result.prepCard.id,
      adoptionType: "viewed",
      adoptionContext: "before_quote"
    }).catch(() => null);

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "generate_quote_prep_failed", 500);
  }
}
