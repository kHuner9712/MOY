import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { addPlaybookFeedback } from "@/services/playbook-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  playbookEntryId: z.string().uuid().optional(),
  feedbackType: z.enum(["useful", "not_useful", "outdated", "inaccurate", "adopted"]),
  feedbackText: z.string().max(500).optional()
});

export async function POST(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const result = await addPlaybookFeedback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      playbookId: params.id,
      playbookEntryId: parsed.data.playbookEntryId,
      feedbackType: parsed.data.feedbackType,
      feedbackText: parsed.data.feedbackText
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "playbook_feedback_failed", 500);
  }
}
