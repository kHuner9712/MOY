import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgAdminAccess, createOrgInvite } from "@/services/org-membership-service";
import { getEntitlementStatus, hasSeatCapacity } from "@/services/plan-entitlement-service";

const requestSchema = z.object({
  email: z.string().email(),
  intendedRole: z.enum(["owner", "admin", "manager", "sales", "viewer"]),
  expiresAt: z.string().datetime().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const entitlement = await getEntitlementStatus({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      refreshUsage: false
    });
    const seatCapacity = hasSeatCapacity(entitlement);

    if (!seatCapacity.allowed) {
      return fail(seatCapacity.reason ?? "Seat limit reached", 403);
    }

    const result = await createOrgInvite({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      email: parsed.data.email,
      intendedRole: parsed.data.intendedRole,
      invitedBy: auth.profile.id,
      expiresAt: parsed.data.expiresAt
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "create_invite_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
