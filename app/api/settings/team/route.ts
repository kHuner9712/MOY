import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import {
  assertOrgManagerAccess,
  canViewOrgUsage,
  isOrgAdminRole,
  listOrgInvites,
  listOrgMembers
} from "@/services/org-membership-service";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const members = await listOrgMembers({
      supabase: auth.supabase,
      orgId: auth.profile.org_id
    });

    const invites = isOrgAdminRole(membership.role)
      ? await listOrgInvites({
          supabase: auth.supabase,
          orgId: auth.profile.org_id
        })
      : [];

    return ok({
      role: membership.role,
      canManageTeam: isOrgAdminRole(membership.role),
      canViewUsage: canViewOrgUsage(membership.role),
      members,
      invites
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_team_settings_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
