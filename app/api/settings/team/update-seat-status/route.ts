import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgAdminAccess, getCurrentOrgMembership, updateMembershipSeatStatus } from "@/services/org-membership-service";

const requestSchema = z.object({
  membershipId: z.string().uuid(),
  seatStatus: z.enum(["invited", "active", "suspended", "removed"])
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

    const selfMembership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    if (
      selfMembership &&
      selfMembership.id === parsed.data.membershipId &&
      (parsed.data.seatStatus === "suspended" || parsed.data.seatStatus === "removed")
    ) {
      return fail("Cannot suspend/remove current operator", 400);
    }

    const membership = await updateMembershipSeatStatus({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      membershipId: parsed.data.membershipId,
      seatStatus: parsed.data.seatStatus
    });

    return ok({ membership });
  } catch (error) {
    const message = error instanceof Error ? error.message : "update_seat_status_failed";
    const status =
      message === "org_admin_access_required"
        ? 403
        : message === "seat_status_transition_not_allowed"
          ? 400
          : 500;
    return fail(message, status);
  }
}
