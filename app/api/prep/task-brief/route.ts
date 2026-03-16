import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { generatePrepCard } from "@/services/preparation-engine-service";
import { trackSuggestionAdoption } from "@/services/suggestion-adoption-service";

const requestSchema = z.object({
  workItemId: z.string().uuid()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const workItemCheck = await auth.supabase
      .from("work_items")
      .select("owner_id")
      .eq("org_id", auth.profile.org_id)
      .eq("id", parsed.data.workItemId)
      .maybeSingle();
    if (workItemCheck.error) throw new Error(workItemCheck.error.message);
    if (!workItemCheck.data) return fail("Work item not found", 404);
    const taskOwnerId = (workItemCheck.data as { owner_id: string } | null)?.owner_id ?? null;
    if (!isManager(auth.profile) && taskOwnerId !== auth.profile.id) return fail("No permission for this task", 403);

    const result = await generatePrepCard({
      supabase: auth.supabase,
      profile: auth.profile,
      cardType: "task_brief",
      workItemId: parsed.data.workItemId
    });

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "prep_card",
      targetId: result.prepCard.id,
      adoptionType: "viewed",
      adoptionContext: "during_task_execution"
    }).catch(() => null);

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "generate_task_brief_failed", 500);
  }
}
