import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { listConversionEvents, listInboundLeads } from "@/services/inbound-lead-service";
import { canViewOrgUsage, getCurrentOrgMembership } from "@/services/org-membership-service";

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const search = new URL(request.url).searchParams;
  const limitRaw = Number(search.get("limit") ?? 40);
  const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(200, Math.round(limitRaw))) : 40;

  try {
    const membership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });
    if (!membership || membership.seatStatus !== "active") return fail("forbidden", 403);

    const events = await listConversionEvents({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      limit
    });

    const viewAll = canViewOrgUsage(membership.role);
    if (viewAll) {
      return ok({
        events
      });
    }

    const ownLeads = await listInboundLeads({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: auth.profile.id,
      limit: 500
    });
    const ownLeadIds = new Set(ownLeads.map((item) => item.id));
    const filtered = events.filter((item) => !item.leadId || ownLeadIds.has(item.leadId));

    return ok({
      events: filtered
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "list_conversion_events_failed", 500);
  }
}
