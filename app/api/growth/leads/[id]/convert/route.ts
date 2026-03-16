import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { convertLeadToSalesPipeline, getInboundLeadById } from "@/services/inbound-lead-service";
import { canViewOrgUsage, getCurrentOrgMembership } from "@/services/org-membership-service";

interface RouteContext {
  params: {
    id: string;
  };
}

export async function POST(_request: Request, context: RouteContext) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });
    if (!membership || membership.seatStatus !== "active") return fail("forbidden", 403);

    const lead = await getInboundLeadById({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      leadId: context.params.id
    });
    if (!lead) return fail("inbound_lead_not_found", 404);

    const viewAll = canViewOrgUsage(membership.role);
    if (!viewAll && lead.assignedOwnerId !== auth.profile.id) {
      return fail("forbidden", 403);
    }

    const result = await convertLeadToSalesPipeline({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      leadId: lead.id,
      actorUserId: auth.profile.id,
      allowExisting: true
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "growth_convert_lead_failed", 500);
  }
}
