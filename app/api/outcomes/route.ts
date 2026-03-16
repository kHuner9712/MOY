import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { listActionOutcomes } from "@/services/action-outcome-service";

const querySchema = z.object({
  ownerId: z.string().uuid().optional(),
  customerId: z.string().uuid().optional(),
  outcomeType: z.enum(["followup_result", "quote_result", "meeting_result", "task_result", "manager_intervention_result"]).optional(),
  limit: z.coerce.number().int().min(1).max(200).optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    ownerId: url.searchParams.get("ownerId") ?? undefined,
    customerId: url.searchParams.get("customerId") ?? undefined,
    outcomeType: url.searchParams.get("outcomeType") ?? undefined,
    limit: url.searchParams.get("limit") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  const ownerId = isManager(auth.profile) ? parsed.data.ownerId : auth.profile.id;

  try {
    const rows = await listActionOutcomes({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId,
      customerId: parsed.data.customerId,
      outcomeType: parsed.data.outcomeType,
      limit: parsed.data.limit ?? 50
    });
    return ok(rows);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "list_outcomes_failed", 500);
  }
}
