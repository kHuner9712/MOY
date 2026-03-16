import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getExecutiveCockpitSummary } from "@/services/executive-cockpit-service";
import { checkOrgAiScenarioAccess } from "@/services/feature-access-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

const querySchema = z.object({
  refresh: z.enum(["true", "false"]).optional(),
  ownerId: z.string().uuid().optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    refresh: url.searchParams.get("refresh") ?? undefined,
    ownerId: url.searchParams.get("ownerId") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const aiAccess = await checkOrgAiScenarioAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "ai_deal_command",
      settingKey: "autoBriefEnabled",
      refreshUsage: false
    });

    const data = await getExecutiveCockpitSummary({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      ownerId: parsed.data.ownerId,
      refreshSignals: parsed.data.refresh === "true",
      allowAiRecommendations: aiAccess.allowed
    });

    return ok({
      ...data,
      aiRecommendationAccess: {
        allowed: aiAccess.allowed,
        reason: aiAccess.reason
      }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_executive_summary_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
