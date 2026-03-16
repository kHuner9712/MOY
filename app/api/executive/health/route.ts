import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getExecutiveHealthSummary } from "@/services/executive-cockpit-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

const querySchema = z.object({
  ownerId: z.string().uuid().optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    ownerId: url.searchParams.get("ownerId") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const data = await getExecutiveHealthSummary({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: parsed.data.ownerId
    });

    return ok(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_executive_health_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
