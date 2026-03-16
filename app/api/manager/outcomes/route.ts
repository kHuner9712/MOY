import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getManagerOutcomeInsight } from "@/services/effectiveness-insight-service";

const querySchema = z.object({
  periodType: z.enum(["weekly", "monthly"]).optional()
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
    const result = await getManagerOutcomeInsight({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      periodType: parsed.data.periodType ?? "weekly"
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "manager_outcomes_failed", 500);
  }
}
