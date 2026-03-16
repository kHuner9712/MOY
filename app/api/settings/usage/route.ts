import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgManagerAccess } from "@/services/org-membership-service";
import { getOrgFeatureFlagMap } from "@/services/org-feature-service";
import { getEntitlementStatus } from "@/services/plan-entitlement-service";
import { generateUsageHealthSummary, getUsageCounters } from "@/services/usage-metering-service";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const [usage, entitlement, featureFlags] = await Promise.all([
      getUsageCounters({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        refresh: true
      }),
      getEntitlementStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        refreshUsage: false
      }),
      getOrgFeatureFlagMap({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      })
    ]);

    const summary = await generateUsageHealthSummary({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      entitlement,
      monthlyUsage: {
        aiRunsCount: usage.monthly?.aiRunsCount ?? 0,
        prepCardsCount: usage.monthly?.prepCardsCount ?? 0,
        draftsCount: usage.monthly?.draftsCount ?? 0,
        reportsCount: usage.monthly?.reportsCount ?? 0,
        touchpointEventsCount: usage.monthly?.touchpointEventsCount ?? 0,
        documentProcessedCount: usage.monthly?.documentProcessedCount ?? 0,
        workPlanGenerationsCount: usage.monthly?.workPlanGenerationsCount ?? 0
      },
      featureFlags
    });

    return ok({
      usage,
      entitlement,
      featureFlags,
      summary
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_usage_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
