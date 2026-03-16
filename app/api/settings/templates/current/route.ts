import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext } from "@/services/industry-template-service";
import { getCurrentOrgMembership } from "@/services/org-membership-service";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });
    if (!membership || membership.seatStatus !== "active") {
      return fail("org_membership_required", 403);
    }

    const context = await getCurrentOrgTemplateContext({
      supabase: auth.supabase,
      orgId: auth.profile.org_id
    });

    return ok({
      ...context,
      role: membership.role
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_current_template_failed";
    const status = message === "org_membership_required" ? 403 : 500;
    return fail(message, status);
  }
}
