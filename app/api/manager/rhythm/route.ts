import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { generateManagerRhythmInsight } from "@/services/team-rhythm-service";

const querySchema = z.object({
  periodType: z.enum(["daily", "weekly"]).optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);
  if (!isManager(auth.profile)) return fail("Manager access required", 403);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    periodType: url.searchParams.get("periodType") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  try {
    const result = await generateManagerRhythmInsight({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      periodType: parsed.data.periodType ?? "daily",
      triggeredByUserId: auth.profile.id
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "manager_rhythm_failed", 500);
  }
}
