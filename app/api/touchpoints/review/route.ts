import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { checkOrgFeatureAccess } from "@/services/feature-access-service";
import { getOrgAiSettings } from "@/services/org-ai-settings-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";
import { generateTouchpointReview } from "@/services/touchpoint-review-service";

const querySchema = z.object({
  ownerId: z.string().uuid().optional(),
  customerId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional(),
  sinceDays: z.coerce.number().int().min(1).max(90).optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    ownerId: url.searchParams.get("ownerId") ?? undefined,
    customerId: url.searchParams.get("customerId") ?? undefined,
    dealRoomId: url.searchParams.get("dealRoomId") ?? undefined,
    sinceDays: url.searchParams.get("sinceDays") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  const payload = parsed.data;
  const ownerId = isManager(auth.profile) ? payload.ownerId : auth.profile.id;
  if (!isManager(auth.profile) && payload.ownerId && payload.ownerId !== auth.profile.id) {
    return fail("Sales can only review own touchpoints", 403);
  }

  try {
    const featureAccess = await checkOrgFeatureAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "external_touchpoints"
    });
    if (!featureAccess.allowed) {
      return fail(featureAccess.reason ?? "External touchpoint feature disabled", 403);
    }

    const [aiSettings, entitlement] = await Promise.all([
      getOrgAiSettings({ supabase: auth.supabase, orgId: auth.profile.org_id }),
      getEntitlementStatus({ supabase: auth.supabase, orgId: auth.profile.org_id, refreshUsage: true })
    ]);

    if (!aiSettings.autoTouchpointReviewEnabled) {
      return fail("Organization disabled auto touchpoint review", 403);
    }

    const aiQuota = canRunAiByEntitlement(entitlement);
    if (!aiQuota.allowed) {
      return fail(`${aiQuota.reason ?? "AI quota reached"}. Touchpoint review can run after quota reset.`, 429);
    }

    const result = await generateTouchpointReview({
      supabase: auth.supabase,
      profile: auth.profile,
      ownerId,
      customerId: payload.customerId ?? null,
      dealRoomId: payload.dealRoomId ?? null,
      sinceDays: payload.sinceDays ?? 7
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "touchpoint_review_failed", 500);
  }
}
