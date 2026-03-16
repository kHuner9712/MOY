import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { listExecutiveBriefs } from "@/services/executive-brief-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

const querySchema = z.object({
  briefType: z.array(z.enum(["executive_daily", "executive_weekly", "retention_watch", "trial_watch", "deal_watch"]))
    .or(z.enum(["executive_daily", "executive_weekly", "retention_watch", "trial_watch", "deal_watch"]))
    .optional(),
  limit: z.coerce.number().int().positive().max(200).optional()
});

function ensureArray<T>(input: T | T[] | undefined): T[] | undefined {
  if (input === undefined) return undefined;
  return Array.isArray(input) ? input : [input];
}

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    briefType: url.searchParams.getAll("briefType"),
    limit: url.searchParams.get("limit") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const briefs = await listExecutiveBriefs({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      briefTypes: ensureArray(parsed.data.briefType),
      limit: parsed.data.limit ?? 30
    });

    return ok({ briefs });
  } catch (error) {
    const message = error instanceof Error ? error.message : "list_executive_briefs_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
