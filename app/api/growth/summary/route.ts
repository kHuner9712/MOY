import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getGrowthSummary } from "@/services/growth-pipeline-service";
import { canViewOrgUsage, getCurrentOrgMembership } from "@/services/org-membership-service";

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const search = new URL(request.url).searchParams;
  const periodDaysRaw = Number(search.get("periodDays") ?? 30);
  const periodDays = Number.isFinite(periodDaysRaw) ? Math.max(7, Math.min(180, Math.round(periodDaysRaw))) : 30;

  try {
    const membership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });
    if (!membership || membership.seatStatus !== "active") return fail("forbidden", 403);

    const viewAll = canViewOrgUsage(membership.role);
    const summary = await getGrowthSummary({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      periodDays,
      ownerId: viewAll ? undefined : auth.profile.id
    });

    return ok({
      summary
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "growth_summary_failed", 500);
  }
}
