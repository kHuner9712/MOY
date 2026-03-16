import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { generateOutcomeReview } from "@/services/outcome-review-service";

const requestSchema = z.object({
  reviewScope: z.enum(["team", "org", "user"]).optional(),
  targetUserId: z.string().uuid().optional(),
  periodStart: z.string().optional(),
  periodEnd: z.string().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  const reviewScope = parsed.data.reviewScope ?? (isManager(auth.profile) ? "team" : "user");

  if (!isManager(auth.profile) && reviewScope !== "user") {
    return fail("Sales can only generate personal outcome review", 403);
  }
  if (!isManager(auth.profile) && parsed.data.targetUserId && parsed.data.targetUserId !== auth.profile.id) {
    return fail("Sales can only generate own outcome review", 403);
  }

  try {
    const result = await generateOutcomeReview({
      supabase: auth.supabase,
      profile: auth.profile,
      reviewScope,
      targetUserId: parsed.data.targetUserId ?? (reviewScope === "user" ? auth.profile.id : undefined),
      periodStart: parsed.data.periodStart,
      periodEnd: parsed.data.periodEnd
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "outcome_review_generate_failed", 500);
  }
}
