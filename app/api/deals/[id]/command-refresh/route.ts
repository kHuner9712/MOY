import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { checkOrgFeatureAccess } from "@/services/feature-access-service";
import { getDealRoomDetail, refreshDealRoomCommandSummary } from "@/services/deal-command-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";

interface RouteParams {
  params: { id: string };
}

export async function POST(_request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const featureAccess = await checkOrgFeatureAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "ai_deal_command"
    });
    if (!featureAccess.allowed) {
      return fail(featureAccess.reason ?? "Deal command feature disabled", 403);
    }

    const entitlement = await getEntitlementStatus({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      refreshUsage: true
    });
    const aiQuota = canRunAiByEntitlement(entitlement);
    if (!aiQuota.allowed) {
      return fail(`${aiQuota.reason ?? "AI quota reached"}. Deal summary can be retried after quota reset.`, 429);
    }

    const detail = await getDealRoomDetail({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id
    });
    if (!detail) return fail("Deal room not found", 404);

    if (!isManager(auth.profile)) {
      const isParticipant = detail.participants.some((item) => item.userId === auth.profile.id && item.isActive);
      if (!isParticipant && detail.room.ownerId !== auth.profile.id) {
        return fail("Sales can only refresh own/joined deal rooms", 403);
      }
    }

    const result = await refreshDealRoomCommandSummary({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id,
      actorUserId: auth.profile.id
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "refresh_deal_command_failed", 500);
  }
}
