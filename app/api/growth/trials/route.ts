import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { listInboundLeads } from "@/services/inbound-lead-service";
import { canViewOrgUsage, getCurrentOrgMembership } from "@/services/org-membership-service";
import { listTrialRequests } from "@/services/trial-request-service";
import type { TrialRequestStatus } from "@/types/commercialization";

const allowedStatuses: TrialRequestStatus[] = ["pending", "approved", "rejected", "activated", "expired"];

function parseStatuses(values: string[]): TrialRequestStatus[] {
  const set = new Set(allowedStatuses);
  return values.filter((item): item is TrialRequestStatus => set.has(item as TrialRequestStatus));
}

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const search = new URL(request.url).searchParams;
  const limitRaw = Number(search.get("limit") ?? 80);
  const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(300, Math.round(limitRaw))) : 80;
  const statuses = parseStatuses(search.getAll("status"));

  try {
    const membership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });
    if (!membership || membership.seatStatus !== "active") return fail("forbidden", 403);

    const viewAll = canViewOrgUsage(membership.role);
    const trialRequests = await listTrialRequests({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      statuses,
      limit
    });

    if (viewAll) {
      return ok({
        trialRequests
      });
    }

    const ownLeads = await listInboundLeads({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: auth.profile.id,
      limit: 500
    });
    const ownLeadIds = new Set(ownLeads.map((item) => item.id));

    return ok({
      trialRequests: trialRequests.filter((item) => ownLeadIds.has(item.leadId))
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "list_growth_trials_failed", 500);
  }
}
