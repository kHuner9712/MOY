import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { submitMemoryFeedback } from "@/services/user-memory-service";

const requestSchema = z.object({
  feedbackType: z.enum(["accurate", "inaccurate", "outdated", "useful", "not_useful"]),
  feedbackText: z.string().max(500).optional()
});

export async function POST(request: Request, context: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const memoryItemId = context.params.id;
  if (!memoryItemId) return fail("Missing memory item id", 400);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const result = await submitMemoryFeedback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      memoryItemId,
      feedbackType: parsed.data.feedbackType,
      feedbackText: parsed.data.feedbackText
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "memory_feedback_failed", 500);
  }
}
